import hashlib
import urllib.request
from pathlib import Path

from .shell import BootstrapError, say

CHUNK = 1024 * 1024


def get(url: str) -> str:
    with urllib.request.urlopen(url) as response:
        return response.read().decode()


def sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as f:
        while block := f.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def fetch(url: str, dest: Path, expected: str) -> Path:
    """Lands at `dest` only once it matches its checksum."""
    partial = dest.with_name(f"{dest.name}.partial")
    say(f"downloading {url}")
    digest = hashlib.sha256()
    with urllib.request.urlopen(url) as response, partial.open("wb") as out:
        while block := response.read(CHUNK):
            digest.update(block)
            out.write(block)
    if (actual := digest.hexdigest()) != expected:
        partial.unlink()
        raise BootstrapError(f"checksum mismatch for {dest.name}: expected {expected}, got {actual}")
    return partial.rename(dest)
