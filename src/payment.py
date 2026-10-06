"""How a mailed filing is paid -- USCIS's rules since Oct. 28, 2025 (Executive
Order 14247; uscis.gov/g-1450, g-1650, g-1651 and "Pay With a Credit Card by
Mail", saved in data/reference/):

  - No checks, money orders or cashier's checks. A card (Form G-1450) or a
    debit from a U.S. bank account (Form G-1650); paper payment only with a
    signed Form G-1651 exemption.
  - One payment for each benefit request: one G-1450 for the I-130, another
    for the I-485 -- a combined payment can get the whole package rejected.
    A Pub. L. 119-21 fee is always its own payment (Form G-1055).
  - The G-1450 goes ON TOP of what it pays for. USCIS rejects the whole
    package without the card holder's first and last name, the card number,
    the expiry, or the exact amount.

This builds one G-1450 per payment, pre-filled with the applicant's name and
the exact amount (and the card holder's name and billing address when the
firm's setting says the client pays with their own card -- schemas/firm/payment.json).
The card number, expiry, CVV and signature are never filled: written by hand.
EOIR fees are paid in the EOIR Payment Portal, never with a G-1450.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any
import schema_path

SETTINGS = schema_path.path("firm", "payment")


def settings() -> dict[str, Any]:
    import settings as firm_settings

    return firm_settings.overlay("payment", json.loads(SETTINGS.read_text(encoding="utf-8")) if SETTINGS.exists() else {})


def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def payments(schema: dict[str, Any], client_dir: Path, graph, today: date) -> list[dict[str, Any]]:
    """[{form_id, form, amount, what, petitioner}] -- each payment this packet makes, in the packet's order. Empty when nothing is paid."""
    import fees
    import filing_questions

    paper, extra = (fees.load(today).get("paper") or {}), (fees.load(today).get("pl_119_21") or {})
    filing, forms = schema.get("filing", "i485"), schema.get("forms", [])
    out: list[dict[str, Any]] = []

    def pay(form_id: str, form: str, amount: Any, what: str, petitioner: bool = False) -> None:
        if isinstance(amount, (int, float)) and amount > 0:
            out.append({"form_id": form_id, "form": form, "amount": int(amount), "what": what, "petitioner": petitioner})

    if filing == "i485":  # the SIJ I-485 packet: no fee for the I-485 or the I-765 (G-1055)
        return out
    if filing == "family":
        if "i130" in forms:
            pay("i130", "I-130", paper.get("i130"), "Form I-130 filing fee", petitioner=True)
        if "i485" in forms:
            pay("i485", "I-485", paper.get("i485"), "Form I-485 filing fee")
        if "i485supa" in forms:  # INA 245(i)'s sum, besides the I-485 fee (G-1055; 8 CFR 245.10(c)): its own payment
            import family

            pay("i485supa", "I-485 Supplement A", family.supa_fee(graph, today)[0], "Form I-485 Supplement A sum (INA 245(i))")
        if "i765" in forms:
            pay("i765", "I-765", paper.get("i765_with_pending_i485_paid"), "Form I-765 filing fee")
    elif filing == "i360":
        pay("i360", "I-360", extra.get("i360_sij"), "Pub. L. 119-21 fee (Special Immigrant Juvenile I-360): its own payment")
    elif filing == "n400":
        reduced = _value(graph, "n400.fee_reduction") == "Yes"
        pay("n400", "N-400", paper.get("n400_reduced" if reduced else "n400"), "Form N-400 filing fee" + (" (reduced)" if reduced else ""))
    elif filing == "i589":
        if _value(graph, "asylum.ms_l") != "Yes" and schema.get("variant") != "in_court":  # in court: the EOIR Payment Portal
            pay("i589", "I-589", extra.get("i589_asylum"), "Pub. L. 119-21 asylum fee: its own payment")
    else:
        mod = filing_questions.module(filing)
        if mod is not None and hasattr(mod, "payments"):  # a filing with more than one payment (src/cuban_adjustment.py: the I-485 and the I-765)
            for form_id, form, amount, what in mod.payments(graph, today, forms):
                pay(form_id, form, amount, what)
        elif mod is not None and hasattr(mod, "fee") and filing not in ("bia", "cancellation", "court_bond", "court_motion"):  # EOIR: its portal
            got = mod.fee(graph, today)
            form = {"i90": "I-90", "i131": "I-131", "n600": "N-600", "i751": "I-751", "ead": "I-765", "i290b": "I-290B", "n336": "N-336",
                    "i601a": "I-601A", "asylee": "I-485", "i730": "I-730", "n565": "N-565"}.get(filing, filing.upper())
            form_id = next((f for f in forms if not f.startswith("g28")), filing)
            if filing == "ead":  # (filing fee, Pub. L. 119-21 fee, why)
                pay(form_id, form, got[0], "Form I-765 filing fee")
                pay(form_id, form, got[1], "Pub. L. 119-21 asylum work permit fee: its own payment")
            else:
                pay(form_id, form, got if isinstance(got, (int, float)) else got[0], f"Form {form} filing fee")
    if schema.get("fee_waiver"):  # Form I-912 inside (src/fee_waiver.py): only the Pub. L. 119-21 fees, which can't be waived, are paid
        out = [p for p in out if "Pub. L." in p["what"]]
    return out


def _payer_facts(graph, p: dict[str, Any]) -> dict[str, Any]:
    v = lambda k: _value(graph, k)  # noqa: E731
    who = "petitioner" if p["petitioner"] else "applicant"
    facts = {"payment.given_name": v(f"{who}.given_name"), "payment.middle_name": v(f"{who}.middle_name"),
             "payment.family_name": v(f"{who}.family_name"), "payment.amount": f"{p['amount']:,}"}
    if settings().get("card_holder") == "client":  # the firm's setting: the client pays with their own card
        facts |= {"payment.holder_given_name": v(f"{who}.given_name"), "payment.holder_middle_name": v(f"{who}.middle_name"),
                  "payment.holder_family_name": v(f"{who}.family_name")}
        if who == "applicant":
            facts |= {f"payment.holder_{k}": v(f"applicant.physical_{k}") for k in ("street", "apt", "city", "state", "zip")}
            facts |= {"payment.holder_phone": v("applicant.daytime_phone"), "payment.holder_email": v("applicant.email")}
    return facts


def render(client_dir: Path, graph, pays: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Fills one G-1450 per payment (g1450_<n>_filled.pdf); returns the payments with their file and the page's title."""
    from factgraph import FactGraph
    from fill.companion import fill_companions, load_profile

    profile = load_profile()
    base = profile["forms"]["g1450"]
    out = []
    for n, p in enumerate(pays, start=1):
        g = FactGraph(f"payment-{n}")
        for key, val in _payer_facts(graph, p).items():
            if val not in (None, ""):
                g.add_source(key, "payment", "derived", str(val), val, 1.0)
        form = dict(base, output=f"g1450_{n}_filled.pdf")
        fill_companions(g, client_dir, {**profile, "forms": {"g1450": form}})
        out.append(p | {"file": form["output"], "id": f"g1450_{n}", "short": f"G-1450 ({p['form']}, ${p['amount']:,})",
                        "title": f"Form G-1450, Authorization for Credit Card Transactions: {p['what']}, ${p['amount']:,}"})
    return out


CHECK_TEXT = ("Pay by the enclosed Form G-1450 (a U.S.-issued card) or Form G-1650 (a debit from a U.S. bank account). Since 10/28/2025, USCIS "
              "takes no checks or money orders for a mailed filing without a signed Form G-1651 exemption.")


def checklist(pays: list[dict[str, Any]]) -> list[dict[str, str]]:
    return [{"kind": "sign", "text": f"{p['short']}: the card holder's first and last name, the card number, the expiry (mm/yyyy) and the signature, by "
                                     "hand: USCIS rejects the whole package without them. On top of the form it pays for."} for p in pays]
