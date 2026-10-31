"""Pinning what a host is, once it's trusted: its SSH host key, and the certificate its PowerDNS
API serves. Each is made on the host and recorded in NetBox from a fingerprint checked some
other way, which every later connection is then held to.

It writes, so it runs as you: point NETBOX_ITEM_REF at your own token, and the changelog says
who trusted what.
"""

import base64
import hashlib
import ipaddress
import socket
import ssl
import subprocess
import sys
from pathlib import Path

from pynetbox import RequestError

from . import netbox
from .model import load

API_SERVICE = "PowerDNS API"
KNOWN_HOSTS = Path.home() / ".ssh" / "known_hosts"


class PinError(Exception):
    pass


def register(subparsers) -> None:
    pin = subparsers.add_parser("pin", help="record a host's identity in NetBox", description=__doc__)
    targets = pin.add_subparsers(dest="target", required=True, title="targets")

    ssh = targets.add_parser(
        "ssh", help="a host's Ed25519 host key, which Ansible connects by",
        description="Scanned at its primary addresses, and recorded if it's the key your own "
        "known_hosts already holds for it, or has the fingerprint you read from its console "
        "(`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`).",
    )
    ssh.add_argument("host")
    ssh.add_argument("--site", help="its site's slug, where the name is at more than one")
    ssh.add_argument("--fingerprint", help="SHA256:..., as its console shows it")
    ssh.set_defaults(run=_ssh)

    api = targets.add_parser(
        "dns-api", help=f"the certificate the primary's {API_SERVICE} serves",
        description="Fetched from the API, and recorded if its SHA-256 fingerprint is the one "
        "the resolvers play printed for the primary, over SSH you already trust.",
    )
    api.add_argument("--fingerprint", help="as the resolvers play printed it")
    api.set_defaults(run=_api)


def _ssh(args) -> int:
    try:
        nb = netbox.api()
        host = _host(nb, args.host, args.site)
        addresses = [str(ipaddress.ip_interface(p.address).ip) for p in (host.primary_ip6, host.primary_ip4) if p]
        if not addresses:
            raise PinError(f"{args.host} has no primary address to scan")
        keys = {_scan(a) for a in addresses}
        if len(keys) != 1:
            raise PinError(f"{args.host} answers with different keys at {', '.join(addresses)}")
        key = keys.pop()
        names = [p.dns_name for p in (host.primary_ip6, host.primary_ip4) if p and p.dns_name]
        _trust(key, args.fingerprint, addresses + names)
        host.update({"custom_fields": {"ssh_host_key": key}})
    except (netbox.Unreachable, PinError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    except RequestError as refused:
        print(f"FAIL: NetBox: {refused}. Pin as yourself: NETBOX_ITEM_REF=op://<vault>/<your token>",
              file=sys.stderr)
        return 1
    print(f"pinned {args.host}: {_fingerprint(key)}")
    return 0


def _api(args) -> int:
    try:
        slug, name, address, port, _ = api_endpoint(load())
        der = _certificate(address, port)
        seen = hashlib.sha256(der).hexdigest()
        if args.fingerprint is None:
            raise PinError(f"{name}'s {API_SERVICE} serves a certificate with SHA-256 fingerprint {seen}. "
                           "If it's the one the resolvers play printed, run this again with --fingerprint")
        if args.fingerprint.replace(":", "").lower() != seen:
            raise PinError(f"{name}'s {API_SERVICE} serves {seen}, not {args.fingerprint}: something "
                           "between you and it isn't it, or its certificate was made again since that run")
        nb = netbox.api()
        host = _host(nb, name, slug)
        kind = "dcim.device" if hasattr(host, "device_type") else "virtualization.virtualmachine"
        service = next((s for s in nb.ipam.services.filter(name=API_SERVICE)
                        if s.parent_object_type == kind and s.parent_object_id == host.id), None)
        if service is None:
            raise PinError(f"{name} has no {API_SERVICE} Service")
        service.update({"custom_fields": {"certificate": ssl.DER_cert_to_PEM_cert(der)}})
    except (netbox.Unreachable, PinError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    except RequestError as refused:
        print(f"FAIL: NetBox: {refused}. Pin as yourself: NETBOX_ITEM_REF=op://<vault>/<your token>",
              file=sys.stderr)
        return 1
    print(f"pinned {name}'s {API_SERVICE}: {seen}")
    return 0


def api_endpoint(model: dict) -> tuple[str, str, str, int, str | None]:
    """The one primary's API: its site, its name, the address and port the Service names, IPv6
    first, and the certificate pinned for it."""
    primary = next(((slug, name) for slug, site in sorted(model["sites"].items())
                    for name, server in site["dns"]["servers"].items() if server["primary"]), None)
    if primary is None:
        raise PinError("no DNS server is the primary")
    slug, name = primary
    service = model["sites"][slug]["services"].get(name, {}).get(API_SERVICE)
    if service is None or not service["addresses"]:
        raise PinError(f"{name} has no {API_SERVICE} Service with an address")
    address = max(service["addresses"], key=lambda a: ipaddress.ip_address(a).version)
    return slug, name, address, service["ports"][0], service["certificate"]


def _host(nb, name: str, site: str | None):
    filters = {"name": name, **({"site": site} if site else {})}
    found = [*nb.dcim.devices.filter(**filters), *nb.virtualization.virtual_machines.filter(**filters)]
    if len(found) != 1:
        where = ", ".join(sorted(str(h.site and h.site.slug) for h in found)) or "no site"
        raise PinError(f"{len(found)} hosts are named {name} ({where}): name its site with --site")
    return found[0]


def _scan(address: str) -> str:
    scanned = subprocess.run(["ssh-keyscan", "-T", "5", "-t", "ed25519", address],
                             capture_output=True, text=True, check=False).stdout
    # OpenSSH 10 prints its `# host:port banner` comment here too, not only to stderr
    keys = [line.split()[1:3] for line in scanned.splitlines() if line.split()[1:2] == ["ssh-ed25519"]]
    if len(keys) != 1:
        raise PinError(f"no Ed25519 host key at {address}")
    return " ".join(keys[0])


def _fingerprint(key: str) -> str:
    digest = hashlib.sha256(base64.b64decode(key.split()[1])).digest()
    return "SHA256:" + base64.b64encode(digest).decode().rstrip("=")


def _trust(key: str, fingerprint: str | None, names: list[str]) -> None:
    """The key, if it's the one checked some other way: given by fingerprint, or already in
    your known_hosts under one of its names. A different key held there is never overridden."""
    if fingerprint is not None:
        if fingerprint != _fingerprint(key):
            raise PinError(f"it answers with {_fingerprint(key)}, not {fingerprint}")
        return
    held = set()
    for name in names:
        found = subprocess.run(["ssh-keygen", "-F", name, "-f", str(KNOWN_HOSTS)],
                               capture_output=True, text=True, check=False).stdout
        held |= {" ".join(line.split()[1:3]) for line in found.splitlines()
                 if not line.startswith("#") and line.split()[1:2] == ["ssh-ed25519"]}
    if key in held:
        return
    if held:
        raise PinError(f"your known_hosts holds another Ed25519 key for it than the {_fingerprint(key)} "
                       "it answers with. If it was rebuilt, check its console and pass --fingerprint")
    raise PinError(f"it answers with {_fingerprint(key)}, which your known_hosts doesn't hold. Check it "
                   "against its console, then pass --fingerprint")


def _certificate(address: str, port: int) -> bytes:
    """Whatever it serves: trust is the fingerprint's, not a CA's."""
    context = ssl.create_default_context()
    context.check_hostname, context.verify_mode = False, ssl.CERT_NONE
    try:
        with socket.create_connection((address, port), timeout=10) as raw, context.wrap_socket(raw) as tls:
            return tls.getpeercert(binary_form=True)
    except OSError as down:
        raise PinError(f"can't reach {address} port {port}: {down}") from down
