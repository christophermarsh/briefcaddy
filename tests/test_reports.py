"""The reports (src/review/reports.py) and the review app's expiring-documents and reports routes.

Everyone here is made up ("Ana Clara Exemplo Souza", "Maria Exemplo"). The made-up world: three cases in two offices, one of them a
protected one (a VAWA self-petition), with their documents, decisions and mailings, and one overnight run's log.
"""

from __future__ import annotations

import csv
import io
import json
import re
import threading
import urllib.error
import urllib.request
from datetime import date, timedelta
from pathlib import Path

import pytest

import index
import second_factor
from factgraph import FactGraph
from review import reports
import schema_path

_REPO = Path(__file__).resolve().parent.parent
TODAY = date.today()  # the review app reads the clock itself


def iso(days: int) -> str:
    return (TODAY + timedelta(days=days)).isoformat()


def doc(doc_id, type_, *, person="applicant", expires=None, issued=None, quality="readable", confidential=None):
    return {"id": doc_id, "files": [f"{doc_id}.pdf"], "doc_ids": [f"{doc_id}.pdf"], "pages": [1], "type": type_, "confidence": 0.9, "person": person, "person_set_by": None,
            "language": "en", "issued": issued, "expires": expires, "identifiers": {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""}, "quality": quality,
            "hash": doc_id * 4, "source": "folder", "added": "2026-09-01T10:00:00+00:00", "roles": [], "tags": [], "confidential": confidential, "text": "", "translated": None}


def make_case(root: Path, case_id: str, name: str, docs, *, filings=(), decisions=None, built=()):
    d = root / case_id
    d.mkdir(parents=True, exist_ok=True)
    g = FactGraph(case_id)
    given, _, family = name.upper().partition(" ")  # the forms' own capitals, as the review app shows them
    for key, value in {"applicant.given_name": given, "applicant.family_name": family}.items():
        g.add_source(key, "questionnaire", "intake_questionnaire", value, value, 0.95)
    g.save(d / "fact_graph.json")
    (d / "meta.json").write_text(json.dumps({"client_id": case_id, "classifications": {}}), encoding="utf-8")
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": list(docs)}), encoding="utf-8")
    if filings:
        (d / "status.json").write_text(json.dumps({"filings": [{"filing": f, "title": t, "mailed_on": "2026-09-01", "carrier": "USPS", "tracking": "", "by": "Paulo Paralegal"}
                                                               for f, t in filings]}), encoding="utf-8")
    if decisions:  # reviewer and time per decision, in the shape review/state.py stores them
        (d / "decisions.json").write_text(json.dumps({iid: {"action": "acknowledge", "values": {}, "reviewer": who, "note": "", "at": at,
                                                           "item": {"id": iid, "kind": "reading", "level": "review", "title": "A made-up item", "group": "g", "facts": []}}
                                                      for iid, (who, at) in decisions.items()}), encoding="utf-8")
    for name_ in built:
        (d / name_).write_text("{}", encoding="utf-8")
    return d


@pytest.fixture
def world(tmp_path, monkeypatch):
    data = tmp_path / "data"
    root = data / "clients"
    monkeypatch.setenv("I485_INDEX", str(data / "index.db"))
    index._LAST_REFRESH.clear()
    make_case(root, "case-ana", "Ana Clara Exemplo Souza", [doc("a1", "passport", expires=iso(40)), doc("a2", "advance_parole", expires=iso(100)),
                                                           doc("a3", "birth_certificate", quality="blurry")],
              filings=[("i360", "I-360 petition (Special Immigrant Juvenile)")],
              decisions={"i1": ("Ana Attorney", "2026-09-20T10:00:00+00:00"), "i2": ("Paulo Paralegal", "2026-09-01T10:00:00+00:00")})
    make_case(root, "case-maria", "Maria Exemplo", [doc("m1", "work_permit", expires=iso(300)), doc("m2", "lease")],
              decisions={"i1": ("Paulo Paralegal", "2026-09-02T10:00:00+00:00")}, built=["packet_ead.json"])
    make_case(root, "case-rosa", "Rosa Exemplo", [doc("r1", "passport", expires=iso(30)), doc("r2", "advance_parole", expires=iso(90)), doc("r3", "declaration")],
              filings=[("vawa", "I-360 VAWA self-petition")])
    (data / "batch_log.jsonl").write_text(
        json.dumps({"started_at": "2026-10-01T02:00:00+00:00", "finished_at": "2026-10-01T03:30:00+00:00", "version": "x", "done": 2, "failed": 1, "left": 0, "stopped": None}) + "\n",
        encoding="utf-8")
    (data / "batch_state.json").write_text(json.dumps({"case-rosa": {"status": "failed", "at": "2026-10-01T03:00:00+00:00", "error": "a document could not be read"},
                                                      "case-ana": {"status": "done", "at": "2026-10-01T02:30:00+00:00"}}), encoding="utf-8")
    return root


def rows_of(root: Path):
    stages = {"case-ana": "filed", "case-maria": "review", "case-rosa": "attorney"}
    offices = {"case-ana": "Massachusetts", "case-maria": "Florida", "case-rosa": "Florida"}
    names = {"case-ana": "ANA CLARA EXEMPLO SOUZA", "case-maria": "MARIA EXEMPLO", "case-rosa": "ROSA EXEMPLO"}
    return [{"id": c, "summary": {"name": names[c]}, "stage": stages[c], "office": offices[c], "open_items": n, "last_activity": "2026-09-20T10:00:00+00:00"}
            for c, n in (("case-ana", 0), ("case-maria", 4), ("case-rosa", 2))] + [{"id": "pilot-nova", "summary": {"name": "NOVA EXEMPLO TESTE"}, "stage": "invited"}]


def table(built: dict, id_: str) -> dict:
    return next(t for t in built["tables"] if t["id"] == id_)


def test_cases_are_counted_by_stage_with_who_they_are(world):
    out = reports.build(rows_of(world), world, role="attorney")
    assert out["cases"] == 3  # the client who was only invited has no case file to report on
    stages = {r["stage"]: r for r in table(out, "stages")["rows"]}
    assert [r["stage"] for r in table(out, "stages")["rows"]][:4] == ["Not invited yet", "Invited", "Answering questions", "Being processed"]
    assert stages["Filed"]["cases"] == 1 and stages["Filed"]["clients"] == ["ANA CLARA EXEMPLO SOUZA"]
    assert stages["Paralegal review"]["clients"] == ["MARIA EXEMPLO"] and stages["With the attorney"]["cases"] == 1 and stages["Ready to file"]["cases"] == 0


def test_cases_are_counted_by_filing_filed_and_built(world):
    rows = {r["filing"]: r for r in table(reports.build(rows_of(world), world), "filings")["rows"]}
    assert rows["I-360 petition (Special Immigrant Juvenile)"]["filed"] == 1 and rows["I-360 petition (Special Immigrant Juvenile)"]["clients"] == ["ANA CLARA EXEMPLO SOUZA"]
    assert rows["I-765 (work permit)"]["filed"] == 0 and rows["I-765 (work permit)"]["ready"] == 1  # Maria's packet is built, not filed
    assert rows["I-360 VAWA self-petition"]["filed"] == 1


def test_cases_are_counted_by_office_and_by_reviewer_of_record(world):
    out = reports.build(rows_of(world), world)
    assert [(r["office"], r["cases"]) for r in table(out, "offices")["rows"]] == [("Florida", 2), ("Massachusetts", 1)]
    rev = table(out, "reviewers")["rows"]
    assert [(r["reviewer"], r["cases"], r["decisions"]) for r in rev] == [("Ana Attorney", 1, 2), ("Paulo Paralegal", 1, 1), ("Nobody yet", 1, 0)]
    assert "never timed" in table(out, "reviewers")["about"]


def test_documents_by_type_and_quality_and_a_paralegal_does_not_count_a_protected_cases(world):
    att = reports.build(rows_of(world), world, role="attorney")
    kinds = {r["type"]: r["documents"] for r in table(att, "document_types")["rows"]}
    assert kinds["Passport"] == 2 and kinds["Advance parole document (Form I-512L)"] == 2 and kinds["Birth certificate"] == 1
    assert {r["quality"]: r["documents"] for r in table(att, "document_quality")["rows"]} == {"Clear": 7, "Hard to read": 1}  # the Documents tab's words
    assert table(att, "document_types")["note"] is None
    para = reports.build(rows_of(world), world, role="paralegal")
    kinds = {r["type"]: r["documents"] for r in table(para, "document_types")["rows"]}
    assert kinds["Passport"] == 1 and "Declaration or affidavit" not in kinds  # Rosa's case is a VAWA one: its three documents are for an attorney
    assert "3 documents in protected cases are counted for an attorney only" in table(para, "document_types")["note"]


def test_the_last_overnight_run_and_what_it_could_not_read(world):
    out = reports.build(rows_of(world), world)
    run = {r["item"]: r["value"] for r in table(out, "overnight")["rows"]}
    assert run["Cases processed"] == "2" and run["Cases that could not be processed"] == "1" and re.fullmatch(r"\d\d/\d\d/\d{4} \d\d:\d\d [AP]M", run["Started"])
    [problem] = table(out, "overnight_problems")["rows"]
    assert problem["client"] == "ROSA EXEMPLO" and problem["problem"] == "a document could not be read"


def test_no_run_yet_says_so(world):
    (world.parent / "batch_log.jsonl").unlink()
    out = reports.build(rows_of(world), world)
    assert table(out, "overnight")["rows"] == [] and "has not recorded a run yet" in table(out, "overnight")["about"]
    assert not [t for t in out["tables"] if t["id"] == "overnight_problems"]


def test_every_case_is_one_line_with_its_dates_as_mm_dd_yyyy(world):
    [ana, maria, rosa] = table(reports.build(rows_of(world), world), "cases")["rows"]
    assert (ana["name"], ana["stage"], ana["office"], ana["reviewer"], ana["filings"], ana["last_activity"]) == (
        "ANA CLARA EXEMPLO SOUZA", "Filed", "Massachusetts", "Ana Attorney", "I-360 petition (Special Immigrant Juvenile)", "09/20/2026")
    assert maria["filings"] == "Nothing filed yet" and maria["open_items"] == 4 and rosa["reviewer"] == "Nobody yet"


def test_nothing_in_the_reports_is_about_money_or_billing(world):
    out = reports.build(rows_of(world), world)
    words = " ".join([t["title"] + " " + t["about"] + " " + " ".join(c["label"] for c in t["columns"]) for t in out["tables"]]).lower()
    assert not re.search(r"\bfees?\b|billing|invoice|price|cost|revenue|payment|\$|hours|timesheet|billable", words), words


def test_a_table_downloads_as_csv_a_spreadsheet_reads_right(world):
    rows = rows_of(world)
    rows[1]["summary"]["name"] = "=HYPERLINK(\"http://example.invalid\") Exemplo"  # a name that would run as a formula
    rows[0]["summary"]["name"] = "JOSÉ EXEMPLO"
    out = reports.build(rows, world)
    text = reports.to_csv(table(out, "stages"))
    assert text.startswith("\ufeff") and text.endswith("\r\n")
    parsed = list(csv.reader(io.StringIO(text.lstrip("\ufeff"))))
    assert parsed[0] == ["Stage", "Cases", "Clients"]
    assert ["Filed", "1", "JOSÉ EXEMPLO"] in parsed  # accents kept
    review = next(r for r in parsed if r[0] == "Paralegal review")
    assert review[2].startswith("'=HYPERLINK")  # text, never a formula
    every = list(csv.reader(io.StringIO(reports.to_csv(table(out, "cases")).lstrip("\ufeff"))))
    assert every[0] == ["Client", "Stage", "Office", "Reviewer", "Filed", "Open review items", "Last activity"] and len(every) == 4


def _wording(base: Path, wid: str, key: str, text: str, uses: list[str], edits: list[str] = (), status: str = "approved", voice: str = "client") -> None:
    import wordings

    wordings._write(base, {"id": wid, "form": "i485", "edition": "09/18/26", "key": key, "part": "9", "item": "23", "page": "14", "voice": voice, "office": "ma", "office_name": "Massachusetts",
                           "text": text, "slots": [], "pattern": {"present": [], "absent": []}, "status": status, "origin": "approval", "created": "2026-10-01T10:00:00-04:00",
                           "approved": {"who": "Sam Attorney", "role": "attorney", "at": "2026-10-01T10:00:00-04:00"}, "parent": None, "number": 1,
                           "uses": [{"case": c, "at": "2026-10-02T10:00:00-04:00", "by": "Sam Attorney", "role": "attorney", "via": "offer" if n else "learned"} for n, c in enumerate(uses)],
                           "edits": [{"case": c, "at": "2026-10-03T10:00:00-04:00", "by": "Sam Attorney", "became": "w-next"} for c in edits], "history": []})


def test_the_firm_wordings_table_counts_uses_and_the_share_edited_after_picking_and_names_no_case(world, monkeypatch):
    import wordings

    base = wordings.root(world)
    key = "applicant.part9.committed_crime"
    _wording(base, "w-aaa", key, "Yes, I was cited in {place_1} on {date_1}.", ["case-ana", "case-rosa", "case-maria"], ["case-x"])  # 3 cases (1 learned, 2 picked) and 1 edited pick
    _wording(base, "w-bbb", key, "Yes, I was cited for driving without a license.", ["case-ana"], status="retired", voice="office")
    _wording(base, "w-ccc", key, "From a past filing, not approved.", [], status="candidate")
    out = reports.build(rows_of(world), world, role="paralegal")
    t = table(out, "wordings")
    assert t["title"] == "Firm wordings" and [c["label"] for c in t["columns"]] == ["Answer", "Wording, with its slots", "Voice", "Status", "Approved by", "Cases", "Times picked", "Edited after picking"]
    assert [(r["status"], r["voice"], r["cases"], r["picked"], r["edited"]) for r in t["rows"]] == [("Approved", "The client's", 3, 3, "33%"), ("Retired", "The office's", 1, 0, "")]
    assert t["rows"][0]["wording"] == "Yes, I was cited in [a place] on [a date]." and t["rows"][0]["approved"] == "Sam Attorney on 10/01/2026"
    assert t["rows"][0]["item"].startswith("Part 9, item 23: ")  # the item as the edition prints it
    # counts only: not a case, not a client
    assert not re.search(r"case-|Exemplo|EXEMPLO", json.dumps(t))
    text = reports.to_csv(t)
    assert text.startswith("﻿") and "Edited after picking" in text and "33%" in text and "case-ana" not in text
    # nothing yet: said in words
    monkeypatch.setenv("I485_WORDINGS", str(world.parent / "no-wordings"))
    empty = reports.build(rows_of(world), world)
    assert "No wording is kept yet" in table(empty, "wordings")["about"] and table(empty, "wordings")["rows"] == []


# -- the review app: who may call what, and what a paralegal is not shown ---------------------------------------------

PASSWORD = "a long enough password for the test"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def staff_server(world):
    from review.auth import Accounts
    from review.server import ReviewApp, make_handler, serve

    accounts = Accounts(world.parent / "staff.json")
    for email, name, role in (("jane@firm.example", "Jane Doe", "paralegal"), ("sam@firm.example", "Sam Attorney", "attorney")):
        accounts.change_password(email, accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(accounts, "sam@firm.example", PASSWORD)  # an attorney signs in with a code (review/auth.py)
    app = ReviewApp(world, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, accounts=accounts)
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _get(url, cookie=None, raw=False):
    req = urllib.request.Request(url, headers={"X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {}), method="GET")
    try:
        with urllib.request.urlopen(req) as r:
            body = r.read()
            return r.status, (body if raw else json.loads(body or b"{}")), r.headers
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), e.headers


def _sign_in(base, email):
    req = urllib.request.Request(base + "/api/login", data=json.dumps({"email": email, "password": PASSWORD}).encode(),
                                 headers={"Content-Type": "application/json", "X-Review-App": "1"}, method="POST")
    with urllib.request.urlopen(req) as r:
        cookie, owed = r.headers.get("Set-Cookie").split(";")[0], json.loads(r.read())["user"].get("second_factor")
    return second_factor.finish(base, email, cookie) if owed else cookie


def test_reports_and_expiring_documents_need_a_sign_in(staff_server):
    assert _get(staff_server + "/api/reports")[0] == 401 and _get(staff_server + "/api/expiring")[0] == 401 and _get(staff_server + "/api/reports.csv?table=stages")[0] == 401


def test_both_roles_get_the_reports_and_a_table_comes_down_as_a_csv_file(staff_server):
    for who in ("jane@firm.example", "sam@firm.example"):
        cookie = _sign_in(staff_server, who)
        status, body, _ = _get(staff_server + "/api/reports", cookie)
        assert status == 200 and {t["id"] for t in body["tables"]} >= {"stages", "filings", "offices", "reviewers", "document_types", "document_quality", "overnight", "cases"}
        status, data, headers = _get(staff_server + "/api/reports.csv?table=offices", cookie, raw=True)
        assert status == 200 and headers["Content-Type"].startswith("text/csv") and 'filename="report-offices.csv"' in headers["Content-Disposition"]
        assert data.decode("utf-8-sig").splitlines()[0] == "Office,Cases,Clients"
        assert _get(staff_server + "/api/reports.csv?table=nothing", cookie)[0] == 404


def test_the_expiring_list_through_the_app_by_office_and_reviewer_and_hides_a_protected_case_from_a_paralegal(staff_server):
    paralegal, attorney = _sign_in(staff_server, "jane@firm.example"), _sign_in(staff_server, "sam@firm.example")
    status, body, _ = _get(staff_server + "/api/expiring?days=365", attorney)
    assert status == 200
    found = {(i["name"], i["document"]) for i in body["items"]}
    assert ("ANA CLARA EXEMPLO SOUZA", "Passport") in found and ("ANA CLARA EXEMPLO SOUZA", "Advance parole document (Form I-512L)") in found
    assert any(n == "ROSA EXEMPLO" for n, _ in found)  # the attorney sees the protected case's passport
    status, body, _ = _get(staff_server + "/api/expiring?days=365", paralegal)
    # a VAWA case is a restricted case (src/restricted.py): absent for a paralegal not named on it, and not counted either
    assert not any(i["name"] == "ROSA EXEMPLO" for i in body["items"]) and body["restricted"] == 0
    status, body, _ = _get(staff_server + "/api/expiring?days=365&reviewer=Ana%20Attorney", attorney)
    assert {i["name"] for i in body["items"]} == {"ANA CLARA EXEMPLO SOUZA"}
    assert _get(staff_server + "/api/expiring?days=soon", attorney)[0] == 400
    # the same documents are in What's due and My work, through the journey's own deadline list
    due = _get(staff_server + "/api/deadlines?size=200", attorney)[1]
    assert any(d["id"].startswith("expiry.passport.") for d in due["items"] if d["client"] == "case-ana")
    work = _get(staff_server + "/api/work?owner=paralegal", paralegal)[1]
    assert not any(d["client"] == "case-rosa" and d["id"].startswith("expiry.") for d in work["deadlines"])
    shown = _get(staff_server + "/api/overview", paralegal)[1]
    assert not any(d["client"] == "case-rosa" and d["id"].startswith("expiry.") for d in _get(staff_server + "/api/deadlines?size=200", paralegal)[1]["items"])
    assert not any(d["id"].startswith("expiry.") for r in shown["clients"] if r["id"] == "case-rosa" for d in (r.get("journey") or {}).get("deadlines", []))
