"""Authenticated Twilio STOP receipts and a bounded internal retry pump.

No outbound reply, automatic START grant, SDK REST client or redelivery
assumption. Lock order: communication gate -> ingest lock. Ingest publishes
under ingest lock alone, releases it, then optionally attempts the gate.
"""
from __future__ import annotations

import hashlib
from datetime import datetime
import json
from pathlib import Path
import re
from urllib.parse import parse_qsl, urlsplit

import clock
import firmsecrets
import oslock
from .communication_consent import Scope, _read, _revoke, gate
from .queue_bridge import _safe, _atomic
from .store import CLIENT_ID, PortalStore

HASH = re.compile(r"[0-9a-f]{64}")
STOP = {"STOP", "STOPALL", "UNSUBSCRIBE", "CANCEL", "END", "QUIT", "REVOKE", "OPTOUT"}
MAX_BODY = 16384
MAX_PENDING = 1024
MAX_CONTACTS = 5000  # bounded headroom above the 2000-case pilot acceptance


def folder(scope):
    return _safe(scope.data / "communication-stop")


def _lock(scope):
    return oslock.locked(folder(scope) / ".ingest.lock", timeout=5)


def _digest(value):
    return hashlib.sha256(json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()


def phone_hash(phone):
    return hashlib.sha256(str(phone).strip().encode()).hexdigest()


def _index(scope):
    index = _read(folder(scope) / "queue.json", {"version": 1, "pending": []})
    pending = index.get("pending")
    if index.get("version") != 1 or not isinstance(pending, list) or len(pending) > MAX_PENDING or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in pending) or len(set(pending)) != len(pending):
        raise ValueError("STOP retry queue is damaged.")
    return index


def _receipt(scope, key):
    if not isinstance(key, str) or not HASH.fullmatch(key):
        raise ValueError("Invalid STOP receipt identity.")
    value = _read(folder(scope) / "receipts" / (key + ".json"))
    return _validate_receipt(scope, key, value)


def _validate_receipt(scope, key, value):
    if value is not None and value.get("state") == "retired":
        if (set(value) != {"version", "id", "state", "installation", "retired_at"}
                or value.get("version") != 1 or value.get("id") != key
                or value.get("installation") != str(scope.root)):
            raise ValueError("STOP retired identity is damaged.")
        if not isinstance(value.get("retired_at"), str):
            raise ValueError("STOP retired identity time is damaged.")
        retired = datetime.fromisoformat(value["retired_at"])
        if retired.tzinfo is None or retired > clock.utcnow():
            raise ValueError("STOP retired identity time is damaged.")
        return value
    if value is not None:
        if (value.get("version") != 1 or value.get("id") != key or value.get("state") not in ("pending", "applied")
                or value.get("installation") != str(scope.root)
                or value.get("provider") != "twilio"
                or not isinstance(value.get("account_sid"), str) or not re.fullmatch(r"AC[0-9a-fA-F]{32}", value["account_sid"])
                or not isinstance(value.get("message_sid"), str) or not re.fullmatch(r"SM[0-9a-fA-F]{32}", value["message_sid"])
                or _digest([value["account_sid"], value["message_sid"]]) != key
                or value.get("channel") not in ("sms", "whatsapp")
                or any(not isinstance(value.get(k), str) or not HASH.fullmatch(value[k]) for k in ("destination_sha256", "payload_sha256", "signature_sha256", "callback_sha256"))
                or not isinstance(value.get("members"), list) or not isinstance(value.get("received_at"), str)):
            raise ValueError("STOP receipt is damaged.")
        if any(not isinstance(m, dict) or m.get("store") not in ("client", "prospect") or not isinstance(m.get("client"), str)
               or not CLIENT_ID.fullmatch(m["client"]) or m.get("state") not in ("pending", "applied", "no_longer_matching") for m in value["members"]):
            raise ValueError("STOP case association is damaged.")
        if len({(m["store"], m["client"]) for m in value["members"]}) != len(value["members"]):
            raise ValueError("STOP case association is ambiguous.")
        received = datetime.fromisoformat(value["received_at"])
        if received.tzinfo is None or received > clock.utcnow():
            raise ValueError("STOP receipt time is damaged.")
        if value["state"] == "applied":
            if not isinstance(value.get("effective_at"), str):
                raise ValueError("STOP completion proof is damaged.")
            effective = datetime.fromisoformat(value.get("effective_at", ""))
            if effective.tzinfo is None or not received <= effective <= clock.utcnow() or any(m["state"] == "pending" for m in value["members"]):
                raise ValueError("STOP completion proof is damaged.")
    return value


def pending_for(scope, profile):
    """One destination index read; corrupt or incomplete publication denies."""
    path = folder(scope) / "destinations" / (phone_hash(profile.get("phone") or "") + ".json")
    value = _read(path)
    if value is None:
        return False
    pending = value.get("pending")
    if value.get("version") != 1 or not isinstance(pending, list) or len(pending) > MAX_PENDING or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in pending):
        raise ValueError("STOP destination intent is damaged.")
    return bool(pending)


def outbound_ready(scope, channel, *, env):
    """Built-in phone provider must have usable authenticated STOP settings."""
    try:
        url = urlsplit(env.get("TWILIO_STOP_WEBHOOK_URL", ""))
        sender = env.get("TWILIO_SMS_FROM" if channel == "sms" else "TWILIO_WHATSAPP_FROM", "").removeprefix("whatsapp:")
        return bool(channel in {"sms", "whatsapp"} and url.scheme == "https" and url.hostname
                    and not any((url.username, url.password, url.fragment, url.query)) and url.path == "/api/communication/stop/twilio"
                    and re.fullmatch(r"AC[0-9a-fA-F]{32}", env.get("TWILIO_ACCOUNT_SID", ""))
                    and re.fullmatch(r"\+[1-9][0-9]{7,14}", sender)
                    and firmsecrets.get("twilio.auth_token", env=env, data_root=scope.data, strict=True))
    except (ValueError, OSError, TypeError, AttributeError):
        return False


def verify(scope, body, signature, *, env):
    """Only official SDK verifies the complete bounded form and configured URL."""
    from twilio.request_validator import RequestValidator
    if not isinstance(body, bytes) or not body or len(body) > MAX_BODY or not isinstance(signature, str):
        raise ValueError("Invalid STOP webhook body.")
    url = env.get("TWILIO_STOP_WEBHOOK_URL", "")
    parsed = urlsplit(url)
    if parsed.scheme != "https" or not parsed.hostname or parsed.username or parsed.password or parsed.fragment or parsed.query or parsed.path != "/api/communication/stop/twilio":
        raise ValueError("An exact HTTPS STOP webhook URL is required.")
    pairs = parse_qsl(body.decode("utf-8"), keep_blank_values=True, strict_parsing=True, max_num_fields=100)
    if len({k for k, _ in pairs}) != len(pairs):
        raise ValueError("Duplicate STOP webhook fields are unsupported.")
    params = dict(pairs)  # include evolving provider fields, not a whitelist
    token = firmsecrets.get("twilio.auth_token", env=env, data_root=scope.data, strict=True)
    if not token or not RequestValidator(token).validate(url, params, signature):
        raise PermissionError("Invalid STOP webhook signature.")
    if params.get("AccountSid") != env.get("TWILIO_ACCOUNT_SID") or not re.fullmatch(r"AC[0-9a-fA-F]{32}", params.get("AccountSid", "")) or not re.fullmatch(r"SM[0-9a-fA-F]{32}", params.get("MessageSid", "")):
        raise PermissionError("STOP webhook account or identity is unavailable.")
    sender, recipient = params.get("From", ""), params.get("To", "")
    channel = "whatsapp" if sender.startswith("whatsapp:") else "sms"
    phone = sender.removeprefix("whatsapp:")
    expected = env.get("TWILIO_WHATSAPP_FROM" if channel == "whatsapp" else "TWILIO_SMS_FROM", "")
    expected = ("whatsapp:" + expected.removeprefix("whatsapp:")) if channel == "whatsapp" else expected
    if not re.fullmatch(r"\+[1-9][0-9]{7,14}", phone) or not expected or recipient != expected:
        raise PermissionError("STOP webhook destination/channel binding is unavailable.")
    recognized = params.get("OptOutType") == "STOP" or (not params.get("OptOutType") and params.get("Body", "").strip().upper() in STOP)
    if not recognized:
        return None  # HELP/START never grants, mutates or sends
    return {"version": 1, "id": _digest([params["AccountSid"], params["MessageSid"]]),
            "installation": str(scope.root), "provider": "twilio", "account_sid": params["AccountSid"], "message_sid": params["MessageSid"],
            "signature_sha256": hashlib.sha256(signature.encode()).hexdigest(), "callback_sha256": hashlib.sha256(url.encode()).hexdigest(),
            "channel": channel, "destination_sha256": phone_hash(phone), "payload_sha256": _digest(params),
            "state": "pending", "received_at": clock.stamp(), "members": []}


def ingest(scope, body, signature, *, env, apply_now=True):
    verified = verify(scope, body, signature, env=env)
    if verified is None:
        return {"state": "ignored"}
    key = verified["id"]
    with _lock(scope):
        current = _receipt(scope, key)
        if current is not None and current["state"] == "retired":
            return {"state": "retired", "receipt": key, "duplicate": True}
        if current is not None and any(current[k] != verified[k] for k in ("channel", "destination_sha256", "payload_sha256")):
            raise ValueError("STOP identity was reused with different content.")
        if current is not None and current["state"] == "applied":
            return {"state": "applied", "receipt": key, "duplicate": True}
        index = _index(scope)
        if key not in index["pending"] and len(index["pending"]) >= MAX_PENDING:
            raise ValueError("STOP retry queue is full; request was not acknowledged.")
        target = folder(scope) / "destinations" / (verified["destination_sha256"] + ".json")
        intent = _read(target, {"version": 1, "pending": []})
        if intent.get("version") != 1 or not isinstance(intent.get("pending"), list) or len(intent["pending"]) >= MAX_PENDING or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in intent["pending"]):
            raise ValueError("STOP destination intent is damaged.")
        if key not in intent["pending"]:
            intent["pending"].append(key)
        _atomic(target, intent)  # durable denying intent before receipt/queue
        _atomic(folder(scope) / "receipts" / (key + ".json"), current or verified)
        if key not in index["pending"]:
            index["pending"].append(key)
        _atomic(folder(scope) / "queue.json", index)  # ACK only after retry work durable
    if apply_now:
        try:
            process(scope, key, timeout=0)
        except (oslock.LockBusy, ValueError, OSError, PermissionError):
            pass  # persisted queue, not assumed provider redelivery
    result = _receipt(scope, key)
    return {"state": result["state"], "receipt": key}


def _stores(scope):
    import prospects
    return [(Scope(scope.root, scope.data / "portal", scope.data / "clients"), PortalStore(scope.data / "portal")),
            (Scope(scope.root, scope.data / "portal/prospects", scope.data / "prospects"), prospects.store(scope.data / "portal"))]


def _inventory(scope):
    """Complete strict profiles; missing/damaged inventory is never no match."""
    rows = []
    for current, store in _stores(scope):
        base = _safe(store.root / "clients")
        children = list(base.iterdir())
        if len(children) > MAX_CONTACTS:
            raise ValueError("STOP contact inventory exceeds the bounded envelope.")
        for child in children:
            _safe(child)
            if not child.is_dir() or not CLIENT_ID.fullmatch(child.name):
                raise ValueError("STOP contact inventory is damaged.")
            profile = _read(child / "profile.json")
            if (not isinstance(profile, dict) or profile.get("id") != child.name
                    or profile.get("phone") is not None and not isinstance(profile["phone"], str)):
                raise ValueError("STOP contact profile is damaged or unavailable.")
            rows.append((current, store, child.name, profile))
    return rows


def process(scope, key, *, timeout=0):
    with gate(scope, timeout=timeout):
        with _lock(scope):
            receipt = _receipt(scope, key)
            if receipt is None:
                raise ValueError("STOP retry proof is unavailable.")
            if receipt["state"] == "retired":
                index = _index(scope)
                index["pending"] = [item for item in index["pending"] if item != key]
                _atomic(folder(scope) / "queue.json", index)
                return receipt  # canonical reread, no case/credential effects
            if receipt["state"] == "applied":
                _dequeue(scope, receipt)
                return receipt
        targets = []
        inventory = _inventory(scope)
        for current, store, client, profile in inventory:
            if phone_hash(profile.get("phone") or "") == receipt["destination_sha256"] and _safe(current.cases / client).is_dir():
                targets.append((current, store, client))
        # Association is durable BEFORE any partial per-client mutation.
        for current, store, client in targets:
            if not any(m["store"] == current.kind and m["client"] == client for m in receipt["members"]):
                receipt["members"].append({"store": current.kind, "client": client, "state": "pending"})
        identities = {(current.kind, client) for current, _, client in targets}
        for member in receipt["members"]:
            if member["state"] == "pending" and (member["store"], member["client"]) not in identities:
                member["state"] = "no_longer_matching"
        with _lock(scope):
            _atomic(folder(scope) / "receipts" / (key + ".json"), receipt)
        for current, store, client in targets:
            member = next(m for m in receipt["members"] if m["store"] == current.kind and m["client"] == client)
            if member["state"] == "applied":
                continue
            _revoke(current, store, client, [receipt["channel"]], requested_at=receipt["received_at"], operation_id=key)
            member["state"] = "applied"
            with _lock(scope):
                _atomic(folder(scope) / "receipts" / (key + ".json"), receipt)
        receipt["state"] = "applied"
        receipt["effective_at"] = clock.stamp()
        with _lock(scope):
            _atomic(folder(scope) / "receipts" / (key + ".json"), receipt)
            if not any(phone_hash(profile.get("phone") or "") == receipt["destination_sha256"] for _, _, _, profile in inventory):
                receipt = _retire(scope, receipt)
            else:
                _dequeue(scope, receipt)
        return receipt


def _retire(scope, receipt):
    """Caller holds communication/ingest locks and proved no surviving owner."""
    _dequeue(scope, receipt)
    target = folder(scope) / "destinations" / (receipt["destination_sha256"] + ".json")
    intent = _read(target)
    if intent is not None and intent.get("pending") == []:
        _safe(target).unlink()
    value = {"version": 1, "id": receipt["id"], "state": "retired", "installation": str(scope.root), "retired_at": clock.stamp()}
    _atomic(folder(scope) / "receipts" / (receipt["id"] + ".json"), value)
    return value


def _dequeue(scope, receipt):
    target = folder(scope) / "destinations" / (receipt["destination_sha256"] + ".json")
    intent = _read(target, {"version": 1, "pending": []})
    if intent.get("version") != 1 or not isinstance(intent.get("pending"), list) or any(not isinstance(x, str) or not HASH.fullmatch(x) for x in intent["pending"]):
        raise ValueError("STOP destination intent is damaged.")
    intent["pending"] = [x for x in intent["pending"] if x != receipt["id"]]
    _atomic(target, intent)
    index = _index(scope)
    index["pending"] = [x for x in index["pending"] if x != receipt["id"]]
    _atomic(folder(scope) / "queue.json", index)  # last: interrupted cleanup stays retryable


def pump(ctx, *, limit=4):
    """Before case locks; FIFO bounded work and rotation prevent starvation."""
    if ctx.portal is None or not (Path(ctx.clients).parent / "communication-stop/queue.json").exists():
        return {"processed": 0, "pending": 0}
    scope = Scope(Path(ctx.clients).parent.parent, ctx.portal, ctx.clients)
    with _lock(scope):
        queue = _index(scope)["pending"][:max(1, min(int(limit), 4))]
    completed, unresolved = 0, 0
    for key in queue:
        try:
            process(scope, key, timeout=0)
            completed += 1
        except (oslock.LockBusy, ValueError, OSError, PermissionError):
            unresolved += 1
            with _lock(scope):
                index = _index(scope)
                if key in index["pending"]:
                    index["pending"].remove(key)
                    index["pending"].append(key)
                    _atomic(folder(scope) / "queue.json", index)
    with _lock(scope):
        pending = len(_index(scope)["pending"])
    return {"processed": completed, "pending": pending, "unresolved": unresolved}
