from pathlib import Path

from . import unifi
from .base import Rendered

__all__ = ["Plan", "plan"]


class Plan:
    def __init__(self, rendered: Rendered, stale: list[Path]) -> None:
        self.rendered = rendered
        self.stale = stale

    def __len__(self) -> int:
        return len(self.rendered)

    def drift(self) -> list[Path]:
        changed = [
            path
            for path, body in sorted(self.rendered.items())
            if not path.exists() or path.read_text() != body
        ]
        return changed + self.stale

    def write(self) -> list[tuple[str, Path]]:
        actions = []
        for path, body in sorted(self.rendered.items()):
            if path.exists() and path.read_text() == body:
                continue
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(body)
            actions.append(("wrote", path))

        for path in self.stale:
            path.unlink()
            actions.append(("removed", path))

        return actions


def plan(repo, ipam) -> Plan:
    rendered = unifi.render(repo, ipam)
    return Plan(rendered, unifi.stale(repo, rendered))
