"""Authoritative draft preview with no stored letter/file/audit side effects."""
import pytest

import engagement
from test_engagement import firm, FEE  # noqa: F401 -- pytest fixture registration and helper reexports


def snapshot(root):
    return {str(path.relative_to(root)): path.read_bytes() for path in root.rglob("*") if path.is_file()}


def test_preview_is_read_only_and_same_composition_as_actual_draft(firm):  # noqa: F811 -- pytest fixture injection
    before = snapshot(firm.root)
    original = engagement.read(firm.case)
    preview = engagement.preview_agreement(firm.case, ["i485", "i360", "i485"], FEE,
                                            government_fees="Fictional typed government fees", additions="Fictional typed additions",
                                            portal_root=firm.portal)
    assert snapshot(firm.root) == before and engagement.read(firm.case) == original
    assert preview["preview"] and preview["draft"] and preview["draft_reason"]
    assert preview["filings"] == ["i485", "i360"] and preview["fee"] == FEE
    assert set(preview["texts"]) == {"en", "pt"} and preview["translation"] == "machine"
    assert all(key not in preview for key in ("id", "signature", "made_at", "made_by", "approved"))
    engagement.make_agreement(firm.case, ["i485", "i360"], FEE, "Fictional Staff", "paralegal",
                              government_fees="Fictional typed government fees", additions="Fictional typed additions", portal_root=firm.portal)
    saved = engagement.read(firm.case)["letters"][-1]
    for key in ("texts", "titles", "wording_hash", "fee", "filings", "government_fees", "additions", "office", "language"):
        assert preview[key] == saved[key]
    assert saved["id"] == "L1"  # preview did not reserve a letter number


@pytest.mark.parametrize("filings,fee", [([], FEE), (["not-a-filing"], FEE), (["i485"], "")])
def test_preview_and_creation_share_input_refusal_without_effects(firm, filings, fee):  # noqa: F811 -- pytest fixture injection
    before = snapshot(firm.root)
    with pytest.raises(ValueError) as preview:
        engagement.preview_agreement(firm.case, filings, fee, portal_root=firm.portal)
    with pytest.raises(ValueError) as create:
        engagement.make_agreement(firm.case, filings, fee, "Fictional Staff", portal_root=firm.portal)
    assert str(preview.value) == str(create.value) and snapshot(firm.root) == before


def test_preview_refuses_ended_case_without_effects(firm):  # noqa: F811 -- pytest fixture injection
    import json
    (firm.case / engagement.FILE).write_text(json.dumps({"end": {"state": "closed", "on": "2026-10-01"}}))
    before = snapshot(firm.root)
    with pytest.raises(ValueError, match="closed"):
        engagement.preview_agreement(firm.case, ["i485"], FEE, portal_root=firm.portal)
    assert snapshot(firm.root) == before
