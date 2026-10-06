"""The last home outside the U.S. cannot silently contain a U.S. country."""
# ruff: noqa: F811 -- shared fixtures
import pytest
from fastapi.testclient import TestClient

from assemble import consistency_findings
from factgraph import FactGraph
from portal.app import create_app
from portal.bank import all_questions, clean, load_bank, missing_required
from portal.demo import answers
from test_prospects import firm, server  # noqa: F401
from test_staff_consent_http import client
from test_staff_questionnaire_access import access


@pytest.mark.parametrize("country", ["US", "U.S.", "USA", "U.S.A.", "United States", "United States of America", "EUA", "Estados Unidos", "EE.UU.", "Etazini"])
def test_us_country_has_client_validation_and_staff_crosscheck(country):
    bank = load_bank()
    q = all_questions(bank)["last_foreign_address"]
    value = {"street": "10 Fictional Street", "city": "Boston", "province": "MA", "country": country}
    assert clean(q, value)[1] == "foreign_country_us"
    assert "last_foreign_address" in missing_required(bank, answers() | {"last_foreign_address": value})
    graph = FactGraph("fictional")
    graph.add_source("applicant.last_foreign_country", "portal questionnaire", "intake_questionnaire", country, country, .95, tier=3)
    found = dict(consistency_findings(graph))
    assert "United States" in found["applicant.last_foreign_country"]


def test_non_us_foreign_country_and_us_current_address_are_valid():
    q = all_questions(load_bank())["last_foreign_address"]
    assert not clean(q, {"country": "Brasil"})[1]
    assert not clean({"type": "us_address"}, {"street": "10 Fictional Street", "zip": "02110", "country": "United States"})[1]


def test_saved_bad_answer_is_visible_and_blocks_submit_until_corrected(server, firm):
    cid = client(server, language="pt")
    wrong = answers()
    wrong["last_foreign_address"] = dict(wrong["last_foreign_address"], country="United States")
    firm["store"].save_answers(cid, wrong)  # an answer recorded before validation was added
    token = access(server, cid)["url"].rsplit("/", 1)[1]
    headers = {"X-Portal": "1"}
    with TestClient(create_app(firm["portal"])) as phone:
        assert phone.post("/api/access-link", json={"token": token}, headers=headers).status_code == 200
        state = phone.get("/api/me").json()
        assert state["answer_checks"]["last_foreign_address"] == "foreign_country_us"
        assert state["answers"]["last_foreign_address"]["country"] == "United States"
        assert phone.post("/api/submit", json={"agree": True, "signature": "Fictional Tester"}, headers=headers).status_code == 400
        rejected = phone.put("/api/answers", json={"last_foreign_address": {"country": "EUA"}}, headers=headers).json()
        assert rejected["errors"]["last_foreign_address"] == "foreign_country_us"
        assert "last_foreign_address" not in rejected["accepted"]
        assert firm["store"].answers(cid)["last_foreign_address"]["country"] == "United States"
        corrected = dict(wrong["last_foreign_address"], country="Brasil")
        saved = phone.put("/api/answers", json={"last_foreign_address": corrected}, headers=headers).json()
        assert not saved["answer_checks"] and "last_foreign_address" in saved["accepted"]
        assert phone.post("/api/submit", json={"agree": True, "signature": "Fictional Tester"}, headers=headers).status_code == 200
