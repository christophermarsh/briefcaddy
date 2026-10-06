"""Staff sign-in for the review app with the firm's own accounts -- Google
Workspace or Microsoft 365 (OpenID Connect). The review app's /auth/start
and /auth/callback (review/server.py) use it once it is switched on.

Switching on (docs/integrations.md): schemas/registers/connectors.json names the
provider in "active" -> "sign_in" ("microsoft", "google", or both as a
list), its "sign_in" section holds the Google domain and the redirect URI,
and the secrets are environment variables (GOOGLE_OAUTH_CLIENT_ID and
_SECRET; MS_TENANT_ID, MS_CLIENT_ID and MS_CLIENT_SECRET). The sign-in
screen offers a provider only when all of that is there (providers()).

The flow is the same for both: the review app sends the person to the
provider (authorize_url), the provider sends them back with a code, the code
is exchanged for an ID token (exchange), and the token is checked here
(verify): the provider's signature (RS256, its published keys), issuer,
audience, expiry, the nonce, and that the account belongs to the firm --
the Workspace domain (Google) or the firm's tenant (Microsoft).

A valid login is not enough: the email must also be an active account on the
review app's staff list (review/auth.py), which keeps the paralegal/attorney
roles in one place. The provider decides who the person is; the firm decides
what they may do.
"""

from __future__ import annotations

import base64
import json
import os
import sys
import time
from pathlib import Path
from typing import Any
from urllib.parse import urlencode

import httpx
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)
import schema_path

# the connectors' settings (I485_CONNECTORS: another copy, for the end-to-end tests)
CONNECTORS = Path(os.environ.get("I485_CONNECTORS") or schema_path.path("register", "connectors"))
LABELS = {"microsoft": "Sign in with Microsoft", "google": "Sign in with Google"}  # the order on the sign-in screen


class SignInError(ValueError):
    pass


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


class OIDCSignIn:
    provider = "oidc"
    authorize_endpoint = token_endpoint = keys_endpoint = ""
    issuers: tuple[str, ...] = ()
    scope = "openid email profile"

    def __init__(self, client_id: str, client_secret: str, redirect_uri: str, transport: httpx.BaseTransport | None = None):
        if not (client_id and client_secret):
            raise SignInError(f"{self.provider} sign-in isn't set up: the client id and secret are missing (docs/integrations.md).")
        self.client_id, self.client_secret, self.redirect_uri = client_id, client_secret, redirect_uri
        self.http = httpx.Client(transport=transport, timeout=20)
        self._keys: tuple[dict[str, Any], float] | None = None

    def extra_params(self) -> dict[str, str]:
        return {}

    def authorize_url(self, state: str, nonce: str) -> str:
        return self.authorize_endpoint + "?" + urlencode({"client_id": self.client_id, "redirect_uri": self.redirect_uri, "response_type": "code",
                                                          "scope": self.scope, "state": state, "nonce": nonce, "prompt": "select_account",
                                                          **self.extra_params()})

    def exchange(self, code: str) -> str:
        r = self.http.post(self.token_endpoint, data={"code": code, "client_id": self.client_id, "client_secret": self.client_secret,
                                                      "redirect_uri": self.redirect_uri, "grant_type": "authorization_code", "scope": self.scope})
        if r.status_code != 200 or "id_token" not in r.json():
            raise SignInError(f"{self.provider} didn't confirm the sign-in. Try again.")
        return r.json()["id_token"]

    def keys(self) -> dict[str, Any]:
        if not self._keys or self._keys[1] < time.time():
            self._keys = ({k["kid"]: k for k in self.http.get(self.keys_endpoint).json()["keys"]}, time.time() + 3600)
        return self._keys[0]

    def firm_checks(self, claims: dict[str, Any]) -> list[tuple[bool, str]]:
        return []

    def email(self, claims: dict[str, Any]) -> str:
        return str(claims.get("email") or "")

    def verify(self, id_token: str, nonce: str, now: float | None = None) -> dict[str, Any]:
        """The ID token's claims (with 'email' set), or SignInError saying what was wrong."""
        from cryptography.exceptions import InvalidSignature
        from cryptography.hazmat.primitives import hashes
        from cryptography.hazmat.primitives.asymmetric import padding, rsa

        try:
            head_b64, body_b64, sig_b64 = id_token.split(".")
            header, claims = json.loads(_unb64(head_b64)), json.loads(_unb64(body_b64))
        except ValueError:
            raise SignInError("That sign-in token is malformed.") from None
        if header.get("alg") != "RS256":
            raise SignInError("Unexpected token signature type.")
        jwk = self.keys().get(header.get("kid"))
        if jwk is None:
            raise SignInError(f"Signed with a key {self.provider} doesn't list.")
        public = rsa.RSAPublicNumbers(int.from_bytes(_unb64(jwk["e"]), "big"), int.from_bytes(_unb64(jwk["n"]), "big")).public_key()
        try:
            public.verify(_unb64(sig_b64), f"{head_b64}.{body_b64}".encode(), padding.PKCS1v15(), hashes.SHA256())
        except InvalidSignature:
            raise SignInError(f"The sign-in token's signature is not {self.provider}'s.") from None
        now = now or time.time()
        checks = [(claims.get("iss") in self.issuers, f"not issued by {self.provider}"), (claims.get("aud") == self.client_id, "issued for another app"),
                  (int(claims.get("exp", 0)) > now, "expired"), (claims.get("nonce") == nonce, "replayed or mixed up (nonce)"),
                  *self.firm_checks(claims), (bool(self.email(claims)), "no email address in the account")]
        for ok, why in checks:
            if not ok:
                raise SignInError(f"Sign-in refused: {why}.")
        return claims | {"email": self.email(claims).lower()}


class GoogleSignIn(OIDCSignIn):
    provider = "Google"
    authorize_endpoint = "https://accounts.google.com/o/oauth2/v2/auth"
    token_endpoint = "https://oauth2.googleapis.com/token"
    keys_endpoint = "https://www.googleapis.com/oauth2/v3/certs"
    issuers = ("https://accounts.google.com", "accounts.google.com")

    def __init__(self, domain: str, redirect_uri: str, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None):
        env = dict(os.environ if env is None else env)
        self.domain = domain.lower()
        super().__init__(env.get("GOOGLE_OAUTH_CLIENT_ID", ""), firmsecrets.get("google.signin_secret", env=env) or "", redirect_uri, transport)

    def extra_params(self) -> dict[str, str]:
        return {"hd": self.domain}

    def firm_checks(self, claims):
        return [(claims.get("email_verified") is True, "email not verified by Google"),
                (str(claims.get("hd", "")).lower() == self.domain, f"not a {self.domain} account")]


class MicrosoftSignIn(OIDCSignIn):
    """Microsoft Entra ID (Microsoft 365). Only the firm's own tenant: the
    issuer and the token's tenant id must both be the firm's."""
    provider = "Microsoft"

    def __init__(self, redirect_uri: str, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None):
        env = dict(os.environ if env is None else env)
        self.tenant = env.get("MS_TENANT_ID", "")
        if not self.tenant:
            raise SignInError("Microsoft sign-in isn't set up: MS_TENANT_ID is missing (docs/integrations.md).")
        base = f"https://login.microsoftonline.com/{self.tenant}"
        self.authorize_endpoint, self.token_endpoint = f"{base}/oauth2/v2.0/authorize", f"{base}/oauth2/v2.0/token"
        self.keys_endpoint, self.issuers = f"{base}/discovery/v2.0/keys", (f"{base}/v2.0",)
        super().__init__(env.get("MS_CLIENT_ID", ""), firmsecrets.get("microsoft.client_secret", env=env) or "", redirect_uri, transport)

    def extra_params(self) -> dict[str, str]:
        return {"response_mode": "query"}

    def firm_checks(self, claims):
        return [(claims.get("tid") == self.tenant, "not an account of the firm's Microsoft 365")]

    def email(self, claims):
        return str(claims.get("email") or claims.get("preferred_username") or "")


def config(path: Path | None = None) -> dict[str, Any]:
    """connectors.json's sign-in part: {"active": [...providers switched on], "google_domain", "redirect_uri"}."""
    path = path or CONNECTORS
    data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    active = (data.get("active") or {}).get("sign_in") or "local"
    return (data.get("sign_in") or {}) | {"active": [p for p in ([active] if isinstance(active, str) else active) if p in LABELS]}


def provider_for(name: str, env: dict[str, str] | None = None, transport: httpx.BaseTransport | None = None,
                 cfg: dict[str, Any] | None = None) -> OIDCSignIn:
    """The verifier for one provider, as switched on in connectors.json; SignInError when it isn't (or a secret, the
    domain or the redirect URI is missing). The redirect URI is always connectors.json's, the one registered with the
    provider: never one built from the request's Host header, which the browser (or anyone) writes."""
    cfg = config() if cfg is None else cfg
    why = _not_ready(name, dict(os.environ if env is None else env), cfg)
    if why:
        raise SignInError(why)
    if name == "google":
        return GoogleSignIn(cfg["google_domain"], cfg["redirect_uri"], env=env, transport=transport)
    return MicrosoftSignIn(cfg["redirect_uri"], env=env, transport=transport)


NEEDS = {"google": (("GOOGLE_OAUTH_CLIENT_ID",), ("google.signin_secret",)), "microsoft": (("MS_TENANT_ID", "MS_CLIENT_ID"), ("microsoft.client_secret",))}  # (ids in the environment, secrets by name)


def _not_ready(name: str, env: dict[str, str], cfg: dict[str, Any]) -> str | None:
    if name not in cfg["active"]:
        return f"{name} sign-in isn't switched on."
    if name == "google" and not cfg.get("google_domain"):
        return "Google sign-in isn't set up: the firm's Workspace domain is missing."
    if not cfg.get("redirect_uri"):
        return (f"{name} sign-in isn't set up: connectors.json has no sign_in.redirect_uri (the review app's https address "
                f"followed by /auth/callback, exactly as registered with {name}).")
    ids, secrets_needed = NEEDS[name]
    missing = [k for k in ids if not env.get(k)] + [firmsecrets.BY_NAME[n].env[0] for n in secrets_needed if not firmsecrets.is_set(n, env=env)]
    return f"{name} sign-in isn't set up: {', '.join(missing)} missing (docs/integrations.md)." if missing else None


_said: set[str] = set()  # each reason a switched-on provider isn't offered, said once on the console


def providers(env: dict[str, str] | None = None, cfg: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """The buttons the sign-in screen shows: only providers switched on and fully set up. A provider switched on but
    not ready (no redirect URI, a missing secret) is left out, and the console says why once."""
    env, cfg = dict(os.environ if env is None else env), (config() if cfg is None else cfg)
    out = []
    for name, label in LABELS.items():
        why = _not_ready(name, env, cfg)
        if why is None:
            out.append({"id": name, "label": label})
        elif name in cfg["active"] and why not in _said:
            _said.add(why)
            print(f"Sign-in: {why} Its button is not shown.", file=sys.stderr)
    return out


def staff_session(accounts, claims: dict[str, Any], provider: str = "google") -> tuple[str, dict[str, Any]]:
    """A review-app session for a verified login -- only for an email already
    on the staff list and active (roles stay in review/auth.py)."""
    return accounts.session_for(str(claims["email"]), how=provider)
