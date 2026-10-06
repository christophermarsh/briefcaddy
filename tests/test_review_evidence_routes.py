"""Current ACL and source identity are enforced before preview-cache reuse."""
import json
from urllib.parse import quote

import pytest

import documents
import restricted
import subject_attribution as subjects
from assignment_route_fixtures import world, app, server, call, sign_in  # noqa: F401 -- pytest fixture registration and helper reexports
from review.state import save_bundle
from synthetic_documents import process_retained_documents
from portal.demo import document_pdf

TEXT = "Most Recent I-94\nAdmission (I-94) Record Number: 11111111111\nLast/Surname: SAMPLE\nFirst (Given) Name: ALPHA\nBirth Date: 01/02/2000"


@pytest.fixture(autouse=True)
def controls(tmp_path, monkeypatch):
    for key, file in {"I485_EVENTS": "events.jsonl", "I485_JOBS": "jobs", "I485_ROSTER": "roster.json"}.items():
        monkeypatch.setenv(key, str(tmp_path / file))
    monkeypatch.setenv("I485_WALK_EVERY", "600")


def retain(world, case):  # noqa: F811 -- pytest fixture injection
    folder = world.parent / "originals" / case
    result = process_retained_documents(case, folder, [("i94.pdf", TEXT)], pages={"i94.pdf": [TEXT]})
    save_bundle(result, world / case, folder)
    row = subjects.views(world / case)[0]
    person = documents.read(world / case)["case_subjects"]["people"][0]["id"]
    subjects.assign(world / case, row["instance_id"], row["fingerprint"], {"holder": person}, "Fictional Reviewer", "paralegal")
    return folder


def test_file_page_cache_acl_and_changed_bytes_http(server, world, app):  # noqa: F811 -- pytest fixture injection
    source = retain(world, "case-ana")
    retain(world, "case-rosa")  # existing law/ACL record remains restricted
    jane = sign_in(server, "jane@firm.example")
    page = "/api/crop?client=case-ana&doc=i94.pdf&page=0"
    assert call(server + page, jane)[0] == 200
    assert call(server + page, jane)[0] == 200  # actually warmed page cache
    assert app._pages
    assert call(server + "/api/file?client=case-ana&doc=i94.pdf", jane)[0] == 200
    digest = documents.read(world / "case-ana")["boundary_plans"]["i94.pdf"]["source_sha256"]
    assert call(server + page + "&expected_sha256=" + digest, jane)[0] == 200
    assert call(server + page + "&expected_sha256=" + "0" * 64, jane)[0] == 404
    assert call(server + page + "&box=0,0,10,10", jane)[0] == 200
    assert call(server + page + "&box=0,0,100000,100000", jane)[0] == 404
    for bad in ["../i94.pdf", "i94.pdf#p9", "missing.pdf"]:
        assert call(server + "/api/file?client=case-ana&doc=" + quote(bad), jane)[0] == 404
    for bad_page in ["-1", "9", "none"]:
        assert call(server + "/api/crop?client=case-ana&doc=i94.pdf&page=" + bad_page, jane)[0] == 404
    for endpoint in ["/api/file?client={}&doc=i94.pdf", "/api/crop?client={}&doc=i94.pdf&page=0"]:
        hidden = call(server + endpoint.format("case-rosa"), jane)
        missing = call(server + endpoint.format("missing-case"), jane)
        assert hidden == missing and hidden[0] == 404
        assert json.loads(hidden[1]) == {"error": "unknown client"}
    # Warm before revocation, then revoke actual current case permission.
    restricted.name_person(world / "case-rosa", "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Doe")
    assert call(server + "/api/crop?client=case-rosa&doc=i94.pdf&page=0", jane)[0] == 200
    restricted.name_person(world / "case-rosa", "jane@firm.example", False, "Sam Attorney", "attorney", "Jane Doe")
    denied = call(server + "/api/crop?client=case-rosa&doc=i94.pdf&page=0", jane)
    assert denied[0] == 404 and json.loads(denied[1]) == {"error": "unknown client"}
    (source / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE"]))
    status, raw = call(server + page, jane)
    assert status == 404 and "original changed" in json.loads(raw)["error"]
    assert call(server + "/api/file?client=case-ana&doc=i94.pdf", jane)[0] == 404


def test_legacy_document_opens_without_claiming_a_bound_page(server, world):  # noqa: F811 -- pytest fixture injection
    retain(world, "case-ana")
    data = documents.read(world / "case-ana"); data["boundary_plans"] = {}
    (world / "case-ana" / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    jane = sign_in(server, "jane@firm.example")
    assert call(server + "/api/file?client=case-ana&doc=i94.pdf", jane)[0] == 200
    status, raw = call(server + "/api/crop?client=case-ana&doc=i94.pdf&page=0", jane)
    assert status == 404 and "Original page location unavailable" in json.loads(raw)["error"]


def test_explicit_one_record_text_retains_translation_and_labels_stale_read(server, world, app):  # noqa: F811 -- pytest fixture injection
    folder = retain(world, "case-ana")
    data = documents.read(world / "case-ana")
    row = data["documents"][0]
    row["text"] = 'FICTIONAL RAW <script>window.syntheticTextExecuted=true</script>'
    row["translated"] = "Fictional English translation, stored for source review."
    second = dict(row, id="another-fictional-record", text="SECOND FICTIONAL RECORD", translated=None)
    data["documents"].append(second)
    (world / "case-ana" / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    jane = sign_in(server, "jane@firm.example")
    status, raw = call(server + "/api/documents?client=case-ana", jane)
    assert status == 200
    compact = json.loads(raw)
    assert all("text" not in item and "translated" not in item for item in compact["documents"])
    assert compact["documents"][0]["source_locations"][0]["source_location"]["page"] == 0
    endpoint = "/api/document-text?client=case-ana&id=" + quote(row["id"])
    status, raw = call(server + endpoint, jane)
    text = json.loads(raw)
    assert status == 200 and text["text"] == row["text"] and text["translated"] == row["translated"]
    assert "SECOND FICTIONAL RECORD" not in raw and "field approval" in text["reading_note"]
    assert text["source_locations"][0]["source_location"]["state"] == "current"
    views = [json.loads(line) for line in app.views_log.read_text(encoding="utf-8").splitlines()]
    assert any(item["client"] == "case-ana" and item["kind"] == "documents" and item["file"] == row["id"] for item in views)
    (folder / "i94.pdf").write_bytes(document_pdf(["NEW FICTIONAL ORIGINAL"]))
    status, raw = call(server + endpoint, jane)
    assert status == 200 and json.loads(raw)["text"] == row["text"]
    assert json.loads(raw)["source_locations"][0]["source_location"]["state"] == "unavailable"
    assert "changed" in json.loads(raw)["source_locations"][0]["source_location"]["reason"]


def test_document_text_acl_missing_id_and_bad_record_refusal(server, world):  # noqa: F811 -- pytest fixture injection
    retain(world, "case-ana")
    jane = sign_in(server, "jane@firm.example")
    hidden = call(server + "/api/document-text?client=case-rosa&id=anything", jane)
    missing = call(server + "/api/document-text?client=missing&id=anything", jane)
    assert hidden == missing and hidden[0] == 404 and json.loads(hidden[1]) == {"error": "unknown client"}
    for record in ["", "missing", "../meta.json"]:
        assert call(server + "/api/document-text?client=case-ana&id=" + quote(record), jane)[0] == 404
    row = documents.read(world / "case-ana")["documents"][0]
    data = documents.read(world / "case-ana")
    data["documents"].append(dict(row))
    (world / "case-ana" / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    assert call(server + "/api/document-text?client=case-ana&id=" + quote(row["id"]), jane)[0] == 404


def test_new_text_route_is_case_classified():
    from review.server import CASE_GET
    assert "/api/document-text" in CASE_GET


@pytest.mark.parametrize("change", ["none", "source", "acl", "session"])
def test_optional_preview_ocr_releases_installation_gate_and_rechecks_authority(server, world, app, monkeypatch, change):  # noqa: F811 -- pytest fixture injection
    """Synthetic slow OCR permits writers, then honors changed source/session/ACL."""
    import threading
    from concurrent.futures import ThreadPoolExecutor
    from classify.ocr import OcrWord
    from portal.communication_consent import data_gate
    from review.evidence import annotate

    folder = retain(world, "case-rosa")
    restricted.name_person(world / "case-rosa", "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Doe")
    jane = sign_in(server, "jane@firm.example")
    started, release, acquired = threading.Event(), threading.Event(), threading.Event()

    def items(client, user):
        value = {"facts": [{"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}]}],
                 "evidence_fingerprints": {"fictional": "unchanged"}}
        annotate(world / client, value)
        return value

    def slow_ocr(*args, **kwargs):
        assert 0 < kwargs["timeout"] <= 10  # total request budget includes render time
        started.set()
        assert release.wait(8), "synthetic reader release timed out"
        return [OcrWord("SAMPLE", 10, 20, 50, 12, 95, (1, 1, 1)),
                OcrWord("ALPHA", 70, 20, 50, 12, 95, (1, 1, 1))]

    def writer():
        with data_gate(app.data_root.parent):
            if change == "source":
                (folder / "i94.pdf").write_bytes(document_pdf(["CHANGED FICTIONAL SOURCE"]))
            elif change == "acl":
                restricted.name_person(world / "case-rosa", "jane@firm.example", False, "Sam Attorney", "attorney", "Jane Doe")
            elif change == "session":
                app.accounts.sign_out(jane.split("=", 1)[1])
            acquired.set()

    monkeypatch.setattr(app, "items", items)
    monkeypatch.setattr("classify.ocr.ocr_words", slow_ocr)
    with ThreadPoolExecutor(max_workers=2) as pool:
        response = pool.submit(call, server + "/api/items?client=case-rosa", jane)
        try:
            assert started.wait(8), "optional preview reader was not reached"
            mutation = pool.submit(writer)
            assert acquired.wait(3), "OCR held the installation communication gate"
            mutation.result(timeout=3)
        finally:
            release.set()
        status, raw = response.result(timeout=8)
    if change == "acl":
        assert status == 404 and json.loads(raw) == {"error": "unknown client"}
    elif change == "session":
        assert status == 401 and json.loads(raw)["sign_in"] is True
    else:
        assert status == 200
        payload = json.loads(raw)
        location = payload["facts"][0]["sources"][0]["source_location"]
        assert payload["evidence_fingerprints"] == {"fictional": "unchanged"}
        if change == "source":
            assert location["state"] == "unavailable" and location["region"] is None
        else:
            assert location["region"] == [10, 20, 120, 32]


def test_changed_source_after_optional_ocr_is_rechecked_before_case_response(server, world, app, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from review.evidence import annotate
    folder = retain(world, "case-ana")
    jane = sign_in(server, "jane@firm.example")
    def items(client, user):
        value = {"sources": [{"doc": "i94.pdf", "page": 0, "raw": "SAMPLE ALPHA"}]}
        annotate(world / client, value)
        return value
    def region(*args, **kwargs):
        # Emulate replacement after reading, without source_quote_region's own
        # final snapshot: the HTTP release check independently refuses it.
        (folder / "i94.pdf").write_bytes(document_pdf(["NEW FICTIONAL SOURCE"]))
        return [10, 20, 120, 32]
    monkeypatch.setattr(app, "items", items)
    monkeypatch.setattr(app, "source_quote_region", region)
    status, raw = call(server + "/api/items?client=case-ana", jane)
    assert status == 200
    location = json.loads(raw)["sources"][0]["source_location"]
    assert location["state"] == "unavailable" and location["region"] is None
