"""A denied N-400: a hearing before another USCIS officer, on Form N-336
(edition 04/01/24) -- 8 CFR 336.2 (data/reference/8cfr336.2.xml) and USCIS's
pages (data/reference/n-336-page.txt, i-290b-when.txt):

  When: within 30 calendar days after the client receives the denial, 33 when
    USCIS mailed it. A late request is rejected and the fee kept -- unless it
    meets a motion's requirements, when USCIS reopens or reconsiders.
  The hearing: new evidence and testimony; a client denied for the English or
    civics test can retake the part they failed.
  Fee (G-1055 10/01/26): $830 on paper; $0 for an N-400 filed under INA 328
    or 329 (military service).
  Where: the N-336's own lockboxes by state (schemas/law/uscis_lockboxes_n336.json),
    or online in the client's USCIS account.
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, addresses, latest_notice, lockbox, money, putter, state_of, us, value
from filing_questions import iso as _d
from holders import ATTORNEY, CLIENT, held, producer

TITLE = "Hearing on a denied N-400 (N-336)"
SECTIONS = [
    ("The denial", "the attorney", [
        ("n336.receipt", "The N-400's receipt number", TEXT, True),
        ("n336.denial_date", "Date of the denial notice", DATE, True),
        ("n336.mailed", "USCIS mailed the denial? (then 33 days instead of 30)", YES_NO, True),
        ("n336.office", "The USCIS office that issued the denial (on the notice)", TEXT, True),
        ("n336.military", "Was the N-400 filed on the basis of military service (INA 328 or 329)?", YES_NO, True),
    ]),
    ("Why the denial was wrong (Part 4)", "the attorney", [
        ("n336.reason", "The reasons, in brief: the full argument and new evidence go with it", LINES, True),
        ("n336.retest", "Denied for the English or civics test? (the client can retake the part failed)", YES_NO, False),
    ]),
]


def derive(graph, today: date):
    put = putter(graph, "n336.derive")
    d = latest_notice(graph, "N-400", "denial")
    if d:
        put("n336.receipt", d["receipt"], "the N-400 denial notice")
        put("n336.denial_date", (_d(d["date"]) or today).isoformat(), "the denial notice's date")
    addresses(graph, put, "n336")
    if value(graph, "applicant.mailing_same_as_physical") != "No":  # the form asks for both: the home address is also the mailing address
        for part in ("street", "unit_type", "apt", "city", "state", "zip"):
            put(f"n336.mailing_{part}", value(graph, f"applicant.physical_{part}"), "the client's home address (also their mailing address)")
    return graph


def due(graph) -> date | None:
    denied = _d(value(graph, "n336.denial_date"))
    return denied + timedelta(days=33 if value(graph, "n336.mailed") == "Yes" else 30) if denied else None


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    if value(graph, "n336.military") == "Yes":
        return 0, "an N-400 filed under INA 328 or 329 (military service): no fee (G-1055)"
    return (fees.load(today).get("paper") or {}).get("n336"), "the N-336 paper filing fee (G-1055)"


def mail_to(graph) -> tuple[list[str] | None, str | None]:
    return lockbox("uscis_lockboxes_n336", state_of(graph))


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    last = due(graph)
    if last:
        out.append({"level": "warn" if today > last - timedelta(days=7) else "info", "title": "When",
                    "text": f"By {us(last)}: 30 days after the client received the denial (8 CFR 336.2(a)), 33 when USCIS mailed it. Late, USCIS rejects it and keeps "
                            "the fee, unless it meets a motion's requirements."})
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}."})
    lines, name = mail_to(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where",
                "text": (f"USCIS {name} lockbox: " + " / ".join(lines) + ", or online in the client's USCIS account.") if lines else
                "The client's state isn't on the N-336 chart : the attorney sets the address by hand."})
    if value(graph, "n336.retest") == "Yes":
        out.append({"level": "info", "title": "The test", "text": "At the hearing the client can retake the part of the test they failed: prepare them for it."})
    return out


@producer(ATTORNEY)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    out = []
    last = due(graph)
    if last and today > last:
        out.append(f"Past the deadline ({us(last)}): USCIS rejects a late N-336 and keeps the fee, unless it meets the requirements of a motion "
                   "to reopen or reconsider: the attorney decides.")
    if not latest_notice(graph, "N-400", "denial") and not value(graph, "n336.denial_date"):
        out.append(held(CLIENT, "No N-400 denial in the case: the N-336 asks for a hearing on one."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, _why = fee(graph, today)
    lines, _ = mail_to(graph)
    import fees

    edition = fees.load(today).get("edition") or "current"
    text = (f"No filing fee is required for this Form N-336: the Form N-400 was filed on the basis of military service (Form G-1055, edition {edition})."
            if amount == 0 else f"Enclosed is the filing fee of {money(amount)} for Form N-336, paid by the enclosed Form G-1450 "
            f"(Form G-1055, edition {edition})." if amount else "Filing fee: [the attorney sets it. See the packet's problems].")
    return {"re_lines": ["Form N-336, Request for a Hearing on a Decision in Naturalization Proceedings",
                         f"Form N-400 Receipt Number {value(graph, 'n336.receipt') or '[receipt]'}"],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": amount == 0}
