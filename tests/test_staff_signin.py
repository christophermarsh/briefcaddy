"""Staff sign-in with the firm's Microsoft 365 or Google account, through the review app's own routes
(/auth/start, /auth/callback in src/review/server.py over src/connectors/signin.py). The provider is faked
the way tests/test_connectors.py fakes it: a real RSA key signs the ID token, an httpx MockTransport plays
the provider's token and key endpoints. Made-up staff only."""

import base64
import http.client
import json
import threading
import time
from urllib.parse import parse_qs, urlencode, urlparse

import httpx
import pytest
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.asymmetric import padding, rsa

from connectors import signin
from connectors.signin import GoogleSignIn
from review.auth import WRONG, Accounts
from review.server import ReviewApp, make_handler, serve
from test_review import _REPO, TEMPLATE, client  # noqa: F401 -- the review app's test client
import schema_path

ENV = {"GOOGLE_OAUTH_CLIENT_ID": "app.apps.googleusercontent.com", "GOOGLE_OAUTH_CLIENT_SECRET": "s"}
REDIRECT = "https://review.firm.example/auth/callback"  # connectors.json's, as registered with the provider


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


@pytest.fixture(scope="module")
def key():
    return rsa.generate_private_key(public_exponent=65537, key_size=2048)


class FakeGoogle:
    """The provider: whoever the test says signs in (email), with the nonce the review app sent."""

    def __init__(self, key):
        self.key, self.email, self.nonces, self.claims = key, "jane@firm.example", {}, {}

    def token(self, nonce: str) -> str:
        claims = {"iss": "https://accounts.google.com", "aud": ENV["GOOGLE_OAUTH_CLIENT_ID"], "exp": int(time.time()) + 600, "nonce": nonce,
                  "email": self.email, "email_verified": True, "hd": "firm.example"} | self.claims
        head = _b64(json.dumps({"alg": "RS256", "kid": "k1"}).encode())
        body = _b64(json.dumps(claims).encode())
        return f"{head}.{body}.{_b64(self.key.sign(f'{head}.{body}'.encode(), padding.PKCS1v15(), hashes.SHA256()))}"

    def handle(self, request: httpx.Request) -> httpx.Response:
        if request.url.host == "oauth2.googleapis.com":  # the code -> the ID token
            code = parse_qs(request.content.decode())["code"][0]
            return httpx.Response(200, json={"id_token": self.token(self.nonces[code])})
        nums = self.key.public_key().public_numbers()
        return httpx.Response(200, json={"keys": [{"kid": "k1", "kty": "RSA", "n": _b64(nums.n.to_bytes(256, "big")), "e": _b64(nums.e.to_bytes(3, "big"))}]})


@pytest.fixture
def staff(client, key, tmp_path):  # noqa: F811
    accounts = Accounts(tmp_path / "staff.json")
    accounts.add("jane@firm.example", "Jane Doe", "paralegal")  # never chose a password: the firm's Google account signs her in
    accounts.add("off@firm.example", "Old Staff", "paralegal")
    accounts.update("off@firm.example", active=False)
    google = FakeGoogle(key)

    def factory(name):
        if name != "google":
            raise signin.SignInError("not switched on")
        return GoogleSignIn("firm.example", REDIRECT, env=ENV, transport=httpx.MockTransport(google.handle))

    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=accounts, sign_in_factory=factory)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield {"port": port, "google": google, "accounts": accounts, "app": app}
    httpd.shutdown()


def _get(port, path, cookie=None):
    """(status, headers as a list, body) without following redirects."""
    conn = http.client.HTTPConnection("127.0.0.1", port, timeout=10)
    conn.request("GET", path, headers={"Cookie": cookie} if cookie else {})
    r = conn.getresponse()
    out = r.status, r.getheaders(), r.read()
    conn.close()
    return out


def _header(headers, name):
    return [v for k, v in headers if k.lower() == name.lower()]


def _start(s):
    """'Sign in with Google': the provider's address, and the browser's state cookie."""
    status, headers, _ = _get(s["port"], "/auth/start?provider=google")
    assert status == 302
    where = urlparse(_header(headers, "Location")[0])
    assert where.netloc == "accounts.google.com"
    q = {k: v[0] for k, v in parse_qs(where.query).items()}
    assert q["redirect_uri"] == REDIRECT and q["hd"] == "firm.example"  # never built from the request's Host header
    [cookie] = _header(headers, "Set-Cookie")
    assert cookie.startswith(f"review_signin={q['state']};") and "HttpOnly" in cookie and "SameSite=Lax" in cookie
    return q["state"], q["nonce"], cookie.split(";")[0]


def _back(s, state, nonce, cookie, code="code-1"):
    """The provider sends the browser back with a code."""
    s["google"].nonces[code] = nonce
    status, headers, _ = _get(s["port"], "/auth/callback?" + urlencode({"state": state, "code": code}), cookie)
    assert status == 302
    return _header(headers, "Location")[0], [c for c in _header(headers, "Set-Cookie") if c.startswith("review_session=")]


def test_a_staff_member_signs_in_with_the_firms_google_account(staff):
    state, nonce, cookie = _start(staff)
    where, session = _back(staff, state, nonce, cookie)
    assert where == "/" and session and "HttpOnly" in session[0] and "SameSite=Strict" in session[0]
    status, _, body = _get(staff["port"], "/api/me", session[0].split(";")[0])
    assert status == 200 and json.loads(body)["user"]["name"] == "Jane Doe" and not json.loads(body)["user"]["must_change"]
    assert _get(staff["port"], "/api/clients", session[0].split(";")[0])[0] == 200
    log = [json.loads(x) for x in staff["accounts"].log_path.read_text().splitlines()]
    assert log[-1]["event"] == "signed_in" and log[-1]["how"] == "google"
    # the state is good once: the same answer again doesn't sign anyone in
    assert _back(staff, state, nonce, cookie, "code-2") == ("/?signin=expired", [])


def test_a_valid_account_not_on_the_staff_list_gets_the_words_of_a_wrong_password(staff):
    for email in ("stranger@firm.example", "off@firm.example"):  # not on the list; turned off
        staff["google"].email = email
        state, nonce, cookie = _start(staff)
        assert _back(staff, state, nonce, cookie) == ("/?signin=refused", [])
    status, _, body = _get(staff["port"], "/api/me?signin=refused")
    assert json.loads(body)["sign_in_error"] == WRONG
    assert "sign_in_failed" in staff["accounts"].log_path.read_text()


def test_a_sign_in_must_come_back_to_the_browser_that_started_it(staff):
    state, nonce, cookie = _start(staff)
    assert _back(staff, state, nonce, None) == ("/?signin=expired", [])  # a link planted by someone else: no state cookie
    assert _back(staff, state, nonce, "review_signin=someone-elses") == ("/?signin=expired", [])
    state, nonce, cookie = _start(staff)
    assert _back(staff, state, "another-nonce", cookie) == ("/?signin=failed", [])  # the token wasn't made for this sign-in
    staff["google"].claims = {"hd": "gmail.com"}  # a personal account, not the firm's
    state, nonce, cookie = _start(staff)
    assert _back(staff, state, nonce, cookie) == ("/?signin=failed", [])
    state, _, cookie = _start(staff)
    status, headers, _ = _get(staff["port"], "/auth/callback?" + urlencode({"state": state, "error": "access_denied"}), cookie)
    assert _header(headers, "Location") == ["/?signin=cancelled"]


def test_only_a_provider_that_is_set_up_is_offered(staff, tmp_path, monkeypatch):
    assert _get(staff["port"], "/auth/start?provider=microsoft")[0] == 404  # the fake knows Google only
    assert "sign_in_with" not in json.loads(_get(staff["port"], "/api/me")[2])  # the shipped connectors.json: passwords only
    settings = tmp_path / "connectors.json"
    monkeypatch.setattr(signin, "CONNECTORS", settings)
    for k, v in (ENV | {"MS_TENANT_ID": "t", "MS_CLIENT_ID": "c"}).items():
        monkeypatch.setenv(k, v)
    monkeypatch.delenv("MS_CLIENT_SECRET", raising=False)
    # switched on, every secret there, but no redirect URI: not offered (the Host header is never used instead)
    settings.write_text(json.dumps({"active": {"sign_in": ["google", "microsoft"]}, "sign_in": {"google_domain": "firm.example", "redirect_uri": None}}))
    assert "sign_in_with" not in json.loads(_get(staff["port"], "/api/me")[2])
    settings.write_text(json.dumps({"active": {"sign_in": ["google", "microsoft"]}, "sign_in": {"google_domain": "firm.example", "redirect_uri": REDIRECT}}))
    me = json.loads(_get(staff["port"], "/api/me")[2])
    assert me["sign_in_with"] == [{"id": "google", "label": "Sign in with Google"}]  # Microsoft's secret is missing
    monkeypatch.setenv("MS_CLIENT_SECRET", "x")
    me = json.loads(_get(staff["port"], "/api/me")[2])
    assert [p["label"] for p in me["sign_in_with"]] == ["Sign in with Microsoft", "Sign in with Google"]


def test_the_redirect_uri_is_connectors_jsons_or_the_provider_is_not_ready(tmp_path, capsys, monkeypatch):
    monkeypatch.setattr(signin, "_said", set())  # each reason is said once per process
    cfg = {"active": ["google"], "google_domain": "firm.example", "redirect_uri": REDIRECT}
    google = signin.provider_for("google", env=ENV, cfg=cfg)
    assert "redirect_uri=https%3A%2F%2Freview.firm.example%2Fauth%2Fcallback" in google.authorize_url("s", "n")
    with pytest.raises(signin.SignInError, match="switched on"):
        signin.provider_for("microsoft", env=ENV, cfg=cfg)
    with pytest.raises(signin.SignInError, match="redirect_uri"):
        signin.provider_for("google", env=ENV, cfg=cfg | {"redirect_uri": None})
    assert signin.providers(env=ENV, cfg=cfg | {"redirect_uri": ""}) == []
    assert "no sign_in.redirect_uri" in capsys.readouterr().err  # the console says why the button is missing
    assert signin.config(tmp_path / "none.json")["active"] == []  # no file: passwords only


def test_sign_ins_under_way_are_capped_and_starts_are_limited_per_address(staff, monkeypatch):
    import review.server as server

    app = staff["app"]
    monkeypatch.setattr(server, "MAX_PENDING", 5)
    states = [app.sign_in_start("google")[1] for _ in range(8)]
    assert list(app._sign_ins) == states[-5:]  # the oldest forgotten, never more than the cap
    with pytest.raises(LookupError):
        app.sign_in_finish(states[0], "code")  # a forgotten one says "try again"
    monkeypatch.setattr(server, "ATTEMPTS", server.ATTEMPTS | {"/auth/start": 3})
    assert [_get(staff["port"], "/auth/start?provider=google")[0] for _ in range(4)] == [302, 302, 302, 429]
    assert len(app._sign_ins) <= 5
