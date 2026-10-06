# ruff: noqa: F811  (the made-up firm's fixtures are imported from tests/test_restricted.py and used as arguments)
"""The attacker-style verification of docs/security/threat_model.md (brief J2): for every finding, the request that showed it, now refused; and the
attacks the model lists as already refused, made for real against the product's own test servers on this machine.

Each test is named after the finding (test_f01 ... test_f12) or the attack (test_a01 ... test_a11) it proves; the model names each test, and the
last test here checks that every route of the five sets and every test the model cites is really there. Everyone here is made up."""

from __future__ import annotations

import base64
import hashlib
import http.client
import json
import re
import sqlite3
import threading
import types
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path
from urllib.parse import quote

import pytest

from review import auth
from review import server as srv
from review.auth import Accounts
from test_restricted import LISTING, NEITHER, NEITHER_POST, PASSWORD, QUERY, TOKENED, WEBHOOKS, app, server, sign_in, world  # noqa: F401
import schema_path

REPO = Path(__file__).resolve().parent.parent
MODEL = REPO / "docs" / "security" / "threat_model.md"


def ask(base, method, path, body=None, headers=None, declared=None):
    u = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    h = {"X-Review-App": "1", "Content-Type": "application/json"} | (headers or {})
    try:
        conn.putrequest(method, path, skip_host="Host" in h, skip_accept_encoding=True)
        for k, v in h.items():
            conn.putheader(k, v)
        data = json.dumps(body).encode() if isinstance(body, (dict, list)) else body
        conn.putheader("Content-Length", str(declared if declared is not None else len(data or b"")))
        conn.endheaders(None if declared is not None else data)
        r = conn.getresponse()
        return r.status, {k.lower(): v for k, v in r.getheaders()}, r.read()
    except (http.client.RemoteDisconnected, ConnectionResetError, BrokenPipeError) as exc:
        return f"dropped: {type(exc).__name__}", {}, b""
    finally:
        conn.close()


def cookie(base, email):
    return {"Cookie": sign_in(base, email)}


@pytest.fixture
def portal(world):
    from fastapi.testclient import TestClient
    from portal.app import create_app
    from portal.notify import Notifier
    from portal.store import PortalStore

    root = world.parent / "portal"
    app_ = create_app(root, base_url="https://portal.example", notifier=Notifier(world.parent / "outbox.jsonl"), trusted_proxy="testclient")
    return TestClient(app_), PortalStore(root)


# -- the findings ----------------------------------------------------------------------------------------------------------------------------


def test_f01_the_first_attorney_is_not_made_without_the_code_from_a_loopback_connection(world, tmp_path):
    """F1: what a TCP forwarder hands the app (a loopback connection, Host 127.0.0.1, no forwarding header) made the first attorney with no code."""
    from review.server import ReviewApp, make_handler, serve

    users = Accounts(tmp_path / "users.json")
    code = users.new_setup_code()
    app_ = ReviewApp(world, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=users)
    httpd = serve(app_, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app_, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        status, _, body = ask(base, "POST", "/api/setup", {"name": "Eve Intruder", "email": "eve@x.example", "password": PASSWORD}, {"Host": f"127.0.0.1:{port}"})
        assert (status, json.loads(body)) == (404, {"error": "not found"}) and users.needs_setup()
        assert ask(base, "POST", "/api/setup", {"name": "Sam Exemplo", "email": "sam@firm.example", "password": PASSWORD, "code": code})[0] == 200
    finally:
        httpd.shutdown()


def test_f02_the_row_saying_who_opened_a_restricted_document_is_written_before_the_bytes(server, app, monkeypatch):
    import http.server

    sam = cookie(server, "sam@firm.example")
    before = len(app.views_log.read_text(encoding="utf-8").splitlines()) if app.views_log.exists() else 0

    class Killed(BaseException):
        pass

    def die(self):
        raise Killed()

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "end_headers", die)
    monkeypatch.setattr("threading.excepthook", lambda args: None)
    assert ask(server, "GET", "/api/items?client=case-rosa", headers=sam)[0].startswith("dropped")
    rows = [json.loads(x) for x in app.views_log.read_text(encoding="utf-8").splitlines()][before:]
    assert [(r["client"], r["kind"], r.get("restricted")) for r in rows] == [("case-rosa", "case", True)]


def _guesses_in_a_day(tmp_path, monkeypatch, cap):
    monkeypatch.setattr(auth, "SCRYPT", {"n": 2**8, "r": 8, "p": 1})
    monkeypatch.setattr(auth, "LOCKOUT_MAX", cap)
    users = Accounts(tmp_path / f"guess-{int(cap.total_seconds())}.json")
    users.change_password("ana@firm.example", users.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    start = datetime(2026, 10, 5, tzinfo=timezone.utc)
    now = {"t": start}
    monkeypatch.setattr(auth, "_now", lambda: now["t"])
    tries = 0
    while now["t"] < start + timedelta(days=1):
        try:
            users.sign_in("ana@firm.example", "a wrong guess, long enough")
        except ValueError:
            pass
        tries += 1
        until = json.loads(users.path.read_text())["users"]["ana@firm.example"].get("locked_until")
        if until and datetime.fromisoformat(until) > now["t"]:
            now["t"] = datetime.fromisoformat(until) + timedelta(seconds=1)
    return tries


def test_f03_guessing_one_accounts_password_or_code_is_held_to_about_fifty_tries_a_day(tmp_path, monkeypatch):
    flat = _guesses_in_a_day(tmp_path, monkeypatch, timedelta(minutes=15))  # 15 minutes each time (the carried finding's "about 480")
    before = _guesses_in_a_day(tmp_path, monkeypatch, timedelta(minutes=60))  # the cap before brief J2
    now = _guesses_in_a_day(tmp_path, monkeypatch, timedelta(hours=4))
    assert (flat, before, now) == (480, 130, 50) and auth.LOCKOUT_MAX == timedelta(hours=4)


def test_f04_three_lockouts_of_one_account_in_a_day_are_an_alert_the_attorney_sees_and_a_morning_line(tmp_path, monkeypatch):
    import overnight
    from review import oversight

    monkeypatch.setattr(auth, "SCRYPT", {"n": 2**8, "r": 8, "p": 1})
    users = Accounts(tmp_path / "review_users.json")
    users.change_password("ana@firm.example", users.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    now = {"t": datetime(2026, 10, 5, 9, tzinfo=timezone.utc)}
    monkeypatch.setattr(auth, "_now", lambda: now["t"])
    for _ in range(3):
        for _ in range(auth.MAX_FAILURES):
            with pytest.raises(ValueError):
                users.sign_in("ana@firm.example", "a wrong guess, long enough")
        now["t"] = datetime.fromisoformat(json.loads(users.path.read_text())["users"]["ana@firm.example"]["locked_until"]) + timedelta(seconds=1)
    rows = [json.loads(x) for x in users.log_path.read_text().splitlines()]
    assert [r["what"] for r in rows if r["event"] == "lockout_alert"] == ["account"]
    assert "Alert: this account was locked 3 times today" in oversight.StaffLog(users, types.SimpleNamespace(), lambda: {})._what(
        next(r for r in rows if r["event"] == "lockout_alert"), {})
    assert overnight.sign_in_alerts(tmp_path, now=now["t"]).startswith("Sign-in alerts: 1 in the last day")


def test_f05_the_feed_the_page_who_am_i_and_the_callback_say_429_to_a_flood(server):
    for path, bucket in (("/calendar/made-up-token.ics", "/calendar/"), ("/api/me", "/api/me"), ("/", "/"), ("/auth/callback?state=x", "/auth/callback")):
        me = {"X-Forwarded-For": "203.0.113.50"}  # no trusted proxy here: the header is ignored, the connection is the key
        statuses = [ask(server, "GET", path, headers=me)[0] for _ in range(srv.ATTEMPTS[bucket] + 1)]
        assert statuses[-1] == 429 and 429 not in statuses[:-1], (path, statuses[-3:])


def test_f06_send_me_a_link_with_many_contacts_from_one_address_is_refused(portal):
    client, _ = portal
    me = {"X-Portal": "1", "X-Forwarded-For": "198.51.100.60"}
    statuses = [client.post("/api/link", json={"contact": f"person{n}@example.com"}, headers=me).status_code for n in range(121)]
    assert statuses[:120] == [200] * 120 and statuses[120] == 429


def test_f07_a_json_body_of_two_megabytes_is_refused_before_it_is_read_on_both_apps(server, portal):
    jane = cookie(server, "jane@firm.example")
    status, _, body = ask(server, "POST", "/api/decide", None, jane, declared=2 * 1024 * 1024)
    assert status == 413 and body == b"request too large"
    status, _, _ = ask(server, "POST", "/api/login", None, {}, declared=2 * 1024 * 1024)
    assert status == 413  # signing in, open to anyone: refused at 16 KB
    client, _ = portal
    big = json.dumps({"contact": "x", "pad": "a" * (2 * 1024 * 1024)})
    assert client.post("/api/link", content=big, headers={"X-Portal": "1", "Content-Type": "application/json"}).status_code == 413


def test_f08_the_portal_page_its_sketches_and_who_am_i_say_429_to_a_flood(portal):
    client, _ = portal
    me = {"X-Forwarded-For": "198.51.100.61"}
    statuses = [client.get("/", headers=me).status_code for _ in range(301)]
    assert statuses[-1] == 429 and 429 not in statuses[:-1]


def test_f09_every_page_runs_its_script_by_nonce_and_no_answer_lacks_a_policy(server, portal):
    sam = cookie(server, "sam@firm.example")
    for path in ("/", "/api/answers?client=pilot-nova"):
        status, h, body = ask(server, "GET", path, headers=sam)
        nonce = re.search(r"script-src 'nonce-([^']+)'", h["content-security-policy"]).group(1)
        assert status == 200 and "unsafe-inline" not in h["content-security-policy"].split("script-src", 1)[1].split(";")[0]
        assert f'<script nonce="{nonce}">'.encode() in body and body.count(b"<script") == 1
    for path in ("/api/items?client=case-ana", "/api/clients", "/api/file?client=case-ana&doc=none.pdf"):
        assert ask(server, "GET", path, headers=sam)[1]["content-security-policy"].startswith("default-src 'none'")
    client, _ = portal
    page = client.get("/")
    assert "'unsafe-inline'" not in page.headers["content-security-policy"].split("script-src", 1)[1].split(";")[0]


def test_f10_every_answer_keeps_other_sites_out_of_its_window_and_its_resources(server, portal):
    for path in ("/", "/api/me", "/no-such-route", "/calendar/x.ics"):
        h = ask(server, "GET", path)[1]
        assert h["cross-origin-opener-policy"] == "same-origin" and h["cross-origin-resource-policy"] == "same-origin" and "camera=()" in h["permissions-policy"]
    client, _ = portal
    for path in ("/", "/api/me", "/no-such-page"):
        h = client.get(path).headers
        assert h["cross-origin-opener-policy"] == "same-origin" and h["cross-origin-resource-policy"] == "same-origin"


def test_f13_opening_a_waiting_notice_is_in_the_view_log_before_its_scan_is_sent(server, app):
    """F13 (found by the J2 verification): a USCIS notice waiting in the inbox (an A-Number, a receipt number) was served with no view-log row."""
    sam = cookie(server, "sam@firm.example")
    before = len(app.views_log.read_text(encoding="utf-8").splitlines()) if app.views_log.exists() else 0
    status, _, body = ask(server, "GET", "/api/inbox/file?id=n-asylum", headers=sam)
    assert status == 200 and body.startswith(b"%PDF")
    rows = [json.loads(x) for x in app.views_log.read_text(encoding="utf-8").splitlines()][before:]
    assert [(r["kind"], r["file"], r["email"]) for r in rows] == [("notice", "n-asylum", "sam@firm.example")]


BROKEN = ["/api/decide", "/api/family", "/api/i360", "/api/i589", "/api/n400", "/api/online-bundle", "/api/packet", "/api/packet-file", "/api/review-bundle", "/api/undo"]


def test_f11_a_body_of_the_wrong_shape_is_a_500_in_words_never_a_dropped_connection(server, monkeypatch):
    sam = cookie(server, "sam@firm.example")
    monkeypatch.setattr("traceback.print_exc", lambda *a, **k: None)
    for route in BROKEN:
        for body in ({"client": "case-ana", "item_id": ["x"], "action": {"a": 1}, "values": "x", "id": ["x"], "filing": ["x"], "kind": 5, "text": 7},
                     {"client": "case-ana", "item_id": 5, "action": 5, "values": [1], "id": 5, "filing": 5}):
            status, h, data = ask(server, "POST", route, body, sam)
            assert isinstance(status, int) and status < 600, (route, status)
            if status == 500:
                assert "Something went wrong" in json.loads(data)["error"] and "Traceback" not in data.decode() and "content-security-policy" in h


def test_f12_http_servers_own_refusals_carry_the_headers_and_a_long_request_line_is_a_414(server):
    status, h, _ = ask(server, "GET", "/api/items?client=" + "a" * 70000)
    assert status == 414 and "content-security-policy" in h
    status, h, _ = ask(server, "PUT", "/api/decide", b"{}")
    assert status == 501 and h["x-frame-options"] == "DENY" and h["cache-control"] == "no-store"


# -- the attacks the model lists as refused ---------------------------------------------------------------------------------------------------


def _same(a, b):
    """Two answers that must not tell one case from another: status, body and every header but the date."""
    return a[0] == b[0] and a[2] == b[2] and {k: v for k, v in a[1].items() if k != "date"} == {k: v for k, v in b[1].items() if k != "date"}


def test_a01_a_paralegal_not_named_on_a_restricted_case_gets_the_byte_identical_404_a_made_up_id_gets(server):
    jane = cookie(server, "jane@firm.example")
    for route in sorted(srv.CASE_GET):
        hidden = ask(server, "GET", route + "?client=case-rosa" + QUERY, headers=jane)
        made_up = ask(server, "GET", route + "?client=case-zzzz" + QUERY, headers=jane)
        assert hidden[0] == 404 and _same(hidden, made_up), route
    for route in sorted(srv.CASE_POST):
        hidden = ask(server, "POST", route, {"client": "case-rosa", "item_id": "x", "action": "confirm"}, jane)
        made_up = ask(server, "POST", route, {"client": "case-zzzz", "item_id": "x", "action": "confirm"}, jane)
        assert hidden[0] == 404 and _same(hidden, made_up), route


def test_a02_an_old_session_ends_with_a_role_change_a_turn_off_and_a_password_change(server, app):
    accounts = app.accounts
    for change in ("role", "off", "password"):
        jar = cookie(server, "kim@firm.example")
        assert ask(server, "GET", "/api/clients", headers=jar)[0] == 200
        if change == "role":
            accounts.update("kim@firm.example", role="attorney")
        elif change == "off":
            accounts.update("kim@firm.example", active=False)
        else:
            accounts.change_password("kim@firm.example", PASSWORD, PASSWORD + " again")
        assert ask(server, "GET", "/api/clients", headers=jar)[0] == 401, change
        accounts.update("kim@firm.example", role="paralegal", active=True)
        if change == "password":
            accounts.change_password("kim@firm.example", PASSWORD + " again", PASSWORD)


def test_a03_a_calendar_address_is_dead_after_revocation_and_after_the_person_is_turned_off(server, app):
    jane = cookie(server, "jane@firm.example")
    path = json.loads(ask(server, "POST", "/api/calendar", {"action": "make", "kind": "person"}, jane)[2])["path"]
    assert ask(server, "GET", path)[0] == 200
    ask(server, "POST", "/api/calendar", {"action": "revoke", "kind": "person"}, jane)
    assert (ask(server, "GET", path)[0], json.loads(ask(server, "GET", path)[2])) == (404, srv.UNKNOWN)
    path = json.loads(ask(server, "POST", "/api/calendar", {"action": "make", "kind": "person"}, jane)[2])["path"]
    app.accounts.update("jane@firm.example", active=False)
    assert ask(server, "GET", path)[0] == 404
    app.accounts.update("jane@firm.example", active=True)
    assert ask(server, "GET", path)[0] == 404  # turning the person on again does not bring the old address back


def test_a04_a_portal_link_after_the_case_ended_after_a_decline_and_after_leave(portal):
    client, store = portal
    token = store.new_link_token("pilot-nova")
    r = client.get(f"/l/{token}", follow_redirects=False)
    jar = {"Cookie": f"portal_session={r.cookies.get('portal_session')}"}  # the cookie is Secure (an https portal): sent by hand here
    assert r.status_code == 303 and client.get("/api/me", headers=jar).status_code == 200
    client.post("/api/logout", headers={"X-Portal": "1"} | jar)  # Leave this page
    assert client.get("/api/me", headers=jar).status_code == 401  # the old cookie is dead on the server
    assert client.get(f"/l/{token}", follow_redirects=False).headers["location"] == "/?expired=1"  # a link works once
    r = client.get(f"/l/{store.new_link_token('pilot-nova')}", follow_redirects=False)
    jar = {"Cookie": f"portal_session={r.cookies.get('portal_session')}"}
    store.save_engagement("pilot-nova", {"ended": {"state": "closed", "since": "2026-10-04"}})
    assert client.get("/api/me", headers=jar).json().get("closed")  # an ended case: the letter and nothing else
    assert client.put("/api/answers", json={"first_name": "X"}, headers={"X-Portal": "1"} | jar).status_code == 409
    store.update_profile("pilot-nova", declined_on="2000-01-01")  # declined: the link and the session stop
    assert client.get("/api/me", headers=jar).status_code == 401
    assert client.get(f"/l/{store.new_link_token('pilot-nova')}", follow_redirects=False).headers["location"] == "/?expired=1"


ODD_IDS = ["../case-ana", "case-ana/../case-ana", "..", "case-ana\x00", "%2e%2e%2fcase-ana", "case-ana\\..\\case-rosa", "a" * 10000]


def test_a05_and_a06_a_path_a_nul_or_ten_thousand_characters_in_an_id_get_the_one_404_on_every_route(server):
    sam = cookie(server, "sam@firm.example")
    for route in sorted(srv.CASE_GET):
        for bad in ODD_IDS:
            status, _, body = ask(server, "GET", route + "?client=" + quote(bad) + QUERY, headers=sam)
            assert (status, json.loads(body)) == (404, srv.UNKNOWN), (route, bad[:12])
    for route in sorted(srv.CASE_POST):
        for bad in ODD_IDS:
            status, _, body = ask(server, "POST", route, {"client": bad, "item_id": "x"}, sam)
            assert (status, json.loads(body)) == (404, srv.UNKNOWN), (route, bad[:12])
    for route, key in (("/api/prospect", "prospect"), ("/api/prospect-letter.pdf", "prospect"), ("/api/export-firm.zip", "name"), ("/api/inbox/file", "id")):
        for bad in ODD_IDS:
            assert ask(server, "GET", f"{route}?{key}={quote(bad)}", headers=sam)[0] in (400, 404), (route, bad[:12])


def test_a07_an_upload_that_is_not_what_its_name_says_is_refused(server, portal):
    from portal.bank import bank_for

    client, store = portal
    session = client.get(f"/l/{store.new_link_token('pilot-nova')}", follow_redirects=False).cookies.get("portal_session")
    jar = {"X-Portal": "1", "Cookie": f"portal_session={session}"}
    bank_doc = bank_for(store.profile("pilot-nova"))["documents"][0]["id"]  # a document the client is really asked for
    html = b"<html><script>alert(1)</script></html>"
    r = client.post("/api/upload", data={"doc_id": bank_doc}, files={"file": ("passport.pdf", html, "application/pdf")}, headers=jar)
    assert r.status_code == 415 and store.uploads("pilot-nova") == []  # checked by its first bytes, never by its name
    png_named_pdf = b"\x89PNG\r\n\x1a\n" + b"\0" * 64
    r = client.post("/api/upload", data={"doc_id": bank_doc}, files={"file": ("passport.pdf", png_named_pdf, "application/pdf")}, headers=jar)
    assert r.status_code == 415  # a photo's first bytes that are not a photo
    sam = cookie(server, "sam@firm.example")
    status, _, body = ask(server, "POST", "/api/client-upload", {"client": "case-ana", "name": "scan.pdf", "data": base64.b64encode(html).decode()}, sam)
    assert status == 400, body


def test_a08_a_host_the_product_does_not_expect_is_refused_and_no_sign_in_address_is_built_from_it(world, tmp_path):
    from review.server import ReviewApp, make_handler, serve

    asked = []

    class Provider:
        def authorize_url(self, state, nonce):
            return "https://login.example/authorize?redirect_uri=" + quote("https://review.firm.example/auth/callback") + f"&state={state}"

    def factory(name, *rest):
        asked.append((name, rest))
        return Provider()

    users = Accounts(tmp_path / "users.json")
    users.add("sam@firm.example", "Sam Exemplo", "attorney")
    app_ = ReviewApp(world, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=users, sign_in_factory=factory)
    httpd = serve(app_, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app_, port, ("review.office.lan",))
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    try:
        base = f"http://127.0.0.1:{port}"
        for host in ("evil.example", f"evil.example:{port}", "review.office.lan.evil.example", f"127.0.0.1:{port + 1}"):
            status, _, body = ask(base, "GET", "/auth/start?provider=google", headers={"Host": host})
            assert (status, body) == (403, b"forbidden host"), host
        status, h, _ = ask(base, "GET", "/auth/start?provider=google", headers={"Host": "review.office.lan"})
        assert status == 302 and "review.firm.example" in h["location"] and "review.office.lan" not in h["location"]
        assert asked == [("google", ())]  # the provider is made from its name alone: the request's Host never reaches it
    finally:
        httpd.shutdown()


def test_a09_x_forwarded_for_from_an_address_that_is_not_the_proxy_is_believed_nowhere(server, app):
    status, _, _ = ask(server, "POST", "/api/login", {"email": "nobody@firm.example", "password": "a wrong guess, long enough"},
                       {"X-Forwarded-For": "203.0.113.99"})
    assert status == 400
    row = [json.loads(x) for x in app.accounts.log_path.read_text().splitlines()][-1]
    assert row["event"] == "sign_in_failed" and row["address"] == "127.0.0.1"  # the connection's, never the header's
    statuses = [ask(server, "POST", "/api/login", {"email": "x@firm.example", "password": "y"}, {"X-Forwarded-For": f"203.0.113.{n}"})[0]
                for n in range(srv.ATTEMPTS["/api/login"] + 1)]
    assert statuses[-1] == 429  # a new made-up address each time bought nothing


def test_a10_two_exports_at_the_same_moment_never_take_the_same_file(tmp_path):
    import sys

    sys.path.insert(0, str(REPO / "tools"))
    import export_firm

    got, barrier = [], threading.Barrier(8)

    def claim():
        barrier.wait()
        got.append(export_firm.claim(None, tmp_path, "2026-10-04"))

    threads = [threading.Thread(target=claim) for _ in range(8)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len({p.name for p in got}) == 8 and all(p.stat().st_mode & 0o077 == 0 for p in got if __import__("os").name == "posix")
    with pytest.raises(export_firm.ExportError):
        export_firm.claim(got[0], tmp_path, "2026-10-04")  # a name given that exists: refused, nothing written over


def test_a11_the_query_layer_asked_for_a_persons_identifier_finds_it_masked_and_the_people_index_answers_only_through_the_gate(server, world, tmp_path):
    """Every table of the query layer but one holds identifiers masked (#1, #2 ...). The people table is the conflict search's index and holds them
    on purpose (src/query.py says so): it is read only through the conflict search, which gives a paralegal not named on a restricted case nothing
    of it (tests/test_conflict_search.py checks the hits byte for byte; here, asked by the A-Number itself)."""
    import os

    import query
    from factgraph import FactGraph

    d = world / "case-ana"
    g = FactGraph.load(d / "fact_graph.json")
    g.add_source("applicant.a_number", "questionnaire", "intake_questionnaire", "A098765432", "A098765432", 0.95)
    g.save(d / "fact_graph.json")
    db = tmp_path / "query.db"
    assert query.rebuild(d, db) and query.rebuild(world / "case-rosa", db)  # Rosa's documents carry an A-Number and a receipt number
    con = sqlite3.connect(db)
    found = []
    for (table,) in con.execute("select name from sqlite_master where type='table'"):
        if table == "people":
            continue
        for col in [c[1] for c in con.execute(f"pragma table_info('{table}')")]:
            for needle in ("098765432", "012345678", "IOE0912345678"):
                found += [(table, col) for _ in con.execute(f"select 1 from '{table}' where cast(\"{col}\" as text) like ?", (f"%{needle}%",))]
    assert found == [], found
    if os.name == "posix":
        assert db.stat().st_mode & 0o077 == 0  # the file itself: owner only
    jane = cookie(server, "jane@firm.example")
    status, _, raw = ask(server, "POST", "/api/conflict-search", {"purpose": "add", "name": "Someone Else", "a_number": "A012345678"}, jane)
    assert status == 200 and b"012345678" not in raw and b"case-rosa" not in raw and b"Rosa" not in raw and b"ROSA" not in raw


def test_a12_the_firms_own_files_are_changed_by_an_attorney_only(server):
    """settings.json, policies_firm.json, rules_approved.json, firm_documents.json, destroyed.json: a paralegal's write is refused (403)."""
    jane = cookie(server, "jane@firm.example")
    for route, body in (("/api/settings", {"section": "firm", "values": {}}), ("/api/policies", {"id": "x"}), ("/api/rules/approve", {"id": "x"}),
                        ("/api/firm-documents", {"office": "main", "kind": "closing", "texts": {}}), ("/api/retention", {"case": "case-ana"})):
        assert ask(server, "POST", route, body, jane)[0] == 403, route


# -- the model itself -------------------------------------------------------------------------------------------------------------------------


def test_the_model_names_every_route_of_the_five_sets_and_every_test_it_cites_exists():
    text = MODEL.read_text(encoding="utf-8")
    routes = set(srv.CASE_GET) | set(srv.CASE_POST) | set(LISTING) | NEITHER | NEITHER_POST | TOKENED | WEBHOOKS
    missing = sorted(r for r in routes if f"`{r}`" not in text)
    assert not missing, missing
    cited = set(re.findall(r"`(tests/[\w/]+\.py)::(\w+)`", text))
    assert len(cited) >= 30
    for path, name in sorted(cited):
        source = (REPO / path).read_text(encoding="utf-8")
        assert re.search(rf"^def {name}\(", source, re.M), f"{path}::{name}"
    for n in range(1, 14):
        assert f"| F{n} |" in text, n
        assert re.search(rf"def test_f{n:02d}_", (REPO / "tests" / "test_threat_model.py").read_text(encoding="utf-8")), n
    assert "K5" in text and "is modelled" in text
    # every record the data catalog lists, and the installer's files, has a line in "Every file the product writes"
    import records

    files = text.split("## Every file the product writes", 1)[1].split("\n## ", 1)[0]
    missing = []
    for rec in records.RECORDS + records.DATABASES:
        items = [x.strip() for x in rec["where"].split("(")[0].split(",") if x.strip()]
        first = items[0]
        if first.startswith("data/clients/<case id>/"):
            wanted = ["`data/clients/<case>/`"]
        elif first.startswith(("data/portal/", "data/clio/")):
            wanted = ["`" + "/".join(first.split("/")[:2]) + "/"]
        else:
            folder = first.rsplit("/", 1)[0] + "/"
            wanted = [f"`{x if x.startswith('data/') else folder + x}`" for x in items]
        missing += [(rec["id"], w) for w in wanted if w not in files]
    for installed in ("`install/backup_passphrase.txt`", "`install/review.log`", "journalctl --user -u i485-review", "`deployment.json`"):
        if installed not in files:
            missing.append(("install", installed))
    assert not missing, missing
    assert hashlib.sha256(text.encode()).hexdigest()  # read whole
