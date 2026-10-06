"""Fictional authenticated routes over accepted assignment authority/cache."""
import json
import threading
from concurrent.futures import ThreadPoolExecutor
from urllib.parse import quote

import pytest

import case_assignment as ca
import events
import jobs
import restricted
from assignment_route_fixtures import app, world, server, call, sign_in, PASSWORD  # noqa: F401 -- pytest fixture registration and helper reexports
from test_case_assignment_lists import cohort as assignment_cohort, A, B, T


@pytest.fixture(autouse=True)
def controls(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))
    monkeypatch.setenv("I485_ROSTER", str(tmp_path / "roster.json"))
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    monkeypatch.setenv("I485_ROSTER_GAP", "0")
    monkeypatch.setenv("I485_ROSTER_BUDGET", "30")


@pytest.fixture
def route_cohort(tmp_path_factory, monkeypatch):
    # Keep the accepted exactly-2,000-folder fixture separate from the
    # authenticated HTTP world's three case folders.
    return assignment_cohort.__wrapped__(tmp_path_factory.mktemp("assignment-route-cohort"), monkeypatch)


def detail(server, cookie, case="case-ana"):  # noqa: F811 -- pytest fixture injection
    status, raw = call(server + "/api/assignment?client=" + quote(case), cookie)
    return status, json.loads(raw)


def change(server, cookie, action="claim", *, case="case-ana", revision=0, operation="a" * 32, **extras):  # noqa: F811 -- pytest fixture injection
    status, raw = call(server + "/api/assignment", cookie,
                       {"client": case, "action": action, "revision": revision, "operation_id": operation, **extras})
    return status, json.loads(raw)


def test_routes_require_auth_and_case_gate_hidden_equals_missing(server):  # noqa: F811 -- pytest fixture injection
    assert call(server + "/api/case-list")[0] == 401
    jane = sign_in(server, "jane@firm.example")
    for case in ["case-rosa", "missing-fictional-case", "../case-ana", ""]:
        status, raw = call(server + "/api/assignment?client=" + quote(case), jane)
        assert status == 404
        assert json.loads(raw) == {"error": "unknown client"}
        assert change(server, jane, case=case)[0] == 404


def test_claim_transfer_clear_public_history_and_named_audit(server, app, world):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    kim = sign_in(server, "kim@firm.example")
    status, initial = detail(server, jane)
    assert status == 200 and initial["can_claim"] and initial["revision"] == 0
    assert {p["name"] for p in initial["eligible_assignees"]} == {"Jane Doe", "Kim Exemplo", "Sam Attorney"}
    assert "@" not in json.dumps(initial)
    assert change(server, jane)[0] == 200
    # Another paralegal can transfer any case they currently may access.
    status, moved = change(server, kim, "reassign", revision=1, operation="b" * 32,
                           assignee=app.person_id("kim@firm.example"), reason="Fictional workload transfer",
                           reviewer="Spoofed actor", email="jane@firm.example", role="attorney")
    assert status == 200 and moved["assignee"]["name"] == "Kim Exemplo"
    assert change(server, jane, "clear", revision=2, operation="c" * 32)[0] == 200
    status, final = detail(server, jane)
    assert status == 200 and final["state"] == "unassigned" and final["revision"] == 3
    assert [r["actor"]["name"] for r in final["history"]] == ["Jane Doe", "Kim Exemplo", "Jane Doe"]
    assert [(r["before"] and r["before"]["name"], r["after"] and r["after"]["name"]) for r in final["history"]] == [
        (None, "Jane Doe"), ("Jane Doe", "Kim Exemplo"), ("Kim Exemplo", None)]
    assert all(r["at"] and r["audit"]["state"] == "recorded" for r in final["history"])
    assert not final["audit_pending"] and "@" not in json.dumps(final)
    assert not any("operation" in r or "payload_sha256" in r for r in final["history"])
    assert len([r for r in events.rows(events.base_path(world.parent), case="case-ana") if r["kind"] == "assignment"]) == 3


def test_stale_cas_identical_retry_and_mismatched_retry(server):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    first = change(server, jane)
    assert first[0] == 200
    assert change(server, jane) == first
    assert change(server, jane, "clear", operation="a" * 32)[0] == 409
    status, stale = change(server, jane, "clear", operation="b" * 32)
    assert status == 409 and stale["conflict"] is True
    assert detail(server, jane)[1]["revision"] == 1


@pytest.mark.parametrize("extra", [{}, {"assignee": "kim@firm.example"}, {"assignee": ""}, {"assignee": "missing-id"}],
                         ids=["missing", "email", "blank", "unknown"])
def test_bad_transfer_selection_does_not_clear(server, extra):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    assert change(server, jane)[0] == 200
    assert change(server, jane, "reassign", revision=1, operation="b" * 32, **extra)[0] == 400
    current = detail(server, jane)[1]
    assert current["revision"] == 1 and current["assignee"]["name"] == "Jane Doe"


def test_concurrent_authenticated_claims_one_winner(server):  # noqa: F811 -- pytest fixture injection
    cookies = [sign_in(server, email) for email in ["jane@firm.example", "kim@firm.example"]]
    barrier = threading.Barrier(2)
    def claim(n):
        barrier.wait(timeout=5)
        return change(server, cookies[n], operation=("a" if n == 0 else "b") * 32)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(claim, [0, 1]))
    assert sorted(r[0] for r in results) == [200, 409]
    final = detail(server, cookies[0])[1]
    assert final["revision"] == 1 and len(final["history"]) == 1


def test_current_acl_inactive_target_and_ended_case_refuse(server, app, world):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    app.accounts.update("kim@firm.example", active=False)
    assert change(server, jane, "reassign", assignee=app.person_id("kim@firm.example"))[0] == 400
    folder = world / "case-ana"
    restricted.mark(folder, True, "Fictional private case", "Sam Attorney", "attorney")
    assert detail(server, jane)[0] == 404 and change(server, jane)[0] == 404
    restricted.name_person(folder, "jane@firm.example", True, "Sam Attorney", "attorney", name="Jane Doe")
    (folder / "engagement.json").write_text(json.dumps({"end": {"state": "closed"}}))
    status, view = detail(server, jane)
    assert status == 200 and not view["can_manage"] and not view["can_claim"]
    assert change(server, jane)[0] == 409


def test_current_target_acl_and_revoked_actor_session(server, app, world):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    folder = world / "case-ana"
    restricted.mark(folder, True, "Fictional private case", "Sam Attorney", "attorney")
    restricted.name_person(folder, "jane@firm.example", True, "Sam Attorney", "attorney", name="Jane Doe")
    view = detail(server, jane)[1]
    assert {p["name"] for p in view["eligible_assignees"]} == {"Jane Doe", "Sam Attorney"}
    assert change(server, jane, "reassign", assignee=app.person_id("kim@firm.example"))[0] == 403
    assert view["revision"] == detail(server, jane)[1]["revision"] == 0
    app.accounts.update("jane@firm.example", active=False)
    assert detail(server, jane)[0] == 401 and change(server, jane)[0] == 401
    assert call(server + "/api/case-list", jane)[0] == 401
    assert not (folder / ca.FILE).exists()


def test_current_reading_lock_returns_truthful_conflict_for_detail_and_change(server, app, world, monkeypatch):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    monkeypatch.setattr(jobs, "BUSY_WAIT", 0.05)
    with jobs.case_lock(app.jobs_root, "case-ana"):
        assert detail(server, jane)[0] == 409
        assert change(server, jane)[0] == 409
    assert not (world / "case-ana" / ca.FILE).exists()


def test_corrupt_state_503_and_pending_ledger_is_truthful(server, world, monkeypatch):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    target = world / "case-ana" / ca.FILE
    target.write_text("{fictional damaged record")
    assert detail(server, jane)[0] == 503 and change(server, jane)[0] == 503
    target.unlink()
    monkeypatch.setattr(events, "record", lambda *a, **k: None)
    status, result = change(server, jane)
    assert status == 200 and result["committed"] and result["audit_pending"]
    assert detail(server, jane)[1]["history"][0]["audit"]["state"] == "pending"


def test_route_my_default_and_parameter_errors(server):  # noqa: F811 -- pytest fixture injection
    jane = sign_in(server, "jane@firm.example")
    assert change(server, jane)[0] == 200
    status, raw = call(server + "/api/case-list", jane)
    data = json.loads(raw)
    assert status == 200 and data["scope"] == "mine" and data["size"] == 50
    assert [row["id"] for row in data["clients"]] == ["case-ana"]
    assert "case-rosa" not in raw and "@" not in raw
    for query in ["scope=bad", "page=0", "size=bad", "assignee=kim%40firm.example"]:
        assert call(server + "/api/case-list?" + query, jane)[0] == 400


def test_actual_route_2000_cached_folders_bounded_and_one_accounts_load(server, app, route_cohort, monkeypatch, record_property):  # noqa: F811 -- pytest fixture injection
    roster, root, calls, loader, seeded, seed_seconds, warm_seconds = route_cohort
    app.roster = roster
    for email, name, role in [(A, "Fictional Alpha", "paralegal"), (B, "Fictional Beta", "paralegal"), (T, "Fictional Attorney", "attorney")]:
        app.accounts.change_password(email, app.accounts.add(email, name, role), PASSWORD)
    cookie = sign_in(server, A)
    real_users = app.accounts.users
    loaded = []
    monkeypatch.setattr(app.accounts, "users", lambda: (loaded.append(True), real_users())[1])
    before = roster.reads, roster.walks
    status, raw = call(server + "/api/case-list?scope=all&size=99999", cookie)
    result = json.loads(raw)
    assert status == 200 and result["size"] == len(result["clients"]) == 200
    assert result["total"] == result["counts"]["all"] == 1900
    assert len(loaded) == 1 and (roster.reads, roster.walks) == before
    assert "fictional-0010" not in raw and "@" not in raw and '"history"' not in raw
    hidden = json.loads(call(server + "/api/case-list?scope=all&q=fictional-0010", cookie)[1])
    missing = json.loads(call(server + "/api/case-list?scope=all&q=fictional-missing", cookie)[1])
    assert {k: v for k, v in hidden.items() if k != "roster_version"} == {
        k: v for k, v in missing.items() if k != "roster_version"}
    record_property("fictional_folder_count", 2000)
    record_property("seed_seconds", seed_seconds)
    record_property("cache_build_seconds", warm_seconds)
    record_property("scope", "real folder/cached assignment list route; not OCR or deployment throughput")
