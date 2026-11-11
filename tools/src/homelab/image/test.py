"""The contract an image keeps with whatever boots it, checked by booting it twice as new VMs."""

import base64
import os
import re
import shutil
import sys
from pathlib import Path

from ..bootstrap.shell import BootstrapError, run, say
from . import build, builder, images as catalog, signing
from .images import Image
from .vm import LEASE_DOMAIN, Vm

DISK = "16G"
# The size an image-based VM starts at, which its owner may grow
IMAGE_BASED_DISK = "4G"
GROWN = "8G"

# The hardening a boot brings up, to compare one boot's with the next
HARDENING = """
sudo sh -c '
systemctl --failed --no-legend --plain
sysctl -a 2>/dev/null
aa-status 2>/dev/null | grep -E "profiles are"
auditctl -l
findmnt -rn -o TARGET,OPTIONS | grep -v -E "^/(proc|sys|run|dev/(hugepages|mqueue))" \\
  | sed -E "s/,(lowerdir|upperdir|workdir|redirect_dir|uuid|index|metacopy|xino)=[^,]*//g"
modprobe --showconfig | grep -E "^(install|blacklist)"
stat -c "%a %U:%G %n" /etc/shadow /etc/gshadow /etc/passwd
systemd-analyze cat-config systemd/journald.conf | grep -E "^[A-Z]"
sshd -T
'
"""
# A system extension as a machine installs one: a service it enables, as a user of its own
# The merged /usr takes its top layer's mode, which CIS's umask would make root's alone
PROBE = """
sudo sh -ec '
umask 022
. /usr/lib/os-release
x=/var/lib/extensions/probe/usr/lib
mkdir -p $x/extension-release.d $x/sysusers.d $x/systemd/system/multi-user.target.wants
printf "ID=%s\\nVERSION_ID=%s\\n" "$ID" "$VERSION_ID" > $x/extension-release.d/extension-release.probe
echo "u probe - \\"Extension probe\\"" > $x/sysusers.d/probe.conf
printf "[Service]\\nType=oneshot\\nRemainAfterExit=yes\\nUser=probe\\nExecStart=true\\n" \\
  > $x/systemd/system/probe.service
ln -s ../probe.service $x/systemd/system/multi-user.target.wants/probe.service
'
"""
# A seed's own network, as linux-vm writes a resolver's: the lease's addresses, and resolvers of its own
SEED_NETWORK = """[Match]
Name=e*

[Network]
DHCP=yes
UseDomains=yes
DNS=10.0.2.3

[DHCPv4]
UseDNS=no

[DHCPv6]
UseDNS=no

[IPv6AcceptRA]
UseDNS=no
"""
# What differs between any two boots
VOLATILE = re.compile(r"random|_seed|boot_id|ns_last_pid|fs\.(file|inode|dentry|quota)|tcp_fastopen_key"
                      r"|user_reserve_kbytes|admin_reserve_kbytes|nr_inodes=|pipe_ino=|fd=|pgrp=|stat_"
                      # What the kernel sizes by the memory it finds at boot
                      r"|threads-max|max_\w+_namespaces|(tcp|udp)_mem")


def register(verbs) -> None:
    test = verbs.add_parser(
        "test", help="boot a built image and check its contract",
        description="Boots the built image twice, as two new VMs with empty key stores, "
        "and checks what every consumer relies on.",
    )
    test.add_argument("image", help="an image in images/images.toml, e.g. debian-13-amd64-vm")
    test.add_argument("--disk", type=Path,
                      help="an image derived from this one, instead of its own build; "
                      "one that boots by its kernel boots the build's")
    build.add_sign(test)
    test.set_defaults(run=_test)


def _test(args) -> int:
    images = builder.ensure()
    base = catalog.load(images, args.image)
    out = builder.CACHE / base.name
    image = (args.disk or out / f"{base.name}.{base.format}").resolve()
    if not image.is_file():
        raise BootstrapError(f"no image at {image}: `homelab image build {base.name}`")
    out.mkdir(parents=True, exist_ok=True)
    if base.image_based:
        return _report(args.image, _image_based(images, base, image, out, args.sign))

    failures = []
    identities = []
    for boot in ("first", "second"):
        say(f"booting it as a new VM, the {boot}")
        identity, failed = _boot(base, image, out, boot)
        identities.append(identity)
        failures += failed
    failures += _check("each VM has its own host key and machine ID", identities[0] != identities[1],
                       f"{identities[0]} vs {identities[1]}")

    return _report(args.image, failures)


def _report(name: str, failures: list[str]) -> int:
    print(f"{'FAIL' if failures else 'PASS'}: {name}, {len(failures)} failed", file=sys.stderr)
    return 1 if failures else 0


def _image_based(images: Path, base: Image, image: Path, out: Path, sign: str) -> list[str]:
    """Two new VMs: the first checked, rebooted to compare its hardening, and updated A/B to a next version."""
    version, = {re.sub(rf"^{base.name}_(.*)\.efi$", r"\1", p.name) for p in (out / "updates").glob("*.efi")}
    following = f"{version}.1"
    # The build's keys, in out, sign its next version too
    work = signing.prepare(images, builder.CACHE / "signing") if sign == "kms" else None
    build.lay_out(images, base, out, following, work)
    builder.run_in({out: "/out"}, "sh", "-ec", "rm -rf /out/next\ncp -a /out/updates /out/next")
    build.updates(base, out, following, work, into="/out/next")

    failures = []
    identities = []
    for boot in ("first", "second"):
        say(f"booting it as a new VM, the {boot}")
        work = _fresh(out)
        vm = Vm(work, "test", base.boot, mounts={image.parent: "/image:ro", out / "next": "/updates:ro"},
                 log=out / f"{boot}.serial.log",
                 credentials={"network.network.50-seed": SEED_NETWORK} if boot == "second" else None)
        try:
            builder.run_in(vm.mounts, "qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2",
                           "-b", f"/image/{image.name}", "/work/disk.qcow2", IMAGE_BASED_DISK)
            vm.start("disk.qcow2")
            vm.wait_for_ssh()
            vm.ssh("systemctl is-system-running --wait >/dev/null || true")
            host_key = vm.ssh("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub").split()[1]
            identities.append((host_key, vm.ssh("cat /etc/machine-id").strip()))
            if boot == "first":
                failures += _contract_image_based(base, vm, host_key)
                failures += _rebooted(vm)
                failures += _extended(vm)
                failures += _updated(vm, base, following)
                failures += _grown(vm)
            else:
                failures += _seeded_network(vm)
        finally:
            vm.remove()
            # What went wrong, once the VM's gone
            builder.run_in({out: "/out"}, "sh", "-c", f"cp /out/test/serial.log /out/{boot}.serial.log; rm -rf /out/test")
    return failures + _check("each VM has its own host key and machine ID", identities[0] != identities[1],
                             f"{identities[0]} vs {identities[1]}")


def _fresh(out: Path) -> Path:
    work = out / "test"
    if work.exists():
        builder.run_in({out: "/out"}, "rm", "-rf", "/out/test")
    work.mkdir()
    return work


def _contract_image_based(base: Image, vm: Vm, host_key: str) -> list[str]:
    hostname = vm.ssh("hostname").strip()
    failed = _check("its hostname is the seed's", hostname == "test", hostname)
    state = vm.ssh("systemctl is-system-running || true").strip()
    failed += _check("it's running, with no unit failed", state == "running",
                     state + "\n" + vm.ssh("systemctl --failed --no-legend --plain || true"))
    status = vm.ssh("sudo bootctl status 2>/dev/null || true")
    failed += _check("Secure Boot enforces, in user mode", "Secure Boot: enabled (user)" in status, status)
    failed += _check("it booted a measured UKI", "Measured UKI: yes" in status, status)

    sshd = vm.ssh("sudo sshd -T").splitlines()
    for setting in ("passwordauthentication no", "kbdinteractiveauthentication no"):
        failed += _check(f"sshd: {setting}", setting in sshd, "")

    mounts = {target: (source, set(options.split(","))) for target, source, options in
              (line.split() for line in vm.ssh("findmnt -rno TARGET,SOURCE,OPTIONS").splitlines())}
    usr = mounts.get("/usr", ("", set()))
    failed += _check("/usr is verity's, read-only", usr[0] == "/dev/mapper/usr" and "ro" in usr[1], str(usr))
    failed += _check("root is encrypted", mounts.get("/", ("",))[0] == "/dev/mapper/root", str(mounts.get("/")))
    for target, wanted in {"/var": {"nodev", "nosuid"}, "/home": {"nodev", "nosuid"},
                           "/var/tmp": {"nodev", "nosuid", "noexec"}, "/var/log": {"nodev", "nosuid", "noexec"},
                           "/var/log/audit": {"nodev", "nosuid", "noexec"}}.items():
        source, options = mounts.get(target, ("not mounted", set()))
        failed += _check(f"{target} is encrypted, with {','.join(sorted(wanted))}",
                         source.startswith("/dev/mapper/") and wanted <= options, f"{source} {sorted(options)}")
    failed += _tmpfs(vm)
    failed += _check("/etc is the image's, under the machine's own", "base" in vm.ssh("systemd-confext status"), "")

    var = vm.ssh("sudo sh -c 'blkid -p -o value -s PART_ENTRY_UUID"
                 " $(cryptsetup status var | sed -n \"s/ *device: *//p\")'")
    wanted = vm.ssh("systemd-id128 -u --app-specific=4d21b016-b534-45c2-a9fb-5c16e091fd2d machine-id")
    failed += _check("/var's partition is bound to the machine-id", var.strip() == wanted.strip(), f"{var} vs {wanted}")
    root_var = vm.ssh("sudo sh -c 'mkdir -p /run/root && mount --bind -o ro / /run/root && lsattr -d /run/root/var"
                      " && ls -A /run/root/var; umount /run/root'")
    failed += _check("root's /var is an empty, immutable mount point",
                     root_var.split()[0].count("i") == 1 and len(root_var.split()) == 2, root_var)
    var_bytes = int(vm.ssh("df --output=size -B1 /var | tail -1"))
    failed += _check(f"/var has 1G of a {IMAGE_BASED_DISK} disk", var_bytes > 1 << 30, f"{var_bytes} bytes")
    journal = vm.ssh("sudo journalctl --header -n0 2>/dev/null | grep -m1 'File path' || true")
    failed += _check("the journal is on disk from the first boot", "/var/log/journal/" in journal, journal)

    domains = vm.ssh("resolvectl domain")
    failed += _check("it searches the lease's domains", LEASE_DOMAIN in domains, domains)

    release = vm.ssh("cat /usr/lib/homelab/image-release")
    failed += _check("the release file names the image", f"IMAGE_ID={base.name}\n" in release, release)
    url = os.environ.get("IMAGES_UPDATE_URL") or "https://images.invalid"
    paths = set(vm.ssh("grep -h '^Path=http' /usr/lib/sysupdate.d/*.transfer").split())
    failed += _check(f"sysupdate reads {url}'s stable channel", paths == {f"Path={url}/%M/stable/"}, str(paths))
    if "qemu-guest-agent" in base.packages:
        failed += _agent(vm, host_key)
    return failed


def _seeded_network(vm: Vm) -> list[str]:
    """A seed's network, taken before udev finds the drive that holds it"""
    status = vm.ssh("networkctl status --no-pager $(ip -o route show default | awk '{print $5; exit}')")
    return _check("networkd took the seed's network from first boot", "/run/systemd/network/50-seed.network" in status,
                  status)


def _hardening(vm: Vm) -> list[str]:
    return sorted(line for line in vm.ssh(HARDENING).splitlines() if not VOLATILE.search(line))


def _rebooted(vm: Vm) -> list[str]:
    before = _hardening(vm)
    vm.reboot()
    after = _hardening(vm)
    changed = sorted(set(before) ^ set(after))
    failed = _check("the second boot is hardened as the first was", not changed, "\n".join(changed))
    boots = len(vm.ssh("sudo journalctl --list-boots --no-pager").splitlines()[1:])
    return failed + _check("the journal kept both boots", boots == 2, f"{boots} boots")


def _extended(vm: Vm) -> list[str]:
    """A system extension in /var, merged at boot: what it brings is there, and what it enables runs. It stays
    merged through the update that follows."""
    vm.ssh(PROBE)
    vm.reboot()
    vm.ssh("systemctl is-system-running --wait >/dev/null || true")
    merged = vm.ssh("systemd-sysext list --no-legend; systemd-sysext status --no-pager")
    failed = _check("a system extension in /var is merged at boot", "probe" in merged, merged)
    user = vm.ssh("getent passwd probe || true").strip()
    failed += _check("its sysusers user exists", user.startswith("probe:"), user)
    state = vm.ssh("systemctl is-active probe.service || true").strip()
    return failed + _check("the service it enables ran", state == "active",
                           vm.ssh("systemctl status --no-pager probe.service 2>&1 || true"))


def _updated(vm: Vm, base: Image, version: str) -> list[str]:
    url = vm.serve("/updates")
    vm.ssh("sudo sh -c 'mkdir -p /etc/sysupdate.d && for f in /usr/lib/sysupdate.d/*.transfer; do"
           f" sed \"s#^Path=http.*#Path={url}#\" $f > /etc/sysupdate.d/$(basename $f); done"
           " && systemctl start systemd-sysupdate-update.service"
           " && while systemctl is-active -q systemd-sysupdate-update.service; do sleep 2; done'")
    installed = vm.ssh("sudo journalctl -u systemd-sysupdate-update -o cat --no-pager"
                       " | grep -c 'Successfully installed update' || true")
    failed = _check("sysupdate installed the next version", installed.strip() == "1",
                    vm.ssh("sudo journalctl -u systemd-sysupdate-update -o cat --no-pager | tail -5"))
    vm.reboot()
    state = vm.ssh("systemctl is-system-running --wait || true").strip()
    failed += _check("it's running after the update", state == "running", state)
    running = vm.ssh(". /usr/lib/os-release && echo $IMAGE_VERSION").strip()
    failed += _check(f"it booted {version}", running == version, running)
    entries = vm.ssh("sudo ls /boot/EFI/Linux")
    return failed + _check(f"{version} was blessed", f"{base.name}_{version}.efi" in entries.split(), entries)


def _boot(base: Image, image: Path, out: Path, boot: str) -> tuple[tuple[str, str], list[str]]:
    work = out / "test"
    if work.exists():
        builder.run_in({out: "/out"}, "rm", "-rf", "/out/test")
    work.mkdir()
    vm = Vm(work, "test", base.boot, mounts={image.parent: "/image:ro"}, log=out / f"{boot}.serial.log")
    try:
        # A larger disk than the image, to show /var takes it
        if image.suffix == ".qcow2":
            disk = "disk.qcow2"
            builder.run_in(vm.mounts, "qemu-img", "create", "-q", "-f", "qcow2", "-F", "qcow2",
                           "-b", f"/image/{image.name}", f"/work/{disk}", DISK)
        else:
            disk = "disk.raw"
            run("cp", "--sparse=always", str(image), str(work / disk))
            run("truncate", "--size", DISK, str(work / disk))
        if base.boot.firmware:
            vm.start(disk)
        else:
            for name in ("vmlinuz", "initrd"):
                shutil.copy(out / f"{base.name}.{name}", work / name)
            vm.start(disk, kernel="vmlinuz", initrd="initrd")
        vm.wait_for_ssh()
        cloud_init = vm.ssh("cloud-init status --wait --long || true")
        vm.ssh("systemctl is-system-running --wait >/dev/null || true")
        host_key = vm.ssh("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub").split()[1]
        identity = (host_key, vm.ssh("cat /etc/machine-id").strip())
        return identity, (_contract(base, vm, host_key, cloud_init) if boot == "first" else [])
    finally:
        vm.remove()
        # What went wrong, once the VM's gone
        builder.run_in({out: "/out"}, "sh", "-c", f"cp /out/test/serial.log /out/{boot}.serial.log; rm -rf /out/test")


def _contract(base: Image, vm: Vm, host_key: str, cloud_init: str) -> list[str]:
    failed = _check("cloud-init took the seed cleanly", "status: done" in cloud_init
                    and "errors: []" in cloud_init and "recoverable_errors: {}" in cloud_init, cloud_init)
    state = vm.ssh("systemctl is-system-running || true").strip()
    failed += _check("it's running, with no unit failed", state == "running",
                     state + "\n" + vm.ssh("systemctl --failed --no-legend --plain || true"))
    if base.boot.firmware:
        failed += _secure_boot(vm)

    sshd = vm.ssh("sudo sshd -T").splitlines()
    for setting in ("passwordauthentication no", "kbdinteractiveauthentication no"):
        failed += _check(f"sshd: {setting}", setting in sshd, "")

    mounted = dict(line.split(maxsplit=1) for line in vm.ssh("findmnt -rno TARGET,OPTIONS").splitlines())
    for line in base.file("/etc/fstab").read_text().splitlines():
        _, target, _, options, *_ = line.split()
        wanted = {o for o in options.split(",") if o in ("nodev", "nosuid", "noexec")}
        have = set(mounted.get(target, "").split(","))
        failed += _check(f"{target} mounted with {','.join(sorted(wanted)) or 'its defaults'}",
                         target in mounted and wanted <= have, mounted.get(target, "not mounted"))
    failed += _tmpfs(vm)
    var_bytes = int(vm.ssh("df --output=size -B1 /var | tail -1"))
    failed += _check(f"/var grew into a {DISK} disk", var_bytes > 4 << 30, f"{var_bytes} bytes")

    failed += _check("apt takes the recipe's sources",
                     vm.ssh(f"cat {base.sources}") == base.file(base.sources).read_text(), "")
    release = vm.ssh("cat /usr/lib/homelab/image-release")
    failed += _check("the release file names the image", f"IMAGE_ID={base.name}\n" in release, release)
    if "qemu-guest-agent" in base.packages:
        failed += _agent(vm, host_key)
    return failed


def _tmpfs(vm: Vm) -> list[str]:
    """CIS's options on the tmpfs mounts, which no disk layout states."""
    failed = []
    mounts = dict(line.split(maxsplit=1) for line in vm.ssh("findmnt -rno TARGET,OPTIONS").splitlines())
    for target in ("/tmp", "/dev/shm"):
        options = set(mounts.get(target, "").split(","))
        failed += _check(f"{target} has nodev,nosuid,noexec", {"nodev", "nosuid", "noexec"} <= options,
                         mounts.get(target, "not mounted"))
    return failed


def _secure_boot(vm: Vm) -> list[str]:
    status = vm.ssh("sudo bootctl status 2>/dev/null")
    failed = _check("Secure Boot enforces, in user mode", "Secure Boot: enabled (user)" in status, status)
    for var, holds in (("PK", "homelab Secure Boot PK"), ("KEK", "homelab Secure Boot KEK"),
                       ("db", "Debian Secure Boot CA"), ("db", "homelab images db")):
        found = vm.ssh(f"sudo grep -c '{holds}' /sys/firmware/efi/efivars/{var}-* || true").strip()
        failed += _check(f"{var} enrolled from the image holds {holds}", found == "1", found)
    return failed


def _agent(vm: Vm, host_key: str) -> list[str]:
    opened, *_ = vm.agent({"execute": "guest-file-open",
                           "arguments": {"path": "/etc/ssh/ssh_host_ed25519_key.pub"}})
    handle = opened.get("return")
    read, _ = vm.agent({"execute": "guest-file-read", "arguments": {"handle": handle}},
                       {"execute": "guest-file-close", "arguments": {"handle": handle}})
    key = base64.b64decode(read.get("return", {}).get("buf-b64", "")).decode()
    pinned = vm.ssh("ssh-keygen -lf -", input=key).split()[1:2] if key else []
    failed = _check("the guest agent reads the host key a pin checks", pinned == [host_key], key)
    refused, = vm.agent({"execute": "guest-exec", "arguments": {"path": "/bin/true"}})
    return failed + _check("the guest agent refuses guest-exec", "error" in refused, str(refused))


def _check(what: str, ok: bool, detail: str) -> list[str]:
    print(f"{'ok  ' if ok else 'FAIL'} {what}" + ("" if ok else f": {detail.strip()[:300]}"), flush=True)
    return [] if ok else [what]


def _grown(vm: Vm) -> list[str]:
    """Its owner grows its disk, and /var takes what's added."""
    before = int(vm.ssh("df --output=size -B1 /var | tail -1"))
    vm.stop()
    builder.run_in(vm.mounts, "qemu-img", "resize", "-q", "/work/disk.qcow2", GROWN)
    vm.start("disk.qcow2")
    vm.wait_for_ssh()
    state = vm.ssh("systemctl is-system-running --wait || true").strip()
    after = int(vm.ssh("df --output=size -B1 /var | tail -1"))
    failed = _check(f"/var grew when the disk grew to {GROWN}", after > before + (3 << 30), f"{before} to {after}")
    return failed + _check("it's running on the grown disk", state == "running", state)
