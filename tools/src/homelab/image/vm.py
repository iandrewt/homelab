"""A disk booted in qemu inside the builder, reached over SSH: how a build bakes and a test checks."""

import json
import os
import platform
import shlex
import socket
import subprocess
import time
import uuid
from dataclasses import dataclass
from pathlib import Path

import yaml

from ..bootstrap.shell import BootstrapError, run
from ..render import seed
from . import builder


@dataclass(frozen=True)
class Boot:
    """How qemu boots a disk, and where its seed is: "drive", a cidata volume of its own, or "partition",
    the disk's own labelled CIDATA."""
    architecture: str
    firmware: bool
    seed: str
    # What reads a drive or partition: cloud-init, or the image itself, taking user-data as credentials
    cloud_init: bool = True
    # By its kernel, the command line it's booted with
    cmdline: str = ""
    tpm: bool = False


@dataclass(frozen=True)
class Machine:
    qemu: str
    machine: str
    # As uname names this architecture, for whether the host can run it under KVM
    native: str
    firmware: tuple[str, ...] = ()


MACHINES = {
    "x86-64": Machine("qemu-system-x86_64", "q35", "x86_64", firmware=(
        "-machine", "q35,smm=on", "-global", "driver=cfi.pflash01,property=secure,value=on",
        "-drive", "if=pflash,format=raw,unit=0,readonly=on,file=/usr/share/OVMF/OVMF_CODE_4M.secboot.fd",
        "-drive", "if=pflash,format=raw,unit=1,file=/work/vars.fd")),
    "arm64": Machine("qemu-system-aarch64", "virt", "aarch64"),
}
# No keys enrolled, as a Proxmox EFI disk without pre-enrolled ones: the image enrolls its own
# The search list its lease hands out, as a site's router does
LEASE_DOMAIN = "lease.test"

OVMF_SETUP_MODE = "/usr/share/OVMF/OVMF_VARS_4M.fd"

# Into the partition whose FAT filesystem is labelled CIDATA, as a card is seeded
SEED_PARTITION = """
set -eu
for start in $(sfdisk --json "$1" | python3 -c 'import json, sys
for p in json.load(sys.stdin)["partitiontable"]["partitions"]: print(p["start"])'); do
  if mlabel -s -i "$1@@$((start * 512))" :: 2>/dev/null | grep -qx ' Volume label is CIDATA *'; then
    exec mcopy -o -i "$1@@$((start * 512))" /work/seed/meta-data /work/seed/user-data ::/
  fi
done
echo "no partition of $1 is labelled CIDATA" >&2
exit 1
"""

# ssh's ProxyCommand to a VM's sshd over vsock, by its CID, which ssh would take for an address
VSOCK_PROXY = """\
import os, socket, sys, threading
sock = socket.socket(socket.AF_VSOCK, socket.SOCK_STREAM)
sock.connect((int(sys.argv[1]), 22))
def send():
    while data := os.read(0, 65536):
        sock.sendall(data)
    sock.shutdown(socket.SHUT_WR)
threading.Thread(target=send, daemon=True).start()
while data := sock.recv(65536):
    os.write(1, data)
"""

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

    def __init__(self, work: Path, hostname: str, boot: Boot, mounts: dict[Path, str] | None = None,
                 log: Path | None = None, credentials: dict[str, str] | None = None) -> None:
        self.work = work
        # Where its console is kept once it's removed, if not in work/
        self.log = log or work / "serial.log"
        self.boot = boot
        self.machine = MACHINES[boot.architecture]
        self.kvm = platform.machine() == self.machine.native and os.path.exists("/dev/kvm")
        # Emulated, everything takes longer
        self.patience = 1 if self.kvm else 6
        self.mounts = {work: "/work", **(mounts or {})}
        self.name = f"homelab-image-{uuid.uuid4().hex[:12]}"
        # Its SMBIOS UUID, as Proxmox gives each VM one, which an image-based machine's ID is
        self.uuid = str(uuid.uuid4())
        self.key = work / "ssh-key"
        with socket.socket() as probe:
            probe.bind(("127.0.0.1", 0))
            self.port = probe.getsockname()[1]
        self._seed(hostname, credentials or {})

    def _seed(self, hostname: str, credentials: dict[str, str]) -> None:
        self.key.unlink(missing_ok=True)
        run("ssh-keygen", "-q", "-t", "ed25519", "-N", "", "-C", self.name, "-f", str(self.key))
        documents = self.work / "seed"
        documents.mkdir(exist_ok=True)
        key = self.key.with_suffix(".pub").read_text().strip()
        (documents / "meta-data").write_text(seed.meta_data(hostname, None, ssh_keys=[key], instance_id=self.name))
        (documents / "user-data").write_text("" if self.boot.cloud_init else seed.credentials(
            {"system.hostname": hostname, "ssh.authorized_keys.debian": key, **credentials}))
        if self.boot.seed == "drive":
            builder.run_in({self.work: "/work"}, "xorriso", "-as", "mkisofs", "-quiet", "-output",
                           "/work/seed.iso", "-volid", "cidata", "-joliet", "-rock", "/work/seed")

    def start(self, disk: str, *, kernel: str | None = None, initrd: str | None = None) -> None:
        """Boots work/<disk>, a .raw or .qcow2: by its kernel and initrd if given, with its boot's
        command line, else through Secure Boot firmware with an empty key store."""
        if self.boot.seed == "partition":
            builder.run_in({self.work: "/work"}, "sh", "-c", SEED_PARTITION, "seed", f"/work/{disk}")
        if kernel:
            boot = ["-machine", self.machine.machine, "-kernel", f"/work/{kernel}", "-initrd", f"/work/{initrd}",
                    "-append", self.boot.cmdline]
        elif self.machine.firmware:
            # Kept when it's started again, as a VM's are when it's powered off
            if not (self.work / "vars.fd").exists():
                builder.run_in({self.work: "/work"}, "cp", OVMF_SETUP_MODE, "/work/vars.fd")
            boot = list(self.machine.firmware)
        else:
            raise BootstrapError(f"{self.boot.architecture} boots by its kernel here: no firmware for it")
        seed_drive = ["-drive", "file=/work/seed.iso,format=raw,if=virtio,readonly=on"] if self.boot.seed == "drive" else []
        accel = ["-accel", "kvm", "-cpu", "host"] if self.kvm else ["-accel", "tcg,thread=multi", "-cpu", "max"]
        tpm = ["-chardev", "socket,id=tpm,path=/work/tpm.sock", "-tpmdev", "emulator,id=tpm,chardev=tpm",
               "-device", "tpm-tis,tpmdev=tpm"] if self.boot.tpm else []
        qemu = [
            self.machine.qemu, *accel, "-m", "2048", "-smp", str(min(os.cpu_count() or 2, 4)), *boot,
            "-uuid", self.uuid,
            "-drive", f"file=/work/{disk},format={Path(disk).suffix.lstrip('.')},if=virtio,discard=unmap",
            *seed_drive, *tpm,
            "-netdev", f"user,id=net,hostfwd=tcp::2222-:22,dnssearch={LEASE_DOMAIN}",
            "-device", "virtio-net-pci,netdev=net",
            "-chardev", "socket,id=qga,path=/work/qga.sock,server=on,wait=off",
            "-device", "virtio-serial", "-device", "virtserialport,chardev=qga,name=org.qemu.guest_agent.0",
            "-display", "none", "-serial", "file:/work/serial.log",
        ]
        if self.boot.tpm:
            # Its state in work/, so it outlives a reboot as a VM's does
            qemu = ["sh", "-c", "mkdir -p /work/tpm && rm -f /work/tpm.sock"
                    " && swtpm socket --tpm2 --daemon --tpmstate dir=/work/tpm"
                    " --ctrl type=unixio,path=/work/tpm.sock && exec " + shlex.join(qemu)]
        builder.run_in(self.mounts, *qemu, detach=self.name, devices=("/dev/kvm",) if self.kvm else (),
                       ports=(f"127.0.0.1:{self.port}:2222",))

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

    def reboot(self, timeout: int = 600) -> None:
        """Reboots it, and waits until it's back from a boot of its own."""
        before = self.ssh("cat /proc/sys/kernel/random/boot_id").strip()
        subprocess.run(["ssh", *self.ssh_args, "debian@127.0.0.1", "sudo systemctl reboot"],
                       capture_output=True, check=False)
        timeout *= self.patience
        deadline = time.monotonic() + timeout
        while True:
            time.sleep(5)
            now = subprocess.run(["ssh", *self.ssh_args, "debian@127.0.0.1", "cat /proc/sys/kernel/random/boot_id"],
                                 capture_output=True, text=True, check=False)
            if now.returncode == 0 and now.stdout.strip() != before:
                return
            if time.monotonic() > deadline:
                raise BootstrapError(f"not back in {timeout}s: see {self.log}")

    def stop(self) -> None:
        """Powers it off, leaving its disk, firmware variables and TPM to start it again with."""
        subprocess.run(["ssh", *self.ssh_args, "debian@127.0.0.1", "sudo systemctl poweroff"],
                       capture_output=True, check=False)
        self.wait_until_stopped()
        self.remove()

    def serve(self, directory: str) -> str:
        """Serves one of its container's directories over HTTP, and returns the URL the VM reaches it at."""
        run("docker", "exec", "--detach", self.name, "python3", "-m", "http.server", "--directory", directory, "8000")
        return "http://10.0.2.2:8000/"

    def wait_for_ssh(self, timeout: int = 300) -> None:
        timeout *= self.patience
        deadline = time.monotonic() + timeout
        while subprocess.run(["ssh", *self.ssh_args, "debian@127.0.0.1", "true"],
                             capture_output=True, check=False).returncode:
            if time.monotonic() > deadline:
                raise BootstrapError(f"no SSH in {timeout}s: see {self.log}")
            time.sleep(5)

    def wait_until_stopped(self, timeout: int = 300) -> None:
        timeout *= self.patience
        try:
            subprocess.run(["docker", "wait", self.name], capture_output=True, timeout=timeout, check=True)
        except subprocess.TimeoutExpired:
            raise BootstrapError(f"still running after {timeout}s: see {self.log}") from None

    def agent(self, *commands: dict) -> list[dict]:
        """Each command's reply from the guest agent, through its socket as a hypervisor reaches it."""
        replies = run("docker", "exec", "--interactive", self.name, "python3", "-c", AGENT_CLIENT,
                      capture=True, input="".join(json.dumps(c) + "\n" for c in commands))
        return [json.loads(line) for line in replies.splitlines()]

    def remove(self) -> None:
        subprocess.run(["docker", "rm", "--force", self.name], capture_output=True, check=False)
