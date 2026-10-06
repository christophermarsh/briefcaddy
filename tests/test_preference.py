"""The family preference wait (src/preference.py): the visa class from the
relationship, a child's category from age and marriage, the priority date
printed on the I-130 notice, the month's family Visa Bulletin setting (never
guessed: a test sets its own), the journey's wait, and which family packet the
case files now. Every client value -- and every cut-off -- is CONSTRUCTED.
"""

import json
from datetime import date

import pytest

import preference
from factgraph import FactGraph

TODAY = date(2026, 10, 1)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.country_of_birth": "BRAZIL"}
                       | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _i130(g, kind, when, pd=None):
    slug = f"{kind}_{when.replace('-', '')}"
    g.add_source(f"folder.uscis_case.IOE0999000555.{slug}", f"{kind}.pdf", "uscis_notice", "IOE0999000555", f"I-130 {kind.upper()}, {when}", 0.9)
    if pd:
        g.add_source(f"folder.notice.IOE0999000555.{slug}.priority_date", f"{kind}.pdf", "uscis_notice", pd, pd, 0.85)


@pytest.fixture
def bulletin(monkeypatch):
    """A made-up October 2026 setting: F4, all chargeability areas, cut-off 01/01/2008."""
    vb = json.loads(preference.SETTINGS.read_text(encoding="utf-8"))
    vb["month"], vb["cutoff"]["F4"]["ALL CHARGEABILITY"] = "October 2026", "2008-01-01"
    monkeypatch.setattr(preference, "settings", lambda: vb)
    return vb


def test_a_childs_category_from_age_and_marriage():
    assert preference.child_category(_graph(petitioner__status="USC", applicant__dob="2001-01-01", applicant__marital_status="Single"), TODAY) \
        == "Unmarried son/daughter 21+ of U.S. citizen"                                                    # F1
    assert preference.child_category(_graph(petitioner__status="USC", applicant__dob="2012-01-01"), TODAY) == "Child under 21 of U.S. citizen"
    assert preference.child_category(_graph(petitioner__status="LPR", applicant__dob="2009-05-01"), TODAY) == "Child under 21 of LPR"
    assert preference.child_category(_graph(petitioner__status="LPR", applicant__dob="2000-05-01", applicant__marital_status="Married"), TODAY) is None
    assert preference.visa_class(_graph(applicant__filing_category="Sibling of U.S. citizen")) == "F4"


def test_unset_the_case_says_so_and_never_guesses():
    s = preference.status(_graph(applicant__filing_category="Sibling of U.S. citizen"), TODAY)
    assert s["current"] is None and any("Set this month's family Visa Bulletin (F4, All Chargeability) on the Settings page" in p for p in s["problems"])
    assert preference.status(_graph(applicant__filing_category="Spouse of U.S. citizen"), TODAY)["current"] is True   # an immediate relative


def test_the_priority_date_from_the_notice_against_the_cut_off(bulletin, monkeypatch):
    early, late = _graph(applicant__filing_category="Sibling of U.S. citizen"), _graph(applicant__filing_category="Sibling of U.S. citizen")
    _i130(early, "receipt", "2007-05-04", pd="2007-05-01")
    _i130(late, "receipt", "2010-03-04", pd="2010-03-01")
    assert preference.status(early, TODAY)["current"] is True and preference.status(late, TODAY)["current"] is False
    assert "priority date 05/01/2007" in preference.status(early, TODAY)["text"]
    monkeypatch.setattr(preference, "settings", lambda: dict(bulletin, month="September 2026"))   # last month's setting: no answer
    stale = preference.status(early, TODAY)
    assert stale["current"] is None and any("update it for October 2026" in p for p in stale["problems"])


def test_the_journey_waits_then_points_to_the_i485(tmp_path, monkeypatch, bulletin):
    import journey

    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": None, "pd": None, "current": None, "problems": []})
    d = tmp_path / "case"
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    late = _graph(applicant__filing_category="Sibling of U.S. citizen", petitioner__status="USC", family__relationship="Sibling")
    _i130(late, "receipt", "2010-03-04", pd="2010-03-01")
    j = journey.journey(d, TODAY, graph=late)
    assert j["track"] == "family" and j["stage"] == "family_wait" and "not yet current" in j["why"]
    assert not next(f for f in j["next_filings"] if f["filing"] == "family")["now"]
    early = _graph(applicant__filing_category="Sibling of U.S. citizen", petitioner__status="USC", family__relationship="Sibling")
    _i130(early, "approval", "2009-01-04", pd="2007-05-01")
    j = journey.journey(d, TODAY, graph=early)
    assert j["stage"] == "family_wait" and "current: the green card application can be filed" in j["why"]
    assert next(f for f in j["next_filings"] if f["filing"] == "family")["now"]


def test_which_family_packet_the_case_files_now(bulletin):
    sibling = _graph(applicant__filing_category="Sibling of U.S. citizen")
    assert preference.packet_variant(sibling, TODAY) == "petition_only"       # no I-130 yet, not current: the petition alone sets the date
    _i130(sibling, "receipt", "2007-05-04", pd="2007-05-01")
    assert preference.packet_variant(sibling, TODAY) == "i485_only"           # the I-130 is on file: the I-485 follows it
    assert preference.packet_variant(_graph(applicant__filing_category="Spouse of U.S. citizen"), TODAY) is None   # together
