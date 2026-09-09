import json
from functools import cache
from pathlib import Path

from jsonschema import Draft202012Validator


@cache
def _validator(path: Path) -> Draft202012Validator:
    return Draft202012Validator(json.loads(path.read_text()))


def errors(schema_path: Path, data, prefix: str) -> list[str]:
    """Schema violations as flat strings, each naming the key that failed."""
    return [
        f"{prefix}: {'.'.join(str(p) for p in err.path) or '<root>'}: {err.message}"
        for err in sorted(_validator(schema_path).iter_errors(data), key=str)
    ]
