"""Support access (brief R3, src/support.py): the provider's support person, let in by an attorney for hours, read-only, the clients masked.

The test that matters walks every GET route of tests/test_restricted.py's sets as support on the made-up world (tools/make_world.py): in a masked session
none of the world's client values (names, case folder names, A-Numbers, receipt numbers, dates of birth, addresses, phones, e-mails) is in any answer, and
in a plain session the same walk shows them (the mask is doing the work). A second walk plants a made-up value in one record of every kind the catalog
holds (src/records.py) and walks again. Every POST is refused but signing out, a restricted case is absent even in a plain session, files are refused in a
masked session, the session ends by itself at its hour, and every step is in the ledger under the support person's name. Everyone in it is made up."""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
from collections import Counter
from datetime import timedelta
from pathlib import Path
from urllib.parse import quote

import pytest

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tests"))
import scale_world  # noqa: E402
from test_restricted import LISTING, NEITHER_POST, QUERY, _routes  # noqa: E402

SUPPORT = ("help@provider.example", "Sol Supportexemplo")
# made-up values of every kind a client has, put in each case's facts (the world's own are mostly "MADE UP ..." words and random digits)
PLANT = {"applicant.a_number": "A0{n}9876543", "applicant.receipt_number": "IOE09{n}1234567", "applicant.date_of_birth": "2009-0{m}-1{m}",
         "applicant.physical_address.street": "{n}7 Canaryexemplo Street", "applicant.phone": "617-555-01{n}{n}", "applicant.email": "canary{n}@client.example"}


def ask(w, path: str, cookie: str, body: dict | None = None) -> tuple[int, str, dict]:
    headers = {"Cookie": cookie, "X-Review-App": "1"} | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(w.base + path, data=json.dumps(body).encode() if body is not None else None, headers=headers,
                                 method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req, timeout=120) as r:
            status, raw, hdrs = r.status, r.read(), dict(r.headers)
    except urllib.error.HTTPError as e:
        status, raw, hdrs = e.code, e.read(), dict(e.headers)
    return status, raw.decode("utf-8", "replace"), hdrs


@pytest.fixture(scope="module")
def world(tmp_path_factory):
    w = scale_world.build(tmp_path_factory.mktemp("support"), 10, staff=4, warm=False)
    w.canaries = {}  # case id -> its client values
    for n, case in enumerate(w.manifest["case_ids"]):
        d = w.clients / case
        graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
        values = {case}
        for key, pattern in PLANT.items():
            value = pattern.format(n=n % 10, m=1 + n % 8)
            graph["facts"][key] = {**next(iter(graph["facts"].values())), "value": value, "key": key}
            values.add(value)
        for key, fact in graph["facts"].items():
            v = str(fact.get("value") or "")
            if key.endswith(("given_name", "family_name")) or re.fullmatch(r"\d{9}|\d{4}-\d{2}-\d{2}", v):
                values.add(v)
        (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
        profile = w.portal / "clients" / case / "profile.json"
        if profile.exists():
            p = json.loads(profile.read_text(encoding="utf-8"))
            values |= {p["name"], p["email"], " ".join(p["name"].split()[-2:])}
        w.canaries[case] = {v for v in values if len(v) >= 4}
    w.restricted = set(w.manifest["restricted_ids"])
    w.app.roster.touch_all()
    yield w
    w.stop()


def let_in(w, plain: bool, hours: int = 1, email: str = SUPPORT[0]) -> str:
    """The attorney lets support in (Settings, Let support in); support signs in with the code. Returns support's cookie."""
    status, text, _ = ask(w, "/api/support", w.cookies["attorney"], {"action": "let_in", "email": email, "name": SUPPORT[1], "hours": hours, "plain": plain})
    assert status == 200, text
    code = json.loads(text)["code"]
    req = urllib.request.Request(w.base + "/api/login", data=json.dumps({"email": email, "password": code}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1"}, method="POST")
    with urllib.request.urlopen(req) as r:
        user = json.loads(r.read())["user"]
        assert user["role"] == "support" and not user.get("second_factor")
        return r.headers.get("Set-Cookie").split(";")[0]


def walk(w, cookie: str, case_names: list[str]) -> dict[str, tuple[int, str]]:
    """Every GET route of tests/test_restricted.py's sets (the ones the handler's own code holds), each case route for each case named."""
    from review import server as srv

    out = {}
    for route in sorted(_routes("GET")):
        if route in ("/auth/start", "/auth/callback"):
            continue
        if route in srv.CASE_GET:
            for c in case_names:
                status, text, _ = ask(w, route + "?client=" + quote(c) + QUERY, cookie)
                out[f"{route}?client={c}"] = (status, text)
        else:
            status, text, _ = ask(w, route + LISTING.get(route, ""), cookie)
            out[route] = (status, text)
    for c in case_names:
        out[f"/api/support/case?case={c}"] = ask(w, "/api/support/case?case=" + quote(c), cookie)[:2]
    return out


def leaks(answers: dict[str, tuple[int, str]], canaries: set[str]) -> list[tuple[str, str]]:
    """(where, value) for every value found as a word of its own (a short given name is no leak inside "several")."""
    found = []
    for where, (_status, text) in answers.items():
        low = text.lower()
        found += [(where, c) for c in canaries if c.lower() in low and re.search(rf"(?<![a-z0-9]){re.escape(c.lower())}(?![a-z0-9])", low)]
    return found


def test_a_masked_walk_shows_no_client_value_and_a_plain_walk_does(world):
    w = world
    every = set().union(*w.canaries.values())
    masked = let_in(w, plain=False)
    listed = json.loads(ask(w, "/api/support/cases", masked)[1])
    names = [c["case"] for c in listed["cases"]]
    assert names and all(re.fullmatch(r"Case \d+", n) for n in names) and listed["total"] == len(w.manifest["case_ids"]) - len(w.restricted)
    answers = walk(w, masked, names[:3])
    assert leaks(answers, every) == []
    statuses = Counter(s for s, _ in answers.values())
    refused = sorted(k for k, (s, _) in answers.items() if s == 403)
    shown = sorted(k for k, (s, _) in answers.items() if s == 200)
    assert statuses[200] >= 40 and refused, statuses
    # every file route is refused in a masked session, with the reason
    for route in [k for k in answers if re.search(r"\.(pdf|zip|csv|json)(\?|$)|/api/(crop|file|answers|inbox/file)\?", k)]:
        assert answers[route][0] in (403, 404), route
    if os.environ.get("SUPPORT_WALK_OUT"):  # the counts for a report
        Path(os.environ["SUPPORT_WALK_OUT"]).write_text(json.dumps({"statuses": statuses, "refused": refused, "shown": shown}, indent=1), encoding="utf-8")
    # the same walk, the attorney having ticked "show client values": the values are there (the mask did the work), the restricted case is not
    plain = let_in(w, plain=True)
    ids = [c["case"] for c in json.loads(ask(w, "/api/support/cases", plain)[1])["cases"]]
    assert set(ids) == set(w.manifest["case_ids"]) - w.restricted
    answers = walk(w, plain, ids[:3])
    shown_values = {c for _, c in leaks(answers, every)}
    for case in ids[:3]:
        assert case in shown_values and {v for v in w.canaries[case] if v.startswith(("A0", "IOE09", "canary"))} <= shown_values, case
    hidden = set().union(*(w.canaries[c] for c in w.restricted)) - set().union(*(w.canaries[c] for c in set(w.canaries) - w.restricted))
    assert leaks(answers, hidden) == []
    for case in w.restricted:  # absent: the one answer a made-up id gets
        assert ask(w, "/api/items?client=" + quote(case), plain)[0] == 404
        assert ask(w, "/api/support/case?case=" + quote(case), plain)[0] == 404


KEEP = {"id", "client_id", "doc_id", "case", "hash", "key", "kind", "type", "status", "state", "email"}  # what links records together: left as it is


def plant(data, leaves: set[str], token: str):
    """The record with the token in every text value under one of its client fields (a date or a time stays one: the screens read it as such)."""
    if isinstance(data, dict):
        return {k: token if k in leaves and isinstance(v, str) and v and not re.match(r"\d{4}-\d{2}-\d{2}", v) else plant(v, leaves, token) for k, v in data.items()}
    if isinstance(data, list):
        return [plant(x, leaves, token) for x in data]
    return data


def test_a_made_up_value_in_one_record_of_every_kind_never_reaches_a_masked_session(world):
    import records

    w = world
    first = sorted(set(w.manifest["case_ids"]) - w.restricted)[0]
    homes = {"case": w.clients / first, "portal": w.portal / "clients" / first, "firm": w.data, "logs": w.data}
    # left out, each for its reason: the accounts (support signs in through them), the ledger and its seals (a planted row breaks the chain the test reads),
    # what is not JSON (a database, scans and PDFs: files are refused in a masked session anyway), the restriction record (it would change who sees the case)
    skip = {"accounts", "events", "ledger_anchors", "ledger_redactions", "ledger_check", "learning", "files", "rebuild", "reference", "access", "portal_secrets"}
    planted = {}
    for rec in records.RECORDS:
        if rec["id"] in skip:
            continue
        token = "Plantedexemplo" + rec["id"].replace("_", "").capitalize()
        pattern = next(p for p in rec["files"] if p.endswith((".json", ".jsonl")))
        path = homes[rec["area"]] / pattern.replace("*", "planted")
        fields = [f[0] for f in rec["fields"] if f[3] == "person" and not f[0].endswith((".json", ".jsonl"))]
        keys = [f.split(".")[0] for f in fields] or ["note"]
        leaves = {re.split(r"[.\[\]]+", f.strip(".[]"))[-1] for f in fields} - KEEP
        path.parent.mkdir(parents=True, exist_ok=True)
        if path.suffix == ".jsonl":
            with open(path, "a", encoding="utf-8") as f:
                f.write(json.dumps({"at": "2026-10-01T09:00:00+00:00", **{k: token for k in keys}}) + "\n")
        else:
            # the record's own client fields where they hold text, and a note beside them, never changing a record's shape (a list stays a list)
            data = plant(json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}, leaves, token)
            if isinstance(data, dict):
                data |= {k: token for k in keys if isinstance(data.get(k), str) and k not in ("client_id", "id")} | {"note": token}
            elif isinstance(data, list):
                data.append({k: token for k in keys} | {"note": token})
            path.write_text(json.dumps(data), encoding="utf-8")
        planted[rec["id"]] = token
    assert len(planted) >= 40
    w.app.roster.touch_all()
    masked = let_in(w, plain=False)
    names = [c["case"] for c in json.loads(ask(w, "/api/support/cases", masked)[1])["cases"]]
    assert leaks(walk(w, masked, names[:2]), set(planted.values())) == []
    plain = let_in(w, plain=True)
    seen = {c for _, c in leaks(walk(w, plain, [first]), set(planted.values()))}
    assert {"PlantedexemploFacts", "PlantedexemploMeta"} <= seen, seen  # the walk reaches planted values: the masked walk's silence is the mask's


def test_support_writes_nothing_and_cannot_be_made_by_the_staff_screen(world):
    from review import server as srv

    w = world
    cookie = let_in(w, plain=True)
    case = sorted(set(w.manifest["case_ids"]) - w.restricted)[0]
    ask(w, "/api/items?client=" + quote(case), w.cookies["attorney"])  # the case read once: the app's own caches are written now, not during the walk
    caches = {"overview.json", "journey_summary.json", "confidentiality.json"}

    def records():
        return {p.name: p.read_bytes() for p in (w.clients / case).iterdir() if p.is_file() and p.name not in caches}

    before = records()
    for route in sorted((NEITHER_POST | srv.CASE_POST) - {"/api/logout"}):
        status, text, _ = ask(w, route, cookie, {"client": case, "id": "x", "case": case, "action": "confirm", "item_id": "x", "text": "x"})
        assert status == 403, (route, status, text)
    assert ask(w, "/api/support", cookie, {"action": "let_in", "email": "x@provider.example", "name": "X", "hours": 8, "plain": True})[0] == 403
    assert records() == before
    # the Staff section gives "paralegal" or "attorney" only
    status, text, _ = ask(w, "/api/staff", w.cookies["attorney"], {"action": "add", "email": "s2@provider.example", "name": "S", "role": "support"})
    assert status == 400 and "support" not in [u["role"] for u in w.app.accounts.users() if u["email"] == "s2@provider.example"]
    # a paralegal lets nobody in, and an attorney without the sign-in code set up is refused
    assert ask(w, "/api/support", w.cookies["paralegal"], {"action": "let_in", "email": "y@provider.example", "name": "Y", "hours": 1})[0] == 403
    assert ask(w, "/api/support", w.cookies["attorney"], {"action": "let_in", "email": "y@provider.example", "name": "Y", "hours": 9})[0] == 400
    # signing out is the one POST support makes
    assert ask(w, "/api/logout", cookie, {})[0] == 200 and ask(w, "/api/support/health", cookie)[0] == 401


def test_files_are_refused_masked_and_sent_and_logged_plain(world):
    w = world
    case = sorted(set(w.manifest["case_ids"]) - w.restricted)[0]
    masked = let_in(w, plain=False)
    status, text, _ = ask(w, "/api/case-summary.pdf?client=Case%201", masked)
    assert status == 403 and "masked" in text
    assert ask(w, "/api/export-firm.zip?name=x.zip", masked)[0] == 403
    plain = let_in(w, plain=True)
    assert ask(w, "/api/export-firm.zip?name=x.zip", plain)[0] == 403  # never: the export holds the restricted cases too
    views = w.app.views_log.read_text(encoding="utf-8") if w.app.views_log.exists() else ""
    status, _, hdrs = ask(w, "/api/reports.csv?table=cases", plain)
    assert status == 200 and hdrs.get("Content-Type", "").startswith("text/csv")
    log = (w.data / "review_users_access.jsonl").read_text(encoding="utf-8")
    assert '"path": "/api/reports.csv"' in log and '"/api/case-summary.pdf"' in log
    assert views is not None and case


def test_the_session_ends_by_itself_at_its_hour_and_an_attorney_ends_it_early(world, monkeypatch):
    from review import auth

    w = world
    cookie = let_in(w, plain=False, hours=1)
    assert ask(w, "/api/support/health", cookie)[0] == 200
    later = auth._now() + timedelta(hours=1, minutes=1)
    monkeypatch.setattr(auth, "_now", lambda: later)
    assert ask(w, "/api/support/health", cookie)[0] == 401
    monkeypatch.undo()
    rows = json.loads(ask(w, "/api/support", w.cookies["attorney"])[1])
    assert not rows["current"] and rows["last"][0]["email"] == SUPPORT[0]
    cookie = let_in(w, plain=False, hours=8)
    assert json.loads(ask(w, "/api/support", w.cookies["attorney"])[1])["current"][0]["hours"] == 8
    assert ask(w, "/api/support", w.cookies["attorney"], {"action": "end", "email": SUPPORT[0]})[0] == 200
    assert ask(w, "/api/support/cases", cookie)[0] == 401
    # the code works once: signing in again with it is refused
    status, text, _ = ask(w, "/api/support", w.cookies["attorney"], {"action": "let_in", "email": SUPPORT[0], "name": SUPPORT[1], "hours": 1})
    code = json.loads(text)["code"]
    body = {"email": SUPPORT[0], "password": code}
    assert ask(w, "/api/login", "", body)[0] == 200 and ask(w, "/api/login", "", body)[0] in (400, 401)


def test_the_health_page_says_what_support_needs_and_the_ledger_holds_every_step(world):
    w = world
    cookie = let_in(w, plain=False)
    health = json.loads(ask(w, "/api/support/health", cookie)[1])
    assert {"version", "roster", "followers", "backups", "register", "jobs", "error_log", "ledger", "stores"} <= set(health)
    settings = ask(w, "/api/settings", cookie)[1]  # the firm's own values, in its own words
    assert '"label": "Bar number"' in settings and '"title": "Visa Bulletin: EB-4 (SIJ green cards)"' in settings  # shown in its words, not denied
    shape = json.loads(ask(w, "/api/support/case?case=Case%201", cookie)[1])
    assert shape["case"] == "Case 1" and shape["records"] and all(r["bytes"] > 0 for r in shape["records"]) and shape["documents"] >= 3
    assert ask(w, "/api/support/health", w.cookies["attorney"])[0] == 403  # support's own pages
    rows = [json.loads(x) for p in sorted(w.data.glob("events*.jsonl")) for x in p.read_text(encoding="utf-8").splitlines() if x.strip()]
    ours = [r for r in rows if r.get("kind") == "support"]
    assert any(r["action"] == "let_in" and "masked" in r["what"] for r in ours)
    asked = [r for r in ours if r["action"] == "request"]
    assert asked and all(r["who"] == SUPPORT[1] and r["via"] == "support" and r["role"] == "support" for r in asked)
    assert any("/api/support/case" in r["what"] and r.get("case") for r in asked)
