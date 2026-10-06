"""A made-up firm of some hundreds of cases (tools/make_world.py), the review app over it as installed (the lists read their own copy of every case, kept up to date from
the event ledger: I485_WALK_EVERY above 0), and a person signed in as the world's attorney and as its paralegal. Used by tests/test_scale.py (the budgets, restricted cases at
size, the lists staying right as cases change) and tests/test_jobs.py. Everyone in the world is made up."""

from __future__ import annotations

import json
import sys
import threading
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))
import schema_path  # noqa: E402

import make_world  # noqa: E402


class World:
    def __init__(self, root: Path, manifest: dict, patch: pytest.MonkeyPatch):
        self.root, self.manifest, self.patch = root, manifest, patch
        self.clients, self.portal, self.data = Path(manifest["clients"]), Path(manifest["portal"]), Path(manifest["data"])
        self.app = None
        self.httpd = None
        self.base = ""
        self.cookies: dict[str, str] = {}

    # -- the app ----------------------------------------------------------------------------------------------

    def start(self) -> "World":
        from review.auth import COOKIE, Accounts
        from review.server import ReviewApp, make_handler, serve

        accounts = Accounts(Path(self.manifest["users"]))
        self.app = ReviewApp(self.clients, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), schema_path.path("law", "policy_sijs"), self.portal,
                             accounts=accounts)
        self.httpd = serve(self.app, 0)
        port = self.httpd.server_address[1]
        self.httpd.RequestHandlerClass = make_handler(self.app, port)
        threading.Thread(target=self.httpd.serve_forever, daemon=True).start()
        self.base = f"http://127.0.0.1:{port}"
        for role, person in self.manifest["people"].items():
            self.cookies[role] = f"{COOKIE}={accounts.session_for(person['email'], 'test')[0]}"
        return self

    def stop(self) -> None:
        if self.httpd is not None:
            self.httpd.shutdown()
        self.patch.undo()

    # -- asking -----------------------------------------------------------------------------------------------

    def call(self, path: str, role: str = "attorney", body: dict | None = None) -> tuple[float, int, dict]:
        """(seconds, status, the answer as JSON when it is) of one request as the world's attorney or paralegal."""
        headers = {"Cookie": self.cookies[role], "X-Review-App": "1"} | ({"Content-Type": "application/json"} if body is not None else {})
        req = urllib.request.Request(self.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
        started = time.perf_counter()
        try:
            with urllib.request.urlopen(req, timeout=600) as r:
                status, raw = r.status, r.read()
        except urllib.error.HTTPError as e:
            status, raw = e.code, e.read()
        seconds = time.perf_counter() - started
        try:
            return seconds, status, json.loads(raw)
        except ValueError:
            return seconds, status, {"raw": raw.decode("utf-8", "replace")}

    def get(self, path: str, role: str = "attorney") -> dict:
        seconds, status, out = self.call(path, role)
        assert status == 200, (path, role, status, out)
        return out

    def every_row(self, role: str, **filters) -> tuple[list[dict], dict]:
        """Every row of All clients for a person, page after page (the way a person pages through), and the last page's answer."""
        rows, page = [], 1
        while True:
            answer = self.get("/api/overview?" + "&".join(f"{k}={v}" for k, v in {"page": page, **filters}.items()), role)
            rows += answer["clients"]
            if page >= answer["pages"]:
                return rows, answer
            page += 1

    def person(self, role: str) -> dict:
        p = self.manifest["people"][role]
        return {"email": p["email"], "name": p["name"], "role": role}


def build(tmp_path: Path, cases: int, *, staff: int = 6, walk: str = "600", warm: bool = True, **kw) -> World:
    """The world and its app. walk: I485_WALK_EVERY (600 as installed). warm: the nightly run's step (every case's row, in a pool of processes) has run, as it has in the morning."""
    patch = pytest.MonkeyPatch()
    manifest = make_world.build(tmp_path / "w", cases=cases, views=kw.pop("views", 2000), ledger=kw.pop("ledger", 6000), restricted=max(1, cases * 15 // 100), staff=staff,
                                sources=kw.pop("sources", 0), log=lambda *_: None, **kw)
    for key, value in manifest["env"].items():
        patch.setenv(key, value)
    patch.setenv("I485_ROSTER", str(Path(manifest["data"]) / "roster.json"))  # the lists' saved copy lives in the world, not in the tests' shared scratch folder
    patch.setenv("I485_WALK_EVERY", walk)
    patch.setenv("I485_QUERY_REFRESH", "60")  # as installed: the query layer is brought up to date in the background, once a minute at most
    patch.setenv("I485_JOBS_WORKER", "0")  # the tests run the worker themselves
    world = World(tmp_path / "w", manifest, patch)
    if warm:
        from review import roster

        roster.warm(world.clients, world.portal, workers=8)
    return world.start()
