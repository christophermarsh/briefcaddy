"""The second factor: codes from an authenticator app (src/review/totp.py, src/review/auth.py), the QR code that sets the app
up (src/review/qr.py), and the sign-in steps over HTTP (src/review/server.py). Everyone here is made up."""

from __future__ import annotations

import json
import os
import stat
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import clock
import second_factor
import settings
from review import auth, qr, totp
from review.auth import Accounts
import schema_path

_REPO = Path(__file__).resolve().parent.parent
PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)
NOW = datetime(2026, 10, 3, 14, 0, 15, tzinfo=timezone.utc)  # 15 seconds into a 30-second step


# -- the algorithm: RFC 4226 and RFC 6238's own test vectors ----------------------------------------------------------

def test_hotp_matches_rfc_4226_appendix_d():
    key = b"12345678901234567890"
    expected = ["755224", "287082", "359152", "969429", "338314", "254676", "287922", "162583", "399871", "520489"]
    assert [totp.hotp(key, c) for c in range(10)] == expected


# RFC 6238 Appendix B, Table 1 (8 digits, X = 30, T0 = 0). The seeds are the reference code's (Appendix A): 20 bytes for
# SHA-1, the same digits repeated to 32 bytes for SHA-256 and to 64 for SHA-512.
SEEDS = {"sha1": b"12345678901234567890", "sha256": b"12345678901234567890123456789012",
         "sha512": b"1234567890123456789012345678901234567890123456789012345678901234"}
RFC6238 = [(59, "94287082", "46119246", "90693936"), (1111111109, "07081804", "68084774", "25091201"),
           (1111111111, "14050471", "67062674", "99943326"), (1234567890, "89005924", "91819424", "93441116"),
           (2000000000, "69279037", "90698825", "38618901"), (20000000000, "65353130", "77737706", "47863826")]


@pytest.mark.parametrize("unix_time,sha1,sha256,sha512", RFC6238)
def test_totp_matches_rfc_6238_appendix_b(unix_time, sha1, sha256, sha512):
    for digest, want in (("sha1", sha1), ("sha256", sha256), ("sha512", sha512)):
        assert totp.code_at(SEEDS[digest], unix_time, digits=8, digest=digest) == want


def test_one_step_either_side_is_accepted_and_no_further():
    key, now = totp.new_secret(), NOW.timestamp()
    assert len(key) == 20  # 160 bits
    for steps, ok in ((-2, False), (-1, True), (0, True), (1, True), (2, False)):
        code = totp.code_at(key, now + steps * 30)
        assert (totp.match(key, code, now, None) is not None) == ok, steps
    step = totp.match(key, totp.code_at(key, now), now, None)
    assert totp.match(key, totp.code_at(key, now), now, step) is None  # the same code again: refused
    assert totp.match(key, totp.code_at(key, now + 30), now, step) == step + 1  # the next one: fine


def test_the_otpauth_address_follows_the_key_uri_format():
    key = bytes(range(20))
    uri = totp.uri(key, "ana@firm.example", "Exemplo LLP Case Review")
    assert uri == ("otpauth://totp/Exemplo%20LLP%20Case%20Review:ana@firm.example?secret=" + totp.b32(key)
                   + "&issuer=Exemplo%20LLP%20Case%20Review&algorithm=SHA1&digits=6&period=30")
    assert "=" not in totp.b32(key) and len(totp.b32(key)) == 32
    assert second_factor.secret_from(totp.grouped(key)) == key  # the letters on screen give back the secret


def test_the_qr_code_reads_back_even_with_damage():
    import numpy as np
    import zxingcpp

    uri = totp.uri(totp.new_secret(), "a.very.long.name.exemplo@firm-exemplo.example", "Georges Exemplo Cote LLP Case Review")
    grid = qr.matrix(uri)
    for damage in (0, 6):  # a few modules flipped: the error correction puts them right
        img = np.full((len(grid) + 8, len(grid) + 8), 255, dtype=np.uint8)
        for y, row in enumerate(grid):
            for x, dark in enumerate(row):
                img[y + 4, x + 4] = 0 if dark else 255
        for i in range(damage):
            x, y = 12 + i, len(grid) - 3  # in the data area, away from the finders and timing
            img[y + 4, x + 4] = 255 - img[y + 4, x + 4]
        found = zxingcpp.read_barcodes(np.kron(img, np.ones((4, 4), dtype=np.uint8)))
        assert found and found[0].text == uri, damage
    picture = qr.svg(uri)
    assert picture.startswith("<svg ") and "<script" not in picture


# -- accounts ------------------------------------------------------------------------------------------------------------

@pytest.fixture
def firm(tmp_path, monkeypatch):
    """Accounts, this test's own settings (the firm's choices), and a frozen clock."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(clock, "_now_override", NOW)
    monkeypatch.delenv(auth.KEY_ENV, raising=False)
    accounts = Accounts(tmp_path / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    return accounts


def _later(monkeypatch, seconds: float) -> None:
    monkeypatch.setattr(clock, "_now_override", clock._now_override + timedelta(seconds=seconds))


def _code(secret: bytes) -> str:
    return totp.code_at(secret, clock.utcnow().timestamp())


def test_an_attorney_sets_up_the_app_at_first_sign_in_and_a_paralegal_need_not(firm):
    token, user = firm.sign_in("jane@firm.example", PASSWORD)
    assert "second_factor" not in user and firm.session_user(token)["email"] == "jane@firm.example"
    token, user = firm.sign_in("sam@firm.example", PASSWORD)
    assert user["second_factor"] == "enrol" and firm.session_user(token) is None  # nothing opens before the app is set up
    shown = firm.enrol_start(token, "Exemplo LLP Case Review")
    assert firm.enrol_start(token, "Exemplo LLP Case Review") == shown  # the same secret while setting up (a reload)
    secret = second_factor.secret_from(shown["letters"])
    assert shown["uri"].startswith("otpauth://totp/Exemplo%20LLP%20Case%20Review:sam@firm.example?secret=" + totp.b32(secret))
    with pytest.raises(ValueError, match="didn't work"):
        firm.enrol_finish(token, "000000" if _code(secret) != "000000" else "111111")
    done = firm.enrol_finish(token, _code(secret))
    assert len(done["recovery_codes"]) == 8 and len(set(done["recovery_codes"])) == 8
    assert firm.session_user(done["token"])["code_set_up"] and firm.session_user(token) is None  # a new token: the old one is gone
    with pytest.raises(LookupError):
        firm.enrol_start(token, "x")  # shown only while setting up
    stored = firm.path.read_text(encoding="utf-8")
    assert totp.b32(secret) not in stored and secret.hex() not in stored  # encrypted at rest
    assert not any(c in stored or c.replace("-", "") in stored for c in done["recovery_codes"])  # kept hashed
    key = firm.key_path
    assert key.exists() and (os.name == "nt" or stat.S_IMODE(key.stat().st_mode) == 0o600)
    log = firm.log_path.read_text(encoding="utf-8")
    assert totp.b32(secret) not in log and _code(secret) not in log  # never a secret or a code in the log


def test_sign_in_asks_the_code_and_the_same_code_never_works_twice(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 30)
    token, user = firm.sign_in("sam@firm.example", PASSWORD)
    assert user["second_factor"] == "code" and firm.session_user(token) is None
    code = _code(secret)
    done = firm.verify_code(token, code)
    assert firm.session_user(done["token"])["role"] == "attorney" and done["device"] is None
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    with pytest.raises(ValueError) as again:
        firm.verify_code(token, code)  # a replay of the code just used (RFC 6238 section 5.2)
    with pytest.raises(ValueError) as wrong:
        firm.verify_code(token, "123456" if code != "123456" else "654321")
    with pytest.raises(ValueError) as recovery:
        firm.verify_code(token, "aaaa-bbbb-cccc-dddd")
    assert str(again.value) == str(wrong.value) == str(recovery.value) == auth.CODE_WRONG  # never which part was wrong
    _later(monkeypatch, 31)  # the next code is fine
    assert firm.session_user(firm.verify_code(token, _code(secret))["token"])


def test_a_drifted_phone_is_accepted_one_step_either_side(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 120)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    now = clock.utcnow().timestamp()
    with pytest.raises(ValueError):
        firm.verify_code(token, totp.code_at(secret, now - 60))  # two steps behind
    assert firm.verify_code(token, totp.code_at(secret, now - 30))["token"]  # one step behind
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    assert firm.verify_code(token, totp.code_at(secret, now + 30))["token"]  # one step ahead


def test_recovery_codes_work_once_each(firm):
    codes = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["recovery"]
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    done = firm.verify_code(token, " " + codes[0].upper().replace("-", " ") + " ")  # typed with spaces, in capitals
    assert firm.session_user(done["token"]) and done["recovery_left"] == 7
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    with pytest.raises(ValueError, match="didn't work"):
        firm.verify_code(token, codes[0])
    assert firm.verify_code(token, codes[1])["recovery_left"] == 6


def test_a_reset_clears_the_app_the_recovery_codes_and_remembered_devices(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 30)  # the code used to set the app up can't be used again
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    device = firm.verify_code(token, _code(secret), remember=True)["device"]
    temporary = firm.reset("sam@firm.example", by="ana@firm.example")
    sam = json.loads(firm.path.read_text(encoding="utf-8"))["users"]["sam@firm.example"]
    assert not {"totp", "totp_pending", "devices"} & set(sam)
    assert not next(u for u in firm.users() if u["email"] == "sam@firm.example")["code_set_up"]
    token, user = firm.sign_in("sam@firm.example", temporary, device)
    assert user["must_change"] and "second_factor" not in user  # first: their own password
    firm.change_password("sam@firm.example", temporary, "another long passphrase")
    token, user = firm.sign_in("sam@firm.example", "another long passphrase", device)
    assert user["second_factor"] == "enrol"  # then the app again; the old device skips nothing
    new = second_factor.secret_from(firm.enrol_start(token, "x")["letters"])
    assert new != secret


def test_wrong_codes_count_toward_the_lockout_and_a_password_does_not_clear_them(firm):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    wrong = "000000" if _code(secret) != "000000" else "111111"
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    for _ in range(auth.MAX_FAILURES - 2):
        with pytest.raises(ValueError, match="didn't work"):
            firm.verify_code(token, wrong)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)  # the right password again: the count stays
    with pytest.raises(ValueError, match="didn't work"):
        firm.verify_code(token, wrong)
    with pytest.raises(ValueError, match="locked"):
        firm.verify_code(token, wrong)  # the fifth wrong try in a row
    with pytest.raises(ValueError, match="locked for 15 minutes"):
        firm.verify_code(token, _code(secret))  # the step under way ended with the lock, and says so (never "that took too long")
    with pytest.raises(ValueError, match="locked"):
        firm.sign_in("sam@firm.example", PASSWORD)  # and the password is refused for 15 minutes too
    reasons = [json.loads(x).get("reason") for x in firm.log_path.read_text(encoding="utf-8").splitlines()]
    assert reasons.count("code") == auth.MAX_FAILURES


def test_the_step_after_the_password_runs_out_after_ten_minutes(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    _later(monkeypatch, 601)
    with pytest.raises(LookupError, match="took too long"):
        firm.verify_code(token, _code(secret))


def test_a_remembered_device_skips_the_code_for_that_account_for_30_days(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 30)  # the code used to set the app up can't be used again
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    device = firm.verify_code(token, _code(secret), remember=True)["device"]
    stored = firm.path.read_text(encoding="utf-8")
    assert device and device not in stored  # kept only as its SHA-256
    token, user = firm.sign_in("sam@firm.example", PASSWORD, device)
    assert "second_factor" not in user and firm.session_user(token)
    settings.save("sign_in", {"code_everyone": "yes"}, "Sam Attorney")  # everyone: Jane too
    jane_token, jane = firm.sign_in("jane@firm.example", PASSWORD, device)
    assert jane["second_factor"] == "enrol"  # Sam's device is Sam's alone
    _later(monkeypatch, 30 * 24 * 3600 + 60)
    token, user = firm.sign_in("sam@firm.example", PASSWORD, device)
    assert user["second_factor"] == "code"  # after 30 days, the code again


def test_the_firm_can_turn_remembering_off(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 30)  # the code used to set the app up can't be used again
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    device = firm.verify_code(token, _code(secret), remember=True)["device"]
    settings.save("sign_in", {"remember_device": "no"}, "Sam Attorney")
    assert firm.sign_in("sam@firm.example", PASSWORD, device)[1]["second_factor"] == "code"
    firm.forget_devices(by="sam@firm.example")  # what turning it off on the Staff section does: none kept for later
    assert "devices" not in json.loads(firm.path.read_text(encoding="utf-8"))["users"]["sam@firm.example"]


def test_with_a_code_set_up_a_password_alone_cannot_change_the_password(firm):
    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    with pytest.raises(ValueError, match="reset"):
        firm.change_password("sam@firm.example", PASSWORD, "someone else's choice")
    assert firm.sign_in("sam@firm.example", PASSWORD)[1]["second_factor"] == "code"  # unchanged


def test_microsoft_or_google_counts_as_the_second_factor(firm):
    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    token, _ = firm.session_for("sam@firm.example", "microsoft")
    assert firm.session_user(token)["role"] == "attorney"


def test_the_key_from_the_environment_and_a_rotation(firm, monkeypatch):
    from cryptography.fernet import Fernet

    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    old_file = firm.key_path.read_bytes()
    assert firm.rotate_key() == 1  # the key file: a new key made, the secret re-encrypted, the old key dropped
    assert firm.key_path.read_bytes() != old_file and len(firm.key_path.read_bytes().split()) == 1
    _later(monkeypatch, 30)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    assert firm.verify_code(token, _code(secret))["token"]
    # the environment's key: the new one first, the old one after it while the secrets are re-encrypted
    file_key, env_key = firm.key_path.read_bytes().strip().decode(), Fernet.generate_key().decode()
    monkeypatch.setenv(auth.KEY_ENV, f"{env_key},{file_key}")
    assert firm.rotate_key() == 1
    monkeypatch.setenv(auth.KEY_ENV, env_key)  # the old key removed
    _later(monkeypatch, 30)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    assert firm.verify_code(token, _code(secret))["token"]
    monkeypatch.setenv(auth.KEY_ENV, Fernet.generate_key().decode())  # a different key: the secret can't be read
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    with pytest.raises(ValueError, match="can't read"):
        firm.verify_code(token, _code(secret))
    monkeypatch.setenv(auth.KEY_ENV, "not-a-key")
    with pytest.raises(ValueError, match="can't read"):
        firm.verify_code(token, _code(secret))


# -- over HTTP: the sign-in steps, the cookies, the Staff section ----------------------------------------------------------

@pytest.fixture
def server(firm, tmp_path):
    from review.server import ReviewApp, make_handler, serve

    (tmp_path / "clients").mkdir()
    app = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=firm)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _post(url, body, cookie=None):
    headers = {"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=headers, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get_all("Set-Cookie") or []
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), []


def _get(url, cookie=None):
    req = urllib.request.Request(url, headers={"X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}))
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}")


def _named(cookies, name):
    return next((c for c in cookies if c.startswith(name + "=")), None)


def test_signing_in_over_http_sets_up_the_app_then_asks_the_code(server, firm, monkeypatch):
    status, body, cookies = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})
    assert status == 200 and body["user"]["second_factor"] == "enrol"
    pending = _named(cookies, auth.COOKIE).split(";")[0]
    assert _get(server + "/api/clients", pending)[0] == 401 and _get(server + "/api/me", pending)[1]["user"] is None
    status, shown, _ = _post(server + "/api/enrol", {}, pending)
    assert status == 200 and shown["qr"].startswith("<svg ") and shown["account"] == "sam@firm.example" and "uri" not in shown
    secret = second_factor.secret_from(shown["letters"])
    status, done, cookies = _post(server + "/api/enrol", {"code": _code(secret)}, pending)
    assert status == 200 and len(done["recovery_codes"]) == 8 and done["user"]["code_set_up"]
    session = _named(cookies, auth.COOKIE).split(";")[0]
    assert _get(server + "/api/clients", session)[0] == 200 and _get(server + "/api/clients", pending)[0] == 401
    assert _post(server + "/api/enrol", {}, session)[0] == 401  # the secret is never shown again
    # the next sign-in: the code, and "remember this device"
    _later(monkeypatch, 30)
    status, body, cookies = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})
    assert body["user"]["second_factor"] == "code" and body["remember_allowed"] is True
    pending = _named(cookies, auth.COOKIE).split(";")[0]
    status, body, _ = _post(server + "/api/code", {"code": "000000" if _code(secret) != "000000" else "111111"}, pending)
    assert status == 400 and body["error"] == auth.CODE_WRONG
    status, body, cookies = _post(server + "/api/code", {"code": _code(secret), "remember": True}, pending)
    device = _named(cookies, auth.DEVICE_COOKIE)
    assert status == 200 and "HttpOnly" in device and "SameSite=Strict" in device and "Path=/api/login" in device
    assert f"Max-Age={30 * 24 * 3600}" in device
    status, body, cookies = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD}, device.split(";")[0])
    assert status == 200 and "second_factor" not in body["user"]  # this browser, this account: no code for 30 days
    assert _get(server + "/api/clients", _named(cookies, auth.COOKIE).split(";")[0])[0] == 200
    assert _post(server + "/api/code", {"code": "123456"})[0:2] == (401, {"error": auth.CODE_EXPIRED, "sign_in": True})


def test_the_staff_section_shows_who_set_up_the_app_and_records_the_firms_choices(server, firm):
    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    status, body, cookies = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})
    sam = second_factor.finish(server, "sam@firm.example", _named(cookies, auth.COOKIE).split(";")[0])
    status, staff = _get(server + "/api/staff", sam)
    assert {p["email"]: p["code_set_up"] for p in staff["people"]} == {"jane@firm.example": False, "sam@firm.example": True}
    assert staff["second_factor"] == {"everyone": False, "remember": True, "updated_by": None, "updated_at": None}
    status, after, _ = _post(server + "/api/staff", {"action": "second_factor", "everyone": True, "remember": False}, sam)
    assert status == 200 and after["second_factor"]["everyone"] and not after["second_factor"]["remember"]
    assert after["second_factor"]["updated_by"] == "Sam Attorney"
    assert settings.values("sign_in") == {"code_everyone": "yes", "remember_device": "no"}
    log = [json.loads(x) for x in firm.log_path.read_text(encoding="utf-8").splitlines()]
    assert any(r["event"] == "second_factor_changed" and r["everyone"] and not r["remember"] and r["by"] == "sam@firm.example" for r in log)
    status, body, _ = _post(server + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})
    assert body["user"]["second_factor"] == "enrol"  # everyone now
    jane = firm.session_for("jane@firm.example", "test")[0]
    assert _post(server + "/api/staff", {"action": "second_factor", "everyone": False, "remember": True}, f"{auth.COOKIE}={jane}")[0] == 403


# -- fixes from verification (10/03/2026) ---------------------------------------------------------------------------------

def _wrong(secret: bytes) -> str:
    return "000000" if _code(secret) != "000000" else "111111"


def test_each_password_sign_in_shows_its_own_secret_and_an_earlier_one_cannot_enrol(firm, monkeypatch):
    """Someone who knows the password opens the set-up step and copies the secret: the attorney's own sign-in later shows a
    different one, and the copied secret can't be set up on the attorney's step."""
    copier, _ = firm.sign_in("sam@firm.example", PASSWORD)
    copied = second_factor.secret_from(firm.enrol_start(copier, "x")["letters"])
    assert second_factor.secret_from(firm.enrol_start(copier, "x")["letters"]) == copied  # a reload in the same window
    attorney, _ = firm.sign_in("sam@firm.example", PASSWORD)
    own = second_factor.secret_from(firm.enrol_start(attorney, "x")["letters"])
    assert own != copied
    with pytest.raises(ValueError, match="didn't work"):
        firm.enrol_finish(attorney, _code(copied))  # the copied secret's code doesn't set up the attorney's step
    assert "totp_pending" not in json.loads(firm.path.read_text(encoding="utf-8"))["users"]["sam@firm.example"]  # never on the account
    firm.enrol_finish(attorney, _code(own))
    _later(monkeypatch, 30)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    with pytest.raises(ValueError, match="didn't work"):
        firm.verify_code(token, _code(copied))


def test_a_set_up_step_left_open_cannot_replace_the_app_once_it_is_set_up(firm, monkeypatch):
    left_open, _ = firm.sign_in("sam@firm.example", PASSWORD)
    stale = second_factor.secret_from(firm.enrol_start(left_open, "x")["letters"])
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]  # the attorney sets the app up in another window
    with pytest.raises(LookupError):
        firm.enrol_start(left_open, "x")
    with pytest.raises(LookupError):
        firm.enrol_finish(left_open, _code(stale))
    # even a set-up step that outlived the set-up is refused: the account has a code now
    data = json.loads(firm.path.read_text(encoding="utf-8"))
    data["sessions"][auth._hash_token("planted")] = {"email": "sam@firm.example", "expires": (auth._now() + auth.PENDING).isoformat(),
                                                     "pending": "enrol"}
    firm.path.write_text(json.dumps(data), encoding="utf-8")
    with pytest.raises(LookupError):
        firm.enrol_start("planted", "x")
    with pytest.raises(LookupError):
        firm.enrol_finish("planted", _code(secret))
    _later(monkeypatch, 30)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    assert firm.verify_code(token, _code(secret))["token"]  # the person's own app still works


def test_an_old_set_up_window_answers_401_over_http(server, firm):
    _, _, cookies = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})
    left_open = _named(cookies, auth.COOKIE).split(";")[0]
    assert _post(server + "/api/enrol", {}, left_open)[0] == 200
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    assert _post(server + "/api/enrol", {}, left_open)[0] == 401
    assert _post(server + "/api/enrol", {"code": _code(secret)}, left_open)[0] == 401


def test_a_wrong_code_at_set_up_does_not_mention_recovery_codes(firm):
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    secret = second_factor.secret_from(firm.enrol_start(token, "x")["letters"])
    with pytest.raises(ValueError) as wrong:
        firm.enrol_finish(token, _wrong(secret))
    assert str(wrong.value) == auth.CODE_WRONG_SETUP and "recovery" not in str(wrong.value)


def test_a_code_lockout_never_signs_out_the_person_already_signed_in(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]
    _later(monkeypatch, 30)
    token, _ = firm.sign_in("sam@firm.example", PASSWORD)
    signed_in = firm.verify_code(token, _code(secret))["token"]
    guesser, _ = firm.sign_in("sam@firm.example", PASSWORD)  # someone who knows the password
    for _ in range(auth.MAX_FAILURES - 1):
        with pytest.raises(ValueError, match="didn't work"):
            firm.verify_code(guesser, _wrong(secret))
    with pytest.raises(ValueError, match="locked for 15 minutes"):
        firm.verify_code(guesser, _wrong(secret))
    assert firm.session_user(signed_in)["email"] == "sam@firm.example"  # Sam keeps working
    with pytest.raises(ValueError, match="locked for 15 minutes"):
        firm.verify_code(guesser, _code(secret))  # the guesser's step is over: even the right code is refused, with the lock's own sentence


def test_a_lockout_again_the_same_day_lasts_longer_and_is_logged(firm, monkeypatch):
    secret = second_factor.set_up(firm, "sam@firm.example", PASSWORD)["secret"]

    def lock_out() -> str:
        token, _ = firm.sign_in("sam@firm.example", PASSWORD)
        for _ in range(auth.MAX_FAILURES - 1):
            with pytest.raises(ValueError, match="didn't work"):
                firm.verify_code(token, _wrong(secret))
        with pytest.raises(ValueError, match="locked") as locked:
            firm.verify_code(token, _wrong(secret))
        return str(locked.value)

    for minutes, said in ((15, "15 minutes"), (30, "30 minutes"), (60, "1 hour"), (120, "2 hours"), (240, "4 hours"), (240, "4 hours")):  # to four hours (brief J2)
        assert f"locked for {said}" in lock_out()
        _later(monkeypatch, minutes * 60 - 1)
        with pytest.raises(ValueError, match=f"locked for {said}"):
            firm.sign_in("sam@firm.example", PASSWORD)  # a second before the end: still locked
        _later(monkeypatch, 2)
    assert next(u for u in firm.users() if u["email"] == "sam@firm.example")["locks_today"] == 6
    rows = [json.loads(x) for x in firm.log_path.read_text(encoding="utf-8").splitlines()]
    locks = [(r["event"], r["minutes"], r["times_today"]) for r in rows if r["event"].startswith("account_locked")]
    assert locks == [("account_locked", 15, 1), ("account_locked_again", 30, 2), ("account_locked_again", 60, 3), ("account_locked_again", 120, 4),
                     ("account_locked_again", 240, 5), ("account_locked_again", 240, 6)]
    assert [r["times"] for r in rows if r["event"] == "lockout_alert"] == [3]  # the third of the day is an alert, once
    _later(monkeypatch, 24 * 3600)  # a day later: 15 minutes again
    assert "locked for 15 minutes" in lock_out()
    _later(monkeypatch, 16 * 60)  # wrong passwords count the same way: a second lockout today, 30 minutes
    for _ in range(auth.MAX_FAILURES - 1):
        with pytest.raises(ValueError, match="don't match"):
            firm.sign_in("sam@firm.example", "not the password!")
    with pytest.raises(ValueError, match="locked for 30 minutes"):  # Implementation note.
        firm.sign_in("sam@firm.example", "not the password!")
    with pytest.raises(ValueError, match="locked for 30 minutes"):
        firm.sign_in("sam@firm.example", PASSWORD)


def test_rotate_key_refuses_where_the_key_is_in_the_environment(firm):
    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    firm.key_path.unlink()  # an installation that keeps its key in the environment has no key file; the command ran without it
    before = firm.path.read_text(encoding="utf-8")
    with pytest.raises(ValueError, match="same environment"):
        firm.rotate_key()
    assert not firm.key_path.exists() and firm.path.read_text(encoding="utf-8") == before  # no stray key file, nothing changed


def test_the_settings_route_refuses_the_staff_sections_choices(server, firm):
    sam = f"{auth.COOKIE}={firm.session_for('sam@firm.example', 'test')[0]}"
    for values in ({"code_everyone": "yes"}, {"remember_device": "no"}, {"idle_minutes": "45", "code_everyone": "yes"}):
        status, body, _ = _post(server + "/api/settings", {"section": "sign_in", "values": values, "reviewer": "Sam Attorney"}, sam)
        assert status == 400 and "Staff section" in body["error"], values
    assert settings.values("sign_in") == {}  # nothing saved
    assert _post(server + "/api/settings", {"section": "sign_in", "values": {"idle_minutes": "45"}, "reviewer": "Sam Attorney"}, sam)[0] == 200


def test_requiring_a_code_for_everyone_signs_out_those_without_the_app(server, firm):
    sam = f"{auth.COOKIE}={firm.session_for('sam@firm.example', 'test')[0]}"  # (Microsoft or Google: counts as the second factor)
    _, _, cookies = _post(server + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})
    jane = _named(cookies, auth.COOKIE).split(";")[0]
    assert _get(server + "/api/clients", jane)[0] == 200
    status, body, _ = _post(server + "/api/staff", {"action": "second_factor", "everyone": True, "remember": True}, sam)
    assert status == 200 and body["signed_out"] == 1
    assert _get(server + "/api/clients", jane)[0] == 401  # signed out at once
    assert _get(server + "/api/clients", sam)[0] == 200
    assert _post(server + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})[1]["user"]["second_factor"] == "enrol"
    status, body, _ = _post(server + "/api/staff", {"action": "second_factor", "everyone": True, "remember": True}, sam)
    assert "signed_out" not in body  # already on: nobody signed out again


def test_the_code_screen_names_the_entry_as_the_app_shows_it(server, firm):
    from review.server import ReviewApp

    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    body = _post(server + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})[1]
    assert body["user"]["second_factor"] == "code" and body["issuer"] == ReviewApp.code_issuer(None)  # the set-up address's issuer


# -- "What staff did" (review/oversight.py) learns the second factor's rows ---------------------------------------------------

def _logged_events() -> set[str]:
    """Every event name the staff accounts and the review app write to the access log, read from their own code."""
    import re

    names = set()
    for path in (_REPO / "src" / "review" / "auth.py", _REPO / "src" / "review" / "server.py"):
        names |= set(re.findall(r'\.log\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    return names | {"account_locked", "account_locked_again"}  # written through _log_lock's conditional


def test_every_access_log_event_has_its_own_words_on_what_staff_did(monkeypatch):
    from review import oversight

    events = _logged_events()
    assert {"password_accepted", "code_set_up", "second_factor_changed", "devices_forgotten", "signed_out_for_code", "code_key_rotated"} <= events
    log = oversight.StaffLog.__new__(oversight.StaffLog)
    with monkeypatch.context() as m:
        m.setattr(oversight, "_fallback", lambda event: "NO WORDS")
        assert log._what({"event": "a_made_up_event"}, {}) == "NO WORDS"  # the generic words, seen
        for event in sorted(events):
            row = {"event": event, "email": "sam@firm.example", "at": "2026-10-03T14:00:00+00:00"}
            assert log._what(row, {"sam@firm.example": "Sam Attorney"}) != "NO WORDS", f"{event}: no words of its own on What staff did"
    for event in sorted(events):
        row = {"event": event, "email": "sam@firm.example", "at": "2026-10-03T14:00:00+00:00"}
        assert oversight._access_fields(row)[2] != "other", f"{event}: listed under Other"
    say = lambda **row: log._what({"at": "2026-10-03T14:00:00+00:00", "email": "sam@firm.example"} | row, {})  # noqa: E731
    assert say(event="sign_in_failed", reason="code") == "Wrong code at sign-in"
    assert say(event="signed_in", how="code") == "Signed in with the app's code"
    assert say(event="signed_in", how="recovery code") == "Signed in with a recovery code"
    assert say(event="signed_in", how="remembered device") == "Signed in on a remembered computer"
    assert say(event="signed_in", how="microsoft") == "Signed in with Microsoft"
    assert say(event="account_locked", minutes=15, times_today=1) == "Account locked for 15 minutes after 5 wrong passwords or codes in a row"
    assert say(event="account_locked") == "Account locked for 15 minutes after 5 wrong passwords or codes in a row"  # an older row
    assert say(event="account_locked_again", minutes=30, times_today=2) == "Locked 2 times today: account locked for 30 minutes after 5 wrong passwords or codes in a row"
    # the lockout is listed under the person locked out, not under Other or nobody
    assert oversight._access_fields({"event": "account_locked_again", "email": "sam@firm.example", "at": "2026-10-03T14:00:00+00:00"})[1:3] == ("sam@firm.example", "lockout")
    for text in [say(event=e, sessions=2, secrets=3, everyone=True, remember=False, next="code") for e in sorted(_logged_events())]:
        assert "—" not in text and " -- " not in text and "_" not in text, text


def test_lockouts_show_on_what_staff_did_and_on_the_staff_list(server, firm, monkeypatch):
    second_factor.set_up(firm, "sam@firm.example", PASSWORD)
    for _ in range(2):  # Jane: two lockouts today (wrong passwords), the second 30 minutes
        for n in range(auth.MAX_FAILURES):
            with pytest.raises(ValueError, match="locked for" if n == auth.MAX_FAILURES - 1 else "don't match"):  # the fifth says it locked
                firm.sign_in("jane@firm.example", "not the password!")
        _later(monkeypatch, 16 * 60)
    sam = f"{auth.COOKIE}={firm.session_for('sam@firm.example', 'test')[0]}"
    status, staff = _get(server + "/api/staff", sam)
    assert next(p for p in staff["people"] if p["email"] == "jane@firm.example")["locks_today"] == 2  # "Locked 2 times today" on the list
    status, log = _get(server + "/api/staff_log?kind=lockout", sam)
    assert status == 200
    shown = [(r["who"], r["what"]) for r in log["rows"]]
    assert ("Jane Doe", "Locked 2 times today: account locked for 30 minutes after 5 wrong passwords or codes in a row") in shown
    assert ("Jane Doe", "Account locked for 15 minutes after 5 wrong passwords or codes in a row") in shown
    status, log = _get(server + "/api/staff_log?kind=second_factor", sam)
    assert [r["what"] for r in log["rows"]] == ["Set up the authenticator app (eight recovery codes shown once)"]


def test_a_request_body_in_the_wrong_shape_is_refused_in_words(server, firm):
    sam = f"{auth.COOKIE}={firm.session_for('sam@firm.example', 'test')[0]}"
    for body in ({"section": "sign_in", "values": [["idle_minutes", "45"]], "reviewer": "Sam Attorney"},
                 {"section": "sign_in", "values": [{"idle_minutes": "45"}], "reviewer": "Sam Attorney"}):
        status, answer, _ = _post(server + "/api/settings", body, sam)
        assert status == 400 and "name and value" in answer["error"]
    status, answer, _ = _post(server + "/api/settings", [["section", "sign_in"]], sam)  # not even an object
    assert status == 400 and "Reload the page" in answer["error"]
    assert _get(server + "/api/staff", sam)[0] == 200  # the server keeps answering
