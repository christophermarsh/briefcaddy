"""Independent fictional adverse probes; no legal determination or real file."""
import pytest

import engagement
import purge
from test_purge import firm, _ended_case  # noqa: F401 -- isolated canonical fixture

# Imported pytest fixture deliberately shares names with the injected arguments.
# ruff: noqa: F811


def test_file_cannot_be_approved_without_explicit_jurisdiction_policy(firm):
    d = _ended_case(firm)
    try:
        engagement.export_file(d, firm.clients, "Leader Fictional Attorney", "attorney", firm.portal)
    except ValueError:
        # Refusing preparation pending explicit policy is a safe implementation.
        assert not (engagement.read(d).get("file") or {}).get("approved")
        return
    saved = engagement.read(d)["file"]
    assert "Florida" not in str(saved.get("proposed_default", "")), "No jurisdiction was selected"
    with pytest.raises(ValueError):
        engagement.approve_file(d, saved["sha256"], "Leader Fictional Attorney", "attorney", firm.portal)


def test_typed_reason_cannot_bypass_unconfirmed_retention_or_destruction_authority(firm):
    d = _ended_case(firm)
    purge.record_contact(d, "letter", "2026-10-01", "Fictional contact recorded", "Leader Fictional Attorney", "attorney")
    purge.review_originals(d, [], True, "Leader Fictional Attorney", "attorney")
    with pytest.raises(ValueError):
        purge.ask(d, "The client asked", "Leader Fictional Attorney", "attorney", attorneys=2)
    assert not purge.waiting(firm.clients)


def test_manual_destroyed_record_cannot_use_unapproved_office_clock_as_legal_authority(firm, monkeypatch):
    from datetime import datetime
    import clock
    import settings

    settings.save("firm", {"firm.state": "MA", "office.retention_years": "6"}, "Leader Fictional Attorney")
    monkeypatch.setattr(clock, "_now_override", datetime(2010, 10, 5, 10, 30))
    d = _ended_case(firm, born="1980-03-14")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 10, 30))
    assert not (engagement.read(d).get("destroyed"))
    with pytest.raises((ValueError, PermissionError, LookupError)):
        engagement.mark_destroyed(firm.clients, d.name, "Leader Fictional Attorney", "attorney",
                                  folder_removed=False, export_kept=True, note="Recorded manually")
    assert not engagement.read(d).get("destroyed")
    assert not engagement.destroyed(firm.clients)
