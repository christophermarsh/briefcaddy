"""Staff accounts on a screen: the Settings page's Staff section (review/server.py staff, staff_change over review/auth.py).

The attorney adds a person (name, email, role), turns them off and on, resets their one-time password (shown once), and changes
their role; a paralegal can do none of it; every change is in the access log with who made it; turning someone off ends their
sessions at once. Everyone here is made up.
"""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest

import second_factor
import schema_path

_REPO = Path(__file__).resolve().parent.parent
PASSWORD = "correct horse battery staple"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def app(tmp_path):
    from review.auth import Accounts
    from review.server import ReviewApp

    (tmp_path / "clients").mkdir()
    accounts = Accounts(tmp_path / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney"),
                              ("ana@firm.example", "Ana Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
        if role == "attorney":
            second_factor.set_up(accounts, email, PASSWORD)  # an attorney signs in with a code (review/auth.py)
    return ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts)


@pytest.fixture
def server(app):
    from review.server import make_handler, serve

    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def call(url, cookie=None, body=None):
    headers = {"X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}) | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), None


def sign_in(base, email, password=PASSWORD):
    status, body, cookie = call(base + "/api/login", body={"email": email, "password": password})
    if status == 200 and body["user"].get("second_factor") == "code":
        return second_factor.finish(base, email, cookie.split(";")[0])
    return (cookie or "").split(";")[0] if status == 200 and not body["user"].get("second_factor") else None


def test_an_attorney_adds_a_person_whose_one_time_password_is_shown_once(server):
    sam = sign_in(server, "sam@firm.example")
    status, body, _ = call(server + "/api/staff", sam, {"action": "add", "name": "Kim Exemplo", "email": "Kim@Firm.example", "role": "paralegal"})
    assert status == 200 and body["for"] == "kim@firm.example" and len(body["one_time_password"]) >= 12
    kim = next(p for p in body["people"] if p["email"] == "kim@firm.example")
    assert kim["name"] == "Kim Exemplo" and kim["role"] == "paralegal" and kim["active"] and kim["must_change"]
    status, listed, _ = call(server + "/api/staff", sam)
    assert status == 200 and "one_time_password" not in json.dumps(listed) and body["one_time_password"] not in json.dumps(listed)  # shown once
    status, first, _ = call(server + "/api/login", body={"email": "kim@firm.example", "password": body["one_time_password"]})
    assert status == 200 and first["user"]["must_change"]  # they choose their own at first sign-in
    assert call(server + "/api/staff", sam, {"action": "add", "name": "Kim Again", "email": "kim@firm.example", "role": "paralegal"})[0] == 400
    assert call(server + "/api/staff", sam, {"action": "add", "name": "", "email": "lee@firm.example", "role": "paralegal"})[0] == 400
    assert call(server + "/api/staff", sam, {"action": "add", "name": "Lee", "email": "lee@firm.example", "role": "administrator"})[0] == 400


def test_turning_someone_off_ends_their_sessions_at_once(server):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    assert call(server + "/api/clients", jane)[0] == 200
    status, body, _ = call(server + "/api/staff", sam, {"action": "disable", "email": "jane@firm.example"})
    assert status == 200 and not next(p for p in body["people"] if p["email"] == "jane@firm.example")["active"]
    assert call(server + "/api/clients", jane)[0] == 401  # the very next request
    assert sign_in(server, "jane@firm.example") is None
    call(server + "/api/staff", sam, {"action": "enable", "email": "jane@firm.example"})
    assert sign_in(server, "jane@firm.example") is not None


def test_a_reset_gives_a_new_one_time_password_and_the_old_one_stops_working(server):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    status, body, _ = call(server + "/api/staff", sam, {"action": "reset", "email": "jane@firm.example"})
    assert status == 200 and body["for"] == "jane@firm.example" and body["one_time_password"]
    assert call(server + "/api/clients", jane)[0] == 401 and sign_in(server, "jane@firm.example") is None
    assert call(server + "/api/login", body={"email": "jane@firm.example", "password": body["one_time_password"]})[1]["user"]["must_change"]


def test_a_role_change_takes_effect_at_once(server, app):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    assert call(server + "/api/staff", jane)[0] == 403
    status, body, _ = call(server + "/api/staff", sam, {"action": "role", "email": "jane@firm.example", "role": "attorney"})
    assert status == 200 and next(p for p in body["people"] if p["email"] == "jane@firm.example")["role"] == "attorney"
    assert call(server + "/api/clients", jane)[0] == 401  # signed out: her next sign-in carries the new role
    assert sign_in(server, "jane@firm.example") is None  # an attorney now: her password alone no longer signs her in
    second_factor.set_up(app.accounts, "jane@firm.example", PASSWORD)  # she sets up her authenticator app
    assert call(server + "/api/staff", sign_in(server, "jane@firm.example"))[0] == 200
    assert call(server + "/api/staff", sam, {"action": "role", "email": "jane@firm.example", "role": "owner"})[0] == 400


def test_only_an_attorney_and_never_on_their_own_account(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    assert call(server + "/api/staff", jane)[0] == 403
    for action in ("add", "disable", "reset", "role"):
        assert call(server + "/api/staff", jane, {"action": action, "email": "sam@firm.example", "name": "X", "role": "paralegal"})[0] == 403
    status, body, _ = call(server + "/api/staff", sam, {"action": "disable", "email": "sam@firm.example"})
    assert status == 400 and body["error"] == "Ask another attorney to change your own account."
    assert call(server + "/api/staff", sam, {"action": "role", "email": "sam@firm.example", "role": "paralegal"})[0] == 400
    assert call(server + "/api/staff", sam, {"action": "disable", "email": "nobody@firm.example"})[0] == 404
    assert call(server + "/api/staff")[0] == 401


def test_every_change_is_in_the_access_log_with_who_made_it(server, app):
    sam = sign_in(server, "sam@firm.example")
    first = call(server + "/api/staff", sam, {"action": "add", "name": "Kim Exemplo", "email": "kim@firm.example", "role": "paralegal"})[1]["one_time_password"]
    second = call(server + "/api/staff", sam, {"action": "reset", "email": "kim@firm.example"})[1]["one_time_password"]
    call(server + "/api/staff", sam, {"action": "role", "email": "kim@firm.example", "role": "attorney"})
    call(server + "/api/staff", sam, {"action": "disable", "email": "kim@firm.example"})
    rows = [json.loads(x) for x in app.accounts.log_path.read_text(encoding="utf-8").splitlines()]
    kims = [(r["event"], {k: r[k] for k in ("role", "active") if k in r}) for r in rows if r["email"] == "kim@firm.example" and r.get("by") == "sam@firm.example"]
    assert kims == [("account_added", {"role": "paralegal"}), ("password_reset", {}), ("password_reset", {}), ("account_changed", {"role": "attorney"}),
                    ("account_changed", {"active": False})]
    text = app.accounts.log_path.read_text(encoding="utf-8")
    assert first not in text and second not in text and PASSWORD not in text  # never a password in the log
    assert all(r.get("at") for r in rows)


def test_without_staff_accounts_there_is_no_staff_section(tmp_path):
    from review.server import ReviewApp

    (tmp_path / "clients").mkdir()
    app = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    with pytest.raises(LookupError):
        app.staff(None)


def test_the_sign_in_screen_says_whom_to_ask_and_offers_no_self_service_reset():
    page = (_REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Forgot your password? Ask your attorney for a reset." in page
    assert "/api/forgot" not in page and "reset link" not in page.lower()
    from review.auth import ROLES

    assert ROLES == ("paralegal", "attorney")  # no administrator role: the attorney runs Settings and Staff


def test_the_command_line_still_makes_the_first_attorney(tmp_path, capsys):
    from review import users

    users.main(["--file", str(tmp_path / "staff.json"), "add", "first@firm.example", "First Attorney", "attorney"])
    assert "One-time password:" in capsys.readouterr().out
    rows = [json.loads(x) for x in (tmp_path / "staff_access.jsonl").read_text(encoding="utf-8").splitlines()]
    assert rows[0]["event"] == "account_added" and "by" not in rows[0]
