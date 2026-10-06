"""Form G-1145 (src/enotice.py): one per lockbox package, on top, to the office's e-mail unless the firm chooses the client
or nobody. The client and the office here are made up."""

from __future__ import annotations

import pytest
from pypdf import PdfReader

import enotice
import settings
from factgraph import FactGraph


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    from conftest import save_shipped_office_as_the_firms

    save_shipped_office_as_the_firms(tmp_path / "settings.json")  # Implementation note.
    return tmp_path


def _graph():
    g = FactGraph("c")
    for k, v in {"applicant.family_name": "EXEMPLO SOUZA", "applicant.given_name": "ANA", "applicant.middle_name": "CLARA",
                 "applicant.email": "ana@example.com", "applicant.mobile_phone": "(555) 010-0142", "applicant.physical_state": "MA"}.items():
        g.add_source(k, "portal questionnaire", "intake_questionnaire", v, v, 0.95, tier=3)
    return g


def _values(path):
    return {k.split(".")[-1]: str(v.get("/V")) for k, v in (PdfReader(str(path)).get_fields() or {}).items() if v.get("/V")}


def test_lockbox_packages_get_one_to_the_offices_email(firm):
    out = enotice.render(firm, _graph(), {"filing": "i485"})
    vals = _values(firm / out["file"])
    assert (vals["LastName[0]"], vals["FirstName[0]"], vals["MiddleName[0]"]) == ("EXEMPLO SOUZA", "ANA", "CLARA")
    assert vals["Email[0]"] == "briefcaddy-demo@example.test" and "MobilePhoneNumber[0]" not in vals  # the office's: email only
    assert enotice.render(firm, _graph(), {"filing": "bia"}) is None  # the BIA isn't a lockbox
    assert enotice.render(firm, _graph(), {"filing": "i589", "variant": "in_court"}) is None  # filed with the immigration court


def test_the_firm_can_send_it_to_the_client_or_skip_it(firm):
    settings.save("enotice", {"recipient": "client"}, "Ana Attorney")
    vals = _values(firm / enotice.render(firm, _graph(), {"filing": "n400"})["file"])
    assert vals["Email[0]"] == "ana@example.com" and vals["MobilePhoneNumber[0]"] == "5550100142"  # digits only, as the form asks
    settings.save("enotice", {"recipient": "none"}, "Ana Attorney")
    assert enotice.render(firm, _graph(), {"filing": "n400"}) is None
