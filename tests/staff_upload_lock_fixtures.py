"""An actual separate reader thread holds the existing case OS lock."""
from contextlib import contextmanager
import threading

import jobs


@contextmanager
def held_reader(world):
    ready, release = threading.Event(), threading.Event()
    errors = []
    def reader():
        try:
            with jobs.case_lock(world["scope"].queue, world["client"]):
                ready.set()
                if not release.wait(30):
                    raise AssertionError("synthetic reader was not released")
        except BaseException as error:
            errors.append(error)
            ready.set()
    thread = threading.Thread(target=reader, daemon=True)
    thread.start()
    try:
        assert ready.wait(5) and not errors, errors
        yield release
    finally:
        release.set()
        thread.join(5)
        assert not thread.is_alive() and not errors, errors
