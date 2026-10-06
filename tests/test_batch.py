"""batch.py orchestration. process_documents is exercised directly with
(doc_id, text) pairs -- same reasoning as classify_text vs
classify_document: no PDF fixture needed to test the actual orchestration
logic. process_client_folder/run_batch get a thin filesystem-level check
that they read real files and don't blow up on a bad one. finalize_client
tests use the real schemas/forms/i485/template.pdf and i485_field_map.json --
the actual stage 5-7 handoff this pipeline will really use."""

from pathlib import Path


from batch import finalize_client, process_client_folder, process_documents, run_batch
from fill import load_field_map
from rules import ALL_RULES
import schema_path

_REPO_ROOT = Path(__file__).resolve().parent.parent
_REAL_TEMPLATE = schema_path.path("template", "i485")
_REAL_FIELD_MAP = schema_path.path("field_map", "i485")


def test_process_documents_classifies_extracts_and_builds_one_graph():
    documents = [
        ("i94.pdf", "Admission (I-94) Record Number: 14335150685\nClass of Admission: B2\nU.S. Customs and Border Protection"),
        ("ssn.pdf", "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"),
    ]
    result = process_documents("maria_eduarda", documents)

    assert result.classifications["i94.pdf"].doc_type == "i94"
    assert result.classifications["ssn.pdf"].doc_type == "ssn_card"

    i94_fact = result.graph.get("applicant.i94_number")
    assert i94_fact.value == "14335150685"
    assert i94_fact.sources[0].doc_id == "i94.pdf"

    ssn_fact = result.graph.get("applicant.ssn")
    assert ssn_fact.value == "123-45-6789"
    assert ssn_fact.sources[0].doc_id == "ssn.pdf"


def test_process_documents_records_unclassified_but_does_not_extract():
    documents = [("mystery.pdf", "This is a grocery list. Milk, eggs, bread.")]
    result = process_documents("maria_eduarda", documents)

    assert result.classifications["mystery.pdf"].doc_type == "unclassified"
    assert result.graph.all_facts() == {}


def test_process_documents_two_docs_agreeing_stay_resolved_not_conflict():
    # Two I-94-shaped docs both reporting the same admission number should
    # resolve, not conflict -- mirrors tests/test_factgraph.py's
    # agreeing-sources case.
    text = "Admission (I-94) Record Number: 14335150685\nU.S. Customs and Border Protection\nClass of Admission: B2\n"
    documents = [("i94_copy1.pdf", text), ("i94_copy2.pdf", text)]
    result = process_documents("maria_eduarda", documents)
    fact = result.graph.get("applicant.i94_number")
    assert fact.status == "resolved"
    assert len(fact.sources) == 2


def test_process_documents_does_not_run_rules_by_default():
    # Rules are opt-in (see batch.py's module docstring): neither rule is
    # attorney-approved to run at volume yet, so a bare call must not
    # derive Part 9 answers even when the inputs are present.
    documents = [
        ("i94.pdf", "Admission (I-94) Record Number: 14335150685\nU.S. Customs and Border Protection\nAdmit Until Date: 02/21/2017\n"),
        ("i360.pdf", "I-797, NOTICE OF ACTION\nReceipt Number: WAC1234567890\nU.S. CITIZENSHIP AND IMMIGRATION SERVICES\nCase Type: I360\nPriority Date: 10/14/2022\n"),
    ]
    result = process_documents("maria_eduarda", documents)
    assert result.graph.get("applicant.part9.violated_nonimmigrant_status") is None


def test_process_documents_runs_rules_when_explicitly_passed():
    documents = [
        ("i94.pdf", "Admission (I-94) Record Number: 14335150685\nU.S. Customs and Border Protection\nAdmit Until Date: 02/21/2017\n"),
        ("i360.pdf", "I-797, NOTICE OF ACTION\nReceipt Number: WAC1234567890\nU.S. CITIZENSHIP AND IMMIGRATION SERVICES\nCase Type: I360\nPriority Date: 10/14/2022\n"),
    ]
    result = process_documents("maria_eduarda", documents, rules=ALL_RULES)
    fact = result.graph.get("applicant.part9.violated_nonimmigrant_status")
    assert fact is not None
    assert fact.value == "Yes"


def test_process_documents_empty_folder_produces_empty_graph():
    result = process_documents("maria_eduarda", [])
    assert result.graph.all_facts() == {}
    assert result.classifications == {}


def test_process_client_folder_reads_real_pdfs_from_disk(tmp_path):
    from pypdf import PdfWriter

    folder = tmp_path / "maria_eduarda"
    folder.mkdir()
    writer = PdfWriter()
    writer.add_blank_page(width=200, height=200)
    with open(folder / "blank.pdf", "wb") as fh:
        writer.write(fh)

    result = process_client_folder("maria_eduarda", folder)

    # A blank page extracts to empty text -> unclassified, not a crash.
    assert result.classifications["blank.pdf"].doc_type == "unclassified"
    assert result.errors == {}


def test_process_client_folder_records_unreadable_pdf_as_an_error_not_a_crash(tmp_path):
    folder = tmp_path / "maria_eduarda"
    folder.mkdir()
    (folder / "corrupt.pdf").write_bytes(b"this is not a real pdf")

    result = process_client_folder("maria_eduarda", folder)

    assert "corrupt.pdf" in result.errors
    assert "corrupt.pdf" not in result.classifications


def test_run_batch_processes_every_client_and_saves_a_fact_graph(tmp_path):
    clients_root = tmp_path / "clients"
    out_root = tmp_path / "out"
    for client_id in ("client_a", "client_b"):
        (clients_root / client_id).mkdir(parents=True)

    results = run_batch(clients_root, out_root)

    assert set(results.keys()) == {"client_a", "client_b"}
    assert (out_root / "client_a" / "fact_graph.json").exists()
    assert (out_root / "client_b" / "fact_graph.json").exists()


def test_run_batch_one_bad_client_does_not_stop_the_others(tmp_path, monkeypatch):
    clients_root = tmp_path / "clients"
    out_root = tmp_path / "out"
    (clients_root / "good_client").mkdir(parents=True)
    (clients_root / "bad_client").mkdir(parents=True)

    import batch as batch_module

    real_process = batch_module.process_client_folder

    def flaky_process(client_id, folder, **kwargs):
        if client_id == "bad_client":
            raise RuntimeError("simulated folder corruption")
        return real_process(client_id, folder, **kwargs)

    monkeypatch.setattr(batch_module, "process_client_folder", flaky_process)

    results = run_batch(clients_root, out_root)

    assert "__client__" in results["bad_client"].errors
    assert (out_root / "bad_client" / "fact_graph.json").exists()
    assert (out_root / "good_client" / "fact_graph.json").exists()


def test_finalize_client_fills_validates_and_reports(tmp_path):
    result = process_documents(
        "maria_eduarda",
        [("ssn.pdf", "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION")],
    )
    field_map = load_field_map(_REAL_FIELD_MAP)

    filing = finalize_client(
        result,
        template_path=_REAL_TEMPLATE,
        field_map=field_map,
        required_fact_keys=["applicant.ssn", "applicant.i94_number"],
        output_dir=tmp_path,
    )

    assert filing.filled_pdf_path.exists()
    assert "applicant.ssn" not in filing.unmapped_facts  # it was mapped and filled
    # applicant.i94_number was never attempted for this client -> blocking.
    assert any(f.level == "blocking" and f.fact_key == "applicant.i94_number" for f in filing.flags)

    from pypdf import PdfReader

    filled = PdfReader(filing.filled_pdf_path).get_fields()
    # SSN field is /MaxLen 9 -- digits only, hyphens stripped
    # (schemas/forms/i485/field_map.json's "digits_only" type).
    assert filled["form1[0].#subform[3].Pt1Line19_SSN[0]"]["/V"] == "123456789"


def test_firm_profile_fills_attorney_preparer_and_mailing_fields(tmp_path, monkeypatch):
    import settings
    from batch import load_firm_profile
    from conftest import save_shipped_office_as_the_firms

    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    save_shipped_office_as_the_firms(tmp_path / "settings.json")  # Implementation note.

    profile = load_firm_profile(schema_path.path("firm", "firm_profile"))
    result = process_documents("maria_eduarda", [], firm_profile=profile)

    fact = result.graph.get("firm.attorney_bar_number")
    assert fact.value == "DEMO-000000"
    assert fact.sources[0].doc_type == "firm_profile"

    filing = finalize_client(
        result,
        template_path=_REAL_TEMPLATE,
        field_map=load_field_map(_REAL_FIELD_MAP),
        required_fact_keys=[],
        output_dir=tmp_path,
    )
    assert filing.unmapped_facts == []

    from pypdf import PdfReader

    filled = PdfReader(filing.filled_pdf_path).get_fields()
    assert filled["form1[0].#subform[0].AttorneyStateBarNumber[0]"]["/V"] == "DEMO-000000"
    assert filled["form1[0].#subform[0].CheckBox1[0]"]["/V"] == "/1"
    assert filled["form1[0].#subform[23].Pt12Line2_BusinessName[0]"]["/V"] == "Example Immigration Office"
    assert filled["form1[0].#subform[2].Pt1Line18_CurrentZipCode[0]"]["/V"] == "02110"
    assert filled["form1[0].#subform[2].Pt1Line18_YN[1]"]["/V"] == "/N"


def test_run_batch_with_finalize_writes_filled_pdf_and_flag_report(tmp_path):
    clients_root = tmp_path / "clients"
    out_root = tmp_path / "out"
    (clients_root / "maria_eduarda").mkdir(parents=True)

    field_map = load_field_map(_REAL_FIELD_MAP)
    results = run_batch(
        clients_root,
        out_root,
        finalize={
            "template_path": _REAL_TEMPLATE,
            "field_map": field_map,
            "required_fact_keys": [],
        },
    )

    assert "__finalize__" not in results["maria_eduarda"].errors
    assert (out_root / "maria_eduarda" / "i485_filled.pdf").exists()
    assert (out_root / "maria_eduarda" / "flag_report.txt").exists()
    report_text = (out_root / "maria_eduarda" / "flag_report.txt").read_text()
    assert "Flag report for maria_eduarda" in report_text


def test_questionnaire_answers_become_tier3_facts_and_unread_items_become_review_flags(tmp_path):
    from questionnaire import QuestionnaireReading
    from questionnaire.checkboxes import ChoiceReading
    from questionnaire.reader import QuestionAnswer

    calls = []

    def fake_reader(doc_id):
        calls.append(doc_id)
        reading = QuestionnaireReading()
        reading.answers.append(
            QuestionAnswer("part9_q3", "applicant.part9.worked_without_authorization", ChoiceReading("No", [], "test"))
        )
        reading.unread["part9_q9"] = "no box clearly darker than the others"
        return reading

    documents = [
        ("questionnaire.pdf", "Questionário para Ajuste de Status\nI-485 - SIJS\n"),
        ("ssn.pdf", "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"),
    ]
    result = process_documents("maria_eduarda", documents, questionnaire_reader=fake_reader)

    assert calls == ["questionnaire.pdf"]  # only the questionnaire is read for checkboxes
    fact = result.graph.get("applicant.part9.worked_without_authorization")
    assert fact.value == "No"
    assert fact.tier == 3

    filing = finalize_client(
        result,
        template_path=_REAL_TEMPLATE,
        field_map=load_field_map(_REAL_FIELD_MAP),
        required_fact_keys=[],
        output_dir=tmp_path,
    )
    assert "part9_q9" in filing.report
    assert any(f.level == "review" and f.fact_key == "applicant.part9.worked_without_authorization" for f in filing.flags)

    from pypdf import PdfReader

    filled = PdfReader(filing.filled_pdf_path).get_fields()
    assert filled["form1[0].#subform[13].Pt9Line12_YesNo[1]"]["/V"] == "/N"


def test_sijs_with_marriage_certificate_is_blocking():
    from batch import eligibility_alerts
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("applicant.i360_receipt_number", "i360.pdf", "i360_approval", "X", "MSC0000000000", 0.99)
    g.add_source("applicant.marriage_date", "m.pdf", "marriage_certificate", "x", "2025-06-01", 0.9)
    flags = eligibility_alerts(g)
    assert [f.level for f in flags] == ["blocking"]
    assert "unmarried" in flags[0].message


def test_client_statement_contradicting_a_document_is_flagged():
    from batch import cross_check
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("applicant.i94_arrival_date", "i94.pdf", "i94", "x", "2016-11-24", 0.9)
    g.add_source("applicant.last_arrival_date_self_reported", "q.pdf", "intake_questionnaire", "x", "2016-11-23", 0.7, tier=3)
    flags = cross_check(g)
    assert any("11/23/2016" in f.message and "11/24/2016" in f.message and "applicant." not in f.message for f in flags)


def test_overlong_value_is_left_blank_and_flagged_not_fatal(tmp_path):
    from factgraph import FactGraph

    g = FactGraph("t")
    g.add_source("applicant.physical_street", "q.pdf", "intake_questionnaire", "x", "42 EXAMPLE ST APT 2 MARLBOROUGH MA 01752 USA", 0.7, tier=3)
    g.add_source("applicant.ssn", "s.pdf", "ssn_card", "x", "123-45-6789", 0.97)
    from batch import ClientResult

    filing = finalize_client(ClientResult(graph=g), _REAL_TEMPLATE, load_field_map(_REAL_FIELD_MAP), [], tmp_path)
    assert any("LEFT BLANK" in f.message and "longer than the form allows" in f.message for f in filing.flags)
    from pypdf import PdfReader

    assert PdfReader(filing.filled_pdf_path).get_fields()["form1[0].#subform[3].Pt1Line19_SSN[0]"]["/V"] == "123456789"


def test_blank_questionnaire_template_is_one_blocking_flag():
    from questionnaire import QuestionnaireReading

    documents = [("q.pdf", "Questionário para Ajuste de Status\nI485 – SIJS\n")]
    result = process_documents("t", documents, questionnaire_reader=lambda doc_id: QuestionnaireReading(blank_template=True))
    reading = [f for f in result.review_flags if f.kind != "missing"]  # "ask the client" items are separate
    assert [f.level for f in reading] == ["blocking"]
    assert "BLANK questionnaire" in reading[0].message
