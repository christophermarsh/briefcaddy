"""Case-scoped STOP cleanup under the existing Q1/communication gates.

Retired opaque identities prevent provider replay after personal metadata is
removed. No lock is removed; uncertain ownership remains explicitly unresolved.
"""
import re

from .communication_consent import gate, _read, _revoke, _record
from .queue_bridge import _safe, _atomic
from . import opt_out as stop

TEMP = re.compile(r"(?P<base>[0-9a-f]{64}|queue)\.json\.[0-9a-f]{16}\.tmp")


def _members(value):
    return {(item["store"], item["client"]) for item in value.get("members", [])}


def _cleanup_temps(scope, selected, affected):
    """Only exact complete temporary copies with canonical survivor proof."""
    removed, left = 0, []
    root = stop.folder(scope)
    for directory in (root, root / "receipts", root / "destinations"):
        _safe(directory)
        if not directory.exists():
            continue
        for path in directory.iterdir():
            match = TEMP.fullmatch(path.name)
            if not match:
                continue
            try:
                _safe(path)
                value = _read(path)
                if not isinstance(value, dict):
                    raise ValueError("Temporary STOP proof is unavailable")
                base = match["base"]
                if directory == root / "receipts" and base != "queue":
                    stop._validate_receipt(scope, base, value)
                    canonical = stop._receipt(scope, base)
                    if not (_members(value) & selected):
                        continue
                    if not canonical:
                        raise ValueError("Selected temporary receipt has no canonical survivor proof")
                    survivors = _members(value) - selected
                    if canonical["state"] == "retired":
                        if survivors:
                            raise ValueError("Unproven surviving temporary members")
                    elif (not survivors <= _members(canonical)
                          or any(value.get(key) != canonical.get(key) for key in ("channel", "destination_sha256", "payload_sha256"))):
                        raise ValueError("Unproven surviving temporary receipt")
                elif (directory == root and base == "queue") or (directory == root / "destinations" and base != "queue"):
                    pending = value.get("pending") if isinstance(value, dict) else None
                    if (not isinstance(pending, list) or value.get("version") != 1
                            or any(not isinstance(key, str) or not stop.HASH.fullmatch(key) for key in pending)
                            or len(set(pending)) != len(pending)):
                        raise ValueError("Damaged temporary index")
                    if not set(pending) & affected:
                        continue
                    target = root / "queue.json" if base == "queue" else root / "destinations" / (base + ".json")
                    canonical = _read(target, {"version": 1, "pending": []})
                    retained = set(canonical.get("pending", []))
                    retired = {key for key in pending if stop._receipt(scope, key) and stop._receipt(scope, key)["state"] == "retired"}
                    if not set(pending) - retired <= retained:
                        raise ValueError("Unproven surviving temporary index")
                    for key in set(pending) - retired:
                        proof = stop._receipt(scope, key)
                        if not proof or base != "queue" and proof.get("destination_sha256") != base:
                            raise ValueError("Unproven temporary index association")
                else:
                    raise ValueError("Unexpected temporary ownership")
                path.unlink()
                removed += 1
            except (ValueError, OSError, TypeError, KeyError):
                left.append("An interrupted STOP write cannot be assigned to this purge with intact survivor proof; it remains unresolved.")
    return removed, left


def purge_members(scope, selected):
    """selected is explicit {(store kind, client id)}, never a bare ID set."""
    selected = set(selected)
    if any(kind not in ("client", "prospect") or not isinstance(client, str) or not stop.CLIENT_ID.fullmatch(client) for kind, client in selected):
        raise ValueError("Invalid communication purge scope")
    with gate(scope), stop._lock(scope):
        inventory = stop._inventory(scope)  # strict complete inventory before any removal
        destinations = {stop.phone_hash(profile.get("phone") or "") for current, _, client, profile in inventory if (current.kind, client) in selected}
        survivors = [(current.kind, client, stop.phone_hash(profile.get("phone") or "")) for current, _, client, profile in inventory if (current.kind, client) not in selected]
        root = stop.folder(scope)
        receipt_dir = _safe(root / "receipts")
        receipts = []
        if receipt_dir.exists():
            for path in receipt_dir.iterdir():
                _safe(path)
                if TEMP.fullmatch(path.name):
                    continue
                if not re.fullmatch(r"[0-9a-f]{64}\.json", path.name) or not path.is_file():
                    raise ValueError("Unassignable STOP receipt inventory")
                receipts.append(stop._receipt(scope, path.stem))
        stop._index(scope)  # corrupted queue is not an empty queue
        affected, removed = set(), 0
        for receipt in receipts:
            if receipt["state"] == "retired":
                continue
            if not (_members(receipt) & selected or receipt["destination_sha256"] in destinations):
                continue
            key = receipt["id"]
            affected.add(key)
            # Q1 cannot clear STOP denial then crash while the old profile and
            # grant still exist. Persist association before the existing
            # verified-operation revoke; no unrelated channel grant is changed.
            chosen = [(current, store, client) for current, store, client, profile in inventory
                      if (current.kind, client) in selected and stop.phone_hash(profile.get("phone") or "") == receipt["destination_sha256"]]
            for current, store, client in chosen:
                if (current.kind, client) not in _members(receipt):
                    if receipt["state"] == "applied":
                        raise ValueError("Completed historical STOP has no proven selected-case effect; cleanup remains unresolved")
                    receipt["members"].append({"store": current.kind, "client": client, "state": "pending"})
                    _atomic(receipt_dir / (key + ".json"), receipt)
                _revoke(current, store, client, [receipt["channel"]], requested_at=receipt["received_at"], operation_id=key)
                if (_record(current, store, client)["channels"].get(receipt["channel"]) or {}).get("state") != "revoked":
                    raise ValueError("Historical STOP operation cannot establish current purge denial")
            remaining = [member for member in receipt["members"] if (member["store"], member["client"]) not in selected]
            matching = [(kind, client) for kind, client, dest in survivors if dest == receipt["destination_sha256"]]
            if remaining or matching:
                if receipt["state"] == "pending":
                    for kind, client in matching:
                        if (kind, client) not in _members({"members": remaining}):
                            remaining.append({"store": kind, "client": client, "state": "pending"})
                receipt["members"] = remaining
                _atomic(receipt_dir / (key + ".json"), receipt)
            else:
                # Remove destination/queue intent before losing its destination
                # binding. An interrupted retired publication can safely retry.
                stop._retire(scope, receipt)
            removed += 1
        temporary, left = _cleanup_temps(scope, selected, affected)
        return {"removed": removed + temporary, "left": left}
