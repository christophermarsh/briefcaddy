# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The headers on every answer (brief J2): every route of the review app in the sets tests/test_restricted.py classifies (CASE_GET, CASE_POST, LISTING,
NEITHER, NEITHER_POST, TOKENED, WEBHOOKS) and every route of the client portal, at every status they give (200, 302, 400, 401, 403, 404, 409, 413, 414,
429, 500, 501), carries the same set: Content-Security-Policy (a page's with its own nonce, never 'unsafe-inline' for scripts), Permissions-Policy,
Cross-Origin-Opener-Policy, Cross-Origin-Resource-Policy, no-store (the calendar file: private), nosniff, no-referrer, never in a frame.

Everyone here is made up (the cases of tests/test_restricted.py's world)."""

from __future__ import annotations

import http.client
import json
import re
import urllib.parse

import pytest

from review import server as srv
from test_restricted import LISTING, NEITHER, NEITHER_POST, QUERY, TOKENED, WEBHOOKS, app, server, sign_in, world  # noqa: F401 -- the made-up firm

NEEDED = ("content-security-policy", "permissions-policy", "cross-origin-opener-policy", "cross-origin-resource-policy", "x-content-type-options",
          "referrer-policy", "x-frame-options", "cache-control")


def ask(base: str, method: str, path: str, body: bytes | None = None, headers: dict | None = None):
    u = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=30)
    h = {"X-Review-App": "1", "Content-Type": "application/json"} | (headers or {})
    declared = "Content-Length" in h  # a length said without the body (a body too large is refused before it is read)
    conn.putrequest(method, path, skip_accept_encoding=True)
    for k, v in h.items():
        conn.putheader(k, v)
    if body is not None and not declared:
        conn.putheader("Content-Length", str(len(body)))
    conn.endheaders(None if declared else body)
    r = conn.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders()}, r.read())
    conn.close()
    return out


def check(status: int, h: dict, body: bytes, what: str) -> None:
    missing = [n for n in NEEDED if n not in h]
    assert not missing, (what, status, missing)
    assert h["x-content-type-options"] == "nosniff" and h["referrer-policy"] == "no-referrer" and h["x-frame-options"] == "DENY", (what, status)
    assert h["cross-origin-opener-policy"] == "same-origin" and h["cross-origin-resource-policy"] == "same-origin", (what, status)
    assert "camera=()" in h["permissions-policy"] and "geolocation=()" in h["permissions-policy"], (what, status)
    policy = h["content-security-policy"]
    assert "frame-ancestors 'none'" in policy, (what, status)
    script = re.search(r"script-src ([^;]*)", policy)
    assert script is None or "unsafe-inline" not in script.group(1), (what, status, policy)
    if h.get("content-type", "").startswith("text/html"):
        nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", policy).group(1)
        assert f'<script nonce="{nonce}">'.encode() in body and b"%%NONCE%%" not in body, what  # the page's script runs with this answer's nonce only
    elif not h.get("content-type", "").startswith("application/pdf"):
        assert policy.startswith("default-src 'none'"), (what, status, policy)  # JSON, CSV, a zip, an image, the calendar: nothing runs
    if status == 200 and what.startswith("GET /calendar/"):
        assert "private" in h["cache-control"] and "public" not in h["cache-control"]  # a calendar program may keep it; no proxy may
    else:
        assert h["cache-control"] == "no-store", (what, status, h["cache-control"])


def test_every_route_in_the_five_sets_at_every_status_carries_the_headers(server, app, monkeypatch):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    seen: dict[int, int] = {}

    def look(method, path, body=None, headers=None):
        status, h, data = ask(server, method, path, body, headers)
        check(status, h, data, f"{method} {path.split('?')[0]}")
        seen[status] = seen.get(status, 0) + 1
        return status, h, data

    gets = sorted(set(LISTING) | NEITHER | srv.CASE_GET)
    for route in gets:
        for cookie in (sam, jane, None):  # the attorney (200s), a paralegal (403s and the one 404), nobody (401)
            look("GET", route + "?client=case-ana" + QUERY + "&provider=nobody", headers={"Cookie": cookie} if cookie else {})
        look("GET", route + "?client=case-rosa" + QUERY, headers={"Cookie": jane})  # a restricted case: the one 404
    posts = sorted((NEITHER_POST | srv.CASE_POST) - {"/api/logout", "/api/export-firm", "/api/inbox/read"})
    for route in posts:
        for cookie in (jane, None):  # a paralegal: 400, 403, 404, 409; nobody: 401
            look("POST", route, json.dumps({"client": ""}).encode(), {"Cookie": cookie} if cookie else {})
        look("POST", route, json.dumps({"client": "case-ana", "item_id": "nothing", "action": "confirm"}).encode(), {"Cookie": sam})
    look("POST", "/api/logout", b"{}")
    # the tokened route and the webhook, before anyone is asked to sign in
    for route in TOKENED:
        look("GET", route + "made-up-token.ics")
    feed = json.loads(ask(server, "POST", "/api/calendar", json.dumps({"action": "make", "kind": "person"}).encode(), {"Cookie": sam})[2])
    assert look("GET", feed["path"])[0] == 200
    for route in WEBHOOKS:
        look("POST", route, b"{}", {"X-Review-App": "", "X-Hook-Signature": "guess"})
    # the errors: a route that does not exist, a body too large (refused before it is read), a malformed length, a method the app has none for, a
    # request line too long for http.server, the sign-in allowance used up, and an error nobody expected
    look("GET", "/api/no-such-route", headers={"Cookie": sam})
    look("POST", "/api/decide", b"", {"Cookie": sam, "Content-Length": str(srv.MAX_BODY + 1)})
    look("POST", "/api/decide", b"", {"Cookie": sam, "Content-Length": "12x"})
    look("POST", "/api/decide", b"{}", {"Cookie": sam, "X-Review-App": ""})
    look("PUT", "/api/decide", b"{}", {"Cookie": sam})
    look("GET", "/api/items?client=" + "a" * 70000)
    for _ in range(srv.ATTEMPTS["/api/login"] + 1):
        last = look("POST", "/api/login", json.dumps({"email": "nobody@firm.example", "password": "wrong password, long enough"}).encode())
    assert last[0] == 429
    monkeypatch.setattr(app, "items", lambda *a, **k: 1 / 0)
    status, h, data = look("GET", "/api/items?client=case-ana", headers={"Cookie": sam})
    assert status == 500 and "Something went wrong" in json.loads(data)["error"] and "ZeroDivision" not in data.decode()
    assert {200, 400, 401, 403, 404, 413, 414, 429, 500, 501} <= set(seen), sorted(seen)


def test_a_page_has_a_new_nonce_every_time_and_its_own_policy(server):
    a, b = ask(server, "GET", "/"), ask(server, "GET", "/")
    na = re.search(r"'nonce-([^']+)'", a[1]["content-security-policy"]).group(1)
    nb = re.search(r"'nonce-([^']+)'", b[1]["content-security-policy"]).group(1)
    assert na != nb and len(na) >= 20
    assert f'<style nonce="{na}">'.encode() in a[2] and f'<script nonce="{na}">'.encode() in a[2]
    policy = a[1]["content-security-policy"]
    assert "default-src 'none'" in policy and f"script-src 'nonce-{na}'" in policy and "style-src-attr 'unsafe-inline'" in policy
    assert "connect-src 'self'" in policy and "base-uri 'none'" in policy


def test_the_client_answers_page_runs_its_script_with_a_nonce_too(server):
    sam = sign_in(server, "sam@firm.example")
    status, h, body = ask(server, "GET", "/api/answers?client=pilot-nova", headers={"Cookie": sam})
    assert status == 200 and h["content-type"].startswith("text/html")
    check(status, h, body, "GET /api/answers")


def test_a_streamed_download_carries_them_too(server, app, tmp_path, monkeypatch):
    sam = sign_in(server, "sam@firm.example")
    made = tmp_path / "client-file.zip"
    made.write_bytes(b"PK\x05\x06" + b"\0" * 18)
    monkeypatch.setattr(app, "case_file", lambda client, user: made)
    status, h, body = ask(server, "GET", "/api/case-file.zip?client=case-ana", headers={"Cookie": sam})
    assert status == 200 and body == made.read_bytes()
    check(status, h, body, "GET /api/case-file.zip")


# -- the client portal -----------------------------------------------------------------------------------------------------------------


@pytest.fixture
def portal(tmp_path):
    from fastapi.testclient import TestClient
    from portal.app import create_app
    from portal.notify import Notifier

    app_ = create_app(tmp_path / "portal", base_url="https://portal.example", notifier=Notifier(tmp_path / "outbox.jsonl"))
    return app_, TestClient(app_, raise_server_exceptions=False)


def check_portal(r, what: str) -> None:
    h = {k.lower(): v for k, v in r.headers.items()}
    missing = [n for n in NEEDED if n not in h]
    assert not missing, (what, r.status_code, missing)
    assert h["cross-origin-opener-policy"] == "same-origin" and h["cross-origin-resource-policy"] == "same-origin"
    assert "camera=(self)" in h["permissions-policy"] and "microphone=()" in h["permissions-policy"]
    assert "strict-transport-security" in h  # its base address is https
    policy = h["content-security-policy"]
    script = re.search(r"script-src ([^;]*)", policy)
    assert script is None or "unsafe-inline" not in script.group(1), (what, policy)
    if h.get("content-type", "").startswith("text/html") and "<script" in r.text:
        nonce = re.search(r"script-src 'nonce-([A-Za-z0-9_-]+)'", policy).group(1)
        assert f'<script nonce="{nonce}">' in r.text and "%%NONCE%%" not in r.text
    elif h.get("content-type", "").startswith("text/html"):  # a page with no script (the 429 page): nothing may run at all
        assert policy.startswith("default-src 'none'")
    if r.status_code == 200 and what.startswith("GET /examples/"):
        assert h["cache-control"].startswith("public")  # a sketch of a document: no client's data
    else:
        assert h["cache-control"] == "no-store", (what, r.status_code)


def test_every_portal_route_at_every_status_carries_the_headers(portal, monkeypatch):
    app_, client = portal
    seen = set()
    for route in app_.routes:
        path = re.sub(r"\{[^}]+\}", "x", getattr(route, "path", ""))
        for method in sorted(getattr(route, "methods", None) or {"GET"}):
            for headers in ({"X-Portal": "1"}, {}):  # a write without the portal's header: 403
                r = client.request(method, path, headers=headers, json={} if method in ("POST", "PUT") else None)
                check_portal(r, f"{method} {path}")
                seen.add(r.status_code)
    r = client.get("/")
    check_portal(r, "GET /")
    examples = [p.name for p in (__import__("portal.app", fromlist=["EXAMPLES"]).EXAMPLES).iterdir() if re.fullmatch(r"[a-z0-9_]+\.(svg|png|jpg)", p.name)]
    if examples:
        r = client.get("/examples/" + examples[0])
        assert r.status_code == 200
        check_portal(r, "GET /examples/")
    r = client.get("/no-such-page")
    check_portal(r, "GET /no-such-page")
    seen.add(r.status_code)
    for _ in range(31):  # the sign-in link's allowance from one address
        r = client.get("/l/made-up-token", follow_redirects=False)
    assert r.status_code == 429
    check_portal(r, "GET /l/ (429)")
    seen.add(429)
    import portal.app as portal_app

    monkeypatch.setattr(portal_app, "firm_name", lambda: 1 / 0)
    r = client.get("/")
    assert r.status_code == 500 and "ZeroDivision" not in r.text
    check_portal(r, "GET / (500)")
    seen.add(500)
    assert {200, 401, 403, 404, 429, 500} <= seen, sorted(seen)
