"""Clio's webhooks (src/connectors/clio_hooks.py, the route in src/review/server.py) against a simulated Clio.

The simulated Clio is test_clio.py's, with what Clio's webhooks page describes added: a subscription made with POST, the handshake (Clio calls the
address with X-Hook-Secret and the answer must echo it), deliveries signed with an HMAC-SHA256 of the raw body, an expiry that is renewed with PATCH and a
subscription that is deleted. Nothing reaches Clio; the firm, its attorney and its clients are made up. No real signing secret is anywhere: each test's is
a made-up string, and the tests check it is kept only in the encrypted vault."""

import base64
import hashlib
import hmac
import http.client
import json
import re
import os
import socket
import threading
import time
from datetime import timedelta
from pathlib import Path

import httpx
import pytest

import clock
import events
from connectors import clio, clio_hooks
from review.auth import COOKIE, Accounts
from review.server import ReviewApp, make_handler, serve
from test_clio import ENV, REDIRECT, REPO, SETUP, FakeClio, Quiet, _http, _pdf  # noqa: F401 -- the simulated Clio and its made-up firm
import schema_path

ANA = "ana_clara_exemplo_souza-cl101"


class FakeHooks(FakeClio):
    """test_clio's Clio plus the webhook calls of Clio's API. A subscription's handshake is made by `confirm` (the test's), in this process."""

    def __init__(self):
        super().__init__()
        self.hooks: dict[int, dict] = {}
        self.next_hook = 300
        self.data_root: Path | None = None
        self.confirm = True  # Clio completes the handshake when a subscription is made
        self.etags = 0

    def api(self, method, path, params, request):
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("POST", "/webhooks.json"):
            data = body["data"]
            assert data["url"].startswith("https://") and data["model"] in ("document", "matter") and data["events"] and data["fields"]
            hid = self.next_hook
            self.next_hook += 1
            hook = {"id": hid, "url": data["url"], "model": data["model"], "events": data["events"], "fields": data["fields"], "expires_at": data["expires_at"],
                    "status": "pending", "secret": f"made-up-signing-secret-{hid}"}
            self.hooks[hid] = hook
            if self.confirm:
                self.handshake(hid)
            return self._json(201, {"data": self.shown(hook)})
        m = re.fullmatch(r"/webhooks/(\d+)/activate", path)
        if method == "PUT" and m:
            hook = self.hooks.get(int(m[1]))
            if hook is None or request.headers.get("X-Hook-Secret") != hook["secret"]:
                return self._json(403, {"error": {"type": "ForbiddenError", "message": "wrong secret"}})
            hook["status"] = "enabled"
            return self._json(200, {"data": self.shown(hook)})
        m = re.fullmatch(r"/webhooks/(\d+)\.json", path)
        if m:
            hook = self.hooks.get(int(m[1]))
            if hook is None:
                return httpx.Response(404, json={"error": {"type": "NotFound", "message": "no such webhook"}})
            if method == "GET":
                return self._json(200, {"data": self.shown(hook)})
            if method == "PATCH":
                hook["expires_at"] = body["data"]["expires_at"]
                return self._json(200, {"data": self.shown(hook)})
            if method == "DELETE":
                del self.hooks[hook["id"]]
                return httpx.Response(204)
        m = re.fullmatch(r"/matters/(\d+)\.json", path)
        if method == "GET" and m:
            found = next((x for x in self.matters if x["id"] == int(m[1])), None)
            return self._json(200, {"data": found}) if found else httpx.Response(404, json={"error": {"type": "NotFound", "message": "no matter"}})
        m = re.fullmatch(r"/documents/(\d+)\.json", path)
        if method == "GET" and m:
            owner = next((mid for mid, docs in self.docs.items() if any(d["id"] == int(m[1]) for d in docs)), None)
            return self._json(200, {"data": {"id": int(m[1]), "matter": {"id": owner}}})
        return super().api(method, path, params, request)

    @staticmethod
    def shown(hook):
        return {k: v for k, v in hook.items() if k != "secret"}  # Clio's answer carries no secret: it only ever came in the handshake

    def handshake(self, hid):
        """Clio's first call to the address: X-Hook-Secret and data.webhook_id; enabled when the answer echoes the secret."""
        hook = self.hooks[hid]
        kind, headers = clio_hooks.receive(self.data_root, json.dumps({"data": {"webhook_id": hid}}).encode(), None, hook["secret"])
        if kind == "handshake" and headers.get("X-Hook-Secret") == hook["secret"]:
            hook["status"] = "enabled"
        return kind

    def deliver(self, model: str, event: str, record: int, matter: int | None = None, hook: int | None = None) -> tuple[bytes, str]:
        """(the raw body, its signature) of one delivery, as Clio's page describes them: {"data": {"id", "etag"[, fields]}, "meta": {"event", "webhook_id"}}."""
        hook = hook or next(h["id"] for h in self.hooks.values() if h["model"] == model)
        self.etags += 1
        data = {"id": record, "etag": f'"etag-{self.etags}"'}
        if model == "document" and matter is not None:
            data["matter"] = {"id": matter}
        body = json.dumps({"data": data, "meta": {"event": event, "webhook_id": hook}}).encode()
        return body, sign(body, self.hooks[hook]["secret"])


def sign(body: bytes, secret: str) -> str:
    return hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()


@pytest.fixture
def fake():
    return FakeHooks()


@pytest.fixture
def firm(tmp_path, fake):
    """A connected firm (as test_clio's), with the in-process Clio behind every call and its data folder where the fake's handshake lands."""
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    clio.save_settings(data, SETUP, "Andrea Attorney", env=ENV)
    clio.connect(data, "good-code", "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    fake.data_root = data
    quiet = Quiet()
    pace = lambda: clio.Pace(monotonic=quiet.mono, sleep=quiet.sleep, wall=quiet.wall)  # noqa: E731
    return {"data": data, "clients": tmp_path / "clients", "out": data / "clients", "portal": data / "portal", "fake": fake, "quiet": quiet, "pace": pace,
            "transport": httpx.MockTransport(fake.handle), "root": tmp_path,
            "run": lambda direction="both": clio.run(data, tmp_path / "clients", data / "clients", data / "portal", direction,
                                                     transport=httpx.MockTransport(fake.handle), env=ENV, pace=pace())}


def subscribe(firm, who="Andrea Attorney"):
    return clio_hooks.subscribe(firm["data"], who, transport=firm["transport"], env=ENV, pace=firm["pace"]())


def linked(firm):
    """The matters are read once, so Clio's matter 101 is Ana's case here."""
    firm["run"]("in")
    assert clio.state(firm["data"])["matters"]["101"]["case"] == ANA


def ledger():
    return list(events.rows(events.base_path(None)))


def everything_written(firm) -> str:
    """Every byte the product wrote for this firm and the ledger, as text, but the vault (which is encrypted)."""
    out = []
    for p in list(firm["root"].rglob("*")) + list(events.base_path(None).parent.glob("events-*.jsonl")):
        if p.is_file() and p.name not in ("secrets.enc", "vault.key"):
            out.append(p.read_bytes().decode("utf-8", errors="replace"))
    return "\n".join(out)


# --- the subscription ----------------------------------------------------------------------------------------------------


def test_subscribe_makes_two_subscriptions_at_the_typed_address_and_keeps_the_secret_only_in_the_vault(firm, fake):
    assert clio_hooks.view(firm["data"], ENV)["state"] == "off" and clio_hooks.view(firm["data"], ENV)["can"]["subscribe"]
    view = subscribe(firm)
    assert view["state"] == "on" and [h["says"] for h in view["hooks"]] == ["on", "on"] and view["line"].startswith("Subscribed.")
    hooks = sorted(fake.hooks.values(), key=lambda h: h["model"])
    assert [h["model"] for h in hooks] == ["document", "matter"]
    assert {h["url"] for h in hooks} == {"https://review.firm.example/clio/webhook"}  # the address typed in Settings, with this route's path
    assert hooks[0]["events"] == ["created", "updated"] and set(hooks[1]["events"]) == {"updated", "matter_opened", "matter_pended", "matter_closed"}
    ends = clock.parse(hooks[0]["expires_at"])
    assert timedelta(days=29) < ends - clock.now() < timedelta(days=31)  # Clio: 31 days at most
    # the signing secrets: in the encrypted vault, by Clio's id; in no plain file, no log row and not on the screen
    stored = clio.Vault(firm["data"], ENV).read()["webhook_secrets"]
    assert stored == {str(h["id"]): h["secret"] for h in hooks}
    text = everything_written(firm) + json.dumps(clio_hooks.view(firm["data"], ENV)) + json.dumps(clio.view(firm["data"], ENV))
    assert not any(h["secret"] in text for h in hooks) and all(h["secret"].encode() not in (firm["data"] / "clio" / "secrets.enc").read_bytes() for h in hooks)
    assert {r["action"] for r in ledger()} >= {"webhook_on"}
    # the words on screen: plain, no em dash, no file name
    for line in (view["line"], clio_hooks.view(firm["data"], ENV)["why_not"] or ""):
        assert "—" not in line and " -- " not in line and ".json" not in line
    # pressing it again changes nothing in Clio
    posts = [r for r in fake.requests if r[0] == "POST" and "webhooks" in r[1]]
    subscribe(firm)
    assert [r for r in fake.requests if r[0] == "POST" and "webhooks" in r[1]] == posts


def test_the_address_is_never_taken_from_a_request_and_must_be_https(firm, fake):
    clio.save_settings(firm["data"], {"redirect_uri": "http://127.0.0.1:8485/auth/callback"}, "Andrea Attorney", env=ENV)  # allowed for sign-in on this computer only
    with pytest.raises(clio.NotReady, match="https"):
        subscribe(firm)
    assert not fake.hooks and clio_hooks.view(firm["data"], ENV)["can"]["subscribe"] is False and "https" in clio_hooks.view(firm["data"], ENV)["why_not"]
    clio.save_settings(firm["data"], {"redirect_uri": "https://review.firm.example/prefix/auth/callback"}, "Andrea Attorney", env=ENV)
    subscribe(firm)
    assert {h["url"] for h in fake.hooks.values()} == {"https://review.firm.example/prefix/clio/webhook"}


def test_without_a_connection_nothing_is_asked_of_clio(tmp_path, fake):
    data = tmp_path / "data"
    clio.save_settings(data, SETUP, "Andrea Attorney", env=ENV)  # the app is set up and saved, but nobody has connected it
    with pytest.raises(clio.NotReady, match="connects it first"):
        clio_hooks.subscribe(data, "Andrea Attorney", transport=httpx.MockTransport(fake.handle), env=ENV)
    assert not fake.hooks and not fake.requests
    with pytest.raises(ValueError, match="your name"):
        clio_hooks.subscribe(data, " ", env=ENV)


def test_the_handshake_is_taken_only_in_the_window_and_the_window_outlasts_subscribe(firm, fake):
    body = json.dumps({"data": {"webhook_id": 555}}).encode()
    assert clio_hooks.receive(firm["data"], body, None, "an-attackers-chosen-secret")[0] == "refuse"  # nobody asked for a subscription
    # Clio's handshake follows its answer ("immediately after", its page says): the window is still open when Subscribe has returned
    fake.confirm = False
    view = subscribe(firm)
    assert view["state"] == "waiting" and "waiting for Clio to confirm" in view["line"] and clio_hooks.needs(firm["data"]) is None
    until = float(clio_hooks.load(firm["data"])["pending"]["until"])
    assert 100 < until - clio_hooks.time.time() <= clio_hooks.HANDSHAKE_WINDOW
    for h in fake.hooks.values():
        assert fake.handshake(h["id"]) == "handshake" and h["status"] == "enabled"  # Clio's handshake, after the POST answered
    # the secrets are only candidates: they sign nothing until Clio's report says the subscription is enabled (Check)
    assert not clio.Vault(firm["data"], ENV).read().get("webhook_secrets") and set(clio.Vault(firm["data"], ENV).read()["webhook_candidates"]) == {str(i) for i in fake.hooks}
    doc = next(h for h in fake.hooks.values() if h["model"] == "document")
    early, early_sig = fake.deliver("document", "created", 9800, matter=101)
    assert clio_hooks.receive(firm["data"], early, early_sig, None)[0] == "refuse"
    assert clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())["state"] == "on"
    vault = clio.Vault(firm["data"], ENV).read()
    assert vault["webhook_secrets"] == {str(h["id"]): h["secret"] for h in fake.hooks.values()} and not vault.get("webhook_candidates")
    assert clio_hooks.load(firm["data"])["pending"] is None  # every subscription has Clio's secret: nothing more to wait for
    assert clio_hooks.receive(firm["data"], early, early_sig, None)[0] == "event" and doc["id"]  # the message refused before Check is taken now
    # once a subscription has its secret, a second handshake for it is refused whoever sends it
    other = json.dumps({"data": {"webhook_id": doc["id"]}}).encode()
    clio_hooks._update(firm["data"], lambda d: d.update(pending={"until": 1e12, "by": "Andrea Attorney"}))
    assert clio_hooks.receive(firm["data"], other, None, "a-second-secret-for-the-same-id")[0] == "refuse"
    # a secret that isn't a header-safe string is not taken
    for bad in ("short", "has a space inside"):
        assert clio_hooks.receive(firm["data"], json.dumps({"data": {"webhook_id": 777}}).encode(), None, bad)[0] == "refuse"


def test_when_the_window_ends_and_on_stop_every_secret_for_a_subscription_we_do_not_hold_is_dropped(firm, fake):
    fake.confirm = False
    subscribe(firm)
    # strangers' handshakes inside the window, for ids that are not ours, and for one that is
    for hid in (7777, 7778, min(fake.hooks)):
        assert clio_hooks.receive(firm["data"], json.dumps({"data": {"webhook_id": hid}}).encode(), None, f"planted-secret-for-{hid}")[0] == "handshake"
    assert set(clio.Vault(firm["data"], ENV).read()["webhook_candidates"]) == {"7777", "7778", str(min(fake.hooks))}
    # Clio reports none of them enabled (it never confirmed): no candidate is believed, and the window's end drops them all
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert clio_hooks.load(firm["data"])["pending"] is not None and not clio.Vault(firm["data"], ENV).read().get("webhook_secrets")
    clio_hooks.end_window(firm["data"], ENV, now=clio_hooks.time.time() + clio_hooks.HANDSHAKE_WINDOW + 5)
    vault = clio.Vault(firm["data"], ENV).read()
    assert clio_hooks.load(firm["data"])["pending"] is None and not vault.get("webhook_candidates") and not vault.get("webhook_secrets")
    assert clio_hooks.receive(firm["data"], json.dumps({"data": {"webhook_id": 7777}}).encode(), None, "planted-secret-too-late")[0] == "refuse"
    # a stray secret left some other way (a window left open, a vault from an older build): Stop leaves none, whatever the id
    def plant(v):
        v["webhook_secrets"] = {"7779": "stray-secret-left-behind", str(min(fake.hooks)): "held-for-a-hook-we-delete"}
        v["webhook_candidates"] = {"7780": ["stray-candidate-secret"]}
    clio_hooks._change_vault(firm["data"], ENV, plant)
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    vault = clio.Vault(firm["data"], ENV).read()
    assert not vault.get("webhook_secrets") and not vault.get("webhook_candidates") and not fake.hooks


def test_a_secret_planted_in_the_window_neither_signs_events_nor_keeps_clios_own_handshake_out(firm, fake):
    """The verifier's probe: an attacker's unsigned handshake names the id Clio is about to assign, and lands first. Clio's own handshake follows."""
    linked(firm)
    real_api = fake.api

    def api(method, path, params, request):
        if (method, path) == ("POST", "/webhooks.json") and fake.next_hook not in fake.hooks:
            hb = json.dumps({"data": {"webhook_id": fake.next_hook}}).encode()
            assert clio_hooks.receive(fake.data_root, hb, None, f"attacker-planted-secret-{fake.next_hook}")[0] == "handshake"
        return real_api(method, path, params, request)

    fake.api = api
    view = subscribe(firm)
    assert view["state"] == "on" and all(h["status"] == "enabled" for h in fake.hooks.values())  # Clio's handshake was not refused
    held = clio.Vault(firm["data"], ENV).read()["webhook_secrets"]
    assert held == {str(h["id"]): h["secret"] for h in fake.hooks.values()}  # Clio's secret, not the attacker's
    doc = next(h for h in fake.hooks.values() if h["model"] == "document")
    forged = json.dumps({"data": {"id": 77, "etag": "q", "matter": {"id": 101}}, "meta": {"event": "created", "webhook_id": doc["id"]}}).encode()
    snapshot = (firm["data"] / "clio" / "webhook.json").read_bytes(), len(ledger())
    assert clio_hooks.receive(firm["data"], forged, sign(forged, f"attacker-planted-secret-{doc['id']}"), None)[0] == "refuse"
    assert (firm["data"] / "clio" / "webhook.json").read_bytes() == snapshot[0] and len(ledger()) == snapshot[1] and not clio_hooks.waiting(firm["data"])
    body, sig = fake.deliver("document", "created", 9802, matter=101)
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and clio_hooks.waiting(firm["data"])  # Clio's own is taken


def test_a_candidate_for_an_id_clio_never_enables_signs_nothing(firm, fake):
    linked(firm)
    fake.confirm = False
    subscribe(firm)
    hid = min(fake.hooks)
    assert clio_hooks.receive(firm["data"], json.dumps({"data": {"webhook_id": hid}}).encode(), None, "attacker-secret-for-a-pending-id")[0] == "handshake"
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())  # Clio still says pending
    forged = json.dumps({"data": {"id": 78, "etag": "r", "matter": {"id": 101}}, "meta": {"event": "created", "webhook_id": hid}}).encode()
    assert clio_hooks.receive(firm["data"], forged, sign(forged, "attacker-secret-for-a-pending-id"), None)[0] == "refuse" and not clio_hooks.waiting(firm["data"])
    # and a subscription Clio later suspends is not believed either, though it has its secret
    fake.handshake(hid)
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    fake.hooks[hid]["status"] = "suspended"
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    body, sig = fake.deliver("document", "created", 9803, matter=101, hook=hid)
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "refuse"


def test_the_subscription_is_renewed_before_it_ends_and_stopped_with_the_one_delete_there_is(firm, fake, monkeypatch):
    subscribe(firm)
    line = clio_hooks.renew(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert line == "Clio webhooks: subscribed." and not any(r[0] == "PATCH" and "webhooks" in r[1] for r in fake.requests)  # 30 days left: nothing to do
    monkeypatch.setattr(clock, "_now_override", clock.now() + timedelta(days=24))  # fewer than 10 days left
    line = clio_hooks.renew(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert line == "Clio webhooks: subscribed, renewed 2." and len([r for r in fake.requests if r[0] == "PATCH" and "webhooks" in r[1]]) == 2
    assert all(timedelta(days=29) < clock.parse(h["expires_at"]) - clock.now() < timedelta(days=31) for h in fake.hooks.values())
    assert {r["action"] for r in ledger()} >= {"webhook_renewed"}
    # Clio no longer has one (it ended): the state says so and Keeping current asks an attorney to subscribe again
    del fake.hooks[min(fake.hooks)]
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert clio_hooks.state_of(firm["data"]) == "ended" and "ended" in clio_hooks.needs(firm["data"]) and clio_hooks.view(firm["data"], ENV)["can"]["subscribe"]
    # a suspended one is the same
    subscribe(firm)
    next(iter(fake.hooks.values()))["status"] = "suspended"
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert clio_hooks.state_of(firm["data"]) == "stopped" and "suspended by Clio" in clio_hooks.needs(firm["data"])
    # Stop: each subscription deleted by its own address, the secrets forgotten, and nothing else in Clio can be deleted
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    deletes = [r for r in fake.requests if r[0] == "DELETE"]
    assert deletes and all(re.fullmatch(r"https://app.clio.com/api/v4/webhooks/\d+\.json", u) for _, u in deletes) and not fake.hooks
    assert clio_hooks.view(firm["data"], ENV)["state"] == "off" and not clio.Vault(firm["data"], ENV).read().get("webhook_secrets")
    assert {r["action"] for r in ledger()} >= {"webhook_off"}
    c = clio.Clio(firm["data"], transport=firm["transport"], env=ENV)
    for path, flag in (("/documents/9001.json", False), ("/documents/9001.json", True), ("/webhooks/12/../documents/9001.json", True), ("/matters/101.json", True),
                       ("/webhooks/12.json?x=1", True)):
        with pytest.raises(clio.ClioError, match="never"):
            c.call("DELETE", path, unsubscribe=flag)
    with pytest.raises(ValueError, match="your name"):
        clio_hooks.stop(firm["data"], "", env=ENV)


def test_a_refusal_from_clio_says_so_in_words_and_the_renewal_that_could_not_be_made_reaches_keeping_current(firm, fake, monkeypatch):
    subscribe(firm)
    monkeypatch.setattr(clock, "_now_override", clock.now() + timedelta(days=24))
    real = fake.api
    fake.api = lambda method, path, params, request: (httpx.Response(403, json={"error": {"message": "no webhooks scope"}}) if method == "PATCH" and "webhooks" in path
                                                      else real(method, path, params, request))
    assert clio_hooks.renew(firm["data"], firm["transport"], ENV, firm["pace"]()).startswith("Clio webhooks: could not renew (Clio refused renewing the webhook subscription")
    assert "could not be renewed" in clio_hooks.needs(firm["data"])
    fake.api = real
    assert clio_hooks.renew(firm["data"], firm["transport"], ENV, firm["pace"]()).startswith("Clio webhooks: subscribed") and clio_hooks.needs(firm["data"]) is None


# --- a delivery ---------------------------------------------------------------------------------------------------------


def test_a_signed_document_event_puts_the_matters_case_on_the_read_tonight_list_in_words(firm, fake):
    linked(firm)
    subscribe(firm)
    before = len(ledger())
    body, sig = fake.deliver("document", "created", 9100, matter=101)
    woken = []
    assert clio_hooks.receive(firm["data"], body, sig, None, wake=lambda: woken.append(1)) == ("event", {}) and woken == [1]
    waiting = clio_hooks.waiting(firm["data"])
    assert set(waiting) == {"101"} and waiting["101"]["case"] == ANA and waiting["101"]["n"] == 1 and waiting["101"]["kinds"] == ["document"]
    rows = ledger()[before:]
    assert [r["what"] for r in rows if r["action"] == "clio_event"] == ["Clio said a document was added in this case's matter; it is read tonight"]
    row = next(r for r in rows if r["action"] == "clio_event")
    assert row["case"] == ANA and row["kind"] == "imports" and row["who"] == "Clio" and row["via"] == "connector"
    # the body is never stored: only its digest; the signing secret is nowhere
    text = everything_written(firm)
    assert body.decode() not in text and '"etag-1"' not in text and hashlib.sha256(body).hexdigest() in text
    assert clio_hooks.view(firm["data"], ENV)["waiting"] == 1 and clio_hooks.view(firm["data"], ENV)["last_event"]
    # a second document, then a matter change: the same case, counted
    body, sig = fake.deliver("document", "updated", 9101, matter=101)
    clio_hooks.receive(firm["data"], body, sig, None)
    body, sig = fake.deliver("matter", "matter_closed", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    assert clio_hooks.waiting(firm["data"])["101"]["n"] == 3 and clio_hooks.waiting(firm["data"])["101"]["kinds"] == ["document", "matter"]
    whats = [r["what"] for r in ledger()[before:] if r["action"] == "clio_event"]
    assert whats[1] == "Clio said a document was changed in this case's matter; it is read tonight"
    assert "matter was closed; nothing is changed in the matter" in whats[2] and "—" not in "".join(whats) and " -- " not in "".join(whats)
    # the case page says so
    from connectors import clio_sent

    assert clio_sent.case_view(firm["data"], firm["out"] / ANA, ANA, True)["waiting"]["since"]


def test_an_event_for_a_matter_that_is_not_a_case_here_does_nothing_and_a_document_without_its_matter_is_looked_up(firm, fake):
    linked(firm)
    subscribe(firm)
    before = len(ledger())
    body, sig = fake.deliver("document", "created", 9200, matter=999)
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and not clio_hooks.waiting(firm["data"])
    assert [r["what"] for r in ledger()[before:]] == ["Clio said something changed on a matter that is not linked to a case here; nothing was done"]
    body, sig = fake.deliver("document", "created", 9001)  # no matter in the fields Clio sent
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and clio_hooks.load(firm["data"])["lookups"] == ["9001"]
    assert not clio_hooks.waiting(firm["data"])
    # the read looks it up (one request through the queue) and then the case is on the list and read
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    line = clio_hooks.read_waiting(firm["data"], firm["clients"], firm["portal"], firm["transport"], ENV, firm["pace"]())
    assert line.startswith("Clio: read 1 matter(s) after Clio's webhook") and ("GET", "https://app.clio.com/api/v4/documents/9001.json?fields=id%2Cmatter%7Bid%7D") in fake.requests
    assert not clio_hooks.waiting(firm["data"]) and clio_hooks.load(firm["data"])["lookups"] == []


def test_a_signature_that_is_wrong_missing_or_for_another_body_is_refused_and_nothing_changes(firm, fake):
    linked(firm)
    subscribe(firm)
    body, sig = fake.deliver("document", "created", 9300, matter=101)
    other_secret = sign(body, "not-the-secret-at-all")
    stale = clio_hooks.load(firm["data"])
    for given in (None, "", "abc", other_secret, sig[:-1] + ("0" if sig[-1] != "0" else "1"), sig.upper()[:30], "sha256=", "x" * 500):
        assert clio_hooks.receive(firm["data"], body, given, None)[0] == "refuse"
    assert clio_hooks.receive(firm["data"], body + b" ", sig, None)[0] == "refuse"  # the signature is of these bytes only
    assert clio_hooks.receive(firm["data"], b"{not json", sign(b"{not json", fake.hooks[min(fake.hooks)]["secret"]), None)[0] == "refuse"
    assert clio_hooks.receive(firm["data"], b"[1]", sign(b"[1]", fake.hooks[min(fake.hooks)]["secret"]), None)[0] == "refuse"
    assert clio_hooks.load(firm["data"]) == stale and not clio_hooks.waiting(firm["data"])
    # a message signed with the other subscription's secret but naming this one is refused
    doc_hook = next(h for h in fake.hooks.values() if h["model"] == "document")
    matter_hook = next(h for h in fake.hooks.values() if h["model"] == "matter")
    swapped = json.dumps({"data": {"id": 1, "etag": "x", "matter": {"id": 101}}, "meta": {"event": "created", "webhook_id": doc_hook["id"]}}).encode()
    assert clio_hooks.receive(firm["data"], swapped, sign(swapped, matter_hook["secret"]), None)[0] == "refuse"
    # and a message for a subscription we do not hold, signed with a held secret
    unknown = json.dumps({"data": {"id": 1}, "meta": {"event": "created", "webhook_id": 99999}}).encode()
    assert clio_hooks.receive(firm["data"], unknown, sign(unknown, doc_hook["secret"]), None)[0] == "refuse"
    # the right signature, in the form Clio's page leaves open (base64 or a sha256= prefix), is taken
    assert clio_hooks.receive(firm["data"], body, base64.b64encode(bytes.fromhex(sig)).decode(), None)[0] == "event"
    body2, sig2 = fake.deliver("document", "created", 9301, matter=101)
    assert clio_hooks.receive(firm["data"], body2, "sha256=" + sig2, None)[0] == "event"


def test_the_same_body_twice_is_a_replay_that_is_answered_and_does_nothing(firm, fake):
    linked(firm)
    subscribe(firm)
    body, sig = fake.deliver("document", "created", 9400, matter=101)
    woken = []
    assert clio_hooks.receive(firm["data"], body, sig, None, wake=lambda: woken.append(1))[0] == "event" and woken == [1]
    before = ((firm["data"] / "clio" / "webhook.json").read_bytes(), (firm["data"] / "clio" / "webhook.json").stat().st_mtime_ns,
              (firm["data"] / "clio" / clio_hooks.SEEN_FILE).read_bytes(), len(ledger()))
    for _ in range(3):  # Clio's retry of a message whose first answer was lost: the same answer, and nothing happens
        assert clio_hooks.receive(firm["data"], body, sig, None, wake=lambda: woken.append(1)) == ("event", {})
    after = ((firm["data"] / "clio" / "webhook.json").read_bytes(), (firm["data"] / "clio" / "webhook.json").stat().st_mtime_ns,
             (firm["data"] / "clio" / clio_hooks.SEEN_FILE).read_bytes(), len(ledger()))
    assert after == before and woken == [1]  # no rewrite of any file, no ledger row, no second wake
    assert clio_hooks.waiting(firm["data"])["101"]["n"] == 1
    # a replay with a bad signature is still refused (the signature is checked first)
    assert clio_hooks.receive(firm["data"], body, "0" * 64, None)[0] == "refuse"
    # a later message about the same document has a new etag: not a replay
    body2, sig2 = fake.deliver("document", "updated", 9400, matter=101)
    assert body2 != body and clio_hooks.receive(firm["data"], body2, sig2, None)[0] == "event" and clio_hooks.waiting(firm["data"])["101"]["n"] == 2
    # the digests are forgotten after a month (the overnight run prunes them), and then the same bytes count as new (the body carries no time to say otherwise)
    assert clio_hooks.prune_seen(firm["data"], now=clio_hooks.time.time() + 40 * 86400) == 0
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and clio_hooks.waiting(firm["data"])["101"]["n"] == 3


def test_the_digests_have_their_own_file_so_an_event_never_rewrites_a_big_store(firm, fake):
    linked(firm)
    subscribe(firm)
    log = firm["data"] / "clio" / clio_hooks.SEEN_FILE
    now = clio_hooks.time.time()
    log.write_text("".join(f"{hashlib.sha256(str(i).encode()).hexdigest()} {int(now) - 100}\n" for i in range(5000)), encoding="utf-8")
    clio_hooks._seen_cache.clear()
    small = (firm["data"] / "clio" / "webhook.json").stat().st_size
    body, sig = fake.deliver("document", "created", 9900, matter=101)
    stamp = log.stat().st_size
    clio_hooks.receive(firm["data"], body, sig, None)
    assert log.stat().st_size - stamp < 100 and len(log.read_text(encoding="utf-8").splitlines()) == 5001  # one line appended
    assert (firm["data"] / "clio" / "webhook.json").stat().st_size < small + 2000 and "seen" not in clio_hooks.load(firm["data"])
    # the nightly renewal prunes: nothing older than 31 days stays
    log.write_text(log.read_text(encoding="utf-8") + f"{'a' * 64} {int(now) - 40 * 86400}\n", encoding="utf-8")
    assert clio_hooks.prune_seen(firm["data"]) == 5001 and "a" * 64 not in log.read_text(encoding="utf-8")


def test_a_signed_message_that_cannot_be_processed_says_so_on_the_console_and_gets_the_same_answer(firm, fake, monkeypatch, capsys):
    linked(firm)
    subscribe(firm)
    body, sig = fake.deliver("document", "created", 9950, matter=101)
    monkeypatch.setattr(clio_hooks, "_update", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("the disk is full")))
    assert clio_hooks.receive(firm["data"], body, sig, None) == ("refuse", {})
    monkeypatch.undo()
    err = capsys.readouterr().err
    assert "a correctly signed message could not be processed (RuntimeError)" in err and "the disk is full" not in err and "9950" not in err and "etag" not in err
    # a message that is not signed says nothing: it is a stranger's
    assert clio_hooks.receive(firm["data"], body, "0" * 64, None) == ("refuse", {}) and capsys.readouterr().err == ""
    # and the same message, once the fault is gone, is taken (it was not remembered as taken)
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and clio_hooks.waiting(firm["data"])


def test_an_oversized_call_or_one_with_nothing_subscribed_is_refused(firm, fake):
    linked(firm)
    body = b'{"data": {"id": 1}, "meta": {"event": "created", "webhook_id": 300}}'
    assert clio_hooks.receive(firm["data"], body, sign(body, "anything"), None)[0] == "refuse"  # nothing subscribed: no secret to check against
    subscribe(firm)
    secret = fake.hooks[min(fake.hooks)]["secret"]
    big = json.dumps({"data": {"id": 1, "pad": "x" * clio_hooks.MAX_BODY}, "meta": {"event": "created", "webhook_id": min(fake.hooks)}}).encode()
    assert len(big) > clio_hooks.MAX_BODY and clio_hooks.receive(firm["data"], big, sign(big, secret), None)[0] == "refuse"
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    assert clio_hooks.receive(firm["data"], body, sign(body, secret), None)[0] == "refuse"  # stopped: the old secret is gone


# --- the read within the hour, through the queue ---------------------------------------------------------------------------


def test_the_hourly_read_copies_the_new_document_in_through_the_queue_and_clears_the_list(firm, fake, monkeypatch):
    linked(firm)
    subscribe(firm)
    fake.docs[101].append({"id": 9002, "name": "Birth certificate", "filename": "Birth certificate.pdf", "content_type": "application/pdf", "size": 10,
                           "updated_at": "2026-10-03T09:00:00-04:00", "latest_document_version": {"id": 2}, "external_properties": []})
    fake.content[9002] = _pdf("birth")
    body, sig = fake.deliver("document", "created", 9002, matter=101)
    clio_hooks.receive(firm["data"], body, sig, None)
    # the hourly read is off: the "clio" job reads nothing, and the list is kept for the night
    assert clio_hooks.read_now(firm["data"], firm["portal"], firm["transport"], ENV, clients_root=firm["clients"]) is None
    assert set(clio_hooks.waiting(firm["data"])) == {"101"} and len(fake.requests) and not any("/documents/9002" in u for _, u in fake.requests)
    # on: the job reads, every Clio request through the queue (Pace), and only the matters Clio named
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    waits, seen = [], len(fake.requests)
    real_wait = clio.Pace.wait
    monkeypatch.setattr(clio.Pace, "wait", lambda self: (waits.append(1), real_wait(self))[1])
    assert clio_hooks.read_now(firm["data"], firm["portal"], firm["transport"], ENV, clients_root=firm["clients"]).startswith("Clio: read 1 matter(s)")
    api = [r for r in fake.requests[seen:] if "/api/v4/" in r[1]]
    assert len(waits) == len(api) and api  # one wait in the queue for every request to Clio's API, none outside it
    paths = [u.split("?")[0].replace("https://app.clio.com/api/v4", "") for _, u in api]
    assert paths[0] == "/matters/101.json" and "/matters.json" not in paths and "/documents.json" in paths  # one matter, not the whole firm
    assert sorted(p.name for p in (firm["clients"] / ANA / "source").glob("*.pdf")) == ["Birth certificate [9002].pdf", "Passport [9001].pdf"]
    assert not clio_hooks.waiting(firm["data"]) and clio_hooks.load(firm["data"])["last_read"]["line"].startswith("Clio: read 1 matter(s)")
    assert any(r["action"] == "synced" and r["case"] == ANA for r in ledger())  # the copy in is in the ledger, as a night's is
    # an event that arrives while it reads stays on the list
    body, sig = fake.deliver("document", "created", 9003, matter=101)
    snap = clio_hooks.snapshot(firm["data"])
    clio_hooks.receive(firm["data"], body, sig, None)
    clio_hooks.clear(firm["data"], snap)
    assert clio_hooks.waiting(firm["data"])["101"]["n"] == 1


def test_the_nights_sync_reads_everything_and_clears_the_list_and_says_so(firm, fake):
    linked(firm)
    subscribe(firm)
    body, sig = fake.deliver("document", "created", 9500, matter=101)
    clio_hooks.receive(firm["data"], body, sig, None)
    line = firm["run"]("in")
    assert "1 of them named by Clio's webhooks" in line and not clio_hooks.waiting(firm["data"])


def test_a_matter_event_refreshes_the_clients_portal_details_and_never_overwrites_the_offices(firm, fake):
    from portal.store import PortalStore

    linked(firm)
    store = PortalStore(firm["portal"])
    store.update_profile(ANA, email="", phone="+1 617 555 9999")  # the office's own phone stays; the blank email is filled again from Clio
    assert not store.profile(ANA).get("email")
    subscribe(firm)
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    body, sig = fake.deliver("matter", "updated", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    clio_hooks.read_waiting(firm["data"], firm["clients"], firm["portal"], firm["transport"], ENV, firm["pace"]())
    p = store.profile(ANA)
    assert p["email"] == "ana.exemplo@example.com" and p["phone"] == "+1 617 555 9999" and p["clio_matter"] == "101"
    # a matter closed in Clio is no longer read: dropped from the list, the case left exactly as it is
    fake.matters[0]["status"] = "Closed"
    body, sig = fake.deliver("matter", "matter_closed", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    assert clio_hooks.read_waiting(firm["data"], firm["clients"], firm["portal"], firm["transport"], ENV, firm["pace"]()).startswith("Clio: read 0 matter(s)")
    assert not clio_hooks.waiting(firm["data"]) and (firm["clients"] / ANA / "source").exists()


def test_one_matter_clio_refuses_stays_on_the_list_for_tonight_and_the_others_are_read(firm, fake):
    linked(firm)
    subscribe(firm)
    for matter in (101, 102):
        body, sig = fake.deliver("matter", "updated", matter)
        clio_hooks.receive(firm["data"], body, sig, None)
    real = fake.api
    fake.api = lambda method, path, params, request: (httpx.Response(403, json={"error": {"message": "no"}}) if path == "/matters/101.json" else real(method, path, params, request))
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    line = clio_hooks.read_waiting(firm["data"], firm["clients"], firm["portal"], firm["transport"], ENV, firm["pace"]())
    assert line.startswith("Clio: read 1 matter(s)") and set(clio_hooks.waiting(firm["data"])) == {"101"}
    assert any(e["case"] == ANA for e in clio.state(firm["data"])["errors"])


def test_the_clio_job_runs_once_never_loops_and_never_stops_the_worker(firm, fake, capsys, monkeypatch, tmp_path):
    import jobs

    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))
    linked(firm)
    subscribe(firm)
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    body, sig = fake.deliver("matter", "updated", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    root = jobs.folder_for(firm["out"])
    calls = []
    monkeypatch.setattr(clio_hooks, "read_waiting", lambda *a, **k: calls.append(1) or "read")  # claims it read, clears nothing
    jobs.submit(root, "clio", by="Clio")
    assert jobs.work(firm["out"], firm["portal"], once=True, log=lambda line: None) == 1
    assert calls == [1] and clio_hooks.waiting(firm["data"])  # one read, no loop: the list stays for tonight
    monkeypatch.setattr(clio_hooks, "read_waiting", lambda *a, **k: 1 / 0)
    job = jobs.submit(root, "clio", by="Clio")
    jobs.work(firm["out"], firm["portal"], once=True, log=lambda line: None)
    assert jobs.get(root, job["id"])["state"] == "failed" and clio_hooks.waiting(firm["data"])
    assert any("stopped unexpectedly" in e["what"] for e in clio.state(firm["data"])["errors"])
    assert "ZeroDivisionError" in capsys.readouterr().err  # the detail on the console, the words on the page
    # another Clio step running (the overnight run's): the job gives way at once and is put back for later, the list kept
    monkeypatch.setattr(clio_hooks, "read_waiting", lambda *a, **k: pytest.fail("read beside the overnight run's Clio step"))
    job = jobs.submit(root, "clio", by="Clio")
    done_before = len(list((root / "done").iterdir()))
    with clio.step_lock(firm["data"]):
        jobs.work(firm["out"], firm["portal"], once=True, log=lambda line: None)
    again = jobs.get(root, job["id"])  # the same job, waiting again: no finished record for the try (verification S6)
    assert again["state"] == "queued" and again["tries"] == 1 and again["args"]["not_before"] > time.time() + jobs.CLIO_AGAIN - 60
    assert len(list((root / "done").iterdir())) == done_before and clio_hooks.waiting(firm["data"])
    assert [j["id"] for j in jobs.jobs(root, kind="clio", recent=0) if j["state"] == "queued"] == [job["id"]]
    # at most CLIO_TRIES tries (two hours of a long overnight step): then it is done, and tonight's step reads the list
    path = root / f"{job['id']}.json"
    path.write_text(json.dumps(again | {"tries": jobs.CLIO_TRIES - 1, "args": {"not_before": 0}}), encoding="utf-8")
    with clio.step_lock(firm["data"]):
        jobs.work(firm["out"], firm["portal"], once=True, log=lambda line: None)
    last = jobs.get(root, job["id"])
    assert last["state"] == "done" and "read tonight" in last["result"]["text"] and clio_hooks.waiting(firm["data"])


# --- the route -----------------------------------------------------------------------------------------------------------


@pytest.fixture
def hub(firm, fake, monkeypatch):
    """The review app over this firm's data, served on a port, with the simulated Clio behind it."""
    tmp = firm["root"]
    accounts = Accounts(tmp / "staff.json")
    accounts.add("andrea@firm.example", "Andrea Attorney", "attorney")
    accounts.add("paulo@firm.example", "Paulo Paralegal", "paralegal")
    monkeypatch.setenv("I485_JOBS", str(tmp / "jobs"))  # this firm's own job queue
    app = ReviewApp(firm["out"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, firm["portal"], accounts=accounts)
    app.clio_transport = firm["transport"]
    app.clio_hourly_delay = 0
    clio._update_state(firm["data"], lambda s: s.__setitem__("clients_root", str(firm["clients"])))
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    import jobs

    run = lambda: jobs.work(app.data_root, app.portal_root, once=True, log=lambda line: None, clio_transport=firm["transport"])  # noqa: E731 -- the job worker, here
    yield {"port": port, "app": app, "attorney": cookie("andrea@firm.example"), "paralegal": cookie("paulo@firm.example"), "jobs": run}
    httpd.shutdown()


def call(port, method, path, body=b"", headers=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    conn.request(method, path, body=body, headers=headers or {})
    r = conn.getresponse()
    out = (r.status, {k.lower(): v for k, v in r.getheaders() if k.lower() not in ("date", "server")}, r.read())
    conn.close()
    return out


def test_the_route_takes_a_signed_call_with_no_cookie_and_answers_a_bad_one_as_it_answers_a_path_that_does_not_exist(firm, fake, hub):
    linked(firm)
    subscribe(firm)
    port = hub["port"]
    body, sig = fake.deliver("document", "created", 9600, matter=101)
    status, headers, answer = call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sig, "Content-Type": "application/json"})  # no cookie, no X-Review-App
    assert status == 200 and answer == b"{}" and set(clio_hooks.waiting(firm["data"])) == {"101"}
    # every way of being refused is one answer, and it is the answer a made-up POST path gets from the same caller
    made_up = call(port, "POST", "/api/no-such-route", body, {"X-Hook-Signature": sig, "Content-Type": "application/json"})
    assert made_up[0] == 403 and made_up[2] == b"forbidden"
    bad = [call(port, "POST", "/clio/webhook", body, {"Content-Type": "application/json"}),  # unsigned
           call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sign(body, "wrong"), "Content-Type": "application/json"}),  # badly signed
           call(port, "POST", "/clio/webhook", b"{}", {"X-Hook-Signature": sign(b"{}", "wrong")}),
           call(port, "POST", "/clio/webhook", body, {"X-Hook-Secret": "an-attackers-secret", "Content-Type": "application/json"})]  # a handshake nobody asked for
    assert all(b == made_up for b in bad), [b for b in bad if b != made_up]
    # a replay (Clio's retry of a message whose answer was lost) is answered like the first and does nothing, however many times, and is not counted as refused
    for _ in range(25):
        assert call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sig, "Content-Type": "application/json"})[0:3:2] == (200, b"{}")
    # nothing signed with a made-up secret got in, and a call with a cookie but no signature is no better: the cookie is not the credential
    assert clio_hooks.waiting(firm["data"])["101"]["n"] == 1 and len(hub["app"]._attempts["/clio/webhook-bad 127.0.0.1"]) <= 10  # the 25 replays are not among the refused calls
    assert call(port, "POST", "/clio/webhook", body, {"Cookie": hub["attorney"], "X-Review-App": "1", "Content-Type": "application/json"}) == made_up
    # a made-up path under it, and a GET, are the app's own answers for a path that is not a route
    assert call(port, "GET", "/clio/webhook")[0] in (401, 404) and call(port, "GET", "/clio/webhook") == call(port, "GET", "/clio/no-such-thing")
    assert call(port, "POST", "/clio/webhook/", body, {"X-Hook-Signature": sig}) == made_up and call(port, "POST", "/clio/webhookx", body) == made_up


def test_the_handshake_over_the_route_echoes_the_secret_in_the_header_and_enables_the_subscription(firm, fake, hub):
    """Clio's own first call arrives over the network: an unsigned POST with X-Hook-Secret, answered 200 with the same header."""
    port = hub["port"]
    fake.confirm = False
    subscribe(firm)  # the subscriptions exist, pending, and the window is still open after Subscribe has returned
    hook = next(iter(fake.hooks.values()))
    hook["status"] = "enabled"  # Clio enables it once the answer to its handshake has our echo
    status, headers, _ = call(port, "POST", "/clio/webhook", json.dumps({"data": {"webhook_id": hook["id"]}}).encode(),
                              {"X-Hook-Secret": hook["secret"], "Content-Type": "application/json"})
    assert status == 200 and headers["x-hook-secret"] == hook["secret"]
    for _ in range(200):  # the review app then asks Clio, on its own thread, through the queue, and keeps the secret that is Clio's
        if not hub["app"]._clio_confirming.locked() and clio.Vault(firm["data"], ENV).read().get("webhook_secrets"):
            break
        time.sleep(0.05)
    assert clio.Vault(firm["data"], ENV).read()["webhook_secrets"][str(hook["id"])] == hook["secret"]
    assert clio_hooks.load(firm["data"])["hooks"][str(hook["id"])]["status"] == "enabled"


def test_an_oversized_body_is_refused_before_a_byte_of_it_is_read(firm, fake, hub):
    linked(firm)
    subscribe(firm)
    port = hub["port"]
    made_up = call(port, "POST", "/api/no-such-route", b"{}", {"Content-Type": "application/json"})
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(f"POST /clio/webhook HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Type: application/json\r\nContent-Length: {clio_hooks.MAX_BODY + 1}\r\n"
              "X-Hook-Signature: abc\r\n\r\n".encode())  # the headers only: the body is never sent, and the answer comes anyway
    answer = ""
    while True:  # the answer is read to the end (the server closes after it): headers and body may arrive in two pieces
        piece = s.recv(4096)
        if not piece:
            break
        answer += piece.decode()
    s.close()
    assert answer.startswith("HTTP/1.0 403") or answer.startswith("HTTP/1.1 403")
    assert answer.endswith("forbidden") and "Content-Type: text/plain" in answer
    assert made_up[0] == 403
    s = socket.create_connection(("127.0.0.1", port), timeout=10)
    s.sendall(f"POST /clio/webhook HTTP/1.1\r\nHost: 127.0.0.1:{port}\r\nContent-Length: banana\r\n\r\n".encode())
    assert " 403 " in s.recv(4096).decode().split("\r\n")[0]
    s.close()


def test_the_route_is_rate_limited_like_the_sign_in_routes_and_a_guesser_cannot_lock_clio_out(firm, fake, hub):
    linked(firm)
    subscribe(firm)
    port = hub["port"]
    body, sig = fake.deliver("document", "created", 9700, matter=101)
    answers = [call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": "guess"})[0] for _ in range(22)]
    assert answers[:20] == [403] * 20 and answers[20:] == [429, 429]  # 20 refused calls a minute from one address, then 429
    assert call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sig})[0] == 200  # a good call from the same address still gets in
    from review.server import ATTEMPTS

    assert ATTEMPTS["/clio/webhook-bad"] == 20 and ATTEMPTS["/clio/webhook"] >= 100
    for _ in range(ATTEMPTS["/clio/webhook"]):
        hub["app"].limited("/clio/webhook", "127.0.0.1")
    assert call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sig})[0] == 429  # past the allowance for every call, a good one is told to wait


def test_the_hourly_read_starts_from_an_event_over_the_route_and_a_restart_picks_up_what_waited(firm, fake, hub):
    linked(firm)
    subscribe(firm)
    port, app = hub["port"], hub["app"]
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    fake.docs[101].append({"id": 9002, "name": "Birth certificate", "filename": "Birth certificate.pdf", "content_type": "application/pdf", "size": 10,
                           "updated_at": "2026-10-03T09:00:00-04:00", "latest_document_version": {"id": 2}, "external_properties": []})
    fake.content[9002] = _pdf("birth")
    body, sig = fake.deliver("document", "created", 9002, matter=101)
    assert call(port, "POST", "/clio/webhook", body, {"X-Hook-Signature": sig})[0] == 200
    import jobs

    assert [j["kind"] for j in jobs.jobs(app.jobs_root, recent=0)] == ["clio"]  # answered at once; the reading is the job worker's
    assert hub["jobs"]() == 1
    assert (firm["clients"] / ANA / "source" / "Birth certificate [9002].pdf").exists() and not clio_hooks.waiting(firm["data"])
    # events that arrived while the server was down are read when it starts
    body, sig = fake.deliver("matter", "updated", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    assert clio_hooks.waiting(firm["data"])
    app.clio_resume()
    assert hub["jobs"]() == 1
    assert not clio_hooks.waiting(firm["data"])


def test_subscribe_and_stop_from_settings_are_the_attorneys_and_the_page_shows_the_state_in_words(firm, fake, hub):
    port = hub["port"]

    def act(who, action, **extra):
        return call(port, "POST", "/api/connections", json.dumps({"action": action, **extra}).encode(),
                    {"Cookie": hub[who], "X-Review-App": "1", "Content-Type": "application/json", "Host": f"127.0.0.1:{port}"})

    assert act("paralegal", "webhook_on")[0] == 403 and not fake.hooks
    status, _, answer = act("attorney", "webhook_on")
    shown = json.loads(answer)["clio"]["webhook"]
    assert status == 200 and shown["state"] == "on" and shown["url"] == "https://review.firm.example/clio/webhook"  # not the Host header the request carried
    assert {h["url"] for h in fake.hooks.values()} == {"https://review.firm.example/clio/webhook"}
    assert act("paralegal", "webhook_off")[0] == 403 and fake.hooks
    status, _, answer = act("attorney", "webhook_off")
    assert status == 200 and json.loads(answer)["clio"]["webhook"]["state"] == "off" and not fake.hooks
    status, _, answer = act("attorney", "webhook_check")
    assert status == 200
    # an attorney's tick for "Read Clio uploads within the hour" is a setting, with who and when
    status, _, answer = act("attorney", "save", values={"uploads_hourly": True})
    assert status == 200 and json.loads(answer)["clio"]["uploads_hourly"] is True and clio.settings(firm["data"])["uploads_hourly"] is True
    # a Clio that refuses says so in words, never a path or a code
    fake.confirm = False
    real = fake.api
    fake.api = lambda method, path, params, request: httpx.Response(403, json={"error": {"message": "x"}}) if (method, path) == ("POST", "/webhooks.json") else real(method, path, params, request)
    status, _, answer = act("attorney", "webhook_on")
    msg = json.loads(answer)["error"]
    assert status == 400 and "Clio refused subscribing to Clio's webhooks" in msg and "/api" not in msg and "403" not in msg


def test_disconnecting_stops_the_subscriptions_first_while_the_token_still_works(firm, fake):
    subscribe(firm)
    clio.disconnect(firm["data"], "Andrea Attorney", env=ENV, transport=firm["transport"])
    assert not fake.hooks and clio_hooks.view(firm["data"], ENV)["state"] == "off"
    assert clio.Vault(firm["data"], ENV).read() == {"client_secret": SETUP["client_secret"]}
    order = [m for m, u in fake.requests if m == "DELETE" or "deauthorize" in u]
    assert order == ["DELETE", "DELETE", "POST"]


def test_the_register_item_for_the_subscription_and_keeping_currents_line(firm, fake, monkeypatch):
    import maintenance

    monkeypatch.setattr(clio, "DATA", firm["data"])  # the finding reads this firm's folder, never the machine's

    items = {i["id"]: i for i in maintenance.registry()["items"]}
    item = items["clio_webhooks"]
    assert item["party"] == "firm" and item["cadence"] == "monthly" and item["firm_steps"] and item["check"]["type"] == "clio_webhook"
    assert "docs.developers.clio.com/guides/clio-manage/webhooks" in item["source"] and "10/03/2026" in item["source"]
    assert not any(w in " ".join(item["firm_steps"]) for w in ("schemas/", "src/", ".json", ".py", "IT:", "clio/webhook"))
    assert maintenance._local_finding(item, clock.today()) is None
    subscribe(firm)
    next(iter(fake.hooks.values()))["status"] = "suspended"
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    import connectors.clio_hooks as hooks

    assert hooks.needs(firm["data"]) and "Subscribe again" in hooks.needs(firm["data"])
    # the check type itself (what Keeping current reads): due at once with the words, for a suspended subscription and for a renewal that failed
    assert maintenance._local_finding(item, clock.today()) == hooks.needs(firm["data"])
    fake.hooks[min(fake.hooks)]["status"] = "enabled"  # Clio lets it run again
    clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert maintenance._local_finding(item, clock.today()) is None
    clio_hooks._update(firm["data"], lambda d: d.update(problem={"at": clock.stamp(), "what": "Clio refused renewing the webhook subscription: no access."}))
    assert maintenance._local_finding(item, clock.today()).startswith("The webhook subscription could not be renewed")
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    assert maintenance._local_finding(item, clock.today()) is None  # nothing subscribed: nothing to look after


# --- one worker, one queue, one Clio step at a time ---------------------------------------------------------------------------


def test_two_events_at_once_queue_one_clio_job_that_waits_its_minutes(firm, fake, hub, monkeypatch):
    import jobs

    app = hub["app"]
    app.clio_wake()
    assert jobs.jobs(app.jobs_root, recent=0) == []  # the attorney has not switched the hourly read on: nothing is queued
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    real_submit = jobs.submit

    def slow_submit(*a, **k):
        time.sleep(0.2)  # wide enough for the second event to arrive while the first is writing its job
        return real_submit(*a, **k)

    monkeypatch.setattr(jobs, "submit", slow_submit)
    app.clio_hourly_delay = 300.0
    threads = [threading.Thread(target=app.clio_wake) for _ in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    queued = jobs.jobs(app.jobs_root, kind="clio", recent=0)
    assert len(queued) == 1 and queued[0]["args"]["not_before"] > time.time() + 200
    assert hub["jobs"]() == 0  # not due for five minutes: a batch of uploads is one read


def test_every_clio_operation_of_a_process_waits_in_the_one_queue(firm, fake, monkeypatch):
    """Subscribe, Check, the renewal, the hourly read and the sync were five queues of 50 a minute: now one, the process's, for this firm."""
    used = set()
    real = clio.Pace.wait
    monkeypatch.setattr(clio.Pace, "wait", lambda self: (used.add(id(self)), real(self))[1])
    data, transport = firm["data"], firm["transport"]
    clio_hooks.subscribe(data, "Andrea Attorney", transport=transport, env=ENV)
    clio_hooks.check(data, transport, ENV)
    clio_hooks.renew(data, transport, ENV)
    linked_run = clio.run(data, firm["clients"], firm["out"], firm["portal"], "in", transport=transport, env=ENV)
    clio.save_settings(data, {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    body, sig = fake.deliver("matter", "updated", 101)
    clio_hooks.receive(data, body, sig, None)
    clio_hooks.read_waiting(data, firm["clients"], firm["portal"], transport, ENV)
    assert linked_run.startswith("Clio: ") and used == {id(clio.shared_pace(data))}
    assert clio.shared_pace(data) is clio.shared_pace(data) and clio.shared_pace(data) is not clio.shared_pace(firm["root"] / "another-firm" / "data")


def test_a_file_lock_is_the_systems_one_held_by_one_owner_and_a_release_by_a_non_holder_changes_nothing(tmp_path):
    path = tmp_path / "x.lock"
    mine = clio.FileLock(path, wait=0)
    with mine:
        with pytest.raises(clio.Busy):  # held: another object (a thread, a process) cannot take it
            clio.FileLock(path, wait=0).__enter__()
        stranger = clio.FileLock(path, wait=0)  # never entered: leaving it is a no-op, and it does not free the holder's lock
        stranger.__exit__(None, None, None)
        stranger.__exit__(None, None, None)
        with pytest.raises(clio.Busy):
            clio.FileLock(path, wait=0).__enter__()
        with pytest.raises(RuntimeError):  # the same object cannot take its own lock twice
            mine.__enter__()
    with clio.FileLock(path, wait=0):  # released: free again, with no file to clear and no age to wait out
        pass
    mine.__exit__(None, None, None)  # a second release is a no-op too
    assert path.exists()  # the lock file is never deleted: that is how two owners happen


HOLDER = """
import os, sys, time
sys.path.insert(0, {src!r})
from pathlib import Path
from connectors import clio
lock = clio.FileLock(Path({path!r}), wait=0)
lock.__enter__()
print("held", flush=True)
how = sys.stdin.readline().strip()
if how == "crash":
    os._exit(1)  # no release, no cleanup: the system frees the lock
"""


def _holder(path):
    import subprocess
    import sys

    src = str(Path(clio.__file__).resolve().parents[1])
    proc = subprocess.Popen([sys.executable, "-c", HOLDER.format(src=src, path=str(path))], stdin=subprocess.PIPE, stdout=subprocess.PIPE, text=True,
                            env={**os.environ, "PYTHONPATH": src})
    assert proc.stdout.readline().strip() == "held"
    return proc


@pytest.mark.parametrize("how", ["exit", "crash"])
def test_two_processes_contend_for_the_step_lock_one_holds_the_other_waits_and_gives_way(tmp_path, how):
    path = tmp_path / "clio" / "step.lock"
    other = _holder(path)
    try:
        with pytest.raises(clio.Busy, match="Another Clio sync is running"):
            clio.FileLock(path, wait=0).__enter__()  # a step that finds it held gives way at once ...
        t0 = time.monotonic()
        with pytest.raises(clio.Busy):
            clio.FileLock(path, wait=0.5).__enter__()  # ... or waits its time and then gives way
        assert 0.4 < time.monotonic() - t0 < 5
        other.stdin.write(how + "\n")  # the holder leaves: by returning (its lock goes with it) or by dying without a word
        other.stdin.flush()
        other.wait(timeout=20)
    finally:
        other.kill() if other.poll() is None else None
    with clio.FileLock(path, wait=0):  # at once, nothing to take over
        pass


def test_the_waiting_step_gets_the_lock_when_the_holder_leaves(tmp_path):
    path = tmp_path / "clio" / "step.lock"
    other = _holder(path)
    threading.Timer(0.5, lambda: (other.stdin.write("exit\n"), other.stdin.flush())).start()
    t0 = time.monotonic()
    with clio.FileLock(path, wait=15):
        assert 0.3 < time.monotonic() - t0 < 10
    other.wait(timeout=20)


def test_the_hourly_read_and_the_nightly_step_never_run_together(firm, fake, hub, monkeypatch):
    linked(firm)
    subscribe(firm)
    clio.save_settings(firm["data"], {"uploads_hourly": True}, "Andrea Attorney", env=ENV)
    body, sig = fake.deliver("matter", "updated", 101)
    clio_hooks.receive(firm["data"], body, sig, None)
    with clio.step_lock(firm["data"]):  # the overnight run is in its Clio step
        with pytest.raises(clio.Busy):  # the job gives way (and is put back for later): the night reads these
            clio_hooks.read_now(firm["data"], firm["portal"], firm["transport"])
        assert set(clio_hooks.waiting(firm["data"])) == {"101"}
        monkeypatch.setattr(clio, "step_lock", lambda data_root, wait=0.0: clio.FileLock(firm["data"] / "clio" / "step.lock", wait=0))
        assert "skipped tonight" in clio.nightly(firm["data"], firm["clients"], firm["out"], "in")
    assert clio_hooks.read_now(firm["data"], firm["portal"], firm["transport"]).startswith("Clio: read 1 matter(s)")
    with clio.step_lock(firm["data"]):  # and the step is free again afterwards
        pass


def test_the_files_of_the_clio_folder_are_written_under_a_lock(firm, monkeypatch):
    taken = []
    real = clio.FileLock.__enter__
    monkeypatch.setattr(clio.FileLock, "__enter__", lambda self: (taken.append(self.path.name), real(self))[1])
    clio._update_state(firm["data"], lambda s: s.__setitem__("x", 1))
    clio_hooks._update(firm["data"], lambda d: d.update(last_event="now"))
    clio_hooks.seen_add(firm["data"], "ab" * 32, 1.0)
    assert taken == ["state.lock", "webhook.lock", "webhook_seen.lock"]


def test_the_vault_is_written_under_the_same_kind_of_lock(firm, monkeypatch):
    taken = []
    real = clio.FileLock.__enter__
    monkeypatch.setattr(clio.FileLock, "__enter__", lambda self: (taken.append(self.path.name), real(self))[1])
    clio.Vault(firm["data"], ENV).update(probe="x")
    clio_hooks._change_vault(firm["data"], ENV, lambda v: v.update(webhook_candidates={}))
    assert taken == ["vault.lock", "vault.lock"] and clio.Vault(firm["data"], ENV).read()["probe"] == "x"


# --- a stranger cannot use up the slots, and "enabled with no key" is its own state ---------------------------------------------------


def test_planted_handshakes_never_turn_clios_own_away(firm, fake):
    linked(firm)
    real_api = fake.api

    def plant_three_for_the_id_clio_is_about_to_get(method, path, params, request):
        if (method, path) == ("POST", "/webhooks.json") and fake.next_hook not in fake.hooks:
            for n in range(3):
                hb = json.dumps({"data": {"webhook_id": fake.next_hook}}).encode()
                assert clio_hooks.receive(fake.data_root, hb, None, f"planted-{fake.next_hook}-number-{n}")[0] == "handshake"
        return real_api(method, path, params, request)

    fake.api = plant_three_for_the_id_clio_is_about_to_get
    assert subscribe(firm)["state"] == "on"
    held = clio.Vault(firm["data"], ENV).read()["webhook_secrets"]
    assert held == {str(h["id"]): h["secret"] for h in fake.hooks.values()} and all(h["status"] == "enabled" for h in fake.hooks.values())
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    # twelve (and more) handshakes for ids nobody holds, planted before Clio's own: the oldest strangers make room
    fake.api = real_api
    fake.next_hook = 400
    clio_hooks._update(firm["data"], lambda d: d.update(pending=None))

    def flood(method, path, params, request):
        if (method, path) == ("POST", "/webhooks.json") and getattr(flood, "done", None) != fake.next_hook:
            flood.done = fake.next_hook
            for i in range(clio_hooks.CANDIDATES_MAX + 8):
                assert clio_hooks.receive(fake.data_root, json.dumps({"data": {"webhook_id": 100000 + i}}).encode(), None, f"stranger-secret-number-{i}")[0] == "handshake"
        return real_api(method, path, params, request)

    fake.api = flood
    assert subscribe(firm)["state"] == "on"
    held = clio.Vault(firm["data"], ENV).read()["webhook_secrets"]
    assert held == {str(h["id"]): h["secret"] for h in fake.hooks.values()}
    # an id we hold is never refused for want of room, and its own candidates keep only the newest few
    clio_hooks._update(firm["data"], lambda d: d.update(pending={"until": 1e12, "by": "Andrea Attorney"}))
    clio_hooks._change_vault(firm["data"], ENV, lambda v: v.update(webhook_secrets={}))
    mine = min(fake.hooks)
    clio_hooks._update(firm["data"], lambda d: d["hooks"][str(mine)].update(status="pending"))  # (an enabled one takes no handshake at all: see the keyless test)
    for n in range(clio_hooks.CANDIDATES_PER_ID + 4):
        assert clio_hooks.receive(firm["data"], json.dumps({"data": {"webhook_id": mine}}).encode(), None, f"another-for-our-id-{n}")[0] == "handshake"
    cands = clio.Vault(firm["data"], ENV).read()["webhook_candidates"]
    assert cands[str(mine)] == [f"another-for-our-id-{n}" for n in range(4, 4 + clio_hooks.CANDIDATES_PER_ID)]
    assert len([k for k in cands if int(k) >= 100000]) <= clio_hooks.CANDIDATES_MAX


def test_enabled_with_no_key_held_is_its_own_state_and_neither_check_nor_the_renewal_clears_it(firm, fake, monkeypatch):
    linked(firm)
    real_api = fake.api

    def plant_and_accept_every_activate(method, path, params, request):
        if (method, path) == ("POST", "/webhooks.json") and fake.next_hook not in fake.hooks:
            hb = json.dumps({"data": {"webhook_id": fake.next_hook}}).encode()
            assert clio_hooks.receive(fake.data_root, hb, None, f"planted-for-{fake.next_hook}")[0] == "handshake"
        if method == "PUT" and path.endswith("/activate"):  # a Clio that does not tell the secrets apart
            return fake._json(200, {"data": {}})
        return real_api(method, path, params, request)

    fake.api = plant_and_accept_every_activate
    view = subscribe(firm)
    sentence = "Clio has this address but the office holds no key for it: press Stop, then Subscribe again."
    assert view["state"] == "unconfirmed" and view["line"] == sentence and not clio.Vault(firm["data"], ENV).read().get("webhook_secrets")
    assert [h["says"] for h in view["hooks"]] == ["no key held", "no key held"] and view["problem"] is None
    assert clio_hooks.needs(firm["data"]) == sentence  # Keeping current: the same words
    # Check writes Clio's "enabled" back over it: still not on
    again = clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert again["state"] == "unconfirmed" and again["line"] == sentence and all(h["status"] == "enabled" for h in fake.hooks.values())
    # the overnight renewal does not clear it, and no day of renewals makes it "on"
    monkeypatch.setattr(clock, "_now_override", clock.now() + timedelta(days=24))
    line = clio_hooks.renew(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert "holds no key" in line and clio_hooks.state_of(firm["data"]) == "unconfirmed" and clio_hooks.needs(firm["data"]) == sentence
    assert clio_hooks.view(firm["data"], ENV)["line"] == sentence and "Subscribed" not in clio_hooks.view(firm["data"], ENV)["line"]
    # every delivery gets the uniform refusal, signed with whatever secret
    doc = next(h for h in fake.hooks.values() if h["model"] == "document")
    body, sig = fake.deliver("document", "created", 9990, matter=101)
    assert clio_hooks.receive(firm["data"], body, sig, None) == ("refuse", {}) and not clio_hooks.waiting(firm["data"])
    planted = sign(body, f"planted-for-{doc['id']}")
    assert clio_hooks.receive(firm["data"], body, planted, None) == ("refuse", {})
    # the register's check sees it too
    import maintenance

    monkeypatch.setattr(clio, "DATA", firm["data"])
    item = next(i for i in maintenance.registry()["items"] if i["id"] == "clio_webhooks")
    assert maintenance._local_finding(item, clock.today()) == sentence
    # Stop, then Subscribe again, is what the sentence says, and it works
    fake.api = real_api
    clio_hooks.stop(firm["data"], "Andrea Attorney", firm["transport"], ENV, firm["pace"]())
    assert subscribe(firm)["state"] == "on"


def test_a_delivery_whose_ledger_row_failed_is_processed_when_clio_sends_it_again(firm, fake, monkeypatch):
    linked(firm)
    subscribe(firm)
    body, sig = fake.deliver("document", "created", 9991, matter=101)
    before = len(ledger())
    real = clio_hooks._ledger

    def fails(*a, **k):
        raise OSError("the ledger could not be written")

    monkeypatch.setattr(clio_hooks, "_ledger", fails)
    assert clio_hooks.receive(firm["data"], body, sig, None) == ("refuse", {})  # Clio is told to send it again
    monkeypatch.setattr(clio_hooks, "_ledger", real)
    assert not clio_hooks.seen_has(firm["data"], hashlib.sha256(body).hexdigest())  # not remembered as taken
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and len(ledger()) == before + 1  # the retry is processed, with its row
    assert clio_hooks.receive(firm["data"], body, sig, None)[0] == "event" and len(ledger()) == before + 1  # and now it is a replay: nothing more


def test_the_review_apps_digest_memory_is_pruned_on_the_same_rule_while_it_runs(firm):
    linked(firm)
    key = str(clio_hooks._seen_path(firm["data"]))
    now = clio_hooks.time.time()
    clio_hooks.seen_add(firm["data"], "a" * 64, now - 40 * 86400)  # a month and more ago (as the file held it when the app started)
    clio_hooks._seen_pruned[key] = now  # the cache was last pruned just now
    clio_hooks.seen_add(firm["data"], "b" * 64, now - 86400)
    assert "a" * 64 in clio_hooks._seen_cache[key]
    clio_hooks.seen_add(firm["data"], "c" * 64, now + clio_hooks.SEEN_PRUNE_EVERY + 1)  # an hour on: the cache forgets what the file will lose tonight
    known = clio_hooks._seen_cache[key]
    assert "a" * 64 not in known and "b" * 64 in known and "c" * 64 in known
    assert clio_hooks.seen_has(firm["data"], "c" * 64) and not clio_hooks.seen_has(firm["data"], "a" * 64)


def test_a_planted_handshake_for_an_enabled_keyless_subscription_becomes_no_secret(firm, fake):
    """The verifier's probe: a flood pushed Clio's candidate out, so the subscription is enabled in Clio and keyless here; then one more planted handshake."""
    linked(firm)
    fake.confirm = False
    subscribe(firm)  # the window is open and Clio's handshake has not come (or was pushed out)
    for h in fake.hooks.values():
        h["status"] = "enabled"  # Clio enabled them, having had its handshake answered
    view = clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert view["state"] == "unconfirmed" and not clio.Vault(firm["data"], ENV).read().get("webhook_secrets")
    doc = next(h for h in fake.hooks.values() if h["model"] == "document")
    # (a) Clio sends its handshake once, while the subscription is pending: one for an id already recorded enabled is refused
    planted = json.dumps({"data": {"webhook_id": doc["id"]}}).encode()
    assert clio_hooks.receive(firm["data"], planted, None, "planted-after-clio-enabled-it")[0] == "refuse"
    assert not clio.Vault(firm["data"], ENV).read().get("webhook_candidates")
    # (b) even a lone candidate that gets in (our record of the subscription was stale) is checked with Clio before it is kept
    clio_hooks._update(firm["data"], lambda d: d["hooks"][str(doc["id"])].update(status="pending"))
    assert clio_hooks.receive(firm["data"], planted, None, "planted-after-clio-enabled-it")[0] == "handshake"
    puts = len([r for r in fake.requests if r[0] == "PUT"])
    view = clio_hooks.check(firm["data"], firm["transport"], ENV, firm["pace"]())
    assert len([r for r in fake.requests if r[0] == "PUT"]) == puts + 1  # offered to Clio's activate call, which said no
    assert view["state"] == "unconfirmed" and not clio.Vault(firm["data"], ENV).read().get("webhook_secrets")
    # a forged event signed with the planted secret is refused and nothing is written
    forged = json.dumps({"data": {"id": 79, "etag": "s", "matter": {"id": 101}}, "meta": {"event": "created", "webhook_id": doc["id"]}}).encode()
    snapshot = ((firm["data"] / "clio" / "webhook.json").read_bytes(), len(ledger()))
    assert clio_hooks.receive(firm["data"], forged, sign(forged, "planted-after-clio-enabled-it"), None) == ("refuse", {})
    assert ((firm["data"] / "clio" / "webhook.json").read_bytes(), len(ledger())) == snapshot and not clio_hooks.waiting(firm["data"])


def test_clio_takes_its_locks_from_the_one_lock_module():
    """The connector's FileLock is src/oslock.py's lock: one lock module in the product (no fcntl or msvcrt call of its own)."""
    source = (REPO / "src" / "connectors" / "clio.py").read_text(encoding="utf-8")
    assert "import oslock" in source and not re.search(r"import (fcntl|msvcrt)|fcntl\.flock\(|msvcrt\.locking\(", source)
    assert "oslock.try_lock(fd)" in source and "oslock.unlock(fd)" in source
