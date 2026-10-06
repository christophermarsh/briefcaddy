"""The event ledger is tamper-evident (src/events.py chain, src/ledger_seal.py, tools/verify_ledger.py; brief R2).

A hash on every row that includes the previous row's, a daily seal the attorney sees, and a check that names the first row that does not match. Each test
changes the files the way someone with a text editor would, and the check says which row and how. Everyone here is made up."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import time
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import clock
import events
import ledger_seal

REPO = Path(__file__).resolve().parent.parent
TOOL = REPO / "tools" / "verify_ledger.py"
PEOPLE = ("Sam Attorney", "Jane Paralegal", "Kim Exemplo")


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    base = tmp_path / "firm" / "events.jsonl"
    monkeypatch.setenv("I485_EVENTS", str(base))
    return base


def write(n: int, start: int = 0) -> None:
    for i in range(start, start + n):
        if clock._now_override is not None:  # a frozen clock moves a second with each row, as a real one does (the test's monkeypatch puts it back)
            clock._now_override = clock._now_override + timedelta(seconds=1)
        events.record("settings", "changed", f"Changed setting number {i}", who=PEOPLE[i % 3], role="attorney", via="staff")


def lines(path: Path) -> list[str]:
    return path.read_text(encoding="utf-8").splitlines()


def put(path: Path, rows: list[str]) -> None:
    path.write_text("".join(r + "\n" for r in rows), encoding="utf-8")


def at(year: int, month: int, day: int, hour: int = 10, minute: int = 0, monkeypatch=None) -> None:
    monkeypatch.setattr(clock, "_now_override", datetime(year, month, day, hour, minute))


# -- the chain ------------------------------------------------------------------------------------------------------------------


def test_every_row_links_to_the_one_before_and_the_first_links_to_nothing(ledger):
    write(5)
    rows = list(events.rows(ledger))
    assert [r["prev"] for r in rows] == [""] + [r["hash"] for r in rows[:-1]]
    assert all(r["hash"] == events.row_hash(r) and len(r["hash"]) == 64 for r in rows)
    assert ledger_seal.verify(ledger)["line"].startswith("intact: 5 rows from ") and ledger_seal.verify(ledger)["ok"]


def test_the_hash_is_the_sha256_of_the_rows_canonical_json_without_the_hash(ledger):
    import hashlib

    write(1)
    row = next(events.rows(ledger))
    body = {k: v for k, v in row.items() if k != "hash"}
    text = json.dumps(body, sort_keys=True, ensure_ascii=False, separators=(",", ":"))
    assert row["hash"] == hashlib.sha256(text.encode("utf-8")).hexdigest()
    events.record("settings", "changed", "Changed the café’s hours", who="Zoë Exemplo", role="attorney")  # characters stay as they are, in the hash and in the file
    last = list(events.rows(ledger))[-1]
    assert last["who"] == "Zoë Exemplo" and "Zoë" in lines(events.files(ledger)[-1])[-1] and last["hash"] == events.row_hash(last)


def test_a_row_is_found_by_the_check_whatever_order_its_keys_are_in(ledger):
    write(3)
    path = events.files(ledger)[0]
    rows = [json.loads(x) for x in lines(path)]
    put(path, [json.dumps(dict(reversed(list(r.items()))), ensure_ascii=False, indent=None) for r in rows])  # the same rows, written another way
    assert ledger_seal.verify(ledger)["ok"]


# -- what each way of changing the files looks like ----------------------------------------------------------------------------------


def test_one_byte_changed_in_a_middle_row_names_that_row(ledger):
    write(9)
    path = events.files(ledger)[0]
    rows = lines(path)
    bad = rows[4].replace("setting number 4", "setting number 7")
    put(path, rows[:4] + [bad] + rows[5:])
    result = ledger_seal.verify(ledger)
    mine = json.loads(rows[4])
    assert not result["ok"] and result["problem"]["kind"] == "changed" and (result["problem"]["file"], result["problem"]["line"]) == (path.name, 5)
    assert result["problem"]["who"] == mine["who"] and result["problem"]["at"] == mine["at"]
    assert result["line"].startswith("does not match: the row written at ") and mine["who"] in result["line"] and "was changed" in result["line"] and f"{path.name}, line 5" in result["line"]


def test_a_removed_row_is_named_by_the_row_that_was_written_after_it(ledger):
    write(9)
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:3] + rows[4:])  # the fourth row is gone
    result = ledger_seal.verify(ledger)
    after = json.loads(rows[4])
    assert not result["ok"] and result["problem"]["kind"] == "removed" and result["problem"]["line"] == 4 and result["problem"]["at"] == after["at"]
    assert "a row was removed before the row written at " in result["line"]


def test_the_first_row_removed_is_seen_because_the_next_one_links_to_it(ledger):
    write(4)
    path = events.files(ledger)[0]
    put(path, lines(path)[1:])
    result = ledger_seal.verify(ledger)
    assert result["problem"]["kind"] == "removed" and result["problem"]["line"] == 1


def test_two_swapped_rows_are_named_where_the_order_breaks(ledger):
    write(9)
    path = events.files(ledger)[0]
    rows = lines(path)
    rows[3], rows[4] = rows[4], rows[3]
    put(path, rows)
    result = ledger_seal.verify(ledger)
    assert not result["ok"] and result["problem"]["kind"] == "moved" and result["problem"]["line"] == 4
    assert result["problem"]["at"] == json.loads(rows[3])["at"] and "out of order" in result["line"]


def test_a_file_cut_short_in_a_row_is_named_after_the_last_whole_row(ledger):
    write(6)
    path = events.files(ledger)[0]
    data = path.read_bytes()
    path.write_bytes(data[:-70])  # the last row loses its end, and its newline
    result = ledger_seal.verify(ledger)
    last_whole = json.loads(lines(path)[-2])
    assert not result["ok"] and result["problem"]["kind"] == "cut_short" and result["problem"]["at"] == last_whole["at"] and result["problem"]["line"] == 5
    assert f"{path.name} was cut short after the row written at " in result["line"]


def test_rows_cut_off_the_end_at_a_row_boundary_are_seen_by_the_days_seal(ledger, monkeypatch):
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(6)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    ledger_seal.nightly(ledger)  # the 1st is sealed
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:4])  # the last two rows are gone, and no row after them says so
    result = ledger_seal.verify(ledger)
    assert not result["ok"] and result["problem"]["kind"] == "cut_short" and result["problem"]["line"] == 4 and result["problem"]["at"] == json.loads(rows[3])["at"]
    assert "was cut short after the row written at 10/01/2026" in result["line"]


def test_a_day_with_fewer_rows_than_its_seal_counted_is_named(ledger, monkeypatch):
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(6)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    write(2, 6)
    at(2026, 10, 3, monkeypatch=monkeypatch)
    ledger_seal.nightly(ledger)  # the 1st and the 2nd are sealed
    path = events.files(ledger)[0]
    rows = lines(path)
    # a row from the middle of the 1st taken out, and the chain mended after it by someone who recomputed every later hash
    kept = [json.loads(r) for i, r in enumerate(rows) if i != 2]
    prev = ""
    for r in kept:
        r["prev"] = prev
        r["hash"] = events.row_hash(r)
        prev = r["hash"]
    put(path, [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in kept])
    result = ledger_seal.verify(ledger)
    assert not result["ok"] and result["problem"]["kind"] in ("gone", "day_short") and "10/01/2026" in result["line"]


def test_a_ledger_rewritten_whole_with_the_seals_gone_passes_and_the_seals_on_paper_would_not(ledger, monkeypatch):
    """What the chain does not prove (docs/security/threat_model.md): that is why the printed seals are the firm's defence."""
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(4)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    ledger_seal.nightly(ledger)
    on_paper = ledger_seal.read_anchors(ledger)[0]
    path = events.files(ledger)[0]
    rewritten, prev = [], ""
    for i, r in enumerate(json.loads(x) for x in lines(path)):
        r["what"] = f"Something else {i}"
        r["prev"] = prev
        r["hash"] = prev = events.row_hash(r)
        rewritten.append(json.dumps(r, ensure_ascii=False, separators=(",", ":")))
    put(path, rewritten)
    assert not ledger_seal.verify(ledger)["ok"]  # the file's own seals still disagree
    ledger_seal.anchors_path(ledger).unlink()  # the attacker holds the seals file too
    assert ledger_seal.verify(ledger)["ok"]  # so it passes
    assert on_paper["hash"] not in {r["hash"] for r in events.rows(ledger)}  # but the seal the firm wrote down names a row that is not there


def test_a_row_without_a_hash_after_the_chain_began_is_a_changed_row(ledger):
    write(4)
    path = events.files(ledger)[0]
    rows = lines(path)
    forged = json.loads(rows[1])
    forged.pop("hash"), forged.pop("prev")
    put(path, rows[:2] + [json.dumps(forged)] + rows[2:])
    result = ledger_seal.verify(ledger)
    assert result["problem"]["kind"] == "changed" and result["problem"]["line"] == 3


def test_a_line_that_is_not_a_row_in_the_middle_is_named_with_the_row_before_it(ledger):
    write(4)
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:2] + ["{not a row"] + rows[2:])
    result = ledger_seal.verify(ledger)
    assert result["problem"]["kind"] == "unreadable" and result["problem"]["line"] == 3 and "cannot be read" in result["line"]


# -- months, rows before the chain, many writers -----------------------------------------------------------------------------------------


def test_rows_across_a_month_boundary_link_and_verify(ledger, monkeypatch):
    at(2026, 9, 30, 23, 58, monkeypatch)
    write(3)
    at(2026, 10, 1, 0, 2, monkeypatch)
    write(3, 3)
    at(2026, 11, 2, monkeypatch=monkeypatch)  # a month with nothing between
    write(2, 6)
    names = [p.name for p in events.files(ledger)]
    assert names == ["events-2026-09.jsonl", "events-2026-10.jsonl", "events-2026-11.jsonl"]
    first_of = {p.name: json.loads(lines(p)[0]) for p in events.files(ledger)}
    last_of = {p.name: json.loads(lines(p)[-1]) for p in events.files(ledger)}
    assert first_of[names[0]]["prev"] == "" and first_of[names[1]]["prev"] == last_of[names[0]]["hash"] and first_of[names[2]]["prev"] == last_of[names[1]]["hash"]
    result = ledger_seal.verify(ledger)
    assert result["ok"] and result["rows"] == 8 and result["line"] == "intact: 8 rows from 09/30/2026 to 11/02/2026"


def test_a_month_whose_file_was_removed_is_seen_by_the_month_after_it(ledger, monkeypatch):
    at(2026, 9, 15, monkeypatch=monkeypatch)
    write(3)
    at(2026, 10, 15, monkeypatch=monkeypatch)
    write(3, 3)
    at(2026, 11, 15, monkeypatch=monkeypatch)
    write(3, 6)
    events.files(ledger)[1].unlink()
    result = ledger_seal.verify(ledger)
    assert result["problem"]["kind"] == "removed" and result["problem"]["file"] == "events-2026-11.jsonl" and result["problem"]["line"] == 1


def test_a_new_month_after_an_empty_one_links_to_the_last_row_before_it(ledger, monkeypatch):
    at(2026, 9, 15, monkeypatch=monkeypatch)
    write(2)
    (ledger.parent / "events-2026-10.jsonl").write_bytes(b"")  # a month's file that holds nothing
    at(2026, 11, 15, monkeypatch=monkeypatch)
    write(2, 2)
    assert ledger_seal.verify(ledger)["ok"]


def test_rows_written_before_the_chain_stay_as_they_are_and_are_counted(ledger):
    ledger.parent.mkdir(parents=True)
    old = [{"at": f"2026-08-0{i + 1}T10:00:00-04:00", "who": "Sam Attorney", "role": "attorney", "via": "staff", "case": None, "kind": "settings", "version": 1,
            "action": "changed", "what": f"An earlier change {i}"} for i in range(3)]
    path = ledger.parent / "events-2026-08.jsonl"
    put(path, [json.dumps(r, ensure_ascii=False, separators=(",", ":")) for r in old])
    before = lines(path)
    events.record("settings", "changed", "The first chained row", who="Sam Attorney", role="attorney")
    write(2, 1)
    assert lines(path)[:3] == before  # untouched
    chained = [r for r in events.rows(ledger) if "hash" in r]
    assert len(chained) == 3 and chained[0]["prev"] == ""  # the first chained row links to nothing: it is where the chain starts
    result = ledger_seal.verify(ledger)
    assert result["ok"] and result["rows"] == 3 and result["before"] == 3
    assert result["line"].endswith("(3 rows before the chain began)") and result["line"].startswith("intact: 3 rows from ")


def test_a_ledger_of_only_old_rows_is_intact_with_no_chain_yet(ledger):
    ledger.parent.mkdir(parents=True)
    put(ledger.parent / "events-2026-08.jsonl", [json.dumps({"at": "2026-08-01T10:00:00-04:00", "who": "A", "kind": "settings", "what": "x"})])
    result = ledger_seal.verify(ledger)
    assert result["ok"] and result["rows"] == 0 and result["before"] == 1 and result["line"] == "intact: no rows in the chain yet (1 rows before the chain began)"


def test_a_row_cut_short_by_a_crash_is_kept_apart_from_the_row_written_after_it(ledger):
    write(3)
    path = events.files(ledger)[0]
    with open(path, "ab") as f:
        f.write(b'{"at":"2026-10-04T10:00:00-04:00","who":"Sam Atto')  # the process died here
    write(2, 3)
    got = lines(path)
    assert len(got) == 6 and got[3].startswith('{"at":"2026-10-04T10:00:00-04:00","who":"Sam Atto') and json.loads(got[4])["prev"] == json.loads(got[2])["hash"]
    assert [r["what"] for r in events.rows(ledger)][-2:] == ["Changed setting number 3", "Changed setting number 4"]  # readers pass over the cut row
    result = ledger_seal.verify(ledger)
    assert not result["ok"] and result["problem"]["kind"] == "unreadable" and result["problem"]["line"] == 4


def test_four_processes_writing_at_once_keep_the_chain(ledger):
    code = ("import sys; sys.path.insert(0, %r); import events\n"
            "for i in range(75): events.record('settings', 'changed', 'Process %%s wrote %%s' %% (sys.argv[1], i), who='Writer ' + sys.argv[1], role='system', via='system')\n") % str(REPO / "src")
    env = dict(os.environ, I485_EVENTS=str(ledger))
    procs = [subprocess.Popen([sys.executable, "-c", code, str(n)], env=env) for n in range(4)]
    assert [p.wait(timeout=120) for p in procs] == [0, 0, 0, 0]
    result = ledger_seal.verify(ledger)
    assert result["ok"] and result["rows"] == 300, result["line"]
    got = list(events.rows(ledger))
    assert len({r["hash"] for r in got}) == 300 and {r["who"] for r in got} == {f"Writer {n}" for n in range(4)}
    stamps = [clock.parse(r["at"]) for r in got]
    assert stamps == sorted(stamps), "the order of the chain is the order of the clock"


def test_threads_and_a_month_turning_at_once_keep_the_chain(ledger, monkeypatch):
    import threading

    ticks = iter(range(10 ** 6))

    def now():  # the clock moves one minute with each row; the month turns during the run
        n = next(ticks)
        return datetime(2026, 9, 30, 23, 0, tzinfo=clock.zone()) + timedelta(minutes=n)

    monkeypatch.setattr(clock, "now", now)
    threads = [threading.Thread(target=write, args=(40, i * 40)) for i in range(4)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    assert len(events.files(ledger)) == 2
    assert ledger_seal.verify(ledger)["ok"] and ledger_seal.verify(ledger)["rows"] == 160


# -- the daily seal, the morning report, the line under Settings ------------------------------------------------------------------------------


def test_the_seal_file_the_morning_report_and_the_settings_line_agree(tmp_path, monkeypatch):
    from test_overnight import _folders, _run, _runner

    from review.server import ReviewApp
    import schema_path

    data = tmp_path / "data"
    monkeypatch.setenv("I485_EVENTS", str(data / "events.jsonl"))
    at(2026, 10, 2, 9, 0, monkeypatch)
    write(5)
    at(2026, 10, 2, 17, 30, monkeypatch)
    write(3, 5)  # the 2nd has eight rows
    at(2026, 10, 3, 2, 0, monkeypatch)  # the night's run
    root = _folders(tmp_path, ["c1"])
    _, lines_out = _run(tmp_path, root, _runner())
    report = (data / "batch_report.txt").read_text(encoding="utf-8")
    base = data / "events.jsonl"
    anchors = ledger_seal.read_anchors(base)
    assert len(anchors) == 1 and anchors[0]["day"] == "2026-10-02" and anchors[0]["rows"] == 8
    last_of_the_day = [r for r in events.rows(base) if r["at"].startswith("2026-10-02")][-1]
    assert anchors[0]["hash"] == last_of_the_day["hash"] and anchors[0]["at"] == last_of_the_day["at"]
    seal = f"The record's seal for 10/02/2026: {anchors[0]['hash'][:16]}, 8 rows that day."
    assert seal in report and any(seal in x for x in lines_out)
    assert oct(os.stat(ledger_seal.anchors_path(base)).st_mode & 0o777) == "0o600" and oct(os.stat(ledger_seal.check_path(base)).st_mode & 0o777) == "0o600"
    # the night's own rows are in the check (the run wrote some), and Settings reads what the check kept
    kept = ledger_seal.kept(base)
    app = ReviewApp(root, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, portal_root=tmp_path / "portal")
    line = app.settings("attorney")["record"]
    assert line["ok"] is True and line["text"] == f"The record is intact as of 10/03/2026 02:00 ({kept['rows']} rows)" and kept["rows"] >= 8
    assert f"The record is intact: {kept['rows']} rows" in report
    assert ledger_seal.verify(base)["rows"] >= kept["rows"]  # rows the run wrote after its own check (none, or the last Clio line) are still chained


def test_a_day_is_sealed_once_and_today_is_never_sealed(ledger, monkeypatch):
    at(2026, 10, 2, monkeypatch=monkeypatch)
    write(3)
    ledger_seal.nightly(ledger)
    assert ledger_seal.read_anchors(ledger) == []  # the 2nd is not over
    at(2026, 10, 3, monkeypatch=monkeypatch)
    ledger_seal.nightly(ledger)
    ledger_seal.nightly(ledger)
    assert [a["day"] for a in ledger_seal.read_anchors(ledger)] == ["2026-10-02"]
    write(1, 3)
    at(2026, 10, 5, monkeypatch=monkeypatch)  # nothing on the 4th: a night with no complete day with rows adds none, a missed night is made up
    ledger_seal.nightly(ledger)
    assert [a["day"] for a in ledger_seal.read_anchors(ledger)] == ["2026-10-02", "2026-10-03"]
    assert ledger_seal.seal_text(ledger_seal.read_anchors(ledger)[-1]).endswith(", 1 row that day.")


def test_no_seal_is_written_for_a_record_that_does_not_match_and_settings_says_so_in_words(ledger, monkeypatch):
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(5)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:2] + [rows[2].replace("number 2", "number 9")] + rows[3:])
    text = ledger_seal.nightly(ledger)
    assert "does not match" in text and "No new seal" in text and not ledger_seal.anchors_path(ledger).exists()
    line = ledger_seal.settings_line(ledger)
    assert line["ok"] is False and line["text"].startswith("does not match: the row written at 10/01/2026 ") and "was changed" in line["text"]


def test_settings_before_any_check_says_so_and_never_walks_the_ledger(ledger, monkeypatch):
    write(3)
    monkeypatch.setattr(ledger_seal, "verify", lambda *a, **k: pytest.fail("a view walked the ledger"))
    line = ledger_seal.settings_line(ledger)
    assert line["ok"] is None and "not been checked yet" in line["text"]


def test_the_register_item_turns_due_and_carries_the_line_when_a_row_does_not_match(ledger, monkeypatch):
    import maintenance

    write(4)
    ledger_seal.nightly(ledger)
    item = next(i for i in maintenance.status() if i["id"] == "record_seal")
    assert item["findings"] == [] and item["cadence"] == "quarterly" and item["responsible"] == "firm" and item["firm_steps"] and item["firm_what"]
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:1] + [rows[1].replace("number 1", "number 8")] + rows[2:])
    ledger_seal.nightly(ledger)
    item = next(i for i in maintenance.status() if i["id"] == "record_seal")
    assert item["due"] and item["findings"] and item["findings"][0].startswith("does not match: ")


def test_the_tool_prints_intact_and_exits_0_or_names_the_row_and_exits_1(ledger, monkeypatch):
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(4)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    ledger_seal.nightly(ledger)
    env = dict(os.environ, I485_EVENTS=str(ledger))
    ok = subprocess.run([sys.executable, str(TOOL), "--anchors"], env=env, capture_output=True, text=True, timeout=60)
    anchor = ledger_seal.read_anchors(ledger)[0]
    assert ok.returncode == 0 and ok.stdout.splitlines()[0] == "intact: 4 rows from 10/01/2026 to 10/01/2026"
    assert ok.stdout.splitlines()[1] == f"10/01/2026  {anchor['hash'][:16]}, 4 rows that day."
    path = events.files(ledger)[0]
    rows = lines(path)
    put(path, rows[:2] + [rows[2].replace("number 2", "number 5")] + rows[3:])
    bad = subprocess.run([sys.executable, str(TOOL)], env=env, capture_output=True, text=True, timeout=60)
    assert bad.returncode == 1 and bad.stdout.startswith("does not match: the row written at 10/01/2026 ") and "events-2026-10.jsonl, line 3" in bad.stdout


def test_the_check_reads_files_and_writes_no_row(ledger):
    write(5)
    before = {p.name: p.read_bytes() for p in ledger.parent.iterdir()}
    ledger_seal.verify(ledger)
    assert {p.name: p.read_bytes() for p in ledger.parent.iterdir()} == before


# -- what a row costs --------------------------------------------------------------------------------------------------------------------


def test_a_row_costs_little_before_and_after_the_chain(ledger):
    if os.getloadavg()[0] > (os.cpu_count() or 1) / 2:
        pytest.skip("a busy machine: the timing would say nothing")
    n = 400
    old = ledger.parent / "old-events-2026-10.jsonl"
    old.parent.mkdir(parents=True, exist_ok=True)
    row = {"at": clock.stamp(), "who": "Sam", "role": "attorney", "via": "staff", "case": None, "kind": "settings", "version": 1, "action": "changed", "what": "x" * 80}
    t0 = time.perf_counter()
    for _ in range(n):  # the writer as it was: one whole line appended, nothing read
        fd = os.open(old, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
        os.write(fd, (json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n").encode("utf-8"))
        os.close(fd)
    before = (time.perf_counter() - t0) / n
    t0 = time.perf_counter()
    write(n)
    after = (time.perf_counter() - t0) / n
    print(f"a row: {before * 1e6:.0f} microseconds before the chain, {after * 1e6:.0f} after")
    assert after < 0.005, f"{after * 1e3:.2f} ms a row"


# -- the exit and the backups carry the seals ---------------------------------------------------------------------------------------------


def test_a_backup_holds_the_ledger_and_its_seals_and_a_restored_ledger_checks_against_them(tmp_path, monkeypatch):
    import zipfile

    import backups

    data = tmp_path / "data"
    base = data / "events.jsonl"
    monkeypatch.setenv("I485_EVENTS", str(base))
    at(2026, 10, 1, monkeypatch=monkeypatch)
    write(3)
    at(2026, 10, 2, monkeypatch=monkeypatch)
    ledger_seal.nightly(base)
    made = backups.make_backup(tmp_path / "bak", data)
    assert not made["problems"]
    with zipfile.ZipFile(made["archive"]) as z:
        names = z.namelist()
    assert {"data/ledger_anchors.jsonl", "data/events-2026-10.jsonl", "data/ledger_check.json"} <= set(names) and not [n for n in names if n.endswith(".lock")]
    restored = tmp_path / "restored"
    backups.restore_into(Path(made["archive"]), None, restored)
    found = ledger_seal.verify(restored / "data" / "events.jsonl")
    assert found["ok"] and found["rows"] == 3 and found["anchors"] == 1


def test_the_export_carries_the_seals_beside_the_ledger(tmp_path, monkeypatch):
    sys.path.insert(0, str(REPO / "tools"))
    import export_firm
    import firm_world
    import zipfile

    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    f = firm_world.make_firm(tmp_path, cases=1)
    base = events.base_path(f["data"])
    write(2)
    ledger_seal.write_anchors(base, [{"day": "2026-10-01", "at": "2026-10-01T10:00:00-04:00", "hash": "ab" * 32, "rows": 2}])
    done = export_firm.everything(export_firm.default_where(f["clients"], f["portal"], f["users"]), who="Sam Attorney", role="attorney", via="staff")
    with zipfile.ZipFile(done["path"]) as z:
        assert z.read("ledger/ledger_anchors.jsonl") == ledger_seal.anchors_path(base).read_bytes()
        assert "ledger/ledger_check.json" not in z.namelist() and any(n.startswith("ledger/events-") for n in z.namelist())
