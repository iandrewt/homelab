"""Platform's base images: each profile in images/ built in the one pinned container."""

from . import build, derive, keys, test


def register(subparsers) -> None:
    image = subparsers.add_parser("image", help="build Platform's base images", description=__doc__)
    verbs = image.add_subparsers(dest="verb", required=True)
    build.register(verbs)
    derive.register(verbs)
    keys.register(verbs)
    test.register(verbs)
