"""The product's one file lock (src/oslock.py): the operating system's own, so a crash lets go of it, nobody takes it from a live holder, and a process that does not
hold it cannot release it. Two real processes contend here. The Windows branch (msvcrt.locking) was not run on Windows."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from pathlib import Path

import pytest

import oslock

SRC = str(Path(__file__).resolve().parent.parent / "src")

HOLDER = """
import sys, time
sys.path.insert(0, {src!r})
import oslock
fd = oslock.open_lock_file({path!r})
assert oslock.try_lock(fd)
print("held", flush=True)
time.sleep(60)
"""


def holder(path: Path) -> subprocess.Popen:
    p = subprocess.Popen([sys.executable, "-c", HOLDER.format(src=SRC, path=str(path))], stdout=subprocess.PIPE, text=True)
    assert p.stdout.readline().strip() == "held"
    return p


def test_two_processes_contend_and_the_holder_keeps_it(tmp_path):
    path = tmp_path / "x.lock"
    other = holder(path)
    try:
        with pytest.raises(oslock.LockBusy):
            with oslock.locked(path, timeout=0.3):
                pass
        assert oslock.held_by_someone(path)
    finally:
        other.kill()
        other.wait()


def test_a_holder_that_crashes_lets_go_at_once(tmp_path):
    path = tmp_path / "x.lock"
    other = holder(path)
    assert oslock.held_by_someone(path)
    other.send_signal(signal.SIGKILL if hasattr(signal, "SIGKILL") else signal.SIGTERM)  # no cleanup runs: nothing was written, nothing is left to take over
    other.wait()
    assert not oslock.held_by_someone(path)
    with oslock.locked(path, timeout=0):  # taken at once, with no age to wait out and no pid to look at
        assert oslock.held_by_someone(path)


def test_a_process_that_does_not_hold_it_cannot_release_it(tmp_path):
    path = tmp_path / "x.lock"
    other = holder(path)
    try:
        fd = oslock.open_lock_file(path)
        try:
            oslock.unlock(fd)  # this open file never held it: a no-op
            assert not oslock.try_lock(fd) and oslock.held_by_someone(path)  # the holder still has it
        finally:
            os.close(fd)
    finally:
        other.kill()
        other.wait()


def test_a_lock_is_not_a_clock_it_is_held_for_as_long_as_the_holder_lives(tmp_path):
    path = tmp_path / "x.lock"
    os.utime(path.parent, (time.time() - 10**6, time.time() - 10**6))
    other = holder(path)
    try:
        old = time.time() - 10**6
        os.utime(path, (old, old))  # a lock file a month old, held by a live process: no age rule takes it
        assert oslock.held_by_someone(path)
    finally:
        other.kill()
        other.wait()


def test_the_overnight_runs_lock_is_the_systems_too(tmp_path):
    import overnight

    path = tmp_path / "batch.lock"
    with overnight.Lock(path):
        with pytest.raises(SystemExit, match="Another run is in progress"):
            with overnight.Lock(path):
                pass
        assert oslock.held_by_someone(path)
    assert not oslock.held_by_someone(path)
    with overnight.Lock(path):  # let go: the next run takes it, nothing to delete
        pass
    # a run that died without finishing: the lock is free and its progress file says it did not finish
    import json

    (tmp_path / "batch_progress.json").write_text(json.dumps({"pid": 1, "total": 3, "done": 1, "running": ["c1"]}), encoding="utf-8")
    assert overnight.progress(tmp_path)["stopped"] == "interrupted"
    with overnight.Lock(path):  # a run that is alive: its progress is its own
        assert "stopped" not in overnight.progress(tmp_path)


# -- the Windows branch, with a stand-in for msvcrt (brief J2 verification S3) ------------------------------------------------------------


def test_the_windows_branch_locks_and_unlocks_the_first_byte_after_the_holder_wrote_to_the_file(tmp_path, monkeypatch):
    """msvcrt.locking locks from the file's current position, and a holder that wrote to the file moved it: oslock seeks to byte 0 first, for the
    lock and for the unlock (a lock and an unlock at different places would never meet). A stand-in records where each call happened; the test fails
    if either seek is taken out."""
    calls = []

    class FakeMsvcrt:
        LK_NBLCK, LK_UNLCK = 2, 0

        @staticmethod
        def locking(fd, mode, nbytes):
            calls.append((mode, os.lseek(fd, 0, os.SEEK_CUR), nbytes))

    monkeypatch.setattr(oslock, "fcntl", None)
    monkeypatch.setattr(oslock, "msvcrt", FakeMsvcrt, raising=False)
    fd = oslock.open_lock_file(tmp_path / "step.lock")
    try:
        os.write(fd, b"12345 2026-10-04T09:00:00\n")  # the holder's note: the position is no longer 0
        assert oslock.try_lock(fd)
        os.write(fd, b"more")
        oslock.unlock(fd)
    finally:
        os.close(fd)
    assert calls == [(FakeMsvcrt.LK_NBLCK, 0, 1), (FakeMsvcrt.LK_UNLCK, 0, 1)]
