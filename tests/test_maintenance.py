"""Keeping current (src/maintenance.py, schemas/registers/maintenance.json): what goes out
of date, whether it has -- from the dates, the settings and the official sources."""

import json
import shutil
from datetime import date

import pytest

import maintenance
import schema_path


@pytest.fixture
def registry(tmp_path, monkeypatch):
    path = tmp_path / "maintenance.json"
    shutil.copy(maintenance.REGISTRY, path)
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    return path


def test_every_item_says_where_it_lives_who_owns_it_and_how(registry):
    items = maintenance.registry(registry)["items"]
    assert len({i["id"] for i in items}) == len(items) >= 20
    for i in items:
        assert i["owner"] in ("attorney", "paralegal", "IT") and i["cadence"] in ("monthly", "quarterly", "yearly", "not set") and i["where"] and i["steps"], i["id"]
    ids = {i["id"] for i in items}
    assert {"visa_bulletin", "fee_schedule", "poverty_guidelines", "lockbox_chart", "form_i485", "form_i130", "form_i864"} <= ids


def test_due_from_the_cadence_and_from_what_the_settings_show(registry):
    by = {i["id"]: i for i in maintenance.status(date(2026, 10, 15), registry)}
    assert by["visa_bulletin"]["due"] and "October 2026" in by["visa_bulletin"]["findings"][0]  # not set yet
    assert not by["poverty_guidelines"]["due"]  # checked 2026-10-01, yearly
    assert maintenance.status(date(2026, 11, 2), registry)[1]["due"]  # the fee schedule is monthly
    late = {i["id"]: i for i in maintenance.status(date(2027, 5, 1), registry)}
    assert late["poverty_guidelines"]["due"] and "more than a year ago" in late["poverty_guidelines"]["findings"][0]


def test_a_check_records_the_date_and_who(registry, tmp_path):
    # the firm's check goes in the firm's own log: an update to the register we ship never undoes it
    log = tmp_path / "firm_log.json"
    shipped = registry.read_text(encoding="utf-8")
    item = maintenance.mark("firm_details", "Andrew", registry, date(2026, 10, 2), log_path=log)
    assert item["last_checked"] == "2026-10-02" and item["log"] == [{"on": "2026-10-02", "by": "Andrew"}]
    assert registry.read_text(encoding="utf-8") == shipped and "firm_details" in log.read_text(encoding="utf-8")
    # ours (a form edition) is recorded in the register, by our own tool
    maintenance.mark("form_i485", "Provider", registry, date(2026, 10, 2), log_path=log)
    assert next(i for i in maintenance.registry(registry)["items"] if i["id"] == "form_i485")["last_checked"] == "2026-10-02"
    with pytest.raises(KeyError):
        maintenance.mark("nothing", "Andrew", registry, log_path=log)


def test_every_item_says_who_keeps_it_and_the_firm_reads_no_code():
    items = maintenance.registry()["items"]
    assert {i["party"] for i in items} == {"firm", "provider", "host"}
    for i in items:
        if i["party"] in ("firm", "host"):
            words = " ".join(i["firm_steps"])
            assert i["firm_steps"] and not any(w in words for w in ("schemas/", "src/", ".json", ".py", "IT:")), i["id"]


def test_live_checks_compare_the_official_sources_and_report_never_change(registry):
    template = (schema_path.path("template", "i485")).read_bytes()
    newer = template.replace(b"09/18/26", b"01/20/27")  # a new edition published

    def fake_get(url, binary=False, timeout=60):
        if url.endswith("i-485.pdf"):
            return newer if b"Edition" in newer else template
        if url.endswith(".pdf"):
            return (schema_path.path("template", "i864")).read_bytes()
        if "i-864p" in url:
            return "<p>These poverty guidelines are effective beginning Mar. 1, 2027.</p>"
        if "lockbox" in url:
            return "Last Reviewed/Updated: 10/11/2024"
        raise OSError("offline")

    out = maintenance.live_checks(registry, get=fake_get)["results"]
    assert out["poverty_guidelines"]["ok"] is False and "2027-03-01" in out["poverty_guidelines"]["finding"]
    assert out["lockbox_chart"]["ok"] is True
    assert out["form_i864"]["ok"] is True
    assert out["postal_links"]["ok"] is False or out["postal_links"]["finding"] is None
    assert json.loads(maintenance.LAST_LIVE.read_text())["results"]  # kept for the review app
    by = {i["id"]: i for i in maintenance.status(date(2026, 10, 15), registry)}
    assert by["poverty_guidelines"]["due"] and any("2027-03-01" in f for f in by["poverty_guidelines"]["findings"])


def test_the_edition_is_read_from_each_forms_own_footer():
    assert maintenance.pdf_edition(schema_path.path("template", "i485")) == "09/18/26"
    assert maintenance.pdf_edition(schema_path.path("template", "g28")) == "09/17/18"  # "Form G-28   09/17/18   Page 1 of 4"


def test_the_tps_item_watches_the_main_page_and_every_designated_countrys_page(registry):
    tps = json.loads((schema_path.path("law", "tps")).read_text(encoding="utf-8"))
    designated = {n: c for n, c in tps["countries"].items() if c["status"] == "designated"}
    assert {"Sudan", "Ukraine", "El Salvador", "Lebanon"} <= set(designated) and "Venezuela" not in designated
    asked = []

    def fake_get(url, binary=False, timeout=60):
        asked.append(url)
        if url == designated["Sudan"]["page"]:
            return "<p>Last Reviewed/Updated: 10/20/2026</p>"   # Sudan's designation changed
        known = {tps["source"]: tps["page_updated"], **{c["page"]: c["page_updated"] for c in designated.values()}}
        if url in known:
            return f"<p>Last Reviewed/Updated: {known[url]}</p>"
        raise OSError("offline")

    out = maintenance.live_checks(registry, get=fake_get)["results"]["tps_status"]
    assert set(asked) >= {tps["source"], *(c["page"] for c in designated.values())}
    assert out["ok"] is False and "Sudan was updated 10/20/2026 (we read the 09/03/2026 version)" in out["finding"] and "El Salvador" not in out["finding"]
