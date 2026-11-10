"""A build's other apt sources at its Snapshot=. mkosi pins only the repositories it makes itself, and apt,
asked to follow a snapshot, falls back to the live archive when it can't reach one, so a source is rewritten
to the snapshot's own URL, or refused. A vendor's own train has no snapshot: it's let through when it's signed
by a key the build ships, which is what pins it."""

import shutil
from collections.abc import Iterable
from pathlib import Path

from ..bootstrap.shell import BootstrapError

ARCHIVES = {"http://deb.debian.org/debian": "debian", "http://deb.debian.org/debian-security": "debian-security"}


def pinned(sources: str, snapshot: str, keys: Iterable[str] = ()) -> str:
    """deb822 sources with Debian's archives at the snapshot, and a vendor's signed by one of `keys` as it
    is; any other is refused, since nothing pins it."""
    stanzas = []
    for stanza in sources.strip().split("\n\n"):
        lines = stanza.splitlines()
        uris = [line.split()[1:] for line in lines if line.startswith("URIs:")]
        signers = [line.split(maxsplit=1)[1] for line in lines if line.startswith("Signed-By:")]
        if len(uris) == 1 and len(uris[0]) == 1 and uris[0][0].rstrip("/") in ARCHIVES:
            archive = ARCHIVES[uris[0][0].rstrip("/")]
            stanzas.append("\n".join(
                [f"URIs: https://snapshot.debian.org/archive/{archive}/{snapshot}" if line.startswith("URIs:")
                 else line for line in lines] + ["Check-Valid-Until: no"]))
        elif signers and signers[0] in set(keys):
            stanzas.append(stanza)
        else:
            raise BootstrapError(f"nothing pins {uris}: only Debian's archives have a snapshot, and a vendor's "
                                 "must be signed by a key the build ships")
    return "\n\n".join(stanzas) + "\n"


def shipped(trees: Iterable[Path]) -> set[str]:
    """Every file the sandbox trees put in the build's sandbox, by its path there."""
    return {f"/{p.relative_to(tree)}" for tree in trees for p in tree.rglob("*") if p.is_file()}


def sandbox(snapshot: str | None, trees: tuple[Path, ...], out: Path) -> list[str]:
    """The mkosi arguments that put a build's sandbox sources at its snapshot, from a tree in out."""
    tree = out / "snapshot-sandbox"
    shutil.rmtree(tree, ignore_errors=True)
    if not snapshot:
        return []
    keys = shipped(trees)
    for source in trees:
        for path in source.glob("etc/apt/sources.list.d/*.sources"):
            pinned_path = tree / path.relative_to(source)
            pinned_path.parent.mkdir(parents=True, exist_ok=True)
            pinned_path.write_text(pinned(path.read_text(), snapshot, keys))
    return ["--sandbox-tree", "/out/snapshot-sandbox"] if tree.exists() else []
