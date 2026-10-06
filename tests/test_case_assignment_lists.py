"""Pure paging and actual roster lifecycle over 2,000 fictional case folders.

The scale fixture supplies synthetic precomputed case rows (not an OCR/form
benchmark). Real assignment files, roster walk/save/load/Tail/sync/listing run.
"""
import json
import time

import pytest

import case_assignment as ca
import events
import restricted
from review.case_lists import page, _id
from review.roster import Roster

A = "alpha@fictional.invalid"
B = "beta@fictional.invalid"
T = "attorney@fictional.invalid"


def accounts():
    return [{"email": e, "name": n, "role": r, "active": True} for e, n, r in
            [(A, "Fictional Alpha", "paralegal"), (B, "Fictional Beta", "paralegal"), (T, "Fictional Attorney", "attorney")]]


def entry(case, assigned=None, *, closed=False, named=None, ended=False, state=None):
    person = next((u for u in accounts() if u["email"] == assigned), None)
    person = {k: person[k] for k in ("email", "name", "role")} if person else None
    return {"row": {"id": case, "summary": {"name": "Same fictional name"}, "stage": "review", "end": {"state": "closed"} if ended else None},
            "closed": closed, "named": named or [], "unwritten": False, "has_case": True, "held": False,
            "office": "Fictional Office", "assignment": {"state": state or ("assigned" if person else "unassigned"),
                                                        "revision": 1 if person else 0, "assignee": person, "audit_pending": False}}


def test_default_mine_acl_first_counts_and_both_roles_management_scopes():
    entries = {"my": entry("my", A), "peer": entry("peer", B), "queue": entry("queue"),
               "hidden": entry("hidden", A, closed=True), "named": entry("named", A, closed=True, named=[A]),
               "ended": entry("ended", A, ended=True), "broken": entry("broken", state="unavailable")}
    mine = page(entries, {"email": A, "role": "attorney"}, accounts())  # saved role is ignored
    assert mine["scope"] == "mine" and [r["id"] for r in mine["clients"]] == ["my", "named"]
    assert mine["counts"] == {"mine": 2, "unassigned": 1, "all": 5, "needs_attention": 1}
    assert page(entries, {"email": A}, accounts(), {"scope": "unassigned"})["clients"][0]["id"] == "queue"
    assert page(entries, {"email": A}, accounts(), {"scope": "needs_attention"})["clients"][0]["id"] == "broken"
    assert page(entries, {"email": T}, accounts(), {"scope": "all"})["total"] == 6
    assert page(entries, {"email": A}, accounts(), {"scope": "all", "ended": "ended"})["total"] == 1
    raw = json.dumps(mine)
    assert "fictional.invalid" not in raw and "history" not in raw and '"named":' not in raw


def test_deactivation_role_and_access_changes_derive_attention_from_current_accounts():
    rows = {"one": entry("one", A, closed=True, named=[A]), "two": entry("two", B)}
    staff = accounts(); staff[0]["active"] = False
    assert page(rows, {"email": T}, staff, {"scope": "needs_attention"})["total"] == 1
    with pytest.raises(PermissionError):
        page(rows, {"email": A, "role": "attorney"}, staff)
    staff = accounts(); staff[2]["role"] = "paralegal"
    assert page(rows, {"email": T, "role": "attorney"}, staff, {"scope": "all"})["total"] == 1
    rows["one"]["named"] = []
    attention = page(rows, {"email": T}, accounts(), {"scope": "needs_attention"})
    assert attention["total"] == 1 and attention["clients"][0]["assignment"]["state"] == "needs_attention"
    staff = [u for u in accounts() if u["email"] != B]
    assert page(rows, {"email": T}, staff, {"scope": "needs_attention"})["total"] == 2


def test_search_eligible_person_filter_and_compact_unassigned_dto():
    rows = {"abc": entry("abc", A), "def": entry("def", B), "queue": entry("queue")}
    rows["queue"]["assignment"]["history"] = [{"email": A}]  # cache extras never enter public DTO
    assert page(rows, {"email": T}, accounts(), {"scope": "all", "q": " ABC "})["total"] == 1
    assert page(rows, {"email": T}, accounts(), {"scope": "all", "assignee": _id(A)})["clients"][0]["id"] == "abc"
    assert "history" not in json.dumps(page(rows, {"email": A}, accounts(), {"scope": "unassigned"}))
    with pytest.raises(ValueError):
        page(rows, {"email": T}, accounts(), {"assignee": A})  # public input is person ID, not email
    staff = accounts(); staff[0]["active"] = False
    with pytest.raises(ValueError):
        page(rows, {"email": T}, staff, {"scope": "all", "assignee": _id(A)})


@pytest.mark.parametrize("query", [{"page": 0}, {"size": 0}, {"page": 1.5}, {"size": True}, {"page": "bad"},
                                   {"scope": "unknown"}, {"ended": "unknown"}, {"q": "x" * 201}],
                         ids=["zero-page", "zero-size", "fraction", "boolean", "bad-page", "scope", "ended", "search-limit"])
def test_invalid_list_queries_refuse(query):
    with pytest.raises(ValueError):
        page({}, {"email": A}, accounts(), query)


def test_unavailable_assignment_and_bad_acl_never_become_claimable_or_visible():
    rows = {"missing-cache": entry("missing-cache"), "bad-cache": entry("bad-cache"), "bad-acl": entry("bad-acl")}
    rows["missing-cache"].pop("assignment")
    rows["bad-cache"]["assignment"]["assignee"] = {"email": A}
    rows["bad-acl"]["closed"] = "unknown"
    got = page(rows, {"email": A}, accounts(), {"scope": "all"})
    assert got["total"] == got["counts"]["needs_attention"] == 2
    assert got["counts"]["unassigned"] == 0
    with pytest.raises(ca.Unavailable):
        page(rows, {"email": A}, [{"email": A, "active": "unknown"}])


def test_explicit_empty_tail_snapshot_survives_restart_and_observes_first_event(tmp_path, monkeypatch):
    base = tmp_path / "events.jsonl"
    monkeypatch.setenv("I485_EVENTS", str(base))
    original = events.Tail(base)
    assert original.take() is None  # omitted snapshot is still unobserved
    assert original.start() == {}
    restarted = events.Tail(base, original.positions)
    assert restarted.take() == set()  # valid known empty ledger is not unobserved
    events.record(ca.KIND, "claim", "Fictional first assignment", case="fictional-first", home=tmp_path, who="Fictional Attorney", role="attorney")
    assert restarted.take() == {"fictional-first"}
    assert restarted.take() == set()


def assignment_record(email):
    person = next(u for u in accounts() if u["email"] == email)
    person = {k: person[k] for k in ("email", "name", "role")}
    request = ca._request(email, "claim", email, "", 0)
    change = {"revision": 1, "operation": "a" * 32, "payload_sha256": ca._digest(request), "actor": person,
              "action": "claim", "before": None, "after": person, "reason": "", "at": "2026-10-04T12:00:00+00:00", "audit": {"state": "pending"}}
    return {"version": 1, "revision": 1, "assignee": person, "history": [change]}


@pytest.fixture
def cohort(tmp_path, monkeypatch):
    root = tmp_path / "data" / "clients"; root.mkdir(parents=True)
    monkeypatch.setenv("I485_EVENTS", str(root.parent / "events.jsonl"))
    monkeypatch.setenv("I485_ROSTER", str(root.parent / "roster.json"))
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    monkeypatch.setenv("I485_ROSTER_GAP", "0")
    monkeypatch.setenv("I485_ROSTER_BUDGET", "30")
    seeded = {}
    started = time.perf_counter()
    for n in range(2000):
        case = f"fictional-{n:04d}"
        folder = root / case; folder.mkdir()
        snap = entry(case, A if n % 3 == 0 else B if n % 3 == 1 else None,
                     closed=n % 10 == 0, named=[A] if n % 20 == 0 else [])
        seeded[case] = snap
        (folder / "fact_graph.json").write_text("{}")
        if snap["assignment"]["assignee"]:
            (folder / ca.FILE).write_text(json.dumps(assignment_record(snap["assignment"]["assignee"]["email"])))
        if snap["closed"]:
            (folder / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": [{"email": e} for e in snap["named"]]}))
    seed_seconds = time.perf_counter() - started
    calls = {"build": 0, "accounts": 0}
    def build(self, case):
        calls["build"] += 1
        self.reads += 1
        snap = dict(seeded[case]); snap["assignment"] = ca.summary(root / case)
        acl = restricted.record(root / case)
        snap["closed"] = bool((acl["marked"] or {}).get("on"))
        snap["named"] = [p["email"] for p in acl["people"]]
        return snap
    monkeypatch.setattr(Roster, "build", build)
    def loader():
        calls["accounts"] += 1
        return accounts()
    roster = Roster(root, {}, "", None)
    started = time.perf_counter(); roster.walk(parallel=False); warm_seconds = time.perf_counter() - started
    return roster, root, calls, loader, seeded, seed_seconds, warm_seconds


def test_2000_real_folders_bounded_stable_paging_acl_and_warmed_cache(cohort, record_property, monkeypatch):
    roster, root, calls, loader, seeded, seed_seconds, warm_seconds = cohort
    assert len(list(root.iterdir())) == len(roster.entries) == calls["build"] == 2000
    before_reads, before_walks = roster.reads, roster.walks
    started = time.perf_counter()
    first = roster.assignment_page({"email": A}, loader, {"scope": "all", "size": 99999})
    assert first["size"] == len(first["clients"]) == 200 and first["total"] == 1900
    all_ids = []
    for number in range(1, first["pages"] + 1):
        got = roster.assignment_page({"email": A}, loader, {"scope": "all", "size": 200, "page": number})
        assert len(got["clients"]) <= 200 and got["counts"]["all"] == 1900
        all_ids.extend(row["id"] for row in got["clients"])
    list_seconds = time.perf_counter() - started
    assert len(all_ids) == len(set(all_ids)) == 1900
    assert all_ids == sorted(all_ids)  # names identical, stable exact-ID tie breaker
    assert "fictional-0010" not in all_ids and "fictional-0000" in all_ids
    assert roster.reads == before_reads and roster.walks == before_walks
    assert calls["accounts"] == 1 + first["pages"]
    assert len(json.dumps(first)) < 150000 and "fictional.invalid" not in json.dumps(first)
    # A restarted process reads the saved cache, not all the case folders.
    restarted = Roster(root, {}, "", None)
    assert restarted.assignment_page({"email": T}, loader, {"scope": "all"})["total"] == 2000
    assert restarted.reads == restarted.walks == 0
    assert roster.assignment_page({"email": A}, loader)["scope"] == "mine"
    for key, value in {"seed_seconds": seed_seconds, "synthetic_row_warm_seconds": warm_seconds, "ten_pages_seconds": list_seconds}.items():
        record_property(key, value)
    print("ASSIGNMENT_SCALE_TIMING", json.dumps({"cases": 2000, "seed_seconds": seed_seconds,
          "synthetic_row_warm_seconds": warm_seconds, "ten_pages_seconds": list_seconds}))


def test_local_touch_tail_change_and_pending_ledger_eventual_refresh(cohort, monkeypatch):
    roster, root, calls, loader, seeded, _, _ = cohort
    other = Roster(root, {}, "", None)
    assert other.assignment_page({"email": A}, loader, {"scope": "unassigned", "q": "fictional-0002"})["total"] == 1
    store = ca.Assignments(root, root.parent / "jobs", accounts)
    store.change("fictional-0002", A, "claim", expected_revision=0, operation_id="b" * 32)
    assert other.assignment_page({"email": A}, loader, {"q": "fictional-0002"})["total"] == 1  # R2 Tail rereads one entry
    assert other.reads == 1 and other.walks == 0
    restricted.mark(root / "fictional-0002", True, "Fictional restriction change", "Fictional Attorney", "attorney")
    denied = other.assignment_page({"email": A}, loader, {"scope": "all", "q": "fictional-0002"})
    assert denied["total"] == 0 and all(n == 0 for n in denied["counts"].values())
    assert other.assignment_page({"email": A}, loader, {"scope": "all", "q": "nonexistent-fictional"})["total"] == 0
    real_record = events.record
    monkeypatch.setattr(events, "record", lambda *a, **kw: None)
    out = store.change("fictional-0005", A, "claim", expected_revision=0, operation_id="c" * 32)
    assert out["audit_pending"]
    roster.touch("fictional-0005")
    assert roster.assignment_page({"email": A}, loader, {"q": "fictional-0005"})["clients"][0]["assignment"]["audit_pending"]
    assert other.assignment_page({"email": A}, loader, {"scope": "unassigned", "q": "fictional-0005"})["total"] == 1  # honestly still stale
    with pytest.raises(ca.Conflict):
        store.change("fictional-0005", B, "claim", expected_revision=0, operation_id="d" * 32)  # fresh CAS protects it
    # Existing periodic background walk, not a new notification protocol.
    monkeypatch.setenv("I485_WALK_EVERY", "1")
    other.last_walk = time.monotonic() - max(2, 10 * other.took + 2)
    other.assignment_page({"email": A}, loader, {"q": "fictional-0005"}); other.wait(30)
    assert other.assignment_page({"email": A}, loader, {"q": "fictional-0005"})["clients"][0]["assignment"]["audit_pending"]
    monkeypatch.setattr(events, "record", real_record)
    store.change("fictional-0005", A, "claim", expected_revision=0, operation_id="c" * 32)
    assert not other.assignment_page({"email": A}, loader, {"q": "fictional-0005"})["clients"][0]["assignment"]["audit_pending"]


def test_product_roster_build_reads_assignment_summary_once(tmp_path, monkeypatch):
    from test_restricted import doc, make_case
    from review.roster import default_catalog
    clients = tmp_path / "clients"
    folder = make_case(clients, "fictional-real-row", "Fictional Roster Client", [doc("one", "passport", text="Fictional passport")])
    (folder / ca.FILE).write_text(json.dumps(assignment_record(T)))
    field_map, template, catalog = default_catalog()
    built = Roster(clients, field_map, template, catalog).build(folder.name)
    assert built["assignment"]["state"] == "assigned" and built["assignment"]["assignee"]["email"] == T
    (folder / ca.FILE).write_text("{interrupted")
    rebuilt = Roster(clients, field_map, template, catalog).build(folder.name)
    assert rebuilt["assignment"]["state"] == "unavailable"


def test_pending_rereads_do_not_use_stale_acl_summary(cohort, monkeypatch):
    roster, _, _, loader, _, _, _ = cohort
    monkeypatch.setattr(roster, "sync", lambda: None)
    roster.later.add("fictional-0002")
    roster.inflight.add("fictional-0005")
    assert roster.assignment_page({"email": A}, loader, {"scope": "all", "q": "fictional-0002"})["total"] == 0
    assert roster.assignment_page({"email": A}, loader, {"scope": "all", "q": "fictional-0005"})["total"] == 0
