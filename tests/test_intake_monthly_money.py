"""Optional ordinary intake amounts, with fictional clients and no provider delivery."""
import pytest
from fastapi.testclient import TestClient

import eoir26a
from communication_fixture import installation, approve_client, accepted_link
from portal.app import create_app
from portal.bank import all_questions, answers_to_facts, clean, load_bank, localized, missing_required
from portal.notify import Notifier

H = {"X-Portal": "1"}
IDS = tuple(eoir26a.questions())


@pytest.fixture
def intake(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    root = data / "portal"
    app = create_app(root, secure_cookies=False, notifier=Notifier(root / "outbox.jsonl", env={}))
    store = app.state.store
    (data / "clients" / "fictional-income").mkdir()
    store.add_client("fictional-income", "Fictional Income", email="income@fictional.example", language="en")
    approve_client(store, "fictional-income")
    with TestClient(app) as client:
        client.get("/l/" + accepted_link(store, "fictional-income"))
        yield client, store, root


@pytest.mark.parametrize("filing", [None, "i485"])
def test_intake_has_optional_monthly_amounts_without_request(filing):
    bank = load_bank(filing=filing)
    section = next(s for s in bank["sections"] if s["id"] == "monthly_money")
    assert tuple(q["id"] for q in section["questions"]) == IDS
    assert all(q["required"] is False and q["allow_unsure"] for q in section["questions"])
    assert len(all_questions(bank)) == sum(len(s["questions"]) for s in bank["sections"])
    assert not set(IDS).intersection(missing_required(bank, {}))
    assert "monthly_money" not in {s["id"] for s in load_bank(filing="first_contact")["sections"]}


@pytest.mark.parametrize("language", ["en", "pt", "es", "ht"])
def test_intake_localizes_neutral_optional_copy_and_every_line(language):
    section = next(s for s in localized(load_bank(), language, {}) if s["id"] == "monthly_money")
    assert section["title"] and section["why"] and section["intro"]
    assert "EOIR" not in section["why"] and "I-912" not in section["why"]
    assert all(q["label"] and q["help"] and q["tip"] for q in section["questions"])


def test_unknown_blank_and_zero_have_distinct_values_and_fact_provenance():
    bank = load_bank()
    q = all_questions(bank)[IDS[0]]
    assert clean(q, None) == (None, "")
    assert clean(q, "0") == ("0.00", "")
    assert clean(q, "Unsure") == ("Unsure", "")
    assert clean(q, "1.500,25") == ("1500.25", "")
    assert clean(q, "bad") == (None, "invalid_money")
    assert clean(eoir26a.questions()[IDS[0]], "Unsure") == (None, "invalid_money")
    assert answers_to_facts({IDS[0]: None}, bank) == []
    zero = answers_to_facts({IDS[0]: "0.00"}, bank)
    assert [(f.fact_key, f.raw_value, f.normalized_value) for f in zero] == [(q["fact"], "0.00", "0.00")]
    unknown = answers_to_facts({IDS[0]: "Unsure"}, bank)
    assert [(f.fact_key, f.normalized_value) for f in unknown] == [("questionnaire.unsure." + q["fact"], q["label"]["en"])]
    assert eoir26a.parse_money("Unsure") is None


def test_api_collects_saves_and_clears_without_waiver_or_delivery(intake):
    client, store, root = intake
    assert client.get("/api/me").json()["money"] is None
    response = client.put("/api/answers", json={IDS[0]: "0", IDS[1]: "Unsure"}, headers=H)
    assert response.status_code == 200
    assert response.json()["answers"][IDS[0]] == "0.00"
    assert response.json()["answers"][IDS[1]] == "Unsure"
    assert set(IDS[:2]).issubset(response.json()["accepted"])
    assert response.json()["money"] is None
    assert not store.fee_waiver("fictional-income")
    assert not (root / "outbox.jsonl").exists()
    response = client.put("/api/answers", json={IDS[0]: None}, headers=H)
    assert response.status_code == 200 and IDS[0] not in store.answers("fictional-income")


def test_completed_questionnaire_is_not_reopened_for_optional_money(intake):
    client, store, _ = intake
    store.save_answers("fictional-income", {IDS[0]: "100.00"})
    store.update_profile("fictional-income", status="submitted")
    response = client.put("/api/answers", json={IDS[0]: "200"}, headers=H)
    assert response.status_code == 409
    assert store.answers("fictional-income")[IDS[0]] == "100.00"
    assert store.profile("fictional-income")["status"] == "submitted"


@pytest.mark.parametrize("lock", ["signing", "signed"])
def test_all_shared_ids_are_locked_even_without_request(intake, lock):
    client, store, _ = intake
    store.save_fee_waiver("fictional-income", {lock: {"fictional": True}})
    assert client.get("/api/me").json()["money_locked"] is True
    for qid in IDS:
        response = client.put("/api/answers", json={qid: "0"}, headers=H)
        assert response.status_code == 409 and response.json()["detail"] == "fee_waiver_locked"
    assert store.answers("fictional-income") == {}


def test_requested_waiver_reuses_intake_unknown_without_treating_it_as_zero(intake):
    client, store, _ = intake
    store.save_fee_waiver("fictional-income", {"request": {"id": "F1", "kind": "motion"}})
    store.update_profile("fictional-income", status="submitted")
    response = client.put("/api/answers", json={IDS[0]: "Unsure", IDS[1]: "0"}, headers=H)
    assert response.status_code == 200
    money = response.json()["money"]
    assert not money["complete"] and IDS[0] in money["missing"] and IDS[1] not in money["missing"]
    assert client.put("/api/answers", json={"given_name": "Changed"}, headers=H).status_code == 409
