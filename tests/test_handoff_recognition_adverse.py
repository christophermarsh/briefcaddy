"""Retained external independent probes, copied for source-hashed final review.

Original evidence stays unchanged under tmp/recognition-followup-32245d3348d14e8cba641eea5526057c/.
Only the OCR timeout comparison now permits the remaining total request budget;
the real subprocess timeout, elapsed bound and cross-process access checks remain.
"""
# ruff: noqa: F811 -- canonical isolated fixtures are injected by pytest
import json
import os
import subprocess
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest
from assignment_route_fixtures import app, call, server, sign_in, world  # noqa: F401
from synthetic_documents import process_retained_documents

import documents
import restricted
from classify.ocr import OcrWord
from extract.birth_certificate import _filiation_entries, _merge_versions
from portal.demo import document_pdf
from review.evidence import annotate, quote_line_region
from review.state import save_bundle


@pytest.mark.parametrize("heading", ["FILIACAO", "FILIATION", "FILIAÇÃO"])
def test_independent_witness_rows_before_empty_heading(heading):
    text = "TESTEMUNHAS\nPEDRO MONTE, natural de Curitiba - PR\nLUCIA RAMOS, natural de Londrina - PR\n" + heading + "\nAVOS"
    assert _filiation_entries(text) == []


@pytest.mark.parametrize("pair", [
    [("PEDRO MONTE", "CURITIBA, PARANA"), ("LUCIA RAMOS", "")],
    [("PEDRO MONTE", ""), ("PEDRO NONTE", "")],
])
def test_independent_fuzzy_parent_pairing_cannot_reuse_or_choose(pair):
    assert _merge_versions([[("PEDRO MONTE", ""), ("PEDRO PONTE", "")], pair]) == []


def test_independent_unique_reversed_parent_version():
    assert _merge_versions([[("PEDRO MONTE", ""), ("LUCIA RAMOS", "")],
                            [("LUCIA RAMOS", "LONDRINA, PARANA"), ("PEDRO MONTE", "CURITIBA, PARANA")]]) == [
                                ("PEDRO MONTE", "CURITIBA, PARANA"), ("LUCIA RAMOS", "LONDRINA, PARANA")]


@pytest.mark.parametrize("separator", [[], [OcrWord("Other", 30, 50, 30, 12, 95, (1, 1, 2))]])
def test_independent_wrapped_second_occurrence_refuses_crop(separator):
    words = [OcrWord("PEDRO", 20, 20, 50, 12, 95, (1, 1, 1)),
             OcrWord("MONTE", 80, 20, 50, 12, 95, (1, 1, 1)), *separator,
             OcrWord("PEDRO", 20, 80, 50, 12, 95, (1, 1, 3)),
             OcrWord("MONTE", 20, 100, 50, 12, 95, (1, 1, 4))]
    assert quote_line_region(words, "PEDRO MONTE", 600, 800) is None


@pytest.mark.parametrize("table", ["Row\nDate\nType\nLocation", "Date Type Location\nNo travel records found", "Row Date Type Location\n1 2025-05-08 Departure JFK"])
def test_independent_history_masthead_empty_and_single_event(table):
    from batch import process_documents
    from classify.classifier import classify_text
    text = "10/5/26, 9:22 AM I-94/I-95 Official Website\nTravel History Results\n" + table + "\nMost Recent I-94\nAdmission (I-94) Record Number: 12345678901\nFirst (Given) Name: PEDRO\nLast/Surname: MONTE\nClass of Admission: B2"
    assert classify_text(text).doc_type == "travel_history"
    result = process_documents("independent-fictional", [("history.pdf", text)])
    assert not any(any(source.doc_id == "history.pdf" for source in fact.sources) for fact in result.graph.all_facts().values())
    records = [{"type": "travel_history", "person": "applicant", "person_basis": "named", "doc_ids": ["history.pdf"]}]
    documents.infer_people(Path("fictional-unused"), records, result.graph)
    assert records[0]["person"] == "unknown"
    records[0].update(person="spouse", person_set_by={"who": "Independent Fictional Reviewer"})
    documents.infer_people(Path("fictional-unused"), records, result.graph)
    assert records[0]["person"] == "spouse" and records[0]["person_basis"] == "set_by_person"


@pytest.mark.parametrize("change", ["none", "source", "source_acl", "source_session"])
def test_independent_actual_timeout_cross_process_gate_and_release_checks(server, world, app, monkeypatch, tmp_path, change):
    """A real child sleeps until the advertised 10-second OCR deadline expires."""
    from classify import ocr
    from portal.communication_consent import data_gate
    folder = tmp_path / "retained-independent"
    text = "Most Recent I-94\nAdmission (I-94) Record Number: 12345678901\nLast/Surname: MONTE\nFirst (Given) Name: PEDRO\nBirth Date: 01/02/2000"
    result = process_retained_documents("case-rosa", folder, [("independent.pdf", text)], pages={"independent.pdf": [text]})
    save_bundle(result, world / "case-rosa", folder)
    restricted.name_person(world / "case-rosa", "jane@firm.example", True, "Fictional Attorney", "attorney", "Jane Doe")
    jane = sign_in(server, "jane@firm.example")
    digest = documents.read(world / "case-rosa")["boundary_plans"]["independent.pdf"]["source_sha256"]
    started = threading.Event()
    observed = []
    run = subprocess.run

    def slow_subprocess(command, **kwargs):
        if command[0] != "independent-synthetic-tesseract":
            return run(command, **kwargs)
        assert 0 < kwargs["timeout"] <= 10
        observed.append(kwargs["timeout"])
        started.set()
        return run([sys.executable, "-c", "import time; time.sleep(60)"], **kwargs)

    def items(client, user):
        payload = {"sources": [{"doc": "independent.pdf", "page": 0, "raw": "PEDRO MONTE"}],
                   "evidence_fingerprints": {"independent": "unchanged"}}
        annotate(world / client, payload)
        return payload

    monkeypatch.setattr(app, "items", items)
    monkeypatch.setattr(ocr, "find_tesseract", lambda: "independent-synthetic-tesseract")
    monkeypatch.setattr(ocr.subprocess, "run", slow_subprocess)
    marker = tmp_path / "os-gate-acquired.txt"
    worker_code = "from pathlib import Path; from portal.communication_consent import data_gate; import sys;\nwith data_gate(Path(sys.argv[1])):\n Path(sys.argv[2]).write_text('acquired', encoding='utf-8')\n"
    start = time.monotonic()
    with ThreadPoolExecutor(max_workers=1) as pool:
        response = pool.submit(call, server + "/api/items?client=case-rosa", jane)
        assert started.wait(8), "optional OCR child did not begin"
        worker = subprocess.Popen([sys.executable, "-c", worker_code, str(app.data_root.parent), str(marker)], stdout=subprocess.PIPE, stderr=subprocess.PIPE, env=os.environ.copy())
        try:
            out, err = worker.communicate(timeout=4)
        except BaseException:
            worker.kill(); worker.communicate(); raise
        assert worker.returncode == 0, (out, err)
        assert marker.read_text(encoding="utf-8") == "acquired", "OS communication gate held by OCR"
        with data_gate(app.data_root.parent):
            if change.startswith("source"):
                (folder / "independent.pdf").write_bytes(document_pdf(["INDEPENDENT CHANGED FICTIONAL SOURCE"]))
            if change == "source_acl":
                restricted.name_person(world / "case-rosa", "jane@firm.example", False, "Fictional Attorney", "attorney", "Jane Doe")
            if change == "source_session":
                app.accounts.sign_out(jane.split("=", 1)[1])
        status, raw = response.result(timeout=18)
    elapsed = time.monotonic() - start
    assert len(observed) == 1 and 0 < observed[0] <= 10 and 9 <= elapsed < 20
    if change == "source_acl":
        assert status == 404 and json.loads(raw) == {"error": "unknown client"}
    elif change == "source_session":
        assert status == 401 and json.loads(raw)["sign_in"] is True
    else:
        assert status == 200
        payload = json.loads(raw)
        assert payload["evidence_fingerprints"] == {"independent": "unchanged"}
        location = payload["sources"][0]["source_location"]
        assert location["region"] is None
        assert location["state"] == ("unavailable" if change == "source" else "current")
        if change == "source":
            for endpoint in ["/api/file?client=case-rosa&doc=independent.pdf", "/api/crop?client=case-rosa&doc=independent.pdf&page=0"]:
                assert call(server + endpoint + "&expected_sha256=" + digest, jane)[0] == 404
