"""The event ledger (src/events.py, read through review/oversight.py Events): one appended row for every change to a case's records and to the
firm's own records; append-only; one file a month; read through an index. Everyone here is made up (Ana Clara Exemplo Souza, Maria Exemplo)."""

from __future__ import annotations

import json
import re
import shutil
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

import clock
import documents
import events
import journey
import offices
import prefile
import restricted
from review import state
from review.oversight import Events

sys.path.insert(0, str(Path(__file__).resolve().parent))

import firm_world  # noqa: E402
import schema_path

REPO = Path(__file__).resolve().parent.parent
KINDS = {k: v["name"] for k, v in events.KINDS.items()}
SECRETS = ("MADE UP", "Ana Clara", "1234567", "9400111899223344556677", "WORKING-LINK", "made-up note", "Abuser reads her texts")


# what no sentence of the ledger may hold, because it reaches the screen: a date a person gave, a raw id or key, a form's id in lower case
RAW = re.compile(r"\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4}|\d{6,}|[a-z]+_[a-z0-9_]+|[a-z]+\.[a-z0-9_]+\.[a-z0-9_.]+|\b(?:i|n|g|ar|eoir|ds)\d+[a-z]?\b|\bpt\d|\bpart\d")


def plain_sentences(base: Path) -> None:
    for r in events.rows(base):
        assert not RAW.search(r["what"]), f"a code or a date on the screen: {r['what']!r}"
        assert "  " not in r["what"] and not r["what"].endswith((": ", " for ", "(a draft)")) or r["what"].endswith("(a draft)") and " for (" not in r["what"], r["what"]
        assert "—" not in r["what"] and " -- " not in r["what"], r["what"]


@pytest.fixture
def ledger(tmp_path, monkeypatch):
    base = tmp_path / "ledger" / "events.jsonl"
    monkeypatch.setenv("I485_EVENTS", str(base))
    yield base
    plain_sentences(base)  # every test that writes rows also proves what it wrote is words


def rows(base: Path, **match) -> list[dict]:
    return [r for r in events.rows(base) if all(r.get(k) == v for k, v in match.items())]


def one(base: Path, **match) -> dict:
    found = rows(base, **match)
    assert len(found) == 1, (match, found)
    return found[0]


# -- the row ---------------------------------------------------------------------------------------------------------------------


def test_a_row_has_exactly_the_documented_fields_and_lives_in_its_months_file(ledger):
    row = events.record("decisions", "confirmed", "Confirmed: applicant date of birth", case="case-a", who="Jane Paralegal", role="paralegal", via="staff", version=1)
    assert tuple(row) == events.FIELDS + events.CHAIN
    assert row["who"] == "Jane Paralegal" and row["role"] == "paralegal" and row["via"] == "staff" and row["case"] == "case-a" and row["version"] == 1
    assert re.fullmatch(r"\d{4}-\d{2}-\d{2}T\d{2}:\d{2}:\d{2}(\.\d+)?[+-]\d{2}:\d{2}", row["at"]), "UTC with its offset"
    month = row["at"][:7]
    assert (ledger.parent / f"events-{month}.jsonl").is_file() and not ledger.exists(), "rotation by month: the name is the ledger's, the rows are in its months"
    assert json.loads((ledger.parent / f"events-{month}.jsonl").read_text(encoding="utf-8").splitlines()[0]) == row


def test_a_file_a_month_and_the_rows_never_rewritten(ledger, monkeypatch):
    stamps = iter(["2026-08-31T23:59:59+00:00", "2026-09-01T00:00:01+00:00", "2026-09-01T00:00:02+00:00"])
    monkeypatch.setattr(clock, "stamp", lambda *a, **k: next(stamps))
    for n in range(3):
        events.record("settings", "changed", f"Changed the firm's details {n}", home=ledger.parent, who="Sam Attorney")
    assert [p.name for p in events.files(ledger)] == ["events-2026-08.jsonl", "events-2026-09.jsonl"]
    first, second = (p.read_text(encoding="utf-8") for p in events.files(ledger))
    assert first.count("\n") == 1 and second.count("\n") == 2
    events.record("settings", "changed", "Changed the firm's details 3", home=ledger.parent, who="Sam Attorney")
    # appended after what was there: the first lines are the same bytes as before
    assert events.files(ledger)[1].read_text(encoding="utf-8").startswith(second)
    assert events.files(ledger)[0].read_text(encoding="utf-8") == first


def test_the_product_never_rewrites_or_deletes_a_row():
    """No writer opens the ledger for anything but appending: the only open() of a ledger file in src/ is src/events.py's O_APPEND, and the readers read."""
    src = (REPO / "src" / "events.py").read_text(encoding="utf-8")
    assert src.count("os.O_APPEND") == 1 and "os.O_TRUNC" not in src and "unlink" not in src and "os.replace" not in src and "write_text" not in src
    for path in (REPO / "src").rglob("*.py"):  # nothing else in the product writes a ledger file: the writers call events.record
        if path.name != "events.py":
            assert 'f"events-' not in path.read_text(encoding="utf-8"), path


def test_a_ledger_that_cannot_be_written_is_said_and_never_raised(tmp_path, monkeypatch, capsys):
    blocked = tmp_path / "a-file"
    blocked.write_text("not a folder", encoding="utf-8")
    monkeypatch.setenv("I485_EVENTS", str(blocked / "events.jsonl"))
    assert events.record("settings", "changed", "x", who="Sam") is None
    assert "event ledger not written" in capsys.readouterr().err
    assert events.record("not-a-kind", "x", "x") is None


def test_who_is_the_acting_person_the_overnight_run_or_the_writer_that_knows(ledger):
    with events.acting("Jane Paralegal", "paralegal"):
        a = events.record("settings", "changed", "x", home=ledger.parent)
        b = events.record("settings", "changed", "x", home=ledger.parent, who="Sam Attorney", role="attorney")
    c = events.record("settings", "changed", "x", home=ledger.parent, who="Pat Typed")
    with events.acting("The overnight run", "system", "overnight"):
        d = events.record("facts", "read", "x", case="c", home=ledger.parent)
    e = events.record("settings", "changed", "x", home=ledger.parent)
    assert (a["who"], a["role"], a["via"]) == ("Jane Paralegal", "paralegal", "staff")
    assert (b["who"], b["role"]) == ("Sam Attorney", "attorney")
    assert (c["who"], c["role"]) == ("Pat Typed", "staff"), "a typed name with no accounts is staff"
    assert (d["who"], d["role"], d["via"]) == ("The overnight run", "system", "overnight")
    assert (e["who"], e["role"], e["via"]) == ("The product", "system", "system") and events.actor() is None


def test_what_names_a_fact_in_words_never_its_value():
    assert events.words("applicant.date_of_birth") == "applicant date of birth"
    assert events.list_words(["applicant.date_of_birth", "applicant.a_number", "x.y", "z.w", "q"]) == "applicant date of birth, applicant a number, x y and 2 more"


# -- every writer appends ------------------------------------------------------------------------------------------------------------


def _case(tmp_path) -> Path:
    clients = tmp_path / "data" / "clients"
    clients.mkdir(parents=True)
    return firm_world.make_case(clients, "ana-exemplo", decisions=0)


def test_decisions_append_who_what_and_the_case_never_the_value(tmp_path, ledger):
    d = _case(tmp_path)
    item = {"id": "i1", "kind": "reading", "level": "review", "title": "A made-up item", "group": "g", "actions": ["confirm", "set", "blank", "acknowledge"],
            "facts": [{"key": "applicant.date_of_birth", "short": "Date of birth", "input": {"type": "date"}}]}
    state.record_decision(d, item, {"action": "set", "values": {"applicant.date_of_birth": "2006-03-14"}, "reviewer": "Jane Paralegal", "role": "paralegal", "note": "made-up note"})
    row = one(ledger, kind="decisions", action="corrected")
    assert (row["who"], row["role"], row["case"], row["version"]) == ("Jane Paralegal", "paralegal", "ana-exemplo", 1)
    assert row["what"] == "Corrected: Date of birth"
    state.undo_decision(d, "i1", "Sam Attorney", "attorney")
    undone = one(ledger, kind="decisions", action="undone")
    assert undone["who"] == "Sam Attorney" and undone["what"] == "Reopened: A made-up item"
    text = (ledger.parent / next(p.name for p in events.files(ledger))).read_text(encoding="utf-8")
    assert "2006-03-14" not in text and "made-up note" not in text


def test_documents_set_by_a_person_and_built_by_the_reader_append(tmp_path, ledger):
    d = _case(tmp_path)
    rid = json.loads((d / "documents.json").read_text(encoding="utf-8"))["documents"][0]["id"]
    documents.set_person(d, rid, "spouse", "Jane Paralegal", "paralegal")
    documents.set_quality(d, rid, "blurry", "Jane Paralegal", "paralegal")
    documents.set_language(d, rid, "pt", "Jane Paralegal", "paralegal")
    documents.set_dates(d, rid, "2020-01-01|2030-01-01", "Jane Paralegal", "paralegal")
    documents.tag(d, rid, documents.roles()[0], "Jane Paralegal", "paralegal")
    documents.untag(d, rid, documents.roles()[0], "Jane Paralegal", "paralegal")
    got = {r["action"]: r for r in rows(ledger, kind="documents")}
    assert set(got) == {"set_person", "set_quality", "set_language", "set_dates", "tagged", "untagged"}
    assert all(r["who"] == "Jane Paralegal" and r["role"] == "paralegal" and r["version"] == documents.VERSION == 1 and r["case"] == "ana-exemplo" for r in got.values())
    assert "2020-01-01" not in json.dumps(list(got.values())) and "spouse" not in got["set_person"]["what"], "the date and the person are the person's data"
    built = documents.build(d / "source", {"passport-0.pdf": SimpleNamespace(doc_type="passport", confidence=0.9)}, {}, texts={"passport-0.pdf": "made-up"}, measure=False)
    documents.save_run(d, built)
    assert one(ledger, kind="documents", action="built")["who"] == "The document reader"


def test_journey_marks_mailing_records_and_the_filed_mark_append(tmp_path, ledger):
    d = _case(tmp_path)
    journey.mark(d, "done", "Jane Paralegal", item="i485.biometrics")
    journey.mark(d, "stage", "Sam Attorney", value="i360_ready")
    journey.mark(d, "lpr_date", "Sam Attorney", value="2026-01-02")
    journey.mark(d, "hearing", "Sam Attorney", value={"date": "2026-12-01", "kind": "Bond"})
    marks = {r["action"]: r for r in rows(ledger, kind="journey")}
    assert marks["done"]["what"] == "Marked a step done: a step" and marks["stage"]["what"].startswith("Set the stage: ") and "i360" not in marks["stage"]["what"]
    assert "2026-01-02" not in marks["lpr_date"]["what"] and "2026-12-01" not in marks["hearing"]["what"]
    prefile.append_record(d, {"filing": "i485", "title": "Form I-485", "mailed_on": "2026-09-01", "carrier": "USPS", "tracking": "9400111899223344556677"}, "Sam Attorney")
    assert one(ledger, kind="filings", action="mailed")["what"] == "Recorded as filed: Form I-485"
    prefile.append_record(d, {"filing": "i485", "mailed_on": "2026-09-02", "carrier": "USPS"}, "Sam Attorney")  # a record with no title: the filing's name, never its id
    assert [r["what"] for r in rows(ledger, kind="filings", action="mailed")][-1] == "Recorded as filed: Form I-485 (adjustment of status)"
    prefile.undo_filing(d, "Sam Attorney", "attorney")
    assert one(ledger, kind="filings", action="removed")["who"] == "Sam Attorney"
    from review import overview

    overview.mark_filed(d, True, "Sam Attorney")
    assert one(ledger, kind="filings", action="marked_filed")["case"] == "ana-exemplo"
    assert "9400111899223344556677" not in "".join(p.read_text(encoding="utf-8") for p in events.files(ledger))


def test_restriction_changes_the_office_and_translations_append(tmp_path, ledger):
    d = _case(tmp_path)
    restricted.mark(d, True, "Abuser reads her texts", "Sam Attorney", "attorney")
    restricted.name_person(d, "jane@firm.example", True, "Sam Attorney", "attorney", "Jane Paralegal")
    restricted.set_messages(d, True, "the client asked", "Sam Attorney", "attorney")
    restricted.mark(d, False, "", "Sam Attorney", "attorney")
    got = rows(ledger, kind="access")
    assert [r["action"] for r in got] == ["restricted", "named", "switched", "lifted"]
    assert all(r["who"] == "Sam Attorney" and r["case"] == "ana-exemplo" for r in got)
    assert "Abuser" not in json.dumps(got), "the reason is the firm's and stays on the case"
    restricted.protect_new(d.parent / "new-vawa", "1367", "VAWA self-petition", "Added as", "Jane Paralegal")
    assert one(ledger, kind="access", case="new-vawa")["what"].startswith("Restricted the case from the start")
    offices.choose(d, offices.offices()[0]["id"], "Sam Attorney")
    assert one(ledger, kind="office")["who"] == "Sam Attorney"


def test_settings_policies_approvals_upkeep_and_accounts_append(tmp_path, ledger):
    import maintenance
    import settings
    from review.auth import Accounts
    from rules import approval, firm_policies

    settings.save("sign_in", {"idle_minutes": "45"}, "Sam Attorney")
    settings.add_translator("Sam Attorney", "Maria Exemplo", ["pt"], "firm")
    settings.remove_translator(next(t["id"] for t in settings.translators() if t["name"] == "Maria Exemplo"), "Sam Attorney")
    changed = one(ledger, kind="settings", action="changed")
    assert changed["case"] is None and changed["who"] == "Sam Attorney" and changed["what"].startswith("Changed ") and "45" not in changed["what"]
    assert {r["what"] for r in rows(ledger, kind="settings", action="added")} == {"Added a translator"}
    assert "Maria Exemplo" not in json.dumps(rows(ledger, kind="settings")), "a translator's name is a person's"
    policy = firm_policies.shipped()[0]
    firm_policies.edit(policy["id"], "Sam Attorney", "attorney", plain_text=policy["plain_text"] + " (reviewed)")
    firm_policies.revert(policy["id"], "Sam Attorney", "attorney")
    assert [r["action"] for r in rows(ledger, kind="policies")] == ["edited", "reverted"]
    approval.approve(approval.catalog()[0]["id"], "Sam Attorney", "attorney")
    assert one(ledger, kind="policies", action="approved")["who"] == "Sam Attorney"
    registry = tmp_path / "register.json"
    shutil.copy(schema_path.path("register", "maintenance"), registry)
    maintenance.mark("firm_details", "Sam Attorney", registry, log_path=tmp_path / "log.json")
    assert one(ledger, kind="upkeep")["what"].startswith("Marked as checked: ")
    accounts = Accounts(tmp_path / "users.json")
    accounts.add("kim@firm.example", "Kim Exemplo", "paralegal", by=None)
    accounts.update("kim@firm.example", role="attorney")
    got = rows(ledger, kind="accounts")
    assert [r["what"] for r in got] == ["Added the account of Kim Exemplo as paralegal", "Changed the account of Kim Exemplo"], "adding a person is one row, with its one-time password"
    assert not any(s in json.dumps([r["what"] for r in got]) for s in ("hash", "salt", "one-time password:"))


def test_the_portals_answers_uploads_requests_and_messages_append(tmp_path, ledger):
    import io
    from pypdf import PdfWriter
    from portal.store import PortalStore

    store = PortalStore(tmp_path / "data" / "portal")
    store.add_client("ana-exemplo", "Ana Clara Exemplo Souza", email="ana@example.com", by="Jane Paralegal")
    store.save_answers("ana-exemplo", {"applicant.given_name": "ANA", "applicant.family_name": "SOUZA"})
    pdf = io.BytesIO()
    writer = PdfWriter(); writer.add_blank_page(width=72, height=72); writer.write(pdf)
    store.add_upload("ana-exemplo", "passport", "passport.pdf", pdf.getvalue(), "application/pdf")
    request = store.add_request("ana-exemplo", "Please send your birth certificate", "birth_certificate", "Jane Paralegal")
    store.add_message("ana-exemplo", "client", "I have a question about my hearing", None)
    store.add_message("ana-exemplo", "office", "We will call you", "Jane Paralegal")
    store.handle_messages("ana-exemplo", "Jane Paralegal")
    store.answer_request("ana-exemplo", request["id"], reply="made-up reply")
    got = {r["action"]: r for r in rows(ledger)}
    assert got["added"]["who"] == "Jane Paralegal" and got["added"]["what"] == "Added the client to the portal"
    assert (got["answered"]["who"], got["answered"]["role"], got["answered"]["via"]) == ("The client", "client", "portal")
    assert got["uploaded"]["who"] == "The client" and got["asked"]["who"] == "Jane Paralegal"
    assert got["wrote"]["case"] == "ana-exemplo"
    text = "".join(p.read_text(encoding="utf-8") for p in events.files(ledger))
    for secret in ("ANA", "SOUZA", "made-up reply", "hearing", "birth certificate", "ana@example.com", "We will call you"):
        assert secret not in text, secret
    assert {r["kind"] for r in rows(ledger)} == {"portal"}


def test_a_step_that_carries_a_date_is_said_by_its_kind_never_by_its_id(tmp_path, ledger):
    d = _case(tmp_path)
    for item, said in (("hearing.2026-12-01.0.prepare", "a court hearing"), ("moved.2026-09-01.0.ar11", "a change of address"), ("IOE0999000123.rfe.2026-09-20", "a request from USCIS"),
                       ("i360_ready.0", "a step")):
        journey.mark(d, "done", "Sam Attorney", item=item)
        assert rows(ledger, kind="journey", action="done")[-1]["what"] == f"Marked a step done: {said}"
    journey.mark(d, "track", "Sam Attorney", value="sij")
    assert rows(ledger, kind="journey", action="track")[-1]["what"] == "Set the track: Special Immigrant Juvenile"
    text = "".join(p.read_text(encoding="utf-8") for p in events.files(ledger))
    for leak in ("2026-12-01", "2026-09-01", "IOE0999000123", "2026-09-20", "ar11"):
        assert leak not in text, leak


def test_a_fact_with_a_receipt_or_passport_number_in_its_key_never_reaches_the_ledger(ledger, tmp_path):
    d = _case(tmp_path)
    item = {"id": "i2", "kind": "reading", "level": "review", "title": "A made-up item", "group": "g", "actions": ["confirm"],
            "facts": [{"key": "folder.uscis_case.IOE0999000123.approval_20250820", "input": {"type": "text"}}, {"key": "folder.passport.XX0001234", "short": "XX0001234", "input": {"type": "text"}}]}
    state.record_decision(d, item, {"action": "confirm", "reviewer": "Jane Paralegal", "role": "paralegal"})
    text = "".join(p.read_text(encoding="utf-8") for p in events.files(ledger))
    assert "IOE0999000123" not in text and "XX0001234" not in text and "20250820" not in text
    assert events.words("folder.uscis_case.IOE0999000123.approval_20250820") == "folder uscis case approval"
    assert events.mask("folder.uscis_case.IOE0999000123.approval_20250820", {}) == "folder.uscis_case.#1.approval_#2"


def test_every_filing_in_a_sentence_is_named_in_words(tmp_path, ledger):
    import packet

    assert packet.filing_title("i485") == "Form I-485 (adjustment of status)" and packet.filing_title(None) == "Form I-485 (adjustment of status)"
    assert packet.filing_title("i601a") == "Provisional unlawful presence waiver (I-601A)" and packet.filing_title("nonsense") == "a filing"
    from online_filing import _title

    assert _title("eoir28").startswith("EOIR-28")


def test_a_connectors_sync_rows_land_in_the_data_folder_not_beside_the_clients_folder(tmp_path, monkeypatch):
    """The overnight run's clients root, as installed, is <repo>/clients: the ledger is <repo>/data/events-*.jsonl, whatever I485_EVENTS says in a test."""
    from connectors import sync
    from connectors.base import RemoteClient, RemoteDoc

    monkeypatch.delenv("I485_EVENTS", raising=False)
    monkeypatch.setattr(events, "REPO", tmp_path / "repo")

    class Source:
        name = "google_drive"

        def clients(self):
            return [RemoteClient("77", "Ana Exemplo")]

        def documents(self, client):
            return [RemoteDoc("d1", "passport.pdf", "application/pdf", 12, "2026-09-01", "abc")]

        def download(self, client, doc):
            return b"%PDF-1.4 made up"

    clients = tmp_path / "repo" / "clients"
    clients.mkdir(parents=True)
    sync.mirror(Source(), clients, home=tmp_path / "repo" / "data")
    assert [p.parent.name for p in events.files(tmp_path / "repo" / "data" / "events.jsonl")] == ["data"]
    assert not list((tmp_path / "repo").glob("events-*.jsonl")), "nothing beside the clients folder"
    sync.mirror(Source(), clients)  # no home given: the data folder the product uses by default
    assert not list((tmp_path / "repo").glob("events-*.jsonl"))


def test_the_other_writes_append_too(tmp_path, ledger):
    from connectors import clio
    from portal.store import PortalStore

    store = PortalStore(tmp_path / "data" / "portal")
    store.add_client("ana-exemplo", "Ana Clara Exemplo Souza", by="Jane Paralegal")
    store.log("ana-exemplo", "invited", {"by": "Jane Paralegal"})
    store.log("ana-exemplo", "reminded", {"by": "Jane Paralegal"})
    store.log("ana-exemplo", "language_changed", {})
    store.add_message("ana-exemplo", "office", "We will call you", "Jane Paralegal")
    store.mark_messages_seen("ana-exemplo")
    got = {r["action"]: r for r in rows(ledger, kind="portal")}
    assert got["invited"]["who"] == "Jane Paralegal" and got["reminded"]["who"] == "Jane Paralegal"
    assert (got["changed"]["who"], got["changed"]["via"]) == ("The client", "portal") and (got["read"]["who"], got["read"]["role"]) == ("The client", "client")
    d = _case(tmp_path)
    import case_status

    rec = {"checked_at": "2026-10-03T06:00:00-04:00", "text": "Case Was Received"}
    case_status.save(d, "IOE0999000123", rec)
    case_status.save(d, "IOE0999000123", rec | {"checked_at": "2026-10-04T06:00:00-04:00"})  # the same answer a night later: no row
    assert len(rows(ledger, kind="journey", action="checked")) == 1 and rows(ledger, kind="journey", action="checked")[0]["who"] == "The overnight run"
    import drafting

    drafting._save(d, "i485", {"status": "draft"})
    assert one(ledger, kind="packet", action="saved")["what"] == "Saved the client's declaration for Form I-485 (adjustment of status)"
    clio.save_settings(tmp_path / "data", {"on": True}, "Sam Attorney")
    assert one(ledger, kind="settings", action="changed")["what"] == "Changed the Clio connection's settings"


def test_a_connectors_sync_and_the_importer_append(tmp_path, ledger):
    from connectors import sync
    from connectors.base import RemoteClient, RemoteDoc
    from portal.store import PortalStore

    class Source:
        name = "google_drive"

        def clients(self):
            return [RemoteClient("77", "Ana Exemplo")]

        def documents(self, client):
            return [RemoteDoc("d1", "passport.pdf", "application/pdf", 12, "2026-09-01", "abc")]

        def download(self, client, doc):
            return b"%PDF-1.4 made up"

    clients = tmp_path / "data" / "clients"
    clients.mkdir(parents=True)
    sync.mirror(Source(), clients)
    row = one(ledger, kind="imports", action="synced")
    assert (row["who"], row["role"], row["via"]) == ("Google Drive", "system", "connector") and row["case"].startswith("ana_exemplo-gd")
    assert row["what"] == "Copied in 1 new and 0 changed document(s) from Google Drive"
    store = PortalStore(tmp_path / "data" / "portal")
    store.add_client("ana-exemplo", "Ana Clara Exemplo Souza", by="firm")
    store.log("ana-exemplo", "imported_from_docketwise", {"by": "firm"})
    imported = one(ledger, kind="imports", action="imported")
    assert (imported["who"], imported["via"]) == ("The importer", "importer")


def test_the_overnight_run_is_the_name_on_what_it_writes(tmp_path, ledger):
    import overnight

    root = tmp_path / "firm"
    (root / "clients").mkdir(parents=True)

    def runner(name, source, out):
        events.record("facts", "read", "Read 1 documents into 2 facts", case=name, home=root)
        return {"status": "done", "client": name, "counts": {"blocking": 0, "review": 0}, "seconds": 0.1, "errors": {}}

    (root / "clients" / "ana" / "source").mkdir(parents=True)
    (root / "clients" / "ana" / "source" / "x.pdf").write_bytes(b"%PDF-1.4 made up")
    overnight.run(root / "clients", root / "out", root / "data", runner=runner, log=lambda *_: None)
    row = one(ledger, kind="facts", case="ana")
    assert (row["who"], row["role"], row["via"]) == ("The overnight run", "system", "overnight")


def test_every_ledger_kind_a_writer_uses_is_one_the_dictionary_knows():
    used = set()
    for path in (REPO / "src").rglob("*.py"):
        used |= set(re.findall(r'events\.record\(\s*"([a-z_]+)"', path.read_text(encoding="utf-8")))
    assert used and used <= set(events.KINDS), used - set(events.KINDS)
    # one kind is written some other way: the export rows, by tools/export_firm.py (the portal rows are also written by the store, src/portal/store.py, and directly by src/client_case.py and src/client_reminders.py)
    assert set(events.KINDS) - used == {"export"}, "a kind nothing writes: " + ", ".join(sorted(set(events.KINDS) - used))
    assert '"export"' in (REPO / "tools" / "export_firm.py").read_text(encoding="utf-8") and '"portal"' in (REPO / "src" / "portal" / "store.py").read_text(encoding="utf-8")


# -- reading it ----------------------------------------------------------------------------------------------------------------------


def _fill(ledger, months=("2026-07", "2026-08", "2026-09"), per=60, cases=3):
    n = 0
    for m in months:
        path = ledger.parent / f"events-{m}.jsonl"
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8") as f:
            for i in range(per):
                n += 1
                f.write(json.dumps({"at": f"{m}-{1 + i * 27 // per:02d}T10:{i % 60:02d}:00+00:00", "who": "Jane Paralegal" if i % 2 else "Sam Attorney", "role": "paralegal" if i % 2 else "attorney",
                                    "via": "staff", "case": f"case-{i % cases}" if i % 10 else None, "kind": "decisions" if i % 10 else "settings", "version": 1, "action": "confirmed",
                                    "what": f"Confirmed: thing {n}"}) + "\n")
    return n


def test_the_reader_pages_filters_and_reads_few_rows(ledger):
    total = _fill(ledger)
    ev = Events(ledger, KINDS)
    first = ev.firm(page=1)
    assert first["total"] == total and first["pages"] == 4 and len(first["rows"]) == 50 and first["per"] == 50
    assert first["rows"][0]["at"] >= first["rows"][-1]["at"], "newest first, across the months"
    assert [r["at"][:7] for r in first["rows"]][0] == "2026-09"
    last = ev.firm(page=4)
    assert len(last["rows"]) == total - 150 and last["rows"][-1]["at"][:7] == "2026-07"
    assert ev.firm(person="Sam Attorney")["total"] == total // 2
    assert ev.firm(kind="settings")["total"] == total // 10
    assert ev.firm(case="case-1")["total"] == sum(1 for r in events.rows(ledger) if r["case"] == "case-1")
    august = ev.firm(start="2026-08-01", end="2026-08-31")
    assert august["total"] == 60 and {r["at"][:7] for r in august["rows"]} == {"2026-08"}
    assert {k["id"] for k in first["kinds"]} == {"decisions", "settings"} and [p["name"] for p in first["people"]] == ["Jane Paralegal", "Sam Attorney"]
    # a page of 50 reads 50 rows from the files, not 180 (and the index is built once)
    reads = sum(ix.rows_read for ix in ev._indexes.values())
    ev.firm(page=2)
    assert sum(ix.rows_read for ix in ev._indexes.values()) - reads == 50
    with pytest.raises(ValueError, match="MM/DD/YYYY"):
        ev.firm(start="soon")


def test_the_reader_extends_its_index_by_what_was_appended_and_finds_a_new_month(ledger):
    _fill(ledger, months=("2026-09",), per=10)
    ev = Events(ledger, KINDS)
    assert ev.firm()["total"] == 10
    scanned = sum(ix.bytes_scanned for ix in ev._indexes.values())
    events.record("settings", "changed", "Changed x", home=ledger.parent, who="Sam Attorney")
    assert ev.firm()["total"] == 11
    assert sum(ix.bytes_scanned for ix in ev._indexes.values()) - scanned < 400, "only the new row was read"
    (ledger.parent / "events-2099-01.jsonl").write_text(json.dumps({"at": "2099-01-02T10:00:00+00:00", "who": "X", "kind": "settings", "what": "new month"}) + "\n", encoding="utf-8")
    assert ev.firm()["total"] == 12 and ev.firm()["rows"][0]["what"] == "new month"


def test_a_cases_rows_and_a_restricted_cases_rows_closed_to_whoever_may_not_open_it(ledger):
    _fill(ledger, months=("2026-09",), per=30, cases=3)
    ev = Events(ledger, KINDS)
    mine = ev.case("case-1", limit=5)
    assert mine["total"] == len([r for r in events.rows(ledger) if r["case"] == "case-1"]) and len(mine["rows"]) == 5
    assert all(r["case"] == "case-1" for r in mine["rows"]) and ev.case("nobody", limit=5)["rows"] == []
    hidden = {"case-1"}
    assert all(r["case"] != "case-1" for r in ev.firm(hidden=hidden)["rows"]) and ev.firm(hidden=hidden)["total"] == ev.firm()["total"] - mine["total"]
    assert b"case-1" not in ev.csv(hidden=hidden) and b"case-1" in ev.csv()
    assert ev.case("case-1", page=1, hidden=hidden)["total"] == 0, "even asked for by name"
    # the pick lists on a case's page are made from that case's own rows: a name that changed nothing on it, or a kind of change nobody made on it, is not offered
    with open(events.files(ledger)[-1], "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": "2026-09-28T10:00:00+00:00", "who": "Solo Person", "role": "staff", "via": "staff", "case": "case-solo", "kind": "export", "version": 1, "action": "x",
                            "what": "Something else"}) + "\n")
    page = ev.case("case-1", page=1)
    assert [p["name"] for p in page["people"]] == ["Jane Paralegal", "Sam Attorney"] and {k["id"] for k in page["kinds"]} == {"decisions", "settings"} - {"settings"} | {"decisions"}
    solo = ev.case("case-solo", page=1)
    assert [p["name"] for p in solo["people"]] == ["Solo Person"] and [k["id"] for k in solo["kinds"]] == ["export"]
    assert "Solo Person" in [p["name"] for p in ev.firm()["people"]], "the firm's list keeps offering everyone"


def test_the_csv_escapes_a_cell_that_could_run_as_a_formula(ledger):
    events.record("settings", "changed", "=HYPERLINK(\"http://x\")", home=ledger.parent, who="+cmd", role="attorney")
    csv = Events(ledger, KINDS).csv().decode("utf-8-sig")
    assert "'=HYPERLINK" in csv and "'+cmd" in csv and csv.splitlines()[0].startswith("When,Who,Role,Kind,Action,What changed,Case,Record version")


def test_two_writers_never_mix_their_rows(ledger):
    import threading

    def write(name):
        for n in range(60):
            events.record("settings", "changed", f"{name} {n} " + "x" * 200, home=ledger.parent, who=name)

    threads = [threading.Thread(target=write, args=(f"w{i}",)) for i in range(6)]
    [t.start() for t in threads]
    [t.join() for t in threads]
    lines = events.files(ledger)[0].read_text(encoding="utf-8").splitlines()
    assert len(lines) == 360 and all(json.loads(line)["what"].endswith("x" * 200) for line in lines)
