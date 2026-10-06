"""Form EOIR-28: the attorney's appearance in immigration court, for a client
in removal proceedings (a Notice to Appear in the folder, or a hearing on the
client's timeline). Hearings themselves are entered by hand on the timeline
(src/journey.py): the court's case status system has no public interface. A
hearing notice scanned into the notice inbox (src/inbox.py) starts the record
instead, marked as read from the notice until a person confirms it.

In a case on the EOIR Courts & Appeals System (ECAS) the attorney files the
EOIR-28 in the EOIR portal; on paper it goes to the court, served on ICE's
Office of the Principal Legal Advisor (OPLA) for that court -- the proof of
service on page 2.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

from filing_questions import TEXT, YES_NO, has_doc, putter, value
from holders import CLIENT, OFFICE, held, producer

TITLE = "EOIR-28 (appearance in immigration court)"
SECTIONS = [
    ("The court case", "the attorney", [
        ("eoir.appearance_for", "Entry of appearance for", {"type": "choice", "options": ["All proceedings", "Custody and bond proceedings only",
                                                                                         "All proceedings other than custody and bond"]}, True),
        ("eoir.electronic_service", "An ECAS (electronic) case: served on ICE electronically?", YES_NO, False),
        ("eoir.dhs_address", "Page 2 · ICE (OPLA) office served, its address", TEXT, True),
        ("eoir.primary", "Primary attorney on the case?", {"type": "choice", "options": ["Primary", "Non-primary"]}, True),
        ("eoir.pro_bono", "Pro bono representation?", YES_NO, True),
        ("applicant.arriving_alien", "Was the client placed in proceedings as an arriving alien (at a port of entry)? (then USCIS, not the judge, decides an I-485)",
         YES_NO, False),
    ]),
]


def nta_hearing(graph) -> dict | None:
    """The first hearing the Notice to Appear itself sets ("on November 18, 2026 at 8:30 AM" at the court's address), or None when the notice
    says "to be set" or prints no date. Read from the paper and never confirmed: src/journey.py shows it as such and asks a person to check it
    against the court, the way a hearing read from a court's notice is (src/inbox.py). {date, time, place, source}."""
    day = value(graph, "nta.hearing_date")
    if not day:
        return None
    fact = graph.get("nta.hearing_date")
    return {"date": day, "time": value(graph, "nta.hearing_time"), "place": value(graph, "nta.hearing_place"),
            "source": fact.sources[0].doc_id if fact is not None and fact.sources else None}


def derive(graph, today: date):
    put = putter(graph, "court.derive")
    v = lambda k: value(graph, k)  # noqa: E731
    # the represented party's own address (where the client lives), not the firm's
    for part, key in (("street", "physical_street"), ("apt", "physical_apt"), ("city", "physical_city"), ("state", "physical_state"), ("zip", "physical_zip")):
        put(f"eoir.{part}", v(f"applicant.{key}"), "the client's home address")
    put("eoir.appearance_for", "All proceedings", "the firm's usual appearance (change it for a bond-only case)")
    put("eoir.primary", "Primary", "the firm is the client's main counsel (change it when appearing for another attorney)")
    put("eoir.pro_bono", "No", "a paid engagement (change it for a pro bono case)")
    middle = str(v("applicant.middle_name") or "").strip()
    put("eoir.middle_initial", middle[:1] or None, "the client's middle name")
    put("companion.preparer_full_name", " ".join(x for x in (v("firm.preparer_given_name"), v("firm.preparer_family_name")) if x) or None, "the firm")
    return graph


MOVED = "if the client has moved, the court and DHS still have the old address. Check it before the first hearing."


def address_note(graph) -> dict[str, str] | None:
    """The address the Notice to Appear prints for the client, against the home address in this case. Nothing when they are the
    same (the client's street number and ZIP code both in the address as DHS prints it). "It differs" only when the scan read the
    address cleanly (extract.notice_to_appear.read_address: a ZIP code, a state, every token a word or a number); a line the scan
    read badly is never shown as the client's address (the real notice's typewritten line read as "..., MASSACHUSETIS,
    ULTSe ei 0974)" on 10/04/2026): the note shows what did read as words and asks a person to compare the notice itself."""
    from extract.notice_to_appear import read_address

    held = value(graph, "nta.respondent_address")
    if not held:
        return None
    read = read_address(str(held))
    street, zip_code = str(value(graph, "applicant.physical_street") or "").split(), str(value(graph, "applicant.physical_zip") or "")[:5]
    if not (street and zip_code):
        return None
    same = street[0].upper() in read["text"].upper().split() and read["zip"] == zip_code
    if same:
        return None
    if read["readable"]:
        return {"level": "info", "title": "The address DHS has",
                "text": f"The Notice to Appear lists the client's address as {read['text']}. It differs from the home address in this case: {MOVED}"}
    seen = f" What did read: {read['text']}." if read["text"] else ""
    return {"level": "info", "title": "The address DHS has",
            "text": f"The Notice to Appear lists an address for the client, but this scan does not read it clearly.{seen} Open the notice and compare "
                    f"its address with the home address in this case: {MOVED}"}


def notes(graph, today: date) -> list[dict[str, str]]:
    ecas = value(graph, "eoir.electronic_service") == "Yes"
    address = address_note(graph)
    return ([address] if address else []) + [{"level": "info", "title": "How it is filed",
             "text": "An ECAS case: file it in the EOIR portal (eportal.eoir.justice.gov) with these answers. Service on ICE is electronic." if ecas else
                     "On paper: file it with the court and serve a copy on ICE (OPLA); complete the proof of service on page 2."},
            {"level": "info", "title": "Hearings", "text": "Enter each hearing on the client's timeline (Where the case stands → Immigration court): the client sees it in the portal. "
                                                           "Record each result there: a decision puts the BIA appeal deadline (10 days, 30 for some asylum decisions; "
                                                           "received by the Board) and the motion deadlines on the timeline."},
            {"level": "info", "title": "EOIR fees and e-filing", "text": "Since 02/23/2026 EOIR takes fees only through the EOIR Payment Portal: no checks or money orders. "
                                                                         "Attorneys must e-file through ECAS in every case eligible for it (BIA Practice Manual 2.1(a)(6))."}]


@producer(OFFICE)
def problems(client_dir: Path, graph, today: date) -> list[str]:
    v = lambda k: value(graph, k)  # noqa: E731
    out = []
    if not v("firm.eoir_id"):
        out.append("The attorney's EOIR ID isn't set: add it on the Settings page (The firm and the attorney).")
    if not v("firm.licensing_authority"):
        out.append("The attorney's bar admission isn't set: add it on the Settings page (The firm and the attorney).")
    if not v("applicant.a_number"):
        out.append(held(CLIENT, "The client's A-Number (on the Notice to Appear): the court files the case by it."))
    if not has_doc(client_dir, "notice_to_appear"):
        out.append(held(CLIENT, "No Notice to Appear in the folder: confirm the client is in proceedings (EOIR case status: 1-800-898-7180) before filing an appearance."))
    return out
