"""The notice inbox (src/inbox.py): the day's USCIS and court mail, scanned into one folder, each notice on its case.

A made-up firm of two processed cases -- Ana Clara Exemplo Souza (an I-485 receipt notice and an I-94) and Maria
Exemplo Lima (an I-360 approval with her A-Number) -- and one day's mail: a combined scan of three notices (an RFE on
Ana's receipt, a receipt notice on Maria's new filing that only her A-Number and name connect, a stranger's), and a
separate immigration court hearing notice for Maria. Everything is invented (EXEMPLO), the receipt numbers too.
"""

from __future__ import annotations

import io
import json
from datetime import date
from pathlib import Path

import pytest
import schema_path

REPO = Path(__file__).resolve().parents[1]
TODAY = date(2026, 10, 2)
I485 = "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS"
I765 = "I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION"
I360 = "I360 - PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT"
RFE_ITEM = "Submit Form I-693, Report of Immigration Medical Examination and Vaccination Record."  # what the RFE asks: never read


def i797(receipt, case_type, notice_type, when, name, a_number=None, body=(), label="Applicant"):
    """A USCIS I-797 in the layout of the real notices (src/extract/uscis_notice.py's docstring)."""
    a = f" A{a_number[:3]} {a_number[3:6]} {a_number[6:]}" if a_number else ""
    return ["Department of Homeland Security", "U.S. Citizenship and Immigration Services", "I-797C, Notice of Action",
            "EXEMPLO: DEMONSTRATION DOCUMENT", "Receipt Number Case Type", f"{receipt} {case_type}",
            f"Received Date Priority Date {label}{a}", f"{when} {name}", "Notice Date Page", f"{when} 1 of 1", f"Notice Type: {notice_type}", *body]


ANA_RECEIPT = i797("IOE0999100001", I485, "Receipt Notice", "07/01/2026", "EXEMPLO SOUZA, ANA CLARA", body=["Receipt Notice"])
ANA_I94 = ["I-94/I-95 Official Website - Get Most Recent Response", "Most Recent I-94", "EXEMPLO: DEMONSTRATION DOCUMENT",
           "Admission (I-94) Record Number: 99900012300", "Arrival/Issued Date: 2019 July 15", "Class of Admission: B2",
           "Admit Until Date: 01/14/2020", "Last/Surname: EXEMPLO SOUZA", "First (Given) Name: ANA CLARA", "Birth Date: 03/14/2006",
           "Document Number: XX0001234", "Country of Citizenship: Brazil"]
MARIA_I360 = ["Department of Homeland Security", "U.S. Citizenship and Immigration Services", "I-797, Notice of Action",
              "EXEMPLO: DEMONSTRATION DOCUMENT", "Receipt Number Case Type", f"IOE0999100002 {I360}",
              "Received Date Priority Date Petitioner A099 900 222", "06/01/2026 06/01/2026 EXEMPLO LIMA, MARIA",
              "Notice Date Page Beneficiary", "06/01/2026 1 of 1 EXEMPLO LIMA, MARIA", "Notice Type: Approval Notice", "Class: SL6",
              "Section: Special Immigrant-Juvenile", "The above petition has been approved."]

RFE = i797("IOE0999100001", I485, "Request for Evidence", "09/30/2026", "EXEMPLO SOUZA, ANA CLARA",
           body=["REQUEST FOR EVIDENCE", "Additional evidence is required to process this case.",
                 "Please submit the evidence listed below by December 28, 2026.", RFE_ITEM])
MARIA_NEW = i797("IOE0999100003", I485, "Receipt Notice", "09/28/2026", "EXEMPLO LIMA, MARIA", a_number="099900222",
                 body=["Receipt Notice", "This notice confirms that USCIS received your application."])
STRANGER = i797("IOE0999100099", I765, "Receipt Notice", "09/29/2026", "ESTRANHO, JOAO EXEMPLO", body=["Receipt Notice"])
COURT = ["UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT",
         "1 EXAMPLE PLAZA, ROOM 100", "BOSTON, MA 02110", "EXEMPLO: DEMONSTRATION DOCUMENT", "RE: EXEMPLO LIMA, MARIA",
         "FILE: A099-900-222", "DATE: Sep 30, 2026", "NOTICE OF IN-PERSON HEARING IN REMOVAL PROCEEDINGS",
         "Your case has been scheduled for a MASTER hearing before the Immigration Judge",
         "on Nov 18, 2026 at 8:30 A.M. at:", "1 EXAMPLE PLAZA, ROOM 100", "BOSTON, MA 02110"]


def pdf(*pages: list[str]) -> bytes:
    """One PDF, one page per list of lines (a text layer, as a clean scan becomes after OCR)."""
    from portal.demo import document_pdf
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for lines in pages:
        writer.add_page(PdfReader(io.BytesIO(document_pdf(lines))).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def make_case(root: Path, case: str, files: dict[str, list[list[str]]], db: Path) -> Path:
    """A processed case: its document folder, and its review bundle as the pipeline leaves it, in the index."""
    import index
    from batch import process_client_folder
    from review.state import save_bundle

    source = root / "sources" / case
    source.mkdir(parents=True)
    for name, pages in files.items():
        (source / name).write_bytes(pdf(*pages))
    out = root / "clients" / case
    save_bundle(process_client_folder(case, source), out, source)
    index.rebuild(out, db)
    return out


def firm(root: Path, maria: bool = True) -> dict[str, Path]:
    db = root / "index.db"
    out = {"db": db, "clients": root / "clients", "inbox": root / "inbox", "sources": root / "sources",
           "ana": make_case(root, "case-ana", {"receipt.pdf": [ANA_RECEIPT], "i94.pdf": [ANA_I94]}, db)}
    if maria:
        out["maria"] = make_case(root, "case-maria", {"i360 approval.pdf": [MARIA_I360]}, db)
    out["inbox"].mkdir(parents=True, exist_ok=True)
    return out


def _records(case: Path) -> dict[str, dict]:
    return {d: r for r in json.loads((case / "documents.json").read_text(encoding="utf-8"))["documents"] for d in r["doc_ids"]}


def confirm_notice_subjects(case: Path, name_part: str = "inbox_") -> None:
    """Named subject review after routing; source review is still separate."""
    import documents
    import subject_attribution as subjects
    identity = next(p["id"] for p in documents.read(case)["case_subjects"]["people"] if p["case_role"] == "applicant")
    for row in subjects.views(case):
        if name_part not in row["file"] or row["type"] not in subjects.NOTICE_TYPES or row["current"]:
            continue
        assert row["bound"] and set(row["slots"]) <= {"case_subject", "beneficiary"}
        refs = [f["evidence_version"] for f in row["facts"] if f["role"] == "unmapped"]
        subjects.assign(case, row["instance_id"], row["fingerprint"], {slot: identity for slot in row["slots"]},
                        "Notice Subject Reviewer", "paralegal", reference_edges=refs,
                        note="Notice recipient/first-matched number remains reference; proceeding subject checked against original")


def confirm_notice_sources(case: Path) -> None:
    """Explicit fictional office checks before asserting accepted timelines."""
    import critical_review
    import subject_attribution as subjects
    from review.state import record_decision, reviewed_graph
    # Older notices can contribute the same derived proceeding fact. Review
    # their subjects too; a new notice never waives a sibling-source hold.
    confirm_notice_subjects(case, name_part="")
    graph = reviewed_graph(case)
    for key, proof in critical_review.context(case).items():
        sources = critical_review.origins(graph, key)
        if not sources or not all(s.doc_type in subjects.NOTICE_TYPES for s in sources):
            continue
        assert proof["bound"]
        item = {"id": "fact:" + key, "kind": "fact", "level": "review", "title": "Check fictional notice source", "group": "other",
                "facts": [{"key": key, "input": {"type": "text"}}], "actions": ["confirm", "set", "blank"]}
        record_decision(case, item, {"action": "confirm", "reviewer": "Notice Source Reviewer", "role": "paralegal",
                                    "evidence_fingerprints": {key: critical_review.context(case)[key]["fingerprint"]}})


@pytest.fixture(scope="module")
def day(tmp_path_factory):
    """One morning: the firm, the day's mail in the inbox, and the inbox read once -- with process_documents watched."""
    import batch
    import inbox
    from overnight import source_signature

    root = tmp_path_factory.mktemp("inbox")
    f = firm(root)
    before = {"ana": _records(f["ana"]), "ana_meta": json.loads((f["ana"] / "meta.json").read_text(encoding="utf-8")),
              "ana_files": {p.name: p.stat().st_mtime_ns for p in (f["sources"] / "case-ana").iterdir()}}
    state = root / "batch_state.json"  # the overnight run's record: Ana's folder as it was last processed
    state.write_text(json.dumps({"case-ana": {"status": "done", "signature": source_signature(f["sources"] / "case-ana")}}))
    (f["inbox"] / "mail 10-02.pdf").write_bytes(pdf(RFE, MARIA_NEW, STRANGER))
    (f["inbox"] / "court letter.pdf").write_bytes(pdf(COURT))
    calls = []
    original = batch.process_documents

    def watched(client_id, documents, *a, **kw):
        calls.append((client_id, [d for d, _ in documents]))
        return original(client_id, documents, *a, **kw)

    batch.process_documents = watched
    try:
        result = inbox.process_inbox(f["clients"], f["inbox"], db_path=f["db"], state_path=state, today=TODAY)
    finally:
        batch.process_documents = original
    return f | {"result": result, "calls": calls, "before": before, "state": state}


# -- the routing rule ---------------------------------------------------------------------------------


def test_a_combined_scan_is_one_part_per_notice():
    import inbox

    pages = [p for p in (inbox._pages_of(pdf(RFE, MARIA_NEW, STRANGER)))]
    assert [(a, b) for a, b, _ in inbox.parts(pages)] == [(0, 0), (1, 1), (2, 2)]
    # page 2 of one notice repeats its receipt number without a date: it stays with page 1
    page2 = ["Receipt Number IOE0999100001", "Page 2 of 2", "Place this cover sheet on top of your response."]
    assert [(a, b) for a, b, _ in inbox.parts(inbox._pages_of(pdf(RFE, page2, STRANGER)))] == [(0, 1), (2, 2)]


def test_what_the_reader_reads_from_a_notice_and_from_the_court():
    import inbox
    from extract import eoir_notice

    r = inbox.read("\n".join(MARIA_NEW), "i485_receipt")
    assert (r["receipt"], r["form"], r["kind"], r["date"], r["a_number"], r["names"]) == \
           ("IOE0999100003", "I-485", "receipt", "2026-09-28", "099900222", ["EXEMPLO LIMA, MARIA"])
    rfe = inbox.read("\n".join(RFE), "i485_receipt")
    assert (rfe["kind"], rfe["due"]) == ("rfe", "2026-12-28") and "I-693" not in json.dumps(rfe)  # that it exists and when it's due, nothing else
    court = eoir_notice.parse("\n".join(COURT))
    assert (court.kind, court.a_number, court.name, court.date, court.time, court.hearing_kind, court.place) == \
           ("hearing", "099900222", "EXEMPLO LIMA, MARIA", "2026-11-18", "8:30 AM", "Master calendar", "1 EXAMPLE PLAZA, ROOM 100, BOSTON, MA 02110")
    from classify import classify_text

    assert classify_text("\n".join(COURT)).doc_type == "eoir_hearing_notice"
    assert inbox.describe(inbox.read("\n".join(COURT), "eoir_hearing_notice")) == \
           "Immigration court hearing notice: master calendar hearing on 11/18/2026 at 8:30 AM"


COURT_HEAD = ["UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT", "1 EXAMPLE PLAZA, ROOM 100",
              "BOSTON, MA 02110", "EXEMPLO: DEMONSTRATION DOCUMENT", "RE: EXEMPLO LIMA, MARIA", "FILE: A099-900-222"]
DECISION = COURT_HEAD + ["DECISION AND ORDER OF THE IMMIGRATION JUDGE",
                         "The respondent failed to appear at the hearing on March 4, 2026, after written notice of the hearing.",
                         "It is HEREBY ORDERED that the respondent be removed from the United States."]
RESCHEDULED = COURT_HEAD + ["NOTICE OF IN-PERSON HEARING IN REMOVAL PROCEEDINGS", "Your hearing on 03/04/2026 has been cancelled.",
                            "Your case has been rescheduled for an INDIVIDUAL hearing on 12/15/2026 at 9:00 AM at:",
                            "1 EXAMPLE PLAZA, ROOM 100", "BOSTON, MA 02110"]
TWO_DATES = COURT_HEAD + ["NOTICE OF IN-PERSON HEARING IN REMOVAL PROCEEDINGS", "Your hearing on 03/04/2026 has been cancelled.",
                          "Please appear for the hearing on 12/15/2026 at 9:00 AM."]


def test_a_judges_decision_is_never_a_hearing_and_a_rescheduled_hearing_takes_its_new_date():
    import inbox
    from extract import eoir_notice

    decision = eoir_notice.parse("\n".join(DECISION))
    assert decision.kind == "decision" and decision.date is None  # "the hearing on March 4, 2026" is what happened, not a hearing to come
    r = inbox.read("\n".join(DECISION), "unclassified")
    assert r["kind"] == "decision" and "hearing" not in r and inbox.describe(r) == "Immigration court decision or order"
    moved = eoir_notice.parse("\n".join(RESCHEDULED))
    assert (moved.kind, moved.date, moved.time, moved.hearing_kind, moved.dates) == ("hearing", "2026-12-15", "9:00 AM", "Individual (merits)", None)
    # two hearing dates and no "scheduled for ... on": no date is taken; the letter waits for a person, with both dates
    both = inbox.read("\n".join(TWO_DATES), "eoir_hearing_notice")
    assert both["hearing"]["date"] is None and both["hearing"]["dates"] == ["2026-03-04", "2026-12-15"]
    d = FakeDirectory({"b": "Maria Exemplo Lima"}, a_numbers={"099900222": ["b"]})
    found = inbox.match(both, d)
    assert found["status"] == "ambiguous" and found["candidates"] == ["b"] and "03/04/2026, 12/15/2026" in found["why"]
    assert "03/04/2026, 12/15/2026" in inbox.describe(both)
    # a letter that only mentions a notice of hearing far below its head isn't a hearing notice
    letter = COURT_HEAD + ["Correspondence"] * 15 + ["Enclosed is a copy of the notice of hearing sent earlier."]
    assert eoir_notice.parse("\n".join(letter)).kind == "other"


def test_the_report_names_no_file():
    import inbox

    line = inbox.report_line({"routed": [], "waiting": 0, "errors": [{"file": "SOUZA ana rfe.pdf", "error": "PdfReadError"}] * 2})
    assert line == "Notice inbox: 0 notices routed, 0 waiting for a person. 2 files couldn't be read and stay in the inbox." and "SOUZA" not in line


def _legacy(tmp_path, late: bool):
    """A case processed before the overnight run kept a record (no batch_state entry), in the overnight's folder layout;
    with `late`, a PDF added after its last run and not read yet."""
    import os

    db = tmp_path / "index.db"
    clients = tmp_path / "clients"
    source = tmp_path / "folders" / "case-ana" / "source"
    source.mkdir(parents=True)
    (source / "receipt.pdf").write_bytes(pdf(ANA_RECEIPT))
    import index
    from batch import process_client_folder
    from review.state import save_bundle

    out = clients / "case-ana"
    save_bundle(process_client_folder("case-ana", source), out, source)
    index.rebuild(out, db)
    meta = out / "meta.json"
    os.utime(meta, (meta.stat().st_mtime + 100, meta.stat().st_mtime + 100))
    if late:
        (source / "lease.pdf").write_bytes(pdf(["LEASE AGREEMENT", "EXEMPLO: DEMONSTRATION DOCUMENT"]))
        os.utime(source / "lease.pdf", (meta.stat().st_mtime + 50, meta.stat().st_mtime + 50))
    (tmp_path / "inbox").mkdir()
    (tmp_path / "inbox" / "rfe.pdf").write_bytes(pdf(RFE))
    state = tmp_path / "batch_state.json"
    return clients, db, state


@pytest.mark.parametrize("late", [True, False])
def test_a_case_with_no_overnight_record_keeps_its_unread_documents_unread(tmp_path, late):
    import inbox
    import overnight

    clients, db, state = _legacy(tmp_path, late)
    meta_time = (clients / "case-ana" / "meta.json").stat().st_mtime
    r = inbox.process_inbox(clients, tmp_path / "inbox", db_path=db, state_path=state, today=TODAY)
    assert [e["case"] for e in r["routed"]] == ["case-ana"] and r["routed"][0]["processed"]
    assert (clients / "case-ana" / "meta.json").stat().st_mtime == meta_time  # meta.json keeps its time
    todo, _ = overnight.choose(tmp_path / "folders", clients, json.loads(state.read_text()) if state.exists() else {}, False, None)
    if late:  # the lease was never read: the case still runs tonight
        assert todo == [("case-ana", "new")] and not state.exists()
    else:  # up to date before the notice: recorded, so the notice alone doesn't re-run the whole folder
        assert todo == [] and json.loads(state.read_text())["case-ana"]["why"] == "processed earlier"


class FakeDirectory:
    def __init__(self, names, receipts=None, a_numbers=None):
        self._n, self._r, self._a = names, receipts or {}, a_numbers or {}

    def names(self):
        return self._n

    def by_receipt(self, r):
        return set(self._r.get(r, ()))

    def by_a_number(self, a):
        return set(self._a.get(a, ()))

    def by_name(self, names):
        import inbox

        return {c for c, n in self._n.items() if any(inbox.name_matches(x, n) for x in names)}


def test_the_routing_rule():
    import inbox

    d = FakeDirectory({"a": "Ana Clara Exemplo Souza", "b": "Maria Exemplo Lima", "c": "Maria Exemplo Lima"},
                      receipts={"R1": ["a"], "R2": ["a", "b"]}, a_numbers={"111": ["b"], "222": ["b", "c"]})
    notice = {"kind": "receipt", "receipt": None, "a_number": None, "names": []}
    assert inbox.match(notice | {"receipt": "R1"}, d) == {"status": "routed", "case": "a", "how": "receipt number"}
    two = inbox.match(notice | {"receipt": "R2"}, d)
    assert two["status"] == "ambiguous" and two["candidates"] == ["a", "b"]
    # a new receipt: the A-Number, with the name agreeing
    assert inbox.match(notice | {"receipt": "NEW", "a_number": "111", "names": ["EXEMPLO LIMA, MARIA"]}, d)["case"] == "b"
    alone = inbox.match(notice | {"a_number": "111"}, d)  # no name read: one misread digit could be another client's, so it waits
    assert alone["status"] == "ambiguous" and alone["candidates"] == ["b"] and "no name was read" in alone["why"]
    wrong = inbox.match(notice | {"a_number": "111", "names": ["EXEMPLO SOUZA, ANA CLARA"]}, d)
    assert wrong["status"] == "ambiguous" and "isn't that client's" in wrong["why"]
    shared = inbox.match(notice | {"a_number": "222", "names": ["EXEMPLO LIMA, MARIA"]}, d)
    assert shared["status"] == "ambiguous" and shared["candidates"] == ["b", "c"]
    # a name alone never routes: it is offered first in the picker
    named = inbox.match(notice | {"names": ["EXEMPLO SOUZA, ANA CLARA"]}, d)
    assert named["status"] == "ambiguous" and named["candidates"] == ["a"]
    assert inbox.match(notice | {"receipt": "R9", "names": ["ESTRANHO, JOAO"]}, d)["status"] == "unmatched"
    assert inbox.match({"kind": None, "type": "lease", "names": []}, d)["status"] == "unmatched"
    assert not inbox.name_matches("SOUZA", "Ana Clara Exemplo Souza")  # a family name alone is no match


# -- one morning's mail ---------------------------------------------------------------------------------


def test_each_notice_goes_to_its_case_and_the_stranger_waits(day):
    r = day["result"]
    routed = {(e["case"], e["read"]["receipt"] or "court"): e for e in r["routed"]}
    assert set(routed) == {("case-ana", "IOE0999100001"), ("case-maria", "IOE0999100003"), ("case-maria", "court")}
    assert routed[("case-ana", "IOE0999100001")]["how"] == "receipt number"
    assert routed[("case-maria", "IOE0999100003")]["how"] == "A-Number and name"
    assert routed[("case-maria", "court")]["how"] == "A-Number and name"
    assert all(e["processed"] for e in r["routed"]) and r["waiting"] == 1 and not r["errors"]
    # the files: one part per notice, named for where they came from; the combined scan kept, the inbox emptied
    ana_files = sorted(p.name for p in (day["sources"] / "case-ana").iterdir())
    assert "inbox_2026-10-02_mail_10-02_part1.pdf" in ana_files
    maria_files = sorted(p.name for p in (day["sources"] / "case-maria").iterdir())
    assert {"inbox_2026-10-02_mail_10-02_part2.pdf", "inbox_2026-10-02_court_letter.pdf"} <= set(maria_files)
    inbox_dir = day["inbox"]
    assert not [p for p in inbox_dir.iterdir() if p.is_file() and p.suffix == ".pdf"]
    assert [p.name for p in (inbox_dir / "originals").iterdir()] == ["2026-10-02_mail 10-02.pdf"]
    assert [p.name for p in (inbox_dir / "waiting").iterdir()] == ["inbox_2026-10-02_mail_10-02_part3.pdf"]
    import inbox

    assert inbox.report_line(r) == "Notice inbox: 3 notices routed, 1 waiting for a person."


def test_the_queue_shows_the_readers_guess(day):
    import inbox

    v = inbox.view(day["clients"], day["inbox"])
    (w,) = v["waiting"]
    assert w["status"] == "unmatched" and w["pages"] == [3] and w["of"] == 3
    assert w["what"] == "I-765 receipt notice (receipt IOE0999100099), notice of 09/29/2026"
    assert w["names"] == ["ESTRANHO, JOAO EXEMPLO"] and "No case has this receipt number" in w["why"]
    assert {x["case"] for x in v["recent"]} == {"case-ana", "case-maria"} and v["files_unread"] == 0
    picker = inbox.Directory(day["clients"], day["db"]).find("lima")
    assert picker == [{"case": "case-maria", "name": "MARIA EXEMPLO LIMA"}]
    assert inbox.Directory(day["clients"], day["db"]).find("A099-900-222")[0]["case"] == "case-maria"


def test_only_the_new_document_is_read_and_the_rest_of_the_case_is_untouched(day):
    import index

    assert all(len(docs) == 1 for _, docs in day["calls"]) and len(day["calls"]) == 3  # one call per notice, one document each
    after = _records(day["ana"])
    for doc, record in day["before"]["ana"].items():
        assert {k: after[doc][k] for k in ("id", "type", "text", "added", "identifiers")} == \
               {k: record[k] for k in ("id", "type", "text", "added", "identifiers")}
    new = after["inbox_2026-10-02_mail_10-02_part1.pdf"]
    assert new["source"] == "scan_inbox" and new["type"] == "i485_receipt" and new["identifiers"]["receipt"] == "IOE0999100001"
    meta = json.loads((day["ana"] / "meta.json").read_text(encoding="utf-8"))
    assert meta["processed_at"] == day["before"]["ana_meta"]["processed_at"]  # no full run
    assert meta["classifications"]["inbox_2026-10-02_mail_10-02_part1.pdf"] == "i485_receipt"
    for name, mtime in day["before"]["ana_files"].items():
        assert (day["sources"] / "case-ana" / name).stat().st_mtime_ns == mtime
    # the index: the new receipt number finds Maria's new notice
    hits = index.search("IOE0999100003", include_confidential=True, db_path=day["db"])["results"]
    assert [(h["case"], h["file"]) for h in hits] == [("case-maria", "inbox_2026-10-02_mail_10-02_part2.pdf")]
    # the overnight run won't read Ana's whole folder again for it
    from overnight import source_signature

    state = json.loads(day["state"].read_text())
    assert state["case-ana"]["signature"] == source_signature(day["sources"] / "case-ana")


def test_the_rfe_lands_with_its_due_date_and_nothing_it_asks_is_read(day):
    import journey
    import rfe

    before = journey.journey(day["ana"], TODAY)
    assert any(d["value"] == "2026-12-28" for p in before["pending_evidence"] for d in p["dates"])
    assert any(s.get("evidence_review") and s["urgent"] for s in before["steps"])
    assert not any(d["id"] == "IOE0999100001.rfe.2026-09-30" for d in before["deadlines"])
    assert not any(e["date"] == "2026-09-30" for e in journey.client_view(before, "pt")["happened"])
    confirm_notice_subjects(day["ana"])
    after_subject = journey.journey(day["ana"], TODAY)
    assert any(p["state"] == "source_unconfirmed" for p in after_subject["pending_evidence"])
    assert not any(d["id"] == "IOE0999100001.rfe.2026-09-30" for d in after_subject["deadlines"])
    confirm_notice_sources(day["ana"])
    j = journey.journey(day["ana"], TODAY)
    due = [d for d in j["deadlines"] if d["id"] == "IOE0999100001.rfe.2026-09-30"]
    assert due and due[0]["date"] == "2026-12-28" and due[0]["owner"] == "attorney"
    assert any(e["what"] == "I-485 request for evidence (IOE0999100001)" and e["date"] == "2026-09-30" for e in j["timeline"])
    (req,) = rfe.requests(day["ana"])
    entry = next(e for e in day["result"]["routed"] if e["case"] == "case-ana")
    assert entry["rfe_key"] == req["key"] and req["due"] == "2026-12-28"
    assert rfe.state(day["ana"], req["key"])["items"] == []  # the items are the reviewer's to type
    for kept in (day["inbox"] / "inbox_log.jsonl", day["inbox"] / "queue.json", day["ana"] / "fact_graph.json", day["ana"] / "status.json"):
        if kept.exists():
            assert "I-693" not in kept.read_text(encoding="utf-8"), kept.name


def test_the_court_notice_starts_the_hearing_record_a_person_confirms(day):
    import journey

    status = json.loads((day["maria"] / "status.json").read_text(encoding="utf-8"))
    (h,) = status["journey"]["hearings"]
    assert {k: h[k] for k in ("date", "time", "kind", "court", "source", "confirmed", "by")} == \
           {"date": "2026-11-18", "time": "8:30 AM", "kind": "Master calendar", "court": "1 EXAMPLE PLAZA, ROOM 100, BOSTON, MA 02110",
            "source": "inbox_2026-10-02_court_letter.pdf", "confirmed": None, "by": "Notice inbox"}
    assert json.loads((day["maria"] / "meta.json").read_text(encoding="utf-8"))["classifications"]["inbox_2026-10-02_court_letter.pdf"] == "eoir_hearing_notice"
    j = journey.journey(day["maria"], TODAY)
    assert any(s["id"] == h["id"] + ".confirm" and s["urgent"] and s["owner"] == "paralegal" for s in j["steps"])
    (court_date,) = [d for d in j["deadlines"] if d["id"] == h["id"]]  # the court date counts at once, said to be unconfirmed
    assert court_date["date"] == "2026-11-18" and court_date["what"].endswith("(read from the hearing notice, not confirmed yet)")
    assert next(d for d in j["deadlines"] if d["id"] == h["id"] + ".filings")["what"].endswith("not confirmed yet)")
    assert not [a for a in journey.client_view(j, "pt")["appointments"] if a["date"] == "2026-11-18"]  # not in the portal until confirmed
    journey.mark(day["maria"], "hearing_confirm", "Paula Paralegal", h["id"])
    j = journey.journey(day["maria"], TODAY)
    assert not any(s["id"] == h["id"] + ".confirm" for s in j["steps"])
    assert "not confirmed" not in next(d for d in j["deadlines"] if d["id"] == h["id"])["what"]
    assert [a["date"] for a in journey.client_view(j, "pt")["appointments"]] == ["2026-11-18"]


def test_the_clients_page_gets_the_notice_in_their_language_after_subject_and_source_review(day):
    import journey

    confirm_notice_subjects(day["ana"])
    confirm_notice_sources(day["ana"])
    j = journey.journey(day["ana"], TODAY)
    pt = {e["date"]: e["text"] for e in journey.client_view(j, "pt")["happened"]}
    en = {e["date"]: e["text"] for e in journey.client_view(j, "en")["happened"]}
    assert "2026-09-30" in pt and pt["2026-09-30"] != en["2026-09-30"] and "30/09/2026" in pt["2026-09-30"]


# -- the reviewer's side --------------------------------------------------------------------------------


def test_a_reviewer_places_a_waiting_notice_and_it_is_recorded(tmp_path):
    import inbox

    f = firm(tmp_path, maria=False)
    (f["inbox"] / "scan.pdf").write_bytes(pdf(STRANGER))
    assert inbox.process_inbox(f["clients"], f["inbox"], db_path=f["db"], today=TODAY)["waiting"] == 1
    (w,) = inbox.view(f["clients"], f["inbox"])["waiting"]
    with pytest.raises(ValueError, match="your name"):
        inbox.place(f["clients"], f["inbox"], w["id"], "case-ana", "", db_path=f["db"])
    done = inbox.place(f["clients"], f["inbox"], w["id"], "case-ana", "Paula Paralegal", db_path=f["db"])
    assert (done["event"], done["by"], done["case"], done["how"]) == ("placed", "Paula Paralegal", "case-ana", "placed by Paula Paralegal")
    assert (f["sources"] / "case-ana" / "inbox_2026-10-02_scan.pdf").exists() and not list((f["inbox"] / "waiting").iterdir())
    assert "inbox_2026-10-02_scan.pdf" in _records(f["ana"])
    v = inbox.view(f["clients"], f["inbox"])
    assert not v["waiting"] and v["recent"][0]["event"] == "placed" and v["recent"][0]["by"] == "Paula Paralegal"
    with pytest.raises(LookupError):
        inbox.place(f["clients"], f["inbox"], w["id"], "case-ana", "Paula Paralegal", db_path=f["db"])


def test_not_ours_is_set_aside_with_a_note(tmp_path):
    import inbox

    clients, box = tmp_path / "clients", tmp_path / "inbox"
    clients.mkdir()
    box.mkdir()
    (box / "stranger.pdf").write_bytes(pdf(STRANGER))
    inbox.process_inbox(clients, box, db_path=tmp_path / "index.db", today=TODAY)
    (w,) = inbox.view(clients, box)["waiting"]
    with pytest.raises(ValueError, match="why it isn't ours"):
        inbox.not_ours(box, w["id"], " ", "Paula Paralegal")
    record = inbox.not_ours(box, w["id"], "a former client", "Paula Paralegal")
    assert (box / "not_ours" / record["file"]).exists() and not list((box / "waiting").iterdir())
    note = json.loads((box / "not_ours" / (Path(record["file"]).stem + ".json")).read_text(encoding="utf-8"))
    assert (note["note"], note["by"]) == ("a former client", "Paula Paralegal") and note["at"]
    v = inbox.view(clients, box)
    assert not v["waiting"] and v["recent"][0]["event"] == "not_ours"


def test_a_notice_waiting_for_a_case_not_processed_yet_goes_once_it_is(tmp_path):
    import inbox

    clients, box = tmp_path / "clients", tmp_path / "inbox"
    clients.mkdir()
    box.mkdir()
    (box / "rfe.pdf").write_bytes(pdf(RFE))
    assert inbox.process_inbox(clients, box, db_path=tmp_path / "index.db", today=TODAY)["waiting"] == 1
    make_case(tmp_path, "case-ana", {"receipt.pdf": [ANA_RECEIPT], "i94.pdf": [ANA_I94]}, tmp_path / "index.db")  # the new client's first run
    again = inbox.process_inbox(clients, box, db_path=tmp_path / "index.db", today=TODAY)
    assert again["waiting"] == 0 and [e["case"] for e in again["routed"]] == ["case-ana"]


def test_the_overnight_run_reads_inbox_then_waits_for_subject_review_before_client_timeline(tmp_path, monkeypatch):
    import overnight
    from portal.store import PortalStore

    f = firm(tmp_path, maria=False)
    monkeypatch.setenv("I485_INBOX", str(f["inbox"]))
    monkeypatch.setenv("I485_INDEX", str(f["db"]))
    monkeypatch.setenv("I485_CASE_STATUS", "0")
    data = tmp_path / "data"
    store = PortalStore(data / "portal")
    store.add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    (f["inbox"] / "today.pdf").write_bytes(pdf(RFE))
    lines = []
    (tmp_path / "nobody").mkdir()
    overnight.run(tmp_path / "nobody", f["clients"], data, runner=lambda *a: None, log=lines.append)
    assert "Notice inbox: 1 notice routed, 0 waiting for a person." in lines
    assert lines.index("Notice inbox: 1 notice routed, 0 waiting for a person.") < next(i for i, x in enumerate(lines) if x.startswith("Case timelines"))
    assert "Notice inbox: 1 notice routed" in (data / "batch_report.txt").read_text(encoding="utf-8")
    pt = store.journey("case-ana")["pt"]
    assert not any(e["date"] == "2026-09-30" for e in pt["happened"])
    import journey
    case = f["clients"] / "case-ana"
    pending = journey.journey(case, TODAY)
    assert any(d["value"] == "2026-12-28" for p in pending["pending_evidence"] for d in p["dates"])
    confirm_notice_subjects(case)
    after_subject = journey.journey(case, TODAY)
    assert any(p["state"] == "source_unconfirmed" for p in after_subject["pending_evidence"])
    assert not any(e["date"] == "2026-09-30" for e in journey.client_view(after_subject, "pt")["happened"])
    confirm_notice_sources(case)
    pt = journey.client_view(journey.journey(case, TODAY), "pt")
    assert any(e["date"] == "2026-09-30" for e in pt["happened"])


def test_my_work_reads_the_inbox_and_counts_what_waits(tmp_path, monkeypatch):
    from review.server import ReviewApp

    f = firm(tmp_path, maria=False)
    import jobs

    monkeypatch.setenv("I485_INBOX", str(f["inbox"]))
    monkeypatch.setenv("I485_INDEX", str(f["db"]))
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))  # this test's own job queue (src/jobs.py)
    (f["inbox"] / "mail.pdf").write_bytes(pdf(RFE, STRANGER))
    app = ReviewApp(f["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None)
    assert app.work()["inbox"] == {"waiting": 0, "unread": 1}
    started = app.inbox_read()  # the app answers at once with a job; the worker reads the inbox
    assert started["job"]["state"] == "queued" and app.work()["inbox"] == {"waiting": 0, "unread": 1}
    assert app.inbox_read()["job"]["id"] == started["job"]["id"]  # asked again while it waits: the same job, not a second reading
    jobs.work(f["clients"], None, once=True)
    out = jobs.get(app.jobs_root, started["job"]["id"])["result"]
    assert out["text"] == "Notice inbox: 1 notice routed, 1 waiting for a person." and app.inbox_view()["counts"]["waiting"] == 1
    assert app.work()["inbox"] == {"waiting": 1, "unread": 0}
    (w,) = app.inbox_view()["waiting"]
    assert app.inbox_cases({"q": "souza"}) == [{"case": "case-ana", "name": "ANA CLARA EXEMPLO SOUZA"}]
    queued = app.inbox_place({"id": w["id"], "case": "case-ana", "reviewer": "Paula Paralegal"})
    assert queued["job"]["state"] == "queued" and len(queued["waiting"]) == 1  # still waiting until the worker has read it into the case
    jobs.work(f["clients"], None, once=True)
    placed = jobs.get(app.jobs_root, queued["job"]["id"])["result"]
    assert placed["placed"]["case"] == "case-ana" and not app.inbox_view()["waiting"]
    from review.auth import Accounts

    staff = ReviewApp(f["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                      accounts=Accounts(tmp_path / "users.json"))
    with pytest.raises(PermissionError):
        staff.inbox_read(None)  # signed in as staff only
