"""The interim Secure Boot keys every image enrolls."""

import os
import tempfile
from pathlib import Path

from .. import secrets
from ..bootstrap.shell import BootstrapError, say
from . import builder

VAULT = "Break-glass"
ITEM = "Secure Boot Key - "

# PK signs KEK and KEK signs db, which holds Debian's CA alone
SIGN = """
set -eu
for k in PK KEK; do
  openssl req -quiet -new -x509 -newkey rsa:2048 -nodes -days 7300 \
    -subj "/CN=homelab Secure Boot $k (interim)/" -keyout $k.key -out $k.crt
done
openssl x509 -inform DER -in /secure-boot/debian-secure-boot-ca.der -out db.crt
owner=$(uuidgen)
mkdir auto
for var in PK:PK KEK:PK db:KEK; do
  name=${var%:*} signer=${var#*:}
  cert-to-efi-sig-list -g "$owner" $name.crt $name.esl
  sign-efi-sig-list -k $signer.key -c $signer.crt $name $name.esl auto/$name.auth >/dev/null
done
"""


def register(verbs) -> None:
    keys = verbs.add_parser(
        "keys", help="make the interim Secure Boot keys",
        description=f"Makes a PK and KEK, keeps their private keys in {VAULT}, and writes what every "
        "image enrolls to images/secure-boot/auto/. Refuses where keys exist: re-keying is D2's.",
    )
    keys.set_defaults(run=_keys)


def _keys(args) -> int:
    images = builder.ensure()
    published = images / "secure-boot"
    if (published / "auto").exists() or secrets.read_items(VAULT, ITEM):
        raise BootstrapError(f"Secure Boot keys exist (images/secure-boot/auto, {VAULT}): re-keying is D2's")

    # The runtime directory is a tmpfs, so the private keys never reach a disk
    with tempfile.TemporaryDirectory(dir=os.environ.get("XDG_RUNTIME_DIR")) as tmp:
        work = Path(tmp)
        say("making PK and KEK, and signing what every image enrolls")
        builder.run_in({published: "/secure-boot:ro", work: "/work"}, "sh", "-c", SIGN,
                       user=f"{os.getuid()}:{os.getgid()}", workdir="/work")
        for key in ("PK", "KEK"):
            fields = {(key, "private key"): (work / f"{key}.key").read_text(),
                      (key, "certificate"): (work / f"{key}.crt").read_text()}
            print(f"{secrets.write_item(VAULT, ITEM + key, fields, concealed={'private key'}, tags=['homelab/secure-boot'])}: {key}")
        (published / "auto").mkdir()
        for key in ("PK", "KEK"):
            (published / f"{key}.crt").write_bytes((work / f"{key}.crt").read_bytes())
        for var in ("PK", "KEK", "db"):
            (published / "auto" / f"{var}.auth").write_bytes((work / "auto" / f"{var}.auth").read_bytes())

    say(f"wrote {published.relative_to(images.parent)}")
    return 0
