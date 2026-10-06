"""Form I-765 on its own: a work permit (EAD) that isn't part of an I-485
packet -- the first one or a renewal, for the firm's categories:

  (c)(9)  while the client's I-485 is pending (a renewal, or one filed later);
  (c)(14) deferred action granted to a Special Immigrant Juvenile;
  (c)(8)  while the client's asylum application (I-589) is pending -- not
          before it has been pending 150 days: USCIS rejects an early one;
  (a)(5)  an asylee (asylum granted).

The case decides the category, the reason (a work permit already in the folder
makes it a renewal), the fee (Form G-1055 10/01/26, Appendix C: SIJ and an
asylee's first card free; (c)(8) carries the Pub. L. 119-21 fee) and the
address (USCIS's I-765 page: (c)(8) at the Dallas lockbox, an asylee by the
non-family chart, deferred action by the family chart, (c)(9) by the I-485's
basis and receipt prefix).
"""

from __future__ import annotations

from datetime import date, timedelta
from pathlib import Path
from typing import Any

from filing_questions import (DATE, YES_NO, addresses, has_doc, iso, latest_notice, lockbox, money, pending, putter, sij, state_of, us,
                              value)
from holders import CLIENT, OFFICE, held, producer

TITLE = "I-765 (work permit)"
C9, C14, C8, A5 = ("(c)(9) Pending green card application (I-485)", "(c)(14) Deferred action (Special Immigrant Juvenile)",
                   "(c)(8) Pending asylum application (I-589)", "(a)(5) Asylee (asylum granted)")
CATEGORIES = [C9, C14, C8, A5]
BOXES = {C9: ("c", "9", ""), C14: ("c", "14", ""), C8: ("c", "8", ""), A5: ("a", "5", "")}
STATUS = {C9: "Adjustment of status applicant", C14: "Deferred action (Special Immigrant Juvenile)", C8: "Asylum applicant", A5: "Asylee"}
C8_DALLAS = ["USCIS", "ATTN: I-765 C08", "P.O. BOX 650888", "DALLAS, TX 75265-0888"]  # uscis.gov/i-765-addresses (updated 03/03/2026)
MORE_QUESTIONS = "Choose the category first: its own questions appear then."


def _cat(graph) -> Any:
    return value(graph, "ead.category")


SECTIONS = [
    ("The work permit", "the attorney", [
        ("ead.category", "Part 2, 27 · Eligibility category", {"type": "choice", "options": CATEGORIES}, True),
        ("ead.reason", "Part 1 · Initial, replacement or renewal", {"type": "choice", "options": ["Initial", "Replacement", "Renewal"]}, True),
        ("ead.card_expires", "The current work permit's expiration date (a renewal)", DATE, False),
        ("ead.previous_filed", "Part 2, 12 · Has the client filed an I-765 before?", YES_NO, True),
        ("ead.uscis_error", "A replacement because USCIS printed the card wrong, or it never arrived (no fee)?", YES_NO, False),
    ]),
    ("Asylum applicant (c)(8)", "the attorney", [
        ("ead.c8_arrested", "Part 2, 30 · Ever arrested for or convicted of any crime?", YES_NO, True),
    ], lambda g: _cat(g) == C8),
    ("Pending green card application (c)(9)", "the attorney", [
        ("ead.i485_fee_paid", "Was the I-485 filed with its fee (not waived)?", YES_NO, False),
        ("ead.sij_chart", "SIJ, filed on its own: which lockbox chart (the I-765 page lists family-based and employment-based I-485s; SIJ is EB-4)?",
         {"type": "choice", "options": ["Family-based chart", "Non-family chart"]}, False),
    ], lambda g: _cat(g) == C9),
]


def derive(graph, today: date):
    put = putter(graph, "work_permit.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "ead")
    put("ead.mailing_same", "No" if v("applicant.mailing_same_as_physical") == "No" else "Yes", "the client's addresses")
    i485, i589 = pending(graph, "I-485"), pending(graph, "I-589")
    granted = latest_notice(graph, "I-589", "approval")
    if i485:
        put("ead.category", C9, f"the I-485 is pending (receipt {i485['receipt']})")
    elif granted and not latest_notice(graph, "I-485", "approval"):
        put("ead.category", A5, f"asylum was granted {us(iso(granted['date']))}")
    elif i589:
        put("ead.category", C8, f"the I-589 is pending (receipt {i589['receipt']})")
    elif sij(graph) and latest_notice(graph, "I-360", "approval"):
        put("ead.category", C14, "the I-360 (SIJ) is approved: deferred action, if USCIS granted it on the approval notice")
    has_card = bool(v("applicant.ead_expiration_date"))
    put("ead.reason", "Renewal" if has_card else None, "a work permit is already in the case")
    put("ead.previous_filed", "Yes" if has_card else None, "a work permit is already in the case")
    put("ead.card_expires", v("applicant.ead_expiration_date"), "the work permit in the folder")
    cat = v("ead.category")
    if cat in BOXES:
        for n, box in enumerate(BOXES[cat], start=1):
            if box:
                put(f"ead.category_{n}", box, "the category")
        put("ead.current_status", STATUS[cat], "the category")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def fee(graph, today: date) -> tuple[int | None, int | None, str]:
    """(the USCIS filing fee, the separate Pub. L. 119-21 fee or None, why) -- G-1055 10/01/26, Appendix C."""
    import fees

    data = fees.load(today)
    paper, pl = data["paper"], data["pl_119_21"]
    cat, reason = value(graph, "ead.category"), value(graph, "ead.reason")
    if reason == "Replacement" and value(graph, "ead.uscis_error") == "Yes":
        return paper.get("i765_replacement_uscis_error", 0), None, "no fee: a replacement because of a USCIS (or USPS) error"
    if cat in (C14,) or (cat == C9 and sij(graph)):
        return paper.get("i765_sij", 0), None, "no fee: a Special Immigrant Juvenile"
    if cat == C8:
        if reason == "Renewal":
            return paper.get("i765_c8_renewal"), pl.get("i765_asylum_renewal"), "a (c)(8) renewal"
        return paper.get("i765_c8_initial", 0), pl.get("i765_asylum_initial"), "a first (c)(8) work permit"
    if cat == A5:
        return (paper.get("i765_a5_initial", 0), None, "no fee: an asylee's first work permit") if reason != "Renewal" else \
               (paper.get("i765_a5_renewal"), None, "an asylee's renewal (not on G-1055's no-fee list)")
    if cat == C9:
        filed = iso((latest_notice(graph, "I-485", "receipt") or {}).get("date"))
        paid = value(graph, "ead.i485_fee_paid") != "No"
        if filed and paid and date(2007, 7, 30) <= filed < date(2024, 4, 1):
            return paper.get("i765_i485_paid_before_2024_04_01", 0), None, f"no fee: the I-485 was filed on {us(filed)} with its fee and is pending"
        if filed and paid and filed >= date(2024, 4, 1):
            return paper.get("i765_with_pending_i485_paid"), None, f"the I-485 was filed on {us(filed)} with its fee and is pending"
        return paper.get("i765"), None, "the general fee"
    return None, None, "choose the category first"


def address(graph) -> tuple[list[str] | None, str]:
    cat, state = value(graph, "ead.category"), state_of(graph)
    if not cat:
        return None, "choose the category first"
    if cat == C8:
        return list(C8_DALLAS), "(c)(8): the USCIS Dallas lockbox, whatever the state"
    if cat == A5:
        lines, box = lockbox("uscis_lockboxes_nfb", state)
        return lines, f"an asylee: the non-family lockbox chart ({box or 'state not on the chart'})"
    if cat == C14:
        lines, box = lockbox("uscis_lockboxes", state)
        return lines, f"deferred action: the family-based lockbox chart ({box or 'state not on the chart'})"
    if sij(graph):  # (c)(9) for an SIJ: the attorney picks the chart (the page lists family- and employment-based I-485s)
        chart = value(graph, "ead.sij_chart")
        if not chart:
            return None, "(c)(9) for an SIJ filed on its own: choose the lockbox chart below (SIJ is EB-4; the I-765 page lists family-based and employment-based I-485s)"
        name = "uscis_lockboxes" if chart == "Family-based chart" else "uscis_lockboxes_nfb"
    else:
        receipt = str((pending(graph, "I-485") or {}).get("receipt") or "").upper()
        name = "uscis_lockboxes" if receipt[:3] in ("MSC", "IOE") else "uscis_lockboxes_nfb"
    lines, box = lockbox(name, state)
    return lines, f"(c)(9): the {'family-based' if 'nfb' not in name else 'non-family'} lockbox chart ({box or 'state not on the chart'})"


def c8_opens(graph) -> date | None:
    """The first day a (c)(8) work permit can be filed: 150 days after the I-589 was filed (USCIS rejects an earlier one)."""
    receipt = latest_notice(graph, "I-589", "receipt")
    filed = iso((receipt or {}).get("date"))
    return filed + timedelta(days=150) if filed else None


def notes(graph, today: date) -> list[dict[str, str]]:
    cat = value(graph, "ead.category")
    amount, extra, why = fee(graph, today)
    out = []
    if amount is None:
        out.append({"level": "warn", "title": "Fee", "text": why + "."})
    else:
        out.append({"level": "info", "title": "Fee", "text": f"{money(amount)} (paper): {why}"
                    + (f"; and the Pub. L. 119-21 fee of {money(extra)} as its own payment (not waivable)." if extra else ".")})
    lines, where = address(graph)
    out.append({"level": "info" if lines else "warn", "title": "Where it is filed", "text": (" / ".join(lines) + f" ({where})") if lines else where})
    if cat == C8:
        opens = c8_opens(graph)
        out.append({"level": "warn" if opens and today < opens else "info", "title": "The 150 days",
                    "text": (f"The I-589 has been pending since {us(opens - timedelta(days=150))}: the work permit can be filed from {us(opens)}"
                             + (f" ({(opens - today).days} days to go)." if today < opens else ".")) if opens else
                    "The I-589's receipt notice isn't in the folder: the work permit can be filed only once the I-589 has been pending 150 days."})
    expires = iso(value(graph, "ead.card_expires"))
    if value(graph, "ead.reason") == "Renewal" and expires:
        opens = expires - timedelta(days=180)
        out.append({"level": "warn" if today >= opens else "info", "title": "The renewal",
                    "text": f"The card expires {us(expires)}; a renewal can be filed from {us(opens)} (180 days before)."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    cat, reason, out = v("ead.category"), v("ead.reason"), []
    if cat == C8:
        opens = c8_opens(graph)
        if opens and today < opens:
            out.append(held(OFFICE, f"Too early: a (c)(8) work permit can be filed only from {us(opens)}, 150 days after the I-589 was filed: USCIS rejects an early one."))
        if not opens:
            out.append("(c)(8): a copy of the I-589's receipt notice (or the court's proof it was filed) goes with the I-765, not in the folder.")
        if v("ead.c8_arrested") == "Yes" and not has_doc(client_dir, "criminal_record"):
            out.append("(c)(8), arrested or convicted: certified court dispositions for each, not in the folder.")
    if cat == C9 and not pending(graph, "I-485"):
        out.append("(c)(9) needs a pending I-485: no I-485 receipt notice in the folder (or the I-485 was already decided).")
    if cat == A5 and not latest_notice(graph, "I-589", "approval"):
        out.append("(a)(5): the asylum approval (or the judge's order granting asylum) goes with the I-765, not in the folder.")
    if cat == C14 and not latest_notice(graph, "I-360", "approval"):
        out.append("(c)(14): the notice granting deferred action goes with the I-765, not in the folder.")
    if reason == "Renewal":
        if not has_doc(client_dir, "work_permit"):
            out.append("A renewal: a copy of the current work permit (front and back), not in the folder.")
        expires = iso(v("ead.card_expires"))
        if expires and today < expires - timedelta(days=180):
            out.append(held(OFFICE, f"Too early for a renewal: it can be filed from {us(expires - timedelta(days=180))} (180 days before the card expires)."))
    if not has_doc(client_dir, "passport", "i94", "work_permit", "drivers_license"):
        out.append("A copy of the client's I-94, passport or other government ID: none in the folder.")
    amount, _extra, why = fee(graph, today)
    if amount is None:
        out.append(held(OFFICE, f"The fee can't be set yet: {why}."))
    lines, where = address(graph)
    if lines is None:
        out.append(held(OFFICE, f"The filing address can't be set yet: {where}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    cat = value(graph, "ead.category") or "work permit"
    amount, extra, why = fee(graph, today)
    lines, _ = address(graph)
    if amount is None:
        text = "Filing fee: [the attorney sets it. See the packet's problems]."
    elif amount == 0 and not extra:
        text = "No filing fee is due for this application: " + why.removeprefix("no fee: ").replace("the client", "the applicant") + "."
    elif amount == 0:
        text = (f"No filing fee is due for Form I-765 ({why}); enclosed, as its own payment, is the Pub. L. 119-21 fee of {money(extra)}, "
                "per Form G-1055, edition 10/01/26.")
    else:
        text = (f"Enclosed is the filing fee of {money(amount)} for Form I-765" + (f", and, as its own payment, the Pub. L. 119-21 fee of {money(extra)}" if extra else "")
                + ", per Form G-1055, edition 10/01/26.")
    return {"re_lines": [f"Application: I-765 Application for Employment Authorization, {cat.split(' ')[0]}"],
            "mail_to": lines or ["[USCIS address: see the packet's problems]"], "fees": text, "no_payment": amount == 0 and not extra}
