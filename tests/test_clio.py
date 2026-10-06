"""The Clio connector (src/connectors/clio.py) against a simulated Clio: an httpx MockTransport plays Clio's OAuth
endpoints, the API v4 calls the connector makes (as Clio's own reference documents them) and the signed storage
links documents go up to and come down from. No test reaches Clio; no real account or person is used: the firm's
Clio user, its attorney and the clients are made up ("Ana Clara Exemplo Souza", "Maria Exemplo")."""

import http.client
import json
import threading
from pathlib import Path
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest

import clock
import documents
import maintenance
from connectors import clio, sync
from review.auth import COOKIE, Accounts
from review.server import SIGN_IN_COOKIE, ReviewApp, make_handler, serve
import schema_path

REPO = Path(__file__).resolve().parent.parent
REDIRECT = "https://review.firm.example/auth/callback"
ENV: dict[str, str] = {}  # nothing from this machine's environment: the vault key is a file in the test's folder
SETUP = {"on": True, "client_id": "madeUpClientId123", "client_secret": "madeUpSecret456", "redirect_uri": REDIRECT,
         "practice_areas": {"7": {"name": "Immigration", "questionnaire": "i485", "office": ""},
                            "8": {"name": "Family law", "questionnaire": ""}},
         "attorney_offices": {"31": {"name": "Andrea Exemplo", "office": "main"}}, "language_field": "55"}


def _pdf(text: str) -> bytes:
    return clio.mailing_pdf("Test", {"title": text, "mailed_on": "2026-10-01"})


class FakeClio:
    """Clio as its documentation describes it: OAuth, the v4 index/show/create/update calls, 303 downloads, signed PUTs."""

    def __init__(self):
        self.requests: list[tuple[str, str]] = []
        self.access, self.refresh, self.issued = "access-1", "refresh-1", 1
        self.refuse_refresh = False
        self.throttle: list[int] = []  # Retry-After seconds for the next API calls
        self.refuse_matter: int | None = None  # uploads onto this matter get a 403 (the user can't write there)
        self.matters = [{"id": 101, "display_number": "00101-Souza", "status": "Open", "client": {
                            "id": 501, "name": "Ana Clara Exemplo Souza", "first_name": "Ana Clara", "last_name": "Exemplo Souza",
                            "primary_email_address": "ana.exemplo@example.com", "primary_phone_number": "+1 617 555 0100"},
                         "practice_area": {"id": 7, "name": "Immigration"}, "responsible_attorney": {"id": 31, "name": "Andrea Exemplo"}},
                        {"id": 102, "display_number": "00102-Exemplo", "status": "Pending", "client": {
                            "id": 502, "name": "Maria Exemplo", "primary_email_address": None, "primary_phone_number": "+1 305 555 0101"},
                         "practice_area": {"id": 7, "name": "Immigration"}, "responsible_attorney": {"id": 32, "name": "Other Lawyer"}}]
        self.languages = {501: "Kreyòl", 502: None}
        self.docs = {101: [{"id": 9001, "name": "Passport", "filename": "Passport.pdf", "content_type": "application/pdf", "size": 10,
                            "updated_at": "2026-09-01T10:00:00-04:00", "latest_document_version": {"id": 1}, "external_properties": []}],
                     102: []}
        self.content = {9001: _pdf("passport")}
        self.created: dict[int, dict] = {}  # documents we were sent: id -> {meta, parent, versions [bytes], fully_uploaded}
        self.uploads: dict[str, int] = {}  # uuid -> document id
        self.notes: list[dict] = []
        self.entries: dict[str, dict] = {}
        self.next_id = 7000

    # -- helpers -------------------------------------------------------------------
    def _id(self) -> int:
        self.next_id += 1
        return self.next_id

    def _json(self, status, body, headers=None):
        return httpx.Response(status, json=body, headers={"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "49", **(headers or {})})

    def handle(self, request: httpx.Request) -> httpx.Response:
        url = request.url
        self.requests.append((request.method, str(url)))
        if url.host == "storage.example":  # the signed links: never Clio's token
            assert "authorization" not in {k.lower() for k in request.headers}
            if request.method == "GET":
                return httpx.Response(200, content=self.content[int(url.path.rsplit("/", 1)[1])])
            assert request.method == "PUT" and request.headers["x-amz-server-side-encryption"] == "AES256"
            doc = self.created[self.uploads[url.path.rsplit("/", 1)[1]]]
            doc["versions"].append(request.content)
            return httpx.Response(200)
        assert url.host == "app.clio.com", url
        if url.path == "/oauth/token":
            form = {k: v[0] for k, v in parse_qs(request.content.decode()).items()}
            assert form["client_id"] == SETUP["client_id"] and form["client_secret"] == SETUP["client_secret"]
            if form["grant_type"] == "authorization_code":
                assert form["redirect_uri"] == REDIRECT
                if form["code"] != "good-code":
                    return httpx.Response(400, json={"error": "invalid_grant"})
                return httpx.Response(200, json={"token_type": "bearer", "access_token": self.access, "expires_in": 2592000, "refresh_token": self.refresh})
            assert form["grant_type"] == "refresh_token" and form["refresh_token"] == self.refresh
            if self.refuse_refresh:
                return httpx.Response(400, json={"error": "invalid_grant"})
            self.issued += 1
            self.access = f"access-{self.issued}"
            return httpx.Response(200, json={"token_type": "bearer", "access_token": self.access, "expires_in": 2592000})  # no new refresh token
        if url.path == "/oauth/deauthorize":
            return httpx.Response(200)
        assert url.path.startswith("/api/v4/") and request.headers["X-API-VERSION"] == clio.API_VERSION
        if request.headers.get("Authorization") != f"Bearer {self.access}":
            return httpx.Response(401, json={"error": {"type": "UnauthorizedError", "message": "expired"}})
        if self.throttle:
            return httpx.Response(429, headers={"Retry-After": str(self.throttle.pop(0))})
        return self.api(request.method, url.path[len("/api/v4"):], {k: v for k, v in url.params.items()}, request)

    def api(self, method, path, params, request):
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("GET", "/users/who_am_i.json"):
            return self._json(200, {"data": {"id": 31, "name": "Andrea Exemplo", "email": "andrea@firm.example", "default_calendar_id": 900}})
        if (method, path) == ("GET", "/practice_areas.json"):
            return self._json(200, {"data": [{"id": 7, "name": "Immigration"}, {"id": 8, "name": "Family law"}], "meta": {"paging": {}}})
        if (method, path) == ("GET", "/users.json"):
            return self._json(200, {"data": [{"id": 31, "name": "Andrea Exemplo", "enabled": True, "subscription_type": "Attorney"}]})
        if (method, path) == ("GET", "/custom_fields.json"):
            assert params["parent_type"] == "contact"
            return self._json(200, {"data": [{"id": 55, "name": "Language", "field_type": "text_line"}]})
        if (method, path) == ("GET", "/calendars.json"):
            return self._json(200, {"data": [{"id": 900, "name": "Firm calendar", "type": "AccountCalendar"}]})
        if (method, path) == ("GET", "/matters.json"):
            assert params["order"] == "id(asc)" and params["status"] == "open,pending" and "display_number" in params["fields"]
            rows = [m for m in self.matters if str(m["practice_area"]["id"]) == params["practice_area_id"]]
            start = int(params.get("page_token") or 0)  # one matter a page: the cursor is followed
            nxt = f"https://app.clio.com/api/v4/matters.json?{urlencode({**params, 'page_token': start + 1})}" if start + 1 < len(rows) else None
            return self._json(200, {"data": rows[start:start + 1], "meta": {"paging": {"next": nxt} if nxt else {}}})
        if method == "GET" and path.startswith("/contacts/"):
            cid = int(path.split("/")[2].split(".")[0])
            assert params["custom_field_ids[]"] == "55"
            value = self.languages.get(cid)
            return self._json(200, {"data": {"id": cid, "custom_field_values": [{"id": "text_line-55", "value": value, "field_name": "Language"}] if value else []}})
        if (method, path) == ("GET", "/documents.json"):
            mid = int(params["matter_id"])
            listed = self.docs.get(mid, []) + [d["meta"] | {"id": i} for i, d in self.created.items() if d["matter"] == mid]
            return self._json(200, {"data": listed})
        if method == "GET" and path.endswith("/download.json"):
            return httpx.Response(303, headers={"Location": f"https://storage.example/dl/{path.split('/')[2]}"})
        if (method, path) == ("POST", "/documents.json"):
            data = body["data"]
            parent = data["parent"]
            if parent == {"id": self.refuse_matter, "type": "Matter"}:
                return self._json(403, {"error": {"type": "ForbiddenError", "message": "User is forbidden from taking that action"}})
            if parent["type"] == "Document":  # a new version of a document we made
                doc_id = parent["id"]
                assert doc_id in self.created
            else:
                assert parent["type"] == "Matter" and data["external_properties"][0]["name"] == clio.EXTERNAL
                doc_id = self._id()
                self.created[doc_id] = {"meta": {"name": data["name"], "external_properties": data["external_properties"]}, "matter": parent["id"],
                                        "versions": [], "fully_uploaded": 0}
            uuid = f"uuid-{self._id()}"
            self.uploads[uuid] = doc_id
            return self._json(201, {"data": {"id": doc_id, "latest_document_version": {
                "uuid": uuid, "put_url": f"https://storage.example/put/{uuid}",
                "put_headers": [{"name": "x-amz-server-side-encryption", "value": "AES256"}, {"name": "Content-Type", "value": "application/pdf"}]}}})
        if method == "PATCH" and path.startswith("/documents/"):
            doc = self.created[int(path.split("/")[2].split(".")[0])]
            assert body["data"]["fully_uploaded"] is True and self.uploads[body["data"]["uuid"]]
            doc["fully_uploaded"] += 1
            return self._json(200, {"data": {"id": 1}})
        if (method, path) == ("POST", "/notes.json"):
            assert body["data"]["type"] == "Matter"
            self.notes.append(body["data"])
            return self._json(201, {"data": {"id": len(self.notes)}})
        if (method, path) == ("POST", "/calendar_entries.json"):
            eid = str(self._id())
            self.entries[eid] = dict(body["data"])
            return self._json(201, {"data": {"id": eid}})
        if method == "PATCH" and path.startswith("/calendar_entries/"):
            eid = path.split("/")[2].split(".")[0]
            self.entries[eid] |= body["data"]
            return self._json(200, {"data": {"id": eid}})
        return httpx.Response(404, json={"error": {"type": "NotFound", "message": path}})


class Quiet:
    """The Pace queue's clock: no real waiting; what it would have slept is kept."""

    def __init__(self):
        self.t, self.wall_t, self.slept = 1000.0, 1_790_000_000.0, []

    def mono(self):
        return self.t

    def wall(self):
        return self.wall_t

    def sleep(self, s):
        self.slept.append(round(s, 2))
        self.t += s
        self.wall_t += s


@pytest.fixture
def fake():
    return FakeClio()


@pytest.fixture
def firm(tmp_path, fake):
    """A firm with Clio set up and connected: data/, clients/, the portal; the simulated Clio behind every call."""
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    clio.save_settings(data, SETUP, "Andrea Attorney", env=ENV)
    clio.connect(data, "good-code", "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    quiet = Quiet()
    return {"data": data, "clients": tmp_path / "clients", "out": data / "clients", "portal": data / "portal", "fake": fake, "quiet": quiet,
            "run": lambda direction="both": clio.run(data, tmp_path / "clients", data / "clients", data / "portal", direction,
                                                     transport=httpx.MockTransport(fake.handle), env=ENV,
                                                     pace=clio.Pace(monotonic=quiet.mono, sleep=quiet.sleep, wall=quiet.wall))}


def _client(firm) -> clio.Clio:
    q = firm["quiet"]
    return clio.Clio(firm["data"], transport=httpx.MockTransport(firm["fake"].handle), env=ENV, pace=clio.Pace(monotonic=q.mono, sleep=q.sleep, wall=q.wall))


# --- OAuth through the review app's /auth routes ---------------------------------------------------------------------


@pytest.fixture
def review(tmp_path, fake):
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    accounts = Accounts(tmp_path / "staff.json")
    accounts.add("andrea@firm.example", "Andrea Attorney", "attorney")
    accounts.add("paulo@firm.example", "Paulo Paralegal", "paralegal")
    app = ReviewApp(data / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts)
    app.clio_transport = httpx.MockTransport(fake.handle)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    yield {"port": port, "app": app, "data": data, "attorney": cookie("andrea@firm.example"), "paralegal": cookie("paulo@firm.example")}
    httpd.shutdown()


def _http(port, method, path, cookie=None, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=20)
    headers = {"Cookie": cookie} if cookie else {}
    if body is not None:
        headers |= {"Content-Type": "application/json", "X-Review-App": "1"}
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = conn.getresponse()
    out = r.status, {k.lower(): v for k, v in r.getheaders() if k.lower() != "set-cookie"} | {"set-cookie": [v for k, v in r.getheaders() if k.lower() == "set-cookie"]}, r.read()
    conn.close()
    return out


def test_connect_to_clio_through_the_review_apps_auth_routes(review, fake):
    p, data = review["port"], review["data"]
    # not set up yet: back to Settings with the reason
    status, h, _ = _http(p, "GET", "/auth/start?provider=clio", review["attorney"])
    assert status == 302 and h["location"] == "/?clio=notready#settings:connections"
    # the attorney saves the app's details (the secret goes into the vault, never back to the page)
    status, _, body = _http(p, "POST", "/api/connections", review["attorney"], {"action": "save", "values": {k: SETUP[k] for k in ("on", "client_id", "client_secret", "redirect_uri")}})
    view = json.loads(body)["clio"]
    assert status == 200 and view["secret_saved"] and SETUP["client_secret"] not in body.decode()
    assert SETUP["client_secret"].encode() not in (data / "clio" / "secrets.enc").read_bytes()  # encrypted at rest
    # a paralegal can't connect, see or change it
    assert _http(p, "GET", "/auth/start?provider=clio", review["paralegal"])[0] == 403
    assert _http(p, "GET", "/api/connections", review["paralegal"])[0] == 403
    assert _http(p, "POST", "/api/connections", review["paralegal"], {"action": "save", "values": {"on": False}})[0] == 403
    assert "connections" not in [s["id"] for s in json.loads(_http(p, "GET", "/api/settings", review["paralegal"])[2])["sections"]]
    assert "connections" in [s["id"] for s in json.loads(_http(p, "GET", "/api/settings", review["attorney"])[2])["sections"]]
    # "Connect to Clio": Clio's consent screen with the registered address (never the Host header) and a one-use state in a cookie
    status, h, _ = _http(p, "GET", "/auth/start?provider=clio", review["attorney"])
    where = urlparse(h["location"])
    q = {k: v[0] for k, v in parse_qs(where.query).items()}
    assert status == 302 and where.netloc == "app.clio.com" and where.path == "/oauth/authorize"
    assert q == {"response_type": "code", "client_id": SETUP["client_id"], "redirect_uri": REDIRECT, "state": q["state"], "redirect_on_decline": "true"}
    [state_cookie] = h["set-cookie"]
    assert state_cookie.startswith(f"{SIGN_IN_COOKIE}={q['state']};") and "HttpOnly" in state_cookie
    browser = state_cookie.split(";")[0]
    # a callback from another browser (a link someone planted) connects nothing
    status, h, _ = _http(p, "GET", "/auth/callback?" + urlencode({"state": q["state"], "code": "good-code"}))
    assert h["location"] == "/?clio=expired#settings:connections" and not clio.state(data).get("connected")
    # the attorney's own browser comes back with the code: tokens in the vault, whose connection recorded
    status, h, _ = _http(p, "GET", "/auth/start?provider=clio", review["attorney"])
    st = parse_qs(urlparse(h["location"]).query)["state"][0]
    browser = h["set-cookie"][0].split(";")[0]
    status, h, _ = _http(p, "GET", "/auth/callback?" + urlencode({"state": st, "code": "good-code"}), f"{review['attorney']}; {browser}")
    assert status == 302 and h["location"] == "/?clio=connected#settings:connections"
    connected = clio.state(data)["connected"]
    assert connected["by"] == "Andrea Attorney" and connected["user"]["name"] == "Andrea Exemplo" and connected["user"]["default_calendar_id"] == 900
    tokens = clio.Vault(data).read()
    assert tokens["access_token"] == "access-1" and tokens["refresh_token"] == "refresh-1" and b"refresh-1" not in (data / "clio" / "secrets.enc").read_bytes()
    # used once: the same state again connects nothing (no longer known as Clio's, it lands on the front page); a declined grant says so
    assert _http(p, "GET", "/auth/callback?" + urlencode({"state": st, "code": "good-code"}), browser)[1]["location"] == "/?signin=expired"
    assert fake.issued == 1
    status, h, _ = _http(p, "GET", "/auth/start?provider=clio", review["attorney"])
    st, browser = parse_qs(urlparse(h["location"]).query)["state"][0], h["set-cookie"][0].split(";")[0]
    assert _http(p, "GET", "/auth/callback?" + urlencode({"state": st, "error": "access_denied"}), browser)[1]["location"] == "/?clio=declined#settings:connections"
    msg = json.loads(_http(p, "GET", "/api/connections?clio=declined", review["attorney"])[2])["message"]
    assert "declined" in msg and "--" not in msg and "—" not in msg
    # the Test button: read-only, says whose connection and what the mapping reaches
    test = json.loads(_http(p, "POST", "/api/connections", review["attorney"], {"action": "test"})[2])["test"]
    assert test["ok"] and test["lines"][0] == "Connected to Clio as Andrea Exemplo." and "No practice area" in test["lines"][1]
    # Clio's lists for the mapping, then the mapping saved
    view = json.loads(_http(p, "POST", "/api/connections", review["attorney"], {"action": "options"})[2])["clio"]
    assert [a["name"] for a in view["options"]["practice_areas"]] == ["Immigration", "Family law"] and view["options"]["calendars"][0]["id"] == "900"
    assert not any(m == "DELETE" for m, _ in fake.requests)


# --- the token -----------------------------------------------------------------------------------------------------


def test_the_token_is_renewed_before_it_runs_out_and_a_refusal_reaches_keeping_current(firm, fake):
    data = firm["data"]
    c = _client(firm)
    assert c.token() == "access-1"  # 30 days left: used as it is
    clio.Vault(data, ENV).update(expires_at=clock.utcnow().timestamp() + 3600)  # an hour left: renewed first
    assert c.call("GET", "/users/who_am_i.json").json()["data"]["name"] == "Andrea Exemplo"
    assert fake.issued == 2 and clio.Vault(data, ENV).read()["access_token"] == "access-2"
    assert clio.Vault(data, ENV).read()["refresh_token"] == "refresh-1"  # Clio's refresh answer has none: the one held is kept
    # Clio says the token expired early (401): renewed once and the call repeated
    fake.access = "rotated-elsewhere"
    fake.issued = 2
    assert c.call("GET", "/users/who_am_i.json").status_code == 200 and fake.issued == 3
    # Clio refuses to renew: the sync stops and says so; Keeping current asks an attorney to connect again
    fake.refuse_refresh, fake.access = True, "gone"
    line = firm["run"]()
    assert "Clio refused to renew the connection" in line and "Connections" in line
    assert clio.state(data)["refresh_failed"]["at"]
    text, needs = clio.state_text(data, ENV)
    assert needs and "the connection stopped" in text and "connects it again" in text
    assert clio.view(data, ENV)["refresh_failed"]
    # connecting again clears it
    fake.refuse_refresh, fake.access = False, "access-9"
    clio.connect(data, "good-code", "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    assert not clio.state(data).get("refresh_failed") and not clio.state_text(data, ENV)[1]


# --- matters -> cases and portal clients; documents in -------------------------------------------------------------------


def test_matters_become_cases_and_portal_clients_and_their_documents_come_in(firm, fake):
    line = firm["run"]("in")
    assert line.startswith("Clio: 2 matter(s) read, 2 new client(s), 1 document(s) copied in"), line
    ana = sync.local_id(sync.RemoteClient("101", "Ana Clara Exemplo Souza"), "clio")
    assert ana == "ana_clara_exemplo_souza-cl101" and documents.source_of(ana) == "clio"
    files = list((firm["clients"] / ana / "source").glob("*.pdf"))
    assert [f.name for f in files] == ["Passport [9001].pdf"] and files[0].read_bytes() == fake.content[9001]
    assert (firm["clients"] / "maria_exemplo-cl102" / "source").is_dir()  # the case folder exists before any document
    from portal.store import PortalStore

    store = PortalStore(firm["portal"])
    p = store.profile(ana)
    assert (p["name"], p["phone"], p["email"]) == ("Ana Clara Exemplo Souza", "+1 617 555 0100", "ana.exemplo@example.com")
    assert p["language"] == "ht" and p["filing"] == "i485" and p["office"] == "main" and p["source"] == "clio" and p["clio_matter"] == "101"
    assert p["consent"] == {"email": False, "sms": False, "whatsapp": False}  # Clio holds no consent to message the client
    m = store.profile("maria_exemplo-cl102")
    assert m["language"] == "pt" and "office" not in m and m["email"] == ""  # no attorney mapping for her matter's attorney
    assert clio.state(firm["data"])["matters"]["101"] == {"case": ana, "name": "Ana Clara Exemplo Souza", "number": "00101-Souza",
                                                          "questionnaire": "i485", "office": "main"}
    # the office changes the questionnaire: a later sync leaves it; nothing new in Clio: nothing downloaded
    store.update_profile(ana, filing="n400")
    before = sum(1 for _, u in fake.requests if "storage.example/dl" in u)
    assert "0 document(s) copied in" in firm["run"]("in")
    assert sum(1 for _, u in fake.requests if "storage.example/dl" in u) == before and store.profile(ana)["filing"] == "n400"
    # a new version in Clio replaces the copy; a document removed in Clio is reported, the copy kept
    fake.docs[101][0]["latest_document_version"] = {"id": 2}
    fake.content[9001] = _pdf("passport v2")
    report = clio.sync_in(firm["data"], firm["clients"], firm["portal"], _client(firm))["report"]
    assert report["clients"][ana]["updated"] == ["Passport [9001].pdf"] and files[0].read_bytes() == fake.content[9001]
    fake.docs[101] = []
    report = clio.sync_in(firm["data"], firm["clients"], firm["portal"], _client(firm))["report"]
    assert report["clients"][ana]["gone"] == ["Passport [9001].pdf"] and files[0].exists()
    # matters outside the mapped practice areas never come in
    assert all(params.get("practice_area_id", ["7"]) == ["7"] for params in (parse_qs(urlparse(u).query) for m, u in fake.requests if "/matters.json" in u))


def test_a_matter_in_an_asylum_practice_area_is_restricted_from_the_start_and_never_invited(firm, fake, monkeypatch):
    """docs/research/buyer_walkthrough_4.md, finding 1: a matter of a protected kind came in as an ordinary portal client. Now its case
    folder holds the restriction before the portal client is made; nothing is sent to the client; the sync's line says so."""
    import restricted
    from portal.notify import Notifier
    from portal.store import PortalStore

    clio.save_settings(firm["data"], {"practice_areas": SETUP["practice_areas"] | {"8": {"name": "Asylum", "questionnaire": "i485", "office": ""}}},
                       "Andrea Attorney", env=ENV)
    fake.matters.append({"id": 103, "display_number": "00103-Asilo", "status": "Open", "client": {
        "id": 503, "name": "Rosa Exemplo Asilo", "primary_email_address": "rosa.asilo@example.com", "primary_phone_number": "+1 617 555 0103"},
        "practice_area": {"id": 8, "name": "Asylum"}, "responsible_attorney": {"id": 31, "name": "Andrea Exemplo"}})
    fake.docs[103] = []
    line = firm["run"]("in")
    assert "1 protected case(s) restricted from the start and not invited" in line, line
    rosa = sync.local_id(sync.RemoteClient("103", "Rosa Exemplo Asilo"), "clio")
    rec = restricted.record(firm["out"] / rosa)
    assert rec["marked"]["law"] == "208.6" and rec["marked"]["reason"] == ("Came in from Clio, practice area Asylum: the law keeps an asylum case "
                                                                            "(8 CFR 208.6) confidential from the start.")
    assert not (firm["out"] / "ana_clara_exemplo_souza-cl101" / "access.json").exists()  # an ordinary immigration matter: as before
    # the restriction record is written before the conflict check (src/connectors/clio.py sync_in): no moment holds the check without it
    rec = firm["out"] / rosa
    assert (rec / "access.json").stat().st_mtime_ns <= (rec / "conflict_check.json").stat().st_mtime_ns
    store = PortalStore(firm["portal"])
    store.update_profile(rosa, consent={"email": True, "sms": True, "whatsapp": False})  # even with consent recorded
    sent = Notifier(firm["portal"] / "outbox.jsonl", env={}, cases_root=firm["out"]).send(store.profile(rosa), "invite", "x")
    assert sent == [{"channel": "all", "result": "skipped", "why": "restricted case"}]
    assert "protected" not in firm["run"]("in")  # marked once: the next sync finds the record and leaves it as it is


# --- results out --------------------------------------------------------------------------------------------------------


def _processed(firm, case: str, filings=None, draft=False) -> Path:
    """A case the pipeline processed: its bundle, a built packet, its review bundle, and the mailing records."""
    out = firm["out"] / case
    out.mkdir(parents=True, exist_ok=True)
    (out / "fact_graph.json").write_text("{}", encoding="utf-8")
    (out / "packet.pdf").write_bytes(_pdf("packet one"))
    (out / "packet.json").write_text(json.dumps({"built_at": "2026-10-01T10:00:00-04:00", "draft": draft, "filing": "i485"}), encoding="utf-8")
    (out / "packet_review_bundle.pdf").write_bytes(_pdf("bundle one"))
    (out / "status.json").write_text(json.dumps({"filings": filings or []}), encoding="utf-8")
    return out


DEADLINES = [{"id": "rfe.IOE0912345678", "date": "2026-11-20", "what": "Answer the request for evidence on the I-485", "owner": "attorney", "source": None},
             {"id": "ead", "date": "2027-03-01", "what": "Work permit expires (renewal can be filed since 09/02/2026)", "owner": "paralegal", "source": None}]


@pytest.fixture
def view(monkeypatch):
    """What src/journey.py says of each case: the stage, the deadlines, which a person marked done."""
    v = {"stage": "Green card application filed", "why": "Receipt notice IOE0912345678", "deadlines": [dict(d) for d in DEADLINES], "done": {}}
    monkeypatch.setattr(clio, "case_view", lambda out_dir: json.loads(json.dumps(v)))
    return v


def test_the_packet_the_review_bundle_and_the_mailing_record_go_to_the_matter(firm, fake, view):
    firm["run"]("in")
    ana = "ana_clara_exemplo_souza-cl101"
    mailed = {"filing": "i485", "title": "SIJ green card (I-485)", "mailed_on": "2026-10-01", "carrier": "USPS Priority Mail", "tracking": "9400100000000000000000",
              "mail_to": "USCIS, Attn: I-485, P.O. Box 660867, Dallas, TX 75266", "fee": "$1,440", "by": "Paulo Paralegal", "at": "2026-10-01T16:00:00-04:00"}
    out = _processed(firm, ana, filings=[mailed])
    line = firm["run"]("out")
    assert "3 document(s) and 1 note(s) sent" in line, line
    made = {d["meta"]["name"]: d for d in fake.created.values()}
    assert set(made) == {"Filing packet - " + clio._title("i485") + ".pdf", "Review bundle - " + clio._title("i485") + ".pdf",
                         "Mailing record - SIJ green card (I-485) - 10-01-2026.pdf"}
    packet = made["Filing packet - " + clio._title("i485") + ".pdf"]
    assert packet["matter"] == 101 and packet["versions"] == [(out / "packet.pdf").read_bytes()] and packet["fully_uploaded"] == 1
    record = made["Mailing record - SIJ green card (I-485) - 10-01-2026.pdf"]["versions"][0]
    from pypdf import PdfReader
    import io

    text = PdfReader(io.BytesIO(record)).pages[0].extract_text()
    assert "Mailing record" in text and "10/01/2026" in text and "9400100000000000000000" in text and "Paulo Paralegal" in text
    assert fake.notes == [{"type": "Matter", "matter": {"id": 101}, "subject": "Case stage: Green card application filed",
                           "detail": fake.notes[0]["detail"], "date": clock.today().isoformat()}]
    assert "Receipt notice IOE0912345678" in fake.notes[0]["detail"]
    # what we put in Clio never comes back in as one of the client's documents
    firm["run"]("in")
    assert sorted(p.name for p in (firm["clients"] / ana / "source").glob("*.pdf")) == ["Passport [9001].pdf"]
    # nothing changed: nothing sent again
    assert "0 document(s) and 0 note(s) sent" in firm["run"]("out")
    # the packet rebuilt: a new version of the same Clio document, never a second document, never a deletion
    (out / "packet.pdf").write_bytes(_pdf("packet two"))
    assert "1 document(s)" in firm["run"]("out")
    assert len(fake.created) == 3 and packet["versions"][-1] == (out / "packet.pdf").read_bytes() and len(packet["versions"]) == 2
    # the stage moves: one more note
    view["stage"] = "Approved"
    firm["run"]("out")
    assert [n["subject"] for n in fake.notes] == ["Case stage: Green card application filed", "Case stage: Approved"]
    assert not any(m == "DELETE" for m, _ in fake.requests)


def test_a_draft_packet_stays_here(firm, fake, view):
    firm["run"]("in")
    out = _processed(firm, "ana_clara_exemplo_souza-cl101", draft=True)
    firm["run"]("out")
    assert not fake.created and (out / "packet.pdf").exists()


def test_deadlines_become_calendar_entries_moved_and_closed_never_deleted(firm, fake, view):
    firm["run"]("in")
    _processed(firm, "ana_clara_exemplo_souza-cl101")
    line = firm["run"]("out")
    assert "deadlines 2 added, 0 moved, 0 closed" in line, line
    by = {e["external_properties"][0]["value"]: (eid, e) for eid, e in fake.entries.items()}
    rfe_id, rfe = by["ana_clara_exemplo_souza-cl101:rfe.IOE0912345678"]
    assert rfe["summary"] == "Answer the request for evidence on the I-485" and rfe["all_day"] is True
    assert rfe["matter"] == {"id": 101} and rfe["calendar_owner"] == {"id": 900}  # the connected user's default calendar
    assert rfe["start_at"].startswith("2026-11-20T00:00:00-05:00") and rfe["end_at"].startswith("2026-11-20T23:59:00-05:00")  # the firm's day (Eastern)
    assert rfe["external_properties"] == [{"name": clio.EXTERNAL, "value": "ana_clara_exemplo_souza-cl101:rfe.IOE0912345678"}]
    # nothing changed: nothing sent
    assert "deadlines 0 added, 0 moved, 0 closed" in firm["run"]("out")
    # the date moves; the work permit's deadline is marked done
    view["deadlines"][0]["date"] = "2026-12-01"
    view["done"]["ead"] = {"by": "Paulo Paralegal", "at": "2026-10-02T09:00:00-04:00"}
    assert "deadlines 0 added, 1 moved, 1 closed" in firm["run"]("out")
    assert fake.entries[rfe_id]["start_at"].startswith("2026-12-01T00:00:00-05:00")
    ead_id, _ = by["ana_clara_exemplo_souza-cl101:ead"]
    assert fake.entries[ead_id]["summary"].startswith("Done: Work permit expires") and "Paulo Paralegal" in fake.entries[ead_id]["description"]
    # the request is answered: the deadline leaves the case's list, and the entry says so
    view["deadlines"] = []
    assert "0 moved, 1 closed" in firm["run"]("out")
    assert fake.entries[rfe_id]["summary"].startswith("No longer due: Answer the request for evidence")
    # the done mark taken back: the entry opens again with its date
    view["deadlines"] = [dict(DEADLINES[1])]
    view["done"] = {}
    assert "1 moved" in firm["run"]("out") and fake.entries[ead_id]["summary"] == DEADLINES[1]["what"]
    # the Clio calendar the attorney chose wins over the user's own
    clio.save_settings(firm["data"], {"calendar": "901"}, "Andrea Attorney", env=ENV)
    view["deadlines"] = [{"id": "oath", "date": "2027-01-15", "what": "Citizenship oath ceremony", "owner": "client", "source": None}]
    firm["run"]("out")
    assert [e for e in fake.entries.values() if e["summary"] == "Citizenship oath ceremony"][0]["calendar_owner"] == {"id": 901}
    assert not any(m == "DELETE" for m, _ in fake.requests) and len(fake.entries) == 3


# --- the queue: Clio's limit -----------------------------------------------------------------------------------------


def test_the_queue_keeps_clios_limit_and_a_429_backs_off(firm, fake):
    q = Quiet()
    pace = clio.Pace(limit=50, monotonic=q.mono, sleep=q.sleep, wall=q.wall)
    for _ in range(50):
        pace.wait()
    assert q.slept == []
    pace.wait()  # the 51st within the minute waits for the first to leave the window
    assert q.slept == [60.0]
    # Clio says how many are left: none until the reset time, so the next one waits for it
    pace.saw(httpx.Headers({"X-RateLimit-Limit": "50", "X-RateLimit-Remaining": "0", "X-RateLimit-Reset": str(int(q.wall_t) + 12)}))
    pace.wait()
    assert q.slept[-1] == 12.0
    pace.saw(httpx.Headers({"X-RateLimit-Limit": "120"}))  # off-peak, a higher limit
    assert pace.limit == 120
    # over the limit anyway: Clio's Retry-After is waited out, then the call is made again
    fake.throttle = [7, 3]
    c = _client(firm)
    assert c.call("GET", "/users/who_am_i.json").status_code == 200
    assert firm["quiet"].slept[-2:] == [7.0, 3.0]
    fake.throttle = [1] * clio.TRIES
    with pytest.raises(clio.ClioError, match="slow down"):
        c.call("GET", "/users/who_am_i.json")
    # a whole sync records how long it waited for the limit
    fake.throttle = [30]
    assert "30 s waiting for Clio's limit" in firm["run"]("in")


# --- nothing deleted; protected cases ---------------------------------------------------------------------------------


def test_nothing_is_ever_deleted(firm, fake):
    c = _client(firm)
    with pytest.raises(clio.ClioError, match="never"):
        c.call("DELETE", "/documents/9001.json")
    clio.disconnect(firm["data"], "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    assert ("POST", "https://app.clio.com/oauth/deauthorize") in fake.requests and not any(m == "DELETE" for m, _ in fake.requests)
    kept = clio.Vault(firm["data"], ENV).read()
    assert kept == {"client_secret": SETUP["client_secret"]} and clio.settings(firm["data"])["practice_areas"]  # the setup stays
    assert clio.ready(firm["data"], ENV) == "not connected"


def test_a_protected_case_sends_nothing_until_the_attorney_allows_it(firm, fake, view):
    firm["run"]("in")
    ana = "ana_clara_exemplo_souza-cl101"
    out = _processed(firm, ana, filings=[{"filing": "vawa", "title": "VAWA self-petition", "mailed_on": "2026-09-30", "carrier": "USPS",
                                          "by": "Paulo Paralegal", "at": "2026-09-30T12:00:00-04:00"}])
    assert documents.case_confidentiality(out) == "1367"
    line = firm["run"]("out")
    assert "1 protected case(s) held back" in line and not fake.created and not fake.notes and not fake.entries
    held = clio.view(firm["data"], ENV)["held"]
    assert held == [{"case": ana, "name": "Ana Clara Exemplo Souza", "why": clio.PROTECTED["1367"]}]
    # documents still come in: only what goes out is held
    assert (firm["clients"] / ana / "source" / "Passport [9001].pdf").exists()
    with pytest.raises(ValueError):
        clio.allow(firm["data"], ana, "")
    clio.allow(firm["data"], ana, "Andrea Attorney")
    assert "held back" not in firm["run"]("out") and fake.created and fake.notes and fake.entries
    assert clio.view(firm["data"], ENV)["allowed"][0]["by"] == "Andrea Attorney"
    clio.allow(firm["data"], ana, "Andrea Attorney", allowed=False)  # taken back: held again from the next sync
    assert "1 protected case(s) held back" in firm["run"]("out")


def test_a_case_an_attorney_restricted_is_held_back_the_same_way(firm, fake, view):
    import restricted

    firm["run"]("in")
    ana = "ana_clara_exemplo_souza-cl101"
    out = _processed(firm, ana)
    assert documents.case_confidentiality(out) is None and "held back" not in firm["run"]("out")
    restricted.mark(out, True, "The client asked that only the attorney see this case.", "Andrea Attorney", "attorney")
    assert "1 protected case(s) held back" in firm["run"]("out")
    held = clio.view(firm["data"], ENV)["held"]
    assert held == [{"case": ana, "name": "Ana Clara Exemplo Souza", "why": "a case an attorney restricted"}]
    (out / "access.json").write_text("{not json", encoding="utf-8")  # a record that can't be read may carry the mark: still held
    assert clio.held_back(out, ana, {}) == "a case an attorney restricted"
    (out / "access.json").unlink()
    clio.allow(firm["data"], ana, "Andrea Attorney")
    assert "held back" not in firm["run"]("out")


# --- settings, the vault, Keeping current -------------------------------------------------------------------------


def test_the_settings_are_checked_and_the_secret_never_shown(firm):
    data = firm["data"]
    for bad in ({"redirect_uri": "http://review.firm.example/auth/callback"}, {"redirect_uri": "https://review.firm.example/elsewhere"},
                {"client_id": "x y"}, {"practice_areas": {"7": {"questionnaire": "i999"}}}, {"attorney_offices": {"31": {"office": "nowhere"}}},
                {"calendar": "the firm one"}, {"anything": 1}):
        with pytest.raises(ValueError):
            clio.save_settings(data, bad, "Andrea Attorney", env=ENV)
    with pytest.raises(ValueError, match="your name"):
        clio.save_settings(data, {"on": False}, " ", env=ENV)
    clio.save_settings(data, {"redirect_uri": "http://127.0.0.1:8485/auth/callback", "client_secret": ""}, "Andrea Attorney", env=ENV)  # this computer only
    assert clio.credentials(data, ENV)[1] == SETUP["client_secret"]  # a blank secret keeps the saved one
    v = clio.view(data, ENV)
    text = json.dumps(v)
    assert v["secret_saved"] and SETUP["client_secret"] not in text and "access-1" not in text and "refresh-1" not in text
    assert v["updated_by"] == "Andrea Attorney" and settings_history(data) >= 1
    # the environment wins (a hosted server's keys)
    assert clio.credentials(data, {"CLIO_CLIENT_ID": "fromServerId99", "CLIO_CLIENT_SECRET": "fromServerSecret99"}) == ("fromServerId99", "fromServerSecret99")
    # a vault from another server (its key changed) is said plainly, not a crash
    assert "connect again" in clio.ready(data, {"CLIO_TOKEN_KEY": "jLNzLy9I3Wz3Hj_5vD1xU8fF1d8OaH8B3k6m3c3jZ2s="})
    # switched off: the sync does nothing and Keeping current says so
    clio.save_settings(data, {"on": False}, "Andrea Attorney", env=ENV)
    assert firm["run"]() == "Clio: not syncing (switched off)." and clio.state_text(data, ENV) == (
        "Clio: switched off. An attorney switches it on in Settings, Connections.", False)


def settings_history(data: Path) -> int:
    return len(json.loads((data / "clio" / "settings.json").read_text(encoding="utf-8"))["history"])


def test_keeping_current_has_clio_and_the_firms_app_credentials():
    items = {i["id"]: i for i in maintenance.registry()["items"]}
    api, app = items["clio_api"], items["clio_app_credentials"]
    assert api["party"] == "provider" and api["cadence"] == "quarterly" and "api-changelog" in api["source"]
    assert app["party"] == "firm" and app["firm_steps"] and app["check"]["type"] == "clio_connection"
    assert not any(w in " ".join(app["firm_steps"]) for w in ("schemas/", "src/", ".json", ".py", "IT:"))


def test_the_other_connections_say_where_they_stand_without_their_keys(tmp_path, monkeypatch):
    connectors = tmp_path / "connectors.json"
    connectors.write_text(json.dumps({"active": {"documents": "google_drive", "results": "none"}, "google_drive": {"root_folder_id": "abc"}}))
    from connectors import signin

    monkeypatch.setattr(signin, "CONNECTORS", connectors)
    rows = {o["id"]: o for o in clio.others({"GOOGLE_SERVICE_ACCOUNT_FILE": "/keys/sa.json", "FILEVINE_PAT": "only-one"})}
    assert rows["google_drive"]["on"] and rows["google_drive"]["keys"] and rows["google_drive"]["state"] == "Switched on: the server has its keys."
    assert not rows["filevine"]["keys"] and rows["filevine"]["state"] == "Not switched on: the server doesn't have its keys yet."
    assert "/keys" not in json.dumps(rows)  # nothing of the keys themselves


def test_the_overnight_run_says_nothing_of_clio_until_it_is_set_up(tmp_path):
    import overnight

    assert overnight.clio_step(tmp_path, tmp_path / "clients", tmp_path / "out", "in") is None
    (tmp_path / "clio").mkdir()
    assert overnight.clio_step(tmp_path, tmp_path / "clients", tmp_path / "out", "in") == "Clio: not syncing (switched off)."


def test_the_check_command_reads_and_changes_nothing(firm, fake):
    clio.save_settings(firm["data"], {"practice_areas": SETUP["practice_areas"]}, "Andrea Attorney", env=ENV)
    ok, lines = clio.check(firm["data"], transport=httpx.MockTransport(fake.handle), env=ENV)
    assert ok and lines == ["Connected to Clio as Andrea Exemplo.", "Immigration: 1 or more open or pending matter(s).",
                            "The first matter (00101-Souza) has 1 document(s)."]
    assert {method for method, url in fake.requests if "/api/v4/" in url} == {"GET"}


def test_one_matters_refusal_is_recorded_against_it_and_the_others_still_go(firm, fake, view, capsys):
    firm["run"]("in")
    ana, maria = "ana_clara_exemplo_souza-cl101", "maria_exemplo-cl102"
    _processed(firm, ana)
    _processed(firm, maria)
    fake.refuse_matter = 101  # the connected Clio user can't write to Ana's matter
    line = firm["run"]("out")
    assert "1 case(s) had a problem (Recent problems, in Settings, Connections)" in line, line
    # Maria's matter, after Ana's, still gets its documents, its note and its calendar entries
    assert {d["matter"] for d in fake.created.values()} == {102} and len(fake.created) == 2
    assert [n["matter"] for n in fake.notes] == [{"id": 102}] and {e["matter"]["id"] for e in fake.entries.values()} == {102}
    # the problem, in words, against Ana's case; the API path only in the server's log
    [problem] = clio.view(firm["data"], ENV)["errors"]
    assert problem["case"] == ana and problem["what"] == ("Ana Clara Exemplo Souza: Clio refused the upload of the filing packet for Ana Clara Exemplo "
                                                          "Souza: no access (the connected Clio user, or the Clio app's permissions, don't allow it).")
    # "403" as a word: a document's sha256 kept in the state may hold those three digits by chance (the packet carries the day)
    import re

    stored = json.dumps(clio.state(firm["data"]))
    assert not any(w in stored for w in ("/documents.json", "POST ", "ForbiddenError")) and not re.search(r"(?<![0-9a-f])403(?![0-9a-f])", stored)
    assert "POST https://app.clio.com/api/v4/documents.json -> 403" in capsys.readouterr().err
    assert clio.state_text(firm["data"], ENV)[1]  # Keeping current flags it
    # fixed in Clio: the next sync sends Ana's case too
    fake.refuse_matter = None
    assert "had a problem" not in firm["run"]("out") and {d["matter"] for d in fake.created.values()} == {101, 102}


def test_what_staff_read_when_clio_refuses_is_words_not_paths(firm, fake):
    assert clio._words("GET", "https://app.clio.com/api/v4/matters.json?page_token=2") == "reading the matters"
    assert clio._words("PATCH", "/calendar_entries/7.json") == "updating a calendar entry"
    with pytest.raises(clio.ClioError) as refused:
        _client(firm).call("GET", "/nothing_here.json")
    assert str(refused.value) == "Clio refused reading a record: it isn't in Clio, or the connected Clio user can't see it."
    # a malformed key on the server: plain words, not the encryption library's
    why = clio.ready(firm["data"], {"CLIO_TOKEN_KEY": "not-a-key"})
    assert why.startswith("The server's encryption key for the Clio connection isn't a valid key") and "base64" not in why and "Fernet" not in why


def test_nothing_goes_out_except_through_the_checks_of_sync_out():
    # the other connectors' publish(files, note) can't tell a protected case or a draft from bare files: Clio has none
    assert not hasattr(clio.Clio, "publish")
