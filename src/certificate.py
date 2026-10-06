"""Form N-600, Certificate of Citizenship -- for a client who became a citizen
automatically through a parent, most often under INA 320 (the Child
Citizenship Act): on or after February 27, 2001, a child became a citizen on
the first day all of these were true at once, before turning 18 --

  - at least one parent is a U.S. citizen (by birth or naturalization);
  - the child is a lawful permanent resident;
  - the child lives in the U.S. in the citizen parent's legal and physical custody.

So a resident child whose parent naturalizes (the firm's N-400 clients'
children) is usually already a citizen: the N-600 asks USCIS to say so on
paper. This module works out that date from the case when it can, and says
which condition is missing or late when it can't. A child born abroad to a
citizen parent (INA 301/309) depends on the parent's years of U.S. physical
presence (Part 6) -- the attorney decides those.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import (DATE, TEXT, YES_NO, addresses, has_doc, in_exhibit, iso, latest_notice, lockbox, money, plus_years, putter, state_of, us,
                              value)
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "N-600 (certificate of citizenship)"
CCA = date(2001, 2, 27)  # the Child Citizenship Act took effect
INA320, BORN_ABROAD = "INA 320: became a citizen after birth, as a resident child of a citizen parent", "INA 301/309: born abroad to a U.S. citizen parent"
SECTIONS = [
    ("Eligibility", "the attorney", [
        ("n600.basis", "How the client became a citizen", {"type": "choice", "options": [INA320, BORN_ABROAD]}, True),
        ("n600.filer", "Part 1, 1 · Who is applying", {"type": "choice", "options": ["The client (adult child)", "A U.S. citizen parent or guardian"]}, True),
        ("n600.relationship", "Part 1, 2 · The client is the citizen parent's",
         {"type": "choice", "options": ["Biological child", "Adopted child", "Child of a gestational parent", "Child through assisted reproduction", "Other"]}, True),
        ("n600.parent_is", "Part 3 · The U.S. citizen parent is the client's", {"type": "choice", "options": ["Mother", "Father"]}, True),
        ("n600.parent_citizen_how", "Part 3, 6 · The parent is a citizen by",
         {"type": "choice", "options": ["Birth in the United States", "Acquisition after birth", "Birth abroad to U.S. citizen parents", "Naturalization"]}, True),
        ("n600.parent_naturalization_date", "Part 3, 6 · Date the parent naturalized (on the certificate)", DATE, False),
        ("n600.parent_certificate_number", "Part 3, 6 · The parent's naturalization or citizenship certificate number", TEXT, False),
        ("n600.parent_naturalization_place", "Part 3, 6 · Where the parent naturalized (USCIS office or court)", TEXT, False),
        ("n600.parent_lost_citizenship", "Part 3, 7 · Has the parent ever lost U.S. citizenship?", YES_NO, True),
        ("n600.custody_before_18", "Part 2 · Before 18, did the client live in the U.S. in the citizen parent's legal and physical custody?", YES_NO, True),
        ("n600.custody_from", "Date that custody began, if after the parent became a citizen and the green card (e.g. a custody order)", DATE, False),
        ("n600.parents_married_at_birth", "Part 2 · Were the client's parents married to each other when the client was born?", YES_NO, True),
        ("n600.parents_married_after", "Part 2 · If not, did they marry each other later?", YES_NO, False),
        ("n600.adopted", "Part 2 · Was the client adopted?", YES_NO, True),
    ]),
    ("The client", "the attorney", [
        ("n600.ever_lpr", "Part 2 · Is (or was) the client a permanent resident?", YES_NO, True),
        ("n600.lpr_date", "Part 2 · Date the client became a resident", DATE, False),
        ("n600.lpr_office", "Part 2 · USCIS office or port where residence was granted", TEXT, False),
        ("n600.armed_forces", "Part 2 · Is the client a member or veteran of the U.S. armed forces? (no fee when applying for themself)", YES_NO, True),
        ("n600.lost_lpr", "Part 2 · Has the client's resident status ever been lost or taken away?", YES_NO, True),
        ("n600.applied_before", "Part 2 · Ever applied for a certificate of citizenship before?", YES_NO, True),
        ("n600.applied_passport", "Part 2 · Ever applied for a U.S. passport?", YES_NO, True),
        ("n600.absent_since_arrival", "Part 2 · Been outside the U.S. since first arriving?", YES_NO, True),
        ("n600.other_is_citizen", "Part 4 · Is the other parent a U.S. citizen?", YES_NO, True),
        ("n600.parent_spouse_is_other_parent", "Part 3, 9 · Is the citizen parent's current spouse the client's other parent?", YES_NO, False),
        ("n600.parent_served", "Part 7 · Has either parent served in the U.S. armed forces?", YES_NO, False),
    ]),
]


def _parent(graph, which: str, prefix: str, put) -> None:
    """The client's mother or father (from the birth certificate) as the form's citizen parent or other parent."""
    side = {"Mother": "mother", "Father": "father"}.get(which)
    if not side:
        return
    for part in ("family_name", "given_name", "middle_name", "dob", "country_of_birth"):
        put(f"n600.{prefix}_{part}", value(graph, f"applicant.{side}_{part}"), f"the client's {side} (birth certificate)")


def derive(graph, today: date):
    put = putter(graph, "certificate.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "n600")
    dob = iso(v("applicant.dob"))
    if dob:
        put("n600.filer", "The client (adult child)" if plus_years(dob, 18) <= today else "A U.S. citizen parent or guardian",
            f"the client is {'18 or older' if plus_years(dob, 18) <= today else 'under 18'}")
    approval = latest_notice(graph, "I-485", "approval")
    lpr = (approval or {}).get("date") or v("n400.lpr_date") or v("petitioner.lpr_date")
    if lpr:
        put("n600.ever_lpr", "Yes", "the I-485 approval" if approval else "the green card in the folder")
        put("n600.lpr_date", lpr, "the I-485 approval" if approval else "the green card in the folder")
    if lpr or v("n600.lpr_date") or v("n600.ever_lpr") == "Yes":
        put("n600.basis", INA320, "a resident child of a citizen parent (the usual case): the attorney confirms")
    parent = v("n600.parent_is")
    _parent(graph, parent, "parent", put)
    _parent(graph, {"Mother": "Father", "Father": "Mother"}.get(parent or ""), "other", put)
    put("n600.other_is", {"Mother": "Father", "Father": "Mother"}.get(parent or ""), "the other parent")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def citizen_since(graph) -> tuple[date | None, list[str]]:
    """INA 320: the day the last condition was met (None, with what is missing or too late)."""
    v = lambda k: value(graph, k)  # noqa: E731
    dob, why = iso(v("applicant.dob")), []
    if not dob:
        return None, ["the client's date of birth"]
    eighteen = plus_years(dob, 18)
    if eighteen <= CCA:
        return None, [f"the client turned 18 on {us(eighteen)}, before the Child Citizenship Act took effect (02/27/2001): earlier law (former INA 321) applies; the attorney decides"]
    how = v("n600.parent_citizen_how")
    parent_citizen = iso(v("n600.parent_naturalization_date")) if how == "Naturalization" else (dob if how else None)
    if how == "Naturalization" and not parent_citizen:
        why.append("the date the parent naturalized")
    elif not how:
        why.append("how the parent is a citizen")
    lpr = iso(v("n600.lpr_date"))
    if v("n600.ever_lpr") == "No":
        return None, ["INA 320 needs the client to be a permanent resident: the client never was"]
    if not lpr:
        why.append("the date the client became a resident")
    if v("n600.custody_before_18") == "No":
        return None, ["INA 320 needs the client to have lived in the U.S. in the citizen parent's legal and physical custody before 18"]
    if v("n600.custody_before_18") is None:
        why.append("whether the client lived in the citizen parent's custody before 18")
    if v("n600.parent_lost_citizenship") == "Yes":
        why.append("the parent lost citizenship at some point: the attorney decides")
    if why:
        return None, why
    custody = iso(v("n600.custody_from"))
    day = max(d for d in (parent_citizen, lpr, CCA, custody) if d)
    if day >= eighteen:
        late = "the parent naturalized" if day == parent_citizen else "the client became a resident" if day == lpr else "custody began"
        return None, [f"the last condition was met on {us(day)} ({late}), on or after the client's 18th birthday ({us(eighteen)}): no automatic citizenship under INA 320"]
    return day, []


def fee(graph, today: date) -> tuple[int | None, str]:
    import fees

    if value(graph, "n600.armed_forces") == "Yes" and value(graph, "n600.filer") == "The client (adult child)":
        return 0, "no fee: a current or former member of the U.S. armed forces applying for themself (Form G-1055)"
    if value(graph, "n600.adopted") == "Yes":
        return None, "an adopted child may have no fee in certain cases (Form G-1055): the attorney sets it"
    return fees.load(today)["paper"].get("n600"), "Form N-600 (paper)"


def address(graph) -> tuple[list[str] | None, str]:
    state = state_of(graph)
    lines, box = lockbox("uscis_lockboxes_n600", state)
    return lines, (f"the N-600 chart, by state ({box})" if box else f"the client's state ({state or 'unknown'}) is not on the N-600 chart")


def notes(graph, today: date) -> list[dict[str, str]]:
    out = []
    if value(graph, "n600.basis") != BORN_ABROAD:
        since, why = citizen_since(graph)
        out.append({"level": "info", "title": "Citizen since", "text": f"{us(since)}: the day the last INA 320 condition was met (the attorney confirms)."}
                   if since else {"level": "warn", "title": "INA 320", "text": "Can't tell yet: " + "; ".join(why) + "."})
    amount, why = fee(graph, today)
    out.append({"level": "info" if amount is not None else "warn", "title": "Fee", "text": f"{money(amount)}: {why}." if amount is not None else why + "."})
    lines, why = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({why})") if lines else why})
    if value(graph, "applicant.public_charge_exemption") == "SIJS" or value(graph, "applicant.i360_receipt_number"):
        out.append({"level": "warn", "title": "Special Immigrant Juvenile",
                    "text": "The client became a resident as an SIJ, on a court finding that reunification with one or both parents is not viable: the attorney reviews a claim through a parent (custody in particular) before filing."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if v("n600.basis") != BORN_ABROAD:
        since, why = citizen_since(graph)
        if not since:
            out.append("INA 320 eligibility not shown yet: " + "; ".join(why) + ".")
    elif not v("n600.presence1_from"):
        out.append("Born abroad to a citizen parent: Part 6 needs the parent's periods of U.S. physical presence before the client's birth.")
    if not has_doc(client_dir, "birth_certificate"):
        out.append("The client's birth certificate (with a certified English translation), not in the folder.")
    if v("n600.basis") == INA320 and not has_doc(client_dir, "green_card"):
        out.append("A copy of the client's green card, not in the folder.")
    if v("n600.parents_married_at_birth") == "Yes" and not has_doc(client_dir, "marriage_certificate"):
        out.append("The parents' marriage certificate, not in the folder.")
    if v("n600.parents_married_at_birth") == "No" and v("n600.parent_is") == "Father":
        out.append(held(ATTORNEY, "Born to unmarried parents, through the father: legitimation (or the father's acknowledgment) must be shown. The attorney reviews."))
    if has_doc(client_dir, "divorce_decree") and not in_exhibit(client_dir, "n600", "custody"):
        out.append(held(OFFICE, "The parents divorced: show the citizen parent's legal custody (the decree's custody terms or a custody order). Put it in the \"Custody\" exhibit."))
    if v("n600.adopted") == "Yes":
        out.append(held(ATTORNEY, "Adopted: the final adoption decree, and proof of 2 years' legal and physical custody (adopted before 16). The attorney reviews."))
    if v("n600.parent_naturalization_date") is None and v("n600.parent_citizen_how") == "Naturalization":
        out.append("Part 3, 6: the date and certificate number of the parent's naturalization.")
    amount, why = fee(graph, today)
    if amount is None:
        out.append(held(OFFICE, f"The fee can't be set yet: {why}."))
    if not address(graph)[0]:
        out.append(held(OFFICE, f"The filing address can't be set yet: {address(graph)[1]}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, why = fee(graph, today)
    lines, _ = address(graph)
    return {"mail_to": lines or ["[USCIS address: see the packet's problems]"], "no_payment": amount == 0,
            "fees": ("No filing fee is due for this application: " + why.removeprefix("no fee: ").replace("the client", "the applicant") + "." if amount == 0 else
                     f"Enclosed is the filing fee of {money(amount)} for Form N-600, per Form G-1055, edition 10/01/26." if amount else
                     "Filing fee: [the attorney sets it. See the packet's problems].")}
