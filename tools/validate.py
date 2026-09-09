#!/usr/bin/env python3
"""Validate every hand-authored source-of-truth file in the repo: ipam.yaml for
addressing, and tofu/clusters/*.yaml for the cluster surface.

Both are checked against a JSON schema for shape, then for the cross-references a schema
can't express.

Run from anywhere: python3 tools/validate.py
"""
import sys

import cluster_checks
import ipam_checks


def main() -> int:
    errors = ipam_checks.check() + cluster_checks.check()
    if errors:
        for e in errors:
            print(f"FAIL: {e}", file=sys.stderr)
        return 1

    print("ipam.yaml + tofu/clusters/*.yaml: valid")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
