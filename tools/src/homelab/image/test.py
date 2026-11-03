"""The contract an image keeps with whatever boots it, checked by booting it twice as new VMs."""

import base64
import shutil
import sys
from pathlib import Path

from ..bootstrap.shell import BootstrapError, run, say
from . import builder, profile
from .profile import Profile
from .vm import Vm

DISK = "16G"


def register(verbs) -> None:
    test = verbs.add_parser(
        "test", help="boot a built image and check its contract",
        description="Boots the profile's built image twice, as two new VMs with empty key stores, "
        "and checks what every consumer relies on.",
    )
    test.add_argument("profile", help="a directory in images/, e.g. debian-13-amd64-vm")
    test.add_argument("--image", type=Path,
                      help="an image derived from the profile's, instead of the profile's own build; "
                      "one that boots by its kernel boots the build's")
    test.set_defaults(run=_test)


def _test(args) -> int:
    images = builder.ensure()
    base = profile.load(images, args.profile)
    out = builder.CACHE / base.name
    image = (args.image or out / f"{base.name}.{base.format}").resolve()
    if not image.is_file():
        raise BootstrapError(f"no image at {image}: `homelab image build {base.name}`")
    out.mkdir(parents=True, exist_ok=True)

    failures = []
    identities = []
    for boot in ("first", "second"):
        say(f"booting it as a new VM, the {boot}")
        identity, failed = _boot(base, image, out, check=boot == "first")
        identities.append(identity)
        failures += failed
    failures += _check("each VM has its own host key and machine ID", identities[0] != identities[1],
                       f"{identities[0]} vs {identities[1]}")

    print(f"{'FAIL' if failures else 'PASS'}: {args.profile}, {len(failures)} failed", file=sys.stderr)
    return 1 if failures else 0


def _boot(base: Profile, image: Path, out: Path, check: bool) -> tuple[tuple[str, str], list[str]]:
    work = out / "test"
    if work.exists():
        builder.run_in({out: "/out"}, "rm", "-rf", "/out/test")
    work.mkdir()
    vm = Vm(work, "test", base, mounts={image.parent: "/image:ro"})
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
        if base.firmware:
            vm.start(disk)
        else:
            for name in ("vmlinuz", "initrd"):
                shutil.copy(out / name, work / name)
            vm.start(disk, kernel="vmlinuz", initrd="initrd")
        vm.wait_for_ssh()
        cloud_init = vm.ssh("cloud-init status --wait --long || true")
        vm.ssh("systemctl is-system-running --wait >/dev/null || true")
        host_key = vm.ssh("ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub").split()[1]
        identity = (host_key, vm.ssh("cat /etc/machine-id").strip())
        return identity, (_contract(base, vm, host_key, cloud_init) if check else [])
    finally:
        vm.remove()
        builder.run_in({out: "/out"}, "rm", "-rf", "/out/test")


def _contract(base: Profile, vm: Vm, host_key: str, cloud_init: str) -> list[str]:
    recipe = base.recipe
    failed = _check("cloud-init took the seed cleanly", "status: done" in cloud_init
                    and "errors: []" in cloud_init and "recoverable_errors: {}" in cloud_init, cloud_init)
    if base.firmware:
        failed += _secure_boot(vm)

    sshd = vm.ssh("sudo sshd -T").splitlines()
    for setting in ("passwordauthentication no", "kbdinteractiveauthentication no"):
        failed += _check(f"sshd: {setting}", setting in sshd, "")

    mounted = dict(line.split(maxsplit=1) for line in vm.ssh("findmnt -rno TARGET,OPTIONS").splitlines())
    for line in (recipe / "mkosi.extra/etc/fstab").read_text().splitlines():
        _, target, _, options, *_ = line.split()
        wanted = {o for o in options.split(",") if o in ("nodev", "nosuid", "noexec")}
        have = set(mounted.get(target, "").split(","))
        failed += _check(f"{target} mounted with {','.join(sorted(wanted)) or 'its defaults'}",
                         target in mounted and wanted <= have, mounted.get(target, "not mounted"))
    var_bytes = int(vm.ssh("df --output=size -B1 /var | tail -1"))
    failed += _check(f"/var grew into a {DISK} disk", var_bytes > 4 << 30, f"{var_bytes} bytes")

    failed += _check("apt takes the recipe's sources", vm.ssh(f"cat {base.sources}") ==
                     (recipe / "mkosi.extra" / base.sources.lstrip("/")).read_text(), "")
    release = vm.ssh("cat /usr/lib/homelab/image-release")
    failed += _check("the release file names the profile", f"IMAGE_ID={recipe.name}\n" in release, release)
    if "qemu-guest-agent" in base.packages:
        failed += _agent(vm, host_key)
    return failed


def _secure_boot(vm: Vm) -> list[str]:
    status = vm.ssh("sudo bootctl status 2>/dev/null")
    failed = _check("Secure Boot enforces, in user mode", "Secure Boot: enabled (user)" in status, status)
    for var, holds in {"PK": "homelab Secure Boot PK", "KEK": "homelab Secure Boot KEK",
                       "db": "Debian Secure Boot CA"}.items():
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
