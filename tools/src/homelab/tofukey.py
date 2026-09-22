"""OpenTofu external key provider, backed by a 1Password passphrase.

https://opentofu.org/docs/v1.12/language/state/encryption/#external-experimental
header on stdout, metadata in on stdin, keys out.
"""

import base64
import hashlib
import json
import os
import sys

from .secrets import SecretError, resolve_one

HEADER = {"magic": "OpenTofu-External-Key-Provider", "version": 1}

PASSPHRASE_REFERENCE = os.environ.get(
    "HOMELAB_STATE_PASSPHRASE_REF", "op://Homelab/Tofu State Passphrase/password"
)
PASSPHRASE_ENV = "HOMELAB_STATE_PASSPHRASE"

KEY_LENGTH = 32  # AES-256, matching the aes_gcm method
ITERATIONS = 600_000
HASH = "sha512"
SALT_LENGTH = 32


def passphrase() -> str:
    """1Password, unless a passphrase is supplied directly - which is how CI validates
    a configuration it will never apply."""
    if direct := os.environ.get(PASSPHRASE_ENV):
        return direct
    return resolve_one(PASSPHRASE_REFERENCE)


def derive(secret: str, salt: bytes, iterations: int, hash_name: str) -> bytes:
    return hashlib.pbkdf2_hmac(hash_name, secret.encode(), salt, iterations, KEY_LENGTH)


def respond(incoming: dict | None, secret: str, salt: bytes) -> dict:
    keys = {}

    previous = (incoming or {}).get("external_data") or {}
    if previous:
        keys["decryption_key"] = _b64(
            derive(
                secret,
                base64.b64decode(previous["salt"]),
                int(previous.get("iterations", ITERATIONS)),
                previous.get("hash", HASH),
            )
        )

    keys["encryption_key"] = _b64(derive(secret, salt, ITERATIONS, HASH))

    return {
        "keys": keys,
        # Recorded alongside the ciphertext so changing the parameters below stays
        # backwards compatible: old state decrypts with the values it was written under.
        "meta": {
            "external_data": {
                "salt": _b64(salt),
                "iterations": ITERATIONS,
                "hash": HASH,
            }
        },
    }


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode()


def main() -> int:
    print(json.dumps(HEADER), flush=True)

    try:
        incoming = json.loads(sys.stdin.read() or "null")
    except json.JSONDecodeError as exc:
        print(f"unreadable metadata from OpenTofu: {exc}", file=sys.stderr)
        return 1

    try:
        secret = passphrase()
    except SecretError as exc:
        print(str(exc), file=sys.stderr)
        return 1

    print(json.dumps(respond(incoming, secret, os.urandom(SALT_LENGTH))), flush=True)
    return 0
