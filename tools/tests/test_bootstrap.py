from pathlib import Path

import pytest
from homelab import secrets
from homelab.bootstrap import blockdev, pi
from homelab.bootstrap.shell import BootstrapError

LISTING = """
<a href="raspios_lite_arm64-2025-05-13/">raspios_lite_arm64-2025-05-13/</a>
<a href="raspios_lite_arm64-2026-06-19/">raspios_lite_arm64-2026-06-19/</a>
<a href="raspios_lite_arm64-2026-01-02/">raspios_lite_arm64-2026-01-02/</a>
"""
BUILD = '<a href="2026-06-18-raspios-trixie-arm64-lite.img.xz">img</a>'


def test_the_newest_dated_build_wins(monkeypatch):
    """Dated directories sort chronologically, so lexicographic order is date order."""
    monkeypatch.setattr(
        pi, "_get", lambda url: BUILD if url.endswith("19/") else LISTING
    )
    url = pi.latest_image_url()
    assert "raspios_lite_arm64-2026-06-19/" in url
    assert url.endswith("2026-06-18-raspios-trixie-arm64-lite.img.xz")


def test_no_builds_at_all_is_an_error(monkeypatch):
    monkeypatch.setattr(pi, "_get", lambda url: "<html>nothing here</html>")
    with pytest.raises(BootstrapError, match="no raspios_lite_arm64"):
        pi.latest_image_url()


def test_a_directory_with_no_image_is_an_error(monkeypatch):
    monkeypatch.setattr(pi, "_get", lambda url: "" if url.endswith("19/") else LISTING)
    with pytest.raises(BootstrapError, match="no .img.xz"):
        pi.latest_image_url()


# --- Secret injection ----------------------------------------------------------------


def test_inject_resolves_references_whose_names_contain_spaces(monkeypatch):
    monkeypatch.setattr(secrets, "resolve", lambda *refs: [f"<{r}>" for r in refs])
    template = (
        'hashed = "op://Vault/some box/password hash"\n'
        'keys = ["op://Vault/control node SSH key/public key"]\n'
    )
    assert secrets.inject(template) == (
        'hashed = "<op://Vault/some box/password hash>"\n'
        'keys = ["<op://Vault/control node SSH key/public key>"]\n'
    )


def test_the_same_reference_twice_resolves_once(monkeypatch):
    calls = []

    def fake(*refs):
        calls.append(refs)
        return ["value"] * len(refs)

    monkeypatch.setattr(secrets, "resolve", fake)
    out = secrets.inject("a = op://v/i/f\nb = op://v/i/f\n")
    assert calls == [("op://v/i/f",)]
    assert out.count("value") == 2


# --- Refusing to overwrite the running system ----------------------------------------


@pytest.fixture
def sysfs(tmp_path, monkeypatch):
    """`zctl0` carries a device node of its own, the way an NVMe controller does - that is
    what made "my parent looks device-ish" resolve a whole disk to its controller. Root
    sits two device-mapper layers up, so `slaves` has to be followed more than one hop.
    """
    block, devices = tmp_path / "sys", tmp_path / "devices"
    block.mkdir()

    def node(path, *, partition=None, slaves=(), listed=True):
        real = devices.joinpath(*path.split("/"))
        real.mkdir(parents=True)
        (real / "dev").write_text("8:0\n")
        if partition is not None:
            (real / "partition").write_text(f"{partition}\n")
        for slave in slaves:
            (real / "slaves" / slave).mkdir(parents=True, exist_ok=True)
        if listed:
            (block / real.name).symlink_to(real)

    node("zctl0", listed=False)
    node("zctl0/zdisk0")
    node("zctl0/zdisk0/zdisk0p1", partition=1)
    node("zctl0/zdisk0/zdisk0p2", partition=2)
    node("dm7", slaves=["zdisk0p2"])
    node("dm9", slaves=["dm7"])
    node("zcard0")
    node("zcard0/zcard0p1", partition=1)

    mounts = tmp_path / "mounts"
    mounts.write_text("/dev/dm9 / xfs rw,relatime 0 0\n")

    monkeypatch.setattr(blockdev, "SYS_BLOCK", block)
    monkeypatch.setattr(blockdev, "MOUNTS", mounts)
    return block


def test_a_stacked_root_resolves_down_to_its_physical_disk(sysfs):
    """Root is an LV on a LUKS volume on a partition, so one hop down `slaves` is not
    enough - the descent has to recurse to reach a disk it can compare."""
    assert blockdev.root_disks() == {"zdisk0"}
    assert blockdev.backing_disks(Path("/dev/dm9")) == {"zdisk0"}


def test_a_whole_disk_is_not_mistaken_for_its_controller(sysfs):
    assert blockdev.disk_of(Path("/dev/zdisk0")) == "zdisk0"
    assert blockdev.backing_disks(Path("/dev/zdisk0")) == {"zdisk0"}


def test_a_partition_resolves_to_its_disk(sysfs):
    assert blockdev.disk_of(Path("/dev/zdisk0p2")) == "zdisk0"


def test_an_unrelated_card_is_not_the_running_disk(sysfs):
    assert blockdev.backing_disks(Path("/dev/zcard0")) & blockdev.root_disks() == set()


def test_the_boot_partition_is_found_by_filesystem_not_position(sysfs):
    """Nothing guarantees the boot partition comes first, so it is identified by being
    the vfat one."""
    table = "/dev/zcard0p1 ext4\n/dev/zcard0p2 vfat\n/dev/zcard0p3 swap\n"
    assert blockdev.boot_partition(Path("/dev/zcard0"), table) == Path("/dev/zcard0p2")


def test_a_device_with_no_boot_filesystem_is_an_error(sysfs):
    with pytest.raises(BootstrapError, match="no vfat partition"):
        blockdev.boot_partition(Path("/dev/zcard0"), "/dev/zcard0p1 ext4\n")


def test_an_ambiguous_boot_filesystem_is_an_error(sysfs):
    """Two of them means guessing, and guessing wrong writes the seed where nothing
    reads it."""
    table = "/dev/zcard0p1 vfat\n/dev/zcard0p2 vfat\n"
    with pytest.raises(BootstrapError, match="several vfat"):
        blockdev.boot_partition(Path("/dev/zcard0"), table)


def test_a_device_with_no_partitions_at_all_is_an_error(sysfs):
    with pytest.raises(BootstrapError, match="no vfat partition"):
        blockdev.boot_partition(Path("/dev/zcard0"), "/dev/zcard0\n")
