import argparse
import sys

from . import bootstrap, dns, netbox, render, tofukey
from . import clusters as cluster_files
from .bootstrap.shell import BootstrapError
from .model import InvalidSource, load
from .render import seed
from .repo import Repo
from .secrets import SecretError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="homelab", description=__doc__)
    subparsers = parser.add_subparsers(
        dest="command", required=True, title="subcommands"
    )

    validate = subparsers.add_parser(
        "validate",
        help="check NetBox and tofu/clusters/*.yaml",
        description="Schema and semantic checks for every hand-authored source file."
    )
    validate.set_defaults(run=_validate)

    generate = subparsers.add_parser(
        "generate",
        help="write everything derived from NetBox",
        description="Rewrite the Ansible inventory, the per-cluster OpenTofu extracts, "
        "and the UniFi config. Only files whose content changed are touched.",
    )
    generate.add_argument(
        "--check",
        action="store_true",
        help="report what would change and exit 1, instead of writing",
    )
    generate.set_defaults(run=_generate)

    seed = subparsers.add_parser(
        "network-config",
        help="print a site's cloud-init network config",
        description="For seeding install media. DHCP and SLAAC; nothing is per-host "
        "except whether the host is a resolver, which is not pointed at itself.",
    )
    seed.add_argument("site", help="site slug")
    seed.add_argument(
        "--resolver", metavar="HOST", help="the seed is for this resolver, not an ordinary host"
    )
    seed.set_defaults(run=_network_config)

    dns.register(subparsers)

    key = subparsers.add_parser(
        "tofu-key",
        help="OpenTofu external key provider (not for interactive use)",
        description="Speaks OpenTofu's external key provider protocol on stdin/stdout, "
        "deriving the state encryption key from a passphrase in 1Password.",
    )
    key.set_defaults(run=lambda args: tofukey.main())

    boot = subparsers.add_parser(
        "bootstrap", help="build install media", description="Build install media."
    )
    targets = boot.add_subparsers(dest="target", required=True, title="targets")
    for target in bootstrap.TARGETS:
        target.register(targets)

    return parser


def _sources(repo: Repo):
    """Both source files, or every reason they can't be used."""
    try:
        ipam = load()
    except netbox.Unreachable as down:
        return None, None, [f"NetBox: {down}"]
    except InvalidSource as invalid:
        return None, None, str(invalid).splitlines()

    clusters, errors = cluster_files.load_all(repo, ipam)
    return ipam, clusters, errors


def _validate(args) -> int:
    repo = Repo.discover()
    _, _, errors = _sources(repo)
    for error in errors:
        print(f"FAIL: {error}", file=sys.stderr)
    if errors:
        return 1

    print("NetBox + tofu/clusters/*.yaml: valid")
    return 0


def _generate(args) -> int:
    repo = Repo.discover()
    ipam, _clusters, errors = _sources(repo)
    if errors:
        for error in errors:
            print(f"FAIL: {error}", file=sys.stderr)
        print(
            "refusing to generate from a source that doesn't validate", file=sys.stderr
        )
        return 1

    plan = render.plan(repo, ipam)

    if args.check:
        drifted = plan.drift()
        for path in drifted:
            print(f"DRIFT: {repo.relative(path)}")
        if drifted:
            print(
                "Generated output doesn't match NetBox. Someone hand-edited a generated "
                "file, or NetBox changed since the last `homelab generate`.",
                file=sys.stderr,
            )
            return 1
        print(f"{len(plan)} generated files match NetBox")
        return 0

    for action, path in plan.write():
        print(f"{action} {repo.relative(path)}")
    return 0


def _network_config(args) -> int:
    try:
        ipam = load()
    except (netbox.Unreachable, InvalidSource) as bad:
        print(f"FAIL: {bad}", file=sys.stderr)
        return 1

    site = ipam.sites.get(args.site)
    if site is None:
        known = ", ".join(sorted(ipam.sites))
        print(f"{args.site!r} is not a site in NetBox. Known: {known}", file=sys.stderr)
        return 1

    resolver = None
    if args.resolver:
        resolver = site.resolvers.get(args.resolver)
        if resolver is None:
            known = ", ".join(sorted(site.resolvers)) or "none"
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
