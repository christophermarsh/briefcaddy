"""Clio's webhooks: Clio tells the product when a document or a matter changed, so it reads that matter within the hour and not only at night.

What Clio's documentation says is in src/connectors/clio.py's docstring ("Webhooks", read 10/03/2026); what is built on it:

  The route.     POST /clio/webhook (review/server.py WEBHOOKS), answered before anyone is asked to sign in (Clio has no cookie) and authenticated
                 by the signature on the body: X-Hook-Signature, an HMAC-SHA256 of the raw body keyed with the secret Clio sent in the handshake. The
                 secret is kept in the Clio vault (secrets.enc, encrypted), never in a log, the ledger or the screen. A call that is unsigned, badly
                 signed, too large, for a subscription Clio has not reported enabled, or sent while nothing is subscribed gets the answer a made-up
                 path gets. A replay (the same bytes again with a valid signature: Clio's body carries no event id, but its etag changes with the
                 record) is answered 200 and does nothing, so that Clio's own retry of a message whose first answer was lost is never refused (and
                 never earns Clio's address a 429). The body is read, acted on and dropped: only its digest is kept (webhook_seen.log).
  The handshake. Creating a subscription makes Clio POST the address with X-Hook-Secret and data.webhook_id; the answer is 200 with the same header.
                 It is not signed (there is no secret yet), so it is taken only inside a window that Subscribe opens and holds open for
                 HANDSHAKE_WINDOW seconds after it returns (Clio's page says the handshake follows the creation "immediately", not that it comes before
                 the answer). Whoever sends one, the secret is only a CANDIDATE (kept in the vault, at most three for an id): it signs nothing. A
                 candidate becomes the subscription's secret only when Clio reports that subscription enabled (Check, through the request queue):
                 with one candidate, that one; with several, each is offered to Clio's activate call and the one Clio accepts is kept. So an attacker
                 who answers first can neither be believed nor keep Clio's own handshake out. When the window ends, and on Stop, every candidate,
                 and every secret for an id we do not hold, is dropped. A message is taken only for a subscription Clio reported enabled.
  Subscribing.   Settings, Connections: "Subscribe" makes two subscriptions through Clio's API (HOOKS: documents created or updated, matters updated,
                 opened, put on pending or closed) with the address built from the review app's address typed in Settings (never the Host header),
                 expiring in LIFE_DAYS; the overnight run renews them when fewer than RENEW_WHEN_DAYS are left (Clio: 3 days by default, 31 at most);
                 "Stop" deletes them (the only DELETE this product ever sends: a subscription it made, never the firm's data).
  What an event does. It is a row in the ledger (in words: the case and what happened, never the body). A document event, or a matter event, on a
                 linked matter puts that matter's case on the "read tonight" list (data/clio/webhook.json "touched": the overnight run's
                 Clio step reads it, and the case page says a Clio upload is waiting) and, when an attorney switched on "Read Clio uploads within
                 the hour", wakes the one worker that reads those matters through the same request queue (Pace) as the nightly sync. A matter
                 event refreshes the case's portal client (what its profile lacks); nothing the office set is overwritten.
  Never.         An event never changes a case by itself beyond that, never deletes, and never sends anything out.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import os
import re
import sys
import threading
import time
from datetime import timedelta
from pathlib import Path
from typing import Any

import httpx

import clock
import events

from . import clio, sync

PATH = "/clio/webhook"
MAX_BODY = 64 * 1024  # a Clio webhook body is a few hundred bytes; anything larger is not one
FILE = "webhook.json"
LIFE_DAYS = 30  # a new or renewed subscription expires this long from now (Clio: 31 days at most)
RENEW_WHEN_DAYS = 10  # the overnight run renews one with fewer days than this left
HANDSHAKE_WINDOW = 120  # seconds after Subscribe in which an unsigned handshake is taken
SEEN_FILE = "webhook_seen.log"  # one line per accepted body: its SHA-256 and the time (append-only, pruned each night)
SEEN_PRUNE_EVERY = 3600  # seconds between prunes of the review app's in-memory digests
SEEN_DAYS, SEEN_MAX = 31, 50000  # how long, and how many, body digests are kept to recognise a replay
CANDIDATES_PER_ID, CANDIDATES_MAX = 6, 12  # handshake secrets kept, unconfirmed, for one id (the oldest goes first) and for ids we do not hold (the oldest goes first)
LOOKUPS_MAX = 200
HOOKS = (
    {"model": "document", "events": ["created", "updated"], "fields": "id,etag,matter{id}"},
    {"model": "matter", "events": ["updated", "matter_opened", "matter_pended", "matter_closed"], "fields": "id,etag,status,responsible_attorney{id}"},
)
HAPPENED = {"created": "added", "updated": "changed", "matter_opened": "opened", "matter_pended": "put on pending", "matter_closed": "closed"}
_lock = threading.Lock()
_seen_lock = threading.Lock()
_seen_cache: dict[str, dict[str, int]] = {}  # digest -> the time it was taken
_seen_pruned: dict[str, float] = {}  # when each cache was last pruned (the file is pruned by the overnight run)
_SECRET = re.compile(r"[\x21-\x7e]{8,512}")


# -- the file: the subscriptions, the read-tonight list, the digests ---------------------------------------------------------


def _path(data_root: Path | None) -> Path:
    return clio.folder(data_root) / FILE


def load(data_root: Path | None) -> dict[str, Any]:
    data = clio._read(_path(data_root), {}) or {}
    return {"hooks": dict(data.get("hooks") or {}), "pending": data.get("pending"), "touched": dict(data.get("touched") or {}),
            "lookups": list(data.get("lookups") or []), "last_event": data.get("last_event"),
            "last_read": data.get("last_read"), "problem": data.get("problem")}


def _update(data_root: Path | None, change) -> dict[str, Any]:
    with _lock, clio._short_lock(data_root, "webhook"):  # the review app's threads and the overnight run both write this file
        data = load(data_root)
        change(data)
        clio._write(_path(data_root), data)
        return data


def waiting(data_root: Path | None) -> dict[str, Any]:
    """The matters Clio said changed that have not been read yet: {matter id: {"case", "since", "last", "n", "kinds"}}."""
    return load(data_root)["touched"]


def snapshot(data_root: Path | None) -> dict[str, Any]:
    """The list as it is now ({matter id: n}, and the documents to look up): taken before a read, so what it reads can be cleared and an event that
    arrives meanwhile (a later n) stays."""
    d = load(data_root)
    return {"touched": {m: t.get("n") for m, t in d["touched"].items()}, "lookups": list(d["lookups"])}


def clear(data_root: Path | None, snap: dict[str, Any], kept: set[str] = frozenset()) -> None:
    """Takes off the list what the read after `snapshot` covered (not `kept`, nor an event that came later)."""
    def done(s):
        s["touched"] = {m: t for m, t in s["touched"].items() if m in kept or t.get("n") != snap["touched"].get(m)}
        s["lookups"] = [x for x in s["lookups"] if x not in snap["lookups"]]
    _update(data_root, done)


# -- the digests of the bodies taken (their own file: an event never rewrites a store of fifty thousand) -----------------------------


def _seen_path(data_root: Path | None) -> Path:
    return clio.folder(data_root) / SEEN_FILE


def _seen_loaded(data_root: Path | None) -> dict[str, int]:
    key = str(_seen_path(data_root))
    if key not in _seen_cache:
        try:
            lines = _seen_path(data_root).read_text(encoding="utf-8").splitlines()
        except OSError:
            lines = []
        rows = [ln.split() for ln in lines if ln.strip()]
        _seen_cache[key] = {r[0]: int(r[1]) if len(r) > 1 and r[1].isdigit() else 0 for r in rows}
    return _seen_cache[key]


def seen_has(data_root: Path | None, digest: str) -> bool:
    with _seen_lock:
        return digest in _seen_loaded(data_root)


def seen_add(data_root: Path | None, digest: str, now: float) -> None:
    """One whole line appended (never a rewrite): the body's digest and the time."""
    with _seen_lock, clio._short_lock(data_root, "webhook_seen"):
        known = _seen_loaded(data_root)
        path = _seen_path(data_root)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        try:
            os.write(fd, f"{digest} {int(now)}\n".encode())
        finally:
            os.close(fd)
        known[digest] = int(now)
        key = str(path)
        if now - _seen_pruned.setdefault(key, now) >= SEEN_PRUNE_EVERY:  # the review app runs for weeks: what the file will lose tonight leaves memory now
            for old in [d for d, t in known.items() if t <= now - SEEN_DAYS * 86400]:
                del known[old]
            for old in sorted(known, key=known.get)[:max(0, len(known) - SEEN_MAX)]:
                del known[old]
            _seen_pruned[key] = now


def prune_seen(data_root: Path | None, now: float | None = None) -> int:
    """The overnight run's step: forgets digests older than SEEN_DAYS (and all but the newest SEEN_MAX). Returns how many are kept."""
    now = time.time() if now is None else now
    with _seen_lock, clio._short_lock(data_root, "webhook_seen"):
        path = _seen_path(data_root)
        try:
            rows = [ln.split() for ln in path.read_text(encoding="utf-8").splitlines() if ln.strip()]
        except OSError:
            return 0
        keep = [r for r in rows if len(r) == 2 and r[1].isdigit() and int(r[1]) > now - SEEN_DAYS * 86400][-SEEN_MAX:]
        tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
        tmp.write_text("".join(f"{d} {t}\n" for d, t in keep), encoding="utf-8")
        os.replace(tmp, path)
        _seen_cache.pop(str(path), None)
        _seen_pruned.pop(str(path), None)
        return len(keep)


# -- the secrets (the Clio vault) -----------------------------------------------------------------------------------------------


def _secrets(data_root: Path | None, env: dict[str, str] | None = None) -> dict[str, str]:
    """The secrets Clio's own enabled subscriptions signed with: {subscription id: secret}. Only these sign anything."""
    try:
        return dict(clio.Vault(data_root, env).read().get("webhook_secrets") or {})
    except clio.NotReady:
        return {}


def _candidates(data_root: Path | None, env: dict[str, str] | None = None) -> dict[str, list[str]]:
    """The handshake secrets nobody has vouched for yet: {subscription id: [secret, ...]}. They sign nothing."""
    try:
        return {k: list(v) for k, v in (clio.Vault(data_root, env).read().get("webhook_candidates") or {}).items()}
    except clio.NotReady:
        return {}


def _change_vault(data_root: Path | None, env: dict[str, str] | None, change) -> None:
    """change(the vault's dict, in place); an empty webhook_secrets or webhook_candidates is not kept."""
    vault = clio.Vault(data_root, env)
    with clio._vault_lock, vault.lock():
        data = vault.read()
        change(data)
        vault.write({k: v for k, v in data.items() if v is not None and (v or k not in ("webhook_secrets", "webhook_candidates"))})


def _forget_id(data_root: Path | None, env: dict[str, str] | None, hook_id: str) -> None:
    def drop(data):
        for key in ("webhook_secrets", "webhook_candidates"):
            data[key] = {k: v for k, v in (data.get(key) or {}).items() if k != hook_id}
    _change_vault(data_root, env, drop)


def signed(body: bytes, header: str, secret: str) -> bool:
    """The signature is the HMAC-SHA256 of the raw body keyed with the secret. Clio's page does not say how it is written (hex or base64), so each
    is accepted: both are the same signature, so accepting either lets nothing in that the other would keep out. Constant time."""
    given = str(header or "").strip()
    if given.lower().startswith("sha256="):
        given = given[7:]
    if not given or len(given) > 200:
        return False
    mac = hmac.new(secret.encode(), body, hashlib.sha256).digest()
    hexed = hmac.compare_digest(mac.hex().encode(), given.lower().encode())
    plain64 = hmac.compare_digest(base64.b64encode(mac), given.encode())
    url64 = hmac.compare_digest(base64.urlsafe_b64encode(mac), given.encode())
    return hexed | plain64 | url64  # no early exit: the same work whichever form matches


# -- receiving ------------------------------------------------------------------------------------------------------------------


def _first_id(value: Any) -> str | None:
    try:
        n = int(value)
    except (TypeError, ValueError):
        return None
    return str(n) if n > 0 else None


def receive(data_root: Path | None, body: bytes, signature: str | None, hook_secret: str | None, wake=None, env: dict[str, str] | None = None,
            now: float | None = None, confirm=None) -> tuple[str, dict[str, str]]:
    """What the route does with a call: ("handshake", headers to answer with), ("event", {}) or ("refuse", {}). Nothing here raises for a bad call, and
    every refusal is the same word: the caller says the same thing to everyone. A replay is an "event" that does nothing. confirm: called after a
    handshake secret was kept as a candidate (the review app then asks Clio, through the queue, which subscription it belongs to)."""
    signed_ok = False
    try:
        if len(body) > MAX_BODY or not clio.folder(data_root).exists():
            return "refuse", {}
        now = time.time() if now is None else now
        if hook_secret is not None:
            return _handshake(data_root, body, hook_secret, env, now, confirm)
        secrets = _secrets(data_root, env)
        if not signature or not secrets:
            return "refuse", {}
        # checks every secret (at most two), whichever matches
        matched = [i for i, s in secrets.items() if signed(body, signature, s)]
        if not matched:
            return "refuse", {}
        signed_ok = True  # from here a failure is ours, not a stranger's: it is said on the console
        payload = json.loads(body)
        if not isinstance(payload, dict) or not isinstance(payload.get("meta") or {}, dict) or not isinstance(payload.get("data") or {}, dict):
            return "refuse", {}
        meta, data = (payload.get("meta") or {}), (payload.get("data") or {})
        hook_id = _first_id(meta.get("webhook_id"))
        if hook_id is None or hook_id not in matched:
            return "refuse", {}
        hook = load(data_root)["hooks"].get(hook_id)
        if hook is None or hook.get("status") != "enabled":  # only a subscription Clio reported enabled (Check) is believed
            return "refuse", {}
        digest = hashlib.sha256(body).hexdigest()
        if seen_has(data_root, digest):
            return "event", {}  # a replay (Clio's retry of a message whose answer was lost): nothing is done, nothing written, nothing counted
        outcome: dict[str, Any] = {}

        def note(d):
            d["last_event"] = clock.stamp()
            outcome["record"] = _record(data_root, d, hook["model"], str(meta.get("event") or ""), data)
        _update(data_root, note)
        _ledger(data_root, outcome["record"])
        seen_add(data_root, digest, now)  # after all the work, the ledger row too: a message that failed halfway is not remembered as taken, Clio's retry is processed
        if outcome["record"].get("case") and wake is not None:
            wake()
        return "event", {}
    except Exception as exc:  # noqa: BLE001 -- a body that isn't JSON, a vault that can't be read: refused like any call that isn't Clio's
        if signed_ok:  # a correctly signed message could not be taken: the operator is told (never the body or a secret), Clio gets the same answer
            sys.stderr.write(f"Clio webhook: a correctly signed message could not be processed ({type(exc).__name__}); Clio will send it again.\n")
        return "refuse", {}


def _handshake(data_root: Path | None, body: bytes, secret: str, env: dict[str, str] | None, now: float, confirm=None) -> tuple[str, dict[str, str]]:
    """Clio's first call for a new subscription (unsigned: there is no secret yet): taken only inside the window Subscribe holds open, and answered
    with the secret in X-Hook-Secret. The secret is a candidate until Clio reports the subscription enabled (confirm_candidates): anyone can send one,
    so it signs nothing, and a candidate never keeps another (Clio's own) out."""
    st = load(data_root)
    if not _SECRET.fullmatch(secret) or float((st["pending"] or {}).get("until") or 0) < now:
        return "refuse", {}
    hook_id = _first_id(((json.loads(body) or {}).get("data") or {}).get("webhook_id"))
    if hook_id is None or hook_id in _secrets(data_root, env):
        return "refuse", {}
    if (st["hooks"].get(hook_id) or {}).get("status") == "enabled":  # Clio sends its handshake once, while the subscription is pending: a later one is not Clio's
        return "refuse", {}
    ours = set(st["hooks"])  # the subscriptions we made: a handshake for one of them is never turned away because slots are full

    def add(vault):
        cands = {k: list(v) for k, v in (vault.get("webhook_candidates") or {}).items()}
        mine = cands.pop(hook_id, [])  # popped and put back last: the dict keeps the order the ids were first heard
        if secret not in mine:
            mine = (mine + [secret])[-CANDIDATES_PER_ID:]  # the oldest candidate for this id makes room
        cands[hook_id] = mine
        if hook_id not in ours:  # ids that are not ours (anyone's guess): at most CANDIDATES_MAX of them, the oldest id dropped to make room
            strangers = [k for k in cands if k not in ours]
            for old in strangers[:max(0, len(strangers) - CANDIDATES_MAX)]:
                del cands[old]
        vault["webhook_candidates"] = cands
    _change_vault(data_root, env, add)
    if confirm is not None:
        confirm()
    return "handshake", {"X-Hook-Secret": secret}


def _record(data_root: Path | None, d: dict[str, Any], model: str, kind: str, data: dict[str, Any]) -> dict[str, Any]:
    """What one event means (in the file's dict d, in place): the case on the read-tonight list, or a document id to look up, or nothing."""
    if kind not in HAPPENED:
        return {"what": "Clio sent a message this product has no use for", "case": None}
    matter = _first_id(data.get("id")) if model == "matter" else _first_id((data.get("matter") or {}).get("id") if isinstance(data.get("matter"), dict) else None)
    mapped = (clio.state(data_root).get("matters") or {})
    if matter is None and model == "document":  # the fields Clio sent did not name the matter: the document is looked up before the next read
        doc = _first_id(data.get("id"))
        if doc and doc not in d["lookups"]:
            d["lookups"] = (d["lookups"] + [doc])[-LOOKUPS_MAX:]
        return {"what": f"Clio said a document was {HAPPENED[kind]}; its matter is looked up before the next read", "case": None, "lookup": True}
    if matter is None or matter not in mapped:
        return {"what": "Clio said something changed on a matter that is not linked to a case here; nothing was done", "case": None}
    case = mapped[matter]["case"]
    seen = d["touched"].get(matter) or {"case": case, "since": clock.stamp(), "n": 0, "kinds": []}
    seen.update(last=clock.stamp(), n=int(seen.get("n") or 0) + 1, kinds=sorted(set(seen.get("kinds") or []) | {model}))
    d["touched"][matter] = seen
    what = (f"Clio said a document was {HAPPENED[kind]} in this case's matter; it is read tonight" if model == "document"
            else f"Clio said this case's matter was {HAPPENED[kind]}; nothing is changed in the matter, and the client's details are refreshed")
    return {"what": what, "case": case}


def _ledger(data_root: Path | None, rec: dict[str, Any]) -> None:
    events.record("imports", "clio_event", rec["what"], case=rec.get("case"), home=data_root, default_who=("Clio", "system", "connector"))


# -- the address, and what Settings shows -------------------------------------------------------------------------------------------


def public_url(data_root: Path | None) -> tuple[str | None, str | None]:
    """(the address Clio is to call, why there isn't one): the review app's address typed in Settings (the one Clio's connection uses), with the
    callback's end taken off, and this route's on. Never the Host header of any request."""
    uri = clio.redirect_uri(data_root)
    if not uri:
        return None, "Enter the review app's address for Clio first (above), then save."
    base = re.sub(r"/auth/callback$", "", uri)
    if not base.startswith("https://"):
        return None, "Clio only sends to an https address: the review app's address for Clio must start with https."
    return base + PATH, None


UNCONFIRMED = "Clio has this address but the office holds no key for it: press Stop, then Subscribe again."


def _unconfirmed(data_root: Path | None, hooks: dict[str, Any], env: dict[str, str] | None = None) -> set[str]:
    """The subscriptions Clio reports enabled for which the office holds no secret and has no candidate left to try: nothing signed for them will ever be
    believed, so they are not "on" however Clio's status reads. (Check writing Clio's "enabled" back, or a renewal, does not change that.)"""
    held, cands = _secrets(data_root, env), _candidates(data_root, env)
    return {i for i, h in hooks.items() if h.get("status") == "enabled" and i not in held and not cands.get(i)}


def _says(h: dict[str, Any], keyless: bool = False) -> str:
    if keyless:
        return "no key held"
    return {"enabled": "on", "pending": "waiting for Clio to confirm", "suspended": "stopped by Clio", "expired": "ended"}.get(str(h.get("status")), "unknown")


def state_of(data_root: Path | None, env: dict[str, str] | None = None) -> str:
    """"off", "on", "waiting", "unconfirmed", "stopped" or "ended": the subscriptions as a whole."""
    hooks = load(data_root)["hooks"]
    if not hooks:
        return "off"
    states = {h.get("status") for h in hooks.values()}
    if "expired" in states:
        return "ended"
    if "suspended" in states:
        return "stopped"
    if _unconfirmed(data_root, hooks, env):
        return "unconfirmed"
    held = _secrets(data_root, env)
    if all(h.get("status") == "enabled" and i in held for i, h in hooks.items()):
        return "on"
    return "waiting"


def view(data_root: Path | None, env: dict[str, str] | None = None) -> dict[str, Any]:
    """Everything Settings, Connections shows about the webhooks: never the secret, the signature or a body."""
    d = load(data_root)
    url, why = public_url(data_root)
    state = state_of(data_root, env)
    keyless = _unconfirmed(data_root, d["hooks"], env)
    ends = min((h.get("expires_at") for h in d["hooks"].values() if h.get("expires_at")), default=None, key=clock.key)
    lines = {"off": "Not subscribed. Until you subscribe, a document added in Clio is read at night with everything else.",
             "on": "Subscribed. Clio tells this product when a document is added to a linked matter, or a matter changes."
                   + (f" The overnight run renews the subscription before it ends ({clock.us_date(ends)})." if ends else ""),
             "waiting": "Subscribed, and waiting for Clio to confirm the address. Press Check in a minute.",
             "stopped": "Clio stopped sending (it suspended the subscription after calls that failed). Press Subscribe again.",
             "ended": "The subscription ended in Clio. Press Subscribe again.", "unconfirmed": UNCONFIRMED}
    return {"state": state, "line": lines[state], "url": url, "why_not": why, "ends": ends,
            "last_event": d["last_event"], "last_read": d["last_read"], "waiting": len(d["touched"]),
            "hooks": [{"model": h["model"], "says": _says(h, i in keyless)} for i, h in d["hooks"].items()],
            "problem": d["problem"], "can": {"subscribe": url is not None and state in ("off", "stopped", "ended"), "stop": state != "off", "check": state != "off"},
            "hourly": bool(clio.settings(data_root).get("uploads_hourly"))}


def needs(data_root: Path | None) -> str | None:
    """Keeping current: why the subscription needs a person, or None (nothing is subscribed, or it is working)."""
    d = load(data_root)
    if not d["hooks"]:
        return None
    state = state_of(data_root)
    if state == "unconfirmed":
        return UNCONFIRMED
    if state in ("stopped", "ended"):
        return "Clio's webhook subscription " + ("was suspended by Clio" if state == "stopped" else "has ended") + ": an attorney presses Subscribe again in Settings, Connections."
    if d["problem"]:
        return "The webhook subscription could not be renewed: " + str(d["problem"].get("what") or "")
    return None


# -- the subscription, through Clio's API (Pace: every request waits in the one queue) ---------------------------------------------


def _client(data_root: Path | None, transport, env, pace) -> clio.Clio:
    return clio.Clio(data_root, transport=transport, env=env, pace=pace)


def _expires() -> str:
    return (clock.utcnow() + timedelta(days=LIFE_DAYS)).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def subscribe(data_root: Path | None, who: str, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None, pace=None) -> dict[str, Any]:
    """"Subscribe": makes the two subscriptions and asks Clio how they stand. NotReady / ClioError say what is missing, in words."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    try:
        connected = bool(clio.Vault(data_root, env).read().get("refresh_token"))
    except clio.NotReady as exc:
        raise clio.NotReady(str(exc)) from None
    if not connected:
        raise clio.NotReady("Clio isn't connected: an attorney connects it first (above).")
    url, problem = public_url(data_root)
    if url is None:
        raise clio.NotReady(problem or "")
    if state_of(data_root) in ("on", "waiting"):
        return check(data_root, transport, env, pace)
    client = _client(data_root, transport, env, pace)
    for old in list(load(data_root)["hooks"]):  # one Clio stopped or ended: what is left of it is deleted first, so there are never two sets
        try:
            client.call("DELETE", f"/webhooks/{old}.json", unsubscribe=True, what="stopping the webhook subscription")
        except clio.ClioError as exc:
            if "isn't in Clio" not in str(exc):
                raise
        _update(data_root, lambda d, i=old: d["hooks"].pop(i, None))
        _forget_id(data_root, env, old)
    _update(data_root, lambda d: d.update(pending={"until": time.time() + HANDSHAKE_WINDOW, "by": who.strip()}, problem=None))
    made: dict[str, dict[str, Any]] = {}
    try:
        for spec in HOOKS:
            r = client.call("POST", "/webhooks.json", params={"fields": "id,status,expires_at,model,events"},
                            json={"data": {"url": url, "model": spec["model"], "events": spec["events"], "fields": spec["fields"], "expires_at": _expires()}},
                            what="subscribing to Clio's webhooks").json().get("data") or {}
            hook_id = _first_id(r.get("id"))
            if hook_id is None:
                raise clio.ClioError("Clio didn't say which subscription it made: press Subscribe again.")
            made[hook_id] = {"model": spec["model"], "events": spec["events"], "status": str(r.get("status") or "pending"), "expires_at": r.get("expires_at"),
                             "made_by": who.strip(), "made_at": clock.stamp()}
            _update(data_root, lambda d, i=hook_id: d["hooks"].__setitem__(i, made[i]))
    finally:  # the window stays open for HANDSHAKE_WINDOW after this returns: Clio's handshake may follow its answer (check or the end of the window closes it)
        _update(data_root, lambda d: d.update(pending={"until": time.time() + HANDSHAKE_WINDOW, "by": who.strip()}))
    events.record("settings", "webhook_on", "Subscribed to Clio's webhooks", home=Path(data_root or clio.DATA), who=who.strip())
    return check(data_root, transport, env, pace, client=client)


def check(data_root: Path | None, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None, pace=None,
          client: clio.Clio | None = None) -> dict[str, Any]:
    """Asks Clio how each subscription stands (status, expiry) and records it. A subscription Clio no longer has is "expired"; one that is still
    "pending" with no secret here is dropped so Subscribe can start again."""
    client = client or _client(data_root, transport, env, pace)
    for hook_id in list(load(data_root)["hooks"]):
        try:
            r = client.call("GET", f"/webhooks/{hook_id}.json", params={"fields": "id,status,expires_at"}, what="reading the webhook subscription").json().get("data") or {}
            status, expires = str(r.get("status") or "unknown"), r.get("expires_at")
        except clio.ClioError as exc:
            if "isn't in Clio" not in str(exc):
                raise
            status, expires = "expired", None
        _update(data_root, lambda d, i=hook_id, s=status, e=expires: d["hooks"][i].update(status=s, **({"expires_at": e} if e else {})))
    confirm_candidates(data_root, client, env)
    after, held = load(data_root)["hooks"], _secrets(data_root, env)
    done = bool(after) and all(h.get("status") == "enabled" and i in held for i, h in after.items())  # every subscription has Clio's own secret: nothing more to wait for
    end_window(data_root, env, now=float("inf") if done else None)
    return view(data_root, env)


def confirm_candidates(data_root: Path | None, client: clio.Clio, env: dict[str, str] | None = None) -> None:
    """For each subscription Clio reports enabled that has handshake candidates: the candidate that is Clio's becomes its secret. Each candidate, one or
    several, is offered to Clio's activate call with the secret in X-Hook-Secret, and the one Clio accepts is kept (a handshake is sent by anyone, so a lone
    candidate proves nothing: only Clio's own answer does); if Clio does not single one out, none is kept: the
    subscription is then enabled with no key held ("unconfirmed": not on, every delivery refused, the person is told to Stop and Subscribe again, and
    neither Check nor the renewal changes that). The others are dropped."""
    hooks = load(data_root)["hooks"]
    for hook_id, candidates in _candidates(data_root, env).items():
        if hook_id not in hooks or hooks[hook_id].get("status") != "enabled":
            continue  # not enabled (yet): a candidate waits for the end of the window and signs nothing
        winners = []  # whatever the number of candidates, each is offered to Clio, and only one Clio accepts is kept (a lone candidate is not trusted on its own)
        for cand in candidates:
            try:
                client.call("PUT", f"/webhooks/{hook_id}/activate", headers={"X-Hook-Secret": cand}, what="confirming the webhook subscription")
                winners.append(cand)
            except clio.ClioError:
                pass

        def settle(vault, i=hook_id, w=winners):
            vault["webhook_candidates"] = {k: v for k, v in (vault.get("webhook_candidates") or {}).items() if k != i}
            if len(w) == 1:
                vault["webhook_secrets"] = (vault.get("webhook_secrets") or {}) | {i: w[0]}
        _change_vault(data_root, env, settle)


def end_window(data_root: Path | None, env: dict[str, str] | None = None, now: float | None = None) -> None:
    """Once the handshake window is over: it is closed, every candidate is dropped, and so is any secret for an id we do not hold."""
    now = time.time() if now is None else now
    d = load(data_root)
    if d["pending"] and float(d["pending"].get("until") or 0) >= now:
        return
    if d["pending"]:
        _update(data_root, lambda s: s.update(pending=None))

    def drop(vault):
        vault["webhook_candidates"] = {}
        vault["webhook_secrets"] = {k: v for k, v in (vault.get("webhook_secrets") or {}).items() if k in d["hooks"]}
    if _candidates(data_root, env) or any(k not in d["hooks"] for k in _secrets(data_root, env)):
        _change_vault(data_root, env, drop)


def renew(data_root: Path | None, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None, pace=None) -> str | None:
    """The overnight run's step: each subscription with fewer than RENEW_WHEN_DAYS left gets a new end (Clio's PATCH of expires_at). None when nothing is
    subscribed; else a line for the report. A refusal is recorded for Keeping current."""
    prune_seen(data_root)
    d = load(data_root)
    if not d["hooks"] or clio.ready(data_root, env) in ("switched off", "not connected"):
        return None
    client = _client(data_root, transport, env, pace)
    renewed = 0
    try:
        check(data_root, transport, env, pace, client=client)
        for hook_id, h in load(data_root)["hooks"].items():
            ends = clock.parse(h.get("expires_at"))
            left = (ends - clock.now()).total_seconds() / 86400 if ends else 0
            if h.get("status") in ("enabled", "pending") and left < RENEW_WHEN_DAYS:
                new = _expires()
                client.call("PATCH", f"/webhooks/{hook_id}.json", params={"fields": "id,expires_at"}, json={"data": {"expires_at": new}},
                            what="renewing the webhook subscription")
                _update(data_root, lambda s, i=hook_id, e=new: s["hooks"][i].update(expires_at=e))
                renewed += 1
        _update(data_root, lambda s: s.update(problem=None))
    except (clio.ClioError, httpx.HTTPError) as exc:
        what = str(exc) if isinstance(exc, clio.ClioError) else "Clio couldn't be reached."
        _update(data_root, lambda s: s.update(problem={"at": clock.stamp(), "what": what}))
        return f"Clio webhooks: could not renew ({what})"
    state = state_of(data_root)
    if renewed:
        events.record("settings", "webhook_renewed", "Renewed Clio's webhook subscription", home=Path(data_root or clio.DATA))
    return f"Clio webhooks: {state_say(state)}" + (f", renewed {renewed}" if renewed else "") + "."


def state_say(state: str) -> str:
    return {"on": "subscribed", "waiting": "waiting for Clio to confirm", "unconfirmed": "Clio has this address but the office holds no key for it (an attorney stops it and subscribes again)", "stopped": "suspended by Clio (an attorney subscribes again)",
            "ended": "ended in Clio (an attorney subscribes again)", "off": "not subscribed"}[state]


def stop(data_root: Path | None, who: str, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None, pace=None) -> None:
    """"Stop": deletes each subscription this product made (a subscription is Clio's setting for us, not the firm's data) and forgets the secrets. A
    subscription Clio no longer has is simply forgotten."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    hooks = load(data_root)["hooks"]
    if hooks:
        client = _client(data_root, transport, env, pace)
        for hook_id in list(hooks):
            try:
                client.call("DELETE", f"/webhooks/{hook_id}.json", unsubscribe=True, what="stopping the webhook subscription")
            except clio.ClioError as exc:
                if "isn't in Clio" not in str(exc):
                    raise
            _update(data_root, lambda d, i=hook_id: d["hooks"].pop(i, None))
    _update(data_root, lambda d: d.update(pending=None))
    _change_vault(data_root, env, lambda v: v.update(webhook_secrets={}, webhook_candidates={}))  # every secret: ours, and any a stranger left
    events.record("settings", "webhook_off", "Stopped Clio's webhook subscription", home=Path(data_root or clio.DATA), who=who.strip())


def forget(data_root: Path | None, env: dict[str, str] | None = None) -> None:
    """The connection was ended (disconnect): no secret is kept, and the subscriptions are shown as gone. (disconnect() tries Stop first.)"""
    _update(data_root, lambda d: d.update(hooks={}, pending=None, problem=None))


# -- reading what Clio said changed -------------------------------------------------------------------------------------------------


def read_waiting(data_root: Path | None, clients_root: Path, portal_root: Path | None, transport: httpx.BaseTransport | None = None,
                 env: dict[str, str] | None = None, pace=None) -> str | None:
    """Reads the matters on the "read tonight" list now, through the same queue as the nightly sync: their new documents copied in, and the client's
    portal details refreshed. Returns the line, or None when there was nothing to read. Run by the "clio" job (read_now; when an attorney switched
    "Read Clio uploads within the hour" on), and tonight by the overnight run's Clio step for whatever is still on the list. A matter that isn't
    open, or no longer in a practice area the attorney mapped, is dropped from the list: nothing about it is read."""
    d = load(data_root)
    if (not d["touched"] and not d["lookups"]) or clio.ready(data_root, env):
        return None
    client = _client(data_root, transport, env, pace)
    mapped = clio.state(data_root).get("matters") or {}
    for doc in d["lookups"]:  # a document whose event did not name its matter: one request each
        try:
            found = client.call("GET", f"/documents/{int(doc)}.json", params={"fields": "id,matter{id}"}, what="reading a document").json().get("data") or {}
        except clio.ClioError:
            continue
        mid = _first_id((found.get("matter") or {}).get("id") if isinstance(found.get("matter"), dict) else None)
        if mid in mapped:
            _update(data_root, lambda s, m=mid: s["touched"].setdefault(m, {"case": mapped[m]["case"], "since": clock.stamp(), "n": 0, "kinds": ["document"]})
                    .update(last=clock.stamp(), n=int(s["touched"][m].get("n") or 0) + 1))
    _update(data_root, lambda s: s.update(lookups=[]))
    snap = snapshot(data_root)  # an event that arrives while this reads is a later n: it stays on the list
    remotes, kept = [], set()
    for mid in sorted(snap["touched"]):
        try:
            rc = client.matter(mid)
        except clio.ClioError as exc:  # this matter's problem: it stays on the list for tonight, and the others are read
            clio._error(data_root, str(exc), case=(mapped.get(mid) or {}).get("case"))
            kept.add(mid)
            continue
        if rc is not None:
            remotes.append(rc)
    client._clients = remotes
    clio._mark_ours(data_root, client)
    report = sync.mirror(client, Path(clients_root), only=[rc.id for rc in remotes], home=Path(data_root or clio.DATA)) if remotes else {"clients": {}}
    store = None
    if portal_root is not None:
        from portal.store import PortalStore

        store = PortalStore(portal_root)
    entries = {str(rc.id): clio._link(client.cfg, store, rc, []) for rc in remotes}
    clio._update_state(data_root, lambda s: s.setdefault("matters", {}).update(entries))
    files = sum(len(c["added"]) + len(c["updated"]) for c in report["clients"].values())
    line = f"Clio: read {len(remotes)} matter(s) after Clio's webhook, {files} document(s) copied in ({client.calls} requests)."

    clear(data_root, snap | {"lookups": []}, kept)
    _update(data_root, lambda s: s.update(last_read={"at": clock.stamp(), "line": line}))
    return line


# -- the read within the hour: a "clio" job of the job queue (src/jobs.py) ----------------------------------------------------------


def read_now(data_root: Path | None, portal_root: Path | None, transport: httpx.BaseTransport | None = None, env: dict[str, str] | None = None,
             pace=None, clients_root: Path | None = None) -> str | None:
    """What the "clio" job runs (src/jobs.py): the matters on the list read now, when an attorney switched "Read Clio uploads within the hour" on. Never
    while another Clio step runs (the overnight run's, Sync now, Send now: clio.step_lock): clio.Busy is raised and the job is put back for later;
    tonight's step reads the list anyway. Returns read_waiting's line, or None when nothing was read (switched off, not connected, nothing waiting)."""
    if not clio.settings(data_root).get("uploads_hourly"):
        return None
    root = clients_root or clio.state(data_root).get("clients_root") or os.environ.get("I485_CLIENTS_ROOT") or Path(data_root or clio.DATA).parent / "clients"
    with events.acting("Clio", "system", "connector"), clio.step_lock(data_root):
        return read_waiting(data_root, Path(root), portal_root, transport=transport, env=env, pace=pace)
