"""Fictional installation configuration; no credentials/provider/network."""
import copy
import json
from pathlib import Path
import threading

import pytest
import events
import jobs
from connectors.drive_intake import IntakeError, installed
from connectors.drive_settings import Settings, Conflict, paths, read, read_record, purge_settings
from test_drive_intake import cohort, preview, job_for  # noqa: F401 -- pytest fixture registration and helper reexports


@pytest.fixture
def configuration(cohort, monkeypatch):  # noqa: F811 -- pytest fixture injection
    scope = cohort["scope"]
    for variable, value in {"I485_RULES_APPROVED": scope.data / "rules_approved.json",
                            "I485_MAINTENANCE_LOG": scope.data / "maintenance_log.json",
                            "I485_SETTINGS": scope.data / "settings.json", "I485_CASES": scope.cases,
                            "I485_PROSPECTS": scope.data / "prospects", "PORTAL_DATA": scope.portal,
                            "I485_EVENTS": scope.data / "events.jsonl"}.items():
        monkeypatch.setenv(variable, str(value))
    shipped = scope.root / "schemas/registers/connectors.json"
    shipped.parent.mkdir(parents=True)
    shipped.write_text(json.dumps({"google_drive": {**cohort["settings"], "results_folder_name": "Disabled results"},
                                  "active": {"documents": "local", "results": "none"}}), encoding="utf-8")
    cohort["user"]["role"] = "attorney"
    controller = Settings(scope, lambda email: cohort["user"] if email == cohort["user"]["email"] else None,
                          lambda user, folder: cohort["access"].get(folder.name, False))
    cohort["helper"].settings_reader = lambda: read(scope)
    return {**cohort, "controller": controller, "shipped": shipped}


def save(c, **changes):
    return c["controller"].save(c["user"]["email"], **{
        "expected_revision": 0, "operation_id": "a" * 32,
        "supplied_provider": {"root_folder_id": "configured-root", "documents_subfolder": None},
        "supplied_bindings": {"remote-a": "case-a"}, **changes})


def own_events(c):
    return [row for row in events.rows(events.base_path(c["scope"].data)) if row["kind"] == "settings"]


def test_missing_data_override_disables_even_historical_shipped_mapping(configuration):
    c = configuration
    assert read(c["scope"])["intake_mapping"] is None
    assert installed(c["scope"]).settings_reader()["intake_mapping"] is None
    with pytest.raises(IntakeError, match="mapping"):
        preview(c)
    assert c["factories"] == [] and not paths(c["scope"])[0].exists()


def test_data_override_is_shared_by_installed_worker_and_preserves_shipped_defaults(configuration):
    c = configuration
    original = c["shipped"].read_bytes()
    result = save(c)
    assert result["saved"] and result["revision"] == 1 and not result["audit_pending"]
    effective = installed(c["scope"]).settings_reader()
    assert effective == read(c["scope"])
    assert effective["intake_mapping"]["bindings"] == {"remote-a": "case-a"}
    assert effective["results_folder_name"] is None
    assert c["shipped"].read_bytes() == original and len(own_events(c)) == 1
    shown = preview(c)
    assert shown["snapshot"]["mapping_binding"]["revision"] == 1
    assert "changed" not in shown["snapshot"]["settings"] and "operation" not in shown["snapshot"]["settings"]


@pytest.mark.parametrize("state", ["paralegal", "inactive", "missing"], ids=["paralegal", "inactive", "missing"])
def test_writer_requires_current_active_attorney(configuration, state):
    c = configuration
    if state == "paralegal": c["user"]["role"] = "paralegal"
    elif state == "inactive": c["user"]["active"] = False
    else: c["controller"].actor_reader = lambda _: None
    with pytest.raises(PermissionError, match="active attorney"):
        save(c)
    assert not paths(c["scope"])[0].exists() and own_events(c) == []


def test_acl_revocation_refuses_configuration_without_settings_effect(configuration):
    c = configuration
    c["access"]["case-a"] = False
    with pytest.raises(PermissionError, match="unavailable"):
        save(c)
    assert not paths(c["scope"])[0].exists() and own_events(c) == []


def test_same_attempt_replay_stale_revision_and_mismatched_payload(configuration):
    c = configuration
    first = save(c)
    assert save(c) == first and len(own_events(c)) == 1
    with pytest.raises(Conflict, match="same Drive settings attempt"):
        save(c, supplied_bindings={"remote-b": "case-b"})
    second = save(c, expected_revision=1, operation_id="b" * 32, supplied_bindings={"remote-b": "case-b"})
    assert second["revision"] == 2
    with pytest.raises(Conflict, match="reload"):
        save(c)  # old attempt keeps its original revision, never makes revision 3
    assert read_record(c["scope"])["revision"] == 2 and len(own_events(c)) == 2


def test_current_authority_rechecked_after_case_lock(configuration, monkeypatch):
    from contextlib import contextmanager
    c = configuration
    original = jobs.case_lock
    @contextmanager
    def revoked(*args, **kwargs):
        with original(*args, **kwargs):
            c["user"]["active"] = False
            yield
    monkeypatch.setattr(jobs, "case_lock", revoked)
    with pytest.raises(PermissionError, match="active attorney"):
        save(c)
    assert not paths(c["scope"])[0].exists() and own_events(c) == []


def test_removed_closed_binding_can_be_cleaned_but_cannot_be_retained(configuration):
    c = configuration
    save(c)
    (c["scope"].cases / "case-a/engagement.json").write_text(json.dumps({"end": {"state": "closed"}}))
    with pytest.raises(ValueError):
        save(c, expected_revision=1, operation_id="b" * 32)
    cleaned = save(c, expected_revision=1, operation_id="c" * 32, supplied_bindings={})
    assert cleaned["revision"] == 2 and cleaned["intake_mapping"]["bindings"] == {}
    assert cleaned["provider"] == {"root_folder_id": "configured-root", "documents_subfolder": None}
    with pytest.raises(IntakeError, match="mapping"):
        preview(c)


def test_audit_failure_visible_same_attempt_repairs_without_revision_duplication(configuration, monkeypatch):
    c = configuration
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    pending = save(c)
    assert pending["saved"] and pending["audit_pending"] and pending["revision"] == 1
    with pytest.raises(Conflict, match="pending audit"):
        save(c, expected_revision=1, operation_id="b" * 32)
    monkeypatch.setattr(events, "record", original)
    repaired = save(c)
    assert not repaired["audit_pending"] and repaired["revision"] == 1 and len(own_events(c)) == 1


def test_append_before_marker_interruption_reconciles_one_r2_row(configuration, monkeypatch):
    c = configuration
    original = jobs._write
    def interrupted(path, value):
        if Path(path) == paths(c["scope"])[0] and value["operation"]["audit"]["state"] == "recorded":
            raise OSError("synthetic interruption after R2 append before settings marker")
        return original(path, value)
    monkeypatch.setattr(jobs, "_write", interrupted)
    with pytest.raises(OSError, match="after R2 append"):
        save(c)
    assert len(own_events(c)) == 1 and read_record(c["scope"])["operation"]["audit"]["state"] == "pending"
    monkeypatch.setattr(jobs, "_write", original)
    assert not save(c)["audit_pending"] and len(own_events(c)) == 1


@pytest.mark.parametrize("bad", ["json", "list", "empty", "boolean-revision", "payload", "mapping-revision", "audit"],
                         ids=["json", "list", "empty", "boolean-revision", "payload", "mapping-revision", "audit"])
def test_damaged_override_fails_closed_without_shipped_mapping_fallback(configuration, bad):
    c = configuration
    save(c)
    path = paths(c["scope"])[0]
    value = json.loads(path.read_text())
    if bad == "json": path.write_text("{interrupted")
    else:
        if bad == "list": value = []
        elif bad == "empty": value = {}
        elif bad == "boolean-revision": value["revision"] = True
        elif bad == "payload": value["operation"]["payload_sha256"] = "0" * 64
        elif bad == "mapping-revision": value["intake_mapping"]["revision"] += 1
        else: value["operation"]["audit"] = []
        path.write_text(json.dumps(value))
    before = path.read_bytes()
    with pytest.raises(IntakeError): read(c["scope"])
    with pytest.raises(IntakeError): installed(c["scope"]).settings_reader()
    with pytest.raises(IntakeError): save(c)
    assert path.read_bytes() == before and len(own_events(c)) == 1


def test_current_data_revision_revokes_enqueued_mapping_before_provider(configuration):
    c = configuration
    save(c)
    job = job_for(c)
    count = len(c["factories"])
    save(c, expected_revision=1, operation_id="b" * 32, supplied_bindings={"remote-b": "case-b"})
    with pytest.raises(IntakeError, match="mapping changed"):
        c["helper"].execute(job, lambda *_: pytest.fail("Revoked settings cannot process"))
    assert len(c["factories"]) == count and c["source"].downloads == []


def test_unreadable_override_does_not_fall_back(configuration, monkeypatch):
    c = configuration
    save(c)
    path = paths(c["scope"])[0]
    original = Path.read_text
    def denied(self, *args, **kwargs):
        if self == path:
            raise PermissionError("synthetic unreadable own config")
        return original(self, *args, **kwargs)
    monkeypatch.setattr(Path, "read_text", denied)
    with pytest.raises(IntakeError, match="unavailable"):
        read(c["scope"])
    assert len(own_events(c)) == 1


def test_concurrent_writers_with_same_revision_commit_one_change(configuration, monkeypatch):
    from connectors import drive_settings
    c = configuration
    barrier, local = threading.Barrier(2), threading.local()
    original = drive_settings.read_record
    def snapshot(scope):
        record = original(scope)
        if not getattr(local, "observed", False):
            local.observed = True
            barrier.wait(5)
        return record
    monkeypatch.setattr(drive_settings, "read_record", snapshot)
    results, errors = [], []
    def writer(letter):
        try:
            results.append(save(c, operation_id=letter * 32, supplied_bindings={"remote-" + letter: "case-" + letter}))
        except Exception as error:
            errors.append(error)
    threads = [threading.Thread(target=writer, args=(letter,)) for letter in ("a", "b")]
    for thread in threads: thread.start()
    for thread in threads:
        thread.join(15)
        assert not thread.is_alive()
    assert len(results) == len(errors) == 1 and isinstance(errors[0], Conflict), errors
    assert results[0]["revision"] == 1 and original(c["scope"])["revision"] == 1
    assert len(own_events(c)) == 1


def test_full_operator_view_refuses_hidden_or_missing_mapping_without_disclosure(configuration):
    c = configuration
    empty = c["controller"].view(c["user"]["email"])
    assert empty["revision"] == 0 and empty["intake_mapping"]["bindings"] == {}
    save(c, supplied_bindings={"remote-a": "case-a", "remote-b": "case-b"})
    before = paths(c["scope"])[0].read_bytes()
    c["access"]["case-b"] = False
    with pytest.raises(PermissionError, match="configuration is unavailable") as error:
        c["controller"].view(c["user"]["email"])
    assert "case-b" not in str(error.value) and "remote-b" not in str(error.value)
    c["access"]["case-b"] = True
    missing = c["scope"].cases / "case-b"
    moved = c["scope"].cases / "fictional-case-temporarily-unavailable"
    missing.rename(moved)
    try:
        with pytest.raises(PermissionError, match="configuration is unavailable"):
            c["controller"].view(c["user"]["email"])
    finally:
        moved.rename(missing)
    (c["scope"].cases / "case-b/engagement.json").write_text(json.dumps({"end": {"state": "closed"}}))
    assert c["controller"].view(c["user"]["email"])["revision"] == 1
    c["user"]["active"] = False
    with pytest.raises(PermissionError): c["controller"].view(c["user"]["email"])
    assert before == paths(c["scope"])[0].read_bytes() and len(own_events(c)) == 1


def test_q1_removes_only_target_binding_and_complete_owned_crash_write(configuration):
    c = configuration
    save(c, supplied_bindings={"remote-a": "case-a", "remote-b": "case-b"})
    path = paths(c["scope"])[0]
    target = path.with_name("settings.json.123.456.tmp")
    target.write_bytes(path.read_bytes())
    survivor = path.with_name("settings.json.123.457.tmp")
    value = read_record(c["scope"])
    value["intake_mapping"]["bindings"] = {"remote-b": "case-b"}
    from connectors.drive_settings import _payload
    value["operation"]["payload_sha256"] = _payload(value["changed"]["actor"], 0, value["provider"], value["intake_mapping"]["bindings"])
    survivor.write_text(json.dumps(value))
    survivor_bytes = survivor.read_bytes()
    removed, unresolved = purge_settings(c["scope"].data, {"case-a", "prospect:fictional", "Former Client"})
    assert (removed, unresolved) == (2, 0)
    current = read_record(c["scope"])
    assert current["revision"] == 2 and current["intake_mapping"]["bindings"] == {"remote-b": "case-b"}
    assert current["provider"] == value["provider"] and not target.exists() and survivor.read_bytes() == survivor_bytes
    assert current["changed"]["actor"] == "system:purge"
    assert own_events(c)[-1]["role"] == "system"
    assert purge_settings(c["scope"].data, {"case-a"}) == (0, 0)
    with pytest.raises(Conflict): save(c)


@pytest.mark.parametrize("damage", ["partial", "foreign-name", "bad-proof", "symlink"], ids=["partial", "foreign-name", "bad-proof", "symlink"])
def test_q1_preserves_damaged_or_unassignable_residue_and_reports_unresolved(configuration, damage):
    c = configuration
    save(c)
    path = paths(c["scope"])[0]
    temporary = path.with_name("settings.json.123.456.tmp" if damage != "foreign-name" else "settings.json.other.tmp")
    if damage == "symlink":
        other = c["scope"].root / "outside-settings.json"
        other.write_bytes(path.read_bytes())
        try: temporary.symlink_to(other)
        except OSError: pytest.skip("Local runtime cannot create a symlink")
    elif damage == "bad-proof":
        value = read_record(c["scope"])
        value["operation"]["payload_sha256"] = "0" * 64
        temporary.write_text(json.dumps(value))
    else: temporary.write_text("{partial")
    before = temporary.read_bytes()
    assert purge_settings(c["scope"].data, {"case-a"}) == (1, 1)
    assert temporary.read_bytes() == before
    assert read_record(c["scope"])["intake_mapping"]["bindings"] == {}


def test_q1_pending_audit_is_visible_and_retry_repairs_without_another_revision(configuration, monkeypatch):
    c = configuration
    save(c)
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    assert purge_settings(c["scope"].data, {"case-a"}) == (1, 1)
    assert read_record(c["scope"])["revision"] == 2
    monkeypatch.setattr(events, "record", original)
    assert purge_settings(c["scope"].data, {"case-a"}) == (0, 0)
    assert read_record(c["scope"])["revision"] == 2 and len(own_events(c)) == 2


def test_q1_corrupt_canonical_state_is_unresolved_and_untouched(configuration):
    c = configuration
    save(c)
    path = paths(c["scope"])[0]
    path.write_text("{partial")
    assert purge_settings(c["scope"].data, {"case-a"}) == (0, 1)
    assert path.read_text() == "{partial"


def test_actual_interrupted_atomic_write_is_attributed_and_removed(configuration, monkeypatch):
    import os
    c = configuration
    path = paths(c["scope"])[0]
    original = os.replace
    def interrupted(source, destination):
        if Path(destination) == path:
            raise OSError("synthetic interruption before config replace")
        return original(source, destination)
    monkeypatch.setattr(os, "replace", interrupted)
    with pytest.raises(OSError, match="before config replace"):
        save(c)
    assert not path.exists()
    residues = list(path.parent.glob("settings.json.*.tmp"))
    assert len(residues) == 1 and json.loads(residues[0].read_text())["intake_mapping"]["bindings"] == {"remote-a": "case-a"}
    monkeypatch.setattr(os, "replace", original)
    assert purge_settings(c["scope"].data, {"case-a"}) == (1, 0)
    assert not residues[0].exists() and not path.exists()


def test_configuration_catalog_export_backup_and_actual_q1_hook(configuration, monkeypatch):
    import records
    import backups
    import purge
    import export_firm
    c = configuration
    scope = c["scope"]
    for variable, value in {"I485_JOBS": scope.queue, "I485_CASES": scope.cases,
                            "PORTAL_DATA": scope.portal, "I485_CLIENTS_ROOT": scope.documents,
                            "I485_MAINTENANCE_LOG": scope.data / "maintenance_log.json",
                            "I485_PROSPECTS": scope.data / "prospects"}.items():
        monkeypatch.setenv(variable, str(value))
    save(c, supplied_bindings={"remote-a": "case-a", "remote-b": "case-b"})
    path = paths(scope)[0]
    temporary = path.with_name("settings.json.123.456.tmp")
    temporary.write_bytes(path.read_bytes())
    assert records.coverage("firm", "drive/settings.json") == "listed"
    assert records.coverage("firm", "drive/settings.lock") == "never"
    assert records.coverage("firm", temporary.relative_to(scope.data).as_posix()) == "never"
    assert export_firm.listed("drive/settings.json", records.patterns("firm"))
    assert not export_firm.listed(temporary.relative_to(scope.data).as_posix(), records.patterns("firm"))
    planned = {item.arcname for item in backups.plan([("data", scope.data)], [], [])}
    assert "data/drive/settings.json" in planned
    assert not any(name.startswith("data/drive/") and name.endswith((".lock", ".tmp")) for name in planned)
    who = purge.Identity("case-a", [], set(), set(), set())
    with events.acting("Fictional retention authorizer", "attorney", "staff"):
        outcome = purge.empty_stores(scope.cases, "case-a", scope.portal, who=who)
    row = next(item for item in outcome["stores"] if item["id"] == "drive_settings")
    assert row["removed"] == 2 and not any("Drive settings" in message for message in outcome["left"])
    retained = read_record(scope)
    assert retained["intake_mapping"]["bindings"] == {"remote-b": "case-b"} and retained["revision"] == 2
    assert retained["provider"]["root_folder_id"] == "configured-root"
    assert retained["changed"]["name"] == "Fictional retention authorizer"
    assert retained["changed"]["role"] == "attorney" and retained["changed"]["via"] == "staff"
    assert not temporary.exists()


@pytest.mark.parametrize("state", ["broken-reparse", "unreadable"], ids=["simulated-broken-reparse", "unreadable"])
def test_q1_absence_probe_does_not_hide_reparse_or_unreadable_settings(configuration, monkeypatch, state):
    """Simulate Windows broken-junction status; no real junction installation."""
    from types import SimpleNamespace
    import stat
    c = configuration
    save(c)
    path = paths(c["scope"])[0]
    before = path.read_bytes()
    folder = path.parent
    original_lstat, original_exists, original_link = Path.lstat, Path.exists, Path.is_symlink
    def probed(self, *args, **kwargs):
        if self == folder:
            if state == "unreadable": raise PermissionError("synthetic denied directory status")
            return SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
        return original_lstat(self, *args, **kwargs)
    monkeypatch.setattr(Path, "lstat", probed)
    monkeypatch.setattr(Path, "exists", lambda self: False if self == folder else original_exists(self))
    monkeypatch.setattr(Path, "is_symlink", lambda self: False if self == folder else original_link(self))
    if state == "broken-reparse":
        with pytest.raises(IntakeError, match="plain installation-owned paths"):
            purge_settings(c["scope"].data, {"case-a"})
    else:
        assert purge_settings(c["scope"].data, {"case-a"}) == (0, 1)
    assert path.read_bytes() == before


@pytest.mark.parametrize("difference", ["missing", "damaged", "provider", "mapping", "operation"],
                         ids=["missing-canonical", "damaged-canonical", "unique-provider", "unique-survivor-mapping", "unique-operation"])
def test_q1_preserves_unique_mixed_case_interrupted_intent(configuration, difference):
    from connectors.drive_settings import _payload
    c = configuration
    save(c, supplied_bindings={"remote-a": "case-a", "remote-b": "case-b"})
    path = paths(c["scope"])[0]
    value = read_record(c["scope"])
    if difference == "missing": path.unlink()
    elif difference == "damaged": path.write_text("{partial")
    elif difference == "provider": value["provider"]["root_folder_id"] = "another-configured-root"
    elif difference == "mapping": value["intake_mapping"]["bindings"] = {"remote-a": "case-a", "remote-c": "case-b"}
    else: value["operation"]["id"] = "b" * 32
    value["operation"]["payload_sha256"] = _payload(value["changed"]["actor"], 0, value["provider"], value["intake_mapping"]["bindings"])
    temporary = path.with_name("settings.json.123.456.tmp")
    temporary.write_text(json.dumps(value))
    before = temporary.read_bytes()
    removed, unresolved = purge_settings(c["scope"].data, {"case-a"})
    assert temporary.read_bytes() == before and unresolved >= 1
    assert removed == (0 if difference in {"missing", "damaged"} else 1)
    if difference not in {"missing", "damaged"}:
        assert read_record(c["scope"])["intake_mapping"]["bindings"] == {"remote-b": "case-b"}


def test_explicit_audit_retry_after_turnover_preserves_saved_author_and_operation(configuration, monkeypatch):
    c = configuration
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    save(c)
    saved = copy.deepcopy(read_record(c["scope"]))
    c["user"]["active"] = False
    replacement = {"email": "replacement@fictional.example", "name": "Replacement Attorney", "role": "attorney", "active": True}
    c["controller"].actor_reader = lambda email: replacement if email == replacement["email"] else None
    monkeypatch.setattr(events, "record", original)
    assert not c["controller"].retry_audit(replacement["email"], 1)["audit_pending"]
    repaired = read_record(c["scope"])
    repaired["operation"]["audit"] = saved["operation"]["audit"]
    assert repaired == saved
    assert len(own_events(c)) == 1 and own_events(c)[0]["who"] == saved["changed"]["name"]
    before = paths(c["scope"])[0].read_bytes()
    assert not c["controller"].retry_audit(replacement["email"], 1)["audit_pending"]
    assert paths(c["scope"])[0].read_bytes() == before and len(own_events(c)) == 1


def test_explicit_retry_repairs_system_cleanup_after_target_mapping_is_gone(configuration, monkeypatch):
    c = configuration
    save(c)
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    assert purge_settings(c["scope"].data, {"case-a"}) == (1, 1)
    saved = copy.deepcopy(read_record(c["scope"]))
    assert saved["changed"]["actor"] == "system:purge" and saved["intake_mapping"]["bindings"] == {}
    monkeypatch.setattr(events, "record", original)
    assert not c["controller"].retry_audit(c["user"]["email"], 2)["audit_pending"]
    repaired = read_record(c["scope"])
    repaired["operation"]["audit"] = saved["operation"]["audit"]
    assert repaired == saved and len(own_events(c)) == 2


def test_explicit_audit_retry_failed_append_stays_pending(configuration, monkeypatch):
    c = configuration
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    save(c)
    before = paths(c["scope"])[0].read_bytes()
    assert c["controller"].retry_audit(c["user"]["email"], 1)["audit_pending"]
    assert paths(c["scope"])[0].read_bytes() == before and own_events(c) == []


def test_explicit_retry_append_before_marker_reconciles_saved_event(configuration, monkeypatch):
    c = configuration
    record = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    save(c)
    monkeypatch.setattr(events, "record", record)
    write = jobs._write
    def interrupted(path, value):
        if Path(path) == paths(c["scope"])[0] and value["operation"]["audit"]["state"] == "recorded":
            raise OSError("synthetic retry marker loss")
        return write(path, value)
    monkeypatch.setattr(jobs, "_write", interrupted)
    with pytest.raises(OSError, match="marker loss"):
        c["controller"].retry_audit(c["user"]["email"], 1)
    assert len(own_events(c)) == 1 and read_record(c["scope"])["operation"]["audit"]["state"] == "pending"
    monkeypatch.setattr(jobs, "_write", write)
    assert not c["controller"].retry_audit(c["user"]["email"], 1)["audit_pending"]
    assert len(own_events(c)) == 1 and read_record(c["scope"])["revision"] == 1


@pytest.mark.parametrize("refusal", ["role", "inactive", "acl", "stale", "boolean"], ids=["role", "inactive", "acl", "stale", "boolean"])
def test_explicit_audit_retry_current_authority_and_revision_refusal(configuration, monkeypatch, refusal):
    c = configuration
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    save(c)
    before = paths(c["scope"])[0].read_bytes()
    revision = 1
    if refusal == "role": c["user"]["role"] = "paralegal"
    elif refusal == "inactive": c["user"]["active"] = False
    elif refusal == "acl": c["access"]["case-a"] = False
    elif refusal == "stale": revision = 2
    else: revision = True
    with pytest.raises((PermissionError, IntakeError)):
        c["controller"].retry_audit(c["user"]["email"], revision)
    assert paths(c["scope"])[0].read_bytes() == before and own_events(c) == []


def test_explicit_retry_rechecks_authority_after_case_lock(configuration, monkeypatch):
    from contextlib import contextmanager
    c = configuration
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    save(c)
    before = paths(c["scope"])[0].read_bytes()
    original = jobs.case_lock
    @contextmanager
    def revoked(*args, **kwargs):
        with original(*args, **kwargs):
            c["user"]["active"] = False
            yield
    monkeypatch.setattr(jobs, "case_lock", revoked)
    with pytest.raises(PermissionError): c["controller"].retry_audit(c["user"]["email"], 1)
    assert paths(c["scope"])[0].read_bytes() == before
