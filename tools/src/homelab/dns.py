"""octoDNS: NetBox's internal view to the one primary, its public view to Cloudflare and Route53.

`plan` prints the changes and a checksum; `apply` takes that checksum and applies only if
the plan still matches it, so what reaches PowerDNS is what was reviewed.
"""

import contextlib
import json
import os
import ssl
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import yaml

from . import netbox, pins, secrets
from .model import PRIMARY_TAG, load, unzoned
from .repo import Repo

POWERDNS = "octodns_powerdns.PowerDnsProvider"
DELEGATIONS = "homelab.dns_sources.PowerDnsDelegations"
CLOUDFLARE_TOKEN_REF = "op://Network/Cloudflare API Token - octoDNS/credential"
# The signing keys exist only on the primary, so they're kept where only tools run by hand read
KEY_VAULT = "Break-glass"
KEY_ITEM = "DNSSEC Key - "
RESTORED_KEYS = "POWERDNS_RESOLVER_RESTORED_KEYS"


class DnsError(Exception):
    pass


@dataclass(frozen=True)
class Api:
    """The one primary's API, as its Service names it, and its pinned certificate's file."""
    site: str
    host: str
    port: int
    cafile: str

    @property
    def base(self) -> str:
        return f"https://{self.host}:{self.port}/api/v1/servers/localhost"


def register(subparsers) -> None:
    dns = subparsers.add_parser(
        "dns", help="sync DNS records from NetBox to PowerDNS", description=__doc__
    )
    actions = dns.add_subparsers(dest="action", required=True, title="actions")

    plan = actions.add_parser("plan", help="show what would change, and its checksum")
    plan.add_argument(
        "--force", action="store_true", help="allow changes past octoDNS's safety threshold"
    )
    plan.set_defaults(run=_plan)

    apply = actions.add_parser("apply", help="apply a plan that was reviewed")
    apply.add_argument("checksum", help="as printed by `homelab dns plan`")
    apply.add_argument(
        "--force", action="store_true", help="allow changes past octoDNS's safety threshold"
    )
    apply.set_defaults(run=_apply)

    keys = actions.add_parser("keys", help="the signing keys, to 1Password and back")
    steps = keys.add_subparsers(dest="step", required=True, title="steps")
    steps.add_parser(
        "backup", help=f"write each signed zone's keys from the primary to {KEY_VAULT}",
        description="One item per zone, a section per key. Run it after a zone is signed or a key rolled.",
    ).set_defaults(run=_backup)
    restore = steps.add_parser(
        "restore", help=f"run the resolvers play on the primary with the keys in {KEY_VAULT}",
        description="For a rebuilt primary. A zone created in that run imports its saved keys rather "
        "than making new ones, so its DS still holds; a zone that has keys keeps them. The keys "
        "reach the play in its environment, never a file or the terminal.",
    )
    restore.add_argument("playbook_args", nargs="*", help="for ansible-playbook, after `--`")
    restore.set_defaults(run=_restore)


def _plan(args) -> int:
    return _sync(*(["--force"] if args.force else []))


def _apply(args) -> int:
    return _sync("--checksum", args.checksum, *(["--force"] if args.force else []))


def _sync(*flags: str) -> int:
    try:
        model = load()
    except netbox.Unreachable as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    for problem in unzoned(model):
        print(f"UNZONED: {problem}", file=sys.stderr)

    try:
        with _pinned(model) as api:
            config = _config(model, api)
            env = _secrets(config)
            problems, signing = _unready(api, env[_env_name(config["providers"][api.site]["api_key"])],
                                         model["zones"])
            for name in signing:
                print(f"UNSIGN: {name}'s DNSSEC policy isn't active, so this sync withdraws its DS; the "
                      "primary still signs it until it's unsigned, once the DS's TTL has passed", file=sys.stderr)
            if problems:
                for problem in problems:
                    print(f"FAIL: {problem}", file=sys.stderr)
                return 1

            sync = Path(sys.executable).with_name("octodns-sync")
            with tempfile.NamedTemporaryFile("w", suffix=".yaml") as path:
                yaml.safe_dump(config, path)
                path.flush()
                command = [str(sync), "--quiet", "--config-file", path.name, *flags]
                return subprocess.run(command, env={**os.environ, **env}, check=False).returncode
    except DnsError as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1


@contextlib.contextmanager
def _pinned(model: dict):
    """The one primary's API, held to the certificate NetBox pins for it, for as long as it's used."""
    primaries = [name for site in model["sites"].values()
                 for name, server in site["dns"]["servers"].items() if server["primary"]]
    if len(primaries) > 1:
        # Every resolver would replicate from whichever came first
        raise DnsError(f"{len(primaries)} servers are tagged {PRIMARY_TAG}; zones have one primary")
    try:
        slug, name, address, port, certificate = pins.api_endpoint(model)
    except pins.PinError as bad:
        raise DnsError(str(bad)) from bad
    if not certificate:
        raise DnsError(f"{name}'s {pins.API_SERVICE} has no pinned certificate: `homelab pin dns-api`")
    with tempfile.NamedTemporaryFile("w", suffix=".pem") as cafile:
        cafile.write(certificate)
        cafile.flush()
        yield Api(slug, f"[{address}]" if ":" in address else address, port, cafile.name)


def _config(model: dict, api: Api) -> dict:
    """The committed config, plus the one primary NetBox tags, by its API's address: the internal
    zones' target, and where the delegations' DS come from. Each internal zone octoDNS writes is
    listed, and one holding another's delegation publishes its DS."""
    config = yaml.safe_load(Repo.discover().octodns_config.read_text())
    api_key = f"env/{api.site.upper()}_POWERDNS_API_KEY"
    config["providers"][api.site] = {"class": POWERDNS, "host": api.host, "port": api.port, "scheme": "https",
                                     "ssl_verify": api.cafile, "api_key": api_key, "enable_dynamic": False}
    config["providers"]["delegations"] = {
        "class": DELEGATIONS, "url": api.base, "api_key": api_key, "ca_file": api.cafile,
        # A zone's DS follows NetBox's policy, so it's withdrawn before the zone is unsigned
        "signed": sorted(f"{name}." for name, zone in model["zones"].items() if zone["dnssec_keys"]),
    }
    targets = [api.site]
    if both := sorted(set(model["zones"]) & set(model["public_zones"])):
        raise DnsError(f"{', '.join(both)} in both views: a name has one answer everywhere")
    # A public reverse zone's name holds a site's prefix, which the repo never does
    reverse = config["zones"].pop("*.arpa.")
    for name in model["public_zones"]:
        if name.endswith(".arpa"):
            config["zones"][f"{name}."] = reverse
    # By name rather than `*`: a zone its owner writes over RFC 2136 would lose every record
    template = config["zones"].pop("*")
    for name, zone in sorted(model["zones"].items()):
        if not zone["octodns"]:
            continue
        sources = template["sources"]
        if any(_parent(other, model["zones"]) == name for other in model["zones"]):
            sources = [*sources, "delegations"]
        config["zones"][f"{name}."] = {**template, "sources": sources, "targets": targets}
    return config


def _parent(name: str, zones) -> str | None:
    """The nearest zone enclosing this one, which holds its delegation."""
    enclosing = [z for z in zones if name.endswith(f".{z}")]
    return max(enclosing, key=len, default=None)


def _primaries(config: dict) -> dict[str, dict]:
    return {
        site: provider
        for site, provider in config["providers"].items()
        if provider["class"] == POWERDNS
    }


def _env_name(value: str) -> str:
    return value.removeprefix("env/")


def _secrets(config: dict) -> dict[str, str]:
    """The config's env/ values, named as the config names them."""
    source = config["providers"]["netbox"]
    url, token = netbox.connection()
    env = {_env_name(source["url"]): url, _env_name(source["token"]): token,
           _env_name(config["providers"]["cloudflare"]["token"]): secrets.resolve_one(CLOUDFLARE_TOKEN_REF)}
    for site, provider in _primaries(config).items():
        env[_env_name(provider["api_key"])] = secrets.resolve_one(
            f"op://Network/PowerDNS API Key - {site}/credential"
        )
    return env


def _unready(api: Api, key: str, zones: dict) -> tuple[list[str], list[str]]:
    """Zones the resolvers play hasn't created, which octoDNS would create with PowerDNS's
    default SOA rather than NetBox's; and zones the primary signs though NetBox's policy is
    inactive, which nothing unsigns."""
    try:
        present = {z["name"].rstrip("."): z for z in _api(api, key, "/zones")}
    except DnsError as down:
        return [f"{api.site}: can't list zones: {down}"], []
    problems = [f"{api.site}: {name} doesn't exist yet; run the resolvers play first"
                for name in sorted(zones.keys() - present.keys())]
    signing = [name for name, zone in sorted(zones.items())
               if not zone["dnssec_keys"] and present.get(name, {}).get("dnssec")]
    return problems, signing


def _backup(args) -> int:
    try:
        with _pinned(load()) as api:
            return _write_keys(api, secrets.resolve_one(f"op://Network/PowerDNS API Key - {api.site}/credential"))
    except (netbox.Unreachable, secrets.SecretError, DnsError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1


def _write_keys(api: Api, key: str) -> int:
    written = set()
    for zone in sorted(_api(api, key, "/zones"), key=lambda z: z["name"]):
        if not zone.get("dnssec"):
            continue
        name = zone["name"].rstrip(".")
        fields = {}
        for listed in _api(api, key, f"/zones/{zone['name']}/cryptokeys"):
            k = _api(api, key, f"/zones/{zone['name']}/cryptokeys/{listed['id']}")
            tag = k["ds"][0].split()[0] if k.get("ds") else str(k["id"])
            section = f"{k['keytype']} {tag}"
            fields |= {
                (section, "private key"): k["privatekey"], (section, "keytype"): k["keytype"],
                (section, "active"): str(k["active"]).lower(),
                (section, "published"): str(k.get("published", True)).lower(),
            }
        title = KEY_ITEM + name
        print(f"{secrets.write_item(KEY_VAULT, title, fields, concealed={'private key'}, tags=['homelab/dnssec'])}: {name}")
        written.add(title)
    for title in sorted(set(secrets.read_items(KEY_VAULT, KEY_ITEM)) - written):
        print(f"STALE: {title}: the primary signs no such zone", file=sys.stderr)
    return 0


def _restore(args) -> int:
    try:
        _, primary, _ = _primary(load())
        items = secrets.read_items(KEY_VAULT, KEY_ITEM)
    except (netbox.Unreachable, secrets.SecretError, DnsError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    restored = {}
    for title, fields in sorted(items.items()):
        restored[title.removeprefix(KEY_ITEM)] = [
            {"keytype": fields[(s, "keytype")], "privatekey": fields[(s, "private key")],
             "active": fields[(s, "active")] == "true", "published": fields[(s, "published")] == "true"}
            for s in sorted({s for s, _ in fields})
        ]
    playbook = Path(sys.executable).with_name("ansible-playbook")
    command = [str(playbook), "site.yml", "--limit", primary, "--tags", "resolvers", *args.playbook_args]
    env = {**os.environ, RESTORED_KEYS: json.dumps(restored)}
    return subprocess.run(command, cwd=Repo.discover().root / "ansible", env=env, check=False).returncode


def _primary(model: dict) -> tuple[str, str, dict]:
    """The one primary: its site, its name and its server."""
    found = next(
        ((slug, name, server) for slug, site in sorted(model["sites"].items())
         for name, server in site["dns"]["servers"].items() if server["primary"]),
        None,
    )
    if found is None:
        raise DnsError(f"no server is tagged {PRIMARY_TAG}")
    return found


def _api(api: Api, key: str, path: str):
    request = urllib.request.Request(api.base + path, headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=10, context=ssl.create_default_context(cafile=api.cafile)) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as down:
        raise DnsError(f"{api.base}{path}: {down}") from down
