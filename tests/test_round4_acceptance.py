"""Synthetic Round 4 evidence, health and release acceptance; no host installation."""
from datetime import timedelta
import json
from types import SimpleNamespace

import pytest
import clock
import engagement
from file_policy_fixture import handover, archive_arguments, receipt_arguments
import events
import ledger_seal
import signing_evidence
from review.health import summary
from test_engagement import firm, approve, NAME, FEE  # noqa: F401 -- pytest fixture registration and helper reexports


def signed(firm):  # noqa: F811 -- pytest fixture injection
    approve()
    engagement.make_agreement(firm.case, ["i485"], FEE, "Synthetic attorney", "attorney", portal_root=firm.portal)
    ident = engagement.read(firm.case)["letters"][-1]["id"]
    engagement.send(firm.case, ident, "Synthetic attorney", "attorney", firm.portal)
    firm.store.sign_agreement("case-ana", ident, NAME, "203.0.113.9", "pt")
    engagement.sync(firm.case, firm.portal)
    return ident


def seal(firm):  # noqa: F811 -- pytest fixture injection
    ledger_seal.nightly(events.base_path(firm.root), clock.today() + timedelta(days=1))


def test_signature_requires_covering_anchor_and_retains_pdf(firm):  # noqa: F811 -- pytest fixture injection
    ident = signed(firm)
    assert not signing_evidence.verify(firm.case, ident)["ok"]
    seal(firm)
    result = signing_evidence.verify(firm.case, ident)
    assert result["ok"] and result["signer"] == NAME
    assert signing_evidence.certificate(firm.case, ident).startswith(b"%PDF")


@pytest.mark.parametrize("part", [0, 1, 2])
def test_altered_signing_artifacts_fail_closed(firm, part):  # noqa: F811 -- pytest fixture injection
    ident = signed(firm)
    seal(firm)
    assert signing_evidence.verify(firm.case, ident)["ok"]
    path = signing_evidence.paths(firm.case, ident)[part]
    path.write_bytes(path.read_bytes() + b"changed")
    assert not signing_evidence.verify(firm.case, ident)["ok"]


def test_corrupt_anchor_is_not_skipped(firm):  # noqa: F811 -- pytest fixture injection
    ident = signed(firm)
    seal(firm)
    path = ledger_seal.anchors_path(events.base_path(firm.root))
    with path.open("ab") as stream:
        stream.write(b"not-json\n")
    assert not signing_evidence.verify(firm.case, ident)["ok"]


def test_client_handover_requires_approval_and_current_archive(firm):  # noqa: F811 -- pytest fixture injection
    binding = handover(firm.case, who="Synthetic attorney", portal_root=firm.portal, include_work_product=True)
    engagement.export_file(firm.case, firm.clients, "Synthetic attorney", "attorney", firm.portal, expected_binding_sha256=binding)
    sha = engagement.read(firm.case)["file"]["sha256"]
    with pytest.raises(ValueError, match="approve"):
        engagement.file_returned(firm.case, "2026-10-05", "in_person", "Synthetic attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    engagement.approve_file(firm.case, sha, "Synthetic attorney", "attorney", firm.portal, **archive_arguments(firm.case))
    path = engagement.file_path(firm.case, firm.clients)
    path.write_bytes(path.read_bytes() + b"changed")
    with pytest.raises(ValueError):
        engagement.file_returned(firm.case, "2026-10-05", "in_person", "Synthetic attorney", "attorney", firm.portal, sha256=sha, **receipt_arguments(firm.case))
    assert not engagement.read(firm.case)["file"]["returned"]


def test_health_missing_and_corrupt_sources_are_not_healthy(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_POSTURE", str(tmp_path / "posture.json"))
    monkeypatch.setenv("I485_BACKUP_LOG", str(tmp_path / "backup_log.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    app = SimpleNamespace(firm_data=tmp_path, roster=None)
    result = summary(app)
    assert result["status"] == "attention"
    assert next(s for s in result["signals"] if s["id"] == "posture")["status"] == "never-run"
    (tmp_path / "posture.json").write_text('{"checks":["bad"]}')
    (tmp_path / "backup_log.json").write_text('{"last_backup":["bad"]}')
    result = summary(app)
    assert next(s for s in result["signals"] if s["id"] == "posture")["status"] == "unavailable"
    assert len({s["id"] for s in result["signals"]}) == len(result["signals"])


def test_partial_posture_or_missing_stamp_cannot_pass(tmp_path, monkeypatch):
    import posture
    path = tmp_path / "posture.json"
    monkeypatch.setenv("I485_POSTURE", str(path))
    app = SimpleNamespace(firm_data=tmp_path, roster=None)
    for checks in ([{"id":"disk", "result":"on"}], [{"id":key, "result":"on"} for key in posture.DUTIES]):
        path.write_text(json.dumps({"checks":checks}))
        assert next(s for s in summary(app)["signals"] if s["id"] == "posture")["status"] == "unavailable"


def test_signed_release_tamper_and_wrong_key_refused(tmp_path):
    import release_support as release
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey
    root = tmp_path / "product"
    for name in release.REQUIRED_FILES:
        path = root / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Synthetic fixture\n")
    (root / "src/version.py").write_text('VERSION = "2026.10.10"\nRELEASED = "2026-10-04"\n')
    (root / "docs/releases.md").write_text('## 2026.10.10 (2026-10-04)\n')
    (root / "src/records.py").write_text('RECORDS = [{"id":"fixture", "version":1, "versions":[(1,"fixture")]}]\n')
    private = Ed25519PrivateKey.generate()
    key = tmp_path / "private.pem"
    key.write_bytes(private.private_bytes(serialization.Encoding.PEM, serialization.PrivateFormat.PKCS8, serialization.NoEncryption()))
    public = tmp_path / "public.pem"
    public.write_bytes(private.public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    archive = tmp_path / "release.zip"
    release.build(root, archive, key)
    release.verify(archive, public, tmp_path / "verified")
    wrong = tmp_path / "wrong.pem"
    wrong.write_bytes(Ed25519PrivateKey.generate().public_key().public_bytes(serialization.Encoding.PEM, serialization.PublicFormat.SubjectPublicKeyInfo))
    with pytest.raises(release.Problem):
        release.verify(archive, wrong)
    archive.write_bytes(archive.read_bytes() + b"tampered")
    with pytest.raises(release.Problem):
        release.verify(archive, public)

def test_health_authorization_and_reads_do_not_mutate_records(tmp_path, monkeypatch):
    from review.server import ReviewApp
    app = ReviewApp.__new__(ReviewApp)
    app.accounts = object()
    app.data_root = tmp_path / "clients"
    app.roster = None
    monkeypatch.setenv("I485_POSTURE", str(tmp_path / "posture.json"))
    monkeypatch.setenv("I485_BACKUP_LOG", str(tmp_path / "backup_log.json"))
    monkeypatch.setenv("I485_EVENTS", str(tmp_path / "events.jsonl"))
    before = {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()}
    for user in (None, {"role":"paralegal"}, {"role":"support"}):
        with pytest.raises(PermissionError):
            app.health(user)
    assert app.health({"role":"attorney"})["status"] == "attention"
    assert {p.relative_to(tmp_path).as_posix():p.read_bytes() for p in tmp_path.rglob("*") if p.is_file()} == before
