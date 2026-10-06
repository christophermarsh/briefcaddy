"""No scan is lost: two scans added to a case in a row, the second one's reading fails or dies, and the overnight run still reads it. The record of what the overnight run last
read (batch_state.json) is a signature of the folder; a reading writes into it only what it read (docs/decisions.md, H5 after verification). Everyone here is made up."""

from __future__ import annotations

import io
import json
import os
import time

import pytest
from pypdf import PdfWriter

import inbox
import jobs
import overnight
from review import front_desk


def pdf_bytes() -> bytes:
    w = PdfWriter()
    w.add_blank_page(width=200, height=200)
    out = io.BytesIO()
    w.write(out)
    return out.getvalue()


@pytest.fixture
def firm(tmp_path):
    clients = tmp_path / "clients"
    case = clients / "case-x"
    source = case / "source"
    source.mkdir(parents=True)
    (source / "old.pdf").write_bytes(pdf_bytes())
    old = time.time() - 3600
    os.utime(source / "old.pdf", (old, old))
    (case / "fact_graph.json").write_text("{}", encoding="utf-8")
    (case / "meta.json").write_text(json.dumps({"source_folder": str(source), "processed_at": "2026-10-02T02:00:00-04:00"}), encoding="utf-8")
    state = tmp_path / "batch_state.json"
    state.write_text(json.dumps({"case-x": {"status": "done", "signature": overnight.source_signature(source)}}), encoding="utf-8")
    return {"clients": clients, "case": case, "source": source, "state": state}


def stage(f, name):
    return front_desk.stage_upload(f["clients"], None, "case-x", name, pdf_bytes(), "Paulo Paralegal")


def finish(f, staged, pending):
    """The end of the job that read this scan (the worker runs read_staff_upload; this is its last step)."""
    inbox._keep_overnight_honest("case-x", f["source"], f["case"] / "meta.json", staged.get("meta_mtime"), staged.get("newest") or 0.0, staged.get("before") or "", f["state"], True,
                                 pending=pending, this=staged["name"])


def choose(f):
    state = json.loads(f["state"].read_text(encoding="utf-8"))
    return overnight.choose(f["clients"], f["clients"], state, False, None)[0]


def test_two_scans_in_a_row_the_second_reading_dies_and_the_overnight_run_reads_it(firm):
    first, second = stage(firm, "first.pdf"), stage(firm, "second.pdf")  # both are on the case before either is read
    finish(firm, first, pending=[second["name"]])  # the first is read; the second's job is waiting, then dies with its worker
    assert choose(firm) == [("case-x", "documents changed")]  # the record does not cover the second scan: tonight reads it
    # a third scan, read while the second's job is dead: the record still leaves the second out
    third = stage(firm, "third.pdf")
    finish(firm, third, pending=[second["name"]])
    assert choose(firm) == [("case-x", "documents changed")]


def test_every_scan_read_leaves_nothing_for_the_night(firm):
    first, second = stage(firm, "first.pdf"), stage(firm, "second.pdf")
    finish(firm, first, pending=[second["name"]])
    finish(firm, second, pending=[])
    assert choose(firm) == []  # both were read by their own jobs: nothing is read twice


def test_a_scan_that_could_not_be_read_changes_nothing_in_the_record(firm):
    staged = stage(firm, "first.pdf")
    before = firm["state"].read_text(encoding="utf-8")
    inbox._keep_overnight_honest("case-x", firm["source"], firm["case"] / "meta.json", staged.get("meta_mtime"), staged.get("newest") or 0.0, staged.get("before") or "", firm["state"], False,
                                 pending=[], this=staged["name"])
    assert firm["state"].read_text(encoding="utf-8") == before and choose(firm) == [("case-x", "documents changed")]


def test_a_document_added_by_hand_beside_a_reading_still_waits_for_its_run(firm):
    staged = stage(firm, "first.pdf")
    (firm["source"] / "by-hand.pdf").write_bytes(pdf_bytes())  # someone copied a file in: no job, no record
    finish(firm, staged, pending=[])
    assert choose(firm) == [("case-x", "documents changed")]  # the record was left alone, as it was before wave H5


def test_the_job_leaves_out_the_scans_whose_readings_are_waiting_failed_or_dead(firm, monkeypatch):
    seen = {}

    def fake(clients, store, client, staged, by, progress=None, db_path=None, state_path=None, pending=()):
        seen["pending"] = sorted(pending)
        return {"processed": True}

    monkeypatch.setattr(front_desk, "read_staff_upload", fake)
    root = firm["clients"].parent / "jobs"
    monkeypatch.setenv("I485_JOBS", str(root))
    a = jobs.submit(root, "staff_upload", "case-x", by="P", args={"name": "a.pdf"})
    waiting = jobs.submit(root, "staff_upload", "case-x", by="P", args={"name": "b.pdf"})
    dead = jobs.submit(root, "staff_upload", "case-x", by="P", args={"name": "c.pdf"})
    failed = jobs.submit(root, "staff_upload", "case-x", by="P", args={"name": "d.pdf"})
    other = jobs.submit(root, "staff_upload", "case-y", by="P", args={"name": "z.pdf"})
    jobs._finish(root, dead | {}, "died", error=jobs.DIED)
    jobs._finish(root, failed | {}, "failed", error="x")
    ctx = jobs.Context(firm["clients"], None)
    jobs._staff_upload(ctx, a, lambda *_: None)
    assert seen["pending"] == ["b.pdf", "c.pdf", "d.pdf"]  # not the other case's, not its own
    assert waiting["id"] != other["id"]


@pytest.mark.parametrize("processed,state,label", [(False, "failed", "Could not be read: it will be read tonight"), (True, "done", "Read")])
def test_a_scan_the_reader_could_not_read_is_not_shown_as_read(firm, monkeypatch, processed, state, label):
    monkeypatch.setattr(front_desk, "read_staff_upload", lambda *a, **k: {"processed": processed, "error": "OSError: boom"})  # the reader catches its own failures and says so
    root = firm["clients"].parent / "jobs"
    monkeypatch.setenv("I485_JOBS", str(root))
    job = jobs.submit(root, "staff_upload", "case-x", by="P", args={"name": "a.pdf"})
    jobs.run_job(jobs.Context(firm["clients"], None), job)
    filed = jobs.get(root, job["id"])
    shown = jobs.view(filed)
    assert filed["state"] == state and shown["label"] == label
    assert ("why" in shown) == (not processed) and (processed or shown["why"] == label)  # in words, never the exception's name
