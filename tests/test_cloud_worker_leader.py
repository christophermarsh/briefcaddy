"""Independent recovery refusal probes; fictional queued rows, no readers or sends."""
import json
from contextlib import contextmanager

import pytest

import jobs
from portal import communication_consent as consent


def pending(tmp_path, monkeypatch, kind='staff_upload'):
    data = tmp_path / 'fictional-installation' / 'data'
    cases = data / 'clients'
    cases.mkdir(parents=True)
    for name, relative in {'I485_JOBS': 'jobs', 'I485_RULES_APPROVED': 'rules_approved.json',
        'I485_MAINTENANCE_LOG': 'maintenance_log.json', 'I485_EVENTS': 'events.jsonl',
        'I485_SETTINGS': 'settings.json', 'I485_CASES': 'clients', 'I485_PROSPECTS': 'prospects',
        'PORTAL_DATA': 'portal'}.items():
        monkeypatch.setenv(name, str(data / relative))
    ctx = jobs.Context(cases, data / 'portal')
    job = jobs.submit(ctx.root, kind, client='fictional-case', args={'name': 'fictional.pdf'})
    job['state'] = 'running'
    path = ctx.root / (job['id'] + '.json')
    path.write_text(json.dumps(job))
    return ctx, job, path


@pytest.mark.parametrize('startup', [False, True])
def test_recovery_gate_timeout_cannot_publish_died_row(startup, tmp_path, monkeypatch):
    ctx, job, path = pending(tmp_path, monkeypatch)
    before = path.read_bytes()
    monkeypatch.setattr(jobs, 'alive', lambda _: False)

    @contextmanager
    def busy(_):
        raise TimeoutError('Fictional installation mutation currently owns the gate')
        yield

    monkeypatch.setattr(consent, 'data_gate', busy)
    try:
        jobs.sweep_dead(ctx) if startup else jobs.sweep(ctx.clients, ctx.portal)
    except TimeoutError:
        pass
    assert path.read_bytes() == before, 'Gate acquisition failure rewrote the running job'
    assert not (ctx.root / 'done' / path.name).exists(), 'Ungated fallback published a recovery outcome'


@pytest.mark.parametrize('kind', ['portal_upload', 'staff_upload'])
def test_recovery_rechecks_cached_case_identity_before_any_effect(kind, tmp_path, monkeypatch):
    ctx, job, path = pending(tmp_path, monkeypatch, kind)
    original = consent.data_gate
    entered = []
    replacement = dict(job, client='different-fictional-case')

    @contextmanager
    def changed_while_acquiring(data):
        if not entered:
            path.write_text(json.dumps(replacement))
            entered.append(True)
        with original(data):
            yield

    monkeypatch.setattr(consent, 'data_gate', changed_while_acquiring)
    jobs.sweep_dead(ctx)
    assert entered, 'Recovery must take the installation mutation gate'
    assert json.loads(path.read_text()) == replacement, 'Cached identity overwrote the current durable job'
    assert not (ctx.root / 'done' / path.name).exists()
