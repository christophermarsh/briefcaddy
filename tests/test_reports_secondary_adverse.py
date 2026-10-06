"""Independent fictional actual Reports unknown-secondary authority probe."""
# ruff: noqa: F811 -- imported canonical fixtures
import json
import os
from pathlib import Path

from test_restricted import app, world  # noqa: F401
from test_reports import _wording
import wordings


def test_reports_excludes_secondary_history_for_cases_current_actor_cannot_open(app, world, monkeypatch):
    actor = {'email': 'jane@firm.example', 'name': 'Jane Doe', 'role': 'paralegal', 'active': True}
    missing, shell = 'fictional-absent-secondary', 'fictional-shell-secondary'
    (world / shell).mkdir()
    assert not (world / missing).exists()
    assert not (world / shell / 'fact_graph.json').exists()
    assert not (world / shell / 'access.json').exists()
    assert app.may_open(actor, missing) is False
    assert app.may_open(actor, shell) is False
    monkeypatch.setenv('I485_WORDINGS', str(world.parent / 'fictional-secondary-wordings'))
    state_path = world.parent / 'batch_state.json'
    state = json.loads(state_path.read_text())
    for cid in (missing, shell):
        state[cid] = {'status': 'failed', 'at': '2026-10-01T03:00:00+00:00', 'error': 'Fictional withheld history ' + cid}
        _wording(wordings.root(world), 'w-' + cid, 'applicant.part9.committed_crime',
                 'Fictional withheld wording ' + cid, [cid])
    state_path.write_text(json.dumps(state))
    report = app.reports('paralegal', actor)
    tables = {table['id']: table for table in report['tables']}
    observed = {'actor': actor, 'may_open': {cid: app.may_open(actor, cid) for cid in (missing, shell)},
                'overnight_problems': tables.get('overnight_problems', {}).get('rows', []),
                'wordings': tables.get('wordings', {}).get('rows', [])}
    Path(os.environ['REVIEW_RECEIPT_RUN'], 'secondary-evidence.json').write_text(json.dumps(observed, indent=2))
    encoded = json.dumps(observed['overnight_problems']) + json.dumps(observed['wordings'])
    leaked = [cid for cid in (missing, shell) if cid in encoded]
    assert not leaked, ('Reports released secondary histories for current-authority-denied case IDs', observed)