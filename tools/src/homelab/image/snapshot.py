"""A build's other apt sources at its Snapshot=. mkosi pins only the repositories it makes itself, and apt,
asked to follow a snapshot, falls back to the live archive when it can't reach one, so a source is rewritten
to the snapshot's own URL, or refused."""

import shutil
from pathlib import Path

from ..bootstrap.shell import BootstrapError
from .images import Image

ARCHIVES = {"http://deb.debian.org/debian": "debian", "http://deb.debian.org/debian-security": "debian-security"}


def pinned(sources: str, snapshot: str) -> str:
    """deb822 sources with Debian's archives at the snapshot; any other is refused, since nothing pins it."""
    stanzas = []
    for stanza in sources.strip().split("\n\n"):
        lines = stanza.splitlines()
        uris = [line.split()[1:] for line in lines if line.startswith("URIs:")]
        if len(uris) != 1 or len(uris[0]) != 1 or uris[0][0].rstrip("/") not in ARCHIVES:
            raise BootstrapError(f"no snapshot pins {uris}: only Debian's archives have one")
        archive = ARCHIVES[uris[0][0].rstrip("/")]
        stanzas.append("\n".join(
            [f"URIs: https://snapshot.debian.org/archive/{archive}/{snapshot}" if line.startswith("URIs:") else line
             for line in lines] + ["Check-Valid-Until: no"]))
    return "\n\n".join(stanzas) + "\n"


def sandbox(image: Image, out: Path) -> list[str]:
    """The mkosi arguments that put the image's sandbox sources at its snapshot, from a tree in out."""
    tree = out / "snapshot-sandbox"
    shutil.rmtree(tree, ignore_errors=True)
    if not image.snapshot:
        return []
    for source in image.sandbox_trees:
        for path in source.glob("etc/apt/sources.list.d/*.sources"):
            pinned_path = tree / path.relative_to(source)
            pinned_path.parent.mkdir(parents=True, exist_ok=True)
            pinned_path.write_text(pinned(path.read_text(), image.snapshot))
    return ["--sandbox-tree", "/out/snapshot-sandbox"] if tree.exists() else []
