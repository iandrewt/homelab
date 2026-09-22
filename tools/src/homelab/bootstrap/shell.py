import shutil
import subprocess
import sys


class BootstrapError(Exception):
    pass


def require(*commands: str) -> None:
    if missing := [c for c in commands if shutil.which(c) is None]:
        raise BootstrapError(f"not on PATH: {', '.join(missing)}")


def run(*command: str, capture: bool = False, **kwargs) -> str:
    result = subprocess.run(
        command, check=False, text=True, capture_output=capture, **kwargs
    )
    if result.returncode:
        detail = (result.stderr or "").strip()
        raise BootstrapError(f"{command[0]} failed{': ' + detail if detail else ''}")
    return (result.stdout or "") if capture else ""


def say(message: str) -> None:
    print(f"==> {message}", file=sys.stderr)
