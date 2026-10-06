"""The overnight run (src/overnight.py): who runs, resuming, and the report."""

import json
import os
import time
from datetime import datetime

import pytest

import overnight
import schema_path


def _folders(tmp_path, names, empty=()):
    root = tmp_path / "clients"
    for name in names:
        (root / name / "source").mkdir(parents=True)
        if name not in empty:
            (root / name / "source" / "passport.pdf").write_bytes(b"%PDF-1.4 sample")
    return root


def _runner(fail=()):
    calls = []

    def run_one(name, source, out):
        calls.append(name)
        if name in fail:
            return {"status": "failed", "client": name, "error": "ValueError: unreadable"}
        os.makedirs(out, exist_ok=True)
        open(os.path.join(out, "fact_graph.json"), "w").write("{}")
        return {"status": "done", "client": name, "counts": {"blocking": 1 if name == "c2" else 0, "review": 5}, "seconds": 2.0,
                "errors": {}, "documents": 1}

    run_one.calls = calls
    return run_one


def _run(tmp_path, root, runner, **kw):
    lines = []
    out = overnight.run(root, tmp_path / "out", tmp_path / "data", runner=runner, log=lines.append, **kw)
    return out, lines


def test_only_new_changed_and_failed_clients_run(tmp_path):
    root = _folders(tmp_path, ["c1", "c2", "c3", "empty"], empty=["empty"])
    first = _runner(fail=["c3"])
    out, _ = _run(tmp_path, root, first)
    assert sorted(first.calls) == ["c1", "c2", "c3"] and out["skipped"]["no_documents"] == 1
    state = json.loads((tmp_path / "data" / "batch_state.json").read_text())
    assert state["c1"]["status"] == "done" and state["c3"]["status"] == "failed"

    # next night: only the failure runs again
    second = _runner()
    out, _ = _run(tmp_path, root, second)
    assert second.calls == ["c3"] and out["skipped"]["unchanged"] == 2

    # a new document for c1: it runs; everything else is unchanged
    time.sleep(0.01)
    (root / "c1" / "source" / "i94.pdf").write_bytes(b"%PDF-1.4 new")
    third = _runner()
    _run(tmp_path, root, third)
    assert third.calls == ["c1"]
    assert json.loads((tmp_path / "data" / "batch_state.json").read_text())["c1"]["why"] == "documents changed"

    every = _runner()
    _run(tmp_path, root, every, every=True)
    assert sorted(every.calls) == ["c1", "c2", "c3"]


def test_the_morning_report_and_progress(tmp_path):
    root = _folders(tmp_path, ["c1", "c2", "c3"])
    _run(tmp_path, root, _runner(fail=["c3"]), workers=2)
    report = (tmp_path / "data" / "batch_report.txt").read_text()
    assert "processed: 2   failed: 1" in report
    assert "with a blocking issue for the attorney: 1" in report and "c2: 1 blocking" in report
    assert "c3: ValueError: unreadable" in report
    progress = overnight.progress(tmp_path / "data")
    assert progress["done"] == 2 and progress["failed"] == 1 and progress["finished_at"] and progress["running"] == []
    log = [json.loads(line) for line in (tmp_path / "data" / "batch_log.jsonl").read_text().splitlines()]
    assert log[-1]["done"] == 2


def test_a_dry_run_estimates_from_past_runs(tmp_path):
    root = _folders(tmp_path, ["c1", "c2", "c3", "c4"])
    _run(tmp_path, root, _runner(), only=["c1", "c2"])
    (root / "c1" / "source" / "new.pdf").write_bytes(b"%PDF-1.4 x")
    out, lines = _run(tmp_path, root, _runner(), dry_run=True, workers=2)
    assert [n for n, _ in out["todo"]] == ["c1", "c3", "c4"]
    assert out["estimate_seconds"] == pytest.approx(2.0 * 3 / 2)  # the 2 s average of real runs, 2 at a time
    assert "1 documents changed" in lines[0] and "2 new" in lines[0]
    assert not (tmp_path / "data" / "batch_progress.json").read_text().count('"total": 3')  # nothing ran


def test_one_run_at_a_time(tmp_path):
    root = _folders(tmp_path, ["c1"])
    (tmp_path / "data").mkdir()
    import oslock

    fd = oslock.open_lock_file(tmp_path / "data" / "batch.lock")  # a run that is alive holds the system's lock (src/oslock.py)
    assert oslock.try_lock(fd)
    with pytest.raises(SystemExit):
        _run(tmp_path, root, _runner())
    os.close(fd)  # the run ended, or died: the system let go of its lock, and there is nothing to take over or delete
    runner = _runner()
    _run(tmp_path, root, runner)
    assert runner.calls == ["c1"] and not oslock.held_by_someone(tmp_path / "data" / "batch.lock")


def test_until_is_the_next_time_on_the_clock():
    stop = overnight._until("06:30")
    now = datetime.now().astimezone()
    assert stop > now and (stop - now).total_seconds() <= 24 * 3600 and (stop.hour, stop.minute) == (6, 30)


def test_the_pipeline_version_changes_with_the_code(tmp_path):
    (tmp_path / "src").mkdir()
    schema_path.schemas_in(tmp_path).mkdir()
    (tmp_path / "src" / "a.py").write_text("x = 1")
    before = overnight.pipeline_version(tmp_path)
    (tmp_path / "src" / "a.py").write_text("x = 2")
    assert overnight.pipeline_version(tmp_path) != before


def test_clients_processed_before_the_overnight_run_are_not_redone(tmp_path):
    root = _folders(tmp_path, ["old", "stale"])
    time.sleep(0.01)
    for name in ("old", "stale"):  # bundles written by process_clients.py after the documents arrived
        (tmp_path / "out" / name).mkdir(parents=True)
        (tmp_path / "out" / name / "fact_graph.json").write_text("{}")
        (tmp_path / "out" / name / "meta.json").write_text(json.dumps({"processed_at": "2026-09-30T12:00:00+00:00"}))
    time.sleep(0.01)
    (root / "stale" / "source" / "added later.pdf").write_bytes(b"%PDF-1.4 later")
    runner = _runner()
    out, _ = _run(tmp_path, root, runner)
    assert runner.calls == ["stale"] and out["skipped"]["unchanged"] == 1
    state = json.loads((tmp_path / "data" / "batch_state.json").read_text())
    assert state["old"]["why"] == "processed earlier" and out["older_version"] == 1  # counted for a later --all
