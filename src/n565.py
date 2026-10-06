"""Form N-565, Application for Replacement Naturalization/Citizenship Document (edition 02/27/25), read on 10/02/2026 from
uscis.gov/n-565 (last updated 06/01/2026), the N-565 Instructions (02/27/25) and Form G-1055 (10/01/26):

  Who: a person issued a Certificate of Naturalization, a Certificate of Citizenship, a Declaration of Intention or a Repatriation
    Certificate whose document was lost, stolen, destroyed or mutilated; is wrong because of a USCIS typographical or clerical error;
    needs a new name (marriage, divorce, annulment or a court order); shows a sex that is not the sex at birth; or, for a Certificate
    of Citizenship only, needs a new date of birth by a court order or a U.S. government document. Also a naturalized citizen who
    needs a SPECIAL certificate of naturalization so a foreign country recognizes them as a citizen ("Who May File Form N-565?").
    USCIS cannot change a name or date of birth on a Certificate of Naturalization when the client gave the wrong one on the N-400
    and swore to it at the interview; it changes a name only if it changed after naturalizing (the same page).
  Evidence ("Initial Evidence"): a copy of a U.S. government photo ID; the document for a name, sex or date of birth change or an
    error; a lost, stolen or destroyed document needs a copy (if available) and a police report and/or a sworn statement; a
    mutilated one is sent in; two passport-style photos only if the applicant lives OUTSIDE the U.S.
  Where: by mail to USCIS Phoenix Lockbox, Attn: N-565, P.O. Box 20050, Phoenix, AZ 85036-0050 (couriers: Attn: N-565 (Box 20050),
    2108 E. Elliot Rd., Tempe, AZ 85284-1806); or online, then the original goes to the Nebraska Service Center (uscis.gov/n-565).
  Fee (Form G-1055, 10/01/26): Paper $555, online $505; $0 when the certificate is wrong because of a USCIS error; a fee waiver
    (Form I-912) is possible.
  The form's own numbering is followed here: Part 4 is the USCIS-error part, Part 5 a name change, Part 6 a date of birth change and
  Part 7 the sex at birth (the Instructions' specific-instructions paragraph numbers them one off).

Every client-facing sentence here is DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, LINES, TEXT, YES_NO, addresses, has_doc, latest_notice, money, putter, value
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

TITLE = "Replacement certificate (N-565)"
MAIL_TO = ["USCIS", "Attn: N-565", "P.O. Box 20050", "Phoenix, AZ 85036-0050"]
COURIER = ["USCIS", "Attn: N-565 (Box 20050)", "2108 E. Elliot Rd.", "Tempe, AZ 85284-1806"]
SOURCE = "uscis.gov/n-565, updated 06/01/2026"
NATURALIZATION, CITIZENSHIP, REPATRIATION, DECLARATION, SPECIAL = (
    "Certificate of Naturalization", "Certificate of Citizenship", "Certificate of Repatriation", "Declaration of Intention",
    "Special certificate of naturalization (so a foreign country recognizes the client as a citizen)")
DOCUMENTS = [NATURALIZATION, CITIZENSHIP, REPATRIATION, DECLARATION, SPECIAL]
LOST, MUTILATED, ERROR, NAME, BIRTHDATE, SEX, OTHER = (
    "Lost, stolen or destroyed", "Mutilated", "Wrong because of a USCIS typographical or clerical error", "The client's name changed",
    "The client's date of birth legally changed (Certificate of Citizenship only)", "The sex on it is not the client's sex at birth",
    "Another reason")
REASONS = [LOST, MUTILATED, ERROR, NAME, BIRTHDATE, SEX, OTHER]
MARRIAGE, COURT = "Marriage, divorce or annulment", "A court order"
COURT_DOC, GOVT_DOC = "A court order", "A document issued by the U.S. Government or a U.S. state"
ERROR_ITEMS = ["Name", "Date of birth", "Sex", "Other"]
MARITAL = ["Single", "Married", "Widowed", "Divorced", "Marriage Annulled"]


def _special(g) -> bool:
    return value(g, "n565.document") == SPECIAL


def _reason(g) -> Any:
    return value(g, "n565.reason")


MORE_QUESTIONS = "Choose the document and the reason first: the questions for that reason (what happened, the name, the date of birth) appear then."


def more_to_come(graph) -> bool:
    """Only until a reason is chosen: then the questions for it are shown and the others do not apply."""
    return not _reason(graph)
SECTIONS = [
    ("The document", "the attorney", [
        ("n565.document", "Part 3, 1 · Which document does the client need", {"type": "choice", "options": DOCUMENTS}, True),
        ("n565.cert_family_name", "Part 1, 1 · Family name exactly as printed on the certificate or declaration", TEXT, True),
        ("n565.cert_given_name", "Part 1, 1 · Given name exactly as printed", TEXT, True),
        ("n565.cert_middle_name", "Part 1, 1 · Middle name exactly as printed", TEXT, False),
        ("n565.cert_dob", "Part 1, 2 · Date of birth on the certificate or declaration", DATE, True),
        ("n565.cert_country_of_birth", "Part 1, 3 · Country of birth on it", TEXT, True),
        ("n565.former_citizenship", "Part 1, 4 · Country of former citizenship or nationality", TEXT, True),
        ("n565.certificate_number", "Part 1, 5 · Certificate or declaration number (on the document, usually in red)", TEXT, False),
        ("n565.issued_by", "Part 1, 7 · Who issued it: the USCIS office or the name of the court", TEXT, True),
        ("n565.issued_on", "Part 1, 7 · Date it was issued", DATE, True),
    ]),
    ("The client now", "the attorney", [
        ("n565.marital_status", "Part 2, 4 · Current marital status", {"type": "choice", "options": MARITAL}, True),
        ("n565.lost_citizenship", "Part 2, 5 · Since becoming a U.S. citizen, has the client lost or renounced U.S. citizenship in any manner?", YES_NO, True),
        ("n565.lives_abroad", "Does the client live outside the U.S.? (two passport photos go with the application, and the new document is "
                              "sent to a U.S. embassy, consulate or USCIS office)", YES_NO, True),
    ]),
    ("The reason", "the attorney", [
        ("n565.reason", "Part 3, 2 · Why the client needs a new document", {"type": "choice", "options": REASONS}, True),
    ], lambda g: not _special(g)),
    ("Lost, stolen or destroyed", "the attorney", [
        ("n565.lost_explanation", "Part 3, 2.a(1) · When, where and how it happened (and any attempt to get it back)", LINES, True),
    ], lambda g: _reason(g) == LOST),
    ("Wrong because of a USCIS error", "the attorney", [
        ("n565.error_what", "Part 4, 1 · What is wrong on it", {"type": "choice", "options": ERROR_ITEMS}, True),
        ("n565.error_explanation", "Part 4, 2 · What is incorrect, in the client's words", LINES, True),
    ], lambda g: _reason(g) == ERROR),
    ("The name changed", "the attorney", [
        ("n565.name_change_how", "Part 5, 1 · How the name changed", {"type": "choice", "options": [MARRIAGE, COURT]}, True),
        ("n565.name_change_date", "Part 5, 1 · Date of the marriage, divorce or annulment, or of the court order", DATE, True),
    ], lambda g: _reason(g) == NAME),
    ("The date of birth changed", "the attorney", [
        ("n565.dob_change_how", "Part 6, 1 · How the date of birth changed", {"type": "choice", "options": [COURT_DOC, GOVT_DOC]}, True),
        ("n565.dob_change_date", "Part 6, 1 · Date of the court order or of the document", DATE, True),
        ("n565.new_dob", "Part 6, 2 · The new date of birth (as shown in the order or the document)", DATE, True),
    ], lambda g: _reason(g) == BIRTHDATE),
    ("The sex at birth", "the attorney", [
        ("n565.sex_at_birth", "Part 7, 1 · The client's sex at birth", {"type": "choice", "options": ["M", "F"]}, True),
    ], lambda g: _reason(g) == SEX),
    ("Another reason", "the attorney", [
        ("n565.other_explanation", "Part 3, 2.g(1) · The reason, explained", LINES, True),
    ], lambda g: _reason(g) == OTHER),
    ("Special certificate for a foreign country", "the attorney", [
        ("n565.foreign_country", "Part 8, 1 · The foreign country that asked for it", TEXT, True),
        ("n565.official_family_name", "Part 8, 2 · The foreign official: family name (if known)", TEXT, False),
        ("n565.official_given_name", "Part 8, 2 · The foreign official: given name", TEXT, False),
        ("n565.official_title", "Part 8, 2 · Official title", TEXT, False),
        ("n565.official_agency", "Part 8, 2 · Name of government agency", TEXT, False),
    ], _special),
]


def derive(graph, today: date):
    put = putter(graph, "n565.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    addresses(graph, put, "n565")
    put("n565.mailing_country", "USA" if v("n565.mailing_street") else None, "a U.S. address")
    for mine, theirs in (("cert_family_name", "family_name"), ("cert_given_name", "given_name"), ("cert_middle_name", "middle_name"),
                         ("cert_dob", "dob"), ("cert_country_of_birth", "country_of_birth"), ("former_citizenship", "citizenship")):
        put(f"n565.{mine}", v(f"applicant.{theirs}"), "the client in the case: change it to exactly what the certificate says")
    if latest_notice(graph, "N-400", "approval"):
        put("n565.document", NATURALIZATION, "the N-400 approval in the case")
    elif latest_notice(graph, "N-600", "approval"):
        put("n565.document", CITIZENSHIP, "the N-600 approval in the case")
    status = v("applicant.marital_status")
    put("n565.marital_status", status if status in MARITAL else None, "the client's marital status in the case")
    put("n565.lives_abroad", "No" if v("applicant.physical_state") or v("applicant.mailing_state") else None, "the client's U.S. address")
    how, when = v("n565.name_change_how"), v("n565.name_change_date")  # Part 5: the date goes in the box of the way it changed
    put("n565.name_date_marriage", when if how == MARRIAGE else None, "Part 5, 1.a")
    put("n565.name_date_court", when if how == COURT else None, "Part 5, 1.b")
    how, when = v("n565.dob_change_how"), v("n565.dob_change_date")  # Part 6
    put("n565.dob_date_court", when if how == COURT_DOC else None, "Part 6, 1.a")
    put("n565.dob_date_govt", when if how == GOVT_DOC else None, "Part 6, 1.b")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    """(the paper fee, why): Form G-1055 (10/01/26): $555; $0 for a certificate wrong because of USCIS's error."""
    import fees

    paper = fees.load(today).get("paper") or {}
    if _reason(graph) == ERROR:
        return paper.get("n565_uscis_error"), "no fee: the certificate is wrong because of a USCIS error (Form G-1055)"
    return paper.get("n565"), "Form N-565, paper filing (Form G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    amount, why = fee(graph, today)
    import fees

    online = 0 if _reason(graph) == ERROR else (fees.load(today).get("online") or {}).get("n565")  # the schedule makes a USCIS error $0 however it is filed
    out = [{"level": "info" if amount is not None else "warn", "title": "Fee",
            "text": f"{money(amount)}: {why}. " + ("Filed online it is also $0 (Form G-1055)." if _reason(graph) == ERROR else f"Filed online it is {money(online)} (Form G-1055).")
                    + " A client who cannot pay can ask for a fee waiver with Form I-912 "
                    "(this system doesn't build one for the N-565)."},
           {"level": "info", "title": "Where it is filed", "text": " / ".join(MAIL_TO) + f" (couriers: {' / '.join(COURIER)}; {SOURCE}). Online, the original "
            "document is then mailed to the Nebraska Service Center, at the address the online application gives."}]
    if v("n565.document") == NATURALIZATION and _reason(graph) == ERROR:
        out.append({"level": "warn", "title": "Naturalization certificates",
                    "text": "USCIS cannot change a wrong name or date of birth on a Certificate of Naturalization when the client gave it on the N-400 and swore to it "
                            "at the interview; it only changes a name that changed after naturalizing (N-565 Instructions). The attorney confirms it was USCIS's error."})
    if _reason(graph) in (MUTILATED, ERROR, NAME, BIRTHDATE, SEX):
        out.append({"level": "info", "title": "The original document",
                    "text": "Send the original certificate with the application, not a copy, and keep a copy for the file (N-565 Instructions, Initial Evidence)."})
    if v("n565.lost_citizenship") == "Yes":
        out.append({"level": "warn", "title": "Lost or renounced citizenship",
                    "text": "Part 2, 5 is Yes: explain it in Part 12 (the form says so). The attorney reviews before filing."})
    if _special(graph):
        out.append({"level": "info", "title": "Special certificate",
                    "text": "Only for a naturalized citizen whose foreign country needs proof of citizenship for a legitimate purpose, not to enter that country or for its own "
                            "immigration benefits (8 CFR 343b.2, as the Instructions quote it). A copy of the naturalization certificate goes with it. Part 8, 4 is "
                            "completed by USCIS or a consular official after approval."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out, reason = [], _reason(graph)
    if not has_doc(client_dir, "passport", "us_passport", "drivers_license", "national_id"):
        out.append("A copy of the client's U.S. government-issued photo ID (passport, driver's license or state ID): not in the folder.")
    if reason == LOST and not has_doc(client_dir, "police_report", "declaration"):
        out.append("A lost, stolen or destroyed certificate needs a police report and/or a sworn statement of what happened and any attempt to get it back: neither is in the folder.")
    if reason == LOST and not has_doc(client_dir, "citizenship_certificate"):
        out.append("A copy of the lost certificate, if the client has one (a photo or an old copy): none in the folder. If there is none, say so in the sworn statement.")
    if reason == NAME and not has_doc(client_dir, "marriage_certificate", "divorce_decree", "court_disposition"):
        out.append("Evidence of the name change (the marriage certificate, divorce or annulment decree, or the court order): not in the folder.")
    if reason == SEX and not has_doc(client_dir, "birth_certificate", "us_birth_certificate"):
        out.append("The client's birth certificate showing the sex at birth (issued at or closest to the birth): not in the folder.")
    if reason == BIRTHDATE:
        if v("n565.document") != CITIZENSHIP:
            out.append(held(ATTORNEY, "A new date of birth can be asked only for a new Certificate of Citizenship (N-565 Instructions, Initial Evidence 9)."))
        if not has_doc(client_dir, "court_disposition", "birth_certificate", "us_birth_certificate"):
            out.append("The court order or the U.S. government or state document that changed the date of birth (a copy of the original or a certified copy): not in the folder.")
    if v("n565.lives_abroad") == "Yes":
        out.append("The client lives outside the U.S.: two identical color passport-style photos go with the application (name and A-Number in pencil on the back). "
                   "The new document is sent to the nearest U.S. embassy, consulate or USCIS office.")
    if v("n565.marital_status") == "Married" and reason == NAME and v("n565.name_change_how") == MARRIAGE and not has_doc(client_dir, "marriage_certificate"):
        out.append("A name change by marriage: the marriage certificate is not in the folder.")
    amount, why = fee(graph, today)
    if amount is None:
        out.append(held(OFFICE, f"The fee can't be set yet: {why}."))
    return out


def letter(graph, today: date) -> dict[str, Any]:
    amount, why = fee(graph, today)
    document = value(graph, "n565.document") or "Replacement document"
    text = ("No filing fee is due for this application: " + why.removeprefix("no fee: ").replace("the certificate", "the applicant's certificate") + "." if amount == 0 else
            f"Enclosed is the filing fee of {money(amount)} for Form N-565, paid by the enclosed Form G-1450, per Form G-1055, edition 10/01/26." if amount else
            "Filing fee: [the attorney sets it. See the packet's problems].")
    return {"re_lines": ["Application: N-565 Application for Replacement Naturalization/Citizenship Document", f"Document: {document}"],
            "mail_to": MAIL_TO, "fees": text, "no_payment": amount == 0}
