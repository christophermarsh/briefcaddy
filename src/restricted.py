"""Restricted cases: who may open a case the law keeps confidential, or one an attorney closed.

A case is restricted when
  - the law says so (documents.case_confidentiality): VAWA, T and U (8 U.S.C. 1367), asylum, the asylee's
    green card and the I-730 (8 CFR 208.6). Automatic, from the case's track and filings; it cannot be
    switched off;
  - or an attorney marks it (a minor's SIJ case, or any case), with a reason. The reason is the firm's: it is
    kept on the case's record for staff and never reaches the client portal.

A restricted case
  - is visible to every attorney and to the staff an attorney names on it, and to nobody else: it is absent
    from every list, count, search result and report for everyone else. The review app asks visible_to() for
    each case it lists (src/review/server.py: the client list, My work, What's due, Search, Reports, the notice
    inbox, the overview counts); a case-page request for it answers as for a case that does not exist;
  - has every opening logged with the "restricted" mark (the view log, review_views.jsonl);
  - sends no automatic message to the client (src/portal/notify.py refuses: no invitation, reminder, request,
    "there's news" or "the office answered" text or email), unless an attorney switches automatic messages on
    for that case with a reason. A text from the firm on a shared or watched phone can itself tell an abuser or
    a trafficker that someone is seeking help (docs/research/security_readiness.md Part 2 E).

A document whose own type is confidential, in a case that is not restricted, is still shown to an attorney and
to the staff named on that case only (src/index.py, src/review/expiring.py): sees_confidential().

The record lives in the case folder (access.json): the attorney's mark, the people named, the automatic-messages
switch, and every change with who, when and why. Without staff accounts (one person on one machine, no sign-in)
nothing is hidden: there is nobody to hide it from.

A client of a protected kind is restricted from the moment it exists, before any document is processed (protect_new):
added on the screen with the kind of case VAWA, T visa, U visa or asylum (review/front_desk.py), or brought in from a
Docketwise matter or a Clio practice area of that kind (tools/import_docketwise.py, connectors/clio.py). The case folder is
made then, holding access.json alone, with the mark (the kind of case, who, when); law() still says None until there is a
case file to read, and the mark keeps it closed meanwhile and after. One record serves the review app's lists and the
portal's messages alike (src/portal/notify.py reads the same folder).
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import clock
import events

FILE = "access.json"
CACHE = "confidentiality.json"  # the law's answer for the case, kept until a file it was worked out from changes
CACHE_VERSION = 1
# what documents.case_confidentiality reads: the track a person chose and the filings recorded (status.json), the facts
# and the decisions on them (the reviewed graph), the documents classified (meta.json), the packets built
_SOURCES = ("status.json", "fact_graph.json", "fact_graph_raw.json", "decisions.json", "meta.json")
LAW_WORDS = {"1367": "VAWA, T or U visa case (8 U.S.C. 1367)", "208.6": "asylum case (8 CFR 208.6)"}


# law()'s answer when a file it must read cannot be read: the case may be a protected one, so it is restricted until it can be read
UNKNOWN = "unknown"
UNREADABLE = "Its case file could not be read, so it is restricted until it can be."
HAND = "Restricted case: messages are sent by hand."  # what the request, reminder and answer screens say
NOTICE = "A restricted case's notice: an attorney places it."  # a waiting notice in the inbox, for someone who may not see its case
# A USCIS notice for one of these forms is itself protected (the same 8 U.S.C. 1367 and 8 CFR 208.6 as
# documents.case_confidentiality): the I-914 T visa, the I-918 U visa, the I-589 asylum application, the I-730 refugee
# or asylee relative petition, and the I-360 (the VAWA self-petition is an I-360; so is the SIJ petition, which a notice
# alone does not tell apart, so every unclaimed I-360 notice is treated as the VAWA one). A notice of one that no case has
# claimed yet waits for an attorney.
PROTECTED_FORMS = ("I-914", "I-918", "I-589", "I-730", "I-360")


def protected_form(form: str | None) -> bool:
    """A notice's form ("I-914", "I-918 Supplement A") is one of PROTECTED_FORMS."""
    text = str(form or "").upper().replace(" ", "")
    return any(text.startswith(f) for f in PROTECTED_FORMS)


def law_words(found: str | None) -> str:
    """LAW_WORDS with its article: "a VAWA, T or U visa case (8 U.S.C. 1367)", "an asylum case (8 CFR 208.6)"."""
    words = LAW_WORDS.get(found or "", "")
    return f"{'an' if words[:1].lower() in 'aeiou' else 'a'} {words}" if words else ""


law_phrase = law_words  # G2 named it so first; the same function (tests and the screen may use either)


# The kinds of case the law protects, in the words a person or another system writes them. The tracks are the review app's
# (documents._TRACKS: vawa, t_visa, u_visa -> 8 U.S.C. 1367; asylum -> 8 CFR 208.6). A Docketwise matter type or a Clio practice
# area is free text the firm chose ("Asylum", "VAWA Self-Petition", "U Visa", "T", "I-918", "Asilo", "Visto U"): read by its
# words, whole words only and without accents or capitals ("CAT" is never "cataract" or "certificate"), in English, Portuguese
# and Spanish. 8 CFR 208.6(a) covers "any application for refugee admission, asylum, withholding of removal under section
# 241(b)(3) of the Act, or protection under regulations issued pursuant to the Convention Against Torture's implementing
# legislation", and credible fear and reasonable fear records (eCFR, read 10/03/2026). The forms of PROTECTED_FORMS count too,
# other than the I-360 (an I-360 alone may be an SIJ petition). A type in other words ("Humanitarian") is not caught: the
# Docketwise report lists every type it saw and whether it was taken as protected, and its --protected-type names more.
_KIND_1367 = re.compile(r"\b(vawa|battered|trafficking|[tu] ?visas?|(visas?|vistos?) [tu]|[tu] nonimmigrants?|i ?914[a-z]?|i ?918[a-z]?)\b")
_KIND_2086 = re.compile(r"\b(asylum|asylee|asylees|asilo|asilado|asilada|asilados|refugee|refugees|refugiado|refugiada|refugiados|"
                        r"withholding|cat|convention against torture|credible fear|reasonable fear|i ?589|i ?730)\b")


def kind_law(kind: str | None) -> str | None:
    """"1367", "208.6" or None for a kind of case: a track id ("u_visa") or a matter type or practice area's name. A type that is
    only the letter T or U (as a firm may name a matter type) is the T or U visa. 1367 wins when both apply, as for a case."""
    import unicodedata

    import documents

    text = str(kind or "").strip()
    if text in documents._TRACKS:
        return documents._TRACKS[text]
    folded = unicodedata.normalize("NFKD", text).encode("ascii", "ignore").decode()  # "Asilo político" -> "Asilo politico"
    words = re.sub(r"[^a-z0-9]+", " ", folded.lower()).strip()
    if words in ("t", "u") or _KIND_1367.search(words):
        return "1367"
    return "208.6" if _KIND_2086.search(words) else None


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _signature(client_dir: Path) -> list:
    sig: list = [CACHE_VERSION]
    for name in _SOURCES:
        p = client_dir / name
        try:
            sig.append(p.stat().st_mtime_ns)
        except FileNotFoundError:
            sig.append(0)
    sig.append(max((p.stat().st_mtime_ns for p in client_dir.glob("packet*.json")), default=0))
    return sig


def law(client_dir: str | Path) -> str | None:
    """"1367", "208.6" or None: documents.case_confidentiality, cached in the case folder (confidentiality.json) against the
    files it reads, so a list of 1,800 cases asks it once per change, not once per look. The overnight run warms it.
    UNKNOWN when one of those files cannot be read: an unreadable status.json may be hiding a U visa filing, so the case is
    restricted (fails closed) until it can be read, and that answer is never cached."""
    client_dir = Path(client_dir)
    if not (client_dir / "fact_graph.json").exists():
        return None  # invited through the portal, not processed yet: nothing says what the case is
    try:
        signature = _signature(client_dir)
    except OSError:
        return UNKNOWN
    cached = _read(client_dir / CACHE, None)
    if isinstance(cached, dict) and cached.get("signature") == signature:
        return cached.get("law")
    for name in _SOURCES:  # documents.case_confidentiality falls back quietly on what it can't read: here that means "not known"
        p = client_dir / name
        try:
            if p.exists():
                json.loads(p.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return UNKNOWN
    import documents

    try:
        found = documents.case_confidentiality(client_dir)
    except Exception:  # noqa: BLE001 -- a case that can't be read may be a protected one: restricted, not cached
        return UNKNOWN
    try:
        _write(client_dir / CACHE, {"signature": signature, "law": found})
    except OSError:
        pass  # a read-only copy: worked out again next time
    return found


def record(client_dir: str | Path) -> dict[str, Any]:
    """The case's access record: {"marked", "people", "messages", "history"}. A record that exists but cannot be read keeps
    the case restricted (an attorney's mark may be in it) and names nobody."""
    path = Path(client_dir) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        data = {}
    except (OSError, ValueError):
        data = None
    if not isinstance(data, dict):
        return {"marked": {"on": True, "by": "", "at": None, "reason": "The record of who may open this case could not be read."},
                "people": [], "messages": None, "history": []}
    return {"marked": data.get("marked"), "people": list(data.get("people") or []), "messages": data.get("messages"),
            "history": list(data.get("history") or [])}


def is_restricted(client_dir: str | Path) -> bool:
    client_dir = Path(client_dir)
    return bool((record(client_dir)["marked"] or {}).get("on")) or law(client_dir) is not None


def _named(user: dict[str, Any] | None, rec: dict[str, Any]) -> bool:
    """Named on the case by an attorney. The provider's support person never is (src/support.py): a restricted case, and a confidential document,
    are absent for support even if someone named its address."""
    if (user or {}).get("role") == "support":
        return False
    email = str((user or {}).get("email") or "").strip().lower()
    return bool(email) and any(p.get("email") == email for p in rec["people"])


def visible_to(user: dict[str, Any] | None, client_dir: str | Path) -> bool:
    """May this person see the case at all, in a list or on its own page? Every attorney may; anyone else unless the case
    is restricted and they are not named on it. user None: the app runs without staff accounts (nobody signs in)."""
    if user is None or user.get("role") == "attorney":
        return True
    client_dir = Path(client_dir)
    return not is_restricted(client_dir) or _named(user, record(client_dir))


def sees_confidential(user: dict[str, Any] | None, client_dir: str | Path) -> bool:
    """A confidential document in a case this person may see: attorneys, and the staff named on the case."""
    return user is None or user.get("role") == "attorney" or _named(user, record(client_dir))


def scope(user: dict[str, Any] | None, data_root: str | Path, *, case_ids=None, inventory=None,
          with_visibility: bool = False, workers: int = 1) -> dict[str, Any]:
    """For a list of every case: {"hidden": the case ids this person may not see, "confidential": the case ids whose
    confidential documents they may see, or None for every case}. One pass over the case folders."""
    if (user is None or user.get("role") == "attorney") and not with_visibility:
        return {"hidden": set(), "confidential": None}
    root = Path(data_root)
    names = set(os.listdir(root)) if inventory is None and root.is_dir() else set(inventory or ())
    selected = names if case_ids is None else {case for case in case_ids if isinstance(case, str)}
    everyone = user is None or user.get("role") == "attorney"
    def current(case):
        if not isinstance(case, str) or not case or case in (".", "..") or "/" in case or "\\" in case or "\0" in case or with_visibility and len(case) > 200:
            return case, False, False, True
        if case not in names:
            return case, None, False, False  # a portal-only case still needs the caller's current portal/hold gate
        d = root / case
        try:
            processed = (d / "fact_graph.json").exists()
            if not (processed or (d / FILE).exists()):
                return case, None, False, False
            if everyone:
                return case, True if processed else None, True, False
            rec = record(d)
            named = _named(user, rec)
            closed = not named and (bool((rec["marked"] or {}).get("on")) or law(d) is not None)
            return case, not closed if processed else None, named, closed
        except (OSError, ValueError, TypeError, AttributeError):
            return case, False, False, True
    if workers > 1 and len(selected) > 1:
        from concurrent.futures import ThreadPoolExecutor
        with ThreadPoolExecutor(max_workers=min(int(workers), 8)) as pool:
            results = list(pool.map(current, selected))
    else:
        results = [current(case) for case in selected]
    hidden, confidential, visibility = set(), set(), {}
    # a case file, or a client of a protected kind not processed yet (protect_new: the record alone)
    for case, visible, named, closed in results:
        if visible is not None:
            visibility[case] = visible
        if named:
            confidential.add(case)
        if closed:
            hidden.add(case)
    out = {"hidden": hidden, "confidential": None if everyone else confidential}
    return out | {"visibility": visibility} if with_visibility else out


def messages_allowed(client_dir: str | Path) -> bool:
    """Automatic texts and emails to the client: always, unless the case is restricted and no attorney switched them on."""
    client_dir = Path(client_dir)
    if not client_dir.is_dir() or not is_restricted(client_dir):
        return True
    return bool((record(client_dir)["messages"] or {}).get("on"))


def _track(client_dir: Path) -> str | None:
    """The case's track as the dashboard last worked it out (review/overview.journey_row's cache), for the SIJ hint."""
    row = (_read(client_dir / "journey_summary.json", {}) or {}).get("row") or {}
    return row.get("track")


def state(client_dir: str | Path, people_names: dict[str, str] | None = None) -> dict[str, Any]:
    """What the case page shows: restricted or not and why, who may open it, the automatic-messages switch, the history.
    people_names: {email: name} of the staff accounts, for names in place of emails."""
    client_dir = Path(client_dir)
    rec, found = record(client_dir), law(client_dir)
    marked = rec["marked"] if (rec["marked"] or {}).get("on") else None
    names = people_names or {}
    return {"restricted": bool(found or marked), "law": found, "law_words": UNREADABLE if found == UNKNOWN else LAW_WORDS.get(found or ""),
            "law_phrase": law_words(found) if found != UNKNOWN else "", "marked": marked,
            "people": [p | {"name": names.get(p["email"]) or p.get("name") or p["email"]} for p in rec["people"]],
            "messages": rec["messages"], "automatic_messages": messages_allowed(client_dir),
            "suggest": not found and not marked and _track(client_dir) == "sij",  # a minor's SIJ case: the attorney's choice
            "history": rec["history"][-20:]}


# -- the attorney's changes, each kept with who, when and why --------------------------------------------------


def _attorney(role: str | None, what: str) -> None:
    if role == "paralegal":
        raise PermissionError(f"Only an attorney {what}.")


def _change(client_dir: Path, who: str, what: str, apply, reason: str | None = None) -> dict[str, Any]:
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    data = _read(client_dir / FILE, {}) or {}
    apply(data)
    data.setdefault("history", []).append({"at": clock.stamp(), "by": who, "what": what} | ({"reason": reason} if reason else {}))
    _write(client_dir / FILE, data)
    events.record("access", what.split(" ")[0].lower(), what, case_dir=client_dir, who=who)  # who may open the case; the reason is the firm's and stays on the case
    return record(client_dir)


def mark(client_dir: str | Path, on: bool, reason: str, who: str, role: str | None) -> dict[str, Any]:
    """The attorney restricts a case (on, with a reason), or lifts their own mark. A case the law protects stays restricted."""
    _attorney(role, "restricts a case or lifts the restriction")
    client_dir = Path(client_dir)
    reason = " ".join(str(reason or "").split())
    if on and not reason:
        raise ValueError("Say why the case is restricted (for example: the client is a minor). The client never sees it.")
    if not on and law(client_dir) == UNKNOWN:
        raise ValueError(UNREADABLE)
    if not on and law(client_dir):
        raise ValueError(f"This is {law_words(law(client_dir))}: the law keeps it restricted, and it cannot be lifted.")

    def apply(data):
        data["marked"] = {"on": True, "by": who, "at": clock.stamp(), "reason": reason} if on else None
    return _change(client_dir, who, "Restricted the case" if on else "Lifted the restriction", apply, reason or None)


def name_person(client_dir: str | Path, email: str, on: bool, who: str, role: str | None, name: str = "") -> dict[str, Any]:
    """The attorney names a staff member on the case (they may then open it), or takes them off."""
    _attorney(role, "chooses who may open a restricted case")
    email = str(email or "").strip().lower()
    if "@" not in email:
        raise ValueError("Choose the staff member.")

    def apply(data):
        people = [p for p in data.get("people") or [] if p.get("email") != email]
        if on:
            people.append({"email": email, "name": name or email, "by": who, "at": clock.stamp()})
        data["people"] = people
    return _change(Path(client_dir), who, f"{'Named' if on else 'Took off'} {name or email}", apply)


FIRM_KIND = "firm"  # protect_new's "found" for a kind the firm names protected itself (the import's --protected-type), no statute read
INVITE_HELD = ("No invitation was sent, and none goes out by itself: the office gives the client their sign-in link "
               "in person (an attorney's Show the link).")  # what the screens say for a protected client nobody may message


def protect_new(client_dir: str | Path, found: str | None, kind: str, how: str, who: str, ledger_case: str | None = None) -> bool:
    """A new client of a protected kind (kind_law(kind) is found), restricted from the moment it exists: the case folder is made
    if need be, holding the record with the mark (the kind of case, who, when). how: "Added as", "Imported from Docketwise as",
    "Came in from Clio as". Only when the case has no record yet, so an attorney's later choice (lifting the mark, say) is never
    undone by the next import or sync. True when it marked the case."""
    client_dir = Path(client_dir)
    if found not in (*LAW_WORDS, FIRM_KIND) or (client_dir / FILE).exists():
        return False
    who = str(who or "").strip() or "the office"
    reason = (f"{how} {kind.strip()}: the firm treats this kind of case as confidential from the start." if found == FIRM_KIND
              else f"{how} {kind.strip()}: the law keeps {law_words(found)} confidential from the start.")
    client_dir.mkdir(parents=True, exist_ok=True)
    at = clock.stamp()
    _write(client_dir / FILE, {"marked": {"on": True, "by": who, "at": at, "reason": reason, "law": found if found != FIRM_KIND else None},
                               "history": [{"at": at, "by": who, "what": "Restricted the case", "reason": reason}]})
    if ledger_case:  # a prospect (src/prospects.py): its folder has no prospect.json yet, so the row is named here, never by the bare folder name a client could share
        events.record("access", "restricted", "Restricted the case from the start (a protected kind of case)", case=ledger_case, home=client_dir.parent.parent, who=who)
    else:
        events.record("access", "restricted", "Restricted the case from the start (a protected kind of case)", case_dir=client_dir, who=who)
    return True


def carry_over(from_dir: str | Path, to_dir: str | Path, who: str, came_from: str = "the first call") -> bool:
    """A restriction passes between a prospect (src/prospects.py) and the case it becomes, in either direction, so each is closed to everyone not named on the other. from_dir is
    restricted: to_dir is restricted the same way, with the same people named and the same history and a line saying where it came from. When to_dir has a record already (a protected kind
    of case made at the front desk), only the people named on from_dir are named there too, and the line is added: what an attorney set on to_dir (a lifted mark, say) is never undone.
    came_from: "the first call" (prospect to case) or "the case it became" (case to prospect). True when it wrote something."""
    from_dir, to_dir = Path(from_dir), Path(to_dir)
    if not is_restricted(from_dir):
        return False
    rec = record(from_dir)
    who = str(who or "").strip() or "the office"
    at = clock.stamp()
    line = {"at": at, "by": who, "what": f"Restricted: it came over from {came_from}, which is restricted"}
    if (to_dir / FILE).exists():
        mine = record(to_dir)
        have = {p.get("email") for p in mine["people"]}
        new = [p for p in rec["people"] if p.get("email") not in have]
        if not new:
            return False
        data = _read(to_dir / FILE, {}) or {}
        data["people"] = mine["people"] + new
        data.setdefault("history", []).append(line | {"what": f"Named the staff already named on {came_from}"})
        _write(to_dir / FILE, data)
        events.record("access", "named", "Named the staff already named on the restricted record it came from", case_dir=to_dir, who=who)
        return True
    data = {"marked": rec["marked"] or {"on": True, "by": who, "at": at, "reason": f"Restricted: it came over from {came_from}, which is restricted.", "law": None},
            "people": rec["people"], "messages": rec["messages"], "history": list(rec["history"]) + [line]}
    to_dir.mkdir(parents=True, exist_ok=True)
    _write(to_dir / FILE, data)
    events.record("access", "restricted", "Restricted: it came over from a restricted record", case_dir=to_dir, who=who)
    return True


def set_messages(client_dir: str | Path, on: bool, reason: str, who: str, role: str | None) -> dict[str, Any]:
    """The attorney switches automatic messages on for a restricted case (with a reason), or off again."""
    _attorney(role, "switches automatic messages on or off for a restricted case")
    reason = " ".join(str(reason or "").split())
    if on and not reason:
        raise ValueError("Say why the client may get automatic texts and emails (for example: the client asked for them, the phone is safe).")

    def apply(data):
        data["messages"] = {"on": bool(on), "by": who, "at": clock.stamp(), "reason": reason or None}
    return _change(Path(client_dir), who, "Switched automatic messages on" if on else "Switched automatic messages off", apply, reason or None)
