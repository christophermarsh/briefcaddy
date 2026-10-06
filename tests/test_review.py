"""src/review: decisions -> fact graph -> re-filled I-485, and the local
server's guards. Built on a synthetic client (constructed documents, a
drawn "scan"), never real client data."""

import json
import threading
import urllib.error
import urllib.request
from pathlib import Path

import pytest
from PIL import Image

from batch import process_documents, record_documents
from document_instances import context as boundary_context
from fill import load_field_map
from questionnaire import QuestionnaireReading
from review.server import ReviewApp, serve
from review.state import Catalog, build_items, load_decisions, record_decision, refill, reviewed_graph, save_bundle, undo_decision
import schema_path

_REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
CATALOG = Catalog(FIELD_MAP, TEMPLATE)

I94 = "Admission (I-94) Record Number: 14335150685\nU.S. Customs and Border Protection\nClass of Admission: B2\nArrival/Issued Date: 2016 November 24\n"
SSN = "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
QUESTIONNAIRE = "Questionário para Ajuste de Status\nI485 - SIJS\n"


def _reader(doc_id):
    from extract.base import ExtractedField

    r = QuestionnaireReading()
    r.text_fields = [
        ExtractedField("applicant.weight_lbs", "118", "118", 0.7),
        ExtractedField("applicant.height", "5'3\"", "5'3\"", 0.7),
        ExtractedField("applicant.last_arrival_date_self_reported", "x", "2016-11-23", 0.7),
    ]
    r.unread["mother_dob"] = "handwriting invalid: value 'DE 1978' is not a valid date"
    r.evidence = {
        "weight": {"kind": "text", "input_kind": "weight", "describe": "weight", "facts": {"value": "applicant.weight_lbs"}, "page": 0, "box": [10, 10, 200, 60], "status": "ok", "reads": [{"value": "118"}], "values": {"value": "118"}, "reason": ""},
        "mother_dob": {"kind": "text", "input_kind": "date", "describe": "the mother's date of birth", "facts": {"value": "applicant.mother_dob"}, "page": 0, "box": [10, 80, 300, 130], "status": "invalid", "reads": [{"value": "de 1978"}], "values": {}, "reason": "not a valid date"},
    }
    return r


@pytest.fixture
def client(tmp_path):
    source = tmp_path / "clients" / "demo" / "source"
    source.mkdir(parents=True)
    Image.new("RGB", (1200, 900), "white").save(source / "q.pdf")  # a one-page "scan"
    from portal.demo import document_pdf
    docs = [("i94.pdf", I94), ("ssn.pdf", SSN), ("q.pdf", QUESTIONNAIRE), ("license.pdf", "DRIVER LICENSE\nHeight 5-05")]
    for name, text in docs:
        if name != "q.pdf":
            (source / name).write_bytes(document_pdf(text.splitlines()))
    result = process_documents(
        "demo",
        docs,
        questionnaire_reader=_reader,
        boundary_context=boundary_context(source, None, [name for name, _ in docs]),
    )
    # The retained license's real reader supplies the conflicting height with
    # its per-edge evidence proof; no unproven source is appended after reading.
    assert any(s.doc_id == "license.pdf" and s.normalized_value == "5'5\""
               for s in result.raw_graph.get("applicant.height").sources)
    out = tmp_path / "data" / "demo"
    record_documents(result, source, docs)
    save_bundle(result, out, source)
    _confirm_subjects(out)
    refill(out, FIELD_MAP, TEMPLATE)
    return out


def _confirm_subjects(out):
    """These tests start after named source review, preserving their field-review assertions."""
    import documents
    import subject_attribution
    identity = next(p["id"] for p in documents.read(out)["case_subjects"]["people"] if p["case_role"] == "applicant")
    for row in subject_attribution.views(out):
        assert row["bound"] and set(row["slots"]) <= {"holder", "respondent"}
        subject_attribution.assign(out, row["instance_id"], row["fingerprint"], {slot: identity for slot in row["slots"]},
                                   "Fixture Subject Reviewer", "paralegal")


def _items(client_dir):
    return {i["id"]: i for i in build_items(client_dir, FIELD_MAP, TEMPLATE, CATALOG)["open"]}


def _decide(client_dir, item_id, **decision):
    item = _items(client_dir)[item_id]
    return record_decision(client_dir, item, {"reviewer": "Jane", "role": "paralegal",
                                           "evidence_fingerprints": item.get("evidence_fingerprints", {}), **decision})


def test_queue_holds_conflicts_unread_answers_and_crosschecks(client):
    items = _items(client)
    assert items["fact:applicant.height"]["group"] == "needs_input"
    unread = items["unread:questionnaire.mother_dob"]
    assert [f["key"] for f in unread["facts"]] == ["applicant.mother_dob"]
    assert unread["evidence"][0]["box"] == [10, 80, 300, 130]
    assert "crosscheck:applicant.i94_arrival_date" in items
    weight = items["fact:applicant.weight_lbs"]
    assert weight["group"] == "questionnaire" and weight["actions"][0] == "confirm"


def test_confirm_signs_off_without_changing_the_value(client):
    _decide(client, "fact:applicant.weight_lbs", action="confirm")
    fact = reviewed_graph(client).get("applicant.weight_lbs")
    assert fact.value == "118" and fact.review.resolved_by == "Jane"
    assert "fact:applicant.weight_lbs" not in _items(client)
    assert FactGraphSourceIds(client, "applicant.weight_lbs") == ["q.pdf"]  # pipeline's fact untouched on disk


def FactGraphSourceIds(client_dir, key):
    return [s["doc_id"] for s in json.loads((client_dir / "fact_graph.json").read_text())["facts"][key]["sources"]]


def test_picking_a_value_resolves_a_conflict_and_keeps_both_sources(client):
    _decide(client, "fact:applicant.height", action="set", values={"applicant.height": "5'3\""}, note="Italian ID + client agree")
    fact = reviewed_graph(client).get("applicant.height")
    assert fact.status == "resolved" and fact.value == "5'3\""
    assert {s.doc_id for s in fact.sources} >= {"q.pdf", "license.pdf"}
    summary = refill(client, FIELD_MAP, TEMPLATE)
    from pypdf import PdfReader

    fields = PdfReader(summary["pdf"]).get_fields()
    assert fields["form1[0].#subform[12].Pt7Line3_HeightFeet[0]"]["/V"] == "5"
    assert fields["form1[0].#subform[12].Pt7Line3_HeightInches[0]"]["/V"] == "3"


def test_entering_an_unread_answer(client):
    _decide(client, "unread:questionnaire.mother_dob", action="set", values={"applicant.mother_dob": "1978-06-02"})
    assert reviewed_graph(client).get("applicant.mother_dob").value == "1978-06-02"
    assert "unread:questionnaire.mother_dob" not in _items(client)


def test_leave_blank_keeps_the_field_empty(client):
    _decide(client, "fact:applicant.weight_lbs", action="blank", note="client unsure")
    summary = refill(client, FIELD_MAP, TEMPLATE)
    from pypdf import PdfReader

    assert not PdfReader(summary["pdf"]).get_fields()["form1[0].#subform[12].Pt7Line4_Weight1[0]"].get("/V")
    assert "traceability broken" not in (client / "flag_report.txt").read_text()


def test_undo_reopens_the_item(client):
    _decide(client, "fact:applicant.weight_lbs", action="confirm")
    undo_decision(client, "fact:applicant.weight_lbs")
    assert "fact:applicant.weight_lbs" in _items(client)


def test_decisions_are_a_chain_and_an_undo_is_marked_not_deleted(client):
    """docs/design_plan.md Part 4.3: record_decision appends, undo_decision marks (who, when); the current
    decision's fields stay at the top of the entry, so every reader works as before."""
    from review.state import load_decision_log

    iid = "fact:applicant.weight_lbs"
    first = _decide(client, iid, action="confirm", note="matches the scan")
    entry = json.loads((client / "decisions.json").read_text())[iid]
    assert {k: entry[k] for k in first} == first and len(entry["history"]) == 1 and "undone" not in entry
    undo_decision(client, iid, "Ana Attorney", "attorney")
    assert iid not in load_decisions(client) and iid in _items(client)  # open again, and no longer applied
    assert reviewed_graph(client).get("applicant.weight_lbs").review is None
    entry = load_decision_log(client)[iid]
    assert entry["undone"]["by"] == "Ana Attorney" and entry["undone"]["role"] == "attorney" and entry["action"] == "confirm"
    assert entry["history"][0]["undone"]["by"] == "Ana Attorney" and entry["history"][0]["note"] == "matches the scan"
    undo_decision(client, iid, "Someone Else")  # undoing an undone item changes nothing
    assert load_decision_log(client)[iid]["undone"]["by"] == "Ana Attorney"

    out = build_items(client, FIELD_MAP, TEMPLATE, CATALOG)
    reopened = next(r for r in out["reopened"] if r["id"] == iid)
    assert reopened["undone"]["by"] == "Ana Attorney" and reopened["history"][0]["action"] == "confirm"

    second = _decide(client, iid, action="set", values={"applicant.weight_lbs": "120"})
    entry = load_decision_log(client)[iid]
    assert "undone" not in entry and entry["action"] == "set" and entry["values"] == {"applicant.weight_lbs": "120"}
    assert [h["action"] for h in entry["history"]] == ["confirm", "set"] and entry["history"][1]["at"] == second["at"]
    assert "undone" in entry["history"][0] and "undone" not in entry["history"][1]
    assert load_decisions(client)[iid]["action"] == "set"
    done = next(d for d in build_items(client, FIELD_MAP, TEMPLATE, CATALOG)["done"] if d["id"] == iid)
    assert len(done["history"]) == 2 and "history" not in done["decision"]


def test_a_decision_written_before_the_chain_is_its_own_history(client):
    """decisions.json files from before 10/2026 hold one decision per item and no "history": still read, and undone by marking."""
    from review.state import load_decision_log

    old = {"action": "confirm", "values": {}, "reviewer": "Jane", "note": "", "at": "2026-09-30T12:00:00+00:00",
           "item": {"id": "fact:applicant.weight_lbs", "kind": "fact", "level": "review", "title": "t", "group": "g", "facts": ["applicant.weight_lbs"]}}
    (client / "decisions.json").write_text(json.dumps({"fact:applicant.weight_lbs": old}))
    # Preserve legacy history, but an unproven critical approval cannot apply.
    assert load_decisions(client) == {}
    assert load_decision_log(client)["fact:applicant.weight_lbs"]["at"] == old["at"]
    undo_decision(client, "fact:applicant.weight_lbs", "Jane")
    entry = load_decision_log(client)["fact:applicant.weight_lbs"]
    assert len(entry["history"]) == 1 and entry["history"][0]["at"] == old["at"] and entry["history"][0]["undone"]["by"] == "Jane"


def test_server_undo_records_who_reopened_it(server, client):
    from review.state import load_decision_log

    _decide(client, "fact:applicant.weight_lbs", action="confirm")
    status, body = _post(server + "/api/undo", {"client": "demo", "item_id": "fact:applicant.weight_lbs"})
    assert status == 400 and b"Enter your name" in body
    status, _ = _post(server + "/api/undo", {"client": "demo", "item_id": "fact:applicant.weight_lbs", "reviewer": "Jane"})
    assert status == 200 and load_decision_log(client)["fact:applicant.weight_lbs"]["undone"]["by"] == "Jane"


@pytest.mark.parametrize(
    "item_id, decision, error",
    [
        ("fact:applicant.height", {"action": "set", "values": {"applicant.height": "tall"}}, "expected the form"),
        ("fact:applicant.height", {"action": "confirm"}, "isn't possible"),  # can't confirm a conflict
        ("unread:questionnaire.mother_dob", {"action": "set", "values": {"applicant.mother_dob": "1978-13-40"}}, "not a valid date"),
        ("fact:applicant.weight_lbs", {"action": "set", "values": {"applicant.ssn": "1"}}, "not part of this item"),
        ("fact:applicant.weight_lbs", {"action": "confirm", "reviewer": ""}, "Enter your name"),
    ],
)
def test_bad_decisions_are_refused(client, item_id, decision, error):
    item = _items(client)[item_id]
    with pytest.raises(ValueError, match=error):
        record_decision(client, item, {"reviewer": "Jane", **decision})
    assert load_decisions(client) == {}


def test_blocking_acknowledgement_needs_a_note(client):
    item = {"id": "alert:x", "kind": "alert", "level": "blocking", "title": "t", "group": "blocking", "facts": [], "actions": ["acknowledge"]}
    with pytest.raises(ValueError, match="needs a note"):
        record_decision(client, item, {"reviewer": "Jane", "action": "acknowledge"})
    record_decision(client, item, {"reviewer": "Jane", "action": "acknowledge", "note": "attorney reviewed eligibility"})


# --- server ------------------------------------------------------------------


@pytest.fixture
def server(client):
    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    # the handler's allowed hosts were computed for port 0 -- rebuild for the real port
    from review.server import make_handler

    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url, headers=None):
    req = urllib.request.Request(url, headers=headers or {})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def _post(url, body, headers=None):
    h = {"Content-Type": "application/json", "X-Review-App": "1"} | (headers or {})
    req = urllib.request.Request(url, data=json.dumps(body).encode(), headers=h, method="POST")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read())
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_server_lists_items_and_serves_a_crop(server):
    status, body = _get(server + "/api/items?client=demo")
    assert status == 200 and any(i["id"] == "fact:applicant.height" for i in json.loads(body)["open"])
    status, png = _get(server + "/api/crop?client=demo&doc=q.pdf&page=0&box=10,10,200,60")
    assert status == 200 and png[:4] == b"\x89PNG"


def test_server_decide_then_apply(server, client):
    body = {"client": "demo", "item_id": "fact:applicant.weight_lbs", "action": "confirm", "reviewer": "Jane",
            "evidence_fingerprints": _items(client)["fact:applicant.weight_lbs"]["evidence_fingerprints"]}
    # The legacy single-user server cannot certify a critical read as staff.
    status, error = _post(server + "/api/decide", body)
    assert status == 400 and b"signed-in staff" in error
    # Use an authenticated staff fixture for the positive operation.
    from review.auth import Accounts
    from review.server import make_handler
    accounts = Accounts(client.parent.parent / "review_users.json")
    password = "Fictional password for review 27!"  # secret-scan: allow -- isolated fictional account, no live credential
    accounts.change_password("jane@example.test", accounts.add("jane@example.test", "Jane", "paralegal"), password)
    app = ReviewApp(client.parent, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=accounts)
    httpd = serve(app, 0); port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    protected = f"http://127.0.0.1:{port}"
    login = urllib.request.Request(protected + "/api/login", data=json.dumps({"email": "jane@example.test", "password": password}).encode(),
                                   headers={"Content-Type": "application/json", "X-Review-App": "1"}, method="POST")
    try:
        with urllib.request.urlopen(login) as response:
            cookie = response.headers["Set-Cookie"].split(";")[0]
        assert _post(protected + "/api/decide", body, {"Cookie": cookie})[0] == 200
    finally:
        httpd.shutdown()
    status, summary = _post(server + "/api/apply", {"client": "demo"})
    assert status == 200 and "counts" in summary
    assert "fact:applicant.weight_lbs" in load_decisions(client)


def test_server_refuses_foreign_host_header(server):
    status, _ = _get(server + "/api/clients", headers={"Host": "attacker.example"})
    assert status == 403


def test_server_refuses_posts_without_the_app_header(server, client):
    req = urllib.request.Request(server + "/api/apply", data=b"{}", headers={"Content-Type": "application/json"}, method="POST")
    with pytest.raises(urllib.error.HTTPError) as exc:
        urllib.request.urlopen(req)
    assert exc.value.code == 403


@pytest.mark.parametrize("doc", ["../../../schemas/firm/firm_profile.json", "..\\q.pdf", "missing.pdf"])
def test_server_serves_only_files_in_the_clients_own_folder(server, doc):
    from urllib.parse import quote

    status, _ = _get(server + f"/api/file?client=demo&doc={quote(doc)}")
    assert status == 404


def test_server_refuses_unknown_client(server):
    status, _ = _get(server + "/api/items?client=../../etc")
    assert status == 404


def test_a_reviewers_correction_to_an_input_reaches_the_boxes_built_from_it(tmp_path):
    """The prior address box is assembled from the questionnaire's history
    line; correcting that line in review must change the box (derivation is
    re-run on the reviewed graph)."""
    from extract.base import ExtractedField

    def reader(doc_id):
        r = QuestionnaireReading()
        r.text_fields = [ExtractedField("questionnaire.prior_address1_street", "x", "10 WRONG ST", 0.7),
                         ExtractedField("questionnaire.prior_address1_city", "x", "WORCESTER", 0.7)]
        return r

    source = tmp_path / "src"
    source.mkdir()
    from portal.demo import document_pdf
    (source / "q.pdf").write_bytes(document_pdf(QUESTIONNAIRE.splitlines()))
    docs = [("q.pdf", QUESTIONNAIRE)]
    result = process_documents("out", docs, questionnaire_reader=reader, boundary_context=boundary_context(source, None, ["q.pdf"]))
    record_documents(result, source, docs)
    out = tmp_path / "out"
    save_bundle(result, out, source)
    _confirm_subjects(out)
    assert reviewed_graph(out).get("applicant.prior_address_street").value == "10 WRONG ST"
    import critical_review
    key = "questionnaire.prior_address1_street"
    item = {"id": "unread:questionnaire.prior_address_1", "kind": "unread", "level": "review", "title": "x", "group": "g",
            "facts": [{"key": key, "input": CATALOG.input(key)}], "actions": ["set"]}
    record_decision(out, item, {"action": "set", "values": {key: "10 RIGHT ST"}, "reviewer": "T", "role": "paralegal", "note": "",
                               "evidence_fingerprints": {key: critical_review.context(out)[key]["fingerprint"]}})
    assert reviewed_graph(out).get("applicant.prior_address_street").value == "10 RIGHT ST"


def test_card_headlines_are_the_question_not_the_form_preamble():
    from review.state import concise

    assert concise("22. Have you EVER been arrested, cited, charged (including any diversion program), or detained?") == \
        "Have you EVER been arrested, cited, charged, or detained?"
    assert concise("Information About Your Marital History. Information About Prior Marriages (if any). 11. Prior Spouse's Legal Name "
                   "Enter Family Name, Last Name") == "Prior Spouse's Legal Name: Family Name, Last Name"
    assert concise("Part 2. Application Type or Filing Category. 2. I am filing this Form I- 4 8 5 as a") == "I am filing this Form I-485 as a"
    assert concise("Removal proceedings (Notice to Appear)") == "Removal proceedings (Notice to Appear)"  # short titles keep their asides
    assert concise("Weight (lb)") == "Weight (lb)"
    assert len(concise("x " * 200)) <= 111


def test_every_case_has_a_stage_from_invitation_to_filing(tmp_path):
    from review.overview import mark_filed, stage_of

    assert stage_of({"portal_status": "invited"}) == "not_invited"  # in the portal, nothing sent yet (buyer visit 4: not "Invited")
    assert stage_of({"portal_status": "invited", "invited_at": "2026-10-01T09:00:00-04:00"}) == "invited"
    assert stage_of({"portal_status": "invited", "reminded_at": "2026-10-02T09:00:00-04:00"}) == "invited"
    assert stage_of({"portal_status": "started"}) == "answering"
    assert stage_of({"portal_status": "submitted"}) == "processing"
    base = {"processed_at": "2026-10-01T00:00:00+00:00"}
    assert stage_of(base | {"fix": 2, "check": 0, "attorney": 5}) == "review"
    assert stage_of(base | {"fix": 0, "check": 0, "attorney": 5}) == "attorney"
    assert stage_of(base | {"fix": 0, "check": 0, "attorney": 0, "blocking": 1}) == "attorney"
    assert stage_of(base | {"fix": 0, "check": 0, "attorney": 0}) == "ready"
    status = mark_filed(tmp_path, True, "Jane")
    assert status["filed_by"] == "Jane" and stage_of(base | status) == "filed"
    assert "filed_at" not in mark_filed(tmp_path, False, "Jane")


def test_server_builds_the_filing_packet(server, client):
    source = Path(json.loads((client / "meta.json").read_text())["source_folder"])
    (source / "i94.pdf").unlink()  # simulate a retained original becoming unavailable after reading
    status, body = _get(server + "/api/packet?client=demo")
    plan = json.loads(body)
    # A missing original is reported and holds readiness; an inspectable draft remains available.
    assert status == 200 and any("Not found in the client's folder: i94.pdf" in p for p in plan["problems"])
    assert not plan["ready"] and any("document boundaries" in p for p in plan["problems"])
    status, body = _post(server + "/api/packet", {"client": "demo", "reviewer": ""})
    assert status == 400  # the packet records who built it
    status, manifest = _post(server + "/api/packet", {"client": "demo", "reviewer": "Jane"})
    assert status == 200 and manifest["draft"] and manifest["built_by"] == "Jane"
    status, pdf = _get(server + "/api/packet.pdf?client=demo")
    assert status == 200 and pdf[:5] == b"%PDF-"
    status, _ = _post(server + "/api/packet-file", {"client": "demo", "doc": "q.pdf", "exhibit": "nowhere", "reviewer": "Jane"})
    assert status == 400


def test_a_number_with_dashes_fits_a_digits_only_box():
    """Picking the SSN as the card prints it ("123-45-6789") must not be
    refused as longer than the 9-character box: the box takes the digits."""
    from review.state import _check_value

    ssn = CATALOG.input("applicant.ssn")
    assert ssn.get("digits") and ssn["maxlen"] == 9
    assert _check_value("123-45-6789", ssn, "applicant.ssn") == "123-45-6789"
    with pytest.raises(ValueError):
        _check_value("123-45-67890", ssn, "applicant.ssn")
    assert not CATALOG.input("applicant.family_name").get("digits")
