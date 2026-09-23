"""A host's unattended Proxmox ISO: the latest release, with its answer file inside."""

import argparse
import hashlib
import re
import shutil
import tempfile
import urllib.request
from pathlib import Path

from . import answers
from .shell import BootstrapError, require, run, say

ISO_INDEX = "https://enterprise.proxmox.com/iso/"
CACHE = Path.home() / ".cache" / "homelab"
CHUNK = 1024 * 1024


def register(subparsers) -> None:
    parser = subparsers.add_parser(
        "proxmox",
        help="build a host's unattended Proxmox ISO",
        description=f"{__doc__} Boot it through the iDRAC's virtual media.",
    )
    parser.add_argument("--host", required=True, help="the NetBox device to install")
    parser.add_argument("--output", type=Path, help="default: ./<host>-<release>.iso")
    parser.set_defaults(run=main)


def main(args: argparse.Namespace) -> int:
    require("xorriso", "openssl")
    answer = answers.render(args.host)
    source = _latest_iso()
    output = args.output or Path(f"{args.host}-{source.name}")

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


def _latest_iso() -> Path:
    """Cached, since it's a gigabyte and a host's install can take a few attempts."""
    releases = re.findall(r"proxmox-ve_(\d+)\.(\d+)-(\d+)\.iso", _get(ISO_INDEX))
    if not releases:
        raise BootstrapError(f"no proxmox-ve_*.iso at {ISO_INDEX}")
    name = "proxmox-ve_{}.{}-{}.iso".format(*max(releases, key=lambda r: tuple(map(int, r))))
    expected = next(
        line.split()[0] for line in _get(f"{ISO_INDEX}SHA256SUMS").splitlines()
        if line.split()[1:] == [name]
    )

    iso = CACHE / name
    if iso.exists() and _sha256(iso) == expected:
        return iso

    CACHE.mkdir(parents=True, exist_ok=True)
    partial = iso.with_suffix(".partial")
    say(f"downloading {ISO_INDEX}{name}")
    with urllib.request.urlopen(f"{ISO_INDEX}{name}") as response, partial.open("wb") as out:
        shutil.copyfileobj(response, out, CHUNK)
    if (actual := _sha256(partial)) != expected:
        partial.unlink()
        raise BootstrapError(f"checksum mismatch for {name}: expected {expected}, got {actual}")
    partial.rename(iso)
    return iso


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def _get(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode()
