"""Hosted responsibility rows use existing public language, not server paths."""
import json

import maintenance
from test_deployment import mode, _app  # noqa: F401 -- isolated deployment fixture


def test_hosted_provider_rows_never_publish_internal_registry_titles(mode, tmp_path):  # noqa: F811 -- imported pytest fixture
    mode('hosted', 'Fictional Provider')
    rows = _app(tmp_path).maintenance()['provider_items']
    shown = json.dumps(rows)
    for path in ('data/', 'clients/', 'schemas/', 'src/', 'tools/', '.db', '.jsonl'):
        assert path not in shown
    assert 'rebuilt every night' not in shown
    registry = {i['id']: i for i in maintenance.registry()['items']}
    for key in ('disk_encryption', 'query_layer'):
        row = next(r for r in rows if r['id'] == key)
        assert row['what'] == registry[key]['firm_what']
        assert row['responsible'] == 'provider' and row['required_role'] == 'IT'
    assert 'data/' in registry['disk_encryption']['what']
    assert 'data/query.db' in registry['query_layer']['what']


def test_public_finding_redacts_client_folder_and_keeps_human_meaning():
    value = maintenance.public_finding('Review files under data/ and clients/; the original check needs attention.')
    assert 'data/' not in value and 'clients/' not in value
    assert 'the original check needs attention' in value
