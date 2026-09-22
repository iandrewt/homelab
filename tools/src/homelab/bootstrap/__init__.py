"""Building install media: an unattended Proxmox ISO, a seeded Raspberry Pi card."""

from . import pi, proxmox

TARGETS = (pi, proxmox)

__all__ = ["TARGETS", "pi", "proxmox"]
