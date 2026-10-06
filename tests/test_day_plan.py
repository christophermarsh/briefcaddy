# ruff: noqa: F811  (the fixtures imported from the other test files are used as arguments)
"""Today, the paralegal's day plan (src/day_plan.py): which cases can be got to a signed packet today, with each one's next deadline, the steps to a signed packet,
who holds them (the client, the office, the attorney: src/holders.py) and the three things to do first.

A made-up firm of twelve Exemplo cases, with every holder in it: two cases ready, cases the office holds, a case that waits for the attorney, cases that wait on the client
only, deadlines in different orders, a restricted case a paralegal is not named on, one she is, and a filed case. The review app, the roster, the approvals registry, the
deadlines and the restrictions are the real ones. What holds each packet is set case by case (the lines packet.plan would make for a case with those papers in its folder:
building twelve complete packets would test the packet and not the plan); the plan's own lines, their holders and the light plan's likeness to the full one are checked on
real packet folders at the end. Everyone here is made up."""

from __future__ import annotations

import json
import sys
from datetime import date, timedelta
from pathlib import Path

import pytest

import day_plan
import deadlines_set
import holders
import packet
import restricted
from holders import ATTORNEY, CLIENT, OFFICE, held

sys.path.insert(0, str(Path(__file__).resolve().parent))
import test_packet as tp  # noqa: E402
from test_approvals_queue import ATTORNEY as SAM, PARALEGAL, REASON, call, firm, server, sign_in, write_case  # noqa: E402,F401
from test_packet import filled  # noqa: E402,F401

TODAY = date(2026, 10, 5)  # test_approvals_queue's clock
PEOPLE = [{"email": PARALEGAL, "name": "Jane Doe", "role": "paralegal"}, {"email": SAM, "name": "Sam Attorney", "role": "attorney"}]


def iso(n: int) -> str:
    return (TODAY + timedelta(days=n)).isoformat()


def us(n: int) -> str:
    return (TODAY + timedelta(days=n)).strftime("%m/%d/%Y")


def lines(*held_lines: tuple[str, str]) -> list:
    return [held(h, t) for h, t in held_lines]


# case id -> (the client's name, what holds its packet, the days to its deadline or None, restricted)
OFFICE_LINES = [(OFFICE, f"Office line {n}.") for n in range(1, 6)]
FIRM = {
    "c01": ("Rosa Exemplo", [], 9, None),
    "c02": ("Ana Exemplo", [], 2, None),
    "c03": ("Marcos Exemplo", OFFICE_LINES[:3], 2, None),
    "c04": ("Luana Exemplo", [(OFFICE, "Save the office under Settings."), (CLIENT, "Missing: Passport.")], 2, None),
    "c05": ("Tiago Exemplo", [], 4, None),  # nothing in the packet's own lines; the attorney's card (an alert from the reading) is the one step
    "c06": ("Beatriz Exemplo", [(CLIENT, "Missing: Birth certificate."), (OFFICE, "Sort the documents."), (CLIENT, "Missing: I-94.")], None, None),
    "c07": ("Caio Exemplo", [(CLIENT, "Missing: Passport."), (CLIENT, "Missing: Birth certificate."), (CLIENT, "Missing: I-94.")], 5, None),
    "c08": ("Marisol Exemplo", [(CLIENT, "Missing: Court order.")], 1, None),
    "c09": ("Joel Exemplo", OFFICE_LINES, 30, None),
    "c10": ("Ines Exemplo", [(OFFICE, "Hidden office line.")], 1, "closed"),
    "c11": ("Davi Exemplo", [(CLIENT, "Missing: Custody order.")], 7, "named"),
    "c12": ("Lia Exemplo", [(OFFICE, "A filed case's line.")], None, "filed"),
}
READY = ["c02", "c01"]
WORK = ["c04", "c03", "c05", "c09", "c06"]
CLIENT_ONLY = ["c08", "c07", "c11"]


@pytest.fixture
def day_firm(firm, server, monkeypatch):
    real = packet.plan

    def plan(client_dir, row, schema=None, _building=False, light=False):
        case = Path(client_dir).name
        if case not in FIRM:
            return real(client_dir, row, schema, _building, light)
        found = FIRM[case][1]
        problems = [held(h, t) for h, t in found]
        return {"summary": row.get("summary") or {}, "ready": not problems, "problems": problems, "held": [{"text": str(p), "holder": holders.holder_of(p)} for p in problems], "filing": "i485"}

    monkeypatch.setattr(packet, "plan", plan)
    firm.clients.mkdir(parents=True, exist_ok=True)
    for case, (name, _found, days, how) in FIRM.items():
        d = write_case(firm.clients, case, name, alert=case == "c05")
        if days is not None:
            deadlines_set.add(d, f"Deadline of {name}", iso(days), PARALEGAL, "", "Sam Attorney", PEOPLE)
        if how in ("closed", "named"):
            restricted.mark(d, True, REASON, "Sam Attorney", "attorney")
        if how == "named":
            restricted.name_person(d, PARALEGAL, True, "Sam Attorney", "attorney", "Jane Doe")
        if how == "filed":
            status = json.loads((d / "status.json").read_text(encoding="utf-8"))
            (d / "status.json").write_text(json.dumps(status | {"filed_at": "2026-09-30T10:00:00+00:00", "filed_by": "Sam Attorney"}), encoding="utf-8")
    server.app.roster.wait()
    return server


def today(srv, who, **q):
    srv.app.roster.wait()
    cookie = sign_in(srv.base, who)
    path = "/api/today" + ("?" + "&".join(f"{k}={v}" for k, v in q.items()) if q else "")
    status, body = call(srv.base + path, cookie)
    assert status == 200, body
    return body


def order(got) -> list[str]:
    return [r["case"] for r in got["rows"]]


# -- the list --------------------------------------------------------------------------------------------------------------------------------


def test_the_order_and_the_groups(day_firm):
    got = today(day_firm, PARALEGAL)
    assert order(got) == READY + WORK + CLIENT_ONLY  # ready first, the office's cases next, the cases that wait only on the client last; each by deadline, then fewest steps
    assert [(g["id"], g["count"]) for g in got["groups"]] == [("ready", 2), ("work", 5), ("client", 3)]
    assert {r["case"]: r["group"] for r in got["rows"]} == {**{c: "ready" for c in READY}, **{c: "work" for c in WORK}, **{c: "client" for c in CLIENT_ONLY}}
    assert got["total"] == 10 and got["page"] == 1 and got["pages"] == 1 and got["size"] == day_plan.PAGE == 50
    by = {r["case"]: r for r in got["rows"]}
    assert [by[c]["deadline"]["date_text"] if by[c]["deadline"] else None for c in ["c02", "c01", "c04", "c05", "c09", "c06"]] == [us(2), us(9), us(2), us(4), us(30), None]
    assert all(r["deadline"] is None or r["deadline"]["date_text"][2] == "/" for r in got["rows"])  # MM/DD/YYYY


def test_each_case_says_its_steps_its_holders_and_the_three_things_to_do_first(day_firm):
    by = {r["case"]: r for r in today(day_firm, PARALEGAL)["rows"]}
    assert by["c01"]["steps"] == 0 and by["c01"]["lines"] == [] and by["c01"]["counts"] == {"client": 0, "office": 0, "attorney": 0}
    assert by["c03"]["steps"] == 3 and by["c03"]["counts"] == {"client": 0, "office": 3, "attorney": 0}
    assert by["c04"]["steps"] == 2 and by["c04"]["counts"] == {"client": 1, "office": 1, "attorney": 0}
    assert [(x["holder"], x["text"]) for x in by["c04"]["lines"]] == [(OFFICE, "Save the office under Settings."), (CLIENT, "Missing: Passport.")]  # the paralegal's own first
    assert by["c05"]["steps"] == 1 and by["c05"]["counts"] == {"client": 0, "office": 0, "attorney": 1}  # the attorney's count is the approvals registry's, not a second one
    assert [(x["holder"], x["text"]) for x in by["c05"]["lines"]] == [(ATTORNEY, "Criminal history in the folder")]  # in the card's own title
    assert [(x["holder"], x["text"]) for x in by["c06"]["lines"]] == [(OFFICE, "Sort the documents."), (CLIENT, "Missing: Birth certificate."), (CLIENT, "Missing: I-94.")]
    assert by["c09"]["steps"] == 5 and [x["text"] for x in by["c09"]["lines"]] == ["Office line 1.", "Office line 2.", "Office line 3."]  # five steps, the first three said
    assert by["c07"]["counts"] == {"client": 3, "office": 0, "attorney": 0} and by["c07"]["client"] == ["Missing: Passport.", "Missing: Birth certificate.", "Missing: I-94."]
    assert by["c06"]["client"] == []  # only the cases that wait on the client alone carry the form's items


def test_the_attorneys_own_items_are_marked_in_the_attorneys_view_and_it_is_the_same_screen(day_firm):
    para, att = today(day_firm, PARALEGAL), today(day_firm, SAM)
    assert order(att) == [c for c in order(para) if c != "c11"]  # the cases the attorney may open: the same screen, without the restricted case she is not named on
    mark = {(r["case"], x["text"]): x["mine"] for r in att["rows"] for x in r["lines"]}
    assert mark[("c05", "Criminal history in the folder")] is True and mark[("c03", "Office line 1.")] is False
    assert all(x["mine"] == (x["holder"] == OFFICE) for r in para["rows"] for x in r["lines"])  # the paralegal's own are the office's


def test_a_restricted_case_is_in_no_row_count_or_line_for_a_paralegal_not_named_on_it(day_firm):
    para = today(day_firm, PARALEGAL)
    assert "c10" not in order(para) and "Ines" not in json.dumps(para) and "Hidden office line" not in json.dumps(para)
    assert "c11" in order(para) and "c12" not in order(para)  # named on the one; the filed case is not a case to plan
    assert para["total"] == 10 and sum(g["count"] for g in para["groups"]) == 10
    sam = today(day_firm, SAM)  # an attorney not named on a restricted case is not told of it either (the stricter rule: src/restricted.py)
    assert "c10" not in order(sam) and "c11" not in order(sam) and "Hidden office line" not in json.dumps(sam) and "Davi" not in json.dumps(sam)
    restricted.name_person(day_firm.app.data_root / "c10", SAM, True, "Sam Attorney", "attorney", "Sam Attorney")
    day_firm.app.roster.touch("c10")
    assert "c10" in order(today(day_firm, SAM))  # named: the attorney is told


def test_the_list_is_paged_and_names_no_file_and_no_code(day_firm):
    first, second = today(day_firm, PARALEGAL, size=4, page=1), today(day_firm, PARALEGAL, size=4, page=2)
    assert first["pages"] == 3 and len(first["rows"]) == 4 and len(second["rows"]) == 4 and order(first) + order(second) == (READY + WORK + CLIENT_ONLY)[:8]
    assert today(day_firm, PARALEGAL, size=4, page=99)["page"] == 3
    status, body = call(day_firm.base + "/api/today?page=x", sign_in(day_firm.base, PARALEGAL))
    assert status == 400
    everything = json.dumps(today(day_firm, PARALEGAL))
    assert ".pdf" not in everything and ".json" not in everything and "—" not in everything and "–" not in everything  # (no em dashes)


def test_my_work_and_the_morning_report_say_how_many_cases_can_reach_a_signed_packet_today(day_firm):
    para = day_firm.app.roster.day_plans(None)
    assert day_plan.line(1) == "1 case can reach a signed packet today" and day_plan.line(0) == "0 cases can reach a signed packet today"
    cookie = sign_in(day_firm.base, PARALEGAL)
    status, work = call(day_firm.base + "/api/work", cookie)
    assert status == 200 and work["today"]["can_reach"] == 4 and work["today"]["line"] == "4 cases can reach a signed packet today"  # two ready, and the two only the office holds
    assert today(day_firm, PARALEGAL)["can_reach"] == 4 and today(day_firm, PARALEGAL)["line"] == work["today"]["line"]
    assert len(para) == 11  # nobody signed in sees the restricted cases too: the filed case is the only one left out
    entries = day_firm.app.roster.entries
    closed = {c for c, e in entries.items() if e["closed"]}
    assert closed == {"c10", "c11"}
    # the morning report is read by everyone: a restricted case is not in it, whoever is named on it (c10 is a case only the office holds: counted, it would make five)
    assert day_plan.night_line(entries, closed) == "4 cases can reach a signed packet today."
    assert day_plan.night_line(entries, set()) == "5 cases can reach a signed packet today."


def test_the_roster_keeps_the_plan_and_reads_a_changed_case_again(day_firm):
    entry = day_firm.app.roster.entries["c04"]
    assert entry["day"]["steps"] == 2 and entry["day"]["filing"] == "i485" and not entry["day"]["ready"]
    assert day_firm.app.roster.entries["c12"]["day"] is None  # a filed case has none
    FIRM["c04"] = (FIRM["c04"][0], [], FIRM["c04"][2], None)  # the office and the client have done their parts
    try:
        day_firm.app.roster.touch("c04")
        got = today(day_firm, PARALEGAL)
        assert "c04" in order(got) and next(r for r in got["rows"] if r["case"] == "c04")["group"] == "ready"
    finally:
        FIRM["c04"] = ("Luana Exemplo", [(OFFICE, "Save the office under Settings."), (CLIENT, "Missing: Passport.")], 2, None)


# -- the plan itself, on real packet folders -------------------------------------------------------------------------------------------------


@pytest.fixture
def i485(tmp_path, monkeypatch):
    """The I-485 alone with the firm's office saved (as tests/test_packet.py has it), so a packet with every paper is ready."""
    import settings
    from conftest import save_shipped_office_as_the_firms

    monkeypatch.setattr(settings, "PATH", tmp_path / "office-settings.json")
    save_shipped_office_as_the_firms(tmp_path / "office-settings.json")
    monkeypatch.setattr(packet, "load_schema", lambda *path: tp.FULL_SCHEMA | {"forms": ["i485"], "cover_letter": False, "index_sheet": True})
    return tmp_path


def case_folder(root: Path, name: str, filled, drop: tuple[str, ...] = ("unclassified",)) -> Path:
    (root / name).mkdir()
    return tp._client(root / name, filled, {k: v for k, v in tp.DOCS.items() if v not in drop})


def test_a_real_packet_ready_to_sign_has_no_steps_and_the_cards_split_between_the_office_and_the_attorney(i485, filled):
    d = case_folder(i485, "ready", filled)
    done = day_plan.summary(d, tp.ROW_DONE, [])
    assert done["ready"] and done["steps"] == 0 and done["first"] == [] and day_plan.group_of(done) == "ready"
    row = tp.ROW_DONE | {"fix": 1, "check": 1, "attorney": 2}
    busy = day_plan.summary(d, row, [{"what": "Sign-off one"}, {"what": "Sign-off two"}])  # the attorney's cards are the approvals registry's two, counted once
    assert busy["counts"] == {"client": 0, "office": 2, "attorney": 2} and busy["steps"] == 4 and not busy["ready"]
    assert [x["holder"] for x in busy["first"]] == [OFFICE, ATTORNEY, ATTORNEY]
    short = day_plan.summary(case_folder(i485, "short", filled, ("unclassified", "i360_approval")), tp.ROW_DONE, [])  # the I-360 approval notice is not in the folder
    assert short["counts"] == {"client": 1, "office": 0, "attorney": 0} and day_plan.group_of(short) == "client" and short["client"][0].startswith("Missing: ")


def test_the_light_plan_holds_exactly_what_the_full_plan_holds_and_every_line_has_a_holder(i485, filled):
    cases = {"every paper": (("unclassified",), tp.ROW_DONE), "a paper short": (("unclassified", "i360_approval"), tp.ROW_DONE | {"check": 2}),
             "cards only the attorney settles": (("unclassified",), tp.ROW_DONE | {"attorney": 1}), "an unsorted file": ((), tp.ROW_DONE | {"fix": 1, "attorney": 1})}
    plans = {}
    for name, (drop, row) in cases.items():
        d = case_folder(i485, name.replace(" ", "-"), filled, drop)
        full, light = packet.plan(d, row), packet.plan(d, row, light=True)
        assert light["problems"] == full["problems"] and light["ready"] == full["ready"] and light["held"] == full["held"], name
        assert [h["text"] for h in full["held"]] == [str(p) for p in full["problems"]] and all(h["holder"] in holders.HOLDERS for h in full["held"])
        plans[name] = {h["text"]: h["holder"] for h in full["held"]}
    assert plans["every paper"] == {}
    assert plans["a paper short"] == {"2 review cards still open (2 check).": OFFICE, "Missing: I-360 approval notice.": CLIENT} or \
        sorted(plans["a paper short"].values()) == [CLIENT, OFFICE]  # a paper the client sends; the cards the paralegal works
    assert plans["cards only the attorney settles"] == {"1 review card still open (1 attorney).": ATTORNEY}  # the attorney's own are the approvals registry's
    mixed = plans["an unsorted file"]
    assert mixed["2 review cards still open (1 fix, 1 attorney)."] == OFFICE and sorted(mixed.values()) == [OFFICE, OFFICE]  # unsorted documents are the office's to sort
