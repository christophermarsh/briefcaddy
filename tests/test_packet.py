"""The ready-to-file packet (src/packet.py): what goes in, in what order,
what stays out, and the page numbers the index and checklist point at."""

import json
from pathlib import Path

import pytest
from pypdf import PdfReader, PdfWriter

import packet
from fill import fill_pdf
from fill.continuation import Block, finish_part14
import schema_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FULL_SCHEMA = packet.load_schema()


@pytest.fixture(autouse=True)
def i485_only(monkeypatch):
    """Most tests are about the exhibits: the I-485 alone, with the page-numbered
    index sheet (the cover letter and companion forms have their own tests)."""
    monkeypatch.setattr(packet, "load_schema", lambda: FULL_SCHEMA | {"forms": ["i485"], "cover_letter": False, "index_sheet": True})


@pytest.fixture(autouse=True)
def office_saved(tmp_path, monkeypatch):
    """Fictional example or implementation helper."""
    import settings
    from conftest import save_shipped_office_as_the_firms

    monkeypatch.setattr(settings, "PATH", tmp_path / "office-settings.json")
    save_shipped_office_as_the_firms(tmp_path / "office-settings.json")


ROW_DONE = {"summary": {"name": "ANA SAMPLE", "a_number": "A099000001", "dob": "2006-01-02"}, "blocking": 0, "fix": 0, "check": 0, "attorney": 0}


def _pdf(path: Path, pages: int) -> None:
    w = PdfWriter()
    for _ in range(pages):
        w.add_blank_page(width=612, height=792)
    with open(path, "wb") as fh:
        w.write(fh)


@pytest.fixture(scope="module")
def filled(tmp_path_factory):
    path = tmp_path_factory.mktemp("form") / "i485_filled.pdf"
    fill_pdf(TEMPLATE, {}, path)
    return path


def _client(tmp_path, filled, classifications, pages=None, continuation=0):
    source = tmp_path / "source"
    source.mkdir()
    for doc in classifications:
        name = doc.split("#")[0]
        if not (source / name).exists():
            _pdf(source / name, (pages or {}).get(name, 1))
    d = tmp_path / "bundle"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"source_folder": str(source), "classifications": classifications}))
    (d / "i485_filled.pdf").write_bytes(filled.read_bytes())
    if continuation:
        # the form's own four Part 14 boxes, then one entry on a copy of the form's page per `continuation`
        finish_part14(d / "i485_filled.pdf", [Block("4", "1", "18", "SAMPLE LINE")] * (4 * continuation + 1), TEMPLATE)
    return d


DOCS = {
    "approval.pdf": "i360_approval",
    "certidao.pdf": "birth_certificate",
    "certidao translation.pdf": "birth_certificate",
    "translator certificate.pdf": "translation_certification",
    "passport.pdf": "passport",
    "i94.pdf": "i94",
    "social.pdf": "ssn_card",
    "questionnaire.pdf": "intake_questionnaire",
    "old 485.pdf": "i485",
    "photos of stamps.pdf": "unclassified",
}


def test_documents_go_to_their_exhibits_in_uscis_order(tmp_path, filled):
    p = packet.plan(_client(tmp_path, filled, DOCS), ROW_DONE)
    # the order of the firm's filed SIJS packets: I-360 approval, passport, other ID (SSN card...), birth certificate, I-94
    assert [(ex["letter"], ex["id"]) for ex in p["exhibits"]] == [("A", "i360"), ("B", "passport"), ("C", "other_id"), ("D", "birth"), ("E", "admission")]
    birth = [f["doc"] for f in p["exhibits"][3]["files"]]
    assert birth == ["certidao.pdf", "certidao translation.pdf", "translator certificate.pdf"]  # original, translation, certificate
    out = {f["doc"]: f for f in p["left_out"]}
    assert set(out) == {"questionnaire.pdf", "old 485.pdf"}
    assert out["old 485.pdf"]["locked"]  # an earlier filing never goes in
    assert [f["doc"] for f in p["unsorted"]] == ["photos of stamps.pdf"]
    assert not p["ready"] and any("not sorted" in x for x in p["problems"])


def test_ready_only_when_review_is_done_and_the_required_evidence_is_there(tmp_path, filled):
    docs = {k: v for k, v in DOCS.items() if v not in ("unclassified", "i360_approval")}
    d = _client(tmp_path, filled, docs)
    p = packet.plan(d, ROW_DONE)
    assert not p["ready"] and p["missing"][0]["id"] == "i360"
    assert any(c["kind"] == "missing" and "I-360" in c["text"] for c in p["checklist"])
    meta = json.loads((d / "meta.json").read_text())
    meta["classifications"]["approval.pdf"] = "i360_approval"
    _pdf(Path(meta["source_folder"]) / "approval.pdf", 1)
    (d / "meta.json").write_text(json.dumps(meta))
    assert packet.plan(d, ROW_DONE)["ready"]
    p = packet.plan(d, ROW_DONE | {"check": 2})
    assert not p["ready"] and "2 review cards still open (2 check)." in p["problems"]


def test_missing_translation_is_on_the_checklist(tmp_path, filled):
    p = packet.plan(_client(tmp_path, filled, {"approval.pdf": "i360_approval", "certidao.pdf": "birth_certificate"}), ROW_DONE)
    assert any("translation" in c["text"] for c in p["checklist"] if c["kind"] == "missing")


def test_the_built_packet_has_every_page_where_the_index_says(tmp_path, filled):
    d = _client(tmp_path, filled, DOCS, pages={"passport.pdf": 3, "certidao.pdf": 2}, continuation=1)
    manifest = packet.build(d, ROW_DONE, "Jane")
    reader = PdfReader(str(d / "packet.pdf"))
    form = len(PdfReader(str(d / "i485_filled.pdf")).pages)
    # index (1) + form + 5 exhibits, each with a tab sheet: approval 1, passport 3, SSN card 1, birth 2+1+1, I-94 1
    assert len(reader.pages) == manifest["pages"] == 1 + form + 5 + 1 + 3 + 1 + 4 + 1
    sections = {s["tab"]: s for s in manifest["sections"]}
    assert sections["I-485"]["first_page"] == 2 and sections["I-485"]["last_page"] == 1 + form
    assert sections["Exhibit B"]["last_page"] - sections["Exhibit B"]["first_page"] + 1 == 4  # tab sheet + 3 passport pages
    assert "Exhibit B" in reader.pages[sections["Exhibit B"]["first_page"] - 1].extract_text()
    index = reader.pages[0].extract_text()
    assert "Index of documents" in index and "pp. 2-" in index
    assert manifest["draft"] and "DRAFT" in index  # a file is still unsorted
    # the applicant signs on the form's Part 10 page, counted in the packet
    sig = next(x["page"] for x in manifest["signatures"] if x["form"] == "I-485" and x["who"] == "client")
    names = " ".join(str(a.get_object().get("/T")) for a in reader.pages[sig - 1].get("/Annots") or [])
    assert packet.APPLICANT_SIGNATURE_FIELD in names
    assert manifest["draft"] == bool(manifest["problems"])
    assert any("copy of the form's Part 14 page" in c["text"] for c in manifest["checklist"])
    assert {f["doc"] for f in manifest["left_out"]} >= {"old 485.pdf", "photos of stamps.pdf"}


def test_a_draft_says_so_on_its_index_sheet(tmp_path, filled):
    d = _client(tmp_path, filled, {"approval.pdf": "i360_approval", "certidao.pdf": "birth_certificate"})
    manifest = packet.build(d, ROW_DONE | {"attorney": 1}, "Jane")
    assert manifest["draft"]
    assert "DRAFT" in PdfReader(str(d / "packet.pdf")).pages[0].extract_text()


def test_a_paralegal_can_move_a_document_but_never_an_earlier_filing(tmp_path, filled):
    d = _client(tmp_path, filled, DOCS)
    packet.choose(d, "photos of stamps.pdf", "admission", "Jane")
    packet.choose(d, "social.pdf", "other_id", "Jane")
    packet.choose(d, "passport.pdf", packet.LEAVE_OUT, "Jane")
    p = packet.plan(d, ROW_DONE)
    files = {ex["id"]: [f["doc"] for f in ex["files"]] for ex in p["exhibits"]}
    assert "photos of stamps.pdf" in files["admission"] and files["other_id"] == ["social.pdf"] and "passport" not in files
    assert not p["unsorted"] and p["ready"]
    packet.choose(d, "passport.pdf", None, "Jane")  # back to its usual place
    assert "passport" in {ex["id"] for ex in packet.plan(d, ROW_DONE)["exhibits"]}
    with pytest.raises(ValueError):
        packet.choose(d, "old 485.pdf", "other_id", "Jane")
    with pytest.raises(ValueError):
        packet.choose(d, "social.pdf", "nowhere", "Jane")
    with pytest.raises(LookupError):
        packet.choose(d, "not-there.pdf", "other_id", "Jane")
    log = json.loads((d / packet.CHOICES).read_text())["log"]
    assert log[0]["by"] == "Jane" and len(log) == 4


def test_a_split_document_takes_only_its_own_pages(tmp_path, filled):
    d = _client(tmp_path, filled, {"approval.pdf": "i360_approval", "combined.pdf#p1": "birth_certificate", "combined.pdf#p2-3": "passport"},
                pages={"combined.pdf": 3})
    manifest = packet.build(d, ROW_DONE, "Jane")
    sections = {s["tab"]: s for s in manifest["sections"]}
    assert sections["Exhibit B"]["last_page"] - sections["Exhibit B"]["first_page"] == 2  # the passport: tab sheet + pages 2-3


def test_filled_form_has_no_xfa_copy(filled):
    # Adobe Reader would show the blank XFA form over the filled fields
    acroform = PdfReader(str(filled)).trailer["/Root"]["/AcroForm"].get_object()
    assert "/XFA" not in acroform


def test_the_g28_and_i765_come_from_the_same_case_and_keep_their_own_boxes(tmp_path, filled):
    from factgraph import FactGraph

    d = _client(tmp_path, filled, {"approval.pdf": "i360_approval", "certidao.pdf": "birth_certificate"})
    graph = FactGraph("sample")
    for key, value in (("applicant.family_name", "SAMPLE"), ("applicant.given_name", "ANA"), ("applicant.a_number", "A099000001"),
                       ("applicant.dob", "2006-01-02"), ("applicant.sex", "F"), ("applicant.last_arrival_city", "NEWARK"),
                       ("applicant.last_arrival_state", "NJ"), ("firm.business_name", "SAMPLE LAW LLP"), ("firm.email", "LAWYER@SAMPLE-LAW.EXAMPLE")):
        graph.add_source(key, "x.pdf", "passport", value, value, 0.9)
    graph.save(d / "fact_graph_reviewed.json")
    manifest = packet.build(d, ROW_DONE, "Jane", FULL_SCHEMA)
    # the G-1145 clipped on top (src/enotice.py), then the letter, then the G-28
    assert [s["tab"] for s in manifest["sections"]][:5] == ["G-1145", "Cover letter", "G-28", "I-485", "I-765"]
    fields = {k: str(v.get("/V")) for k, v in PdfReader(str(d / "packet.pdf")).get_fields().items() if v.get("/V")}
    g28 = {k.rsplit(".", 1)[-1]: v for k, v in fields.items() if k.startswith("g28_")}
    i765 = {k.rsplit(".", 1)[-1]: v for k, v in fields.items() if k.startswith("i765_")}
    assert g28["Pt3Line5a_FamilyName[0]"] == "SAMPLE" and g28["Pt3Line9_ANumber[0]"] == "099000001"
    assert g28["Line7_MobileTelephoneNumber[0]"] == "LAWYER@SAMPLE-LAW.EXAMPLE"  # the email box, whatever its name says
    assert i765["Line1a_FamilyName[0]"] == "SAMPLE" and i765["Line19_DOB[0]"] == "01/02/2006" and i765["place_entry[0]"] == "NEWARK, NJ"
    assert i765["section_1[0]"] == "c" and i765["section_2[0]"] == "9"
    # every form signs where it should, counted in the printed packet
    signers = {(x["form"], x["who"]) for x in manifest["signatures"]}
    assert {("G-28", "client"), ("G-28", "attorney"), ("I-765", "client"), ("I-485", "client")} <= signers
    texts = [c["text"] for c in manifest["checklist"]]
    assert any(t.startswith("I-765: Part 2, item 12") for t in texts)  # left for the attorney, never guessed
    assert any("Four identical passport-style photos" in t for t in texts)
    assert any(t.startswith("I-765: no settled answer yet for the Social Security number") for t in texts)


def test_the_cover_letter_goes_on_top_and_waits_for_this_months_visa_bulletin(tmp_path, filled):
    from factgraph import FactGraph

    d = _client(tmp_path, filled, {"approval.pdf": "i360_approval", "certidao.pdf": "birth_certificate"})
    graph = FactGraph("sample")
    for key, value in (("applicant.family_name", "SAMPLE"), ("applicant.given_name", "ANA"), ("applicant.sex", "F"),
                       ("applicant.country_of_birth", "BRAZIL"), ("applicant.i360_priority_date", "2025-02-10")):
        graph.add_source(key, "x.pdf", "passport", value, value, 0.9)
    graph.save(d / "fact_graph_reviewed.json")
    manifest = packet.build(d, ROW_DONE, "Jane", FULL_SCHEMA | {"forms": ["i485"]})
    notice, first = manifest["sections"][0], manifest["sections"][1]
    assert notice["tab"] == "G-1145" and first["tab"] == "Cover letter" and first["first_page"] == 2  # the G-1145 is clipped on top
    page1 = PdfReader(str(d / "packet.pdf")).pages[1].extract_text()
    assert "VIA USPS PRIORITY" in page1 and "Ana Sample" in page1 and "DRAFT" in page1
    assert manifest["draft"] and any("Visa Bulletin" in p for p in manifest["problems"])  # the cut-off isn't set for this month
    assert any(c["text"].startswith("Cover letter: the attorney signs") for c in manifest["checklist"])
    assert any("Fee Calculator" in c["text"] for c in manifest["checklist"])  # the printouts the letter says are enclosed
