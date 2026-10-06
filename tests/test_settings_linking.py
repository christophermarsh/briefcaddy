"""The Settings page's store (src/settings.py) laid over the shipped defaults;
family members linked both ways (src/journey.py); questions for the client
gathered and sent once (src/portal/store.py). Every client value is CONSTRUCTED.
"""

import json
from datetime import date

import pytest

import settings


@pytest.fixture
def firm_settings(tmp_path, monkeypatch):
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    return settings


def test_the_visa_bulletin_is_set_on_the_page_not_in_a_file(firm_settings):
    from fill.cover_letter import load_config

    assert load_config()["visa_bulletin"]["month"] is None                               # the shipped default: never a guess
    firm_settings.save("visa_bulletin_eb4", {"month": "October 2026", "eb4_cutoff/MEXICO": "Current", "eb4_cutoff/ALL CHARGEABILITY": "2021-02-15"}, "Ana Attorney")
    vb = load_config()["visa_bulletin"]
    assert vb["month"] == "October 2026" and vb["eb4_cutoff"]["MEXICO"] == "C" and vb["eb4_cutoff"]["ALL CHARGEABILITY"] == "2021-02-15"
    with pytest.raises(ValueError, match="a date"):
        firm_settings.save("visa_bulletin_eb4", {"eb4_cutoff/INDIA": "Feb 2021"}, "Ana Attorney")
    with pytest.raises(ValueError, match="your name"):
        firm_settings.save("visa_bulletin_eb4", {"month": "October 2026"}, "")
    firm_settings.save("visa_bulletin_eb4", {"month": "November 2026"}, "Ana Attorney")
    saved = json.loads(firm_settings.PATH.read_text(encoding="utf-8"))["visa_bulletin_eb4"]
    assert saved["updated_by"] == "Ana Attorney" and saved["history"][0]["values"]["month"] == "October 2026"   # the earlier value is kept
    assert load_config()["visa_bulletin"]["eb4_cutoff"]["MEXICO"] == "C"                   # a later save keeps the other values


def test_every_loader_reads_the_page(firm_settings):
    import fees
    import payment
    import preference
    from fill.companion import load_profile

    firm_settings.save("visa_bulletin_family", {"month": "October 2026", "cutoff/F4/MEXICO": "2001-04-22"}, "Ana Attorney")
    firm_settings.save("fees", {"paper/i90": "$470"}, "Ana Attorney")
    firm_settings.save("firm", {"firm.eoir_id": "AB123456"}, "Ana Attorney")
    firm_settings.save("payment", {"card_holder": "client"}, "Ana Attorney")
    assert preference.settings()["cutoff"]["F4"]["MEXICO"] == "2001-04-22" and preference.settings()["cutoff"]["F1"]["MEXICO"] is None
    assert fees.load(date(2026, 10, 1))["paper"]["i90"] == 470
    assert load_profile()["firm"]["firm.eoir_id"] == "AB123456" and payment.settings()["card_holder"] == "client"
    sections = {s["id"]: s for s in firm_settings.specs()}
    assert next(f for f in sections["fees"]["fields"] if f["key"] == "paper/i90")["value"] == 470 and sections["fees"]["updated_by"] == "Ana Attorney"


def _case(root, name):
    d = root / name
    d.mkdir()
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    return d


def test_family_members_are_linked_both_ways(tmp_path):
    import journey

    mom, kid = _case(tmp_path, "case-mom"), _case(tmp_path, "case-kid")
    authorized = lambda case: case in {"case-mom", "case-kid"}  # explicit local unit context; protected routes use current accounts/ACL
    journey.mark(mom, "link", "Paula", value={"client": "case-kid", "relationship": "Child"}, may_open=authorized)
    linked = lambda d: {x["client"]: x["relationship"] for x in journey._status(d)["journey"]["linked"]}  # noqa: E731
    assert linked(mom) == {"case-kid": "Child"} and linked(kid) == {"case-mom": "Parent"}
    with pytest.raises(ValueError, match="another client"):
        journey.mark(mom, "link", "Paula", value={"client": "case-mom", "relationship": "Spouse"}, may_open=authorized)
    journey.mark(kid, "unlink", "Paula", value={"client": "case-mom"}, may_open=authorized)
    assert linked(mom) == {} and linked(kid) == {}


def test_questions_wait_in_a_list_and_go_out_once(tmp_path):
    from portal.store import PortalStore

    store = PortalStore(tmp_path / "portal")
    store.add_client("c1", "Ana Exemplo", email="ana@example.com", language="pt")
    store.add_request("c1", "Your spouse's A-Number, if any", None, "Paula", draft=True)
    store.add_request("c1", "Your I-94", "i94", "Paula", draft=True)
    assert [r["status"] for r in store.requests("c1")] == ["draft", "draft"]               # the portal shows only open requests
    sent = store.send_drafts("c1", "Paula")
    assert len(sent) == 2 and all(r["status"] == "open" for r in store.requests("c1"))
    store.add_request("c1", "A typo", None, "Paula", draft=True)
    store.drop_draft("c1", store.requests("c1")[-1]["id"])
    assert len(store.requests("c1")) == 2
    with pytest.raises(LookupError):
        store.drop_draft("c1", store.requests("c1")[0]["id"])                               # a sent question stays
