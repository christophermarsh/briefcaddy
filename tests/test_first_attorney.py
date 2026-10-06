"""The first attorney, made from the screen (review/auth.py create_first, the /api/setup route): always with a one-time code (the installer's,
or the one the review app prints at its start), from the computer that runs the app too (brief J2: a TCP forwarder on that computer
makes a stranger look like it), only while no account exists, and never again after."""

from __future__ import annotations

import json
import os
import socket
import threading
import urllib.error
import urllib.parse
import urllib.request
from datetime import timedelta

import pytest

import second_factor
from review import auth
from review.auth import Accounts
from review.server import ReviewApp, make_handler, serve
from test_review import _REPO, TEMPLATE, client  # noqa: F401 -- the review app's test client
import schema_path

PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)
GOOD = {"name": "Sam Exemplo", "email": "Sam@Firm.example", "password": PASSWORD}


def _start(client_dir, users, hostnames=(), **kw):  # noqa: F811
    app = ReviewApp(client_dir.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=users, **kw)
    httpd = serve(app, 0, "127.0.0.1", hostnames)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port, hostnames)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}", port


def _call(url, body=None, cookie=None, headers=None):
    h = {"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}) | (headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=h, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        text = e.read() or b"{}"
        try:
            return e.code, json.loads(text), None
        except ValueError:  # a plain "not found" for a route that does not exist
            return e.code, text.decode(), None


@pytest.fixture
def fresh(client, tmp_path):  # noqa: F811
    """An installation as the installer leaves it: an accounts file with nobody in it and a setup code printed."""
    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    httpd, base, port = _start(client, users, hostnames=("review.office.lan",))
    yield {"base": base, "users": users, "code": code, "port": port, "client": client, "path": tmp_path}
    httpd.shutdown()


def _elsewhere(f):
    """A request that is not from the computer itself: it arrives under the office's name, as it would through a proxy or from another PC."""
    return {"Host": f"review.office.lan:{f['port']}"}


def test_the_computer_itself_sees_the_setup_screen_creates_the_first_attorney_with_the_code_and_it_never_comes_back(fresh):
    base, users = fresh["base"], fresh["users"]
    assert users.needs_setup()
    status, me, _ = _call(base + "/api/me")
    assert status == 200 and me["setup"] == {"allowed": True, "code_needed": True} and me["user"] is None and "sign_in_with" not in me
    assert _call(base + "/api/setup", GOOD)[0] == 404 and users.needs_setup()  # the computer itself, without the code: nothing (brief J2)
    status, body, cookie = _call(base + "/api/setup", GOOD | {"code": fresh["code"]})
    assert status == 200 and body["user"]["role"] == "attorney" and body["user"]["email"] == "sam@firm.example" and not body["user"]["must_change"]
    assert "HttpOnly" in cookie and "SameSite=Strict" in cookie
    # an attorney sets the authenticator app up next (review/auth.py: the password alone is not a sign-in): the account is made, the session waits
    assert body["user"]["second_factor"] == "enrol"
    pending = cookie.split(";")[0]
    assert _call(base + "/api/me", cookie=pending)[1]["user"] is None
    shown = _call(base + "/api/enrol", {}, cookie=pending)[1]
    secret = second_factor.secret_from(shown["letters"])
    status, done, cookie = _call(base + "/api/enrol", {"code": second_factor.code_now(secret)}, cookie=pending)
    assert status == 200 and len(done["recovery_codes"]) == 8 and done["user"]["email"] == "sam@firm.example"
    cookie = cookie.split(";")[0]
    status, me, _ = _call(base + "/api/me", cookie=cookie)
    assert me["user"]["name"] == "Sam Exemplo" and "setup" not in me and me["first_sign_in"] is True
    assert not users.needs_setup() and users.users()[0]["role"] == "attorney" and users.users()[0]["active"]
    stored = (fresh["path"] / "users.json").read_text()
    assert PASSWORD not in stored and "setup" not in json.loads(stored)  # hashed, and the code is used up
    assert users.sign_in("sam@firm.example", PASSWORD)  # the password they chose works at once: no one-time password to change
    log = [json.loads(line) for line in users.log_path.read_text().splitlines()]
    assert [e for e in log if e["event"] == "first_attorney_created"][0]["how"] == "setup code"
    # gone for good: not offered to anyone, and the route answers as if it did not exist
    for headers in (None, _elsewhere(fresh)):
        assert "setup" not in _call(base + "/api/me" + f"?setup={fresh['code']}", headers=headers)[1]
        status, body, _ = _call(base + "/api/setup", {"name": "Eve", "email": "eve@x.example", "password": PASSWORD, "code": fresh["code"]}, headers=headers)
        assert status == 404 and body == {"error": "not found"}
    assert [u["email"] for u in users.users()] == ["sam@firm.example"]


def test_a_request_from_anywhere_else_without_the_code_is_refused_and_not_told_there_are_no_accounts(fresh):
    base, users = fresh["base"], fresh["users"]
    assert "setup" not in _call(base + "/api/me", headers=_elsewhere(fresh))[1]
    assert "setup" not in _call(base + "/api/me?setup=WRONG-CODE-0000-0000", headers=_elsewhere(fresh))[1]
    for body, headers in ((GOOD, _elsewhere(fresh)), (GOOD | {"code": "AAAA-BBBB-CCCC-DDDD"}, _elsewhere(fresh)),
                          (GOOD, {"X-Forwarded-For": "203.0.113.9"}), (GOOD, {"X-Real-IP": "203.0.113.9"}), (GOOD, {"Forwarded": "for=203.0.113.9"}),
                          *[(GOOD, {h: "203.0.113.9"}) for h in ("Via", "X-Client-IP", "True-Client-IP", "CF-Connecting-IP", "X-Forwarded-Server")]):
        status, out, _ = _call(base + "/api/setup", body, headers=headers)
        assert status == 404 and out == {"error": "not found"}, (headers, out)  # told nothing: the same answer as a page that does not exist
    assert users.needs_setup() and users.users() == []
    log = users.log_path.read_text()
    assert log.count("setup_refused") == 10 and PASSWORD not in log and fresh["code"] not in log


def test_the_code_lets_a_request_from_another_computer_in_once(fresh):
    base, users, code = fresh["base"], fresh["users"], fresh["code"]
    assert "-" in code and len(code.replace("-", "")) == 16
    lower = code.lower().replace("-", " ")  # typed by hand: any case, spaces for dashes
    status, me, _ = _call(base + "/api/me?setup=" + urllib.parse.quote(lower), headers=_elsewhere(fresh))
    assert me["setup"] == {"allowed": True}
    status, body, _ = _call(base + "/api/setup", GOOD | {"code": lower}, headers=_elsewhere(fresh))
    assert status == 200 and body["user"]["role"] == "attorney"
    log = [json.loads(line) for line in users.log_path.read_text().splitlines()]
    assert [e for e in log if e["event"] == "first_attorney_created"][0]["how"] == "setup code"
    assert _call(base + "/api/setup", GOOD | {"email": "other@firm.example", "code": code}, headers=_elsewhere(fresh))[0] == 404  # used up


def test_an_expired_or_replaced_code_does_not_work(fresh):
    base, users, code = fresh["base"], fresh["users"], fresh["code"]
    newer = users.new_setup_code()
    assert newer != code
    assert _call(base + "/api/setup", GOOD | {"code": code}, headers=_elsewhere(fresh))[0] == 404  # the old one stopped working
    data = json.loads(users.path.read_text())
    data["setup"]["expires"] = (auth._now() - timedelta(minutes=1)).isoformat()
    users.path.write_text(json.dumps(data))
    assert _call(base + "/api/setup", GOOD | {"code": newer}, headers=_elsewhere(fresh))[0] == 404  # a printed code does not last forever
    assert users.needs_setup()


def test_behind_a_proxy_the_computer_itself_is_not_enough(client, tmp_path):  # noqa: F811
    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    httpd, base, port = _start(client, users, trusted_proxies=("127.0.0.1",))  # a proxy on this computer connects from loopback too
    try:
        assert "setup" not in _call(base + "/api/me")[1]
        assert _call(base + "/api/setup", GOOD)[0] == 404
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 200  # the code still works
    finally:
        httpd.shutdown()
    httpd, base, port = _start(client, Accounts(tmp_path / "users2.json"), behind_tls=True)
    try:
        Accounts(tmp_path / "users2.json").new_setup_code()
        assert "setup" not in _call(base + "/api/me")[1] and _call(base + "/api/setup", GOOD)[0] == 404
    finally:
        httpd.shutdown()


@pytest.mark.skipif(os.name != "posix", reason="file modes")
def test_the_accounts_file_stays_owner_only_after_every_save(tmp_path):
    users = Accounts(tmp_path / "users.json")
    users.new_setup_code()
    assert users.path.stat().st_mode & 0o077 == 0
    users.create_first("sam@firm.example", "Sam Exemplo", PASSWORD, users.new_setup_code())
    users.add("jane@firm.example", "Jane Exemplo", "paralegal")
    users.sign_in("sam@firm.example", PASSWORD)
    assert users.path.stat().st_mode & 0o077 == 0  # the installer's mode survives the first save and every one after


def test_the_computer_itself_needs_a_setup_code_on_record_that_has_not_expired(client, tmp_path):  # noqa: F811
    users = Accounts(tmp_path / "users.json")
    (tmp_path / "users.json").write_text(json.dumps({"users": {}, "sessions": {}}), encoding="utf-8")  # an accounts file with no setup record
    httpd, base, port = _start(client, users)
    try:
        assert "setup" not in _call(base + "/api/me")[1]
        assert _call(base + "/api/setup", GOOD) [0] == 404 and users.needs_setup()  # no record: nobody, the computer itself included
        code = users.new_setup_code()
        assert _call(base + "/api/me")[1]["setup"] == {"allowed": True, "code_needed": True}
        data = json.loads(users.path.read_text())
        data["setup"]["expires"] = (auth._now() - timedelta(days=6)).isoformat()  # day 20 of a 14-day code
        users.path.write_text(json.dumps(data))
        assert "setup" not in _call(base + "/api/me")[1]
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 404 and users.needs_setup()
        code = users.new_setup_code()  # a new code opens it again: typed on the computer itself
        assert _call(base + "/api/setup", GOOD)[0] == 404
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 200
    finally:
        httpd.shutdown()


def test_secure_cookies_is_a_sign_of_a_proxy_too(client, tmp_path):  # noqa: F811
    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    httpd, base, port = _start(client, users, secure_cookies=True)  # the flag says the app is reached over HTTPS, so something is in front
    try:
        assert "setup" not in _call(base + "/api/me")[1] and _call(base + "/api/setup", GOOD)[0] == 404
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 200
    finally:
        httpd.shutdown()


def test_an_app_that_serves_the_network_or_has_a_name_opens_setup_only_to_the_code(client, tmp_path):  # noqa: F811
    from review.server import local_setup_allowed

    assert local_setup_allowed("127.0.0.1") and local_setup_allowed("localhost") and local_setup_allowed("::1")
    assert not local_setup_allowed("0.0.0.0") and not local_setup_allowed("127.0.0.1", ("review.office.lan",)) and not local_setup_allowed("192.168.1.5")
    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    httpd, base, port = _start(client, users, local_setup=False)  # what main() builds for such an app
    try:
        assert "setup" not in _call(base + "/api/me")[1]
        assert _call(base + "/api/setup", GOOD)[0] == 404
        assert _call(base + "/api/me?setup=" + code)[1]["setup"] == {"allowed": True}
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 200
    finally:
        httpd.shutdown()


def test_a_connection_from_another_address_is_refused_too(client, tmp_path):  # noqa: F811
    """A real connection from this machine's network address, not loopback (the app serving the network, as an office would)."""
    try:
        probe = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
        probe.connect(("192.0.2.1", 9))  # no packet is sent: this asks the system which address it would use
        ip = probe.getsockname()[0]
        probe.close()
    except OSError:
        pytest.skip("no network address on this machine")
    if ip.startswith("127."):
        pytest.skip("no network address on this machine")
    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=users)
    httpd = serve(app, 0, "0.0.0.0", (ip,))
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port, (ip,))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://{ip}:{port}"
        assert "setup" not in _call(base + "/api/me")[1]
        assert _call(base + "/api/setup", GOOD)[0] == 404 and users.needs_setup()
        assert _call(base + "/api/setup", GOOD | {"code": code})[0] == 200
    finally:
        httpd.shutdown()


def test_a_bad_email_name_or_password_creates_nothing(fresh):
    base, users = fresh["base"], fresh["users"]
    for body, words in ((GOOD | {"password": "short"}, "at least 12"), (GOOD | {"name": "  "}, "full name"), (GOOD | {"email": "not-an-email"}, "work email")):
        status, out, _ = _call(base + "/api/setup", body | {"code": fresh["code"]})
        assert status == 400 and words in out["error"]
    assert users.needs_setup()
    assert _call(base + "/api/setup", GOOD | {"code": fresh["code"]})[0] == 200  # and then the right one still can


def test_guessing_the_code_is_rate_limited(fresh):
    base = fresh["base"]
    statuses = [_call(base + "/api/setup", GOOD | {"code": "AAAA-BBBB-CCCC-DDDD"}, headers=_elsewhere(fresh))[0] for _ in range(22)]
    assert statuses[:20] == [404] * 20 and statuses[20:] == [429, 429]
    assert fresh["users"].needs_setup()


def test_two_requests_at_once_make_one_account(fresh):
    users, users_code = fresh["users"], fresh["code"]
    results = []

    def go(n):
        try:
            users.create_first(f"p{n}@firm.example", f"Person {n}", PASSWORD, users_code)
            results.append("made")
        except LookupError:
            results.append("closed")

    threads = [threading.Thread(target=go, args=(n,)) for n in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert sorted(results) == ["closed"] * 3 + ["made"] and len(users.users()) == 1


def test_it_does_not_come_back_even_if_the_only_account_is_turned_off(fresh):
    users = fresh["users"]
    users.create_first("sam@firm.example", "Sam Exemplo", PASSWORD, fresh["code"])
    users.update("sam@firm.example", active=False)
    assert not users.needs_setup()
    assert "setup" not in _call(fresh["base"] + "/api/me")[1]
    assert _call(fresh["base"] + "/api/setup", GOOD | {"email": "eve@x.example"})[0] == 404
    with pytest.raises(ValueError, match="already exist"):
        users.new_setup_code()


def test_without_an_accounts_file_there_is_no_setup_route(client):  # noqa: F811
    httpd, base, _ = _start(client, None)
    try:
        status, me, _ = _call(base + "/api/me")
        assert me["accounts"] is False and "setup" not in me
        assert _call(base + "/api/setup", GOOD)[0] == 404
    finally:
        httpd.shutdown()


def test_the_command_line_makes_the_file_and_the_code_and_refuses_after_the_first_account(tmp_path, capsys):
    from review import users as cli

    path = tmp_path / "data" / "review_users.json"
    cli.main(["--file", str(path), "setup-code"])
    code = capsys.readouterr().out.strip()
    assert path.exists() and Accounts(path).needs_setup() and Accounts(path).setup_code_ok(code)
    assert not Accounts(path).setup_code_ok("AAAA-BBBB-CCCC-DDDD") and not Accounts(path).setup_code_ok("")
    Accounts(path).create_first("sam@firm.example", "Sam Exemplo", PASSWORD, code=code)
    with pytest.raises(SystemExit, match="already exist"):
        cli.main(["--file", str(path), "setup-code"])

