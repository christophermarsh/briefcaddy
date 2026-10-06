"""Damaged upkeep evidence remains retained, uncredited and safe to inspect."""
import json
from datetime import date, datetime, timezone
from zoneinfo import ZoneInfo

import pytest

import clock
import deployment
import maintenance
from cloud_daily_work_fixtures import cloud_world, cloud_app, cloud_server, ATTORNEY, login, request  # noqa: F401 -- pytest fixture injection
from test_cloud_maintenance_status import _isolated_status


BAD_ROWS = [None, [], "broken", {"last_checked": []}, {"log": None}, {"log": {}},
    {"last_checked": "2026-10-02", "log": [None]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-10-02", "by": []}]},
    {"last_checked": "2026-10-02", "log": [{"on": {}, "by": "Fictional Reviewer"}]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-10-02", "by": ""}]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-10-06", "by": "Fictional Reviewer"}]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-99-99", "by": "Fictional Reviewer"}]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-10-01", "by": "Fictional Reviewer"}]},
    {"last_checked": "2026-10-02", "log": [{"on": "2026-10-02", "by": "Fictional Reviewer"},
                                             {"on": "2026-10-01", "by": "Fictional Reviewer"}]}]


@pytest.mark.parametrize("row", BAD_ROWS)
def test_nested_manual_damage_has_no_date_or_actor_credit_and_mark_refuses(tmp_path, monkeypatch, row):
    _isolated_status(tmp_path, monkeypatch, reviewed="2026-10-02")
    monkeypatch.setattr(deployment, "responsible", lambda _: "firm")
    raw = json.dumps({"fictional_provider": row}, indent=3)
    maintenance.FIRM_LOG.write_text(raw)
    path = tmp_path / "maintenance.json"
    registry_before = path.read_bytes()
    shown = maintenance.status(date(2026, 10, 5), path)[0]
    assert shown["state"] == "needs_attention" and shown["due"]
    assert shown["last_checked"] is None and shown["checked_by"] is None
    assert maintenance.MANUAL_RECORD_HOLD in shown["findings"]
    with pytest.raises(ValueError, match="Preserve it"):
        maintenance.mark("fictional_provider", "Fictional Reviewer", path, date(2026, 10, 5))
    assert maintenance.FIRM_LOG.read_text() == raw and path.read_bytes() == registry_before


@pytest.mark.parametrize("raw", ["not json", "[]", "null", '"broken"', "{", '{"unrelated":null}'])
def test_mark_refuses_whole_record_damage_without_reset_or_event(tmp_path, monkeypatch, raw):
    _isolated_status(tmp_path, monkeypatch)
    monkeypatch.setattr(deployment, "responsible", lambda _: "firm")
    maintenance.FIRM_LOG.write_text(raw)
    recorded = []
    monkeypatch.setattr(maintenance.events, "record", lambda *args, **kwargs: recorded.append((args, kwargs)))
    with pytest.raises(ValueError, match="reconcile or restore"):
        maintenance.mark("fictional_provider", "Fictional Reviewer", tmp_path / "maintenance.json", date(2026, 10, 5))
    assert maintenance.FIRM_LOG.read_text() == raw and not recorded


def test_damaged_row_does_not_credit_itself_or_erase_a_valid_sibling(tmp_path, monkeypatch):
    _isolated_status(tmp_path, monkeypatch)
    monkeypatch.setattr(deployment, "responsible", lambda _: "firm")
    path = tmp_path / "maintenance.json"
    registry = json.loads(path.read_text())
    registry["items"].append(dict(registry["items"][0], id="fictional_peer"))
    path.write_text(json.dumps(registry))
    value = {"fictional_provider": None, "fictional_peer": {"last_checked": "2026-10-02",
             "log": [{"on": "2026-10-02", "by": "Fictional Peer Reviewer"}]}}
    raw = json.dumps(value)
    maintenance.FIRM_LOG.write_text(raw)
    damaged, peer = maintenance.status(date(2026, 10, 5), path)
    assert damaged["state"] == "needs_attention" and damaged["last_checked"] is None
    assert peer["state"] == "reviewed_within_cadence" and peer["checked_by"] == "Fictional Peer Reviewer"
    with pytest.raises(ValueError):
        maintenance.mark("fictional_peer", "Fictional Reviewer", path, date(2026, 10, 5))
    assert maintenance.FIRM_LOG.read_text() == raw


def test_actual_translation_timestamp_history_is_preserved_without_generic_check_credit(tmp_path, monkeypatch):
    _isolated_status(tmp_path, monkeypatch)
    monkeypatch.setattr(deployment, "responsible", lambda _: "firm")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, 0))
    translation = {"on": "2026-10-05T09:00:00-04:00", "by": "fictional.attorney@example.test",
                   "review_type": "actual_qualified_translation_review", "language": "pt", "evidence": {"fictional": True}}
    maintenance.FIRM_LOG.write_text(json.dumps({"fictional_provider": {"log": [translation]}}))
    before = maintenance.status(clock.today(), tmp_path / "maintenance.json")[0]
    assert before["state"] == "not_checked" and before["checked_by"] is None
    maintenance.mark("fictional_provider", "Fictional Manual Reviewer", tmp_path / "maintenance.json")
    rows = json.loads(maintenance.FIRM_LOG.read_text())["fictional_provider"]["log"]
    assert rows == [translation, {"on": "2026-10-05", "by": "Fictional Manual Reviewer"}]
    after = maintenance.status(clock.today(), tmp_path / "maintenance.json")[0]
    assert after["state"] == "reviewed_within_cadence" and after["checked_by"] == "Fictional Manual Reviewer"


@pytest.mark.parametrize("stamp", ["2026-10-05T11:00:00-04:00", "2026-10-05T15:00:00Z",
    "2026-10-05T15:00:00", "2026-10-06T00:00:00+09:00", "2026-10-05T09:00:00.000001-04:00",
    "2026-99-99", "2026-10-05T99:00:00", [], None])
def test_full_future_or_malformed_observation_never_gets_checked_credit(monkeypatch, stamp):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 9, 0))
    shown = maintenance.live_evidence({"ok": True, "finding": "Previously observed fictional change."}, stamp, clock.today())
    assert shown["state"] == "unknown" and shown["checked_at"] is None and shown["ok"] is None
    assert "Previously observed fictional change." in shown["finding"]


@pytest.mark.parametrize("stamp", ["2026-10-05T09:00:00-04:00", "2026-10-05T13:00:00Z",
    "2026-10-05T13:00:00", "2026-10-05T22:00:00+09:00", "2026-10-05"])
def test_equal_or_past_instants_keep_legacy_utc_and_office_date_semantics(monkeypatch, stamp):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 9, 0))
    shown = maintenance.live_evidence({"ok": True, "finding": None}, stamp, clock.today())
    assert shown["state"] == "checked" and shown["checked_at"] == stamp and shown["ok"] is True


def test_dst_fold_comparison_uses_instants_not_the_repeated_wall_clock(monkeypatch):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 11, 1, 1, 30, tzinfo=ZoneInfo("America/New_York"), fold=0))
    shown = maintenance.live_evidence({"ok": True}, "2026-11-01T01:15:00-05:00", clock.today())
    assert shown["state"] == "unknown" and shown["checked_at"] is None
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 11, 1, 6, 30, tzinfo=timezone.utc))
    assert maintenance.live_evidence({"ok": True}, "2026-11-01T01:45:00-04:00", clock.today())["state"] == "checked"


def test_observation_uses_the_current_firm_zone_not_a_fixed_eastern_offset(monkeypatch):
    monkeypatch.setattr(clock, "zone_name", lambda: "Asia/Tokyo")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 9, 0))
    assert maintenance.live_evidence({"ok": True}, "2026-10-05T00:00:00Z", clock.today())["state"] == "checked"
    future = maintenance.live_evidence({"ok": True}, "2026-10-04T20:30:00-04:00", clock.today())
    assert future["state"] == "unknown" and future["checked_at"] is None


@pytest.mark.parametrize("error", [[], {}, "Traceback: fictional internal parser failure"])
def test_malformed_saved_error_cannot_crash_or_expose_a_raw_trace(tmp_path, monkeypatch, error):
    _isolated_status(tmp_path, monkeypatch)
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, 0))
    raw = json.dumps({"at": "2026-10-05T10:00:00-04:00", "results": {"fictional_provider": {"ok": True}}, "error": error})
    maintenance.LAST_LIVE.write_text(raw)
    observation = maintenance.recorded_live()
    assert observation["at"] is None and "Traceback" not in observation["error"]
    shown = maintenance.status(clock.today(), tmp_path / "maintenance.json")[0]
    assert shown["state"] == "needs_attention" and "Traceback" not in json.dumps(shown)
    assert maintenance.LAST_LIVE.read_text() == raw


def test_present_null_source_observation_is_unknown_not_a_never_run_check(tmp_path, monkeypatch):
    _isolated_status(tmp_path, monkeypatch, reviewed="2026-10-02")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, 0))
    raw = json.dumps({"at": "2026-10-05T10:00:00-04:00", "results": {"fictional_provider": None}})
    maintenance.LAST_LIVE.write_text(raw)
    row = maintenance.status(clock.today(), tmp_path / "maintenance.json")[0]
    assert row["state"] == "needs_attention" and row["live_check"]["state"] == "unknown"
    assert row["live_check"]["checked_at"] is None and maintenance.LAST_LIVE.read_text() == raw


@pytest.mark.parametrize("result", [[], "broken", {}, {"ok": []}, {"ok": 1}, {"ok": True, "finding": {}}, {"ok": True, "finding": [None]}])
def test_nested_live_result_damage_is_unknown_without_raw_value_exposure(monkeypatch, result):
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 12, 0))
    shown = maintenance.live_evidence(result, "2026-10-05T10:00:00-04:00", clock.today())
    assert shown["state"] == "unknown" and shown["checked_at"] is None and shown["ok"] is None
    assert shown["finding"]


@pytest.mark.parametrize("raw", ["not json", "[]", '{"firm_details":"broken"}',
    '{"firm_details":{"last_checked":"2026-10-02","log":[null]}}',
    '{"firm_details":{"last_checked":"2026-10-02","log":[{"on":"2026-10-02","by":[]}]}}'])
def test_actual_protected_get_and_mark_preserve_damaged_manual_record(cloud_world, cloud_app, cloud_server, monkeypatch, raw):  # noqa: F811 -- pytest fixture injection
    log = cloud_world["scope"].data / "maintenance_log.json"
    monkeypatch.setattr(maintenance, "FIRM_LOG", log)
    log.write_text(raw)
    cookie = login(cloud_server, ATTORNEY)
    status, _, response = request(cloud_server, "/api/maintenance", cookie)
    assert status == 200, response
    data = json.loads(response)
    row = next(item for item in data["items"] if item["id"] == "firm_details")
    assert row["state"] == "needs_attention" and row["last_checked"] is None and row["checked_by"] is None and row["due"]
    status, _, refused = request(cloud_server, "/api/maintenance", cookie,
        {"id": "firm_details", "reviewer": "Fictional Current Reviewer"})
    assert status == 400, refused
    message = json.loads(refused)["error"]
    assert "Preserve it" in message and "reconcile or restore" in message
    assert "Traceback" not in message and "JSONDecodeError" not in message and str(log) not in message
    assert log.read_text() == raw and not cloud_world["sent"]
    status, _, _ = request(cloud_server, "/api/maintenance")
    assert status == 401 and log.read_text() == raw


def test_actual_protected_future_live_time_is_unknown_in_row_and_header(cloud_world, cloud_app, cloud_server, monkeypatch):  # noqa: F811 -- pytest fixture injection
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 9, 0))
    path = cloud_world["scope"].data / "maintenance_status.json"
    monkeypatch.setattr(maintenance, "LAST_LIVE", path)
    raw = json.dumps({"at": "2026-10-05T15:00:00Z", "results": {"form_i485": {"ok": True, "finding": None}}})
    path.write_text(raw)
    status, _, response = request(cloud_server, "/api/maintenance", login(cloud_server, ATTORNEY))
    assert status == 200, response
    result = json.loads(response)
    row = next(row for row in result["provider_items"] if row["id"] == "form_i485")
    assert row["live_check"]["state"] == "unknown" and row["live_check"]["checked_at"] is None
    assert row["state"] == "needs_attention" and row["due"] and result["live_checked_at"] is None
    assert path.read_text() == raw and not cloud_world["sent"]
