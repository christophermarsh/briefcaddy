"""Secrets kept encrypted at rest with Fernet (https://cryptography.io/en/latest/fernet/, read 10/03/2026: AES-128 in
CBC mode with a 128-bit key, HMAC with SHA256; MultiFernet "performs all encryption options using the first key in the
list provided" and "attempts to decrypt tokens with each key in turn", and rotate() re-encrypts a token under the first
key: "add your new key at the front of the list ... and remove old keys as they are no longer needed").

The key comes from the server's environment when set there (the better choice for a hosted server: a copy of the data
folder alone then shows nothing), else from a key file beside the secrets, created once with owner-only access (0600).
A key in the environment or the file may list several keys, separated by commas or one per line: the first encrypts,
each decrypts, so a new key can be put first while the old one still reads what it wrote (rotation).

Used by the Clio connection's vault (connectors/clio.py) and the staff accounts' authenticator secrets (review/auth.py).
"""

from __future__ import annotations

import os
from pathlib import Path


def key_file(path: Path) -> bytes:
    """The key file's contents, the file made first (a new Fernet key, owner-only) if there is none."""
    from cryptography.fernet import Fernet

    if not path.exists():
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        except FileExistsError:  # made a moment ago by another request
            return path.read_bytes().strip()
        with os.fdopen(fd, "wb") as f:
            f.write(Fernet.generate_key())
    return path.read_bytes().strip()


def keys(text: bytes | str) -> list[bytes]:
    raw = text.decode() if isinstance(text, bytes) else text
    return [k.strip().encode() for k in raw.replace(",", "\n").splitlines() if k.strip()]


def cipher(text: bytes | str):
    """A MultiFernet over every key listed (the first encrypts). ValueError when one isn't a Fernet key."""
    from cryptography.fernet import Fernet, MultiFernet

    listed = keys(text)
    if not listed:
        raise ValueError("no key")
    try:
        return MultiFernet([Fernet(k) for k in listed])
    except (ValueError, TypeError):  # "Fernet key must be 32 url-safe base64-encoded bytes"
        raise ValueError("not a Fernet key") from None


def write_key_file(path: Path, listed: list[bytes]) -> None:
    """Replaces the key file (owner-only) with these keys, one per line, the first encrypting."""
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "wb") as f:
        f.write(b"\n".join(listed) + b"\n")
    os.replace(tmp, path)
