"""The one pinned container every image is built and booted in."""

import os
from pathlib import Path

from ..bootstrap.shell import require, run, say
from ..repo import Repo
from . import signing as release

CACHE = Path.home() / ".cache" / "homelab" / "images"
# Every image's and the bake's artifacts, together, as BaseTrees=%O/bake_%a.tar expects
OUTPUT = CACHE / "output"
NAME = "homelab-image-builder"


def ensure() -> Path:
    """Builds the builder from images/Containerfile, and returns images/."""
    require("docker")
    images = Repo.discover().root / "images"
    say("building the builder")
    run("docker", "build", "--quiet", "--tag", NAME, "--file", str(images / "Containerfile"),
        str(images), capture=True)
    return images


# mkosi mounts its sandbox's API filesystems, which takes a privileged container
def run_in(mounts: dict[Path, str], *command: str, privileged: bool = False, user: str | None = None,
           workdir: str | None = None, input: str | None = None, detach: str | None = None,
           devices: tuple[str, ...] = (), ports: tuple[str, ...] = (), env: dict[str, str] | None = None,
           capture: bool = False) -> str:
    """Runs a command in the builder, returning its output if captured; `detach` names a container
    left running."""
    # Docker would make a missing one as root, and nothing after could write beside it
    for host in mounts:
        host.mkdir(parents=True, exist_ok=True)
    return run("docker", "run", *(["--detach", "--name", detach] if detach else ["--rm"]),
               *(["--privileged"] if privileged else []), *(["--interactive"] if input else []),
               *(["--user", user] if user else []), *(["--workdir", workdir] if workdir else []),
               *(a for d in devices for a in ("--device", d)), *(a for p in ports for a in ("--publish", p)),
               *(a for host, guest in mounts.items() for a in ("--volume", f"{host}:{guest}")),
               # By name, so a credential's value never reaches the command line
               *(a for name in (env or {}) for a in ("--env", name)),
               NAME, *command, input=input, capture=capture or bool(detach), env=os.environ | (env or {}))


def mkosi(images: Path, *args: str, mounts: dict[Path, str] | None = None, detach: str | None = None,
          devices: tuple[str, ...] = (), signing: Path | None = None) -> str:
    """mkosi on a copy of images/, as root, so its tools tree, caches and output stay beside the cache, never
    in the checkout: mkosi replaces the tools tree it builds where it looks for one, in the project. Returns
    the container's ID if detached. `signing` is a release's module and its config, whose server runs
    beside the verb, the last argument."""
    for path in (OUTPUT, CACHE / "mkosi", CACHE / "packages"):
        path.mkdir(parents=True, exist_ok=True)
    run_in({images: "/images:ro", CACHE: "/cache"}, "rsync", "--archive", "--delete", "--exclude", "/mkosi.tools*",
           "/images/", "/cache/project/")
    command = ["mkosi", "-C", "/cache/project", "--output-directory", "/cache/output",
               "--cache-directory", "/cache/mkosi", "--package-cache-directory", "/cache/packages", *args]
    if signing:
        command = ["sh", "-c", release.SERVER, "sh", command[-1], *command[:-1]]
    return run_in({CACHE: "/cache", **(mounts or {}), **(release.mounts(signing) if signing else {})},
                  *command, privileged=True, detach=detach, devices=devices,
                  env=release.env() if signing else None)
