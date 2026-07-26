from __future__ import annotations

from base64 import b64decode
from binascii import Error as Base64Error
from collections.abc import Mapping
import getpass
import os
from typing import Callable

_KEY_BYTES = 32


class EncryptionKeyError(ValueError):
    """Encryption key input is absent or malformed."""


def decode_encryption_key(value: str) -> bytes:
    """Decode an exact random AES-256 key without retaining its text form."""
    try:
        decoded = b64decode(value.encode("ascii"), validate=True)
    except (UnicodeEncodeError, Base64Error, ValueError) as error:
        raise EncryptionKeyError("backup encryption key is malformed") from error
    if len(decoded) != _KEY_BYTES:
        raise EncryptionKeyError(
            "backup encryption key must decode to exactly 32 bytes"
        )
    return decoded


def load_encryption_key(
    *,
    env_name: str = "BACKUP_ENCRYPTION_KEY",
    environ: Mapping[str, str] | None = None,
    interactive: bool = False,
    prompt: Callable[[str], str] = getpass.getpass,
) -> bytes:
    """Read a key from process environment or a non-echoing operator prompt."""
    source = os.environ if environ is None else environ
    encoded = source.get(env_name)
    if encoded is None and interactive:
        encoded = prompt("Backup encryption key: ")
    if not encoded:
        raise EncryptionKeyError("required backup encryption key is unavailable")
    return decode_encryption_key(encoded)
