"""Where the hand-authored files live, and where derived ones land."""

from pathlib import Path

MARKER = Path("pyproject.toml")


class Repo:
    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()

    @classmethod
    def discover(cls, start: Path | None = None) -> Repo:
        base = Path(start or Path.cwd()).resolve()
        for candidate in (base, *base.parents):
            if (candidate / MARKER).exists():
                return cls(candidate)
        raise SystemExit(
            f"not in the homelab repo: no {MARKER} in any parent of {base}"
        )

    @property
    def clusters(self) -> Path:
        return self.root / "tofu" / "clusters"

    @property
    def cluster_schema(self) -> Path:
        return self.clusters / "schema.json"

    @property
    def unifi(self) -> Path:
        return self.root / "unifi" / "generated"

    @property
    def octodns_config(self) -> Path:
        return self.root / "octodns" / "config.yaml"

    def cluster_files(self) -> list[Path]:
        return sorted(self.clusters.glob("*.yaml"))

    def relative(self, path: Path) -> str:
        return str(path.relative_to(self.root))
