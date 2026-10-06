"""Fictional fixture helper."""

from __future__ import annotations

from classify import classify_text, split_documents
from classify.classifier import readability
import schema_path


def test_a_form_page_is_known_by_its_footer_not_its_keywords():
    page = "Part 1. Information About You\nMost Recent I-94 ... Class of Admission\nForm I-485 Edition 09/18/26 Page 3 of 24"
    assert classify_text(page).doc_type == "i485"
    assert classify_text("Form 1-765 Edition 08/21/25 Page 2 of 7").doc_type == "i765"  # OCR reads I as 1


def test_browser_printouts_are_not_client_documents():
    assert classify_text("3/3/26, 10:21 AM Visa Bulletin For March 2026\nForm I-485 adjustment ...").doc_type == "web_printout"
    assert classify_text("3/29/26, 5:07 PM I-94/I-95 Official Website - Get Most Recent Response\nMost Recent I-94").doc_type != "web_printout"


def test_a_combined_pdf_is_split_into_its_documents():
    pages = [
        "Notice of Entry of Appearance as Attorney\nForm G-28 Edition 04/01/24 Page 1 of 4",
        "Application to Register Permanent Residence\nForm I-485 Edition 09/18/26 Page 1 of 24",
        "A-Number\nPart 6. Information About Your Marital History\nmarriage certificate date of marriage",  # footer not read
        "P<BRASILVA<<ANA<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<<\nPASSAPORTE PASSPORT",
        "3/3/26, 10:21 AM Visa Bulletin For March 2026",
    ]
    kinds = [k for _, _, k in split_documents(pages)]
    assert kinds == ["g28", "i485", "passport", "web_printout"]


def test_one_document_over_several_pages_stays_one():
    pages = ["CERTIDAO DE NASCIMENTO\nFILIACAO", "second page, signatures only"]
    assert split_documents(pages) == [(0, 1, "birth_certificate")]


def test_an_upside_down_scan_reads_as_nonsense():
    assert readability("SUVAUSS VUNOW VINYW YOL3S DALYVINSNOD") < 3
    assert readability("REPUBLICA FEDERATIVA DO BRASIL PASSAPORTE NOME DATA DE NASCIMENTO") > 8


def test_a_completed_uscis_form_in_the_folder_is_an_attorney_alert_not_a_source():
    from batch import process_documents

    result = process_documents("t", [("old.pdf", "Application to Register Permanent Residence\nForm I-485 Edition 09/18/26 Page 1 of 24")])
    alerts = [f for f in result.review_flags if f.message.startswith("PRIOR FORMS")]
    assert alerts and "Part 4 item 5" in alerts[0].message
    assert not any(k.startswith("applicant.") and k != "applicant.na.other_names" for k in result.graph.all_facts() if result.graph.get(k).sources)


# --- USCIS notices: the client's immigration history --------------------------

NOTICE = """Receipt Number Case Type
IOE0912345678 I130 - PETITION FOR ALIEN RELATIVE
Received Date Priority Date Petitioner A123 456 789

01/05/2024 01/05/2024 SILVA, JOAO
Notice Date Page Beneficiary A123 456 789
06/10/2025 1 of 1 SILVA, ANA
Notice Type: Approval Notice Class: F21
"""


def test_any_uscis_notice_is_read_from_its_own_case_type_line():
    from extract.uscis_notice import parse

    n = parse(NOTICE)
    assert (n.receipt, n.form, n.notice_type, n.klass) == ("IOE0912345678", "I-130", "APPROVAL", "F21")
    assert (n.received_date, n.priority_date, n.notice_date) == ("2024-01-05", "2024-01-05", "2025-06-10")
    # an I-360 approval that MENTIONS the I-765 is still an I-360 notice
    sijs = "Receipt Number Case Type\nMSC2390000001 1360 - PETITION FOR AMERASIAN, WIDOWER, OR SPECIAL\nNOTICE OF ACTION I-797\n... may apply for employment authorization (Form I-765)"
    assert classify_text(sijs).doc_type == "i360_approval"


def test_notices_drive_the_attorney_alerts():
    from batch import process_documents

    earlier = NOTICE.replace("IOE0912345678 I130 - PETITION FOR ALIEN RELATIVE", "IOE0911111111 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE").replace(
        "Approval Notice", "Receipt Notice")
    result = process_documents("t", [("n1.pdf", "NOTICE OF ACTION I-797\n" + NOTICE), ("n2.pdf", "NOTICE OF ACTION I-797\n" + earlier)])
    messages = " ".join(f.message for f in result.review_flags)
    assert "FILING BASIS" in messages and "family-based (I-130)" in messages
    assert "EARLIER I-485" in messages and "Part 4 item 5" in messages


# --- passports, entry, and history answers --------------------------------------


def test_passport_mrz_is_read_and_verified_by_its_check_digits():
    from extract.passport import mrz_check_digit, read_mrz

    assert mrz_check_digit("L898902C3") == 6 and mrz_check_digit("740812") == 2  # ICAO 9303 specimen
    line = "L898902C36UTO7408122F1204159<<<<<<<<<<<<<<06"
    got = read_mrz("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n" + line)
    assert (got["number"], got["issuer"], got["expiry"]) == ("L898902C3", "UTO", "2012-04-15")
    slipped = read_mrz("P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n" + line.replace("L898902C3", "L8989O2C3"))
    assert slipped["number"] == "L898902C3"  # the one correction the check digit allows
    # words printed on the page are not the MRZ's first line: "PASSAPORTE" once read as issuer "SSA"
    printed = read_mrz("REPUBLICA FEDERATIVA DO BRASIL\nPASSAPORTE PASSPORT\nP<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\n" + line)
    assert printed["issuer"] == "UTO"
    no_line1 = read_mrz("PASSAPORTO\n" + line)  # first line unreadable: the nationality on line 2
    assert no_line1["issuer"] == "UTO"


def test_entry_passport_is_the_one_whose_number_is_on_the_i94():
    from assemble import assemble
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("applicant.travel_document_number", "i94.pdf", "i94", "x", "L898902C3", 0.9)
    g.add_source("folder.passport.L898902C3", "p1.pdf", "passport", "x", "ITALY|2030-01-01", 0.95)
    g.add_source("folder.passport.FO000001", "p2.pdf", "passport", "x", "BRAZIL|2029-05-05", 0.95)
    assemble(g)
    assert (g.get("applicant.travel_document_country").value, g.get("applicant.travel_document_expiry").value) == ("ITALY", "2030-01-01")


def test_part9_followup_is_no_only_when_the_main_question_is_no():
    from assemble import completeness_findings
    from factgraph import FactGraph
    from rules.policy import load_policy_profile, run_policies

    policies = [p for p in load_policy_profile(schema_path.path("law", "policy_sijs")) if p["id"].startswith("PART9-FOLLOWUP")]
    g = FactGraph("t")
    g.add_source("applicant.part9.pt8line28", "q.pdf", "intake_questionnaire", "x", "No", 0.7, tier=3)
    g.add_source("applicant.part9.unlawfully_present_since_1997", "i94.pdf", "i94", "x", "Yes", 0.9)
    run_policies(g, policies)
    assert g.get("applicant.part9.pt8line29").value == "No"
    assert g.get("applicant.part9.pt9line77") is None  # main question Yes: the attorney answers
    assert "applicant.part9.pt9line77" in dict(completeness_findings(g))


def test_client_who_says_no_i94_while_one_is_in_the_folder_is_flagged():
    from assemble import consistency_findings
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("questionnaire.has_i94_or_parole", "q.pdf", "intake_questionnaire", "x", "No", 0.7, tier=3)
    g.add_source("applicant.i94_number", "i94.pdf", "i94", "x", "12345678901", 0.98)
    assert "I-94 12345678901" in dict(consistency_findings(g))["applicant.i94_number"]


# --- the document record (src/documents.py, schemas/registers/document_types.json) ------------------------
# A made-up folder in the demo client's shapes (src/portal/demo.py): everything EXEMPLO.

import json  # noqa: E402
import re  # noqa: E402
from pathlib import Path  # noqa: E402

import pytest  # noqa: E402
from portal.demo import _mrz  # noqa: E402

PASSPORT = ["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE PASSPORT", "EXEMPLO: DEMONSTRATION DOCUMENT",
            "SOBRENOME / SURNAME: EXEMPLO SOUZA", "NOME / GIVEN NAMES: ANA CLARA", "NACIONALIDADE / NATIONALITY: BRASILEIRO(A)", "",
            *_mrz()]
I94 = ["I-94/I-95 Official Website - Get Most Recent Response", "Most Recent I-94", "EXEMPLO: DEMONSTRATION DOCUMENT",
       "Admission (I-94) Record Number: 99900012300", "Arrival/Issued Date: 2019 July 15", "Class of Admission: B2",
       "Admit Until Date: 01/14/2020", "Last/Surname: EXEMPLO SOUZA", "First (Given) Name: ANA CLARA", "Birth Date: 03/14/2006",
       "Document Number: XX0001234", "Country of Citizenship: Brazil"]
CHILD_BIRTH = ["REPUBLICA FEDERATIVA DO BRASIL", "CERTIDAO DE NASCIMENTO", "EXEMPLO: DEMONSTRATION DOCUMENT", "NOME: /", "PEDRO EXEMPLO SOUZA /",
               "MUNICIPIO DE REGISTRO E UF LOCAL, MUNICIPIO DE NASCIMENTO E UF SEXO", "SOROCABA - SP HOSPITAL REGIONAL, SOROCABA-SP. M",
               "FILIAGAO", "JOAO EXEMPLO SOUZA, NACIONALIDADE: BRASILEIRO(A), NATURALIDADE: SOROCABA-SP, e ANA",
               "CLARA EXEMPLO SOUZA, NACIONALIDADE: BRASILEIRO(A), NATURALIDADE: SOROCABA-SP.", "AVOS", "JOSE EXEMPLO e MARIA EXEMPLO"]
SSN = ["SOCIAL SECURITY", "EXEMPLO: DEMONSTRATION DOCUMENT", "THIS NUMBER HAS BEEN ESTABLISHED FOR", "123-45-6789",
       "ANA CLARA EXEMPLO SOUZA", "VALID FOR WORK ONLY WITH DHS AUTHORIZATION"]
ENVELOPE = ["DO NOT OPEN. FOR USCIS USE ONLY.", "Form I-693", "EXEMPLO: DEMONSTRATION DOCUMENT", "ANA CLARA EXEMPLO SOUZA"]
OLD_ASYLUM = ["Application for Asylum and for Withholding of Removal", "EXEMPLO: DEMONSTRATION DOCUMENT",
              "Form I-589 Edition 01/20/25 Page 1 of 12"]


def _pdf(*pages: list[str]) -> bytes:
    """One PDF, one page per list of lines (a text layer, as a clean scan becomes after OCR)."""
    import io

    from portal.demo import document_pdf
    from pypdf import PdfReader, PdfWriter

    writer = PdfWriter()
    for lines in pages:
        writer.add_page(PdfReader(io.BytesIO(document_pdf(lines))).pages[0])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


@pytest.fixture(scope="module")
def folder_run(tmp_path_factory):
    """A made-up client folder: a combined scan (passport + I-94), the same passport sent twice, the client's child's
    Portuguese birth certificate, the Social Security card, the sealed I-693 envelope, an earlier I-589."""
    from batch import process_client_folder
    from review.state import save_bundle

    root = tmp_path_factory.mktemp("docs")
    source, out = root / "source", root / "bundle"
    source.mkdir()
    (source / "a scan of everything.pdf").write_bytes(_pdf(PASSPORT, I94))
    (source / "passport.pdf").write_bytes(_pdf(PASSPORT))
    (source / "passport again.pdf").write_bytes((source / "passport.pdf").read_bytes())
    (source / "certidao pedro.pdf").write_bytes(_pdf(CHILD_BIRTH))
    (source / "ssn.pdf").write_bytes(_pdf(SSN))
    (source / "medical.pdf").write_bytes(_pdf(ENVELOPE))
    (source / "old asylum.pdf").write_bytes(_pdf(OLD_ASYLUM))
    result = process_client_folder("t-docs", source)
    save_bundle(result, out, source)
    by_file = {}
    for r in json.loads((out / "documents.json").read_text(encoding="utf-8"))["documents"]:
        for d in r["doc_ids"]:
            by_file[d] = r
    return {"result": result, "out": out, "source": source, "by": by_file}


def test_every_document_gets_one_record_in_the_fixed_shape(folder_run):
    data = json.loads((folder_run["out"] / "documents.json").read_text(encoding="utf-8"))
    assert data["version"] == 1 and data["built"]
    keys = {"id", "files", "doc_ids", "pages", "type", "confidence", "person", "person_set_by", "person_basis", "language", "language_basis",
            "language_country", "issued", "expires", "issued_kind", "expires_kind", "dates_read", "identifiers", "quality", "quality_set_by",
            "quality_basis", "quality_measures", "hash", "source", "added", "roles", "found", "tags", "confidential", "text", "translated"}
    for r in data["documents"]:
        assert set(r) == keys, set(r) ^ keys
        assert re.fullmatch(r"[0-9a-f]{16}", r["id"]) and re.fullmatch(r"[0-9a-f]{64}", r["hash"])
        assert set(r["identifiers"]) == {"a_number", "receipt", "passport", "ssn_last4"} and r["source"] == "folder"
    assert len({r["id"] for r in data["documents"]}) == len(data["documents"])


def test_a_combined_scan_is_one_record_per_part_with_its_pages(folder_run):
    by = folder_run["by"]
    first, second = by["a scan of everything.pdf#p1"], by["a scan of everything.pdf#p2"]
    assert (first["type"], first["pages"]) == ("passport", [1]) and (second["type"], second["pages"]) == ("i94", [2])
    assert first["hash"] == second["hash"] and first["id"] != second["id"]  # one file, two documents
    assert (second["issued"], second["expires"], second["identifiers"]["passport"]) == ("2019-07-15", "2020-01-14", "XX0001234")
    assert first["expires"] == "2032-01-10" and first["roles"] == ["identity", "nationality"]  # no admission stamp or visa in it: no entry
    assert (second["issued_kind"], second["expires_kind"]) == ("arrived", "admit_until")


def test_the_same_file_sent_twice_is_one_record_and_one_exhibit(folder_run, tmp_path):
    import packet

    record = folder_run["by"]["passport.pdf"]
    assert record is folder_run["by"]["passport again.pdf"] and record["files"] == ["passport again.pdf", "passport.pdf"]
    plan = packet.plan(folder_run["out"], {"blocking": 0})
    placed = [f["doc"] for ex in plan["exhibits"] for f in ex["files"]]
    assert sum(1 for d in placed if d in ("passport.pdf", "passport again.pdf")) == 1
    twice = [f for f in plan["left_out"] if f["doc"] in ("passport.pdf", "passport again.pdf")]
    assert len(twice) == 1 and "sent twice" in twice[0]["why"]


def test_language_person_and_quality(folder_run):
    by = folder_run["by"]
    assert by["certidao pedro.pdf"]["language"] == "pt" and by["ssn.pdf"]["language"] == "en"
    assert by["passport.pdf"]["person"] == "applicant"  # the MRZ's name is the client's (as the I-94 states it)
    assert by["certidao pedro.pdf"]["person"] == "child_1"  # the client is a parent on it
    assert by["ssn.pdf"]["person"] == "unknown"  # its reader doesn't read the name: a reviewer says
    assert by["ssn.pdf"]["identifiers"]["ssn_last4"] == "6789" and by["passport.pdf"]["quality"] == "readable"


def test_the_sealed_envelope_is_recognized_by_its_label_and_never_read(folder_run):
    env = folder_run["by"]["medical.pdf"]
    assert env["type"] == "i693_envelope" and env["roles"] == ["medical"]
    assert env["text"] == "" and env["language"] == "unknown"  # never read beyond the label
    assert "medical.pdf" not in folder_run["result"].extracted
    assert classify_text("DO NOT OPEN. FOR USCIS USE ONLY.").doc_type == "i693_envelope"


def test_confidential_types_carry_their_flag(folder_run):
    assert folder_run["by"]["old asylum.pdf"]["confidential"] == "208.6"
    assert folder_run["by"]["passport.pdf"]["confidential"] is None
    import documents

    assert documents.type_info("i914")["confidential"] == documents.type_info("i918")["confidential"] == "1367"


def test_reprocessing_keeps_what_a_reviewer_set(folder_run, tmp_path):
    import shutil

    import documents
    from batch import process_client_folder
    from review.state import save_bundle

    out = tmp_path / "bundle"
    shutil.copytree(folder_run["out"], out)
    ssn = folder_run["by"]["ssn.pdf"]["id"]
    documents.set_person(out, ssn, "applicant", "Paulo Paralegal", "paralegal")
    documents.set_quality(out, ssn, "cut_off", "Paulo Paralegal", "paralegal")
    documents.tag(out, ssn, "presence", "Paulo Paralegal", "paralegal")
    with pytest.raises(ValueError):
        documents.set_person(out, ssn, "the neighbour", "Paulo Paralegal")
    with pytest.raises(ValueError):
        documents.tag(out, ssn, "presence", "")  # who did it is always recorded
    save_bundle(process_client_folder("t-docs", folder_run["source"]), out, folder_run["source"])
    again = next(r for r in documents.read(out)["documents"] if r["id"] == ssn)
    assert again["person"] == "applicant" and again["person_set_by"]["who"] == "Paulo Paralegal"
    assert again["quality"] == "cut_off" and [t["role"] for t in again["tags"]] == ["presence"]
    assert again["roles"] == ["identity", "presence"]  # the type's, then the tag
    documents.untag(out, ssn, "presence", "Paulo Paralegal")
    assert documents.by_doc(out)["ssn.pdf"]["roles"] == ["identity"]


def test_the_taxonomy_and_the_classifier_stay_in_step():
    from classify.classifier import _FORM_TYPES
    from classify.patterns import NOTICE_CASE_TYPE_MAP, PATTERNS, TAXONOMY_PATH

    taxonomy = json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))
    types = {t["id"]: t for t in taxonomy["types"]}
    by_how = lambda how: {t for t, info in types.items() if info["recognized"] == how}  # noqa: E731
    assert set(PATTERNS) == by_how("text")  # a pattern without a name, or a name "recognized by text" with no pattern
    assert by_how("explicit_header") == {"travel_history"}  # title plus table; checked before I-94/browser scoring
    assert set(_FORM_TYPES.values()) <= set(types) and by_how("form_footer") <= set(_FORM_TYPES.values())
    assert set(NOTICE_CASE_TYPE_MAP.values()) == by_how("notice_case_type")
    assert {"web_printout", "unclassified"} <= set(types)
    for t in taxonomy["types"]:
        assert set(t["roles"]) <= set(taxonomy["roles"]) and t["roles"], t["id"]
        assert t["confidential"] in (None, "1367", "208.6") and t["person_from"] in ("document", "reviewer"), t["id"]
        assert isinstance(t["expires"], bool) and isinstance(t["read_beyond_type"], bool), t["id"]
        assert t["name"] and " -- " not in t["name"] and "—" not in t["name"] and "_" not in t["name"], t["id"]
    for kept_out in ("engagement_letter", "attorney_correspondence", "i693_envelope"):
        assert types[kept_out]["read_beyond_type"] is False
    # every type a packet's exhibits name is in the taxonomy (its name on screen)
    for path in schema_path.glob("packet"):
        for ex in json.loads(path.read_text(encoding="utf-8")).get("exhibits", []):
            assert set(ex["types"]) <= set(types), (path.stem, set(ex["types"]) - set(types))
    raw = TAXONOMY_PATH.read_text(encoding="utf-8")
    assert raw == json.dumps(taxonomy, indent=2, ensure_ascii=False) + "\n"


def test_the_demo_clients_packet_has_the_same_exhibits_with_the_record(tmp_path):
    import packet
    from portal import demo
    from portal.store import PortalStore

    demo.seed(PortalStore(tmp_path / "portal"), tmp_path / "clients")
    d = tmp_path / "clients" / "demo-ana"
    records = json.loads((d / "documents.json").read_text(encoding="utf-8"))["documents"]
    assert {r["type"] for r in records} == {"passport", "birth_certificate", "i360_approval", "i94", "ssn_card"}
    assert all(r["source"] == "portal" for r in records)
    exhibits = lambda: [(ex["letter"], ex["id"], [f["doc"] for f in ex["files"]]) for ex in packet.plan(d, {"blocking": 0})["exhibits"]]  # noqa: E731
    with_record = exhibits()
    (d / "documents.json").unlink()
    assert exhibits() == with_record
    assert [x[1] for x in with_record] == ["i360", "passport", "other_id", "birth", "admission"]


def _case(tmp_path, classifications: dict[str, str]) -> Path:
    """A processed case's folder: meta.json and blank one-page documents (as tests/test_packet.py makes them)."""
    from pypdf import PdfWriter

    source, d = tmp_path / "source", tmp_path / "case"
    source.mkdir()
    d.mkdir()
    for n, doc in enumerate(classifications):
        w = PdfWriter()
        w.add_blank_page(width=612 + n, height=792)  # each its own bytes: no two are the same file
        with open(source / doc, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"client_id": "t-case", "source_folder": str(source), "classifications": classifications}))
    return d


def _set(d: Path, doc: str, person: str) -> None:
    import documents

    documents.set_person(d, documents.by_doc(d)[doc]["id"], person, "Paulo Paralegal", "paralegal")


def test_a_reviewers_tag_places_a_document_by_what_it_shows(tmp_path):
    import documents
    import packet

    d = _case(tmp_path, {"screenshot.pdf": "photograph", "school.pdf": "school_record", "report.pdf": "country_conditions"})
    schema = packet.load_schema() | {"forms": ["i485"], "cover_letter": False, "leave_out": {},
                                     "exhibits": [{"id": "good_faith", "title": "Good faith", "types": ["lease"]},
                                                  {"id": "abuse", "title": "The abuse", "types": ["photograph"]},
                                                  {"id": "residence", "title": "Living here", "types": [], "roles": ["presence"]},
                                                  {"id": "hardship", "title": "Hardship", "types": []}]}
    plan = lambda: packet.plan(d, {"blocking": 0}, schema)  # noqa: E731
    where = lambda: {f["doc"]: ex["id"] for ex in plan()["exhibits"] for f in ex["files"]}  # noqa: E731
    # by type; a school record by its type's role, to the exhibit that lists the role; an exhibit's id alone takes no type
    assert where() == {"screenshot.pdf": "abuse", "school.pdf": "residence"}
    assert [f["doc"] for f in plan()["unsorted"]] == ["report.pdf"]
    documents.tag(d, documents.by_doc(d)["screenshot.pdf"]["id"], "good_faith", "Paulo Paralegal", "paralegal")
    assert where()["screenshot.pdf"] == "good_faith"  # "this screenshot is a joint lease": a reviewer's word names the exhibit
    documents.tag(d, documents.by_doc(d)["report.pdf"]["id"], "hardship", "Paulo Paralegal", "paralegal")
    assert where()["report.pdf"] == "hardship"


def test_family_reads_the_petitioners_proof_from_the_record_first(tmp_path):
    import family

    d = _case(tmp_path, {"card.pdf": "green_card", "passport.pdf": "us_passport"})
    assert family.petitioner_documents(d) == ["card.pdf", "passport.pdf"]  # nobody has said otherwise: by type, as before
    _set(d, "card.pdf", "applicant")  # the client's own green card
    assert family.petitioner_documents(d) == ["passport.pdf"]
    assert family.petitioner_documents(d, doc_types=("green_card",)) == []


def _graph(**facts):
    from factgraph import FactGraph

    g = FactGraph("t")
    for key, value in facts.items():
        g.add_source(key.replace("__", "."), "t", "intake_questionnaire", value, value, 0.95)
    return g


def test_vawa_reads_whose_proof_of_status_from_the_record_first(tmp_path):
    import vawa

    d = _case(tmp_path, {"us passport.pdf": "us_passport", "passport.pdf": "passport"})
    g = _graph(vawa__classification=vawa.SPOUSE)
    note = vawa.document_notes(d, g)[0]["text"]
    assert "The abuser's proof of citizenship or residence: U.S. passport" in note
    _set(d, "us passport.pdf", "applicant")
    _set(d, "passport.pdf", "applicant")
    note = vawa.document_notes(d, g)[0]["text"]
    assert "abuser" not in note and "The client's documents: U.S. passport, passport" in note


def test_i730_reads_each_relatives_documents_from_the_record(tmp_path):
    import i730

    d = _case(tmp_path, {"passport.pdf": "passport", "certidao.pdf": "birth_certificate"})
    g = _graph(i730__count="2", i730__r1_relationship=i730.SPOUSE, i730__r2_relationship=i730.CHILDREN[0])
    assert i730.document_notes(d, g) == []  # nothing says whose: no guess
    _set(d, "passport.pdf", "spouse")
    _set(d, "certidao.pdf", "child_1")
    note = i730.document_notes(d, g)[0]["text"]
    assert "The spouse's documents: passport" in note and "The children's documents: birth certificate" in note


def test_t_visa_reads_each_family_members_documents_from_the_record(tmp_path):
    import t_visa

    d = _case(tmp_path, {"passport.pdf": "passport"})
    g = _graph(tvisa__family_count="1", **{"tvisa__m1__relationship": t_visa.PARENT})
    assert t_visa.document_notes(d, g) == []
    _set(d, "passport.pdf", "parent")
    assert "A parent's documents: passport" in t_visa.document_notes(d, g)[0]["text"]


def test_u_visa_reads_each_family_members_documents_from_the_record(tmp_path):
    import u_visa

    d = _case(tmp_path, {"certidao.pdf": "birth_certificate"})
    g = _graph(uvisa__members="1", uvisa__m1_relationship="Child")
    assert u_visa.document_notes(d, g) == []
    _set(d, "certidao.pdf", "child_2")
    assert "The children's documents: birth certificate" in u_visa.document_notes(d, g)[0]["text"]


def test_a_supplements_footer_names_it():
    assert classify_text("Form I-918 Supplement A Edition 01/20/25 Page 1 of 12").doc_type == "i918a"
    assert classify_text("Form I-914 Supp B Edition 01/20/25 Page 1 of 5").doc_type == "i914b"
    assert classify_text("Form I-192 Edition 01/20/25 Page 1 of 9").doc_type == "i192"
    assert classify_text("Form I-918 Edition 01/20/25 Page 1 of 11").doc_type == "i918"


def test_a_protected_case_protects_every_document_in_it(tmp_path):
    import documents

    d = _case(tmp_path, {"passport.pdf": "passport", "old asylum.pdf": "i589"})
    assert documents.case_confidentiality(d) is None
    _set(d, "passport.pdf", "applicant")
    assert documents.by_doc(d)["passport.pdf"]["confidential"] is None  # no filing yet: the type's own flag
    assert documents.read(d)["documents"][1]["confidential"] == "208.6"
    (d / "status.json").write_text(json.dumps({"filings": [{"filing": "vawa", "mailed_on": "2026-09-01"}]}))
    assert documents.case_confidentiality(d) == "1367"
    _set(d, "passport.pdf", "applicant")
    assert {r["confidential"] for r in documents.read(d)["documents"]} == {"1367"}  # 8 U.S.C. 1367 wins over 208.6
    (d / "status.json").write_text(json.dumps({"journey": {"track": {"value": "asylum"}}}))
    assert documents.case_confidentiality(d) == "208.6"
    (d / "status.json").write_text(json.dumps({"journey": {"track": {"value": "u_visa"}}}))
    assert documents.case_confidentiality(d) == "1367"


def test_whose_name_is_the_name_the_case_settles_on(tmp_path):
    """A child's I-94 next to the client's: the client's own answer says which name is the client's."""
    from types import SimpleNamespace

    import documents
    from extract.base import ExtractedField as F
    from factgraph import FactGraph

    def i94(given, family):
        return [F("applicant.given_name", given, given, 0.9), F("applicant.family_name", family, family, 0.9)]

    fields = {"mine.pdf": i94("ANA CLARA", "EXEMPLO SOUZA"), "pedro i94.pdf": i94("PEDRO", "EXEMPLO SOUZA"),
              "pedro birth.pdf": [F("applicant.birth_certificate_name", "x", "PEDRO EXEMPLO SOUZA", 0.8),
                                  F("applicant.birth_cert.parent_a_name", "x", "JOAO EXEMPLO SOUZA", 0.85),
                                  F("applicant.birth_cert.parent_b_name", "x", "ANA CLARA EXEMPLO SOUZA", 0.85)]}
    types = {"mine.pdf": "i94", "pedro i94.pdf": "i94", "pedro birth.pdf": "birth_certificate"}

    def people(stated: bool) -> dict[str, str]:
        g = FactGraph("t")
        if stated:
            g.add_source("applicant.given_name", "portal questionnaire", "intake_questionnaire", "Ana Clara", "ANA CLARA", 0.95, tier=3)
            g.add_source("applicant.family_name", "portal questionnaire", "intake_questionnaire", "Exemplo Souza", "EXEMPLO SOUZA", 0.95, tier=3)
        for doc, fs in fields.items():
            for f in fs:
                g.add_source(f.fact_key, doc, types[doc], f.raw_value, f.normalized_value, f.confidence)
        built = documents.build(tmp_path, {d: SimpleNamespace(doc_type=t, confidence=0.9) for d, t in types.items()}, fields, graph=g)
        return {r["files"][0]: r["person"] for r in built["documents"]}

    expected = {"mine.pdf": "applicant", "pedro i94.pdf": "unknown", "pedro birth.pdf": "child_1"}
    assert people(stated=True) == expected
    fields["mine again.pdf"], types["mine again.pdf"] = fields["mine.pdf"], "i94"  # no answer: the name most documents give
    assert people(stated=False) == expected | {"mine again.pdf": "applicant"}


def test_a_deleted_first_copy_leaves_the_other_in_the_packet(folder_run, tmp_path):
    import shutil

    import packet

    source, out = tmp_path / "source", tmp_path / "bundle"
    shutil.copytree(folder_run["source"], source)
    shutil.copytree(folder_run["out"], out)
    meta = json.loads((out / "meta.json").read_text(encoding="utf-8"))
    (out / "meta.json").write_text(json.dumps(meta | {"source_folder": str(source)}), encoding="utf-8")
    (source / "passport again.pdf").unlink()  # the copy the record lists first
    placed = [f["doc"] for ex in packet.plan(out, {"blocking": 0})["exhibits"] for f in ex["files"]]
    assert "passport.pdf" in placed


def test_untag_takes_only_a_role_from_the_list(tmp_path):
    import documents

    d = _case(tmp_path, {"passport.pdf": "passport"})
    doc = documents.by_doc(d)["passport.pdf"]["id"]
    with pytest.raises(ValueError):
        documents.untag(d, doc, "not a role", "Paulo Paralegal")


# --- the Documents tab tells the truth (brief G1): whose, language, dates, quality, what it shows ------------------------------
# Every value says where it came from (person_basis, language_basis, issued_kind/expires_kind, quality_basis, found).


def _made_case(tmp_path, files: dict[str, tuple[str, list[str] | None]], facts: list[tuple] = ()) -> Path:
    """A case processed before documents.json existed: the folder's documents (a text layer when lines are given, a blank page
    otherwise), meta.json's classifications and a fact graph whose sources name the documents: (key, doc id, doc type, value[, raw])."""
    from factgraph import FactGraph
    from pypdf import PdfWriter

    source, d = tmp_path / "source", tmp_path / "case"
    source.mkdir()
    d.mkdir()
    for n, (name, (_, lines)) in enumerate(files.items()):
        if lines:
            (source / name).write_bytes(_pdf(lines))
        else:
            w = PdfWriter()
            w.add_blank_page(width=612 + n, height=792)
            with open(source / name, "wb") as fh:
                w.write(fh)
    (d / "meta.json").write_text(json.dumps({"client_id": "t-case", "source_folder": str(source),
                                             "classifications": {name: kind for name, (kind, _) in files.items()}}))
    g = FactGraph("t-case")
    for fact in facts:
        key, doc, doc_type, value = fact[:4]
        g.add_source(key, doc, doc_type, fact[4] if len(fact) > 4 else value, value, 0.95, tier=3 if doc_type == "intake_questionnaire" else 1)
    g.save(d / "fact_graph.json")
    return d


Q = ("portal questionnaire", "intake_questionnaire")


def test_whose_from_a_number_that_is_the_clients(tmp_path):
    """Several people on the case (a child's birth certificate names the client as a parent): a document that names nobody is
    the client's only when it carries the client's own number."""
    import documents

    d = _made_case(tmp_path, {"pedro.pdf": ("birth_certificate", CHILD_BIRTH), "ssn.pdf": ("ssn_card", SSN), "other ssn.pdf": ("ssn_card", None),
                              "notice.pdf": ("uscis_notice", None), "passport.pdf": ("passport", None)},
                   [("applicant.given_name", *Q, "ANA CLARA"), ("applicant.family_name", *Q, "EXEMPLO SOUZA"),
                    ("applicant.birth_certificate_name", "pedro.pdf", "birth_certificate", "PEDRO EXEMPLO SOUZA"),
                    ("applicant.birth_cert.parent_b_name", "pedro.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA"),
                    ("applicant.ssn", *Q, "123-45-6789"), ("applicant.ssn", "ssn.pdf", "ssn_card", "123-45-6789"),
                    ("applicant.ssn", "other ssn.pdf", "ssn_card", "987-65-4321"),
                    ("questionnaire.a_number", *Q, "099000123"), ("applicant.a_number", "notice.pdf", "uscis_notice", "A099000123"),
                    ("applicant.travel_document_number", *Q, "XX0001234"), ("folder.passport.XX0001234", "passport.pdf", "passport", "BRAZIL|2032-01-10")])
    by = documents.by_doc(d)
    assert (by["pedro.pdf"]["person"], by["pedro.pdf"]["person_basis"]) == ("child_1", "named")
    assert (by["ssn.pdf"]["person"], by["ssn.pdf"]["person_basis"]) == ("applicant", "identifiers")  # the SSN the client gave
    assert (by["other ssn.pdf"]["person"], by["other ssn.pdf"]["person_basis"]) == ("unknown", "unknown")  # someone else's number: not assumed
    assert (by["notice.pdf"]["person"], by["notice.pdf"]["person_basis"]) == ("applicant", "identifiers")  # the A-Number, written either way
    assert (by["passport.pdf"]["person"], by["passport.pdf"]["person_basis"]) == ("applicant", "identifiers")  # the passport number


def test_whose_when_the_case_has_only_the_client_and_never_by_assumption_otherwise(tmp_path):
    import documents

    d = _made_case(tmp_path, {"ssn.pdf": ("ssn_card", SSN), "scan.pdf": ("passport", None), "report.pdf": ("country_conditions", None),
                              "i94.pdf": ("i94", I94)},
                   [("applicant.given_name", *Q, "ANA CLARA"), ("applicant.family_name", *Q, "EXEMPLO SOUZA"), ("applicant.marital_status", *Q, "Single"),
                    ("applicant.total_children", *Q, "0"), ("applicant.spouse_in_military", *Q, "N/A"),
                    ("applicant.given_name", "i94.pdf", "i94", "ANA CLARA"), ("applicant.family_name", "i94.pdf", "i94", "EXEMPLO SOUZA")])
    by = documents.by_doc(d)
    assert (by["i94.pdf"]["person"], by["i94.pdf"]["person_basis"]) == ("applicant", "named")
    assert (by["ssn.pdf"]["person"], by["ssn.pdf"]["person_basis"]) == ("applicant", "only_person")
    assert (by["scan.pdf"]["person"], by["scan.pdf"]["person_basis"]) == ("applicant", "only_person")
    assert by["report.pdf"]["person"] == "unknown"  # a country report is nobody's document
    documents.save(d, documents.load(d))  # the case's record written (as processing writes it)
    assert set(documents.documents_for(d, ["ssn_card"], ["applicant"], guess=False)) == {"ssn.pdf"}  # the filings use it as a name on it
    # a person says the scanned passport is the spouse's: the case is no longer the client's alone, and the assumption goes
    documents.set_person(d, by["scan.pdf"]["id"], "spouse", "Paulo Paralegal", "paralegal")
    by = documents.by_doc(d)
    assert (by["scan.pdf"]["person"], by["scan.pdf"]["person_basis"]) == ("spouse", "set_by_person")
    assert (by["ssn.pdf"]["person"], by["ssn.pdf"]["person_basis"]) == ("unknown", "unknown")
    assert {r["person"] for r in documents.read(d)["documents"] if r["files"] == ["ssn.pdf"]} == {"unknown"}  # saved as the case now says


RAFAEL_PASSPORT = ["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE PASSPORT", "EXEMPLO: DEMONSTRATION DOCUMENT",
                   "P<BRAEXEMPLO<COSTA<<RAFAEL".ljust(44, "<")]
CLIENT = [("applicant.given_name", *Q, "ANA CLARA"), ("applicant.family_name", *Q, "EXEMPLO SOUZA")]


def test_a_second_named_person_ends_the_only_person_assumption(tmp_path):
    """Verification 1: a spouse's passport (another name in its MRZ) or another person's I-94, with no marital facts in the case."""
    import documents

    for name, files, facts in (
        ("spouse passport", {"ssn.pdf": ("ssn_card", SSN), "rafael.pdf": ("passport", RAFAEL_PASSPORT)}, CLIENT),
        ("other i94", {"ssn.pdf": ("ssn_card", SSN), "i94.pdf": ("i94", None)},
         CLIENT + [("applicant.given_name", "i94.pdf", "i94", "RAFAEL"), ("applicant.family_name", "i94.pdf", "i94", "EXEMPLO COSTA")])):
        case = tmp_path / name
        case.mkdir()
        d = _made_case(case, files, facts)
        by = documents.by_doc(d)
        other = next(r for doc, r in by.items() if doc != "ssn.pdf")
        assert (other["person"], other["person_basis"]) == ("unknown", "named"), name  # a name nobody on the case has
        assert (by["ssn.pdf"]["person"], by["ssn.pdf"]["person_basis"]) == ("unknown", "unknown"), name


def test_a_name_comes_before_a_number_and_the_disagreement_is_shown(tmp_path):
    """Verification 3: an I-94 naming someone else that carries the client's passport number stays that person's (unknown),
    with the disagreement recorded for the screen."""
    import documents

    d = _made_case(tmp_path, {"i94.pdf": ("i94", None)},
                   CLIENT + [("applicant.travel_document_number", *Q, "XX0001234"),
                             ("applicant.given_name", "i94.pdf", "i94", "RAFAEL"), ("applicant.family_name", "i94.pdf", "i94", "EXEMPLO COSTA"),
                             ("applicant.travel_document_number", "i94.pdf", "i94", "XX0001234")])
    record = documents.by_doc(d)["i94.pdf"]
    assert (record["person"], record["person_basis"], record["person_conflict"]) == ("unknown", "named", "name_and_number")


def test_vawa_t_u_and_family_cases_are_never_the_client_alone(tmp_path):
    """Verification 2: a case known as VAWA, U or family only by a filing mailed, by the abuser's facts, or by an I-130 notice."""
    import documents

    cases = {
        "vawa mailed": ([], {"filings": [{"filing": "vawa", "mailed_on": "2026-09-01"}]}),
        "abuser facts": ([("vawa.abuser_family_name", *Q, "EXEMPLO COSTA")], {}),
        "i130 filed": ([], {"filings": [{"filing": "family", "mailed_on": "2026-09-01"}]}),
        "u visa filed": ([], {"filings": [{"filing": "u_visa", "mailed_on": "2026-09-01"}]}),
        "i130 notice": ([("folder.uscis_case.IOE0999000777.receipt_20260101", "n.pdf", "uscis_notice", "I-130 RECEIPT, 2026-01-01", "IOE0999000777")], {}),
    }
    for name, (facts, status) in cases.items():
        case = tmp_path / name
        case.mkdir()
        d = _made_case(case, {"ssn.pdf": ("ssn_card", SSN)}, CLIENT + facts)
        if status:
            (d / "status.json").write_text(json.dumps(status))
        assert documents.by_doc(d)["ssn.pdf"]["person"] == "unknown", name
    alone = tmp_path / "alone"
    alone.mkdir()
    d = _made_case(alone, {"ssn.pdf": ("ssn_card", SSN)}, CLIENT)
    assert documents.by_doc(d)["ssn.pdf"]["person_basis"] == "only_person"  # the same case with none of them


def test_the_search_index_reads_whose_as_the_case_says_now(tmp_path):
    """Verification 8: the index (and the expiry watch) read through load(), so an assumption saved earlier doesn't outlive the case."""
    import sqlite3

    import documents
    import index

    d = _made_case(tmp_path, {"ssn.pdf": ("ssn_card", SSN)}, CLIENT)
    documents.save(d, documents.load(d))
    assert documents.read(d)["documents"][0]["person"] == "applicant"  # saved while the client was the only person
    (d / "status.json").write_text(json.dumps({"filings": [{"filing": "family", "mailed_on": "2026-09-01"}]}))
    index.rebuild(d, tmp_path / "index.db")
    db = sqlite3.connect(tmp_path / "index.db")
    assert db.execute("select person from documents where case_id = ?", (d.name,)).fetchall() == [("unknown",)]


def test_a_family_case_is_never_the_client_alone(tmp_path):
    import documents

    for fact in (("petitioner.status", *Q, "U.S. citizen"), ("applicant.marital_status", *Q, "Married"), ("applicant.total_children", *Q, "2"),
                 ("applicant.child1_given_name", *Q, "PEDRO"), ("tvisa.family_count", *Q, "1")):
        case = tmp_path / fact[0]
        case.mkdir()
        d = _made_case(case, {"ssn.pdf": ("ssn_card", SSN)}, [fact])
        assert documents.by_doc(d)["ssn.pdf"]["person"] == "unknown", fact
    case = tmp_path / "with a green card"
    case.mkdir()
    d = _made_case(case, {"ssn.pdf": ("ssn_card", SSN), "card.pdf": ("green_card", None)})
    assert documents.by_doc(d)["ssn.pdf"]["person"] == "unknown"  # a relative's proof of status in the folder


def test_language_by_its_words_by_its_kind_and_by_the_issuing_country(tmp_path):
    import documents

    d = _made_case(tmp_path, {"certidao.pdf": ("birth_certificate", CHILD_BIRTH[:2] + ["NOME: /", "ANA CLARA EXEMPLO SOUZA /", "FILIACAO", "CARTORIO"]),
                              "ssn.pdf": ("ssn_card", ["SEGURO SOCIAL", "NOME DA FILHA", "DO DA DOS", "123-45-6789"]),
                              "passport.pdf": ("passport", list(_mrz())), "blank birth.pdf": ("birth_certificate", None),
                              "id.pdf": ("national_id", None)},
                   [("applicant.country_of_birth", *Q, "HAITI"), ("applicant.citizenship", *Q, "GUATEMALA")])
    by = documents.by_doc(d)
    assert (by["certidao.pdf"]["language"], by["certidao.pdf"]["language_basis"]) == ("pt", "text")
    assert (by["ssn.pdf"]["language"], by["ssn.pdf"]["language_basis"]) == ("en", "type")  # a U.S. government document, whatever names are on it
    assert (by["passport.pdf"]["language"], by["passport.pdf"]["language_basis"], by["passport.pdf"]["language_country"]) == ("pt", "country", "Brazil")
    assert (by["blank birth.pdf"]["language"], by["blank birth.pdf"]["language_country"]) == ("fr", "Haiti")  # the client's country of birth
    assert (by["id.pdf"]["language"], by["id.pdf"]["language_country"]) == ("es", "Guatemala")  # an identity card: the client's citizenship
    # a person corrects the assumption; it is kept through reprocessing (merge)
    documents.set_language(d, by["blank birth.pdf"]["id"], "ht", "Paulo Paralegal", "paralegal")
    again = documents.by_doc(d)["blank birth.pdf"]
    assert (again["language"], again["language_basis"], again["language_set_by"]["who"]) == ("ht", "set_by_person", "Paulo Paralegal")
    rebuilt = {"documents": [r | {"language": "fr", "language_basis": "country"} for r in documents.read(d)["documents"]]}
    assert {r["language"] for r in documents.merge(documents.read(d), rebuilt)["documents"] if r["files"] == ["blank birth.pdf"]} == {"ht"}
    with pytest.raises(ValueError):
        documents.set_language(d, again["id"], "klingon", "Paulo Paralegal")
    assert documents.document_language("birth_certificate", "", [], "ATLANTIS") == ("unknown", "unknown", None)  # a country the table doesn't know
    assert documents.country_of("Brasil") == documents.country_of("BRA") == "BRA"


def test_a_passport_the_mrz_says_another_country_issued_is_never_the_clients_language():
    """Verification 5: the MRZ names the issuer (here the ICAO specimen's "UTO", in no table): never "assumed: Brazil"."""
    import documents
    from extract.base import ExtractedField as F

    specimen = "P<UTOERIKSSON<<ANNA<MARIA<<<<<<<<<<<<<<<<<<<\nL898902C36UTO7408122F1204159<<<<<<<<<<<<<<06"
    assert documents.document_language("passport", specimen, [], "BRAZIL") == ("unknown", "unknown", None)
    assert documents.document_language("passport", "", [F("folder.passport.E12345678", "x", "CHINA|2030-01-01", 0.95)], "BRAZIL") == ("unknown", "unknown", None)
    assert documents.document_language("passport", "", [], "BRAZIL") == ("pt", "country", "Brazil")  # nothing on it says: the client's country


def test_portuguese_names_do_not_make_an_english_document_portuguese():
    """Verification 6: the particles of names and places (DA, DOS, DAS, SAO) count only beside real Portuguese words."""
    import documents

    english = ("CERTIFICATE OF BIRTH\nNAME OF CHILD: JOAO DA SILVA DOS SANTOS\nNAME OF MOTHER: MARIA DAS GRACAS\nPLACE OF BIRTH: SAO PAULO\n"
               "DATE OF BIRTH: 01/02/2010\nTHIS IS A TRUE COPY OF THE RECORD")
    assert documents.language(english) == "en"
    assert documents.language("\n".join(CHILD_BIRTH)) == "pt"
    assert documents.language("ACTA DE NACIMIENTO\nNOMBRE: MARIA DEL CARMEN DE LOS SANTOS\nFECHA DE NACIMIENTO\nLUGAR: EL SALVADOR") == "es"


def test_a_childs_certificate_is_not_assumed_to_be_in_the_clients_language(tmp_path):
    from types import SimpleNamespace

    import documents
    from extract.base import ExtractedField as F
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("applicant.given_name", *Q, "Ana Clara", "ANA CLARA", 0.95, tier=3)
    g.add_source("applicant.family_name", *Q, "Exemplo Souza", "EXEMPLO SOUZA", 0.95, tier=3)
    g.add_source("applicant.country_of_birth", *Q, "Brasil", "BRAZIL", 0.95, tier=3)
    fields = {"pedro.pdf": [F("applicant.birth_certificate_name", "x", "PEDRO EXEMPLO SOUZA", 0.8),
                            F("applicant.birth_cert.parent_b_name", "x", "ANA CLARA EXEMPLO SOUZA", 0.85)]}
    built = documents.build(tmp_path, {"pedro.pdf": SimpleNamespace(doc_type="birth_certificate", confidence=0.9)}, fields, graph=g)
    (record,) = built["documents"]
    assert record["person"] == "child_1" and (record["language"], record["language_basis"]) == ("unknown", "unknown")


PASSPORT_ISSUED = ["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE PASSPORT", "DATA DE EXPEDICAO / DATE OF ISSUE    VALIDADE / DATE OF EXPIRY",
                   "11 JAN/JAN 2022    10 JAN/JAN 2032", *_mrz()]


def test_every_readers_dates_reach_the_record():
    import documents
    from extract.base import ExtractedField as F

    read = documents.read_dates
    passport = read("passport", [], "\n".join(PASSPORT_ISSUED))
    assert passport == {"issued": "2022-01-11", "expires": "2032-01-10", "issued_kind": "issued", "expires_kind": "expires"}  # the MRZ in the text
    assert read("passport", [F("folder.passport.XX0001234", "x", "BRAZIL|2032-01-10", 0.95)], "")["expires"] == "2032-01-10"
    certificate = read("birth_certificate", [], "CERTIDAO DE NASCIMENTO\nNOME DA REGISTRADA: ANA\nDATA DO REGISTRO: 20/03/2006")
    assert (certificate["issued"], certificate["issued_kind"]) == ("2006-03-20", "registered")  # day first: a Portuguese document
    issued = read("birth_certificate", [], "CERTIDAO DE NASCIMENTO\nDATA DO REGISTRO: 20/03/2006\nData de emissão: 10 de janeiro de 2022")
    assert (issued["issued"], issued["issued_kind"]) == ("2022-01-10", "issued")  # the copy's issue date first
    assert read("birth_certificate", [], "ACTA DE NACIMIENTO\nNOMBRE DEL INSCRITO\nFECHA DE EXPEDICIÓN: 03/02/2020")["issued"] == "2020-02-03"
    assert read("birth_certificate", [], "EXTRAIT DES ARCHIVES\nDélivré le 5 mai 2019")["issued"] == "2019-05-05"
    assert read("birth_certificate", [], "DATA DE EMISSAO: 01/01/2999")["issued"] is None  # never a date in the future
    notice = read("i360_approval", [F("folder.uscis_case.IOE0999000123", "IOE0999000123", "I-360 APPROVAL (Special Immigrant Juvenile), 2025-08-20", 0.9)])
    assert (notice["issued"], notice["issued_kind"]) == ("2025-08-20", "notice")  # the case line's own date
    valid = read("uscis_notice", [F("folder.notice.IOE1.approval_20250101.date", "x", "2025-01-01", 0.85),
                                  F("folder.notice.IOE1.approval_20250101.valid_to", "x", "2027-01-01", 0.85)])
    assert valid == {"issued": "2025-01-01", "expires": "2027-01-01", "issued_kind": "notice", "expires_kind": "valid_to"}
    i94 = read("i94", [F("applicant.i94_arrival_date", "x", "2019-07-15", 0.9), F("applicant.i94_admit_until_date", "x", "2020-01-14", 0.9)])
    assert (i94["issued_kind"], i94["expires_kind"]) == ("arrived", "admit_until")
    assert read("sij_order", [F("sij.order_date", "x", "2026-08-20", 0.75)])["issued_kind"] == "order"
    assert documents.dates([F("applicant.ead_expiration_date", "x", "2027-05-01", 0.98)]) == (None, "2027-05-01")  # the old call still answers


def test_not_read_yet_is_not_none_on_it(tmp_path, folder_run):
    import documents

    ssn = folder_run["by"]["ssn.pdf"]
    assert ssn["dates_read"] is True and ssn["issued"] is None and ssn["expires"] is None  # read: nothing on it
    assert documents.type_info("ssn_card")["has_dates"] is False  # and none to find: the screen says so
    d = _made_case(tmp_path, {"lease.pdf": ("lease", None)})
    assert documents.by_doc(d)["lease.pdf"]["dates_read"] is False  # no reader ran on a blank scan: "Not read yet"


def _page_image(blur: float = 0.0):
    import pypdfium2 as pdfium
    from PIL import ImageFilter
    from portal.demo import document_pdf

    pdf = pdfium.PdfDocument(document_pdf(PASSPORT))
    page = pdf[0]
    image = page.render(scale=1000 / page.get_width()).to_pil().convert("L")
    pdf.close()
    return image.filter(ImageFilter.GaussianBlur(blur)) if blur else image


def test_quality_is_measured_sharp_against_blurred_from_the_same_page(tmp_path):
    import documents
    from PIL import Image
    from portal import demo

    text = "\n".join(PASSPORT)
    sharp = documents.measure_quality(_page_image(), text=text)
    assert sharp["quality"] == "readable" and sharp["sharpness"] >= documents.SHARP and sharp["contrast"] >= documents.CONTRAST
    assert sharp["said"].startswith("sharp, good contrast, ") and sharp["said"].endswith(" words read")
    soft = documents.measure_quality(_page_image(1.5), text=text)
    assert soft["quality"] == "check" and "a little soft" in soft["said"]
    blurred = documents.measure_quality(_page_image(3), text=text)
    assert blurred["quality"] == "blurry" and blurred["said"].startswith("blurry")
    assert sharp["sharpness"] > soft["sharpness"] > blurred["sharpness"]
    tilted = documents.measure_quality(_page_image().rotate(6, fillcolor=255), text=text)
    assert tilted["quality"] == "check" and "tilted 6 degrees" in tilted["said"]
    assert "tilted 12 degrees" in documents.measure_quality(_page_image().rotate(12, fillcolor=255), text=text)["said"]  # looked for out to 15
    blank = documents.measure_quality(Image.new("L", (1000, 1294), 255), text="")
    assert blank["quality"] == "check" and blank["said"].startswith("nothing printed")
    few = documents.measure_quality(_page_image(), text="PASSAPORTE")
    assert few["quality"] == "check" and "only 1 word read" in few["said"]
    photo = tmp_path / "photo.png"
    photo.write_bytes(demo.blurry_photo())  # the showcase's out-of-focus passport photo
    assert documents.measure_quality(photo, expects_text=False)["quality"] == "blurry"
    assert documents.measure_quality(tmp_path / "gone.pdf") is None  # nothing to look at: the old rule stands


def test_a_faint_photo_read_in_full_is_a_check_never_a_retake(tmp_path, monkeypatch):
    """Verification 4: a faint, blurred photo of an I-94 the reader classified and read 42 words from measures "blurry" but is
    kept at "check" (a person looks; the client is not asked); a photo nothing was read from stays "blurry"."""
    from types import SimpleNamespace

    import documents
    from portal.engine import RETAKE_REASONS

    faint = _page_image(3).point(lambda v: 150 + v * 0.35)
    faint.save(tmp_path / "i94.png")
    faint.rotate(1, fillcolor=255).save(tmp_path / "blank.png")  # another photo as faint, nothing read from it
    text = "\n".join(I94)
    assert documents.measure_quality(tmp_path / "i94.png", text=text)["quality"] == "blurry"  # the picture alone
    built = documents.build(tmp_path, {"i94.png": SimpleNamespace(doc_type="i94", confidence=0.9),
                                       "blank.png": SimpleNamespace(doc_type="i94", confidence=0.9)}, {}, texts={"i94.png": text, "blank.png": ""}, ocr=False)
    by = {r["files"][0]: r for r in built["documents"]}
    assert by["i94.png"]["quality_measures"]["words"] >= documents.ENOUGH_WORDS
    assert (by["i94.png"]["quality"], by["i94.png"]["quality_basis"]) == ("check", "measured") and "check" not in RETAKE_REASONS
    assert "the client is not asked" in by["i94.png"]["quality_measures"]["said"]
    assert by["blank.png"]["quality"] == "blurry"  # nothing read: the photo is asked for again, as before
    # Tesseract's score is said in words, never as a number
    monkeypatch.setattr(documents, "_ocr_confidence", lambda image: 92)
    said = documents.measure_quality(_page_image(), text="PASSAPORTE REPUBLICA FEDERATIVA BRASIL NOME DATA", ocr=True)["said"]
    assert "the words read clearly" in said and "%" not in said


def test_the_record_keeps_the_estimate_and_never_over_a_persons_word(folder_run, tmp_path):
    import shutil

    import documents

    passport = folder_run["by"]["passport.pdf"]
    assert (passport["quality"], passport["quality_basis"]) == ("readable", "measured")
    assert passport["quality_measures"]["said"].startswith("sharp, good contrast")
    out = tmp_path / "bundle"
    shutil.copytree(folder_run["out"], out)
    documents.set_quality(out, passport["id"], "check", "Paulo Paralegal", "paralegal")
    mine = next(r for r in documents.load(out, measure=True)["documents"] if r["id"] == passport["id"])
    assert (mine["quality"], mine["quality_basis"]) == ("check", "set_by_person")


def test_a_passport_shows_an_entry_only_with_a_stamp_or_a_visa():
    import documents

    assert documents.found_roles("passport", "\n".join(PASSPORT)) == {}
    stamp = "\n".join(PASSPORT + ["U.S. DEPARTMENT OF HOMELAND SECURITY", "ADMITTED", "JUL 15 2019", "CLASS B2 UNTIL JAN 14 2020"])
    assert documents.found_roles("passport", stamp) == {"entry": "a U.S. admission stamp"}
    assert documents.found_roles("passport", "\n".join(PASSPORT + ["CLASS B2 15JUL2019"])) == {"entry": "a U.S. admission stamp"}
    visa = "\n".join(PASSPORT + ["VNUSAEXEMPLO<SOUZA<<ANA<CLARA<<<<<<<<<<<<<<<<<<"])
    assert documents.found_roles("passport", visa) == {"entry": "a U.S. visa"}
    assert documents.found_roles("i94", stamp) == {}
    record = documents._with_roles({"type": "passport", "found": {"entry": "a U.S. admission stamp"}, "tags": []})
    assert record["roles"] == ["identity", "nationality", "entry"]
    assert "nationality" in documents.roles() and documents.type_info("passport")["roles"] == ["identity", "nationality"]


def test_the_role_audit_of_the_taxonomy():
    import documents

    t = documents.type_info
    assert t("i94")["roles"] == ["entry"] and t("marriage_certificate")["roles"] == ["relationship"]
    assert t("green_card")["roles"] == ["identity", "status"] and t("us_passport")["roles"] == ["identity", "nationality"]
    assert t("passport_photo")["roles"] == ["photo"] and "entry" not in t("i590_approval")["roles"]
    taxonomy = json.loads(schema_path.path("register", "document_types").read_text(encoding="utf-8"))
    assert set(taxonomy["role_uses"]) == set(taxonomy["roles"]) and set(taxonomy["qualities"]) == set(documents.QUALITIES)
    for spec in taxonomy["types"]:
        assert spec.get("language") in (None, "en") and spec.get("language_by") in (None, "country"), spec["id"]
        assert not (spec.get("language") and spec.get("language_by")), spec["id"]
    countries = json.loads(documents.COUNTRY_LANGUAGES.read_text(encoding="utf-8"))["countries"]
    for code, c in countries.items():
        assert re.fullmatch(r"[A-Z]{3}", code) and c["language"] in ("pt", "es", "fr", "en") and c["name"] and c["names"], code


def test_the_demo_clients_five_documents_say_whose_language_dates_and_quality(tmp_path):
    """What the owner looked at: the demo's passport, birth certificate, I-360 approval, I-94 and Social Security card, built by the
    pipeline -- and the same case read without documents.json (a case processed before the record existed)."""
    import documents
    from portal import demo
    from portal.store import PortalStore

    demo.seed(PortalStore(tmp_path / "portal"), tmp_path / "clients")
    d = tmp_path / "clients" / "demo-ana"
    expected = {
        "passport": ("applicant", "named", "pt", "text", "2022-01-11", "2032-01-10", ["identity", "nationality"]),
        "birth_certificate": ("applicant", "named", "pt", "text", "2023-05-15", None, ["identity", "relationship"]),
        "i360_approval": ("applicant", "named", "en", "type", "2025-08-20", None, ["notice"]),
        "i94": ("applicant", "named", "en", "type", "2019-07-15", "2020-01-14", ["entry"]),
        "ssn_card": ("applicant", "only_person", "en", "type", None, None, ["identity"]),
    }
    for built in (True, False):
        if not built:
            (d / "documents.json").unlink()
        records = {r["type"]: r for r in documents.load(d, measure=True)["documents"]}
        got = {k: (r["person"], r["person_basis"], r["language"], r["language_basis"], r["issued"], r["expires"], r["roles"]) for k, r in records.items()}
        assert got == expected, (built, got)
        for r in records.values():
            assert (r["quality"], r["quality_basis"]) == ("readable", "measured") and r["quality_measures"]["said"].startswith("sharp, good contrast"), r
            assert r["dates_read"] is True


def test_the_showcase_keeps_its_processed_documents_beside_the_blurry_photo(tmp_path):
    from datetime import date

    import documents
    from portal import demo
    from portal.store import PortalStore

    demo.showcase(PortalStore(tmp_path / "portal"), tmp_path / "clients", process=True, today=date(2026, 10, 2))
    records = documents.load(tmp_path / "clients" / "demo-bia", measure=True)["documents"]
    assert sorted(r["type"] for r in records) == ["birth_certificate", "i360_approval", "passport"]
    photo = next(r for r in records if r["type"] == "passport")
    assert photo["quality"] == "blurry" and photo["quality_measures"]["said"].startswith("blurry")
    certificate = next(r for r in records if r["type"] == "birth_certificate")
    assert (certificate["person"], certificate["language"], certificate["issued"]) == ("applicant", "pt", "2023-05-15")
def test_travel_history_has_review_label_without_ownership_or_status_inference():
    import documents
    from batch import process_documents
    text = "Travel History Results\nRow Date Type Location\n1 2026-01-01 Arrival BOS\nName: FICTIONAL PERSON\nClass of Admission: B2"
    result = process_documents("fictional-history", [("history.pdf", text)])
    record = documents.type_info("travel_history")
    assert documents.name("travel_history") == "Travel history"
    assert record["person_from"] == "reviewer" and record["read_beyond_type"] is False
    assert "status" not in record["roles"] and "entry" not in record["roles"]
    assert not any(f.sources and any(s.doc_id == "history.pdf" for s in f.sources) for f in result.graph.all_facts().values())


@pytest.mark.parametrize("legacy_basis", [None, "named", "identifiers", "only_person"])
def test_travel_history_loaded_record_requires_explicit_reviewer_ownership(tmp_path, legacy_basis):
    import documents
    text = ["Travel History Results", "Row Date Type Location", "1 2026-01-01 Arrival BOS"]
    case = _made_case(tmp_path, {"history.pdf": ("travel_history", text), "latest.pdf": ("i94", I94)},
                      [("applicant.given_name", *Q, "ANA CLARA"), ("applicant.family_name", *Q, "EXEMPLO SOUZA"),
                       ("applicant.marital_status", *Q, "Single"), ("applicant.total_children", *Q, "0"),
                       ("applicant.given_name", "latest.pdf", "i94", "ANA CLARA"),
                       ("applicant.family_name", "latest.pdf", "i94", "EXEMPLO SOUZA"),
                       ("applicant.travel_document_number", *Q, "XX0001234"),
                       ("applicant.travel_document_number", "history.pdf", "travel_history", "XX0001234")])
    records = documents.load(case)
    history = next(record for record in records["documents"] if record["type"] == "travel_history")
    assert (history["person"], history["person_basis"]) == ("unknown", "unknown")
    assert next(record for record in records["documents"] if record["type"] == "i94")["person"] == "applicant"
    if legacy_basis is not None:
        history.update(person="applicant", person_basis=legacy_basis, person_set_by=None)
    else:
        history.update(person="applicant", person_set_by=None)
        history.pop("person_basis", None)
    documents.save(case, records)
    current = documents.by_doc(case)["history.pdf"]
    assert (current["person"], current["person_basis"]) == ("unknown", "unknown")
    documents.set_person(case, history["id"], "applicant", "Fictional Reviewer", "paralegal")
    current = documents.by_doc(case)["history.pdf"]
    assert (current["person"], current["person_basis"]) == ("applicant", "set_by_person")
    assert current["person_set_by"]["who"] == "Fictional Reviewer"
