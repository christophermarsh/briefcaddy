"""Staff accounts and roles in the review app (src/review/auth.py)."""

import json
import threading
import urllib.error
import urllib.request

import pytest

import second_factor

from review import auth
from review.auth import Accounts, needs_attorney
from review.server import ReviewApp, make_handler, serve
from test_review import _REPO, TEMPLATE, client  # noqa: F401 -- the review app's test client
import schema_path

PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def accounts(tmp_path):
    return Accounts(tmp_path / "users.json")


def test_a_new_account_must_choose_its_own_password(accounts):
    temporary = accounts.add("Jane@Firm.example", "Jane Doe", "paralegal")
    token, user = accounts.sign_in("jane@firm.example", temporary)
    assert user["must_change"] and user["name"] == "Jane Doe"
    with pytest.raises(ValueError):
        accounts.change_password("jane@firm.example", temporary, "short")
    accounts.change_password("jane@firm.example", temporary, PASSWORD)
    assert accounts.session_user(token) is None  # a password change ends old sessions
    token, user = accounts.sign_in("jane@firm.example", PASSWORD)
    assert not user["must_change"] and accounts.session_user(token)["role"] == "paralegal"
    stored = json.loads(accounts.path.read_text())
    assert PASSWORD not in accounts.path.read_text() and token not in json.dumps(stored["sessions"])


def test_wrong_passwords_lock_the_account_and_say_nothing_about_who_exists(accounts):
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    with pytest.raises(ValueError) as unknown:
        accounts.sign_in("nobody@firm.example", "whatever it is here")
    with pytest.raises(ValueError) as wrong:
        accounts.sign_in("jane@firm.example", "not the password!")
    assert str(unknown.value) == str(wrong.value)
    for _ in range(auth.MAX_FAILURES - 1):
        with pytest.raises(ValueError):
            accounts.sign_in("jane@firm.example", "not the password!")
    with pytest.raises(ValueError, match="locked"):
        accounts.sign_in("jane@firm.example", temporary)  # even the right one, until the lock ends
    accounts.reset("jane@firm.example")  # an administrator can let them back in
    log = [json.loads(line)["event"] for line in accounts.log_path.read_text().splitlines()]
    assert log.count("sign_in_failed") >= auth.MAX_FAILURES and "password_reset" in log


def test_disabling_or_changing_a_role_takes_effect_immediately(accounts):
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    accounts.change_password("jane@firm.example", temporary, PASSWORD)
    token, _ = accounts.sign_in("jane@firm.example", PASSWORD)
    accounts.update("jane@firm.example", role="attorney")
    assert accounts.session_user(token) is None
    token, user = accounts.sign_in("jane@firm.example", PASSWORD)
    assert user["role"] == "attorney"
    accounts.update("jane@firm.example", active=False)
    assert accounts.session_user(token) is None
    with pytest.raises(ValueError, match="turned off"):
        accounts.sign_in("jane@firm.example", PASSWORD)
    with pytest.raises(ValueError):
        accounts.add("sam@firm.example", "Sam", "partner")  # only the two roles


def test_legal_sign_off_is_the_attorneys():
    assert needs_attorney({"group": "attorney", "level": "review"}, "acknowledge")
    assert needs_attorney({"group": "rule:OVERSTAY-01", "level": "review"}, "confirm")
    assert needs_attorney({"group": "blocking", "level": "blocking"}, "acknowledge")
    assert not needs_attorney({"group": "blocking", "level": "blocking"}, "set")  # a paralegal may correct the value
    assert not needs_attorney({"group": "needs_input", "level": "review"}, "set")


# --- the server with accounts ------------------------------------------------------


@pytest.fixture
def staff_server(client, tmp_path):  # noqa: F811
    accounts = Accounts(tmp_path / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)  # an attorney signs in with a code
    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}", accounts
    httpd.shutdown()


def _call(url, body=None, cookie=None):
    headers = {"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), None


def _sign_in(base, email):
    status, body, cookie = _call(base + "/api/login", {"email": email, "password": PASSWORD})
    assert status == 200 and "HttpOnly" in cookie and "SameSite=Strict" in cookie
    if body["user"].get("second_factor"):  # an attorney: the code from the app next
        return second_factor.finish(base, email, cookie.split(";")[0])
    return cookie.split(";")[0]


def test_nothing_is_served_without_signing_in(staff_server):
    base, _ = staff_server
    assert _call(base + "/api/clients")[0] == 401
    assert _call(base + "/api/items?client=demo")[0] == 401
    status, me, _ = _call(base + "/api/me")
    assert status == 200 and me == {"accounts": True, "user": None, "firm": ""}  # the firm name (none saved) is how the sign-in screen names it
    status, body, _ = _call(base + "/api/login", {"email": "jane@firm.example", "password": "wrong password!!"})
    assert status == 400
    cookie = _sign_in(base, "jane@firm.example")
    assert _call(base + "/api/clients", cookie=cookie)[0] == 200
    assert _call(base + "/api/logout", {}, cookie=cookie)[0] == 200
    assert _call(base + "/api/clients", cookie=cookie)[0] == 401


def test_a_one_time_password_only_reaches_the_change_password_step(staff_server):
    base, accounts = staff_server
    temporary = accounts.add("new@firm.example", "New Person", "paralegal")
    status, body, cookie = _call(base + "/api/login", {"email": "new@firm.example", "password": temporary})
    assert status == 200 and body["user"]["must_change"]
    assert _call(base + "/api/clients", cookie=cookie.split(";")[0])[0] == 403
    status, body, cookie = _call(base + "/api/password", {"email": "new@firm.example", "password": temporary, "new_password": PASSWORD})
    assert status == 200 and not body["user"]["must_change"]
    assert _call(base + "/api/clients", cookie=cookie.split(";")[0])[0] == 200


def test_decisions_are_recorded_under_the_signed_in_name_and_role(staff_server, client, monkeypatch):  # noqa: F811
    base, _ = staff_server
    jane = _sign_in(base, "jane@firm.example")
    import critical_review
    def reviewed_fingerprints(cookie, key):
        for name in ("license.pdf", "q.pdf"):
            request = urllib.request.Request(base + "/api/file?client=demo&doc=" + name, headers={"Cookie": cookie, "X-Review-App": "1"})
            with urllib.request.urlopen(request) as response:
                assert response.status == 200 and response.read().startswith(b"%PDF")
        current = critical_review.context(client)[key]
        assert current["bound"], current["reasons"]
        return {key: current["fingerprint"]}
    status, _, _ = _call(base + "/api/decide", {"client": "demo", "item_id": "fact:applicant.height", "action": "set",
                                                "values": {"applicant.height": "5'4\""}, "reviewer": "Someone Else", "evidence_fingerprints": reviewed_fingerprints(jane, "applicant.height")}, cookie=jane)
    assert status == 200
    decision = json.loads((client / "decisions.json").read_text())["fact:applicant.height"]
    assert decision["reviewer"] == "Jane Doe" and decision["role"] == "paralegal"

    # an item that needs legal sign-off: the paralegal is refused, the attorney isn't
    import review.server

    monkeypatch.setattr(review.server, "needs_attorney", lambda item, action: item.get("id") == "fact:applicant.weight_lbs" or (item.get("item") or {}).get("id") == "fact:applicant.weight_lbs")
    body = {"client": "demo", "item_id": "fact:applicant.weight_lbs", "action": "confirm"}
    status, err, _ = _call(base + "/api/decide", body, cookie=jane)
    assert status == 403 and "attorney" in err["error"]
    sam = _sign_in(base, "sam@firm.example")
    body["evidence_fingerprints"] = reviewed_fingerprints(sam, "applicant.weight_lbs")
    assert _call(base + "/api/decide", body, cookie=sam)[0] == 200
    assert _call(base + "/api/undo", {"client": "demo", "item_id": "fact:applicant.weight_lbs"}, cookie=jane)[0] == 403
    # recording a mailing (src/prefile.py): the attorney only; no packet was built here, so the attorney gives the reason
    mailed = {"client": "demo", "filing": "i485", "mailed_on": "2026-01-05", "carrier": "USPS", "tracking": "9400100000000000000000",
              "override": "test: mailed without the system's packet"}
    assert _call(base + "/api/filed", mailed, cookie=jane)[0] == 403
    assert _call(base + "/api/filed", mailed, cookie=sam)[0] == 200


def test_the_office_network_needs_accounts(client):  # noqa: F811
    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    with pytest.raises(SystemExit):
        serve(app, 0, host="0.0.0.0")


def test_only_the_configured_names_are_answered(staff_server):
    base, _ = staff_server
    port = base.rsplit(":", 1)[1]
    req = urllib.request.Request(base + "/api/me", headers={"Host": f"evil.example:{port}"})
    with pytest.raises(urllib.error.HTTPError) as e:
        urllib.request.urlopen(req)
    assert e.value.code == 403


# --- the password hash, the idle timeout ------------------------------------------------


def test_passwords_are_hashed_at_owasps_scrypt_cost_and_old_hashes_are_upgraded_at_sign_in(accounts):
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    accounts.change_password("jane@firm.example", temporary, PASSWORD)
    stored = json.loads(accounts.path.read_text())["users"]["jane@firm.example"]
    assert stored["scrypt"] == {"n": 2**17, "r": 8, "p": 1} == auth.SCRYPT  # OWASP Password Storage Cheat Sheet (read 2026-10-02)
    # an account from before: hashed at N=2^14, nothing stored beside the hash
    data = json.loads(accounts.path.read_text())
    salt = bytes(range(16))
    data["users"]["jane@firm.example"] |= {"salt": salt.hex(), "hash": auth._scrypt(PASSWORD, salt, auth.LEGACY_SCRYPT)}
    del data["users"]["jane@firm.example"]["scrypt"]
    accounts.path.write_text(json.dumps(data))
    with pytest.raises(ValueError, match="don't match"):
        accounts.sign_in("jane@firm.example", "not the password!")  # still checked at its own cost...
    assert "scrypt" not in json.loads(accounts.path.read_text())["users"]["jane@firm.example"]  # ...and not upgraded by a wrong one
    token, _ = accounts.sign_in("jane@firm.example", PASSWORD)
    upgraded = json.loads(accounts.path.read_text())["users"]["jane@firm.example"]
    assert upgraded["scrypt"] == auth.SCRYPT and upgraded["salt"] != salt.hex() and accounts.session_user(token)
    assert "password_rehashed" in accounts.log_path.read_text()
    accounts.sign_in("jane@firm.example", PASSWORD)  # and it still signs in afterwards


def test_the_idle_timeout_is_the_attorneys_setting_and_slides(accounts, tmp_path, monkeypatch):
    from datetime import datetime, timedelta, timezone

    import settings

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    assert auth.session_idle() == timedelta(minutes=30)  # the shipped value
    with pytest.raises(ValueError, match="from 5 to 600"):
        settings.save("sign_in", {"idle_minutes": "2"}, "Sam Attorney")
    with pytest.raises(ValueError):
        settings.save("sign_in", {"idle_minutes": "an hour"}, "Sam Attorney")
    settings.save("sign_in", {"idle_minutes": "45"}, "Sam Attorney")
    assert auth.session_idle() == timedelta(minutes=45)
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    accounts.change_password("jane@firm.example", temporary, PASSWORD)
    start = datetime(2026, 10, 2, 9, 0, tzinfo=timezone.utc)
    clock = {"now": start}
    monkeypatch.setattr(auth, "_now", lambda: clock["now"])
    token, _ = accounts.sign_in("jane@firm.example", PASSWORD)
    clock["now"] = start + timedelta(minutes=40)
    assert accounts.session_user(token)  # used within 45 minutes: the time starts again
    clock["now"] = start + timedelta(minutes=80)
    assert accounts.session_user(token)
    clock["now"] = start + timedelta(minutes=80 + 46)
    assert accounts.session_user(token) is None  # 46 minutes untouched: signed out
    settings.save("sign_in", {"idle_minutes": "10"}, "Sam Attorney")
    token, _ = accounts.sign_in("jane@firm.example", PASSWORD)
    clock["now"] += timedelta(minutes=11)
    assert accounts.session_user(token) is None


# --- the review app facing a network --------------------------------------------------


def _raw(base, method, path, headers=None):
    """The status for a request whose Content-Length promises a body that is never sent."""
    import http.client

    host, port = base.rsplit("/", 1)[1].split(":")
    conn = http.client.HTTPConnection(host, int(port), timeout=10)
    conn.putrequest(method, path, skip_host=True)
    for k, v in (headers or {}).items():
        conn.putheader(k, v)
    conn.endheaders()
    status = conn.getresponse().status
    conn.close()
    return status


def test_an_oversized_body_is_refused_before_it_is_read(staff_server):
    base, _ = staff_server
    json_headers = {"Host": base.split("//")[1], "Content-Type": "application/json", "X-Review-App": "1"}
    # signing in is open to anyone: 16 KB at most, refused without waiting for the body (none is sent)
    assert _raw(base, "POST", "/api/login", json_headers | {"Content-Length": str(10**9)}) == 413
    assert _raw(base, "POST", "/api/login", json_headers | {"Content-Length": str(20 * 1024)}) == 413
    assert _raw(base, "POST", "/api/decide", json_headers | {"Content-Length": str(2 * 1024 * 1024)}) == 413
    assert _raw(base, "POST", "/api/login", json_headers | {"Content-Length": "-5"}) == 400
    assert _raw(base, "POST", "/api/login", json_headers | {"Content-Length": "²"}) == 400  # a digit to isdigit(), not to int()
    assert _call(base + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})[0] == 200  # an ordinary sign-in fits
    assert make_handler(None, 0).timeout == 30  # a client that stalls mid-request is dropped


def test_password_attempts_are_limited_per_address(staff_server, monkeypatch):
    import review.server as server

    base, _ = staff_server
    monkeypatch.setattr(server, "ATTEMPTS", server.ATTEMPTS | {"/api/login": 3})
    tries = [_call(base + "/api/login", {"email": "jane@firm.example", "password": "not the password!"}) for _ in range(4)]
    assert [t[0] for t in tries] == [400, 400, 400, 429] and "Wait a minute" in tries[3][1]["error"]
    assert _call(base + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})[0] == 429  # this address waits


def test_a_password_check_never_keeps_signed_in_staff_waiting(accounts, monkeypatch):
    """The hash (about 0.3 s) runs outside the accounts lock: a sign-in in progress doesn't stall everyone else's requests."""
    temporary = accounts.add("jane@firm.example", "Jane Doe", "paralegal")
    accounts.change_password("jane@firm.example", temporary, PASSWORD)
    token, _ = accounts.sign_in("jane@firm.example", PASSWORD)
    real, hashing = auth._scrypt, threading.Event()

    def slow(*a, **kw):
        hashing.set()
        import time

        time.sleep(1.0)
        return real(*a, **kw)

    monkeypatch.setattr(auth, "_scrypt", slow)
    attempt = threading.Thread(target=lambda: pytest.raises(ValueError, accounts.sign_in, "jane@firm.example", "not the password!"))
    attempt.start()
    assert hashing.wait(5)
    import time

    started = time.monotonic()
    assert accounts.session_user(token)["name"] == "Jane Doe"
    assert time.monotonic() - started < 0.5 and attempt.is_alive()  # answered while the attempt was still hashing
    attempt.join()


def _server(app):
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}"


def _staff_app(client, folder, **kw):  # noqa: F811
    accounts = Accounts(folder / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)  # an attorney signs in with a code
    return ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=accounts, **kw)


def _sign_in_headers(base, extra=None):
    req = urllib.request.Request(base + "/api/login", data=json.dumps({"email": "jane@firm.example", "password": PASSWORD}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1"} | (extra or {}), method="POST")
    with urllib.request.urlopen(req) as r:
        return r.headers


def test_behind_the_firms_tls_proxy_cookies_are_secure_and_hsts_is_sent(client, tmp_path):  # noqa: F811
    # plain HTTP on this machine: no HSTS, no Secure; never framed either way
    httpd, base = _server(_staff_app(client, tmp_path))
    h = _sign_in_headers(base, {"X-Forwarded-Proto": "https"})  # nobody was declared a proxy: the header is not believed
    assert h["X-Frame-Options"] == "DENY" and "Strict-Transport-Security" not in h and "Secure" not in h["Set-Cookie"]
    assert "Max-Age" not in h["Set-Cookie"]  # lasts while the browser is open; the server ends it when unused
    httpd.shutdown()
    for name, kw, extra, secure in (("p", {"trusted_proxies": ("127.0.0.1",)}, {"X-Forwarded-Proto": "https"}, True),  # the proxy, declared
                                    ("q", {"trusted_proxies": ("127.0.0.1",)}, {}, False),  # the same proxy, a plain HTTP request
                                    ("t", {"behind_tls": True}, {}, True)):  # --behind-tls-proxy
        (tmp_path / name).mkdir()
        httpd, base = _server(_staff_app(client, tmp_path / name, **kw))
        h = _sign_in_headers(base, extra)
        assert ("Strict-Transport-Security" in h) == secure and ("; Secure" in h["Set-Cookie"]) == secure, name
        assert not secure or h["Strict-Transport-Security"] == "max-age=31536000; includeSubDomains"
        httpd.shutdown()
    (tmp_path / "s").mkdir()
    httpd, base = _server(_staff_app(client, tmp_path / "s", secure_cookies=True))  # --secure-cookies keeps working
    assert "; Secure" in _sign_in_headers(base)["Set-Cookie"]
    httpd.shutdown()


def test_every_opening_of_a_client_file_is_logged_and_the_attorney_can_see_who(client, tmp_path):  # noqa: F811
    app = _staff_app(client, tmp_path, trusted_proxies=("127.0.0.1",))
    httpd, base = _server(app)
    jane = _call(base + "/api/login", {"email": "jane@firm.example", "password": PASSWORD})[2].split(";")[0]
    sam = second_factor.finish(base, "sam@firm.example", _call(base + "/api/login", {"email": "sam@firm.example", "password": PASSWORD})[2].split(";")[0])

    def get(path, cookie):
        req = urllib.request.Request(base + path, headers={"Cookie": cookie, "X-Forwarded-For": "198.51.100.7, 127.0.0.1"})
        try:
            with urllib.request.urlopen(req) as r:
                return r.status, r.read()
        except urllib.error.HTTPError as e:
            return e.code, e.read()

    assert get("/api/items?client=demo", jane)[0] == 200
    assert get("/api/items?client=demo", jane)[0] == 200  # the case again soon: one row
    for _ in range(3):
        assert get("/api/crop?client=demo&doc=q.pdf&page=0&box=10,10,200,60", jane)[0] == 200  # a card's crops: one row
    assert get("/api/file?client=demo&doc=q.pdf", jane)[0] == 200
    assert get("/api/filled?client=demo", jane)[0] == 200
    assert get("/api/file?client=demo&doc=nothing.pdf", jane)[0] == 404  # nothing served: nothing logged
    assert app.views_log == tmp_path / "review_views.jsonl"  # beside the staff accounts and their sign-in log
    rows = [json.loads(x) for x in app.views_log.read_text().splitlines()]
    assert [(r["kind"], r["file"]) for r in rows] == [("case", None), ("scan", "q.pdf"), ("document", "q.pdf"), ("filled_form", "i485_filled.pdf")]
    assert all(r["email"] == "jane@firm.example" and r["role"] == "paralegal" and r["client"] == "demo" and r["address"] == "198.51.100.7"
               for r in rows)
    assert set(rows[0]) == {"at", "email", "name", "role", "client", "kind", "file", "address"}  # never a value from the case
    assert get("/api/access_log?client=demo", jane)[0] == 403  # the attorney's
    status, body = get("/api/access_log?client=demo&limit=2", sam)
    assert status == 200 and [r["what"] for r in json.loads(body)["rows"]] == ["Opened a filled form", "Opened a document"]  # newest first
    # an id that is no client: the one answer every client route gives it (src/restricted.py: never "no rows", which a hidden case would not get)
    assert get("/api/access_log?client=someone-else", sam)[0] == 404
    httpd.shutdown()
