"""Request-local current authorization: freshness, deduplication and exact names."""
import json
from pathlib import Path
import time

import pytest
import restricted
from review.server import ReviewApp

WHO = {"email": "jane@firm.example", "role": "paralegal", "active": True}


def case(root, name, marked=False, people=()):
    folder = root / name
    folder.mkdir(parents=True)
    (folder / "fact_graph.json").write_text('{"facts":{}}')
    (folder / restricted.FILE).write_text(json.dumps({"marked": {"on": marked}, "people": [{"email": p} for p in people]}))
    (folder / restricted.CACHE).write_text(json.dumps({"signature": restricted._signature(folder), "law": None}))
    return folder


@pytest.fixture
def application(tmp_path, monkeypatch):
    value = ReviewApp.__new__(ReviewApp)
    value.data_root = tmp_path / "data" / "clients"
    value.data_root.mkdir(parents=True)
    value.portal_root = tmp_path / "data" / "portal"
    value.accounts = object()
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    return value


def test_batch_scope_matches_current_policy_and_refreshes_on_each_request(application):
    root = application.data_root
    case(root, "public")
    folder = case(root, "named", True, [WHO["email"]])
    case(root, "hidden", True)
    malformed = case(root, "malformed")
    (malformed / restricted.FILE).write_text("broken JSON")
    expected = restricted.scope(WHO, root)
    scope, allowed = application._current_list_scope(WHO)
    assert {key: scope[key] for key in ("hidden", "confidential")} == expected
    assert allowed("public") and allowed("named")
    assert not allowed("hidden") and not allowed("malformed") and not allowed("missing")
    (folder / restricted.FILE).write_text(json.dumps({"marked": {"on": True}, "people": []}))
    later, allowed = application._current_list_scope(WHO)
    assert "named" in later["hidden"] and not allowed("named")
    support, allowed = application._current_list_scope(WHO | {"role": "support"})
    assert not allowed("named") and support["confidential"] == set()


def test_targeted_ids_read_current_records_once_and_deny_unknown(application, monkeypatch):
    for name in ("public", "unused", "other"):
        case(application.data_root, name)
    actual, reads = restricted.record, []
    def record(folder):
        reads.append(Path(folder).name)
        return actual(folder)
    monkeypatch.setattr(restricted, "record", record)
    scope, allowed = application._current_list_scope(WHO, {"public", "missing"})
    assert reads == ["public"]
    assert allowed("public") and allowed("public") and not allowed("missing")
    assert reads == ["public"] and "missing" in scope["hidden"]


def test_exact_names_and_portal_only_fallback_are_preserved(application):
    case(application.data_root, "case-lower")
    folder = application.portal_root / "clients" / "portal-only"
    folder.mkdir(parents=True)
    (folder / "profile.json").write_text(json.dumps({"id": "portal-only", "track": "family"}))
    scope, allowed = application._current_list_scope(WHO, {"CASE-LOWER", "portal-only", "not-there"})
    assert not allowed("CASE-LOWER") and not allowed("not-there")
    assert allowed("portal-only") and "portal-only" not in scope["hidden"]
    full, allowed = application._current_list_scope(WHO)
    assert "portal-only" in full["permitted"] and allowed("portal-only")


def test_access_only_folder_without_portal_never_bypasses_original_gate(application):
    folder = application.data_root / "held-without-portal"
    folder.mkdir()
    (folder / restricted.FILE).write_text(json.dumps({"marked": {"on": True}, "people": [{"email": WHO["email"]}]}))
    assert not application.may_open(WHO, folder.name)
    scope, allowed = application._current_list_scope(WHO)
    assert not allowed(folder.name) and folder.name in scope["hidden"]
    long_name = "x" * 201
    case(application.data_root, long_name)
    scope, allowed = application._current_list_scope(WHO)
    assert not application.may_open(WHO, long_name) and not allowed(long_name)


@pytest.mark.parametrize("candidate", [{"case": None}, {"case": []}, {"case": {}}, {}, "bad"])
def test_malformed_waiting_candidates_fail_closed(candidate):
    assert ReviewApp._notice_closed({"form": "I-485", "candidates": [candidate]}, {"hidden": set(), "confidential": set()})
    assert not ReviewApp._notice_closed({"form": "I-485", "candidates": []}, {"hidden": set(), "confidential": set()})


def test_signature_stat_reduction_keeps_all_source_and_packet_times(tmp_path):
    folder = case(tmp_path, "fixture")
    for name in restricted._SOURCES:
        (folder / name).write_text("{}")
    (folder / "packet-one.json").write_text("{}")
    before = [restricted.CACHE_VERSION] + [(folder / n).stat().st_mtime_ns if (folder / n).exists() else 0 for n in restricted._SOURCES]
    before.append(max((p.stat().st_mtime_ns for p in folder.glob("packet*.json")), default=0))
    assert restricted._signature(folder) == before
    (folder / "status.json").unlink()
    assert restricted._signature(folder)[1] == 0


def test_permission_errors_remain_unknown_and_never_grant_a_warm_cached_case(application, monkeypatch):
    folder = case(application.data_root, "blocked")
    actual_stat = Path.stat
    def stat(path, *args, **kwargs):
        if path == folder / "fact_graph_raw.json":
            raise PermissionError("Fictional source cannot be inspected")
        return actual_stat(path, *args, **kwargs)
    monkeypatch.setattr(Path, "stat", stat)
    assert restricted.law(folder) == restricted.UNKNOWN
    _, allowed = application._current_list_scope(WHO)
    assert not allowed("blocked")
    monkeypatch.setattr(Path, "stat", actual_stat)
    actual_read = Path.read_text
    def read(path, *args, **kwargs):
        if path == folder / restricted.FILE:
            raise PermissionError("Fictional current access record cannot be read")
        return actual_read(path, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", read)
    assert restricted.record(folder)["people"] == []
    _, allowed = application._current_list_scope(WHO)
    assert not allowed("blocked")


def test_three_hundred_current_cases_have_one_inventory_and_bounded_reads(application, monkeypatch):
    for index in range(300):
        case(application.data_root, f"fictional-{index}", marked=index % 10 == 0)
    counts = {"inventory": 0, "record": 0}
    actual_list, actual_record = restricted.os.listdir, restricted.record
    def inventory(path):
        counts["inventory"] += 1
        return actual_list(path)
    def record(folder):
        counts["record"] += 1
        return actual_record(folder)
    monkeypatch.setattr(restricted.os, "listdir", inventory)
    monkeypatch.setattr(restricted, "record", record)
    started = time.monotonic()
    scope, allowed = application._current_list_scope(WHO)
    assert sum(allowed(f"fictional-{i}") for i in range(300)) == 270
    elapsed = time.monotonic() - started
    assert len(scope["hidden"]) == 30 and counts == {"inventory": 1, "record": 300}
    assert elapsed < 1.5, f"300 fresh current policy decisions took {elapsed:.3f}s"
