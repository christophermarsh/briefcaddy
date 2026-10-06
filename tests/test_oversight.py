"""What the attorney can see of what the office did (src/review/oversight.py): the staff access log on a screen, who viewed a case and
the firm in full (over an index, never the whole file), the restricted cases that send automatic messages, the firm policies' record
of changes and the way back to the shipped wording, and an approval "as edited by the firm".

Everyone here is made up. A paralegal is refused on every one of these routes.
"""

from __future__ import annotations

import csv
import hashlib
import io
import json
import threading
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path

import pytest

import clock
import restricted
import second_factor
from factgraph import FactGraph
import schema_path

REPO = Path(__file__).resolve().parent.parent
SHIPPED = schema_path.path("law", "policy_sijs")
PASSWORD = "correct horse battery staple"  # secret-scan: allow (a made-up test password)
H = {"X-Review-App": "1"}


def make_case(root: Path, case_id: str, name: str) -> Path:
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(case_id)
    given, _, family = name.upper().partition(" ")
    for key, value in {"applicant.given_name": given, "applicant.family_name": family}.items():
        g.add_source(key, "questionnaire", "intake_questionnaire", value, value, 0.95)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": case_id, "classifications": {}}), encoding="utf-8")
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": []}), encoding="utf-8")
    return d


@pytest.fixture(autouse=True)
def firm_files(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_POLICIES_FIRM", str(tmp_path / "policies_firm.json"))
    monkeypatch.setenv("I485_RULES_APPROVED", str(tmp_path / "rules_approved.json"))


@pytest.fixture
def app(tmp_path):
    from review.auth import Accounts
    from review.server import ReviewApp

    root = tmp_path / "clients"
    make_case(root, "case-ana", "Ana Clara Exemplo Souza")
    make_case(root, "case-bia", "Beatriz Exemplo Lima")
    accounts = Accounts(tmp_path / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney"),
                              ("ana@firm.example", "Ana Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
        if role == "attorney":
            second_factor.set_up(accounts, email, PASSWORD)  # an attorney signs in with a code from an app (review/auth.py)
    return ReviewApp(root, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), SHIPPED, accounts=accounts)


@pytest.fixture
def server(app):
    from review.server import make_handler, serve

    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def call(url, cookie=None, body=None):
    headers = H | ({"Cookie": cookie} if cookie else {}) | ({"Content-Type": "application/json"} if body is not None else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, r.read(), r.headers
    except urllib.error.HTTPError as e:
        return e.code, e.read(), e.headers


def get(url, cookie=None):
    status, body, headers = call(url, cookie)
    return status, (json.loads(body) if headers.get("Content-Type", "").startswith("application/json") else body)


def sign_in(base, email, password=PASSWORD):
    status, body, headers = call(base + "/api/login", body={"email": email, "password": password})
    if status != 200:
        return None
    cookie = headers["Set-Cookie"].split(";")[0]
    owed = json.loads(body)["user"].get("second_factor")
    return second_factor.finish(base, email, cookie) if owed == "code" else None if owed else cookie


def rows_of(data: bytes) -> list[dict]:
    return list(csv.DictReader(io.StringIO(data.decode("utf-8-sig"))))


ROUTES = ["/api/staff_log", "/api/staff_log.csv", "/api/views", "/api/views.csv", "/api/views?group=day", "/api/messages_on", "/api/policy_changes",
          "/api/policy_changes.csv", "/api/access_log?client=case-ana", "/api/access_log?client=case-ana&page=1", "/api/access_log.csv?client=case-ana"]


# -- every route is the attorney's -----------------------------------------------------------------------------


def test_a_paralegal_is_refused_on_every_route_and_nobody_signed_out_gets_in(server):
    jane, sam = sign_in(server, "jane@firm.example"), sign_in(server, "sam@firm.example")
    for route in ROUTES:
        status, body = get(server + route, jane)
        assert status == 403, (route, status)
        assert b"attorney" in (body if isinstance(body, bytes) else json.dumps(body).encode())  # in plain words, not a bare refusal
        assert get(server + route)[0] == 401, route
        assert get(server + route, sam)[0] == 200, route
    # the policies list a paralegal may read has no record of edits in it; the way back is refused
    assert all("edits" not in p for p in get(server + "/api/policies", jane)[1]["policies"])
    assert all("edits" in p for p in get(server + "/api/policies", sam)[1]["policies"])
    status, body, _ = call(server + "/api/policies", jane, {"policy": "NOT-EOIR", "revert": True, "reviewer": "Jane Doe"})
    assert status == 403 and b"attorney" in body


# -- 1. what staff did --------------------------------------------------------------------------------------------


def test_what_staff_did_lists_each_kind_with_who_what_when_and_the_address(server, app):
    sam = sign_in(server, "sam@firm.example")
    assert call(server + "/api/login", body={"email": "jane@firm.example", "password": "wrong password here"})[0] == 400
    call(server + "/api/login", body={"email": "nobody@firm.example", "password": "wrong password here"})
    for _ in range(5):  # five wrong passwords lock the account
        call(server + "/api/login", body={"email": "ana@firm.example", "password": "wrong password here"})
    call(server + "/api/staff", sam, {"action": "add", "name": "Kim Exemplo", "email": "kim@firm.example", "role": "paralegal"})
    call(server + "/api/staff", sam, {"action": "role", "email": "kim@firm.example", "role": "attorney"})
    call(server + "/api/staff", sam, {"action": "reset", "email": "kim@firm.example"})
    call(server + "/api/staff", sam, {"action": "disable", "email": "kim@firm.example"})
    call(server + "/api/access", sam, {"client": "case-bia", "action": "mark", "reason": "A minor.", "reviewer": "Sam Attorney"})
    status, log = get(server + "/api/staff_log", sam)
    assert status == 200 and log["page"] == 1 and log["per"] == 50
    by_what = {r["what"]: r for r in log["rows"]}
    assert "Signed in" in by_what and by_what["Signed in"]["who"] in ("Jane Doe", "Sam Attorney", "Ana Attorney")
    assert "Wrong password" in by_what and "Tried an email that is not on the staff list (nobody@firm.example)" in by_what
    assert "Account locked for 15 minutes after 5 wrong passwords or codes in a row" in by_what
    assert "Added the account of Kim Exemplo as paralegal" in by_what
    assert "Changed the account of Kim Exemplo: role is now attorney" in by_what and "Changed the account of Kim Exemplo: turned off" in by_what
    assert "Gave Kim Exemplo a new one-time password" in by_what and "Restricted the case" in by_what
    assert by_what["Restricted the case"]["case"] == "case-bia" and by_what["Restricted the case"]["who"] == "Sam Attorney"
    for what in ("Wrong password", "Account locked for 15 minutes after 5 wrong passwords or codes in a row", "Added the account of Kim Exemplo as paralegal", "Restricted the case",
                 "Tried an email that is not on the staff list (nobody@firm.example)"):
        assert by_what[what]["address"] == "127.0.0.1", "every row says where it came from"
    assert {k["id"] for k in log["kinds"]} >= {"sign_in", "failed", "lockout", "password", "account", "case_access", "rule", "policy", "link"}
    assert [p["email"] for p in log["people"]] == ["ana@firm.example", "jane@firm.example", "kim@firm.example", "sam@firm.example"]
    moments = [clock.parse(r["at"]) for r in log["rows"]]
    assert moments == sorted(moments, reverse=True)  # newest first


def test_the_staff_log_filters_by_person_kind_and_dates_and_pages_fifty_at_a_time(server, app):
    sam = sign_in(server, "sam@firm.example")
    for _ in range(60):
        app.accounts.log("signed_in", "kim@firm.example", address="10.0.0.5")  # kim has no account: only these rows are hers
    status, log = get(server + "/api/staff_log?person=kim@firm.example", sam)
    assert log["total"] == 60 and len(log["rows"]) == 50 and log["pages"] == 2
    assert len(get(server + "/api/staff_log?person=kim@firm.example&page=2", sam)[1]["rows"]) == 10
    assert get(server + "/api/staff_log?person=kim@firm.example&page=99", sam)[1]["page"] == 2  # kept within the pages there are
    assert get(server + "/api/staff_log?page=two", sam)[0] == 400
    assert {r["kind"] for r in get(server + "/api/staff_log?kind=failed", sam)[1]["rows"]} <= {"failed"}
    done = get(server + "/api/staff_log?person=sam@firm.example&kind=sign_in", sam)[1]
    assert done["total"] >= 1 and {r["who"] for r in done["rows"]} == {"Sam Attorney"} and {r["kind"] for r in done["rows"]} == {"sign_in"}
    today = clock.today().isoformat()
    assert get(server + f"/api/staff_log?from={today}&to={today}&person=kim@firm.example", sam)[1]["total"] == 60
    yesterday = (clock.today() - timedelta(days=1)).isoformat()
    assert get(server + f"/api/staff_log?to={yesterday}&person=kim@firm.example", sam)[1]["total"] == 0
    assert get(server + f"/api/staff_log?from={today}&to={yesterday}", sam)[0] == 400
    status, body = get(server + "/api/staff_log?from=10/03/2026", sam)
    assert status == 400 and body["error"] == "Write the dates as MM/DD/YYYY."


def test_the_staff_log_csv_has_every_row_and_a_formula_cannot_run_in_it(server, app):
    sam = sign_in(server, "sam@firm.example")
    for _ in range(60):
        app.accounts.log("signed_in", "kim@firm.example", address="10.0.0.5")
    assert call(server + "/api/login", body={"email": "=HYPERLINK(\"http://evil.example\")", "password": "x" * 14})[0] == 400  # typed into the email box
    app.accounts.add("evil@firm.example", "=Evil Name", "paralegal")  # a name that would run as a formula
    app.accounts.log("signed_in", "evil@firm.example")
    status, body, headers = call(server + "/api/staff_log.csv?person=kim@firm.example", sam)
    assert status == 200 and headers["Content-Type"].startswith("text/csv") and "attachment" in headers["Content-Disposition"]
    rows = rows_of(body)
    assert len(rows) == 60 and set(rows[0]) == {"When", "Who", "Email", "Kind", "What", "Case", "Restricted case", "From"}  # every row, not one page
    assert rows[0]["Who"] == "kim@firm.example" and rows[0]["From"] == "10.0.0.5" and rows[0]["Kind"] == "Sign-ins and sign-outs"
    assert __import__("re").fullmatch(r"\d{2}/\d{2}/\d{4} \d{1,2}:\d{2} (AM|PM)", rows[0]["When"])
    everything = rows_of(call(server + "/api/staff_log.csv", sam)[1])
    cells = [c for row in everything for c in row.values()]
    assert any(c.startswith("'=hyperlink") for c in cells), "the typed email shows as text"
    assert "'=Evil Name" in cells, "a staff member's name that starts with = shows as text"
    assert not [c for c in cells if c[:1] in ("=", "+", "-", "@")], "no cell starts a formula"


def test_rule_approvals_and_policy_edits_and_link_shown_are_in_the_same_log(server, app):
    from rules import approval

    sam = sign_in(server, "sam@firm.example")
    approval.approve("OVERSTAY-01", "Sam Attorney", "attorney")
    approval.approve("POLICY:NOT-EOIR", "Sam Attorney", "attorney")
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "enabled": False, "reviewer": "Sam Attorney"})
    app.viewed({"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}, "case-ana", "link", address="127.0.0.1")
    log = get(server + "/api/staff_log", sam)[1]
    by_kind = {}
    for r in log["rows"]:
        by_kind.setdefault(r["kind"], []).append(r["what"])
    assert any(w.startswith("Approved the overstay rule") for w in by_kind["rule"])
    assert any("Approved the firm policy “Not filing with the immigration court”" == w for w in by_kind["rule"]), by_kind["rule"]
    assert by_kind["policy"] == ["Switched off: Not filing with the immigration court"]
    link = next(r for r in log["rows"] if r["kind"] == "link")
    assert link["what"] == "Showed the client's portal sign-in link" and link["case"] == "case-ana" and link["who"] == "Sam Attorney"
    assert all(r["who"] == "Sam Attorney" and r["email"] == "sam@firm.example" for r in log["rows"] if r["kind"] in ("rule", "policy"))
    # the person filter finds a person's approvals by the name the approval carries
    assert {r["kind"] for r in get(server + "/api/staff_log?person=sam@firm.example&kind=rule", sam)[1]["rows"]} == {"rule"}
    assert get(server + "/api/staff_log?person=jane@firm.example&kind=rule", sam)[1]["total"] == 0


# -- 2. who viewed this, in full; who viewed what -----------------------------------------------------------------


def write_views(path: Path, count: int, *, cases: int = 1800, people=("a@firm.example", "b@firm.example", "c@firm.example", "d@firm.example")) -> list[dict]:
    """A view log of `count` rows over about 90 days (one every 39 seconds), spread over `cases` cases and the people."""
    kinds = ("case", "scan", "document", "filled_form", "packet", "link")
    start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    rows = []
    for i in range(count):
        rows.append({"at": (start + timedelta(seconds=39 * i)).isoformat(), "email": people[i % len(people)], "name": people[i % len(people)].split("@")[0].title(),
                     "role": "paralegal" if i % 4 else "attorney", "client": f"case-{(i * 7) % cases:04d}", "kind": kinds[i % len(kinds)], "file": f"doc{i % 9}.pdf" if i % 3 == 0 else None,
                     "address": "10.0.0.7"} | ({"restricted": True} if i % 11 == 0 else {}))
    path.write_text("".join(json.dumps(r) + "\n" for r in rows), encoding="utf-8")
    return rows


def test_the_view_log_is_read_through_an_index_never_whole_per_request(tmp_path):
    from review import oversight

    path = tmp_path / "review_views.jsonl"
    rows = write_views(path, 200_000)
    kinds = {"case": "Opened the case", "scan": "Looked at a scan", "document": "Opened a document", "filled_form": "Opened a filled form", "packet": "Opened the filing packet",
             "link": "Showed the client's portal sign-in link"}
    views = oversight.Views(path, kinds, lambda: {"a@firm.example": "Ana A"})
    first = views.firm(page=1)  # builds the index: the one time the file is read through
    assert first["total"] == 200_000 and first["pages"] == 4000 and len(first["rows"]) == 50
    scanned, read = views.index.bytes_scanned, views.index.rows_read
    assert scanned == path.stat().st_size and read == 50  # the page itself: 50 rows by seeking, nothing else
    assert first["rows"][0]["at"] == rows[-1]["at"] and first["rows"][49]["at"] == rows[-50]["at"]  # newest first

    # a case: the latest 20, then every row, then filtered: each reads only the rows on the page
    case = "case-0007"
    expected = [r for r in rows if r["client"] == case]
    latest = views.case(case, limit=20)
    assert [r["at"] for r in latest["rows"]] == [r["at"] for r in reversed(expected)][:20] and latest["total"] == len(expected)
    full = views.case(case, page=2)
    assert full["total"] == len(expected) and full["per"] == 50 and full["page"] == 2 and len(full["rows"]) == min(50, len(expected) - 50)
    mine = views.case(case, person="b@firm.example", kind="scan")
    want = [r for r in expected if r["email"] == "b@firm.example" and r["kind"] == "scan"]
    assert mine["total"] == len(want) and [r["at"] for r in mine["rows"]] == [r["at"] for r in reversed(want)][:50]
    assert {p["email"] for p in mine["people"]} <= {"a@firm.example", "b@firm.example", "c@firm.example", "d@firm.example"}
    assert next(p for p in views.firm(page=1)["people"] if p["email"] == "a@firm.example")["name"] == "Ana A"
    assert views.index.bytes_scanned == scanned, "no question read the file through again"
    assert views.index.rows_read - read <= 20 + 50 + 50 + 50 + 50 + 50

    # the firm: by person, by kind, by dates, restricted only, and the totals by person and day
    day = "2026-08-15"
    want = [r for r in rows if r["email"] == "c@firm.example" and r.get("restricted") and clock.local_date(r["at"]).isoformat() == day]
    got = views.firm(person="c@firm.example", start=day, end=day, restricted=True)
    assert got["total"] == len(want) and all(r["restricted"] and r["email"] == "c@firm.example" for r in got["rows"])
    totals = views.firm(start=day, end=day, group="day")
    one_day = [r for r in rows if clock.local_date(r["at"]).isoformat() == day]
    assert sum(t["openings"] for t in totals["rows"]) == len(one_day) and {t["day"] for t in totals["rows"]} == {"08/15/2026"}
    assert {t["email"] for t in totals["rows"]} == {"a@firm.example", "b@firm.example", "c@firm.example", "d@firm.example"}
    row = next(t for t in totals["rows"] if t["email"] == "a@firm.example")
    mine = [r for r in one_day if r["email"] == "a@firm.example"]
    assert row["openings"] == len(mine) and row["cases"] == len({r["client"] for r in mine}) and row["restricted"] == sum(1 for r in mine if r.get("restricted"))
    assert views.index.bytes_scanned == scanned

    # rows appended later are the only thing read next time; a half-written row waits for its end
    with open(path, "a", encoding="utf-8") as f:
        f.write(json.dumps(dict(rows[0], at="2026-10-02T12:00:00+00:00", client="case-new")) + "\n")
        f.write('{"at": "2026-10-02T12:01:00+00:00", "client": "case-half')
    size = path.stat().st_size
    assert views.case("case-new", limit=5)["total"] == 1 and views.firm(page=1)["total"] == 200_001
    assert views.index.bytes_scanned - scanned < 400 and views.index.size < size  # the new row only; the unfinished one is left
    with open(path, "a", encoding="utf-8") as f:
        f.write('", "kind": "case", "email": "a@firm.example"}\n')
    assert views.case("case-half", limit=5)["total"] == 1

    # a file cut short or replaced is read afresh
    path.write_text(json.dumps(rows[5]) + "\n", encoding="utf-8")
    assert views.firm(page=1)["total"] == 1


def test_the_index_agrees_with_reading_every_row_on_a_small_log(tmp_path):
    """The brute-force answer, from parsing every row, against the index's, for every filter."""
    from review import oversight

    path = tmp_path / "v.jsonl"
    rows = write_views(path, 3000, cases=40)
    views = oversight.Views(path, {}, lambda: {})
    for person in ("", "a@firm.example", "d@firm.example"):
        for kind in ("", "case", "link"):
            for restricted_only in (False, True):
                for start, end in (("", ""), ("2026-07-10", "2026-07-12"), ("2026-07-31", "")):
                    a, b = oversight._span(start, end)
                    want = [r for r in rows if (not person or r["email"] == person) and (not kind or r["kind"] == kind) and (not restricted_only or r.get("restricted"))
                            and (a is None or clock.parse(r["at"]).timestamp() >= a) and (b is None or clock.parse(r["at"]).timestamp() < b)]
                    got = views.firm(person=person, kind=kind, start=start, end=end, restricted=restricted_only)
                    assert got["total"] == len(want) and [r["at"] for r in got["rows"]] == [r["at"] for r in reversed(want)][:50], (person, kind, restricted_only, start, end)


def test_who_viewed_this_shows_the_latest_20_then_every_row_with_filters_and_a_csv(server, app):
    sam = sign_in(server, "sam@firm.example")
    jane = {"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}
    ana = {"email": "ana@firm.example", "name": "Ana Attorney", "role": "attorney"}
    for i in range(70):
        app.viewed(jane if i % 2 else ana, "case-ana", "scan" if i % 3 else "packet", f"d{i}.pdf", "10.0.0.9")
    app.viewed(jane, "case-bia", "document", "other.pdf", "10.0.0.9")
    latest = get(server + "/api/access_log?client=case-ana&limit=20", sam)[1]
    assert len(latest["rows"]) == 20 and latest["total"] == 70 and latest["rows"][0]["file"] == "d69.pdf"  # as before, with a count for "Show all"
    page = get(server + "/api/access_log?client=case-ana&page=1", sam)[1]
    assert page["total"] == 70 and page["pages"] == 2 and len(page["rows"]) == 50 and page["per"] == 50
    assert [p["name"] for p in page["people"]] == ["Ana Attorney", "Jane Doe"] and {k["label"] for k in page["kinds"]} == {"Looked at a scan", "Opened the filing packet"}
    assert len(get(server + "/api/access_log?client=case-ana&page=2", sam)[1]["rows"]) == 20
    only = get(server + "/api/access_log?client=case-ana&page=1&person=jane@firm.example&kind=scan", sam)[1]
    assert only["total"] == sum(1 for i in range(70) if i % 2 and i % 3) and all(r["email"] == "jane@firm.example" and r["kind"] == "scan" for r in only["rows"])
    assert all(r["client"] == "case-ana" for r in page["rows"])  # no other case's rows
    status, body, headers = call(server + "/api/access_log.csv?client=case-ana&person=jane@firm.example", sam)
    csv_rows = rows_of(body)
    assert status == 200 and "attachment" in headers["Content-Disposition"] and len(csv_rows) == 35 and set(csv_rows[0]) >= {"When", "Who", "What", "Document", "From"}
    assert get(server + "/api/access_log?client=case-ana&page=x", sam)[0] == 400
    assert get(server + "/api/access_log?client=nobody-here&page=1", sam)[0] == 404


def test_who_viewed_what_across_the_firm(server, app):
    sam = sign_in(server, "sam@firm.example")
    jane = {"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}
    ana = {"email": "ana@firm.example", "name": "Ana Attorney", "role": "attorney"}
    restricted.mark(app.data_root / "case-bia", True, "A minor.", "Ana Attorney", "attorney")
    for n, (client, who) in enumerate((("case-ana", jane), ("case-ana", jane), ("case-bia", ana), ("case-bia", ana), ("case-ana", ana))):
        app.viewed(who, client, "scan", f"x{n}.pdf", "10.0.0.9")  # (a scan opened again within 10 minutes is one row: each its own)
    firm = get(server + "/api/views", sam)[1]
    assert firm["total"] == 5 and firm["openings"] == 5 and [r["restricted"] for r in firm["rows"] if r["client"] == "case-bia"] == [True, True]
    assert {r.get("restricted") for r in firm["rows"] if r["client"] == "case-ana"} == {None}  # the mark is on the row only when the case was restricted
    assert get(server + "/api/views?restricted=1", sam)[1]["total"] == 2
    assert get(server + "/api/views?person=jane@firm.example", sam)[1]["total"] == 2
    day = get(server + "/api/views?group=day", sam)[1]
    assert day["group"] == "day" and sum(r["openings"] for r in day["rows"]) == 5 and {r["email"]: (r["openings"], r["cases"], r["restricted"]) for r in day["rows"]} == {
        "jane@firm.example": (2, 1, 0), "ana@firm.example": (3, 2, 2)}
    today = clock.today().strftime("%m/%d/%Y")
    assert {r["day"] for r in day["rows"]} == {today}
    csv_rows = rows_of(call(server + "/api/views.csv?restricted=1", sam)[1])
    assert len(csv_rows) == 2 and csv_rows[0]["Case"] == "case-bia" and csv_rows[0]["Restricted case"] == "Yes"
    totals = rows_of(call(server + "/api/views.csv?group=day", sam)[1])
    assert {(r["Person"], r["Openings"]) for r in totals} == {("Jane Doe", "2"), ("Ana Attorney", "3")}


# -- 3. restricted cases that send automatic messages -------------------------------------------------------------------


def test_the_restricted_cases_that_send_automatic_messages_and_switching_one_off(server, app):
    sam, jane = sign_in(server, "sam@firm.example"), sign_in(server, "jane@firm.example")
    assert get(server + "/api/messages_on", sam)[1]["cases"] == []
    restricted.mark(app.data_root / "case-bia", True, "A minor.", "Sam Attorney", "attorney")
    restricted.set_messages(app.data_root / "case-bia", True, "The client asked for texts.", "Sam Attorney", "attorney")
    restricted.set_messages(app.data_root / "case-ana", True, "Not restricted: the switch means nothing.", "Sam Attorney", "attorney")
    listed = get(server + "/api/messages_on", sam)[1]["cases"]
    assert [c["id"] for c in listed] == ["case-bia"]
    assert listed[0]["by"] == "Sam Attorney" and listed[0]["reason"] == "The client asked for texts." and clock.parse(listed[0]["at"]) and listed[0]["name"]
    rows = {r["id"]: r for r in get(server + "/api/overview", sam)[1]["clients"]}
    assert rows["case-bia"]["restricted"] and rows["case-bia"]["messages_on"] is True and "messages_on" not in rows["case-ana"]
    # a paralegal named on the case sees the case, and nothing says automatic messages are on
    call(server + "/api/access", sam, {"client": "case-bia", "action": "name", "email": "jane@firm.example", "reviewer": "Sam Attorney"})
    seen = {r["id"]: r for r in get(server + "/api/overview", jane)[1]["clients"]}
    assert "case-bia" in seen and "messages_on" not in seen["case-bia"]
    assert get(server + "/api/messages_on", jane)[0] == 403
    # "Switch off": recorded now, by the attorney, like the case page's own button
    status, body, _ = call(server + "/api/access", sam, {"client": "case-bia", "action": "messages_off", "reviewer": "Someone Else"})  # the signed-in person is who it records
    assert status == 200 and json.loads(body)["automatic_messages"] is False
    assert get(server + "/api/messages_on", sam)[1]["cases"] == []
    last = restricted.record(app.data_root / "case-bia")["history"][-1]
    assert last["what"] == "Switched automatic messages off" and last["by"] == "Sam Attorney" and clock.parse(last["at"])
    assert restricted.record(app.data_root / "case-bia")["messages"]["on"] is False
    row = next(r for r in get(server + "/api/staff_log?kind=case_access", sam)[1]["rows"] if r["what"] == "Switched automatic messages off")
    assert row["case"] == "case-bia"


# -- 4. firm policies: the record, and the way back --------------------------------------------------------------------


def shipped_hash() -> str:
    return hashlib.sha256(SHIPPED.read_bytes()).hexdigest()


def policy(sam, server, pid):
    return next(p for p in get(server + "/api/policies", sam)[1]["policies"] if p["id"] == pid)


def test_each_policy_shows_every_edit_and_goes_back_to_the_shipped_wording(server, app):
    from rules import approval

    sam = sign_in(server, "sam@firm.example")
    before = shipped_hash()
    row = policy(sam, server, "NOT-EOIR")
    key, shipped_text = row["answers"][0]["key"], row["shipped_text"]
    assert row["edits"] == [] and row["edit"] is None
    post = lambda body: call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney"} | body)  # noqa: E731
    assert post({"revert": True})[0] == 400 and b"already says what the product shipped with" in post({"revert": True})[1]  # nothing to undo
    assert post({"plain_text": "The firm's own wording.", "answers": {key: "Yes"}})[0] == 200
    assert post({"enabled": False})[0] == 200
    row = policy(sam, server, "NOT-EOIR")
    first, second = row["edits"][1], row["edits"][0]  # newest first
    assert first["by"] == "Sam Attorney" and clock.parse(first["at"]) and first["what"] == "Changed the words and an answer"
    assert [(p["what"], p["before"], p["after"]) for p in first["parts"]] == [("Words", shipped_text, "The firm's own wording."), ("Answer", "No", "Yes")]
    assert second["what"] == "Switched off" and [(p["what"], p["before"], p["after"]) for p in second["parts"]] == [("Applies", "Applies", "Switched off")]
    assert first["parts"][1]["labels"] and key not in first["parts"][1]["labels"][0]  # the box by its words, not its key

    approval.approve("POLICY:NOT-EOIR", "Ana Attorney", "attorney")  # 5. approved as edited
    row = policy(sam, server, "NOT-EOIR")
    assert row["approval"] == "approved" and row["approval_text"].startswith("Approved as edited by the firm on ")
    assert row["approval_text"] == f"Approved as edited by the firm on {clock.today():%m/%d/%Y} (approved by Ana Attorney on {clock.today():%m/%d/%Y})"
    assert approval.history()["POLICY:NOT-EOIR"][-1]["hash"] == next(r for r in approval.catalog() if r["id"] == "POLICY:NOT-EOIR")["hash"]
    from rules import firm_policies

    edited = firm_policies.effective(next(p for p in firm_policies.shipped() if p["id"] == "NOT-EOIR"))
    assert approval.history()["POLICY:NOT-EOIR"][-1]["hash"] == firm_policies.policy_hash(edited)  # the approval holds for the edited text

    assert post({"revert": True})[0] == 200  # back to the shipped wording
    row = policy(sam, server, "NOT-EOIR")
    assert row["plain_text"] == shipped_text and row["answers"][0]["value"] == "No" and row["enabled"] is False  # on or off is its own switch
    assert row["edit"]["text"].startswith("Switched off by Sam Attorney")  # the words are the shipped ones; it is still off
    assert row["edits"][0]["what"] == "Went back to the shipped wording" and row["edits"][0]["by"] == "Sam Attorney"
    assert [(p["what"], p["before"], p["after"]) for p in row["edits"][0]["parts"]] == [("Words", "The firm's own wording.", shipped_text), ("Answer", "Yes", "No")]
    assert row["approval"] == "changed" and row["approval_text"].startswith("Changed since approval (approved by Ana Attorney on ")  # approved the firm's words
    assert len(row["edits"]) == 3  # the history keeps what was undone
    assert approval.status("POLICY:NOT-EOIR")["state"] == "changed"
    assert shipped_hash() == before, "the shipped file is never written"
    assert policy(sam, server, "SIJS-STATUS")["edits"] == []  # another policy's record is its own


def test_the_engine_reads_the_shipped_wording_again_after_the_way_back(server, app):
    from rules.policy import load_policy_profile

    sam = sign_in(server, "sam@firm.example")
    row = policy(sam, server, "NOT-EOIR")
    key = row["answers"][0]["key"]
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "answers": {key: "Yes"}, "plain_text": "Changed words."})
    assert next(p for p in load_policy_profile(SHIPPED) if p["id"] == "NOT-EOIR")["set"] == {key: "Yes"}
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "revert": True})
    engine = next(p for p in load_policy_profile(SHIPPED) if p["id"] == "NOT-EOIR")
    assert engine["set"] == {key: "No"} and engine["plain_text"] == row["shipped_text"]
    assert policy(sam, server, "NOT-EOIR")["edit"] is None  # the firm's version is the shipped one


def test_every_change_across_the_policies_newest_first_and_as_a_csv(server, app, monkeypatch):
    sam, ana = sign_in(server, "sam@firm.example"), sign_in(server, "ana@firm.example")
    part9 = policy(sam, server, "PART9-DEFAULT-NO")
    start = datetime.now(clock.zone()).replace(microsecond=0)

    def at(minutes):  # a clock that moves on between the edits, so "newest first" is not a tie
        monkeypatch.setattr(clock, "_now_override", start + timedelta(minutes=minutes))

    at(1)
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "plain_text": "=1+1 is not a formula here."})
    at(2)
    call(server + "/api/policies", ana, {"policy": "PART9-DEFAULT-NO", "reviewer": "Ana Attorney", "answers": {a["key"]: "Yes" for a in part9["answers"][:3]}})
    at(3)
    call(server + "/api/policies", ana, {"policy": "NOT-EOIR", "reviewer": "Ana Attorney", "revert": True})
    changes = get(server + "/api/policy_changes", sam)[1]["changes"]
    assert [c["what"] for c in changes] == ["Went back to the shipped wording", "Changed the answers", "Changed the words"]
    assert [c["name"] for c in changes][1] == part9["name"] and [c["by"] for c in changes] == ["Ana Attorney", "Ana Attorney", "Sam Attorney"]  # the signed-in person
    assert len(changes[1]["parts"]) == 1 and len(changes[1]["parts"][0]["labels"]) == 3  # three boxes changed the same way: one line naming them
    status, body, headers = call(server + "/api/policy_changes.csv", sam)
    rows = rows_of(body)
    assert status == 200 and "attachment" in headers["Content-Disposition"] and set(rows[0]) == {"When", "Who", "Policy", "What happened", "What changed", "Before", "After"}
    assert {r["Who"] for r in rows} == {"Sam Attorney", "Ana Attorney"} and len(rows) == 3
    assert any(r["Before"] == "'=1+1 is not a formula here." or r["After"] == "'=1+1 is not a formula here." for r in rows)
    assert not [c for r in rows for c in r.values() if c[:1] in ("=", "+", "-", "@")]
    assert get(server + "/api/policy_changes", sign_in(server, "jane@firm.example"))[0] == 403


def test_an_approval_of_the_shipped_wording_is_plain_and_an_old_one_comes_back_after_the_way_back(server, app):
    """Approved as shipped, edited (changed since approval), reverted: the text is the approved text again, so the approval holds again."""
    from rules import approval

    sam = sign_in(server, "sam@firm.example")
    approval.approve("POLICY:NOT-EOIR", "Ana Attorney", "attorney")
    assert policy(sam, server, "NOT-EOIR")["approval_text"].startswith("Approved by Ana Attorney on ")  # not "as edited": it was never edited
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "plain_text": "Reworded."})
    assert policy(sam, server, "NOT-EOIR")["approval"] == "changed"
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "revert": True})
    row = policy(sam, server, "NOT-EOIR")
    assert row["approval"] == "approved" and row["approval_text"].startswith("Approved by Ana Attorney on ")


def test_the_approval_for_the_shipped_wording_after_an_off_switch_is_still_plain(server, app):
    from rules import approval

    sam = sign_in(server, "sam@firm.example")
    call(server + "/api/policies", sam, {"policy": "NOT-EOIR", "reviewer": "Sam Attorney", "enabled": False})
    approval.approve("POLICY:NOT-EOIR", "Ana Attorney", "attorney")
    assert policy(sam, server, "NOT-EOIR")["approval_text"].startswith("Approved by Ana Attorney on ")  # switched off is not an edit of the words


# -- a log replaced while a question runs; one pass over the file; restricted later -------------------------------------


def client_rows(client: str, count: int) -> str:
    start = datetime(2026, 7, 1, 12, 0, tzinfo=timezone.utc)
    return "".join(json.dumps({"at": (start + timedelta(minutes=i)).isoformat(), "email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal", "client": client,
                               "kind": "link" if i % 4 == 0 else "scan", "file": f"d{i}.pdf", "address": "10.0.0.7"}) + "\n" for i in range(count))


def test_a_log_replaced_while_a_question_runs_is_answered_not_a_500(server, app, monkeypatch):
    """A rebuild of the index between a question's selection and its read of the rows used to shift the entries (IndexError, a 500)."""
    from review import oversight

    sam = sign_in(server, "sam@firm.example")
    path = app.views_log
    real = oversight.RowIndex.read
    for route in ("/api/access_log?client=case-ana&page=1", "/api/access_log?client=case-ana&limit=20", "/api/access_log.csv?client=case-ana", "/api/views",
                  "/api/views?group=day", "/api/views.csv", "/api/staff_log", "/api/staff_log.csv"):
        monkeypatch.setattr(oversight.RowIndex, "read", real)
        path.write_text(client_rows("case-ana", 3000), encoding="utf-8")
        assert get(server + route, sam)[0] == 200  # indexed against the large file
        swapped = []

        def swap_then_read(self, offsets, swapped=swapped):
            if not swapped:
                swapped.append(1)
                path.write_text(client_rows("case-ana", 10), encoding="utf-8")  # replaced by a small one, and another request's rebuild
                app.views.index.refresh()
                app.staff_log.index.refresh()
            return real(self, offsets)

        monkeypatch.setattr(oversight.RowIndex, "read", swap_then_read)
        status, body = get(server + route, sam)
        assert status == 200, (route, status, body)
        assert swapped or "group=day" in route, route


def test_threads_asking_while_the_log_is_replaced_never_fail(tmp_path):
    import time

    from review import oversight

    path = tmp_path / "review_views.jsonl"
    path.write_text(client_rows("case-ana", 2000), encoding="utf-8")
    views = oversight.Views(path, {}, lambda: {})
    errors, stop = [], threading.Event()

    def ask():
        while not stop.is_set():
            try:
                views.case("case-ana", page=1)
                views.case("case-ana", limit=20)
                views.firm(page=1)
                views.firm(group="day")
                views.firm_csv()
            except Exception:  # noqa: BLE001 -- anything at all is a failure here
                errors.append(__import__("traceback").format_exc())

    threads = [threading.Thread(target=ask) for _ in range(3)]
    for t in threads:
        t.start()
    for n in range(60):  # smaller, then larger, then smaller again
        path.write_text(client_rows("case-ana", 100 if n % 2 else 2000 + n), encoding="utf-8")
        time.sleep(0.01)
    stop.set()
    for t in threads:
        t.join()
    assert not errors, errors[:3]


def test_the_selected_rows_are_read_in_file_order_in_one_pass(tmp_path, monkeypatch):
    import builtins

    from review import oversight

    path = tmp_path / "review_views.jsonl"
    path.write_text(client_rows("case-ana", 5000), encoding="utf-8")
    views = oversight.Views(path, {}, lambda: {})
    assert views.firm(page=1)["total"] == 5000  # indexed
    seeks = []

    class Counting:
        def __init__(self, f):
            self._f = f

        def seek(self, *args):
            seeks.append(args)
            return self._f.seek(*args)

        def __enter__(self):
            self._f.__enter__()
            return self

        def __exit__(self, *args):
            return self._f.__exit__(*args)

        def __getattr__(self, name):
            return getattr(self._f, name)

    monkeypatch.setattr(oversight, "open", lambda *a, **k: Counting(builtins.open(*a, **k)), raising=False)
    rows = rows_of(views.firm_csv())
    assert len(rows) == 5000 and rows[0]["Document"] == "d4999.pdf" and rows[-1]["Document"] == "d0.pdf"  # newest first, as asked
    assert len(seeks) == 1, "5,000 rows newest first are one sequential read, not 5,000 seeks"
    seeks.clear()
    scattered = views.index.read([views.index.pos[i] for i in (4000, 10, 2500)])  # asked out of order: answered in the order asked
    assert [r["file"] for r in scattered] == ["d4000.pdf", "d10.pdf", "d2500.pdf"] and len(seeks) == 3


def test_a_case_restricted_later_keeps_its_older_rows_in_restricted_cases_only(server, app):
    sam = sign_in(server, "sam@firm.example")
    jane = {"email": "jane@firm.example", "name": "Jane Doe", "role": "paralegal"}
    app.viewed(jane, "case-ana", "scan", "before.pdf", "10.0.0.9")  # written while the case was not restricted: no mark on the row
    assert get(server + "/api/views?restricted=1", sam)[1]["total"] == 0
    restricted.mark(app.data_root / "case-ana", True, "A minor.", "Sam Attorney", "attorney")
    app.roster.touch("case-ana")  # a mark written to the disk by something other than the app's own route: the lists are told
    now = get(server + "/api/views?restricted=1", sam)[1]
    assert now["total"] == 1 and now["rows"][0]["restricted"] is True and now["openings"] == 1  # restricted now: the older row is found
    day = get(server + "/api/views?group=day", sam)[1]
    assert [r["restricted"] for r in day["rows"]] == [1]
    assert get(server + "/api/access_log?client=case-ana&page=1", sam)[1]["rows"][0]["restricted"] is True
    assert rows_of(call(server + "/api/views.csv?restricted=1", sam)[1])[0]["Restricted case"] == "Yes"
    app.viewed(jane, "case-ana", "packet", "while.pdf", "10.0.0.9")  # written while restricted: the mark is on the row itself
    restricted.mark(app.data_root / "case-ana", False, "", "Sam Attorney", "attorney")
    app.roster.touch("case-ana")
    lifted = get(server + "/api/views?restricted=1", sam)[1]
    assert [r["file"] for r in lifted["rows"]] == ["while.pdf"] and lifted["total"] == 1  # open again: only what was written restricted stays marked


def test_what_staff_did_marks_the_rows_of_a_restricted_case(server, app):
    sam = sign_in(server, "sam@firm.example")
    app.viewed({"email": "sam@firm.example", "name": "Sam Attorney", "role": "attorney"}, "case-bia", "link", address="127.0.0.1")

    def rows():
        return {r["what"]: r for r in get(server + "/api/staff_log", sam)[1]["rows"] if r["case"] == "case-bia"}

    assert rows()["Showed the client's portal sign-in link"]["restricted"] is False
    call(server + "/api/access", sam, {"client": "case-bia", "action": "mark", "reason": "A minor.", "reviewer": "Sam Attorney"})
    shown = rows()
    assert shown["Restricted the case"]["restricted"] is True and shown["Showed the client's portal sign-in link"]["restricted"] is True  # restricted now
    call(server + "/api/access", sam, {"client": "case-bia", "action": "unmark", "reviewer": "Sam Attorney"})
    shown = rows()
    assert shown["Lifted the restriction"]["restricted"] is True  # it was restricted when that was done
    assert shown["Restricted the case"]["restricted"] is True  # the row says so itself
    assert shown["Showed the client's portal sign-in link"]["restricted"] is False  # open again, and not restricted when it was shown
    assert "Restricted case" in rows_of(call(server + "/api/staff_log.csv", sam)[1])[0]


def test_a_big_csv_does_not_look_up_the_firms_zone_for_every_row(tmp_path, monkeypatch):
    """clock.zone() reads the settings file's modified time on each call: on a network drive that was most of the 223 seconds 200,000 rows took."""
    from review import oversight

    path = tmp_path / "review_views.jsonl"
    path.write_text(client_rows("case-ana", 3000), encoding="utf-8")
    views = oversight.Views(path, {}, lambda: {})
    views.firm(page=1)
    calls = []
    real = clock.zone
    monkeypatch.setattr(clock, "zone", lambda: calls.append(1) or real())
    rows = rows_of(views.firm_csv())
    assert len(rows) == 3000 and rows[0]["When"].endswith(("AM", "PM"))
    assert len(calls) < 10, f"{len(calls)} zone lookups for 3,000 rows"
