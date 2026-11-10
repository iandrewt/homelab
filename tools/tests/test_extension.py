from homelab.image.extension import Extension, next_version

RECURSOR = Extension(name="pdns-recursor", images=("ymir-amd64-vm",), package="pdns-recursor", packages=(),
                     snapshot=None, sandbox_trees=(), files=(), inputs=())


def locked(version: str, *packages: str) -> dict:
    return {"version": version, "ymir-amd64-vm": {"inputs": "abc", "packages": list(packages)}}


def test_only_a_moved_lock_rebuilds_and_its_version_sorts_above_the_last():
    """sysupdate installs the newest version it sees: a rebuild for a bundled dependency counts up, and a new
    upstream starts again at 1, which still sorts above every rebuild of the last"""
    was = locked("5.4.9-1", "libsodium23=1.0.18-1+deb13u1", "pdns-recursor=5.4.9-1pdns.debian13")
    assert next_version(RECURSOR, was, {"ymir-amd64-vm": was["ymir-amd64-vm"]}) is None
    fixed = locked("", "libsodium23=1.0.18-1+deb13u2", "pdns-recursor=5.4.9-1pdns.debian13")
    assert next_version(RECURSOR, was, {"ymir-amd64-vm": fixed["ymir-amd64-vm"]}) == "5.4.9-2"
    upstream = locked("", "libsodium23=1.0.18-1+deb13u2", "pdns-recursor=1:5.4.10-1pdns.debian13")
    assert next_version(RECURSOR, was, {"ymir-amd64-vm": upstream["ymir-amd64-vm"]}) == "5.4.10-1"
    assert next_version(RECURSOR, {}, {"ymir-amd64-vm": was["ymir-amd64-vm"]}) == "5.4.9-1"
