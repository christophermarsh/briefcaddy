"""Codes from an authenticator app: the review app's second factor, with no outside service.

TOTP, RFC 6238 (https://www.rfc-editor.org/rfc/rfc6238, read 10/03/2026) over HOTP, RFC 4226
(https://www.rfc-editor.org/rfc/rfc4226, read 10/03/2026):
  - HOTP(K, C) = Truncate(HMAC-SHA-1(K, C)) mod 10^Digit, C the 8-byte big-endian counter, Truncate the
    "dynamic truncation" of RFC 4226 section 5.3 (the low 4 bits of the last byte pick 4 bytes; the top bit is dropped);
  - TOTP: C = floor((unix time - T0) / X), T0 = 0 and X = 30 seconds ("We RECOMMEND a default time-step size of 30
    seconds", section 5.2), 6 digits, SHA-1: what every authenticator app shows by default;
  - the secret is 160 bits ("This document RECOMMENDs a shared secret length of 160 bits", RFC 4226 section 4);
  - a code is accepted one step either side of now ("We RECOMMEND that at most one time step is allowed as the network
    delay", RFC 6238 section 5.2), and never twice: "The verifier MUST NOT accept the second attempt of the OTP after the
    successful validation has been issued for the first OTP" (section 5.2), so a code's step must be later than the last
    one accepted for that account.
The algorithm is checked against the RFC's own test vectors (Appendix B) in tests/test_totp.py.

The authenticator app learns the secret from an otpauth:// address, shown as a QR code (review/qr.py), in Google's Key
Uri Format (https://github.com/google/google-authenticator/wiki/Key-Uri-Format, read 10/03/2026): the secret in
Base32 with the padding left out, the issuer both as the label's prefix and as a parameter.

Recovery codes: eight, each 16 characters drawn at random from 28 letters and digits (28^16, about 2^77), written as
four groups of four; kept only as a salted SHA-256 (a code that random needs no slow hash: there is no word list to
try), each good once.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import secrets
import struct
from urllib.parse import quote

STEP = 30          # seconds per code (RFC 6238 X)
DIGITS = 6
SECRET_BYTES = 20  # 160 bits (RFC 4226 section 4)
DRIFT = 1          # steps accepted either side of now (RFC 6238 section 5.2)
RECOVERY_CODES = 8
# Base32 without 0/1/8/O/I/L/B lookalikes for the recovery codes a person types from paper
_RECOVERY_ALPHABET = "acdefghjkmnpqrstuvwxyz234679"


def hotp(key: bytes, counter: int, digits: int = DIGITS, digest: str = "sha1") -> str:
    """RFC 4226 section 5.3: HMAC over the 8-byte counter, dynamic truncation, the last `digits` digits."""
    mac = hmac.new(key, struct.pack(">Q", counter), digest).digest()
    offset = mac[-1] & 0x0F
    binary = struct.unpack(">I", mac[offset:offset + 4])[0] & 0x7FFFFFFF
    return str(binary % 10 ** digits).zfill(digits)


def step(unix_time: float) -> int:
    """RFC 6238 section 4.2: T = floor((Current Unix time - T0) / X), T0 = 0."""
    return int(unix_time // STEP)


def code_at(key: bytes, unix_time: float, digits: int = DIGITS, digest: str = "sha1") -> str:
    return hotp(key, step(unix_time), digits, digest)


def new_secret() -> bytes:
    return secrets.token_bytes(SECRET_BYTES)


def b32(key: bytes) -> str:
    """The secret as an authenticator app takes it: Base32, no padding (Key Uri Format)."""
    return base64.b32encode(key).decode().rstrip("=")


def grouped(key: bytes) -> str:
    """The secret in groups of four letters, for typing it into the app by hand."""
    text = b32(key)
    return " ".join(text[i:i + 4] for i in range(0, len(text), 4))


def uri(key: bytes, account: str, issuer: str) -> str:
    """otpauth://totp/Issuer:account?secret=...&issuer=Issuer&algorithm=SHA1&digits=6&period=30"""
    label = quote(issuer, safe="") + ":" + quote(account, safe="@")
    return (f"otpauth://totp/{label}?secret={b32(key)}&issuer={quote(issuer, safe='')}"
            f"&algorithm=SHA1&digits={DIGITS}&period={STEP}")


def match(key: bytes, code: str, unix_time: float, last_step: int | None) -> int | None:
    """The step the code belongs to (now, or one step either side), if it is later than the last one accepted;
    else None. Every candidate is compared, in constant time, whatever the first one said."""
    found = None
    now = step(unix_time)
    for s in range(now - DRIFT, now + DRIFT + 1):
        if hmac.compare_digest(hotp(key, s), code) and (last_step is None or s > last_step) and found is None:
            found = s
    return found


def clean(code: str) -> str:
    """What the person typed, without spaces or dashes (an app shows "123 456"; a recovery code has dashes)."""
    return "".join(ch for ch in str(code or "").strip().lower() if ch not in " -\t")


def is_app_code(code: str) -> bool:
    return len(code) == DIGITS and code.isascii() and code.isdigit()


def recovery_codes() -> list[str]:
    """Eight one-use codes, each 16 characters from the alphabet above (28^16, about 2^77), in groups of four."""
    out = []
    for _ in range(RECOVERY_CODES):
        raw = "".join(secrets.choice(_RECOVERY_ALPHABET) for _ in range(16))
        out.append("-".join(raw[i:i + 4] for i in range(0, 16, 4)))
    return out


def recovery_hash(code: str, salt: str) -> str:
    return hashlib.sha256((salt + ":" + clean(code)).encode()).hexdigest()


def recovery_match(code: str, salt: str, stored: list[str]) -> int | None:
    """Which stored recovery code this is (its index), comparing against every one in constant time."""
    wanted, found = recovery_hash(code, salt), None
    for i, h in enumerate(stored):
        if hmac.compare_digest(wanted, h) and found is None:
            found = i
    return found
