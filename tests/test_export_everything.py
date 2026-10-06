"""Export everything (tools/export_firm.py --everything): the exit made real. A made-up firm (tests/firm_world.py) is exported; every file the data
dictionary lists is in the zip and nothing else is, the manifest verifies, and no key or secret is inside. Everyone here is made up."""

from __future__ import annotations

import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

import events
from review.auth import Accounts

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "tools"))
sys.path.insert(0, str(Path(__file__).resolve().parent))

import export_firm  # noqa: E402
import firm_world  # noqa: E402

REPO = Path(__file__).resolve().parent.parent
SECRETS = ("SEALED-SECRET", "ab12" * 16, "cd34" * 8, "RECOVERY-HASH", "PROVIDER-KEY", "WORKING-LINK", "NOT DATA", "ef56" * 16, "SEALED-CLIO-TOKEN", "CLIO-VAULT-KEY")


@pytest.fixture
def firm(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "data" / "events.jsonl"))
    monkeypatch.delenv("I485_SETTINGS", raising=False)
    monkeypatch.delenv("I485_POLICIES_FIRM", raising=False)
    monkeypatch.delenv("I485_RULES_APPROVED", raising=False)
    monkeypatch.delenv("I485_MAINTENANCE_LOG", raising=False)
    f = firm_world.make_firm(tmp_path, cases=2)
    outside = tmp_path / "scans-elsewhere"  # a case's original documents kept outside the data folder
    outside.mkdir()
    (outside / "birth.pdf").write_bytes(b"%PDF-1.4 made up birth certificate")
    (outside / "notes.txt").write_text("not a document", encoding="utf-8")
    meta = f["clients"] / "ana-exemplo-1" / "meta.json"
    meta.write_text(json.dumps({"client_id": "ana-exemplo-1", "source_folder": str(outside), "classifications": {}}), encoding="utf-8")
    events.record("decisions", "confirmed", "Confirmed: applicant date of birth", case="ana-exemplo", who="Jane Paralegal", role="paralegal", version=1)
    return f | {"outside": outside}


def where(f) -> export_firm.Where:
    return export_firm.default_where(f["clients"], f["portal"], f["users"])


def read_zip(path: Path) -> dict[str, bytes]:
    with zipfile.ZipFile(path) as z:
        assert z.testzip() is None
        return {n: z.read(n) for n in z.namelist()}


def test_every_file_the_dictionary_lists_is_in_the_zip_and_nothing_else(firm):
    done = export_firm.everything(where(firm), who="Sam Attorney", role="attorney", via="staff")
    path = Path(done["path"])
    assert path.parent == firm["data"] / "exports" and path.name.startswith("i485-firm-data-") and path.name.endswith(".zip")
    got = set(read_zip(path))
    expected = {"README.txt", "data_dictionary.md", "manifest.json", "MANIFEST.md"}
    for case in ("ana-exemplo", "ana-exemplo-1"):
        expected |= {f"cases/{case}/{n}" for n in ("fact_graph.json", "meta.json", "documents.json", "decisions.json", "source/passport-0.pdf")}
    expected |= {"cases/ana-exemplo/status.json", "cases/ana-exemplo/packet.pdf", "cases/ana-exemplo/i485_filled.pdf", "cases/ana-exemplo/packet_i360.json",
                 "cases/ana-exemplo/flag_report.txt", "cases/ana-exemplo-1/documents/birth.pdf"}  # the original document outside the data folder, not the stray note beside it
    expected |= {f"cases/rosa-exemplo/{n}" for n in ("fact_graph.json", "meta.json", "documents.json", "decisions.json", "status.json", "source/passport-0.pdf")}  # a restricted case is the firm's data
    expected |= {f"cases/ana-exemplo/{n}" for n in ("translations/translation-0123456789abcdef.pdf", "declarations/declaration-i485.pdf", "declarations.json", "translations.json",
                                                    "packet_review_bundle.pdf", "packet_review_bundle.json")}
    expected |= {f"firm/{n}" for n in ("batch_state.json", "batch_progress.json", "batch_log.jsonl", "batch_report.txt", "case_status_run.json", "accuracy_history.jsonl", "getting_started.json",
                                       "inbox/queue.json", "inbox/inbox_log.jsonl", "inbox/waiting/n-1.pdf", "clio/state.json", "clio/settings.json", "reference/case-a.pdf",
                                       "reference/case-a.marks.json", "sync_state.json")}
    expected |= {f"portal/ana-exemplo/{n}" for n in ("profile.json", "answers.json", "events.jsonl", "uploads/passport-0123456789abcdef.pdf")}
    expected |= {"firm/settings.json", "firm/policies_firm.json", "firm/rules_approved.json", "firm/maintenance_log.json", "firm/review_users.json",
                 "logs/review_users_access.jsonl", "logs/review_views.jsonl"}
    expected |= {f"ledger/{p.name}" for p in events.files(events.base_path(firm["data"]))}
    expected |= {"index.html"} | {f"ledger/{p.stem}.html" for p in events.files(events.base_path(firm["data"]))}  # the export reads itself (src/export_reader.py)
    assert got == expected, (sorted(got - expected), sorted(expected - got))
    assert done["files"] == len(got) and done["bytes"] == path.stat().st_size and len(done["sha256"]) == 64
    assert hashlib.sha256(path.read_bytes()).hexdigest() == done["sha256"]


def test_the_listing_is_the_dictionarys_not_a_second_list(firm):
    """The files in the zip are decided by src/records.py, the catalog docs/data_dictionary.md is made from: a record added there is exported."""
    import records

    assert "events-*.jsonl" in records.patterns("logs") and "decisions.json" in records.patterns("case") and "profile.json" in records.patterns("portal")
    assert "overview.json" not in records.patterns("case") and "auth.json" not in records.patterns("portal") and "overview.json" in records.patterns("case", exported_only=False)
    assert export_firm.listed("source/a.pdf", ["source/*"]) and not export_firm.listed("source/sub/a.pdf", ["source/*"]) and not export_firm.listed("sub/a.pdf", ["*.pdf"])
    assert export_firm.listed("a.pdf", ["*.pdf"]) and not export_firm.listed("secret.key", records.patterns("case"))


def test_the_manifest_verifies_member_by_member(firm):
    path = Path(export_firm.everything(where(firm), who="Sam Attorney", role="attorney")["path"])
    members = read_zip(path)
    manifest = json.loads(members["manifest.json"])
    assert manifest["scope"] == "everything the firm keeps" and manifest["file_count"] == len(manifest["files"])
    listed = {f["path"]: f for f in manifest["files"]}
    assert set(members) - {"manifest.json", "MANIFEST.md"} == set(listed), "every member is listed and every listed file is there"
    for name, f in listed.items():
        assert f["bytes"] == len(members[name]) and f["sha256"] == hashlib.sha256(members[name]).hexdigest(), name
        assert f["what"] and f["format"]
    assert "cases/ana-exemplo/documents.json" in members["MANIFEST.md"].decode("utf-8")
    assert [n for n in manifest["not_included"] if "deployment.json" in n and "provider's keys" in n]


def test_no_key_secret_hash_or_working_link_is_inside_and_the_accounts_have_only_the_safe_fields(firm):
    members = read_zip(Path(export_firm.everything(where(firm), who="Sam Attorney", role="attorney")["path"]))
    blob = b"\n".join(members.values()).decode("utf-8", errors="replace")
    for secret in SECRETS:
        assert secret not in blob, secret
    names = " ".join(members)
    for forbidden in ("deployment", "auth.json", "outbox", ".key", "index.db", "query.db", "overview.json", "journey_summary", "confidentiality.json", "secret", "maintenance_status",
                      "backup_log", ".reading", "vault", "exports/"):
        assert forbidden not in names, forbidden
    users = json.loads(members["firm/review_users.json"])
    assert users == {"users": {"sam@firm.example": {"name": "Sam Attorney", "role": "attorney", "active": True, "created_at": "2026-01-01T09:00:00+00:00"}}}


def test_the_dictionary_is_at_the_root_and_the_readme_says_what_each_folder_is(firm):
    members = read_zip(Path(export_firm.everything(where(firm), who="Sam Attorney", role="attorney")["path"]))
    assert members["data_dictionary.md"] == (REPO / "docs" / "data_dictionary.md").read_bytes()
    readme = members["README.txt"].decode("utf-8")
    for needle in ("cases/<case id>/", "portal/<client id>/", "firm/", "logs/", "ledger/", "data_dictionary.md", "manifest.json", "need our software", "Sam Attorney",
                   "Restricted cases", "are in this zip", "not encrypted", "deployment.json", "sha256"):
        assert needle in readme, needle
    assert " -- " not in readme and "—" not in readme


def test_the_ledger_and_the_access_log_record_who_exported_and_the_zip_carries_the_start_row(firm):
    accounts = Accounts(firm["users"])
    done = export_firm.everything(where(firm), who="Sam Attorney", role="attorney", via="staff",
                                  access_log=lambda files: accounts.log("data_exported", "sam@firm.example", files=files))
    started = [r for r in events.rows(events.base_path(firm["data"])) if r["kind"] == "export"]
    assert [r["action"] for r in started] == ["started", "exported"]
    assert all((r["who"], r["role"], r["via"], r["case"]) == ("Sam Attorney", "attorney", "staff", None) for r in started)
    assert started[1]["what"] == f"Exported the firm's data: {done['files']} files"
    inside = read_zip(Path(done["path"]))
    ledger = "".join(v.decode("utf-8") for n, v in inside.items() if n.startswith("ledger/"))
    assert '"action":"started"' in ledger and '"action":"exported"' not in ledger, "the zip's own copy holds the row that says it began"
    log = [json.loads(line) for line in accounts.log_path.read_text(encoding="utf-8").splitlines()]
    assert [(r["event"], r["email"], r["files"]) for r in log if r["event"] == "data_exported"] == [("data_exported", "sam@firm.example", 0), ("data_exported", "sam@firm.example", done["files"])]
    assert '"event": "data_exported"' in inside["logs/review_users_access.jsonl"].decode("utf-8")
    from review import oversight

    shown = oversight.StaffLog.__new__(oversight.StaffLog)
    assert shown._what({"event": "data_exported", "email": "sam@firm.example", "files": done["files"]}, {}) == f"Exported all of the firm's data ({done['files']} files)"
    assert oversight._access_fields({"event": "data_exported", "email": "sam@firm.example", "at": "2026-10-03T14:00:00+00:00"})[1:3] == ("sam@firm.example", "export")


def test_it_never_overwrites_an_earlier_export_and_never_writes_inside_what_it_reads(firm):
    first = Path(export_firm.everything(where(firm), who="Sam Attorney")["path"])
    second = Path(export_firm.everything(where(firm), who="Sam Attorney")["path"])
    assert first != second and first.exists() and second.exists() and second.name.endswith("-2.zip")
    with pytest.raises(export_firm.ExportError, match="Refusing to write the export inside"):
        export_firm.everything(where(firm), out=firm["clients"] / "ana-exemplo", who="Sam Attorney")
    with pytest.raises(export_firm.ExportError, match="already exists"):
        export_firm.everything(where(firm), out=first, who="Sam Attorney")
    # the exports folder is never part of a backup of the data folder
    from backups import EXPORTS

    assert EXPORTS == "exports" and first.parent.name == EXPORTS


def test_a_case_whose_documents_are_not_on_this_machine_is_said_not_failed(firm):
    (firm["clients"] / "ana-exemplo-1" / "meta.json").write_text(json.dumps({"client_id": "x", "source_folder": "/nowhere/at/all", "classifications": {}}), encoding="utf-8")
    done = export_firm.everything(where(firm), who="Sam Attorney")
    assert any("ana-exemplo-1" in w and "not on this machine" in w for w in done["warnings"])


def test_every_file_a_world_holds_is_in_the_dictionary_or_named_as_never_exported(firm):
    """The walk: after everything has written its files, a file nothing accounts for (the verifier found the signed translations and the declarations, the inbox, the
    overnight run's records) fails here, so a new kind of file cannot be left out of the export without anyone deciding it."""
    import records

    (firm["clients"] / "ana-exemplo" / "secret.key").unlink()  # the stray the other tests plant: nothing the product writes
    done = export_firm.everything(where(firm), who="Sam Attorney", role="attorney")
    members = set(read_zip(Path(done["path"])))
    data = firm["data"]
    uncovered, exported, never = [], set(), set()
    for path in sorted(p for p in data.rglob("*") if p.is_file()):
        rel = path.relative_to(data).as_posix()
        if rel.startswith("clients/") and rel.count("/") >= 2:
            area, inner = "case", rel.split("/", 2)[2]
        elif rel.startswith("portal/clients/") and rel.count("/") >= 3:
            area, inner = "portal", rel.split("/", 3)[3]
        else:
            area, inner = "firm", rel
        got = records.coverage(area, inner)
        if got is None:
            uncovered.append(rel)
        (exported if got == "listed" else never).add(rel)
    assert not uncovered, f"files the dictionary does not cover: {uncovered}"
    assert "inbox/.reading" in never and "clio/vault.key" in never and "maintenance_status.json" in never and "backup_log.json" in never and "clients/ana-exemplo/overview.json" in never
    # and what the dictionary lists is what is exported: every listed file is in the zip (under its place in it), every never-exported one is not
    exports = {n.split("/", 2)[-1] if n.startswith(("cases/", "portal/")) else n.removeprefix("firm/") for n in members}
    for rel in exported:
        inner = rel.split("/", 2)[2] if rel.startswith("clients/") and rel.count("/") >= 2 else rel.split("/", 3)[3] if rel.startswith("portal/clients/") and rel.count("/") >= 3 else rel.removeprefix("clients/")
        assert inner in exports or rel.endswith(("review_users.json", "learning.db")) or rel.startswith("events-") or rel.startswith("review_"), rel
    assert not any(rel.endswith(("secrets.enc", "vault.key", ".reading")) for rel in members)
    # a file nothing accounts for is caught
    (data / "clients" / "ana-exemplo" / "mystery.dat").write_text("?", encoding="utf-8")
    assert records.coverage("case", "mystery.dat") is None and records.coverage("firm", "mystery.dat") is None


def test_a_world_the_product_built_and_ran_over_has_no_file_the_dictionary_misses(tmp_path, monkeypatch):
    """The same walk over a world the product itself wrote: the demo client and the cases cloned from it (tests/e2e/world.py), then the overnight run (its records, the
    timelines' caches, the search index, the query layer, the accuracy history, the ledger), a translation's PDF and a declaration's, and an export."""
    import overnight
    import records

    monkeypatch.syspath_prepend(str(REPO / "tests" / "e2e"))
    import world as w

    root = tmp_path / "w"
    info = w.build(root)
    monkeypatch.setenv("I485_EVENTS", str(root / "events.jsonl"))
    monkeypatch.setenv("I485_QUERY_DB", str(root / "query.db"))
    monkeypatch.setenv("I485_INDEX", str(root / "index.db"))
    monkeypatch.setenv("I485_REFERENCE", str(root / "reference"))
    monkeypatch.setenv("I485_ACCURACY_HISTORY", str(root / "accuracy_history.jsonl"))
    monkeypatch.setenv("I485_INBOX", str(root / "inbox"))
    monkeypatch.setenv("I485_SETTINGS", str(root / "settings.json"))
    (root / "src-clients" / "case-sij" / "source").mkdir(parents=True)  # something for the run to choose

    def runner(name, source, out):
        return {"status": "done", "client": name, "counts": {"blocking": 0, "review": 0}, "seconds": 0.1, "errors": {}}

    overnight.run(root / "src-clients", info["clients"], root, runner=runner, log=lambda *_: None)
    ana = info["clients"] / "case-sij"
    for folder, name in (("translations", "translation-0123456789abcdef.pdf"), ("declarations", "declaration-i485.pdf")):
        (ana / folder).mkdir(exist_ok=True)
        (ana / folder / name).write_bytes(b"%PDF-1.4 made up")
    done = export_firm.everything(export_firm.default_where(info["clients"], info["portal"], info["users"]), who="Sam Attorney", role="attorney")
    uncovered = []
    for path in sorted(p for p in root.rglob("*") if p.is_file()):
        rel = path.relative_to(root).as_posix()
        if rel.startswith("src-clients/") or rel.startswith("exports/") or rel in ("users.json", "users_access.jsonl"):  # the world names its accounts files for itself
            continue
        if rel.startswith("clients/") and rel.count("/") >= 2:
            area, inner = "case", rel.split("/", 2)[2]
        elif rel.startswith("portal/clients/") and rel.count("/") >= 3:
            area, inner = "portal", rel.split("/", 3)[3]
        else:
            area, inner = "firm", rel
        if records.coverage(area, inner) is None:
            uncovered.append(rel)
    assert not uncovered, f"files the dictionary does not cover: {uncovered[:20]}"
    members = set(read_zip(Path(done["path"])))
    assert "firm/batch_state.json" in members and "cases/case-sij/translations/translation-0123456789abcdef.pdf" in members and "cases/case-sij/declarations/declaration-i485.pdf" in members


def test_two_exports_at_the_same_moment_do_not_wreck_each_other_and_the_zip_is_owner_only(firm, monkeypatch):
    import os
    import stat
    import threading

    paths, errors = [], []
    gate = threading.Barrier(3)
    real = export_firm.write_zip

    def slow(*a, **k):  # both exports are past choosing their names before either finishes writing
        gate.wait(timeout=20)
        return real(*a, **k)

    monkeypatch.setattr(export_firm, "write_zip", slow)

    def run():
        try:
            paths.append(Path(export_firm.everything(where(firm), who="Sam Attorney")["path"]))
        except Exception as exc:  # noqa: BLE001
            errors.append(repr(exc))

    threads = [threading.Thread(target=run) for _ in range(2)]
    [t.start() for t in threads]
    gate.wait(timeout=20)
    [t.join() for t in threads]
    assert not errors and len(set(paths)) == 2, (errors, paths)
    for p in paths:
        assert read_zip(p) and json.loads(read_zip(p)["manifest.json"])["file_count"] > 10, "both are whole"
        if os.name == "posix":
            assert stat.S_IMODE(p.stat().st_mode) == 0o600, "a copy of every case is the firm's alone"
    assert not list(paths[0].parent.glob("*.part")) and any(p.name.endswith("-2.zip") for p in paths)
    # an export that fails leaves no empty file under the name it claimed
    monkeypatch.setattr(export_firm, "write_zip", lambda *a, **k: (_ for _ in ()).throw(OSError("disk full")))
    before = sorted(p.name for p in paths[0].parent.glob("*.zip"))
    with pytest.raises(OSError):
        export_firm.everything(where(firm), who="Sam Attorney")
    assert sorted(p.name for p in paths[0].parent.glob("*.zip")) == before


def test_the_command_line_runs_the_same_code(firm, capsys, tmp_path):
    out = tmp_path / "elsewhere"
    code = export_firm.main(["--everything", "--data", str(firm["clients"]), "--portal", str(firm["portal"]), "--users", str(firm["users"]), "--out", str(out), "--by", "Pat Typed"])
    printed = capsys.readouterr().out
    assert code == 0 and "sha256:" in printed and (out).is_dir() and len(list(out.glob("i485-firm-data-*.zip"))) == 1
    rows = [r for r in events.rows(events.base_path(firm["data"])) if r["kind"] == "export"]
    assert [(r["who"], r["via"]) for r in rows] == [("Pat Typed", "tool")] * 2
    log = firm["users"].with_name("review_users_access.jsonl").read_text(encoding="utf-8")
    assert '"event": "data_exported"' in log and '"by": "Pat Typed"' in log
    assert export_firm.main(["--everything", "--data", str(tmp_path / "none"), "--portal", str(tmp_path / "none2"), "--users", str(tmp_path / "u.json"), "--out", str(out)]) == 2


def test_the_old_exports_are_untouched(firm, tmp_path):
    """--all and --client still make what docs/security/exit_procedure.md described: the new mode is beside them."""
    code = export_firm.main(["--all", "--data", str(firm["clients"]), "--portal", str(firm["portal"]), "--users", str(firm["users"]), "--out", str(tmp_path / "old")])
    assert code == 0 and len(list((tmp_path / "old").glob("i485-export-all-*.zip"))) == 1
