import pytest
from homelab.bootstrap.shell import BootstrapError
from homelab.image.publish import merge

IMAGE = "ymir-amd64-vm"


def sums(*versions: str) -> str:
    return "".join(f"{'0' * 64}  {IMAGE}_{v}.efi\n{'1' * 64}  {IMAGE}_{v}.usr-x86-64.abc.raw\n" for v in versions)


def test_a_channel_keeps_its_newest_versions_by_number_and_prunes_the_rest():
    """sysupdate takes the newest it can see, and a version string sorts 2026.11.10 before 2026.11.9"""
    kept, pruned = merge(sums("2026.11.8", "2026.11.9"), sums("2026.11.10"), IMAGE, keep=2)
    assert kept == sums("2026.11.9", "2026.11.10")
    assert pruned == [f"{IMAGE}_2026.11.8.efi", f"{IMAGE}_2026.11.8.usr-x86-64.abc.raw"]


def test_a_channel_holds_releases_alone():
    with pytest.raises(BootstrapError):
        merge("", sums("dev"), IMAGE)
    with pytest.raises(BootstrapError):
        merge(f"{'0' * 64}  debian-13-amd64-vm_2026.11.1.efi\n", sums("2026.11.2"), IMAGE)
