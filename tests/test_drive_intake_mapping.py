"""Trusted mapping acceptance; fictional provider orchestration only."""
from contextlib import contextmanager
import copy
import json

import pytest
import jobs
from connectors.drive_intake import IntakeError, MAX_MAPPED_CASES, digest
from test_drive_intake import cohort, preview, job_for  # noqa: F401 -- pytest fixture registration and helper reexports


@pytest.mark.parametrize("bad", ["missing", "none", "list", "empty", "version", "boolean-version", "revision-zero",
    "boolean-revision", "extra-field", "empty-bindings", "list-bindings", "bad-remote", "bad-case", "duplicate-case", "over-limit"],
    ids=["missing", "none", "list", "empty", "version", "boolean-version", "revision-zero", "boolean-revision",
         "extra-field", "empty-bindings", "list-bindings", "bad-remote", "bad-case", "duplicate-case", "over-limit"])
def test_invalid_operator_mapping_refuses_before_provider_or_jobs(cohort, bad):  # noqa: F811 -- pytest fixture injection
    settings = cohort["settings"]
    record = copy.deepcopy(settings["intake_mapping"])
    if bad == "missing":
        del settings["intake_mapping"]
    elif bad in {"none", "list", "empty"}:
        settings["intake_mapping"] = {"none": None, "list": [], "empty": {}}[bad]
    else:
        if bad == "version": record["version"] = 2
        elif bad == "boolean-version": record["version"] = True
        elif bad == "revision-zero": record["revision"] = 0
        elif bad == "boolean-revision": record["revision"] = True
        elif bad == "extra-field": record["body_actor"] = "untrusted"
        elif bad == "empty-bindings": record["bindings"] = {}
        elif bad == "list-bindings": record["bindings"] = []
        elif bad == "bad-remote": record["bindings"] = {"../remote": "case-a"}
        elif bad == "bad-case": record["bindings"] = {"remote-a": "../case-a"}
        elif bad == "duplicate-case": record["bindings"]["remote-b"] = "case-a"
        elif bad == "over-limit": record["bindings"] = {"remote-" + str(i): "case-" + str(i) for i in range(MAX_MAPPED_CASES + 1)}
        settings["intake_mapping"] = record
    with pytest.raises(IntakeError, match="mapping"):
        preview(cohort)
    assert cohort["factories"] == [] and cohort["source"].client_calls == 0
    assert not list(cohort["scope"].queue.glob("*-drive_intake-*.json"))
    assert not (cohort["scope"].queue / "drive-receipts").exists()


@pytest.mark.parametrize("mapping", [{"remote-a": "case-b"}, {"not-configured": "case-a"}], ids=["forged-local-case", "unconfigured-remote"])
def test_body_mapping_cannot_authorize_an_existing_accessible_case(cohort, mapping):  # noqa: F811 -- pytest fixture injection
    with pytest.raises(IntakeError, match="not configured"):
        preview(cohort, list(mapping), mapping)
    assert cohort["factories"] == [] and cohort["source"].downloads == []


def test_only_opaque_binding_and_selected_pairs_leave_config_reader(cohort):  # noqa: F811 -- pytest fixture injection
    record = cohort["settings"]["intake_mapping"]
    record["bindings"]["hidden-remote"] = "hidden-local-case"
    cohort["access"]["case-b"] = False
    shown = preview(cohort)
    binding = shown["snapshot"]["mapping_binding"]
    assert binding == {"version": 1, "revision": 1, "digest": digest(record)}
    assert set(cohort["factories"][0]) == {"root_folder_id", "documents_subfolder", "results_folder_name"}
    job = job_for(cohort, shown)
    for row in (shown, job):
        serialized = json.dumps(row)
        assert all(value not in serialized for value in ("hidden-remote", "hidden-local-case", "case-b", "remote-b", "Fictional B"))
        assert "intake_mapping" not in serialized
        assert row.get("snapshot", job["args"]["preview"]["snapshot"])["mapping"] == {"remote-a": "case-a"}
    assert cohort["helper"].execute(job, lambda *_: {"processed": True})["processing"]["state"] == "completed"
    assert cohort["source"].downloads == [("remote-a", "doc-remote-a")]


@pytest.mark.parametrize("change", ["revision", "selection", "unrelated"], ids=["revision-changed", "selection-revoked", "whole-map-changed"])
def test_preview_mapping_change_refuses_enqueue_without_new_job(cohort, change):  # noqa: F811 -- pytest fixture injection
    shown = preview(cohort)
    record = cohort["settings"]["intake_mapping"]
    if change == "revision": record["revision"] += 1
    elif change == "selection": del record["bindings"]["remote-a"]
    else: record["bindings"]["hidden-remote"] = "hidden-local-case"
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].enqueue(cohort["user"]["email"], shown, "b" * 32, lambda *_: pytest.fail("No worker wake authorized"))
    assert not list(cohort["scope"].queue.glob("*-drive_intake-*.json"))
    assert not (cohort["scope"].queue / "drive-receipts").exists()


@pytest.mark.parametrize("completed", [False, True], ids=["queued", "previously-completed"])
def test_revoked_mapping_refuses_worker_even_with_saved_completion(cohort, completed):  # noqa: F811 -- pytest fixture injection
    job = job_for(cohort)
    if completed:
        cohort["helper"].execute(job, lambda *_: {"processed": True})
    count = len(cohort["factories"])
    downloads = list(cohort["source"].downloads)
    del cohort["settings"]["intake_mapping"]["bindings"]["remote-a"]
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].execute(job, lambda *_: pytest.fail("Revoked mapping cannot process or return completion"))
    assert len(cohort["factories"]) == count and cohort["source"].downloads == downloads


def test_mapping_changed_while_waiting_for_case_lock_is_rechecked(cohort, monkeypatch):  # noqa: F811 -- pytest fixture injection
    job = job_for(cohort)
    count = len(cohort["factories"])
    original = jobs.case_lock
    @contextmanager
    def after_wait(*args, **kwargs):
        with original(*args, **kwargs):
            cohort["settings"]["intake_mapping"]["revision"] += 1
            yield
    monkeypatch.setattr(jobs, "case_lock", after_wait)
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].execute(job, lambda *_: pytest.fail("Stale mapping cannot process"))
    assert len(cohort["factories"]) == count and cohort["source"].downloads == []


def test_mapping_revoked_after_listing_refuses_before_download(cohort):  # noqa: F811 -- pytest fixture injection
    job = job_for(cohort)
    def progress(step, *_):
        if step == 2:
            del cohort["settings"]["intake_mapping"]["bindings"]["remote-a"]
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].execute(job, lambda *_: pytest.fail("Revoked mapping cannot process"), progress)
    assert cohort["source"].downloads == []
    assert not list((cohort["scope"].documents / "case-a/source").glob("*.pdf"))


def test_legacy_preview_without_mapping_binding_requires_new_preview(cohort):  # noqa: F811 -- pytest fixture injection
    shown = preview(cohort)
    del shown["snapshot"]["mapping_binding"]
    shown["digest"] = digest(shown["snapshot"])
    count = len(cohort["factories"])
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].enqueue(cohort["user"]["email"], shown, "c" * 32, lambda *_: pytest.fail("No wake authorized"))
    assert len(cohort["factories"]) == count
    assert not list(cohort["scope"].queue.glob("*-drive_intake-*.json"))


@pytest.mark.parametrize("malformed", ["boolean-version", "boolean-revision", "extra-field", "digest-shape"],
                         ids=["boolean-version", "boolean-revision", "extra-field", "digest-shape"])
def test_supplied_binding_shape_is_strict_even_with_recomputed_preview_digest(cohort, malformed):  # noqa: F811 -- pytest fixture injection
    shown = preview(cohort)
    binding = shown["snapshot"]["mapping_binding"]
    if malformed == "boolean-version": binding["version"] = True
    elif malformed == "boolean-revision": binding["revision"] = True
    elif malformed == "extra-field": binding["unexpected"] = "field"
    else: binding["digest"] = "not-a-digest"
    shown["digest"] = digest(shown["snapshot"])
    count = len(cohort["factories"])
    with pytest.raises(IntakeError, match="mapping changed"):
        cohort["helper"].enqueue(cohort["user"]["email"], shown, "d" * 32, lambda *_: pytest.fail("Malformed binding must not wake"))
    assert len(cohort["factories"]) == count
    assert not list(cohort["scope"].queue.glob("*-drive_intake-*.json"))
