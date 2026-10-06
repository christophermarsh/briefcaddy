"""Restricted cases (src/restricted.py) through the review app: a VAWA case is restricted by law, an attorney restricts any
other case (a minor's SIJ case) with a reason, names the staff who may open it, and may switch automatic messages on.

The heart of it: every route of the review app that lists cases is enumerated from the server's own code, and each one is
asked, as a paralegal not named on the case, whether the restricted case shows up. A route added later that is not
classified here fails the test until someone decides how it hides a restricted case.

Everyone here is made up ("Ana Clara Exemplo Souza", "Rosa Exemplo", "Beatriz Exemplo Lima").
"""

from __future__ import annotations

import json
import ast
import re
import textwrap
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from urllib.parse import quote
from pathlib import Path

import pytest

import index
import overnight
import restricted
import second_factor
from factgraph import FactGraph
import schema_path
from communication_fixture import installation, approve_client, accepted_link

_REPO = Path(__file__).resolve().parent.parent
TODAY = date.today()
PASSWORD = "correct horse battery staple"  # secret-scan: allow (a made-up test password)
SECRET = ("case-rosa", "ROSA", "Rosa", "A012345678", "IOE0912345678")  # nothing of the VAWA case, for someone not named on it
ODD = "Lia Exemplo São, Vawa"  # a VAWA case's folder named the way a firm names folders
ODD_SECRET = ("Lia Exemplo", "SEGURA", "Segura", "A055566677", "055566677")


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def doc(doc_id, type_, *, expires=None, text="", a_number="", receipt=""):
    return {"id": doc_id, "files": [f"{doc_id}.pdf"], "doc_ids": [f"{doc_id}.pdf"], "pages": [1], "type": type_, "confidence": 0.9, "person": "applicant",
            "person_set_by": None, "language": "en", "issued": None, "expires": expires,
            "identifiers": {"a_number": a_number, "receipt": receipt, "passport": "", "ssn_last4": ""}, "quality": "readable", "hash": doc_id * 4,
            "source": "folder", "added": "2026-09-01T10:00:00+00:00", "roles": [], "tags": [], "confidential": None, "text": text, "translated": None}


def make_case(root: Path, case_id: str, name: str, docs, *, filings=(), decisions=None):
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(case_id)
    given, _, family = name.upper().partition(" ")
    from portal.bank import PORTAL_DOC_ID
    # This fixture supplies typed intake names, not a scanned questionnaire.
    # Use the actual portal-answer source identity; no missing paper is approved.
    for key, value in {"applicant.given_name": given, "applicant.family_name": family}.items():
        g.add_source(key, PORTAL_DOC_ID, "intake_questionnaire", value, value, 0.95)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": case_id, "classifications": {}}), encoding="utf-8")
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": list(docs)}), encoding="utf-8")
    if filings:
        (d / "status.json").write_text(json.dumps({"filings": [{"filing": f, "title": t, "mailed_on": "2026-09-01", "carrier": "USPS", "tracking": "", "by": "Paulo Paralegal"}
                                                               for f, t in filings]}), encoding="utf-8")
    if decisions:
        (d / "decisions.json").write_text(json.dumps({iid: {"action": "acknowledge", "values": {}, "reviewer": who, "note": "", "at": at,
                                                           "item": {"id": iid, "kind": "reading", "level": "review", "title": "A made-up item", "group": "g", "facts": []}}
                                                      for iid, (who, at) in decisions.items()}), encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path, monkeypatch):
    """Three cases: Ana (an ordinary case), Rosa (a VAWA self-petition: restricted by law), Beatriz (an SIJ case an attorney
    may restrict); Rosa in the client portal; a notice waiting in the inbox for Rosa's case and an asylum notice nobody has
    matched; last night's run, which could not read one of Rosa's documents."""
    data = installation(tmp_path, monkeypatch)
    root = data / "clients"
    for key, target in {"I485_RULES_APPROVED": data / "rules_approved.json", "I485_MAINTENANCE_LOG": data / "maintenance_log.json",
                        "I485_EVENTS": data / "events.jsonl", "I485_SETTINGS": data / "settings.json", "I485_CASES": root,
                        "I485_PROSPECTS": data / "prospects", "PORTAL_DATA": data / "portal"}.items():
        monkeypatch.setenv(key, str(target))
    monkeypatch.setenv("I485_INDEX", str(data / "index.db"))
    monkeypatch.setenv("I485_INBOX", str(data / "inbox"))
    index._LAST_REFRESH.clear()
    make_case(root, "case-ana", "Ana Clara Exemplo Souza", [doc("a1", "passport", expires=iso(40), text="Passaporte Sorocaba"), doc("a2", "advance_parole", expires=iso(100))],
              decisions={"i1": ("Ana Attorney", "2026-09-20T10:00:00+00:00")})
    make_case(root, "case-rosa", "Rosa Exemplo", [doc("r1", "passport", expires=iso(30), text="Passaporte Sorocaba", a_number="A012345678"),
                                                 doc("r2", "declaration", text="declaration of abuse", receipt="IOE0912345678"), doc("r3", "advance_parole", expires=iso(90))],
              filings=[("vawa", "I-360 VAWA self-petition")], decisions={"i1": ("Paulo Paralegal", "2026-09-21T10:00:00+00:00")})
    make_case(root, "case-bia", "Beatriz Exemplo Lima", [doc("b1", "passport", expires=iso(50), text="Passaporte Sorocaba")])
    # a VAWA case whose folder is named as a firm names folders: spaces, an accent, a comma (ids are folder names verbatim)
    make_case(root, ODD, "Lia Segura", [doc("l1", "passport", expires=iso(35), text="Passaporte Sorocaba", a_number="A055566677")],
              filings=[("vawa", "I-360 VAWA self-petition")])
    monkeypatch.setenv("I485_CASES", str(root))  # the portal's messages check these case folders (src/portal/notify.py)
    from portal.store import PortalStore

    store = PortalStore(data / "portal")
    store.add_client("case-rosa", "Rosa Exemplo", email="rosa@example.com", language="pt")
    (root / "pilot-nova").mkdir()  # Canonical unprocessed scaffold, no synthetic fact graph.
    store.add_client("pilot-nova", "Nova Exemplo Teste", email="nova@example.com", language="pt")  # invited, no case folder yet
    inbox = data / "inbox"
    (inbox / "waiting").mkdir(parents=True)
    queue = []
    for nid, form, cands in (("n-rosa", "I-360", [{"case": "case-rosa", "name": "ROSA EXEMPLO"}]), ("n-asylum", "I-589", []), ("n-vawa", "I-360", [])):
        (inbox / "waiting" / f"{nid}.pdf").write_bytes(b"%PDF-1.4 made up")
        queue.append({"id": nid, "hash": nid, "file": f"{nid}.pdf", "original": "mail.pdf", "pages": [1], "of": 1, "scanned": TODAY.isoformat(),
                      "read": {"court": False, "kind": "receipt", "receipt": "IOE0912345678", "form": form, "names": ["ROSA EXEMPLO"], "a_number": "012345678"},
                      "what": f"{form} receipt notice (receipt IOE0912345678)", "status": "ambiguous", "why": "The name on the notice matches one case",
                      "candidates": cands, "queued_at": "2026-10-02T08:00:00+00:00"})
    (inbox / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    from clock import stamp

    (inbox / "inbox_log.jsonl").write_text(json.dumps({"event": "routed", "at": stamp(), "case": "case-rosa", "name": "ROSA EXEMPLO",
                                                       "how": "receipt number", "what": "I-360 receipt notice", "read": {"kind": "receipt"}}) + "\n", encoding="utf-8")
    (data / "batch_log.jsonl").write_text(json.dumps({"started_at": "2026-10-01T02:00:00+00:00", "finished_at": "2026-10-01T03:30:00+00:00", "version": "x",
                                                      "done": 2, "failed": 1, "left": 0, "stopped": None}) + "\n", encoding="utf-8")
    (data / "batch_state.json").write_text(json.dumps({"case-rosa": {"status": "failed", "at": "2026-10-01T03:00:00+00:00", "error": "a document could not be read"}}),
                                           encoding="utf-8")
    index.rebuild_all(root, data / "index.db")
    return root


@pytest.fixture
def app(world):
    from review.auth import Accounts
    from review.server import ReviewApp

    accounts = Accounts(world.parent / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("kim@firm.example", "Kim Exemplo", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)  # an attorney signs in with a code (review/auth.py)
    application = ReviewApp(world, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                            portal_root=world.parent / "portal", accounts=accounts)
    application.roster.wait()
    return application


@pytest.fixture
def server(app):
    from review.server import make_handler, serve

    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def call(url, cookie=None, body=None):
    headers = {"X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}) | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read().decode("utf-8", "replace")
    except urllib.error.HTTPError as e:
        return e.code, e.read().decode("utf-8", "replace")


def sign_in(base, email):
    req = urllib.request.Request(base + "/api/login", data=json.dumps({"email": email, "password": PASSWORD}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1"}, method="POST")
    with urllib.request.urlopen(req) as r:
        cookie, owed = r.headers.get("Set-Cookie").split(";")[0], json.loads(r.read())["user"].get("second_factor")
    return second_factor.finish(base, email, cookie) if owed else cookie


# -- the record ------------------------------------------------------------------------------------------------


def test_a_vawa_case_is_restricted_by_law_and_it_cannot_be_lifted(world):
    rosa, ana = world / "case-rosa", world / "case-ana"
    assert restricted.law(rosa) == "1367" and restricted.is_restricted(rosa) and not restricted.is_restricted(ana)
    assert json.loads((rosa / restricted.CACHE).read_text())["law"] == "1367"  # kept until a file it was worked out from changes
    with pytest.raises(ValueError, match="cannot be lifted"):
        restricted.mark(rosa, False, "", "Sam Attorney", "attorney")
    state = restricted.state(rosa)
    assert state["restricted"] and state["law_words"] == "VAWA, T or U visa case (8 U.S.C. 1367)" and not state["automatic_messages"]
    paralegal, attorney = {"email": "jane@firm.example", "role": "paralegal"}, {"email": "sam@firm.example", "role": "attorney"}
    assert not restricted.visible_to(paralegal, rosa) and restricted.visible_to(attorney, rosa) and restricted.visible_to(None, rosa)
    assert restricted.visible_to(paralegal, ana)


def test_an_attorney_restricts_a_case_with_a_reason_and_only_an_attorney(world):
    bia = world / "case-bia"
    with pytest.raises(PermissionError):
        restricted.mark(bia, True, "a minor", "Jane Doe", "paralegal")
    with pytest.raises(ValueError, match="Say why"):
        restricted.mark(bia, True, "  ", "Sam Attorney", "attorney")
    restricted.mark(bia, True, "The client is a minor (SIJ).", "Sam Attorney", "attorney")
    state = restricted.state(bia)
    assert state["restricted"] and state["law"] is None and state["marked"]["by"] == "Sam Attorney" and state["marked"]["reason"] == "The client is a minor (SIJ)."
    assert [h["what"] for h in state["history"]] == ["Restricted the case"] and state["history"][0]["reason"] == "The client is a minor (SIJ)."
    restricted.mark(bia, False, "", "Sam Attorney", "attorney")
    assert not restricted.is_restricted(bia) and [h["what"] for h in restricted.state(bia)["history"]] == ["Restricted the case", "Lifted the restriction"]


def test_a_case_whose_files_cannot_be_read_is_restricted_until_they_can(world):
    """An unreadable status.json may hide a U visa filing: the case is closed (fails closed), and not cached that way."""
    d = make_case(world, "case-uvisa", "Uma Exemplo", [doc("u1", "passport")], filings=[("u_visa", "I-918 U visa petition")])
    assert restricted.law(d) == "1367"
    (d / "status.json").write_text('{"filings": [{"filing": "u_vi', encoding="utf-8")  # cut off mid-write
    assert restricted.law(d) == restricted.UNKNOWN and restricted.is_restricted(d)
    assert not restricted.visible_to({"email": "jane@firm.example", "role": "paralegal"}, d)
    assert restricted.state(d)["law_words"] == restricted.UNREADABLE
    with pytest.raises(ValueError, match="could not be read"):
        restricted.mark(d, False, "", "Sam Attorney", "attorney")
    (d / "status.json").write_text(json.dumps({"filings": []}), encoding="utf-8")
    assert restricted.law(d) is None  # readable again: worked out afresh, nothing stale cached
    ana = world / "case-ana"
    (ana / restricted.FILE).write_text("{not json", encoding="utf-8")  # an attorney's record that cannot be read
    assert restricted.is_restricted(ana) and restricted.record(ana)["people"] == []


def test_the_reason_never_reaches_the_client_portal(world):
    restricted.mark(world / "case-rosa", True, "Abuser reads her texts", "Sam Attorney", "attorney")
    portal = world.parent / "portal"
    assert not any("Abuser" in p.read_text(encoding="utf-8", errors="replace") for p in portal.rglob("*") if p.is_file())


# -- every route that lists cases ---------------------------------------------------------------------------------

# The review app's routes, each classified. LISTING: lists or counts cases across the firm, and must leave a restricted case
# out for someone not named on it. The routes that open one client are server.CASE_GET and CASE_POST: each answers 404
# "unknown client" for a case the person may not see, the same as for an id that names nothing. NEITHER: no case in it (or
# the attorney's only, or one notice). Routes are read from the handler's own code (src/review/server.py).
LISTING = {"/api/clients": "", "/api/case-list": "?scope=all", "/api/overview": "", "/api/deadlines": "", "/api/work": "?owner=paralegal", "/api/search": "?q=sorocaba", "/api/reports": "",
           "/api/reports.csv": "?table=cases", "/api/expiring": "?days=365", "/api/inbox": "", "/api/inbox/cases": "?q=exemplo",
           "/api/learning": "", "/api/month": "?month=" + TODAY.strftime("%Y-%m") + "&scope=firm",
           "/api/prospects": "", "/api/prospects.csv": ""}  # the first calls (src/prospects.py): a restricted prospect is left out for anyone not named on it
NEITHER = {"/api/promotion-recovery", "/api/contact-recovery", "/api/client-wording", "/api/prospect-communication", "/", "/api/me", "/auth/start", "/auth/callback", "/api/maintenance", "/api/settings", "/api/rules", "/api/staff",
           "/api/accuracy", "/api/accuracy.pdf", "/api/connections", "/api/drive-settings",  # current attorney plus all mapped-case access for Drive configuration
           "/api/accuracy/references",  # the attorney's only: the comparison with hand-filled references (src/accuracy.py); a case the reader may not open is counted, not listed
           "/api/front-desk", "/api/policies",  # what the Add a client form offers; the firm's standard answers (review/front_desk.py, rules/firm_policies.py)
           "/api/getting-started",  # the attorney's first-day list: no case in it (src/getting_started.py)
           "/api/staff_log", "/api/staff_log.csv", "/api/views", "/api/views.csv", "/api/messages_on",  # what the office did (review/oversight.py): the attorney's only, no case in them
           "/api/policy_changes", "/api/policy_changes.csv",
           "/api/firm_events", "/api/firm_events.csv",  # what changed across the firm (the event ledger): the attorney's only, and a hidden case's rows are left out
           "/api/export-firm", "/api/export-firm.zip",  # the export of the firm's data: the attorney's only, and it holds every case (the firm's data)
           "/api/calendar",  # the signed-in person's own calendar address (src/calendar_feed.py): no case in it
           "/api/conflicts", "/api/conflicts.csv",  # Settings, Conflict checks (src/conflicts.py): the attorney's only, the hits in full; a paralegal gets 403
           "/api/inbox/file",  # one waiting notice: refused when it is a restricted case's (test below)
           "/api/jobs/firm",  # the readings that belong to no case (the inbox) and how many wait: no case is named, and a job on a case is shown only to someone who may open it (tests/test_jobs.py)
           # one prospect's page and its letter (src/prospects.py): opened through ReviewApp.prospect_dir, so a restricted prospect is the one answer a made-up id gets (tests/test_prospects.py)
           "/api/prospect", "/api/prospect-letter.pdf",
           "/api/firm-documents",  # Settings, Firm documents: the firm's letters, no case in them (src/engagement.py)
           "/api/wordings", "/api/wordings.json",  # Settings, Firm wordings (src/wordings.py): no case in them but the ids of the cases a wording was used on, each shown only to someone who may open that case (tests/test_wordings.py)
           "/api/retention-rules",  # Settings, Keeping closed files (src/purge.py): the attorney's only (403 for a paralegal); the purges waiting are listed for attorneys, who may open every case
           "/api/find",  # Find across the firm (src/find.py): the switch, and for an attorney who asked what; no case named or counted (tests/test_find.py)

           "/api/posture",  # Settings, This computer (src/posture.py): the machine's duties as last read; the attorney's only (a paralegal gets 403); names no case (tests/test_posture.py)
           "/api/health",  # Existing attorney-only installation health; no case is named (docs/health.md).
           "/api/today",  # Today (src/day_plan.py): the day plan of the person signed in; the stricter rule for a restricted case, in every row, count and line (tests/test_day_plan.py)
           "/api/approvals",  # My approvals (src/approvals.py): the attorney's only (a paralegal gets 403); an attorney is told of a restricted case's items only when named on it (tests/test_approvals_queue.py)
           "/api/retention",  # Keeping current, files past their keeping date: the attorney's only, and every attorney sees every case
           # Settings, Let support in (the attorney's: the current support session and the last ten), and the pages made for support itself (src/support.py): the
           # install's health, the case list as "Case N" and one case's shape, each built through may_open (a restricted case is absent for support) (tests/test_support_access.py)
           "/api/support", "/api/support/health", "/api/support/cases", "/api/support/case"}
NEITHER_POST = {"/api/promotion-recovery", "/api/contact-recovery", "/api/client-wording", "/api/prospect-communication", "/api/login", "/api/password", "/api/logout", "/api/code", "/api/enrol", "/api/staff", "/api/settings", "/api/maintenance", "/api/rules/approve",
                "/api/accuracy/mark",  # the attorney's mark that a hand-filled reference was wrong (src/accuracy.py); checks the case it names
                "/api/connections", "/api/drive-settings", "/api/drive-settings-audit",  # Drive settings additionally recheck every mapped-case ACL
                "/api/client-add", "/api/policies",  # a new client (no case yet); the attorney's policy edits
                "/api/prospect-new", "/api/prospect-change",  # a first call recorded; a note, task, answer, send or decline on one prospect: opened through prospect_dir first (src/prospects.py)
                # the conflict search (src/conflicts.py): attorney and paralegal, for Add a client, with the gate inside: each hit on a case the person
                # may not open (may_open) comes back as "a hit on a case you cannot open" and nothing else (tests/test_conflict_search.py, the leak test).
                # It opens no case: the people searched for are typed, not a case id. The note on a search by hand is the attorney's.
                "/api/conflict-search", "/api/conflict-note",
                "/api/export-firm",  # start the export of the firm's data: the attorney's only
                "/api/restore-drill",  # "Run it now" on the monthly restore drill (src/backups.py drill): a job, the attorney's only; it names no case and writes nothing into the install (tests/test_restore_drill.py)
                "/api/setup", "/api/getting-started",  # the first attorney (not found once an account exists); the attorney's "seen" mark
                "/api/inbox/read", "/api/calendar",  # make or turn off the person's own calendar address; reminders: no case in it
                "/api/inbox/place", "/api/inbox/not-ours",  # a notice: refused when it is a restricted case's; its "case" is checked (test below)
                "/api/firm-documents", "/api/retention", "/api/wordings",  # the attorney's edit of a letter; the attorney's destruction record (src/engagement.py); the attorney's change to the firm's wordings
                # a question to Find across the firm (src/find.py): a firm route filtered per reader, like the wordings. Each passage is gated before the ranking
                # (a restricted case the person is not named on is in no hit and no count) and by may_open after it; refused (403) while the switch is off (tests/test_find.py)
                "/api/find",
                "/api/support",  # Let support in, or end a support session: the attorney's (with the sign-in code set up); support itself writes nothing (src/support.py)
                "/api/retention-rules"}  # Settings, Keeping closed files: an attorney confirms an office's retention rule or sets the waiting period before a purge (src/purge.py)
# TOKENED: a route whose address is its credential (the calendar feed, /calendar/<token>.ics). A calendar program has no session, so it is answered
# before anyone is asked to sign in; the token opens one person's view only, built through the same gate as the screens (ReviewApp.may_open), and any
# token that is not a current one gets the one 404 a case that does not exist gets. tests/test_calendar_feed.py checks each of those.
TOKENED = {"/calendar/"}
# WEBHOOKS: Clio's webhook (src/connectors/clio_hooks.py), a POST with no cookie and no X-Review-App header, authenticated by the signature on its body (not a TOKENED
# route: the credential is in a header and the body, not the address). It is answered before the X-Review-App check, opens no case (a call names a Clio matter, and
# the only thing it does is put that matter's case on the list the overnight run reads), and a call that fails the signature gets the answer a made-up POST path
# gets. tests/test_clio_webhook.py checks each of those.
WEBHOOKS = {"/clio/webhook"}
SETS = {"CASE_GET", "CASE_POST", "open_paths", "ATTEMPTS", "OPEN_GETS", "GATED_POST"}  # the named sets a dispatch may compare url.path with
LITERALS = re.compile(r'url\.path (?:== "(/[^"]*)"|in \(((?:\s*"/[^"]*",?)+)\s*\))')
USES = re.compile(r"url\.path(?:[,)]| in (?:" + "|".join(SETS) + r")\b)")  # an argument, or one of the named sets


def _dispatch(source: str, method: str) -> str:
    return source.split(f"        def do_{method}(self):", 1)[1].split("\n        def ", 1)[0]


def _routes(method: str, source: str | None = None) -> dict[str, str]:
    """{route: the code of its branch}, for every comparison of url.path in the handler's do_GET or do_POST, at any indent. Fails
    on any comparison that is not == "literal", in ("literal", ...) or in one of SETS: a startswith(), a set by another name, a
    regular expression would let a route past the classification."""
    source = source if source is not None else (_REPO / "src" / "review" / "server.py").read_text(encoding="utf-8")
    body = _dispatch(source, method)
    for m in re.finditer(r"url\.path", body):
        here = body[m.start():m.start() + 400]
        assert LITERALS.match(here) or USES.match(here), f"do_{method}: a route compared in a way this test cannot classify: {here[:80]!r}"
    out: dict[str, str] = {}
    source_body = textwrap.dedent(body)
    for node in ast.walk(ast.parse(source_body)):
        if not isinstance(node, ast.If):
            continue
        test = ast.get_source_segment(source_body, node.test) or ""
        branch = "\n".join(ast.get_source_segment(source_body, statement) or "" for statement in node.body)
        for m in LITERALS.finditer(test):
            for route in [m.group(1)] if m.group(1) else re.findall(r'"(/[^"]*)"', m.group(2)):
                out[route] = out.get(route, "") + "\n" + branch
    return out


def test_every_route_is_classified_and_the_case_sets_match_the_code():
    from review import server

    gets, posts = _routes("GET"), _routes("POST")
    case_get = {r for r, b in gets.items() if re.search(r'q\["client"\]|q\.get\("client"\)', b)}
    case_post = {r for r, b in posts.items() if re.search(r"\(client\b", b)}
    assert case_get == server.CASE_GET and case_post == server.CASE_POST, "a route that opens a client must be in CASE_GET / CASE_POST"
    assert set(gets) <= set(LISTING) | NEITHER | server.CASE_GET, set(gets) - (set(LISTING) | NEITHER | server.CASE_GET)
    assert (set(LISTING) | server.CASE_GET) <= set(gets)
    assert set(posts) == NEITHER_POST | server.CASE_POST, set(posts) ^ (NEITHER_POST | server.CASE_POST)
    assert len(server.CASE_GET) >= 20 and len(server.CASE_POST) >= 30
    # the tokened routes: named here, answered once, before the sign-in check, and by the set, never by a literal in the dispatch
    assert server.TOKENED == TOKENED and not (TOKENED & (set(gets) | set(posts)))
    source = (_REPO / "src" / "review" / "server.py").read_text(encoding="utf-8")
    body = _dispatch(source, "GET")
    assert body.count("self._calendar(url.path)") == 1 and body.index("self._calendar(url.path)") < body.index("self._signed_in(url.path)")
    assert '"/calendar/' not in body and '"/calendar/' not in _dispatch(source, "POST")
    # the webhook: named here, answered once, before the X-Review-App check and the sign-in check, and by the set, never by a literal in the dispatch
    assert server.WEBHOOKS == WEBHOOKS and not (WEBHOOKS & (set(gets) | set(posts))) and not (WEBHOOKS & (set(LISTING) | NEITHER | NEITHER_POST | TOKENED))
    post = _dispatch(source, "POST")
    assert post.count("self._webhook(url.path)") == 1 and post.index("self._webhook(url.path)") < post.index('self.headers.get("X-Review-App")') \
        and post.index("self._webhook(url.path)") < post.index("self._signed_in(url.path)")
    assert '"/clio/webhook"' not in post and '"/clio/webhook"' not in body


def test_the_classification_fails_on_a_route_it_cannot_read():
    source = (_REPO / "src" / "review" / "server.py").read_text(encoding="utf-8")
    for bad in ('url.path.startswith("/api/rules")', "url.path in SOME_ROUTES", 'url.path.endswith(".pdf")'):
        with pytest.raises(AssertionError, match="cannot classify"):
            _routes("GET", source.replace('url.path == "/api/rules"', bad, 1))
    # a route at another indent is still found (and then must be classified)
    moved = source.replace('                elif url.path == "/api/rules":', '                elif url.path == "/api/rules":\n                    if url.path == "/api/new-thing":\n                        pass', 1)
    assert "/api/new-thing" in _routes("GET", moved)
    post_staff = 'elif url.path == "/api/staff":  # the Settings page\'s Staff section (review/auth.py)'
    assert source.count(post_staff) == 1
    added = source.replace(post_staff, 'elif url.path == "/api/brand-new":\n                    pass\n                ' + post_staff, 1)
    assert "/api/brand-new" in _routes("POST", added) and "/api/brand-new" not in NEITHER_POST | __import__("review.server").server.CASE_POST


def test_every_listing_hides_a_restricted_case_from_a_paralegal_not_named_on_it(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    for route, query in LISTING.items():
        status, text = call(server + route + query, jane)
        assert status == 200, (route, text[:200])
        leaked = [s for s in SECRET + ODD_SECRET if s in text]
        assert not leaked, f"{route} shows {leaked} to a paralegal not named on the case"
    # the attorney sees them on the same lists: the test above is not passing for want of the cases
    for route, query in (("/api/clients", ""), ("/api/overview", ""), ("/api/search", "?q=sorocaba"), ("/api/reports", ""), ("/api/expiring", "?days=365"),
                         ("/api/inbox", ""), ("/api/inbox/cases", "?q=rosa")):
        text = call(server + route + query, sam)[1]
        assert "case-rosa" in text or "ROSA" in text, route
    for route, query in (("/api/clients", ""), ("/api/overview", ""), ("/api/search", "?q=sorocaba"), ("/api/reports", ""), ("/api/inbox/cases", "?q=lia")):
        assert "Lia Exemplo" in call(server + route + query, sam)[1] or "SEGURA" in call(server + route + query, sam)[1], route


def test_the_counts_leave_it_out_too(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    para, att = json.loads(call(server + "/api/overview", jane)[1]), json.loads(call(server + "/api/overview", sam)[1])
    assert sum(s["count"] for s in para["stages"]) == len(para["clients"]) == 3 and sum(s["count"] for s in att["stages"]) == 5  # + the invited client
    found = json.loads(call(server + "/api/search?q=sorocaba", jane)[1])
    assert found["total"] == 2 and found["restricted"] == 0 and found["cases"] == 2  # not "1 more in restricted documents"
    assert json.loads(call(server + "/api/search?q=abuse", jane)[1])["restricted"] == 0
    assert json.loads(call(server + "/api/expiring?days=365", jane)[1])["restricted"] == 0
    assert {t["id"]: t["count"] for t in found["types"]} == {"passport": 2, "advance_parole": 1}
    reports = json.loads(call(server + "/api/reports", jane)[1])
    assert reports["cases"] == 2 and not any(t["id"] == "overnight_problems" for t in reports["tables"])


def test_the_audit_of_what_the_office_changes_leaves_a_restricted_case_out_for_a_reader_not_named_on_it(server, world, tmp_path, monkeypatch):
    """Reports' "Boxes the office changes" and its three-case line are built from the cases the reader may open: with the VAWA case three cases made a line, without it two do,
    and a box only the VAWA case changed is no row at all (src/restricted.py: absent from every count, row and line)."""
    import audit_fill

    monkeypatch.setenv("I485_AUDIT_FILL", str(tmp_path / "audit_fill.json"))
    rows = [audit_fill._row(c, audit_fill.FORM, "i485", "applicant.family_name", "Part 1, Item 2", "Family name", "NOT APPLICABLE", "MADEUPNAME") for c in ("case-ana", "case-bia", "case-rosa")]
    rows += [audit_fill._row("case-rosa", audit_fill.FORM, "i485", "applicant.dob", "Part 1, Item 5", "Date of birth", "01/02/2005", "02/01/2005")]
    audit_fill.write(world, {"version": 1, "software": "x", "forms": {"day": "2026-10-01", "cases": 3, "forms": 3, "boxes": 100, "identical": 90, "changed": 4}, "saves": None, "rows": rows, "skipped": []})
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    para, att = json.loads(call(server + "/api/reports", jane)[1]), json.loads(call(server + "/api/reports", sam)[1])
    para_rows = next(t for t in para["tables"] if t["id"] == "audit")["rows"]
    att_rows = next(t for t in att["tables"] if t["id"] == "audit")["rows"]
    assert [(r["box"], r["cases"], r["clients"]) for r in para_rows] == [("Part 1, Item 2: Family name", 2, ["ANA CLARA EXEMPLO SOUZA", "BEATRIZ EXEMPLO LIMA"])]
    assert [(r["box"], r["cases"]) for r in att_rows] == [("Part 1, Item 2: Family name", 3), ("Part 1, Item 5: Date of birth", 1)]
    assert para["alerts"] == [] and len(att["alerts"]) == 1 and "on 3 cases" in att["alerts"][0]["text"]
    body = json.dumps(para) + call(server + "/api/reports.csv?table=audit", jane)[1]
    assert not [s for s in SECRET + ODD_SECRET + ("MADEUPNAME", "Date of birth") if s in body]
    assert "Only the cases you may open are counted" in body


QUERY = "&doc=r1.pdf&page=0&box=0,0,1,1&key=x&form=i485&id=0123456789abcdef"


def test_a_hidden_case_and_a_made_up_one_get_the_same_answer_everywhere(server):
    """No route tells a restricted case from a name that is no case: a paralegal cannot confirm a guessed folder name."""
    from review import server as srv

    jane = sign_in(server, "jane@firm.example")
    hidden = ("case-rosa", ODD)
    for route in srv.CASE_GET:
        for c in hidden + ("nobody-here", "Nobody Here, Ç", ""):
            status, text = call(server + route + "?client=" + quote(c) + QUERY, jane)
            assert (status, json.loads(text)) == (404, srv.UNKNOWN), (route, c)
    for route in srv.CASE_POST:
        for c in hidden + ("nobody-here", ""):
            status, text = call(server + route, jane, {"client": c, "item_id": "x", "action": "confirm", "text": "x", "id": "x"})
            assert (status, json.loads(text)) == (404, srv.UNKNOWN), (route, c)
    # every other route ignores a client it is sent: the same answer for the hidden case and the made-up name
    for route, query in list(LISTING.items()) + [(r, "") for r in NEITHER - {"/", "/auth/start", "/auth/callback", "/api/connections"}]:
        joiner = "&" if "?" in query else "?"
        a = call(server + route + query + joiner + "client=case-rosa", jane)
        b = call(server + route + query + joiner + "client=nobody-here", jane)
        assert a[0] == b[0], (route, a[0], b[0])
    for route in NEITHER_POST - {"/api/login", "/api/password", "/api/logout", "/api/inbox/read"}:
        a = call(server + route, jane, {"client": "case-rosa", "id": "x", "case": "case-rosa", "note": "x"})
        b = call(server + route, jane, {"client": "nobody-here", "id": "x", "case": "nobody-here", "note": "x"})
        assert a == b, (route, a, b)
    # a client invited to the portal and not processed yet is still reachable on the portal routes
    assert call(server + "/api/answers?client=pilot-nova", jane)[0] == 200
    assert call(server + "/api/requests?client=pilot-nova", jane)[0] == 200


def test_a_case_folder_named_with_spaces_accents_and_commas_is_closed_too(server, app):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    from review import server as srv

    for route in srv.CASE_GET:
        status, text = call(server + route + "?client=" + quote(ODD) + QUERY, jane)
        assert (status, json.loads(text)) == (404, srv.UNKNOWN), route
    for route in srv.CASE_POST:
        status, text = call(server + route, jane, {"client": ODD, "item_id": "x", "action": "confirm"})
        assert (status, json.loads(text)) == (404, srv.UNKNOWN), route
    assert call(server + "/api/items?client=" + quote(ODD), sam)[0] == 200  # the attorney opens it
    import time

    for _ in range(50):  # the row is written just after the answer goes out
        if app.views_log.exists() and app.views_log.read_text(encoding="utf-8").endswith("\n"):
            break
        time.sleep(0.05)
    rows = [json.loads(x) for x in app.views_log.read_text(encoding="utf-8").splitlines()]
    assert [(r["client"], r.get("restricted")) for r in rows] == [(ODD, True)]
    call(server + "/api/access", sam, {"client": ODD, "action": "name", "email": "jane@firm.example"})
    assert call(server + "/api/items?client=" + quote(ODD), jane)[0] == 200 and ODD in call(server + "/api/clients", jane)[1]


def test_naming_a_paralegal_opens_the_case_to_her_and_nobody_else(server, app):
    jane, kim, sam = sign_in(server, "jane@firm.example"), sign_in(server, "kim@firm.example"), sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/access", jane, {"client": "case-ana", "action": "mark", "reason": "x"})
    assert status == 403  # a paralegal restricts nothing
    status, text = call(server + "/api/access", sam, {"client": "case-rosa", "action": "name", "email": "jane@firm.example"})
    access = json.loads(text)
    assert status == 200 and [p["name"] for p in access["people"]] == ["Jane Doe"] and {s["email"] for s in access["staff"]} == {"jane@firm.example", "kim@firm.example"}
    assert call(server + "/api/access", sam, {"client": "case-rosa", "action": "name", "email": "sam@firm.example"})[0] == 400  # an attorney sees every case anyway
    assert call(server + "/api/access", sam, {"client": "case-rosa", "action": "name", "email": "nobody@firm.example"})[0] == 400
    app.roster.sync()
    app.roster.wait()
    assert "case-rosa" in call(server + "/api/clients", jane)[1] and call(server + "/api/items?client=case-rosa", jane)[0] == 200
    items = json.loads(call(server + "/api/items?client=case-rosa", jane)[1])
    assert items["access"]["restricted"] and not items["access"]["can_change"] and items["access"]["staff"] == []
    assert any(r["doc"] == "r2" for r in json.loads(call(server + "/api/search?q=abuse", jane)[1])["results"])  # its confidential documents too
    assert "case-rosa" not in call(server + "/api/clients", kim)[1] and call(server + "/api/items?client=case-rosa", kim)[0] == 404
    call(server + "/api/access", sam, {"client": "case-rosa", "action": "unname", "email": "jane@firm.example"})
    assert call(server + "/api/items?client=case-rosa", jane)[0] == 404


def test_an_attorney_marked_case_disappears_for_the_paralegal(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    assert "case-bia" in call(server + "/api/clients", jane)[1]
    assert json.loads(call(server + "/api/items?client=case-bia", sam)[1])["access"]["restricted"] is False
    assert call(server + "/api/access", sam, {"client": "case-bia", "action": "mark", "reason": ""})[0] == 400
    status, text = call(server + "/api/access", sam, {"client": "case-bia", "action": "mark", "reason": "The client is a minor."})
    assert status == 200 and json.loads(text)["marked"]["reason"] == "The client is a minor."
    for route, query in LISTING.items():
        assert "case-bia" not in call(server + route + query, jane)[1] and "BEATRIZ" not in call(server + route + query, jane)[1], route
    assert call(server + "/api/items?client=case-bia", jane)[0] == 404


def test_the_case_page_says_who_may_open_it(app):
    sam = {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}
    app.access_change("case-rosa", {"action": "name", "email": "jane@firm.example", "reviewer": "Sam Attorney"}, sam)
    a = app.items("case-rosa", sam)["access"]
    assert a["restricted"] and a["can_change"] and [p["name"] for p in a["people"]] == ["Jane Doe"] and a["people"][0]["by"] == "Sam Attorney"
    lines = [json.loads(x) for x in app.accounts.log_path.read_text().splitlines()]
    assert {"event": "case_access", "email": "sam@firm.example", "client": "case-rosa", "action": "name", "person": "jane@firm.example"}.items() <= lines[-1].items()


def test_every_opening_of_a_restricted_case_carries_the_mark(server, app):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    call(server + "/api/items?client=case-rosa", sam)
    call(server + "/api/documents?client=case-rosa", sam)
    call(server + "/api/items?client=case-ana", jane)
    call(server + "/api/items?client=case-rosa", jane)  # refused: not a view
    import time

    for _ in range(50):  # each row is written just after its answer goes out
        if app.views_log.exists() and len(app.views_log.read_text().splitlines()) >= 3:
            break
        time.sleep(0.05)
    rows = [json.loads(x) for x in app.views_log.read_text().splitlines()]
    assert [(r["client"], r["kind"], r.get("restricted")) for r in rows] == [("case-rosa", "case", True), ("case-rosa", "documents", True), ("case-ana", "case", None)]
    shown = json.loads(call(server + "/api/access_log?client=case-rosa", sam)[1])["rows"]
    assert all(r["restricted"] for r in shown) and shown[0]["what"] == "Listed the case's documents"


# -- the notice inbox ---------------------------------------------------------------------------------------------


def test_a_restricted_cases_notice_waits_nameless_for_someone_who_may_not_see_it(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    waiting = json.loads(call(server + "/api/inbox", jane)[1])["waiting"]
    assert [w["what"] for w in waiting] == [restricted.NOTICE] * 3 and all(w["restricted"] and not w["names"] and not w["candidates"] and not w["a_number"] for w in waiting)
    assert call(server + "/api/inbox/file?id=n-rosa", jane)[0] == 403 and call(server + "/api/inbox/file?id=n-rosa", sam)[0] == 200
    # an I-360 notice no case has claimed (the VAWA self-petition is an I-360): it waits for an attorney too
    assert call(server + "/api/inbox/file?id=n-vawa", jane)[0] == 403 and restricted.protected_form("I-360")
    assert "ROSA EXEMPLO" in call(server + "/api/inbox", sam)[1]  # the attorney sees the names on it
    assert call(server + "/api/inbox/place", jane, {"id": "n-rosa", "case": "case-ana"})[0] == 403
    assert call(server + "/api/inbox/not-ours", jane, {"id": "n-rosa", "note": "not ours"})[0] == 403
    assert json.loads(call(server + "/api/inbox", jane)[1])["recent"] == []  # what went to her case is not in the recent list
    # named on Rosa's case: her notice is Jane's to place; the asylum notice nobody matched still waits for an attorney
    call(server + "/api/access", sam, {"client": "case-rosa", "action": "name", "email": "jane@firm.example"})
    waiting = {w["id"]: w for w in json.loads(call(server + "/api/inbox", jane)[1])["waiting"]}
    assert waiting["n-rosa"]["candidates"] == [{"case": "case-rosa", "name": "ROSA EXEMPLO"}] and waiting["n-asylum"]["what"] == restricted.NOTICE
    assert waiting["n-vawa"]["what"] == restricted.NOTICE


# -- automatic messages -----------------------------------------------------------------------------------------


def test_a_restricted_case_gets_no_automatic_message_until_an_attorney_says_so(app, world, monkeypatch):
    outbox = world.parent / "portal" / "outbox.jsonl"
    sam = {"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}
    out = app.remind("case-rosa", {"reviewer": "Sam Attorney"})
    assert out["delivery"]["status"] == "hand" and out["delivery"]["text"] == restricted.HAND and not outbox.exists()
    asked = app.ask_client("case-rosa", {"reviewer": "Jane Doe", "text": "Please send your passport."})
    assert asked["delivery"]["status"] == "hand" and asked["request"]["text"] == "Please send your passport." and not outbox.exists()  # in the portal, no text
    from portal.notify import Notifier
    from portal.store import PortalStore

    profile = PortalStore(world.parent / "portal").profile("case-rosa")
    assert Notifier(outbox).send(profile, "case_update", "x") == [{"channel": "all", "result": "skipped", "why": "restricted case"}]  # the portal's own default
    with pytest.raises(PermissionError):
        app.access_change("case-rosa", {"action": "messages_on", "reason": "She asked", "reviewer": "Jane Doe"}, {"email": "jane@firm.example", "role": "paralegal"})
    with pytest.raises(ValueError, match="Say why"):
        app.access_change("case-rosa", {"action": "messages_on", "reason": "", "reviewer": "Sam Attorney"}, sam)
    a = app.access_change("case-rosa", {"action": "messages_on", "reason": "She asked for texts; the phone is hers alone.", "reviewer": "Sam Attorney"}, sam)
    assert a["automatic_messages"] and a["messages"]["reason"] == "She asked for texts; the phone is hers alone."
    # The restriction override is separate from actual client approval/control.
    # Use the canonical fictional attorney who may review this protected case.
    import communication_fixture
    monkeypatch.setattr(communication_fixture, "STAFF", communication_fixture.ATTORNEY)
    store = PortalStore(world.parent / "portal")
    approve_client(store, "case-rosa")
    accepted_link(store, "case-rosa")
    from portal.communication_consent import retain_evidence
    from portal.request_readiness import review_request
    scope = store.communication_scope()
    evidence = retain_evidence(scope, b'{"fictional":true,"choice":"client requested complete English wording"}',
                               actor_email=communication_fixture.ATTORNEY, kind="request_wording", store=store, client="case-rosa")
    review_request(scope, store, "case-rosa", asked["request"]["id"], actor_email=communication_fixture.ATTORNEY,
                   evidence_ref=evidence, mode="english_fallback", publish=True,
                   fallback_reason="Fictional actual client choice: keep this complete English question for office-assisted review.")
    assert app.remind("case-rosa", {"reviewer": "Sam Attorney"})["delivery"]["status"] == "queued" and outbox.exists()
    app.access_change("case-rosa", {"action": "messages_off", "reviewer": "Sam Attorney"}, sam)
    assert app.remind("case-rosa", {"reviewer": "Sam Attorney"})["delivery"]["status"] == "hand"


def test_the_portals_messages_never_guess_where_the_case_folders_are(world, tmp_path, monkeypatch):
    """The portal's own sender (portal/app.py, admin.py) takes the case folders from I485_CASES, never from where PORTAL_DATA is;
    when they are not there, nothing is sent at all."""
    from portal.notify import Notifier, delivery
    from portal.store import PortalStore

    elsewhere = tmp_path / "somewhere" / "portal"  # PORTAL_DATA not beside the case folders
    profile = PortalStore(world.parent / "portal").profile("case-rosa")
    assert Notifier(elsewhere / "outbox.jsonl").send(profile, "case_update", "x")[0]["why"] == "restricted case"  # I485_CASES found the case
    monkeypatch.setenv("I485_CASES", str(tmp_path / "no-such-folder"))
    sent = Notifier(elsewhere / "outbox.jsonl").send(PortalStore(world.parent / "portal").profile("pilot-nova"), "reminder", "x")
    assert sent == [{"channel": "all", "result": "skipped", "why": "case folders not found"}] and not (elsewhere / "outbox.jsonl").exists()
    assert delivery(sent)["status"] == "failed" and "case folders were not found" in delivery(sent)["text"]


def test_an_ordinary_case_still_gets_its_messages(app, world):
    from portal.store import PortalStore

    PortalStore(world.parent / "portal").add_client("case-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    store = PortalStore(world.parent / "portal")
    approve_client(store, "case-ana")
    accepted_link(store, "case-ana")
    assert app.remind("case-ana", {"reviewer": "Jane Doe"})["delivery"]["status"] == "queued"


def test_the_screens_say_messages_are_sent_by_hand():
    page = (_REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    assert "Office/manual follow-up: no current approved channel is available." in page and page.count("byHand()") >= 3
    assert "Restricted case: " in page and "Who may open this case" in page


# -- the overnight morning report ----------------------------------------------------------------------------------


def test_the_morning_report_never_names_a_restricted_case(tmp_path):
    root = tmp_path / "clients"
    for name in ("c-open", "c-vawa", "c-minor"):
        (root / name / "source").mkdir(parents=True)
        (root / name / "source" / "scan.pdf").write_bytes(b"%PDF-1.4 sample")
    out_root = tmp_path / "out"
    (out_root / "c-minor").mkdir(parents=True)
    restricted.mark(out_root / "c-minor", True, "A minor", "Sam Attorney", "attorney")
    (out_root / "c-vawa").mkdir(parents=True)
    (out_root / "c-vawa" / "status.json").write_text(json.dumps({"filings": [{"filing": "vawa"}]}), encoding="utf-8")

    def run_one(name, source, out):
        Path(out).mkdir(parents=True, exist_ok=True)
        (Path(out) / "fact_graph.json").write_text("{}", encoding="utf-8")
        return {"status": "done", "client": name, "counts": {"blocking": 2, "review": 1}, "seconds": 1.0, "errors": {"scan.pdf": "unreadable"}, "documents": 1}

    lines = []
    overnight.run(root, out_root, tmp_path / "data", runner=run_one, log=lines.append)
    report = (tmp_path / "data" / "batch_report.txt").read_text(encoding="utf-8")
    assert "c-open: 2 blocking" in report and "with a blocking issue for the attorney: 1" in report
    assert "c-vawa" not in report and "c-minor" not in report and "Restricted cases are not named in this report" in report
    assert not any("c-vawa" in x or "c-minor" in x for x in lines)  # nor on the console as it runs


# -- a protected client before any document is processed (docs/research/buyer_walkthrough_4.md, finding 1) ---------------

HELD_NAME = "Rosa Exemplo Vawa"
HELD_SECRET = ("Rosa Exemplo Vawa", "ROSA EXEMPLO VAWA", "rosa-exemplo-vawa", "rosa.vawa@example.com", "555) 010-2222")


def test_the_kind_of_case_says_which_law_protects_it():
    assert [restricted.kind_law(k) for k in ("vawa", "t_visa", "u_visa", "asylum", "sij", "family", "")] == ["1367", "1367", "1367", "208.6", None, None, None]
    for words, law in (("Asylum", "208.6"), ("Affirmative asylum (I-589)", "208.6"), ("I-730 asylee relative", "208.6"), ("VAWA Self-Petition", "1367"),
                       ("U Visa", "1367"), ("U-Visa certification", "1367"), ("T", "1367"), ("u", "1367"), ("I-918", "1367"), ("I-914 T nonimmigrant", "1367"),
                       ("Trafficking victim", "1367"), ("SIJ", None), ("I-485", None), ("Family law", None), ("Immigration", None), ("Tourist visa", None),
                       # 8 CFR 208.6(a): withholding of removal, CAT protection, credible and reasonable fear, refugee admission (eCFR, 10/03/2026)
                       ("Withholding of Removal", "208.6"), ("Withholding", "208.6"), ("CAT", "208.6"), ("cat claim", "208.6"),
                       ("Convention Against Torture", "208.6"), ("Credible fear", "208.6"), ("Reasonable Fear Interview", "208.6"),
                       ("Refugee", "208.6"), ("Refugee-based green card", "208.6"),
                       # Portuguese and Spanish, with or without accents
                       ("Asilo", "208.6"), ("asilo politico", "208.6"), ("Asilo político", "208.6"), ("Asilo/Withholding", "208.6"), ("Refugiado", "208.6"),
                       ("Visa U", "1367"), ("Visto U", "1367"), ("Visa T", "1367"), ("Visto T", "1367"), ("Visto de turista", None),
                       # whole words only: CAT is never a cataract or a certificate
                       ("Cataract surgery waiver", None), ("Certificate of citizenship", None), ("Catalina Exemplo", None), ("Humanitarian", None)):
        assert restricted.kind_law(words) == law, words
    assert restricted.law_words("208.6") == "an asylum case (8 CFR 208.6)" and restricted.law_words("1367").startswith("a VAWA")


def test_a_record_made_at_once_is_made_once(tmp_path):
    case = tmp_path / "clients" / "rosa-exemplo-vawa"
    assert restricted.protect_new(case, "1367", "VAWA self-petition", "Added as", "Paulo Paralegal")
    rec = restricted.record(case)
    assert rec["marked"]["on"] and rec["marked"]["by"] == "Paulo Paralegal" and rec["marked"]["law"] == "1367"
    assert rec["marked"]["reason"] == "Added as VAWA self-petition: the law keeps a VAWA, T or U visa case (8 U.S.C. 1367) confidential from the start."
    assert restricted.is_restricted(case) and not restricted.messages_allowed(case) and restricted.law(case) is None  # no case file yet: the mark holds it
    assert not restricted.protect_new(case, "1367", "VAWA self-petition", "Added as", "Paulo Paralegal")  # once
    restricted.mark(case, False, "", "Sam Attorney", "attorney")  # an attorney's later choice stands: no import or sync marks it again
    assert not restricted.protect_new(case, "1367", "VAWA", "Came in from Clio, practice area", "the Clio sync") and not restricted.is_restricted(case)
    assert not restricted.protect_new(tmp_path / "clients" / "ana", None, "SIJ", "Added as", "x") and not (tmp_path / "clients" / "ana").exists()


def test_a_vawa_client_added_on_the_screen_is_restricted_and_never_invited(server, app, world, monkeypatch):
    """Add a client, kind of case VAWA, texts agreed, "Send the invitation now": restricted at once, nothing sent by any path, and for a
    paralegal not named on it the client is nowhere, answering exactly as a made-up id does."""
    from portal import admin
    from portal.notify import Notifier
    from portal.store import PortalStore
    from review import server as srv

    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    portal = world.parent / "portal"
    outbox = portal / "outbox.jsonl"
    # the conflict search first (src/conflicts.py): her name is a hit on Rosa's restricted case, which Jane may not open, so an attorney decides
    found = json.loads(call(server + "/api/conflict-search", jane, {"name": HELD_NAME, "purpose": "add", "reviewer": "Jane Doe"})[1])
    assert found["hidden"] == 1 and "case-rosa" not in json.dumps(found)
    status, text = call(server + "/api/client-add", jane, {"name": HELD_NAME, "phone": "(555) 010-2222", "email": "rosa.vawa@example.com", "language": "es",
                                                           "consent": {"sms": True, "email": True}, "track": "vawa", "invite": True, "reviewer": "Jane Doe",
                                                           "conflict": {"search": found["id"], "decision": "undecided"}})
    added = json.loads(text)
    assert status == 200 and added["restricted"] and "delivery" not in added and added["law"].startswith("a VAWA") and "in person" in added["note"]
    cid = added["id"]
    assert cid == "rosa-exemplo-vawa" and not outbox.exists()
    rec = restricted.record(world / cid)
    assert rec["marked"]["by"] == "Jane Doe" and "VAWA self-petition" in rec["marked"]["reason"] and not (world / cid / "fact_graph.json").exists()
    store = PortalStore(portal)
    assert store.profile(cid)["track"] == "vawa" and not store.profile(cid).get("invited_at")

    # the paralegal who added her, not named on the case: the client is in no list, and no address tells her id from a made-up one
    for route, query in LISTING.items():
        got = call(server + route + query, jane)
        assert got[0] == 200 and not [s for s in HELD_SECRET if s in got[1]], route
    for route in srv.CASE_GET:
        for c in (cid, "rosa-exemplo-nobody"):
            got = call(server + route + "?client=" + quote(c) + QUERY, jane)
            assert (got[0], json.loads(got[1])) == (404, srv.UNKNOWN), (route, c)
    for route in srv.CASE_POST:
        for c in (cid, "rosa-exemplo-nobody"):
            got = call(server + route, jane, {"client": c, "item_id": "x", "action": "confirm", "text": "x", "id": "x", "again": True, "filing": "n400"})
            assert (got[0], json.loads(got[1])) == (404, srv.UNKNOWN), (route, c)
    para = json.loads(call(server + "/api/overview", jane)[1])
    assert len(para["clients"]) == sum(s["count"] for s in para["stages"]) == 3  # her counts leave the client out as well

    # the attorney sees her, marked restricted, with the line that says why no invitation went
    row = next(r for r in json.loads(call(server + "/api/overview", sam)[1])["clients"] if r["id"] == cid)
    assert row["restricted"] and row["held_note"] == restricted.INVITE_HELD
    assert call(server + "/api/answers?client=" + cid, sam)[0] == 200  # a portal route still opens for her

    # no path sends her anything: Invite and Send again, Remind, the command line's invite to everyone, the portal's "send me a link"
    for again in (False, True):
        sent = json.loads(call(server + "/api/client-invite", sam, {"client": cid, "again": again, "reviewer": "Sam Attorney"})[1])
        assert sent["delivery"]["status"] == "hand"
    assert json.loads(call(server + "/api/remind", sam, {"client": cid, "reviewer": "Sam Attorney"})[1])["delivery"]["status"] == "hand"
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example")
    assert admin.main(["--root", str(portal), "invite"]) == 0
    assert Notifier(outbox).send(store.profile(cid), "invite", "x") == [{"channel": "all", "result": "skipped", "why": "restricted case"}]
    assert not outbox.exists() or "rosa.vawa" not in outbox.read_text(encoding="utf-8")
    assert not store.profile(cid).get("invited_at")  # never marked invited: nothing went

    # the portal's own "send me a link" (portal/app.py): the same "Done!" for her as for anyone, and nothing in the outbox
    from fastapi.testclient import TestClient

    from portal.app import create_app

    with TestClient(create_app(root=portal, base_url="https://portal.example", secure_cookies=False)) as client:
        assert client.post("/api/link", json={"contact": "rosa.vawa@example.com"}, headers={"X-Portal": "1"}).json() == {"ok": True}
    assert any(json.loads(x)["event"] == "link_requested" for x in (store.client_dir(cid) / "events.jsonl").read_text(encoding="utf-8").splitlines())
    assert not outbox.exists() or "rosa.vawa" not in outbox.read_text(encoding="utf-8")
    # and no path made a sign-in link for a message that never went (a link is a credential for 72 hours)
    auth = json.loads((portal / "auth.json").read_text(encoding="utf-8")) if (portal / "auth.json").exists() else {}
    assert not [x for x in (auth.get("links") or {}).values() if x.get("client") == cid]

    # once her documents are processed the mark still holds, and the law's own reading of the case agrees (the track is applied)
    make_case(world, cid, HELD_NAME, [doc("h1", "passport", text="Passaporte")])
    import journey

    journey.mark(world / cid, "track", "Jane Doe", value="vawa", note="Named when the client was added.")
    assert restricted.is_restricted(world / cid) and restricted.law(world / cid) == "1367"
    assert call(server + "/api/items?client=" + cid, jane)[0] == 404 and call(server + "/api/items?client=" + cid, sam)[0] == 200


def test_a_protected_client_added_before_records_were_made_at_once_still_gets_nothing(world):
    """A client whose profile says U visa but who has no record yet (added before this release): the portal still sends nothing."""
    from portal.notify import Notifier
    from portal.store import PortalStore

    store = PortalStore(world.parent / "portal")
    (world / "old-uvisa").mkdir()
    store.add_client("old-uvisa", "Old Exemplo", email="old@example.com", language="pt", consent={"email": True})
    store.update_profile("old-uvisa", track="u_visa")
    assert Notifier(world.parent / "portal" / "outbox.jsonl").send(store.profile("old-uvisa"), "invite", "x")[0]["why"] == "restricted case"
    store.update_profile("old-uvisa", track="family")
    approve_client(store, "old-uvisa")
    accepted_link(store, "old-uvisa")
    assert Notifier(world.parent / "portal" / "outbox.jsonl").send(store.profile("old-uvisa"), "invite", "x")[0]["result"] == "dry-run (outbox)"


def test_a_protected_kind_with_no_record_and_no_case_file_is_closed_and_gets_its_record(server, app, world):
    """Buyer visit 5, verifier: a portal client whose profile names a protected kind, in a folder with no record and no case file (a Docketwise import made
    before records existed, or a held folder whose record was taken away), opened for any paralegal and Invite and Remind sent. Now it answers a paralegal
    exactly as a made-up id does, nothing is sent, and the record is written the first time anything asks."""
    from portal.notify import Notifier
    from portal.store import PortalStore
    from review import server as srv

    portal = world.parent / "portal"
    store = PortalStore(portal)
    states = {"held-gone-vawa": {"track": "vawa"},  # a held folder whose access.json is gone
              "dw-vawa-import": {"docketwise_matter_type": "VAWA Self Petition"},  # an import from before records
              "dw-no-folder-asylum": {"track": "asylum"},  # not even a folder
              # the H2 x G5 merge: a folder that holds the conflict check alone (an add or a sync that stopped before its restriction record)
              "conflict-only-vawa": {"track": "vawa"}}
    for cid, extra in states.items():
        (world / cid).mkdir()
        store.add_client(cid, "Rosa Held Exemplo", email=f"{cid}@example.com", language="es", consent={"email": True})
        store.update_profile(cid, **extra)
    # This is the historical missing-scaffold state, after genuine enrollment;
    # no credential/proof is manufactured for a case that never existed.
    (world / "dw-no-folder-asylum").rmdir()
    for cid in ("held-gone-vawa", "dw-vawa-import"):
        (world / cid / "source").mkdir(parents=True)
        (world / cid / "source" / "passport.pdf").write_bytes(b"%PDF-1.4 made up")
    (world / "conflict-only-vawa").mkdir(exist_ok=True)
    (world / "conflict-only-vawa" / "conflict_check.json").write_text(json.dumps({  # decided "no conflict": nothing but the restriction may hold it
        "version": 1, "subject": {"name": "Rosa Held Exemplo"}, "parties": [], "searches": [{"id": "0123456789abcdef", "purpose": "sync"}],
        "decision": {"decision": "none", "words": "No conflict", "by": "Sam Attorney", "at": "2026-10-03T09:00:00-04:00"}, "history": []}), encoding="utf-8")
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    para = json.loads(call(server + "/api/overview", jane)[1])
    assert "conflict-only-vawa" not in {r["id"] for r in para["clients"]}  # in no list for her, and the record is written by the list itself
    assert (world / "conflict-only-vawa" / restricted.FILE).exists()
    (world / "conflict-only-vawa" / restricted.FILE).unlink()  # and taken away again: the routes below must close it on their own
    outbox = portal / "outbox.jsonl"
    for cid in states:
        assert not Notifier(portal / "outbox.jsonl", cases_root=world).allowed(store.profile(cid) | {"id": cid})  # nothing automatic, even before the record
        for route in srv.CASE_GET:
            made_up = call(server + route + "?client=" + quote("s-nobody-made-up") + QUERY, jane)
            got = call(server + route + "?client=" + quote(cid) + QUERY, jane)
            assert (got[0], json.loads(got[1])) == (404, srv.UNKNOWN) == (made_up[0], json.loads(made_up[1])), (route, cid)
        for route in srv.CASE_POST:
            body = {"item_id": "x", "action": "confirm", "text": "x", "id": "x", "again": True, "filing": "n400", "reviewer": "Jane Doe"}
            got = call(server + route, jane, body | {"client": cid})
            made_up = call(server + route, jane, body | {"client": "s-nobody-made-up"})
            assert (got[0], got[1]) == (made_up[0], made_up[1]) and got[0] == 404 and json.loads(got[1]) == srv.UNKNOWN, (route, cid)
        assert call(server + "/api/client-invite", jane, {"client": cid, "reviewer": "Jane Doe"})[0] == 404
        assert call(server + "/api/remind", jane, {"client": cid, "reviewer": "Jane Doe"})[0] == 404
        assert restricted.is_restricted(world / cid) and (world / cid / restricted.FILE).exists()  # the record was written the first time it was asked about
        assert call(server + "/api/answers?client=" + cid, sam)[0] == 200  # the attorney opens it
        assert json.loads(call(server + "/api/client-invite", sam, {"client": cid, "reviewer": "Sam Attorney"})[1])["delivery"]["status"] == "hand"
    assert not outbox.exists() or not any(f"{cid}@example.com" in outbox.read_text(encoding="utf-8") for cid in states)


# -- what changed on a case, and the export of the firm's data (brief H1) -------------------------------------------------------


def test_what_changed_on_a_case_is_closed_like_the_rest_and_the_firm_list_is_the_attorneys(server, world):
    """The case's fold is gated by may_open like every route that opens one case: a paralegal not named on a restricted case is told it does not exist;
    the firm-wide list is the attorney's. The ledger holds no value either way."""
    import events

    events.record("decisions", "confirmed", "Confirmed: applicant a number", case="case-rosa", who="Sam Attorney", role="attorney")
    events.record("decisions", "confirmed", "Confirmed: applicant family name", case="case-ana", who="Sam Attorney", role="attorney")
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    status, text = call(server + "/api/case_events?client=case-rosa&limit=20", jane)
    assert (status, json.loads(text)) == (404, {"error": "unknown client"})
    assert call(server + "/api/case_events.csv?client=case-rosa", jane)[0] == 404 and call(server + "/api/case_events?client=nobody-here&limit=20", jane)[0] == 404
    ordinary = json.loads(call(server + "/api/case_events?client=case-ana&limit=20", jane)[1])
    assert [r["what"] for r in ordinary["rows"]][0] == "Confirmed: applicant family name" and all(r["case"] == "case-ana" for r in ordinary["rows"])
    assert "A012345678" not in json.dumps(ordinary)
    status, text = call(server + "/api/case_events?client=case-rosa&limit=20", sam)
    assert status == 200 and any(r["what"] == "Confirmed: applicant a number" for r in json.loads(text)["rows"])
    assert call(server + "/api/case_events.csv?client=case-rosa", sam)[1].lstrip("﻿").startswith("When,Who,Role,Kind,Action,What changed,Case,Record version")
    paged = json.loads(call(server + "/api/case_events?client=case-ana&page=1", sam)[1])
    assert paged["per"] == 50 and paged["rows"] and {k["id"] for k in paged["kinds"]} >= {"decisions"}
    # the paralegal an attorney names on the case opens it, and then sees its rows
    restricted.name_person(world / "case-rosa", "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Doe")
    assert call(server + "/api/case_events?client=case-rosa&limit=20", jane)[0] == 200
    # across the firm: the attorney's, a page at a time, as a file too
    assert call(server + "/api/firm_events", jane)[0] == 403 and call(server + "/api/firm_events.csv", jane)[0] == 403
    firm = json.loads(call(server + "/api/firm_events", sam)[1])
    assert firm["per"] == 50 and firm["total"] >= 2 and any(r["case"] == "case-rosa" for r in firm["rows"])
    assert json.loads(call(server + "/api/firm_events?case=case-rosa", sam)[1])["total"] >= 1
    assert call(server + "/api/firm_events?from=soon", sam)[0] == 400 and call(server + "/api/firm_events.csv?kind=decisions", sam)[0] == 200


def _download(url: str, cookie: str) -> tuple[int, bytes]:
    req = urllib.request.Request(url, headers={"Cookie": cookie})
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def test_export_the_firms_data_is_the_attorneys_runs_the_tools_code_and_streams_the_file(server, app, world):
    import hashlib
    import time
    import zipfile
    import io

    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    assert call(server + "/api/export-firm", jane)[0] == 403 and call(server + "/api/export-firm", jane, {})[0] == 403
    assert _download(server + "/api/export-firm.zip?name=i485-firm-data-2026-10-03.zip", jane)[0] == 403
    assert json.loads(call(server + "/api/export-firm", sam)[1]) == {"export": {}, "earlier": []}
    import sys as _sys
    import events

    _sys.path.insert(0, str(_REPO / "tools"))
    import export_firm

    real, first = export_firm.everything, len([r for r in events.rows(events.base_path(world.parent)) if r["kind"] == "export" and r["action"] == "started"])

    def slow(*a, **k):  # long enough for a second press to arrive while the first export is running
        time.sleep(1.5)
        return real(*a, **k)

    export_firm.everything = slow
    try:
        started = json.loads(call(server + "/api/export-firm", sam, {})[1])
        assert started["export"]["state"] == "running"
        again_now = json.loads(call(server + "/api/export-firm", sam, {})[1])  # a second press while one runs: told how the first is going, nothing started
        assert again_now["export"]["state"] == "running" and again_now["export"]["started"] == started["export"]["started"]
        deadline = time.time() + 60
        while json.loads(call(server + "/api/export-firm", sam)[1])["export"].get("state") == "running" and time.time() < deadline:
            time.sleep(0.2)
    finally:
        export_firm.everything = real
    assert len([r for r in events.rows(events.base_path(world.parent)) if r["kind"] == "export" and r["action"] == "started"]) == first + 1, "one export, not two"
    deadline = time.time() + 60
    state = started["export"]
    while state.get("state") == "running" and time.time() < deadline:
        time.sleep(0.2)
        state = json.loads(call(server + "/api/export-firm", sam)[1])["export"]
    assert state["state"] == "done" and state["by"] == "Sam Attorney" and state["files"] > 10 and state["name"].startswith("i485-firm-data-"), state
    status, data = _download(server + "/api/export-firm.zip?name=" + state["name"], sam)
    assert status == 200 and data[:2] == b"PK" and hashlib.sha256(data).hexdigest() == state["sha256"] and len(data) == state["bytes"]
    with zipfile.ZipFile(io.BytesIO(data)) as z:
        names = z.namelist()
        assert "cases/case-rosa/fact_graph.json" in names, "a restricted case is the firm's data: it is in the export (the attorney's, who may open every case)"
        assert "data_dictionary.md" in names and "README.txt" in names and "manifest.json" in names and not any("auth.json" in n or ".key" in n for n in names)
        assert b"hash" not in z.read("firm/review_users.json") and b"salt" not in z.read("firm/review_users.json")
    again = json.loads(call(server + "/api/export-firm", sam)[1])
    assert [e["name"] for e in again["earlier"]] == [state["name"]] and again["earlier"][0]["bytes"] == len(data)
    # only a file this feature made, from its own folder
    for bad in ("../../etc/passwd", "settings.json", "i485-firm-data-x.zip", ""):
        assert _download(server + "/api/export-firm.zip?name=" + quote(bad), sam)[0] == 404, bad
    # who exported: the access log and the ledger, and the view log for the download
    log = [json.loads(line) for line in app.accounts.log_path.read_text(encoding="utf-8").splitlines()]
    assert [r["files"] for r in log if r["event"] == "data_exported"][-2:] == [0, state["files"]]
    import events

    rows = [r for r in events.rows(events.base_path(world.parent)) if r["kind"] == "export"]
    assert [r["action"] for r in rows][-2:] == ["started", "exported"] and rows[-1]["who"] == "Sam Attorney" and rows[-1]["role"] == "attorney" and rows[-1]["via"] == "staff"
    views = [json.loads(line) for line in app.views_log.read_text(encoding="utf-8").splitlines()]
    assert any(v["kind"] == "export" and v["email"] == "sam@firm.example" for v in views)
