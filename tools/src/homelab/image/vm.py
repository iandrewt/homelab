"""A disk booted in qemu inside the builder, reached over SSH: how a build bakes and a test checks."""

import json
import socket
import subprocess
import time
import uuid
from pathlib import Path

import yaml

from ..bootstrap.shell import BootstrapError, run
from ..render import seed
from . import builder

OVMF = "/usr/share/OVMF/OVMF_CODE_4M.secboot.fd"
# No keys enrolled, as a Proxmox EFI disk without pre-enrolled ones: the image enrolls its own
OVMF_SETUP_MODE = "/usr/share/OVMF/OVMF_VARS_4M.fd"

# A throwaway VM's host key is never pinned; IdentitiesOnly, since CIS's MaxAuthTries is less than
# an agent's keys
SSH_OPTIONS = ("-o", "StrictHostKeyChecking=no", "-o", "UserKnownHostsFile=/dev/null", "-o", "LogLevel=ERROR",
               "-o", "ConnectTimeout=5", "-o", "IdentitiesOnly=yes")

# One JSON command per line in, one reply per line out
AGENT_CLIENT = """
import socket, sys
with socket.socket(socket.AF_UNIX) as s:
    s.connect("/work/qga.sock")
    replies = s.makefile()
    for command in sys.stdin:
        s.sendall(command.encode())
        print(replies.readline(), end="")
"""


class Vm:
    """One boot of a disk in work/, with a throwaway seed whose key the default user takes."""

    def __init__(self, work: Path, hostname: str, mounts: dict[Path, str] | None = None) -> None:
        self.work = work
        self.mounts = {work: "/work", **(mounts or {})}
        self.name = f"homelab-image-{uuid.uuid4().hex[:12]}"
        self.key = work / "ssh-key"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._seed(hostname)

    def _seed(self, hostname: str) -> None:
        self.key.unlink(missing_ok=True)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", self.name, "-f", str(self.key))
        documents = self.work / "seed"
        documents.mkdir(exist_ok=True)
        (documents / "meta-data").write_text(seed.meta_data(
            hostname, None, ssh_keys=[self.key.with_suffix(".pub").read_text().strip()],
            instance_id=self.name))
        (documents / "user-data").write_text("")
        builder.run_in({self.work: "/work"}, "xorriso", "-as", "mkisofs", "-quiet", "-output",
                       "/work/seed.iso", "-volid", "cidata", "-joliet", "-rock", "/work/seed")

    def start(self, disk: str, *, kernel: str | None = None, initrd: str | None = None,
              append: str | None = None, vars_from: str | None = None) -> None:
        """Boots work/<disk>, a .raw or .qcow2: by its kernel and initrd if given, else through
        Secure Boot firmware whose variable store starts as a copy of `vars_from`."""
        if kernel:
            boot = ["-machine", "q35", "-kernel", f"/work/{kernel}", "-initrd", f"/work/{initrd}",
                    "-append", append or ""]
        else:
            builder.run_in({self.work: "/work"}, "cp", vars_from or OVMF_SETUP_MODE, "/work/vars.fd")
            boot = ["-machine", "q35,smm=on", "-global", "driver=cfi.pflash01,property=secure,value=on",
                    "-drive", f"if=pflash,format=raw,unit=0,readonly=on,file={OVMF}",
                    "-drive", "if=pflash,format=raw,unit=1,file=/work/vars.fd"]
        builder.run_in(
            self.mounts, "qemu-system-x86_64", "-enable-kvm", "-cpu", "host", "-m", "2048",
            "-smp", "2", *boot,
            "-drive", f"file=/work/{disk},format={Path(disk).suffix.lstrip('.')},if=virtio,discard=unmap",
            "-drive", "file=/work/seed.iso,media=cdrom,readonly=on",
            "-netdev", "user,id=net,hostfwd=tcp::2222-:22", "-device", "virtio-net-pci,netdev=net",
            "-chardev", "socket,id=qga,path=/work/qga.sock,server=on,wait=off",
            "-device", "virtio-serial", "-device", "virtserialport,chardev=qga,name=org.qemu.guest_agent.0",
            "-display", "none", "-serial", "file:/work/serial.log",
            detach=self.name, devices=("/dev/kvm",), ports=(f"127.0.0.1:{self.port}:2222",))

    @property
    def ssh_args(self) -> list[str]:
        return ["-i", str(self.key), "-p", str(self.port), *SSH_OPTIONS]

    def ssh(self, command: str, *, input: str | None = None) -> str:
        return run("ssh", *self.ssh_args, "debian@127.0.0.1", command, capture=True, input=input)

    def inventory(self) -> Path:
        """An Ansible inventory of this VM alone, as host `build`."""
        path = self.work / "inventory.yml"
        path.write_text(yaml.safe_dump({"all": {"hosts": {"build": {
            "ansible_host": "127.0.0.1", "ansible_port": self.port, "ansible_user": "debian",
            "ansible_ssh_private_key_file": str(self.key),
            "ansible_ssh_common_args": " ".join(SSH_OPTIONS),
        }}}}))
        return path

    def seal(self) -> None:
        """Runs the image's seal, and waits for it to power off."""
        # Scheduled, so the seal's exit reaches us before the default user it removes is gone
        self.ssh("sudo sh -c '/usr/lib/homelab/seal && systemd-run --on-active=2 systemctl poweroff' >/dev/null")
        self.wait_until_stopped()

    def wait_for_ssh(self, timeout: int = 300) -> None:
        deadline = time.monotonic() + timeout
        while subprocess.run(["ssh", *self.ssh_args, "debian@127.0.0.1", "true"],
                             capture_output=True, check=False).returncode:
            if time.monotonic() > deadline:
                raise BootstrapError(f"no SSH in {timeout}s: see {self.work / 'serial.log'}")
            time.sleep(5)

    def wait_until_stopped(self, timeout: int = 300) -> None:
        try:
            subprocess.run(["docker", "wait", self.name], capture_output=True, timeout=timeout, check=True)
        except subprocess.TimeoutExpired:
            raise BootstrapError(f"still running after {timeout}s: see {self.work / 'serial.log'}") from None

    def agent(self, *commands: dict) -> list[dict]:
        """Each command's reply from the guest agent, through its socket as a hypervisor reaches it."""
        replies = run("docker", "exec", "--interactive", self.name, "python3", "-c", AGENT_CLIENT,
                      capture=True, input="".join(json.dumps(c) + "\n" for c in commands))
        return [json.loads(line) for line in replies.splitlines()]

    def remove(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, check=False)
