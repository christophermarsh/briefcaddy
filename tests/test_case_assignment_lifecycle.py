"""Assignment catalog/exit/client-file/backup and actual attorney-gated Q1 purge."""
from file_policy_fixture import own_case_identity, disposition
import json
import zipfile
from datetime import datetime, timedelta
from pathlib import Path

import pytest

import backups
import case_assignment as ca
import client_file
import clock
import engagement
import events
import purge
import records
import export_firm

from test_purge import firm as purge_firm  # noqa: F401 -- pytest fixture registration and helper reexports
from test_restricted import doc, make_case
from test_case_assignment_lists import accounts, assignment_record, T


def test_registered_catalog_state_and_working_residue():
    assert "case_assignment.json" in records.patterns("case")
    assert records.record_of("case", ca.FILE)["id"] == "case_assignment"
    assert records.coverage("case", ca.FILE) == "listed"
    assert records.coverage("case", ca.PART) == "never"
    assert records.not_backed_up("clients/fictional/" + ca.PART)
    assert not records.not_backed_up("clients/fictional/" + ca.FILE)
    assert any(row["id"] == "case_assignment_partial" for row in records.WORKING_FILES)
    assert events.KINDS[ca.KIND]["firm"] is False


def test_firm_exit_includes_staff_assignment_and_q2_never_selects_it(tmp_path, monkeypatch):
    clients = tmp_path / "data" / "clients"; folder = clients / "fictional-case"; folder.mkdir(parents=True)
    portal = tmp_path / "data" / "portal"
    monkeypatch.setenv("I485_EVENTS", str(clients.parent / "events.jsonl"))
    (folder / "fact_graph.json").write_text("{}")
    (folder / ca.FILE).write_text(json.dumps(assignment_record(T)))
    (folder / ca.PART).write_text("fictional interrupted transfer")
    # Explicit negative: staff records registered as documents are still internal.
    (folder / "documents.json").write_text(json.dumps({"documents": [{"files": [ca.FILE, ca.PART]}]}))
    where = export_firm.default_where(clients, portal, clients.parent / "review_users.json")
    result = export_firm.everything(where, who="Fictional Attorney", role="attorney")
    with zipfile.ZipFile(result["path"]) as archive:
        assert "cases/fictional-case/" + ca.FILE in archive.namelist()
        assert not any(name.endswith(ca.PART) for name in archive.namelist())
        assert json.loads(archive.read("cases/fictional-case/" + ca.FILE))["assignee"]["email"] == T
    selected, excluded, _ = client_file.gather(folder, portal)
    assert not any(item.source and item.source.name in (ca.FILE, ca.PART) for item in selected)
    assert any(item["path"].endswith(ca.FILE) and "Internal case, authority or security record" in item["why"] for item in excluded)


def test_actual_q1_purge_removes_assignment_partial_cache_and_future_backup(purge_firm, monkeypatch):  # noqa: F811 -- pytest fixture injection
    firm = purge_firm
    folder = make_case(firm.clients, "fictional-assignment-purge", "Fictional Assignment Client", [doc("fictional-doc", "passport", text="Fictional passport")])
    keep = make_case(firm.clients, "fictional-assignment-keep", "Fictional Keeper", [doc("keep-doc", "passport", text="Fictional keeper")])
    store = ca.Assignments(firm.clients, firm.data / "jobs", accounts)
    store.change(folder.name, T, "reassign", target_email=T, reason="fictional-transfer-canary", expected_revision=0, operation_id="b" * 32)
    assert records.coverage("case", ca.FILE) == "listed"
    (folder / ca.PART).write_text("fictional-partial-canary")
    (firm.data / "roster.json").write_text(json.dumps({"version": 4, "entries": {folder.name: {"assignment": ca.summary(folder)}, keep.name: {"row": {"id": keep.name}}}}))
    before = backups.make_backup(firm.tmp / "backup-before", firm.data, clients=firm.docs)
    with zipfile.ZipFile(before["archive"]) as archive:
        assert any(name.endswith(ca.FILE) for name in archive.namelist())
        assert not any(name.endswith(ca.PART) for name in archive.namelist())
    engagement.end(folder, "closed", "Fictional Attorney", "attorney", reason="Fictional closed case", portal_root=firm.portal)
    purge.record_contact(folder, "phone", "10/05/2026", "Fictional contact attempt", "Fictional Attorney", "attorney")
    purge.review_originals(folder, [], True, "Fictional Attorney", "attorney")
    own_case_identity(folder)
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 10, 30))
    disposition(folder, who="Fictional Attorney", portal_root=firm.portal,
                completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    requested = purge.ask(folder, "Fictional early purge acceptance", "Fictional Attorney", "attorney", attorneys=1)
    with pytest.raises(purge.PurgeError):
        purge.run(firm.clients, folder.name, firm.portal)
    clock._now_override = datetime.fromisoformat(requested["purge_on"]) + timedelta(hours=12)
    purge.run(firm.clients, folder.name, firm.portal)
    assert not folder.exists() and keep.exists()
    assert purge.entry(firm.clients, folder.name)["state"] == "done"
    roster = json.loads((firm.data / "roster.json").read_text())
    assert folder.name not in roster["entries"] and keep.name in roster["entries"]
    assert not any(row["kind"] == ca.KIND for row in events.rows(events.base_path(firm.data), case=folder.name))
    after = backups.make_backup(firm.tmp / "backup-after", firm.data, clients=firm.docs)
    with zipfile.ZipFile(after["archive"]) as archive:
        assert not any(name.endswith(ca.FILE) or name.endswith(ca.PART) for name in archive.namelist())
        assert not any(b"fictional-transfer-canary" in archive.read(name) or b"fictional-partial-canary" in archive.read(name) for name in archive.namelist())
    # Prior retained backups are intentionally not rewritten by Q1.
    assert Path(before["archive"]).exists()
