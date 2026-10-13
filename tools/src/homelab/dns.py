"""octoDNS, from NetBox's internal view to each site's primary.

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
from .model import load, unzoned
from .repo import Repo

POWERDNS = "octodns_powerdns.PowerDnsProvider"


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


def _plan(args) -> int:
    return _sync(*(["--plan"] + (["--force"] if args.force else [])))


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

    config = _config(model)
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
    """The committed config, plus a target per site: the primary NetBox tags, by the name
    of its service address."""
    config = yaml.safe_load(Repo.discover().octodns_config.read_text())
    targets = []
    for slug, site in sorted(model["sites"].items()):
        primary = next((r for r in site["resolvers"].values() if r["primary"]), None)
        if primary is None:
            continue
        config["providers"][slug] = {
            "class": POWERDNS,
            "host": site["named"][primary["ipv6"]],
            "api_key": f"env/{slug.upper()}_POWERDNS_API_KEY",
            "enable_dynamic": False,
        }
        targets.append(slug)
    for zone in config["zones"].values():
        zone["targets"] = targets
    return config


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
    env = {_env_name(source["url"]): url, _env_name(source["token"]): token}
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
