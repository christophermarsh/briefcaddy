"""Settings, Connections, in the browser: the attorney sets up the firm's Clio app, connects it (the whole OAuth round
trip, against a simulated Clio on this machine), maps the practice areas and tests it; a paralegal never sees it;
Keeping current has a line per connection.

The world's review app ships without Clio. A second review app on an empty data folder is started with Clio's address
pointed at a small simulated Clio (I485_CLIO_BASE, for tests only): its authorize page sends the browser straight back
with a code, its token endpoint and the few API calls answer as Clio's documentation describes. Nothing here reaches
Clio. The Clio user and the attorney are made up.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

from conftest import REPO, Screen, _port, _wait

SECRET = "madeUpSecretForTheTest456"


def _fake_clio(review: dict | None = None):
    """Clio's authorize page, token endpoint and the API calls Settings makes (who_am_i, the lists, matters, documents). review: {"port": the review app's}, for
    the webhook subscription's handshake (Clio calls the subscribed address with X-Hook-Secret and the answer must echo it: here, the review app's own port)."""
    seen: list[str] = []
    hooks: dict[int, dict] = {}

    def handshake(hid: int) -> None:
        import urllib.request

        req = urllib.request.Request(f"http://127.0.0.1:{review['port']}/clio/webhook", data=json.dumps({"data": {"webhook_id": hid}}).encode(), method="POST",
                                     headers={"X-Hook-Secret": hooks[hid]["secret"], "Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=10) as answer:
            if answer.headers.get("X-Hook-Secret") == hooks[hid]["secret"]:
                hooks[hid]["status"] = "enabled"

    class Clio(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def _json(self, body, status=200):
            data = json.dumps(body).encode()
            self.send_response(status)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.send_header("X-RateLimit-Limit", "50")
            self.send_header("X-RateLimit-Remaining", "48")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self):
            url = urlparse(self.path)
            q = {k: v[0] for k, v in parse_qs(url.query).items()}
            seen.append(f"GET {url.path}")
            if url.path == "/oauth/authorize":  # the attorney "approves": straight back to the review app with a code
                assert q["response_type"] == "code" and q["client_id"] == "madeUpClientId123"
                self.send_response(302)
                self.send_header("Location", q["redirect_uri"] + "?" + urlencode({"code": "good-code", "state": q["state"]}))
                self.end_headers()
                return
            if self.headers.get("Authorization") != "Bearer access-1":
                return self._json({"error": {"type": "UnauthorizedError"}}, 401)
            hook = re.fullmatch(r"/api/v4/webhooks/(\d+)\.json", url.path)
            if hook:
                found = hooks.get(int(hook[1]))
                return self._json({"data": {k: v for k, v in found.items() if k != "secret"}} if found else {"error": {"message": "not found"}}, 200 if found else 404)
            data = {"/api/v4/users/who_am_i.json": {"id": 31, "name": "Andrea Exemplo", "email": "andrea@firm.example", "default_calendar_id": 900},
                    "/api/v4/practice_areas.json": [{"id": 7, "name": "Immigration"}, {"id": 8, "name": "Family law"}],
                    "/api/v4/users.json": [{"id": 31, "name": "Andrea Exemplo", "enabled": True, "subscription_type": "Attorney"}],
                    "/api/v4/custom_fields.json": [{"id": 55, "name": "Language", "field_type": "text_line"}],
                    "/api/v4/calendars.json": [{"id": 900, "name": "Firm calendar", "type": "AccountCalendar"}],
                    "/api/v4/matters.json": [{"id": 101, "display_number": "00101-Souza"}],
                    "/api/v4/documents.json": [{"id": 9001}]}.get(url.path)
            return self._json({"data": data, "meta": {"paging": {}}} if data is not None else {"error": {"message": "not found"}}, 200 if data is not None else 404)

        def do_PUT(self):  # activating a subscription with its secret in X-Hook-Secret (how the review app checks which secret is Clio's)
            seen.append(f"PUT {self.path}")
            hook = re.fullmatch(r"/api/v4/webhooks/(\d+)/activate", urlparse(self.path).path)
            found = hooks.get(int(hook[1])) if hook and self.headers.get("Authorization") == "Bearer access-1" else None
            if found and self.headers.get("X-Hook-Secret") == found["secret"]:
                found["status"] = "enabled"
                return self._json({"data": {k: v for k, v in found.items() if k != "secret"}})
            self._json({"error": {"message": "wrong secret"}}, 403)

        def do_DELETE(self):  # only ever a webhook subscription (Stop)
            seen.append(f"DELETE {self.path}")
            hook = re.fullmatch(r"/api/v4/webhooks/(\d+)\.json", urlparse(self.path).path)
            if hook and self.headers.get("Authorization") == "Bearer access-1" and hooks.pop(int(hook[1]), None):
                self.send_response(204)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
            self._json({"error": {"message": "not found"}}, 404)

        def do_POST(self):
            raw = self.rfile.read(int(self.headers.get("Content-Length") or 0))
            body = parse_qs(raw.decode())
            seen.append(f"POST {self.path}")
            if urlparse(self.path).path == "/api/v4/webhooks.json" and self.headers.get("Authorization") == "Bearer access-1":
                data = json.loads(raw)["data"]
                hid = 300 + len(hooks)
                hooks[hid] = {"id": hid, "url": data["url"], "model": data["model"], "events": data["events"], "expires_at": data["expires_at"], "status": "pending",
                              "secret": f"made-up-signing-secret-{hid}"}
                handshake(hid)
                return self._json({"data": {k: v for k, v in hooks[hid].items() if k != "secret"}}, 201)
            if self.path == "/oauth/token" and body.get("code") == ["good-code"] and body.get("client_secret") == [SECRET]:
                return self._json({"token_type": "bearer", "access_token": "access-1", "expires_in": 2592000, "refresh_token": "refresh-1"})
            return self._json({"error": "invalid_grant"}, 400)

    port = _port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Clio)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    httpd.hooks = hooks  # what the test looks at: the subscriptions Clio holds
    return httpd, f"http://127.0.0.1:{port}", seen


def _shot(target, name: str) -> None:
    """The section (a locator) as the attorney sees it, kept when E2E_SHOTS names a folder."""
    if os.environ.get("E2E_SHOTS"):
        Path(os.environ["E2E_SHOTS"]).mkdir(parents=True, exist_ok=True)
        target.screenshot(path=str(Path(os.environ["E2E_SHOTS"]) / f"clio-{name}.png"))


def test_a_paralegal_sees_where_connections_stand_but_not_their_settings(paralegal):
    page = paralegal.page
    page.goto("about:blank")
    page.goto(paralegal.world["review"] + "#settings")
    page.wait_for_selector("#set-firm", state="visible")
    assert page.locator("#set-connections").count() == 0
    page.evaluate("showMaintenance()")
    page.wait_for_selector("#connections-state")
    rows = page.locator("#connections-state").inner_text()
    assert "Clio: switched off. An attorney switches it on in Settings, Connections." in rows
    assert "Google Drive" in rows and "Filevine" in rows and "Your IT" in rows
    assert "Open Connections in Settings" not in rows
    paralegal.check("clio-keeping-current-paralegal")


def test_the_attorney_sets_up_connects_maps_and_tests_clio(world, browser, tmp_path):
    import world as w

    fake, fake_url, seen = _fake_clio()
    (tmp_path / "data" / "clients").mkdir(parents=True)
    port = _port()
    env = world["env"] | {"I485_CLIO_BASE": fake_url}
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(tmp_path / "data" / "clients"), "--port", str(port),
                               "--users", str(world["users"]), "--portal", str(world["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    s = None
    try:
        _wait(f"http://127.0.0.1:{port}/")
        s = Screen(browser, world | {"review": f"http://127.0.0.1:{port}/"}, w.ATTORNEY)
        page = s.page
        s.settle(200)  # the client list finishes loading first: leaving mid-answer is a broken pipe in this server's log
        page.goto("about:blank")  # a fresh load: an address that differs only after # wouldn't reload
        page.goto(f"http://127.0.0.1:{port}/#settings:connections")
        page.wait_for_selector("#set-connections", state="visible")
        sec = page.locator("#set-connections")
        assert sec.locator("#clio-status").inner_text() == "Not connected"
        others = sec.locator("#other-connections").inner_text()
        assert "Google Drive" in others and "Microsoft 365" in others and "Filevine" in others and "keys" in others
        # the Clio app's details: the secret goes to the server and is never shown again
        sec.get_by_label("Sync with Clio every night").check()
        sec.get_by_label("The Clio app's client id").fill("madeUpClientId123")
        sec.get_by_label("The Clio app's client secret").fill(SECRET)
        sec.get_by_label("The review app's address for Clio").fill(f"http://127.0.0.1:{port}/auth/callback")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."
        page.wait_for_selector("#set-connections")
        sec = page.locator("#set-connections")
        assert sec.get_by_label("The Clio app's client secret").input_value() == ""
        assert "Saved (hidden)" in sec.get_by_label("The Clio app's client secret").get_attribute("placeholder")
        assert SECRET not in page.content()
        _shot(sec, "set-up")
        # Connect to Clio: off to (the simulated) Clio and back, connected
        sec.get_by_role("link", name="Connect to Clio").click()
        page.wait_for_url(f"http://127.0.0.1:{port}/#settings:connections")
        page.wait_for_selector("#clio-status")
        assert s.toast() == "Connected to Clio."
        sec = page.locator("#set-connections")
        assert sec.locator("#clio-status").inner_text() == "Connected"
        assert "Connected as Andrea Exemplo by Ana Attorney" in sec.inner_text()
        assert "clio=" not in page.url
        # Clio's lists, then the mapping: Immigration matters are green card cases
        sec.get_by_role("button", name="Load Clio's lists").click()
        assert s.toast() == "Clio's lists loaded."
        page.wait_for_selector("#clio-areas")
        sec = page.locator("#set-connections")
        sec.get_by_label("Questionnaire for Immigration").select_option("i485")
        sec.get_by_label("Office for Andrea Exemplo").select_option("main")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."
        page.wait_for_selector("#clio-areas")
        sec = page.locator("#set-connections")
        assert sec.get_by_label("Questionnaire for Immigration").input_value() == "i485"
        assert sec.get_by_label("Questionnaire for Family law").input_value() == ""
        # Test: read-only, in plain words
        sec.get_by_role("button", name="Test").click()
        page.wait_for_selector("#clio-test div")
        result = page.locator("#clio-test").inner_text()
        assert "Connected to Clio as Andrea Exemplo." in result and "Immigration: 1 open or pending matter(s)." in result
        _shot(sec, "connected-mapped-tested")
        body = s.check("clio-connections")
        assert SECRET not in body and "access-1" not in body and "refresh-1" not in body
        # Keeping current: the Clio line, the firm's own
        page.evaluate("showMaintenance()")
        page.wait_for_selector("#connections-state")
        rows = page.locator("#connections-state").inner_text()
        assert "Clio: connected as Andrea Exemplo; not synced since it was connected" in rows and "The firm" in rows
        _shot(page.locator("#connections-state"), "keeping-current")
        s.check("clio-keeping-current")
        assert not any(x.startswith("DELETE") for x in seen) and "POST /oauth/token" in seen
    finally:
        if s:
            s.close()
        review.terminate()
        log.close()
        fake.shutdown()
    assert "Traceback" not in (tmp_path / "review.log").read_text(errors="replace")


def test_the_attorney_subscribes_to_clios_webhooks_and_the_case_page_says_what_went_to_clio(world, browser, tmp_path):
    """Settings, Connections: Subscribe (the simulated Clio calls the review app back to confirm the address), the state line, "Read Clio uploads within the
    hour", Stop; and the case page's Clio section: the line of what went, what failed and the button, for a case that is a Clio matter."""
    import world as w

    holder: dict = {}
    fake, fake_url, seen = _fake_clio(holder)
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    port = _port()
    holder["port"] = port
    env = world["env"] | {"I485_CLIO_BASE": fake_url}
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(data / "clients"), "--port", str(port),
                               "--users", str(world["users"]), "--portal", str(world["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    s = None
    try:
        _wait(f"http://127.0.0.1:{port}/")
        # this app's own clients folder: a case the test writes there by hand gets its ledger row (conftest.tell_the_app), as installed the lists follow the ledger
        s = Screen(browser, world | {"review": f"http://127.0.0.1:{port}/", "clients": str(data / "clients")}, w.ATTORNEY)
        page = s.page
        s.settle(200)
        page.goto("about:blank")
        page.goto(f"http://127.0.0.1:{port}/#settings:connections")
        page.wait_for_selector("#set-connections", state="visible")
        sec = page.locator("#set-connections")
        assert sec.locator("#clio-webhook").count() == 0  # nothing to subscribe to before Clio is connected
        sec.get_by_label("Sync with Clio every night").check()
        sec.get_by_label("The Clio app's client id").fill("madeUpClientId123")
        sec.get_by_label("The Clio app's client secret").fill(SECRET)
        sec.get_by_label("The review app's address for Clio").fill(f"http://127.0.0.1:{port}/auth/callback")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."
        page.wait_for_selector("#set-connections")
        page.locator("#set-connections").get_by_role("link", name="Connect to Clio").click()
        page.wait_for_url(f"http://127.0.0.1:{port}/#settings:connections")
        page.wait_for_selector("#clio-status")
        assert s.toast() == "Connected to Clio."
        sec = page.locator("#set-connections")
        sec.get_by_role("button", name="Load Clio's lists").click()
        assert s.toast() == "Clio's lists loaded."
        page.wait_for_selector("#clio-areas")
        sec = page.locator("#set-connections")
        sec.get_by_label("Questionnaire for Immigration").select_option("i485")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."
        page.wait_for_selector("#clio-webhook")
        # Clio only sends to an https address: the plain-http one used to connect on this computer is said, in words, and Subscribe waits for a real one
        hook = page.locator("#clio-webhook")
        assert "Not subscribed" in hook.locator("#clio-webhook-state").inner_text() and "https" in hook.inner_text()
        assert hook.get_by_role("button", name="Subscribe").is_disabled() and hook.get_by_role("button", name="Stop").is_disabled()
        sec = page.locator("#set-connections")
        sec.get_by_label("The review app's address for Clio").fill("https://review.firm.example/auth/callback")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."
        page.wait_for_selector("#clio-webhook")
        hook = page.locator("#clio-webhook")
        assert "https://review.firm.example/clio/webhook" in hook.inner_text()
        _shot(hook, "webhook-before")
        # Subscribe: two subscriptions at that address; Clio calls back, the review app echoes the secret, and the line says Subscribed
        hook.get_by_role("button", name="Subscribe").click()
        page.wait_for_function("() => document.querySelector('#clio-webhook-state') && document.querySelector('#clio-webhook-state').innerText.includes('Subscribed')")
        hook = page.locator("#clio-webhook")
        state = hook.locator("#clio-webhook-state").inner_text()
        assert state.startswith("Subscribed") and "renews the subscription before it ends" in state
        assert "Documents: on" in hook.inner_text() and "Matters: on" in hook.inner_text()
        assert sorted(h["model"] for h in fake.hooks.values()) == ["document", "matter"] and {h["url"] for h in fake.hooks.values()} == {"https://review.firm.example/clio/webhook"}
        assert hook.get_by_role("button", name="Subscribe").is_disabled() and hook.get_by_role("button", name="Stop").is_enabled()
        _shot(hook, "webhook-subscribed")
        body = s.check("clio-webhook-subscribed")
        assert "made-up-signing-secret" not in body and "—" not in hook.inner_text() and " -- " not in hook.inner_text()
        assert not any(h["secret"] in page.content() for h in fake.hooks.values())
        # "Read Clio uploads within the hour": a setting, with its own words
        hook.get_by_label("Read Clio uploads within the hour").check()
        assert s.toast() == "Clio uploads are read within the hour."
        # Check asks Clio again; the line is still Subscribed
        hook.get_by_role("button", name="Check").click()
        page.wait_for_function("() => { const e = document.querySelector('#clio-webhook-state'); return !!e && e.innerText.includes('Subscribed'); }")
        assert page.locator("#clio-webhook").get_by_label("Read Clio uploads within the hour").is_checked()
        # Stop: deleted in Clio, forgotten here
        page.locator("#clio-webhook").get_by_role("button", name="Stop").click()
        page.wait_for_function("() => { const e = document.querySelector('#clio-webhook-state'); return !!e && e.innerText.includes('Not subscribed'); }")
        assert not fake.hooks and [x for x in seen if x.startswith("DELETE")] == ["DELETE /api/v4/webhooks/300.json", "DELETE /api/v4/webhooks/301.json"]
        s.check("clio-webhook-stopped")
        # the case page: a Clio matter's line of what went, what failed, and the button
        case = data / "clients" / "ana_clara_exemplo_souza-cl101"
        case.mkdir()
        (case / "fact_graph.json").write_text(json.dumps({"client_id": case.name, "facts": {}}), encoding="utf-8")
        (case / "status.json").write_text("{}", encoding="utf-8")
        state_path = data / "clio" / "state.json"
        st = json.loads(state_path.read_text(encoding="utf-8"))
        st["matters"] = {"101": {"case": case.name, "name": "Ana Clara Exemplo Souza", "number": "00101-Souza", "questionnaire": "i485", "office": None}}
        st["connected"]["at"] = "2026-10-01T09:00:00-04:00"  # connected before the night's sync that follows
        st["last_sync"] = {"at": "2026-10-03T02:10:00-04:00", "direction": "both", "line": "Clio: 1 matter(s) read.", "ok": False}
        refused = "Clio refused the upload of the filing packet for Ana Clara Exemplo Souza: no access. The overnight run tries again tonight."
        st["failed"] = {case.name: {"at": "2026-10-03T02:10:00-04:00", "what": refused}}
        state_path.write_text(json.dumps(st), encoding="utf-8")
        (case / "clio_sent.json").write_text(json.dumps({"version": 1, "matter": "101", "packet_at": "2026-10-02T16:00:00-04:00", "mailings": 0, "stage_at": "2026-09-30T02:05:00-04:00",
                                                         "end": None, "deadlines": 3, "tasks": 0, "by_hand": None,
                                                         "failed": [{"at": "2026-10-03T02:10:00-04:00", "what": refused}]}), encoding="utf-8")
        s.open(case.name, "journey")
        panel = page.locator("#in-clio")
        panel.wait_for(state="visible")
        assert panel.locator("#clio-line").inner_text() == "In Clio: packet sent 10/02/2026, 3 deadlines, stage note 09/30/2026; last sync 10/03/2026 02:10."
        assert "What failed (1)" in panel.inner_text() and "The overnight run tries again tonight." in panel.inner_text() and "/api" not in panel.inner_text()
        assert panel.get_by_role("button", name="Send now").is_enabled()
        _shot(panel, "case-in-clio")
        s.check("clio-case-line")
        # Keeping current: the line says how many cases have something Clio refused
        page.evaluate("showMaintenance()")
        page.wait_for_selector("#connections-state")
        assert "1 case has something Clio refused" in page.locator("#connections-state").inner_text()
        s.check("clio-keeping-current-refused")
    finally:
        if s:
            s.close()
        review.terminate()
        log.close()
        fake.shutdown()
    assert "Traceback" not in (tmp_path / "review.log").read_text(errors="replace")
