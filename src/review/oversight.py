"""What the attorney can see of what the office did: the access log, the view log, the cases that send automatic messages.

Three logs already exist, written where the work happens, and the attorney reads them here on a screen; nothing is written twice:
  - the staff access log (review/auth.py, <accounts>_access.jsonl): sign-ins, failed sign-ins, lockouts, password resets and changes,
    account changes, changes to who may open a case;
  - the rule approvals (src/rules/approval.py) and the firm's policy edits (src/rules/firm_policies.py), read from their own files;
  - the view log (review/server.py, review_views.jsonl): every opening of a case, scan, form, packet, plus "link shown".

The view log grows with every opening (1,800 cases and months of staff: hundreds of thousands of rows), so a question is never answered
by reading the file. RowIndex keeps, in memory, one compact entry per row (where the row starts in the file, when, who, what kind, which
case, restricted or not), built once and then only extended by the rows appended since (a request reads from the byte where the last
one stopped). A page of 50 rows is read from the file by seeking to those 50 rows; filters (person, kind, case, dates) and totals are
answered from the index alone. An entry costs about 40 bytes, so a million rows is 40 MB. Nothing is stored beside the log: after a
restart the first question reads the file once (about a second for 200,000 rows) and every later one is instant. If the file shrinks or
its first bytes change (rotated, replaced), the index is rebuilt.

Everything here is the attorney's. The server refuses a paralegal before it calls in (review/server.py), and every cell written to a
CSV file that starts with = + - @ gets a leading apostrophe (review/reports._cell) so a name can't run as a spreadsheet formula.
"""

from __future__ import annotations

import json
import threading
from array import array
from datetime import date, datetime, time as day_time, timedelta
from pathlib import Path
from typing import Any, Callable, Iterable

import clock

PAGE = 50  # rows a page of any of these screens shows

# What an access-log event is called on the screen, and which kind (the filter) it falls under.
KINDS = {"sign_in": "Sign-ins and sign-outs", "failed": "Failed sign-ins", "lockout": "Lockouts", "password": "Password resets and changes",
         "account": "Account changes", "case_access": "Who may open a case", "rule": "Rule and policy approvals", "policy": "Policy edits",
         "link": "Client sign-in link shown", "second_factor": "Codes from an authenticator app", "export": "Exports of the firm's data",
         "calendar": "Calendar addresses", "support": "Support sessions"}
_KIND_OF = {"signed_in": "sign_in", "signed_out": "sign_in", "sign_in_failed": "failed", "account_locked": "lockout", "password_reset": "password",
            "password_changed": "password", "password_rehashed": "password", "account_added": "account", "account_changed": "account",
            "case_access": "case_access",
            # the code from an authenticator app (review/auth.py)
            "account_locked_again": "lockout", "password_accepted": "sign_in", "code_set_up": "second_factor",
            "second_factor_changed": "second_factor", "devices_forgotten": "second_factor", "signed_out_for_code": "second_factor",
            "code_key_rotated": "second_factor",
            # the first attorney, made from the screen (review/auth.py create_first)
            "first_attorney_created": "account", "setup_refused": "failed",
            # the export of all of the firm's data (tools/export_firm.py --everything)
            "data_exported": "export",
            # the calendar feed (src/calendar_feed.py): an address made or turned off, and one read (a row a day for each address)
            "calendar_address_made": "calendar", "calendar_address_revoked": "calendar", "calendar_feed": "calendar",
            # the alert after the third lockout in a day of one account or one address (review/auth.py, review/server.py limited)
            "lockout_alert": "lockout",
            # the provider's support person (src/support.py): let in by an attorney, every request it made, the end of its session
            "support_let_in": "support", "support_request": "support", "support_ended": "support"}
# the person at the keyboard is the row's email for these; for the others the row's "by" is, and the email is who it was done to
_SELF = {"signed_in", "signed_out", "sign_in_failed", "account_locked", "password_changed", "password_rehashed", "case_access",
         "account_locked_again", "password_accepted", "code_set_up", "first_attorney_created", "setup_refused", "data_exported",
         "calendar_address_made", "calendar_address_revoked", "calendar_feed", "lockout_alert", "support_request"}
_PROVIDERS = {"microsoft": "Microsoft", "google": "Google"}
_FAILED = {"password": "Wrong password", "locked": "Tried to sign in while the account was locked", "unknown": "Tried an email that is not on the staff list",
           "changed": "Sign-in failed (the password was changed at that moment)", "disabled": "Tried to sign in to an account that is turned off",
           "code": "Wrong code at sign-in"}
# how a sign-in finished, when it wasn't the password alone (review/auth.py verify_code, enrol_finish, sign_in)
_HOW = {"code": "Signed in with the app's code", "recovery code": "Signed in with a recovery code", "remembered device": "Signed in on a remembered computer"}
_CASE_ACTIONS = {"mark": "Restricted the case", "unmark": "Lifted the restriction", "name": "Let {person} open the case", "unname": "Took {person} off the case",
                 "messages_on": "Switched automatic messages on", "messages_off": "Switched automatic messages off"}


# -- dates -------------------------------------------------------------------------------------------------------


def us_when(value: Any, tz=None) -> str:
    """A stored stamp as the screen writes it, in the firm's zone: "10/03/2026 3:05 PM". A file of 200,000 rows passes the zone once (clock.zone()
    reads the settings file's modified time on every call, which on a network drive is most of the time a CSV takes)."""
    at = clock.parse(value)
    at = at.astimezone(tz or clock.zone()) if at else None
    return f"{at:%m/%d/%Y} {at.hour % 12 or 12}:{at:%M} {'AM' if at.hour < 12 else 'PM'}" if at else ""


def _span(start: str, end: str) -> tuple[float | None, float | None]:
    """Two dates ("2026-10-01", the firm's days, either may be empty) as the moments the range runs from and to: the start of the first day
    to the end of the last. A date that isn't one is refused in words."""
    out: list[float | None] = []
    for text, plus in ((start, 0), (end, 1)):
        text = (text or "").strip()
        if not text:
            out.append(None)
            continue
        try:
            day = date.fromisoformat(text)
        except ValueError:
            raise ValueError("Write the dates as MM/DD/YYYY.") from None
        out.append((datetime.combine(day, day_time.min, tzinfo=clock.zone()) + timedelta(days=plus)).timestamp())
    if out[0] is not None and out[1] is not None and out[0] >= out[1]:
        raise ValueError("The end date comes before the start date.")
    return out[0], out[1]


# -- the index -----------------------------------------------------------------------------------------------------


class RowIndex:
    """An in-memory index over an append-only file of JSON lines (the module docstring). fields(row) -> (moment, who, kind, case, flag, other)
    or None to leave a row out: who is the person who did it, other the person it was done to (or ""), flag the restricted mark."""

    HEAD = 256  # the first bytes of the file, to tell a replaced file from a longer one

    def __init__(self, path: str | Path, fields: Callable[[dict], tuple | None]):
        self.path = Path(path)
        self._fields = fields
        self.lock = threading.RLock()  # held by a question across its select and the offsets it takes from the entries (below)
        self.rows_read = 0  # rows read from the file to answer questions, and bytes scanned to extend the index: a test checks both stay small
        self.bytes_scanned = 0
        self._clear()

    def _clear(self) -> None:
        self.size, self._head = 0, b""
        self.pos, self.ts = array("Q"), array("d")
        self.who, self.kind, self.case, self.other = array("I"), array("I"), array("I"), array("I")
        self.flag = array("B")
        self._ids: dict[str, int] = {"": 0}
        self._names: list[str] = [""]
        self.by_case: dict[int, array] = {}

    def _id(self, text: str) -> int:
        n = self._ids.get(text)
        if n is None:
            n = self._ids[text] = len(self._names)
            self._names.append(text)
        return n

    def name(self, n: int) -> str:
        return self._names[n]

    def refresh(self) -> int:
        """Reads the rows appended since the last question (all of them the first time); returns how many rows are indexed."""
        with self.lock:
            try:
                total = self.path.stat().st_size
                f = open(self.path, "rb")
            except OSError:
                self._clear()
                return 0
            with f:
                head = f.read(self.HEAD)
                if total < self.size or head[:len(self._head)] != self._head:
                    self._clear()  # the file was cut short or replaced: start again
                if len(head) > len(self._head):
                    self._head = head
                if total > self.size:
                    f.seek(self.size)
                    at = self.size
                    for raw in f:
                        if not raw.endswith(b"\n"):
                            break  # a row still being written: the next question reads it whole
                        self.bytes_scanned += len(raw)
                        try:
                            row = json.loads(raw)
                        except ValueError:
                            row = None
                        got = self._fields(row) if isinstance(row, dict) else None
                        if got:
                            self._add(at, *got)
                        at += len(raw)
                    self.size = at
            return len(self.pos)

    def _add(self, at: int, moment: float, who: str, kind: str, case: str, flag: bool, other: str) -> None:
        n = len(self.pos)
        self.pos.append(at)
        self.ts.append(moment)
        self.who.append(self._id(who))
        self.kind.append(self._id(kind))
        c = self._id(case)
        self.case.append(c)
        self.other.append(self._id(other))
        self.flag.append(1 if flag else 0)
        if case:
            self.by_case.setdefault(c, array("I")).append(n)

    def select(self, *, case: str = "", people: Iterable[str] = (), kinds: Iterable[str] = (), start: float | None = None, end: float | None = None,
               flag: bool = False, flag_cases: Iterable[str] = (), refresh: bool = True) -> list[int]:
        """The entries that match, newest first: of this case, done by or to one of these people, of one of these kinds, within the moments,
        restricted only (flag: marked restricted when the row was written, or, in flag_cases, a case restricted now). Answered from the index;
        no row is read. The entries are positions in the index as it is now: a caller keeps self.lock from here until it has taken what it needs
        from them (offsets_of, the columns), because a file replaced meanwhile makes the index start again and the positions mean nothing. A second question in the same hold passes
        refresh=False: it must be asked of the same state of the index as the first."""
        with self.lock:
            if refresh:
                self.refresh()
            return self._select(case, people, kinds, start, end, flag, flag_cases)

    def _select(self, case: str, people: Iterable[str], kinds: Iterable[str], start: float | None, end: float | None, flag: bool, flag_cases: Iterable[str]) -> list[int]:
        people, kinds = {self._ids.get(p) for p in people}, {self._ids.get(k) for k in kinds}
        want_people, want_kinds = bool(people), bool(kinds)
        people.discard(None), kinds.discard(None)
        if case and case not in self._ids or (want_people and not people) or (want_kinds and not kinds):
            return []
        closed = {self._ids[c] for c in flag_cases if c in self._ids} if flag else set()
        every = self.by_case.get(self._ids[case], array("I")) if case else range(len(self.pos))
        if not (want_people or want_kinds or start is not None or end is not None or flag):
            return list(reversed(every))
        ts, who, other, kind, marks, cases = self.ts, self.who, self.other, self.kind, self.flag, self.case
        return [i for i in reversed(every)
                if (not want_kinds or kind[i] in kinds) and (not want_people or who[i] in people or other[i] in people)
                and (start is None or ts[i] >= start) and (end is None or ts[i] < end) and (not flag or marks[i] or cases[i] in closed)]

    def offsets_of(self, entries: Iterable[int]) -> list[int]:
        """Where these entries' rows start in the file. Call it holding self.lock, in the same hold as the select that gave the entries."""
        return [self.pos[i] for i in entries]

    def read(self, offsets: list[int]) -> list[dict[str, Any]]:
        """The rows at these offsets, in the order given, without holding the lock (a long file read must not stop other questions). They are read
        in file order, in one pass, a seek only where there is a gap: 200,000 rows are one sequential read, not 200,000 seeks. A file replaced
        meanwhile gives empty rows for what is no longer there, never an error."""
        out: list[dict[str, Any]] = [{}] * len(offsets)
        if not offsets:
            return out
        try:
            f = open(self.path, "rb")
        except OSError:
            return out
        with f:
            at = -1
            for n in sorted(range(len(offsets)), key=offsets.__getitem__):
                if offsets[n] != at:
                    f.seek(offsets[n])
                line = f.readline()
                at = offsets[n] + len(line)
                try:
                    out[n] = json.loads(line)
                except ValueError:
                    out[n] = {}
        self.rows_read += len(offsets)
        return out

    def case_names(self) -> list[str]:
        """Every case the index has a row for (hold self.lock)."""
        return [self._names[c] for c in self.by_case]

    def distinct(self, entries: Iterable[int] | None, column: str) -> list[str]:
        """The values one column takes over these entries (every entry when None): who, kind. Without reading rows."""
        col = getattr(self, column)
        return sorted({self._names[n] for n in (set(col) if entries is None else {col[i] for i in entries})} - {""})


def _day_of(moment: float, cache: dict[int, str]) -> str:
    key = int(moment // 900)  # every zone's offset is a whole number of quarter hours, so a quarter hour never straddles midnight
    day = cache.get(key)
    if day is None:
        day = cache[key] = datetime.fromtimestamp(key * 900, clock.zone()).date().isoformat()
    return day


def pages(total: int, page: int) -> tuple[int, int]:
    """(the page asked for, kept within the pages there are; how many pages)."""
    count = max(1, -(-total // PAGE))
    return max(1, min(count, int(page or 1))), count


def csv_file(columns: list[tuple[str, str]], rows: list[dict[str, Any]]) -> bytes:
    from review.reports import to_csv

    return to_csv({"columns": [{"key": k, "label": label} for k, label in columns], "rows": rows}).encode("utf-8")


# -- the view log ---------------------------------------------------------------------------------------------------


def _view_fields(row: dict) -> tuple:
    at = clock.parse(row.get("at"))
    return (at.timestamp() if at else 0.0, str(row.get("email") or ""), str(row.get("kind") or ""), str(row.get("client") or ""), bool(row.get("restricted")), "")


class Views:
    """"Who viewed this" for one case, and "Who viewed what" for the firm, over review_views.jsonl."""

    def __init__(self, path: str | Path, kinds: dict[str, str], names: Callable[[], dict[str, str]], closed: Callable[[Iterable[str]], set[str]] | None = None):
        self.index = RowIndex(path, _view_fields)
        self.kinds = kinds  # what each kind of opening is called on the screen
        self.names = names  # {email: name} of the staff accounts
        # which of these cases are restricted now (src/restricted.py, asked by the server). A row carries the mark of the day it was written; a case
        # restricted later still shows its older rows as restricted, and "Restricted cases only" finds them.
        self.closed = closed or (lambda cases: set())

    def _rows(self, offsets: list[int]) -> list[dict[str, Any]]:
        """The rows at these offsets (read without holding the index's lock), each with what it was and marked restricted when its case is now."""
        rows = self.index.read(offsets)
        closed = self.closed({str(r["client"]) for r in rows if r.get("client")})
        return [r | {"what": self.kinds.get(r.get("kind"), r.get("kind"))} | ({"restricted": True} if r.get("client") in closed else {}) for r in rows]

    def _people(self, entries: list[int] | None) -> list[dict[str, str]]:
        names = self.names()
        return [{"email": e, "name": names.get(e) or e} for e in sorted(self.index.distinct(entries, "who"), key=lambda e: (names.get(e) or e).casefold())]

    def _kinds(self, entries: list[int] | None) -> list[dict[str, str]]:
        return [{"id": k, "label": self.kinds.get(k, k)} for k in sorted(self.index.distinct(entries, "kind"), key=lambda k: self.kinds.get(k, k))]

    def _closed_everywhere(self) -> set[str]:
        """Every case with a row in the log that is restricted now (a look at each case's own record: only when a question needs it)."""
        with self.index.lock:
            self.index.refresh()
            names = self.index.case_names()
        return self.closed(names)

    def case(self, client: str, *, limit: int | None = None, page: int = 1, person: str = "", kind: str = "") -> dict[str, Any]:
        """One case's rows, newest first. limit: just the latest few (the case page). Otherwise a page of 50 of the rows that match the
        person and the kind, with the people and kinds to filter by."""
        ix = self.index
        with ix.lock:  # the selection and what is taken from it in one hold; the file is read after
            entries = ix.select(case=client, people=[person] if person else (), kinds=[kind] if kind else ())
            if limit is not None:
                offsets, extra = ix.offsets_of(entries[:max(1, min(500, limit))]), {}
            else:
                page, count = pages(len(entries), page)
                every = ix.select(case=client, refresh=False)  # the same state of the index as the selection above
                offsets = ix.offsets_of(entries[(page - 1) * PAGE:page * PAGE])
                extra = {"page": page, "pages": count, "per": PAGE, "people": self._people(every), "kinds": self._kinds(every)}
            total = len(entries)
        return {"rows": self._rows(offsets), "total": total} | extra

    def case_csv(self, client: str, *, person: str = "", kind: str = "") -> bytes:
        ix = self.index
        with ix.lock:
            offsets = ix.offsets_of(ix.select(case=client, people=[person] if person else (), kinds=[kind] if kind else ()))
        tz = clock.zone()
        rows = [{"when": us_when(r.get("at"), tz), "who": r.get("name") or r.get("email") or "This computer (no sign-in)", "email": r.get("email"),
                 "role": (r.get("role") or "").capitalize(), "what": r["what"], "file": r.get("file"), "address": r.get("address"),
                 "restricted": "Yes" if r.get("restricted") else ""} for r in self._rows(offsets)]
        return csv_file([("when", "When"), ("who", "Who"), ("email", "Email"), ("role", "Role"), ("what", "What"), ("file", "Document"), ("address", "From"),
                         ("restricted", "Restricted case")], rows)

    def _select(self, person: str, kind: str, start: str, end: str, restricted: bool, closed: set[str]) -> list[int]:
        a, b = _span(start, end)
        return self.index.select(people=[person] if person else (), kinds=[kind] if kind else (), start=a, end=b, flag=restricted, flag_cases=closed)

    def firm(self, *, person: str = "", kind: str = "", start: str = "", end: str = "", restricted: bool = False, group: str = "", page: int = 1) -> dict[str, Any]:
        """Every opening across the firm, newest first, or (group "day") the totals by person and day. Filters: person, kind, the firm's dates
        (from, to), restricted cases only (marked when opened, or restricted now)."""
        _span(start, end)  # a date that isn't one is refused before any work
        closed = self._closed_everywhere() if (restricted or group == "day") else set()
        ix = self.index
        with ix.lock:
            entries = self._select(person, kind, start, end, restricted, closed)
            base = {"people": self._people(None), "kinds": self._kinds(None), "per": PAGE, "openings": len(entries)}
            if group == "day":
                totals = self._totals(entries, closed)
                page, count = pages(len(totals), page)
                return base | {"group": "day", "rows": totals[(page - 1) * PAGE:page * PAGE], "total": len(totals), "page": page, "pages": count}
            page, count = pages(len(entries), page)
            offsets, total = ix.offsets_of(entries[(page - 1) * PAGE:page * PAGE]), len(entries)
        return base | {"group": "", "rows": self._rows(offsets), "total": total, "page": page, "pages": count}

    def _totals(self, entries: list[int], closed: set[str]) -> list[dict[str, Any]]:
        """Per day and person: how many openings, of how many cases, how many in restricted cases. Newest day first. From the index alone
        (hold the index's lock)."""
        ix, names, cache = self.index, self.names(), {}
        shut = {ix._ids[c] for c in closed if c in ix._ids}
        tally: dict[tuple[str, int], list] = {}
        for i in entries:
            row = tally.setdefault((_day_of(ix.ts[i], cache), ix.who[i]), [0, set(), 0])
            row[0] += 1
            row[1].add(ix.case[i])
            row[2] += 1 if ix.flag[i] or ix.case[i] in shut else 0
        out = []
        for (day, who), (n, cases, shut_n) in tally.items():
            email = ix.name(who)
            out.append({"iso": day, "day": f"{day[5:7]}/{day[8:10]}/{day[:4]}", "who": names.get(email) or email or "This computer (no sign-in)", "email": email,
                        "openings": n, "cases": len(cases), "restricted": shut_n})
        return sorted(out, key=lambda r: (r["iso"], r["openings"]), reverse=True)

    def firm_csv(self, *, person: str = "", kind: str = "", start: str = "", end: str = "", restricted: bool = False, group: str = "") -> bytes:
        _span(start, end)
        closed = self._closed_everywhere() if (restricted or group == "day") else set()
        ix = self.index
        with ix.lock:
            entries = self._select(person, kind, start, end, restricted, closed)
            if group == "day":
                totals = self._totals(entries, closed)
            else:
                offsets = ix.offsets_of(entries)
        if group == "day":
            return csv_file([("day", "Day"), ("who", "Person"), ("email", "Email"), ("openings", "Openings"), ("cases", "Cases"), ("restricted", "Openings in restricted cases")], totals)
        tz = clock.zone()
        rows = [{"when": us_when(r.get("at"), tz), "who": r.get("name") or r.get("email") or "This computer (no sign-in)", "email": r.get("email"), "what": r["what"],
                 "case": r.get("client"), "file": r.get("file"), "address": r.get("address"), "restricted": "Yes" if r.get("restricted") else ""}
                for r in self._rows(offsets)]  # read in file order in one pass, with no lock held
        return csv_file([("when", "When"), ("who", "Who"), ("email", "Email"), ("what", "What"), ("case", "Case"), ("file", "Document"), ("address", "From"),
                         ("restricted", "Restricted case")], rows)


# -- the staff access log -------------------------------------------------------------------------------------------------


def _fallback(event: Any) -> str:
    """An event this screen has no words for (a newer version's row): its name, made readable. Every event the app writes has its own words
    (tests/test_totp.py checks it)."""
    return str(event or "Something happened").replace("_", " ").capitalize()


def _access_fields(row: dict) -> tuple:
    event, email, by = str(row.get("event") or ""), str(row.get("email") or ""), str(row.get("by") or "")
    kind = _KIND_OF.get(event, "other")
    if event == "sign_in_failed" and row.get("reason") == "locked":
        kind = "lockout"
    actor = by or (email if event in _SELF else "")
    other = str(row.get("person") or "") if event == "case_access" else email if actor != email else ""
    at = clock.parse(row.get("at"))
    return (at.timestamp() if at else 0.0, actor, kind, str(row.get("client") or ""), False, other)


class StaffLog:
    """"What staff did": the access log's rows, the rule approvals, the policy edits and the "link shown" rows of the view log, together, newest
    first, each with who, what, when, the case where there is one and the address. Filters: person, kind, the firm's dates."""

    def __init__(self, accounts, views: Views, rule_names: Callable[[], dict[str, str]]):
        self.accounts = accounts
        self.views = views
        self.index = RowIndex(accounts.log_path, _access_fields)
        self.rule_names = rule_names  # {rule id: how a person reads it} for the approvals

    # the small sources, as whole rows: approvals and policy edits (a few hundred in a year at most)
    def _small(self, names: dict[str, str]) -> list[dict[str, Any]]:
        from rules import approval, firm_policies

        by_name = {n: e for e, n in names.items()}
        shown = self.rule_names()
        out = []
        for rule_id, records in approval.history().items():
            for r in records:
                label = shown.get(rule_id) or "a rule or policy that is no longer in the product"
                out.append({"at": r.get("at"), "who": r.get("by"), "email": by_name.get(r.get("by") or "", ""), "kind": "rule",
                            "what": f"Approved {label}", "case": "", "address": ""})
        for c in firm_policies.changes():
            out.append({"at": c["at"], "who": c["by"], "email": by_name.get(c["by"] or "", ""), "kind": "policy",
                        "what": f"{c['what']}: {c['name']}", "case": "", "address": ""})
        return out

    def _what(self, row: dict, names: dict[str, str]) -> str:
        event, email = row.get("event"), str(row.get("email") or "")
        person = names.get(email) or email
        if event == "signed_in":
            how = str(row.get("how") or "")
            if how in _HOW:
                return _HOW[how] + (" and asked to remember this computer for 30 days" if row.get("remembered") else "")
            return "Signed in" + (f" with {_PROVIDERS.get(how, how.title())}" if how else "")
        if event == "signed_out":
            return "Signed out"
        if event == "sign_in_failed":
            reason = str(row.get("reason") or "")
            if reason in _FAILED:
                return _FAILED[reason] + (f" ({email})" if reason == "unknown" and email else "")
            for key, name in _PROVIDERS.items():
                if reason.startswith(key + ":"):
                    detail = reason.split(":", 1)[1].strip()
                    return f"Sign-in with {name} refused" + (": " + ("not on the staff list" if "staff list" in detail else "the account is turned off" if "disabled" in detail else "it did not finish")
                                                              if detail else "")
            return "Sign-in failed"
        if event in ("account_locked", "account_locked_again"):
            minutes = int(row.get("minutes") or 15)  # rows written before 10/03/2026 carry no length: it was always 15
            times = int(row.get("times_today") or 1)
            from review.auth import lock_length

            locked = f"Account locked for {lock_length(minutes)} after 5 wrong passwords or codes in a row"
            return f"Locked {times} times today: " + locked[0].lower() + locked[1:] if times >= 2 else locked
        if event == "lockout_alert":
            times = int(row.get("times") or 3)
            if row.get("what") == "address":
                return f"Alert: this computer address was refused {times} times today for too many tries (someone may be guessing passwords)"
            return f"Alert: this account was locked {times} times today (someone may be guessing its password or code)"
        if event == "password_accepted":
            return "Password accepted; asked for the code from the app" if row.get("next") == "code" else "Password accepted; asked to set up the authenticator app"
        if event == "code_set_up":
            return "Set up the authenticator app (eight recovery codes shown once)"
        if event == "second_factor_changed":
            who_must = "everyone must use a code from the app" if row.get("everyone") else "attorneys must use a code from the app"
            remember = "a computer may be remembered for 30 days" if row.get("remember") else "no computer is remembered"
            return f"Changed the sign-in codes: {who_must}; {remember}"
        if event == "devices_forgotten":
            return "Turned off remembering computers: every remembered computer asks for the code again"
        if event == "signed_out_for_code":
            n = int(row.get("sessions") or 0)
            return f"Signed out {n} {'person' if n == 1 else 'people'} without the app, now that everyone must use a code"
        if event == "code_key_rotated":
            return f"Changed the key that protects the authenticator apps' secrets ({int(row.get('secrets') or 0)} re-encrypted, on the server)"
        if event in ("calendar_address_made", "calendar_address_revoked", "calendar_feed"):
            mine = "the firm's calendar address" if row.get("kind") == "firm" else "their own calendar address"
            return {"calendar_address_made": f"Made {mine}", "calendar_address_revoked": f"Turned off {mine}",
                    "calendar_feed": f"A calendar program read {mine} (logged once a day)"}[event]
        if event == "support_let_in":
            hours = int(row.get("hours") or 0)
            return f"Let {person} in for support for {hours} hour{'s' if hours != 1 else ''}, client values {'shown plain' if row.get('values') == 'plain' else 'masked'}"
        if event == "support_request":
            return f"Support asked for {row.get('path') or 'a page'}" + (f" ({row.get('method')})" if row.get("method") not in (None, "GET") else "") \
                + (": refused" if row.get("refused") else "")
        if event == "support_ended":
            return f"Ended the support session of {person} early" if row.get("by") else f"The support session of {person} ended by itself at its hour"
        if event == "data_exported":
            files = int(row.get("files") or 0)
            return "Exported all of the firm's data" + (f" ({files} files)" if files else " (started)")
        if event == "first_attorney_created":
            return "Set up the first attorney's account " + ("with the one-time setup code" if row.get("how") == "setup code" else "from the computer that runs the app")
        if event == "setup_refused":
            return "A try at setting up the first attorney was refused (no valid setup code)"
        if event == "password_reset":
            return f"Gave {person} a new one-time password" if row.get("by") else f"A new one-time password was made for {person} on the server"
        if event == "password_changed":
            return "Changed their own password"
        if event == "password_rehashed":
            return "Password stored with stronger protection (automatic, at sign-in)"
        if event == "account_added":
            return f"Added the account of {person} as {str(row.get('role') or '').lower() or 'staff'}"
        if event == "account_changed":
            changed = ([f"role is now {str(row['role']).lower()}"] if "role" in row else []) + (["turned on" if row["active"] else "turned off"] if "active" in row else [])
            return f"Changed the account of {person}: " + (", ".join(changed) or "no visible change")
        if event == "case_access":
            other = names.get(str(row.get("person") or "")) or str(row.get("person") or "someone")
            return _CASE_ACTIONS.get(str(row.get("action")), "Changed who may open the case").format(person=other)
        return _fallback(event)

    def _entries(self, person: str, kind: str, a: float | None, b: float | None, names: dict[str, str]) -> list[tuple]:
        """(moment, where, what) for everything that matches, newest first: where is "a" (a row of the access log, by its offset in the file), "v" (a row of
        the view log, by offset) or "x" (a whole row)."""
        out: list[tuple] = []
        who = [person] if person else ()
        if kind in ("", *(k for k, _ in KINDS.items() if k not in ("rule", "policy", "link"))):
            ix = self.index
            with ix.lock:  # the entries and the offsets taken from them in one hold
                out += [(ix.ts[i], "a", ix.pos[i]) for i in ix.select(people=who, kinds=[kind] if kind else (), start=a, end=b)]
        if kind in ("", "link"):
            vx = self.views.index
            with vx.lock:
                out += [(vx.ts[i], "v", vx.pos[i]) for i in vx.select(people=who, kinds=["link"], start=a, end=b)]
        if kind in ("", "rule", "policy"):
            mine = names.get(person, "") if person else ""
            for r in self._small(names):
                moment = clock.parse(r["at"])
                if (kind and r["kind"] != kind) or (person and person != r["email"] and not (mine and mine == r["who"])):
                    continue
                t = moment.timestamp() if moment else 0.0
                if (a is None or t >= a) and (b is None or t < b):
                    out.append((t, "x", r))
        out.sort(key=lambda e: e[0], reverse=True)
        return out

    def _present(self, entries: list[tuple], names: dict[str, str]) -> list[dict[str, Any]]:
        """The entries as rows for the screen, in the order given (the files are read for these entries only, in file order, with no lock held)."""
        got: dict[tuple, dict[str, Any]] = {}
        for src, ix in (("a", self.index), ("v", self.views.index)):
            wanted = [e[2] for e in entries if e[1] == src]
            for offset, row in zip(wanted, ix.read(wanted)):
                got[(src, offset)] = row
        closed = self.views.closed({str(r["client"]) for r in got.values() if r.get("client")})  # the cases restricted now
        out = []
        for _, src, ref in entries:
            if src == "x":
                out.append(ref | {"kind_label": KINDS[ref["kind"]], "restricted": False})
                continue
            row = got[(src, ref)]
            case = str(row.get("client") or "")
            shut = bool(case and (row.get("restricted") or case in closed))  # marked when it happened, or restricted now
            if src == "v":
                email = str(row.get("email") or "")
                out.append({"at": row.get("at"), "who": row.get("name") or names.get(email) or email or "This computer (no sign-in)", "email": email, "kind": "link",
                            "kind_label": KINDS["link"], "what": "Showed the client's portal sign-in link", "case": case, "address": row.get("address") or "", "restricted": shut})
                continue
            event, email = row.get("event"), str(row.get("email") or "")
            actor = str(row.get("by") or "") or (email if event in _SELF else "")
            kind = _access_fields(row)[2]
            nobody = "The review app" if event == "lockout_alert" else "The server (command line)"  # an address's alert is nobody's doing
            out.append({"at": row.get("at"), "who": names.get(actor) or actor or nobody, "email": actor, "kind": kind,
                        "kind_label": KINDS.get(kind, "Other"), "what": self._what(row, names), "case": case, "address": row.get("address") or "", "restricted": shut})
        return out

    def _names(self) -> dict[str, str]:
        return {u["email"]: u["name"] for u in self.accounts.users()}

    def page(self, *, person: str = "", kind: str = "", start: str = "", end: str = "", page: int = 1) -> dict[str, Any]:
        a, b = _span(start, end)
        names = self._names()
        entries = self._entries(person, kind, a, b, names)
        page, count = pages(len(entries), page)
        return {"rows": self._present(entries[(page - 1) * PAGE:page * PAGE], names), "total": len(entries), "page": page, "pages": count, "per": PAGE,
                "people": [{"email": e, "name": n} for e, n in sorted(names.items(), key=lambda p: p[1].casefold())],
                "kinds": [{"id": k, "label": label} for k, label in KINDS.items()]}

    def csv(self, *, person: str = "", kind: str = "", start: str = "", end: str = "") -> bytes:
        a, b = _span(start, end)
        names = self._names()
        tz = clock.zone()
        rows = [r | {"when": us_when(r["at"], tz), "case": r.get("case") or "", "shut": "Yes" if r.get("restricted") else ""} for r in self._present(self._entries(person, kind, a, b, names), names)]
        return csv_file([("when", "When"), ("who", "Who"), ("email", "Email"), ("kind_label", "Kind"), ("what", "What"), ("case", "Case"), ("shut", "Restricted case"),
                         ("address", "From")], rows)


# -- the event ledger -----------------------------------------------------------------------------------------------------


def _event_fields(row: dict) -> tuple:
    at = clock.parse(row.get("at"))
    return (at.timestamp() if at else 0.0, str(row.get("who") or ""), str(row.get("kind") or ""), str(row.get("case") or ""), False, "")


class Events:
    """"What changed on this case" and, for the firm, "What changed across the firm": the rows of the event ledger (src/events.py), read the way the view
    log is: one RowIndex per month's file (events-YYYY-MM.jsonl), built once and then only extended by what was appended, so a page of 50 rows is
    read by seeking to those rows and the filters and totals never read a row. A month's file that is complete is never opened again except for a page
    that has a row in it. Newest first across the months.

    hidden: the cases the reader may not open (src/restricted.py scope): their rows are left out of every page, total and file, the same as they
    are from every list in the app. The server passes it; the case's own page is gated before this is asked (may_open)."""

    def __init__(self, base: str | Path, kinds: dict[str, str]):
        self.base = Path(base)
        self.kinds = kinds  # what each kind of change is called on the screen
        self._indexes: dict[Path, RowIndex] = {}
        self._lock = threading.RLock()

    def _live(self) -> list[RowIndex]:
        """The month files that exist, newest month first (an index for a file not seen before; none for a file that is gone)."""
        from events import files

        with self._lock:
            paths = files(self.base)
            for gone in set(self._indexes) - set(paths):
                del self._indexes[gone]
            for p in paths:
                if p not in self._indexes:
                    self._indexes[p] = RowIndex(p, _event_fields)
            return [self._indexes[p] for p in reversed(paths)]

    def _select(self, *, case: str = "", person: str = "", kind: str = "", start: float | None = None, end: float | None = None,
                hidden: Iterable[str] = (), not_kinds: Iterable[str] = ()) -> list[tuple[RowIndex, list[int]]]:
        """not_kinds: kinds of row left out (a purge's rows, for anyone but an attorney: src/purge.py)."""
        gone, skip = set(hidden), set(not_kinds) | {"redacted"}  # a purged case's blanked rows (src/purge.py) are on no screen
        out = []
        for ix in self._live():
            with ix.lock:
                entries = ix.select(case=case, people=[person] if person else (), kinds=[kind] if kind else (), start=start, end=end)
                if gone:
                    entries = [i for i in entries if ix.name(ix.case[i]) not in gone]
                if skip:
                    entries = [i for i in entries if ix.name(ix.kind[i]) not in skip]
            out.append((ix, entries))
        return out

    @staticmethod
    def _rows_at(parts: list[tuple[RowIndex, list[int]]], lo: int, hi: int | None) -> list[dict[str, Any]]:
        """Rows lo..hi of the selection (hi None: to the end), newest first, each file read in one pass."""
        rows: list[dict[str, Any]] = []
        seen = 0
        for ix, entries in parts:
            n = len(entries)
            if seen + n > lo and (hi is None or seen < hi):
                a, b = max(0, lo - seen), n if hi is None else min(n, hi - seen)
                with ix.lock:
                    offsets = ix.offsets_of(entries[a:b])
                rows += ix.read(offsets)
            seen += n
        return rows

    def _present(self, rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
        return [{"at": r.get("at"), "who": r.get("who") or "", "role": r.get("role") or "", "via": r.get("via") or "", "case": r.get("case") or "", "kind": r.get("kind") or "",
                 "kind_label": self.kinds.get(r.get("kind"), "Other"), "action": r.get("action") or "", "what": r.get("what") or "", "version": r.get("version")}
                for r in rows if r]

    def _choices(self, parts: list[tuple[RowIndex, list[int]]] | None = None) -> tuple[list[dict[str, str]], list[dict[str, str]]]:
        """The people and kinds to filter by: of the rows in `parts` (a case's own, so a case page never offers a name that did nothing on it), else of every row."""
        people: set[str] = set()
        kinds: set[str] = set()
        for ix, entries in parts if parts is not None else [(i, None) for i in self._live()]:
            with ix.lock:
                people |= set(ix.distinct(entries, "who"))
                kinds |= set(ix.distinct(entries, "kind"))
        return ([{"email": p, "name": p} for p in sorted(people, key=str.casefold)],
                [{"id": k, "label": self.kinds.get(k, k)} for k in sorted(kinds, key=lambda k: self.kinds.get(k, k))])

    def case(self, client: str, *, limit: int | None = None, page: int = 1, person: str = "", kind: str = "", hidden: Iterable[str] = (),
             not_kinds: Iterable[str] = ()) -> dict[str, Any]:
        """One case's rows, newest first: the latest few (limit: the case page), or a page of 50 of those that match the person and the kind."""
        parts = self._select(case=client, person=person, kind=kind, hidden=hidden, not_kinds=not_kinds)
        total = sum(len(e) for _, e in parts)
        if limit is not None:
            return {"rows": self._present(self._rows_at(parts, 0, max(1, min(500, limit)))), "total": total}
        page, count = pages(total, page)
        people, kinds = self._choices(self._select(case=client, hidden=hidden, not_kinds=not_kinds))  # the case's own rows, not the person's or the kind's filter
        return {"rows": self._present(self._rows_at(parts, (page - 1) * PAGE, page * PAGE)), "total": total, "page": page, "pages": count, "per": PAGE,
                "people": people, "kinds": kinds}

    def firm(self, *, person: str = "", kind: str = "", start: str = "", end: str = "", case: str = "", page: int = 1, hidden: Iterable[str] = ()) -> dict[str, Any]:
        """Every change across the firm, newest first, 50 a page, filtered by person, kind, the firm's dates (from, to) and case."""
        a, b = _span(start, end)  # a date that isn't one is refused before any work
        parts = self._select(case=case, person=person, kind=kind, start=a, end=b, hidden=hidden)
        total = sum(len(e) for _, e in parts)
        page, count = pages(total, page)
        people, kinds = self._choices()
        return {"rows": self._present(self._rows_at(parts, (page - 1) * PAGE, page * PAGE)), "total": total, "page": page, "pages": count, "per": PAGE,
                "people": people, "kinds": kinds}

    def csv(self, *, case: str = "", person: str = "", kind: str = "", start: str = "", end: str = "", hidden: Iterable[str] = (), not_kinds: Iterable[str] = ()) -> bytes:
        """Every row that matches as a CSV file (a cell that starts with = + - @ gets a leading apostrophe: review/reports._cell)."""
        a, b = _span(start, end)
        tz = clock.zone()
        rows = [r | {"when": us_when(r["at"], tz)} for r in self._present(self._rows_at(self._select(case=case, person=person, kind=kind, start=a, end=b, hidden=hidden,
                                                                                                    not_kinds=not_kinds), 0, None))]
        return csv_file([("when", "When"), ("who", "Who"), ("role", "Role"), ("kind_label", "Kind"), ("action", "Action"), ("what", "What changed"), ("case", "Case"),
                         ("version", "Record version")], rows)
