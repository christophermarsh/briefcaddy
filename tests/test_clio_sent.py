"""What went to Clio, per case (src/connectors/clio_sent.py): the record the sync writes beside the case, the line on the case page in words, "What failed" when
Clio refused something (with the retry the overnight run makes), "Send now" for one case, and the count of cases with a refused send on Keeping current.
Against test_clio.py's simulated Clio; everyone is made up."""

import http.client
import json
import threading
import time
from datetime import datetime

import httpx
import pytest

import clock
import events
import records
from connectors import clio, clio_sent
from review.auth import COOKIE, Accounts
from review.server import ReviewApp, make_handler, serve
from test_clio import DEADLINES, ENV, REPO, SETUP, FakeClio, Quiet, _pdf, _processed
from test_clio_kinds import processed
import schema_path

ANA, MARIA = "ana_clara_exemplo_souza-cl101", "maria_exemplo-cl102"


@pytest.fixture
def fake():
    return FakeClio()


@pytest.fixture
def firm(tmp_path, fake):
    """A connected firm, as test_clio.py's: the simulated Clio behind every call."""
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    clio.save_settings(data, SETUP, "Andrea Attorney", env=ENV)
    clio.connect(data, "good-code", "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    quiet = Quiet()
    return {"data": data, "clients": tmp_path / "clients", "out": data / "clients", "portal": data / "portal", "fake": fake,
            "run": lambda direction="both": clio.run(data, tmp_path / "clients", data / "clients", data / "portal", direction,
                                                     transport=httpx.MockTransport(fake.handle), env=ENV,
                                                     pace=clio.Pace(monotonic=quiet.mono, sleep=quiet.sleep, wall=quiet.wall))}


@pytest.fixture
def view(monkeypatch):
    """What src/journey.py says of each case (its stage, its deadlines, which a person marked done), for the cases whose fact graph is not real."""
    v = {"stage": "Green card application filed", "why": "Receipt notice IOE0912345678", "deadlines": [dict(d) for d in DEADLINES], "done": {}}
    monkeypatch.setattr(clio, "case_view", lambda out_dir: json.loads(json.dumps(v)))
    return v


def ledger(case=None):
    return [r for r in events.rows(events.base_path(None)) if case is None or r.get("case") == case]


def test_the_line_says_what_went_and_when_in_words_and_is_written_only_when_it_changes(firm, fake, view, monkeypatch):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 3, 2, 10))  # the firm's night: 10/03/2026 02:10
    firm["run"]("in")
    out = _processed(firm, ANA, filings=[{"filing": "i485", "title": "SIJ green card (I-485)", "mailed_on": "2026-10-01", "by": "Paulo Paralegal", "at": "2026-10-01T16:00:00-04:00"}])
    before = len(ledger(ANA))
    firm["run"]("out")
    rec = clio_sent.read(out)
    assert rec["matter"] == "101" and rec["packet_at"].startswith("2026-10-03T02:10") and rec["mailings"] == 1 and rec["deadlines"] == 2 and rec["tasks"] == 0
    assert rec["stage_at"].startswith("2026-10-03") and rec["failed"] == [] and rec["end"] is None and rec["version"] == 1
    assert set(rec) <= {"version", "matter", "packet_at", "mailings", "stage_at", "end", "deadlines", "tasks", "failed", "by_hand"}
    shown = clio_sent.case_view(firm["data"], out, ANA, True)
    assert shown["line"] == "In Clio: packet sent 10/03/2026, 1 mailing record, 2 deadlines, stage note 10/03/2026; last sync 10/03/2026 02:10."
    assert "—" not in shown["line"] and " -- " not in shown["line"] and shown["failed"] == [] and shown["held"] is None
    assert shown["can_send"] and shown["button"] == "Send now" and shown["waiting"] is None
    # one ledger row for the write, in words with no date, id or name
    rows = ledger(ANA)[before:]
    assert [(r["kind"], r["action"], r["what"], r["who"]) for r in rows if r["action"] == "sent_to_clio"] == [("imports", "sent_to_clio", "Sent something about this case to Clio", "Clio")]
    # nothing changed: the file is not touched and no row is added, though the sync ran
    stamp, rows_now = (out / "clio_sent.json").stat().st_mtime_ns, len(ledger(ANA))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 4, 2, 10))
    firm["run"]("out")
    assert (out / "clio_sent.json").stat().st_mtime_ns == stamp and len(ledger(ANA)) == rows_now
    assert clio_sent.case_view(firm["data"], out, ANA, True)["line"].endswith("last sync 10/04/2026 02:10.")  # the line's sync is the connection's own
    # a deadline moves, a closing note goes: the record changes, and a row is written
    view["deadlines"].pop()
    firm["run"]("out")
    assert clio_sent.read(out)["deadlines"] == 1 and "1 deadline," in clio_sent.case_view(firm["data"], out, ANA, True)["line"]
    # a case that was never sent anything has no file, and says so
    other = _processed(firm, MARIA)
    (other / "packet.pdf").unlink()
    (other / "packet.json").unlink()
    (other / "packet_review_bundle.pdf").unlink()
    view["deadlines"].clear()
    view["stage"] = ""
    firm["run"]("out")
    assert not (other / "clio_sent.json").exists() and clio_sent.case_view(firm["data"], other, MARIA, True)["line"].startswith("In Clio: nothing sent yet; last sync")


def test_a_case_that_is_not_a_clio_matter_or_a_firm_without_clio_has_no_line(tmp_path, firm):
    assert clio_sent.case_view(firm["data"], firm["out"] / "someone-else", "someone-else", True) is None
    assert clio_sent.case_view(tmp_path / "no-clio-here", tmp_path, "x", True) is None


def test_what_clio_refused_is_said_in_words_with_the_retry_and_clears_when_it_goes_through(firm, fake, view):
    firm["run"]("in")
    ana, maria = _processed(firm, ANA), _processed(firm, MARIA)
    fake.refuse_matter = 101  # the connected Clio user can't write to Ana's matter
    line = firm["run"]("out")
    assert "1 case(s) had a problem" in line
    rec = clio_sent.read(ana)
    [failed] = rec["failed"]
    assert failed["what"].startswith("Clio refused the upload of the filing packet for Ana Clara Exemplo Souza: no access") and failed["what"].endswith("The overnight run tries again tonight.")
    assert "/api" not in failed["what"] and "403" not in failed["what"] and "—" not in failed["what"] and " -- " not in failed["what"]
    assert clio_sent.read(maria)["failed"] == [] and clio_sent.read(maria)["packet_at"]  # the other case went through
    page = clio_sent.case_view(firm["data"], ana, ANA, True)
    assert page["failed"] == [failed] and page["can_send"]
    # Keeping current: the count of cases with a refused send, and Settings lists them
    text, needs = clio.state_text(firm["data"], ENV)
    assert needs and "1 case has something Clio refused (Settings, Connections, Cases Clio refused)" in text and "the overnight run tries again" in text
    shown = clio.view(firm["data"], ENV)["failed"]
    assert shown == [{"case": ANA, "name": "Ana Clara Exemplo Souza", "at": failed["at"], "what": failed["what"]}]
    assert any(r["what"] == "Clio refused something sent for this case" for r in ledger(ANA))
    # the overnight run tries again: Clio now says yes, the refusal is gone from the case, the page and Keeping current
    fake.refuse_matter = None
    line = firm["run"]("out")
    assert "had a problem" not in line and clio_sent.read(ana)["failed"] == [] and clio_sent.read(ana)["packet_at"]
    assert clio.view(firm["data"], ENV)["failed"] == [] and "refused" not in clio.state_text(firm["data"], ENV)[0] and not clio.state_text(firm["data"], ENV)[1]
    assert any(r["what"] == "What Clio refused for this case went through" for r in ledger(ANA))


def test_the_record_is_in_the_catalog_the_dictionary_and_the_export(firm, fake, view):
    from test_data_dictionary import _documented

    firm["run"]("in")
    out = _processed(firm, ANA)
    firm["run"]("out")
    written = json.loads((out / "clio_sent.json").read_text(encoding="utf-8"))
    assert set(written) <= _documented("clio_sent") and records.coverage("case", "clio_sent.json") == "listed"
    assert records.by_id("clio_sent")["exported"] and records.by_id("clio_webhook")["exported"] and records.coverage("firm", "clio/webhook.json") == "listed"
    assert "clio_sent.json" in " ".join(records.patterns("case"))
    docs = (REPO / "docs" / "data_dictionary.md").read_text(encoding="utf-8")
    assert "clio_sent.json" in docs and "clio/webhook.json" in docs


# --- the case page and "Send now" ------------------------------------------------------------------------------------------------


@pytest.fixture
def hub(firm, fake, tmp_path):
    accounts = Accounts(tmp_path / "staff.json")
    accounts.add("andrea@firm.example", "Andrea Attorney", "attorney")
    accounts.add("paulo@firm.example", "Paulo Paralegal", "paralegal")
    app = ReviewApp(firm["out"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, firm["portal"], accounts=accounts)
    app.clio_transport = httpx.MockTransport(fake.handle)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    yield {"port": port, "app": app, "attorney": cookie("andrea@firm.example"), "paralegal": cookie("paulo@firm.example")}
    httpd.shutdown()


def api(hub, who, method, path, body=None):
    conn = http.client.HTTPConnection("127.0.0.1", hub["port"], timeout=30)
    headers = {"Cookie": hub[who]} | ({"X-Review-App": "1", "Content-Type": "application/json"} if body is not None else {})
    conn.request(method, path, body=json.dumps(body) if body is not None else None, headers=headers)
    r = conn.getresponse()
    out = r.status, json.loads(r.read() or b"{}")
    conn.close()
    return out


def settle(hub):
    for _ in range(200):  # the send runs on its own thread: one at a time, so the lock says when it is over
        if not hub["app"]._clio_running.locked():
            return
        time.sleep(0.05)
    raise AssertionError("the send did not finish")


def test_send_now_sends_this_case_and_the_case_page_shows_the_line_and_who_pressed_it(firm, fake, hub):
    firm["run"]("in")
    case = processed(firm, ANA, filings=[{"filing": "i485", "title": "SIJ green card (I-485)", "mailed_on": "2026-10-01", "by": "Paulo Paralegal", "at": "2026-10-01T16:00:00-04:00"}])
    (case / "packet.pdf").write_bytes(_pdf("packet"))
    (case / "packet.json").write_text(json.dumps({"built_at": "2026-10-01T10:00:00-04:00", "draft": False, "filing": "i485"}), encoding="utf-8")
    processed(firm, MARIA)
    status, page = api(hub, "paralegal", "GET", f"/api/journey?client={ANA}")
    assert status == 200 and page["clio"]["line"].startswith("In Clio: nothing sent yet; last sync") and page["clio"]["can_send"] and page["clio"]["button"] == "Send now"
    assert api(hub, "paralegal", "POST", "/api/clio-send", {"client": "no-such-case"})[0] == 404
    status, answer = api(hub, "paralegal", "POST", "/api/clio-send", {"client": ANA})
    assert status == 200 and answer == {"started": True}
    settle(hub)
    assert any(d["meta"]["name"].startswith("Filing packet") for d in fake.created.values()) and fake.notes  # this case's packet and stage went, now
    assert all(d["matter"] == 101 for d in fake.created.values()) and all(e["matter"] == {"id": 101} for e in fake.entries.values())  # nothing for Maria's matter
    assert not any(n["matter"] == {"id": 102} for n in fake.notes)
    rec = clio_sent.read(case)
    assert rec["by_hand"]["by"] == "Paulo Paralegal" and rec["packet_at"]
    status, page = api(hub, "paralegal", "GET", f"/api/journey?client={ANA}")
    assert page["clio"]["line"].startswith("In Clio: packet sent ") and page["clio"]["by_hand"]["by"] == "Paulo Paralegal"
    pressed = [r for r in ledger(ANA) if r["action"] == "sent_now"]
    assert pressed and pressed[-1]["who"] == "Paulo Paralegal" and pressed[-1]["what"] == "Pressed Send now to send this case to Clio"
    # not a Clio matter: no line, and no send
    plain = firm["out"] / "walk-in-client"
    plain.mkdir()
    (plain / "fact_graph.json").write_text(json.dumps({"client_id": plain.name, "facts": {}}), encoding="utf-8")
    assert api(hub, "paralegal", "GET", f"/api/journey?client={plain.name}")[1]["clio"] is None
    assert api(hub, "paralegal", "POST", "/api/clio-send", {"client": plain.name})[0] == 404
    assert not any(m == "DELETE" for m, _ in fake.requests)


def test_a_protected_case_is_sent_only_by_an_attorney_whose_press_is_send_to_clio(firm, fake, hub):
    firm["run"]("in")
    mailed = [{"filing": "vawa", "title": "VAWA self-petition", "mailed_on": "2026-09-30", "carrier": "USPS", "by": "Paulo Paralegal", "at": "2026-09-30T12:00:00-04:00"}]
    case = processed(firm, ANA, filings=mailed)
    page = api(hub, "attorney", "GET", f"/api/journey?client={ANA}")[1]["clio"]
    assert page["held"] == clio.PROTECTED["1367"] and page["button"] == "Send to Clio" and page["can_send"] and page["line"] is None
    # a paralegal not named on a protected case cannot open it at all: the one answer for a case that does not exist (src/restricted.py)
    assert api(hub, "paralegal", "POST", "/api/clio-send", {"client": ANA})[0] == 404 and api(hub, "paralegal", "GET", f"/api/journey?client={ANA}")[0] == 404
    # one the attorney named on the case may open it, and still may not send it: the rule is in the send itself
    with pytest.raises(PermissionError, match="Only an attorney sends a protected case to Clio"):
        hub["app"].clio_send(ANA, {"reviewer": "Paulo Paralegal"}, "paralegal")
    assert not fake.created and not fake.notes and not clio.state(firm["data"]).get("allowed")
    assert clio_sent.case_view(firm["data"], case, ANA, False)["can_send"] is False  # the page gives a paralegal no button for it
    status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
    assert status == 200
    settle(hub)
    allowed = clio.state(firm["data"])["allowed"][ANA]
    assert allowed["by"] == "Andrea Attorney" and fake.notes and clio_sent.read(case)["by_hand"]["by"] == "Andrea Attorney"
    assert api(hub, "attorney", "GET", f"/api/journey?client={ANA}")[1]["clio"]["held"] is None  # allowed: no longer held


def test_send_now_says_why_it_cannot_in_words(firm, fake, hub):
    firm["run"]("in")
    case = firm["out"] / ANA
    status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
    assert status == 404 or (status == 400 and "has not been read yet" in answer["error"])  # no case folder processed yet
    processed(firm, ANA)
    clio.save_settings(firm["data"], {"on": False}, "Andrea Attorney", env=ENV)
    status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
    assert status == 400 and answer["error"] == "Clio can't send yet: switched off." and not fake.notes
    clio.save_settings(firm["data"], {"on": True}, "Andrea Attorney", env=ENV)
    with hub["app"]._clio_running:  # a sync is running
        status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
        assert status == 400 and "A Clio sync is running now" in answer["error"]
    assert case.exists()


def test_a_press_that_is_turned_away_leaves_nothing_allowed(firm, fake, hub):
    firm["run"]("in")
    mailed = [{"filing": "vawa", "title": "VAWA self-petition", "mailed_on": "2026-09-30", "carrier": "USPS", "by": "Paulo Paralegal", "at": "2026-09-30T12:00:00-04:00"}]
    processed(firm, ANA, filings=mailed)
    with hub["app"]._clio_running:  # a sync is running in this process
        status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
    assert status == 400 and "A Clio sync is running now" in answer["error"] and not clio.state(firm["data"]).get("allowed")
    with clio.step_lock(firm["data"]):  # the overnight run's Clio step is running
        status, answer = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})
    assert status == 400 and "the overnight run's" in answer["error"] and not clio.state(firm["data"]).get("allowed")
    assert not hub["app"]._clio_running.locked()
    with clio.step_lock(firm["data"]):  # nothing is left held
        pass
    status, _ = api(hub, "attorney", "POST", "/api/clio-send", {"client": ANA})  # "try again" works, and now it is allowed
    assert status == 200
    settle(hub)
    assert clio.state(firm["data"])["allowed"][ANA]["by"] == "Andrea Attorney"
    with clio.step_lock(firm["data"]):  # the send let go of the step when it was done
        pass
