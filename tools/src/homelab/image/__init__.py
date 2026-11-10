"""Platform's base images: each one images/images.toml names, built by mkosi in the one pinned container."""

from . import build, derive, keys, publish, test


def register(subparsers) -> None:
    image = subparsers.add_parser("image", help="build Platform's base images", description=__doc__)
    verbs = image.add_subparsers(dest="verb", required=True)
    build.register(verbs)
    derive.register(verbs)
    keys.register(verbs)
    publish.register(verbs)
    test.register(verbs)
