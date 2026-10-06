"""Pure, bounded staff case lists. No files, account loads or index queries here."""
from __future__ import annotations

import hashlib
import math

from case_assignment import ROLES, Unavailable, _email

SCOPES = ("mine", "unassigned", "all", "needs_attention")


def _accounts(rows):
    if not isinstance(rows, (list, tuple)):
        raise Unavailable("Current staff accounts are unavailable.")
    out = {}
    try:
        for row in rows:
            if not isinstance(row, dict) or type(row.get("active")) is not bool:
                raise ValueError()
            email = _email(row.get("email"))
            if email in out:
                raise ValueError()
            out[email] = row
    except ValueError as exc:
        raise Unavailable("Current staff accounts are unavailable.") from exc
    return out


def _visible(user, entry):
    # Same list rule as Roster.hides/restricted.visible_to; the current account
    # role is used, never the request's or the assignment's historical role.
    if type(entry.get("closed")) is not bool or not isinstance(entry.get("named"), list) or any(not isinstance(e, str) for e in entry["named"]):
        return False
    if user["role"] == "attorney":
        return True
    if entry.get("unwritten"):
        return False
    if entry.get("closed"):
        return user["email"] in (entry.get("named") or [])
    return True


def _id(email):
    return hashlib.sha256(("person|" + email).encode()).hexdigest()[:12]


def _assignment(entry, accounts):
    snapshot = entry.get("assignment")
    unavailable = {"state": "unavailable", "revision": None, "assignee": None, "audit_pending": True}
    if not isinstance(snapshot, dict) or snapshot.get("state") not in ("assigned", "unassigned", "unavailable"):
        return unavailable
    if snapshot["state"] == "unavailable":
        return unavailable
    if type(snapshot.get("revision")) is not int or snapshot["revision"] < 0 or type(snapshot.get("audit_pending")) is not bool:
        return unavailable
    target = snapshot.get("assignee")
    if snapshot["state"] == "unassigned":
        return {"state": "unassigned", "revision": snapshot["revision"], "assignee": None, "audit_pending": snapshot["audit_pending"]} if target is None else unavailable
    if not isinstance(target, dict) or not isinstance(target.get("email"), str):
        return unavailable
    try:
        email = _email(target["email"])
    except ValueError:
        return unavailable
    current = accounts.get(email)
    eligible = bool(current and current.get("active") is True and current.get("role") in ROLES and _visible(current | {"email": email}, entry))
    name = (current or {}).get("name") or target.get("name")
    role = (current or {}).get("role") if eligible else target.get("role")
    if not isinstance(name, str) or not name.strip() or role not in ROLES:
        return unavailable
    # Public summary carries the existing hashed person id, not an email.
    return {"state": "assigned" if eligible else "needs_attention", "revision": snapshot["revision"],
            "assignee": {"id": _id(email), "name": name, "role": role, "eligible": eligible}, "audit_pending": snapshot["audit_pending"]}


def page(entries, actor, accounts, query=None):
    """ACL -> open/ended -> search/person -> scope counts/filter -> bounded page.

    Caller supplies synchronized cached entries and ONE fresh accounts snapshot.
    Counts contain no inaccessible rows. No account/ACL authority is inferred
    from saved actor or assignee snapshots. Fresh routes remain authoritative.
    """
    query = query or {}
    staff = _accounts(accounts)
    try:
        email = _email((actor or {}).get("email"))
    except ValueError as exc:
        raise PermissionError("Sign in with an active staff account.") from exc
    user = staff.get(email)
    if not user or user.get("active") is not True or user.get("role") not in ROLES:
        raise PermissionError("Sign in with an active attorney or paralegal account.")
    user = user | {"email": email}
    scope = query.get("scope", "mine")
    ended = query.get("ended", "open")
    if scope not in SCOPES or ended not in ("open", "ended", "all"):
        raise ValueError("Choose a case scope and open/ended filter.")
    try:
        requested_page = int(query.get("page", 1))
        size = int(query.get("size", 50))
        if (isinstance(query.get("page"), (bool, float)) or isinstance(query.get("size"), (bool, float))
                or requested_page < 1 or size < 1):
            raise ValueError()
    except (TypeError, ValueError, OverflowError) as exc:
        raise ValueError("Page and size must be positive whole numbers.") from exc
    size = min(size, 200)
    needle = query.get("q", "")
    if not isinstance(needle, str) or len(needle) > 200:
        raise ValueError("Search uses at most 200 characters.")
    needle = needle.strip().casefold()
    chosen = query.get("assignee")
    if chosen is not None:
        if not isinstance(chosen, str):
            raise ValueError("Choose an active staff member.")
        # Already the existing public person id. Raw email never leaves helper.
        candidates = [e for e, u in staff.items() if u.get("active") is True and u.get("role") in ROLES and _id(e) == chosen]
        if len(candidates) != 1:
            raise ValueError("Choose an active staff member.")
    counts = {key: 0 for key in SCOPES}
    kept = []
    for case, entry in entries.items():
        if not isinstance(case, str) or not isinstance(entry, dict) or not isinstance(entry.get("row"), dict) or not _visible(user, entry):
            continue
        row = entry["row"]
        is_ended = bool(row.get("end"))
        if ended != "all" and (ended == "ended") != is_ended:
            continue
        summary = row.get("summary") if isinstance(row.get("summary"), dict) else {}
        name = summary.get("name") if isinstance(summary.get("name"), str) and summary["name"].strip() else case
        if needle and needle not in f"{case} {name}".casefold():
            continue
        assignment = _assignment(entry, staff)
        person = assignment["assignee"]
        if chosen and (not person or person.get("id") != chosen):
            continue
        matches = {"all": True, "mine": bool(person and person.get("id") == _id(email)),
                   "unassigned": assignment["state"] == "unassigned", "needs_attention": assignment["state"] in ("needs_attention", "unavailable")}
        for key, matches_scope in matches.items():
            counts[key] += int(matches_scope)
        if matches[scope]:
            # Explicit compact DTO: no graphs, messages, histories, operation IDs,
            # staff emails, named ACL list, or raw internal row/entry fields.
            kept.append({"id": case, "name": name, "stage": row.get("stage"), "office": entry.get("office"),
                         "restricted": bool(entry.get("closed")), "end": row.get("end"), "assignment": assignment})
    kept.sort(key=lambda row: (str(row["name"]).casefold(), row["id"]))
    total = len(kept)
    pages = max(1, math.ceil(total / size))
    selected_page = min(requested_page, pages)
    return {"scope": scope, "ended": ended, "counts": counts, "total": total, "page": selected_page, "pages": pages, "size": size,
            "clients": kept[(selected_page - 1) * size:selected_page * size]}
