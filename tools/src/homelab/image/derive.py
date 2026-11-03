"""An image of your own, from one of ours: boot it, provision it, seal it."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ..bootstrap.shell import BootstrapError, run, say
from . import builder, profile
from .vm import Vm


def register(verbs) -> None:
    derive = verbs.add_parser(
        "derive", help="derive an image of your own from a built one",
        description="Boots a base image as a new VM, provisions it with your playbook or script, "
        "seals it with the base's own seal, and writes the result in the base's format.",
    )
    derive.add_argument("profile", help="the profile the base was built from, e.g. debian-13-amd64-vm")
    derive.add_argument("base", type=Path, help="the base image, in its profile's format")
    derive.add_argument("output", type=Path, help="where to write the derived image, in the same format")
    how = derive.add_mutually_exclusive_group(required=True)
    how.add_argument("--playbook", type=Path,
                     help="run against the VM as host `build`, from the playbook's own directory")
    how.add_argument("--script", type=Path, help="run on the VM as root, by sh")
    derive.set_defaults(run=_derive)


def _derive(args) -> int:
    provisioner = (args.playbook or args.script).resolve()
    if not args.base.is_file() or not provisioner.is_file():
        raise BootstrapError(f"no such file: {args.base if not args.base.is_file() else provisioner}")
    base = profile.load(builder.ensure(), args.profile)
    built = builder.CACHE / base.name
    if not base.firmware and not (built / "vmlinuz").is_file():
        raise BootstrapError(f"{base.name} boots by its kernel here: `homelab image build {base.name}` first")
    work = builder.CACHE / f"derive-{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True)
    disk = f"disk{args.base.suffix}"

    vm = Vm(work, "build", base)
    try:
        run("cp", "--sparse=always", str(args.base), str(work / disk))
        say("booting the base")
        if base.firmware:
            vm.start(disk)
        else:
            for name in ("vmlinuz", "initrd"):
                shutil.copy(built / name, work / name)
            vm.start(disk, kernel="vmlinuz", initrd="initrd")
        vm.wait_for_ssh()
        vm.ssh("cloud-init status --wait >/dev/null || true")

        say(f"provisioning it with {provisioner.name}")
        if args.playbook:
            subprocess.run(["ansible-playbook", "-i", str(vm.inventory()), str(provisioner)],
                           cwd=provisioner.parent, env=os.environ, check=True)
        else:
            vm.ssh("sudo sh -s", input=provisioner.read_text())

        say("sealing it")
        vm.seal()
    finally:
        vm.remove()

    derived = f"derived{args.base.suffix}"
    if base.format == "qcow2":
        say("converting it")
        builder.run_in({work: "/work"}, "qemu-img", "convert", "-O", "qcow2", "-c", f"/work/{disk}", f"/work/{derived}")
    else:
        builder.run_in({work: "/work"}, "mv", f"/work/{disk}", f"/work/{derived}")
    builder.run_in({work: "/work"}, "chown", f"{os.getuid()}:{os.getgid()}", f"/work/{derived}")
    shutil.move(work / derived, args.output)
    builder.run_in({work.parent: "/cache"}, "rm", "-rf", f"/cache/{work.name}")
    say(f"derived {args.output}")
    return 0
