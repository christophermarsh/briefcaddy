"""A firm with offices in two states (src/offices.py): each case is filed
from its office -- the attorney, bar admission, address and letterhead on
its forms -- chosen by the client's state unless someone picks another.
The Florida office and its attorney here are made up."""

from __future__ import annotations

import json

import pytest

import offices
import settings
from factgraph import FactGraph

FLORIDA = {"office.name": "Miami, FL", "office.states": "FL", "firm.preparer_given_name": "MARIA", "firm.preparer_family_name": "EXEMPLO",
           "firm.attorney_bar_number": "1234567", "firm.licensing_authority": "Supreme Court of Florida", "firm.street": "100 EXAMPLE AVE",
           "firm.city": "MIAMI", "firm.state": "FL", "firm.zip": "33101", "firm.phone": "3055550100", "firm.email": "MIAMI@EXAMPLE.COM",
           "office.signer": "Maria Exemplo, Esq."}


@pytest.fixture
def two_offices(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    from conftest import save_shipped_office_as_the_firms

    save_shipped_office_as_the_firms(tmp_path / "settings.json")  # Implementation note.
    oid = settings.add_office("Ana Attorney")
    settings.save(oid, FLORIDA, "Ana Attorney")
    return oid


def _case(tmp_path, state="MA"):
    d = tmp_path / "client"
    d.mkdir(exist_ok=True)
    g = FactGraph("client")
    g.add_source("applicant.physical_state", "portal questionnaire", "intake_questionnaire", state, state, 0.95, tier=3)
    for key, value in {"firm.preparer_family_name": "Example", "firm.attorney_bar_number": "DEMO-000000",
                       "applicant.mailing_in_care_of": "Example Immigration Office", "applicant.mailing_street": "123 Example Street",
                       "applicant.mailing_city": "Boston", "applicant.mailing_state": "MA", "applicant.mailing_zip": "02110"}.items():
        g.add_source(key, "firm_profile.json", "firm_profile", value, value, 1.0)
    return d, g


def test_a_case_files_from_the_office_for_its_state(two_offices, tmp_path):
    d, g = _case(tmp_path, "FL")
    assert offices.for_case(d, "FL")["name"] == "Miami, FL" and offices.for_case(d, "MA")["id"] == offices.MAIN
    assert offices.for_case(d, "TX")["why"] == "the main office"  # no office files for it
    offices.apply(g, d)
    assert g.get("firm.preparer_family_name").value == "EXEMPLO" and g.get("firm.attorney_bar_number").value == "1234567"
    # the client's safe mailing address was the firm's: now the Florida office's
    assert (g.get("applicant.mailing_street").value, g.get("applicant.mailing_city").value) == ("100 EXAMPLE AVE", "MIAMI")


def test_someone_can_move_a_case_and_a_reviewers_answer_stays(two_offices, tmp_path):
    d, g = _case(tmp_path, "MA")
    g.set_by_review("applicant.mailing_street", "9 OTHER ST", "Paulo")
    offices.choose(d, two_offices, "Paulo")
    office = offices.for_case(d, "MA")
    assert office["name"] == "Miami, FL" and office["why"] == "chosen by Paulo"
    offices.apply(g, d)
    assert g.get("applicant.mailing_street").value == "9 OTHER ST" and g.get("firm.preparer_family_name").value == "EXEMPLO"
    with pytest.raises(LookupError):
        offices.choose(d, "office_99", "Paulo")


def test_the_letter_carries_the_offices_letterhead_and_signer(two_offices, tmp_path):
    d, _ = _case(tmp_path, "FL")
    config = {"letterhead": {"name_bold": "COTE LLP", "address": "123 Example Street, Boston, MA, 02110"},
              "signer": {"name": "Andrew G. FictionalMarkerA, Esq.", "lines": ["Tel-(617) 884-1000"]}}
    out = offices.letter(config, d, "FL")
    assert out["letterhead"]["address"] == "100 EXAMPLE AVE, MIAMI, FL, 33101" and out["letterhead"]["name_bold"] == "Example Immigration Office"  # the firm's name from Settings
    assert out["letterhead"]["tagline"] == "" and out["letterhead"]["attorneys"] == ""  # the shipped letter's roster is the sample firm's: none unless the office lists one
    assert out["signer"]["name"] == "Maria Exemplo, Esq." and "Tel-(305) 555-0100" in out["signer"]["lines"]
    main = offices.letter(config, d, "MA")  # Implementation note.
    assert main["letterhead"]["address"] == "123 Example Street, Boston, MA, 02110" and main["letterhead"]["name_light"] == "" and main["signer"]["name"] == ""


def test_an_office_missing_its_attorney_or_address_holds_the_packet(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    oid = settings.add_office("Ana Attorney")
    settings.save(oid, {"office.name": "Orlando, FL", "office.states": "FL"}, "Ana Attorney")
    d, _ = _case(tmp_path, "FL")
    assert offices.problems(d, "FL") == ["The Orlando, FL office is missing the attorney's name, the bar number, the street, the city, "
                                         "the ZIP code, the phone: fill it in on the Settings page."]
    settings.remove_office(oid, "Ana Attorney")
    assert offices.for_case(d, "FL")["id"] == offices.MAIN
    assert json.loads(settings.PATH.read_text(encoding="utf-8"))["_removed"][0]["values"]["office.name"] == "Orlando, FL"  # kept in the history


def test_the_firms_client_list_can_name_the_office(two_offices, tmp_path, monkeypatch):
    portal = tmp_path / "portal"
    (portal / "clients" / "client").mkdir(parents=True)
    (portal / "clients" / "client" / "profile.json").write_text(json.dumps({"office": "Miami, FL"}), encoding="utf-8")
    monkeypatch.setenv("PORTAL_DATA", str(portal))
    d, _ = _case(tmp_path, "MA")  # a Massachusetts address, but the list says Miami
    office = offices.for_case(d, "MA")
    assert office["name"] == "Miami, FL" and office["why"] == "on the firm's client list"
    offices.choose(d, offices.MAIN, "Paulo")  # a choice on the case wins over the list
    assert offices.for_case(d, "MA")["id"] == offices.MAIN
