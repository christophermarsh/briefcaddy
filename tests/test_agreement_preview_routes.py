"""Optional exact agreement preview: current case ACL, no letter, stale bindings."""
import json
from datetime import datetime

import pytest

import clock
import engagement
import settings
from assignment_route_fixtures import world, app, server, call, sign_in  # noqa: F401 -- pytest fixture registration and helper reexports
from test_review_evidence_routes import controls  # noqa: F401 -- pytest fixture registration and helper reexports

TERMS = {"client": "case-ana", "filings": ["i485"],
         "fee": "Fictional professional service fee: $100, payable as agreed.",
         "government_fees": "Government filing fees are separate.",
         "additions": "Fictional additional terms for review."}


@pytest.fixture(autouse=True)
def letter_controls(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))


def post_preview(server, cookie, body=None):  # noqa: F811 -- pytest fixture injection
    return call(server + "/api/engagement-preview", cookie, TERMS if body is None else body)


def test_preview_is_unsaved_exact_and_creation_remains_explicit(server, world):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    before = engagement.read(world / "case-ana")
    status, raw = post_preview(server, cookie)
    assert status == 200, raw
    preview = json.loads(raw)
    assert preview["preview"] is True
    assert len(preview["preview_sha256"]) == 64
    assert not ({"id", "made_by", "signature", "approved"} & preview.keys())
    assert preview["filings"] == TERMS["filings"] and preview["fee"] == TERMS["fee"]
    assert preview["texts"]["en"] and preview["wording_hash"]
    assert engagement.read(world / "case-ana") == before
    assert not (world / "case-ana" / engagement.FOLDER).exists()
    status, raw = call(server + "/api/engagement", cookie, TERMS | {"action": "make", "preview_sha256": preview["preview_sha256"]})
    assert status == 200, raw
    saved = engagement.read(world / "case-ana")["letters"][-1]
    assert saved["texts"] == preview["texts"]
    assert not saved.get("sent") and not saved.get("signature")


@pytest.mark.parametrize("change", [{"fee": "Changed fictional fee."}, {"filings": ["n400"]},
                                   {"government_fees": "Changed government wording."}, {"additions": "Changed additions."}],
                         ids=["fee", "filing", "government", "additions"])
def test_displayed_preview_refuses_changed_input_without_creating(server, world, change):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    status, raw = post_preview(server, cookie)
    assert status == 200, raw
    digest = json.loads(raw)["preview_sha256"]
    status, raw = call(server + "/api/engagement", cookie, TERMS | change | {"action": "make", "preview_sha256": digest})
    assert status == 400 and "preview changed" in json.loads(raw)["error"]
    assert not engagement.read(world / "case-ana")["letters"]


def test_displayed_preview_refuses_new_current_date(server, world, monkeypatch):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    status, raw = post_preview(server, cookie)
    assert status == 200, raw
    digest = json.loads(raw)["preview_sha256"]
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 6, 10, 30))
    cookie = sign_in(server, "jane@firm.example")  # fresh session, same actor, prior preview digest
    status, raw = call(server + "/api/engagement", cookie, TERMS | {"action": "make", "preview_sha256": digest})
    assert status == 400 and "preview changed" in json.loads(raw)["error"]
    assert not engagement.read(world / "case-ana")["letters"]


def test_preview_optional_make_preserves_existing_api_contract(server, world):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    status, raw = call(server + "/api/engagement", cookie, TERMS | {"action": "make"})
    assert status == 200, raw
    assert len(engagement.read(world / "case-ana")["letters"]) == 1


def test_preview_gate_hides_restricted_missing_and_revoked_cases(server, world):  # noqa: F811 -- pytest fixture injection
    import restricted
    cookie = sign_in(server, "jane@firm.example")
    denied = post_preview(server, cookie, TERMS | {"client": "case-rosa"})
    missing = post_preview(server, cookie, TERMS | {"client": "missing-case"})
    assert denied == missing and denied[0] == 404
    assert json.loads(denied[1]) == {"error": "unknown client"}
    restricted.name_person(world / "case-rosa", "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Doe")
    assert post_preview(server, cookie, TERMS | {"client": "case-rosa"})[0] == 200
    restricted.name_person(world / "case-rosa", "jane@firm.example", False, "Sam Attorney", "attorney", "Jane Doe")
    assert post_preview(server, cookie, TERMS | {"client": "case-rosa"}) == missing


@pytest.mark.parametrize("change", [{"filings": "i485"}, {"filings": []}, {"filings": ["unknown"]},
                                   {"filings": [None]}, {"fee": []}, {"government_fees": {}}, {"additions": None}],
                         ids=["filing-string", "filing-empty", "filing-unknown", "filing-null", "fee-list", "government-object", "additions-null"])
def test_preview_rejects_malformed_terms(server, world, change):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    status, raw = post_preview(server, cookie, TERMS | change)
    assert status == 400, raw
    assert not engagement.read(world / "case-ana")["letters"]


def test_invalid_supplied_preview_digest_is_not_a_make_request(server, world):  # noqa: F811 -- pytest fixture injection
    cookie = sign_in(server, "jane@firm.example")
    status, raw = call(server + "/api/engagement", cookie, TERMS | {"action": "make", "preview_sha256": None})
    assert status == 400 and "preview" in json.loads(raw)["error"]
    assert not engagement.read(world / "case-ana")["letters"]
