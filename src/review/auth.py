"""Staff accounts for the review app: who is signed in, and what their role
lets them do.

Two roles:
  - paralegal: works the review queues, asks clients, builds packets;
  - attorney:  everything a paralegal can, plus legal sign-off -- the
               Attorney sign-off queue, acknowledging blocking issues, and
               marking a case filed.

The attorney also runs Settings and the Staff section (no third,
"administrator" role: every attorney may add, turn off and reset staff,
and change Settings; legal sign-off stays with the same role).

Local accounts until the firm picks its single sign-on (Microsoft 365 /
Google): one JSON file. The first attorney's account is created from the
screen ("Set up the first attorney", create_first below: always with a
one-time setup code, the one the installer printed or the one the review
app prints each time it starts while there is no account (docs/deployment.md
says where that line goes on each platform);
the computer that runs the app is shown the screen with a box for the code,
anyone else only when the address carries it; and only while there is no
account at all) or with
src/review/users.py; after that an attorney manages staff on the Settings
page's Staff section (every change in the access log with who made it).
Security choices:
  - passwords are hashed with scrypt (stdlib), per-user salt, constant-time
    comparison; 12 characters minimum. The cost is OWASP's: N=2^17, r=8, p=1
    (the first of the equivalent settings in the Password Storage Cheat
    Sheet, https://cheatsheetseries.owasp.org/cheatsheets/Password_Storage_Cheat_Sheet.html,
    read 2026-10-02). Each hash carries the parameters it was made with, so
    an account hashed at the earlier N=2^14 still signs in and is re-hashed
    at the current cost on that successful sign-in (the cheat sheet: the
    password "should be re-hashed using the new algorithm" when the user
    next enters it);
  - a new or reset account gets a one-time password it must change at
    first sign-in;
  - 5 wrong passwords in a row lock the account for 15 minutes (30, 60,
    120, then 240 at most, when it happens again within a day); the third
    lockout in a day is an alert row in the access log;
  - session cookies are random 32-byte tokens, stored only as SHA-256,
    HttpOnly + SameSite=Strict, ending after a set time without use (the
    Settings page's Sign-in section, 30 minutes unless the attorney changes
    it; sliding: every request starts the time again);
  - every sign-in, failure, sign-out and password change goes to an
    access log (never the password);
  - a second factor, a code from an authenticator app (review/totp.py, RFC
    6238), asked after the password: every attorney sets one up at the first
    sign-in after the account is made or reset, everyone does once the
    attorney sets "Everyone" on the Settings page's Staff section (the
    sign_in settings, recorded with who and when). The app's secret is kept
    encrypted (Fernet, src/secretbox.py: the key is I485_TOTP_KEY from the
    server's environment, else the owner-only key file beside the accounts
    file) and shown only while it is being set up (a new secret for each
    password sign-in, kept with that step, never on the account until the
    first code confirms it); eight one-use recovery codes are shown once and
    kept hashed. A code is good one step (30 seconds) either side of now and
    never twice; wrong codes count toward the same lockout as wrong
    passwords, and a password accepted while a code is still owed doesn't
    clear that count. A second lockout within a day lasts 30 minutes, a third
    60, then 120 and at most 240 (the access log and the Staff section say so). After a code, "remember this
    device" (unless the attorney turned it off) skips the code on that
    browser for that account for 30 days (a separate cookie, kept here only
    as SHA-256). A reset clears the code, the recovery codes and the
    remembered devices. Signing in with Microsoft or Google asks no code:
    the firm's own Microsoft 365 or Google sign-in is the second factor then.
"""

from __future__ import annotations

import hashlib
import hmac
import json
import os
import secrets
import threading
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import clock
import events
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)
import secretbox
from review import totp

ROLES = ("paralegal", "attorney")  # the roles the Staff section gives; "support" is never one of them (let_support_in)
SUPPORT = "support"  # the provider's support person, let in by an attorney for hours, read-only (src/support.py, brief R3)
SUPPORT_HOURS = (1, 8)
MIN_PASSWORD = 12
MAX_FAILURES = 5
LOCKOUT = timedelta(minutes=15)
# Each lockout within a day locks twice as long as the one before (15, 30, 60, 120 minutes), at most four hours: someone who keeps guessing one
# account's password or code gets about 50 tries in the first day and about 30 a day after that (5 tries every four hours). At 15 minutes each it was
# about 480 a day; with an hour at most, about 120 (brief J2, docs/hardening.md). The third lockout of an account in a day is also an alert
# (lockout_alert: the access log, the Staff screen, the morning report).
LOCKOUT_MAX = timedelta(hours=4)
LOCKOUT_MEMORY = timedelta(days=1)
ALERT_AFTER = 3
COOKIE = "review_session"
SETUP_CODE_DAYS = 14  # a setup code printed by the installer stops working after this long (users.py setup-code makes a new one)
SETUP_ALPHABET = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"  # no 0/O, 1/I/L: the code is read off a screen and typed
# OWASP Password Storage Cheat Sheet, scrypt (read 2026-10-02): N=2^17 (128 MiB), r=8, p=1.
SCRYPT = {"n": 2**17, "r": 8, "p": 1}
LEGACY_SCRYPT = {"n": 2**14, "r": 8, "p": 1}  # accounts hashed before 2026-10-02 (no parameters stored with them)
# The one refusal for a wrong password, an unknown email, and a staff sign-in (Microsoft, Google) for
# someone not on the staff list or turned off: none of them says whether the account exists.
WRONG = "That email and password don't match. After 5 tries the account locks for 15 minutes (longer if it happens again the same day)."
LOCKED = ("Too many wrong tries: this account is locked for {length}. Ask an attorney to reset it if you need in now; if you are the only attorney, "
          "the firm's IT person resets it on the server (the deployment guide, \"Locked out\").")
# Password hashes computed at once (each holds 128 MiB for about 0.3 s); more attempts wait their turn,
# without holding the accounts lock, so signed-in staff are never kept waiting behind them.
_HASHING = threading.BoundedSemaphore(2)

# The second factor (the module docstring): the code from an authenticator app.
KEY_ENV = firmsecrets.BY_NAME["accounts.totp_key"].env[0]  # the environment variable that holds the Fernet key (or keys, the first encrypting) for the authenticator secrets: named in src/firmsecrets.py
DEVICE_COOKIE = "review_device"     # "remember this device": its own cookie, only ever sent to the sign-in route
REMEMBER = timedelta(days=30)
MAX_DEVICES = 10                    # remembered browsers per account; beyond, the oldest is forgotten
PENDING = timedelta(minutes=10)     # from the password to the code (or to setting the app up)
# One refusal for every wrong code: a wrong app code, one already used, a wrong or used recovery code.
CODE_WRONG = ("That code didn't work. Use the newest code your app shows, or one of your recovery codes. "
              "After 5 wrong tries the account locks for 15 minutes (longer if it happens again the same day).")
# Setting the app up: there are no recovery codes yet
CODE_WRONG_SETUP = ("That code didn't work. Type the 6-digit code the app shows now for this account. "
                    "After 5 wrong tries the account locks for 15 minutes (longer if it happens again the same day).")
CODE_EXPIRED = "That took too long. Sign in again."
CODE_UNREADABLE = ("This server can't read the saved authenticator settings (its encryption key changed). "
                   "Ask your attorney to reset your password: you then set up the app again.")


def second_factor() -> dict[str, bool]:
    """The firm's choices (the Settings page's Staff section, kept with the Sign-in settings): everyone must use a code
    (else attorneys only), and whether a device may be remembered for 30 days. Unreadable settings: attorneys only, and
    no device remembered."""
    import settings

    try:
        values = settings.values("sign_in")
    except (ValueError, TypeError, OSError):
        return {"everyone": False, "remember": False}
    return {"everyone": values.get("code_everyone") == "yes", "remember": values.get("remember_device", "yes") != "no"}


def lock_length(minutes: int) -> str:
    """A lockout's length as a person says it: "15 minutes", "1 hour", "4 hours"."""
    if minutes < 60 or minutes % 60:
        return f"{minutes} minutes"
    return "1 hour" if minutes == 60 else f"{minutes // 60} hours"


def _locked_text(user: dict[str, Any]) -> str:
    return LOCKED.format(length=lock_length(int(user.get("lock_minutes") or LOCKOUT.total_seconds() // 60)))


def _now() -> datetime:
    return clock.utcnow()  # the firm's clock in UTC (src/clock.py): a lockout or idle time is real minutes, even the night the clocks change


def _support_open(user: dict[str, Any]) -> bool:
    """A support account's session is open: active and before its hour."""
    until = clock.parse((user.get("support") or {}).get("until"))
    return bool(user.get("active", True) and until and _now() < until)


def _session_end(user: dict[str, Any]) -> datetime:
    """When a new session ends: after the idle time; a support session at its hour (the attorney chose the hours: they are not cut by idle time)."""
    if user.get("role") == SUPPORT:
        return clock.parse((user.get("support") or {}).get("until")) or _now()
    return _now() + session_idle()


def _hash_token(token: str) -> str:
    return hashlib.sha256(token.encode()).hexdigest()


def _scrypt(password: str, salt: bytes, params: dict[str, int] | None = None) -> str:
    p = params or SCRYPT
    # needs about 128 * r * N bytes (128 MiB at N=2^17), above hashlib's 32 MiB default ceiling
    return hashlib.scrypt(password.encode(), salt=salt, n=p["n"], r=p["r"], p=p["p"], dklen=32,
                          maxmem=128 * p["r"] * (p["n"] + p["p"] + 2) + 2**20).hex()


def session_idle() -> timedelta:
    """How long a session lasts without use: the attorney's value on the Settings page's Sign-in
    section (src/settings.py, minutes), else 30."""
    import settings

    try:
        minutes = int(settings.values("sign_in").get("idle_minutes") or settings.IDLE_DEFAULT)
    except (ValueError, TypeError, OSError):  # an unreadable settings file: the default
        minutes = settings.IDLE_DEFAULT
    return timedelta(minutes=max(settings.IDLE_MIN, min(settings.IDLE_MAX, minutes)))


class Accounts:
    def __init__(self, path: str | Path):
        self.path = Path(path)
        self.log_path = self.path.with_name(self.path.stem + "_access.jsonl")
        self.key_path = firmsecrets.totp_key_path(self.path)  # when the environment does not hold the key
        self._lock = threading.RLock()
        self._first = threading.Lock()  # create_first
        self._where = threading.local()  # the address of the request being served in this thread (from_address)

    # -- storage -------------------------------------------------------------

    def _load(self) -> dict[str, Any]:
        return json.loads(self.path.read_text(encoding="utf-8")) if self.path.exists() else {"users": {}, "sessions": {}}

    def _save(self, data: dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp = self.path.with_suffix(f".{os.getpid()}.tmp")
        fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)  # the file holds password hashes: owner only, as the installer leaves it
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            f.write(json.dumps(data, indent=1))
        os.replace(tmp, self.path)

    def from_address(self, address: str) -> None:
        """The address the request now being served in this thread came from: every row logged from here on carries it (the attorney's
        "What staff did" shows it). The server sets it at the start of each request; the command line sets none."""
        self._where.address = address or ""

    def log(self, event: str, email: str, **detail: Any) -> None:
        address = getattr(self._where, "address", "")
        row = {"at": _now().isoformat(), "event": event, "email": email, **detail} | ({"address": address} if address else {})
        with self._lock, open(self.log_path, "a", encoding="utf-8") as f:
            f.write(json.dumps(row) + "\n")
        self._ledger(event, email, detail)

    # what an account change says in the event ledger (src/events.py): the accounts file is a record the firm keeps, so a change to it is a row
    # there too (sign-ins and failures are the access log's alone). Never a password, a code or a secret.
    _LEDGER = {"account_added": ("added", "Added the account of {person} as {role}"), "account_changed": ("changed", "Changed the account of {person}"),
               "password_reset": ("reset", "Gave {person} a new one-time password"), "first_attorney_created": ("added", "Set up the first attorney's account"),
               "second_factor_changed": ("changed", "Changed the sign-in codes setting"), "devices_forgotten": ("changed", "Turned off remembering computers"),
               "signed_out_for_code": ("changed", "Signed everyone out who has no authenticator app"),
               "code_key_rotated": ("changed", "Changed the key that protects the authenticator apps' secrets"),
               # the calendar feed (src/calendar_feed.py): that an address was made or turned off, never the address; a feed request itself is the access log's alone
               "calendar_address_made": ("calendar_address_made", "A calendar address was made for {person}"),
               "calendar_address_revoked": ("calendar_address_revoked", "A calendar address of {person} was turned off")}

    def _ledger(self, event: str, email: str, detail: dict[str, Any]) -> None:
        if event not in self._LEDGER or getattr(self._where, "quiet", False):
            return
        try:
            users = self._load()["users"]
        except (OSError, ValueError):
            users = {}
        person = (users.get(email) or {}).get("name") or email or "someone"
        by = str(detail.get("by") or "")
        action, text = self._LEDGER[event]
        events.record("accounts", action, text.format(person=person, role=str(detail.get("role") or "staff")), home=self.path.parent,
                      who=(users.get(by) or {}).get("name") or by or None, default_who=("The server (command line)", "system", "tool"))

    @staticmethod
    def _public(email: str, user: dict[str, Any]) -> dict[str, Any]:
        return {"email": email, "name": user["name"], "role": user["role"], "active": user.get("active", True),
                "must_change": user.get("must_change", False), "created_at": user.get("created_at"),
                **({"support": {k: (user.get("support") or {}).get(k) for k in ("id", "until", "plain", "by")}} if user.get("role") == SUPPORT else {}),
                "code_set_up": bool(user.get("totp")),
                "locks_today": sum(1 for t in user.get("lockouts") or [] if clock.parse(t) > _now() - LOCKOUT_MEMORY)}

    # -- the authenticator secrets, encrypted at rest (src/secretbox.py) --------

    def _cipher(self):
        text = firmsecrets.get("accounts.totp_key", path=self.key_path, create=True)  # the environment's key (or keys), else the key file beside the accounts: src/firmsecrets.py
        try:
            return secretbox.cipher(text)
        except ValueError:  # not a Fernet key: for IT, not for the person signing in
            raise ValueError(CODE_UNREADABLE) from None

    def _seal(self, secret: bytes) -> str:
        return self._cipher().encrypt(secret).decode()

    def _open(self, sealed: str) -> bytes:
        from cryptography.fernet import InvalidToken

        try:
            return self._cipher().decrypt(sealed.encode())
        except InvalidToken:
            raise ValueError(CODE_UNREADABLE) from None

    # -- managing accounts (src/review/users.py) -------------------------------

    def users(self) -> list[dict[str, Any]]:
        return [self._public(e, u) for e, u in sorted(self._load()["users"].items())]

    def add(self, email: str, name: str, role: str, by: str | None = None) -> str:
        """Creates the account; returns its one-time password. by: the attorney who added it on the Settings page's Staff
        section (the access log says who), None from the command line."""
        email = email.strip().lower()
        if "@" not in email:
            raise ValueError("Use the person's work email as their sign-in.")
        if role not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
        if not name.strip():
            raise ValueError("Enter the person's full name: it is recorded on every decision they make.")
        with self._lock:
            data = self._load()
            if email in data["users"]:
                raise ValueError(f"{email} already has an account.")
            data["users"][email] = {"name": name.strip(), "role": role, "active": True, "created_at": _now().isoformat()}
            self._save(data)
        self.log("account_added", email, role=role, **({"by": by} if by else {}))
        self._where.quiet = True  # the one-time password that goes with a new account is part of the same change: one row in the event ledger
        try:
            return self.reset(email, by=by)
        finally:
            self._where.quiet = False

    def reset(self, email: str, by: str | None = None) -> str:
        """A new one-time password (the old one stops working; sessions end). The second factor goes too: the
        authenticator app, the recovery codes and every remembered device; the person sets the app up again (a lost
        phone is the usual reason for a reset)."""
        if (self._load()["users"].get(email.strip().lower()) or {}).get("role") == SUPPORT:
            raise ValueError("A support account has no password: an attorney lets support in from Settings, with a one-time code.")
        temporary = secrets.token_urlsafe(12)
        self._set_password(email, temporary, must_change=True, forget_code=True)
        self.log("password_reset", email, **({"by": by} if by else {}))
        return temporary

    def update(self, email: str, by: str | None = None, **changes: Any) -> dict[str, Any]:
        email = email.strip().lower()
        if "role" in changes and changes["role"] not in ROLES:
            raise ValueError(f"Role must be one of: {', '.join(ROLES)}.")
        with self._lock:
            data = self._load()
            if email not in data["users"]:
                raise LookupError(f"No account for {email}.")
            if data["users"][email].get("role") == SUPPORT and (changes.get("active") is not False or set(changes) - {"active"}):
                raise ValueError("A support account is let in and ended from Settings, Let support in: it cannot be changed here.")
            data["users"][email].update(changes)
            if changes.get("active") is False or "role" in changes:  # takes effect now, not at the next sign-in
                data["sessions"] = {k: s for k, s in data["sessions"].items() if s["email"] != email}
            self._save(data)
        self.log("account_changed", email, **{k: v for k, v in changes.items() if k in ("role", "active")}, **({"by": by} if by else {}))
        return self._public(email, data["users"][email])

    def _set_password(self, email: str, password: str, must_change: bool, forget_code: bool = False) -> None:
        email = email.strip().lower()
        if len(password) < MIN_PASSWORD:
            raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
        salt = secrets.token_bytes(16)
        with _HASHING:  # outside the accounts lock, like sign_in
            hashed = _scrypt(password, salt)
        with self._lock:
            data = self._load()
            if email not in data["users"]:
                raise LookupError(f"No account for {email}.")
            data["users"][email].update(salt=salt.hex(), hash=hashed, scrypt=dict(SCRYPT), must_change=must_change,
                                        failures=0, locked_until=None)
            if forget_code:
                for key in ("totp", "totp_pending", "devices"):
                    data["users"][email].pop(key, None)
            data["sessions"] = {k: s for k, s in data["sessions"].items() if s["email"] != email}
            self._save(data)

    # -- signing in ------------------------------------------------------------

    def sign_in(self, email: str, password: str, device: str | None = None) -> tuple[str, dict[str, Any]]:
        """(session token, user), or ValueError with a message safe to show --
        it never says whether the email exists.

        When a code is owed, the token is only a step towards a session: user["second_factor"] is "code" (the app's code
        or a recovery code, verify_code) or "enrol" (set the app up first, enrol_start / enrol_finish), the token lasts
        PENDING and opens nothing (session_user is None for it). device: the "remember this device" cookie, which
        skips the code for the account it was given to."""
        email = (email or "").strip().lower()
        wrong = ValueError(WRONG)
        # 1. what to hash against, under the lock (a quick file read)
        with self._lock:
            user = self._load()["users"].get(email)
            known = user is not None and bool(user.get("hash"))
            if known and user.get("locked_until") and clock.parse(user["locked_until"]) > _now():
                self.log("sign_in_failed", email, reason="locked")
                raise ValueError(_locked_text(user))
            salt, params, stored = ((bytes.fromhex(user["salt"]), user.get("scrypt") or LEGACY_SCRYPT, user["hash"]) if known
                                    else (b"0" * 16, SCRYPT, None))
        # 2. the slow part (about 0.3 s, 128 MiB) outside the lock, so signed-in staff aren't kept waiting behind it;
        # at most HASHING at once, so a burst of attempts can't take all the memory
        with _HASHING:
            ok = hmac.compare_digest(_scrypt(password or "", salt, params), stored or "")  # the same work for an unknown email
            rehash = None
            if known and ok and params != SCRYPT:  # hashed at an older cost: re-hashed now, while the password is at hand
                new_salt = secrets.token_bytes(16)
                rehash = {"salt": new_salt.hex(), "hash": _scrypt(password, new_salt), "scrypt": dict(SCRYPT)}
        # 3. the outcome, under the lock again, against the account as it is now
        with self._lock:
            data = self._load()
            user = data["users"].get(email)
            if not known or user is None or user.get("hash") != stored:  # unknown, or its password changed meanwhile
                self.log("sign_in_failed", email, reason="unknown" if not known else "changed")
                raise wrong
            if user.get("locked_until") and clock.parse(user["locked_until"]) > _now():
                self.log("sign_in_failed", email, reason="locked")
                raise ValueError(_locked_text(user))
            if not ok:
                user["failures"] = user.get("failures", 0) + 1
                locking = user["failures"] >= MAX_FAILURES
                if locking:
                    self._lock_account(user)
                self._save(data)
                self.log("sign_in_failed", email, reason="password")
                if locking:
                    self._log_lock(email, user)
                    raise ValueError(_locked_text(user))  # Implementation note.
                raise wrong
            if not user.get("active", True):
                self.log("sign_in_failed", email, reason="disabled")
                raise ValueError("This account is turned off. Ask an administrator.")
            if user.get("role") == SUPPORT and not _support_open(user):
                self.log("sign_in_failed", email, reason="support session ended")
                raise ValueError("This support session has ended. Ask the firm's attorney to let you in again.")
            # a one-time password goes straight to choosing one's own (an account just made or reset has no code yet)
            owed = None if user.get("must_change") else self._owed(user)
            remembered = owed == "code" and self._remembered(user, device)
            if remembered:
                owed = None
            if owed is None:  # signed in: the count of wrong tries starts again (not before the code: the code's tries count too)
                user["failures"], user["locked_until"] = 0, None
            rehashed = rehash is not None
            if rehashed:
                user.update(rehash)
            token = secrets.token_urlsafe(32)
            data["sessions"][_hash_token(token)] = ({"email": email, "expires": (_now() + PENDING).isoformat(), "pending": owed} if owed
                                                    else {"email": email, "expires": _session_end(user).isoformat()})
            if user.get("role") == SUPPORT:  # the one-time code is spent: it never opens a second session
                user.pop("hash", None)
                user["support"]["signed_in"] = _now().isoformat()
            data["sessions"] = {k: s for k, s in data["sessions"].items() if clock.parse(s["expires"]) > _now()}
            self._save(data)
        if rehashed:
            self.log("password_rehashed", email, scrypt_n=SCRYPT["n"])
        if owed:
            self.log("password_accepted", email, next=owed)  # signed in only once the code is right
            return token, self._public(email, user) | {"second_factor": owed}
        self.log("signed_in", email, **({"how": "remembered device"} if remembered else {}))
        return token, self._public(email, user)

    # -- the second factor ---------------------------------------------------------

    def _owed(self, user: dict[str, Any]) -> str | None:
        """What a correct password still needs: "code" once the app is set up; "enrol" for an attorney (or everyone, when
        the firm says so) who hasn't set it up; None otherwise."""
        if user.get("role") == SUPPORT:  # the one-time code the attorney passed on is the factor; nothing to set up for a few hours
            return None
        if user.get("totp"):
            return "code"
        return "enrol" if user["role"] == "attorney" or second_factor()["everyone"] else None

    def _remembered(self, user: dict[str, Any], device: str | None) -> bool:
        if not device or not second_factor()["remember"]:
            return False
        expires = (user.get("devices") or {}).get(_hash_token(device))
        return bool(expires) and clock.parse(expires) > _now()

    def _pending(self, data: dict[str, Any], token: str | None, stage: str) -> tuple[str, dict[str, Any]]:
        """(email, user) of a session waiting for this step, or LookupError (it ran out, or there is none, or it is the
        set-up step of someone whose app is already set up)."""
        session = data["sessions"].get(_hash_token(token or ""))
        if session is None or session.get("pending") != stage:
            raise LookupError(CODE_EXPIRED)
        user = data["users"].get(session["email"])
        if user is None or not user.get("active", True) or not user.get("hash"):
            raise LookupError(CODE_EXPIRED)
        if user.get("locked_until") and clock.parse(user["locked_until"]) > _now():  # a step the lockout ended says so, never "that took too long"
            raise ValueError(_locked_text(user))
        if clock.parse(session["expires"]) <= _now():
            raise LookupError(CODE_EXPIRED)
        if stage == "enrol" and user.get("totp"):  # set up since this step began (in this window or another): it is over
            raise LookupError(CODE_EXPIRED)
        return session["email"], user

    @staticmethod
    def _lock_account(user: dict[str, Any]) -> None:
        """Locks the account: 15 minutes, doubled for each earlier lockout in the last day, at most LOCKOUT_MAX (four hours)."""
        now = _now()
        recent = [t for t in user.get("lockouts") or [] if clock.parse(t) > now - LOCKOUT_MEMORY]
        length = min(LOCKOUT_MAX, LOCKOUT * 2 ** min(len(recent), 12))  # the doubling stops long before a timedelta could overflow
        user["lockouts"] = recent + [now.isoformat()]
        user["locked_until"], user["failures"], user["lock_minutes"] = (now + length).isoformat(), 0, int(length.total_seconds() // 60)

    def _log_lock(self, email: str, user: dict[str, Any]) -> None:
        """The access log's row for a lockout; from the second in a day it says so (the Staff section shows it too)."""
        times = len(user.get("lockouts") or [])
        self.log("account_locked_again" if times >= 2 else "account_locked", email, minutes=user["lock_minutes"], times_today=times)
        if times == ALERT_AFTER:  # someone keeps guessing: the attorney is told (the Staff screen, the morning report), once a day
            self.log("lockout_alert", email, what="account", times=times)

    def _wrong_code(self, data: dict[str, Any], email: str, user: dict[str, Any], setting_up: bool = False) -> ValueError:
        """A wrong code: one more toward the lockout (the password's count). At the limit the account locks and every
        step under way for it ends; a session already signed in stays (as with a password lockout: someone who only
        knows the password must not be able to sign the person out). The steps under way are not deleted: they run out on their
        own (PENDING, 10 minutes, well before the shortest lock ends) and _pending refuses them first of all with the lock's own
        sentence, so a window still on its code screen is told the account is locked, never "that took too long"."""
        user["failures"] = user.get("failures", 0) + 1
        locked = user["failures"] >= MAX_FAILURES
        if locked:
            self._lock_account(user)
        self._save(data)
        self.log("sign_in_failed", email, reason="code")
        if locked:
            self._log_lock(email, user)
        return ValueError(_locked_text(user) if locked else CODE_WRONG_SETUP if setting_up else CODE_WRONG)

    def _signed_in(self, data: dict[str, Any], token: str, email: str, user: dict[str, Any]) -> str:
        """The step under way becomes a session (a new token). Every step under way for the person ends with it: the
        token that held this one, and any other window's (a set-up step must not outlive the set-up)."""
        user["failures"], user["locked_until"] = 0, None
        data["sessions"] = {k: s for k, s in data["sessions"].items() if not (s["email"] == email and s.get("pending"))}
        fresh = secrets.token_urlsafe(32)
        data["sessions"][_hash_token(fresh)] = {"email": email, "expires": (_now() + session_idle()).isoformat()}
        return fresh

    def verify_code(self, token: str | None, code: str, remember: bool = False) -> dict[str, Any]:
        """The code from the app (or a recovery code) after the password. {"token": the session, "user", "device": the
        remember-this-device token when asked for and the firm allows it, else None, "recovery_left": how many recovery
        codes remain when one was used}. ValueError (CODE_WRONG, or locked) or LookupError (the step ran out)."""
        typed = totp.clean(code)
        with self._lock:
            data = self._load()
            email, user = self._pending(data, token, "code")
            held = user["totp"]
            used_recovery = False
            if totp.is_app_code(typed):
                step = totp.match(self._open(held["secret"]), typed, _now().timestamp(), held.get("last_step"))
                if step is None:
                    raise self._wrong_code(data, email, user)
                held["last_step"] = step  # this code, and any earlier one, never again (RFC 6238 section 5.2)
            else:
                found = totp.recovery_match(typed, held["salt"], held.get("recovery") or []) if typed else None
                if found is None:
                    raise self._wrong_code(data, email, user)
                held["recovery"].pop(found)  # one use
                used_recovery = True
            fresh = self._signed_in(data, token, email, user)
            device = None
            if remember and second_factor()["remember"]:
                device = secrets.token_urlsafe(32)
                devices = {k: v for k, v in (user.get("devices") or {}).items() if clock.parse(v) > _now()}
                devices[_hash_token(device)] = (_now() + REMEMBER).isoformat()
                user["devices"] = dict(sorted(devices.items(), key=lambda kv: kv[1])[-MAX_DEVICES:])
            self._save(data)
        self.log("signed_in", email, how="recovery code" if used_recovery else "code", **({"remembered": True} if device else {}))
        return {"token": fresh, "user": self._public(email, user), "device": device,
                "recovery_left": len(held.get("recovery") or []) if used_recovery else None}

    def enrol_start(self, token: str | None, issuer: str) -> dict[str, str]:
        """Setting the app up, after the password: a secret as an otpauth:// address for the QR code and in letters for
        typing. The secret belongs to this step (kept encrypted in its pending session): a reload in the same window
        shows it again, while every new password sign-in makes a new one, so a secret someone else saw on an earlier
        sign-in can never become the person's app. Shown only during this step, and refused once a code is set up."""
        with self._lock:
            data = self._load()
            email, _ = self._pending(data, token, "enrol")
            session = data["sessions"][_hash_token(token)]
            if session.get("secret"):
                secret = self._open(session["secret"])
            else:
                secret = totp.new_secret()
                session["secret"] = self._seal(secret)
                self._save(data)
        return {"uri": totp.uri(secret, email, issuer), "letters": totp.grouped(secret), "account": email}

    def enrol_finish(self, token: str | None, code: str) -> dict[str, Any]:
        """The first code from the app confirms it: the secret is kept, eight recovery codes are made (returned here,
        once; kept hashed) and the person is signed in. {"token", "user", "recovery_codes"}."""
        typed = totp.clean(code)
        with self._lock:
            data = self._load()
            email, user = self._pending(data, token, "enrol")
            sealed = data["sessions"][_hash_token(token)].get("secret")
            if not sealed:  # no secret shown in this window
                raise LookupError(CODE_EXPIRED)
            step = totp.match(self._open(sealed), typed, _now().timestamp(), None) if totp.is_app_code(typed) else None
            if step is None:
                raise self._wrong_code(data, email, user, setting_up=True)
            salt, codes = secrets.token_hex(16), totp.recovery_codes()
            user.pop("totp_pending", None)  # an earlier version kept the secret on the account
            user["totp"] = {"secret": sealed, "last_step": step, "set_up_at": _now().isoformat(),
                            "salt": salt, "recovery": [totp.recovery_hash(c, salt) for c in codes]}
            fresh = self._signed_in(data, token, email, user)
            self._save(data)
        self.log("code_set_up", email)
        self.log("signed_in", email, how="code")
        return {"token": fresh, "user": self._public(email, user), "recovery_codes": codes}

    def sign_out_without_code(self, by: str | None = None) -> int:
        """"Require a code for everyone" was just switched on: everyone signed in by password alone (no app set up) is
        signed out now, so their next sign-in sets the app up. Microsoft / Google sessions stay (they count as the second
        factor). Returns how many sessions ended."""
        with self._lock:
            data = self._load()
            users = data["users"]
            ended = {k for k, s in data["sessions"].items()
                     if not s.get("how") and not s.get("pending") and not (users.get(s["email"]) or {}).get("totp")}
            data["sessions"] = {k: s for k, s in data["sessions"].items() if k not in ended}
            self._save(data)
        self.log("signed_out_for_code", "", sessions=len(ended), **({"by": by} if by else {}))
        return len(ended)

    def forget_devices(self, by: str | None = None) -> None:
        """Every remembered device, for everyone (the firm turned "remember this device" off)."""
        with self._lock:
            data = self._load()
            for user in data["users"].values():
                user.pop("devices", None)
            self._save(data)
        self.log("devices_forgotten", "", **({"by": by} if by else {}))

    def second_factor_summary(self) -> tuple[int, int]:
        """(who has set the app up, who must): the active attorneys, or every active account when the firm requires a code for
        everyone (the Settings page's Staff section). Getting started's line reads this."""
        everyone = second_factor()["everyone"]
        owed = [u for u in self._load()["users"].values() if u.get("active", True) and (everyone or u["role"] == "attorney")]
        return sum(1 for u in owed if u.get("totp")), len(owed)

    def rotate_key(self) -> int:
        """Every authenticator secret encrypted again under the first key (secretbox: MultiFernet.rotate). With the key
        file: a new key is made and put first, the secrets are re-encrypted, then the old key is dropped from the file.
        With I485_TOTP_KEY: the new key must already be listed first, the old one after it; remove the old one once
        this has run. Returns how many secrets were re-encrypted."""
        from cryptography.fernet import Fernet, InvalidToken

        in_env = firmsecrets.where("accounts.totp_key", path=self.key_path) == "environment"
        if not in_env and not self.key_path.exists():  # this installation's key is in the environment, or there is none yet
            raise ValueError(f"There is no key file beside the staff accounts and {KEY_ENV} isn't set here: run this with the "
                             "same environment the review app starts with. Nothing was changed.")
        if not in_env:
            old = secretbox.keys(secretbox.key_file(self.key_path))
            new = Fernet.generate_key()
            secretbox.write_key_file(self.key_path, [new] + old)  # both read while the secrets are re-encrypted
        with self._lock:
            data = self._load()
            cipher, n = self._cipher(), 0
            try:
                holders = [(u.get("totp") or {}, "secret") for u in data["users"].values()]
                holders += [(u, "totp_pending") for u in data["users"].values()] + [(s, "secret") for s in data["sessions"].values()]
                for holder, key in holders:
                    if holder.get(key):
                        holder[key] = cipher.rotate(holder[key].encode()).decode()
                        n += 1
            except InvalidToken:
                if not in_env:
                    secretbox.write_key_file(self.key_path, old)
                raise ValueError("A saved authenticator secret can't be read with any key listed: nothing was changed.") from None
            self._save(data)
        if not in_env:
            secretbox.write_key_file(self.key_path, [new])
        self.log("code_key_rotated", "", secrets=n)
        return n

    def session_for(self, email: str, how: str) -> tuple[str, dict[str, Any]]:
        """A session for someone already signed in elsewhere (Google Workspace
        or Microsoft 365, connectors/signin.py): only an active account in this staff
        list; no password involved, so no password change is asked for."""
        email = (email or "").strip().lower()
        with self._lock:
            data = self._load()
            user = data["users"].get(email)
            if user is None or not user.get("active", True):
                self.log("sign_in_failed", email, reason=f"{how}: " + ("not on the staff list" if user is None else "disabled"))
                raise ValueError(WRONG)  # the words of a wrong password: nothing says who is on the list
            token = secrets.token_urlsafe(32)
            data["sessions"][_hash_token(token)] = {"email": email, "expires": (_now() + session_idle()).isoformat(), "how": how}
            self._save(data)
        self.log("signed_in", email, how=how)
        return token, self._public(email, user) | {"must_change": False}

    def session_user(self, token: str | None) -> dict[str, Any] | None:
        if not token:
            return None
        with self._lock:
            data = self._load()
            session = data["sessions"].get(_hash_token(token))
            if session is None:
                return None
            user = data["users"].get(session["email"])
            if user is not None and user.get("role") == SUPPORT and user.get("active", True) and not _support_open(user):
                ended = self._end_support(data, session["email"], None)  # its hour has come: checked here, on every request
                self._save(data)
                self.log("support_ended", session["email"], how="its time ran out", **ended)
                events.record("support", "ended", f"The support session of {user['name']} ended by itself at its hour", home=self.path.parent,
                              default_who=("The product", "system", "system"))
                return None
            if clock.parse(session["expires"]) < _now() or user is None or not user.get("active", True):
                data["sessions"].pop(_hash_token(token))
                self._save(data)
                return None
            if session.get("pending"):  # a password still waiting for its code: nobody is signed in yet
                return None
            # the idle timer slides, but the file isn't rewritten on every request (at most every 5 minutes, or
            # every sixth of a short timeout); a timeout shortened on the Settings page applies from the next request
            idle = session_idle()
            left = clock.parse(session["expires"]) - _now()
            if user.get("role") != SUPPORT and (left < idle - min(timedelta(minutes=5), idle / 6) or left > idle):
                session["expires"] = (_now() + idle).isoformat()
                self._save(data)
            public = self._public(session["email"], user)
            return public | {"must_change": False} if session.get("how") else public  # signed in with Google: no password to change

    # -- support (src/support.py): the provider's person, let in by an attorney for hours ------------------------------------------------

    def let_support_in(self, email: str, name: str, hours: Any, plain: bool, by: str) -> tuple[str, dict[str, Any]]:
        """(the one-time sign-in code, the session) for the provider's support person: read-only, until now + hours (1 to 8), client values masked
        unless plain. The code works once and is never sent by the product: the attorney passes it on. Letting someone in again replaces the session."""
        email, name = (email or "").strip().lower(), " ".join(str(name or "").split())
        if "@" not in email:
            raise ValueError("Enter the support person's e-mail (the provider's).")
        if not name:
            raise ValueError("Enter the support person's name: every request they make is recorded under it.")
        try:
            hours = int(str(hours).strip())
        except ValueError:
            raise ValueError("The hours: a whole number from 1 to 8.") from None
        if not SUPPORT_HOURS[0] <= hours <= SUPPORT_HOURS[1]:
            raise ValueError("The hours: from 1 to 8.")
        code = secrets.token_urlsafe(12)
        with self._lock:
            data = self._load()
            old = data["users"].get(email)
            if old is not None and old.get("role") != SUPPORT:
                raise ValueError(f"{email} is a staff account: support needs an address of its own.")
            session = {"id": secrets.token_hex(6), "until": (_now() + timedelta(hours=hours)).isoformat(), "hours": hours, "plain": bool(plain),
                       "by": by, "at": _now().isoformat()}
            history = list((old or {}).get("support_history") or []) + ([old["support"]] if (old or {}).get("support") else [])
            data["users"][email] = {"name": name, "role": SUPPORT, "active": True, "created_at": (old or {}).get("created_at") or _now().isoformat(),
                                    "support": session, "support_history": history[-20:]}
            data["sessions"] = {k: s for k, s in data["sessions"].items() if s["email"] != email}
            self._save(data)
        self._set_support_code(email, code)
        self.log("support_let_in", email, by=by, hours=hours, values="plain" if plain else "masked", until=session["until"])
        events.record("support", "let_in", f"Let {name} in for support for {hours} hour{'s' if hours != 1 else ''}, client values "
                      + ("shown plain" if plain else "masked"), home=self.path.parent, who=by)
        return code, session

    def _set_support_code(self, email: str, code: str) -> None:
        salt = secrets.token_bytes(16)
        with _HASHING:
            hashed = _scrypt(code, salt)
        with self._lock:
            data = self._load()
            data["users"][email].update(salt=salt.hex(), hash=hashed, scrypt=dict(SCRYPT), must_change=False, failures=0, locked_until=None)
            self._save(data)

    def _end_support(self, data: dict[str, Any], email: str, by: str | None) -> dict[str, Any]:
        user = data["users"][email]
        user["active"] = False
        user.pop("hash", None)
        user["support"].update(ended_at=_now().isoformat(), ended_by=by)
        data["sessions"] = {k: s for k, s in data["sessions"].items() if s["email"] != email}
        return {"session": user["support"]["id"]} | ({"by": by} if by else {})

    def end_support(self, email: str, by: str) -> bool:
        """An attorney ends a support session early (every session of that account ends now). False when none was open."""
        email = (email or "").strip().lower()
        with self._lock:
            data = self._load()
            user = data["users"].get(email)
            if user is None or user.get("role") != SUPPORT or not user.get("active", True):
                return False
            ended = self._end_support(data, email, by)
            self._save(data)
        self.log("support_ended", email, how="ended by an attorney", **ended)
        events.record("support", "ended", f"Ended the support session of {data['users'][email]['name']} early", home=self.path.parent, who=by)
        return True

    def support_accounts(self) -> list[dict[str, Any]]:
        """Every support account, with its latest session (the Settings list; the history is src/support.py's)."""
        out = []
        with self._lock:
            data = self._load()
            for email, user in data["users"].items():
                if user.get("role") == SUPPORT:
                    if user.get("active", True) and not _support_open(user):
                        self._end_support(data, email, None)
                        self._save(data)
                        self.log("support_ended", email, how="its time ran out")
                        events.record("support", "ended", f"The support session of {user['name']} ended by itself at its hour", home=self.path.parent,
                                      default_who=("The product", "system", "system"))
                    for n, past in enumerate([*(user.get("support_history") or []), user.get("support") or {}]):
                        latest = n == len(user.get("support_history") or [])
                        out.append({"email": email, "name": user["name"], "open": latest and user.get("active", True), **past})
        return out

    def sign_out(self, token: str | None) -> None:
        with self._lock:
            data = self._load()
            session = data["sessions"].pop(_hash_token(token or ""), None)
            self._save(data)
        if session:
            self.log("signed_out", session["email"])

    def change_password(self, email: str, current: str, new: str) -> None:
        """The person's own change (first sign-in, or any time). Once the app is set up a password alone changes nothing:
        a password someone learned must not let them lock the person out (a reset from an attorney instead)."""
        _, user = self.sign_in(email, current)  # checks the current password, with the same lockout
        if user.get("second_factor") == "code":
            raise ValueError("With a code set up, your password is changed by a reset: ask your attorney.")
        if current == new:
            raise ValueError("Choose a password different from the one you were given.")
        self._set_password(email, new, must_change=False)
        self.log("password_changed", email)

    # -- the first attorney, from the screen ----------------------------------------------------------------------

    def needs_setup(self) -> bool:
        """True while no account exists at all: the screen offers "Set up the first attorney" instead of signing in. Accounts are
        turned off, never deleted, so once there is one this is False for good."""
        return not self._load()["users"]

    @staticmethod
    def _code_hash(code: str) -> str:
        return hashlib.sha256("".join(c for c in (code or "").upper() if c.isalnum()).encode()).hexdigest()

    def _new_code(self) -> tuple[str, dict[str, str]]:
        raw = "".join(secrets.choice(SETUP_ALPHABET) for _ in range(16))
        return "-".join(raw[i:i + 4] for i in range(0, 16, 4)), {"hash": self._code_hash(raw), "expires": (_now() + timedelta(days=SETUP_CODE_DAYS)).isoformat()}

    def new_setup_code(self) -> str:
        """The installer's one-time code for creating the first attorney (also users.py setup-code): 16 letters and digits (about 79 bits) in
        four groups, kept only as a SHA-256, good for SETUP_CODE_DAYS days or until the first account exists. A new one replaces the old; the code
        the review app printed at its start (console_setup_code) is kept."""
        with self._lock:
            data = self._load()
            if data["users"]:
                raise ValueError("Accounts already exist: the first attorney has been set up.")
            shown, record = self._new_code()
            console = (data.get("setup") or {}).get("console")
            data["setup"] = record | ({"console": console} if console else {})
            self._save(data)
        return shown

    def console_setup_code(self) -> str | None:
        """The code the review app prints each time it starts while no account exists (its window, the systemd journal or install/review.log; nowhere under
        the Windows task) (brief J2: the setup page's own second factor,
        so a forwarder on the same computer cannot make a stranger look like the computer itself). It replaces the code the app printed before, never
        the installer's. None once an account exists."""
        with self._lock:
            data = self._load()
            if data["users"]:
                return None
            shown, record = self._new_code()
            data.setdefault("setup", {})["console"] = record
            self._save(data)
        return shown

    def _setup_hashes(self) -> list[str]:
        """The setup codes on record that have not expired: the installer's (or users.py's) and the one the app printed at its start."""
        setup = self._load().get("setup") or {}
        out = []
        for record in (setup, setup.get("console") or {}):
            expires = clock.parse(record.get("expires"))
            if record.get("hash") and expires is not None and expires >= _now():
                out.append(record["hash"])
        return out

    def setup_open(self) -> bool:
        """Whether a setup code is on record and still good (under SETUP_CODE_DAYS old). Setting up the first attorney needs one for every way in:
        with none on record, nobody can until a new code is made (the installer, users.py setup-code, or the app's next start)."""
        return bool(self._setup_hashes())

    def setup_code_ok(self, code: str | None) -> bool:
        """Whether this is a setup code that was printed (constant time, against each one on record), still unused and unexpired."""
        if not code:
            return False
        typed = self._code_hash(code)
        return sum(hmac.compare_digest(typed, h) for h in self._setup_hashes()) > 0

    def create_first(self, email: str, name: str, password: str, code: str | None = None) -> tuple[str, dict[str, Any]]:
        """The first attorney, with the password they chose (no one-time password: they are the one typing it). Needs a setup code that was
        printed and has not expired, from whatever computer (the computer that runs the app included: a TCP forwarder on that computer makes a
        stranger's connection look like its own, brief J2); refused for good once any account exists. Every refusal is LookupError("not found"):
        the server answers as if there were no such page, so a stranger learns nothing, not even that no account exists yet. Signs them in.
        Returns (session token, user)."""
        email = (email or "").strip().lower()
        with self._first:  # one at a time: two requests cannot both find the list empty
            if not self.needs_setup():
                raise LookupError("not found")  # the server answers as if there were no such page
            if not self.setup_code_ok(code):
                self.log("setup_refused", email)
                raise LookupError("not found")
            if "@" not in email:
                raise ValueError("Use the person's work email as their sign-in.")
            if not (name or "").strip():
                raise ValueError("Enter the person's full name: it is recorded on every decision they make.")
            if len(password or "") < MIN_PASSWORD:
                raise ValueError(f"Use at least {MIN_PASSWORD} characters.")
            with self._lock:
                data = self._load()
                data["users"][email] = {"name": name.strip(), "role": "attorney", "active": True, "created_at": _now().isoformat()}
                data.pop("setup", None)  # the code is used up
                self._save(data)
            self._set_password(email, password, must_change=False)
            self.log("first_attorney_created", email, how="setup code")
        return self.sign_in(email, password)


def needs_attorney(item: dict[str, Any], action: str | None) -> bool:
    """Legal sign-off: the Attorney sign-off queue, and acknowledging a
    blocking issue (a paralegal may still correct a blocking value)."""
    group = item.get("group") or ""
    return group == "attorney" or group.startswith("rule:") or (item.get("level") == "blocking" and action == "acknowledge")
