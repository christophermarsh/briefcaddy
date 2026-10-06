"""The review app and the portal at size: a made-up firm (tools/make_world.py), the app as installed (the lists read their own copy of every case, kept up to date from the event
ledger), and every list and page timed against the budgets of docs/design_plan.md H5.

Reduced by default (300 cases, so the suite stays quick); I485_SCALE_FULL=1 runs the full 2,000 cases and 20 requests a page, as tools/measure_pages.py does. A page fails the
test only when its slowest one in twenty is more than half again over its budget, so a slow test machine does not fail the suite on noise: a page that went back to walking
the case folders (17 seconds at 2,000 cases on a fast disk) is a hundred times over. Everyone here is made up.
"""

from __future__ import annotations

import json
import os
import sys
import time
import urllib.request
from pathlib import Path
from urllib.parse import quote

import pytest

import events
import restricted

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))

import measure_pages  # noqa: E402
import scale_world  # noqa: E402
import schema_path

FULL = bool(os.environ.get("I485_SCALE_FULL"))
CASES = 2000 if FULL else 300
REQUESTS = 20 if FULL else 8
SLACK = 1.5  # a page may be this much over its budget before the test fails


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = scale_world.build(tmp_path_factory.mktemp("scale"), CASES, views=20000 if FULL else 4000, ledger=60000 if FULL else 8000, staff=12)
    yield w
    w.stop()


def test_the_made_up_firm_has_the_size_asked_for(world):
    m = world.manifest
    assert m["cases"] == CASES and len(m["case_ids"]) == CASES and 0.1 * CASES <= m["restricted"] <= 0.25 * CASES
    attorney = world.get("/api/overview?counts=1")
    assert attorney["counts"]["total"] == CASES and sum(s["count"] for s in attorney["stages"]) == CASES


# -- the budgets ------------------------------------------------------------------------------------------------------


def test_every_list_and_page_is_inside_its_budget(world):
    """The pages tools/measure_pages.py times, asked the same way, as the attorney and as the paralegal. p95 of REQUESTS requests against the budget plus half."""
    closed = set(world.manifest["restricted_ids"])
    open_ids = [c for c in world.manifest["case_ids"] if c not in closed]
    sample = open_ids[:: max(1, len(open_ids) // REQUESTS)][:REQUESTS]
    slow = []
    for spec in measure_pages.pages(sample):
        for role in ("attorney", "paralegal"):
            if spec["who"] == "attorney" and role != "attorney":
                continue
            if spec.get("token"):  # a calendar address: made as this person, asked for as a calendar program does
                made = world.call("/api/calendar", role, {"action": "make", "kind": spec["token"]})[2]
                path = (lambda k, p=made["path"]: p)
            else:
                path = spec["path"]
            send = spec.get("body") or (lambda k: None)
            seconds, status, _ = world.call(path(0), role, send(0))  # the first ask pays for what the app had not read yet (the index, the query layer)
            assert status == 200, (spec["name"], role, status)
            times = []
            for k in range(1, REQUESTS + 1):
                took, status, _ = world.call(path(k), role, send(k))
                assert status == 200, (spec["name"], role, status)
                times.append(took)
            p95 = measure_pages.percentile(times, 0.95)
            budget = measure_pages.budget_for(spec["name"])
            if p95 > budget * SLACK:
                slow.append(f"{spec['name']} as the {role}: p95 {p95:.2f} s, budget {budget} s")
    assert not slow, "over budget by more than half:\n" + "\n".join(slow)


def test_the_first_look_after_a_start_is_inside_the_budget_too(world, monkeypatch):
    """The app starts, the saved copy of every case's row is read, and the first list asks for nothing else: a night's warm-up leaves the morning fast."""
    from review.server import ReviewApp

    monkeypatch.setenv("I485_ROSTER", str(world.data / "roster.json"))  # the world's own saved copy (each test has one of its own)

    app = ReviewApp(world.clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                    world.portal, accounts=world.app.accounts)
    started = time.perf_counter()
    out = app.overview("attorney", {"role": "attorney", "email": "x"})
    assert out["counts"]["total"] == CASES and time.perf_counter() - started < measure_pages.BUDGET_LIST * SLACK * (4 if FULL else 2)
    assert app.roster.walks == 0 and app.roster.reads < 40  # nothing was read from the folders: the saved copy was adopted (a few the ledger names may have been read again)


# -- a restricted case stays hidden, at size ------------------------------------------------------------------------


def test_a_restricted_case_is_in_no_page_no_list_and_no_count_for_a_paralegal_not_named_on_it(world):
    paralegal = world.person("paralegal")
    ids = world.manifest["case_ids"]
    hidden = {c for c in ids if not restricted.visible_to(paralegal, world.clients / c)}  # the gate itself, one case folder at a time
    assert hidden and len(hidden) < len(world.manifest["restricted_ids"]) + 1  # some are closed to her; the ones she is named on are not
    named = set(world.manifest["restricted_ids"]) - hidden
    assert named, "the world names the paralegal on some restricted cases: she must see those"
    visible = set(ids) - hidden

    rows, last = world.every_row("paralegal")
    assert {r["id"] for r in rows} == visible and len(rows) == last["total"] == len(visible)
    assert sum(s["count"] for s in last["stages"]) == len(visible) and last["counts"]["total"] == len(visible)  # the counts leave them out too
    assert all(r.get("restricted") for r in rows if r["id"] in named)  # she is told which she may open are restricted
    attorney_rows, _ = world.every_row("attorney")
    assert {r["id"] for r in attorney_rows} == set(ids)
    assert hidden <= {r["id"] for r in attorney_rows if r.get("restricted")}

    # every filter and every other list, the same
    for filters in ({"stage": "review"}, {"blocked": 1}, {"idle": 1}, {"q": "exemplo"}):
        got, _ = world.every_row("paralegal", **filters)
        assert not ({r["id"] for r in got} & hidden), filters
    assert {c["id"] for c in world.get("/api/clients", "paralegal")} == visible
    text = json.dumps(world.get("/api/work?owner=paralegal&limit=2000", "paralegal")) + json.dumps(world.get("/api/expiring?days=3650", "paralegal"))
    page, items = 1, []
    while True:
        answer = world.get(f"/api/deadlines?page={page}&size=200", "paralegal")
        items += answer["items"]
        if page >= answer["pages"]:
            break
        page += 1
    text += json.dumps(items)
    assert not [c for c in hidden if f'"{c}"' in text]
    reports = world.get("/api/reports", "paralegal")
    assert reports["cases"] == len(visible) and len(next(t for t in reports["tables"] if t["id"] == "cases")["rows"]) == len(visible)
    found = world.get("/api/search?q=passport&limit=200", "paralegal")
    assert {r["case"] for r in found["results"]} <= visible
    for case in sorted(hidden)[:3]:  # a hidden case is no case: the same 404 as a name that is nothing
        assert world.call(f"/api/items?client={case}", "paralegal")[1:] == world.call("/api/items?client=nobody-here-0000", "paralegal")[1:]
        assert world.call(f"/api/jobs?client={case}", "paralegal")[1:] == world.call("/api/jobs?client=nobody-here-0000", "paralegal")[1:]


# -- paging ----------------------------------------------------------------------------------------------------------


def test_all_clients_is_a_page_of_fifty_with_the_count_and_the_filters_are_the_servers(world):
    first = world.get("/api/overview")
    assert len(first["clients"]) == 50 and first["page"] == 1 and first["pages"] == -(-CASES // 50) and first["total"] == CASES and first["size"] == 50
    second = world.get("/api/overview?page=2")
    assert {r["id"] for r in first["clients"]}.isdisjoint(r["id"] for r in second["clients"])
    last = world.get("/api/overview?page=9999")
    assert last["page"] == last["pages"]  # past the end is the last page, not an error
    assert len(world.get("/api/overview?size=500")["clients"]) == 200  # at most 200 a page
    by_stage = {s["id"]: s["count"] for s in first["stages"]}
    for stage, n in by_stage.items():
        if n:
            got = world.get(f"/api/overview?stage={stage}")
            assert got["total"] == n and all(r["stage"] == stage for r in got["clients"])
    blocked = world.get("/api/overview?blocked=1")
    assert blocked["total"] == blocked["counts"]["blocking"] and all(r["blocking"] for r in blocked["clients"])
    assert blocked["clients"] == sorted(blocked["clients"], key=lambda r: -(r["blocking"] or 0))[: len(blocked["clients"])]  # most blocking first
    needle = first["clients"][0]["id"].split("-")[0]
    named = world.get(f"/api/overview?q={needle}")
    assert named["total"] and all(needle in (r["id"] + " " + (r.get("summary") or {}).get("name", "")).lower() for r in named["clients"])
    assert all("due" in r and isinstance(r["due"], list) for r in first["clients"])  # each row carries its own deadlines
    assert not any(k.startswith("_") for r in first["clients"] for k in r)  # nothing of the app's own bookkeeping goes to the screen
    bad = world.call("/api/overview?page=two")
    assert bad[1] == 400 and "number" in bad[2]["error"]


def test_my_approvals_is_a_page_of_fifty_inside_the_lists_budget_and_names_no_restricted_case(world):
    """The attorney's queue (src/approvals.py) at size: a page of fifty, oldest first in each kind, built from the roster (no case folder is read), inside the budget a list has
    (docs/scale.md), and without one item of a restricted case the attorney is not named on (in this world no attorney is named on any: the stricter rule)."""
    closed = set(world.manifest["restricted_ids"])
    seconds, status, first = world.call("/api/approvals?page=1", "attorney")
    assert status == 200 and len(first["rows"]) == 50 and first["size"] == 50 and first["pages"] == -(-first["total"] // 50) and first["page"] == 1
    mine = [c for c in world.manifest["case_ids"][::6] if c not in closed]  # a sixth of the cases have a card for the attorney (tools/make_world.py)
    cards = next(k["count"] for k in first["kinds"] if k["id"] == "card")
    assert cards >= len(mine) and first["total"] >= cards  # (the alerts, and the cards some other rule leaves for the attorney)
    rows = list(first["rows"])
    for page in range(2, first["pages"] + 1):
        rows += world.call(f"/api/approvals?page={page}", "attorney")[2]["rows"]
    assert len(rows) == first["total"] and not ({r["case"] for r in rows} & closed)
    assert not any(c in json.dumps(first) for c in list(closed)[:20])
    times = [world.call("/api/approvals?page=1", "attorney")[0] for _ in range(REQUESTS)]
    print(f"My approvals: {first['total']} items in {first['pages']} pages, first ask {seconds:.2f} s, p50 {measure_pages.percentile(times, 0.5):.3f} s, p95 {measure_pages.percentile(times, 0.95):.3f} s")
    assert measure_pages.percentile(times, 0.95) <= measure_pages.BUDGET_LIST, f"My approvals p95 {measure_pages.percentile(times, 0.95):.2f} s"
    assert world.call("/api/approvals", "paralegal")[1] == 403
    seconds, status, work = world.call("/api/work", "attorney")
    assert status == 200 and work["approvals"]["total"] == first["total"] and seconds <= measure_pages.BUDGET_LIST * SLACK


def test_today_is_a_page_of_fifty_inside_the_lists_budget_and_names_no_restricted_case(world):
    """Today (src/day_plan.py) at size: a page of fifty, from the roster's entries (no case folder is read), inside the budget a list has (docs/scale.md), in the groups the
    server draws, every holder in the world, and without one row, count or line of a restricted case the reader is not named on."""
    closed = set(world.manifest["restricted_ids"])
    for role in ("paralegal", "attorney"):
        seconds, status, first = world.call("/api/today?page=1", role)
        assert status == 200 and first["size"] == 50 and first["page"] == 1 and first["pages"] == -(-first["total"] // 50) and len(first["rows"]) == min(50, first["total"])
        rows = list(first["rows"])
        for page in range(2, first["pages"] + 1):
            rows += world.call(f"/api/today?page={page}", role)[2]["rows"]
        assert len(rows) == first["total"] == sum(g["count"] for g in first["groups"]) and len({r["case"] for r in rows}) == len(rows)
        seen = {r["case"] for r in rows}
        if role == "attorney":  # in this world no attorney is named on a restricted case: the stricter rule leaves every one out
            assert not (seen & closed)
        else:  # the paralegal is named on a fifth of the marked cases and a few the law closes: those and no other
            assert seen & closed and seen & closed <= {c for c in closed if c in set(world.manifest["case_ids"])}
            assert len(seen & closed) < len(closed)
        assert [r["group"] for r in rows] == sorted((r["group"] for r in rows), key=("ready", "work", "client").index)  # the groups in their order
        held = {h for r in rows for h, n in r["counts"].items() if n}
        assert held == {"client", "office", "attorney"}, held  # each holder is somewhere in the world (the three lines of a case say the office's first: the counts say all)
        reach = sum(1 for r in rows if not r["counts"]["client"] and not r["counts"]["attorney"])
        assert first["can_reach"] == reach > 0 and first["line"] == f"{reach} case{'s' if reach != 1 else ''} can reach a signed packet today"
        assert all(len(r["lines"]) <= 3 and r["steps"] == sum(r["counts"].values()) for r in rows)
        # A boundary attention line deliberately names the source to review. The
        # list still carries only its compact DTO, never a graph, document record,
        # extracted full text, evidence payload or folder path.
        for r in rows:
            assert set(r) == {"case", "name", "group", "steps", "counts", "filing", "deadline", "lines", "client"}
            assert set(r["counts"]) == {"client", "office", "attorney"}
            assert all(set(line) == {"holder", "text", "mine"} and isinstance(line["text"], str) for line in r["lines"])
            assert len(r["client"]) <= 10 and all(isinstance(text, str) for text in r["client"])
            assert r["deadline"] is None or set(r["deadline"]) == {"date", "date_text", "what", "days_left", "level"}
            # Permit filenames only in the deliberate attention/ask-client text.
            without_attention = r | {"lines": [line | {"text": ""} for line in r["lines"]], "client": []}
            assert ".pdf" not in json.dumps(without_attention)
        assert not any(c in json.dumps(first) for c in list(closed - seen)[:20])
        times = [world.call("/api/today?page=1", role)[0] for _ in range(REQUESTS)]
        print(f"Today as the {role}: {first['total']} cases in {first['pages']} pages, first ask {seconds:.2f} s, p50 {measure_pages.percentile(times, 0.5):.3f} s, p95 {measure_pages.percentile(times, 0.95):.3f} s")
        assert measure_pages.percentile(times, 0.95) <= measure_pages.BUDGET_LIST, f"Today p95 {measure_pages.percentile(times, 0.95):.2f} s"
        status, work = world.call("/api/work", role)[1:]
        assert status == 200 and work["today"]["can_reach"] == first["can_reach"]


# -- the lists are the roster's, and it follows the ledger ----------------------------------------------------------------


def test_a_list_does_not_read_the_case_folders_and_a_change_the_ledger_names_is_read_once(world, monkeypatch):
    from review import overview as ov

    for _ in range(2):  # what earlier tests changed is read again first
        world.get("/api/overview?counts=1")
        world.get("/api/overview?counts=1", "paralegal")
        time.sleep(0.6)
    world.app.roster.wait()
    calls = []
    real = ov.case_row
    monkeypatch.setattr(ov, "case_row", lambda d, *a, **k: (calls.append(d.name), real(d, *a, **k))[1])
    for path in ("/api/overview", "/api/overview?page=3&stage=review", "/api/work", "/api/deadlines", "/api/reports", "/api/expiring", "/api/clients"):
        for role in ("attorney", "paralegal"):
            world.get(path, role)
    assert calls == [], "a list read case folders again although nothing changed"
    # another process (the worker, the overnight run, the portal) changed a case and wrote its row in the ledger
    case = next(c for c in world.manifest["case_ids"] if c not in set(world.manifest["restricted_ids"]))
    status_path = world.clients / case / "status.json"
    status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
    status["filed_at"], status["filed_by"] = "2026-10-02T10:00:00-04:00", "Someone Else"
    status_path.write_text(json.dumps(status), encoding="utf-8")
    events.record("filings", "marked_filed", "Marked the case as filed", case=case, home=world.data, who="Someone Else", role="staff", via="tool")
    time.sleep(0.6)  # the lists look at the ledger at most twice a second
    row = next(r for r in world.get(f"/api/overview?q={case}&size=200")["clients"] if r["id"] == case)
    assert row["stage"] == "filed" and calls == [case]


def test_the_review_apps_own_write_shows_on_the_lists_at_once(world):
    import document_instances
    import documents
    import subject_attribution
    from inbox import reprocess_documents
    from synthetic_documents import retain_documents

    # Only one case gets real retained evidence. Choose a bounded fixture case
    # without an existing unproven height edge, so that unrelated legacy holds
    # cannot invalidate the legitimate new height review.
    closed = set(world.manifest["restricted_ids"])
    candidates = [c for c in world.manifest["case_ids"] if c not in closed][:20]
    key, value = "applicant.height", "5'5\""
    case = next(c for c in candidates if key not in json.loads((world.clients / c / "fact_graph_raw.json").read_text(encoding="utf-8"))["facts"])
    case_dir, source = world.clients / case, world.clients / case / "source"
    held = {p["file"] for p in document_instances.views(case_dir) if p["held"]}
    assert held, "the synthetic missing sources must remain held, not be treated as confirmed evidence"
    docs = [("cache-license-a.pdf", "DRIVER LICENSE\nHeight 5-05"), ("cache-license-b.pdf", "DRIVER LICENSE\nHeight 5-07")]
    retain_documents(source, docs)
    # Read actual retained PDF pages through the existing incremental path;
    # no replacement reader, injected card or manually manufactured fact.
    reprocess_documents(case_dir, source, docs, {name: {"pages": 1} for name, _ in docs}, source="folder", who="Synthetic Source Reviewer")
    identity = next(p["id"] for p in documents.read(case_dir)["case_subjects"]["people"] if p["case_role"] == "applicant")
    named = {name for name, _ in docs}
    evidence = [r for r in subject_attribution.views(case_dir) if r["file"] in named]
    assert len(evidence) == 2 and all(r["bound"] and r["slots"] == ["holder"] for r in evidence)
    for r in evidence:
        subject_attribution.assign(case_dir, r["instance_id"], r["fingerprint"], {"holder": identity}, "Synthetic Subject Reviewer", "attorney")
    world.app.roster.touch(case)  # establish the current fixture row before the write being measured
    row = lambda: next(r for r in world.get(f"/api/overview?q={case}")["clients"] if r["id"] == case)  # noqa: E731
    before = row()
    items = world.get(f"/api/items?client={case}")
    item = next(i for i in items["open"] if i["id"] == f"fact:{key}")
    assert "set" in item["actions"] and {s["value"] for f in item["facts"] if f["key"] == key for s in f["sources"]} == {value, "5'7\""}
    assert item["kind"] not in ("document_boundary", "document_subject")
    body = {"client": case, "item_id": item["id"], "action": "set", "values": {key: value}}
    decision_path = case_dir / "decisions.json"
    prior_decisions = decision_path.read_bytes() if decision_path.exists() else None
    refused, reason = world.call("/api/decide", "attorney", body)[1:]
    assert refused == 400 and "source review" in reason["error"]
    assert (decision_path.read_bytes() if decision_path.exists() else None) == prior_decisions
    # The same signed-in actor opens both retained originals before submitting
    # the current item fingerprints, as the protected source-review flow does.
    for name in sorted(named):
        request = urllib.request.Request(f"{world.base}/api/file?client={quote(case)}&doc={quote(name)}",
                                         headers={"Cookie": world.cookies["attorney"]})
        with urllib.request.urlopen(request, timeout=600) as response:
            assert response.status == 200 and response.read() == (source / name).read_bytes()
    status, answer = world.call("/api/decide", "attorney", body | {"evidence_fingerprints": item["evidence_fingerprints"]})[1:]
    assert status == 200 and answer["recorded"] == 1, answer
    after = row()  # no waiting for the ledger: the app told the lists itself
    assert after["decided"] == before["decided"] + 1 and after["last_decision"] != before["last_decision"]
    saved = json.loads((world.clients / case / "decisions.json").read_text(encoding="utf-8"))[item["id"]]
    assert saved["action"] == "set" and saved["values"] == {key: value}
    assert saved["reviewer"] == world.person("attorney")["name"] and saved["role"] == "attorney"
    assert saved["evidence_confirmation"]["keys"][key]["fingerprint"] == item["evidence_fingerprints"][key]
    assert held <= {p["file"] for p in document_instances.views(case_dir) if p["held"]}


def test_a_case_an_attorney_restricts_leaves_the_paralegals_lists_at_the_next_ask(world):
    paralegal_rows, _ = world.every_row("paralegal")
    case = next(r["id"] for r in paralegal_rows if not r.get("restricted"))
    total = world.get("/api/overview?counts=1", "paralegal")["counts"]["total"]
    status, answer = world.call("/api/access", "attorney", {"client": case, "action": "mark", "reason": "The client is a minor."})[1:]
    assert status == 200, answer
    assert world.get("/api/overview?counts=1", "paralegal")["counts"]["total"] == total - 1
    rows, _ = world.every_row("paralegal")
    assert case not in {r["id"] for r in rows} and case not in {c["id"] for c in world.get("/api/clients", "paralegal")}
    assert world.call(f"/api/items?client={case}", "paralegal")[1] == 404
    assert world.get("/api/overview?counts=1", "attorney")["counts"]["total"] == CASES  # the attorney still sees it
    # and lifting the mark gives it back
    status, answer = world.call("/api/access", "attorney", {"client": case, "action": "unmark", "reason": ""})[1:]
    assert status == 200, answer
    assert world.get("/api/overview?counts=1", "paralegal")["counts"]["total"] == total


def test_a_change_nobody_recorded_is_caught_by_the_background_walk(tmp_path, monkeypatch):
    """A file copied in by hand has no ledger row; a walk over every case, in the background and not on the request, finds it."""
    w = scale_world.build(tmp_path, 40, warm=False)
    try:
        monkeypatch.setenv("I485_WALK_EVERY", "1")
        monkeypatch.setenv("I485_ROSTER_GAP", "0")  # (the lists look at the clock and the ledger at most twice a second: a walk that is due is noticed on the next ask)
        w.get("/api/overview?counts=1")  # Implementation note.
        w.app.roster.wait()
        case = w.manifest["case_ids"][3]
        status_path = w.clients / case / "status.json"
        status = json.loads(status_path.read_text(encoding="utf-8")) if status_path.exists() else {}
        status["filed_at"], status["filed_by"] = "2026-10-02T10:00:00-04:00", "Someone Else"
        status_path.write_text(json.dumps(status), encoding="utf-8")
        w.app.roster.last_walk = 0  # a walk is due
        w.get("/api/overview?counts=1")  # asking starts it; the answer does not wait for it
        w.app.roster.wait()
        row = next(r for r in w.get(f"/api/overview?q={case}")["clients"] if r["id"] == case)
        assert row["stage"] == "filed"
    finally:
        w.stop()


def test_the_overnight_run_leaves_the_lists_ready_for_the_morning(tmp_path):
    """overnight.journeys reads every case's row in a pool and saves them; an app that starts after it reads nothing but that file."""
    import overnight
    from review import roster

    w = scale_world.build(tmp_path, 60, warm=False)
    try:
        saved = Path(w.manifest["data"]) / "roster.json"
        assert not saved.exists()
        said = overnight.journeys(w.clients, w.portal, workers=4)
        assert saved.exists() and "Case timelines:" in said
        data = json.loads(saved.read_text(encoding="utf-8"))
        assert len(data["entries"]) >= 60 and data["positions"]
        r = roster.Roster(w.clients, {}, "", None, w.portal)
        r.sync()  # as an app starting up: the saved copy, and no walk over the folders
        assert r.walks == 0 and r.reads == 0 and len(r.entries) == len(data["entries"])
    finally:
        w.stop()


def test_a_file_the_night_has_just_built_is_not_walked_over_again_at_the_first_look(tmp_path, monkeypatch):
    import keepup

    monkeypatch.setenv("I485_WALK_EVERY", "600")
    walked, some = [], []
    young = keepup.Follower(lambda: tmp_path, lambda: walked.append(1), lambda cases: some.append(cases), age=lambda: 120.0)
    young.poke()
    young.wait()
    assert walked == [] and young.walks == 0 and young.tail is not None  # followed from now on; the walk is due 480 seconds from now
    old = keepup.Follower(lambda: tmp_path, lambda: walked.append(1), lambda cases: some.append(cases), age=lambda: 7200.0)
    old.poke()
    old.wait()
    assert walked == [1] and old.walks == 1  # a file from last night, or none at all: walked over once
    none = keepup.Follower(lambda: tmp_path, lambda: walked.append(1), lambda cases: some.append(cases), age=lambda: None)
    none.poke()
    none.wait()
    assert walked == [1, 1]
    # a young file, but the ledger has a row written after it was built: that change may not be in the file, so it is walked over
    (tmp_path / "events-2026-10.jsonl").write_text('{"case": "x"}\n', encoding="utf-8")
    loud = keepup.Follower(lambda: tmp_path / "events.jsonl", lambda: walked.append(1), lambda cases: some.append(cases), age=lambda: 120.0)
    loud.poke()
    loud.wait()
    assert walked == [1, 1, 1] and loud.walks == 1
    (tmp_path / "events-2026-10.jsonl").write_text('{"case": "x"}\n', encoding="utf-8")
    os.utime(tmp_path / "events-2026-10.jsonl", (time.time() - 3600, time.time() - 3600))  # a ledger quiet for an hour: older than the file built two minutes ago
    quiet = keepup.Follower(lambda: tmp_path / "events.jsonl", lambda: walked.append(1), lambda cases: some.append(cases), age=lambda: 120.0)
    quiet.poke()
    quiet.wait()
    assert walked == [1, 1, 1] and quiet.walks == 0


def _prospect(w, case: str, decision: str = "undecided") -> None:
    """A client the conflict check holds: a folder with the check and nothing else, and a profile in the portal."""
    (w.clients / case).mkdir()
    (w.clients / case / "conflict_check.json").write_text(json.dumps({"decision": {"decision": decision, "by": "Ana Attorneyexemplo", "at": "2026-10-02T10:00:00+00:00"}}), encoding="utf-8")
    (w.portal / "clients" / case).mkdir(parents=True)
    (w.portal / "clients" / case / "profile.json").write_text(json.dumps({"id": case, "name": "Waiting Exemplo", "language": "pt", "status": "started"}), encoding="utf-8")


def test_the_lists_know_an_ended_case_a_held_conflict_check_and_a_client_whose_kind_could_not_be_recorded(tmp_path):
    """The cases the other waves added (ended, a conflict check waiting for an attorney, a protected kind with no record and none that can be written) are in the lists' own copy
    as the folders say: the same rows, the same hiding, the way an app reading every folder would show them."""
    w = scale_world.build(tmp_path, 40)
    try:
        roster = w.app.roster
        ended = w.manifest["case_ids"][5]
        (w.clients / ended / "engagement.json").write_text(json.dumps({"end": {"state": "closed", "on": "2026-09-01"}}), encoding="utf-8")
        roster.touch(ended)
        first = w.get("/api/overview?counts=1")
        assert first["ended"] == 1 and first["counts"]["total"] == 40
        assert ended not in {r["id"] for r in w.every_row("attorney")[0]}  # off the open list...
        listed = w.get("/api/overview?ended=ended")["clients"]
        assert [r["id"] for r in listed] == [ended] and listed[0]["end"]["state"] == "closed"  # ...and on the ended one
        assert ended not in json.dumps(w.get("/api/month?scope=firm"))  # nor on the calendar
        _prospect(w, "waiting-exemplo")
        roster.touch("waiting-exemplo")
        row = next(r for r in w.get("/api/overview?q=waiting-exemplo")["clients"] if r["id"] == "waiting-exemplo")
        assert row.get("conflict_held")  # its row says why no invitation goes out
        roster.hold = lambda case: False  # a protected kind that has no record and could not be given one: closed to everyone but an attorney (fail closed)
        _prospect(w, "unwritten-exemplo")
        roster.touch("unwritten-exemplo")
        assert "unwritten-exemplo" in {r["id"] for r in w.every_row("attorney")[0]}
        assert "unwritten-exemplo" not in {r["id"] for r in w.every_row("paralegal")[0]}
    finally:
        w.stop()



def test_a_list_asked_while_a_changed_case_is_being_read_again_waits_for_it(tmp_path, monkeypatch):
    """Two requests 30 ms apart: the first takes the changed case off the list and reads it again outside the lock; the second must not answer
    without it (flake pass 2: reproduced 12 of 12 before the fix; the engagement, ask-case and EOIR-26A browser tests opened All clients
    without the case a fixture had just written)."""
    import threading

    from review import roster

    monkeypatch.setenv("I485_WALK_EVERY", "600")
    monkeypatch.setenv("I485_ROSTER_GAP", "0")
    w = scale_world.build(tmp_path, 8, warm=False)
    try:
        r = roster.Roster(w.clients, {}, "", None, w.portal)
        r.sync()
        r.wait()
        case = next(c for c in w.manifest["case_ids"] if c in r.entries)
        real = r.build

        def slow(name):
            time.sleep(0.3)  # a case folder on a slow disk
            return real(name)

        monkeypatch.setattr(r, "build", slow)
        with r.lock:
            r.entries.pop(case)  # the app's own write: the case must be read again before the next list
            r.dirty.add(case)
        seen = {}
        first = threading.Thread(target=r.sync)
        first.start()
        time.sleep(0.03)
        r.sync()  # the second request: it arrives while the first is still reading the case
        with r.lock:
            seen["listed"] = case in r.entries
        first.join()
        assert seen["listed"], "the second list was answered without the case the first request was still reading"
    finally:
        w.stop()
