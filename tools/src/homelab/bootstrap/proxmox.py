"""An unattended Proxmox ISO, baked from answer.toml.tmpl."""

import argparse
import shutil
import tempfile
from pathlib import Path

from .. import secrets
from ..repo import Repo
from .shell import BootstrapError, require, run, say

# The assistant has to come from a Proxmox release at least as new as the installer.
DEFAULT_SUITE = "trixie"

PREPARE = r"""
set -euo pipefail
apt-get update -qq
apt-get install -y -qq --no-install-recommends ca-certificates curl gnupg >/dev/null
curl -fsSL -o /usr/share/keyrings/proxmox-archive-keyring.gpg \
    https://enterprise.proxmox.com/debian/proxmox-archive-keyring-{suite}.gpg
cat > /etc/apt/sources.list.d/pve.sources <<SOURCES
Types: deb
URIs: http://download.proxmox.com/debian/pve
Suites: {suite}
Components: pve-no-subscription
Signed-By: /usr/share/keyrings/proxmox-archive-keyring.gpg
SOURCES
apt-get update -qq
apt-get install -y -qq --no-install-recommends proxmox-auto-install-assistant >/dev/null
proxmox-auto-install-assistant validate-answer /work/answer.toml
proxmox-auto-install-assistant prepare-iso /work/source.iso \
    --fetch-from iso --answer-file /work/answer.toml --output /work/output.iso
"""


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "proxmox",
        help="bake an unattended-install Proxmox ISO",
        description="Resolves the answer file's op:// references and runs "
        "proxmox-auto-install-assistant in a container.",
    )
    parser.add_argument("source", type=Path, help="the Proxmox ISO to start from")
    parser.add_argument(
        "output", type=Path, nargs="?", help="default: <source>-auto.iso"
    )
    parser.add_argument(
        "--suite", default=DEFAULT_SUITE, help=f"default: {DEFAULT_SUITE}"
    )
    parser.add_argument("--image", help="container image, default debian:<suite>-slim")
    parser.set_defaults(run=main)


def main(args: argparse.Namespace) -> int:
    require("docker")
    if not args.source.is_file():
        raise BootstrapError(f"no such ISO: {args.source}")

    output = args.output or args.source.with_name(f"{args.source.stem}-auto.iso")
    image = args.image or f"debian:{args.suite}-slim"
    template = Repo.discover().answer_template

    with tempfile.TemporaryDirectory() as tmp:
        work = Path(tmp)
        say(f"resolving secrets into {work / 'answer.toml'}")
        answer = secrets.inject(template.read_text())
        if "op://" in answer:
            raise BootstrapError(
                "unresolved op:// references - check the item and field names"
            )

        (work / "answer.toml").write_text(answer)
        (work / "answer.toml").chmod(0o600)
        shutil.copy(args.source, work / "source.iso")

        say(f"preparing the ISO in {image}")
        run(
            "docker",
            "run",
            "--rm",
            "-i",
            "-v",
            f"{work}:/work",
            "-w",
            "/work",
            "-e",
            "DEBIAN_FRONTEND=noninteractive",
            image,
            "bash",
            "-c",
            PREPARE.format(suite=args.suite),
        )
        shutil.move(work / "output.iso", output)

    say(f"wrote {output}")
    return 0
