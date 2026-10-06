"""The second factor in tests (src/review/auth.py, review/totp.py): an account's authenticator app set up through the real
steps, the code it would show, and the last step of a sign-in over HTTP. Everyone here is made up."""

from __future__ import annotations

import base64
import json
import urllib.request

import clock
from review import totp

# the recovery codes of each account set up by set_up(), used one per sign-in by finish() (an app code can't be used twice
# within 30 seconds, and a test may sign the same person in several times)
_RECOVERY: dict[str, list[str]] = {}


def secret_from(letters: str) -> bytes:
    """The secret from the letters the set-up screen shows (Base32, in groups, no padding)."""
    text = letters.replace(" ", "").upper()
    return base64.b32decode(text + "=" * (-len(text) % 8))


def code_now(secret: bytes, offset_steps: int = 0) -> str:
    return totp.code_at(secret, clock.utcnow().timestamp() + offset_steps * totp.STEP)


def set_up(accounts, email: str, password: str) -> dict:
    """Signs in with the password and sets the app up with the code for now: {"secret", "recovery"}."""
    token, user = accounts.sign_in(email, password)
    assert user.get("second_factor") == "enrol", user
    secret = secret_from(accounts.enrol_start(token, "Test")["letters"])
    done = accounts.enrol_finish(token, code_now(secret))
    _RECOVERY[email.lower()] = list(done["recovery_codes"])
    return {"secret": secret, "recovery": list(done["recovery_codes"])}


def finish(base: str, email: str, cookie: str) -> str:
    """After /api/login answered that a code is owed (cookie: its session cookie, "name=value"): the code step with one of the
    account's recovery codes; the signed-in cookie."""
    req = urllib.request.Request(base + "/api/code", data=json.dumps({"code": _RECOVERY[email.lower()].pop()}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1", "Cookie": cookie}, method="POST")
    with urllib.request.urlopen(req) as r:
        return r.headers.get("Set-Cookie").split(";")[0]
