"""Whether the process a worker watches is still there (src/jobs.py _pid_alive): one branch for Windows, where signal 0 is Ctrl+C and must never be sent, one for the rest.
Both run here on any computer, with a stand-in for the system's own calls; the Windows branch was not run on Windows."""

from __future__ import annotations

import os
import subprocess
import sys

import pytest

import jobs


class FakeKernel32:
    """Stands for kernel32: a table of process ids to exit codes (STILL_ACTIVE is 259), and what the last error would be for an id that is not in it."""

    def __init__(self, table: dict[int, int], denied: set[int] = frozenset()):
        self.table, self.denied, self.last_error, self.closed = table, set(denied), 0, []

        def open_process(access, inherit, pid):
            assert access == 0x1000 and not inherit  # PROCESS_QUERY_LIMITED_INFORMATION only: it asks for nothing it could misuse
            if pid in self.table:
                return 1000 + pid
            self.last_error = 5 if pid in self.denied else 87  # access denied (it exists), or invalid parameter (it does not)
            return 0

        def exit_code(handle, ref):
            ref._obj.value = self.table[handle - 1000]
            return 1

        self.OpenProcess, self.GetExitCodeProcess = open_process, exit_code
        self.CloseHandle = lambda handle: self.closed.append(handle)


def test_windows_asks_the_system_and_never_sends_a_signal(monkeypatch):
    def never(*a):
        raise AssertionError("os.kill must not be called on Windows: signal 0 is Ctrl+C there")

    monkeypatch.setattr(os, "kill", never)
    k = FakeKernel32({10: 259, 11: 0, 12: 1}, denied={13})
    assert jobs._pid_alive(10, "win32", k) is True  # running
    assert jobs._pid_alive(11, "win32", k) is False  # exited (code 0): its id may still be held by a handle
    assert jobs._pid_alive(12, "win32", k) is False
    assert jobs._pid_alive(99, "win32", k) is False  # no such process
    assert jobs._pid_alive(13, "win32", k) is True  # one this account may not look at exists
    assert k.closed == [1010, 1011, 1012]  # every handle opened was closed


def test_elsewhere_signal_zero_is_the_check(monkeypatch):
    assert jobs._pid_alive(os.getpid(), "linux") is True
    child = subprocess.Popen([sys.executable, "-c", "pass"])
    child.wait()
    assert jobs._pid_alive(child.pid, "linux") is False
    calls = []

    def kill(pid, sig):
        calls.append((pid, sig))
        raise PermissionError

    monkeypatch.setattr(os, "kill", kill)
    assert jobs._pid_alive(1, "darwin") is True and calls == [(1, 0)]  # someone else's process: it is there


@pytest.mark.skipif(not sys.platform.startswith("win"), reason="the real kernel32 exists only on Windows (the builder could not run this)")
def test_on_windows_the_real_system_calls_work():
    assert jobs._pid_alive(os.getpid()) is True
