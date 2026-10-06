"""Rate limits on every route anyone can reach without a session (brief J2), the lockout alert, and the longer lockout per account.

Review app: the sign-in routes, the page, who-am-I, the provider's callback, the calendar feed (TOKENED) and Clio's webhook (WEBHOOKS) each have an
allowance per address a minute (ATTEMPTS), keyed by the trusted proxy's X-Forwarded-For, else the connection; past it, 429 with the sentence already
used. Portal: a sign-in link, "send me a link", the page, its sketches and who-am-I without a session, per address; a body too large is refused before
it is read. Everyone here is made up."""

from __future__ import annotations

import http.client
import json
import re
import threading
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

from review import auth, oversight
from review import server as srv
from review.auth import Accounts
from review.server import ReviewApp, make_handler, serve
import schema_path

REPO = Path(__file__).resolve().parent.parent
PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)


def _start(tmp_path, accounts, **kw):
    (tmp_path / "clients").mkdir(exist_ok=True)
    app = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts, **kw)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return app, httpd, port


def ask(port, method, path, body=None, headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    h = {"X-Review-App": "1", "Content-Type": "application/json"} | (headers or {})
    conn.request(method, path, body=body, headers=h)
    r = conn.getresponse()
    out = (r.status, r.read())
    conn.close()
    return out


@pytest.fixture
def cheap_hashes(monkeypatch):
    """scrypt at a test's cost: the lockouts are about counting, not hashing."""
    monkeypatch.setattr(auth, "SCRYPT", {"n": 2**8, "r": 8, "p": 1})


@pytest.fixture
def review(tmp_path, cheap_hashes):
    accounts = Accounts(tmp_path / "staff.json")
    accounts.change_password("ana@firm.example", accounts.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    app, httpd, port = _start(tmp_path, accounts, trusted_proxies=("127.0.0.1",))
    yield {"app": app, "port": port, "accounts": accounts}
    httpd.shutdown()


def _open_paths() -> set[str]:
    source = (REPO / "src" / "review" / "server.py").read_text(encoding="utf-8")
    return set(re.findall(r'"(/[^"]*)"', re.search(r"open_paths = \{([^}]*)\}", source).group(1)))


def test_every_route_reachable_without_a_session_has_an_allowance():
    reachable = _open_paths() | srv.OPEN_GETS | {p for p in srv.TOKENED} | srv.WEBHOOKS
    unlimited = {p for p in reachable if p not in srv.ATTEMPTS and srv.ATTEMPTS_AS.get(p) not in srv.ATTEMPTS}
    assert unlimited == {"/api/logout"}, unlimited  # signing out ends the session the cookie names, or nothing: no work to flood
    assert srv.OPEN_GETS <= _open_paths() and srv.ATTEMPTS["/calendar/"] <= 60 and srv.ATTEMPTS["/"] >= 60


@pytest.mark.parametrize("method,path,body", [
    ("GET", "/", None), ("GET", "/api/me", None), ("GET", "/auth/callback?state=made-up&code=x", None), ("GET", "/calendar/made-up-token.ics", None),
    ("POST", "/api/login", {"email": "nobody@firm.example", "password": "x"}), ("POST", "/api/password", {"email": "x"}), ("POST", "/api/code", {"code": "1"}),
    ("POST", "/api/enrol", {"code": "1"}), ("POST", "/api/setup", {"code": "AAAA"}), ("GET", "/auth/start?provider=nobody", None),
    ("POST", "/clio/webhook", {})])
def test_each_route_says_429_past_its_allowance_per_address_and_another_address_still_gets_in(review, method, path, body):
    port = review["port"]
    bucket = path.split("?")[0]
    bucket = "/calendar/" if bucket.startswith("/calendar/") else srv.ATTEMPTS_AS.get(bucket, bucket)
    if bucket == "/clio/webhook":
        bucket = "/clio/webhook-bad"  # an unsigned call is a refused one: 20 a minute
    limit = srv.ATTEMPTS[bucket]
    data = json.dumps(body).encode() if body is not None else None
    me = {"X-Forwarded-For": "203.0.113.7"}  # through the trusted proxy: the person's own address is the key
    statuses = [ask(port, method, path, data, me)[0] for _ in range(limit + 1)]
    assert 429 not in statuses[:limit] and statuses[-1] == 429, (path, statuses[-3:])
    status, text = ask(port, method, path, data, me)
    assert status == 429 and any(s in text.decode() for s in (srv.TOO_MANY, srv.TOO_MANY_REQUESTS))  # the sentence already used (in JSON, or as text)
    assert ask(port, method, path, data, {"X-Forwarded-For": "203.0.113.8"})[0] != 429  # someone else, through the same proxy


def test_signed_in_staff_are_never_counted_on_the_page_and_who_am_i(review):
    port, accounts = review["port"], review["accounts"]
    token, _ = accounts.sign_in("ana@firm.example", PASSWORD)
    cookie = {"Cookie": f"{auth.COOKIE}={token}"}
    assert all(ask(port, "GET", "/api/me", headers=cookie)[0] == 200 for _ in range(srv.ATTEMPTS["/api/me"] + 5))


def test_x_forwarded_for_from_an_address_that_is_not_the_proxy_is_ignored(tmp_path, cheap_hashes):
    accounts = Accounts(tmp_path / "staff.json")
    accounts.add("ana@firm.example", "Ana Exemplo", "paralegal")
    app, httpd, port = _start(tmp_path, accounts)  # no trusted proxy: the connection is the key, whatever the header says
    try:
        statuses = [ask(port, "GET", "/api/me", headers={"X-Forwarded-For": f"203.0.113.{n % 250}"})[0] for n in range(srv.ATTEMPTS["/api/me"] + 1)]
        assert statuses[-1] == 429  # a new made-up address each time changed nothing
    finally:
        httpd.shutdown()


# -- the lockout alert -------------------------------------------------------------------------------------------------------------------------


def _rows(accounts):
    return [json.loads(x) for x in accounts.log_path.read_text().splitlines()] if accounts.log_path.exists() else []


def test_an_address_refused_three_times_in_a_day_is_one_alert_on_the_staff_screen(review, monkeypatch):
    app, port, accounts = review["app"], review["port"], review["accounts"]
    import time as real
    import types

    now = [real.time()]
    monkeypatch.setattr(srv, "time", types.SimpleNamespace(time=lambda: now[0], monotonic=real.monotonic, sleep=real.sleep, time_ns=real.time_ns))
    me = {"X-Forwarded-For": "198.51.100.4"}
    for episode in range(4):
        for _ in range(srv.ATTEMPTS["/api/login"] + 3):  # over the allowance, refused a few times: one episode
            ask(port, "POST", "/api/login", json.dumps({"email": "nobody@firm.example", "password": "x"}).encode(), me)
        alerts = [r for r in _rows(accounts) if r["event"] == "lockout_alert"]
        assert len(alerts) == (1 if episode >= 2 else 0), episode  # the third episode in a day, once
        now[0] += 120  # the next try comes two minutes later
    alert = alerts[0]
    assert alert["what"] == "address" and alert["address"] == "198.51.100.4" and alert["times"] == 3
    words = oversight.StaffLog(accounts, app.views, lambda: {}).page(page=1, kind="lockout")
    assert any("Alert: this computer address was refused 3 times today" in r["what"] for r in words["rows"]), words["rows"]


def test_an_account_locked_three_times_in_a_day_is_an_alert_and_a_line_in_the_morning_report(tmp_path, cheap_hashes, monkeypatch):
    import overnight

    accounts = Accounts(tmp_path / "review_users.json")
    accounts.change_password("ana@firm.example", accounts.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    clock_now = {"now": datetime(2026, 10, 5, 9, 0, tzinfo=timezone.utc)}
    monkeypatch.setattr(auth, "_now", lambda: clock_now["now"])
    for _ in range(3):
        for _ in range(auth.MAX_FAILURES):
            with pytest.raises(ValueError):
                accounts.sign_in("ana@firm.example", "wrong password, long enough")
        until = datetime.fromisoformat(json.loads(accounts.path.read_text())["users"]["ana@firm.example"]["locked_until"])
        clock_now["now"] = until + timedelta(seconds=1)
    alerts = [r for r in _rows(accounts) if r["event"] == "lockout_alert"]
    assert len(alerts) == 1 and alerts[0]["what"] == "account" and alerts[0]["email"] == "ana@firm.example"
    line = overnight.sign_in_alerts(tmp_path, now=clock_now["now"])
    assert line.startswith("Sign-in alerts: 1 in the last day") and "ana@" not in line and "Settings, Staff" in line  # how many, never whose
    assert overnight.sign_in_alerts(tmp_path, now=clock_now["now"] + timedelta(days=2)) is None  # the last day only
    assert overnight.sign_in_alerts(tmp_path / "nowhere") is None


# -- the longer lockout per account ----------------------------------------------------------------------------------------------------------


def test_someone_guessing_one_account_gets_about_fifty_tries_the_first_day_and_thirty_a_day_after(tmp_path, cheap_hashes, monkeypatch):
    accounts = Accounts(tmp_path / "staff.json")
    accounts.change_password("ana@firm.example", accounts.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    start = datetime(2026, 10, 5, 0, 0, tzinfo=timezone.utc)
    clock_now = {"now": start}
    monkeypatch.setattr(auth, "_now", lambda: clock_now["now"])
    tries, lengths, per_day = 0, [], {}
    while clock_now["now"] < start + timedelta(days=3):
        try:
            accounts.sign_in("ana@firm.example", "a guess that is wrong")
        except ValueError:
            pass
        user = json.loads(accounts.path.read_text())["users"]["ana@firm.example"]
        until = datetime.fromisoformat(user["locked_until"]) if user.get("locked_until") else None
        tries += 1
        day = (clock_now["now"] - start).days
        per_day[day] = per_day.get(day, 0) + 1
        if until and until > clock_now["now"]:
            if not lengths or lengths[-1][1] != until:
                lengths.append((clock_now["now"], until))
            clock_now["now"] = until + timedelta(seconds=1)  # the guesser waits the lockout out, then tries again at once
    minutes = [int((u - a).total_seconds() // 60) for a, u in lengths]
    assert minutes[:6] == [15, 30, 60, 120, 240, 240] and max(minutes) == 240
    assert per_day[0] <= 55 and per_day[1] <= 35 and per_day[2] <= 35, per_day  # it was about 480 a day at 15 minutes, about 120 with an hour at most
    assert auth.LOCKOUT_MAX == timedelta(hours=4)


def test_an_attorney_reset_still_lets_the_person_in_at_once(tmp_path, cheap_hashes, monkeypatch):
    """The lockout is also a way to keep a named person out: an attorney's reset (or IT's on the server) ends it."""
    accounts = Accounts(tmp_path / "staff.json")
    accounts.change_password("ana@firm.example", accounts.add("ana@firm.example", "Ana Exemplo", "paralegal"), PASSWORD)
    for _ in range(auth.MAX_FAILURES):
        with pytest.raises(ValueError):
            accounts.sign_in("ana@firm.example", "wrong password, long enough")
    with pytest.raises(ValueError, match="locked"):
        accounts.sign_in("ana@firm.example", PASSWORD)
    temporary = accounts.reset("ana@firm.example", by="sam@firm.example")
    assert accounts.sign_in("ana@firm.example", temporary)[1]["must_change"]


# -- the portal ------------------------------------------------------------------------------------------------------------------------------------


@pytest.fixture
def portal(tmp_path):
    from fastapi.testclient import TestClient
    from portal.app import create_app
    from portal.notify import Notifier

    app_ = create_app(tmp_path / "portal", base_url="https://portal.example", notifier=Notifier(tmp_path / "outbox.jsonl"), trusted_proxy="testclient")
    return TestClient(app_)


@pytest.mark.parametrize("method,path,limit", [("GET", "/l/made-up-token", 30), ("POST", "/api/link", 120), ("GET", "/", 300),
                                               ("GET", "/api/me", 300), ("GET", "/examples/none_such.svg", 300)])
def test_every_portal_route_anyone_reaches_has_an_allowance_per_address(portal, method, path, limit):
    me, other = {"X-Portal": "1", "X-Forwarded-For": "203.0.113.20"}, {"X-Portal": "1", "X-Forwarded-For": "203.0.113.21"}
    body = {"contact": "someone@example.com"} if method == "POST" else None
    statuses = [portal.request(method, path, headers=me, json=body, follow_redirects=False).status_code for _ in range(limit + 1)]
    assert 429 not in statuses[:limit] and statuses[-1] == 429, (path, statuses[-3:])
    assert portal.request(method, path, headers=other, json=body, follow_redirects=False).status_code != 429


def test_send_me_a_link_with_many_contacts_from_one_address_is_refused_without_saying_who_is_a_client(portal):
    """120 an hour from one address: a workshop or a clinic on one Wi-Fi gets through; the 121st is refused (and the page says so: never "Done!")."""
    from portal import app as portal_app

    assert portal_app.LIMITS["ask_address"] == (120, 3600)
    me = {"X-Portal": "1", "X-Forwarded-For": "203.0.113.30"}
    answers = [portal.post("/api/link", json={"contact": f"person{n}@example.com"}, headers=me) for n in range(121)]
    assert {a.status_code for a in answers[:120]} == {200} and {a.json()["ok"] for a in answers[:120]} == {True}
    assert answers[120].status_code == 429
    # one contact is still held to 5 an hour, from any address: the 6th ask makes no new link (the answer is the same either way)
    same = [portal.post("/api/link", json={"contact": "one@example.com"}, headers={"X-Portal": "1", "X-Forwarded-For": f"198.51.100.{n}"}) for n in range(6)]
    assert {a.status_code for a in same} == {200}


def test_the_page_says_a_refusal_in_the_clients_language_and_never_done():
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert page.count("link_busy: ") == 4  # pt, es, en, ht
    send = page.split("const send = async () => {", 1)[1].split("};", 1)[0]
    assert 'r.ok ? t("sent") : r.status === 429 ? t("link_busy") : t("errors").network' in send


def test_a_portal_body_too_large_is_refused_before_it_is_read(portal):
    from portal import app as portal_app

    big = json.dumps({"contact": "x", "pad": "a" * (2 * 1024 * 1024)})
    r = portal.post("/api/link", content=big, headers={"X-Portal": "1", "Content-Type": "application/json"})
    assert r.status_code == 413 and portal_app.MAX_BODY < len(big)

    def chunks():
        yield b'{"contact": "x"}'

    r = portal.post("/api/link", content=chunks(), headers={"X-Portal": "1", "Content-Type": "application/json"})
    assert r.status_code == 411  # no length: it can't be measured before it is read
    r = portal.post("/api/link", json={"contact": "x"}, headers={"X-Portal": "1"})
    assert r.status_code == 200


def test_a_phone_over_its_allowance_gets_a_page_in_four_languages_and_the_page_words_the_other_refusals(portal):
    """Verification S4: opening a sign-in link or the page past the allowance is a small page in the four languages (DRAFT), never raw JSON; and the
    page words the server's English refusals (too large, a flood, its own error) in the client's language."""
    me = {"X-Forwarded-For": "203.0.113.40"}
    for path, n in (("/l/made-up-token", 31), ("/", 301)):
        r = [portal.get(path, headers=me, follow_redirects=False) for _ in range(n)][-1]
        assert r.status_code == 429 and r.headers["content-type"].startswith("text/html"), path
        for words in ("Muitas tentativas desta rede", "Demasiados intentos desde esta red", "Too many tries from this network", "Twòp esè soti nan rezo"):
            assert words in r.text, (path, words)
        assert "<script" not in r.text and r.headers["content-security-policy"].startswith("default-src 'none'")
    page = (REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    for key in ("err_too_large: ", "err_server_error: ", "err_busy: "):
        assert page.count(key) == 4, key  # pt, es, en, ht
    api = page.split("async function api(", 1)[1].split("\n}\n", 1)[0]
    assert 'too_large: "err_too_large"' in api and 'server_error: "err_server_error"' in api and '"too many requests": "err_busy"' in api
