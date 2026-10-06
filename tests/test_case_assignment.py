"""Fictional assignment state, fault injection and real-process claim contention."""
import json
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

import case_assignment as assignment
import events
import jobs

A = "alpha@fictional.invalid"
B = "beta@fictional.invalid"
T = "attorney@fictional.invalid"
S = "support@fictional.invalid"
CASE = "fictional-case"


def write(path, data):
    path.write_text(json.dumps(data), encoding="utf-8")


@pytest.fixture
def firm(tmp_path, monkeypatch):
    data = tmp_path / "data"
    clients = data / "clients"
    folder = clients / CASE
    folder.mkdir(parents=True)
    write(folder / "fact_graph.json", {})
    accounts_path = data / "staff.json"
    staff = [{"email": email, "name": name, "role": role, "active": True} for email, name, role in
             [(A, "Fictional Alpha", "paralegal"), (B, "Fictional Beta", "paralegal"), (T, "Fictional Attorney", "attorney"), (S, "Fictional Support", "support")]]
    write(accounts_path, staff)
    monkeypatch.setenv("I485_EVENTS", str(data / "events.jsonl"))
    # Registration is an isolated fixture until the shared additive kind patch.
    monkeypatch.setitem(events.KINDS, assignment.KIND, {"name": "Case responsibility", "firm": False})
    loader = lambda: json.loads(accounts_path.read_text(encoding="utf-8"))
    store = assignment.Assignments(clients, data / "jobs", loader)
    return store, folder, accounts_path


def change(store, actor=A, action="claim", revision=0, operation="a" * 32, **kwargs):
    return store.change(CASE, actor, action, expected_revision=revision, operation_id=operation, **kwargs)


def ledger(folder):
    return [r for r in events.rows(events.base_path(folder.parent.parent), case=CASE) if r["kind"] == assignment.KIND]


def test_claim_and_identical_lost_response_retry_are_one_revision(firm):
    store, folder, _ = firm
    assert store.view(CASE, A)["state"] == "unassigned"
    first = change(store, actor=" ALPHA@fictional.invalid ")
    retry = change(store)
    assert first == retry
    assert first["committed"] and first["committed_revision"] == 1 and not first["audit_pending"]
    assert len(store.read(CASE)["history"]) == len(ledger(folder)) == 1
    assert first["assignee"]["email"] == A


def test_both_staff_roles_reassign_any_accessible_case_and_clear(firm):
    store, folder, _ = firm
    change(store)
    moved = change(store, actor=B, action="reassign", target_email=T, revision=1, operation="b" * 32)
    assert moved["assignee"]["email"] == T  # B did not previously own this case; reason is optional
    cleared = change(store, actor=A, action="clear", revision=2, operation="c" * 32)
    assert cleared["assignee"] is None
    last = store.read(CASE)["history"][-1]
    assert last["before"]["email"] == T and last["after"] is None and last["actor"]["email"] == A and last["at"]
    moved = change(store, actor=T, action="reassign", target_email=B, reason="  Workload\n transfer  ", revision=3, operation="d" * 32)
    assert moved["assignee"]["email"] == B
    assert store.read(CASE)["history"][-1]["reason"] == "Workload transfer"
    assert len(ledger(folder)) == 4


@pytest.mark.parametrize("target", [None, "", "not-an-account", "absent@fictional.invalid"], ids=["missing", "empty", "invalid", "unknown"])
def test_missing_or_invalid_reassign_target_never_clears(firm, target):
    store, _, _ = firm
    change(store)
    with pytest.raises((ValueError, PermissionError)):
        change(store, action="reassign", target_email=target, revision=1, operation="b" * 32)
    assert store.read(CASE)["revision"] == 1 and store.read(CASE)["assignee"]["email"] == A
    with pytest.raises(ValueError):
        change(store, action="clear", target_email=B, revision=1, operation="c" * 32)


def test_stale_or_mismatched_retry_cannot_change_current_owner(firm):
    store, folder, _ = firm
    change(store)
    with pytest.raises(assignment.Conflict):
        change(store, actor=B, operation="b" * 32)
    with pytest.raises(assignment.Conflict):
        change(store, action="reassign", target_email=B, revision=1)  # same operation, changed payload
    change(store, actor=B, action="reassign", target_email=B, revision=1, operation="b" * 32)
    retry = change(store)
    assert retry["committed_revision"] == 1 and retry["revision"] == 2
    assert retry["operation_assignee"]["email"] == A and retry["assignee"]["email"] == B
    assert len(ledger(folder)) == 2


def test_payload_normalization_and_actor_binding(firm):
    store, _, _ = firm
    first = change(store, actor=T, action="reassign", target_email=" ALPHA@fictional.invalid ", reason="  Workload\n transfer ")
    assert change(store, actor=T, action="reassign", target_email=A, reason="Workload transfer") == first
    with pytest.raises(assignment.Conflict):
        change(store, actor=B, action="reassign", target_email=A, reason="Workload transfer")
    with pytest.raises(assignment.Conflict):
        change(store, actor=T, action="reassign", target_email=A, reason="Changed reason")


@pytest.mark.parametrize("actor", [S, "missing@fictional.invalid"], ids=["support", "unknown"])
def test_role_and_unknown_account_cannot_mutate(firm, actor):
    store, folder, _ = firm
    with pytest.raises(PermissionError):
        change(store, actor=actor)
    assert not (folder / assignment.FILE).exists()


def test_fresh_accounts_not_actor_snapshot_and_inactive_assignee_attention(firm):
    store, _, path = firm
    change(store)
    staff = json.loads(path.read_text()); staff[0]["active"] = False; write(path, staff)
    assert store.view(CASE, T)["state"] == "needs_attention"
    with pytest.raises(PermissionError):
        change(store, action="reassign", target_email=B, revision=1, operation="b" * 32)
    with pytest.raises(PermissionError):
        change(store, actor=T, action="reassign", target_email=A, revision=1, operation="b" * 32)
    moved = change(store, actor=B, action="reassign", target_email=B, revision=1, operation="b" * 32)
    assert moved["assignee"]["email"] == B
    assert store.read(CASE)["history"][0]["after"]["email"] == A
    staff = [r for r in staff if r["email"] != B]; write(path, staff)
    assert store.view(CASE, T)["state"] == "needs_attention"


def test_accounts_are_reloaded_inside_case_lock(firm, monkeypatch):
    store, folder, path = firm
    original = jobs.case_lock
    from contextlib import contextmanager
    @contextmanager
    def revoke_then_lock(*args, **kwargs):
        with original(*args, **kwargs):
            staff = json.loads(path.read_text()); staff[0]["active"] = False; write(path, staff)
            yield
    monkeypatch.setattr(jobs, "case_lock", revoke_then_lock)
    with pytest.raises(PermissionError):
        change(store)
    assert not (folder / assignment.FILE).exists()


def test_current_case_acl_and_no_assignment_access_grant(firm):
    store, folder, _ = firm
    access = {"marked": {"on": True}, "people": [{"email": A}], "messages": {}, "history": []}
    write(folder / "access.json", access)
    before = (folder / "access.json").read_bytes()
    change(store)
    with pytest.raises(PermissionError):
        change(store, actor=B, action="reassign", target_email=A, revision=1, operation="b" * 32)
    with pytest.raises(PermissionError):
        change(store, actor=T, action="reassign", target_email=B, revision=1, operation="b" * 32)
    assert (folder / "access.json").read_bytes() == before
    access["people"] = []; write(folder / "access.json", access)
    assert store.view(CASE, T)["state"] == "needs_attention"
    with pytest.raises(PermissionError):
        change(store)  # even a committed retry needs current actor access


@pytest.mark.parametrize("end", ["declined", "withdrawn", "transferred", "closed"])
def test_actual_engagement_end_states_refuse_new_assignments_and_retain_retry(firm, end):
    store, folder, _ = firm
    change(store)
    write(folder / "engagement.json", {"end": {"state": end, "on": "2026-10-04"}})
    before = (folder / assignment.FILE).read_bytes()
    with pytest.raises(assignment.Conflict):
        change(store, actor=B, action="reassign", target_email=B, revision=1, operation="b" * 32)
    assert change(store)["committed_revision"] == 1
    assert (folder / assignment.FILE).read_bytes() == before


@pytest.mark.parametrize("kind", ["conflict", "abandoned", "destroyed", "purge-wait", "purge-done"])
def test_existing_lifecycle_guards_refuse(firm, kind):
    store, folder, _ = firm
    if kind == "conflict":
        write(folder / "conflict_check.json", {"decision": {"decision": "declined"}})
    elif kind == "abandoned":
        write(folder / "conflict_check.json", {"abandoned": {"at": "2026-10-04"}})
    elif kind == "destroyed":
        write(folder.parent.parent / "destroyed.json", {"cases": [{"case": CASE}]})
    else:
        write(folder.parent.parent / "purges.json", {"cases": {CASE: {"state": "waiting" if kind == "purge-wait" else "done"}}})
    with pytest.raises(assignment.Conflict):
        change(store)
    assert not (folder / assignment.FILE).exists()


@pytest.mark.parametrize("file,value", [(assignment.FILE, []), (assignment.FILE, {"version": 1, "revision": 1, "assignee": None, "history": []}),
                                       ("engagement.json", {"end": []}), ("conflict_check.json", {"decision": []}),
                                       ("purges.json", {"cases": []}), ("destroyed.json", {"cases": {}})],
                         ids=["assignment-list", "revision", "end-list", "decision-list", "purge-list", "destroyed-map"])
def test_wrong_shapes_fail_closed(firm, file, value):
    store, folder, _ = firm
    path = folder.parent.parent / file if file in ("purges.json", "destroyed.json") else folder / file
    write(path, value)
    with pytest.raises(assignment.Unavailable):
        change(store)
    if file != assignment.FILE:
        assert not (folder / assignment.FILE).exists()


def test_corrupt_assignment_and_unavailable_accounts_never_mean_unassigned(firm):
    store, folder, path = firm
    (folder / assignment.FILE).write_text("{cut", encoding="utf-8")
    with pytest.raises(assignment.Unavailable):
        store.view(CASE, T)
    (folder / assignment.FILE).unlink()
    path.write_text("{cut", encoding="utf-8")
    with pytest.raises(assignment.Unavailable):
        change(store)


def test_malformed_nested_history_fails_closed(firm):
    store, folder, _ = firm
    change(store)
    data = store.read(CASE); data["history"][0]["action"] = []
    write(folder / assignment.FILE, data)
    with pytest.raises(assignment.Unavailable):
        store.read(CASE)


def test_ledger_failure_is_committed_pending_and_retries_once(firm, monkeypatch):
    store, folder, _ = firm
    original = events.record
    monkeypatch.setattr(events, "record", lambda *a, **k: None)
    outcome = change(store)
    assert outcome["committed"] and outcome["audit_pending"]
    assert len(store.read(CASE)["history"]) == 1 and not ledger(folder)
    monkeypatch.setattr(events, "record", original)
    assert not change(store)["audit_pending"]
    assert len(ledger(folder)) == len(store.read(CASE)["history"]) == 1


def test_audit_append_before_marker_fault_recovers_without_duplicate(firm, monkeypatch):
    store, folder, _ = firm
    original = store._write
    def fail_marker(where, data):
        if data["history"][0]["audit"]["state"] == "recorded":
            raise OSError("fictional crash after append before marker")
        return original(where, data)
    monkeypatch.setattr(store, "_write", fail_marker)
    with pytest.raises(OSError):
        change(store)
    assert store.read(CASE)["history"][0]["audit"]["state"] == "pending" and len(ledger(folder)) == 1
    monkeypatch.setattr(store, "_write", original)
    assert not change(store)["audit_pending"]
    assert len(ledger(folder)) == 1


def test_same_revision_actor_action_with_different_payload_is_not_reconciled(firm):
    store, folder, _ = firm
    # A row for a different transfer cannot authenticate this record merely
    # because actor/action/revision happen to agree.
    events.record(assignment.KIND, "claim", "Claimed case responsibility (assignment SHA-256 " + "0" * 64 + ")",
                  case_dir=folder, who="Fictional Alpha", role="paralegal", via="staff", version=1)
    with pytest.raises(assignment.Unavailable, match="reconciliation"):
        change(store)
    assert store.read(CASE)["history"][0]["audit"]["state"] == "pending"
    assert len(ledger(folder)) == 1


def test_crash_before_state_replace_leaves_only_uncommitted_partial(firm, monkeypatch):
    store, folder, _ = firm
    original = assignment.os.replace
    monkeypatch.setattr(assignment.os, "replace", lambda *a: (_ for _ in ()).throw(OSError("fictional interruption")))
    with pytest.raises(OSError):
        change(store)
    assert (folder / assignment.PART).exists() and not (folder / assignment.FILE).exists() and not ledger(folder)
    assert store.read(CASE)["revision"] == 0
    monkeypatch.setattr(assignment.os, "replace", original)
    assert change(store)["revision"] == 1 and not (folder / assignment.PART).exists()


@pytest.mark.parametrize("case", ["../fictional-case", "fictional-case/child", "FICTIONAL-CASE", "absent"], ids=["traversal", "child", "wrong-case", "unknown"])
def test_unknown_case_and_traversal_cannot_create_case(firm, case):
    store, folder, _ = firm
    with pytest.raises((LookupError, assignment.Unavailable)):
        store.change(case, A, "claim", expected_revision=0, operation_id="a" * 32)
    assert not (folder / assignment.FILE).exists()


def test_linked_assignment_target_is_refused_without_write(firm, tmp_path):
    store, folder, _ = firm
    outside = tmp_path / "outside.json"; outside.write_text("{}")
    try:
        (folder / assignment.FILE).symlink_to(outside)
    except OSError:
        pytest.skip("Host does not permit creating a symlink")
    with pytest.raises(assignment.Unavailable):
        change(store)
    assert outside.read_text() == "{}"


def test_history_capacity_does_not_drop_retry_proof(firm, monkeypatch):
    store, _, _ = firm
    monkeypatch.setattr(assignment, "MAX_HISTORY", 1)
    change(store)
    with pytest.raises(assignment.Unavailable):
        change(store, action="clear", revision=1, operation="b" * 32)
    assert change(store)["committed_revision"] == 1


def test_two_real_processes_claim_one_case_with_one_winner(firm, tmp_path):
    store, folder, accounts = firm
    script = '''
import json, os, sys, time
from pathlib import Path
import case_assignment as ca, events
events.KINDS[ca.KIND] = {"name":"Case responsibility", "firm":False}
root, staff, actor, token, barrier = sys.argv[1:]
store = ca.Assignments(Path(root)/"clients", Path(root)/"jobs", lambda:json.loads(Path(staff).read_text()))
print("ready", flush=True)
Path(barrier+"."+token).write_text("ready")
deadline=time.monotonic()+10
while not Path(barrier).exists():
    if time.monotonic()>deadline: raise TimeoutError("bounded fictional barrier")
    time.sleep(.01)
try:
    outcome=store.change("fictional-case", actor, "claim", expected_revision=0, operation_id=token)
    print(json.dumps({"result":"won", "revision":outcome["revision"]}), flush=True)
except ca.Conflict:
    print(json.dumps({"result":"conflict"}), flush=True)
'''
    src = str(Path(assignment.__file__).parent)
    env = dict(os.environ, PYTHONPATH=src, I485_EVENTS=str(folder.parent.parent / "events.jsonl"))
    barrier = tmp_path / "go"
    children = [subprocess.Popen([sys.executable, "-u", "-c", script, str(folder.parent.parent), str(accounts), actor, token, str(barrier)],
                                 stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True, env=env) for actor, token in [(A, "a" * 32), (B, "b" * 32)]]
    try:
        # communicate() has the hard bound; no blocking readline on a failed child.
        deadline = time.monotonic() + 10
        while not all(Path(str(barrier) + "." + token).exists() for token in ("a" * 32, "b" * 32)):
            assert time.monotonic() < deadline, "Fictional processes did not reach the bounded start barrier"
            assert all(p.poll() is None for p in children), "Fictional process failed before contention"
            time.sleep(.01)
        barrier.touch()
        outputs = [p.communicate(timeout=20) for p in children]
        results = []
        for p, (out, err) in zip(children, outputs):
            assert p.returncode == 0, err
            assert out.splitlines()[0] == "ready"
            results.append(json.loads(out.splitlines()[-1])["result"])
        assert sorted(results) == ["conflict", "won"]
        assert store.read(CASE)["revision"] == 1
        assert len(store.read(CASE)["history"]) == len(ledger(folder)) == 1
    finally:
        for p in children:
            if p.poll() is None:
                p.kill(); p.communicate(timeout=5)
