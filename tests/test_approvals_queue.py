"""The attorney's queue, My approvals (src/approvals.py): every open item only an attorney may approve, across every case, on one screen. The registry
the screen is drawn from, the kinds it finds today, the stricter rule for a restricted case (shown to the attorneys named on it and counted nowhere else),
the paralegal's 403, the counts on My work and in the morning report, the page of fifty, and the heart of it: twenty cases with forty open items cleared through
the queue's own routes in forty actions, each leaving the case's record and the ledger exactly as the case page's own buttons do.

Everyone here is made up (the Exemplo family)."""

from __future__ import annotations

import json
import threading
import urllib.error
import urllib.request
import os
from datetime import datetime
from pathlib import Path
from types import SimpleNamespace
from urllib.parse import quote

import pytest

import approvals
import clock
import eoir26a
import events
import filing_questions
import restricted
import second_factor
import settings
from factgraph import FactGraph
import schema_path

PASSWORD = "correct horse battery staple"  # secret-scan: allow (a made-up test password)
ATTORNEY, OTHER_ATTORNEY, PARALEGAL = "sam@firm.example", "ana@firm.example", "jane@firm.example"
K = {name: f"applicant.part9.{name}" for name in ("worked_without_authorization", "in_removal_proceedings", "pt9line75", "committed_crime")}
CLIENT = ("questionnaire.pdf", "intake_questionnaire", 3)
FIRM_FACTS = {"firm.preparer_given_name": "ANA", "firm.preparer_family_name": "EXEMPLO", "firm.business_name": "EXAMPLE LAW LLP", "firm.street": "1 EXAMPLE PLAZA",
              "firm.city": "BOSTON", "firm.state": "MA", "firm.zip": "02101", "firm.phone": "6175550100", "firm.email": "ANA@EXAMPLE.COM", "firm.eoir_id": "ZZ999999",
              "firm.licensing_authority": "MASSACHUSETTS", "firm.attorney_bar_number": "000000"}
FIGURES = {"fw_income_work": "1500", "fw_income_property": "0", "fw_income_interest": "0", "fw_income_other": "350",
           "fw_exp_rent": "1200", "fw_exp_utilities": "180", "fw_exp_debts": "120", "fw_exp_living": "500", "fw_exp_other": "100"}
ALERT = {"level": "review", "fact_key": "folder.criminal_history", "message": "CRIMINAL HISTORY: the folder shows a made-up citation for the attorney to read.", "kind": "alert"}


def write_case(root: Path, case: str, name: str, *, yes: tuple[str, ...] = (), alert: bool = False, track: str = "family", at: str | None = None) -> Path:
    d = root / case
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(case)
    given, _, family = name.upper().partition(" ")
    for key, value in {"applicant.given_name": given, "applicant.family_name": family, **FIRM_FACTS}.items():
        g.add_source(key, "questionnaire", "intake_questionnaire", value, value, 0.95)
    for n in yes:
        g.add_source(K[n], CLIENT[0], CLIENT[1], "Yes", "Yes", 0.95, tier=CLIENT[2])
    g.save(d / "fact_graph.json")
    # Keep the fixture's original scanned-source aliases/tier/card semantics,
    # with retained fictional evidence and real byte/range identities (EV1).
    from portal.demo import document_pdf
    import document_instances
    folder = d / "source"
    folder.mkdir(exist_ok=True)
    texts = {}
    for fact in g.all_facts().values():
        for source in fact.sources:
            texts.setdefault(source.doc_id, []).append(f"{fact.fact_key}: {source.raw_value}")
    docs = [(alias, "Questionário para Ajuste de Status\nI485 - SIJS\n" + "\n".join(lines)) for alias, lines in texts.items()]
    for alias, text in docs:
        (folder / alias).write_bytes(document_pdf(text.splitlines()))
    plans = document_instances.prepare(docs, None, document_instances.context(folder, None, list(texts)))[2]
    (d / "documents.json").write_text(json.dumps({"version": 1, "documents": [], "boundary_plans": plans}), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"client_id": case, "source_folder": str(folder), "classifications": {}}), encoding="utf-8")
    if at:  # when the product read the case: what a card no person raised dates from
        stamp = datetime.fromisoformat(at).timestamp()
        os.utime(d / "meta.json", (stamp, stamp))
    if alert:
        (d / "reading_flags.json").write_text(json.dumps([ALERT]), encoding="utf-8")
    (d / "status.json").write_text(json.dumps({"journey": {"track": {"value": track, "by": "Jane Doe", "at": "2026-09-21T10:00:00+00:00", "note": None}}}), encoding="utf-8")
    return d


def paralegal_draft(d: Path) -> None:
    """A paralegal writes the Part 14 explanation of a Yes: it waits for the attorney's approval."""
    import part14_explain as px

    px.edit(d, K["committed_crime"], "Yes, I was cited for driving without a license in Boston on 03/03/2024. The charge was dismissed.", "Jane Doe", "paralegal")


def paralegal_waiver(d: Path) -> None:
    """A paralegal types the fee waiver's figures and drafts item 4: it waits for the attorney's approval."""
    filing_questions.answer("court_motion", d, {"ijmotion.fee_waiver": "Yes"}, "Sam")
    eoir26a.set_figures(d, FIGURES, "Jane Doe", "paralegal", None)
    eoir26a.edit_sentence(d, "My monthly expenses are more than my income, so I cannot pay the filing fee.", "Jane Doe", "paralegal")


def make_firm(tmp_path: Path, monkeypatch) -> SimpleNamespace:
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("I485_WORDINGS", str(tmp_path / "wordings"))
    monkeypatch.setenv("I485_AUDIT_FILL", str(tmp_path / "audit_fill.json"))
    monkeypatch.setenv("I485_INDEX", str(tmp_path / "data" / "index.db"))
    monkeypatch.setenv("I485_ROSTER", str(tmp_path / "saved-lists.json"))
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    return SimpleNamespace(root=tmp_path, clients=tmp_path / "data" / "clients", portal=tmp_path / "data" / "portal")


@pytest.fixture
def firm(tmp_path, monkeypatch):
    return make_firm(tmp_path, monkeypatch)


def make_app(firm):
    from review.auth import Accounts
    from review.server import ReviewApp

    firm.clients.mkdir(parents=True, exist_ok=True)
    accounts = Accounts(firm.root / "staff.json")
    for email, name, role in ((PARALEGAL, "Jane Doe", "paralegal"), ("kim@firm.example", "Kim Exemplo", "paralegal"), (ATTORNEY, "Sam Attorney", "attorney"),
                              (OTHER_ATTORNEY, "Ana Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
        if role == "attorney":
            second_factor.set_up(accounts, email, PASSWORD)
    return ReviewApp(firm.clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=firm.portal, accounts=accounts)


def serve_app(firm):
    from review.server import make_handler, serve

    app = make_app(firm)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return SimpleNamespace(base=f"http://127.0.0.1:{port}", app=app, httpd=httpd)


@pytest.fixture
def server(firm):
    srv = serve_app(firm)
    yield srv
    srv.httpd.shutdown()


def call(url, cookie=None, body=None):
    headers = {"X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}) | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read().decode("utf-8", "replace") or "null")
    except urllib.error.HTTPError as e:
        raw = e.read().decode("utf-8", "replace")
        try:
            return e.code, json.loads(raw)
        except ValueError:
            return e.code, raw


def sign_in(base, email):
    req = urllib.request.Request(base + "/api/login", data=json.dumps({"email": email, "password": PASSWORD}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1"}, method="POST")
    with urllib.request.urlopen(req) as r:
        cookie, owed = r.headers.get("Set-Cookie").split(";")[0], json.loads(r.read())["user"].get("second_factor")
    return second_factor.finish(base, email, cookie) if owed else cookie


def queue(srv, cookie, **q):
    # First schedule dirty entries, then wait for the complete snapshot. wait()
    # alone joins work already running; the next request otherwise starts its
    # 1.5-second budget and can legitimately return the earlier cached rows.
    srv.app.roster.sync()
    srv.app.roster.wait()
    status, body = call(srv.base + "/api/approvals" + ("?" + "&".join(f"{k}={quote(str(v))}" for k, v in q.items()) if q else ""), cookie)
    assert status == 200, body
    return body


def everything(srv, cookie, **q):
    """Every row of the queue, page after page."""
    rows, page = [], 1
    while True:
        got = queue(srv, cookie, page=page, **q)
        rows += got["rows"]
        if page >= got["pages"]:
            return got, rows
        page += 1


# -- the world: twenty cases, forty items -------------------------------------------------------------------------------------------------------


def twenty_cases(firm) -> None:
    """Twenty Exemplo cases. Each has a card for the attorney (an alert from the reading); six have a Part 14 explanation a paralegal wrote, four a fee waiver's item 4,
    four a minor's SIJ case the attorney may restrict. With the firm's six practices waiting, that is forty items of six kinds."""
    for n in range(20):
        d = write_case(firm.clients, f"case-{n:02d}", f"Pessoa{n:02d} Exemplo", yes=("committed_crime",) if n < 6 else (), alert=True, track="sij" if 10 <= n < 14 else "family")
        if n < 6:
            paralegal_draft(d)
        elif n < 10:
            paralegal_waiver(d)


def practices(srv=None) -> int:
    from rules import approval

    return sum(1 for e in approval.catalog() if e["kind"] == "practice" and approval.status(e["id"], e["hash"])["state"] != "approved")


# -- the queue --------------------------------------------------------------------------------------------------------------------------------


def test_the_queue_finds_every_kind_from_the_registry_and_each_row_says_what_why_who_and_when(firm, server):
    twenty_cases(firm)
    cookie = sign_in(server.base, ATTORNEY)
    got, rows = everything(server, cookie)
    assert got["total"] == 40 == len(rows) and practices() == 6
    counts = {k["id"]: k["count"] for k in got["kinds"]}
    assert counts == {"card": 20, "part14": 6, "practice": 5, "g28": 1, "eoir26a": 4, "restricted": 4, "wording": 0, "audit": 0, "path": 0}
    assert [k["id"] for k in got["kinds"]] == [k["id"] for k in approvals.kinds()]
    assert {r["kind"] for r in rows} >= {"card", "part14", "practice", "g28", "eoir26a", "restricted"}
    explain = next(r for r in rows if r["kind"] == "part14")
    assert explain["name"].endswith("EXEMPLO") and explain["by"] == "Jane Doe" and explain["date"] == "10/05/2026" and explain["waited_days"] == 0
    assert explain["what"].startswith("The Part 14 explanation for Part 9, item") and "waits for an attorney's approval" in explain["why"] and "driving without a license" in explain["detail"]
    assert [a["id"] for a in explain["actions"]] == ["approve", "ask", "open"]
    card = next(r for r in rows if r["kind"] == "card" and r["case"] == "case-15")  # no one has worked this case: the reading raised it
    assert card["what"] == "Criminal history in the folder" and card["why"].startswith("The folder shows a made-up citation") and card["by"] == approvals.PRODUCT
    assert next(r for r in rows if r["kind"] == "card" and r["case"] == "case-00")["by"] == "Jane Doe"  # the paralegal who last worked the case sent it on
    assert {p["name"]: p["count"] for p in got["people"]} == {"Jane Doe": 10 + 6 + 4, approvals.PRODUCT: 40 - 20}
    assert got["oldest"]["date"]
    assert not any("Ana Attorney" in json.dumps(r) for r in rows)


def test_a_paralegal_gets_403_and_never_sees_the_link(firm, server):
    twenty_cases(firm)
    jane = sign_in(server.base, PARALEGAL)
    status, body = call(server.base + "/api/approvals", jane)
    assert status == 403 and "attorney" in body["error"]
    status, work = call(server.base + "/api/work?owner=paralegal", jane)
    assert status == 200 and work.get("approvals") is None
    status, work = call(server.base + "/api/work?owner=attorney", jane)
    assert status == 200 and work.get("approvals") is None
    status, body = call(server.base + "/api/approvals")  # nobody signed in
    assert status in (401, 403)
    sam = sign_in(server.base, ATTORNEY)
    server.app.roster.sync()
    server.app.roster.wait()  # this assertion is the completed 20-case snapshot, not initial reading progress
    status, work = call(server.base + "/api/work?owner=attorney", sam)
    assert status == 200 and work["approvals"]["total"] == 40 and work["approvals"]["line"].startswith("40 approvals waiting, the oldest from ")


# -- forty items cleared through the queue, the same as the case page would ------------------------------------------------------------------------


REASON = "A minor's case: the made-up office restricts it."
SENTENCE = "My monthly expenses are more than my income, so I cannot pay the filing fee."


def snapshot(firm) -> dict:
    """What the cases and the firm keep after the sitting, without clocks: each case's decisions, explanations, fee waiver and restriction, the firm's approvals, and the
    ledger's rows (what kind, which action, in whose words, by whom)."""
    import part14_explain as px
    from rules import approval

    out: dict = {"cases": {}}
    for d in sorted(firm.clients.iterdir()):
        decisions = json.loads((d / "decisions.json").read_text(encoding="utf-8")) if (d / "decisions.json").exists() else {}
        request = eoir26a.read(d).get("request") or {}
        out["cases"][d.name] = {
            "decisions": {k: (v["action"], v["values"], v["reviewer"], v.get("role"), v.get("note"), bool(v.get("undone"))) for k, v in sorted(decisions.items())},
            "part14": {k: (v["text"], v["by"], v["role"]) for k, v in sorted(px.read(d)["approvals"].items())},
            "eoir26a": {k: v[k] for k in ("by", "role") for v in [request.get("sentence") or {}] if k in v},
            "restricted": {k: v for k, v in ((restricted.record(d)["marked"] or {}).items()) if k in ("on", "by", "reason")}}
    out["firm"] = {k: [(r["by"], r.get("role"), r["hash"]) for r in v] for k, v in sorted(approval.history().items())}
    out["ledger"] = sorted((r.get("kind"), r.get("action"), r.get("sentence"), r.get("who"), r.get("role"), r.get("case")) for r in events.rows(events.base_path())
                           if r.get("kind") != "audit")
    return out


def by_the_case_page(srv, cookie) -> int:
    """What the case page's own buttons send, written out by hand (not read from the queue): the card's Acknowledge, the explanation's Approve, item 4's Approve, the
    restriction's Restrict this case, and the Settings page's Approve for a practice."""
    from rules import approval

    sent = []
    for n in range(20):
        case = f"case-{n:02d}"
        sent.append(("/api/decide", {"client": case, "item_ids": ["alert:folder.criminal_history"], "action": "acknowledge"}))
        if n < 6:
            sent.append(("/api/explain", {"client": case, "action": "approve", "key": K["committed_crime"]}))
        elif n < 10:
            sent.append(("/api/eoir26a", {"client": case, "action": "approve_sentence", "text": SENTENCE}))
        elif n < 14:
            sent.append(("/api/access", {"client": case, "action": "mark", "reason": REASON}))
    sent += [("/api/rules/approve", {"rule": e["id"]}) for e in approval.catalog() if e["kind"] == "practice"]
    for route, body in sent:
        status, out = call(srv.base + route, cookie, body | {"reviewer": "Sam Attorney"})
        assert status == 200, (route, body, out)
    return len(sent)


def through_the_queue(srv, cookie) -> int:
    """The same sitting, every action read from the queue and sent to the route it names, with what the attorney types in the box the action asks for."""
    got, rows = everything(srv, cookie)
    assert got["total"] == 40
    done = 0
    for r in rows:
        act = next(a for a in r["actions"] if a["id"] in ("approve", "acknowledge", "confirm", "mark"))
        body = dict(act["body"]) | {"reviewer": "Sam Attorney"}
        for box in act["inputs"]:
            assert box["required"] and box["name"] == "reason"
            body[box["name"]] = REASON
        status, out = call(srv.base + act["route"], cookie, body)
        assert status == 200, (r["id"], act, out)
        done += 1
    return done


def test_twenty_cases_and_forty_items_are_cleared_in_forty_actions_and_leave_what_the_case_page_leaves(tmp_path, monkeypatch):
    shots, counts = {}, {}
    for how in ("page", "queue"):
        firm = make_firm(tmp_path / how, monkeypatch)
        twenty_cases(firm)
        srv = serve_app(firm)
        try:
            cookie = sign_in(srv.base, ATTORNEY)
            counts[how] = by_the_case_page(srv, cookie) if how == "page" else through_the_queue(srv, cookie)
            if how == "queue":
                # nothing the queue offered is still open on a case the attorney may be told of; the four restricted cases left it with the restriction
                got, rows = everything(srv, cookie)
                assert {r["case"] for r in rows if r["case"]} <= {f"case-{n:02d}" for n in range(10, 14)} or not rows
                assert not [r for r in rows if r["kind"] in ("part14", "eoir26a", "practice", "g28")]
            shots[how] = snapshot(firm)
        finally:
            srv.httpd.shutdown()
    assert counts["queue"] == 40 and counts["page"] == 40
    assert shots["queue"] == shots["page"]
    cases = shots["queue"]["cases"]
    assert all(any(v[0] == "acknowledge" and v[2] == "Sam Attorney" and v[3] == "attorney" for v in c["decisions"].values()) for c in cases.values())  # every card, by the attorney
    assert sum(1 for c in cases.values() if c["part14"]) == 6 and sum(1 for c in cases.values() if c["eoir26a"]) == 4 and sum(1 for c in cases.values() if c["restricted"]) == 4
    assert len(shots["queue"]["firm"]) == 6 and {r[0] for r in shots["queue"]["ledger"]} >= {"decisions", "policies", "access"}


# -- the rules that hold ---------------------------------------------------------------------------------------------------------------------------


def test_a_restricted_cases_items_are_for_the_attorneys_named_on_it_and_are_counted_nowhere_else(firm, server):
    hidden = write_case(firm.clients, "case-hidden", "Rosa Exemplo", alert=True)
    write_case(firm.clients, "case-open", "Ana Exemplo", alert=True)
    restricted.mark(hidden, True, REASON, "Sam Attorney", "attorney")
    sam, ana = sign_in(server.base, ATTORNEY), sign_in(server.base, OTHER_ATTORNEY)
    for cookie in (sam, ana):  # restricted, and no one named: no attorney is told of its items
        got, rows = everything(server, cookie)
        assert {r["case"] for r in rows if r["case"]} == {"case-open"}
        assert "Rosa" not in json.dumps(got).title().replace("ROSA", "Rosa") or "ROSA" not in json.dumps(got)
        assert got["total"] == 1 + 6 and sum(k["count"] for k in got["kinds"]) == got["total"]
    restricted.name_person(hidden, OTHER_ATTORNEY, True, "Sam Attorney", "attorney", "Ana Attorney")
    server.app.roster.touch("case-hidden")  # (the case page's own route does this after every change)
    got, rows = everything(server, ana)  # named: Ana sees it, in the rows and in every count
    assert {r["case"] for r in rows if r["case"]} == {"case-open", "case-hidden"} and got["total"] == 2 + 6
    assert next(k for k in got["kinds"] if k["id"] == "card")["count"] == 2 and sum(p["count"] for p in got["people"]) == got["total"]
    status, work = call(server.base + "/api/work?owner=attorney", ana)
    assert work["approvals"]["total"] == 2 + 6
    got, rows = everything(server, sam)  # Sam restricted it and is not named: not in his rows, not in his counts, not in My work
    assert {r["case"] for r in rows if r["case"]} == {"case-open"} and got["total"] == 1 + 6
    status, work = call(server.base + "/api/work?owner=attorney", sam)
    assert work["approvals"]["total"] == 1 + 6
    jane = sign_in(server.base, PARALEGAL)
    assert call(server.base + "/api/approvals", jane)[0] == 403


def test_a_kind_the_product_adds_later_appears_without_a_change_to_the_screen(firm, server, monkeypatch):
    monkeypatch.setattr(approvals, "KINDS", dict(approvals.KINDS))
    approvals.register("retention", "A file to destroy", "Files waiting to be destroyed", "firm",
                       lambda ctx: [{"ref": "box-1", "what": "The made-up closed file of the Exemplo family", "why": "Seven years have passed since the case closed.", "at": "2026-09-01T09:00:00-04:00"}])
    sam = sign_in(server.base, ATTORNEY)
    got, rows = everything(server, sam)
    assert next(k for k in got["kinds"] if k["id"] == "retention") == {"id": "retention", "label": "A file to destroy", "plural": "Files waiting to be destroyed", "scope": "firm", "count": 1}
    row = next(r for r in rows if r["kind"] == "retention")
    assert row["what"].startswith("The made-up closed file") and row["date"] == "09/01/2026" and row["waited_days"] == 34 and got["oldest"]["date"] == "09/01/2026"
    assert queue(server, sam, kind="retention")["matching"] == 1


def test_the_page_is_fifty_rows_oldest_first_in_each_kind_with_a_filter_by_kind_and_by_person(firm, server):
    for n in range(60):
        write_case(firm.clients, f"case-{n:02d}", f"Pessoa{n:02d} Exemplo", alert=True, at=f"2026-09-{1 + (59 - n) % 28:02d}T09:00:00-04:00", yes=("committed_crime",) if n == 7 else ())
    paralegal_draft(firm.clients / "case-07")
    sam = sign_in(server.base, ATTORNEY)
    first = queue(server, sam, kind="card")
    assert (first["matching"], first["pages"], first["size"], len(first["rows"])) == (60, 2, 50, 50) and len(queue(server, sam, kind="card", page=2)["rows"]) == 10
    dates = [r["at"] for r in first["rows"]]
    assert dates == sorted(dates, key=clock.key)  # the oldest first
    everyone = queue(server, sam)
    assert everyone["total"] == 60 + 1 + 6 and len(everyone["rows"]) == 50 and [r["kind"] for r in everyone["rows"]] == ["card"] * 50  # grouped: the cards, then the rest
    assert {r["kind"] for r in queue(server, sam, page=2)["rows"]} >= {"part14", "practice"}
    by_jane = queue(server, sam, by="Jane Doe")
    assert by_jane["matching"] == 2 and {r["case"] for r in by_jane["rows"]} == {"case-07"}  # the explanation she wrote, and the card on the case she worked
    assert queue(server, sam, kind="part14", by="Jane Doe")["matching"] == 1 and queue(server, sam, kind="card", by="Nobody")["matching"] == 0
    assert call(server.base + "/api/approvals?page=x", sam)[0] == 400


def test_the_ask_the_paralegal_action_is_a_task_on_the_case_with_the_attorneys_note_and_the_open_action_names_the_card(firm, server):
    write_case(firm.clients, "case-ask", "Ana Exemplo", alert=True)
    sam = sign_in(server.base, ATTORNEY)
    row = next(r for r in queue(server, sam)["rows"] if r["case"] == "case-ask")
    ask = next(a for a in row["actions"] if a["id"] == "ask")
    assert ask["route"] == "/api/case-notes" and [b["name"] for b in ask["inputs"]] == ["who", "note"] and ask["body"]["date"] == "2026-10-07"
    status, out = call(server.base + ask["route"], sam, ask["body"] | {"who": PARALEGAL, "note": "Is the citation in the folder the client's? Please ask her.", "reviewer": "Sam Attorney"})
    assert status == 200, out
    tasks = out["tasks"]["open"] if "open" in out["tasks"] else next(iter(out["tasks"].values()))
    assert any("The attorney asks: Criminal history in the folder" in json.dumps(t) and "Please ask her" in json.dumps(t) for t in tasks)
    assert next(a for a in row["actions"] if a["id"] == "open")["open"] == {"tab": "attorney", "card": 0, "client": "case-ask"}


def test_every_action_the_queue_offers_is_a_route_that_already_exists_and_is_gated_like_the_case_page(firm, server):
    from review import server as srv

    twenty_cases(firm)
    sam = sign_in(server.base, ATTORNEY)
    got, rows = everything(server, sam)
    routes = {a["route"] for r in rows for a in r["actions"] if a["route"]}
    assert routes == {"/api/decide", "/api/explain", "/api/eoir26a", "/api/access", "/api/rules/approve", "/api/case-notes"}
    assert routes - {"/api/rules/approve"} <= srv.CASE_POST  # a case route is gated by who may open the case, as it is on the case page
    for r in rows:
        for a in r["actions"]:
            if a["route"] in srv.CASE_POST:
                assert a["body"]["client"] == r["case"]
    jane = sign_in(server.base, PARALEGAL)  # and a paralegal who sends one of them is refused by the item's own route, not by the queue
    act = next(a for r in rows for a in r["actions"] if a["id"] == "approve" and a["route"] == "/api/explain")
    assert call(server.base + act["route"], jane, act["body"] | {"reviewer": "Jane Doe"})[0] == 403


# -- the morning report ----------------------------------------------------------------------------------------------------------------------------


def test_the_morning_report_says_how_many_approvals_wait_and_counts_no_restricted_case(tmp_path, monkeypatch):
    import overnight

    firm = make_firm(tmp_path, monkeypatch)
    root, out_root = tmp_path / "clients", firm.clients
    for name in ("c-open", "c-minor"):
        (root / name / "source").mkdir(parents=True)
        (root / name / "source" / "scan.pdf").write_bytes(b"%PDF-1.4 sample")

    def run_one(name, source, out):
        write_case(Path(out).parent, Path(out).name, "Ana Exemplo", alert=True)
        return {"status": "done", "client": name, "counts": {"blocking": 0, "review": 1}, "seconds": 1.0, "errors": {}, "documents": 1}

    out_root.mkdir(parents=True)
    write_case(out_root, "c-minor", "Beatriz Exemplo", alert=True)
    restricted.mark(out_root / "c-minor", True, REASON, "Sam Attorney", "attorney")
    lines = []
    overnight.run(root, out_root, tmp_path / "data", runner=run_one, log=lines.append)
    report = (tmp_path / "data" / "batch_report.txt").read_text(encoding="utf-8")
    assert "  7 approvals waiting, the oldest from " in report  # c-open's card and the firm's six practices; c-minor's card is not counted
    assert any(x.startswith("7 approvals waiting") for x in lines) and "c-minor" not in report and not any("c-minor" in x for x in lines)
