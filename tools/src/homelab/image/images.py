"""An image images/images.toml names: the mkosi profiles it's built with, its configuration as mkosi
composes them, and what booting it in qemu takes."""

import json
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..bootstrap.shell import BootstrapError
from . import builder
from .vm import Boot

@dataclass(frozen=True)
class Bake:
    """The distribution's tree for one architecture, baked by the baseline, which every image lays over."""
    architecture: str

    @property
    def tar(self) -> str:
        """Where the bake is sealed to, which BaseTrees=%O/bake_%a.tar names."""
        return f"bake_{self.architecture}.tar"


@dataclass(frozen=True)
class Image:
    name: str
    profiles: tuple[str, ...]
    architecture: str
    release: str
    # Laid out as a signed image, /usr under verity, rather than as a tree on its target's disk
    image_based: bool
    packages: frozenset[str]
    # In mkosi's order, a later one's file winning
    extra_trees: tuple[Path, ...]
    boot: Boot
    format: str
    # Debian's, at which every package the build takes is pinned
    snapshot: str | None = None
    sandbox_trees: tuple[Path, ...] = ()

    @property
    def sources(self) -> str:
        """Its package sources."""
        return f"/etc/apt/sources.list.d/{self.release}.sources"

    @property
    def args(self) -> list[str]:
        """What mkosi takes to build or boot it."""
        return ["--architecture", self.architecture, *(a for p in self.profiles for a in ("--profile", p))]

    def file(self, path: str) -> Path:
        """Where a file of the image comes from: the last extra tree holding it."""
        for tree in reversed(self.extra_trees):
            if (tree / path.lstrip("/")).is_file():
                return tree / path.lstrip("/")
        raise BootstrapError(f"no extra tree of {self.name} holds {path}")


def summary(images: Path, *args: str) -> dict:
    """mkosi's own view of a configuration's main image."""
    found = json.loads(builder.run_in(
        {images: "/images:ro"}, "mkosi", "-C", "/images", "--output-directory", "/cache/output",
        "--image-version", "dev", *args, "--json", "summary", capture=True))
    return next(c for c in found["Images"] if c["Image"] == "main")


def load(images: Path, name: str) -> Image:
    catalog = tomllib.loads((images / "images.toml").read_text())
    if name not in catalog:
        raise BootstrapError(f"{name!r} is not an image in {images / 'images.toml'}")
    entry = catalog[name]
    args = ["--architecture", entry["architecture"], *(a for p in entry["profiles"] for a in ("--profile", p))]
    config = summary(images, *args)
    if config["ImageId"] != name:
        raise BootstrapError(f"{entry['profiles']} make {config['ImageId']!r}, not {name!r}: a pair's mkosi.conf.d "
                             "file names it")
    return Image(
        name=name,
        profiles=tuple(entry["profiles"]),
        architecture=entry["architecture"],
        release=config["Release"],
        image_based=config["Verity"] == "signed",
        packages=frozenset(config["Packages"]),
        extra_trees=tuple(images / Path(t["Source"]).relative_to("/images") for t in config["ExtraTrees"]
                          if t["Source"].startswith("/images/")),
        boot=Boot(architecture=entry["architecture"], firmware=entry["firmware"], seed=entry["seed"],
                  cloud_init=entry.get("cloud_init", True), cmdline=entry.get("cmdline", ""),
                  tpm=entry.get("tpm", False)),
        format=entry["format"],
        snapshot=config["Snapshot"],
        sandbox_trees=tuple(images / Path(t["Source"]).relative_to("/images") for t in config["SandboxTrees"]
                            if t["Source"].startswith("/images/")),
    )
