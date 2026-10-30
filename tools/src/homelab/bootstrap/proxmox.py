"""A host's unattended Proxmox ISO: the newest of its cluster's major release, with its answer file
inside."""

import argparse
import re
import shutil
import tempfile
from pathlib import Path

from . import answers
from .download import fetch, get, sha256
from .shell import BootstrapError, require, run, say

ISO_INDEX = "https://enterprise.proxmox.com/iso/"
CACHE = Path.home() / ".cache" / "homelab"
# Proxmox's own, as the Debian 13 install guide on pve.proxmox.com gives its SHA-256: a second
# origin for what vouches for the first
KEYRING = Path(__file__).with_name("proxmox-archive-keyring-trixie.gpg")


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "proxmox",
        help="build a host's unattended Proxmox ISO",
        description=f"{__doc__} Boot it through the iDRAC's virtual media.",
    )
    parser.add_argument("--host", required=True, help="the NetBox device to install")
    parser.add_argument("--output", type=Path, help=f"default: {CACHE}/<host>-<iso>")
    parser.set_defaults(run=main)


def main(args: argparse.Namespace) -> int:
    require("xorriso", "openssl", "gpgv")
    cluster = answers.cluster_of(args.host)
    answer = answers.render(args.host, cluster)
    source = _iso(cluster["release"])
    output = args.output or CACHE / f"{args.host}-{source.name}"

    with tempfile.TemporaryDirectory() as tmp:
        mode, answer_file = Path(tmp) / "mode.toml", Path(tmp) / "answer.toml"
        mode.write_text('mode = "iso"\n')
        answer_file.write_text(answer)

        shutil.copy(source, output)
        output.chmod(0o600)
        # What proxmox-auto-install-assistant does: the installer finds its own medium by
        # this UUID, so the added session has to keep it
        run(
            "xorriso", "-boot_image", "any", "keep", "-volume_date", "uuid", _uuid(output),
            "-dev", str(output),
            "-map", str(mode), "/auto-installer-mode.toml",
            "-map", str(answer_file), "/answer.toml",
            capture=True,
        )

    say(f"wrote {output}")
    return 0


def _uuid(iso: Path) -> str:
    report = run("xorriso", "-dev", str(iso), "-report_system_area", "cmd", capture=True)
    line = next(line for line in report.splitlines() if line.startswith("-volume_date uuid"))
    return line.split()[-1].strip("'")


def _iso(major: int) -> Path:
    """The newest minor: once installed, apt decides a node's version, not its ISO. Cached, since
    it's a gigabyte and a host's install can take a few attempts."""
    isos = {
        tuple(map(int, found.groups())): (checksum, name)
        for checksum, name in (line.split() for line in _signed_sums().splitlines())
        if (found := re.fullmatch(rf"proxmox-ve_{major}\.(\d+)-(\d+)\.iso", name))
    }
    if not isos:
        raise BootstrapError(f"no proxmox-ve_{major}.x ISO in {ISO_INDEX}SHA256SUMS")
    expected, name = isos[max(isos)]

    iso = CACHE / name
    if iso.exists() and sha256(iso) == expected:
        return iso
    CACHE.mkdir(mode=0o700, parents=True, exist_ok=True)
    return fetch(f"{ISO_INDEX}{name}", iso, expected)


def _signed_sums() -> str:
    sums, signature = get(f"{ISO_INDEX}SHA256SUMS"), get(f"{ISO_INDEX}SHA256SUMS.asc")
    with tempfile.TemporaryDirectory() as tmp:
        (Path(tmp) / "SHA256SUMS").write_text(sums)
        (Path(tmp) / "SHA256SUMS.asc").write_text(signature)
        run("gpgv", "--keyring", str(KEYRING.resolve()), f"{tmp}/SHA256SUMS.asc", f"{tmp}/SHA256SUMS",
            capture=True)
    return sums
