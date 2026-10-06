"""USCIS case status (src/case_status.py): the Case Status API client, ready for
its keys and quiet without them -- the token, the limits, what an answer means
for the case, the nightly run within its budget. A fake HTTP layer stands in
for USCIS: no test goes on the network. Receipt numbers are USCIS's own
sandbox staging numbers (developer.uscis.gov) or made up; the clients are made up.
"""

import json
from datetime import date, datetime, timedelta, timezone

import pytest

import case_status
import journey
import maintenance
from factgraph import FactGraph

NOW = datetime(2026, 10, 2, 8, 0, tzinfo=timezone.utc)  # 4 am Eastern: the API's day began at midnight
SANDBOX = {"USCIS_CASE_STATUS_CLIENT_ID": "test-id", "USCIS_CASE_STATUS_CLIENT_SECRET": "test-secret"}
# the API page's own example answer (swagger_3.yaml, SuccessResponse)
APPROVED = {"receiptNumber": "EAC9999103403", "formType": "I-130", "submittedDate": "09-05-2023 14:28:46", "modifiedDate": "09-05-2023 14:28:46",
            "current_case_status_text_en": "Case Was Approved",
            "current_case_status_desc_en": "On September 5, 2023, we approved your Form I-130, Petition for Alien Relative, Receipt Number EAC9999103403. "
                                           "We sent you an approval notice. Please follow the instructions in the notice.",
            "current_case_status_text_es": "Caso Fue Aprobado", "current_case_status_desc_es": "...",
            "hist_case_status": [{"date": "2023-09-05", "completed_text_en": "We approved your Form I-130, Petition for Alien Relative.",
                                  "completed_text_es": "Aprobamos su Formulario I-130."}]}


class FakeUSCIS:
    """Stands in for the token URL and the Case Status API: {receipt: [(status, body), ...]} answered in turn."""

    def __init__(self, answers=None, token_status=200):
        self.answers, self.token_status, self.calls, self.tokens = answers or {}, token_status, [], 0

    def request(self, method, url, headers, data=None):
        self.calls.append((method, url, dict(headers), dict(data or {})))
        if method == "POST":
            self.tokens += 1
            return self.token_status, ({"access_token": f"token-{self.tokens}", "token_type": "Bearer", "expires_in": "1799"}
                                       if self.token_status == 200 else {"message": "Invalid client"})
        queue = self.answers[url.rsplit("/", 1)[1]]
        return queue.pop(0) if len(queue) > 1 else queue[0]


class Clock:
    def __init__(self):
        self.t, self.slept = 1000.0, []

    def __call__(self):
        return self.t

    def sleep(self, s):
        self.slept.append(round(s, 3))
        self.t += s


def _cfg(**env):
    return case_status.config(SANDBOX | env, deployment_path=None)


def test_without_keys_nothing_is_switched_on(tmp_path):
    cfg = case_status.config({}, deployment_path=tmp_path / "none.json")
    assert cfg == {"ready": False, "why": "no USCIS Case Status API keys on this server"}
    assert case_status.config(SANDBOX | {"USCIS_CASE_STATUS_ENABLED": "0"})["ready"] is False
    prod = case_status.config(SANDBOX | {"USCIS_CASE_STATUS_ENVIRONMENT": "production"})
    assert not prod["ready"] and "access letter" in prod["why"]                 # production's URLs aren't public: never guessed
    line = case_status.nightly(tmp_path / "clients", tmp_path, cfg=cfg, transport=FakeUSCIS())
    assert line.startswith("USCIS case status: not switched on") and json.loads((tmp_path / case_status.RUN).read_text())["configured"] is False
    assert "Not switched on" in case_status.state_text(tmp_path, cfg) and case_status.finding(tmp_path, cfg) is None


def test_the_keys_from_the_environment_or_the_installation_never_git(tmp_path):
    cfg = _cfg()
    assert cfg["ready"] and (cfg["token_url"], cfg["api_url"]) == ("https://api-int.uscis.gov/oauth/accesstoken", "https://api-int.uscis.gov/case-status")
    assert (cfg["daily_budget"], cfg["per_second"]) == (1000, 5)                  # the sandbox's 1,000 a day, 5 a second
    deployment = tmp_path / "deployment.json"
    deployment.write_text(json.dumps({"mode": "hosted", "case_status": {"client_id": "a", "environment": "production",
                                                                        "token_url": "https://prod.example/token", "api_url": "https://prod.example/cs/",
                                                                        "daily_budget": 2000}}))
    assert not case_status.config({}, deployment_path=deployment)["ready"]  # the id and the addresses are the installation's; the secret is not read from this file
    prod = case_status.config({"USCIS_CASE_STATUS_CLIENT_SECRET": "b"}, deployment_path=deployment)  # (made up) it comes from the environment, or the vault
    assert prod["ready"] and prod["api_url"] == "https://prod.example/cs" and (prod["daily_budget"], prod["per_second"]) == (2000, 10)
    assert case_status.config({"USCIS_CASE_STATUS_DAILY_BUDGET": "50", "USCIS_CASE_STATUS_CLIENT_SECRET": "b"}, deployment_path=deployment)["daily_budget"] == 50   # the environment wins


def test_one_token_a_bearer_header_and_five_requests_a_second():
    fake, clock = FakeUSCIS({"EAC9999103403": [(200, {"case_status": APPROVED, "message": "ok"})]}), Clock()
    client = case_status.Client(_cfg(USCIS_CASE_STATUS_DEMO_ID="1234"), fake, clock, clock.sleep)
    for _ in range(3):
        assert client.status("EAC-9999-103403")["formType"] == "I-130"           # dashes dropped, as the API asks
    token = [c for c in fake.calls if c[0] == "POST"]
    assert len(token) == 1 and token[0][3] == {"grant_type": "client_credentials", "client_id": "test-id", "client_secret": "test-secret"}
    get = [c for c in fake.calls if c[0] == "GET"]
    assert get[0][1] == "https://api-int.uscis.gov/case-status/EAC9999103403"
    assert get[0][2]["Authorization"] == "Bearer token-1" and get[0][2]["demo_id"] == "1234"
    assert clock.slept == [0.2, 0.2]                                               # one request every 200 ms


def test_an_expired_token_is_renewed_and_the_limits_stop_the_night():
    fake = FakeUSCIS({"EAC9999103403": [(401, {"code": 401, "message": "Invalid Access Token"}), (200, {"case_status": APPROVED})],
                      "LIN9999106498": [(429, {"code": 429, "message": "Spike Arrest Violation"})],
                      "SRC9999102777": [(404, {"code": 404, "message": "Case Status Online does not recognize the receipt number entered."})],
                      "SRC9999102778": [(503, {"errors": [{"code": "503", "message": "Service Unavailable"}]})]})
    clock = Clock()
    client = case_status.Client(_cfg(), fake, clock, clock.sleep)
    assert client.status("EAC9999103403")["receiptNumber"] == "EAC9999103403" and fake.tokens == 2
    with pytest.raises(case_status.Stop, match="limit"):
        client.status("LIN9999106498")
    with pytest.raises(case_status.CaseStatusError) as nf:
        client.status("SRC9999102777")
    assert nf.value.status == 404 and "does not recognize" in nf.value.message
    with pytest.raises(case_status.Stop, match="Monday to Friday"):
        client.status("SRC9999102778")
    with pytest.raises(case_status.Stop, match="refused the keys"):
        case_status.Client(_cfg(), FakeUSCIS(token_status=401), clock, clock.sleep).status("EAC9999103403")
    with pytest.raises(case_status.CaseStatusError):
        client.status("EAC123")                                                    # never sent: not a receipt number


def test_what_a_status_means_for_the_case():
    k = case_status.kind
    assert (k("Case Was Approved"), k("Case Approval Was Affirmed")) == ("approval", "approval")   # the API's own examples
    assert k("Request for Evidence Was Sent") == "rfe" and k("Response To USCIS' Request For Evidence Was Received") == "update"
    assert (k("Notice Of Intent To Deny Was Sent"), k("Case Was Denied"), k("Case Rejected Because I Sent An Incorrect Fee")) == ("noid", "denial", "rejection")
    assert (k("Interview Was Scheduled"), k("Fingerprint Fee Was Received"), k("Case Was Received")) == ("interview", "receipt", "receipt")
    assert k("Something USCIS has never said") == "update"
    rec = case_status.record_of(APPROVED, NOW, "sandbox")
    assert (rec["kind"], rec["date"], rec["submitted"], rec["form"]) == ("approval", "2023-09-05", "2023-09-05", "I-130")
    html = case_status.record_of({"current_case_status_text_en": "Case Approval Was Affirmed",
                                  "current_case_status_desc_en": 'The approval ... go to <a href="https://www.uscis.gov/addresschange">www.uscis.gov/addresschange</a>.'},
                                 NOW, "sandbox")
    assert "<a" not in html["desc"] and "www.uscis.gov/addresschange" in html["desc"] and html["date"] is None


def _client(root, name, notices, filed=None):
    """A made-up client's bundle: its USCIS notices as the notice reader records them, and a filing recorded."""
    d = root / name
    d.mkdir(parents=True)
    (d / "meta.json").write_text(json.dumps({"classifications": {}}))
    g = FactGraph(name)
    g.add_source("applicant.dob", "t", "t", "2000-01-01", "2000-01-01", 1.0)
    for receipt, form, kind, when in notices:
        g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)
        g.add_source(f"folder.notice.{receipt}.{kind}_{when.replace('-', '')}.date", f"{kind}.pdf", "uscis_notice", when, when, 0.85)
    g.save(d / "fact_graph.json")
    if filed:
        (d / "status.json").write_text(json.dumps({"filings": [filed], "filed_at": filed["at"]}))
    return d


def test_the_night_checks_the_newest_open_receipts_within_the_budget(tmp_path):
    clients = tmp_path / "clients"
    old = _client(clients, "case-old", [("EAC9999103403", "I-130", "receipt", "2025-01-10")])
    new = _client(clients, "case-new", [("LIN9999106498", "I-485", "receipt", "2026-09-01")])
    _client(clients, "case-done", [("SRC9999102777", "I-765", "receipt", "2026-01-05"), ("SRC9999102777", "I-765", "approval", "2026-04-01")])
    online = _client(clients, "case-online", [], {"filing": "i751", "title": "I-751", "mailed_on": "2026-09-20", "carrier": "USCIS online account",
                                                  "online": True, "receipt": "IOE0999000777", "by": "Ana Attorney", "at": "2026-09-20T12:00:00+00:00"})
    rfe = dict(APPROVED, receiptNumber="LIN9999106498", formType="I-485", current_case_status_text_en="Request for Evidence Was Sent",
               current_case_status_desc_en="On September 28, 2026, we sent a request for evidence for your Form I-485, Receipt Number LIN9999106498.")
    fake = FakeUSCIS({"LIN9999106498": [(200, {"case_status": rfe})], "IOE0999000777": [(200, {"case_status": dict(APPROVED, receiptNumber="IOE0999000777",
                                                                                                                         formType="I-751", current_case_status_text_en="Case Was Received", current_case_status_desc_en="On September 21, 2026, we received your Form I-751.")})],
                      "EAC9999103403": [(200, {"case_status": APPROVED})]})
    clock = Clock()
    line = case_status.nightly(clients, tmp_path, cfg=_cfg(USCIS_CASE_STATUS_DAILY_BUDGET="2"), transport=fake, now=NOW, clock=clock, sleep=clock.sleep)
    asked = [c[1].rsplit("/", 1)[1] for c in fake.calls if c[0] == "GET"]
    assert asked == ["IOE0999000777", "LIN9999106498"]                            # newest first; the approved one never; the budget is 2
    assert "2 receipt(s) checked" in line and "1 left for tomorrow" in line
    assert case_status.records(new)["LIN9999106498"]["kind"] == "rfe" and case_status.records(online)["IOE0999000777"]["kind"] == "receipt"
    assert not case_status.records(old)

    # the same night again: the day's budget is spent, and nobody is asked twice
    fake.calls.clear()
    again = case_status.nightly(clients, tmp_path, cfg=_cfg(USCIS_CASE_STATUS_DAILY_BUDGET="2"), transport=fake, now=NOW + timedelta(hours=1))
    assert not [c for c in fake.calls if c[0] == "GET"] and "budget" in again
    # the next day: the one left over
    case_status.nightly(clients, tmp_path, cfg=_cfg(USCIS_CASE_STATUS_DAILY_BUDGET="2"), transport=fake, now=NOW + timedelta(days=1))
    assert case_status.records(old)["EAC9999103403"]["kind"] == "approval"
    assert "Switched on. Last checked 10/03/2026" in case_status.state_text(tmp_path, _cfg())

    # on the case: the status on the timeline, and the request for evidence the folder doesn't have yet
    j = journey.journey(new, date(2026, 10, 2))
    status = [e for e in j["timeline"] if e["kind"] == "uscis_status"]
    assert status == [{"date": "2026-09-28", "what": "USCIS status: Request for Evidence Was Sent (I-485 LIN9999106498, checked 10/02/2026)",
                       "doc": None, "kind": "uscis_status"}]
    step = next(s for s in j["steps"] if s["id"].startswith("case_status.LIN9999106498.rfe"))
    assert step["urgent"] and step["owner"] == "paralegal" and "isn't in the client's folder yet" in step["text"] and "due date" in step["text"]


def test_a_notice_in_the_folder_closes_the_follow_up_and_a_404_asks_for_the_number(tmp_path):
    d = _client(tmp_path, "case-x", [("EAC9999103403", "I-130", "approval", "2023-09-05")])
    case_status.save(d, "EAC9999103403", case_status.record_of(APPROVED, NOW, "sandbox"))
    case_status.save(d, "SRC9999102777", {"receipt": "SRC9999102777", "checked_at": NOW.isoformat(), "error": "not_found", "message": "not recognised"})
    events, steps = case_status.for_journey(d, journey.notices(FactGraph.load(d / "fact_graph.json")))
    assert [e["what"] for e in events] == ["USCIS status: Case Was Approved (I-130 EAC9999103403, checked 10/02/2026)"]
    assert [s["id"] for s in steps] == ["case_status.SRC9999102777.not_found"] and "8 U.S.C. 1367" in steps[0]["text"]
    # an error later never wipes the last good answer
    case_status.save(d, "EAC9999103403", {"receipt": "EAC9999103403", "checked_at": NOW.isoformat(), "error": "error", "message": "x"})
    assert case_status.records(d)["EAC9999103403"]["text"] == "Case Was Approved"


def test_keeping_current_says_whether_it_is_switched_on(tmp_path):
    item = next(i for i in maintenance.registry()["items"] if i["id"] == "uscis_case_status")
    assert item["party"] == "host" and item["check"]["type"] == "case_status_api" and item["firm_steps"]
    words = " ".join(item["firm_steps"])
    assert "USCIS online account" in words and not any(w in words for w in ("src/", ".json", "USCIS_CASE_STATUS"))
    stale = {"at": (NOW - timedelta(days=5)).isoformat(), "configured": True, "checked": 3, "changed": 0, "errors": 0, "left": 0, "stopped": None}
    (tmp_path / case_status.RUN).write_text(json.dumps(stale))
    assert "more than three days ago" in case_status.finding(tmp_path, _cfg(), now=NOW)
    (tmp_path / case_status.RUN).write_text(json.dumps(stale | {"at": NOW.isoformat(), "stopped": "over USCIS's limit for today or this second"}))
    assert "stopped early" in case_status.finding(tmp_path, _cfg(), now=NOW)
    for page in ("online_filing", "online_upload_limits"):                        # the online filing pages: their dates are checked every night
        entry = next(i for i in maintenance.registry()["items"] if i["id"] == page)
        assert entry["check"]["type"] == "page_updated" and entry["party"] == "provider"
