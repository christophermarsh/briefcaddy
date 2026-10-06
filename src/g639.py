"""Form G-639, Freedom of Information/Privacy Act Request (edition 12/12/24), for the client's USCIS file (the A-file): the takeover
checklist's "request the file" step as a built request. Read on 10/02/2026 from USCIS's page "Request Records through the Freedom of
Information Act or Privacy Act" (uscis.gov/records/request-records-through-the-freedom-of-information-act-or-privacy-act, last updated
01/27/2026, where uscis.gov/g-639 now redirects), the form itself and Form G-1055 (10/01/26):

  How it is filed: "Effective January 22, 2026, all Freedom of Information Act (FOIA)/Privacy requests for USCIS records should be
    submitted online ... at first.uscis.gov" and "Online submission is generally the only acceptable method for submitting a request."
    The page links no PDF and gives no mailing address for the paper form (the form's own first page lists the same three options as
    before: online, this form, or a letter): so this packet carries no mailing address and no cover letter. Its pages hold every answer
    the online request asks for, and the client's signed consent.
  Fee: none with the request. G-1055 (10/01/26), page 4: "USCIS will notify you if a fee must be submitted after we review your
    request." The form (Part 3): do not send payment; the request is an agreement to pay up to $25; the first 100 copies and two hours
    of search are free and search and copying must exceed $14.00 before anything is charged; no search or processing fees for Privacy Act
    requests.
  A third party: the firm requests another person's record (Part 1, item 1.B) as an attorney (Part 4, item 3.A) and must show the
    subject's consent: a declaration under penalty of perjury (Option 1) or a notarized affidavit (Option 2) (the form's Part 4).
  Faster: request specific documents rather than the whole A-file; one request for each person; an A-file for a client with a scheduled
    immigration court hearing is prioritized if a Notice to Appear (or another listed notice) showing the date is included (the page).
  Not USCIS: CBP holds records of apprehension, detention, deportation, entry, exit and inspection and Form I-94 records; the State
    Department visa records; ICE bond, student and detention medical records (the page names where to ask for each).

Every client-facing sentence here is DRAFT for the attorney.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path
from typing import Any

from filing_questions import DATE, TEXT, YES_NO, addresses, has_doc, putter, value
from holders import CLIENT, OFFICE, held, producer

TITLE = "FOIA request for the client's USCIS file (G-639)"
PORTAL = "first.uscis.gov"
SOURCE = "USCIS's FOIA page, updated 01/27/2026"
ALL, SPECIFIC = "The complete A-file", "Only specific documents (choose them below)"
TYPE = "Another person's immigration record (the firm requests it for the client)"
RELATIONSHIP, CONSENT = "Attorney or accredited representative", "Declaration under penalty of perjury"
# (key, the form's box, what the form says): Part 1, item 2's commonly requested records; the four with a date are DATED
DOCS = [("apprehensions", "A", "Apprehensions"), ("birth_certificate", "B", "Birth certificate"), ("i94", "C", "Form I-94"), ("passport", "D", "Passport"),
        ("arrival_docs", "E", "Other arrival or departure documents into the U.S."), ("i129", "F", "I-129, Petition for a Nonimmigrant Worker"),
        ("i90", "G", "I-90, Application to Replace Permanent Resident Card"), ("i130", "H", "I-130, Petition for Alien Relative"),
        ("i140", "I", "I-140, Immigrant Petition for Alien Workers"), ("i485", "J", "I-485, Application to Register Permanent Residence or Adjust Status"),
        ("i751", "K", "I-751, Petition to Remove Conditions on Residence"), ("n400", "L", "N-400, Application for Naturalization"),
        ("labor_cert", "M", "Labor certification issued by the U.S. Department of Labor"), ("naturalization_cert", "N", "Naturalization certificate"),
        ("lpr_proof", "O", "Proof of Lawful Permanent Resident (LPR) status"), ("removal_record", "P", "Record of removal from the U.S.")]
DATED = {"apprehensions": "Date of apprehension", "i94": "Date of entry", "arrival_docs": "Date of entry", "removal_record": "Date of removal"}


def _specific(g) -> bool:
    return value(g, "g639.scope") == SPECIFIC


def _doc_questions() -> list[tuple[str, str, dict[str, Any], bool]]:
    out = []
    for key, _letter, label in DOCS:
        out.append((f"g639.doc_{key}", f"Part 1, 2 · {label}", YES_NO, False))
        if key in DATED:
            out.append((f"g639.doc_{key}_date", f"Part 1, 2 · {label}: {DATED[key].lower()}", DATE, False))
    return out


SECTIONS = [
    ("The request (Part 1)", "the attorney", [
        ("g639.scope", "Part 1, 2 · What to ask USCIS for (specific documents are processed faster than a whole A-file)",
         {"type": "choice", "options": [ALL, SPECIFIC]}, True),
        ("g639.court_hearing", "Part 1, 5 · Does the client have a date scheduled for an immigration court hearing? (the Notice to Appear or the hearing notice goes with the request)",
         YES_NO, True),
    ]),
    ("Which documents (Part 1, 2)", "the attorney", _doc_questions(), _specific),
    ("About the client (Part 2)", "the paralegal", [
        ("g639.receipt_1", "Part 2, 4.A · A USCIS receipt number the client has filed under", TEXT, False),
        ("g639.receipt_2", "Part 2, 4.B · Another receipt number", TEXT, False),
        ("g639.receipt_3", "Part 2, 4.C · Another receipt number", TEXT, False),
        ("g639.other_family_name", "Part 2, 6.A · Another name the client has used: family name", TEXT, False),
        ("g639.other_given_name", "Part 2, 6.A · Another name: given name", TEXT, False),
        ("g639.other_middle_name", "Part 2, 6.A · Another name: middle name", TEXT, False),
        ("g639.entry_family_name", "Part 2, 7 · The name used when the client entered the U.S., if different: family name", TEXT, False),
        ("g639.entry_given_name", "Part 2, 7 · The name used on entry: given name", TEXT, False),
        ("g639.entry_middle_name", "Part 2, 7 · The name used on entry: middle name", TEXT, False),
        ("g639.mother_maiden_name", "Part 2, 10 · The client's mother's maiden name or previous last names", TEXT, False),
    ]),
    ("Fees and consent (Parts 3 and 4)", "the attorney", [
        ("g639.consent_to_pay", "Part 3 · The firm agrees to pay search and copying charges up to $25 if USCIS ever charges them (the form's box; nothing is paid with the request)",
         YES_NO, True),
        ("g639.consent", "Part 4 · How the client gives consent to release the records to the firm", {"type": "choice", "options": [CONSENT]}, True),
    ]),
]
MORE_QUESTIONS = "Choose the documents above: a list of the commonly requested ones appears when only specific documents are asked for."


def more_to_come(graph) -> bool:
    """Only until the scope is chosen."""
    return not value(graph, "g639.scope")


def derive(graph, today: date):
    import journey

    put = putter(graph, "g639.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    put("g639.type", TYPE, "the firm requests the client's own file as their attorney (Part 1, 1.B; Part 4, 3.A)")
    put("g639.relationship", RELATIONSHIP, "the firm's attorney (Part 4, 3.A)")
    put("g639.consent", CONSENT, "the usual way: the client signs a declaration under penalty of perjury (Part 4, Option 1)")
    if v("g639.scope") == ALL:
        put("g639.scope_text", "Complete A-File (all documents)", "the complete A-file (Part 1, 2: Other)")
    addresses(graph, put, "g639")
    put("g639.mailing_country", "USA" if v("g639.mailing_street") else None, "a U.S. address")
    receipts = []
    for n in sorted((n for n in journey.notices(graph) if n["receipt"]), key=lambda n: n["date"] or "", reverse=True):
        if n["receipt"] not in receipts:
            receipts.append(n["receipt"])
    for i, receipt in enumerate(receipts[:3], start=1):
        put(f"g639.receipt_{i}", receipt, "a USCIS notice in the case")
    put("firm.country", "USA", "the firm")
    return graph


def fee(graph, today: date) -> tuple[int | None, str]:
    """Nothing is paid with a FOIA request (Form G-1055 10/01/26, page 4; the form's Part 3)."""
    import fees

    return (fees.load(today).get("paper") or {}).get("g639", 0), "nothing is paid with the request: USCIS tells the requester if a fee is due after it reviews it (Form G-1055)"


def notes(graph, today: date) -> list[dict[str, str]]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = [{"level": "info", "title": "Where it is filed",
            "text": f"Online at {PORTAL}. {SOURCE}: all FOIA and Privacy Act requests for USCIS records are to be made online since 01/22/2026, and online "
                    "submission is generally the only acceptable method. The page gives no mailing address for the paper G-639, so this packet has none: it holds every "
                    "answer the online request asks for, and the client's signed consent. One request for each person."},
           {"level": "info", "title": "Fee", "text": "None with the request. USCIS notifies the requester if a fee is due after it reviews it (Form G-1055); the form's own rule: up to $25, and "
                                                      "nothing until search and copying pass $14.00 (the first 100 copies and two hours are free); no fees for a Privacy Act request."},
           {"level": "info", "title": "Not everything is at USCIS",
            "text": "USCIS's page sends requests for apprehension, detention, deportation, entry, exit and inspection records and Form I-94 records to CBP; visa records to the "
                    "Department of State; bond, student and detention medical records to ICE. This request asks USCIS only."}]
    if v("g639.court_hearing") == "Yes":
        out.append({"level": "info", "title": "A hearing date", "text": "USCIS prioritizes an A-file request when a Notice to Appear (Form I-862), an Order to Show Cause, a Notice of Referral to "
                                                                       "Immigration Judge (I-863) or a written notice of continuation showing the hearing date is included (USCIS's FOIA page). Put it in the folder."})
    if not v("applicant.a_number") and not v("g639.receipt_1"):
        out.append({"level": "warn", "title": "Finding the file", "text": "No A-Number and no receipt number in the case: USCIS finds an A-file by them. Add one if the client has any."})
    return out


@producer(CLIENT)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if _specific(graph) and not any(v(f"g639.doc_{key}") == "Yes" for key, *_ in DOCS):
        out.append(held(OFFICE, "Only specific documents are asked for, but none is chosen: tick the ones the client needs, or ask for the complete A-file."))
    if v("g639.court_hearing") == "Yes" and not has_doc(client_dir, "notice_to_appear", "eoir_hearing_notice"):
        out.append("A hearing date is scheduled: the Notice to Appear or the hearing notice showing it goes with the request, and none is in the folder.")
    if not v("applicant.a_number") and not any(v(f"g639.receipt_{i}") for i in (1, 2, 3)):
        out.append("No A-Number and no receipt number: USCIS can't find the file without one of them.")
    if not v("firm.preparer_family_name"):
        out.append(held(OFFICE, "Part 4: the attorney's name isn't set (the Settings page, The firm and the attorney): the firm is the requestor."))
    for key, label in (("applicant.dob", "the client's date of birth (Part 2, 2)"), ("applicant.country_of_birth", "the client's country of birth (Part 2, 3)")):
        if not v(key):
            out.append(f"Part 2: {label}.")
    return out
