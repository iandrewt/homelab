from pathlib import Path

from .shell import BootstrapError

SYS_BLOCK = Path("/sys/class/block")
MOUNTS = Path("/proc/self/mounts")


def _node(device: Path) -> Path:
    """sysfs knows /dev/mapper/root as dm-0, so follow the symlink before looking."""
    return SYS_BLOCK / device.resolve().name


def disk_of(device: Path) -> str:
    """The whole disk a partition belongs to, or the name itself if it is one."""
    node = _node(device)
    if not node.exists():
        return device.resolve().name
    if (node / "partition").exists():
        return node.resolve().parent.name
    return node.resolve().name


def backing_disks(device: Path) -> set[str]:
    """Every physical disk under a device, following device-mapper and RAID down"""
    return _descend(_node(device).resolve().name)


def _descend(name: str, seen: set[str] | None = None) -> set[str]:
    seen = seen if seen is not None else set()
    if name in seen:
        return set()
    seen.add(name)

    slaves = SYS_BLOCK / name / "slaves"
    if slaves.is_dir() and (children := list(slaves.iterdir())):
        return {disk for child in children for disk in _descend(child.name, seen)}
    return {disk_of(Path("/dev") / name)}


def root_disks() -> set[str]:
    """The disks this system is running from, so they can be refused as a target."""
    for line in MOUNTS.read_text().splitlines():
        source, mountpoint = line.split()[:2]
        if mountpoint == "/" and source.startswith("/dev/"):
            return backing_disks(Path(source))
    return set()


BOOT_FILESYSTEM = "vfat"
ROOT_FILESYSTEM = "ext4"


def boot_partition(device: Path, table: str) -> Path:
    return _partition(device, table, BOOT_FILESYSTEM, "holds the seed")


def root_partition(device: Path, table: str) -> Path:
    return _partition(device, table, ROOT_FILESYSTEM, "is the root")


def _partition(device: Path, table: str, filesystem: str, role: str) -> Path:
    """A partition found by its filesystem rather than by position.

    `lsblk -no PATH,FSTYPE` output. Nothing guarantees an image puts its boot partition
    first, or that partition 1 is even the one cloud-init reads.
    """
    candidates = [
        Path(fields[0])
        for line in table.splitlines()
        if len(fields := line.split()) == 2 and fields[1] == filesystem
    ]
    if not candidates:
        raise BootstrapError(
            f"no {filesystem} partition on {device} - did the write finish, and is "
            "this the image you meant?"
        )
    if len(candidates) > 1:
        raise BootstrapError(
            f"{device} has several {filesystem} partitions "
            f"({', '.join(str(c) for c in candidates)}) - can't tell which {role}"
        )
    return candidates[0]
