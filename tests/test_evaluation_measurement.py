"""Fictional independent references; no live corpus or reader services."""
from copy import deepcopy
import hashlib
import json
import os
from pathlib import Path
import subprocess
import sys
import types

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
from evaluation.corpus import digest, validate, safe_source, write_new
from evaluation.scoring import score
from evaluation.releases import freeze_policy, compare, decision
from evaluation import runner

ADJ = {"basis": "independent", "by": "fictional-adjudicator", "history": [{"reason": "Fictional independent source inspection"}]}
PENDING = {"version": 1, "state": "pending_ev3", "keys": ["applicant.i94_number"]}
ACCEPTED = {**PENDING, "state": "accepted", "inventory_source_digest": "a" * 64, "accepted_by": "fictional-engineering-owner", "acceptance_evidence": "Synthetic fixture only; not actual EV3 acceptance"}
THRESHOLDS = {"minimum_precision": 1, "minimum_coverage": 1, "maximum_wrong_person": 0, "maximum_critical_errors": 0, "minimum_scored_fills": 1}
TEXT = "Most Recent I-94\nAdmission I-94 Record Number: 11111111111\nClass of Admission: B2\nArrival/Issued Date: 01/02/2020\nLast/Surname: EXAMPLE\nFirst (Given) Name: ALPHA\n"


@pytest.fixture
def corpus(tmp_path):
    root = tmp_path / "fictional-corpus"; root.mkdir()
    (root / "i94.txt").write_text(TEXT, encoding="utf-8")
    doc = {"id": "d-one", "path": "i94.txt", "sha256": hashlib.sha256((root / "i94.txt").read_bytes()).hexdigest(), "language": "en", "quality": "clear", "type": "i94", "page_count": 1, "subject": "person-a", "subject_scope": "validated_single_subject", "subject_adjudication": deepcopy(ADJ)}
    fact = {"subject": "person-a", "key": "applicant.i94_number", "label": "supported", "value": "11111111111", "documents": ["d-one"], "adjudication": deepcopy(ADJ)}
    manifest = {"version": 1, "id": "fictional-evaluation", "synthetic_only": True, "cases": [{"id": "case-a", "family_group": "family-a", "partition": "evaluation", "subjects": ["person-a", "person-b", "parent-a"], "completion": "accepted", "documents": [doc], "expected": [fact]}]}
    validate(manifest, root)
    return root, manifest


def observations(manifest, state="filled", value="11111111111"):
    proposal = {"state": state}
    if state == "filled": proposal["value"] = value
    return {"version": 1, "corpus_digest": digest(manifest), "configuration_digest": digest({"adapter": "synthetic-declared"}), "facts": [{"case": "case-a", "subject": "person-a", "key": "applicant.i94_number", "proposal": proposal, "accepted": None, "evidence": [{"document": "d-one", "page": 0}]}], "outcomes": [{"case": "case-a", "state": "completed"}], "pdf_boxes": [], "labor": []}


def rescore(manifest, obs, inventory=PENDING):
    obs["corpus_digest"] = digest(manifest)
    return score(manifest, obs, inventory)


def test_fact_rates_review_and_pdf_are_separate(corpus):
    root, manifest = corpus
    obs = observations(manifest)
    manifest["cases"][0]["pdf_boxes"] = [{"id": name, "subject": "person-a", "key": "applicant.i94_number", "value": "11111111111", "adjudication": deepcopy(ADJ)} for name in ("box-a", "box-b")]
    obs["pdf_boxes"] = [{"case": "case-a", "id": "box-a", "value": "11111111111"}, {"case": "case-a", "id": "box-b", "value": "wrong"}]
    obs["facts"].append(deepcopy(obs["facts"][0]))
    validate(manifest, root)
    report = rescore(manifest, obs)
    assert report["extraction"]["proposal"]["precision"] == {"numerator": 1, "denominator": 1, "rate": 1}
    assert report["extraction"]["accepted"]["precision"]["rate"] is None
    assert report["extraction"]["accepted"]["counts"]["no_observation"] == 1
    assert report["pdf"]["counts"]["scored_boxes"] == 2
    assert report["pdf"]["counts"]["fact_groups"] == 1
    assert report["pdf"]["distinct_fact_group_error"]["rate"] == 1
    assert report["labor"]["total_minutes_per_accepted_case"]["rate"] is None


@pytest.mark.parametrize("label,value,count", [("supported", "wrong", "wrong"), ("unsupported", "anything", "unsupported"), ("unknown", "anything", "unscored_fills"), ("disputed", "anything", "unscored_fills")], ids=["wrong", "unsupported", "unknown", "disputed"])
def test_labels_remain_distinct(corpus, label, value, count):
    _, manifest = corpus; manifest["cases"][0]["expected"][0]["label"] = label
    report = rescore(manifest, observations(manifest, value=value))
    assert report["extraction"]["proposal"]["counts"][count] == 1
    if label in ("unknown", "disputed"):
        assert report["extraction"]["proposal"]["precision"]["denominator"] == 0


@pytest.mark.parametrize("state", ["held", "abstained", "not_attempted"])
def test_safe_hold_is_not_correct_fill(corpus, state):
    _, manifest = corpus; manifest["cases"][0]["expected"][0]["safe_hold"] = True
    report = rescore(manifest, observations(manifest, state))
    assert report["extraction"]["proposal"]["counts"]["correct"] == 0
    assert report["extraction"]["proposal"]["counts"]["justified_hold"] == int(state != "not_attempted")
    assert report["extraction"]["proposal"]["coverage"]["rate"] == 0


def test_missing_is_not_abstention_and_workflow_is_weak(corpus):
    _, manifest = corpus; obs = observations(manifest); obs["facts"] = []
    report = score(manifest, obs, PENDING)
    assert report["extraction"]["proposal"]["counts"]["missing_expected"] == 1
    assert report["extraction"]["proposal"]["counts"]["abstained"] == 0
    manifest["cases"][0]["expected"][0]["adjudication"]["basis"] = "workflow"
    report = rescore(manifest, observations(manifest))
    assert report["extraction"]["proposal"]["precision"]["rate"] is None
    assert report["extraction"]["proposal"]["counts"]["unscored_fills"] == 1


def test_same_value_wrong_person_and_multisubject_parent(corpus):
    root, manifest = corpus; doc = manifest["cases"][0]["documents"][0]
    doc["subject_scope"] = "multi_subject"
    doc["attributions"] = [{"subject": "person-b", "key": "applicant.i94_number", "page": 0, "raw_value": "11111111111", "adjudication": deepcopy(ADJ)}]
    obs = observations(manifest); obs["facts"][0]["evidence"][0]["raw_value"] = "11111111111"
    validate(manifest, root)
    report = rescore(manifest, obs)
    assert report["extraction"]["proposal"]["counts"]["wrong_person"] == 1
    assert report["extraction"]["proposal"]["counts"]["wrong"] == 1
    assert report["extraction"]["proposal"]["counts"]["critical_errors"] == 1
    # A child-owned birth record may legitimately establish a parent's fact.
    doc["subject"] = "person-a"; doc["type"] = "birth_certificate"
    doc["attributions"][0].update(subject="parent-a", key="applicant.parent1_name")
    manifest["cases"][0]["expected"][0].update(subject="parent-a", key="applicant.parent1_name")
    obs["facts"][0].update(subject="parent-a", key="applicant.parent1_name")
    report = rescore(manifest, obs)
    assert report["extraction"]["proposal"]["counts"]["correct"] == 1
    assert report["extraction"]["proposal"]["counts"]["wrong_person"] == 0
    doc["attributions"] = []
    report = rescore(manifest, obs)
    assert report["extraction"]["proposal"]["counts"]["attribution_unscored_fills"] == 1
    assert report["extraction"]["proposal"]["precision"]["rate"] is None


def test_page_bounds_verified_declared_and_audit_separate(corpus):
    root, manifest = corpus; obs = observations(manifest)
    report = score(manifest, obs, PENDING)
    metrics = report["extraction"]["proposal"]
    assert metrics["source_location_available"]["rate"] == 1
    assert metrics["source_link_correctness"]["rate"] is None
    obs["facts"][0]["evidence"][0]["audit"] = {"basis": "independent", "by": "fictional-inspector", "correct": False}
    assert score(manifest, obs, PENDING)["extraction"]["proposal"]["source_link_correctness"]["rate"] == 0
    manifest["cases"][0]["documents"][0].pop("page_count")
    metrics = rescore(manifest, obs)["extraction"]["proposal"]
    assert metrics["source_location_available"]["rate"] == 0
    assert metrics["source_location_declared"]["rate"] == 1
    manifest["cases"][0]["documents"][0]["page_count"] = 2
    with pytest.raises(ValueError, match="page count differs"): validate(manifest, root)
    manifest["cases"][0]["documents"][0]["page_count"] = 1
    obs["facts"][0]["evidence"][0]["page"] = 1
    with pytest.raises(ValueError, match="outside"): rescore(manifest, obs)


@pytest.mark.parametrize("kind", ["missing", "duplicate", "unknown-state", "wrong-shape"])
def test_reader_outcomes_fail_closed(corpus, kind):
    _, manifest = corpus; obs = observations(manifest)
    if kind == "missing": obs["outcomes"] = []
    elif kind == "duplicate": obs["outcomes"] *= 2
    elif kind == "unknown-state": obs["outcomes"][0]["state"] = "success-ish"
    else: obs["outcomes"] = {}
    with pytest.raises(ValueError): score(manifest, obs, PENDING)


def test_labor_keeps_failed_held_and_unfinished_cases(corpus):
    _, manifest = corpus
    for n, state in enumerate(("failed", "held", "unfinished")):
        other = deepcopy(manifest["cases"][0]); other.update(id="case-" + state, family_group="family-" + state, completion=state)
        manifest["cases"].append(other)
    obs = observations(manifest)
    obs["outcomes"] = [{"case": c["id"], "state": "failed" if c["completion"] == "failed" else "completed"} for c in manifest["cases"]]
    obs["labor"] = [{"case": c["id"], "stage": "correction", "active_minutes": 2, "basis": "human_observed", "observer": "fictional-staff"} for c in manifest["cases"]]
    report = rescore(manifest, obs)
    assert report["cohort"]["cases"] == 4
    assert report["labor"]["total_minutes_per_accepted_case"]["rate"] == 8
    assert report["reader_outcomes"]["counts"]["failed"] == 1
    obs["labor"][0]["basis"] = "automated_runtime"
    with pytest.raises(ValueError, match="not human"): score(manifest, obs, PENDING)
    obs["labor"][0].update(basis="human_observed", active_minutes=float("inf"))
    with pytest.raises(ValueError): score(manifest, obs, PENDING)


@pytest.mark.parametrize("kind", ["family", "hash", "changed", "duplicate-fact", "bounds", "shape"])
def test_corpus_integrity_and_leakage(corpus, kind):
    root, manifest = corpus
    if kind in ("family", "hash"):
        other = deepcopy(manifest["cases"][0]); other.update(id="case-calibration", partition="calibration")
        if kind == "hash": other["family_group"] = "family-b"
        manifest["cases"].append(other)
    elif kind == "changed": (root / "i94.txt").write_text("changed bytes", encoding="utf-8")
    elif kind == "duplicate-fact": manifest["cases"][0]["expected"] *= 2
    elif kind == "bounds": manifest["cases"][0]["documents"][0]["instances"] = [{"id": "b" * 64, "pages": [0, 1]}]
    else: manifest["cases"][0]["documents"][0]["attributions"] = "not-list"
    with pytest.raises(ValueError): validate(manifest, root)


@pytest.mark.parametrize("path", ["../i94.txt", "/i94.txt", "a/../i94.txt", "a\\i94.txt", "C:/i94.txt", "a//i94.txt"], ids=["parent", "absolute", "mid-parent", "backslash", "drive", "empty-component"])
def test_source_paths_cannot_escape(corpus, path):
    root, _ = corpus
    with pytest.raises(ValueError): safe_source(root, path)


def test_source_symlink_refused(corpus, tmp_path):
    root, _ = corpus
    link = root / "linked.txt"
    try: link.symlink_to(root / "i94.txt")
    except OSError: pytest.skip("platform lacks local symlink privilege")
    with pytest.raises(ValueError, match="symlink"): safe_source(root, "linked.txt")


def test_frozen_artifact_cannot_be_overwritten(tmp_path):
    path = tmp_path / "frozen.json"; write_new(path, {"a": 1})
    with pytest.raises(FileExistsError): write_new(path, {"a": 2})
    assert json.loads(path.read_text())["a"] == 1


def release_inputs(manifest, inventory=ACCEPTED):
    config = {"adapter": "fictional-configuration"}
    obs = observations(manifest); obs["configuration_digest"] = digest(config)
    report = score(manifest, obs, inventory)
    policy = freeze_policy(manifest, THRESHOLDS, "fictional-release-owner", ["fictional"])
    policy["frozen_at"] = "2020-01-01T00:00:00+00:00"
    run = {"kind": "local_pipeline_execution", "started_at": "2020-01-02T00:00:00+00:00", "configuration": config, "configuration_digest": digest(config), "corpus_digest": digest(manifest), "observations_digest": digest(obs)}
    return report, run, policy


@pytest.mark.parametrize("kind", ["negative", "infinite", "rate", "missing", "blank-owner", "boolean"])
def test_policy_revalidation_on_compare(corpus, kind):
    _, manifest = corpus; report, run, policy = release_inputs(manifest)
    if kind == "negative": policy["thresholds"]["maximum_wrong_person"] = -1
    elif kind == "infinite": policy["thresholds"]["minimum_precision"] = float("inf")
    elif kind == "rate": policy["thresholds"]["minimum_coverage"] = 1.1
    elif kind == "missing": policy["thresholds"].pop("minimum_scored_fills")
    elif kind == "boolean": policy["thresholds"]["minimum_scored_fills"] = True
    else: policy["owner"] = " "
    with pytest.raises(ValueError): compare(report, report, run, run, policy)


def test_comparison_pending_imported_timing_and_no_promotion(corpus):
    _, manifest = corpus; report, run, policy = release_inputs(manifest, PENDING)
    comparison = compare(report, report, run, run, policy)
    assert comparison["proposal"] == "blocked"
    assert comparison["approved"] is False
    report, run, policy = release_inputs(manifest)
    assert compare(report, report, run, run, policy)["proposal"] == "accept"
    run["kind"] = "imported_observations"
    assert compare(report, report, run, run, policy)["proposal"] == "blocked"
    run.update(kind="local_pipeline_execution", started_at="2019-01-01T00:00:00+00:00")
    assert compare(report, report, run, run, policy)["proposal"] == "blocked"
    draft = decision(comparison, "fictional-owner", "Await EV3 and pilot", "fictional-base-commit")
    assert draft["state"] == "draft" and draft["approved"] is False
    run["configuration"]["edited"] = True
    with pytest.raises(ValueError, match="actual configuration"): compare(report, report, run, run, policy)


def test_wrong_critical_challenger_rejected(corpus):
    _, manifest = corpus; incumbent, irun, policy = release_inputs(manifest)
    obs = observations(manifest, value="wrong"); config = {"adapter": "fictional-challenger"}; obs["configuration_digest"] = digest(config)
    challenger = score(manifest, obs, ACCEPTED); crun = {**irun, "configuration": config, "configuration_digest": digest(config), "observations_digest": digest(obs)}
    proposal = compare(incumbent, challenger, irun, crun, policy)
    assert proposal["proposal"] == "reject" and not proposal["checks"]["critical_errors"]
    assert proposal["proposal_comparison"]["precision"]["delta"]["rate"] == -1
    assert proposal["proposal_comparison"]["critical_errors"]["challenger"] == {"numerator": 1, "denominator": 1, "rate": 1}
    assert proposal["proposal_comparison"]["critical_errors"]["delta"]["numerator"] == 1
    assert proposal["incumbent"]["configuration_digest"] != proposal["challenger"]["configuration_digest"]


def cli(*args, env=None):
    return subprocess.run([sys.executable, str(REPO / "tools" / "evaluate_corpus.py"), *map(str, args)], cwd=REPO, env=env, capture_output=True, text=True, timeout=60)


def test_real_product_adapter_fresh_process_labels_excluded_and_stable(corpus, tmp_path):
    root, manifest = corpus
    mf = tmp_path / "manifest.json"; cfg = tmp_path / "config.json"
    mf.write_text(json.dumps(manifest)); cfg.write_text(json.dumps({"version": 1, "adapter": "pipeline_text", "subject_map": {"case-a": {"applicant": "person-a"}}}))
    inherited = dict(os.environ, I485_SETTINGS=str(tmp_path / "must-not-read-settings.json"), I485_UNKNOWN_PRODUCT_CONTROL="must-not-inherit", PORTAL_DATA=str(tmp_path / "must-not-touch"), OLLAMA_URL="http://must-not-contact.invalid")
    outputs = []
    for name in ("actual-a", "actual-b"):
        out = tmp_path / name
        result = cli("run", "--manifest", mf, "--corpus-root", root, "--config", cfg, "--out", out, env=inherited)
        assert result.returncode == 0, result.stderr
        outputs.append((json.loads((out / "observations.json").read_text()), json.loads((out / "run.json").read_text())))
    a, arun = outputs[0]; b, brun = outputs[1]
    assert a["facts"] == b["facts"]
    assert a["configuration_digest"] == b["configuration_digest"]
    assert digest(a) == digest(b)
    assert digest(score(manifest, a, PENDING)) == digest(score(manifest, b, PENDING))
    assert arun["implementation"]["inspection_succeeded"] is True
    assert "tools/evaluate_corpus.py" in arun["implementation"]["files"]
    assert arun["actual_call"]["case-a"]["reference_labels_passed"] is False
    call = arun["actual_call"]["case-a"]
    if call["boundary_context_supplied"]:
        assert "boundary_context" in call["parameters"]
        assert call["boundary_context_digest"] is not None
        plan = call["boundary_plans"]["d-one.txt"]
        original_hash = manifest["cases"][0]["documents"][0]["sha256"]
        assert plan["source_sha256"] == original_hash
        for part in plan["instances"]:
            assert part["instance_id"] == digest({"identity": "original-range-1", "sha256": original_hash, "pages_zero_based": [part["first"], part["last"]]})
        assert any(link.get("retained_source_sha256") == original_hash for row in a["facts"] for link in row["evidence"])
    else:
        assert "boundary_context" not in call["parameters"]
        assert call["boundary_context_digest"] is None
    assert arun["configuration"]["isolation"]["inherited_product_controls"] is False
    assert arun["human_labor_measured"] is False
    assert a["outcomes"][0]["state"] == "completed"
    assert any(row["key"] == "applicant.i94_number" for row in a["facts"])
    assert not (tmp_path / "must-not-touch").exists()
    assert not (tmp_path / "must-not-read-settings.json").exists()
    manifest["cases"][0]["expected"][0]["value"] = "not-reader-input"
    mf.write_text(json.dumps(manifest)); out = tmp_path / "changed-reference"
    result = cli("run", "--manifest", mf, "--corpus-root", root, "--config", cfg, "--out", out)
    assert result.returncode == 0, result.stderr
    assert json.loads((out / "observations.json").read_text())["facts"] == a["facts"]
    # Scoring these actual observations does not fabricate accepted/PDF/labor.
    scored = score(manifest, json.loads((out / "observations.json").read_text()), PENDING)
    assert scored["extraction"]["accepted"]["precision"]["rate"] is None
    assert scored["labor"]["cases_without_labor_observation"] == 1


def test_adapter_refuses_cached_product_modules(corpus, tmp_path, monkeypatch):
    root, manifest = corpus
    fake = types.ModuleType("fictional_cached_product"); fake.__file__ = str(REPO / "src" / "settings.py")
    monkeypatch.setitem(sys.modules, "fictional_cached_product", fake)
    with pytest.raises(ValueError, match="fresh isolated process"):
        runner.run(manifest, root, {"version": 1, "adapter": "pipeline_text"}, tmp_path / "never-created", ["fictional"])
    assert not (tmp_path / "never-created").exists()


def test_import_provenance_declared_only(corpus):
    _, manifest = corpus; obs = observations(manifest)
    provenance = runner.imported(obs, manifest, {"adapter": "synthetic-declared"}, {"producer": "fictional-import", "evidence": "fictional-file-hash"}, ["score-import"])
    assert provenance["execution_by_this_tool"] is False
    assert provenance["configuration_is_declared_only"] is True
    with pytest.raises(ValueError): runner.imported(obs, manifest, {"adapter": "edited"}, {"producer": "fictional", "evidence": "fictional"}, [])


def test_compare_refuses_malformed_outcome_counts(corpus):
    _, manifest = corpus; report, run, policy = release_inputs(manifest)
    report["reader_outcomes"]["counts"] = {"success-ish": 1}
    with pytest.raises(ValueError, match="outcome counts"): compare(report, report, run, run, policy)


def test_adapter_unsupported_source_retains_failed_case(corpus, tmp_path):
    root, manifest = corpus; doc = manifest["cases"][0]["documents"][0]
    (root / "opaque.dat").write_bytes(b"Fictional unsupported source")
    doc.update(path="opaque.dat", sha256=hashlib.sha256((root / "opaque.dat").read_bytes()).hexdigest()); doc.pop("page_count")
    mf = tmp_path / "manifest.json"; cfg = tmp_path / "config.json"
    mf.write_text(json.dumps(manifest)); cfg.write_text(json.dumps({"version": 1, "adapter": "pipeline_text"}))
    out = tmp_path / "failed-reader"
    result = cli("run", "--manifest", mf, "--corpus-root", root, "--config", cfg, "--out", out)
    assert result.returncode == 0, result.stderr
    obs = json.loads((out / "observations.json").read_text())
    assert obs["outcomes"][0]["state"] == "failed"
    assert obs["outcomes"][0]["document_errors"][0]["document"] == "d-one"
