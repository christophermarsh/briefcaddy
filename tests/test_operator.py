"""Everything a paralegal does, on a screen (review/front_desk.py, rules/firm_policies.py; docs/design_plan.md Part 10.3).

A made-up firm: the demo client Ana Clara Exemplo Souza (src/portal/demo.py) with her case folder, a folder-only case for
Maria Exemplo Lima, and the attorney Ana Attorney and the paralegal Paulo Paralegal. Nothing here is a real person.
"""

from __future__ import annotations

import base64
import io
import json
import shutil
from datetime import datetime
from pathlib import Path

import pytest
from PIL import Image

import clock
from factgraph import FactGraph
from portal import demo as seed
from portal.admin import import_clients
from portal.store import PortalStore
from review.server import ReviewApp
import schema_path

REPO = Path(__file__).resolve().parent.parent
TEMPLATE = schema_path.path("template", "i485")
SHIPPED = schema_path.path("law", "policy_sijs")
ID = seed.DEMO_ID
NOW = datetime(2026, 10, 2, 20, 5, tzinfo=clock.zone())


@pytest.fixture(autouse=True)
def _quiet(monkeypatch, tmp_path):
    for var in ("SMTP_HOST", "TWILIO_ACCOUNT_SID", "PORTAL_OUTBOX_FULL_LINKS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("I485_POLICIES_FIRM", str(tmp_path / "policies_firm.json"))
    monkeypatch.setenv("I485_JOBS", str(tmp_path / "jobs"))  # each test has its own job queue (src/jobs.py)
    monkeypatch.setattr(clock, "_now_override", NOW)


@pytest.fixture(scope="module")
def seeded(tmp_path_factory):
    root = tmp_path_factory.mktemp("operator")
    seed.seed(PortalStore(root / "portal"), root / "clients")
    return root


@pytest.fixture
def world(seeded, tmp_path):
    """A private copy of the seeded demo world for each test: the portal's folder, the case folders, the review app over both."""
    shutil.copytree(seeded, tmp_path / "w")
    root = tmp_path / "w"
    for meta_path in (root / "clients").glob("*/meta.json"):  # the copy's cases read their documents from the copy's folders
        meta = json.loads(meta_path.read_text(encoding="utf-8"))
        meta["source_folder"] = meta["source_folder"].replace(str(seeded), str(root))
        meta_path.write_text(json.dumps(meta, indent=2), encoding="utf-8")
    app = ReviewApp(root / "clients", schema_path.path("field_map", "i485"), TEMPLATE, SHIPPED, root / "portal")
    return root, PortalStore(root / "portal"), app


def worker_reads(app, started: dict) -> dict:
    """The job worker's turn (src/jobs.py): the app answered at once with a job; the worker reads it now, and what the reading found is added to the app's answer."""
    import jobs

    assert started["job"]["state"] == "queued" and started["reading"] is True
    jobs.work(app.data_root, app.portal_root, once=True)
    job = jobs.get(app.jobs_root, started["job"]["id"])
    assert job["state"] == "done", job
    return started | job["result"]


def pdf(*lines: str) -> bytes:
    return seed.document_pdf(list(lines))


def png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (60, 40), "white").save(out, format="PNG")
    return out.getvalue()


def b64(data: bytes) -> str:
    return base64.b64encode(data).decode()


ANA = {"name": "Maria Exemplo Teste", "phone": "(555) 010-0188", "email": "maria.teste@example.com", "language": "es", "filing": "n400",
       "consent": {"email": True, "sms": True, "whatsapp": False}, "reviewer": "Paulo Paralegal"}


def add(app, body: dict) -> dict:
    """Add a client as the screen does: the conflict search for the name first (src/conflicts.py), then the client with the decision recorded."""
    found = app.conflict_search({"name": body.get("name", ""), "purpose": "add", "reviewer": body.get("reviewer") or "Paulo Paralegal"}, None)
    return app.client_add(body | {"conflict": {"search": found["id"], "decision": "none"}})


# -- 1. add a client -------------------------------------------------------------------------------


def test_add_a_client_makes_what_the_import_makes(world, tmp_path):
    root, store, app = world
    done = add(app, ANA | {"office": "main", "track": "naturalization"})
    from portal.contact_access import PHONE_CONTEXT
    assert done == {"id": "maria-exemplo-teste", "name": "Maria Exemplo Teste", "filing": "n400",
                    "phone_access": "office_only", "phone_note": PHONE_CONTEXT}

    csv_path = tmp_path / "list.csv"  # the same client through the command line's import
    csv_path.write_text("id,name,phone,email,language,email_ok,sms_ok,whatsapp_ok,filing,office\n"
                        "other,Maria Exemplo Teste,(555) 010-0188,maria.teste@example.com,Spanish,yes,yes,no,n400,main\n", encoding="utf-8")
    other = PortalStore(tmp_path / "other")
    import_clients(other, csv_path)
    mine, theirs = store.profile("maria-exemplo-teste"), other.profile("other")
    for key in ("name", "phone", "email", "language", "consent", "filing", "office", "status"):
        assert mine[key] == theirs[key], key
    assert mine["status"] == "invited" and mine["consent"] == {"email": True, "sms": True, "whatsapp": False}
    assert mine["added_by"] == "Paulo Paralegal" and mine["added_at"] == NOW.isoformat() and mine["track"] == "naturalization"
    events = [json.loads(line) for line in (store.client_dir("maria-exemplo-teste") / "events.jsonl").read_text(encoding="utf-8").splitlines()]
    assert events[0]["event"] == "client_added" and events[0]["by"] == "Paulo Paralegal"


def test_what_a_client_form_may_not_hold(world):
    root, store, app = world
    with pytest.raises(ValueError, match="full name"):
        app.client_add(ANA | {"name": " "})
    with pytest.raises(ValueError, match="full country-code"):
        app.client_add(ANA | {"phone": "+1234567"})  # malformed attempted full number, never a guessed country
    with pytest.raises(ValueError, match="unsupported_email"):
        app.client_add(ANA | {"email": "not-an-address"})
    with pytest.raises(ValueError, match="Choose the client's language"):
        app.client_add(ANA | {"language": "Klingon"})
    with pytest.raises(ValueError, match="agreed to receive email, but there is no email address"):
        app.client_add(ANA | {"email": "", "consent": {"email": True}})
    with pytest.raises(ValueError, match="Choose a questionnaire"):
        app.client_add(ANA | {"filing": "asylum"})
    with pytest.raises(ValueError, match="Choose the office"):
        app.client_add(ANA | {"office": "atlantis"})
    with pytest.raises(ValueError, match="Choose the case's track"):
        app.client_add(ANA | {"track": "moon"})
    with pytest.raises(ValueError, match="Enter your name first"):
        app.client_add(ANA | {"reviewer": ""})
    add(app, ANA)
    with pytest.raises(ValueError, match="contact cannot be used") as duplicate:
        app.client_add(ANA | {"name": "Someone Else", "phone": "", "consent": {"email": True}})
    assert ANA["name"] not in str(duplicate.value) and "maria-exemplo-teste" not in str(duplicate.value)
    twin = add(app, ANA | {"email": "other@example.com", "phone": "(555) 010-0199"})
    assert twin["id"] == "maria-exemplo-teste-2"  # the same name is told apart, never overwritten
    assert [q["id"] for q in app.front_desk()["questionnaires"]] == ["i485", "n400", "parole"]


# -- 2. invite, again, show the link -------------------------------------------------------------------


def test_the_invitation_goes_by_the_agreed_channels_with_no_case_details(world):
    root, store, app = world
    add(app, ANA)
    done = app.client_invite("maria-exemplo-teste", {"reviewer": "Paulo Paralegal"})
    assert done["delivery"]["status"] == "queued" and done["delivery"]["text"] == "Queued, no mail server configured; queued, no text service configured"
    lines = [json.loads(x) for x in (store.root / "outbox.jsonl").read_text(encoding="utf-8").splitlines()]
    assert sorted(x["channel"] for x in lines) == ["email", "sms"]  # WhatsApp was not agreed to
    for line in lines:
        text = line["subject"] + line["body"]
        assert "ciudadanía" in text and "/l/…" in line["body"]  # the citizenship invitation, in Spanish, the link cut to its last six characters
        assert not any(word in text for word in ("Maria", "Exemplo", "Teste", "N-400", "I-485", "juvenil"))  # no name, no case
    profile = store.profile("maria-exemplo-teste")
    assert profile["last_invite_by"] == "Paulo Paralegal" and profile["last_invite"]["status"] == "queued" and profile["invited_at"] == NOW.isoformat()

    app.client_invite("maria-exemplo-teste", {"reviewer": "Paulo Paralegal", "again": True})
    sent = [json.loads(x) for x in (store.client_dir("maria-exemplo-teste") / "events.jsonl").read_text(encoding="utf-8").splitlines()
            if '"sent_invite"' in x]
    assert len(sent) == 2 and sent[1]["again"] is True and sent[1]["by"] == "Paulo Paralegal" and "again" not in sent[0]

    quiet = add(app, ANA | {"name": "Caio Exemplo", "email": "caio@example.com", "phone": "", "consent": {}})
    assert app.client_invite(quiet["id"], {"reviewer": "Paulo Paralegal"})["delivery"]["status"] == "none"  # nothing agreed to: nothing sent
    store.update_profile("maria-exemplo-teste", status="submitted")
    with pytest.raises(ValueError, match="already sent their questionnaire"):
        app.client_invite("maria-exemplo-teste", {"reviewer": "Paulo Paralegal"})


def test_adding_and_inviting_in_one_step(world):
    root, store, app = world
    done = add(app, ANA | {"invite": True})
    assert done["delivery"]["status"] == "queued" and (store.root / "outbox.jsonl").exists()


def test_only_the_attorney_sees_the_link_and_it_is_logged(world):
    root, store, app = world
    add(app, ANA)
    with pytest.raises(PermissionError, match="attorney's"):
        app.client_link("maria-exemplo-teste", {"reviewer": "Paulo Paralegal"}, "paralegal")
    shown = app.client_link("maria-exemplo-teste", {"reviewer": "Ana Attorney"}, "attorney")
    assert shown["url"].startswith("http://localhost:8600/l/") and shown["hours"] == 72
    token = shown["url"].rsplit("/", 1)[1]
    assert store.redeem_link(token) and store.redeem_link(token) is None  # a working link, once
    shown_events = [json.loads(x) for x in (store.client_dir("maria-exemplo-teste") / "events.jsonl").read_text(encoding="utf-8").splitlines()
                    if "link_shown" in x]
    assert shown_events[0]["by"] == "Ana Attorney"
    assert token not in (store.client_dir("maria-exemplo-teste") / "events.jsonl").read_text(encoding="utf-8")  # the log never keeps the link itself


# -- 3. the office adds a scan --------------------------------------------------------------------------


def _gibberish() -> bytes:
    """A scan nobody could read: a page of lines out of focus (a picture alone, no text layer). A typed page of nonsense words is graded by its
    measures now (sharp, good contrast), so it is the picture that must be bad."""
    from PIL import ImageDraw, ImageFilter

    page = Image.new("L", (1000, 1294), 255)
    draw = ImageDraw.Draw(page)
    for line in range(30):
        draw.rectangle((80, 100 + 36 * line, 80 + 40 * (7 + line % 9), 114 + 36 * line), fill=0)  # lines of print
    out = io.BytesIO()
    page.filter(ImageFilter.GaussianBlur(6)).save(out, format="PDF")
    return out.getvalue()


def test_a_staff_scan_on_a_portal_case_is_the_offices_and_is_read_at_once(world, monkeypatch):
    import batch
    import documents

    root, store, app = world
    case = root / "clients" / ID
    before = {r["id"] for r in documents.read(case)["documents"]}
    calls = []
    original = batch.process_documents

    def watched(client_id, docs, *a, **kw):
        calls.append([d for d, _ in docs])
        return original(client_id, docs, *a, **kw)

    monkeypatch.setattr(batch, "process_documents", watched)
    started = app.client_upload(ID, {"name": "driver license scan.pdf", "data": b64(_gibberish()), "reviewer": "Paulo Paralegal"})
    assert started["name"] == "scan_2026-10-02_driver_license_scan.pdf" and calls == []  # the app answered at once: nothing was read in its process
    done = worker_reads(app, started)
    assert done["processed"] is True
    assert calls == [[done["name"]]]  # only the new file was read: the case's other documents were not read again
    folder = store.client_dir(ID) / "uploads"
    assert (folder / done["name"]).exists()
    [up] = [u for u in store.uploads(ID) if u["stored"] == done["name"]]
    assert up["source"] == "folder" and up["by"] == "Paulo Paralegal" and up["doc_id"] == "folder"
    new = [r for r in documents.read(case)["documents"] if r["id"] not in before]
    [record] = new
    assert record["source"] == "folder" and record["files"] == [done["name"]] and record["added_by"] == {"who": "Paulo Paralegal", "at": NOW.isoformat()}
    assert done["name"] in json.loads((case / "meta.json").read_text(encoding="utf-8"))["classifications"]
    assert any(r["id"] == record["id"] for r in app.documents(ID)["documents"])  # the Documents tab lists it

    # a hard-to-read office scan is never put on the client: no retake, and the Documents tab says it is the office's own
    from portal import engine

    assert record["quality"] == "blurry"
    assert engine.quality_retakes([record], store.uploads(ID)) == []
    assert engine.photo_status([record], store.uploads(ID), [], True)[record["id"]] == {"retake": "office_scan"}
    process = engine.process_client(store, ID, out_root=root / "clients")  # the worker's next pass keeps it, as the office's
    assert not [t for t in store.tasks(ID) if t["kind"] == "retake" and t.get("record") == record["id"]] and process["client"] == ID
    again = [r for r in documents.read(case)["documents"] if done["name"] in r["files"]]
    assert again and again[0]["source"] == "folder"
    # the same blurry file sent by the client is asked again: only the source tells them apart
    assert engine.quality_retakes([record], [{k: v for k, v in up.items() if k != "source"}])


def test_a_staff_scan_on_a_folder_case_is_stored_in_the_cases_folder(tmp_path):
    from batch import process_client_folder
    from review.state import save_bundle

    import documents

    source, out = tmp_path / "sources" / "case-maria", tmp_path / "clients" / "case-maria"
    source.mkdir(parents=True)
    (source / "receipt.pdf").write_bytes(pdf("Department of Homeland Security", "I-797C, Notice of Action", "EXEMPLO: DEMONSTRATION DOCUMENT",
                                           "Receipt Number Case Type", "IOE0999100002 I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS",
                                           "Received Date Priority Date Applicant", "06/01/2026 EXEMPLO LIMA, MARIA", "Notice Type: Receipt Notice"))
    save_bundle(process_client_folder("case-maria", source), out, source)
    app = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), TEMPLATE, None, tmp_path / "portal")
    old_files = {p.name: p.stat().st_mtime_ns for p in source.iterdir()}

    done = worker_reads(app, app.client_upload("case-maria", {"name": "passport.png", "data": b64(png()), "reviewer": "Paulo Paralegal"}))
    assert done["name"] == "scan_2026-10-02_passport.pdf" and done["processed"] is True
    assert (source / done["name"]).read_bytes()[:4] == b"%PDF"  # a photo is kept as one PDF, as the portal does
    assert {p.name: p.stat().st_mtime_ns for p in source.iterdir() if p.name in old_files} == old_files  # nothing else was touched
    records = documents.read(out)["documents"]
    mine = next(r for r in records if done["name"] in r["files"])
    assert mine["source"] == "folder" and mine["added_by"]["who"] == "Paulo Paralegal"
    assert len(records) == 2 and (app.client_dir("case-maria") / "fact_graph.json").exists()


def test_what_may_not_be_added(world):
    root, store, app = world
    ok = {"reviewer": "Paulo Paralegal"}
    with pytest.raises(ValueError, match="Only a PDF, a JPEG or a PNG"):
        app.client_upload(ID, ok | {"name": "x.docx", "data": b64(b"PK\x03\x04 not a pdf")})
    with pytest.raises(ValueError, match="empty"):
        app.client_upload(ID, ok | {"name": "x.pdf", "data": ""})
    with pytest.raises(ValueError, match="didn't arrive whole"):
        app.client_upload(ID, ok | {"name": "x.pdf", "data": "%%%"})
    with pytest.raises(ValueError, match="can't be opened"):
        app.client_upload(ID, ok | {"name": "x.pdf", "data": b64(b"%PDF-1.4 truncated")})
    with pytest.raises(ValueError, match="larger than 15 MB"):
        app.client_upload(ID, ok | {"name": "big.pdf", "data": b64(b"%PDF-1.4" + b"0" * (15 * 1024 * 1024))})
    for head in (b"\xff\xd8\xff\xe0", b"\x89PNG\r\n\x1a\n"):  # a photo's first bytes, then junk: a plain refusal, not a dropped connection
        with pytest.raises(ValueError, match="That image can't be opened. Please add a clear photo or a PDF."):
            app.client_upload(ID, ok | {"name": "x.jpg", "data": b64(head + b"junk" * 50)})
    with pytest.raises(ValueError, match="Enter your name"):
        app.client_upload(ID, {"name": "x.pdf", "data": b64(pdf("hello"))})
    with pytest.raises(LookupError):
        app.client_upload("nobody-here", ok | {"name": "x.pdf", "data": b64(pdf("hello"))})
    assert [u for u in store.uploads(ID) if u.get("source") == "folder"] == []


def test_a_scan_for_a_client_who_has_no_case_yet_makes_the_case(world):
    root, store, app = world
    added = add(app, ANA | {"track": "naturalization"})["id"]
    assert [p.name for p in (root / "clients" / added).iterdir()] == ["conflict_check.json"]  # the conflict check alone: no case yet
    done = worker_reads(app, app.client_upload(added, {"name": "green card.pdf", "data": b64(pdf("hello green card")), "reviewer": "Paulo Paralegal"}))
    assert done["processed"] is True and done["case_made"] is True
    assert (root / "clients" / added / "fact_graph.json").exists()
    import documents

    [record] = documents.read(root / "clients" / added)["documents"]
    assert record["source"] == "folder" and record["added_by"]["who"] == "Paulo Paralegal"
    marks = json.loads((root / "clients" / added / "status.json").read_text(encoding="utf-8"))["journey"]
    assert marks["track"]["value"] == "naturalization" and marks["track"]["by"] == "Paulo Paralegal"  # the track named when the client was added


# -- 4. the questionnaire --------------------------------------------------------------------------------


def test_choosing_the_questionnaire_for_an_existing_client(world):
    from portal.bank import bank_for

    root, store, app = world
    add(app, ANA | {"filing": "i485"})
    done = app.client_questionnaire("maria-exemplo-teste", {"filing": "n400", "reviewer": "Paulo Paralegal"})
    assert done == {"filing": "n400", "from": "i485"}
    profile = store.profile("maria-exemplo-teste")
    assert profile["filing"] == "n400" and bank_for(profile)["filing"] == "n400"
    assert profile["filing_changed"] == {"by": "Paulo Paralegal", "at": NOW.isoformat(), "from": "i485", "to": "n400"}
    assert "maria-exemplo-teste" in store.queued()  # the client's list of things to do follows at the next pass
    assert app._portal_note("maria-exemplo-teste")["portal"]["filing"] == "n400"
    with pytest.raises(ValueError, match="already answers"):
        app.client_questionnaire("maria-exemplo-teste", {"filing": "n400", "reviewer": "Paulo Paralegal"})
    with pytest.raises(ValueError, match="Choose a questionnaire"):
        app.client_questionnaire("maria-exemplo-teste", {"filing": "asylum", "reviewer": "Paulo Paralegal"})
    store.update_profile("maria-exemplo-teste", status="submitted")
    with pytest.raises(ValueError, match="already sent their questionnaire"):
        app.client_questionnaire("maria-exemplo-teste", {"filing": "i485", "reviewer": "Paulo Paralegal"})
    assert app.items(ID)["portal"]["questionnaires"][1] == {"id": "n400", "name": "Citizenship (N-400)"}  # on the case page, for a portal client


# -- 5. the firm's policies ---------------------------------------------------------------------------------


def _shipped_bytes() -> bytes:
    return SHIPPED.read_bytes()


def _policy(app, pid):
    return next(p for p in app.policies("attorney")["policies"] if p["id"] == pid)


def test_the_attorney_edits_a_policy_and_the_shipped_schema_is_untouched(world, tmp_path, monkeypatch):
    from rules import approval, firm_policies
    from rules.policy import load_policy_profile

    root, store, app = world
    shipped_before = _shipped_bytes()
    approval_log = tmp_path / "approved.json"
    import os

    monkeypatch.setenv("I485_RULES_APPROVED", str(approval_log))
    approval.approve("POLICY:NOT-EOIR", "Ana Attorney", "attorney")  # approved as shipped
    assert approval.status("POLICY:NOT-EOIR")["state"] == "approved"

    listed = _policy(app, "NOT-EOIR")
    assert listed["name"] == "Not filing with the immigration court" and listed["enabled"] and listed["edit"] is None
    assert [(a["label"].split("?")[0][:40], a["value"], a["input"]["options"]) for a in listed["answers"]] == [
        (listed["answers"][0]["label"].split("?")[0][:40], "No", ["Yes", "No"])]  # a Yes or No box takes Yes or No
    key = listed["answers"][0]["key"]
    new_words = "The client is not in removal proceedings, so the form's court question is answered Yes for this firm."
    done = app.policies_edit({"policy": "NOT-EOIR", "plain_text": new_words, "answers": {key: "Yes"}, "reviewer": "Ana Attorney"}, "attorney")
    row = next(p for p in done["policies"] if p["id"] == "NOT-EOIR")
    assert row["plain_text"] == new_words and row["answers"][0]["value"] == "Yes" and row["answers"][0]["shipped"] == "No"
    assert row["edit"]["text"] == "The firm's wording and answer, edited by Ana Attorney on 10/02/2026"

    assert _shipped_bytes() == shipped_before  # the shipped schema, byte for byte
    firm_file = Path(os.environ["I485_POLICIES_FIRM"])
    record = json.loads(firm_file.read_text(encoding="utf-8"))["edits"]["NOT-EOIR"][-1]
    assert record["by"] == "Ana Attorney" and record["role"] == "attorney" and record["at"] == clock.stamp("seconds")
    assert record["old_plain_text"] == listed["plain_text"] != new_words
    assert record["plain_text"] == new_words and record["old_set"] == {key: "No"} and record["set"] == {key: "Yes"}
    assert len(record["hash"]) == 64 and record["hash"] == firm_policies.policy_hash(
        firm_policies.effective(next(p for p in firm_policies.shipped() if p["id"] == "NOT-EOIR")))

    engine_view = next(p for p in load_policy_profile(SHIPPED) if p["id"] == "NOT-EOIR")  # the engine reads the firm's version
    assert engine_view["plain_text"] == new_words and engine_view["set"] == {key: "Yes"}
    assert next(p for p in load_policy_profile(SHIPPED, firm=False) if p["id"] == "NOT-EOIR")["set"] == {key: "No"}
    assert app.catalog.policy_why["POLICY:NOT-EOIR"] == new_words

    assert approval.status("POLICY:NOT-EOIR")["state"] == "changed"  # the approval held for the old words
    from review.state import rule_info

    info = rule_info("POLICY:NOT-EOIR")
    assert info["plain_text"] == new_words and info["edited_text"] == "The firm's wording and answer, edited by Ana Attorney on 10/02/2026"
    assert info["approval_text"].startswith("Changed since approval")
    assert rule_info("POLICY:HAS-A-NUMBER")["edited_text"] is None  # an untouched policy says nothing


def test_the_engine_fills_the_firms_answer_and_a_switched_off_policy_sets_nothing(world):
    from rules.policy import load_policy_profile, run_policies

    root, store, app = world

    def run():
        graph = FactGraph("x")
        graph.add_source("applicant.i360_receipt_number", "d", "i360_approval", "IOE0999100002", "IOE0999100002", 0.95)
        policies = [p for p in load_policy_profile(SHIPPED) if p["id"] in ("SIJS-CATEGORY", "NOT-EOIR")]
        applied = run_policies(graph, policies, NOW.date())
        return graph, applied

    graph, applied = run()
    assert "NOT-EOIR" in applied
    assert graph.get("applicant.filing_with_eoir").value == "No"
    key = "applicant.filing_with_eoir"
    app.policies_edit({"policy": "NOT-EOIR", "answers": {key: "Yes"}, "reviewer": "Ana Attorney"}, "attorney")
    graph, _ = run()
    assert graph.get(key).value == "Yes" and graph.get(key).derived_by == "POLICY:NOT-EOIR"  # the firm's answer, still traced to the policy

    app.policies_edit({"policy": "NOT-EOIR", "enabled": False, "reviewer": "Ana Attorney"}, "attorney")
    graph, applied = run()
    assert graph.get(key) is None and "NOT-EOIR" not in applied  # switched off: it sets nothing
    assert "NOT-EOIR" not in [p["id"] for p in load_policy_profile(SHIPPED)]
    row = _policy(app, "NOT-EOIR")
    assert row["enabled"] is False and "switched off by Ana Attorney on 10/02/2026" in row["edit"]["text"]
    assert any(p["id"] == "SIJS-CATEGORY" for p in load_policy_profile(SHIPPED))  # the others still apply

    app.policies_edit({"policy": "NOT-EOIR", "enabled": True, "reviewer": "Ana Attorney"}, "attorney")
    graph, _ = run()
    assert graph.get(key).value == "Yes"  # back on, with the firm's answer


def test_a_policy_edit_reaches_the_review_cards_at_once(world):
    root, store, app = world

    def facts(rule):
        return [f for c in app.items(ID)["cards"] for f in c["facts"] if f.get("derived_by") == rule]

    [fact] = facts("POLICY:SIJS-STATUS")  # the demo case carries it: filled by the shipped policy
    assert fact["value"] == "I-360 APPROVED" and fact["rule"]["edited_text"] is None
    words = "The firm's own words for this standard answer."
    key = _policy(app, "SIJS-STATUS")["answers"][0]["key"]
    app.policies_edit({"policy": "SIJS-STATUS", "plain_text": words, "answers": {key: "I-360 approved, SIJ"}, "reviewer": "Ana Attorney"}, "attorney")
    [fact] = facts("POLICY:SIJS-STATUS")  # no restart, no reprocessing: the card reads the firm's version
    assert fact["value"] == "I-360 APPROVED, SIJ" and fact["why"] == words  # a text box takes capitals, as every text answer on the form does
    assert fact["rule"]["edited_text"] == "The firm's wording and answer, edited by Ana Attorney on 10/02/2026"

    app.policies_edit({"policy": "SIJS-STATUS", "enabled": False, "reviewer": "Ana Attorney"}, "attorney")
    assert facts("POLICY:SIJS-STATUS") == []  # switched off: the case no longer has the answer
    rows = {r["id"]: r for r in app.overview()["clients"]}
    assert rows[ID]["id"] == ID  # the dashboard row was rebuilt, not served from before the edit


def test_what_a_policy_may_be_changed_to(world):
    root, store, app = world
    edit = lambda pid, **kw: app.policies_edit({"policy": pid, "reviewer": "Ana Attorney", **kw}, "attorney")  # noqa: E731
    with pytest.raises(PermissionError, match="attorney's"):
        app.policies_edit({"policy": "NOT-EOIR", "enabled": False, "reviewer": "Paulo Paralegal"}, "paralegal")
    with pytest.raises(LookupError, match="built-in rules can't be edited"):
        edit("OVERSTAY-01", plain_text="Anything")
    with pytest.raises(ValueError, match="Nothing changed"):
        edit("NOT-EOIR")
    with pytest.raises(ValueError, match="Write what the policy says"):
        edit("NOT-EOIR", plain_text="   ")
    with pytest.raises(ValueError, match="under 1000"):
        edit("NOT-EOIR", plain_text="x" * 1001)
    with pytest.raises(ValueError, match="doesn't set that answer"):
        edit("NOT-EOIR", answers={"applicant.ssn": "123"})
    key = _policy(app, "NOT-EOIR")["answers"][0]["key"]
    with pytest.raises(ValueError, match="is one of: Yes, No"):
        edit("NOT-EOIR", answers={key: "Maybe"})
    with pytest.raises(ValueError, match="Give an answer"):
        edit("NOT-EOIR", answers={key: ""})
    with pytest.raises(ValueError, match="Enter your name first"):
        app.policies_edit({"policy": "NOT-EOIR", "plain_text": "Words", "reviewer": ""}, "attorney")
    # a choice box takes only the form's own options; a text box a short text
    sij = _policy(app, "SIJS-CATEGORY")
    category = next(a for a in sij["answers"] if a["input"]["type"] == "choice" and len(a["input"]["options"]) > 2)
    with pytest.raises(ValueError, match="is one of"):
        edit("SIJS-CATEGORY", answers={category["key"]: "Not a category"})
    status = _policy(app, "SIJS-STATUS")["answers"][0]
    assert status["input"]["type"] == "text"
    edit("SIJS-STATUS", answers={status["key"]: "  I-360   approved,   SIJ  "})
    assert _policy(app, "SIJS-STATUS")["answers"][0]["value"] == "I-360 APPROVED, SIJ"  # spaces tidied, capitals as the form's text boxes take them
    with pytest.raises(ValueError, match="room for"):
        edit("SIJS-STATUS", answers={status["key"]: "y" * 101})
    # the conditions are not editable: an edit can't carry them
    edit("SIJS-STATUS", plain_text="Reworded.", when_present=["applicant.ssn"])
    from rules import firm_policies

    shipped = next(p for p in firm_policies.shipped() if p["id"] == "SIJS-STATUS")
    assert next(p for p in firm_policies.apply([shipped]))["when_present"] == shipped["when_present"]
    # a long policy sets every answer it sets, and changing one leaves the others
    part9 = _policy(app, "PART9-DEFAULT-NO")
    assert len(part9["answers"]) == 65 and {a["value"] for a in part9["answers"]} == {"No"}
    one = part9["answers"][0]["key"]
    edit("PART9-DEFAULT-NO", answers={one: "Yes"})
    values = [a["value"] for a in _policy(app, "PART9-DEFAULT-NO")["answers"]]
    assert values.count("Yes") == 1 and values.count("No") == 64


def test_the_review_bundle_says_who_edited_the_firms_words(world):
    from review import bundle

    root, store, app = world
    app.policies_edit({"policy": "NOT-EOIR", "plain_text": "The firm's own words.", "reviewer": "Ana Attorney"}, "attorney")
    from review.state import rule_info

    info = rule_info("POLICY:NOT-EOIR")
    source = {"kind": "policy", "title": info["name"], "rule": info["id"], "text": info["plain_text"], "edited": info["edited_text"]}
    lines = [t for _, t in bundle._source_lines(source)]
    assert "The firm's own words." in lines and "The firm's wording, edited by Ana Attorney on 10/02/2026." in lines


def test_the_firms_choices_item_tells_the_attorney_where_to_edit():
    items = json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))
    steps = next(i for i in (items["items"] if isinstance(items, dict) else items) if i["id"] == "firm_choices")["firm_steps"]
    text = " ".join(steps)
    assert "Firm policies" in text and "Settings" in text and "switch" in text and "who made it" in text


# Implementation note.


def test_a_typed_answer_fills_the_box_before_any_worker_runs(world):
    """Finding 2: the answer reached the box only when the portal's worker processed the client again. Now the case reads it as it
    is read: the card shows it at once and says where it came from, and opening the case fills the form again."""
    from review.state import build_items, reviewed_graph

    root, store, app = world
    case = root / "clients" / ID
    assert reviewed_graph(case).get("applicant.father_dob") is None or reviewed_graph(case).get("applicant.father_dob").value in (None, "")
    request = store.add_request(ID, "What is your father's date of birth?", None, "Paulo Paralegal", facts=["applicant.father_dob"],
                                typed={"type": "date", "text_client": "Qual é a data de nascimento do seu pai?", "language": "pt",
                                       "machine_translated": False, "needs_translator": False})
    store.answer_request(ID, request["id"], reply="1970-05-04")  # the client answers on the phone; no worker runs
    fact = reviewed_graph(case).get("applicant.father_dob")
    assert fact.value == "1970-05-04" and fact.status == "resolved" and [s.doc_id for s in fact.sources] == ["office question"]
    out = app.items(ID)
    assert not any(i["id"] == "missing:applicant.father_dob" for i in out["open"])  # no longer "Needs an answer"
    card = next(c for c in out["cards"] if any(f["key"] == "applicant.father_dob" for f in c["facts"]))
    # the card (the client's Part 5 answers, here) says first that a box holds the client's answer to the office's question
    assert card["tab"] != "fix" and card["filled_from_client"] and "filled from the client's answer to the office's question" in card["messages"][0].lower()
    assert card["asked"][0]["answer"] == "05/04/1970"  # and the source line, as before
    filled = json.loads((case / "fact_graph_reviewed.json").read_text(encoding="utf-8"))  # opening the case filled the form again
    assert "1970-05-04" in json.dumps(filled)

    # the worker's own pass later adds nothing twice; a newer answer replaces the older one
    second = store.add_request(ID, "What is your father's date of birth?", None, "Paulo Paralegal", facts=["applicant.father_dob"],
                               typed={"type": "date", "language": "en"})
    store.answer_request(ID, second["id"], reply="1971-01-02")
    fact = reviewed_graph(case).get("applicant.father_dob")
    assert fact.value == "1971-01-02" and len([s for s in fact.sources if s.doc_id == "office question"]) == 1
    assert build_items(case, app.field_map, app.template, app.catalog, pending=False)["cards"]


def test_changing_the_questionnaire_after_the_client_started_asks_first(world):
    root, store, app = world
    assert store.answers(ID)  # the demo client has started answering the green card questionnaire
    warning = app._portal_note(ID)["portal"]["change_warning"]
    assert "has started answering" in warning and "saved answers are kept" in warning and "Green card" in warning
    with pytest.raises(ValueError, match="has started answering"):
        app.client_questionnaire(ID, {"filing": "n400", "reviewer": "Paulo Paralegal"})
    assert store.profile(ID).get("filing", "i485") == "i485"
    before = store.answers(ID)
    assert app.client_questionnaire(ID, {"filing": "n400", "reviewer": "Paulo Paralegal", "confirmed": True})["filing"] == "n400"
    assert store.answers(ID) == before  # nothing deleted
    add(app, ANA)  # a client who has not answered anything is changed without a question
    assert app._portal_note("maria-exemplo-teste")["portal"]["change_warning"] is None
    assert app.client_questionnaire("maria-exemplo-teste", {"filing": "i485", "reviewer": "Paulo Paralegal"})["filing"] == "i485"


def test_reports_say_how_many_clients_have_no_case_file(world):
    root, store, app = world
    before = app.reports("attorney")
    add(app, ANA)
    after = app.reports("attorney")
    assert after["cases"] == before["cases"] and after["without_case"] == before["without_case"] + 1


def test_settings_show_what_changed_and_the_clio_screen_says_it_is_unproven(world):
    import version

    root, store, app = world
    for role in ("attorney", "paralegal"):  # read-only, for everyone
        notes = app.settings(role)["releases"]
        assert notes[0]["version"] == version.VERSION and notes[0]["released"] == version.RELEASED and len(notes[0]["lines"]) >= 5
        assert len(notes) >= 5 and "2026.10.8" in [n["version"] for n in notes] and all(n["lines"] for n in notes)  # the older releases stay listed under the newest
        for line in (x for r in notes for x in r["lines"]):  # plain words for staff: no file names, no code, no dashes
            assert not any(t in line for t in (".py", ".json", ".md", "_", "—", " -- ")), line
    connections = next(s for s in app.settings("attorney")["sections"] if s["id"] == "connections")
    assert "has not yet run against a real Clio account" in connections["unproven"] and "permissions" in connections["unproven"]
    assert connections["help"].startswith("The firm's other systems. Clio, once connected:")


def test_each_n400_question_says_who_answers_it():
    """The client's own questions (a name change, the Social Security card, every Part 9 and Part 10 one) say "Ask: the client";
    the attorney decides only the basis; the office reads the dates it holds documents for."""
    import naturalization

    asks = {key: naturalization.who_answers(key) for key, *_ in naturalization.QUESTIONS}
    assert asks["n400.name_change"] == ("client", "Ask: the client") and asks["n400.ssa_card"][0] == asks["n400.ssa_consent"][0] == "client"
    assert asks["n400.new_given_name"][0] == asks["n400.parent_citizen_before_18"][0] == asks["n400.spouse_times_married"][0] == "client"
    assert {k for k, (w, _) in asks.items() if w == "attorney"} == {"n400.basis", "n400.basis_other"}
    assert asks["n400.lpr_date"] == ("document", "From a document: the green card")
    assert all(asks[k][0] == "client" for k in asks if k.startswith("n400.p9_") or k.startswith("n400.household") or k in ("n400.fee_reduction", "n400.head_of_household"))


def test_the_spouse_questions_are_asked_only_on_a_spouse_based_filing(world):
    import naturalization
    from review.state import reviewed_graph

    root, store, app = world
    case = root / "clients" / ID
    status = naturalization.status(case)
    assert status["eligibility"]["basis"] == "General"
    assert not [q for q in status["questions"] if q["section"] == naturalization.SPOUSE_SECTION]
    assert status["not_asked"] == [{"section": naturalization.SPOUSE_SECTION, "why": "Not asked: basis is General (5 years)"}]
    # VAWA (Part 1, C) skips Part 5's spouse items too: the form asks them only on the two spouse bases (Part 1, B and D)
    assert [b for b in naturalization.BASES if naturalization.spouse_based(b)] == ["Spouse of U.S. citizen", "Spouse of U.S. citizen working abroad"]
    naturalization.answer(case, {"n400.basis": "VAWA"}, "Ana Attorney", "attorney")
    status = naturalization.status(case)
    assert not [q for q in status["questions"] if q["section"] == naturalization.SPOUSE_SECTION]
    assert status["not_asked"][0]["why"].startswith("Not asked: basis is VAWA")
    naturalization.answer(case, {"n400.basis": "Spouse of U.S. citizen"}, "Ana Attorney", "attorney")
    status = naturalization.status(case)
    assert len([q for q in status["questions"] if q["section"] == naturalization.SPOUSE_SECTION]) == 4 and status["not_asked"] == []
    # a client's typed answer to an N-400 question asked in the portal lands on its card, as on the I-485's
    request = store.add_request(ID, "Please tell us: Legally change their name at naturalization?", None, "Paulo Paralegal", facts=["n400.name_change"],
                                typed={"type": "yes_no", "language": "en"})
    store.answer_request(ID, request["id"], reply="No")
    assert reviewed_graph(case).get("n400.name_change").value == "No"
    q = next(q for q in naturalization.status(case)["questions"] if q["key"] == "n400.name_change")
    assert q["value"] == "No" and q["sources"][0]["type"] == "office_question" and q["asks"] == "client"


def test_each_n400_card_starts_with_the_forms_own_opening_words():
    import re as _re

    import naturalization
    from pypdf import PdfReader

    assert naturalization.lead("n400.p9_7d").startswith("Have you EVER ordered, incited") and naturalization.lead("n400.p9_1") is None
    assert naturalization.lead("n400.p9_10b") is None and naturalization.lead("n400.p9_22c_date") is None
    for key, _label, _section, _spec, _req in naturalization.QUESTIONS:
        m = _re.fullmatch(r"n400\.p9_(5|6|7|17)[a-z]", key)
        assert bool(m) == bool(naturalization.lead(key)), key
    # every lead is the form's own words (the official PDF the packet fills)
    text = " ".join(" ".join(p.extract_text() for p in PdfReader(schema_path.path("template", "n400")).pages[5:9]).split())
    for words in set(naturalization.PART9_LEADS.values()) | {naturalization.PART9_INTRO}:
        assert " ".join(words.split()) in text, words
