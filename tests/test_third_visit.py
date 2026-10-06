"""Fictional example or implementation helper."""

from __future__ import annotations

import io
import json
import shutil
from datetime import date
from pathlib import Path

import pytest
from PIL import Image

import index
import portal.questions as questions
import version
from fill import load_field_map
from portal import demo as seed
from portal.engine import new_photos, photo_status, process_client, sync_retakes
from portal.store import PortalStore
from review.server import ReviewApp
from review.state import Catalog, build_items, record_decision, reviewed_graph, undo_decision
import schema_path

_REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
FIELD_MAP = load_field_map(schema_path.path("field_map", "i485"))
POLICIES = json.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
CATALOG = Catalog(FIELD_MAP, TEMPLATE, POLICIES)
ID = seed.DEMO_ID


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    root = tmp_path_factory.mktemp("third")
    seed.seed(PortalStore(root / "portal"), root / "clients")
    return root


@pytest.fixture
def world(seeded, tmp_path, monkeypatch):
    """A private copy of the seeded demo world for each test (the portal's folder, the case folders, the review app on both)."""
    shutil.copytree(seeded, tmp_path / "w")
    monkeypatch.setenv("I485_INDEX", str(tmp_path / "index.db"))  # this copy of the world has its own search index and query layer (one built by an earlier test is for other cases)
    monkeypatch.setenv("I485_QUERY_DB", str(tmp_path / "query.db"))
    root = tmp_path / "w"
    app = ReviewApp(root / "clients", schema_path.path("field_map", "i485"), TEMPLATE, schema_path.path("law", "policy_sijs"), root / "portal")
    return root, PortalStore(root / "portal"), app


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


def _open_ids(case: Path) -> set[str]:
    return {i["id"] for i in build_items(case, FIELD_MAP, TEMPLATE, CATALOG, pending=False)["open"]}


# -- 2. a client's typed answer fills the box ---------------------------------------------------------------


def test_a_clients_typed_date_answer_fills_the_box_with_its_own_source(world):
    root, store, app = world
    case = root / "clients" / ID
    assert "missing:applicant.father_dob" in _open_ids(case)  # the box is empty and the form needs it
    request = store.add_request(ID, "What is your father's date of birth?", None, "Paulo Paralegal", facts=["applicant.father_dob"],
                                typed={"type": "date", "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt",
                                       "machine_translated": True, "needs_translator": False})
    store.answer_request(ID, request["id"], reply="1970-05-04")
    process_client(store, ID, out_root=root / "clients")  # the worker's next pass (the portal queues the client on every answer)

    fact = reviewed_graph(case).get("applicant.father_dob")
    assert fact.value == "1970-05-04" and fact.status == "resolved" and fact.tier == 3  # the client's statement, as a portal answer is
    [source] = fact.sources
    assert (source.doc_id, source.doc_type, source.raw_value) == ("office question", "office_question", "05/04/1970")
    assert "missing:applicant.father_dob" not in _open_ids(case)  # no longer "Needs an answer"
    card = next(c for c in build_items(case, FIELD_MAP, TEMPLATE, CATALOG)["cards"] if any(f["key"] == "applicant.father_dob" for f in c["facts"]))
    assert card["tab"] != "fix" and "office question" not in card["docs"]  # a pseudo-document, never an Open link
    assert [s["type"] for f in card["facts"] if f["key"] == "applicant.father_dob" for s in f["sources"]] == ["office_question"]

    # undone like any decision: the paralegal types another value, then takes it back
    item = next(i for i in build_items(case, FIELD_MAP, TEMPLATE, CATALOG, pending=False)["open"] if any(f["key"] == "applicant.father_dob" for f in i["facts"]))
    record_decision(case, item, {"reviewer": "Jane", "action": "set", "values": {"applicant.father_dob": "1970-05-05"}})
    assert reviewed_graph(case).get("applicant.father_dob").value == "1970-05-05"
    undo_decision(case, item["id"], "Jane")
    assert reviewed_graph(case).get("applicant.father_dob").value == "1970-05-04"


def test_each_kind_of_answer_is_typed_the_way_the_form_reads_it():
    yes_no = next(k for k, v in FIELD_MAP.items() if isinstance(v, dict) and v.get("type") == "yes_no")
    choice = next(k for k, v in FIELD_MAP.items() if isinstance(v, dict) and v.get("type") == "choice_by_value" and len(v["options"]) > 2)
    option = list(FIELD_MAP[choice]["options"])[1]
    words = next(k for k, v in FIELD_MAP.items() if isinstance(v, dict) and v.get("type") == "text")

    def done(kind, key, reply, facts=None, **extra):
        return {"id": f"req-{kind}", "type": kind, "status": "answered", "facts": facts or [key], "reply": reply, "answered_at": "2026-10-02T10:00:00+00:00", **extra}

    got = {f.fact_key: f for f in questions.office_answers([
        done("date", "applicant.father_dob", "1970-05-04"), done("yes_no", yes_no, "No"), done("choice", choice, option),
        done("text", words, "  maria exemplo  "), done("text", words, "ignored", doc_id="passport")], FIELD_MAP)}
    assert got["applicant.father_dob"].normalized_value == "1970-05-04" and got["applicant.father_dob"].raw_value == "05/04/1970"
    assert got[yes_no].normalized_value == "No" and got[choice].normalized_value == option and got[words].normalized_value is not None
    assert got[words].normalized_value == "MARIA EXEMPLO"  # words go in capitals, as the firm fills the form

    # a card with several boxes that fit, a document request and an empty answer fill nothing; the newest answer to a box wins
    two = [k for k, v in FIELD_MAP.items() if isinstance(v, dict) and v.get("type") == "date"][:2]
    assert questions.office_answers([done("date", two[0], "1970-05-04", facts=two)], FIELD_MAP) == []
    assert questions.office_answers([done("date", "applicant.father_dob", "")], FIELD_MAP) == []
    later = done("date", "applicant.father_dob", "1971-01-02", answered_at="2026-10-03T10:00:00+00:00")
    newest = questions.office_answers([later, done("date", "applicant.father_dob", "1970-05-04")], FIELD_MAP)
    assert [f.normalized_value for f in newest] == ["1971-01-02"]


# -- 3. a photo marked hard to read by hand is asked of the client; a new photo is noticed -------------------


def _record(case: Path, doc_type: str) -> dict:
    return next(r for r in json.loads((case / "documents.json").read_text(encoding="utf-8"))["documents"] if r["type"] == doc_type)


def test_marking_a_clients_photo_blurry_asks_for_a_retake_at_once_and_a_new_photo_is_noticed(world):
    root, store, app = world
    case = root / "clients" / ID
    record = _record(case, "birth_certificate")
    assert not [t for t in store.tasks(ID) if t["kind"] == "retake"]

    done = app.document_change(ID, {"id": record["id"], "field": "quality", "value": "cut_off", "reviewer": "Paulo Paralegal"}, "paralegal")
    [task] = [t for t in store.tasks(ID) if t["kind"] == "retake"]  # no wait for the next run
    assert task["record"] == record["id"] and task["quality"] == "cut_off" and task["asked_at"] and set(task["texts"]) == {"pt", "es", "en", "ht"}
    row = next(r for r in done["documents"] if r["id"] == record["id"])
    assert row["quality"] == "cut_off" and row["client_note"].startswith("Retake asked of the client (") and done["note"] == row["client_note"]
    assert any(r["document"] and r["why"] for r in app.client_messages(ID)["retakes"])  # My work and the case's thread list it

    app.document_change(ID, {"id": record["id"], "field": "quality", "value": "readable", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert not [t for t in store.tasks(ID) if t["kind"] == "retake"]  # set back: taken off the client's list
    app.document_change(ID, {"id": record["id"], "field": "quality", "value": "partial", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert len([t for t in store.tasks(ID) if t["kind"] == "retake"]) == 1  # asked again, never twice

    # the client's new photo: the portal keeps the task as "received" (it closes when the photo is read); the row stops saying "hard to read" and says why
    store.add_upload(ID, "birth_certificate", "photo.png", _png(), "image/png", retake=True)
    store.mark_retakes_received(ID, "birth_certificate")
    row = next(r for r in app.documents(ID)["documents"] if r["id"] == record["id"])
    assert row["quality"] == "unknown" and row["quality_set_by"] is None
    from review.state import us_date

    assert row["client_note"] == f"New photo received {us_date(store.uploads(ID)[-1]['uploaded_at'])} (not read yet)"
    from review.overview import my_work, overview

    work = my_work(overview(root / "clients", FIELD_MAP, TEMPLATE, CATALOG, root / "portal")["clients"], "paralegal")
    [photo] = work["new_photos"]
    assert photo["client"] == ID and photo["document"] == "birth certificate" and photo["retake"] and work["counts"]["new_photos"] == 1
    assert not work["retakes"]  # a photo that is in is "arrived", not "waiting for a new photo"


def test_the_offices_own_scan_is_never_put_back_on_the_client(world):
    root, store, app = world
    case = root / "clients" / ID
    data = json.loads((case / "documents.json").read_text(encoding="utf-8"))
    scan = data["documents"][0] | {"id": "office-scan-1", "files": ["office-scan.pdf"], "doc_ids": ["office-scan.pdf"], "type": "passport", "quality": "unknown",
                                   "quality_set_by": None, "source": "folder"}
    data["documents"].append(scan)
    (case / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    done = app.document_change(ID, {"id": "office-scan-1", "field": "quality", "value": "blurry", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert done["note"] == "This is the office's scan: the client is not asked"
    assert not [t for t in store.tasks(ID) if t["kind"] == "retake"]  # the client is not asked
    assert next(r for r in done["documents"] if r["id"] == "office-scan-1")["client_note"] == done["note"]


def test_a_client_who_is_not_in_the_portal_is_told_so(world):
    root, store, app = world
    case = root / "clients" / ID
    shutil.rmtree(root / "portal" / "clients" / ID)  # the case has no portal folder
    done = app.document_change(ID, {"id": _record(case, "passport")["id"], "field": "quality", "value": "blurry", "reviewer": "Paulo Paralegal"}, "paralegal")
    assert done["note"] == "This client is not in the portal, so nobody is asked for another photo"


def test_the_photo_status_needs_no_portal_files():
    records = [{"id": "r1", "files": ["a-01.pdf"], "quality": "blurry"}, {"id": "r2", "files": ["b-01.pdf"], "quality": "readable"}]
    ups = [{"id": "a-01", "doc_id": "a", "stored": "a-01.pdf", "status": "checked", "uploaded_at": "2026-10-01T10:00:00+00:00"},
           {"id": "b-01", "doc_id": "b", "stored": "b-01.pdf", "status": "checked", "uploaded_at": "2026-10-01T10:00:00+00:00"},
           {"id": "b-02", "doc_id": "b", "stored": "b-02.pdf", "status": "received", "uploaded_at": "2026-10-02T10:00:00+00:00"}]
    status = photo_status(records, ups, [{"kind": "retake", "record": "r1", "asked_at": "2026-10-01T11:00:00+00:00"}])
    assert status["r1"] == {"retake": "asked", "asked_at": "2026-10-01T11:00:00+00:00"}
    assert status["r2"] == {"new_photo": {"at": "2026-10-02T10:00:00+00:00", "waiting": True}}
    assert new_photos(records, ups) == [{"record": "r2", "type": None, "at": "2026-10-02T10:00:00+00:00"}]
    assert sync_retakes  # imported: the review app calls it when a reviewer marks a photo


# -- 10. the welcome screen's line, in the four languages -------------------------------------------------------


def test_the_welcome_screens_line_about_what_the_office_asked_is_in_every_language():
    html = (_REPO / "src" / "portal" / "static" / "portal.html").read_text(encoding="utf-8")
    assert html.count("asked_n: (n) =>") == 4 and html.count("asked_go: ") == 4  # Portuguese, Spanish, English, Haitian Creole
    assert "The office asked you ${n}" in html and "O escritório pediu ${n}" in html and "La oficina le pidió ${n}" in html and "Biwo a mande w ${n}" in html
    attorney = (_REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8")
    assert "The office asked you" in attorney and "DRAFT" in attorney  # listed for the attorney and the certified translator


# -- 6, 7. the version and the search index in the security papers ---------------------------------------------


def test_the_version_moved_with_this_release():
    """Fictional example or implementation helper."""
    assert tuple(map(int, version.VERSION.split("."))) > (2026, 10, 3)
    newest = version.releases()[0]
    assert (newest["version"], newest["released"]) == (version.VERSION, version.RELEASED) and newest["lines"]


def test_the_security_papers_say_where_the_search_index_is():
    docs = _REPO / "docs"
    for name in ("security/data_flow.md", "security/security_program.md", "security/product_data_statement.md", "security/exit_procedure.md", "deployment.md"):
        assert "index.db" in (docs / name).read_text(encoding="utf-8"), name
    exit_text = " ".join((docs / "security/exit_procedure.md").read_text(encoding="utf-8").split())
    assert "does not include it" in exit_text and "rebuilt from the cases" in exit_text


# -- 8, 13. the search result names its case and document; a saved search that finds nothing says so --------------


def test_a_search_result_names_its_case_and_the_document_row_it_opens(world):
    root, store, app = world
    found = app.search({"q": "passport"}, "attorney")
    hit = next(h for h in found["results"] if h["case"] == ID)
    assert hit["case"] == ID  # the screen prints "Case demo-ana" beside the name
    ids = {r["id"] for r in app.documents(ID)["documents"]}
    assert hit["doc"] in ids  # the Documents tab has a row (id "doc-<this>") to scroll to
    html = (_REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert 'id: "doc-" + d.id' in html and 'S.focusDoc = h.doc; openPacket(h.case, "documents")' in html


def test_every_saved_search_has_its_words_for_finding_nothing(world):
    root, store, app = world
    found = app.search({"q": ""}, "attorney")
    assert [s["none"] for s in found["saved"]] == ["No work permits expire in the next 90 days",
                                                   "No case has a Notice to Appear without an EOIR-28 filed", "No police clearance is older than two years"]
    assert found["cases"] >= 1  # "N cases checked"
    paralegal = app.search({"q": ""}, "paralegal")
    assert paralegal["cases"] <= found["cases"]
    assert index.facets(db_path=root / "nothing.db")["cases"] == 0


# -- 12. the packet tab's sentence matches the state -----------------------------------------------------------


def test_the_packet_check_says_draft_until_the_problems_are_settled_or_ready_to_mail(world):
    import prefile

    tmp_path = world[0] / "clients" / ID

    def titles(manifest):
        (tmp_path / "packet.json").write_text(json.dumps({"built_at": "2026-10-02T10:00:00+00:00", "built_by": "Jane", "forms": [], **manifest}), encoding="utf-8")
        checks = prefile.check(tmp_path, "i485", date(2026, 10, 2))["checks"]
        return next(c for c in checks if c["id"] == "draft")

    draft = titles({"draft": True, "problems": ["40 review cards still open.", "Missing: the birth certificate."]})
    assert draft["title"] == "Draft until 2 problems are settled" and draft["level"] == "fail" and "Not a draft" not in draft["title"]
    assert "40 review cards still open." in draft["text"]
    assert titles({"draft": True, "problems": ["One thing."]})["title"] == "Draft until 1 problem is settled"
    ready = titles({"draft": False, "problems": []})
    assert ready["title"] == "Ready to mail" and ready["level"] == "pass"


# -- the header's count: the items a policy will add are counted from the start --------------------------------


def test_settling_the_social_security_number_never_raises_the_open_total(world):
    root, store, app = world
    case = root / "clients" / ID
    before = build_items(case, FIELD_MAP, TEMPLATE, CATALOG)
    assert before["pending"]["count"] >= 2 and before["pending"]["because"]  # the card-already-issued policy waits on the number
    total = len(before["open"]) + before["pending"]["count"] + len(before["done"])
    waiting = {i["id"] for i in before["pending"]["items"]}

    item = next(i for i in before["open"] if i["id"] == "fact:applicant.ssn")
    record_decision(case, item, {"reviewer": "Jane", "role": "paralegal", "action": "set", "values": {"applicant.ssn": "123-45-6789"}, "note": "the card controls"})
    after = build_items(case, FIELD_MAP, TEMPLATE, CATALOG)
    assert after["pending"]["count"] == 0
    assert len(after["open"]) + after["pending"]["count"] + len(after["done"]) == total  # "0 of 183" became "1 of 183", not "1 of 185"
    assert waiting <= {i["id"] for i in after["open"]}  # the items that were counted ahead are the ones that arrived

    # the case itself is unchanged by the looking ahead: nothing was written, and a build twice says the same
    again = build_items(case, FIELD_MAP, TEMPLATE, CATALOG)
    assert again["pending"] == after["pending"] and [i["id"] for i in again["open"]] == [i["id"] for i in after["open"]]
