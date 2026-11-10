import pytest
from homelab.bootstrap.shell import BootstrapError
from homelab.image.snapshot import pinned

SNAPSHOT = "20261110T022553Z"
VENDOR = ("Types: deb\nURIs: http://repo.powerdns.com/debian\nSuites: trixie-rec-54\nComponents: main\n"
          "Signed-By: /etc/apt/keyrings/powerdns.asc\n")


def test_debian_archives_are_pinned_and_nothing_else_is_let_through():
    """apt falls back to the live archive when a snapshot it follows itself can't be reached, so a source the
    build doesn't rewrite would go unpinned without a word"""
    sources = ("Types: deb\nURIs: http://deb.debian.org/debian\nSuites: forky\n\n"
               "Types: deb\nURIs: http://deb.debian.org/debian-security\nSuites: forky-security\n")
    assert pinned(sources, SNAPSHOT) == (
        f"Types: deb\nURIs: https://snapshot.debian.org/archive/debian/{SNAPSHOT}\nSuites: forky\nCheck-Valid-Until: no\n\n"
        f"Types: deb\nURIs: https://snapshot.debian.org/archive/debian-security/{SNAPSHOT}\nSuites: forky-security\n"
        "Check-Valid-Until: no\n")
    with pytest.raises(BootstrapError):
        pinned("Types: deb\nURIs: https://packages.example.org/debian\nSuites: trixie\n", SNAPSHOT)


def test_a_vendor_train_is_let_through_only_signed_by_a_key_the_build_ships():
    """A vendor's train has no snapshot, so the key it ships is all that pins it: one trusted from the system's
    keyring would take whatever that keyring signs"""
    assert pinned(VENDOR, SNAPSHOT, {"/etc/apt/keyrings/powerdns.asc"}) == VENDOR
    with pytest.raises(BootstrapError):
        pinned(VENDOR.replace("/etc/apt/keyrings/powerdns.asc", "/usr/share/keyrings/debian-archive-keyring.gpg"),
               SNAPSHOT, {"/etc/apt/keyrings/powerdns.asc"})
