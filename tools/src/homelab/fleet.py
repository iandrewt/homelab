"""Which fleet artifact a cluster's ring runs, and the files it was built from."""

import base64
import json
import os
import re
import subprocess
import sys
import urllib.error
import urllib.request

import yaml

from . import secrets
from .repo import Repo

# The pull token while the repository is private; anonymous pulls are tried first
TOKEN_REF_ENV = "GHCR_TOKEN_REF"
TOKEN_REF = "op://Platform/GitHub Token - Flux GHCR"

MANIFEST = "application/vnd.oci.image.manifest.v1+json"
REVISION = "org.opencontainers.image.revision"


class FleetError(Exception):
    pass


def register(subparsers) -> None:
    fleet = subparsers.add_parser(
        "fleet", help="resolve a cluster's ring to an artifact", description=__doc__
    )
    actions = fleet.add_subparsers(dest="action", required=True, title="actions")

    resolve = actions.add_parser("resolve", help="print the version and revision a cluster runs")
    resolve.add_argument("cluster", help="a directory under kubernetes/fleet/")
    resolve.set_defaults(run=_resolve)

    files = actions.add_parser(
        "files",
        help="for OpenTofu's external data source: the fleet's files at the ring's revision",
        description="Reads {\"cluster\": ...} on stdin and prints one JSON object: the "
        "version, the revision, and every file under kubernetes/components and the "
        "cluster's own directory, by path under kubernetes/.",
    )
    files.set_defaults(run=_files)


def _resolve(args) -> int:
    try:
        version, revision = resolve(Repo.discover(), args.cluster)
    except FleetError as failure:
        print(failure, file=sys.stderr)
        return 1
    print(f"{version} {revision}")
    return 0


def _files(args) -> int:
    repo = Repo.discover()
    cluster = json.load(sys.stdin)["cluster"]
    try:
        version, revision = resolve(repo, cluster)
        result = {"version": version, "revision": revision}
        result |= files_at(repo, revision, ["kubernetes/components", f"kubernetes/clusters/{cluster}"])
    except FleetError as failure:
        print(failure, file=sys.stderr)
        return 1
    json.dump(result, sys.stdout)
    return 0


def resolve(repo: Repo, cluster: str) -> tuple[str, str]:
    """The newest version in the cluster's ring, and the commit it was pushed from."""
    ring = repo.root / "kubernetes" / "fleet" / cluster / "ring.yaml"
    source = next(d for d in yaml.safe_load_all(ring.read_text()) if d["kind"] == "OCIRepository")
    url = source["spec"]["url"].removeprefix("oci://")
    host, name = url.split("/", 1)

    registry = Registry(host, name)
    candidates = [t for t in registry.tags() if parse(t) and satisfies(t, source["spec"]["ref"]["semver"])]
    if not candidates:
        raise FleetError(f"nothing in {url} satisfies {cluster}'s ring: has CI pushed the fleet?")
    version = max(candidates, key=precedence)

    annotations = registry.manifest(version).get("annotations", {})
    if REVISION not in annotations:
        raise FleetError(f"{url}:{version} carries no {REVISION}")
    # `flux push artifact` writes <ref>@sha1:<commit>
    return version, annotations[REVISION].rsplit(":", 1)[-1]


def files_at(repo: Repo, revision: str, paths: list[str]) -> dict[str, str]:
    def git(*command: str) -> str:
        done = subprocess.run(["git", "-C", str(repo.root), *command], capture_output=True, text=True)
        if done.returncode:
            raise FleetError(f"git {' '.join(command)}: {done.stderr.strip()}")
        return done.stdout

    if subprocess.run(["git", "-C", str(repo.root), "cat-file", "-e", f"{revision}^{{commit}}"],
                      capture_output=True).returncode:
        git("fetch", "--quiet", "origin")

    listed = git("ls-tree", "-r", "--name-only", revision, "--", *paths).splitlines()
    return {path.removeprefix("kubernetes/"): git("show", f"{revision}:{path}") for path in listed}


class Registry:
    def __init__(self, host: str, name: str) -> None:
        self.host, self.name = host, name
        self._token: str | None = None

    def tags(self) -> list[str]:
        return self._get(f"/v2/{self.name}/tags/list").get("tags") or []

    def manifest(self, tag: str) -> dict:
        return self._get(f"/v2/{self.name}/manifests/{tag}", accept=MANIFEST)

    def _get(self, path: str, accept: str = "application/json") -> dict:
        for authenticated in (False, True):
            try:
                request = urllib.request.Request(
                    f"https://{self.host}{path}",
                    headers={"Accept": accept, "Authorization": f"Bearer {self.token(authenticated)}"},
                )
                with urllib.request.urlopen(request) as response:
                    return json.load(response)
            except urllib.error.HTTPError as error:
                if error.code not in (401, 403, 404) or authenticated:
                    raise FleetError(f"{self.host}{path}: {error}") from error
                self._token = None
        raise AssertionError("unreachable")

    def token(self, authenticated: bool) -> str:
        if self._token:
            return self._token
        request = urllib.request.Request(
            f"https://{self.host}/token?scope=repository:{self.name}:pull&service={self.host}"
        )
        if authenticated:
            reference = os.environ.get(TOKEN_REF_ENV, TOKEN_REF)
            username, password = secrets.resolve(f"{reference}/username", f"{reference}/credential")
            basic = base64.b64encode(f"{username}:{password}".encode()).decode()
            request.add_header("Authorization", f"Basic {basic}")
        with urllib.request.urlopen(request) as response:
            self._token = json.load(response)["token"]
        return self._token


# Semver precedence, as Flux orders tags. A ring is a `>=` range.
SEMVER = re.compile(r"^v?(\d+)\.(\d+)\.(\d+)(?:-([0-9A-Za-z.-]+))?(?:\+[0-9A-Za-z.-]+)?$")


def parse(version: str):
    match = SEMVER.match(version)
    if not match:
        return None
    major, minor, patch, pre = match.groups()
    return int(major), int(minor), int(patch), pre.split(".") if pre else None


def precedence(version: str):
    major, minor, patch, pre = parse(version)
    # A release outranks its pre-releases; numeric identifiers rank below alphanumeric ones
    identifiers = [(0, int(i), "") if i.isdigit() else (1, 0, i) for i in pre] if pre else []
    return major, minor, patch, pre is None, identifiers


def satisfies(version: str, constraint: str) -> bool:
    match = re.fullmatch(r"\s*>=\s*(\S+)\s*", constraint)
    if not match or not parse(match[1]):
        raise FleetError(f"a ring is a `>=` range, not {constraint!r}")
    floor = match[1]
    # As in Masterminds/semver: pre-releases count only if the range names one
    if parse(version)[3] and not parse(floor)[3]:
        return False
    return precedence(version) >= precedence(floor)
