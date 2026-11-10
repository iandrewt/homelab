"""A release's signatures, by the key in KMS only a release may use, through aws-kms-pkcs11.

mkosi's signing tools run without network, so the module runs behind p11-kit's server, which `mkosi box`
starts in the tools tree: the tools load p11-kit's client of the same release, on the /run their sandbox
binds, by two variables ymir's configs pass by name, the initrd's too: mkosi validates every image's keys,
and Environment= isn't passed to a subimage."""

import hashlib
import json
import os
import platform
import shutil
import urllib.request
from pathlib import Path

# Prebuilt, its AWS SDK static; built for Ubuntu 24.04, whose glibc and libcurl forky's and trixie's outrun
MODULE = "https://github.com/JackOfMostTrades/aws-kms-pkcs11/releases/download/v0.0.19/aws_kms_pkcs11-ubuntu24.04-{}.so"
MODULE_SHA256 = {
    "x86-64": ("x86_64", "75ce286818706e0e178e81a2006cbca2c735f221c5492ad410cd0d9288efeaf5"),
    "arm64": ("aarch64", "8d5bb531d2b1bee51f268ea4282454e744c903755141560f7bbeb148906d95c9"),
}
# Where the module, its config and the certificate are, in the builder and in mkosi's sandbox
DIR = "/run/homelab-signing"
SOCKET = "/run/p11-kit/kms"
URI = "pkcs11:token=images;type=private"
# What the module reads, and what reaches KMS: a fork's key and region are its own
ENV = ("AWS_ACCESS_KEY_ID", "AWS_SECRET_ACCESS_KEY", "AWS_SESSION_TOKEN", "AWS_REGION", "AWS_ENDPOINT_URL")

# Runs a mkosi verb with the server beside it: $1 the verb, the rest mkosi and its arguments
SERVER = f"""
set -eu
verb=$1; shift
mkdir -p {os.path.dirname(SOCKET)}
"$@" --force box -- /usr/libexec/p11-kit/p11-kit-server -f --provider {DIR}/aws_kms_pkcs11.so \\
  --name {SOCKET} 'pkcs11:token=images' &
for i in $(seq 600); do [ -S {SOCKET} ] && break; sleep 1; done
[ -S {SOCKET} ]
multiarch=$(python3 -c 'import sysconfig; print(sysconfig.get_config_var("MULTIARCH"))')
export PKCS11_PROVIDER_MODULE=/usr/lib/$multiarch/pkcs11/p11-kit-client.so P11_KIT_SERVER_ADDRESS=unix:path={SOCKET}
exec "$@" "$verb"
"""

# sysupdate verifies SHA256SUMS with gpg: the release key as OpenPGP, through gnupg-pkcs11-scd, its creation
# time fixed so every release makes the same certificate
KEYS = f"""
set -eu
rm -rf /out/keys
mkdir -p /out/keys/tree/usr/lib/systemd /out/keys/gnupg
chmod 700 /out/keys/gnupg
export GNUPGHOME=/out/keys/gnupg
echo "scdaemon-program /usr/bin/gnupg-pkcs11-scd" > $GNUPGHOME/gpg-agent.conf
# The key's policy signs SHA-256 alone, and gpg would pick SHA-512 for an RSA key
printf 'digest-algo SHA256\\ncert-digest-algo SHA256\\npersonal-digest-preferences SHA256\\n' > $GNUPGHOME/gpg.conf
printf 'providers kms\\nprovider-kms-library {DIR}/aws_kms_pkcs11.so\\n' > $GNUPGHOME/gnupg-pkcs11-scd.conf
gpg --batch --card-status >/dev/null
grip=$(gpg-connect-agent 'scd learn --force' /bye | awk '/S KEYPAIRINFO/ {{print $3; exit}}')
gpg --batch --quiet --generate-key <<P
Key-Type: RSA
Key-Grip: $grip
Key-Usage: sign
Name-Real: homelab images updates
Creation-Date: 20261101T000000
Expire-Date: 0
%no-protection
%commit
P
gpg --export > /out/keys/tree/usr/lib/systemd/import-pubring.pgp
"""


def prepare(images: Path, work: Path) -> Path:
    """The module, checked against its pin, its config, and the certificate the key's in db by, in work. The
    builder's architecture, whatever the image's: the module loads there."""
    work.mkdir(parents=True, exist_ok=True)
    arch, digest = MODULE_SHA256[{"x86_64": "x86-64", "aarch64": "arm64"}[platform.machine()]]
    module = work / "aws_kms_pkcs11.so"
    if not module.exists() or hashlib.sha256(module.read_bytes()).hexdigest() != digest:
        with urllib.request.urlopen(MODULE.format(arch)) as response:
            data = response.read()
        if hashlib.sha256(data).hexdigest() != digest:
            raise ValueError(f"{MODULE.format(arch)} isn't the module pinned")
        module.write_bytes(data)
        module.chmod(0o755)
    shutil.copy(images / "secure-boot" / "db.crt", work / "db.crt")
    (work / "aws-kms-pkcs11").mkdir(exist_ok=True)
    (work / "aws-kms-pkcs11" / "config.json").write_text(json.dumps({"slots": [{
        "label": "images", "kms_key_id": os.environ.get("HOMELAB_IMAGES_KEY") or "alias/images",
        "certificate_path": f"{DIR}/db.crt",
    }]}))
    return work


def mkosi_args() -> list[str]:
    """Every key the release's, and the keys it enrolls ours rather than ones mkosi makes of its certificate."""
    return ["--secure-boot-auto-enroll", "no", "--extra-tree", "/cache/project/secure-boot/auto:/efi/loader/keys/auto",
            *_keys("secure-boot", "verity", "sign-expected-pcr")]


def verity_args() -> list[str]:
    """The release key for verity alone, as a system extension is signed."""
    return _keys("verity")


def _keys(*kinds: str) -> list[str]:
    return [a for key in kinds for a in (f"--{key}-key", URI, f"--{key}-key-source", "provider:pkcs11",
                                         f"--{key}-certificate", "/cache/project/secure-boot/db.crt")]


def mounts(work: Path) -> dict[Path, str]:
    return {work: f"{DIR}:ro"}


def env() -> dict[str, str]:
    """What the module reads in the builder: AWS's credentials, and where its config is. A job re-run with
    debug logging has the module's and p11-kit's log too."""
    debug = {"AWS_KMS_PKCS11_DEBUG": "stderr", "P11_KIT_DEBUG": "all"} if os.environ.get("RUNNER_DEBUG") == "1" else {}
    return {**{name: os.environ[name] for name in ENV if name in os.environ}, "XDG_CONFIG_HOME": DIR, **debug}
