"""Temporary Protected Status: Form I-821 (edition 01/20/25) with the work permit, Form I-765 under category (a)(12) or (c)(19). Read on
10/02/2026 from uscis.gov/humanitarian/temporary-protected-status (updated 09/09/2026) and each country's own page, uscis.gov/i-821
(updated 09/30/2026), the I-821 and I-765 pages and Form G-1055 (10/01/26):

  When: ONLY in the registration or re-registration period the country's Federal Register notice sets. 8 CFR 244.17(a)-(b): "Applicants
    for re-registration must apply during the period provided by USCIS"; a beneficiary who fails to re-register without good cause has
    TPS withdrawn. The I-821 page: "All TPS applicants should wait to file their Form I-821 ... until publication of the Federal
    Register notice for their respective TPS country". So this filing is offered only while a period recorded in schemas/law/tps.json is
    open today: a reopened or newly extended designation is switched on by copying its notice's dates into that file (the register item
    tps_status says how), and nothing in the code changes. A late re-registration needs good cause and a letter; a late initial
    application needs one of the TPS page's late-initial conditions (a note here, never automatic).
  Today (10/02/2026) no period is open for any country: El Salvador, Sudan and Ukraine's re-registration periods ended 03/18/2025,
    Ukraine's initial period 04/19/2025, Lebanon's registration period 05/27/2026 (its designation was extended automatically to
    11/27/2026 with no re-registration); Venezuela and the others are terminated.
  Who: a national of a designated country (or a stateless person who last habitually resided there) who has lived continuously in the
    U.S. since the country's continuous-residence date and been physically present since its presence date, and is not barred by the
    criminal and security grounds of Part 7 (the TPS page, "Eligibility Requirements").
  What goes in: the I-821; the I-765 when a work permit is wanted; identity and nationality evidence, date-of-entry evidence, continuous-
    residence evidence, certified court dispositions for any arrest or conviction (the TPS page, "Evidence"; the I-821 checklist).
  Fees (Form G-1055 10/01/26, pages 24, 50 and 51): initial registration $510 plus the $30 biometric services fee; re-registration $0
    plus the $30 biometric fee, each separately; the I-765 $520 with the Pub. L. 119-21 fee ($560 initial, $280 renewal) as its own
    payment. From 10/16/2026 the I-821 initial fee is $520 and the initial EAD's Pub. L. 119-21 fee $570 (USCIS alert of 09/30/2026).
  Where: the country's own page names the lockbox: by state for El Salvador and Ukraine, one address for Lebanon and Sudan.
  Travel: a TPS beneficiary who leaves without a TPS travel authorization document may lose TPS (Form I-131, not built here).

Every client-facing sentence here is DRAFT for the attorney.
"""

from __future__ import annotations

import json
import re
from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, addresses, has_doc, latest_notice, money, pending, putter, state_of, us, value
from filing_questions import iso as _d
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "TPS application (I-821 with the I-765)"
DATA = schema_path.path("law", "tps")
INITIAL, REREG = "Initial registration (first time)", "Re-registration (the client has TPS now)"
TPS_EAD = {INITIAL: ("c", "19", ""), REREG: ("a", "12", "")}  # Form I-765, Part 2, 27: (c)(19) a pending applicant, (a)(12) TPS granted
MARITAL = {"Single": "Single", "Married": "Married", "Divorced": "Divorced", "Widowed": "Widowed"}
PROCEEDINGS = ["Immigration Court (before an Immigration Judge)", "Board of Immigration Appeals (BIA)",
               "No longer in DOJ or DHS proceedings, but in or was in Federal court proceedings about immigration"]
MARITAL_OPTIONS = ["Single", "Married", "Divorced", "Widowed", "Separated", "Marriage Annulled", "Other"]
ARRESTS = ["8a", "8b", "8c", "10a", "11", "15a", "15b", "15c"]  # Part 7's items about an arrest, a charge or a conviction: certified court dispositions go with a Yes


def data() -> dict[str, Any]:
    return json.loads(DATA.read_text(encoding="utf-8"))


def _item(item: str) -> str:
    m = re.fullmatch(r"(\d+)([a-e]?)", item)
    return f"{m.group(1)}.{m.group(2).upper()}" if m and m.group(2) else item


def _type(g) -> Any:
    return value(g, "tps.type")


def _part7() -> list[tuple[str, str, dict[str, Any], bool]]:
    return [(f"tps.q{x['item']}", f"Part 7, {_item(x['item'])} · {x['label']}", YES_NO, True) for x in data()["part7"]["items"]]


SECTIONS = [
    ("The request (Part 1)", "the attorney", [
        ("tps.country", "Part 1, 4 · The designated country the client is a national of (or last habitually lived in, if stateless)", TEXT, True),
        ("tps.type", "Part 1, 1 · Initial registration or re-registration", {"type": "choice", "options": [INITIAL, REREG]}, True),
        ("tps.granted_by", "Part 1, 2 · Who granted the client TPS", {"type": "choice", "options": ["USCIS", "Immigration Judge or the Board of Immigration Appeals"]}, False),
        ("tps.ead", "Part 1, 3 · Is the client also asking for a work permit (an I-765 in the same package)?", YES_NO, True),
        ("tps.late", "Late filing: why (a late initial application needs one of the TPS page's conditions; a late re-registration needs good cause, in a letter)", LINES, False),
    ]),
    ("About the client (Part 2)", "the paralegal", [
        ("tps.birth_city", "Part 2, 13 · City, town or village of birth", TEXT, True),
        ("tps.marital_status", "Part 2, 17 · Current marital status", {"type": "choice", "options": MARITAL_OPTIONS}, True),
        ("tps.marriage_date", "Part 2, 18 · Date of the current marriage", DATE, False),
        ("tps.residence_country_1", "Part 2, 15.A · A country the client lived in before entering the U.S. (if not the one above)", TEXT, False),
        ("tps.last_entry_date", "Part 2, 19 · Date of the client's last entry into the U.S.", DATE, True),
        ("tps.entry_status", "Part 2, 20 · The client's immigration status when they last entered (visitor, student, no status...)", TEXT, True),
        ("tps.port_of_entry", "Part 2, 21 · The U.S. port of entry, if any", TEXT, False),
        ("tps.entry_city", "Part 2, 22.A · City or town of the last entry", TEXT, False),
        ("tps.entry_state", "Part 2, 22.B · State of the last entry (two letters)", TEXT, False),
        ("tps.current_status", "Part 2, 31 · The client's immigration status now (or lack of status)", TEXT, True),
        ("tps.in_proceedings", "Part 2, 32 · Is the client now, or ever was, in immigration proceedings?", YES_NO, True),
    ]),
    ("Immigration proceedings (Part 2, 33-36)", "the attorney", [
        ("tps.proceedings_type", "Part 2, 33 · The kind of proceedings", {"type": "choice", "options": PROCEEDINGS}, True),
        ("tps.proceedings_where", "Part 2, 34 · Where the DOJ or DHS proceedings were held or are held", TEXT, False),
        ("tps.federal_court_where", "Part 2, 35 · Where the Federal court proceedings about immigration were held", TEXT, False),
        ("tps.proceedings_from", "Part 2, 36.A · The proceedings began", DATE, True),
        ("tps.proceedings_to", "Part 2, 36.B · The proceedings ended (blank if they are still going)", DATE, False),
    ], lambda g: value(g, "tps.in_proceedings") == "Yes"),
    ("Eligibility (Part 7, 1)", "the attorney", [
        ("tps.entry_date", "Part 7, 1.B · The date the client entered the U.S. and has lived here since", DATE, True),
        ("tps.traveled_elsewhere", "Part 7, 1.C · Has the client ever traveled to and entered another country, other than the one above, before they last entered the U.S.? "
                                   "(if Yes, Part 7 items 2-7 are completed by hand)", YES_NO, True),
    ]),
    ("Criminal, security and other grounds (Part 7)", "the attorney", _part7()),
]
MORE_QUESTIONS = "Answer the type of application first: who granted TPS, the marriage date and the proceedings appear when they apply."


def more_to_come(graph) -> bool:
    """Only until the proceedings question is answered: it is the last one that adds questions."""
    return value(graph, "tps.in_proceedings") is None


# -- the countries, and whether a period is open -------------------------------------------------

def country_of(graph) -> tuple[str | None, dict[str, Any] | None]:
    """(the country's name, its entry) for the client's country: the answer, else the nationality in the case."""
    raw = re.sub(r"\s+", " ", str(value(graph, "tps.country") or value(graph, "applicant.citizenship") or "")).strip().upper()
    for name, entry in data()["countries"].items():
        if raw == name.upper() or raw in entry.get("aliases", []):
            return name, entry
    return None, None


def terminated(graph) -> tuple[str | None, dict[str, Any] | None]:
    raw = re.sub(r"\s+", " ", str(value(graph, "tps.country") or value(graph, "applicant.citizenship") or "")).strip().upper()
    for name, entry in data().get("terminated", {}).items():
        if name.startswith("_"):
            continue
        if raw in (name.upper(), name.upper().split(" (")[0]):
            return name, entry
    return None, None


def periods(entry: dict[str, Any], on: date) -> list[dict[str, Any]]:
    """The periods open on this day (copied from the country's Federal Register notice)."""
    return [p for p in entry.get("periods") or [] if _d(p["from"]) and _d(p["to"]) and _d(p["from"]) <= on <= _d(p["to"])]


def window(graph, today: date) -> dict[str, Any]:
    """kind: open (a period is open now), closed (designated, none open), awaiting (the date the page gives has passed, no period is open and USCIS
    has not announced what comes next: protection continues, 8 U.S.C. 1254a(b)(3)(C)), terminated, unlisted, or no_country."""
    name, entry = country_of(graph)
    if entry is None:
        gone, info = terminated(graph)
        raw = value(graph, "tps.country") or value(graph, "applicant.citizenship")
        if gone:
            return {"kind": "terminated", "country": gone, "info": info}
        return {"kind": "unlisted" if raw else "no_country", "country": raw}
    if entry["status"] != "designated":
        return {"kind": "terminated", "country": name, "entry": entry}
    open_now = periods(entry, today)
    past = sorted((p for p in entry.get("periods") or [] if _d(p["to"]) and _d(p["to"]) < today), key=lambda p: p["to"])
    awaiting = not open_now and bool(_d(entry.get("through"))) and _d(entry["through"]) < today
    return {"kind": "open" if open_now else "awaiting" if awaiting else "closed", "country": name, "entry": entry, "open": open_now, "last": past[-1] if past else None}


def closed(graph, today: date) -> dict[str, str] | None:
    """The one sentence a country shows, with nothing else on its panel (no questions, no packet), when there is nothing to file: its
    designation was terminated, the country was never designated, or the date its page gives has passed with no period open and no announcement
    yet. None for every other case."""
    w = window(graph, today)
    if w["kind"] == "terminated":
        info = w.get("info") or {}
        return {"level": "warn", "title": "Terminated",
                "text": f"{w['country']}'s TPS designation was terminated" + (f", effective {us(_d(info['effective']))}" if info.get("effective") else "")
                + (f" ({info['source']})" if info.get("source") else "") + ". Nothing to file now; if USCIS reopens it, the register's TPS item says how this filing switches on."
                + (f" {w['entry']['note']}" if w.get("entry", {}).get("note") else "")}
    if w["kind"] == "unlisted":  # Implementation note.
        return {"level": "warn", "title": "Not on USCIS's list",
                "text": f"{w['country']} isn't on USCIS's list of countries designated for TPS (its TPS page, updated {data()['page_updated']}): there is "
                        "nothing to file. If USCIS designates it, the register's TPS item says how this filing switches on."}
    if w["kind"] == "awaiting":
        entry = w["entry"]
        read = f" (last reviewed {us(_d(entry['page_updated_iso']))}, read {us(_d(entry['read_on']))})" if entry.get("page_updated_iso") and entry.get("read_on") else ""
        return {"level": "warn", "title": "Nothing to file: no registration period is open",
                "text": f"{w['country']}: USCIS's page{read} gives TPS continued through {us(_d(entry['through']))} and says an announcement will be made; until then "
                        "people in the U.S. under TPS keep protection, including work authorization. The statute extends a designation by six months when no "
                        "determination is made (8 U.S.C. 1254a(b)(3)(C)). No registration period is open, so there is nothing to file here."
                        f"{_past(w)} When USCIS publishes a new period, the register's TPS item says how this filing switches on."}
    return None


def offered(graph, today: date) -> bool:
    """The case path offers the filing only while a registration period is open for the client's country."""
    return window(graph, today)["kind"] == "open"


def recipient(graph) -> bool:
    """A TPS case: the client's country is one of those USCIS lists as designated, or a TPS work permit or notice is in the case."""
    import re as _re

    return country_of(graph)[1] is not None or _re.sub(r"[^A-Z0-9]", "", str(value(graph, "applicant.ead_category") or "").upper()) in ("A12", "C19")


# -- the filing's facts --------------------------------------------------------------------------

def _holds_tps(graph) -> bool:
    cat = re.sub(r"[^A-Z0-9]", "", str(value(graph, "applicant.ead_category") or "").upper())
    return cat in ("A12", "C19") or bool(latest_notice(graph, "I-821", "approval"))


def derive(graph, today: date):
    put = putter(graph, "tps.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    name, entry = country_of(graph)
    put("tps.country", name.upper() if name else v("applicant.citizenship"), "the client's nationality in the case" if not name else f"{name}: on USCIS's TPS list")
    w = window(graph, today)
    if _holds_tps(graph):
        put("tps.type", REREG, "the client has a TPS work permit or approval in the case")
    elif w["kind"] == "open" and any(p["kind"] == "initial" for p in w["open"]):
        put("tps.type", INITIAL, "an initial registration period is open and the client has no TPS in the case")
    put("tps.mailing_same", "No" if v("applicant.mailing_same_as_physical") == "No" else "Yes", "the client's addresses")
    addresses(graph, put, "tps")
    put("tps.birth_city", v("applicant.birth_city"), "the client's birth certificate")
    status = v("applicant.marital_status")
    put("tps.marital_status", MARITAL.get(status), "the client's marital status in the case")
    put("tps.marriage_date", v("applicant.marriage_date") if v("tps.marital_status") == "Married" else None, "the client's marriage")
    arrived = v("applicant.last_arrival_date") or v("applicant.i94_arrival_date") or v("applicant.last_arrival_date_self_reported")
    put("tps.last_entry_date", arrived, "the last entry in the case")
    put("tps.entry_date", arrived, "the last entry in the case: change it if the client first entered earlier")
    put("tps.entry_status", v("applicant.i94_class_of_admission"), "the class of admission on the client's I-94")
    put("tps.current_status", "TPS" if v("tps.type") == REREG else None, "the client has TPS")
    if v("applicant.nta_present"):
        put("tps.in_proceedings", "Yes", "a Notice to Appear is in the case")
    # the work permit (Form I-765): its own answers, so no other filing's reach it
    addresses(graph, put, "tps_ead")
    put("tps_ead.mailing_same", "No" if v("applicant.mailing_same_as_physical") == "No" else "Yes", "the client's addresses")
    kind = v("tps.type")
    if kind in TPS_EAD:
        for n, box in enumerate(TPS_EAD[kind], start=1):
            if box:
                put(f"tps_ead.category_{n}", box, f"TPS: category ({TPS_EAD[kind][0]})({TPS_EAD[kind][1]})")
        put("tps_ead.current_status", "TPS beneficiary" if kind == REREG else "TPS applicant", "the TPS application")
    has_card = bool(v("applicant.ead_expiration_date")) and _holds_tps(graph)
    put("tps_ead.reason", "Renewal" if has_card else "Initial", "a TPS work permit is already in the case" if has_card else "a first TPS work permit")
    put("tps_ead.previous_filed", "Yes" if has_card or v("applicant.ead_expiration_date") else "No",  # Part 2, 12
        "a work permit is already in the case" if has_card or v("applicant.ead_expiration_date") else "no work permit in the case: change it if the client filed an I-765 before")
    put("tps_ead.entry_status", v("tps.entry_status"), "Part 2, 24: the status at the last entry, the same answer as the I-821's Part 2, 20")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def forms_for(graph, forms: list[str]) -> list[str]:
    """The I-765 goes in only when the client is asking for a work permit (Part 1, 3)."""
    return [f for f in forms if f != "i765_tps" or value(graph, "tps.ead") != "No"]


# -- fees, payments, address ----------------------------------------------------------------------

def fees(graph, today: date) -> dict[str, int | None]:
    """The amounts for this request (Form G-1055 10/01/26): the I-821, its biometric fee, the I-765 and its Pub. L. 119-21 fee."""
    import fees as schedule

    data_ = schedule.load(today)
    paper, pl = data_.get("paper") or {}, data_.get("pl_119_21") or {}
    rereg = _type(graph) == REREG
    return {"i821": paper.get("i821_reregistration" if rereg else "i821_initial"), "biometrics": paper.get("i821_biometrics"),
            "i765": paper.get("i765_tps_renewal" if value(graph, "tps_ead.reason") == "Renewal" else "i765_tps_initial"),
            "i765_pl": pl.get("tps_ead_renewal" if value(graph, "tps_ead.reason") == "Renewal" else "tps_ead_initial")}


def payments(graph, today: date, forms: list[str]) -> list[tuple[str, str, Any, str]]:
    """(form id, form, amount, what): one payment for each request, and each fee USCIS says is separate (src/payment.py)."""
    f = fees(graph, today)
    out = [("i821", "I-821", f["i821"], "Form I-821 filing fee"), ("i821", "I-821 biometrics", f["biometrics"], "Form I-821 biometric services fee: its own payment")]
    if "i765_tps" in forms:
        out += [("i765_tps", "I-765", f["i765"], "Form I-765 filing fee, TPS work permit"),
                ("i765_tps", "I-765", f["i765_pl"], "Pub. L. 119-21 fee for a TPS work permit: its own payment")]
    return out


def address(graph) -> tuple[list[str] | None, str]:
    name, entry = country_of(graph)
    if not entry or not entry.get("lockboxes"):
        return None, "the client's country isn't on USCIS's TPS filing list"
    state = state_of(graph)
    for box in entry["lockboxes"].values():
        if state in box["states"] or "*" in box["states"]:
            return list(box["usps"]), f"{name}'s TPS page ({entry['page_updated']}): the {box['name']} for {state or 'every state'}"
    return None, f"the client's state ({state or 'unknown'}) isn't on {name}'s TPS page: the attorney confirms where it goes"


# -- notes, problems, letter ------------------------------------------------------------------------

def _past(w: dict[str, Any]) -> str:
    last = w.get("last")
    return f" The last one ({last['kind']}) ran {us(_d(last['from']))} to {us(_d(last['to']))} ({last['source']})." if last else ""


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    w = window(graph, today)
    out = []
    if w["kind"] == "open":
        p = w["open"][0]
        out.append({"level": "info", "title": "The period is open", "text": f"{w['country']}: a {p['kind']} period is open from {us(_d(p['from']))} to {us(_d(p['to']))} ({p['source']}). "
                    "File inside it: the date the package is received decides it (8 CFR 244.17)."})
    elif w["kind"] == "closed":
        out.append({"level": "warn", "title": "No period is open", "text": f"{w['country']} is designated through {us(_d(w['entry'].get('through')))}, but USCIS takes a TPS application only during the "
                    f"period its Federal Register notice sets (8 CFR 244.17; the I-821 page says to wait for the notice).{_past(w)} Late filing needs good cause (re-registration) or one of "
                    "the TPS page's late-initial conditions. " + w["entry"].get("note", "")})
    elif w["kind"] == "terminated":
        info = w.get("info") or {}
        out.append({"level": "warn", "title": "Terminated", "text": f"{w['country']}'s TPS designation was terminated" + (f", effective {us(_d(info['effective']))}" if info.get("effective") else "")
                    + (f" ({info['source']})" if info.get("source") else "") + ". Nothing to file now; if USCIS reopens it, the register's TPS item says how this filing switches on."
                    + (f" {w['entry']['note']}" if w.get("entry") else "")})
    elif w["kind"] == "awaiting":
        out.append(closed(graph, today))
    elif w["kind"] == "unlisted":
        out.append({"level": "warn", "title": "Not on USCIS's list", "text": f"{w['country']} isn't on USCIS's list of countries designated for TPS (its TPS page, updated {data()['page_updated']})."})
    f = fees(graph, today)
    out.append({"level": "info", "title": "Fees", "text": f"I-821: {money(f['i821'])} plus the {money(f['biometrics'])} biometric services fee, as separate payments. A work permit: {money(f['i765'])} for the "
                f"I-765 plus the Pub. L. 119-21 fee of {money(f['i765_pl'])}, each its own payment (Form G-1055, edition 10/01/26). From 10/16/2026 an initial I-821 is $520 and the "
                "initial work permit's Pub. L. 119-21 fee $570 (USCIS alert of 09/30/2026): the amounts above already follow the date. A fee waiver (Form I-912) can cover the biometric "
                "fee only; this system doesn't build one for TPS."})
    lines, where = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({where})") if lines else where})
    out.append({"level": "info", "title": "Online instead", "text": "USCIS also takes the I-821 and I-765 online, in a USCIS online account, by the guided form (not by PDF upload). "
                "This packet is the paper filing."})
    name, entry = country_of(graph)
    if entry and entry.get("status") == "designated" and entry.get("continuous_residence_since"):
        out.append({"level": "info", "title": "Continuous residence and presence", "text": f"{name}: the client must have lived in the U.S. continuously since {us(_d(entry['continuous_residence_since']))} "
                    f"and been physically present since {us(_d(entry['continuous_presence_since']))} (the country's TPS page)."})
    out.append({"level": "warn", "title": "Travel", "text": "A client with TPS who leaves the U.S. without a TPS travel authorization document may lose TPS and not be allowed back; one with a "
                "pending application needs advance parole (the TPS page). Both are an I-131, which this system doesn't build for TPS."})
    yes = [_item(x["item"]) for x in data()["part7"]["items"] if v(f"tps.q{x['item']}") == "Yes"]
    if yes:
        out.append({"level": "warn", "title": "Part 7", "text": f"Yes to {', '.join(yes)}: explain each in Part 11 with the place and date, and attach the certified court dispositions "
                    "(or a signed statement why they can't be had). The attorney weighs the grounds of INA 244(c)(2) before filing."})
    if pending(graph, "I-821"):
        out.append({"level": "warn", "title": "An I-821 is pending", "text": f"The I-821 with receipt {pending(graph, 'I-821')['receipt']} hasn't been decided: filing another may be wrong."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    w, out = window(graph, today), []
    if w["kind"] == "no_country":
        out.append("The client's country isn't in the case: TPS is for a national of a designated country.")
    elif w["kind"] == "unlisted":
        out.append(held(ATTORNEY, f"{w['country']} isn't on USCIS's list of countries designated for TPS (updated {data()['page_updated']}): there is nothing to file."))
    elif w["kind"] == "terminated":
        info = w.get("info") or {}
        out.append(held(ATTORNEY, f"{w['country']}'s TPS designation was terminated" + (f" (effective {us(_d(info['effective']))})" if info.get("effective") else "")
                        + ": USCIS isn't taking applications. If USCIS reopens it, add the period to the register's TPS item."))
    elif w["kind"] == "awaiting":
        out.append(held(OFFICE, f"No registration period is open for {w['country']} (USCIS's page gives TPS continued through {us(_d(w['entry']['through']))} and an announcement "
                        "is pending): there is nothing to file now."))
    elif w["kind"] == "closed":
        out.append(held(ATTORNEY, f"No registration period is open for {w['country']}: USCIS takes a TPS application only during the period its Federal Register notice sets (8 CFR 244.17).{_past(w)} "
                        "A late re-registration needs good cause, in a letter; a late initial application needs one of the TPS page's conditions: the attorney decides."))
    elif w["kind"] == "open":
        kinds = {p["kind"] for p in w["open"]}
        if _type(graph) == REREG and "re-registration" not in kinds:
            out.append(held(ATTORNEY, "Re-registration is for a client who has TPS now, and only the initial registration period is open: the client can't re-register now."))
        if _type(graph) == INITIAL and "initial" not in kinds:
            out.append(held(ATTORNEY, "An initial application: only the re-registration period is open, which is for clients who already have TPS. The client can't register for the first time now, "
                            "unless a late-initial condition applies (the TPS page): the attorney decides."))
    entry = w.get("entry")
    arrived, resides = _d(v("tps.entry_date")), _d((entry or {}).get("continuous_residence_since"))
    if arrived and resides and arrived > resides:
        out.append(held(ATTORNEY, f"The client entered on {us(arrived)}, after {w['country']}'s continuous residence date, {us(resides)}: not eligible unless USCIS's rules for that country say otherwise. "
                        "The attorney decides."))
    if _type(graph) == REREG and not v("applicant.a_number"):
        out.append("A re-registration needs the A-Number: USCIS rejects it without one (the I-821 page's Filing Tips).")
    if _type(graph) == REREG and not v("tps.granted_by"):
        out.append("Part 1, 2: who granted the client TPS (USCIS, or an Immigration Judge or the Board).")
    if not has_doc(client_dir, "passport", "national_id", "birth_certificate"):
        out.append("Evidence of the client's identity and nationality (a passport, a birth certificate with photo ID, or a national ID): none in the folder.")
    if not has_doc(client_dir, "i94", "passport", "visa"):
        out.append("Evidence of the date the client entered the U.S. (the I-94, or the passport page with the entry stamp): none in the folder.")
    if not has_doc(client_dir, "lease", "utility_bill", "pay_stub", "w2", "tax_return", "medical_record", "school_record", "bank_statement"):
        out.append("Evidence of continuous residence in the U.S. since the country's date (employment records, rent receipts or utility bills, school or medical records): none in the folder.")
    if any(v(f"tps.q{item}") == "Yes" for item in ARRESTS) and not has_doc(client_dir, "criminal_record", "court_disposition", "police_report"):
        out.append("A Yes about an arrest, charge or conviction: the certified court dispositions and arrest reports are not in the folder (or a signed statement why they can't be had).")
    f = fees(graph, today)
    if f["i821"] is None:
        out.append(held(OFFICE, "The I-821 fee can't be set yet: choose initial registration or re-registration."))
    lines, where = address(graph)
    if lines is None:
        out.append(held(OFFICE, f"The filing address can't be set: {where}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    f = fees(graph, today)
    lines, _ = address(graph)
    wants_ead = value(graph, "tps.ead") != "No"
    rereg = _type(graph) == REREG
    paid = [f"{money(f['i821'])} for Form I-821" if f["i821"] else None, f"{money(f['biometrics'])} for the I-821 biometric services fee",
            f"{money(f['i765'])} for Form I-765" if wants_ead else None,
            f"{money(f['i765_pl'])} for the Pub. L. 119-21 fee on Form I-765" if wants_ead and f["i765_pl"] else None]
    text = ("Enclosed are the following payments, each by its own enclosed Form G-1450, per Form G-1055, edition 10/01/26: " + "; ".join(p for p in paid if p) + ".")
    return {"re_lines": [f"Application: I-821 Application for Temporary Protected Status ({'Re-registration' if rereg else 'Initial registration'}), {value(graph, 'tps.country') or '[country]'}",
                         *(["With: I-765 Application for Employment Authorization, category " + ("(a)(12)" if rereg else "(c)(19)")] if wants_ead else [])],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": False}
