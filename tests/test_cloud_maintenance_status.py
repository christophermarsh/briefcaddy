"""Fictional maintenance evidence must not become an invented update job."""
import json
from datetime import date

import maintenance
from review.server import ReviewApp
import schema_path
import pytest


def _isolated_status(tmp_path, monkeypatch, *, reviewed=None, finding=None, live=None):
    path = tmp_path / 'maintenance.json'
    path.write_text(json.dumps({'items': [{'id': 'fictional_provider', 'what': 'Fictional form', 'owner': 'attorney',
        'cadence': 'monthly', 'last_checked': reviewed, 'party': 'provider', 'where': 'private-provider-source',
        'steps': ['Provider implementation step'], 'check': {'type': 'fictional_manual'}, 'source': 'https://example.invalid'}]}))
    monkeypatch.setattr(maintenance, 'FIRM_LOG', tmp_path / 'firm.json')
    monkeypatch.setattr(maintenance, 'LAST_LIVE', tmp_path / 'live.json')
    monkeypatch.setattr(maintenance, '_local_finding', lambda item, today: finding)
    if live:
        maintenance.LAST_LIVE.write_text(json.dumps(live))
    return maintenance.status(date(2026, 10, 5), path)[0]


def test_never_checked_provider_is_not_an_active_update_job(tmp_path, monkeypatch):
    row = _isolated_status(tmp_path, monkeypatch)
    monkeypatch.setattr(maintenance, 'status', lambda: [row])
    data = tmp_path / 'clients'; data.mkdir()
    app = ReviewApp(data, schema_path.path('field_map', 'i485'), schema_path.path('template', 'i485'), None)
    provider = app.maintenance()['provider_items'][0]
    assert provider['status'] == 'Not checked', provider


@pytest.mark.parametrize('reviewed,finding,state', [(None, None, 'not_checked'),
    ('2026-10-02', None, 'reviewed_within_cadence'), ('2026-09-01', None, 'due_for_review'),
    ('2026-10-02', 'The official source changed.', 'needs_attention'),
    (None, 'The official source is unavailable.', 'needs_attention')])
def test_status_follows_actual_review_and_findings(tmp_path, monkeypatch, reviewed, finding, state):
    row = _isolated_status(tmp_path, monkeypatch, reviewed=reviewed, finding=finding)
    assert row['state'] == state and row['status'] == maintenance.STATUS_LABELS[state]
    assert row['check_type'] == 'fictional_manual'
    assert row['live_check']['state'] == 'not_checked'


@pytest.mark.parametrize('reviewed', ['not-a-date', '2026-99-31', '2026-10-06', True, 123])
def test_invalid_or_future_review_date_cannot_be_healthy(tmp_path, monkeypatch, reviewed):
    row = _isolated_status(tmp_path, monkeypatch, reviewed=reviewed)
    assert row['state'] == 'needs_attention' and row['due'] and row['last_checked'] is None
    assert any('review date' in f for f in row['findings'])


def test_saved_live_check_does_not_invent_human_review(tmp_path, monkeypatch):
    row = _isolated_status(tmp_path, monkeypatch, live={'at': '2026-10-05T10:00:00-04:00',
        'results': {'fictional_provider': {'ok': True, 'finding': None}}})
    assert row['state'] == 'not_checked' and row['last_checked'] is None
    assert row['live_check'] == {'state': 'checked', 'checked_at': '2026-10-05T10:00:00-04:00', 'ok': True, 'finding': None}


@pytest.mark.parametrize('stamp', ['not-a-date', '2026-10-06T10:00:00-04:00', None])
def test_invalid_live_timestamp_preserves_old_finding_and_unknown_state(tmp_path, monkeypatch, stamp):
    row = _isolated_status(tmp_path, monkeypatch, reviewed='2026-10-02', live={'at': stamp,
        'results': {'fictional_provider': {'ok': False, 'finding': 'Previously observed official change.'}}})
    assert row['state'] == 'needs_attention' and row['due']
    assert row['live_check']['state'] == 'unknown' and row['live_check']['checked_at'] is None
    assert any('Previously observed official change.' in f for f in row['findings'])


def test_tps_actual_live_finding_is_sanitized_only_in_staff_dto(tmp_path, monkeypatch):
    from test_deployment import _app
    fixture = json.loads(maintenance.REGISTRY.read_text())
    fixture['items'] = [next(i for i in fixture['items'] if i['id'] == 'tps_status')]
    path = tmp_path / 'maintenance.json'; path.write_text(json.dumps(fixture))
    monkeypatch.setattr(maintenance, 'LAST_LIVE', tmp_path / 'live.json')
    monkeypatch.setattr(maintenance, 'FIRM_LOG', tmp_path / 'firm.json')
    tps = json.loads(schema_path.path('law', 'tps').read_text())
    country = next(c for c in tps['countries'].values() if c['status'] == 'designated')
    def get(url, binary=False, timeout=60):
        known = {tps['source']: tps['page_updated'], **{c['page']: c['page_updated'] for c in tps['countries'].values() if c['status'] == 'designated'}}
        return 'Last Reviewed/Updated: ' + ('10/04/2026' if url == country['page'] else known[url])
    raw = maintenance.live_checks(path, get=get)
    assert 'schemas/law/tps.json' in raw['results']['tps_status']['finding']
    actual_status = maintenance.status
    monkeypatch.setattr(maintenance, 'status', lambda: actual_status(path=path))
    row = _app(tmp_path).maintenance()['provider_items'][0]
    assert row['state'] == 'needs_attention' and row['required_role'] == 'attorney'
    assert all('schemas/' not in text for text in row['findings'])
    assert 'schemas/' not in json.dumps(row['live_check'])
    assert 'schemas/law/tps.json' in json.loads(maintenance.LAST_LIVE.read_text())['results']['tps_status']['finding']


@pytest.mark.parametrize('contents', ['not json', '[]', '{"results": []}'])
def test_malformed_live_record_is_unknown_without_a_status_crash(tmp_path, monkeypatch, contents):
    from test_deployment import _app
    monkeypatch.setattr(maintenance, 'LAST_LIVE', tmp_path / 'live.json')
    monkeypatch.setattr(maintenance, 'FIRM_LOG', tmp_path / 'firm.json')
    maintenance.LAST_LIVE.write_text(contents)
    result = _app(tmp_path).maintenance()
    row = next(i for i in result['provider_items'] if i['id'] == 'form_i485')
    assert row['state'] == 'needs_attention' and row['due']
    assert row['live_check']['state'] == 'unknown' and row['live_check']['checked_at'] is None
    assert result['live_checked_at'] is None and result['held'] == []


def test_review_without_a_known_cadence_cannot_claim_current(tmp_path, monkeypatch):
    row = _isolated_status(tmp_path, monkeypatch, reviewed='2026-10-02')
    path = tmp_path / 'maintenance.json'
    registry = json.loads(path.read_text())
    registry['items'][0]['cadence'] = 'not set'
    path.write_text(json.dumps(registry))
    row = maintenance.status(date(2026, 10, 5), path)[0]
    assert row['state'] == 'needs_attention' and row['due']
    assert row['last_checked'] == '2026-10-02'


def test_non_boolean_saved_check_result_is_unknown():
    observation = maintenance.live_evidence({'ok': 'true'}, '2026-10-05', date(2026, 10, 5))
    assert observation['state'] == 'unknown' and observation['ok'] is None
    assert observation['finding']
