"""Fictional deterministic scheduling probe; no browser or provider."""
import json
import os
from pathlib import Path

import offices
import overnight
from review.roster import Roster
from review.server import ReviewApp


def test_completed_first_walk_never_strands_an_incomplete_overview(tmp_path, monkeypatch):
    monkeypatch.setenv('I485_WALK_EVERY', '600')
    clients = tmp_path / 'data' / 'clients'
    cid = 'fictional-roster-late-row'
    (clients / cid).mkdir(parents=True)
    actor = {'email': 'jane@firm.example', 'role': 'paralegal', 'active': True}
    entry = {'row': {'id': cid, 'stage': 'invited', 'summary': {'name': 'Fictional Late Roster'}, 'journey': {'deadlines': []}},
             'has_case': False, 'held': False, 'closed': False, 'named': [], 'office': None, 'ts': None, 'filed': False, 'built': False}
    roster = Roster(clients, {}, '', None)
    roster.reading = {'total': 1, 'done': 0}
    # Model sync returning after its initial reading budget while worker remains busy.
    monkeypatch.setattr(roster, 'sync', lambda: None)
    monkeypatch.setattr(roster, 'build', lambda case: entry if case == cid else None)
    app = ReviewApp.__new__(ReviewApp)
    app.data_root = clients
    app.roster = roster
    monkeypatch.setattr(app, 'prospect_tasks', lambda user: [])
    monkeypatch.setattr(overnight, 'progress', lambda data: None)
    observed = {'before_rows': roster.rows(actor), 'before_pending': roster.still_reading(), 'actor': actor}
    assert not roster.hides(actor, entry)
    assert observed['before_rows'] == [] and observed['before_pending'] == 1

    def finish_first_walk_after_row_snapshot():
        # offices.offices runs after overview's _rows snapshot. Complete the real
        # progressive walk here to impose one legal worker/request interleaving.
        roster.walk(parallel=False, progressive=True)
        observed['after_rows'] = roster.rows(actor)
        observed['after_pending'] = roster.still_reading()
        return []

    monkeypatch.setattr(offices, 'offices', finish_first_walk_after_row_snapshot)
    response = app.overview(user=actor)
    observed['response'] = response
    destination = Path(os.environ['REVIEW_RECEIPT_RUN']) / 'roster-schedule-evidence.json'
    destination.write_text(json.dumps(observed, indent=2))
    assert observed['after_pending'] == 0
    assert [row['id'] for row in observed['after_rows']] == [cid]
    assert any(row['id'] == cid for row in response['clients']) or response['still_reading'] > 0, (
        'Overview returned an incomplete row snapshot with still_reading=0, so renderAll stops polling', observed)