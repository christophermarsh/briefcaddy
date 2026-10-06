"""Keeping a derived file up to date without looking at every case.

The search index (src/index.py), the query layer (src/query.py) and the review app's lists (src/review/roster.py) are copies of what the case folders hold. At a
few cases a screen can check every case's files before it answers; at 1,800 on a Windows disk that is twenty seconds a look. So a screen only pokes the follower:
it never waits. The follower asks the event ledger (src/events.py) which cases changed since the last look and has the copy rebuild just those; a walk over every
case (for a change nobody recorded: someone copying a file in by hand) runs in the background, at most every I485_WALK_EVERY seconds (600), and never more often than
ten times the time the last one took, so a slow disk is not kept busy. Tests set I485_WALK_EVERY=0 and the old way: every ask looks at everything, then answers.
"""

from __future__ import annotations

import os
import sys
import threading
import time
from pathlib import Path
from typing import Callable

import events


def walk_every() -> float:
    """Seconds between walks over every case (I485_WALK_EVERY); 0 means every ask looks at everything first, as the tests do."""
    try:
        return max(0.0, float(os.environ.get("I485_WALK_EVERY", "600")))
    except ValueError:
        return 600.0


class Follower:
    """One derived file's keeper. full(): bring everything up to date (the walk). some(cases): rebuild these cases only. Both run in the follower's own thread."""

    def __init__(self, base: Callable[[], Path], full: Callable[[], None], some: Callable[[set[str]], None], name: str = "keepup", age: Callable[[], float | None] | None = None):
        """age(): how many seconds ago the file was last written (None: there is none). A file the night has just built is not walked over again at the first look: the ledger is
        followed from now and the walk is due when the file is as old as a walk's interval."""
        self._base, self._full, self._some, self.name, self._age = base, full, some, name, age
        self.tail: events.Tail | None = None
        self.last_walk = 0.0
        self.took = 0.0
        self.thread: threading.Thread | None = None
        self.lock = threading.Lock()
        self.walks = 0  # how many full walks have run: a test checks a screen did not cause one

    def poke(self) -> None:
        """A screen is about to read the file: bring it up to date in the background, if it is not being now. Never waits."""
        with self.lock:
            if self.thread is not None and self.thread.is_alive():
                return
            self.thread = threading.Thread(target=self._run, name=self.name, daemon=True)
            self.thread.start()

    def _ledger_quiet(self, age: float) -> bool:
        """The event ledger has no row newer than the file: what the file was built from is what the ledger knows (a change recorded since is a change the file may not have)."""
        try:
            newest = max((p.stat().st_mtime for p in events.files(self._base())), default=0.0)
        except OSError:
            return False
        return newest <= time.time() - age

    def wait(self, seconds: float = 120.0) -> None:
        """Until the running look has finished (a test, or a caller that needs the file as it will be)."""
        t = self.thread
        if t is not None:
            t.join(seconds)

    def _run(self) -> None:
        try:
            if self.tail is None and self.last_walk == 0.0 and self._age is not None:
                old = self._age()
                if old is not None and old < walk_every() and self._ledger_quiet(old):  # built a few minutes ago (the night's run, or the last start), and nothing recorded since: nothing to walk over for
                    tail = events.Tail(self._base())
                    tail.start()
                    self.last_walk, self.tail = time.monotonic() - max(0.0, old), tail
                    return
            cases = self.tail.take() if self.tail is not None else None
            due = time.monotonic() - self.last_walk > max(walk_every(), 10 * self.took)
            if cases is None or due:
                tail = events.Tail(self._base())
                tail.start()  # the position before the walk: what changes while it runs is taken next time
                started = time.monotonic()
                self._full()
                self.took = time.monotonic() - started
                self.last_walk, self.tail = time.monotonic(), tail
                self.walks += 1
            elif cases:
                self._some(cases)
        except Exception as exc:  # noqa: BLE001 -- the screen reads the file as it is; the next poke tries again
            sys.stderr.write(f"{self.name} not brought up to date ({type(exc).__name__})\n")
