"""Form I-90: renewing or replacing a green card -- for a resident whose card
is expiring or expired (10-year cards), lost or stolen, never received, wrong
because of a USCIS error, or out of date after a name change; and at 14,
when a child resident must register for a new card.

Not for a conditional resident whose 2-year card is ending: that is Form
I-751 (src/conditions.py) -- this module says so instead of filing an I-90.
Filed (on paper) with the USCIS Phoenix lockbox, whatever the state.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, TEXT, YES_NO, addresses, has_doc, in_exhibit, iso, latest_notice, money, plus_years, putter, us, value
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

MAIL_TO = ["USCIS", "ATTN: I-90", "P.O. BOX 21262", "PHOENIX, AZ 85036-1262"]  # uscis.gov/i-90, "Where to File" (updated 06/16/2026)

TITLE = "I-90 (green card renewal or replacement)"
REASONS = ["Expired or expires within 6 months", "Lost, stolen or destroyed", "Issued but never received", "Mutilated",
           "Wrong because of a DHS error", "Name or other information changed", "Turned 14 (card expires after 16th birthday)",
           "Turned 14 (card expires before 16th birthday)", "Other reason"]
FREE = {"Turned 14 (card expires after 16th birthday)", "Wrong because of a DHS error"}  # no fee (Form G-1055 10/01/26)
SECTIONS = [
    ("The card", "the attorney", [
        ("i90.reason", "Part 2 · Why a new card", {"type": "choice", "options": REASONS}, True),
        ("i90.status", "Part 2, 1 · The client is a", {"type": "choice", "options": ["Permanent resident", "Commuter", "Conditional resident"]}, True),
        ("i90.card_expires", "The current card's expiration date (on the card)", DATE, False),
        ("i90.lpr_date", "Part 1, 15 · Date the client became a resident (on the card)", DATE, True),
        ("i90.class_of_admission", "Part 1, 14 · Class of admission (the category on the card, e.g. SL6, IR1)", TEXT, True),
        ("i90.name_changed", "Part 1, 4 · Has the client's name legally changed since the card was issued?",
         {"type": "choice", "options": ["Yes", "No", "Never received a card"]}, True),
        ("i90.card_family_name", "Part 1, 5 · Family name exactly as on the current card (if the name changed)", TEXT, False),
        ("i90.card_given_name", "Part 1, 5 · Given name exactly as on the current card (if the name changed)", TEXT, False),
    ]),
    ("Processing information", "the attorney", [
        ("i90.location_applied", "Part 3, 1 · Where the client applied for the immigrant visa or adjustment (e.g. the USCIS office)", TEXT, True),
        ("i90.location_issued", "Part 3, 2 · Where the visa was issued or adjustment granted", TEXT, True),
        ("i90.ever_in_proceedings", "Part 3, 4 · Ever in exclusion, deportation or removal proceedings, or ordered removed?", YES_NO, True),
        ("i90.ever_abandoned", "Part 3, 5 · Ever filed Form I-407 or been judged to have abandoned residence?", YES_NO, True),
        ("i90.accommodation", "Part 4 · Requesting an accommodation for a disability?", YES_NO, True),
    ]),
]


def derive(graph, today: date):
    put = putter(graph, "card_renewal.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "i90")  # the mailing address, and the home address when different
    approval = latest_notice(graph, "I-485", "approval")
    approval = approval if approval and approval["date"] else None
    put("i90.lpr_date", approval["date"] if approval else v("n400.lpr_date") or v("petitioner.lpr_date"),
        "the I-485 approval" if approval else "the green card in the folder")
    put("i90.class_of_admission", v("petitioner.lpr_class"), "the green card in the folder (check it is the client's)")
    put("i90.status", "Permanent resident", "a resident (not conditional) unless the attorney says otherwise")
    put("i90.ever_in_proceedings", "Yes" if v("applicant.nta_present") else None, "a Notice to Appear in the folder")
    put("i90.accommodation", "No", "no accommodation requested (the default: change it if needed)")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    expires = iso(v("i90.card_expires"))
    if expires and (expires - today).days <= 183:
        put("i90.reason", "Expired or expires within 6 months", f"the card expires {us(expires)}")
    return graph


def fee(graph, today: date) -> int | None:
    import fees

    return 0 if value(graph, "i90.reason") in FREE else fees.load(today)["paper"].get("i90")


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    amount = fee(graph, today)
    out.append({"level": "info", "title": "Fee", "text": f"{money(amount)} (paper)" + (": no fee for this reason." if amount == 0 else ".")})
    expires = iso(v("i90.card_expires"))
    if expires:
        days = (expires - today).days
        out.append({"level": "warn" if days < 0 else "info", "title": "The card",
                    "text": f"Expires {us(expires)}" + (". Already expired: file now (the I-90 receipt notice extends the card's validity, as stated on the notice)." if days < 0 else
                                                        f" ({days} days). An I-90 can be filed in the 6 months before." if days > 183 else
                                                        ". Inside the 6 months: file now.")})
    out.append({"level": "info", "title": "Where it is filed", "text": " / ".join(MAIL_TO) + ", whatever the state (or online)."})
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, reason = fee(graph, today), value(graph, "i90.reason")
    return {"mail_to": MAIL_TO, "no_payment": amount == 0,
            "fees": (f"No filing fee is due for this application (reason: {str(reason).lower()}; Form G-1055, edition 10/01/26)." if amount == 0 else
                     f"Enclosed is the filing fee of {money(amount)} for Form I-90, per Form G-1055, edition 10/01/26.")}


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v("i90.status") == "Conditional resident" and str(v("i90.reason") or "").startswith(("Expired", "Turned 14")):
        out.append(held(OFFICE, "A conditional resident whose 2-year card is ending files Form I-751 (removing conditions), not the I-90."))
    if v("i90.reason") == "Name or other information changed" and not (has_doc(client_dir, "marriage_certificate", "divorce_decree")
                                                                         or in_exhibit(client_dir, "i90", "name_change")):
        out.append("A name change needs its proof (marriage certificate, divorce decree or court order): put it in the \"Name change\" exhibit.")
    if v("i90.name_changed") == "Yes" and not (v("i90.card_family_name") and v("i90.card_given_name")):
        out.append("Part 1, 5: the name exactly as printed on the current card.")
    if not has_doc(client_dir, "green_card") and v("i90.reason") not in ("Lost, stolen or destroyed", "Issued but never received"):
        out.append("A copy of the current green card (front and back) goes with the I-90, not in the folder.")
    dob = iso(v("applicant.dob"))
    if dob and str(v("i90.reason") or "").startswith("Turned 14"):
        fourteen = plus_years(dob, 14)
        if (today - fourteen).days > 30:
            out.append(held(ATTORNEY, f"The client turned 14 on {us(fourteen)}: the new card must be applied for within 30 days of the 14th birthday. The attorney reviews."))
    return out
