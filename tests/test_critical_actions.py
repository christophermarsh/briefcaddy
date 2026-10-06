"""Critical proof transport uses the existing signed-in case ACL."""

import documents
import critical_review
import restricted
import subject_attribution as subjects
from review.state import load_decision_log
from test_subject_actions import world  # existing isolated authenticated fixture  # noqa: F401 -- pytest fixture registration and helper reexports
from test_restricted import call

KEY = "applicant.i94_number"


def assign(case):
    row = subjects.views(case)[0]
    person = documents.read(case)["case_subjects"]["people"][0]["id"]
    subjects.assign(case, row["instance_id"], row["fingerprint"], {"holder": person}, "Reviewer", "paralegal")


def payload(case):
    return {"client": case.name, "item_id": "fact:" + KEY, "action": "confirm", "reviewer": "Forged Attorney", "role": "attorney",
            "evidence_fingerprints": {KEY: critical_review.context(case)[KEY]["fingerprint"]}}


def test_signed_in_actor_wins_and_stale_proof_has_no_partial_batch_write(world):  # noqa: F811 -- pytest fixture injection
    case, base, cookie, app = world
    assign(case)
    body = payload(case)
    name = "applicant.given_name"
    status, text = call(base + "/api/decide", cookie, body | {"decisions": [
        {"item_id": "fact:" + KEY, "action": "confirm"},
        {"item_id": "fact:" + name, "action": "confirm", "evidence_fingerprints": {name: "stale"}}]})
    assert status == 400, text
    assert not (case / "decisions.json").exists()
    status, text = call(base + "/api/decide", cookie, body)
    assert status == 200, text
    saved = load_decision_log(case)["fact:" + KEY]
    assert saved["reviewer"] == "Signed In Reviewer" and saved["role"] == "paralegal"
    assert saved["evidence_confirmation"]["basis"] == "manual_retained_source_review"


def test_restricted_case_refuses_critical_confirmation(world):  # noqa: F811 -- pytest fixture injection
    case, base, cookie, app = world
    assign(case)
    body = payload(case)
    restricted.mark(case, True, "Synthetic protected case", "Attorney", "attorney")
    status, text = call(base + "/api/decide", cookie, body)
    assert status == 404, text
    assert not (case / "decisions.json").exists()


def test_changed_quality_makes_browser_fingerprint_stale(world):  # noqa: F811 -- pytest fixture injection
    case, base, cookie, app = world
    assign(case)
    body = payload(case)
    doc = documents.read(case)["documents"][0]
    documents.set_quality(case, doc["id"], "blurry", "Reviewer", "paralegal")
    status, text = call(base + "/api/decide", cookie, body)
    assert status == 400 and "changed" in text
    assert not (case / "decisions.json").exists()
