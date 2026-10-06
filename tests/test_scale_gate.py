"""The gate on one case, in the mode the product ships in (the lists read their own copy of every case): a single case is asked of its own folder, never of that copy
(docs/decisions.md, H5 after verification). Everyone here is made up."""

from __future__ import annotations

import sys
from pathlib import Path

import pytest

import restricted

sys.path.insert(0, str(Path(__file__).resolve().parent))

import scale_world  # noqa: E402


def test_one_case_is_asked_of_its_own_folder_never_of_the_lists_copy(tmp_path):
    """The lists keep a copy of every case and may be half a second behind; the gate on one case is not a list. A record that cannot be read, or a restriction another process wrote
    with nothing told to the app, closes the case to a paralegal on the very next request."""
    w = scale_world.build(tmp_path, 14)
    try:
        rows, _ = w.every_row("paralegal")
        first, second = [r["id"] for r in rows if not r.get("restricted")][:2]
        assert w.call(f"/api/items?client={first}", "paralegal")[1] == 200
        (w.clients / first / "access.json").write_text("{not json", encoding="utf-8")  # an attorney's record that cannot be read keeps the case closed (restricted.record)
        assert w.call(f"/api/items?client={first}", "paralegal")[1] == 404  # at once, nothing touched, no ledger row
        assert w.call(f"/api/items?client={second}", "paralegal")[1] == 200
        restricted.mark(w.clients / second, True, "A minor.", "Ana Attorneyexemplo", "attorney")  # written by another process: the app was not told
        assert w.call(f"/api/items?client={second}", "paralegal")[1] == 404
        assert w.call(f"/api/items?client={second}", "attorney")[1] == 200
    finally:
        w.stop()


def test_a_protected_kind_with_no_record_is_closed_on_every_request_of_a_burst(tmp_path):
    from portal.store import PortalStore

    w = scale_world.build(tmp_path, 14)
    try:
        store = PortalStore(w.portal)
        for cid, extra in (("burst-vawa", {"track": "vawa"}), ("burst-dw", {"docketwise_matter_type": "VAWA Self Petition"})):
            store.add_client(cid, "Burst Exemplo", email=f"{cid}@example.com", language="es", consent={"email": True})
            store.update_profile(cid, **extra)
            (w.clients / cid / "source").mkdir(parents=True)  # an import made before restriction records: documents and no record
            (w.clients / cid / "source" / "passport.pdf").write_bytes(b"%PDF-1.4 made up")
        w.get("/api/overview?counts=1", "paralegal")
        for cid in ("burst-vawa", "burst-dw"):
            answers = [w.call(f"{path}?client={cid}", "paralegal")[1] for path in ("/api/engagement", "/api/documents", "/api/items", "/api/engagement", "/api/journey")]
            assert answers == [404] * 5, (cid, answers)  # five quick asks, five answers as for a made-up id
            assert (w.clients / cid / restricted.FILE).exists()  # and the record was written by the first
            assert cid not in {r["id"] for r in w.every_row("paralegal")[0]}
    finally:
        w.stop()


def test_a_ledger_row_over_64_kb_caught_mid_append_is_taken_whole_next_time(tmp_path):
    import json

    import events

    month = tmp_path / "events-2026-10.jsonl"
    month.write_text(json.dumps({"case": "a"}) + "\n", encoding="utf-8")
    tail = events.Tail(tmp_path / "events.jsonl")
    tail.start()
    row = (json.dumps({"case": "e", "note": "x" * 150000}) + "\n").encode()
    with open(month, "ab") as f:
        f.write(row[:100000])  # the app is half way through writing a row longer than a block
    late = events.Tail(tmp_path / "events.jsonl")
    assert late.start() == {month.name: len(json.dumps({"case": "a"}) + "\n")}  # a reader starting now stands before the unfinished row, not inside it
    with open(month, "ab") as f:
        f.write(row[100000:])
    assert late.take() == {"e"} and tail.take() == {"e"}


def test_a_clients_own_signing_in_the_portal_reaches_the_lists_through_the_ledger(tmp_path, monkeypatch):
    """The portal is another process and has no roster to touch: eoir26a._save writes a ledger row that names the case, and the review app's lists follow the ledger."""
    import events
    import eoir26a

    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    case = tmp_path / "clients" / "case-x"
    case.mkdir(parents=True)
    tail = events.Tail(tmp_path / "events.jsonl")
    tail.start()
    eoir26a._save(case, {"request": None, "history": []}, "signed", "The client signed the fee waiver request (EOIR-26A) in the portal", who="The client", role="client")
    assert tail.take() == {"case-x"}


def test_a_second_person_reading_the_inbox_is_told_it_is_already_being_read(tmp_path):
    w = scale_world.build(tmp_path, 14)
    try:
        first = w.call("/api/inbox/read", "attorney", {})
        again = w.call("/api/inbox/read", "paralegal", {})
        assert first[1] == 200 and again[1] == 200 and "already" not in first[2]
        assert again[2]["job"]["id"] == first[2]["job"]["id"] and again[2]["already"].startswith("Already being read")  # in words, not an error
        assert "result" not in again[2]["job"] and "result" not in w.get(f"/api/jobs/firm?id={again[2]['job']['id']}", "paralegal")["job"]  # the answer is the starter's
    finally:
        w.stop()


def test_the_saved_copy_and_the_job_files_are_for_the_owner_only(tmp_path):
    """data/roster.json holds every case's identifiers (restricted cases included) and a job holds a case id and a file name: 0600, and the jobs folder 0700, as query.db is."""
    import os
    import stat

    import jobs

    probe = tmp_path / "private-mode-probe"
    probe.mkdir(mode=0o700)
    descriptor = os.open(probe / "marker", os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
    os.close(descriptor)
    modes = (stat.S_IMODE((probe / "marker").stat().st_mode), stat.S_IMODE(probe.stat().st_mode))
    if modes != (0o600, 0o700):
        pytest.skip(f"Filesystem cannot enforce Unix private creation modes: file={modes[0]:o}, directory={modes[1]:o}; effective Windows ACL isolation unverified")
    w = scale_world.build(tmp_path, 14)
    try:
        saved = w.data / "roster.json"
        assert saved.exists() and stat.S_IMODE(saved.stat().st_mode) == 0o600
        job = jobs.submit(w.app.jobs_root, "staff_upload", w.manifest["case_ids"][0], by="Paulo", args={"name": "scan.pdf"})
        queued = w.app.jobs_root / f"{job['id']}.json"
        assert stat.S_IMODE(queued.stat().st_mode) == 0o600
        jobs._finish(w.app.jobs_root, dict(job), "done", result={})
        finished = w.app.jobs_root / "done" / f"{job['id']}.json"
        assert finished.exists() and stat.S_IMODE(finished.stat().st_mode) == 0o600
        for p in (w.app.jobs_root, w.app.jobs_root / "done", w.app.jobs_root / "locks"):
            assert stat.S_IMODE(p.stat().st_mode) == 0o700, p
    finally:
        w.stop()
