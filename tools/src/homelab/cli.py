"""Reads NetBox, renders what can't read it, and syncs DNS."""

import argparse
import sys

from . import bootstrap, dns, fleet, netbox
from .bootstrap.shell import BootstrapError
from .model import load
from .render import frr, seed
from .repo import Repo
from .secrets import SecretError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homelab", description=__doc__)
    subparsers = parser.add_subparsers(
        dest="command", required=True, title="subcommands"
    )

    generate = subparsers.add_parser(
        "generate",
        help="write the UDM Pro's FRR config from NetBox",
        description="Rewrite unifi/generated/<site>-frr.conf for every site with a router "
        "and something to route. Only files whose content changed are touched.",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="report what would change and exit 1, instead of writing",
    )
    generate.set_defaults(run=_generate)

    network_config = subparsers.add_parser(
        "network-config",
        help="print a site's cloud-init network config",
        description="For seeding install media. DHCP and SLAAC; nothing is per-host "
        "except whether the host is a resolver, which is not pointed at itself.",
    )
    network_config.add_argument("site", help="site slug")
    network_config.add_argument(
        "--resolver", metavar="HOST", help="the seed is for this resolver, not an ordinary host"
    )
    network_config.set_defaults(run=_network_config)

    dns.register(subparsers)
    fleet.register(subparsers)

    boot = subparsers.add_parser(
        "bootstrap", help="build install media", description="Build install media."
    )
    targets = boot.add_subparsers(dest="target", required=True, title="targets")
    for target in bootstrap.TARGETS:
        target.register(targets)

    return parser


def _generate(args) -> int:
    repo = Repo.discover()
    try:
        model = load()
    except netbox.Unreachable as down:
        print(f"FAIL: NetBox: {down}", file=sys.stderr)
        return 1

    rendered = {
        repo.unifi / f"{site['slug']}-frr.conf": frr.frr_config(site)
        for site in model["sites"].values()
        if site["router_asn"] and site["speakers"]
    }
    changed = [p for p, body in sorted(rendered.items()) if not p.exists() or p.read_text() != body]
    stale = sorted(set(repo.unifi.glob("*-frr.conf")) - set(rendered))

    if args.check:
        for path in changed + stale:
            print(f"DRIFT: {repo.relative(path)}")
        if changed or stale:
            print(
                "Generated output doesn't match NetBox. Someone hand-edited a generated "
                "file, or NetBox changed since the last `homelab generate`.",
                file=sys.stderr,
            )
            return 1
        print(f"{len(rendered)} generated files match NetBox")
        return 0

    for path in changed:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered[path])
        print(f"wrote {repo.relative(path)}")
    for path in stale:
        path.unlink()
        print(f"removed {repo.relative(path)}")
    return 0


def _network_config(args) -> int:
    try:
        model = load()
    except netbox.Unreachable as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1

    site = model["sites"].get(args.site)
    if site is None:
        known = ", ".join(sorted(model["sites"]))
        print(f"{args.site!r} is not a site in NetBox. Known: {known}", file=sys.stderr)
        return 1

    resolver = None
    if args.resolver:
        resolver = site["resolvers"].get(args.resolver)
        if resolver is None:
            known = ", ".join(sorted(site["resolvers"])) or "none"
            print(f"{args.resolver!r} is not a resolver at {args.site}. Known: {known}",
                  file=sys.stderr)
            return 1

    print(seed.network_config(site, resolver=resolver), end="")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.run(args)
    except (BootstrapError, SecretError) as failure:
        print(failure, file=sys.stderr)
        return 1
