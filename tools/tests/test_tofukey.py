"""The OpenTofu external key provider protocol.

Field names come from OpenTofu's own OutputV1/MetadataV1 structs: `keys`,
carrying base64 `encryption_key` / `decryption_key`, and `meta.external_data`.
"""

import base64
import json
import subprocess
import sys

from homelab import tofukey

PASSPHRASE = "a-passphrase-for-testing"


def encrypt_call(salt=b"\x00" * 32):
    return tofukey.respond(None, PASSPHRASE, salt)


def test_the_first_call_yields_only_an_encryption_key():
    out = encrypt_call()
    assert set(out["keys"]) == {"encryption_key"}


def test_keys_are_base64_of_the_right_length():
    key = base64.b64decode(encrypt_call()["keys"]["encryption_key"])
    assert len(key) == tofukey.KEY_LENGTH


def test_the_key_that_encrypted_is_the_key_returned_to_decrypt():
    first = encrypt_call(b"\x01" * 32)
    second = tofukey.respond(first["meta"], PASSPHRASE, b"\x02" * 32)
    assert second["keys"]["decryption_key"] == first["keys"]["encryption_key"]


def test_a_decrypt_call_also_rekeys_for_the_next_write():
    first = encrypt_call(b"\x01" * 32)
    second = tofukey.respond(first["meta"], PASSPHRASE, b"\x02" * 32)
    assert second["keys"]["encryption_key"] != second["keys"]["decryption_key"]
    assert (
        second["meta"]["external_data"]["salt"]
        != first["meta"]["external_data"]["salt"]
    )


def test_a_different_passphrase_derives_a_different_key():
    salt = b"\x03" * 32
    assert (
        tofukey.respond(None, PASSPHRASE, salt)["keys"]["encryption_key"]
        != tofukey.respond(None, "something-else", salt)["keys"]["encryption_key"]
    )


def test_the_parameters_are_recorded_so_they_can_change_later():
    """Old state has to stay readable when ITERATIONS or HASH change, so decryption uses
    what was stored rather than what the code currently defaults to."""
    stored = {
        "external_data": {
            "salt": base64.b64encode(b"\x04" * 32).decode(),
            "iterations": 1000,
            "hash": "sha256",
        }
    }
    expected = tofukey.derive(PASSPHRASE, b"\x04" * 32, 1000, "sha256")
    out = tofukey.respond(stored, PASSPHRASE, b"\x05" * 32)
    assert base64.b64decode(out["keys"]["decryption_key"]) == expected


def test_metadata_carries_what_decryption_needs(ipam):
    external = encrypt_call()["meta"]["external_data"]
    assert set(external) == {"salt", "iterations", "hash"}


# --- The wire protocol ---------------------------------------------------------------


def run_provider(stdin: str, passphrase=PASSPHRASE):
    return subprocess.run(
        [
            sys.executable,
            "-c",
            "from homelab.cli import main; raise SystemExit(main())",
            "tofu-key",
        ],
        check=False, input=stdin,
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin", "HOMELAB_STATE_PASSPHRASE": passphrase},
    )


def test_the_header_comes_first_and_then_the_keys():
    result = run_provider("null")
    assert result.returncode == 0, result.stderr
    header, body = result.stdout.splitlines()[:2]
    assert json.loads(header) == {
        "magic": "OpenTofu-External-Key-Provider",
        "version": 1,
    }
    assert "encryption_key" in json.loads(body)["keys"]


def test_unreadable_stdin_is_a_failure():
    assert run_provider("{not json").returncode == 1


def test_no_passphrase_and_no_token_is_a_clean_failure():
    result = subprocess.run(
        [
            sys.executable,
            "-c",
            "from homelab.cli import main; raise SystemExit(main())",
            "tofu-key",
        ],
        check=False, input="null",
        capture_output=True,
        text=True,
        env={"PATH": "/usr/bin:/bin"},
    )
    assert result.returncode == 1
    assert "OP_SERVICE_ACCOUNT_TOKEN" in result.stderr
