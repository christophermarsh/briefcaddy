"""USCIS case status, from USCIS's own Case Status API -- ready for the keys,
quiet without them. Never USCIS's website: only the API USCIS runs for
approved organizations (developer.uscis.gov, "Torch").

What the API is (developer.uscis.gov, read 2026-10-02; the OpenAPI file
https://developer.uscis.gov/sites/default/files/apidoc_specs/swagger_3.yaml
behind https://developer.uscis.gov/api/case-status, version 1.0.0):

  - OAuth 2.0 client credentials: POST grant_type=client_credentials,
    client_id, client_secret (form-encoded) to the access token URL; the
    answer's access_token goes in "Authorization: Bearer ..." and expires
    in 30 minutes ("expires_in": "1799") -- "How to Get Access Tokens with
    Client Credentials".
  - GET {API URL}/{receiptNumber}: 13 characters, "[a-zA-Z]{3}[0-9]{10}" or
    "[a-zA-Z]{3}\\*[0-9]{9}", no dashes. 200: case_status {receiptNumber,
    formType, submittedDate, modifiedDate (not for IOE receipts),
    current_case_status_text_en/_desc_en (and _es), hist_case_status
    [{date, completed_text_en, completed_text_es}]}. 401: token invalid or
    expired. 404: not recognised -- also every receipt of a person protected
    under 8 U.S.C. 1367 (only the USCIS Contact Center answers for them).
    422: badly formed. 429: over the per-second or daily limit. 503:
    unavailable (the sandbox only runs Monday to Friday, 7 am to 8 pm EST).
  - Sandbox: https://api-int.uscis.gov/case-status, token
    https://api-int.uscis.gov/oauth/accesstoken; staging receipt numbers
    only; 1,000 requests a day, 5 a second (one per 200 ms).
  - Production: its URLs and keys come in USCIS's letter once production
    access is granted (a U.S. organization, an affidavit, a demo with a
    "demo_id" request header, 5 days of sandbox traffic) -- the production
    URLs are not public, so they are read from the configuration. The
    API page says 400,000 a day (reset at midnight Eastern) and 10 a second;
    the sandbox page says production limits come with the access. The
    configuration's daily_budget and per_second win.

Keys never live in git: the environment (USCIS_CASE_STATUS_CLIENT_ID,
_CLIENT_SECRET, _ENVIRONMENT "sandbox" | "production", _TOKEN_URL, _API_URL,
_DAILY_BUDGET, _PER_SECOND, _DEMO_ID) or the installation's deployment.json
("case_status": {...}, git-ignored). USCIS's advice is a secrets manager or
environment variables on a back end it controls, never the browser.

Every night (src/overnight.py -> nightly()): the receipts still open on each
case (src/journey.py: no approval, denial or rejection notice in the folder,
and the receipt numbers recorded for online filings) -- never looked at
first, then the longest unchecked, the newest activity first within each --
within the day's budget; each answer saved in the client's case_status.json.
The case timeline shows "USCIS status: ... (checked MM/DD/YYYY)", and when
the status names a notice the folder doesn't have yet (a request for
evidence, an approval, a denial), a step asks the paralegal to get it: the
notice, not the status line, is what the case acts on (due dates are only
on the notice). Without keys it does nothing, and Keeping current says so.

USCIS publishes no list of its status texts; kind() maps the words of the
text (two of them are in the API's own examples, "Case Was Approved" and
"Case Approval Was Affirmed"). A text it doesn't recognise is shown as USCIS
wrote it, with no step.
"""

from __future__ import annotations

import json
import os
import re
import time
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import clock as firm_clock  # the firm's clock (nightly() has a "clock" parameter of its own)
import firmsecrets  # keys and secrets are read by name (src/firmsecrets.py)

REPO = Path(__file__).resolve().parents[1]
DATA = REPO / "data"
FILE = "case_status.json"  # in each client's folder
RUN, USAGE = "case_status_run.json", "case_status_usage.json"  # next to the clients (data/)
ENV = "USCIS_CASE_STATUS_"
SANDBOX = {"token_url": "https://api-int.uscis.gov/oauth/accesstoken", "api_url": "https://api-int.uscis.gov/case-status",
           "daily_budget": 1000, "per_second": 5}  # developer.uscis.gov "Sandbox": Case Status API 1,000 daily, 5 tps
PRODUCTION = {"daily_budget": 400_000, "per_second": 10}  # the API page's "Production Features & Capabilities"; USCIS's letter may say otherwise
RECEIPT = re.compile(r"[A-Z]{3}[0-9]{10}|[A-Z]{3}\*[0-9]{9}")  # the API's own receiptNumber patterns
CLOSED = ("approval", "denial", "rejection")
FOLLOW_UP = {"approval": "an approval notice", "rfe": "a request for evidence", "noid": "a notice of intent to deny", "denial": "a denial notice",
             "rejection": "a rejection notice (the filing returned)", "interview": "an interview notice", "biometrics": "a biometrics appointment notice"}
RECHECK_HOURS = 20  # one look a day per receipt: a second run the same night spends nothing
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]


class CaseStatusError(Exception):
    """One receipt's answer was an error (404, 422, an unexpected status): recorded, and the night goes on."""

    def __init__(self, status: int, message: str):
        super().__init__(message)
        self.status, self.message = status, message


class Stop(Exception):
    """The night stops here: over the limit (429), USCIS unavailable (503), or the keys refused (401 twice)."""


# -- configuration -------------------------------------------------------------------------------------


def _deployment_block(path: Path | None = None) -> dict[str, Any]:
    import deployment

    p = path or deployment.PATH
    try:
        return (json.loads(p.read_text(encoding="utf-8")).get("case_status") or {}) if p.exists() else {}
    except (OSError, ValueError):
        return {}


def config(env: dict[str, str] | None = None, deployment_path: Path | None = None) -> dict[str, Any]:
    """{"ready": bool, "why": what is missing, and when ready: environment, token_url, api_url, client_id, client_secret,
    daily_budget, per_second, demo_id}. The environment wins over deployment.json; the client secret is never read from
    deployment.json (a copy found there is moved into the vault by firmsecrets.migrate_deployment when the app and the overnight run start)."""
    env = os.environ if env is None else env
    saved = _deployment_block(deployment_path)

    def get(key: str) -> Any:
        return env.get(ENV + key.upper()) or saved.get(key)

    if str(get("enabled") or "").lower() in ("0", "false", "no", "off"):
        return {"ready": False, "why": "switched off on this server"}
    client_id, secret = get("client_id"), firmsecrets.get("uscis.client_secret", env=env)  # a secret: the environment, else the vault (src/firmsecrets.py), never deployment.json
    if not (client_id and secret):
        return {"ready": False, "why": "no USCIS Case Status API keys on this server"}
    environment = str(get("environment") or "sandbox").lower()
    if environment not in ("sandbox", "production"):
        return {"ready": False, "why": "the environment must be sandbox or production"}
    base = SANDBOX if environment == "sandbox" else PRODUCTION
    token_url, api_url = get("token_url") or base.get("token_url"), get("api_url") or base.get("api_url")
    if not (token_url and api_url):  # production's addresses are in USCIS's letter, not on its public pages
        return {"ready": False, "why": "the production addresses from USCIS's access letter aren't set on this server"}
    try:
        daily, per_second = int(get("daily_budget") or base["daily_budget"]), float(get("per_second") or base["per_second"])
    except ValueError:
        return {"ready": False, "why": "the daily budget and the requests per second must be numbers"}
    return {"ready": True, "why": None, "environment": environment, "token_url": token_url, "api_url": api_url.rstrip("/"),
            "client_id": client_id, "client_secret": secret, "daily_budget": max(0, daily), "per_second": max(0.1, per_second),
            "demo_id": get("demo_id")}


# -- the HTTP layer (tests replace it) -------------------------------------------------------------------


class Transport:
    """request(method, url, headers, data) -> (status code, the JSON answer or {}). httpx, the project's HTTP client."""

    def __init__(self, timeout: float = 30):
        self.timeout = timeout

    def request(self, method: str, url: str, headers: dict[str, str], data: dict[str, str] | None = None) -> tuple[int, dict[str, Any]]:
        import httpx

        r = httpx.request(method, url, headers=headers, data=data, timeout=self.timeout)
        try:
            body = r.json()
        except ValueError:
            body = {}
        return r.status_code, body if isinstance(body, dict) else {}


def _message(body: dict[str, Any]) -> str:
    """USCIS's error text: {"message": ...} (the API page) or {"errors": [{"message": ...}]} (the sandbox page, RFC 9457 style)."""
    errors = body.get("errors")
    if isinstance(errors, list) and errors and isinstance(errors[0], dict):
        return str(errors[0].get("message") or errors[0].get("code") or "")
    return str(body.get("message") or "")


class Client:
    """One night's conversation with the Case Status API: a token reused until it nearly expires, at most per_second requests a second."""

    def __init__(self, cfg: dict[str, Any], transport: Any = None, clock=time.monotonic, sleep=time.sleep):
        self.cfg, self.http, self.clock, self.sleep = cfg, transport or Transport(), clock, sleep
        self._token: str | None = None
        self._expires = 0.0
        self._last = None

    def token(self, fresh: bool = False) -> str:
        if self._token and not fresh and self.clock() < self._expires:
            return self._token
        status, body = self.http.request("POST", self.cfg["token_url"], {"Content-Type": "application/x-www-form-urlencoded"},
                                         {"grant_type": "client_credentials", "client_id": self.cfg["client_id"], "client_secret": self.cfg["client_secret"]})
        if status != 200 or not body.get("access_token"):
            raise Stop(f"USCIS refused the keys ({status}{': ' + _message(body) if _message(body) else ''})")
        self._token = body["access_token"]
        try:
            life = float(body.get("expires_in") or 1799)
        except ValueError:
            life = 1799.0
        self._expires = self.clock() + max(0.0, life - 60)  # renewed a minute early
        return self._token

    def _pace(self) -> None:
        gap = 1.0 / self.cfg["per_second"]
        if self._last is not None:
            wait = self._last + gap - self.clock()
            if wait > 0:
                self.sleep(wait)
        self._last = self.clock()

    def status(self, receipt: str) -> dict[str, Any]:
        """case_status for one receipt; CaseStatusError for this receipt only, Stop for the night."""
        receipt = re.sub(r"[\s-]", "", receipt).upper()
        if not RECEIPT.fullmatch(receipt):
            raise CaseStatusError(422, "Not a receipt number: three letters and ten digits.")
        for attempt in (1, 2):
            self._pace()
            headers = {"Authorization": f"Bearer {self.token(fresh=attempt == 2)}", "Accept": "application/json"}
            if self.cfg.get("demo_id"):
                headers["demo_id"] = str(self.cfg["demo_id"])  # developer.uscis.gov "Demo ID Requirement": only while USCIS watches the demo
            status, body = self.http.request("GET", f"{self.cfg['api_url']}/{receipt}", headers)
            if status == 200 and isinstance(body.get("case_status"), dict):
                return body["case_status"]
            if status == 401 and attempt == 1:
                continue  # the token expired early: one new token, one more try
            if status == 401:
                raise Stop("USCIS refused the access token twice: the keys may have been withdrawn")
            if status == 429:
                raise Stop("over USCIS's limit for today or this second")
            if status == 503:
                raise Stop("USCIS's Case Status API is unavailable" + (" (the sandbox runs Monday to Friday, 7 am to 8 pm Eastern)"
                                                                      if self.cfg.get("environment") == "sandbox" else ""))
            raise CaseStatusError(status, _message(body) or f"USCIS answered {status}")
        raise Stop("no answer")  # not reached


# -- what an answer means for the case ---------------------------------------------------------------------


def kind(text: str | None) -> str:
    """The notice kind (src/journey.py KIND_NAMES) a status text points to; "update" when it names none."""
    t = (text or "").lower()
    if "response" in t and "received" in t:  # "Response To ... Request For Evidence Was Received": the firm's answer arrived
        return "update"
    for pattern, k in ((r"request for (initial |additional )?evidence", "rfe"), (r"intent to (deny|revoke)", "noid"), (r"\bdenied\b|\bdenial\b", "denial"),
                       (r"\brejected\b|\brejection\b", "rejection"), (r"approv", "approval")):
        if re.search(pattern, t):
            return k
    if "scheduled" in t and "interview" in t:
        return "interview"
    if "scheduled" in t and re.search(r"fingerprint|biometric", t):
        return "biometrics"
    if re.search(r"transferred|relocated", t):
        return "transfer"
    if re.search(r"\breceived\b|\baccepted\b", t):
        return "receipt"
    return "update"


def _iso_from_desc(desc: str | None) -> str | None:
    """'On September 5, 2023, we approved ...' -> '2023-09-05'."""
    m = re.search(r"\b(?:On|As of) ([A-Z][a-z]+) (\d{1,2}), (\d{4})", desc or "")
    if not m or m.group(1) not in MONTHS:
        return None
    try:
        return date(int(m.group(3)), MONTHS.index(m.group(1)) + 1, int(m.group(2))).isoformat()
    except ValueError:
        return None


def _iso_from_stamp(stamp: str | None) -> str | None:
    """'09-05-2023 14:28:46' (the API's submittedDate / modifiedDate) -> '2023-09-05'."""
    m = re.match(r"(\d{2})-(\d{2})-(\d{4})", str(stamp or ""))
    return f"{m.group(3)}-{m.group(1)}-{m.group(2)}" if m else None


def _plain(html: str | None) -> str:
    """USCIS's description as plain text: the tags taken out and the character references (&amp;, &#39;, &nbsp;) read as the characters they stand for, nothing else changed."""
    import html as htmllib

    return re.sub(r"\s+", " ", htmllib.unescape(re.sub(r"<[^>]+>", "", html or ""))).strip()


def record_of(case: dict[str, Any], checked_at: datetime, environment: str) -> dict[str, Any]:
    """What is kept of one answer."""
    text = case.get("current_case_status_text_en")
    desc = _plain(case.get("current_case_status_desc_en"))
    history = [{"date": h.get("date"), "text": h.get("completed_text_en")} for h in (case.get("hist_case_status") or []) if isinstance(h, dict)]
    return {"receipt": case.get("receiptNumber"), "form": case.get("formType"), "text": text, "desc": desc, "kind": kind(text),
            "date": _iso_from_desc(desc) or _iso_from_stamp(case.get("modifiedDate")), "submitted": _iso_from_stamp(case.get("submittedDate")),
            "history": history, "checked_at": checked_at.isoformat(timespec="seconds"), "environment": environment, "error": None}


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def records(client_dir: Path) -> dict[str, dict[str, Any]]:
    """{receipt: the last answer} for one client (empty without keys)."""
    return (_read(Path(client_dir) / FILE, {}) or {}).get("receipts") or {}


def save(client_dir: Path, receipt: str, rec: dict[str, Any]) -> None:
    data = _read(Path(client_dir) / FILE, {}) or {}
    old = (data.get("receipts") or {}).get(receipt) or {}
    if rec.get("error") and old.get("text"):  # an error never wipes the last good answer
        rec = old | {"error": rec["error"], "message": rec.get("message"), "error_at": rec["checked_at"]}
    data.setdefault("receipts", {})[receipt] = rec
    _write(Path(client_dir) / FILE, data)
    if rec.get("text") != old.get("text"):  # a night that found the same answer writes no row
        import events

        events.record("journey", "checked", "USCIS's case status changed", case_dir=client_dir, default_who=("The overnight run", "system", "overnight"))


def _us(iso: str | None) -> str:
    """A check's stamp as the office's date (src/clock.py: a check at 9 pm Eastern is that day, not UTC's next one)."""
    return firm_clock.us_date(iso) or str(iso or "")


def for_journey(client_dir: Path, notices: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(timeline events, paralegal steps) from the saved answers: "USCIS status: ... (checked MM/DD/YYYY)", and
    a step to get the notice the status names when the folder doesn't have it yet."""
    events, steps = [], []
    for receipt, r in sorted(records(client_dir).items()):
        checked = _us(r.get("checked_at"))
        label = f"{r.get('form') or 'USCIS'} {receipt}"
        if r.get("error") and not r.get("text"):
            if r["error"] == "not_found":
                steps.append({"id": f"case_status.{receipt}.not_found", "owner": "paralegal", "urgent": False,
                              "text": f"USCIS's case status doesn't recognise {label} (checked {checked}): check the number against the notice. "
                                      "A case protected under 8 U.S.C. 1367 is never answered online: ask the USCIS Contact Center."})
            continue
        if not r.get("text"):
            continue
        events.append({"date": (r.get("date") or firm_clock.day(r.get("checked_at"))), "what": f"USCIS status: {r['text']} ({label}, checked {checked})",
                       "doc": None, "kind": "uscis_status"})
        k = r.get("kind")
        if k in FOLLOW_UP:
            since = r.get("date") or firm_clock.day(r.get("checked_at"))
            have = any(n["receipt"] == receipt and n["kind"] == k and (n.get("date") or "") >= (since or "") for n in notices)
            if not have:
                steps.append({"id": f"case_status.{receipt}.{k}.{since}", "owner": "paralegal", "urgent": k in ("rfe", "noid", "denial", "rejection"),
                              "text": f"USCIS's case status for {label} says \"{r['text']}\" (checked {checked}), but {FOLLOW_UP[k]} isn't in the "
                                      "client's folder yet: download it from the firm's USCIS online account, or watch the mail, and scan it in. "
                                      "The case acts on the notice itself (any due date is printed there)."})
    return events, steps


# -- the night's run ----------------------------------------------------------------------------------------


def _quota_day(now: datetime) -> str:
    """The API's day: 'resets everyday at -04:00 UTC (Midnight EST)' -- counted on UTC-4 (USCIS's day, not the firm's: the
    instant in UTC first, whatever zone `now` carries)."""
    return (now.astimezone(timezone.utc) - timedelta(hours=4)).date().isoformat()


def queue(out_root: Path, now: datetime | None = None) -> list[dict[str, Any]]:
    """The receipts to look at tonight, newest first: open on the case (the timeline's own list), not closed by an
    earlier answer, not looked at in the last RECHECK_HOURS."""
    from review.overview import journey_row

    now = now or firm_clock.now()
    out = []
    for d in sorted(p for p in Path(out_root).iterdir() if (p / "fact_graph.json").exists()):
        try:
            row = journey_row(d) or {}
        except Exception:  # noqa: BLE001 -- one broken bundle must not stop the others
            continue
        known = records(d)
        for r in row.get("receipts") or []:
            if not RECEIPT.fullmatch(str(r["receipt"] or "")):  # a number the notice reader misread: never sent to USCIS
                continue
            last = known.get(r["receipt"]) or {}
            if last.get("kind") in CLOSED:
                continue
            at = last.get("error_at") or last.get("checked_at")
            if at and now - firm_clock.parse(at) < timedelta(hours=RECHECK_HOURS):
                continue
            out.append({"client_dir": d, "receipt": r["receipt"], "form": r.get("form"), "since": r.get("since") or "", "last": firm_clock.day(at)})
    # never looked at first, then the longest unchecked; within each, the newest activity first -- so a new filing goes
    # straight to the front and, when the budget is short, every receipt still gets its turn within a few nights
    out.sort(key=lambda x: (x["since"], x["receipt"]), reverse=True)
    out.sort(key=lambda x: x["last"])
    return out


def nightly(out_root: Path, data_root: Path = DATA, cfg: dict[str, Any] | None = None, transport: Any = None, now: datetime | None = None,
            clock=time.monotonic, sleep=time.sleep) -> str:
    """Checks the open receipts within the day's budget; saves each answer with the client; returns the morning's line."""
    cfg = cfg if cfg is not None else config()
    now = now or firm_clock.now()
    run_path, usage_path = Path(data_root) / RUN, Path(data_root) / USAGE
    if not cfg.get("ready"):
        _write(run_path, {"at": now.isoformat(timespec="seconds"), "configured": False, "why": cfg.get("why")})
        return f"USCIS case status: not switched on ({cfg.get('why')}): nothing checked."
    day = _quota_day(now)
    usage = _read(usage_path, {}) or {}
    used = usage.get("used", 0) if usage.get("day") == day else 0
    todo = queue(out_root, now)
    client = Client(cfg, transport, clock, sleep)
    checked, changed, errors, stopped = 0, 0, 0, None
    for item in todo:
        if used >= cfg["daily_budget"]:
            stopped = f"the day's budget of {cfg['daily_budget']:,} is spent"
            break
        try:
            rec = record_of(client.status(item["receipt"]), now, cfg["environment"])
        except Stop as exc:  # over the limit, USCIS unavailable, the keys refused: the rest wait for tomorrow
            stopped = str(exc)
            break
        except CaseStatusError as exc:
            errors += 1
            rec = {"receipt": item["receipt"], "form": item["form"], "checked_at": now.isoformat(timespec="seconds"), "environment": cfg["environment"],
                   "error": "not_found" if exc.status == 404 else "error", "message": exc.message}
        used += 1
        before = records(item["client_dir"]).get(item["receipt"]) or {}
        changed += int(bool(rec.get("text")) and rec.get("text") != before.get("text"))
        checked += 1
        save(item["client_dir"], item["receipt"], rec)
    left = len(todo) - checked
    _write(usage_path, {"day": day, "used": used})
    _write(run_path, {"at": now.isoformat(timespec="seconds"), "configured": True, "environment": cfg["environment"], "checked": checked, "changed": changed,
                      "errors": errors, "left": left, "stopped": stopped, "used_today": used, "budget": cfg["daily_budget"]})
    text = f"USCIS case status ({cfg['environment']}): {checked} receipt(s) checked, {changed} changed" + (f", {errors} not answered" if errors else "")
    if left:
        text += f"; {left} left for tomorrow ({stopped or 'the budget'})"
    return text + "."


def summary(data_root: Path = DATA, cfg: dict[str, Any] | None = None) -> dict[str, Any]:
    """For Keeping current: switched on or not, and the last night's run. No keys, no paths."""
    cfg = cfg if cfg is not None else config()
    last = _read(Path(data_root) / RUN, None)
    return {"configured": bool(cfg.get("ready")), "why": cfg.get("why"), "environment": cfg.get("environment"),
            "last_run": {k: last.get(k) for k in ("at", "checked", "changed", "errors", "left", "stopped")} if last and last.get("configured") else None}


def state_text(data_root: Path = DATA, cfg: dict[str, Any] | None = None) -> str:
    """The plain line Keeping current shows under the item: switched on or not, and the last check."""
    s = summary(data_root, cfg)
    if not s["configured"]:
        return (f"Not switched on: {s['why']}. Nothing is checked automatically; check each case's status in the firm's USCIS online "
                "account until it is.")
    last = s["last_run"]
    if not last:
        return "Switched on; the first check runs tonight."
    return (f"Switched on. Last checked {_us(last['at'])}: {last.get('checked') or 0} receipt(s), {last.get('changed') or 0} changed"
            + (f", {last['left']} left for the next night" if last.get("left") else "") + ".")


def finding(data_root: Path = DATA, cfg: dict[str, Any] | None = None, now: datetime | None = None) -> str | None:
    """Keeping current's warning for this item (src/maintenance.py): only a switched-on check that isn't working. Not switched on
    isn't a fault to fix every month: state_text() says so on the page instead."""
    s = summary(data_root, cfg)
    if not s["configured"]:
        return None
    last = s["last_run"]
    if not last:
        return None  # switched on today: the first check runs tonight (state_text)
    at = firm_clock.parse(last["at"])
    if (now or firm_clock.now()) - at > timedelta(days=3):
        return f"Last checked {_us(last['at'])}: more than three days ago. Is the nightly run running?"
    if last.get("stopped") and not str(last["stopped"]).startswith("the day's budget"):
        return f"The last run stopped early: {last['stopped']}."
    return None
