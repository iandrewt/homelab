"""octoDNS: NetBox's internal view to the one primary, its public view to Cloudflare and Route53.

`plan` prints the changes and a checksum; `apply` takes that checksum and applies only if
the plan still matches it, so what reaches PowerDNS is what was reviewed.
"""

import json
import os
import subprocess
import sys
import tempfile
import urllib.error
import urllib.request
from pathlib import Path

import yaml

from . import netbox, secrets
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
        config = _config(model)
    except DnsError as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
    env = _secrets(config)
    zones = {f"{name}." for name in model["zones"]}

    if missing := _uncreated(config, env, zones):
        for problem in missing:
            print(f"FAIL: {problem}", file=sys.stderr)
        print(
            "The resolvers play creates zones, with NetBox's SOA; octoDNS would create "
            "them with PowerDNS's default instead. Run it first.",
            file=sys.stderr,
        )
        return 1

    sync = Path(sys.executable).with_name("octodns-sync")
    with tempfile.NamedTemporaryFile("w", suffix=".yaml") as path:
        yaml.safe_dump(config, path)
        path.flush()
        command = [str(sync), "--quiet", "--config-file", path.name, *flags]
        return subprocess.run(command, env={**os.environ, **env}, check=False).returncode


def _config(model: dict) -> dict:
    """The committed config, plus the one primary NetBox tags, by its authoritative address: the
    internal zones' target, and where the delegations' DS come from. Each internal zone
    octoDNS writes is listed, and one holding another's delegation publishes its DS."""
    config = yaml.safe_load(Repo.discover().octodns_config.read_text())
    primaries = [
        (slug, server)
        for slug, site in sorted(model["sites"].items())
        for server in site["dns"]["servers"].values()
        if server["primary"]
    ]
    if len(primaries) > 1:
        # Every resolver would replicate from whichever came first
        raise DnsError(f"{len(primaries)} servers are tagged {PRIMARY_TAG}; zones have one primary")
    targets = []
    for slug, primary in primaries:
        # By address: its name is served by the primary itself, so it's only right once synced
        host = primary["ipv4"] or f"[{primary['ipv6']}]"
        api_key = f"env/{slug.upper()}_POWERDNS_API_KEY"
        config["providers"][slug] = {"class": POWERDNS, "host": host, "api_key": api_key,
                                     "enable_dynamic": False}
        config["providers"]["delegations"] = {
            "class": DELEGATIONS, "url": f"http://{host}:8081/api/v1/servers/localhost", "api_key": api_key,
        }
        targets.append(slug)
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


def _uncreated(config: dict, env: dict[str, str], zones: set[str]) -> list[str]:
    problems = []
    for site, provider in _primaries(config).items():
        url = f"http://{provider['host']}:8081/api/v1/servers/localhost/zones"
        request = urllib.request.Request(
            url, headers={"X-API-Key": env[_env_name(provider["api_key"])]}
        )
        try:
            with urllib.request.urlopen(request, timeout=10) as response:
                present = {z["name"] for z in json.load(response)}
        except (urllib.error.URLError, TimeoutError) as down:
            problems.append(f"{site}: can't list zones at {url}: {down}")
            continue
        problems += [f"{site}: {zone} doesn't exist yet" for zone in sorted(zones - present)]
    return problems


def _backup(args) -> int:
    try:
        api, key = _primary_api(load())
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
    except (netbox.Unreachable, secrets.SecretError, DnsError) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1
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


def _primary_api(model: dict) -> tuple[str, str]:
    """The one primary's API, and its key."""
    slug, _, primary = _primary(model)
    host = primary["ipv4"] or f"[{primary['ipv6']}]"
    key = secrets.resolve_one(f"op://Network/PowerDNS API Key - {slug}/credential")
    return f"http://{host}:8081/api/v1/servers/localhost", key


def _api(base: str, key: str, path: str):
    request = urllib.request.Request(base + path, headers={"X-API-Key": key})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            return json.load(response)
    except (urllib.error.URLError, TimeoutError) as down:
        raise DnsError(f"{base}{path}: {down}") from down
