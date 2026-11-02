"""An image of your own, from one of ours: boot it, provision it, seal it."""

import os
import shutil
import subprocess
import uuid
from pathlib import Path

from ..bootstrap.shell import BootstrapError, say
from . import builder
from .vm import Vm


def register(verbs) -> None:
    derive = verbs.add_parser(
        "derive", help="derive an image of your own from a built one",
        description="Boots a base image as a new VM, provisions it with your playbook or script, "
        "seals it with the base's own seal, and writes the result as a qcow2.",
    )
    derive.add_argument("base", type=Path, help="the base image, a .qcow2")
    derive.add_argument("output", type=Path, help="where to write the derived .qcow2")
    how = derive.add_mutually_exclusive_group(required=True)
    how.add_argument("--playbook", type=Path,
                     help="run against the VM as host `build`, from the playbook's own directory")
    how.add_argument("--script", type=Path, help="run on the VM as root, by sh")
    derive.set_defaults(run=_derive)


def _derive(args) -> int:
    provisioner = (args.playbook or args.script).resolve()
    if not args.base.is_file() or not provisioner.is_file():
        raise BootstrapError(f"no such file: {args.base if not args.base.is_file() else provisioner}")
    builder.ensure()
    work = builder.CACHE / f"derive-{uuid.uuid4().hex[:12]}"
    work.mkdir(parents=True)

    vm = Vm(work, "build")
    try:
        shutil.copy(args.base, work / "disk.qcow2")
        say("booting the base")
        vm.start("disk.qcow2")
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

    say("converting it")
    builder.run_in({work: "/work"}, "qemu-img", "convert", "-O", "qcow2", "-c",
                   "/work/disk.qcow2", "/work/derived.qcow2")
    builder.run_in({work: "/work"}, "chown", f"{os.getuid()}:{os.getgid()}", "/work/derived.qcow2")
    shutil.move(work / "derived.qcow2", args.output)
    builder.run_in({work.parent: "/cache"}, "rm", "-rf", f"/cache/{work.name}")
    say(f"derived {args.output}")
    return 0
