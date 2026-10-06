"""Fictional owner sources: no publication or actual approval is performed."""
import json
from pathlib import Path
import sys

import pytest

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "tools"))
import public_pages


def approved_fixture():
    return {"version": 1, "status": "owner_approved", "approved": True,
            "approved_by": "fictional-owner", "approval_record": "Fictional test approval only",
            "terms": {"provider_legal_name": "Fictional Provider LLC", "pilot_fee_usd": 5000, "first_year_total_usd": 35000,
                      "renewal_monthly_usd": 1250, "named_users": 40, "pilot_credited": True,
                      "scope": "Supported preparation and attorney review", "installation": "One local fictional installation",
                      "training": "Agreed staff sessions", "support_contact": "support@example.invalid", "support_hours": "Weekdays 9am to 5pm Eastern",
                      "first_response": "One business day for acknowledgement", "hardware": "Firm supplies agreed host; separately priced",
                      "license": "Fictional reviewed agreement reference", "payment": "Expansion requires firm's separate decision",
                      "exit": "Export assistance and firm retention choices"}}


def source(tmp_path, data):
    path = tmp_path / "owner-terms.json"; path.write_text(json.dumps(data), encoding="utf-8")
    return path


def test_default_draft_publishes_no_provisional_price():
    draft = public_pages.commercial_terms()
    assert draft["approved"] is False and draft["status"] == "draft"
    assert draft["proposal"]["named_users_approximately"] == 40
    assert public_pages.render_price() == public_pages.PRICE_SENTENCE + "\n"
    assert "$" not in public_pages.render_price()


@pytest.mark.parametrize("kind", ["wrong-version", "wrong-shape", "inconsistent-draft", "unapproved", "missing-owner", "missing-record", "missing-support", "pending-support", "negative", "bool-amount", "float-users", "credit-exceeds-total"], ids=["version", "shape", "draft-approval", "no-approval", "owner", "record", "support", "pending", "negative", "bool", "users", "credit"])
def test_malformed_or_incomplete_public_terms_refused(tmp_path, kind):
    data = approved_fixture()
    if kind == "wrong-version": data["version"] = 2
    elif kind == "wrong-shape": data = []
    elif kind == "inconsistent-draft": data["status"] = "draft"
    elif kind == "unapproved": data["approved"] = False
    elif kind == "missing-owner": data["approved_by"] = " "
    elif kind == "missing-record": data["approval_record"] = None
    elif kind == "missing-support": data["terms"].pop("support_contact")
    elif kind == "pending-support": data["terms"]["support_hours"] = "pending"
    elif kind == "negative": data["terms"]["renewal_monthly_usd"] = -1
    elif kind == "bool-amount": data["terms"]["pilot_fee_usd"] = True
    elif kind == "float-users": data["terms"]["named_users"] = 40.0
    else: data["terms"]["first_year_total_usd"] = 1000
    with pytest.raises(public_pages.PageError): public_pages.render_price(source(tmp_path, data))


def test_owner_values_escaped_and_draft_numbers_ignored(tmp_path):
    data = approved_fixture()
    data["terms"]["provider_legal_name"] = "Fictional <script>alert('x')</script> & [link](https://example.invalid)"
    data["proposal"] = {"first_year_target_usd": 1}
    page = public_pages.render_price(source(tmp_path, data))
    assert "<script>" not in page and "&lt;script&gt;" in page
    assert "[link](https" not in page and "\\[link\\]\\(https" in page
    assert "$35,000 USD" in page and "$1 USD" not in page
    assert "40" in page and "$1,250 USD" in page


def test_same_owner_source_survives_regeneration_and_checks(tmp_path, monkeypatch):
    monkeypatch.setattr(public_pages, "provider_from_deployment", lambda: None)
    data = approved_fixture(); path = source(tmp_path, data); out = tmp_path / "owner-review"
    args = ["--no-sample", "--commercial-source", str(path), "--out", str(out)]
    assert public_pages.main(args) == 0
    first = (out / "price.md").read_bytes()
    assert public_pages.main(args) == 0
    assert (out / "price.md").read_bytes() == first
    assert public_pages.main(["--check", "--commercial-source", str(path), "--out", str(out)]) == 0
    assert json.loads(path.read_text()) == data
    assert not (out / "accuracy.md").exists()


def test_existing_owner_page_preserved_before_any_default_write(tmp_path):
    out = tmp_path / "owner-review"; out.mkdir()
    price = out / "price.md"; original = "Actual owner custom price, preserved in fictional test\n"
    price.write_text(original, encoding="utf-8")
    with pytest.raises(public_pages.PageError, match="preserved"):
        public_pages.main(["--no-sample", "--out", str(out)])
    assert price.read_text() == original
    assert list(out.iterdir()) == [price]


def test_nonfinite_source_refused_and_draft_override_safe(tmp_path):
    path = tmp_path / "bad.json"; path.write_text('{"version":1,"price":NaN}')
    with pytest.raises(public_pages.PageError): public_pages.commercial_terms(path)
    draft = {"version": 1, "status": "draft", "approved": False, "proposal": {"price": 999999}}
    assert public_pages.render_price(source(tmp_path, draft)) == public_pages.PRICE_SENTENCE + "\n"
