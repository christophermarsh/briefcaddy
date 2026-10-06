"""One small file lock, the operating system's own, for everything in the product that must be held by one process at a time.
(Named oslock, not filelock: `filelock` is the third-party package the translation libraries import, and a module of that name in src/ would hide it.)

fcntl.flock on Linux and macOS, msvcrt.locking on Windows. The system owns the lock, so:

  - a process that crashes (or is killed, or whose computer is shut down) lets go of it at once: no stale lock, no age rule, no clock, no pid to probe;
  - nobody can take it from a live holder, however long it holds it;
  - a process that does not hold it cannot release it: unlock() on a file this process did not lock changes nothing (the holder keeps it);
  - a pid that is reused after a restart blocks nobody, because no pid is recorded.

Used by the job queue (src/jobs.py: the worker, a case's writes, the GPU's turn), the overnight run (src/overnight.py: one run at a time) and
the Clio connector (src/connectors/clio.py FileLock: one Clio step at a time, the vault, the state files). A lock is one byte of
a file; the file stays (it holds nothing). The Windows branch (msvcrt) was written from the documented calls and was not run on Windows by its author.
"""

from __future__ import annotations

import os
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Iterator

try:
    import fcntl
except ImportError:  # Windows
    fcntl = None
    import msvcrt


class LockBusy(TimeoutError):
    """The lock is held by another process and was not given up within the time asked."""


def try_lock(fd: int) -> bool:
    """Takes the lock on this open file if nobody else holds it. True when this file now holds it. (A second open of the same file in this process is "somebody else".)"""
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
        else:
            os.lseek(fd, 0, os.SEEK_SET)  # msvcrt locks from the file's position: always the first byte (a holder that wrote to the file moved it)
            msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
        return True
    except OSError:
        return False


def unlock(fd: int) -> None:
    """Lets go of the lock this open file holds. A file that does not hold it: nothing happens (the real holder keeps it)."""
    try:
        if fcntl:
            fcntl.flock(fd, fcntl.LOCK_UN)
        else:
            os.lseek(fd, 0, os.SEEK_SET)
            msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    except OSError:
        pass


def open_lock_file(path: str | Path) -> int:
    """The file a lock lives in, made (with its folder) when it is not there, readable by its owner only."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    return os.open(path, os.O_CREAT | os.O_RDWR, 0o600)


def held_by_someone(path: str | Path) -> bool:
    """Whether any process holds the lock now (the file's lock is taken for a moment and let go; nothing is held by the answer)."""
    if not Path(path).exists():
        return False
    fd = os.open(path, os.O_RDWR)
    try:
        if try_lock(fd):
            unlock(fd)
            return False
        return True
    finally:
        os.close(fd)


@contextmanager
def locked(path: str | Path, timeout: float | None = 0.0, poll: float = 0.05) -> Iterator[None]:
    """Holds the lock for the block. timeout: seconds to wait for another process to let go (None: as long as it takes; 0: do not wait). Raises LockBusy when it is not got."""
    fd = open_lock_file(path)
    try:
        end = None if timeout is None else time.monotonic() + timeout
        while not try_lock(fd):
            if end is not None and time.monotonic() >= end:
                raise LockBusy(str(path))
            time.sleep(poll)
        try:
            yield
        finally:
            unlock(fd)
    finally:
        os.close(fd)
