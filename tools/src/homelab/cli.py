"""Reads NetBox, renders what can't read it, and syncs DNS."""

import argparse
import sys

from . import bootstrap, dns, fleet, image, netbox, pins, schema, secrets, unifi
from .bootstrap.shell import BootstrapError
from .model import load
from .render import firewall, frr, seed
from .repo import Repo
from .secrets import SecretError

# A peer group's TCP-MD5 key at a site, when NetBox gives it one
BGP_PASSWORD_REF = "op://Network/BGP Password - {site} {peer_group}/password"


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homelab", description=__doc__)
    subparsers = parser.add_subparsers(
        dest="command", required=True, title="subcommands"
    )

    generate = subparsers.add_parser(
        "generate",
        help="write what the UniFi gateways are given by hand, from NetBox",
        description="Rewrite unifi/generated/: <router>-frr.conf for every router with a "
        "speaker to peer with, and <site>-ipv6.txt for every site with another to reach. "
        "Only files whose content changed are touched.",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="report what would change and exit 1, instead of writing",
    )
    generate.set_defaults(run=_generate)

    network_config = subparsers.add_parser(
        "network-config",
        help="print the cloud-init network config",
        description="For seeding install media: DHCP and SLAAC, the lease's resolvers and search list, "
        "the same for every host.",
    )
    network_config.set_defaults(run=_network_config)

    dns.register(subparsers)
    fleet.register(subparsers)
    image.register(subparsers)
    pins.register(subparsers)
    schema.register(subparsers)
    unifi.register(subparsers)

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

    sites = model["sites"]
    routing = [
        (site, name, router) for site in sites.values() for name, router in sorted(site["routers"].items())
        if site["router_asn"] and router["peer_groups"]
    ]
    refs = sorted({BGP_PASSWORD_REF.format(site=site["slug"], peer_group=g["name"])
                   for site, _, router in routing for g in router["peer_groups"] if g["tcp_md5"]})
    passwords = dict(zip(refs, secrets.resolve(*refs))) if refs else {}
    rendered = {
        repo.unifi / f"{name}-frr.conf": frr.frr_config(site, name, {
            g["name"]: passwords[BGP_PASSWORD_REF.format(site=site["slug"], peer_group=g["name"])]
            for g in router["peer_groups"] if g["tcp_md5"]
        })
        for site, name, router in routing
    }
    for site in sites.values():
        if (policies := firewall.cross_site_ipv6_text(site, sites)) is not None:
            rendered[repo.unifi / f"{site['slug']}-ipv6.txt"] = policies
    changed = [p for p, body in sorted(rendered.items()) if not p.exists() or p.read_text() != body]
    stale = sorted({*repo.unifi.glob("*-frr.conf"), *repo.unifi.glob("*-ipv6.txt")} - set(rendered))

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
    print(seed.network_config(), end="")
    return 0


def main() -> int:
    args = build_parser().parse_args()
    try:
        return args.run(args)
    except (BootstrapError, SecretError) as failure:
        print(failure, file=sys.stderr)
        return 1
