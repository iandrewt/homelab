"""The interim Secure Boot keys every image enrolls."""

import base64
import os
import tempfile
from pathlib import Path

from .. import secrets
from ..bootstrap.shell import BootstrapError, run, say
from . import builder

VAULT = "Break-glass"
ITEM = "Secure Boot Key - "

# PK signs KEK and KEK signs db, which holds Debian's CA until `db` adds the release key
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

# A db certificate is a trust anchor, so KEK issuing it does what KEK is for, and KMS needn't sign
DB = """
set -eu
openssl pkey -pubin -inform DER -in release.der -out release.pem
printf 'basicConstraints=critical,CA:FALSE\nkeyUsage=critical,digitalSignature\n' > ext
openssl x509 -new -force_pubkey release.pem -subj "/CN=homelab images db/" -days 7300 -sha256 \
  -CA KEK.crt -CAkey KEK.key -extfile ext -out db.crt
openssl x509 -inform DER -in /secure-boot/debian-secure-boot-ca.der -out debian.crt
owner=$(uuidgen)
cert-to-efi-sig-list -g "$owner" debian.crt debian.esl
cert-to-efi-sig-list -g "$owner" db.crt ours.esl
cat debian.esl ours.esl > db.esl
mkdir auto
sign-efi-sig-list -k KEK.key -c KEK.crt db db.esl auto/db.auth >/dev/null
"""

def register(verbs) -> None:
    keys = verbs.add_parser(
        "keys", help="make the interim Secure Boot keys",
        description=f"Makes a PK and KEK, keeps their private keys in {VAULT}, and writes what every "
        "image enrolls to images/secure-boot/auto/. Refuses where keys exist: re-keying is D2's.",
    )
    keys.set_defaults(run=_keys)

    db = verbs.add_parser(
        "db", help="certify the release key, and sign the db that holds it",
        description="Issues images/secure-boot/db.crt for the release key in KMS, signed by the KEK in "
        f"{VAULT}, and re-signs auto/db.auth with it beside Debian's CA. KMS signs nothing: the key's "
        "policy lets only a release.",
    )
    db.set_defaults(run=_db)


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


def _db(args) -> int:
    images = builder.ensure()
    published = images / "secure-boot"
    kek = secrets.read_items(VAULT, ITEM + "KEK").get(ITEM + "KEK")
    if not kek:
        raise BootstrapError(f"no {ITEM}KEK in {VAULT}: run `homelab image keys` first")
    if not (key := os.environ.get("HOMELAB_IMAGES_KEY")):
        raise BootstrapError("HOMELAB_IMAGES_KEY names the release key: platform/aws/signing's output `key`")
    with tempfile.TemporaryDirectory(dir=os.environ.get("XDG_RUNTIME_DIR")) as tmp:
        work = Path(tmp)
        (work / "release.der").write_bytes(base64.b64decode(run(
            "aws", "kms", "get-public-key", "--key-id", key,
            "--query", "PublicKey", "--output", "text", capture=True)))
        (work / "KEK.key").write_text(kek[("KEK", "private key")])
        (work / "KEK.crt").write_text(kek[("KEK", "certificate")])
        say(f"certifying {key} with KEK, and signing the db that holds it")
        builder.run_in({published: "/secure-boot:ro", work: "/work"}, "sh", "-c", DB,
                       user=f"{os.getuid()}:{os.getgid()}", workdir="/work")
        (published / "db.crt").write_bytes((work / "db.crt").read_bytes())
        (published / "auto" / "db.auth").write_bytes((work / "auto" / "db.auth").read_bytes())
    say(f"wrote {(published / 'db.crt').relative_to(images.parent)} and auto/db.auth")
    return 0
