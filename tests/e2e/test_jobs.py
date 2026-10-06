"""The job worker in the browser (src/jobs.py): a scan the office adds and a photo a client sends are answered at once and read by the worker the review app started itself
(there is no installed unit in the made-up world: the app starts one, as it does on a laptop); the Documents tab says "Reading now" with how far it is, "Read" when it is done, and
"Did not finish: will be read tonight" for a reading a stopped worker left. Everyone here is made up."""

from __future__ import annotations

import json
import os
import signal
import time
from pathlib import Path

import jobs


def _root(world) -> Path:
    return Path(world["env"]["I485_JOBS"])


def _worker_pid(world) -> int | None:
    """The worker the review app started (it writes who it is once the pipeline's libraries are loaded: some seconds after the app starts)."""
    end = time.time() + 90
    while time.time() < end:
        try:
            return json.loads((_root(world) / "worker.json").read_text(encoding="utf-8"))["pid"]
        except (OSError, ValueError, KeyError):
            time.sleep(0.5)
    return None


def _pdf(tmp_path: Path, name: str = "scan.pdf") -> str:
    from portal.demo import document_pdf

    path = tmp_path / name
    path.write_bytes(document_pdf(["EXEMPLO: DEMONSTRATION DOCUMENT", "PASSPORT", "Surname EXEMPLO TESTE", "Given names JOB WORKER", "Nationality BRAZILIAN"]))
    return str(path)


def test_a_scan_is_answered_at_once_and_read_by_the_worker_the_app_started(world, paralegal, tmp_path):
    s = paralegal
    s.open("case-family", "documents")
    before = s.page.locator("#documents tr").count()
    started = time.time()
    s.page.locator("#dropzone input[type=file]").set_input_files(_pdf(tmp_path))
    s.page.wait_for_function("() => document.getElementById('toast').innerText.startsWith('Added')", timeout=60000)
    said = s.toast()
    assert said == "Added 1 document. It is being read now." and time.time() - started < 20  # the answer did not wait for the reading
    # the tab asks the worker's progress and draws the case again with what it found
    s.page.wait_for_function("(n) => document.querySelectorAll('#documents tr').length > n", arg=before, timeout=120000)
    s.settle(300)
    s.page.wait_for_function("() => document.body.innerText.includes('Added by Paulo Paralegal on ')", timeout=60000)  # the row says who added it a moment after it appears
    body = s.check("jobs_scan_read")
    assert "Added by Paulo Paralegal on " in body
    panel = s.page.locator("#readings")
    assert panel.count() == 1 and "A scan the office added: Read" in panel.inner_text()  # "Read" for the minutes after
    done = [j for j in jobs.jobs(_root(world), client="case-family") if j["kind"] == "staff_upload"]
    assert done and all(j["state"] == "done" and j["result"]["processed"] for j in done)
    assert not s.errors


def test_the_tab_says_reading_now_and_how_far_the_reading_is(world, paralegal):
    """A reading under way as the worker writes it down (step 2 of 3): the words the person sees. (A real one is over in a second, so this one is written by hand.)"""
    root = _root(world)
    job = jobs.submit(root, "staff_upload", "case-resident", by="Paulo Paralegal", args={"name": "x.pdf"})
    path = root / f"{job['id']}.json"
    job |= {"state": "running", "pid": _worker_pid(world), "started": "2026-10-03T09:00:00-04:00", "progress": {"step": 2, "steps": 3, "text": "Adding it to the case"}}
    path.write_text(json.dumps(job), encoding="utf-8")
    try:
        s = paralegal
        s.open("case-resident", "documents")
        s.page.wait_for_selector("#readings:not([hidden])")
        text = s.page.locator("#readings").inner_text()
        assert text == "A scan the office added: Reading now: step 2 of 3, adding it to the case"
        s.check("jobs_reading_now")
        assert "scan_" not in s.text() and ".pdf" not in s.page.locator("#readings").inner_text()  # never a file name
    finally:
        path.unlink(missing_ok=True)


def test_a_photo_a_client_sends_is_read_through_the_worker_within_seconds(world, browser, paralegal, tmp_path):
    """The portal and the review app share this machine: the phone is answered at once, the worker reads the photo, and the office sees it read."""
    from portal import demo
    from portal.store import PortalStore
    from test_portal_safety import _copy_of_ana, _phone, _retake_task, _sound

    store = _copy_of_ana(world, "jobs-now", here=True)
    _retake_task(store, "jobs-now")
    assert _worker_pid(world), "the review app started a worker"
    ctx, page, errors = _phone(browser, world, "jobs-now", store)
    try:
        page.locator(".asked-line").get_by_role("button", name="Ver agora").click()
        scan = tmp_path / "passport-again.pdf"
        scan.write_bytes(demo.document_pdf(demo.DOCUMENTS["passport"][1] + ["EXEMPLO: THE WORKER'S PHOTO"]))
        started = time.time()
        page.locator(".alert input[type=file]:not([capture])").set_input_files(str(scan))
        page.wait_for_selector(".note.sent")
        assert time.time() - started < 15  # the answer to the phone did not wait for the reading
        _sound(page, errors)
        end = time.time() + 120
        while time.time() < end and any(u["status"] == "received" for u in PortalStore(world["portal"]).uploads("jobs-now")):
            time.sleep(0.5)
        assert not any(u["status"] == "received" for u in PortalStore(world["portal"]).uploads("jobs-now")), "the worker did not read the photo in two minutes"
    finally:
        ctx.close()
    [job] = [j for j in jobs.jobs(_root(world), client="jobs-now", recent=3600) if j["kind"] == "portal_upload"]
    assert job["state"] == "done" and job["result"]["read"] == 1 and job["by"] == "The client"
    paralegal.open("jobs-now", "documents")
    body = paralegal.check("jobs_photo_read")
    assert "New from the client" not in body and "A photo the client sent: Read" in body


def test_a_reading_a_stopped_worker_left_says_it_did_not_finish(world, paralegal):
    pid = _worker_pid(world)
    assert pid, "the review app started a worker"
    root = _root(world)
    job = jobs.submit(root, "staff_upload", "case-court", by="Paulo Paralegal", args={"name": "x.pdf"})
    path = root / f"{job['id']}.json"
    job |= {"state": "running", "pid": pid, "started": "2026-10-03T09:00:00-04:00", "progress": {"step": 1, "steps": 3, "text": "Reading the scan"}}
    path.write_text(json.dumps(job), encoding="utf-8")
    os.kill(pid, signal.SIGKILL)  # the worker dies with the reading under way
    end = time.time() + 20
    while jobs.alive(root) and time.time() < end:
        time.sleep(0.2)
    assert not jobs.alive(root)
    s = paralegal
    s.open("case-court", "documents")
    s.page.wait_for_selector("#readings:not([hidden])")
    assert s.page.locator("#readings").inner_text() == "A scan the office added: Did not finish: will be read tonight"
    s.check("jobs_did_not_finish")
    assert jobs.get(root, job["id"])["state"] == "died" and not jobs.pending(root, "case-court")  # marked for good: no worker, no job left running
    assert not s.errors  # (the next scan or inbox reading the office asks for starts a new worker: tests/test_jobs.py)
