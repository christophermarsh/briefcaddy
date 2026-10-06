"""Form G-1145, e-Notification of Application/Petition Acceptance: USCIS
emails (and texts) the receipt number within 24 hours of accepting a filing,
instead of the firm waiting days for the mailed receipt notice.

From the form itself (edition 09/26/14, the copy in schemas/forms/g1145/template.pdf):
"This service is available for applications filed at a USCIS Lockbox
facility", "clip this form to the first page of your application package",
and "You will receive one e-mail and/or text message for each form you are
filing." So: one per package, on top, for filings mailed to a lockbox.

Who receives it is the firm's choice (the Settings page, "e-Notification"):
the case's office (the email USCIS writes to, so the firm sees every receipt
first), the client (their email and mobile from the case), or nobody (no
G-1145). The list of lockbox filings below is DRAFT for the attorney.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

# Filings mailed to a USCIS lockbox (their address comes from a lockbox chart or a lockbox P.O. box).
# Not: anything filed with the immigration court or the BIA, the AR-11, the expedite request, the consulate, the U visa
# certification request (it goes to the certifying agency).
LOCKBOX_FILINGS = {"i485", "i360", "family", "n400", "i90", "i131", "i751", "ead", "i290b", "n336", "i601a", "n600", "asylee", "i589"}
# The Cuban Adjustment Act (and HRIFA dependents): USCIS's "Direct Filing Addresses for Form I-485" (updated 12/01/2025) sends
# CAA to the family-based lockbox chart and HRIFA dependents to the non-family lockbox chart (src/cuban_adjustment.py).
LOCKBOX_FILINGS |= {"caa"}
# The VAWA self-petition goes to an "Attn: 1367" lockbox (uscis.gov/Certain-VAWA-T-U-Filing-Locations, updated 02/05/2026): its
# receipt e-mail always goes to the office, never to the client's own e-mail or phone, which the abuser may see (8 U.S.C. 1367).
LOCKBOX_FILINGS |= {"vawa"}
# The T visa: uscis.gov/i-914 (updated 06/05/2026) sends the I-914 to the Elgin or Phoenix lockbox by state (src/t_visa.py).
LOCKBOX_FILINGS |= {"i914"}
# u_visa: uscis.gov/i-918 "Where to File" (updated 06/05/2026) names the Elgin, Dallas, Phoenix and Chicago lockboxes (Attn: 1367).
LOCKBOX_FILINGS |= {"u_visa"}
# DACA renewals: USCIS "Direct Filing Addresses for Form I-821D" (updated 09/24/2024) sends every state's I-821D to a USCIS lockbox
# (Phoenix, Dallas or Chicago, "Attn: DACA"); uscis.gov/i-821d's Special Instructions name the G-1145 for it (src/daca.py).
LOCKBOX_FILINGS |= {"daca"}
# The I-730: uscis.gov/i-730 "Where to File" (updated 09/09/2026): USCIS, Attn: I-730, P.O. Box 20018, Phoenix, AZ 85036-0018, couriers to
# "Attn: I-730 (Box 20018), 2108 E. Elliot Rd., Tempe" -- the Phoenix lockbox's courier address (the I-90's, uscis.gov/i-90; uscis.gov/lockbox
# names the Phoenix lockbox). An inference, for the attorney to confirm (docs/decisions.md).
LOCKBOX_FILINGS |= {"i730"}
# The I-601 and the I-212: their pages' Special Instructions name the G-1145 for a lockbox filing (uscis.gov/i-601 and /i-212, updated
# 06/01/2026). Only some of their addresses are lockboxes (a field office, the court, the I-485's package are not): the case's schema
# says so ("lockbox": False, src/inadmissibility_waiver.py and src/reapply.py case_schema).
LOCKBOX_FILINGS |= {"i601", "i212"}
# The N-565: uscis.gov/n-565 "Where to File" (updated 06/01/2026) sends a mailed N-565 to the USCIS Phoenix Lockbox (Attn: N-565, P.O. Box 20050),
# and its Special Instructions name the G-1145 for a form "accepted at a USCIS lockbox" (src/n565.py).
LOCKBOX_FILINGS |= {"n565"}
# TPS: each country's own TPS page names a USCIS lockbox (Chicago, Dallas, Elgin or Phoenix), and uscis.gov/i-821's Special Instructions name the
# G-1145 for a form "accepted at a USCIS lockbox" (src/tps.py).
LOCKBOX_FILINGS |= {"tps"}
# Humanitarian parole: uscis.gov/i-131-addresses (updated 08/11/2026), Part 1, Item 7, sends the I-131 to the USCIS Dallas Lockbox (Attn: HP), and the
# humanitarian parole page names the G-1145 for it (src/parole.py).
LOCKBOX_FILINGS |= {"parole"}
TITLE = "Form G-1145, e-Notification of Application/Petition Acceptance"


def wanted(schema: dict[str, Any]) -> bool:
    import settings

    who = settings.overlay("enotice", {"recipient": "office"}).get("recipient")
    filing = schema.get("filing", "i485")
    in_court = schema.get("variant") == "in_court"  # an I-589 in court is filed with the immigration court, not a lockbox
    return who in ("office", "client") and filing in LOCKBOX_FILINGS and not in_court and schema.get("lockbox", True)


def _contact(graph, client_dir: Path, filing: str | None = None) -> dict[str, str | None]:
    import offices
    import settings

    def v(key: str):
        f = graph.get(key)
        return f.value if f is not None and f.status == "resolved" and f.value not in (None, "") else None

    # the I-601 or I-212 of a VAWA, T or U case (8 U.S.C. 1367), by the case or by where the client stands (src/inadmissibility_waiver.py
    # confidential: the same test the fee uses): to the office too, as the self-petition's
    import filing_questions

    mod = filing_questions.module(filing or "") if filing in ("i601", "i212") else None
    vawa_waiver = mod is not None and mod.confidential(graph)
    if settings.overlay("enotice", {"recipient": "office"}).get("recipient") == "client" and filing != "vawa" and not vawa_waiver:
        mobile = "".join(ch for ch in str(v("applicant.mobile_phone") or "") if ch.isdigit()) or None
        return {"email": v("applicant.email"), "mobile": mobile}
    state = v("applicant.physical_state")
    office = offices.for_case(client_dir, state)["values"]
    return {"email": office.get("firm.email"), "mobile": None}  # an office line isn't a mobile: email only


def render(client_dir: Path, graph, schema: dict[str, Any]) -> dict[str, Any] | None:
    """Fills g1145_filled.pdf for this packet; returns its page entry (like a payment's), or None when the firm doesn't use it here."""
    if not wanted(schema):
        return None
    from factgraph import FactGraph
    from fill.companion import fill_companions, load_profile

    def v(key: str):
        f = graph.get(key)
        return f.value if f is not None and f.status == "resolved" and f.value not in (None, "") else None

    g = FactGraph("g1145")
    contact = _contact(graph, client_dir, schema.get("filing"))
    for key, val in {"enotice.family_name": v("applicant.family_name"), "enotice.given_name": v("applicant.given_name"),
                     "enotice.middle_name": v("applicant.middle_name"), "enotice.email": contact["email"],
                     "enotice.mobile": contact["mobile"]}.items():
        if val not in (None, ""):
            g.add_source(key, "enotice", "derived", str(val), str(val), 1.0)
    profile = load_profile()
    fill_companions(g, client_dir, {**profile, "forms": {"g1145": profile["forms"]["g1145"]}})
    return {"id": "g1145", "form_id": None, "file": profile["forms"]["g1145"]["output"], "short": "G-1145", "title": TITLE,
            "email": contact["email"]}
