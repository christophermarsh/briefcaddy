"""tools/make_world.py: a made-up firm at size, written as the records the product keeps, never by reading a document. Deterministic from a seed, every kind of case, the restricted
ones among them, a ledger and a view log in the shapes the product writes, and nobody real in it."""

from __future__ import annotations

import hashlib
import json
import re
import sys
import time
from pathlib import Path

import pytest

import events
import restricted

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import make_world  # noqa: E402

SMALL = dict(cases=60, views=500, ledger=1500, restricted=9, staff=6, sources=5, log=lambda *_: None)
NOT_REAL = re.compile("|".join(["nic" + "oly", "serv" + "are", "jaky" + "son", "feni" + "man", "MSC23" + "90028320", "A22" + "07", "al" + "ves"]), re.I)  # names from the firm's real files, kept out of the repo


def files_of(root: Path) -> dict[str, str]:
    """{relative path: sha256} of every file under the world, with the world's own folder name taken out of what it says (a case's record names the folder of its scan)."""
    return {str(p.relative_to(root)): hashlib.sha256(p.read_bytes().replace(str(root).encode(), b"<world>")).hexdigest() for p in sorted(root.rglob("*")) if p.is_file()}


@pytest.fixture(scope="module")
def built(tmp_path_factory):
    root = tmp_path_factory.mktemp("world") / "w"
    return root, make_world.build(root, **SMALL)


def test_two_builds_with_the_same_seed_are_the_same_firm_and_another_seed_is_another(tmp_path, built):
    root, manifest = built
    again = tmp_path / "again"
    make_world.build(again, **SMALL)
    a, b = files_of(root), files_of(again)
    # the staff accounts are made with the real sign-in code (a password's salt, an authenticator's secret, the access log's clock): everything else is byte for byte the same
    accounts = {"data/review_users.json", "data/review_users_access.jsonl", "data/review_users_totp.key", "world.json"}
    same = {k for k in a if a[k] == b.get(k)}
    assert set(a) == set(b) and {k for k in a if k not in same} <= accounts, sorted(set(a) ^ set(b) | {k for k in a if k not in same})
    other = tmp_path / "other"
    make_world.build(other, **{**SMALL, "seed": 7})
    assert json.loads((other / "world.json").read_text(encoding="utf-8"))["case_ids"] != manifest["case_ids"]


def test_it_makes_what_was_asked_for(built):
    root, m = built
    data = root / "data"
    assert m["cases"] == 60 and len(m["case_ids"]) == 60 and len(set(m["case_ids"])) == 60
    assert len([p for p in (data / "clients").iterdir() if (p / "fact_graph.json").exists()]) == 60
    assert len(m["restricted_ids"]) == m["restricted"] and 5 <= m["restricted"] <= 9
    assert len((data / "review_views.jsonl").read_text(encoding="utf-8").splitlines()) == 500
    ledger = list(events.rows(data / "events.jsonl"))
    assert len(ledger) == 1500 and all(set(events.FIELDS) <= set(r) for r in ledger)
    users = json.loads((data / "review_users.json").read_text(encoding="utf-8"))["users"]
    assert len(users) == 6 + 2 and {u["role"] for u in users.values()} == {"attorney", "paralegal"}  # the staff beyond the two who sign in, and the two
    assert m["people"]["attorney"]["device"] and m["people"]["paralegal"]["password"]
    assert len(list((root / "clients").glob("*/source/scan-*.pdf"))) == 5 * 2  # the scans the overnight run reads: two each for the first few cases
    papers = list((root / "clients").glob("*/source/approval.pdf"))  # a third of the others have the papers the packet asks for: no paper is left for the client to send (Today)
    assert len(papers) == len([n for n in range(5, 60) if n % 3 == 1]) and len(list((root / "clients").glob("*/source/birth.pdf"))) == len(papers)


def test_every_track_is_there_and_the_restricted_cases_are_restricted_by_the_product_itself(built):
    root, m = built
    clients = root / "data" / "clients"
    tracks = {}
    for case in m["case_ids"]:
        status = json.loads((clients / case / "status.json").read_text(encoding="utf-8"))
        tracks.setdefault(status["journey"]["track"]["value"], []).append(case)
    assert {"sij", "family"} <= set(tracks)  # the small firm; the 2,000-case firm has all nine (below)
    closed = {c for c in m["case_ids"] if restricted.is_restricted(clients / c)}
    assert closed == set(m["restricted_ids"])  # the law's tracks and the attorney's marks: the product's own test agrees with the world's list
    paralegal = {"email": m["people"]["paralegal"]["email"], "role": "paralegal"}
    assert any(restricted.visible_to(paralegal, clients / c) for c in closed) and not all(restricted.visible_to(paralegal, clients / c) for c in closed)  # named on some, not on the rest


def test_the_two_thousand_case_firm_has_every_kind_of_case_and_the_sizes_the_brief_names(tmp_path):
    plan = make_world.case_plan(2000, 300, make_world.SEED)
    assert len(plan) == 2000 and {p["track"] for p in plan} == {t for t, _ in make_world.TRACK_MIX}
    assert sum(1 for p in plan if p["restricted"]) == 300 and len({p["id"] for p in plan}) == 2000
    assert {p["office"] for p in plan} == set(make_world.OFFICES) and {p["language"] for p in plan} >= {"pt", "es", "en", "ht"}
    law = [p for p in plan if p["restricted"] == "law"]
    assert law and all(p["track"] in ("vawa", "asylum", "t_visa", "u_visa") for p in law) and all(p["restricted"] != "law" or p["track"] != "sij" for p in plan)
    # the defaults are the brief's: 200,000 view-log rows, 500,000 ledger rows, 40 staff, 300 restricted cases
    assert 200_000 in make_world.build.__defaults__ and 500_000 in make_world.build.__defaults__ and 40 in make_world.build.__defaults__ and 300 in make_world.build.__defaults__


def test_the_records_are_the_ones_the_product_reads(built):
    root, m = built
    clients = root / "data" / "clients"
    import documents
    from factgraph import FactGraph
    from review.state import load_decisions

    case = clients / m["case_ids"][0]
    graph = FactGraph.load(case / "fact_graph.json")
    assert len(graph.all_facts()) > 30 and graph.get("applicant.given_name").value
    assert documents.read(case)["documents"] and isinstance(load_decisions(case), dict)
    assert json.loads((case / "meta.json").read_text(encoding="utf-8"))["client_id"] == case.name
    portal = root / "data" / "portal" / "clients"
    assert len(list(portal.glob("*/profile.json"))) >= 55  # most clients have a portal folder
    queue = json.loads((root / "data" / "inbox" / "queue.json").read_text(encoding="utf-8"))
    assert len(queue) == 60 and all(q["candidates"] for q in queue)
    for row in list(events.rows(root / "data" / "events.jsonl"))[:300]:  # the ledger's rows say words, never a date, a number or a key (tests/conftest's fixture holds the product's own rows to it)
        assert not re.search(r"\d{4}-\d{2}-\d{2}|\d{6}|[a-z]+\.[a-z_]+", row["what"]), row
    assert "SECRET" not in (root / "world.json").read_text(encoding="utf-8")


def test_nobody_in_it_is_real_and_no_document_was_read(built):
    root, _ = built
    text = "".join(p.read_text(encoding="utf-8", errors="replace") for p in root.rglob("*") if p.is_file() and p.suffix in (".json", ".jsonl", ".txt"))
    assert not NOT_REAL.search(text)
    assert "Exemplo" in text and "MADE UP" in text
    code = (REPO / "tools" / "make_world.py").read_text(encoding="utf-8")
    assert "process_documents" not in code and "process_client" not in code and "classify" not in code  # it writes the records; it never reads a document


def test_a_world_is_built_in_minutes_not_hours(tmp_path):
    started = time.time()
    make_world.build(tmp_path / "w", cases=300, views=5000, ledger=20000, restricted=45, staff=10, sources=10, log=lambda *_: None)
    assert time.time() - started < 120  # 2,000 cases with the full logs: about 20 s on a Linux disk, 20 s over /mnt/c; the brief's bound is five minutes


def test_it_refuses_a_folder_that_already_has_something_in_it(tmp_path):
    (tmp_path / "x").mkdir()
    (tmp_path / "x" / "keep.txt").write_text("a firm's own file", encoding="utf-8")
    with pytest.raises(SystemExit):
        make_world.build(tmp_path / "x", **SMALL)
    assert (tmp_path / "x" / "keep.txt").read_text(encoding="utf-8") == "a firm's own file"
