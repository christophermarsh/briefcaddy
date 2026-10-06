"""Where a case stands (src/journey.py), from whatever stage the firm picks it up at.

The address-block lines are the three real I-360 approval notices' own OCR
(the firm's name and attorney; no client details). Every other notice
line here is CONSTRUCTED in the I-797 layout -- no real RFE, appointment,
denial or I-485 approval is in a client folder yet -- and the receipt
numbers, dates and names are made up.
"""

import json
from datetime import date

import pytest

import journey
from extract.uscis_notice import addressed_to, extract, parse
from factgraph import FactGraph

TODAY = date(2026, 10, 1)


def _notice(g, doc, receipt, form, kind, when, **extra):
    """A notice as uscis_notice.extract() records it (one key per notice)."""
    slug = f"{kind}_{when.replace('-', '')}"
    titles = {"receipt": "RECEIPT", "approval": "APPROVAL", "rfe": "REQUEST FOR EVIDENCE", "denial": "DENIAL", "biometrics": "BIOMETRICS APPOINTMENT",
              "interview": "INTERVIEW", "rejection": "REJECTION"}
    g.add_source(f"folder.uscis_case.{receipt}.{slug}", doc, "uscis_notice", receipt, f"{form} {titles[kind]}, {when}", 0.9)
    g.add_source(f"folder.notice.{receipt}.{slug}.date", doc, "uscis_notice", when, when, 0.85)
    for name, value in extra.items():
        g.add_source(f"folder.notice.{receipt}.{slug}.{name}", doc, "uscis_notice", value, value, 0.85)


def _bundle(tmp_path, docs=None, status=None):
    d = tmp_path / "bundle"
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"classifications": docs or {}}), encoding="utf-8")
    # These unit cases supply the working graph explicitly. Keep the on-disk
    # empty bundle structurally valid for ancillary evidence/status readers.
    FactGraph(d.name).save(d / "fact_graph.json")
    if status:
        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def bulletin(monkeypatch):
    """The Visa Bulletin setting changes monthly: tests set it themselves."""
    state = {"current": None}
    monkeypatch.setattr(journey, "_visa", lambda graph, today: {"month": "October 2026", "area": "ALL CHARGEABILITY", "cutoff": None,
                                                                "pd": "2023-01-10", "current": state["current"], "problems": []})
    return state


@pytest.fixture(autouse=True)
def _firm_saved(tmp_path, monkeypatch):
    """Fictional example or implementation helper."""
    import settings
    from conftest import save_shipped_office_as_the_firms

    monkeypatch.setattr(settings, "PATH", tmp_path / "firm-settings.json")
    save_shipped_office_as_the_firms(tmp_path / "firm-settings.json")


def test_the_real_notices_address_block_names_the_firm():
    # the three real I-360 approvals, as OCR read them
    assert addressed_to("Example Immigration Office Notice Type: Approval Notice\nc/o Alex Example ) Class: SL6\n") == \
        "Example Immigration Office; Alex Example"
    assert addressed_to("clo Example Immigration Office Class: SL6\n") == "Example Immigration Office"
    assert addressed_to("GEORGES COTE LAW Notice Type: Approval Notice\n") == "GEORGES COTE LAW"
    assert journey.representation([{"addressed_to": "GEORGES COTE LAW"}])["who"] == "firm"
    assert journey.representation([{"addressed_to": "c/o Alex Example"}])["who"] == "firm"
    other = journey.representation([{"addressed_to": "SMITH & JONES LLP"}])
    assert other["who"] == "other" and "SMITH & JONES LLP" in other["text"]


def test_a_request_for_evidence_its_due_date_and_an_appointment_are_read():
    # CONSTRUCTED notices in the I-797 layout
    head = "Receipt Number Case Type\nIOE0999000001 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS\nNotice Date\n09/01/2026\n"
    rfe = parse(head + "REQUEST FOR EVIDENCE\nPlease submit the evidence listed below by November 24, 2026.\n")
    assert (rfe.kind, rfe.due_date) == ("rfe", "2026-11-24")
    bio = parse(head + "ASC Appointment Notice\nDate and Time of Appointment: 10/14/2026 10:00 AM\n")
    assert (bio.kind, bio.appointment) == ("biometrics", "2026-10-14 10:00 AM")
    keys = {f.fact_key for f in extract(head + "ASC Appointment Notice\nDate: 10/14/2026\nTime: 9:30 AM\n")}
    assert {"folder.uscis_case.IOE0999000001.biometrics_20260901", "folder.notice.IOE0999000001.biometrics_20260901.appointment"} <= keys


def test_an_approved_i360_filed_by_the_firm_is_ready_for_the_i485(tmp_path):
    g = FactGraph("c")
    _notice(g, "approval.pdf", "IOE0999000002", "I-360", "approval", "2025-08-20", addressed_to="Example Immigration Office; Alex Example")
    j = journey.journey(_bundle(tmp_path, {"approval.pdf": "i360_approval"}), TODAY, graph=g)
    assert (j["track"], j["stage"]) == ("sij", "i485_ready")
    assert j["takeover"] == [] and j["representation"]["who"] == "firm"
    assert any("I-693" in s["text"] for s in j["steps"])


def test_waiting_for_the_visa_bulletin(tmp_path, bulletin):
    g = FactGraph("c")
    _notice(g, "approval.pdf", "IOE0999000002", "I-360", "approval", "2025-08-20", addressed_to="Example Immigration Office")
    bulletin["current"] = False
    j = journey.journey(_bundle(tmp_path, {"approval.pdf": "i360_approval"}), TODAY, graph=g)
    assert j["stage"] == "visa_wait"
    assert journey.client_view(j, "pt")["title"].startswith("I-360 aprovada")


def test_a_case_taken_over_mid_way_gets_the_takeover_checklist_and_its_deadlines(tmp_path):
    g = FactGraph("c")
    g.add_source("applicant.nta_present", "nta.pdf", "notice_to_appear", "Yes", "Yes", 0.9)
    _notice(g, "i360.pdf", "IOE0999000003", "I-360", "approval", "2025-02-03", addressed_to="SMITH IMMIGRATION LAW")
    _notice(g, "receipt.pdf", "IOE0999000004", "I-485", "receipt", "2026-06-01", addressed_to="SMITH IMMIGRATION LAW")
    _notice(g, "rfe.pdf", "IOE0999000004", "I-485", "rfe", "2026-09-01", due="2026-10-10")
    _notice(g, "bio.pdf", "IOE0999000004", "I-485", "biometrics", "2026-09-05", appointment="2026-10-14 10:00 AM")
    j = journey.journey(_bundle(tmp_path, {"nta.pdf": "notice_to_appear"}), TODAY, graph=g)
    assert j["stage"] == "i485_pending"
    ids = {t["id"] for t in j["takeover"]}
    assert {"takeover.g28", "takeover.foia", "takeover.eoir", "takeover.court_order"} <= ids
    assert "IOE0999000004" in next(t["text"] for t in j["takeover"] if t["id"] == "takeover.g28")  # the pending case, not the approved one
    # the dashboard's summary carries the takeover list into each person's work list
    assert {"takeover.g28", "nta"} <= {s["id"] for s in journey.summary(j)["steps"]}
    rfe = next(d for d in j["deadlines"] if "request for evidence" in d["what"])
    assert (rfe["date"], rfe["days_left"], rfe["level"], rfe["owner"]) == ("2026-10-10", 9, "urgent", "attorney")
    assert any(d["owner"] == "client" and d["date"] == "2026-10-14" for d in j["deadlines"])
    assert any(s["id"] == "nta" and s["urgent"] for s in j["steps"])
    view = journey.client_view(j, "es")
    # what happened, newest first: a request for evidence never explains itself to the client
    assert [h["text"] for h in view["happened"]][:2] == [
        "USCIS pidió más información sobre su I-485 (01/09/2026). La oficina ya se está encargando.",
        "USCIS confirmó que recibió su I-485 el 01/06/2026."]
    assert view["appointments"] == [{"date": "2026-10-14", "time": "10:00 AM", "what": "Toma de huellas (biometría)",
                                     "bring": "Lleve la carta de USCIS y una identificación con foto."}]
    # the takeover list goes once the firm records it handled the case
    journey.mark(tmp_path / "bundle", "done", "Paralegal", "takeover.foia")
    j = journey.journey(tmp_path / "bundle", TODAY, graph=g)
    assert next(t for t in j["takeover"] if t["id"] == "takeover.foia")["done"]["by"] == "Paralegal"


def test_an_answered_request_is_no_longer_due_and_a_denial_opens_the_motion_window(tmp_path):
    g = FactGraph("c")
    _notice(g, "rfe.pdf", "IOE0999000005", "I-360", "rfe", "2026-03-01", due="2026-05-25")
    _notice(g, "denial.pdf", "IOE0999000005", "I-360", "denial", "2026-09-15")
    j = journey.journey(_bundle(tmp_path, {"order.pdf": "sij_order"}), TODAY, graph=g)
    assert not any("request for evidence" in d["what"] for d in j["deadlines"])
    denial = next(d for d in j["deadlines"] if "denied" in d["what"])
    assert denial["date"] == "2026-10-18"  # 30 days + 3 for mail


def test_before_the_i360_the_21st_birthday_is_the_deadline(tmp_path):
    g = FactGraph("c")
    g.add_source("applicant.dob", "birth.pdf", "birth_certificate", "2005-12-01", "2005-12-01", 0.9)
    j = journey.journey(_bundle(tmp_path, {"order.pdf": "sij_order"}), TODAY, graph=g)
    assert j["stage"] == "i360_ready"
    age = next(d for d in j["deadlines"] if d["id"] == "age_21")
    assert (age["date"], age["level"]) == ("2026-11-30", "soon")


def test_a_resident_gets_the_citizenship_date_and_the_sij_parent_rule(tmp_path):
    g = FactGraph("c")
    _notice(g, "i485.pdf", "IOE0999000006", "I-485", "approval", "2024-03-15")
    j = journey.journey(_bundle(tmp_path, {"i360.pdf": "i360_approval"}), TODAY, graph=g)
    assert j["stage"] == "resident" and j["citizenship_from"] == "2028-12-15"  # 5 years less 90 days
    assert any("natural or prior adoptive parent" in s["text"] for s in j["steps"])


def test_a_person_can_set_the_stage_when_the_folder_is_incomplete(tmp_path):
    g = FactGraph("c")
    d = _bundle(tmp_path, {"q.pdf": "intake_questionnaire"})
    assert journey.journey(d, TODAY, graph=g)["stage"] == "state_court"
    journey.mark(d, "stage", "Attorney", value="i360_pending", note="per the prior attorney's file")
    j = journey.journey(d, TODAY, graph=g)
    assert (j["stage"], j["found"], j["set_by"]["by"]) == ("i360_pending", "state_court", "Attorney")
    with pytest.raises(ValueError):
        journey.mark(d, "stage", "Attorney", value="nonsense")
    with pytest.raises(ValueError):
        journey.mark(d, "done", "", "x")


def test_the_portal_gets_the_view_and_news_only_when_something_changed(tmp_path, monkeypatch):
    from portal.store import PortalStore

    from communication_fixture import installation, approve_client
    from portal.notify import Notifier

    installation_data = installation(tmp_path, monkeypatch)
    data = installation_data / "clients"
    (data / "c1").mkdir(parents=True)
    (data / "c1" / "fact_graph.json").write_text("{}", encoding="utf-8")
    portal = installation_data / "portal"
    store = PortalStore(portal)
    store.add_client("c1", "Ana Exemplo", email="ana@example.com", language="pt")
    approve_client(store, "c1")
    sent = []
    def accept_fictional_email(self, to, subject, body, kind):
        sent.append({"subject": subject, "body": body})
        return "sent"
    monkeypatch.setattr(Notifier, "_email", accept_fictional_email)
    stages = iter(["i360_pending", "i360_pending", "visa_wait"])
    fake = lambda d, today=None: {"stage": (s := next(stages)), "stages": [{"id": x} for x in ("i360_pending", "visa_wait")],  # noqa: E731
                                  "stage_index": 0 if s == "i360_pending" else 1, "notices": [], "today": "2026-10-01"}
    monkeypatch.setattr(journey, "journey", fake)
    assert journey.push_to_portal(data, portal) == {"changed": ["c1"], "notified": 0}  # the first view arrives quietly
    assert journey.push_to_portal(data, portal) == {"changed": [], "notified": 0}
    assert journey.push_to_portal(data, portal) == {"changed": ["c1"], "notified": 1}
    assert PortalStore(portal).journey("c1")["pt"]["title"].startswith("I-360 aprovada")
    assert "novidade" in sent[-1]["subject"] and "I-360" not in sent[-1]["body"]  # no case details in the message


def _sij_client(tmp_path, state, dob):
    g = FactGraph("c")
    g.add_source("applicant.dob", "birth.pdf", "birth_certificate", dob, dob, 0.9)
    g.add_source("applicant.physical_state", "portal questionnaire", "intake_questionnaire", state, state, 0.95, tier=3)
    return journey.journey(_bundle(tmp_path, {"q.pdf": "intake_questionnaire"}), TODAY, graph=g)


def test_florida_needs_the_state_court_order_before_18(tmp_path):
    # Fla. Stat. 39.01(12): a dependent "child" is under 18; O.I.C.L. (Fla. 2016): moot at 18
    j = _sij_client(tmp_path, "FL", "2009-03-15")
    assert j["stage"] == "state_court"
    fl = next(d for d in j["deadlines"] if d["id"] == "age_18_state")
    assert fl["date"] == "2027-03-14" and "before the 18th birthday" in fl["what"] and "O.I.C.L." in fl["source"]
    assert any("Fla. Stat. chapter 39" in s["text"] for s in j["steps"]) and not any("CJP 35" in s["text"] for s in j["steps"])


def test_massachusetts_keeps_its_own_court_and_no_18th_birthday_deadline(tmp_path):
    j = _sij_client(tmp_path, "MA", "2009-03-15")
    assert not any(d["id"] == "age_18_state" for d in j["deadlines"]) and any("CJP 35" in s["text"] for s in j["steps"])


def test_a_florida_client_already_18_is_flagged_for_the_attorney(tmp_path):
    j = _sij_client(tmp_path, "FL", "2007-05-01")
    late = next(s for s in j["steps"] if s["id"] == "state_court_age")
    assert late["owner"] == "attorney" and late["urgent"] and "can no longer adjudicate" in late["text"]
