"""A profile in images/: its mkosi.conf, and what booting it in qemu takes."""

import re
import tomllib
from dataclasses import dataclass
from pathlib import Path

from ..bootstrap.shell import BootstrapError

# What the bake points the package manager at, its snapshot's archives
SNAPSHOT_SOURCES = {
    "debian": """\
Types: deb
URIs: http://snapshot.debian.org/archive/debian/{snapshot}
Suites: {release} {release}-updates
Components: {components}
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg

Types: deb
URIs: http://snapshot.debian.org/archive/debian-security/{snapshot}
Suites: {release}-security
Components: {components}
Signed-By: /usr/share/keyrings/debian-archive-keyring.gpg
""",
}


# Where each distribution's packages put a kernel and its initrd, relative to the tree
KERNEL = {
    "debian": ("boot/vmlinuz-*", "boot/initrd.img-*"),
}


@dataclass(frozen=True)
class Profile:
    recipe: Path
    distribution: str
    release: str
    architecture: str
    snapshot: str
    components: tuple[str, ...]
    packages: frozenset[str]
    firmware: bool
    seed: str
    format: str
    cmdline: str

    @property
    def name(self) -> str:
        return self.recipe.name

    @property
    def sources(self) -> str:
        """The image's own package sources, which the bake's snapshot stands in for."""
        return f"/etc/apt/sources.list.d/{self.release}.sources"

    @property
    def kernel(self) -> tuple[str, str]:
        return KERNEL[self.distribution]

    @property
    def snapshot_sources(self) -> str:
        return SNAPSHOT_SOURCES[self.distribution].format(
            snapshot=self.snapshot, release=self.release, components=" ".join(self.components))


def load(images: Path, name: str) -> Profile:
    recipe = images / name
    if not (recipe / "mkosi.conf").is_file() or not (recipe / "profile.toml").is_file():
        raise BootstrapError(f"{name!r} is not a profile in {images}")
    conf = (recipe / "mkosi.conf").read_text()
    boot = tomllib.loads((recipe / "profile.toml").read_text())

    def setting(key: str, default: str | None = None) -> str:
        found = re.search(rf"^{key}=(.*)$", conf, re.MULTILINE)
        if found is None and default is None:
            raise BootstrapError(f"{name}'s mkosi.conf sets no {key}=")
        return found.group(1).strip() if found else default

    packages = re.search(r"^Packages=\n((?:[ \t]+\S+\n?)+)", conf, re.MULTILINE)
    return Profile(
        recipe=recipe,
        distribution=setting("Distribution"),
        release=setting("Release"),
        architecture=setting("Architecture"),
        snapshot=setting("Snapshot"),
        components=("main", *filter(None, setting("Repositories", "").split(","))),
        packages=frozenset(packages.group(1).split()) if packages else frozenset(),
        firmware=boot["firmware"],
        seed=boot["seed"],
        format=boot["format"],
        cmdline=boot.get("cmdline") or (recipe / "mkosi.extra/etc/kernel/cmdline").read_text().strip(),
    )
