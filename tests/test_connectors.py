"""Connectors (src/connectors): built against simulated Filevine and Google
servers -- not switched on for the pipeline yet."""

import base64
import hashlib
import hmac
import io
import json
import time
from urllib.parse import parse_qs

import httpx
import pytest
from cryptography.hazmat.primitives import hashes, serialization
from cryptography.hazmat.primitives.asymmetric import padding, rsa
from PIL import Image

from connectors import sync
from connectors.base import RemoteClient, RemoteDoc
from connectors.filevine import Filevine, FilevineError, verify_webhook
from connectors.gdrive import FOLDER, DriveError, GoogleDrive
from connectors.signin import GoogleSignIn, MicrosoftSignIn, SignInError, staff_session
from connectors.local import FolderSink, LocalFolderSource


def _png() -> bytes:
    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


# --- mirroring into clients/<id>/source ---------------------------------------------


class FakeSource:
    name = "filevine"

    def __init__(self):
        self.docs = {"1": [RemoteDoc("10", "Passport.pdf", "application/pdf", 9, "2026-09-01", "v1"),
                           RemoteDoc("11", "SSN card.jpg", "image/jpeg", 9, "2026-09-01", "v1"),
                           RemoteDoc("12", "Intake.xlsx", "application/vnd.ms-excel", 9, "2026-09-01", "v1")]}
        self.body = {"10": b"%PDF-1.4 passport", "11": _png()}
        self.downloads = []

    def clients(self):
        return [RemoteClient("1", "Ana Clara Exemplo Souza")]

    def documents(self, client):
        return self.docs[client.id]

    def download(self, client, doc):
        self.downloads.append(doc.id)
        return self.body[doc.id]


def test_a_source_is_mirrored_into_the_folders_the_pipeline_reads(tmp_path):
    source = FakeSource()
    report = sync.mirror(source, tmp_path)
    lid = "ana_clara_exemplo_souza-fv1"
    folder = tmp_path / lid / "source"
    files = sorted(p.name for p in folder.iterdir())
    assert files == ["Passport [10].pdf", "SSN card [11].pdf"]  # a photo becomes a PDF; a spreadsheet is skipped
    assert (folder / "SSN card [11].pdf").read_bytes()[:4] == b"%PDF"
    assert report["clients"][lid]["skipped"] == ["Intake.xlsx"]
    # nothing changed at the source: nothing is downloaded, nothing touched
    mtime = (folder / "Passport [10].pdf").stat().st_mtime_ns
    source.downloads.clear()
    report = sync.mirror(source, tmp_path)
    assert source.downloads == [] and (folder / "Passport [10].pdf").stat().st_mtime_ns == mtime
    # a new version of one document: only that one is fetched; a removed one is reported, never deleted
    source.docs["1"] = [RemoteDoc("10", "Passport.pdf", "application/pdf", 12, "2026-09-02", "v2")]
    source.body["10"] = b"%PDF-1.4 passport v2"
    report = sync.mirror(source, tmp_path)
    assert source.downloads == ["10"] and report["clients"][lid]["updated"] == ["Passport [10].pdf"]
    assert report["clients"][lid]["gone"] == ["SSN card [11].pdf"] and (folder / "SSN card [11].pdf").exists()


def test_local_folders_and_a_results_folder_work_as_connectors(tmp_path):
    (tmp_path / "c1" / "source").mkdir(parents=True)
    (tmp_path / "c1" / "source" / "a.pdf").write_bytes(b"%PDF-1.4 a")
    src = LocalFolderSource(tmp_path)
    [client] = src.clients()
    [doc] = src.documents(client)
    assert src.download(client, doc) == b"%PDF-1.4 a" and doc.checksum == hashlib.md5(b"%PDF-1.4 a").hexdigest()
    out = FolderSink(tmp_path / "results").publish(client, [tmp_path / "c1" / "source" / "a.pdf"], "packet built")
    assert out["published"] and (tmp_path / "results" / "c1" / "a.pdf").exists()


# --- Filevine -----------------------------------------------------------------------------

FV_ENV = {"FILEVINE_PAT": "pat-1", "FILEVINE_CLIENT_ID": "cid", "FILEVINE_CLIENT_SECRET": "sec", "FILEVINE_ORG_ID": "77", "FILEVINE_USER_ID": "88"}


def filevine_server(calls):
    def handle(request: httpx.Request) -> httpx.Response:
        calls.append(request)
        path, q = request.url.path, dict(request.url.params)
        if request.url.host == "identity.filevine.com":
            form = parse_qs(request.content.decode())
            assert form["grant_type"] == ["personal_access_token"] and form["token"] == ["pat-1"]
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1200})
        if request.url.host == "storage.example":  # step 2: the bytes go to the storage address, with none of Filevine's headers
            assert request.method == "PUT" and "authorization" not in request.headers and "x-fv-orgid" not in request.headers
            assert request.content == b"%PDF-1.4 packet"
            return httpx.Response(200)
        if request.url.host == "files.example":  # the pre-signed download link
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"%PDF-1.4 from filevine")
        assert request.headers["authorization"] == "Bearer tok" and request.headers["x-fv-orgid"] == "77" and request.headers["x-fv-userid"] == "88"
        if path == "/fv-app/v2/Projects" and request.method == "GET":
            assert q.get("projectTypeId") == "5"
            if q["offset"] == "0":
                return httpx.Response(200, json={"items": [{"projectId": 1, "projectName": "Ana Sample", "clientId": 9},
                                                           {"projectId": 2, "projectName": "Old", "isArchived": True}], "hasMore": True})
            return httpx.Response(200, json={"items": [{"projectId": 3, "projectName": "Bruno Sample"}], "hasMore": False})
        if path == "/fv-app/v2/Projects/1/Documents" and request.method == "GET":
            return httpx.Response(200, json={"items": [{"documentId": 10, "filename": "passport.pdf", "contentType": "application/pdf",
                                                        "size": 22, "version": 3}], "hasMore": False})
        if path == "/fv-app/v2/Documents/10":
            return httpx.Response(200, json={"documentId": 10, "downloadUrl": "https://files.example/p/10?sig=x"})
        if path == "/fv-app/v2/Documents" and request.method == "POST":  # step 1: ask for a document id and a storage address
            body = json.loads(request.content)
            assert body == {"Filename": "packet.pdf", "OrgID": "77", "Size": 15}
            return httpx.Response(201, json={"documentId": 501, "url": "https://storage.example/upload/501?sig=x"})
        if path == "/fv-app/v2/Projects/1/Documents/501":  # step 3: add it to the project
            assert request.method == "POST"
            assert json.loads(request.content) == {"tags": ["i485-pipeline"]}
            return httpx.Response(200, json={})
        if path == "/fv-app/v2/Projects/1/Notes":
            body = json.loads(request.content)
            assert body["attachedDocuments"] == [501] and "packet" in body["body"]
            return httpx.Response(201, json={"noteId": 1})
        if path == "/fv-app/v2/Projects/1" and request.method == "PATCH":
            return httpx.Response(200, json={"phaseName": json.loads(request.content)["phaseName"]})
        return httpx.Response(404)
    return handle


def test_filevine_lists_sijs_projects_and_their_documents(tmp_path):
    calls = []
    fv = Filevine({"project_type_id": 5}, env=FV_ENV, transport=httpx.MockTransport(filevine_server(calls)))
    clients = fv.clients()
    assert [c.name for c in clients] == ["Ana Sample", "Bruno Sample"]  # archived left out; both pages read
    [doc] = fv.documents(clients[0])
    assert (doc.id, doc.name, doc.checksum) == ("10", "passport.pdf", "3")
    assert fv.download(clients[0], doc) == b"%PDF-1.4 from filevine"
    assert sum(1 for c in calls if c.url.host == "identity.filevine.com") == 1  # one token, reused


def test_filevine_gets_the_packet_back_with_a_note_and_changes_phases_only_when_told(tmp_path):
    calls = []
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(b"%PDF-1.4 packet")
    fv = Filevine({}, env=FV_ENV, transport=httpx.MockTransport(filevine_server(calls)))
    out = fv.publish(RemoteClient("1", "Ana Sample"), [packet], "I-485 packet built (draft).", stage="ready")
    assert out == {"published": True, "documents": [501], "phase": None}
    assert not any(c.method == "PATCH" for c in calls)
    fv = Filevine({"set_phase": {"ready": "Ready to File"}}, env=FV_ENV, transport=httpx.MockTransport(filevine_server(calls)))
    assert fv.publish(RemoteClient("1", "Ana Sample"), [packet], "packet ready", stage="ready")["phase"] == "Ready to File"


def test_filevine_uploads_in_filevines_three_steps_in_order(tmp_path):
    """Filevine's Create Document URL for Upload page (read 2026-10-02): ask, PUT to the storage address, add to the project."""
    calls = []
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(b"%PDF-1.4 packet")
    fv = Filevine({}, env=FV_ENV, transport=httpx.MockTransport(filevine_server(calls)))
    assert fv.upload("1", packet) == 501
    steps = [(c.method, c.url.host, c.url.path) for c in calls if c.url.host != "identity.filevine.com"]
    assert steps == [("POST", "api.filevineapp.com", "/fv-app/v2/Documents"), ("PUT", "storage.example", "/upload/501"),
                     ("POST", "api.filevineapp.com", "/fv-app/v2/Projects/1/Documents/501")]


def test_filevine_says_so_when_the_upload_answer_has_no_address(tmp_path):
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(b"%PDF-1.4 packet")

    def handle(request):
        if request.url.host == "identity.filevine.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1200})
        return httpx.Response(201, json={"documentId": {"native": 501}})  # an id as an object is read; the missing address is named
    with pytest.raises(FilevineError, match="upload address"):
        Filevine({}, env=FV_ENV, transport=httpx.MockTransport(handle)).upload("1", packet)


def test_filevine_says_so_when_storage_refuses_the_file(tmp_path):
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(b"%PDF-1.4 packet")

    def handle(request):
        if request.url.host == "identity.filevine.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1200})
        if request.url.host == "storage.example":
            return httpx.Response(403)
        return httpx.Response(201, json={"documentID": 7, "uploadUrl": "https://storage.example/u"})
    with pytest.raises(FilevineError, match="storage refused"):
        Filevine({}, env=FV_ENV, transport=httpx.MockTransport(handle)).upload("1", packet)


def test_filevine_without_credentials_says_what_is_missing():
    with pytest.raises(FilevineError, match="FILEVINE_PAT"):
        Filevine({}, env={})


def test_filevine_waits_out_rate_limits(monkeypatch):
    monkeypatch.setattr("connectors.filevine.time.sleep", lambda s: None)
    seen = {"n": 0}

    def handle(request):
        if request.url.host == "identity.filevine.com":
            return httpx.Response(200, json={"access_token": "tok", "expires_in": 1200})
        seen["n"] += 1
        return httpx.Response(429, headers={"Retry-After": "1"}) if seen["n"] < 3 else httpx.Response(200, json={"items": [], "hasMore": False})
    assert Filevine({}, env=FV_ENV, transport=httpx.MockTransport(handle)).clients() == []


def test_webhook_signatures():
    body = b'{"event":"document.uploaded","projectId":1}'
    sig = hmac.new(b"key", body, hashlib.sha256).hexdigest()
    assert verify_webhook(body, sig, "key") and verify_webhook(body, f"sha256={sig}", "key")
    assert not verify_webhook(body + b" ", sig, "key")


# --- Google Drive -------------------------------------------------------------------------


@pytest.fixture(scope="module")
def rsa_key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


def _unb64(text):
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def drive_server(public_key, calls):
    def handle(request):
        calls.append(request)
        if request.url.host == "oauth2.googleapis.com":
            assertion = parse_qs(request.content.decode())["assertion"][0]
            head, body, sig = assertion.split(".")
            public_key.verify(_unb64(sig), f"{head}.{body}".encode(), padding.PKCS1v15(), hashes.SHA256())  # really signed by the key
            claims = json.loads(_unb64(body))
            assert claims["scope"].endswith("drive.readonly") and claims["iss"] == "svc@firm.iam.gserviceaccount.com"
            return httpx.Response(200, json={"access_token": "gtok", "expires_in": 3600})
        assert request.headers["authorization"] == "Bearer gtok"
        q = request.url.params.get("q", "")
        if "'ROOT' in parents" in q:
            return httpx.Response(200, json={"files": [{"id": "F1", "name": "Ana Sample", "mimeType": FOLDER},
                                                       {"id": "x", "name": "readme.txt", "mimeType": "text/plain"}]})
        if "'F1' in parents" in q:
            return httpx.Response(200, json={"files": [{"id": "D", "name": "Documents", "mimeType": FOLDER}]})
        if "'D' in parents" in q:
            if not request.url.params.get("pageToken"):
                return httpx.Response(200, json={"files": [{"id": "p", "name": "passport.pdf", "mimeType": "application/pdf", "size": "5",
                                                            "md5Checksum": "abc", "modifiedTime": "2026-09-01T00:00:00Z"}], "nextPageToken": "n2"})
            return httpx.Response(200, json={"files": [{"id": "g", "name": "Notes", "mimeType": "application/vnd.google-apps.document",
                                                        "modifiedTime": "2026-09-02T00:00:00Z"}]})
        if request.url.path == "/drive/v3/files/p":
            assert request.url.params["alt"] == "media"
            return httpx.Response(200, content=b"%PDF-1.4 drive")
        if request.url.path == "/drive/v3/files/g/export":
            assert request.url.params["mimeType"] == "application/pdf"
            return httpx.Response(200, content=b"%PDF-1.4 exported")
        return httpx.Response(404)
    return handle


def test_google_drive_reads_client_folders_with_a_service_account(tmp_path, rsa_key):
    pem = rsa_key.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()).decode()
    key_file = tmp_path / "sa.json"
    key_file.write_text(json.dumps({"client_email": "svc@firm.iam.gserviceaccount.com", "private_key": pem, "private_key_id": "k1",
                                    "token_uri": "https://oauth2.googleapis.com/token"}))
    calls = []
    drive = GoogleDrive({"root_folder_id": "ROOT", "documents_subfolder": "documents"}, env={"GOOGLE_SERVICE_ACCOUNT_FILE": str(key_file)},
                        transport=httpx.MockTransport(drive_server(rsa_key.public_key(), calls)))
    [client] = drive.clients()  # folders only
    docs = drive.documents(client)
    assert [d.name for d in docs] == ["passport.pdf", "Notes.pdf"]  # both pages; a Google Doc arrives as a PDF
    assert drive.download(client, docs[0]) == b"%PDF-1.4 drive" and drive.download(client, docs[1]) == b"%PDF-1.4 exported"
    report = sync.mirror(drive, tmp_path / "clients")
    lid = sync.local_id(client, "google_drive")
    assert lid.startswith("ana_sample-gd") and lid == lid.lower()
    assert sorted(report["clients"][lid]["added"]) == ["Notes [g].pdf", "passport [p].pdf"]


def test_google_drive_without_a_key_or_folder_says_what_is_missing(tmp_path):
    with pytest.raises(DriveError, match="GOOGLE_SERVICE_ACCOUNT_FILE"):
        GoogleDrive({"root_folder_id": "ROOT"}, env={})
    (tmp_path / "k.json").write_text("{}")
    with pytest.raises(DriveError, match="root_folder_id"):
        GoogleDrive({}, env={"GOOGLE_SERVICE_ACCOUNT_FILE": str(tmp_path / "k.json")})


# --- Google sign-in -------------------------------------------------------------------------

SIGNIN_ENV = {"GOOGLE_OAUTH_CLIENT_ID": "app.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "s"}


def _b64(data):
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def id_token(key, **claims):
    base = {"iss": "https://accounts.google.com", "aud": SIGNIN_ENV["GOOGLE_OAUTH_CLIENT_ID"], "exp": int(time.time()) + 600,
            "nonce": "n1", "email": "jane@firm.example", "email_verified": True, "hd": "firm.example"}
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(base | claims).encode())
    return f"{head}.{body}.{_b64(key.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


@pytest.fixture
def signin(rsa_key):
    nums = rsa_key.public_key().public_numbers()
    jwks = {"keys": [{"kid": "k1", "kty": "RSA", "alg": "RS256", "n": _b64(nums.n.to_bytes(256, "big")), "e": _b64(nums.e.to_bytes(3, "big"))}]}
    return GoogleSignIn("firm.example", "https://review.firm.example/auth/google", env=SIGNIN_ENV,
                        transport=httpx.MockTransport(lambda r: httpx.Response(200, json=jwks)))


def test_google_sign_in_accepts_only_a_verified_firm_account(signin, rsa_key):
    assert signin.verify(id_token(rsa_key), "n1")["email"] == "jane@firm.example"
    assert "hd=firm.example" in signin.authorize_url("s", "n1") and "openid" in signin.authorize_url("s", "n1")
    for claims, why in (({"hd": "gmail.com"}, "not a firm.example account"), ({"aud": "other"}, "another app"),
                        ({"exp": 1}, "expired"), ({"nonce": "zz"}, "nonce"), ({"email_verified": False}, "not verified")):
        with pytest.raises(SignInError, match=why):
            signin.verify(id_token(rsa_key, **claims), "n1")
    forged = rsa.generate_private_key(public_exponent=65537, key_size=2048)
    with pytest.raises(SignInError, match="signature"):
        signin.verify(id_token(forged), "n1")


def test_a_google_login_still_needs_a_place_on_the_staff_list(tmp_path, signin, rsa_key):
    from review.auth import Accounts

    accounts = Accounts(tmp_path / "users.json")
    accounts.add("jane@firm.example", "Jane Doe", "paralegal")  # never chose a password: Google signs her in
    claims = signin.verify(id_token(rsa_key), "n1")
    token, user = staff_session(accounts, claims)
    assert user["role"] == "paralegal" and not user["must_change"]
    assert accounts.session_user(token)["must_change"] is False
    with pytest.raises(ValueError, match="don't match"):  # the words of a wrong password: nothing says who is on the list
        staff_session(accounts, signin.verify(id_token(rsa_key, email="stranger@firm.example"), "n1"))


# --- Microsoft 365 (SharePoint / OneDrive through Graph) ------------------------------------

from connectors.msgraph import GraphError, Microsoft365  # noqa: E402

MS_ENV = {"MS_TENANT_ID": "tenant-guid", "MS_CLIENT_ID": "app-id", "MS_CLIENT_SECRET": "s"}


def graph_server(calls):
    def handle(request):
        calls.append(request)
        url, path = request.url, request.url.path
        if url.host == "login.microsoftonline.com":
            form = parse_qs(request.content.decode())
            assert path == "/tenant-guid/oauth2/v2.0/token" and form["grant_type"] == ["client_credentials"]
            assert form["scope"] == ["https://graph.microsoft.com/.default"]
            return httpx.Response(200, json={"access_token": "mstok", "expires_in": 3600})
        if url.host == "download.example":  # pre-authenticated link: no token
            assert "authorization" not in request.headers
            return httpx.Response(200, content=b"%PDF-1.4 " + url.path.encode())
        assert request.headers["authorization"] == "Bearer mstok"
        if path == "/v1.0/sites/firm.sharepoint.com:/sites/Clients":
            return httpx.Response(200, json={"id": "SITE"})
        if path == "/v1.0/sites/SITE/drives":
            return httpx.Response(200, json={"value": [{"id": "OTHER", "name": "Shared"}, {"id": "DRV", "name": "Documents"}]})
        if path == "/v1.0/drives/DRV/root:/Clients/SIJS:/children":
            if url.params.get("$skiptoken"):
                return httpx.Response(200, json={"value": [{"id": "C2", "name": "Bruno Sample", "folder": {"childCount": 0}}]})
            return httpx.Response(200, json={"value": [{"id": "C1", "name": "Ana Sample", "folder": {"childCount": 2}},
                                                       {"id": "f", "name": "readme.docx", "file": {"mimeType": "x"}}],
                                             "@odata.nextLink": "https://graph.microsoft.com/v1.0/drives/DRV/root:/Clients/SIJS:/children?$skiptoken=2"})
        if path == "/v1.0/drives/DRV/items/C1/children" and request.method == "GET":
            return httpx.Response(200, json={"value": [{"id": "P", "name": "passport.pdf", "size": 9, "cTag": "c1", "file": {"mimeType": "application/pdf"},
                                                        "lastModifiedDateTime": "2026-09-01T00:00:00Z"},
                                                       {"id": "W", "name": "statement.docx", "size": 9, "cTag": "c2",
                                                        "file": {"mimeType": "application/vnd.openxmlformats-officedocument.wordprocessingml.document"}},
                                                       {"id": "R", "name": "I-485 packet", "folder": {}}]})
        if path == "/v1.0/drives/DRV/items/P/content":
            return httpx.Response(302, headers={"location": "https://download.example/passport"})
        if path == "/v1.0/drives/DRV/items/W/content":
            assert url.params["format"] == "pdf"  # Microsoft converts the Word file
            return httpx.Response(302, headers={"location": "https://download.example/statement"})
        if path.startswith("/v1.0/drives/DRV/items/R:/") and request.method == "PUT":
            return httpx.Response(201, json={"id": "new-" + path.split(":/")[1]})
        return httpx.Response(404)
    return handle


def test_microsoft_365_reads_client_folders_from_a_sharepoint_library(tmp_path):
    calls = []
    ms = Microsoft365({"site": "firm.sharepoint.com:/sites/Clients", "root_path": "Clients/SIJS"}, env=MS_ENV,
                      transport=httpx.MockTransport(graph_server(calls)))
    clients = ms.clients()
    assert [c.name for c in clients] == ["Ana Sample", "Bruno Sample"]  # folders only, both pages
    docs = ms.documents(clients[0])
    assert [(d.name, d.mime) for d in docs] == [("passport.pdf", "application/pdf"), ("statement.pdf", "application/pdf")]
    assert ms.download(clients[0], docs[0]) == b"%PDF-1.4 /passport" and ms.download(clients[0], docs[1]) == b"%PDF-1.4 /statement"
    report = sync.mirror(ms, tmp_path / "clients", only=["C1"])
    lid = sync.local_id(clients[0], "microsoft")
    assert lid.startswith("ana_sample-ms") and sorted(report["clients"][lid]["added"]) == ["passport [P].pdf", "statement [W].pdf"]
    assert sum(1 for c in calls if c.url.host == "login.microsoftonline.com") == 1  # one token, reused


def test_microsoft_365_writes_results_only_when_a_results_folder_is_set(tmp_path):
    packet = tmp_path / "packet.pdf"
    packet.write_bytes(b"%PDF-1.4 packet")
    calls = []
    settings = {"drive_id": "DRV", "root_path": "Clients/SIJS"}
    ms = Microsoft365(settings, env=MS_ENV, transport=httpx.MockTransport(graph_server(calls)))
    assert ms.publish(RemoteClient("C1", "Ana Sample"), [packet], "built")["published"] is False
    ms = Microsoft365(settings | {"results_folder_name": "I-485 packet"}, env=MS_ENV, transport=httpx.MockTransport(graph_server(calls)))
    out = ms.publish(RemoteClient("C1", "Ana Sample"), [packet], "built")
    assert out["folder"] == "R" and out["files"] == ["new-packet.pdf", "new-NOTE.txt"]
    assert not any("/sites/" in str(c.url) for c in calls)  # a drive id skips the site lookup


def test_microsoft_365_without_setup_says_what_is_missing():
    with pytest.raises(GraphError, match="MS_TENANT_ID"):
        Microsoft365({"site": "x", "root_path": "y"}, env={})
    with pytest.raises(GraphError, match="root_path"):
        Microsoft365({"site": "x"}, env=MS_ENV)


@pytest.fixture
def ms_signin(rsa_key):
    nums = rsa_key.public_key().public_numbers()
    jwks = {"keys": [{"kid": "k1", "kty": "RSA", "n": _b64(nums.n.to_bytes(256, "big")), "e": _b64(nums.e.to_bytes(3, "big"))}]}
    return MicrosoftSignIn("https://review.firm.example/auth/callback", env=MS_ENV, transport=httpx.MockTransport(lambda r: httpx.Response(200, json=jwks)))


def ms_token(key, **claims):
    base = {"iss": "https://login.microsoftonline.com/tenant-guid/v2.0", "aud": "app-id", "tid": "tenant-guid", "exp": int(time.time()) + 600,
            "nonce": "n1", "preferred_username": "Jane@Firm.example"}
    head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
    body = _b64(json.dumps(base | claims).encode())
    return f"{head}.{body}.{_b64(key.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"


def test_microsoft_sign_in_accepts_only_the_firms_own_tenant(ms_signin, rsa_key, tmp_path):
    claims = ms_signin.verify(ms_token(rsa_key), "n1")
    assert claims["email"] == "jane@firm.example"  # from preferred_username, lower-cased
    assert "/tenant-guid/oauth2/v2.0/authorize" in ms_signin.authorize_url("s", "n1")
    for bad, why in (({"tid": "other-tenant"}, "firm's Microsoft 365"), ({"iss": "https://login.microsoftonline.com/other/v2.0"}, "not issued"),
                     ({"aud": "someone-else"}, "another app"), ({"preferred_username": None}, "no email")):
        with pytest.raises(SignInError, match=why):
            ms_signin.verify(ms_token(rsa_key, **bad), "n1")
    from review.auth import Accounts

    accounts = Accounts(tmp_path / "users.json")
    accounts.add("jane@firm.example", "Jane Doe", "attorney")
    token, user = staff_session(accounts, claims, "microsoft")
    assert user["role"] == "attorney" and accounts.session_user(token)["must_change"] is False
