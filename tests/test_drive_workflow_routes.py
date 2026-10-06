"""Actual protected configuration HTTP; fictional roots, no provider calls."""
import copy
import json
from pathlib import Path

import pytest
import events
import jobs
import second_factor
from connectors.drive_settings import paths, read_record, purge_settings
from drive_workflow_fixtures import source_firm, world, drive_world, configured_world, app, server, call, sign_in, ATTORNEY, PASSWORD  # noqa: F401 -- pytest fixture registration and helper reexports


def inputs(world, **changes):  # noqa: F811 -- pytest fixture injection
    return {"revision": 0, "attempt": "a" * 32,
            "provider": {"root_folder_id": "mocked-configured-root", "documents_subfolder": None},
            "bindings": {"remote-fictional": world["client"]}, **changes}


def posted(server, cookie, world, **changes):  # noqa: F811 -- pytest fixture injection
    status, raw = call(server + "/api/drive-settings", cookie, inputs(world, **changes))
    return status, json.loads(raw)


def record_without_audit(value):
    result = copy.deepcopy(value)
    result["operation"].pop("audit")
    return result


def test_settings_read_is_protected_readonly_and_no_provider(drive_world, app, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    from connectors import drive_intake
    monkeypatch.setattr(drive_intake, "installed", lambda *_a, **_k: pytest.fail("Config GET must not create a provider"))
    assert call(server + "/api/drive-settings", "")[0] == 401
    paralegal = sign_in(server, "jane@firm.example")
    assert call(server + "/api/drive-settings", paralegal)[0] == 403
    cookie = sign_in(server, ATTORNEY)
    status, raw = call(server + "/api/drive-settings", cookie)
    result = json.loads(raw)
    assert status == 200 and result["revision"] == 0 and result["intake_mapping"]["bindings"] == {}
    assert not paths(drive_world["scope"])[0].exists() and not list(drive_world["scope"].queue.glob("*.json"))


def test_protected_save_identity_retry_and_revision_conflict(configured_world, server):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    status, first = posted(server, cookie, w, reviewer="Forged name", role="paralegal", actor="forged@example.com", root="/foreign")
    assert status == 200 and first["revision"] == 1 and not first["audit_pending"]
    assert first["changed"]["actor"] == ATTORNEY and first["changed"]["name"] == "Pat Drive Attorney"
    assert posted(server, cookie, w)[1] == first
    assert posted(server, cookie, w, attempt="b" * 32)[0] == 409
    assert read_record(w["scope"])["revision"] == 1 and not list(w["scope"].queue.glob("*.json"))


def test_full_view_preserves_current_attorney_visibility_and_missing_case_refusal(configured_world, server):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    assert posted(server, cookie, w)[0] == 200
    second = "other-attorney@fictional.example"
    w["accounts"].change_password(second, w["accounts"].add(second, "Other Attorney", "attorney"), PASSWORD)
    second_factor.set_up(w["accounts"], second, PASSWORD)
    other = sign_in(server, second)
    status, raw = call(server + "/api/drive-settings", other)
    assert status == 200 and json.loads(raw)["intake_mapping"]["bindings"] == {"remote-fictional": w["client"]}
    assert call(server + "/api/drive-settings-audit", other, {"revision": 1})[0] == 200
    # Existing application policy permits current attorneys, whether named or
    # not. An actually missing mapped case still makes the full view unavailable.
    folder = w["scope"].cases / w["client"]
    absent = folder.with_name("temporarily-absent-fictional-case")
    folder.rename(absent)
    try:
        for url, body in [("/api/drive-settings", None), ("/api/drive-settings-audit", {"revision": 1})]:
            status, raw = call(server + url, other, body)
            assert status == 403
            assert w["client"] not in raw and "remote-fictional" not in raw
    finally:
        absent.rename(folder)


def test_pending_refresh_explicit_audit_retry_uses_saved_author(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    status, pending = posted(server, cookie, w)
    assert status == 200 and pending["audit_pending"]
    saved = read_record(w["scope"])
    assert "operation" not in pending
    status, raw = call(server + "/api/drive-settings", cookie)
    assert status == 200 and json.loads(raw)["audit_pending"]
    assert posted(server, cookie, w, revision=1, attempt="b" * 32)[0] == 409
    status, raw = call(server + "/api/drive-settings-audit", cookie, {"revision": 1})
    assert status == 200 and json.loads(raw)["audit_pending"]  # no false success
    monkeypatch.setattr(events, "record", original)
    status, raw = call(server + "/api/drive-settings-audit", cookie, {"revision": 1, "actor": "forged"})
    assert status == 200 and not json.loads(raw)["audit_pending"]
    assert record_without_audit(read_record(w["scope"])) == record_without_audit(saved)
    assert call(server + "/api/drive-settings-audit", cookie, {"revision": 2})[0] == 409


def test_different_attorney_can_repair_saved_system_cleanup_operation(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    assert posted(server, cookie, w)[0] == 200
    original = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    assert purge_settings(w["scope"].data, {w["client"]}) == (1, 1)
    saved = read_record(w["scope"])
    assert saved["changed"]["actor"] == "system:purge" and saved["intake_mapping"]["bindings"] == {}
    second = "replacement-attorney@fictional.example"
    w["accounts"].change_password(second, w["accounts"].add(second, "Replacement Attorney", "attorney"), PASSWORD)
    second_factor.set_up(w["accounts"], second, PASSWORD)
    other = sign_in(server, second)
    monkeypatch.setattr(events, "record", original)
    status, raw = call(server + "/api/drive-settings-audit", other, {"revision": 2})
    assert status == 200 and not json.loads(raw)["audit_pending"]
    assert record_without_audit(read_record(w["scope"])) == record_without_audit(saved)


def test_http_retry_append_before_marker_loss_reconciles_without_rewrite(configured_world, server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    original_record = events.record
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    assert posted(server, cookie, w)[1]["audit_pending"]
    saved = read_record(w["scope"])
    monkeypatch.setattr(events, "record", original_record)
    original_write = jobs._write
    def interrupted(path, value):
        if Path(path) == paths(w["scope"])[0] and value["operation"]["audit"]["state"] == "recorded":
            raise OSError("synthetic HTTP marker response loss")
        return original_write(path, value)
    monkeypatch.setattr(jobs, "_write", interrupted)
    assert call(server + "/api/drive-settings-audit", cookie, {"revision": 1})[0] == 500
    assert read_record(w["scope"])["operation"]["audit"]["state"] == "pending"
    monkeypatch.setattr(jobs, "_write", original_write)
    assert not json.loads(call(server + "/api/drive-settings-audit", cookie, {"revision": 1})[1])["audit_pending"]
    rows = [row for row in events.rows(events.base_path(w["scope"].data)) if row["kind"] == "settings"]
    assert len(rows) == 1 and record_without_audit(read_record(w["scope"])) == record_without_audit(saved)


@pytest.mark.parametrize("root", ["accounts", "documents", "queue"], ids=["foreign-accounts", "foreign-documents", "foreign-queue"])
def test_preflight_foreign_roots_refuse_without_configuration_effect(drive_world, app, server, monkeypatch, root):  # noqa: F811 -- pytest fixture injection
    w = drive_world
    cookie = sign_in(server, ATTORNEY)
    if root == "accounts":
        # Same actual authenticated store, wrong canonical configured spelling.
        foreign = w["scope"].data / "foreign-users.json"
        foreign.write_bytes(w["accounts"].path.read_bytes())
        monkeypatch.setattr(w["accounts"], "path", foreign)
    elif root == "documents": monkeypatch.setenv("I485_CLIENTS_ROOT", str(w["scope"].root / "foreign-documents"))
    else: monkeypatch.setattr(app, "jobs_root", w["scope"].data / "foreign-jobs")
    status, raw = call(server + "/api/drive-settings", cookie)
    assert status == 400, raw
    assert not paths(w["scope"])[0].exists() and not list(w["scope"].queue.glob("*.json"))


@pytest.mark.parametrize("change", ["role", "inactive"], ids=["role-revoked", "account-inactive"])
def test_http_current_account_revocation_denies_audit_retry(configured_world, server, monkeypatch, change):  # noqa: F811 -- pytest fixture injection
    w = configured_world
    cookie = sign_in(server, ATTORNEY)
    monkeypatch.setattr(events, "record", lambda *_a, **_k: None)
    assert posted(server, cookie, w)[1]["audit_pending"]
    before = paths(w["scope"])[0].read_bytes()
    w["accounts"].update(ATTORNEY, **({"role": "paralegal"} if change == "role" else {"active": False}))
    assert call(server + "/api/drive-settings-audit", cookie, {"revision": 1})[0] == 401
    paralegal = sign_in(server, "jane@firm.example")
    assert call(server + "/api/drive-settings-audit", paralegal, {"revision": 1})[0] == 403
    assert paths(w["scope"])[0].read_bytes() == before
