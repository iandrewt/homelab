"""The one pinned container every image is built and booted in."""

from pathlib import Path

from ..bootstrap.shell import require, run, say
from ..repo import Repo

CACHE = Path.home() / ".cache" / "homelab" / "images"
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
           devices: tuple[str, ...] = (), ports: tuple[str, ...] = ()) -> None:
    """Runs a command in the builder; `detach` names a container left running."""
    run("docker", "run", *(["--detach", "--name", detach] if detach else ["--rm"]),
        *(["--privileged"] if privileged else []), *(["--interactive"] if input else []),
        *(["--user", user] if user else []), *(["--workdir", workdir] if workdir else []),
        *(a for d in devices for a in ("--device", d)), *(a for p in ports for a in ("--publish", p)),
        *(a for host, guest in mounts.items() for a in ("--volume", f"{host}:{guest}")),
        NAME, *command, input=input, capture=bool(detach))
