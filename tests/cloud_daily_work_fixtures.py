"""Fresh fictional installation for one protected daily-work case.

Seed accounts/notices only. No case, graph, association, grant, document subject,
field approval or legal approval is fabricated. Helpers call actual HTTP routes;
PDF text is fictional source content, read by the registered product worker.
"""
import base64
import hashlib
import http.client
import json
import re
import shutil
import threading
from urllib.parse import quote, urlsplit

import httpx
import pytest

import jobs
import schema_path
import second_factor
import settings
from communication_fixture import installation
from connectors import drive_intake
from connectors.drive_intake import FirmScope
from portal.demo import document_pdf
from portal.notify import Notifier
from portal.store import PortalStore
from review.auth import Accounts
from review.server import ReviewApp, make_handler, serve

PASSWORD = "fictional daily workflow password"  # secret-scan: allow (fictional test only)
ATTORNEY = "attorney@fictional.example"
STAFF = "staff@fictional.example"
OTHER_STAFF = "transfer@fictional.example"
NAME = "Alpha Example Fictional"
EMAIL = "alpha.daily@example.test"
PHONE = "+15550100242"
REMOTE = "fictional-daily-selected"
I94 = """Most Recent I-94
Admission I-94 Record Number: 11111111111
Class of Admission: B2
Arrival/Issued Date: 01/02/2020
Last/Surname: EXAMPLE
First (Given) Name: ALPHA
Birth Date: 01/02/2000
"""
NTA = """NOTICE TO APPEAR
Department of Homeland Security
Form I-862
In the Matter of: ALPHA EXAMPLE
File No: A099000001
DOB: January 2, 2000
X You have been admitted to the United States, but are removable for the reasons stated below.
The Department of Homeland Security alleges that you:
1. You are not a citizen or national of the United States;
2. You are a native of BRAZIL and a citizen of BRAZIL;
3. You arrived in the United States at or near NEWARK, NJ on or about March 4, 2024;
4. You were admitted to the United States as a nonimmigrant.
On the basis of the foregoing, it is charged that you are subject to removal under the following provisions of law:
Section 237(a)(1)(B) of the Immigration and Nationality Act.
You are ordered to appear before an immigration judge at:
1 Fictional Court Street, Boston, MA 02100
on a date to be set
"""
BIRTH = """FEDERATIVE REPUBLIC OF BRAZIL
BIRTH CERTIFICATE
Name: ALPHA EXAMPLE
Date of Birth: January 2, 2000
City and State of Birth: CAMPINAS - SAO PAULO
Father: FICTIONAL FATHER EXAMPLE
Mother: FICTIONAL MOTHER EXAMPLE
"""
MARRIAGE = """The Commonwealth of Massachusetts
Certificate of Marriage
Date of Marriage: JULY 1, 2026
Place of Marriage: BOSTON, MA
Party A Party B
Name: ALPHA EXAMPLE Name: DELTA EXAMPLE
Date of Birth: JANUARY 2, 2000 Date of Birth: MARCH 3, 2001
Place of Birth: SAO PAULO, BRAZIL Place of Birth: BOGOTA, COLOMBIA
Marriage: FIRST Marriage: FIRST
"""
PORTAL_ANSWERS = {
    "given_name": "Alpha", "family_name": "Example", "dob": "2000-01-02",
    "birth_country": "Brazil", "last_entry_date": "2022-05-06",
    "last_entry_place": {"city": "Boston", "state": "MA"},
    "job_history": [{"employer": "Fictional Example Shop", "occupation": "Clerk",
                     "city": "Boston", "state": "MA", "country": "United States",
                     "date_from": "2021-01-01", "date_to": "2022-01-01"},
                    {"employer": "Fictional Example School", "occupation": "Student",
                     "city": "Boston", "state": "MA", "country": "United States",
                     "date_from": "2022-01-02", "date_to": "2023-01-01"}],
}


def pdf(text):
    return document_pdf(text.splitlines())


def request(base, route, cookie=None, body=None, *, method=None):
    """Return status, headers, original response bytes, without redirects."""
    origin = urlsplit(base)
    conn = http.client.HTTPConnection(origin.hostname, origin.port, timeout=60)
    headers = {"X-Review-App": "1"}
    if cookie:
        headers["Cookie"] = cookie
    payload = None
    if body is not None:
        payload = json.dumps(body).encode()
        headers["Content-Type"] = "application/json"
    conn.request(method or ("POST" if body is not None else "GET"), route, payload, headers)
    response = conn.getresponse()
    result = response.status, dict(response.getheaders()), response.read()
    conn.close()
    return result


def ok(base, route, cookie, body=None):
    status, _, raw = request(base, route, cookie, body)
    assert status == 200, (route, status, raw[:1000])
    return json.loads(raw)


def login(base, email, password=PASSWORD):
    status, headers, raw = request(base, "/api/login", body={"email": email, "password": password})
    assert status == 200, raw
    cookie = headers["Set-Cookie"].split(";", 1)[0]
    user = json.loads(raw)["user"]
    return second_factor.finish(base, email, cookie) if user.get("second_factor") else cookie


def retain(base, cookie, route, body, content):
    return ok(base, route, cookie, dict(body, action="evidence",
        content_base64=base64.b64encode(content).decode()))["evidence"]


def review_wording(base, cookie, language="en"):
    evidence = retain(base, cookie, "/api/client-wording", {},
                      b'{"fictional":true,"actual_review":"Current complete fictional service wording"}')
    return ok(base, "/api/client-wording", cookie,
              {"action": "attorney_review", "language": language, "evidence": evidence})


def last_token(world):
    return re.search(r"/l/([A-Za-z0-9_-]+)", world["sent"][-1]["body"]).group(1)


def field_card(base, cookie, client, key):
    """Current protected card; never invent an item or reuse stale proof."""
    items = ok(base, "/api/items?client=" + client, cookie)
    return next(card for card in items["cards"] if any(fact["key"] == key for fact in card["facts"]))


def decide_field(base, cookie, client, key, value, *, note):
    """Explicit test reviewer choice, bound to currently exposed source proof."""
    card = field_card(base, cookie, client, key)
    assert "set" in card["actions"], card
    item_id = next(fact["item_id"] for fact in card["facts"] if fact["key"] == key)
    return ok(base, "/api/decide", cookie, {"client": client, "item_id": item_id,
        "action": "set", "values": {key: value}, "note": note,
        "evidence_fingerprints": card.get("evidence_fingerprints", {})})


def review_source_subjects(base, cookie, client):
    """Explicit retained-page review of these four fictional source types.

    This is a caller-invoked human-review simulation, never seed-world approval.
    It rejects other source types/slots rather than inferring a person from names.
    Critical field decisions remain separate and are not made here.
    """
    data = ok(base, "/api/documents?client=" + client, cookie)
    known = {"i94", "notice_to_appear", "birth_certificate", "marriage_certificate"}
    expected_role = {"holder": "applicant", "respondent": "applicant",
                     "birth_subject": "applicant", "father": "father", "mother": "mother",
                     "party_a": "applicant", "party_b": "spouse"}
    for row in data["subject_reviews"]:
        assert row["type"] in known and row["bound"], row
        # Open actual immutable original and page with the current digest.
        status, _, raw = request(base, "/api/file?client=" + client + "&doc=" + quote(row["file"]), cookie)
        assert status == 200 and raw.startswith(b"%PDF")
        status, _, raw = request(base, "/api/crop?client=" + client + "&doc=" + quote(row["file"])
            + "&page=" + str(row["first"]) + "&expected_sha256=" + row["source_sha256"], cookie)
        assert status == 200 and raw
        mappings = {}
        for slot in row["slots"]:
            assert slot in expected_role, row
            role = expected_role[slot]
            people = ok(base, "/api/documents?client=" + client, cookie)["case_subjects"]
            person = next((person for person in people if person["case_role"] == role), None)
            if person is None:
                assert role in {"mother", "father", "spouse"}
                label = "Delta Example Fictional" if role == "spouse" else "Fictional " + role.title() + " Example"
                added = ok(base, "/api/document", cookie, {"client": client, "field": "subject_person",
                    "value": label, "case_role": role})
                person = next(person for person in added["case_subjects"] if person["case_role"] == role)
            mappings[slot] = person["id"]
        current = next(item for item in ok(base, "/api/documents?client=" + client, cookie)["subject_reviews"]
                       if item["instance_id"] == row["instance_id"])
        ok(base, "/api/document", cookie, {"client": client, "field": "subject_assignment",
            "id": current["instance_id"], "fingerprint": current["fingerprint"], "mappings": mappings,
            "note": "FICTIONAL reviewer compared the retained original page and explicit case people."})
    return ok(base, "/api/documents?client=" + client, cookie)


def work(world):
    """Real installed loop and registered handlers; policies remain held."""
    return jobs.work(world["scope"].cases, world["scope"].portal, once=True,
                     jobs_root=world["scope"].queue, use_policies=True)


@pytest.fixture
def cloud_world(tmp_path, monkeypatch):
    root = tmp_path / "fictional-daily-installation"
    root.mkdir()
    data = installation(root, monkeypatch)
    (root / "install").mkdir()
    (root / "install/install.json").write_text("{}")
    scope = FirmScope(root)
    scope.documents.mkdir()
    shutil.copytree(schema_path.ROOT, schema_path.schemas_in(root), dirs_exist_ok=True)
    monkeypatch.setattr(schema_path, "ROOT", schema_path.schemas_in(root))
    monkeypatch.setattr(settings, "PATH", data / "settings.json")
    for key, path in {"I485_CLIENTS_ROOT": scope.documents, "I485_JOBS": scope.queue,
                      "I485_ROSTER": data / "roster.json", "I485_FIND": data / "find.db",
                      "I485_CONFLICTS": data / "conflict_checks.jsonl",
                      "I485_READER_EXAMPLES": data / "reader_examples",
                      "I485_POLICIES_FIRM": data / "policies_firm.json"}.items():
        monkeypatch.setenv(key, str(path))
    monkeypatch.setenv("I485_JOBS_WORKER", "0")
    monkeypatch.setenv("PORTAL_BASE_URL", "http://testserver")
    for key in ("SMTP_HOST", "TWILIO_ACCOUNT_SID", "PORTAL_OUTBOX_FULL_LINKS"):
        monkeypatch.delenv(key, raising=False)
    accounts = Accounts(data / "review_users.json")
    # installation() created these two fictional accounts without passwords.
    for email in (ATTORNEY, STAFF):
        accounts.change_password(email, accounts.reset(email), PASSWORD)
    second_factor.set_up(accounts, ATTORNEY, PASSWORD)
    sent = []
    def email_provider(self, destination, subject, body, kind):
        sent.append({"destination": destination, "subject": subject, "body": body, "kind": kind})
        return "sent"
    monkeypatch.setattr(Notifier, "_email", email_provider)
    # Explicit deterministic worker scheduling; work() still executes real handlers.
    monkeypatch.setattr(jobs, "ensure_worker", lambda *_a, **_k: False)
    return {"scope": scope, "store": PortalStore(scope.portal), "accounts": accounts,
            "sent": sent, "root": root, "pages": [I94, NTA, BIRTH]}


@pytest.fixture
def cloud_app(cloud_world):
    scope = cloud_world["scope"]
    app = ReviewApp(scope.cases, schema_path.path("field_map", "i485"),
                    schema_path.path("template", "i485"), None,
                    portal_root=scope.portal, accounts=cloud_world["accounts"])
    cloud_world["app"] = app
    return app


@pytest.fixture
def cloud_server(cloud_app):
    httpd = serve(cloud_app, 0)
    httpd.RequestHandlerClass = make_handler(cloud_app, httpd.server_address[1])
    thread = threading.Thread(target=httpd.serve_forever, daemon=True)
    thread.start()
    try:
        yield f"http://127.0.0.1:{httpd.server_address[1]}"
    finally:
        httpd.shutdown()
        httpd.server_close()
        thread.join(5)


def install_selected_drive(world, monkeypatch):
    """Real installed readonly adapter; all HTTP handled by MockTransport."""
    import firmsecrets
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa
    key = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    secret = json.dumps({"client_email": "fictional-daily-service@invalid.example",
        "private_key": key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8,
                                         serialization.NoEncryption()).decode(),
        "token_uri": "https://fictional-daily-token.invalid/token"})
    def vault(name, **kwargs):
        assert name == "gdrive.service_account" and kwargs["data_root"] == world["scope"].data and kwargs["env"] == {}
        return secret
    monkeypatch.setattr(firmsecrets, "get", vault)
    content = pdf(BIRTH)
    state = {"calls": [], "downloads": 0, "pdf": content}
    def provider(request):
        state["calls"].append((request.method, request.url.host, request.url.path))
        if request.url.host == "fictional-daily-token.invalid":
            assert request.method == "POST" and b"jwt-bearer" in request.content
            return httpx.Response(200, json={"access_token": "fictional-daily-token", "expires_in": 3600})
        assert request.method == "GET" and request.url.host == "www.googleapis.com"
        if request.url.path == "/drive/v3/files":
            query = request.url.params["q"]
            if "'fictional-daily-root'" in query:
                return httpx.Response(200, json={"files": [
                    {"id": REMOTE, "name": NAME, "mimeType": "application/vnd.google-apps.folder"},
                    {"id": "fictional-unselected-private", "name": "Unselected Fictional Person",
                     "mimeType": "application/vnd.google-apps.folder"}]})
            assert "'" + REMOTE + "'" in query
            return httpx.Response(200, json={"files": [{"id": "fictional-daily-birth", "name": "Fictional Birth.pdf",
                "mimeType": "application/pdf", "size": str(len(content)), "modifiedTime": "2026-10-05T00:00:00Z",
                "md5Checksum": hashlib.md5(content).hexdigest()}]})
        assert request.url.path == "/drive/v3/files/fictional-daily-birth" and request.url.params["alt"] == "media"
        state["downloads"] += 1
        return httpx.Response(200, content=content)
    original = drive_intake.installed
    transport = httpx.MockTransport(provider)
    monkeypatch.setattr(drive_intake, "installed", lambda scope: original(scope, transport=transport))
    return state
