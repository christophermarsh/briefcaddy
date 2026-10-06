"""What could this person apply for (src/apply_for.py, schemas/registers/apply_for.json): for each relief the product handles, the questions an attorney would ask, each from a source the
product already holds; answered yes, no or unsure by a person, the screen recording who answered what and when; and never a conclusion. Everyone here is made up."""

from __future__ import annotations

import json
import re
import sys
import threading
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import pytest

import apply_for
import clock
import events
import restricted
import settings
import schema_path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(Path(__file__).resolve().parent))

# the words a screen that concludes would use: none may be on this one (and none in the questions, their sources or their readings)
CONCLUSIONS = re.compile(r"eligib|qualif|likely|score|rank|best fit|recommend|should apply|will be approved|strong case|weak case", re.I)
RELIEFS = {"sij", "family", "naturalization", "asylum", "caa", "vawa", "t_visa", "u_visa", "daca", "tps", "parole", "waiver_i601a", "waiver_i601", "waiver_i212"}


@pytest.fixture
def firm(tmp_path, monkeypatch):
    import people_world

    data = tmp_path / "data"
    clients = data / "clients"
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    for var, path in (("I485_QUERY_DB", data / "query.db"), ("I485_EVENTS", data / "events.jsonl"), ("I485_CONFLICTS", data / "conflict_checks.jsonl"), ("I485_CASES", clients),
                      ("PORTAL_DATA", data / "portal"), ("I485_INDEX", data / "index.db"), ("I485_INBOX", data / "inbox")):
        monkeypatch.setenv(var, str(path))
    monkeypatch.delenv("I485_PROSPECTS", raising=False)
    people_world.make(clients)
    (data / "portal").mkdir(parents=True)
    return {"data": data, "clients": clients, "portal": data / "portal"}


@pytest.fixture
def server(firm):
    from review.auth import Accounts
    from review.server import COOKIE, ReviewApp, make_handler, serve

    accounts = Accounts(firm["data"] / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Paralegal", "paralegal"), ("kim@firm.example", "Kim Paralegal", "paralegal"),
                              ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.add(email, name, role)
    restricted.name_person(firm["clients"] / "case-rosa", "kim@firm.example", True, "Sam Attorney", "attorney", "Kim Paralegal")
    app = ReviewApp(firm["clients"], schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=firm["portal"], accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    cookie = lambda email: f"{COOKIE}={accounts.session_for(email, how='test')[0]}"  # noqa: E731
    yield {"base": f"http://127.0.0.1:{port}", "jane": cookie("jane@firm.example"), "kim": cookie("kim@firm.example"), "sam": cookie("sam@firm.example"), "app": app}
    httpd.shutdown()


def call(srv, who, path, body=None) -> tuple[int, bytes]:
    headers = {"X-Review-App": "1", "Cookie": srv[who]} | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(srv["base"] + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read()
    except urllib.error.HTTPError as e:
        return e.code, e.read()


def ok(srv, who, path, body=None) -> dict:
    status, raw = call(srv, who, path, body)
    assert status == 200, (path, status, raw[:300])
    return json.loads(raw)


# -- the questions: every one has a source the product holds ---------------------------------------------------------------------------------


def test_every_relief_the_product_handles_is_there_and_every_question_rests_on_a_source_the_product_holds():
    catalog = apply_for.catalog()
    assert {r["id"] for r in catalog["reliefs"]} == RELIEFS
    ids = [x["id"] for r in catalog["reliefs"] for x in r["questions"]]
    assert len(ids) == len(set(ids)) >= 90
    for r in catalog["reliefs"]:
        assert r["name"] and r["about"] and len(r["questions"]) >= 4, r["id"]
        for x in r["questions"]:
            assert x["id"].split(".")[0] and x["text"].endswith(("?", ".")) and x["cite"] and x["read"], x["id"]
            # the product holds the source: the file exists and has the words the question rests on
            held = REPO / x["held_in"]
            assert held.is_file(), (x["id"], x["held_in"])
            assert x["held_text"] in held.read_text(encoding="utf-8"), (x["id"], x["held_in"], x["held_text"])
            # a question names its reading: a date (MM/DD/YYYY) the product read it, or says plainly that none is recorded or that the product holds none
            assert re.search(r"\d{2}/\d{2}/\d{4}", x["read"]) or re.search(r"no reading date|holds no cut-off|no date", x["read"], re.I), (x["id"], x["read"])
            assert x["cite"] != x["read"]
            shown = " ".join([x["text"], x["cite"], x["read"]])
            assert "—" not in shown and " -- " not in shown and not CONCLUSIONS.search(shown), (x["id"], CONCLUSIONS.findall(shown))
            assert not re.search(r"\d{4}-\d{2}-\d{2}", shown), (x["id"], "dates are written MM/DD/YYYY")
    # the catalog says it is the attorney's to approve, and the register item names it
    register = json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"]
    item = next(i for i in register if i["id"] == "apply_for_questions")
    assert schema_path.rel("register", "apply_for") in item["where"][0] and item["party"] == "firm" and item["firm_steps"] and item["owner"] == "attorney"
    assert (schema_path.path("register", "apply_for")).read_text(encoding="utf-8") == json.dumps(catalog, indent=2, ensure_ascii=False) + "\n"


def test_the_screen_names_each_source_in_words_never_a_file_and_says_no_more_than_its_source(tmp_path):
    catalog = apply_for.catalog()
    files = re.compile(r"schemas/|docs/|src/|\.json|\.md|\.py\b")
    d = tmp_path / "case"
    d.mkdir()
    view = apply_for.view(d)
    assert set(catalog["held_names"]) >= {x["held_in"] for r in catalog["reliefs"] for x in r["questions"]}
    for r in view["reliefs"]:
        for x in r["questions"]:
            shown = " ".join([r["name"], r["about"], x["text"], x["cite"], x["read"]])
            assert not files.search(shown), (x["id"], files.findall(shown))
            assert "; held in the product's " in x["read"], x["id"]
    assert not files.search(json.dumps(view))
    page = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    block = page[page.index("// What could this person apply for: the questions an attorney would ask"):page.index("async function renderNotesTab")]
    assert not files.search(re.sub(r"//[^\n]*", "", block))  # the words the page draws, not its comments
    # a question says no more than the words it rests on: the removal-proceedings question keeps the exception the product's own step states
    court = next(x for r in catalog["reliefs"] for x in r["questions"] if x["id"] == "sij.court_case")
    assert "arriving alien excepted" in court["held_text"] and "arriving alien" in court["text"] and court["held_text"] in (REPO / court["held_in"]).read_text(encoding="utf-8")
    byid = {x["id"]: x for r in catalog["reliefs"] for x in r["questions"]}
    assert byid["asylum.court"]["held_in"] == "src/asylum.py" and byid["family.bars"]["held_text"] == "Last entered as a nonimmigrant crewman"
    assert "Permanent resident since" in byid["nat.resident"]["held_text"]


def test_the_reliefs_are_listed_by_name_the_same_for_everyone_and_the_attorney_review_names_each(tmp_path):
    names = [r["name"] for r in apply_for.catalog()["reliefs"]]
    assert names == sorted(names, key=str.lower)  # alphabetical: no order that reflects fit
    empty, answered = tmp_path / "a", tmp_path / "b"
    empty.mkdir()
    answered.mkdir()
    for qid in ("sij.under21", "vawa.abuse", "u.crime"):
        apply_for.answer(answered, qid, "yes", "Jane Paralegal", "paralegal")
    assert [r["id"] for r in apply_for.view(empty)["reliefs"]] == [r["id"] for r in apply_for.view(answered)["reliefs"]]
    review = (REPO / "docs" / "attorney_review.md").read_text(encoding="utf-8")
    assert "What could this person apply for" in review
    for r in apply_for.catalog()["reliefs"]:
        assert r["name"] in review, r["id"]


# -- answers ------------------------------------------------------------------------------------------------------------------------------


def test_yes_no_and_unsure_are_recorded_with_who_and_when_and_a_change_is_kept(tmp_path, monkeypatch):
    d = tmp_path / "case"
    d.mkdir()
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    got = apply_for.answer(d, "sij.under21", "YES", "Jane Paralegal", "paralegal")
    assert got["answer"] == "yes" and got["by"] == "Jane Paralegal" and got["role"] == "paralegal" and got["at"].startswith("2026-10-05T10:30")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 6, 9, 0))
    apply_for.answer(d, "sij.under21", "unsure", "Sam Attorney", "attorney")
    apply_for.answer(d, "sij.unmarried", "no", "Sam Attorney", "attorney")
    view = apply_for.view(d)
    sij = next(r for r in view["reliefs"] if r["id"] == "sij")
    under21 = next(x for x in sij["questions"] if x["id"] == "sij.under21")
    assert (under21["answer"], under21["by"], under21["role"], under21["changes"]) == ("unsure", "Sam Attorney", "attorney", 1) and under21["at"].startswith("2026-10-06")
    assert [h["answer"] for h in apply_for.history(d, "sij.under21")] == ["unsure", "yes"] and apply_for.history(d, "sij.under21")[1]["by"] == "Jane Paralegal"
    assert sij["answered"] == 2 and view["answered"] == 2 and view["total"] == sum(len(r["questions"]) for r in view["reliefs"])
    # said again: nothing new is recorded; taken back: a new entry, the earlier ones stay
    apply_for.answer(d, "sij.unmarried", "no", "Sam Attorney", "attorney")
    assert len(json.loads((d / apply_for.FILE).read_text(encoding="utf-8"))["answers"]["sij.unmarried"]["history"]) == 1
    apply_for.answer(d, "sij.unmarried", "", "Jane Paralegal", "paralegal")
    entry = json.loads((d / apply_for.FILE).read_text(encoding="utf-8"))["answers"]["sij.unmarried"]
    assert entry["answer"] is None and [h["answer"] for h in entry["history"]] == ["no", None] and entry["by"] == "Jane Paralegal"
    # refused: no name, no such question, not one of the three
    for args, error, words in ((("sij.under21", "yes", ""), ValueError, "Enter your name"), (("nonsense", "yes", "Jane"), LookupError, "No such question"),
                               (("sij.under21", "probably", "Jane"), ValueError, "yes, no or unsure")):
        with pytest.raises(error, match=words):
            apply_for.answer(d, *args)


def test_the_screen_never_concludes_it_says_the_attorney_decides_and_shows_who_answered(server, firm):
    ok(server, "jane", "/api/apply-for", {"client": "case-ana", "question": "sij.court", "answer": "yes"})
    view = ok(server, "sam", "/api/apply-for?client=case-ana")
    assert view["decides"] == "The attorney decides." and view["answered"] == 1 and view["intro"].endswith("Nothing here says what a person can apply for.")
    sij = next(r for r in view["reliefs"] if r["id"] == "sij")
    court = next(x for x in sij["questions"] if x["id"] == "sij.court")
    assert court["answer"] == "yes" and court["by"] == "Jane Paralegal" and court["role"] == "paralegal" and court["cite"] and court["read"]
    # the whole payload the screen is drawn from, and the words the page itself adds around it, hold none of the words a conclusion would use
    payload = json.dumps(view, ensure_ascii=False)
    assert not CONCLUSIONS.search(payload), CONCLUSIONS.findall(payload)
    page = (REPO / "src" / "review" / "static" / "index.html").read_text(encoding="utf-8")
    block = page[page.index("// What could this person apply for: the questions an attorney would ask"):page.index("async function renderNotesTab")]
    block += page[page.index('["applyfor", "Could apply for"'):].split("\n", 1)[0]
    block += page[page.index('el("h3", {}, "What could this person apply for")'):].split("\n", 1)[0]
    assert not CONCLUSIONS.search(block), CONCLUSIONS.findall(block)
    assert "—" not in block and " -- " not in block
    # the ledger: who and when, never the question or the answer
    rows = [r for r in events.rows(firm["data"] / "events.jsonl") if r["kind"] == "notes"]
    assert [(r["action"], r["who"], r["case"], r["what"]) for r in rows] == [("applied_for_answered", "Jane Paralegal", "case-ana", "Answered a question on what the person could apply for")]


def test_a_restricted_cases_answers_are_closed_by_the_same_gate(server):
    ok(server, "sam", "/api/apply-for", {"client": "case-rosa", "question": "vawa.abuse", "answer": "yes"})
    nothing = call(server, "jane", "/api/apply-for?client=nobody-here")
    assert nothing[0] == 404 and call(server, "jane", "/api/apply-for?client=case-rosa") == nothing
    a = call(server, "jane", "/api/apply-for", {"client": "case-rosa", "question": "vawa.abuse", "answer": "no"})
    b = call(server, "jane", "/api/apply-for", {"client": "nobody-here", "question": "vawa.abuse", "answer": "no"})
    assert a == b and a[0] == 404
    assert next(x for r in ok(server, "kim", "/api/apply-for?client=case-rosa")["reliefs"] for x in r["questions"] if x["id"] == "vawa.abuse")["answer"] == "yes"  # Kim is named on it


def test_on_a_prospect_the_answers_are_kept_and_go_onto_the_case_when_it_becomes_a_client(server, firm):
    pid = ok(server, "jane", "/api/prospect-new", {"name": "Lia Exemplo Prospecto", "phone": "(555) 010-4444", "language": "pt"})["id"]
    page = ok(server, "jane", "/api/prospect-change", {"prospect": pid, "action": "apply", "question": "u.crime", "answer": "unsure"})
    assert page["apply_for"]["answered"] == 1 and page["apply_for"]["decides"] == "The attorney decides."
    row = [r for r in events.rows(firm["data"] / "events.jsonl") if r["kind"] == "notes"][0]
    assert row["case"] == f"prospect:{pid}" and row["action"] == "applied_for_answered"
    case = firm["clients"] / "case-bia"
    assert apply_for.carry(firm["data"] / "prospects" / pid, case) == 1 and apply_for.carry(firm["data"] / "prospects" / pid, case) == 0
    assert json.loads((case / apply_for.FILE).read_text(encoding="utf-8"))["answers"]["u.crime"]["by"] == "Jane Paralegal"
