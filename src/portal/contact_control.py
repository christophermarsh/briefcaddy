"""Current destination control from a durably consumed accepted credential.

This is not legal identity or consent. Receipts stay in the own existing access
record and bind the original enrollment, accepted attempt and current fences.
"""
from __future__ import annotations

import re
from datetime import datetime

import clock
from . import contact_transitions as access

BINDINGS = ("revision", "destination_sha256", "wording_sha256", "contact_revision", "contact_group_sha256", "enrollment_sha256")


def enrollment(scope, store, client):
    record = access._record(scope, store, client)
    starts = [row for row in record["history"] if row.get("action") == "enrollment_denied"]
    if len(starts) != 1 or not isinstance(starts[0].get("id"), str) or not re.fullmatch(r"[0-9a-f]{32}", starts[0]["id"]):
        raise ValueError("Original enrollment identity is unavailable.")
    profile = store.profile(client)
    return access._hash([str(scope.portal), scope.kind, client, profile.get("created_at"), starts[0]["id"]])


def _accepted(scope, store, entry):
    from .communication_consent import _read
    return _read(scope.check(store, entry["client"]) / "communication-attempts" / (entry["communication"] + ".json"))


def _publish(scope, store, entry, credential_sha256):
    """Internal redemption only, after durable credential removal under locks."""
    from .communication_consent import credential_valid
    if not credential_valid(scope, store, entry) or entry.get("purpose") not in {"sign_in", "verify_contact"}:
        raise ValueError("Current accepted contact credential is required.")
    if credential_sha256 in store._auth()["links"]:
        raise ValueError("Contact credential must be durably consumed first.")
    record = access._record(scope, store, entry["client"])
    receipt = {"version": 1, "state": "verified", "store": str(scope.portal), "client": entry["client"],
               "channel": entry["channel"], "communication": entry["communication"], "purpose": entry["purpose"],
               "credential_sha256": credential_sha256, "accepted_sha256": access._hash(_accepted(scope, store, entry)),
               "verified_at": clock.stamp(), **{key: entry[key] for key in BINDINGS}}
    receipt["id"] = access._hash(receipt)
    record.setdefault("control_receipts", []).append(receipt)
    record["history"].append({"action": "contact_verified", "id": receipt["id"], "channel": entry["channel"],
                               "revision": record["revision"], "at": receipt["verified_at"]})
    access._save(scope, store, entry["client"], record)
    return receipt["id"]


def _valid(scope, store, receipt, current):
    from .communication_consent import _read
    if (not isinstance(receipt, dict) or type(receipt.get("version")) is not int or receipt["version"] != 1 or receipt.get("state") != "verified"
            or receipt.get("store") != str(scope.portal) or receipt.get("client") != current["client"]
            or receipt.get("channel") != current["channel"] or receipt.get("purpose") not in {"sign_in", "verify_contact"}
            or type(receipt.get("revision")) is not int or type(receipt.get("contact_revision")) is not int
            or any(receipt.get(key) != current.get(key) for key in BINDINGS)
            or not isinstance(receipt.get("communication"), str) or not re.fullmatch(r"[0-9a-f]{32}", receipt["communication"])
            or not isinstance(receipt.get("credential_sha256"), str) or not re.fullmatch(r"[0-9a-f]{64}", receipt["credential_sha256"])
            or receipt.get("id") != access._hash({key: value for key, value in receipt.items() if key != "id"})):
        return False
    at = datetime.fromisoformat(receipt["verified_at"])
    if at.tzinfo is None or at > clock.now():
        return False
    attempt = _read(scope.check(store, current["client"]) / "communication-attempts" / (receipt["communication"] + ".json"))
    created = datetime.fromisoformat(attempt["created_at"]) if attempt else None
    recorded = datetime.fromisoformat(attempt["recorded_at"]) if attempt else None
    return bool(attempt and type(attempt.get("version")) is int and attempt["version"] == 1
                and attempt.get("id") == receipt["communication"]
                and created is not None and recorded is not None and created.tzinfo is not None and recorded.tzinfo is not None and created <= recorded <= at
                and attempt.get("state") == "accepted" and attempt.get("provider_status") == "sent"
                and receipt.get("accepted_sha256") == access._hash(attempt)
                and all(attempt.get(key) == receipt.get(key) for key in (*BINDINGS, "client", "store", "channel", "purpose")))


def status(scope, store, client, channel, *, receipt_id=None, closed_artifacts=False):
    """Truthful own current status; callers must enforce their route authority."""
    from .communication_consent import _base_eligible, gate
    with gate(scope):
        try:
            current = _base_eligible(scope, store, client, channel, closed_artifacts=closed_artifacts)
            if not current["allowed"]:
                return {"verified": False, "reason": current["reason"]}
            current.update(client=client, channel=channel, enrollment_sha256=enrollment(scope, store, client))
            record = access._record(scope, store, client)
            for receipt in reversed(record.get("control_receipts", [])):
                if (receipt_id is None or receipt.get("id") == receipt_id) and _valid(scope, store, receipt, current):
                    return {"verified": True, "channel": channel, "receipt": receipt["id"], "verified_at": receipt["verified_at"]}
            return {"verified": False, "reason": "contact_control_required"}
        except (ValueError, TypeError, KeyError, LookupError, OSError, PermissionError):
            return {"verified": False, "reason": "contact_control_unavailable"}


def session_valid(scope, store, entry, *, closed_artifacts=False, allow_verification=False):
    from .communication_consent import credential_valid
    if (not isinstance(entry, dict) or entry.get("purpose") not in ({"sign_in", "verify_contact"} if allow_verification else {"sign_in"})
            or not isinstance(entry.get("control_receipt"), str)
            or not credential_valid(scope, store, entry, closed_artifacts=closed_artifacts)):
        return False
    receipt = next((row for row in access._record(scope, store, entry["client"]).get("control_receipts", [])
                    if row.get("id") == entry["control_receipt"]), None)
    return bool(receipt and all(receipt.get(key) == entry.get(key) for key in (*BINDINGS, "communication", "channel", "client", "purpose"))
                and status(scope, store, entry["client"], entry["channel"], receipt_id=entry["control_receipt"],
                           closed_artifacts=closed_artifacts)["verified"])


def staff_view(scope, store, client, *, actor_email):
    from .communication_consent import _open, gate, staff
    with gate(scope):
        _, _, case = _open(scope, store, client)
        staff(scope, actor_email, case=case)
        return {channel: status(scope, store, client, channel) for channel in sorted(access.contacts.CHANNELS)}
