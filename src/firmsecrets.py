"""Keys and secrets on the firm's machine: one reader, by name (brief R4).

    get("smtp.password")          the value, or None when nobody has set it

Every secret the product uses is read here and nowhere else: the environment wins when the firm sets it there (and the Settings page says so), else the vault
(data/clio/secrets.enc, with its key: it began as Clio's, which is why it sits in data/clio/; it is the one vault, and it is in every backup), else, for the two secrets that
must be readable before the vault is (the staff authenticator key, the backups' passphrase), the file named below. A secret is never printed, logged, shown on a screen
or written to the ledger, not even masked or truncated: the page says whether one is set, where it lives, when it was last changed and by whom, never what it is.
tests/test_secrets.py reads src/ for the old ways (an environment variable named here read anywhere else, a key file opened by name) and fails on one.

Not here, on purpose: a client's sign-in link, a session, a calendar address, a recovery or setup code. Those are random values the product makes and keeps only as
hashes (the staff accounts' file, the portal's table, calendar_feeds.json): there is nothing to read back, so nothing to rotate. The portal has no signing key.

The rotation record, data/secrets_log.json (0600): each change (the name, when, by whom, how: never the value) and the cadence the attorney chose for each kind.
tools/rotate_secret.py writes a change; the Settings page, "Keys and secrets", reads the record. No cadence is guessed: a kind ships with "not set".
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass
from datetime import date, timedelta
from pathlib import Path
from typing import Any, Mapping

import clock
import secretbox

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
LOG = "secrets_log.json"
CADENCES = {"monthly": 31, "quarterly": 92, "yearly": 366}  # the register's own days (src/maintenance.py DAYS)
NOT_SET = "not set"
VAULT_DIR = "clio"  # the vault's folder under the data folder, and the vault's key file beside it: data/clio/secrets.enc, data/clio/vault.key
VAULT_KEY_FILE = "vault.key"


@dataclass(frozen=True)
class Secret:
    name: str
    kind: str
    label: str
    env: tuple[str, ...] = ()  # the environment variables that hold it (the first set one wins)
    store: str = "vault"  # where it lives when the environment does not hold it: vault, or file
    vault_key: str = ""  # its key in the vault
    keys: bool = False  # a list of keys: the first encrypts, the rest still read what they wrote (secretbox)
    app: bool = False  # written by the product itself (a token Clio sent), never typed by the firm
    file: str = ""  # a file beside the data folder or the installer's folder (store "file")
    file_env: str = ""  # an environment variable that names a file holding it
    base: str = "data"  # "data" or "install"
    reads: str = ""  # who reads it, for the inventory and the page


SECRETS: tuple[Secret, ...] = (
    Secret("clio.client_secret", "clio", "The Clio app's client secret", ("CLIO_CLIENT_SECRET",), vault_key="client_secret", reads="src/connectors/clio.py"),
    Secret("clio.access_token", "clio", "Clio's access token (the product asked Clio for it)", vault_key="access_token", app=True, reads="src/connectors/clio.py"),
    Secret("clio.refresh_token", "clio", "Clio's refresh token (the product asked Clio for it)", vault_key="refresh_token", app=True, reads="src/connectors/clio.py"),
    Secret("clio.vault_key", "vault", "The vault's key (what opens every saved secret)", ("CLIO_TOKEN_KEY",), store="file", keys=True, file=f"{VAULT_DIR}/{VAULT_KEY_FILE}", reads="src/connectors/clio.py Vault"),
    Secret("smtp.password", "mail", "The mail server's password", ("SMTP_PASSWORD",), vault_key="smtp.password", reads="src/portal/notify.py"),
    Secret("twilio.auth_token", "text", "The text-message provider's token", ("TWILIO_AUTH_TOKEN",), vault_key="twilio.auth_token", reads="src/portal/notify.py"),
    Secret("uscis.client_secret", "uscis", "USCIS case status: the client secret", ("USCIS_CASE_STATUS_CLIENT_SECRET",), vault_key="uscis.client_secret", reads="src/case_status.py"),
    Secret("google.signin_secret", "google", "Sign in with Google: the client secret", ("GOOGLE_OAUTH_CLIENT_SECRET",), vault_key="google.signin_secret", reads="src/connectors/signin.py"),
    Secret("gdrive.service_account", "google", "Google Drive: the service account's key", store="vault", vault_key="gdrive.service_account", file_env="GOOGLE_SERVICE_ACCOUNT_FILE", reads="src/connectors/gdrive.py"),
    Secret("microsoft.client_secret", "microsoft", "Microsoft 365: the client secret (sign-in and the connector)", ("MS_CLIENT_SECRET",), vault_key="microsoft.client_secret",
           reads="src/connectors/signin.py, src/connectors/msgraph.py"),
    Secret("filevine.pat", "filevine", "Filevine: the personal access token", ("FILEVINE_PAT",), vault_key="filevine.pat", reads="src/connectors/filevine.py"),
    Secret("filevine.client_secret", "filevine", "Filevine: the client secret", ("FILEVINE_CLIENT_SECRET",), vault_key="filevine.client_secret", reads="src/connectors/filevine.py"),
    Secret("backups.passphrase", "backups", "The backups' passphrase", ("I485_BACKUP_PASSPHRASE",), store="file", keys=False, file="backup_passphrase.txt", file_env="I485_BACKUP_PASSPHRASE_FILE", base="install",
           reads="src/backups.py, tools/backup.py, tools/restore.py"),
    Secret("accounts.totp_key", "staff", "The key of the staff members' authenticator secrets", ("I485_TOTP_KEY",), store="file", keys=True, file="review_users_totp.key", reads="src/review/auth.py"),
)
BY_NAME = {s.name: s for s in SECRETS}

# one register item and one rotation cadence for each kind: what it is, and what rotating it means
KINDS: dict[str, dict[str, str]] = {
    "clio": {"label": "Clio", "how": "Paste the new client secret from Clio's developer page; the tokens are Clio's and come back when an attorney connects again."},
    "vault": {"label": "The vault's key", "how": "A new key is made, every saved secret is saved again under it, and the old key is dropped."},
    "mail": {"label": "Mail", "how": "Make a new password at the mail provider and give it to the tool."},
    "text": {"label": "Text messages", "how": "Make a new token at the text provider and give it to the tool."},
    "uscis": {"label": "USCIS case status", "how": "Ask USCIS for a new client secret and give it to the tool."},
    "google": {"label": "Google", "how": "Make a new client secret (or service account key) at Google and give it to the tool."},
    "microsoft": {"label": "Microsoft 365", "how": "Make a new client secret in the Microsoft app registration and give it to the tool."},
    "filevine": {"label": "Filevine", "how": "Make a new token and client secret in Filevine and give them to the tool."},
    "backups": {"label": "The backups' passphrase", "how": "A new passphrase goes first for new backups; the old ones stay listed so older backups still open."},
    "staff": {"label": "The staff sign-in secrets", "how": "A new key is made and put first, every authenticator secret is saved again under it, and the old key is dropped."},
}


# -- where it is ---------------------------------------------------------------------------------------------------------------------------------


def _env(env: Mapping[str, str] | None) -> Mapping[str, str]:
    return os.environ if env is None else env


def data_folder(data_root: str | Path | None = None) -> Path:
    return Path(data_root) if data_root else DATA


def vault_folder(data_root: str | Path | None = None) -> Path:
    return data_folder(data_root) / VAULT_DIR


def _file(spec: Secret, data_root: str | Path | None, path: str | Path | None) -> Path:
    if path is not None:
        return Path(path)
    return (REPO / "install" if spec.base == "install" else data_folder(data_root)) / spec.file


def _vault(data_root: str | Path | None, env: Mapping[str, str] | None):
    from connectors import clio  # the vault's own class: storage only, its key comes from vault_cipher() below

    return clio.Vault(Path(data_root) if data_root else None, dict(_env(env)))


def vault_cipher(directory: Path, env: Mapping[str, str] | None = None):
    """The vault's cipher: the environment's key (or keys, the first encrypting), else the key file beside the vault, made once owner-only. ValueError when not a key."""
    text = (_env(env).get("CLIO_TOKEN_KEY") or "").strip() or secretbox.key_file(Path(directory) / VAULT_KEY_FILE)
    return secretbox.cipher(text)


def _read_file(p: Path) -> str | None:
    try:
        return p.read_text(encoding="utf-8").strip() or None
    except OSError:
        return None


def _locate(spec: Secret, env: Mapping[str, str], data_root: str | Path | None, path: str | Path | None, create: bool, strict: bool = False,
            explicit: bool = False) -> tuple[str | None, str | None]:
    """(the value, where it was found): "environment", "vault" or "file"; (None, None) when nobody has set it. explicit: the file in `path` was named by the person
    running the tool (--passphrase-file), so it is read before the environment."""
    if explicit and path is not None:
        v = _read_file(Path(path))
        if v:
            return v, "file"
    for name in spec.env:
        v = (env.get(name) or "").strip()
        if v:
            return v, "environment"
    named = (env.get(spec.file_env) or "").strip() if spec.file_env else ""
    if named:
        v = _read_file(Path(named))
        if v:
            return v, "file"
    if spec.store == "vault" and spec.vault_key:
        try:
            v = _vault(data_root, env).read().get(spec.vault_key)
        except Exception:  # noqa: BLE001 -- a vault that cannot be opened holds nothing the caller can use (strict: the Clio screens say why, so it is raised)
            if strict:
                raise
            v = None
        return (str(v), "vault") if v else (None, None)
    if spec.store == "file":
        p = _file(spec, data_root, path)
        if create and spec.keys and not p.exists():
            secretbox.key_file(p)
        v = _read_file(p)
        return (v, "file") if v else (None, None)
    return None, None


def get(name: str, *, env: Mapping[str, str] | None = None, data_root: str | Path | None = None, path: str | Path | None = None, create: bool = False, strict: bool = False,
        explicit: bool = False) -> str | None:
    """The secret called `name`, or None when it is not set. The environment wins over the vault and the file. path: where its key file is when it is not beside the
    data folder (the staff accounts' file may be elsewhere); create: make the key file once, owner-only, when it is a key and there is none; strict: a vault that cannot
    be opened raises (the Clio screens tell the person why), instead of reading as "not set"."""
    spec = BY_NAME[name]
    value, _where = _locate(spec, _env(env), data_root, path, create, strict, explicit)
    if value and spec.name == "backups.passphrase":
        return value.splitlines()[0].strip()  # the first line encrypts; the lines after it are the older passphrases (get_all)
    return value


def get_all(name: str, **kw: Any) -> list[str]:
    """Every value listed for a secret that keeps old ones (the backups' passphrases, a list of keys): the first is the one in use."""
    spec = BY_NAME[name]
    value, _where = _locate(spec, _env(kw.get("env")), kw.get("data_root"), kw.get("path"), False, False, bool(kw.get("explicit")))
    if not value:
        return []
    return [v.strip() for v in value.replace(",", "\n").splitlines() if v.strip()] if spec.keys else [v.strip() for v in value.splitlines() if v.strip()]


def where(name: str, *, env: Mapping[str, str] | None = None, data_root: str | Path | None = None, path: str | Path | None = None) -> str | None:
    """"environment", "vault" or "file": where the secret is held now, or None when it is not set. Never the value."""
    return _locate(BY_NAME[name], _env(env), data_root, path, False)[1]


def in_environment(name: str, env: Mapping[str, str] | None = None) -> bool:
    """Whether the server's environment holds this secret (and so wins over the vault and the file)."""
    return any((_env(env).get(n) or "").strip() for n in BY_NAME[name].env)


def totp_key_path(accounts_file: str | Path) -> Path:
    """Where the staff accounts' authenticator key is when the environment does not hold it: beside the accounts' file, named for it."""
    p = Path(accounts_file)
    return p.with_name(p.stem + "_totp.key")


def is_set(name: str, **kw: Any) -> bool:
    return where(name, **kw) is not None


def configured(name: str, *, env: Mapping[str, str] | None = None, data_root: str | Path | None = None) -> bool:
    """Whether the server has been given this secret: the environment names it (or the file that holds it), or the vault holds it. The file is not opened (the page for IT
    says "the server has its keys"; a file that cannot be read is found out when it is used)."""
    spec, e = BY_NAME[name], _env(env)
    if any((e.get(n) or "").strip() for n in spec.env) or (spec.file_env and (e.get(spec.file_env) or "").strip()):
        return True
    return is_set(name, env=env, data_root=data_root)


def put(name: str, value: str, by: str, *, env: Mapping[str, str] | None = None, data_root: str | Path | None = None, how: str = "set") -> None:
    """Saves a secret in the vault (the Clio tokens, and what the tool is given). Records the change (never the value). The environment still wins when it holds the secret."""
    spec = BY_NAME[name]
    if spec.store != "vault" or not spec.vault_key:
        raise ValueError(f"{name} is not kept in the vault")
    if not str(value or "").strip():
        raise ValueError("a secret cannot be empty")
    _vault(data_root, env).update(**{spec.vault_key: str(value).strip()})
    record(name, by, how, data_root=data_root)


def passphrase_file(env: Mapping[str, str] | None = None) -> Path:
    """The file the backups' passphrases are kept in: the one the environment names, else the installer's own (install/backup_passphrase.txt)."""
    spec = BY_NAME["backups.passphrase"]
    named = (_env(env).get(spec.file_env) or "").strip()
    return Path(named) if named else _file(spec, None, None)


def put_passphrase(value: str, by: str, *, env: Mapping[str, str] | None = None) -> Path:
    """A new backups passphrase goes first (new backups use it); the ones already listed stay after it, so every older backup still opens. The file is written whole, owner-only."""
    value = str(value or "").strip()
    if not value or "\n" in value:
        raise ValueError("a passphrase is one line")
    path = passphrase_file(env)
    held = [v for v in get_all("backups.passphrase", env={}, path=path, explicit=True) if v != value]
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write("\n".join([value] + held) + "\n")
    os.replace(tmp, path)
    record("backups.passphrase", by, "rotated")
    return path


# -- the record of changes and the cadence --------------------------------------------------------------------------------------------------------


def log_path(data_root: str | Path | None = None) -> Path:
    return data_folder(data_root) / LOG


def read_log(data_root: str | Path | None = None) -> dict[str, Any]:
    try:
        data = json.loads(log_path(data_root).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"cadence": dict(data.get("cadence") or {}), "changes": list(data.get("changes") or [])}


def _write_log(data: dict[str, Any], data_root: str | Path | None) -> None:
    path = log_path(data_root)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(data, indent=1, ensure_ascii=False) + "\n")
    os.replace(tmp, path)


WORDS = {"set": "Saved", "rotated": "Changed", "recorded": "Recorded a change of", "migrated": "Moved into the vault"}


def record(name: str, by: str, how: str, *, data_root: str | Path | None = None) -> dict[str, Any]:
    """One change on record: the secret's name, when, by whom, how. Never the value. Also a ledger row (kind secrets) in words."""
    import events

    spec = BY_NAME[name]
    entry = {"name": name, "at": clock.stamp("seconds"), "by": str(by or "").strip() or "someone", "how": how}
    data = read_log(data_root)
    data["changes"].append(entry)
    _write_log(data, data_root)
    events.record("secrets", how, f"{WORDS.get(how, 'Changed')} {'the' + spec.label[3:] if spec.label.startswith('The ') else spec.label}", home=data_folder(data_root), who=entry["by"], via="tool")
    return entry


def set_cadence(kind: str, cadence: str, by: str, *, data_root: str | Path | None = None) -> None:
    """The attorney's choice for how often a kind is rotated: monthly, quarterly, yearly or "not set". Counting starts from the day it is chosen when nothing was changed since."""
    import events

    if kind not in KINDS:
        raise ValueError(f"unknown kind {kind!r}")
    if cadence != NOT_SET and cadence not in CADENCES:
        raise ValueError(f"the cadence must be one of {', '.join(CADENCES)} or '{NOT_SET}'")
    data = read_log(data_root)
    data["cadence"][kind] = {"cadence": cadence, "by": by, "at": clock.stamp("seconds")}
    _write_log(data, data_root)
    events.record("secrets", "cadence", f"Chose how often to change the keys for {KINDS[kind]['label']}: {cadence}", home=data_folder(data_root), who=by, via="staff")


# -- what the page and the morning report say ------------------------------------------------------------------------------------------------------


def _day(stamp: Any) -> date | None:
    return clock.local_date(stamp)


def status(today: date | None = None, *, data_root: str | Path | None = None, env: Mapping[str, str] | None = None, key_path: str | Path | None = None) -> list[dict[str, Any]]:
    """One row for each kind of secret, for the Settings page: {"kind", "label", "names": [{"name", "label", "set", "where", "by_firm"}], "in_use", "last_changed" (MM/DD/YYYY or None),
    "by", "cadence", "due_on" (a date or None), "overdue", "how"}. Never a value, never a fragment."""
    today = today or clock.today()
    log = read_log(data_root)
    out = []
    for kind, info in KINDS.items():
        names = []
        for s in (s for s in SECRETS if s.kind == kind):
            found = where(s.name, env=env, data_root=data_root, path=key_path if s.name == "accounts.totp_key" else None)
            names.append({"name": s.name, "label": s.label, "set": found is not None, "where": found or NOT_SET, "by_firm": not s.app})
        mine = [c for c in log["changes"] if BY_NAME.get(c.get("name")) and BY_NAME[c["name"]].kind == kind]
        last = mine[-1] if mine else None
        chosen = log["cadence"].get(kind) or {}
        cadence = chosen.get("cadence") or NOT_SET
        base = max([d for d in (_day(last["at"]) if last else None, _day(chosen.get("at"))) if d], default=None)
        due_on = base + timedelta(days=CADENCES[cadence]) if base and cadence in CADENCES else None
        in_use = any(n["set"] for n in names)
        out.append({"kind": kind, "label": info["label"], "names": names, "in_use": in_use, "last_changed": clock.us_date(last["at"]) if last else None, "last_changed_iso": clock.day(last["at"]) if last else None,
                    "by": last["by"] if last else None,
                    "cadence": cadence, "due_on": due_on, "overdue": bool(in_use and due_on and today >= due_on), "how": info["how"]})
    return out


def overdue(today: date | None = None, **kw: Any) -> list[dict[str, Any]]:
    return [r for r in status(today, **kw) if r["overdue"]]


def morning_line(today: date | None = None, **kw: Any) -> str:
    """One line for the morning report: which kinds are overdue (their names, never a value), or that none is."""
    late = overdue(today, **kw)
    if not late:
        return "Keys and secrets: none overdue for a change."
    return "Keys and secrets overdue for a change: " + ", ".join(f"{r['label']} (due {clock.us_date(r['due_on'].isoformat())})" for r in late) + "."


# -- moving a key out of a plain file (the deployment record) ----------------------------------------------------------------------------------------


def migrate_deployment(data_root: str | Path | None = None, deployment_path: str | Path | None = None, env: Mapping[str, str] | None = None) -> list[str]:
    """A USCIS case-status secret found in deployment.json ("case_status": {"client_secret": ...}) moves into the vault and is taken out of the file, which is rewritten
    owner-only; the ledger says so (never the value). Returns what was done, in words (empty when there was nothing to move). Run when the review app and the overnight run start."""
    import deployment

    path = Path(deployment_path) if deployment_path else deployment.PATH
    try:
        saved = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return []
    block = saved.get("case_status") if isinstance(saved, dict) else None
    if not isinstance(block, dict) or not str(block.get("client_secret") or "").strip():
        return []
    secret = str(block.pop("client_secret")).strip()
    if not _env(env).get("USCIS_CASE_STATUS_CLIENT_SECRET"):  # the environment already holds it: the plain copy is only taken out
        put("uscis.client_secret", secret, "The product", env=env, data_root=data_root, how="migrated")
    else:
        record("uscis.client_secret", "The product", "migrated", data_root=data_root)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    with os.fdopen(fd, "w", encoding="utf-8") as f:
        f.write(json.dumps(saved, indent=2, ensure_ascii=False) + "\n")
    os.replace(tmp, path)
    return ["The USCIS case-status secret was moved out of deployment.json into the vault."]
