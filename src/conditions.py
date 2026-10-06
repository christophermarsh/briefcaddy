"""Form I-751, removing the conditions on a 2-year green card (a resident
through a marriage less than 2 years old when residence was granted, or the
child of one).

The dates are the whole point (Form I-751 Instructions, edition 04/01/24;
USCIS's I-751 filing page): a JOINT petition is filed only in the 90 days
before the card expires -- USCIS rejects one filed earlier; a WAIVER
(the spouse died, divorce, abuse, extreme hardship) can be filed any time
after residence was granted. A petition filed after the card expires needs
a written explanation of why it is late; meanwhile the client's residence
ends automatically on the expiry date. Filed by the client's state at the
Elgin or Phoenix lockbox (schemas/law/uscis_lockboxes_i751.json).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import (DATE, TEXT, YES_NO, addresses, has_doc, in_exhibit, iso, latest_notice, lockbox, money, plus_years, putter, state_of,
                              us, value)
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "I-751 (removing conditions on residence)"
JOINT, JOINT_PARENT = "Joint petition with my spouse", "Joint petition with my parent's spouse"
WAIVERS = {"i751.waiver_spouse_deceased": "the spouse died", "i751.waiver_divorce": "good-faith marriage ended in divorce or annulment",
           "i751.waiver_abuse": "battered or subjected to extreme cruelty by the spouse",
           "i751.waiver_parent_abuse": "the conditional resident parent's marriage, battery or extreme cruelty",
           "i751.waiver_hardship": "removal would cause extreme hardship"}
SECTIONS = [
    ("The petition", "the attorney", [
        ("i751.card_expires", "Part 1, 14 · Date the 2-year card expires (on the card)", DATE, True),
        ("i751.basis", "Part 3 · Joint petition (still married), or leave blank and choose a waiver below",
         {"type": "choice", "options": [JOINT, JOINT_PARENT]}, False),
        *[(key, f"Part 3 · Waiver: {why}", YES_NO, False) for key, why in WAIVERS.items()],
        ("i751.spouse_relationship", "Part 4, 1 · The person in Part 4 is the client's",
         {"type": "choice", "options": ["Spouse or former spouse", "Parent's spouse or former spouse"]}, True),
        ("i751.marriage_place", "Part 1, 12 · Place of the marriage (city, state or country)", TEXT, True),
        ("i751.marriage_ended", "Part 1, 13 · Date the marriage ended (divorce or death), if it did", DATE, False),
        ("i751.late_reason", "Filed after the card expired: why (good cause. Goes in a letter with the petition)", {"type": "text", "multiline": True}, False),
    ]),
    ("The client", "the attorney", [
        ("i751.in_proceedings", "Part 1, 18 · In removal, deportation or rescission proceedings?", YES_NO, True),
        ("i751.fee_paid_to_other", "Part 1, 19 · Was a fee paid to anyone other than an attorney for this petition?", YES_NO, True),
        ("i751.ever_arrested", "Part 1, 20 · Ever arrested, detained, charged, fined or imprisoned, or committed a crime?", YES_NO, True),
        ("i751.different_marriage", "Part 1, 21 · If married: a different marriage from the one the card came through?", YES_NO, False),
        ("i751.other_addresses", "Part 1, 22 · Lived at any other address since becoming a resident?", YES_NO, True),
        ("i751.spouse_gov_abroad", "Part 1, 23 · Spouse serving with or employed by the U.S. government abroad?", YES_NO, True),
        ("i751.accommodation", "Part 6, 1 · Requesting an accommodation for a disability?", YES_NO, True),
        ("applicant.spouse_family_name", "Part 4, 2.a · The spouse's family name (or the parent's spouse's)", TEXT, True),
        ("applicant.spouse_given_name", "Part 4, 2.b · The spouse's given name", TEXT, True),
        ("applicant.spouse_dob", "Part 4, 3 · The spouse's date of birth", DATE, False),
        ("i751.spouse_ssn", "Part 4, 4 · The spouse's Social Security number", TEXT, False),
        ("i751.spouse_a_number", "Part 4, 5 · The spouse's A-Number (a resident spouse)", TEXT, False),
    ]),
]


def window(graph) -> tuple[date | None, date | None]:
    """(the first day a joint petition may be filed, the card's expiry)."""
    expires = iso(value(graph, "i751.card_expires"))
    return (expires - timedelta(days=90), expires) if expires else (None, None)


def joint(graph) -> bool:
    return value(graph, "i751.basis") in (JOINT, JOINT_PARENT)


def waivers(graph) -> list[str]:
    return [why for key, why in WAIVERS.items() if value(graph, key) == "Yes"]


def derive(graph, today: date):
    put = putter(graph, "conditions.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "i751")
    put("i751.physical_different", "Yes" if v("applicant.mailing_same_as_physical") == "No" else "No", "the client's addresses")
    approval = latest_notice(graph, "I-485", "approval")
    resident = iso((approval or {}).get("date") or v("petitioner.lpr_date"))
    if resident:
        put("i751.card_expires", plus_years(resident, 2).isoformat(),
            f"2 years after the client became a resident ({us(resident)}, {'the I-485 approval' if approval else 'the green card'}): check the card")
    if v("applicant.marital_status") == "Married" and not waivers(graph):
        put("i751.basis", JOINT, "the client is married (a joint petition, unless the attorney chooses a waiver)")
    put("i751.spouse_relationship", "Spouse or former spouse", "the client's own marriage (change it for a child filing through a parent)")
    put("i751.in_proceedings", "Yes" if v("applicant.nta_present") else None, "a Notice to Appear in the folder")
    put("i751.fee_paid_to_other", "No", "the firm prepared it (change it if someone else was paid)")
    put("i751.spouse_gov_abroad", "No" if v("applicant.spouse_in_military") == "No" else None, "the spouse is not in the military")
    put("i751.accommodation", "No", "no accommodation requested (the default: change it if needed)")
    put("i751.marriage_place", ", ".join(x for x in (v("applicant.marriage_city"), v("applicant.marriage_state") or v("applicant.marriage_country")) if x) or None,
        "the marriage certificate")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def address(graph) -> tuple[list[str] | None, str]:
    state = state_of(graph)
    lines, box = lockbox("uscis_lockboxes_i751", state)
    return lines, (f"the I-751 chart, by state ({box})" if box else f"the client's state ({state or 'unknown'}) is not on the I-751 chart")


def fee(graph, today: date) -> tuple[int | None, str]:
    """The I-751 fee (G-1055 10/01/26): $750 on paper; $0 for a conditional resident, spouse or child whose waiver is based on
    battery or extreme cruelty. One fee covers the children listed in Part 5."""
    import fees

    paper = fees.load(today).get("paper") or {}
    if any(value(graph, key) == "Yes" for key in ("i751.waiver_abuse", "i751.waiver_parent_abuse")):
        return paper.get("i751_abuse_waiver"), "a waiver based on battery or extreme cruelty: no fee (G-1055)"
    return paper.get("i751"), "the I-751 fee (G-1055, paper filing)"


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    opens, expires = window(graph)
    if expires:
        if joint(graph):
            if today < opens:
                text, level = f"A joint petition can be filed from {us(opens)} to {us(expires)}, not before: USCIS rejects it ({(opens - today).days} days to go).", "info"
            elif today <= expires:
                text, level = f"The 90-day window is open: file by {us(expires)} ({(expires - today).days} days left).", "warn"
            else:
                text, level = f"The card expired {us(expires)}: file now with a letter explaining why it is late. The client's residence ended on that date until USCIS accepts the late filing.", "bad"
        else:
            text = (f"A waiver petition can be filed any time before the client is removed; the card expires {us(expires)}"
                    + (". Already expired: file now and explain the delay." if today > expires else "."))
            level = "warn" if today > expires else "info"
        out.append({"level": level, "title": "When to file", "text": text})
    amount, why = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)}: {why}. One fee covers the children listed in Part 5."})
    lines, why = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({why})") if lines else why})
    out.append({"level": "info", "title": "After filing", "text": "The receipt notice extends the client's residence for the period it states: the client keeps it with the expired card as proof of status."})
    return out


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    opens, expires = window(graph)
    chosen = waivers(graph)
    if not joint(graph) and not chosen:
        out.append("Part 3: a joint petition or at least one waiver.")
    if joint(graph) and chosen:
        out.append("Part 3: a joint petition and a waiver are both chosen. Choose one or the other.")
    if joint(graph) and opens and today < opens:
        out.append(f"A joint petition can't be filed before {us(opens)} (90 days before the card expires on {us(expires)}): USCIS rejects an early one.")
    if expires and today > expires and not v("i751.late_reason"):
        out.append(held(CLIENT, f"The card expired on {us(expires)}: a late petition needs the written reason it is late (good cause)."))
    if joint(graph) and v("applicant.marital_status") in ("Divorced", "Widowed"):
        out.append(f"The client is {v('applicant.marital_status').lower()}: a joint petition isn't possible. Use the waiver for the spouse's death or the divorce.")
    if "good-faith marriage ended in divorce or annulment" in chosen and not has_doc(client_dir, "divorce_decree"):
        out.append(held(CLIENT, "Divorce waiver: the final divorce or annulment decree, not in the folder. (A pending divorce: USCIS issues a request for evidence; the attorney decides.)"))
    if "the spouse died" in chosen and not in_exhibit(client_dir, "i751", "ended"):
        out.append("Waiver for the spouse's death: the death certificate. Put it in the \"How the marriage ended\" exhibit.")
    good_faith = set(has_doc(client_dir, "lease", "bank_statement", "tax_return", "utility_bill", "photograph")) | set(in_exhibit(client_dir, "i751", "good_faith"))
    if v("i751.basis") == JOINT or "good-faith marriage ended in divorce or annulment" in chosen or "the spouse died" in chosen:
        if len(good_faith) < 2:
            out.append(held(CLIENT, "Evidence of a good-faith marriage (joint lease or mortgage, joint bank accounts, joint tax returns, insurance, children's birth "
                       "certificates, affidavits): " + (f"only {len(good_faith)} document" if good_faith else "none") + " in the folder."))
    if v("i751.ever_arrested") == "Yes" and not has_doc(client_dir, "criminal_record"):
        out.append(held(CLIENT, "Part 1, 20 is \"Yes\": certified court dispositions for every arrest or charge, not in the folder."))
    if v("i751.other_addresses") == "Yes":
        out.append("Part 1, 22 is \"Yes\": every address since becoming a resident, with dates, goes in Part 11 (filled by hand).")
    if v("i751.in_proceedings") == "Yes":
        out.append(held(ATTORNEY, "The client is in removal proceedings: the attorney reviews where and how the I-751 is filed."))
    if not address(graph)[0]:
        out.append(f"The filing address can't be set yet: {address(graph)[1]}.")
    return out


def letter(graph, today: date) -> dict[str, Any]:
    lines, _ = address(graph)
    amount, _why = fee(graph, today)
    basis = "joint petition" if joint(graph) else "request to waive the joint filing requirement (" + "; ".join(waivers(graph)) + ")" if waivers(graph) else "petition"
    return {"re_lines": [f"Petition: I-751 Petition to Remove Conditions on Residence ({basis})"], "noun": "petition", "who": "Petitioner",
            "mail_to": lines or ["[USCIS address: see the packet's problems]"],
            "fees": (f"Enclosed is the filing fee of {money(amount)} for Form I-751, paid by the enclosed Form G-1450, per Form G-1055, edition 10/01/26."
                     if amount else "No filing fee is due for Form I-751: the petition requests a waiver of the joint filing requirement based on battery "
                                    "or extreme cruelty (Form G-1055, edition 10/01/26)."), "no_payment": not amount}
