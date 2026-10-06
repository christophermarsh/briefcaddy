# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""The row saying who opened a file is written before the file is sent (brief J2, the finding carried from wave G): a send that dies part way, the
process killed between the row and the bytes, a browser that drops the connection, never loses the row. The answer is made first, so a refusal or a
file that is not there writes nothing; a row that cannot be written stops the file (503), never the other way round.

Everyone here is made up (tests/test_restricted.py's world)."""

from __future__ import annotations

import http.client
import http.server
import json
import urllib.parse

import pytest

from test_restricted import app, server, sign_in, world  # noqa: F401 -- the made-up firm


class Killed(BaseException):
    """The process dying between the row and the bytes: nothing in the handler catches it (not an Exception)."""


def rows(app):
    return [json.loads(x) for x in app.views_log.read_text(encoding="utf-8").splitlines()] if app.views_log.exists() else []


def get(base, path, cookie):
    u = urllib.parse.urlparse(base)
    conn = http.client.HTTPConnection(u.hostname, u.port, timeout=20)
    try:
        conn.request("GET", path, headers={"Cookie": cookie})
        r = conn.getresponse()
        return r.status, r.read()
    except (http.client.RemoteDisconnected, ConnectionResetError, http.client.IncompleteRead):
        return None, b""  # the send died
    finally:
        conn.close()


@pytest.mark.parametrize("path,kind", [("/api/items?client=case-ana", "case"), ("/api/documents?client=case-ana", "documents"),
                                       ("/api/answers?client=pilot-nova", "answers")])
def test_a_send_killed_before_a_byte_went_out_still_leaves_the_row(server, app, monkeypatch, path, kind):
    sam = sign_in(server, "sam@firm.example")
    before = len(rows(app))

    def die(self):
        raise Killed("the process stopped here")

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "end_headers", die)
    monkeypatch.setattr("threading.excepthook", lambda args: None)  # the dying thread's report is expected
    status, body = get(server, path, sam)
    assert status is None and body == b""  # nothing reached the reader ...
    added = rows(app)[before:]
    assert [(r["client"], r["kind"], r["email"]) for r in added] == [(path.split("client=")[1], kind, "sam@firm.example")]  # ... and the row is there


def test_a_streamed_download_that_dies_part_way_still_leaves_the_row(server, app, monkeypatch, tmp_path):
    sam = sign_in(server, "sam@firm.example")
    made = tmp_path / "client-file.zip"
    made.write_bytes(b"PK" + b"\0" * (3 << 20))
    monkeypatch.setattr(app, "case_file", lambda client, user: made)
    real_write = None

    class Dying:
        def __init__(self, inner):
            self.inner, self.n = inner, 0

        def write(self, data):
            self.n += 1
            if self.n > 1:  # the headers went out, then the process stopped mid-file
                raise Killed("the process stopped mid-file")
            return self.inner.write(data)

        def __getattr__(self, name):
            return getattr(self.inner, name)

    real_setup = http.server.BaseHTTPRequestHandler.setup

    def setup(self):
        real_setup(self)
        self.wfile = Dying(self.wfile)

    monkeypatch.setattr(http.server.BaseHTTPRequestHandler, "setup", setup)
    monkeypatch.setattr("threading.excepthook", lambda args: None)
    assert real_write is None
    status, body = get(server, "/api/case-file.zip?client=case-ana", sam)
    assert status is None or len(body) < made.stat().st_size  # the file never arrived whole
    assert [(r["kind"], r["file"]) for r in rows(app) if r["client"] == "case-ana"][-1] == ("case_file", "client-file.zip")


def test_a_refusal_or_a_missing_file_writes_no_row(server, app):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    before = len(rows(app))
    assert get(server, "/api/items?client=case-rosa", jane)[0] == 404  # a restricted case, for someone not named on it
    assert get(server, "/api/file?client=case-ana&doc=not-there.pdf", sam)[0] == 404
    assert get(server, "/api/packet.pdf?client=case-ana", sam)[0] == 404  # no packet built yet
    assert rows(app)[before:] == []


def test_when_the_row_cannot_be_written_the_file_is_not_sent(server, app, monkeypatch):
    sam = sign_in(server, "sam@firm.example")

    def full_disk(*a, **k):
        raise OSError(28, "No space left on device")

    monkeypatch.setattr(app, "viewed", full_disk)
    status, body = get(server, "/api/items?client=case-ana", sam)
    assert status == 503 and "could not be written" in json.loads(body)["error"] and b"Ana" not in body
