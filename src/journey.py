"""Application parser or workflow helper."""
from __future__ import annotations
import calendar
import json
import os
import re
import secrets
import stat
from contextlib import ExitStack
from datetime import date, timedelta
from functools import lru_cache
from pathlib import Path
from typing import Any
import clock
import events
import schema_path
from holders import ATTORNEY, producer
SETTINGS = schema_path.path('register', 'journey')
OWNERS = ('attorney', 'paralegal', 'client')
_ISO = re.compile('\\d{4}-\\d{2}-\\d{2}')
_WORDS = [('REQUEST FOR EVIDENCE', 'rfe'), ('INTENT TO DENY', 'noid'), ('DENIAL', 'denial'), ('REJECTION', 'rejection'), ('APPROVAL', 'approval'), ('TRANSFER', 'transfer'), ('BIOMETRICS', 'biometrics'), ('INTERVIEW', 'interview'), ('RECEIPT', 'receipt')]
KIND_NAMES = {'receipt': 'receipt notice', 'approval': 'approval', 'rfe': 'request for evidence', 'noid': 'notice of intent to deny', 'denial': 'denial', 'rejection': 'rejection (filing returned)', 'transfer': 'transfer notice', 'biometrics': 'biometrics appointment', 'interview': 'interview notice', 'notice': 'notice'}
CLOSED = ('approval', 'denial', 'rejection')

@lru_cache(maxsize=4)
def _settings(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding='utf-8'))

def settings(path: Path=SETTINGS) -> dict[str, Any]:
    return _settings(str(path), path.stat().st_mtime)

def _d(value: Any) -> date | None:
    m = _ISO.search(str(value or ''))
    if not m:
        return None
    try:
        return date.fromisoformat(m.group(0))
    except ValueError:
        return None

def us(value: Any) -> str:
    d = _d(value)
    return d.strftime('%m/%d/%Y') if d else str(value or '')

def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == 'resolved' and (fact.value not in (None, '')) else None

def notices(graph) -> list[dict[str, Any]]:
    """Application parser or workflow helper."""
    facts = graph.all_facts()
    out = []
    for key, fact in facts.items():
        if not key.startswith('folder.uscis_case.'):
            continue
        parts = key.split('.')
        receipt, slug = (parts[2], parts[3] if len(parts) > 3 else '')
        for s in fact.sources or []:
            if s.doc_id in getattr(graph, '_critical_notice_holds', set()):
                continue
            text = str(fact.value if fact.review is not None else s.normalized_value or '')
            if not text:
                continue
            kind = slug.split('_')[0] or next((k for w, k in _WORDS if w in text), 'notice')
            extra = {}
            for name in ('date', 'due', 'appointment', 'valid_to', 'addressed_to', 'priority_date', 'where', 'bring'):
                f = facts.get(f'folder.notice.{receipt}.{slug}.{name}') if slug else None
                extra[name] = (f.value if f.review is not None else next((x.normalized_value for x in f.sources if x.doc_id == s.doc_id), None)) if f else None
            when = extra['date'] or (_ISO.findall(text) or [None])[-1]
            form = text.split(' ', 1)[0] if re.match('[A-Z]{1,2}-\\d', text) else None
            out.append({'receipt': receipt, 'kind': kind, 'form': form, 'date': when, 'due': extra['due'], 'appointment': extra['appointment'], 'valid_to': extra['valid_to'], 'addressed_to': extra['addressed_to'], 'priority_date': extra['priority_date'], 'where': extra['where'], 'bring': [x for x in str(extra['bring'] or '').split('\n') if x.strip()] or None, 'doc': s.doc_id, 'text': text})
    seen, unique = (set(), [])
    for n in sorted(out, key=lambda n: (n['date'] or '', n['receipt'])):
        if (n['receipt'], n['kind'], n['date']) not in seen:
            seen.add((n['receipt'], n['kind'], n['date']))
            unique.append(n)
    return unique

def _docs(client_dir: Path) -> dict[str, list[str]]:
    meta = json.loads((client_dir / 'meta.json').read_text(encoding='utf-8')) if (client_dir / 'meta.json').exists() else {}
    by_type: dict[str, list[str]] = {}
    for doc, kind in (meta.get('classifications') or {}).items():
        by_type.setdefault(kind, []).append(doc)
    return by_type

def _status(client_dir: Path) -> dict[str, Any]:
    path = client_dir / 'status.json'
    return json.loads(path.read_text(encoding='utf-8')) if path.exists() else {}
_FIRM = schema_path.path('firm', 'firm_profile')
_NOT_NAMES = {'LLP', 'LLC', 'PLLC', 'PC', 'P.C.', 'LAW', 'OFFICE', 'OFFICES', 'GROUP', 'FIRM', 'ESQ', 'ESQ.'}

def _firm_words(path: Path=_FIRM) -> tuple[list[str], str | None]:
    """Application parser or workflow helper."""
    import settings
    facts = json.loads(path.read_text(encoding='utf-8')).get('facts', {}) if path.exists() else {}
    facts = settings.overlay('firm', settings.shipped(facts))
    core = [w for w in str(facts.get('firm.business_name') or '').upper().split() if w not in _NOT_NAMES]
    surname = (str(facts.get('firm.preparer_family_name') or '').upper().split() or [None])[-1]
    return (core, surname)

def representation(ns: list[dict]) -> dict[str, Any]:
    """Application parser or workflow helper."""
    core, surname = _firm_words()
    named = [n['addressed_to'] for n in ns if n.get('addressed_to')]
    if named and (not core) and (not surname):
        return {'who': 'unknown', 'text': 'The notices are addressed to ' + '; '.join(sorted(set(named))) + '. Save the office under Settings so the product can tell whether that is the firm.', 'others': []}
    ours = [a for a in named if core and all((w in a.upper() for w in core)) or (surname and surname in a.upper())]
    others = sorted({a for a in named if a not in ours})
    if ours:
        return {'who': 'firm', 'text': 'USCIS notices are addressed to the firm.', 'others': others}
    if others:
        return {'who': 'other', 'text': 'The notices are addressed to ' + '; '.join(others) + ', not the firm.', 'others': others}
    return {'who': 'none' if ns else None, 'text': "The notices don't name a representative." if ns else None, 'others': []}

def _latest_packet(client_dir: Path) -> dict[str, Any]:
    """Application parser or workflow helper."""
    built = [p for p in client_dir.glob('packet*.json') if not p.name.startswith('packet_choices') and (not p.name.endswith('_review_bundle.json'))]
    for path in sorted(built, key=lambda p: p.stat().st_mtime, reverse=True):
        try:
            manifest = json.loads(path.read_text(encoding='utf-8'))
        except (OSError, ValueError):
            continue
        if isinstance(manifest, dict) and manifest.get('built_at'):
            return manifest | {'filing': manifest.get('filing') or ('i485' if path.name == 'packet.json' else path.stem.split('_', 1)[1])}
    return {}

def _plus_years_d(d: date, years: int) -> date:
    try:
        return d.replace(year=d.year + years)
    except ValueError:
        return date(d.year + years, 3, 1)

@lru_cache(maxsize=16)
def _federal_holidays(year: int) -> frozenset[date]:
    """Application parser or workflow helper."""

    def nth(month: int, weekday: int, n: int) -> date:
        if n > 0:
            first = date(year, month, 1)
            return first + timedelta(days=(weekday - first.weekday()) % 7 + 7 * (n - 1))
        last = date(year, month, calendar.monthrange(year, month)[1])
        return last - timedelta(days=(last.weekday() - weekday) % 7)
    fixed = [date(year, 1, 1), date(year, 6, 19), date(year, 7, 4), date(year, 11, 11), date(year, 12, 25)]
    observed = {d - timedelta(days=1) if d.weekday() == 5 else d + timedelta(days=1) if d.weekday() == 6 else d for d in fixed}
    return frozenset(observed | {nth(1, 0, 3), nth(2, 0, 3), nth(5, 0, -1), nth(9, 0, 1), nth(10, 0, 2), nth(11, 3, 4)})

def _next_business_day(d: date) -> date:
    while d.weekday() >= 5 or d in _federal_holidays(d.year):
        d += timedelta(days=1)
    return d

def _after_decision(h: dict, result: dict, today: date, cfg: dict, deadlines: list, step, event) -> None:
    """Application parser or workflow helper."""
    decided = _d(result.get('decision_date'))
    if not decided:
        return
    rules = settings().get('court_decisions') or {}
    outcome, mid = (result['outcome'], h['id'] + '.decision')
    how = 'the written decision was mailed or sent' if result.get('written') else 'the oral decision'
    if outcome == 'Removal ordered in absentia':
        due = _next_business_day(decided + timedelta(days=rules['in_absentia_reopen_days']))
        deadlines.append(_deadline(mid + '.reopen', due, f"Motion to reopen the in absentia removal order of {us(decided)} for exceptional circumstances ({rules['in_absentia_reopen_days']} days, 8 CFR 1003.23(b)(4)(ii)); at any time for lack of notice or custody", 'attorney', today, cfg))
        step(mid + '.absentia', 'attorney', f"Removal was ordered in absentia on {us(decided)}: decide on a motion to reopen (exceptional circumstances within {rules['in_absentia_reopen_days']} days; lack of notice or custody at any time). That motion stays removal.", True)
        return
    if not result.get('appeal_waived'):
        days = rules['bia_appeal_days_asylum'] if result.get('asylum') else rules['bia_appeal_days']
        due = _next_business_day(decided + timedelta(days=days))
        who = 'the client' if outcome == 'Decision: removal ordered or relief denied' else 'DHS'
        if who == 'the client':
            deadlines.append(_deadline(mid + '.appeal', due, f'Appeal to the BIA (Form EOIR-26): {days} days from {how} on {us(decided)}: RECEIVED by the Board by this date (no mailbox rule), e-filed in ECAS, with the fee through the EOIR Payment Portal or Form EOIR-26A (8 CFR 1003.38)', 'attorney', today, cfg))
            step(mid + '.appeal', 'attorney', f"The judge ruled against the client on {us(decided)}: decide on an appeal to the BIA (due {us(due)}, received by the Board) or a motion. A motion doesn't stop removal; for a discretionary stay, ask DHS first (Matter of Herrera-Nunez, 29 I&N Dec. 691 (BIA 2026)). Build the appeal on the Filing packet tab → More… → Appeal to the BIA (EOIR-26 and EOIR-27).", True)
        else:
            event(due.isoformat(), f'DHS can appeal the {us(decided)} decision to the BIA until this date ({days} days, 8 CFR 1003.38)', None, 'future')
            step(mid + '.dhs', 'paralegal', f'After {us(due)}, check whether DHS appealed the {us(decided)} decision (EOIR case status: 1-800-898-7180) and record it.')
    if outcome == 'Decision: removal ordered or relief denied':
        if result.get('appeal_waived'):
            for kind, key in (('reconsider', 'reconsider_days'), ('reopen', 'reopen_days')):
                deadlines.append(_deadline(f'{mid}.{kind}', _next_business_day(decided + timedelta(days=rules[key])), f'Motion to {kind} with the immigration court ({rules[key]} days from the final order of {us(decided)}, appeal waived; 8 CFR 1003.23(b)(1))', 'attorney', today, cfg))
        else:
            step(mid + '.motions', 'attorney', f"If no appeal is taken, the {us(decided)} order becomes final when the appeal period ends: calendar the motions to reconsider ({rules['reconsider_days']} days) and reopen ({rules['reopen_days']} days) from that date (8 CFR 1003.23(b)(1)).")

def _court_offers(hearings: list[dict], records: list[dict], deadlines: list[dict], today: date) -> list[dict[str, Any]]:
    """Application parser or workflow helper."""
    filed, out = ({r.get('filing') for r in records}, [])
    latest = hearings[-1] if hearings else None
    if latest and latest.get('detained') and ('court_bond' not in filed) and ((latest.get('result') or {}).get('outcome') not in DECISIONS):
        out.append({'filing': 'court_bond', 'now': True, 'label': 'Bond request to the immigration judge: the client is detained (8 CFR 1003.19)'})
    decided = [h for h in hearings if (h.get('result') or {}).get('outcome') in DECISIONS[1:] and _d((h.get('result') or {}).get('decision_date'))]
    if not decided or 'court_motion' in filed:
        return out
    h = decided[-1]
    r, mid = (h['result'], h['id'] + '.decision')
    due = {d['id']: d for d in deadlines}
    if r['outcome'] == 'Removal ordered in absentia':
        d = due.get(mid + '.reopen')
        out.append({'filing': 'court_motion', 'now': True, 'label': 'Motion to reopen the in absentia order: ' + (f"by {us(_d(d['date']))} for exceptional circumstances; " if d and d['days_left'] >= 0 else '') + 'at any time for lack of notice or custody'})
    elif r.get('appeal_waived'):
        reconsider, reopen = (due.get(mid + '.reconsider'), due.get(mid + '.reopen'))
        if reopen and reopen['days_left'] >= 0:
            out.append({'filing': 'court_motion', 'now': True, 'label': 'Motion to reopen (by ' + us(_d(reopen['date'])) + ')' + (' or reconsider (by ' + us(_d(reconsider['date'])) + ')' if reconsider and reconsider['days_left'] >= 0 else '') + ' with the judge'})
    else:
        rules = settings().get('court_decisions') or {}
        decided_on = _d(r['decision_date'])
        appeal_ends = _next_business_day(decided_on + timedelta(days=rules['bia_appeal_days_asylum'] if r.get('asylum') else rules['bia_appeal_days']))
        if 'bia' not in filed and today <= appeal_ends + timedelta(days=rules['reopen_days']):
            out.append({'filing': 'court_motion', 'now': False, 'label': f"Motion to reopen or reconsider with the judge, if the attorney chooses it: {rules['reconsider_days']} and {rules['reopen_days']} days from the final order"})
    return out
TERMINATED = 'Case terminated or dismissed'

def in_court(status: dict[str, Any], nta: bool) -> bool:
    """Application parser or workflow helper."""
    hearings = (status.get('journey') or {}).get('hearings') or []
    ended = any(((h.get('result') or {}).get('outcome') == TERMINATED for h in hearings))
    return bool(nta or hearings) and (not ended)

@producer(ATTORNEY)
def i485_court_problem(client_dir: Path, graph=None) -> list[str]:
    """Application parser or workflow helper."""
    if graph is None:
        from review.state import reviewed_graph
        graph = reviewed_graph(client_dir)
    nta = bool(_value(graph, 'applicant.nta_present') or _docs(client_dir).get('notice_to_appear'))
    if not in_court(_status(client_dir), nta) or _value(graph, 'applicant.arriving_alien') == 'Yes':
        return []
    return ["The client is in removal proceedings: the immigration judge has exclusive jurisdiction over the I-485 (8 CFR 1245.2(a)(1)(i)). Ask the judge to terminate first (8 CFR 1003.18(d)(1)(ii)(B): prima facie eligible to adjust) and record the result on the case page, or file the I-485 with the court. An arriving alien is the exception (USCIS decides): answer 'applicant.arriving_alien' Yes."]

def _latest(ns: list[dict], form: str) -> dict | None:
    mine = [n for n in ns if n['form'] == form]
    return mine[-1] if mine else None

def _petitioner(graph) -> bool:
    """Application parser or workflow helper."""
    return bool(_value(graph, 'petitioner.status') or _value(graph, 'petitioner.family_name') or _value(graph, 'family.relationship'))

def track_of(graph, ns: list[dict], docs: dict[str, list[str]], filing: str | None=None) -> str:
    forms = {n['form'] for n in ns}
    category = str(_value(graph, 'applicant.filing_category') or '').lower()
    if _value(graph, 'vawa.classification') or filing == 'vawa':
        return 'vawa'
    if 'I-360' in forms or docs.get('sij_order') or docs.get('i360_approval') or _value(graph, 'applicant.i360_receipt_number') or ('juvenile' in category) or (_value(graph, 'applicant.public_charge_exemption') == 'SIJS'):
        return 'sij'
    if 'I-914' in forms or filing in ('i914', 'i914b') or any((k.startswith('tvisa.') and _value(graph, k) for k in graph.all_facts())):
        return 't_visa'
    if 'cuban adjustment' in category or 'hrifa' in category or filing == 'caa':
        return 'caa'
    if 'I-589' in forms or filing in ('i589', 'asylee') or 'asylee' in category or ('refugee' in category) or any((k.startswith('asylum.') and _value(graph, k) for k in graph.all_facts())):
        return 'asylum'
    if 'I-918' in forms or filing in ('u_visa', 'u_cert') or any((k.startswith('uvisa.') and _value(graph, k) for k in graph.all_facts())):
        return 'u_visa'
    if 'I-130' in forms or _petitioner(graph) or 'family' in category:
        return 'family'
    if 'N-400' in forms or filing == 'n400' or docs.get('green_card') or _value(graph, 'n400.lpr_date'):
        return 'naturalization'
    import daca
    if filing == 'daca' or daca.recipient(graph):
        return 'daca'
    import cuban_adjustment
    if cuban_adjustment.cuban(graph):
        return 'caa'
    return 'sij'

def _citizenship_stage(ns, filed) -> tuple[str, str] | None:
    """Application parser or workflow helper."""
    n400 = _latest(ns, 'N-400')
    if n400 and n400['kind'] == 'approval':
        return ('citizen', f"N-400 approved {us(n400['date'])}")
    if n400 and n400['kind'] != 'rejection':
        return ('n400_pending', f"N-400 {KIND_NAMES[n400['kind']]} {us(n400['date'])}")
    if filed == 'n400':
        return ('n400_pending', 'the firm recorded the N-400 as filed; no receipt notice in the folder yet')
    return None

def _asylum_stage(ns, docs, graph, filed, granted: dict | None=None) -> tuple[str, str]:
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i589 = (_latest(ns, 'I-485'), _latest(ns, 'I-589'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    applied = f"; green card application {KIND_NAMES[i485['kind']]} {us(i485['date'])}" if i485 else '; the firm recorded the green card application as filed' if filed == 'asylee' else ''
    if i589 and i589['kind'] == 'approval':
        return ('asylee', f"asylum granted {us(i589['date'])}" + applied)
    if granted:
        return ('asylee', granted['how'] + (f" {us(granted['date'])}" if granted.get('date') else '') + applied)
    if i589 and i589['kind'] != 'rejection':
        return ('asylum_pending', f"I-589 {KIND_NAMES[i589['kind']]} {us(i589['date'])}")
    if filed == 'i589':
        return ('asylum_pending', 'the firm recorded the I-589 as filed; no receipt notice in the folder yet')
    return ('asylum_ready', 'an asylum case: no I-589 notice in the folder yet')

def _vawa_stage(ns, docs, graph, filed) -> tuple[str, str]:
    """Application parser or workflow helper."""
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i360 = (_latest(ns, 'I-485'), _latest(ns, 'I-360'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if i485 and i485['kind'] != 'rejection':
        return ('i485_pending', f"I-485 {KIND_NAMES[i485['kind']]} {us(i485['date'])}")
    if i360 and i360['kind'] == 'approval' or docs.get('i360_approval'):
        return ('vawa_approved', 'I-360 approved' + (f" {us(i360['date'])}" if i360 and i360['kind'] == 'approval' else ''))
    if i360 and i360['kind'] not in ('rejection', 'denial'):
        return ('vawa_pending', f"I-360 {KIND_NAMES[i360['kind']]} {us(i360['date'])}")
    if filed == 'vawa':
        return ('vawa_pending', 'the firm recorded the VAWA self-petition as filed; no receipt notice in the folder yet')
    if _value(graph, 'vawa.classification'):
        return ('vawa_ready', 'a VAWA self-petition: no I-360 notice in the folder yet')
    return ('intake', "the VAWA questions aren't answered yet")

def _t_visa_stage(ns, docs, graph, filed) -> tuple[str, str]:
    """Application parser or workflow helper."""
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i914 = (_latest(ns, 'I-485'), _latest(ns, 'I-914'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if i914 and i914['kind'] == 'approval':
        return ('t_status', f"I-914 approved {us(i914['date'])}" + (f"; I-485 {KIND_NAMES[i485['kind']]} {us(i485['date'])}" if i485 else ''))
    if i914 and i914['kind'] != 'rejection':
        return ('t_pending', f"I-914 {KIND_NAMES[i914['kind']]} {us(i914['date'])}")
    if filed == 'i914':
        return ('t_pending', 'the firm recorded the I-914 as filed; no receipt notice in the folder yet')
    return ('t_ready', 'a T visa case: no I-914 notice in the folder yet')

def _naturalization_stage(ns, docs, graph, filed) -> tuple[str, str]:
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    if docs.get('green_card') or _value(graph, 'n400.lpr_date'):
        return ('resident', 'a green card (or the date the client became a resident) is in the case')
    return ('intake', 'no green card or USCIS notice in the folder yet')

def _caa_stage(ns, docs, graph, filed, today: date) -> tuple[str, str]:
    """Application parser or workflow helper."""
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    import cuban_adjustment
    i485 = _latest(ns, 'I-485')
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if i485 and i485['kind'] != 'rejection':
        return ('i485_pending', f"I-485 {KIND_NAMES[i485['kind']]} {us(i485['date'])}")
    if docs.get('green_card'):
        return ('resident', "a green card is in the folder (check that it is the client's)")
    if filed == 'caa':
        return ('i485_pending', 'the firm recorded the green card application as filed; no receipt notice in the folder yet')
    if _value(graph, 'applicant.filing_category') == cuban_adjustment.HRIFA:
        return ('caa_ready', 'a dependent of a principal HRIFA beneficiary')
    since, opens = (cuban_adjustment.present_since(graph), cuban_adjustment.eligible_on(graph))
    who = {'native': 'born in Cuba', 'citizen': 'a citizen of Cuba'}.get(cuban_adjustment.cuban(graph) or '', 'the Cuban Adjustment Act')
    if opens and today < opens:
        return ('caa_wait', f'{who}, in the U.S. since {us(since)}: one year on {us(opens)}')
    if opens:
        return ('caa_ready', f'{who}, in the U.S. since {us(since)}')
    return ('intake', f"{who}: the date of arrival isn't in the case yet")

def _u_requested(graph, records: list[dict]) -> str | None:
    """Application parser or workflow helper."""
    sent = [r['mailed_on'] for r in records if r.get('filing') == 'u_cert' and r.get('mailed_on')]
    return sent[-1] if sent else _value(graph, 'uvisa.cert_requested')

def _u_stage(ns, graph, filed, records) -> tuple[str, str]:
    """Application parser or workflow helper."""
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i918 = (_latest(ns, 'I-485'), _latest(ns, 'I-918'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if i918 and i918['kind'] == 'approval':
        return ('u_approved', f"I-918 approved {us(i918['date'])}")
    if i918 and i918['kind'] != 'rejection' or filed == 'u_visa':
        why = f"I-918 {KIND_NAMES[i918['kind']]} {us(i918['date'])}" if i918 else 'the firm recorded the I-918 as filed; no receipt notice in the folder yet'
        waiting = _value(graph, 'uvisa.waitlist_date') or _value(graph, 'uvisa.bfd_date')
        if waiting:
            return ('u_deferred', why + (f"; waiting-list notice {us(_value(graph, 'uvisa.waitlist_date'))}" if _value(graph, 'uvisa.waitlist_date') else f"; bona fide determination {us(_value(graph, 'uvisa.bfd_date'))}"))
        return ('u_pending', why)
    signed = _value(graph, 'uvisa.supb_signed')
    if signed:
        return ('u_ready', f'Supplement B signed {us(signed)}')
    requested = _u_requested(graph, records)
    if requested:
        return ('u_certification', f"certification requested {us(requested)}; the signed Supplement B hasn't come back yet")
    return ('u_certification', "a U visa case: the certification (Supplement B) isn't requested yet")

def _daca_stage(ns, graph, filed, filed_on, today: date) -> tuple[str, str]:
    """Application parser or workflow helper."""
    import daca
    i821d = _latest(ns, 'I-821D')
    if i821d and i821d['kind'] not in CLOSED:
        return ('daca_pending', f"I-821D {KIND_NAMES[i821d['kind']]} {us(i821d['date'])}")
    if filed == 'daca' and (not (i821d and (i821d['date'] or '') >= (filed_on or ''))):
        return ('daca_pending', 'the firm recorded the DACA renewal as filed; no receipt notice in the folder yet')
    w = daca.window(graph, today)
    if w:
        return ('daca_current', daca.window_text(w, today))
    return ('intake', "a DACA case: the current DACA expiration date isn't in the case yet (the I-797 approval notice or the work permit)")

def _visa(graph, today: date) -> dict[str, Any]:
    from fill.cover_letter import load_config, priority
    facts = {'country_of_birth': _value(graph, 'applicant.country_of_birth'), 'i360_priority_date': _value(graph, 'applicant.i360_priority_date')}
    values, problems = priority(facts, load_config(), today)
    return values | {'problems': problems}

def _sij_stage(ns, docs, graph, filed, visa) -> tuple[str, str]:
    """Application parser or workflow helper."""
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i360 = (_latest(ns, 'I-485'), _latest(ns, 'I-360'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if i485 and i485['kind'] != 'rejection':
        return ('i485_pending', f"I-485 {KIND_NAMES[i485['kind']]} {us(i485['date'])}")
    if docs.get('green_card'):
        return ('resident', "a green card is in the folder (check that it is the client's)")
    approved = i360 and i360['kind'] == 'approval' or docs.get('i360_approval')
    if approved:
        if filed == 'i485':
            return ('i485_pending', 'the firm recorded the I-485 as filed; no receipt notice in the folder yet')
        if visa.get('current') is False:
            return ('visa_wait', f"I-360 approved; priority date {us(visa['pd'])} not yet current ({visa['month']} Visa Bulletin)")
        why = 'I-360 approved' + (f"; priority date current in the {visa['month']} Visa Bulletin" if visa.get('current') else '; Visa Bulletin not set this month')
        return ('i485_ready', why)
    if i360 and i360['kind'] != 'rejection':
        return ('i360_pending', f"I-360 {KIND_NAMES[i360['kind']]} {us(i360['date'])}")
    if filed == 'i360':
        return ('i360_pending', 'the firm recorded the I-360 as filed; no receipt notice in the folder yet')
    if docs.get('sij_order') or _value(graph, 'sij.order_date'):
        return ('i360_ready', "the state court's SIJ order is in the folder")
    if docs.get('intake_questionnaire') or _value(graph, 'applicant.dob'):
        return ('state_court', 'no SIJ court order or USCIS notice in the folder yet')
    return ('intake', 'the folder has no questionnaire, court order or USCIS notice yet')

def _family_stage(ns, docs, graph, filed, pref: dict | None=None) -> tuple[str, str]:
    later = _citizenship_stage(ns, filed)
    if later:
        return later
    i485, i130 = (_latest(ns, 'I-485'), _latest(ns, 'I-130'))
    if i485 and i485['kind'] == 'approval':
        return ('resident', f"I-485 approved {us(i485['date'])}")
    if _value(graph, 'visa.entry_date'):
        return ('resident', f"entered the U.S. on the immigrant visa {us(_value(graph, 'visa.entry_date'))}")
    if _value(graph, 'visa.nvc_case_number'):
        return ('consular', f"NVC case {_value(graph, 'visa.nvc_case_number')}")
    if pref and pref.get('class') not in (None, 'IR') and i130 and (i130['kind'] not in ('rejection', 'denial')) and (not (i485 and i485['kind'] != 'rejection')) and (filed != 'family'):
        when = 'current: the green card application can be filed' if pref.get('current') else 'not yet current' if pref.get('current') is False else "the family Visa Bulletin isn't set for this month"
        return ('family_wait', f"{pref['text']}: {when}")
    if i130 and i130['kind'] == 'approval' and (not i485) and (filed != 'family'):
        return ('consular', f"I-130 approved {us(i130['date'])} and no I-485 in the folder: the visa goes through NVC and the consulate")
    latest = next((n for n in reversed(ns) if n['form'] in ('I-485', 'I-130') and n['kind'] != 'rejection'), None)
    if latest:
        return ('family_pending', f"{latest['form']} {KIND_NAMES[latest['kind']]} {us(latest['date'])}")
    if filed == 'family':
        return ('family_pending', 'the firm recorded the family packet as filed; no receipt notice in the folder yet')
    if filed == 'family_i130':
        return ('family_wait', 'the firm recorded the I-130 as filed alone; its receipt notice prints the priority date')
    if i130 or _petitioner(graph):
        return ('family_ready', "the petitioner's details are in the case")
    return ('intake', 'the folder has no petitioner details or USCIS notice yet')

def _level(days: int, cfg: dict) -> str:
    return 'overdue' if days < 0 else 'urgent' if days <= cfg['urgent_days'] else 'soon' if days <= cfg['soon_days'] else 'later'

def _birthday(dob: Any, years: int) -> date | None:
    """Application parser or workflow helper."""
    try:
        born = dob if isinstance(dob, date) else date.fromisoformat(str(dob)[:10])
    except ValueError:
        return None
    try:
        return born.replace(year=born.year + years)
    except ValueError:
        return date(born.year + years, 3, 1)

def _deadline(id_: str, when: date, what: str, owner: str, today: date, cfg: dict, source: str | None=None) -> dict[str, Any]:
    days = (when - today).days
    return {'id': id_, 'date': when.isoformat(), 'days_left': days, 'level': _level(days, cfg), 'what': what, 'owner': owner, 'source': source}

def _answered(n: dict, ns: list[dict]) -> bool:
    """Application parser or workflow helper."""
    return any((m['receipt'] == n['receipt'] and (m['date'] or '') > (n['date'] or '') and (m['kind'] in ('approval', 'denial', 'noid', 'rejection')) for m in ns))

def _waiver_offers(graph, ns: list[dict], hearings: list[dict], records: list[dict]) -> list[dict[str, Any]]:
    """Application parser or workflow helper."""
    refused = _value(graph, 'visa.result') == 'Refused'
    denied = any((n['form'] == 'I-485' and n['kind'] == 'denial' for n in ns))
    ordered = _value(graph, 'waiver.q29_final_order') == 'Yes' or any(((h.get('result') or {}).get('outcome') in ('Decision: removal ordered or relief denied', 'Removal ordered in absentia') for h in hearings))
    out = []
    for filing, name, key, reason in (('i601', 'Waiver of inadmissibility (I-601)', 'i601.situation', 'the consulate refused the visa' if refused else 'USCIS denied the I-485' if denied else None), ('i212', 'Permission to reapply after removal (I-212)', 'i212.situation', 'a removal order is on the case' if ordered else 'the consulate refused the visa' if refused else None)):
        chosen = _value(graph, key)
        if any((r.get('filing') == filing for r in records)) or not (chosen or reason):
            continue
        out.append({'filing': filing, 'now': bool(chosen), 'label': f'{name}: the attorney chose it' if chosen else f'{name}, if the attorney decides the client needs it: {reason}'})
    return out

def journey(client_dir: Path, today: date | None=None, graph=None, *, may_open=None) -> dict[str, Any]:
    """Application parser or workflow helper."""
    today = today or clock.today()
    if graph is None:
        from review.state import reviewed_graph
        graph = reviewed_graph(client_dir)
    import critical_review
    import subject_attribution
    pending_evidence = subject_attribution.pending_notices(client_dir) + critical_review.pending_notices(client_dir, graph)
    held_notices = getattr(graph, '_critical_notice_holds', set())
    if held_notices:
        from factgraph import FactGraph
        import document_instances
        graph = FactGraph.from_dict(graph.to_dict())
        document_instances.without_sources(graph, held_notices)
    s = settings()
    cfg = s['deadlines']
    status = _status(client_dir)
    marks = status.get('journey') or {}
    done = marks.get('done') or {}
    docs = _docs(client_dir)
    ns = notices(graph)
    import client_case
    client_case.apply_reads(ns, marks)
    packet = _latest_packet(client_dir)
    track = (marks.get('track') or {}).get('value') or track_of(graph, ns, docs, packet.get('filing'))
    records = status.get('filings') or []
    main = [r for r in records if r.get('filing') not in ('rfe', 'i914b', 'court_bond', 'court_motion')]
    filed = main[-1]['filing'] if main else packet.get('filing') or 'i485' if status.get('filed_at') else None
    filed_on = main[-1]['mailed_on'] if main else clock.day(status.get('filed_at')) or None
    visa = _visa(graph, today) if track == 'sij' else {}
    import asylee
    import preference
    granted = asylee.grant(graph, status) if track == 'asylum' else None
    pref = preference.status(graph, today) if track == 'family' else None
    filed_family = 'family_i130' if filed == 'family' and main and (main[-1].get('variant') == 'petition_only') else filed
    found, why = _sij_stage(ns, docs, graph, filed, visa) if track == 'sij' else _family_stage(ns, docs, graph, filed_family, pref) if track == 'family' else _asylum_stage(ns, docs, graph, filed, granted) if track == 'asylum' else _u_stage(ns, graph, filed, records) if track == 'u_visa' else _t_visa_stage(ns, docs, graph, filed) if track == 't_visa' else _caa_stage(ns, docs, graph, filed, today) if track == 'caa' else _vawa_stage(ns, docs, graph, filed) if track == 'vawa' else _daca_stage(ns, graph, filed, filed_on, today) if track == 'daca' else _naturalization_stage(ns, docs, graph, filed)
    oath = marks.get('oath') or {}
    oath_day = _d(oath.get('date'))
    if found == 'citizen':
        if oath_day and oath_day <= today:
            why += f'; oath taken {us(oath_day)}'
        else:
            found, why = ('oath', why + (f'; oath ceremony {us(oath_day)}' if oath_day else "; the oath ceremony date isn't recorded yet"))
    chosen = marks.get('stage') or {}
    stages = s['stages'][track]
    if track == 'family' and 'consular' not in (found, chosen.get('value')) and (not (_value(graph, 'visa.nvc_case_number') or _value(graph, 'visa.entry_date'))):
        stages = [x for x in stages if x != 'consular']
    import path as own_path
    template, own = (list(stages), own_path.approved(client_dir))
    if own:
        stages = [x['id'] for x in own['steps']]
    stage = chosen.get('value') if chosen.get('value') in stages else own_path.place(found, s['stages'][track], stages) if own else found
    tstage = stage if stage in template else found if found in template else template[0]
    dob = _value(graph, 'applicant.dob')
    nta = bool(_value(graph, 'applicant.nta_present') or docs.get('notice_to_appear'))
    timeline: list[dict[str, Any]] = []

    def event(when: Any, what: str, doc: str | None=None, kind: str='event') -> None:
        if _d(when):
            timeline.append({'date': _d(when).isoformat(), 'what': what, 'doc': doc, 'kind': kind})
    event(dob, 'Born')
    event(_value(graph, 'applicant.last_arrival_date') or _value(graph, 'applicant.i94_arrival_date') or _value(graph, 'applicant.last_arrival_date_self_reported'), 'Entered the United States')
    event(_value(graph, 'sij.order_date'), "State court's SIJ order" + (f" ({_value(graph, 'sij.court_name')})" if _value(graph, 'sij.court_name') else ''), (docs.get('sij_order') or [None])[0], 'court')
    event(_value(graph, 'applicant.marriage_date'), 'Married', (docs.get('marriage_certificate') or [None])[0])
    for n in ns:
        what = f"{n['form'] or 'USCIS'} {KIND_NAMES[n['kind']]} ({n['receipt']})"
        event(n['date'], what, n['doc'], 'uscis')
        if n['appointment']:
            event(n['appointment'], f"{('Interview' if n['kind'] == 'interview' else 'Biometrics appointment')} {n['appointment'][11:]}".strip(), n['doc'], 'appointment')
    for r in records:
        if r.get('online'):
            event(r['mailed_on'], f"Filed online in the USCIS online account: {r.get('title') or r['filing']} (" + (f"receipt {r['receipt']}" if r.get('receipt') else 'receipt number not added yet') + f"), recorded by {r['by']}" + (f". Filed despite: {', '.join(r['failed_checks'])} ({r['override']})" if r.get('override') else ''), None, 'filed')
            continue
        to = f", to {r['mail_to'][2]}" if r.get('mail_to') and len(r['mail_to']) > 2 else ''
        online = r.get('carrier') == 'Online'
        event(r['mailed_on'], f"{('Filed online' if online else 'Mailed')}: {r.get('title') or r['filing']} ({('confirmation' if online else r.get('carrier') or 'carrier not recorded')}{(' ' + r['tracking'] if r.get('tracking') else '')}{to}), recorded by {r.get('by') or '?'}" + (f". Mailed despite: {', '.join(r['failed_checks'])} ({r['override']})" if r.get('override') else ''), None, 'filed')
    if status.get('filed_at') and (not records):
        event(status['filed_at'], f"Filed by the firm ({(packet.get('filing') or 'i485').upper().replace('FAMILY', 'family packet')}), recorded by {status.get('filed_by') or '?'}", None, 'filed')
    if track == 'u_visa':
        if not any((r.get('filing') == 'u_cert' for r in records)):
            event(_value(graph, 'uvisa.cert_requested'), 'U visa certification (Supplement B) requested from the certifying agency')
        event(_value(graph, 'uvisa.supb_signed'), 'Supplement B signed by the certifying official')
        event(_value(graph, 'uvisa.supb_received'), 'The signed Supplement B reached the office')
        event(_value(graph, 'uvisa.bfd_date'), 'USCIS: bona fide determination (work permit and deferred action)', None, 'uscis')
        event(_value(graph, 'uvisa.waitlist_date'), 'USCIS: placed on the U visa waiting list (8 CFR 214.14(d)(2))', None, 'uscis')
    if nta:
        timeline.append({'date': None, 'what': 'Notice to Appear (immigration court) in the folder', 'doc': (docs.get('notice_to_appear') or [None])[0], 'kind': 'court'})
    import case_status
    status_events, status_steps = case_status.for_journey(client_dir, ns)
    timeline += status_events
    timeline.sort(key=lambda e: e['date'] or '9999')
    deadlines: list[dict[str, Any]] = []
    steps: list[dict[str, Any]] = []

    def step(id_: str, owner: str, text: str, urgent: bool=False) -> None:
        steps.append({'id': id_, 'owner': owner, 'text': text, 'urgent': urgent, 'done': done.get(id_)})
    if track == 'sij' and template.index(tstage) < template.index('i360_pending'):
        from i360 import turns_21
        t21 = turns_21(dob)
        if t21:
            deadlines.append(_deadline('age_21', t21 - timedelta(days=1), 'File the I-360 before the 21st birthday', 'attorney', today, cfg))
        else:
            step('dob', 'paralegal', 'No date of birth in the case: the I-360 must be filed before the 21st birthday. Add the birth certificate or passport.', True)
    if track == 'vawa' and stage in ('intake', 'vawa_ready'):
        import vawa
        last = vawa.deadline(graph, today)
        if last:
            deadlines.append(_deadline('vawa_deadline', last['date'], f"File the VAWA self-petition: {last['why']}", 'attorney', today, cfg, last['cite']))
    if track == 'asylum':
        import asylum
        if template.index(tstage) < template.index('asylum_pending'):
            y = asylum.one_year(graph, today)
            if y.get('deadline') and _value(graph, 'asylum.uac') != 'Yes':
                deadlines.append(_deadline('one_year', _d(y['deadline']), 'File the I-589: 1 year from the last arrival. USCIS must receive it by this date', 'attorney', today, cfg))
            if y['level'] in ('late', 'check') or y.get('minor_at_arrival'):
                step('one_year', 'attorney', y['text'], y['level'] in ('late', 'check'))
        if stage == 'asylum_pending' and filed_on and _d(filed_on):
            ead = _d(filed_on) + timedelta(days=asylum.settings()['ead_clock_days'])
            event(ead.isoformat(), 'Can apply for a work permit, category (c)(8): 150 days after the complete I-589 was filed', None, 'future')
        if stage == 'asylum_pending' and nta and _latest(ns, 'I-589') and (_value(graph, 'asylum.court') != 'now'):
            step('asylum_referral', 'attorney', "A Notice to Appear came after the I-589 was filed with USCIS: the asylum office referred the case to the immigration court (8 CFR 208.14(c)(1)): the same I-589 goes to the judge with the Notice to Appear. File the EOIR-28, enter the first hearing, and answer 'now' to the I-589's court question (Part A.I, 18).", True)
        if stage == 'asylee' and granted and granted.get('date'):
            refugee = granted['category'] == asylee.REFUGEE
            event(_plus_years_d(granted['date'], asylum.settings()['asylee_adjustment_years']).isoformat(), 'Must apply for a green card (I-485): 1 year after admission as a refugee (8 CFR 209.1)' if refugee else 'Can apply for a green card (I-485): 1 year after asylum was granted (8 CFR 209.2)', None, 'future')
            import i730
            i730_filed = any((r.get('filing') == 'i730' for r in records)) or any((n['form'] == 'I-730' for n in ns))
            if not i730_filed and (not done.get('i730')):
                deadlines.append(_deadline('i730', i730.last_day(granted['date']), "Bring the client's spouse and unmarried children under 21: USCIS must receive a Form I-730 for each within 2 years of " + ('the admission as a refugee (8 CFR 207.7(d))' if refugee else 'the grant of asylum (8 CFR 208.21(d))'), 'attorney', today, cfg))
                step('i730', 'attorney', "Ask the client about a spouse or unmarried child under 21 who didn't receive the status with them: file a Form I-730 for each (Filing packet tab → More… → Relative petition (I-730)). Mark done if there is none.")
    import cuban_adjustment
    hrifa = _value(graph, 'applicant.filing_category') == cuban_adjustment.HRIFA
    caa_opens = cuban_adjustment.eligible_on(graph) if track == 'caa' and (not hrifa) else None
    if caa_opens and stage in ('intake', 'caa_wait', 'caa_ready'):
        event(caa_opens.isoformat(), 'Can apply for a green card under the Cuban Adjustment Act (I-485): one year in the U.S. (8 CFR 245.2(a)(2)(ii))', None, 'future')
    if track == 't_visa' and stage == 't_status':
        ends = _d(next((n['valid_to'] for n in reversed(ns) if n['form'] == 'I-914' and n['kind'] == 'approval'), None))
        if ends and (not any((n['form'] == 'I-485' for n in ns))):
            deadlines.append(_deadline('t_status_ends', ends, 'T status ends: file the green card application (I-485 under INA 245(l)) before it does (8 CFR 214.203(c), 245.23(a))', 'attorney', today, cfg))
        elif not ends:
            step('t_status_ends', 'paralegal', 'Record when T status ends (the validity dates on the I-914 approval notice): the green card application must be filed before then (8 CFR 214.203(c)).', True)
    if track == 'u_visa':
        from u_visa import supb_last_day
        signed = _d(_value(graph, 'uvisa.supb_signed'))
        if signed and stage in ('u_certification', 'u_ready'):
            deadlines.append(_deadline('u_supb', supb_last_day(signed), f'File the U petition (I-918): USCIS must receive it while the Supplement B signed {us(signed)} is valid (six months from the signature: 8 CFR 214.14(c)(2)(i))', 'attorney', today, cfg))
    open_denials = []
    for n in ns:
        label = f"{n['form'] or 'USCIS'} {n['receipt']}"
        mark = f"{n['receipt']}.{n['kind']}.{n['date']}"
        if n['kind'] in ('rfe', 'noid') and (not _answered(n, ns)) and (not done.get(mark)):
            if _d(n['due']):
                deadlines.append(_deadline(mark, _d(n['due']), f"Answer the {KIND_NAMES[n['kind']]} on {label}", 'attorney', today, cfg, n['doc']))
            step(mark, 'attorney', f"Answer the {KIND_NAMES[n['kind']]} on {label} (notice of {us(n['date'])})" + ('' if _d(n['due']) else ". The due date wasn't read: take it from the notice today") + ". Build the response on this case's 'Where the case stands' tab.", True)
        if n['kind'] == 'denial' and (not _answered(n, ns)) and (not done.get(mark)) and _d(n['date']):
            last = _d(n['date']) + timedelta(days=cfg['motion_or_appeal_days'] + cfg['motion_or_appeal_mail_days'])
            open_denials.append((n, last))
            if n['form'] == 'N-400':
                deadlines.append(_deadline(mark, last, f'Request a hearing on the denied {label} (Form N-336): 30 days from receiving the denial (33 from a mailed notice): confirm the date it was received', 'attorney', today, cfg, n['doc']))
                step(mark, 'attorney', f"{label} was denied on {us(n['date'])}: decide on a hearing request (Form N-336, 8 CFR 336.2) or reapplying: Filing packet tab → More… → Hearing on a denied N-400 (N-336).", True)
            else:
                deadlines.append(_deadline(mark, last, f'Motion or appeal on the denied {label} (Form I-290B: 30 days, 33 if mailed. Confirm on the decision)', 'attorney', today, cfg, n['doc']))
                step(mark, 'attorney', f"{label} was denied on {us(n['date'])}: decide on a motion or appeal (the decision says which is available). Filing packet tab → More… → Motion or appeal after a USCIS denial (I-290B).", True)
        if n['kind'] == 'rejection' and (not _answered(n, ns)) and (not done.get(mark)):
            step(mark, 'paralegal', f"USCIS returned the {label} filing on {us(n['date'])} (rejected, not decided): correct the problem it names and refile.", True)
        if n['kind'] in ('biometrics', 'interview') and _d(n['appointment']) and (_d(n['appointment']) >= today):
            who = 'attorney' if n['kind'] == 'interview' else 'paralegal'
            deadlines.append(_deadline(mark, _d(n['appointment']), f"{('Interview' if n['kind'] == 'interview' else 'Biometrics appointment')} on {label}" + (f" at {n['appointment'][11:]}" if len(n['appointment']) > 10 else ''), 'client', today, cfg, n['doc']))
            step(mark, who, 'Prepare the client for the interview and confirm they will attend.' if n['kind'] == 'interview' else 'Confirm the client knows about the biometrics appointment and will bring the notice and a photo ID.')
        if n['kind'] in ('biometrics', 'interview') and (not n['appointment']) and (not _answered(n, ns)):
            step(mark, 'paralegal', f"The {KIND_NAMES[n['kind']]} on {label} ({us(n['date'])}): the appointment date wasn't read. Add it from the notice.")
    for x in status_steps:
        step(x['id'], x['owner'], x['text'], x['urgent'])
    if main and main[-1].get('online') and (not main[-1].get('receipt')) and _d(filed_on):
        import online_filing
        since = (today - _d(filed_on)).days
        if since >= online_filing.rules()['accept_within_days']:
            step('no_receipt', 'paralegal', f"Filed online {us(filed_on)}, {since} days ago, and no receipt number added yet: look at the case card in the firm's USCIS online account (a rejection shows under Case Status, and its notice comes by mail), then add the receipt number on the packet tab.", True)
    if filed_on and _d(filed_on) and (not (main and main[-1].get('online'))):
        since = (today - _d(filed_on)).days
        filed_forms = {'i360': {'I-360'}, 'i485': {'I-485'}, 'family': {'I-130', 'I-485'}, 'n400': {'N-400'}, 'i589': {'I-589'}, 'i90': {'I-90'}, 'i131': {'I-131'}, 'n600': {'N-600'}, 'i751': {'I-751'}, 'caa': {'I-485'}, 'vawa': {'I-360', 'I-485'}, 'i914': {'I-914'}, 'u_visa': {'I-918'}, 'daca': {'I-821D', 'I-765'}, 'i730': {'I-730'}, 'n565': {'N-565'}, 'tps': {'I-821', 'I-765'}, 'parole': {'I-131'}}.get(filed or '')
        if filed_forms and since >= cfg['receipt_expected_days'] and (not any((n['form'] in filed_forms and n['date'] and (n['date'] >= filed_on) for n in ns))):
            track_no = f" (tracking {main[-1]['tracking']})" if main and main[-1].get('tracking') else ''
            step('no_receipt', 'paralegal', f'Mailed {us(filed_on)}{track_no}, {since} days ago, and no receipt notice in the folder: check the delivery, then for a rejection, lost mail or a payment problem.', True)
    ead = _d(_value(graph, 'applicant.ead_expiration_date')) or max((_d(n['valid_to']) for n in ns if _d(n['valid_to'])), default=None)
    resident_or_later = stage in ('resident', 'n400_pending', 'oath', 'citizen')
    import daca
    daca_case = track == 'daca' or daca.recipient(graph)
    if ead and (not resident_or_later) and (not daca_case):
        opens = ead - timedelta(days=cfg['ead_renewal_days_before'])
        if today >= opens:
            deadlines.append(_deadline('ead', ead, f'Work permit expires (renewal can be filed since {us(opens)})', 'paralegal', today, cfg))
        else:
            event(opens.isoformat(), f'Work permit renewal can be filed (card expires {us(ead)})', None, 'future')
    dw = daca.window(graph, today) if daca_case and (not resident_or_later) and (stage != 'daca_pending') and (not daca.renewal_pending(graph)) else None
    if dw:
        end = us(dw['expires'])
        if dw['kind'] == 'early':
            event(dw['opens'].isoformat(), f'DACA renewal can be filed: 150 days before DACA expires on {end} (not earlier: USCIS may reject it)', None, 'future')
        elif dw['kind'] == 'open':
            deadlines.append(_deadline('daca', dw['file_by'], f'File the DACA renewal (I-821D + I-765): USCIS asks for it 150 to 120 days before DACA expires on {end}', 'paralegal', today, cfg))
        elif dw['kind'] == 'late':
            deadlines.append(_deadline('daca', dw['expires'], 'DACA and the work permit expire: file the renewal now (USCIS asks for it 150 to 120 days before)', 'paralegal', today, cfg))
        elif dw['kind'] == 'expired':
            deadlines.append(_deadline('daca', dw['renewal_until'], f'Last day to file DACA as a renewal: one year after it expired on {end} (the client has no DACA or work permit until a renewal is approved)', 'attorney', today, cfg))
        else:
            step('daca.initial', 'attorney', f'DACA expired {end}, more than one year ago: a new request is an initial request. {daca.STATUS}', True)
    lpr = _d((marks.get('lpr_date') or {}).get('value')) or next((_d(n['date']) for n in reversed(ns) if n['form'] == 'I-485' and n['kind'] == 'approval'), None) or _d(_value(graph, 'visa.entry_date')) or _d(_value(graph, 'n400.lpr_date')) or (_d(_value(graph, 'petitioner.lpr_date')) if track == 'naturalization' else None)
    citizenship = None
    if stage == 'resident' and lpr:
        spouse = _value(graph, 'n400.basis') == 'Spouse of U.S. citizen' or (_value(graph, 'family.relationship') == 'spouse' and 'citizen' in str(_value(graph, 'petitioner.status') or '').lower())
        years = cfg.get('naturalization_years_spouse', 3) if spouse else cfg['naturalization_years']
        anniversary = lpr.replace(year=lpr.year + years) if not (lpr.month == 2 and lpr.day == 29) else date(lpr.year + years, 3, 1)
        citizenship = (anniversary - timedelta(days=cfg['naturalization_early_days'])).isoformat()
        event(citizenship, f'Can apply for citizenship (Form N-400): {years} years as a resident' + (' married to and living with the U.S. citizen spouse' if spouse else '') + ', less 90 days', None, 'future')
        if track == 'sij':
            step('sij_parents', 'attorney', 'Tell the client: a resident through SIJ can never petition for a natural or prior adoptive parent (INA 101(a)(27)(J)(iii)(II)).')
        married = _d(_value(graph, 'applicant.marriage_date'))
        conditional = _d(_value(graph, 'i751.card_expires')) or (_plus_years_d(lpr, 2) if track == 'family' and married and ((lpr - married).days < 730) else None)
        if conditional and (not any((n['form'] == 'I-751' for n in ns))):
            deadlines.append(_deadline('i751', conditional, f'Conditional green card: file Form I-751 from {us(conditional - timedelta(days=90))} (90 days before it expires on {us(conditional)}), not earlier for a joint petition', 'attorney', today, cfg))
    elif stage == 'resident':
        step('lpr_date', 'paralegal', 'Record the date the client became a resident (on the approval notice or the card): the citizenship date is counted from it.')
    card = _d(_value(graph, 'i90.card_expires'))
    if card and resident_or_later and (stage != 'citizen') and (not any((n['form'] == 'I-90' and n['date'] and (n['date'] >= (card - timedelta(days=365)).isoformat()) for n in ns))):
        months = cfg.get('card_renewal_months_before', 6)
        y, m = (card.year - (card.month <= months), (card.month - months - 1) % 12 + 1)
        opens = date(y, m, min(card.day, calendar.monthrange(y, m)[1]))
        if today >= opens:
            deadlines.append(_deadline('i90', card, f'Green card expires: renew with Form I-90 (can be filed since {us(opens)})', 'paralegal', today, cfg))
        else:
            event(opens.isoformat(), f'Green card renewal (Form I-90) can be filed (card expires {us(card)})', None, 'future')
    if nta and stage != 'citizen':
        step('nta', 'attorney', 'A Notice to Appear is in the folder. While removal proceedings are pending, the immigration judge (not USCIS) decides the I-485 (unless the client is an arriving alien: 8 CFR 245.2(a)(1)). Check the court case before filing.', True)
    hearings = sorted(marks.get('hearings') or [], key=lambda h: (h['date'], h.get('time') or ''))
    first = None
    if nta and stage != 'citizen':
        import court
        first = court.nta_hearing(graph)
    if first and _d(first['date']) and (not any((h['date'] == first['date'] for h in hearings))):
        where = f" ({first['place']})" if first.get('place') else ''
        label = 'first hearing set by the Notice to Appear' + (f" at {first['time']}" if first.get('time') else '') + where
        event(first['date'], 'Immigration court: ' + label + ' (read from the Notice to Appear, not confirmed yet)', first.get('source'), 'court')
        step('hearing.nta', 'paralegal', f"The Notice to Appear sets a first hearing for {us(first['date'])}" + (f" at {first['time']}" if first.get('time') else '') + ': check it against the court (EOIR case status, 1-800-898-7180), then add it as a hearing under Immigration court on this page. The client sees it once it is added.', True)
        if _d(first['date']) >= today:
            deadlines.append(_deadline('hearing.nta', _d(first['date']), f'Immigration court, not confirmed yet: {label}: the client must attend (read from the Notice to Appear)', 'client', today, cfg))
    for h in hearings:
        where = ', '.join((x for x in (h.get('court'), f"Judge {h['judge']}" if h.get('judge') else None) if x))
        label = f"{h['kind']} hearing" + (f" at {h['time']}" if h.get('time') else '') + (f' ({where})' if where else '')
        result = h.get('result') or {}
        said = ': '.join((x for x in (result.get('outcome') if result.get('outcome') != 'Other' else None, result.get('text')) if x))
        unconfirmed = bool(h.get('source')) and (not h.get('confirmed'))
        event(h['date'], 'Immigration court: ' + label + (f': {said}' if said else '') + (' (read from the hearing notice, not confirmed yet)' if unconfirmed else ''), h.get('source'), 'court')
        if unconfirmed:
            step(h['id'] + '.confirm', 'paralegal', f"A hearing was read from the court's notice ({us(h['date'])}" + (f" at {h['time']}" if h.get('time') else '') + '): check the date, time and place against the notice, then press Confirm under Immigration court on this page. The client sees it once it is confirmed.', True)
        _after_decision(h, result, today, cfg, deadlines, step, event)
        when = _d(h['date'])
        if when >= today and (not h.get('result')):
            deadlines.append(_deadline(h['id'], when, f'Immigration court: {label}: the client must attend' + (' (read from the hearing notice, not confirmed yet)' if unconfirmed else ''), 'client', today, cfg))
            individual = h['kind'].startswith('Individual')
            days = (cfg.get('court_filing_days_before') or {}).get('individual' if individual else 'master')
            if h.get('detained'):
                step(h['id'] + '.filings', 'attorney', f"Detained: the court sets the filing deadlines for the {us(h['date'])} hearing: calendar them from its order or notice.")
            elif days and h['kind'] in ('Master calendar', 'Individual (merits)'):
                due = _next_business_day(when - timedelta(days=days))
                deadlines.append(_deadline(h['id'] + '.filings', due, f"Court filings due for the {us(h['date'])} {h['kind'].lower()} hearing ({days} days before" + ('' if individual else ', when asking for a ruling at or before it') + f"; {cfg.get('court_filing_source') or 'Immigration Court Practice Manual 3.1(b)'})" + (' (the hearing was read from its notice, not confirmed yet)' if unconfirmed else ''), 'attorney', today, cfg))
            else:
                step(h['id'] + '.filings', 'attorney', f"Calendar any court filing deadline for the {us(h['date'])} {h['kind'].lower()} hearing (Immigration Court Practice Manual, Chapter 3.1(b), or the court's order).")
            step(h['id'] + '.prepare', 'attorney', f"Prepare the client for the {us(h['date'])} {h['kind'].lower()} hearing; confirm an interpreter and that they will attend.")
        elif when < today and (not h.get('result')):
            step(h['id'] + '.result', 'paralegal', f"Record what happened at the {us(h['date'])} hearing: the next date, or the judge's decision.", True)
    if oath_day:
        event(oath_day.isoformat(), 'Citizenship oath ceremony' + (f" at {oath['time']}" if oath.get('time') else '') + (f" ({oath['place']})" if oath.get('place') else ''), None, 'appointment')
        if oath_day >= today:
            deadlines.append(_deadline('oath', oath_day, 'Citizenship oath ceremony: the client must attend; citizenship begins there', 'client', today, cfg))
    elif stage == 'oath':
        step('oath.record', 'paralegal', 'Record the oath ceremony from the notice (Form N-445) on this page: date, time and place.', True)
    open_move = False
    for mv in marks.get('moves') or []:
        moved_on = _d(mv['date'])
        event(mv['date'], 'Moved' + (f" to {mv['address']}" if mv.get('address') else '') + f" (recorded by {mv['by']})", None, 'event')
        open_move = open_move or not done.get(mv['id'] + '.ar11')
        if not done.get(mv['id'] + '.ar11'):
            deadlines.append(_deadline(mv['id'] + '.ar11', moved_on + timedelta(days=cfg.get('ar11_days', 10)), 'Report the new address to USCIS: Form AR-11 (online or on paper) and a change of address on every pending USCIS case (8 CFR 265.1)', 'paralegal', today, cfg))
            step(mv['id'] + '.ar11', 'paralegal', f"The client moved on {us(mv['date'])}: file Form AR-11 and change the address on each pending USCIS case (Filing packet tab → More… → Change of address). Mark done when filed.", True)
        if (nta or marks.get('hearings')) and stage != 'citizen' and (not done.get(mv['id'] + '.eoir33')):
            open_move = True
            deadlines.append(_deadline(mv['id'] + '.eoir33', moved_on + timedelta(days=cfg.get('eoir33_days', 5)), 'Tell the immigration court: Form EOIR-33 within 5 days of the move (8 CFR 1003.15(d)(2)). A notice sent to an old address can lead to an order made without the client', 'attorney', today, cfg))
            step(mv['id'] + '.eoir33', 'attorney', f"The client moved on {us(mv['date'])} and has a court case: file Form EOIR-33 with the court (Filing packet tab → More… → Change of address). Mark done when filed.", True)
    for key, what in VISA_EVENTS:
        event(_value(graph, key), what, None, 'uscis')
    interview = _d(_value(graph, 'visa.interview_date'))
    consulate = _value(graph, 'visa.consulate')
    if interview and interview >= today and (not _value(graph, 'visa.result')):
        deadlines.append(_deadline('visa.interview', interview, f"Immigrant visa interview{(' at ' + consulate if consulate else '')}: the client must attend, with the medical exam done", 'client', today, cfg))
        step('visa.prepare', 'attorney', f'Prepare the client for the {us(interview)} consulate interview: original documents, the medical exam with the panel physician.')
    elif interview and interview < today and (not _value(graph, 'visa.result')):
        step('visa.result', 'paralegal', f'Record the result of the {us(interview)} consulate interview (issued, or refused under 221(g) with what is asked).', True)
    if _value(graph, 'visa.result') == 'Issued' and (not _value(graph, 'visa.entry_date')):
        step('visa.enter', 'attorney', 'The visa was issued: the client must enter the U.S. before it expires (the date on the visa), and pay the USCIS Immigrant Fee so the green card is mailed. Record the date of entry.', True)
    eoir28_filed = any((r.get('filing') == 'eoir28' for r in records))
    if (nta or hearings) and stage != 'citizen' and (not eoir28_filed):
        step('eoir28', 'attorney', "File the EOIR-28 (the attorney's appearance) with the immigration court: in the EOIR portal for an ECAS case. Build it on the Filing packet tab → More… → Immigration court appearance (EOIR-28).", bool(hearings))
    if track == 'sij' and (not resident_or_later) and in_court(status, nta):
        if stage in ('i485_ready', 'i485_pending'):
            step('sij_court', 'attorney', 'In removal proceedings: only the judge can decide the I-485 (8 CFR 1245.2(a)(1)(i); an arriving alien excepted). Move to terminate (8 CFR 1003.18(d)(1)(ii)(B): prima facie eligible to adjust) so the I-485 goes to USCIS. Ask ICE (OPLA) to join: a joint or unopposed motion must be granted (1003.18(d)(1)(i)(G)), or file the I-485 with the court.', True)
        else:
            step('sij_court', 'attorney', 'In removal proceedings: the I-360 is still filed with USCIS. Once it is filed, ask the judge to terminate (8 CFR 1003.18(d)(1)(ii)(B)) or to administratively close the case (1003.18(c)) while USCIS decides it and the visa date waits. Ask ICE (OPLA) to join: a joint or unopposed motion must be granted (1003.18(d)(1)(i)(G), (c)(3)).', stage in ('i360_pending', 'visa_wait'))
    if track == 'sij' and (not resident_or_later) and _value(graph, 'applicant.marriage_date'):
        step('sij_married', 'attorney', f"The client married on {us(_value(graph, 'applicant.marriage_date'))}. SIJ requires the client to be unmarried until the green card is approved: decide the case's path before anything else is filed.", True)
    if track == 'sij' and stage in ('visa_wait', 'i485_ready'):
        for p in visa.get('problems') or []:
            step(f'visa_bulletin.{today:%Y-%m}' if p.startswith(("Set this month's Visa Bulletin", 'The Visa Bulletin (EB-4) is set')) else 'visa', 'attorney', p, True)
    if track == 'family' and stage == 'family_wait' and pref:
        for p in pref.get('problems') or []:
            step(f'visa_bulletin.family.{today:%Y-%m}' if p.startswith(("Set this month's family Visa Bulletin", 'The family Visa Bulletin is set')) else 'family_pd', 'attorney', p, True)
    court = (s.get('state_court') or {}).get(str(_value(graph, 'applicant.physical_state') or '').upper()) if track == 'sij' else None
    if court and stage in ('intake', 'state_court') and court.get('order_before_age') and dob:
        before = _birthday(dob, court['order_before_age'])
        if before and before > today:
            deadlines.append(_deadline(f"age_{court['order_before_age']}_state", before - timedelta(days=1), court['deadline'], 'attorney', today, cfg, court.get('_source')))
        elif before:
            step('state_court_age', 'attorney', court['too_late'], True)
    stage_steps = court['steps'] if court and stage == 'state_court' and court.get('steps') else s['steps'].get(stage, [])
    for i, (owner, text) in enumerate(stage_steps):
        step(f'{stage}.{i}', owner, text)
    cancel_offer = None
    if in_court(status, nta) and stage != 'citizen' and (not any((r.get('filing') == 'cancellation' for r in records))):
        import cancellation
        picked = _value(graph, 'cancel.form')
        resident = picked == cancellation.FORMS[1] if picked else bool(lpr or docs.get('green_card'))
        form = 'EOIR-42A' if resident else 'EOIR-42B'
        cancel_offer = {'filing': 'cancellation', 'now': bool(picked), 'label': f'Cancellation of removal ({form}): the attorney chose it' if picked else f"Cancellation of removal{(' for a permanent resident' if resident else '')} ({form}), if the attorney chooses it"}
        if picked:
            step('cancellation', 'attorney', f'Cancellation of removal ({form}): pay the EOIR fee and the biometrics fee, send the USCIS package, then file with the court and serve ICE. Build it on the Filing packet tab → More… → Cancellation of removal.')
            merits = next((h for h in hearings if h['kind'].startswith('Individual') and (not h.get('result')) and (not h.get('detained')) and (_d(h['date']) >= today)), None)
            days = (cfg.get('court_filing_days_before') or {}).get('individual')
            if merits and days:
                due = _next_business_day(_d(merits['date']) - timedelta(days=days))
                deadlines.append(_deadline('cancellation.file', due, f"File the {form} with the immigration court: {days} days before the {us(merits['date'])} individual hearing, unless the judge set another date (Immigration Court Practice Manual 3.1(b), 4.15(j))", 'attorney', today, cfg))
                cancel_offer['label'] += f': file by {us(due)}'
    waiver_offers = _waiver_offers(graph, ns, hearings, records) if stage != 'citizen' else []
    takeover = []
    rep = representation(ns)
    pending = sorted({n['receipt'] for n in ns if not any((m['receipt'] == n['receipt'] and m['kind'] in CLOSED for m in ns))})
    receipts: dict[str, dict[str, Any]] = {}
    for n in ns:
        r = receipts.setdefault(n['receipt'], {'receipt': n['receipt'], 'form': n['form'], 'since': '', 'closed': False})
        r.update(since=max(r['since'], n['date'] or ''), form=r['form'] or n['form'], closed=r['closed'] or n['kind'] in CLOSED)
    for rec in records:
        if rec.get('receipt'):
            r = receipts.setdefault(rec['receipt'], {'receipt': rec['receipt'], 'form': None, 'since': '', 'closed': False})
            r['since'] = max(r['since'], rec.get('mailed_on') or '')
    if ns and rep['who'] not in ('firm', 'unknown') and (not status.get('filed_at')) and (not marks.get('ours')):
        for item in s['takeover']['items']:
            id_, owner, text, *only = item
            if only and (only[0] == 'sij' and track != 'sij' or (only[0] == 'nta' and (not nta))):
                continue
            if id_ == 'g28' and (not pending):
                continue
            takeover.append({'id': f'takeover.{id_}', 'owner': owner, 'done': done.get(f'takeover.{id_}'), 'text': text.format(pending=', '.join(pending) or 'none', receipts=', '.join(sorted({n['receipt'] for n in ns})))})
    import expiry
    try:
        deadlines += expiry.deadlines(client_dir, today, {'stage': stage, 'resident': resident_or_later, 'daca': bool(daca_case), 'daca_pending': stage == 'daca_pending' or bool(daca.renewal_pending(graph)), 'consular': track == 'family' and 'consular' in stages, 'travel': any((r.get('filing') == 'i131' for r in records)), 'filed': filed, 'packet_filing': packet.get('filing'), 'ids': {d['id'] for d in deadlines}, 'notices': ns, 'graph': graph})
    except (OSError, ValueError, KeyError, TypeError, AttributeError):
        step('expiry.unreadable', 'paralegal', "The list of this case's documents could not be read, so its expiring documents are not being watched: process the case again.", True)
    appts: dict[str, dict[str, Any]] = {h['id']: {'kind': 'hearing', 'time': h.get('time'), 'place': h.get('court'), 'judge': h.get('judge'), 'title': f"{h['kind']} hearing"} for h in hearings}
    for n in ns:
        if n['kind'] in ('biometrics', 'interview') and _d(n['appointment']):
            appts[f"{n['receipt']}.{n['kind']}.{n['date']}"] = {'kind': n['kind'], 'time': str(n['appointment'])[11:].strip() or None, 'place': n.get('where'), 'title': 'Interview' if n['kind'] == 'interview' else 'Biometrics appointment'}
    if first and _d(first['date']) and (not any((h['date'] == first['date'] for h in hearings))):
        appts['hearing.nta'] = {'kind': 'hearing_unconfirmed', 'time': first.get('time'), 'place': first.get('place'), 'title': 'First hearing set by the Notice to Appear (not confirmed)'}
    if oath_day:
        appts['oath'] = {'kind': 'oath', 'time': oath.get('time'), 'place': oath.get('place'), 'title': 'Citizenship oath ceremony'}
    if interview:
        appts['visa.interview'] = {'kind': 'visa_interview', 'time': _value(graph, 'visa.interview_time'), 'place': consulate, 'title': 'Immigrant visa interview'}
    for d in deadlines:
        if d['id'] in appts:
            d['appt'] = appts[d['id']]
    import deadlines_set
    deadlines += deadlines_set.open_deadlines(client_dir, today, cfg)
    if own:
        for d in own_path.derive(own['steps'], own.get('removed'))['deadlines']:
            if d.get('what'):
                deadlines.append(_deadline(f"path.{d['step']}.{d['filing']}", date.fromisoformat(d['date']), d['what'], d['owner'], today, cfg, "The case's path, as approved"))
    deadlines_set.apply_assignments(client_dir, deadlines)
    deadlines.sort(key=lambda x: x['date'])
    for evidence in reversed(pending_evidence):
        dates = '; '.join((f"{d['label']}: {d['value']}" for d in evidence['dates']))
        steps.insert(0, {'id': 'subject_notice:' + evidence['instance_id'], 'owner': 'paralegal', 'urgent': True, 'done': None, 'evidence_review': True, 'text': f"Review notice evidence: {evidence['file']}. {dates}. These are unconfirmed printed dates, not accepted case deadlines. Check the source promptly."})
    open_steps = [x for x in steps if not x['done']]
    nxt = next((x for x in deadlines if x['days_left'] >= -60), None)
    next_filings = [{'filing': f, 'label': label, 'now': bool(now)} for f, label, now in (s.get('next_filings') or {}).get(stage, [])]
    left_out = set((own or {}).get('removed') or [])
    if stage == 'resident':
        i751 = next((d for d in deadlines if d['id'] == 'i751'), None)
        if i751:
            opens = _d(i751['date']) - timedelta(days=90)
            next_filings.append({'filing': 'i751', 'label': f'Remove the conditions on the 2-year green card (I-751): from {us(opens)}, the 90 days before it expires', 'now': today >= opens})
        next_filings.append({'filing': 'n400', 'label': 'Citizenship (N-400)' + (f', from {us(citizenship)}' if citizenship else ''), 'now': bool(citizenship) and today.isoformat() >= citizenship})
        if any((d['id'] == 'i90' for d in deadlines)):
            next_filings.append({'filing': 'i90', 'label': 'Renew the green card (I-90)', 'now': True})
    if any((d['id'] == 'ead' for d in deadlines)) and (not resident_or_later):
        next_filings.append({'filing': 'ead', 'label': 'Renew the work permit (I-765)', 'now': True})
    elif stage == 'asylum_pending' and filed_on and _d(filed_on):
        from_day = _d(filed_on) + timedelta(days=150)
        next_filings.insert(0, {'filing': 'ead', 'label': f'Work permit, category (c)(8) (I-765): from {us(from_day)}, 150 days after the I-589', 'now': today >= from_day})
    elif stage == 'asylee':
        next_filings.append({'filing': 'ead', 'label': 'Work permit as an asylee, category (a)(5) (I-765), if the client wants a card', 'now': False})
    elif stage in ('visa_wait', 'i485_ready') and track == 'sij':
        next_filings.append({'filing': 'ead', 'label': 'Work permit on SIJ deferred action, category (c)(14) (I-765), if USCIS granted deferred action', 'now': False})
    if stage == 'family_wait':
        for f in next_filings:
            if f['filing'] == 'family':
                f['now'] = bool(pref and pref.get('current'))
                f['label'] = 'Green card application (I-485) on the filed I-130: the priority date is current' if f['now'] else 'Green card application (I-485) on the filed I-130, when the priority date is current'
    if stage == 'u_certification' and _u_requested(graph, records):
        for f in next_filings:
            if f['filing'] == 'u_cert':
                f['now'] = False
                f['label'] = f"Certification requested {us(_u_requested(graph, records))}: waiting for the agency's signed Supplement B"
    if stage == 'asylee':
        opens = asylee.eligible_on(granted)
        applied = filed == 'asylee' or any((n['form'] == 'I-485' for n in ns))
        for f in next_filings:
            if f['filing'] == 'asylee':
                f['now'] = bool(opens and today >= opens and (not applied))
                f['label'] = 'Green card application filed (I-485)' if applied else f"Green card as {('a refugee' if granted and granted['category'] == asylee.REFUGEE else 'an asylee')} (I-485)" + (f': from {us(opens)}, one year after the grant' if opens else ": one year after the grant (the grant date isn't in the case)")
            if f['filing'] == 'i730':
                last = next((d for d in deadlines if d['id'] == 'i730'), None)
                f['label'] = f"Spouse or children (I-730): USCIS must receive it by {us(_d(last['date']))}" if last else 'Spouse or children (I-730), within 2 years of the grant'
    if stage == 'citizen' and _value(graph, 'n565.reason'):
        next_filings.append({'filing': 'n565', 'now': True, 'label': f"Replacement certificate (N-565): {str(_value(graph, 'n565.reason')).lower()}"})
    if track == 'caa' and stage in ('intake', 'caa_wait', 'caa_ready'):
        if not any((f['filing'] == 'caa' for f in next_filings)):
            next_filings.append({'filing': 'caa', 'label': '', 'now': False})
        for f in next_filings:
            if f['filing'] == 'caa':
                f['now'] = hrifa or bool(caa_opens and today >= caa_opens)
                f['label'] = 'Green card as a HRIFA dependent (I-485)' if hrifa else 'Green card under the Cuban Adjustment Act (I-485)' + (f': from {us(caa_opens)}, one year in the U.S.' if caa_opens else ": the date of arrival isn't in the case yet")
    elif track in ('asylum', 'family') and (not resident_or_later) and cuban_adjustment.cuban(graph) and (not any((n['form'] == 'I-485' for n in ns))):
        next_filings.append({'filing': 'caa', 'label': 'Green card under the Cuban Adjustment Act (I-485): the client is Cuban, the attorney decides whether it is the better path', 'now': False})
    if cancel_offer:
        next_filings.append(cancel_offer)
    next_filings += [f for f in waiver_offers if not any((x['filing'] == f['filing'] for x in next_filings))]
    if dw and dw['kind'] != 'initial':
        label = 'DACA renewal (I-821D + I-765): ' + (f"from {us(dw['opens'])}, 150 days before DACA expires on {us(dw['expires'])}" if dw['kind'] == 'early' else f"by {us(dw['file_by'])}, 120 days before DACA expires on {us(dw['expires'])}" if dw['kind'] == 'open' else f"now, DACA expires on {us(dw['expires'])}" if dw['kind'] == 'late' else f"by {us(dw['renewal_until'])}, one year after it expired")
        next_filings = [f for f in next_filings if f['filing'] != 'daca'] + [{'filing': 'daca', 'label': label, 'now': dw['kind'] != 'early'}]
    elif stage == 'daca_current':
        next_filings = [f for f in next_filings if f['filing'] != 'daca']
    import tps
    tw = tps.window(graph, today)
    period = tw['open'][0] if tw['kind'] == 'open' else None
    filed_since = bool(period) and any((n['form'] == 'I-821' and n['kind'] in ('receipt', 'approval') and ((n['date'] or '') >= period['from']) for n in ns))
    if period and (not filed_since) and (stage not in ('resident', 'n400_pending', 'oath', 'citizen')):
        next_filings.append({'filing': 'tps', 'now': True, 'label': f"TPS for {tw['country']} (I-821 + I-765): the {period['kind']} period is open until {us(_d(period['to']))}"})
    if (nta or hearings) and stage != 'citizen' and (not eoir28_filed):
        next_filings.insert(0, {'filing': 'eoir28', 'label': 'Appear in immigration court for the client (EOIR-28)', 'now': True})
    for d in deadlines:
        if d['id'].endswith('.decision.appeal') and d['days_left'] >= 0 and (not done.get(d['id'])):
            next_filings.insert(0, {'filing': 'bia', 'label': f"Appeal to the BIA (EOIR-26): received by the Board by {us(_d(d['date']))}", 'now': True})
    next_filings += _court_offers(hearings, records, deadlines, today) if in_court(status, nta) and stage != 'citizen' else []
    for n, last in open_denials:
        n336, first = (n['form'] == 'N-400', _d(n['date']) + timedelta(days=cfg['motion_or_appeal_days']))
        by = f': by {us(first)} (30 days; 33 if USCIS mailed it)'
        next_filings.insert(0, {'filing': 'n336' if n336 else 'i290b', 'now': today <= last, 'label': ('Hearing on the denied N-400 (N-336)' + by if n336 else f"Motion or appeal on the denied {n['form'] or 'filing'} (I-290B)" + by) + ('' if today <= last else ", past: only a motion's requirements can save it")})
    if open_move:
        next_filings.insert(0, {'filing': 'address', 'label': 'Change of address: AR-11 for USCIS (10 days) and, with a court case, EOIR-33 (5 days)', 'now': True})
    next_filings = [f for f in next_filings if f['filing'] not in left_out]
    return {'track': track, 'track_name': {'sij': 'Special Immigrant Juvenile', 'family': 'Family-based', 'naturalization': 'Citizenship', 'asylum': 'Asylum', 'caa': 'Haitian Refugee Immigration Fairness Act' if hrifa else 'Cuban Adjustment Act', 'vawa': 'VAWA self-petition', 't_visa': 'T visa (trafficking victim)', 'u_visa': 'U visa (crime victim)', 'daca': 'Deferred Action for Childhood Arrivals (DACA)'}[track], 'stage': stage, 'stage_name': s['stage_names'][stage], 'stage_index': stages.index(stage), 'stages': [{'id': x, 'name': s['stage_names'][x]} for x in stages], 'template_stages': template, 'own_path': bool(own), 'found': found, 'why': why, 'set_by': chosen if chosen.get('value') == stage else None, 'visa': {k: visa.get(k) for k in ('month', 'area', 'cutoff', 'pd', 'current')} if visa else None, 'timeline': timeline, 'deadlines': deadlines, 'deadlines_done': deadlines_set.finished(client_dir), 'next_deadline': nxt, 'pending_evidence': pending_evidence, 'steps': steps, 'open_steps': len(open_steps), 'urgent_steps': sum((1 for x in open_steps if x['urgent'])), 'takeover': takeover, 'representation': rep, 'pending_receipts': pending, 'citizenship_from': citizenship, 'receipts': sorted(receipts.values(), key=lambda r: (r['since'], r['receipt']), reverse=True), 'hearings': hearings, 'hearing_kinds': list(HEARING_KINDS), 'court_outcomes': s.get('court_outcomes') or [], 'court_decisions': list(DECISIONS), 'oath': oath or None, 'moves': marks.get('moves') or [], 'linked': linked(client_dir, marks, may_open=may_open), 'relationships': list(RELATIONSHIPS), 'names': _names(graph), 'people': marks.get('people') or [], 'person_tags': [['petitioner', 'Petitioner'], ['spouse', 'Spouse'], ['parent', 'Parent'], *[[f'child_{n}', f'Child {n}'] for n in range(1, 5)]], 'visa_interview': {'date': interview.isoformat(), 'time': _value(graph, 'visa.interview_time'), 'consulate': consulate} if interview and (not _value(graph, 'visa.result')) else None, 'notices': ns, 'today': today.isoformat(), 'draft': s.get('_status', '').startswith('DRAFT'), 'next_filings': next_filings, 'uscis_status': client_case.status_records(client_dir), 'office_phone': client_case.office_phone(client_dir), 'notice_reads': client_case.reads({'notices': ns, 'today': today.isoformat()}), 'filings': [{k: r.get(k) for k in ('filing', 'title', 'mailed_on', 'carrier', 'form', 'forms', 'variant')} for r in records]}

def _names(graph) -> dict[str, Any] | None:
    """Application parser or workflow helper."""
    from name_events import view
    return view(graph)

def summary(j: dict[str, Any]) -> dict[str, Any]:
    """Application parser or workflow helper."""
    return {'track': j['track'], 'track_name': j.get('track_name'), 'stage': j['stage'], 'stage_name': j['stage_name'], 'why': j['why'], 'deadlines': [{k: d[k] for k in ('id', 'date', 'what', 'owner')} | ({'expiry': d['expiry']} if d.get('expiry') else {}) | {k: d[k] for k in ('appt', 'who', 'who_name', 'set', 'note', 'task') if d.get(k)} for d in j['deadlines']], 'urgent_steps': j['urgent_steps'], 'open_steps': j['open_steps'], 'steps': [{'id': x['id'], 'owner': x['owner'], 'text': x['text'], 'urgent': bool(x.get('urgent'))} | ({'evidence_review': True} if x.get('evidence_review') else {}) for x in j['steps'] + j['takeover'] if not x['done']], 'takeover_open': sum((1 for t in j['takeover'] if not t['done'])), 'citizenship_from': j['citizenship_from'], 'next_filings': j.get('next_filings') or [], 'receipts': [{k: r[k] for k in ('receipt', 'form', 'since')} for r in j.get('receipts') or [] if not r['closed']]}
HEARING_KINDS = ('Master calendar', 'Individual (merits)', 'Bond', 'Other')
VISA_EVENTS = [('visa.welcome_letter_date', 'NVC welcome letter (the case is at the National Visa Center)'), ('visa.fees_paid_date', 'NVC fees paid'), ('visa.ds260_submitted', 'DS-260 immigrant visa application submitted'), ('visa.documents_submitted', 'Civil documents and financial evidence submitted to NVC'), ('visa.dq_date', 'Documentarily qualified at NVC'), ('visa.medical_date', 'Medical exam with the panel physician'), ('visa.interview_date', 'Immigrant visa interview at the consulate'), ('visa.issued_date', 'Immigrant visa issued'), ('visa.entry_date', 'Entered the U.S. on the immigrant visa (a permanent resident from this day)')]
DECISIONS = ('Decision: relief granted', 'Decision: removal ordered or relief denied', 'Removal ordered in absentia')

def _hearing_result(value: Any) -> dict[str, Any]:
    """Application parser or workflow helper."""
    v = {'outcome': 'Other', 'text': value} if isinstance(value, str) else dict(value or {})
    outcomes = settings().get('court_outcomes') or []
    if v.get('outcome') not in outcomes:
        raise ValueError(f"What happened: one of {', '.join(outcomes)}.")
    text = str(v.get('text') or '').strip()
    if v['outcome'] == 'Other' and (not text):
        raise ValueError("What happened at the hearing? (e.g. 'reset to the individual hearing')")
    out = {'outcome': v['outcome'], 'text': text or None}
    if v['outcome'] in DECISIONS:
        if not _d(v.get('decision_date')):
            raise ValueError("The decision's date as YYYY-MM-DD: the day of an oral decision, or the day a written one was mailed or sent electronically. the appeal deadline counts from it.")
        out.update(decision_date=_d(v['decision_date']).isoformat(), written=bool(v.get('written')), asylum=bool(v.get('asylum')), appeal_waived=bool(v.get('appeal_waived')))
    return out
_MARK_WORDS = {'done': 'Marked a step done', 'undo': 'Reopened a step', 'stage': 'Set the stage', 'track': 'Set the track', 'lpr_date': 'Set the date the client became a permanent resident', 'ours': 'Recorded that the firm filed the case itself', 'hearing': 'Added a court hearing', 'hearing_confirm': 'Confirmed a hearing against the paper', 'hearing_result': 'Recorded what happened at a hearing', 'hearing_remove': 'Removed a hearing', 'oath': 'Set the oath ceremony', 'moved': 'Recorded a move', 'person': 'Added a person to the case', 'person_remove': 'Removed a person from the case', 'bring_confirm': "Confirmed the notice's list of what to bring against the paper", 'bring_reject': "Said the notice's list of what to bring was read wrongly", 'place_confirm': "Confirmed an appointment's address against the notice", 'place_reject': "Said an appointment's address was read wrongly", 'place_set': "Typed an appointment's address"}
_TRACK_WORDS = {'sij': 'Special Immigrant Juvenile', 'family': 'Family-based', 'naturalization': 'Citizenship', 'asylum': 'Asylum', 'caa': 'Cuban Adjustment Act', 'vawa': 'VAWA self-petition', 't_visa': 'T visa', 'u_visa': 'U visa', 'daca': 'DACA'}
_ID_KINDS = ((re.compile('^hearing\\.'), 'a court hearing'), (re.compile('^moved\\.'), 'a change of address'), (re.compile('\\.(rfe|noid|nta)\\b'), 'a request from USCIS'))

def _named(action: str, value: Any) -> str:
    """Application parser or workflow helper."""
    if action == 'track':
        return _TRACK_WORDS.get(str(value), 'another track')
    return str(settings()['stage_names'].get(str(value)) or 'another stage')

def _step_words(client_dir: Path, item: str) -> str:
    """Application parser or workflow helper."""
    try:
        j = journey(client_dir)
        title = next((x['text'] for x in j['steps'] + j['takeover'] if x['id'] == item), '')
        if events.plain(title):
            return events.plain(title, 90)
    except Exception:
        pass
    return next((words for rx, words in _ID_KINDS if rx.search(item)), 'a step')

def mark(client_dir: Path, action: str, who: str, item: str | None=None, value: Any=None, note: str | None=None, *, may_open=None, jobs_root=None) -> dict[str, Any]:
    """Application parser or workflow helper."""
    if not who:
        raise ValueError('Enter your name first: every change records who made it.')
    if action in ('link', 'unlink'):
        return family_mark(client_dir, action, who, value, may_open=may_open, jobs_root=jobs_root)
    status = _status(client_dir)
    marks = status.setdefault('journey', {})
    at = clock.stamp()
    if action in ('done', 'undo'):
        if str(item or '').startswith('subject_notice:'):
            raise ValueError("Review the notice's fact subjects in Documents; marking a task done cannot accept this evidence.")
        if not item:
            raise ValueError('Which item?')
        if action == 'done':
            marks.setdefault('done', {})[item] = {'by': who, 'at': at, 'note': note}
        else:
            (marks.get('done') or {}).pop(item, None)
    elif action in ('stage', 'track', 'lpr_date'):
        s = settings()
        if action == 'stage' and value is not None and (not any((value in v for v in s['stages'].values()))):
            raise ValueError(f'Unknown stage {value!r}.')
        if action == 'track' and value not in (None, 'sij', 'family', 'naturalization', 'asylum', 'caa', 'vawa', 't_visa', 'u_visa', 'daca'):
            raise ValueError('The track is sij, family, naturalization, asylum, caa, vawa, t_visa, u_visa or daca.')
        if action == 'lpr_date' and value is not None and (not _d(value)):
            raise ValueError('Give the date as YYYY-MM-DD.')
        if value is None:
            marks.pop(action, None)
        else:
            marks[action] = {'value': value, 'by': who, 'at': at, 'note': note}
    elif action == 'ours':
        marks['ours'] = {'by': who, 'at': at} if value is not False else None
    elif action == 'hearing':
        h = dict(value or {})
        if not _d(h.get('date')):
            raise ValueError('Give the hearing date as YYYY-MM-DD (from the hearing notice).')
        if h.get('kind') not in HEARING_KINDS:
            raise ValueError(f"The hearing is one of: {', '.join(HEARING_KINDS)}.")
        if h.get('time') and (not re.fullmatch('\\d{1,2}:\\d{2}( ?[AP]M)?', str(h['time']).strip(), re.I)):
            raise ValueError('Give the time as on the notice, e.g. 9:00 AM.')
        hearing = {k: str(h.get(k)).strip() if h.get(k) else None for k in ('date', 'time', 'kind', 'court', 'judge')}
        hearing['detained'] = bool(h.get('detained'))
        hearing.update(id=f"hearing.{_d(h['date']).isoformat()}.{len(marks.get('hearings') or [])}", by=who, at=at, note=note)
        if h.get('source'):
            hearing.update(source=str(h['source']), confirmed=None)
        marks.setdefault('hearings', []).append(hearing)
    elif action == 'hearing_confirm':
        found = next((h for h in marks.get('hearings') or [] if h['id'] == item), None)
        if not found:
            raise ValueError('No such hearing.')
        found['confirmed'] = {'by': who, 'at': at}
    elif action in ('hearing_result', 'hearing_remove'):
        found = next((h for h in marks.get('hearings') or [] if h['id'] == item), None)
        if not found:
            raise ValueError('No such hearing.')
        if action == 'hearing_remove':
            marks['hearings'].remove(found)
        else:
            found['result'] = _hearing_result(value) | {'by': who, 'at': at}
    elif action in ('bring_confirm', 'bring_reject', 'place_confirm', 'place_reject', 'place_set'):
        import client_case
        read = next((x for x in journey(client_dir)['notices'] if client_case.notice_id(x) == item and x.get('kind') in ('biometrics', 'interview')), None)
        if read is None:
            raise ValueError('No such appointment notice.')
        if action.startswith('bring'):
            if not read.get('bring') or list(value or []) != read['bring']:
                raise ValueError('The notice was read again: look at its list again before you answer.')
            marks.setdefault('bring', {})[item] = {'state': 'confirmed' if action == 'bring_confirm' else 'rejected', 'lines': read['bring'], 'by': who, 'at': at}
        elif action == 'place_set':
            typed = ' '.join(str(value or '').split())
            if not 8 <= len(typed) <= 200:
                raise ValueError('Type the address as it is on the notice (at least 8 characters).')
            marks.setdefault('places', {})[item] = {'state': 'confirmed', 'read': read.get('where_read'), 'typed': typed, 'by': who, 'at': at}
        else:
            if not read.get('where_read') or value != read['where_read']:
                raise ValueError('The notice was read again: look at its address again before you answer.')
            marks.setdefault('places', {})[item] = {'state': 'confirmed' if action == 'place_confirm' else 'rejected', 'read': read['where_read'], 'by': who, 'at': at}
    elif action == 'oath':
        if value is None:
            marks.pop('oath', None)
        else:
            o = dict(value)
            if not _d(o.get('date')):
                raise ValueError('Give the oath ceremony date as YYYY-MM-DD (from Form N-445).')
            marks['oath'] = {'date': _d(o['date']).isoformat(), 'time': str(o.get('time') or '').strip() or None, 'place': str(o.get('place') or '').strip() or None, 'by': who, 'at': at}
    elif action == 'moved':
        m = dict(value or {})
        if not _d(m.get('date')):
            raise ValueError('Give the date the client moved as YYYY-MM-DD.')
        if _d(m['date']) > clock.today():
            raise ValueError("The move date can't be in the future: record it once the client has moved.")
        moves = marks.setdefault('moves', [])
        moves.append({'id': f"moved.{_d(m['date']).isoformat()}.{len(moves)}", 'date': _d(m['date']).isoformat(), 'address': str(m.get('address') or '').strip() or None, 'by': who, 'at': at})
    elif action in ('person', 'person_remove'):
        people = marks.setdefault('people', [])
        if action == 'person_remove':
            if not any((p['id'] == item for p in people)):
                raise ValueError('No such person.')
            marks['people'] = [p for p in people if p['id'] != item]
        else:
            people.append(_person(dict(value or {}), people, who, at))
    else:
        raise ValueError(f'Unknown action {action!r}.')
    (client_dir / 'status.json').write_text(json.dumps(status, indent=1), encoding='utf-8')
    events.record('journey', action, _MARK_WORDS.get(action, 'Changed where the case stands') + (f': {_step_words(client_dir, item)}' if action in ('done', 'undo') and item else f': {_named(action, value)}' if action in ('stage', 'track') and value else ''), case_dir=client_dir, who=who)
    return status

def _person(p: dict[str, Any], people: list[dict[str, Any]], who: str, at: str) -> dict[str, Any]:
    """Application parser or workflow helper."""
    import documents
    text = lambda k, n=80: ' '.join(str(p.get(k) or '').split())[:n]
    given, family = (text('given_name'), text('family_name'))
    if not (given or family):
        raise ValueError("Enter the person's name.")
    relationship = str(p.get('relationship') or '')
    if relationship not in RELATIONSHIPS:
        raise ValueError(f"How are they related to the client? One of: {', '.join(RELATIONSHIPS)}.")
    tag = str(p.get('person') or '')
    if tag in ('applicant', 'unknown') or not documents.valid_person(tag):
        raise ValueError('Choose whose documents are theirs: the petitioner, the spouse, a parent or a child.')
    email = text('email', 120)
    if email and (not re.fullmatch('[^@\\s]+@[^@\\s]+\\.[^@\\s]{2,}', email)):
        raise ValueError("That email address doesn't look right.")
    n = 1 + max((int(x['id'].split('.')[-1]) for x in people), default=0)
    return {'id': f'person.{n}', 'given_name': given, 'family_name': family, 'name': f'{given} {family}'.strip(), 'relationship': relationship, 'phone': text('phone', 40), 'email': email, 'person': tag, 'by': who, 'at': at}
RELATIONSHIPS = {'Spouse': 'Spouse', 'Child': 'Parent', 'Parent': 'Child', 'Sibling': 'Sibling', 'Other relative': 'Other relative'}

def family_id(value):
    return isinstance(value, str) and 1 <= len(value) <= 200 and (value not in ('.', '..')) and (not any((c in value for c in ('/', '\\', '\x00')))) and (not any((ord(c) < 32 for c in value)))

def _family_safe(path):
    for part in (path, *path.parents):
        try:
            info = part.lstat()
        except FileNotFoundError:
            continue
        if part.is_symlink() or getattr(info, 'st_file_attributes', 0) & getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 1024):
            raise LookupError('unknown client')

def _family_folder(root, client, may_open):
    if not family_id(client):
        raise LookupError('unknown client')
    folder = root / client
    _family_safe(folder)
    if may_open is None:
        allowed = False
    else:
        allowed = may_open(client)
    if allowed is not True or not (folder / 'meta.json').is_file():
        raise LookupError('unknown client')
    _family_safe(folder / 'status.json')
    return folder

def _family_save(folder, status):
    path = folder / 'status.json'
    _family_safe(path)
    temporary = folder / ('.family-status-' + secrets.token_hex(16) + '.part')
    try:
        with temporary.open('x', encoding='utf-8') as handle:
            json.dump(status, handle, indent=1)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
        if os.name == 'posix':
            fd = os.open(folder, os.O_RDONLY | getattr(os, 'O_DIRECTORY', 0))
            try:
                os.fsync(fd)
            finally:
                os.close(fd)
    finally:
        temporary.unlink(missing_ok=True)

def _family_open(folder):
    """Application parser or workflow helper."""
    from case_assignment import Assignments
    import jobs
    Assignments(folder.parent, jobs.folder_for(folder.parent), lambda: [])._open(folder)

def _family_instance(status, *, create=False):
    marks = status.setdefault('journey', {})
    if not isinstance(marks, dict):
        raise ValueError('Recorded family status is unavailable; manual reconciliation is required.')
    instance = marks.get('family_instance')
    if instance is None and create:
        instance = marks['family_instance'] = secrets.token_hex(16)
    if not isinstance(instance, str) or not re.fullmatch('[0-9a-f]{32}', instance):
        raise ValueError('Recorded family identity is absent or changed; manual reconciliation is required.')
    return instance

def _family_plan(status, operation):
    plans = status.get('journey', {}).get('family_operations', [])
    if not isinstance(plans, list):
        raise ValueError('The retained family operation is unavailable; manual reconciliation is required.')
    plan = next((p for p in plans if isinstance(p, dict) and p.get('id') == operation), None)
    if not isinstance(plan, dict) or plan.get('action') not in ('link', 'unlink') or (not family_id(plan.get('source'))) or (not family_id(plan.get('target'))) or (plan['source'] == plan['target']) or (not isinstance(plan.get('relationship'), str)) or (plan['relationship'] not in RELATIONSHIPS) or (plan.get('state') not in ('pending', 'completed')) or any((not isinstance(plan.get(k), str) or not re.fullmatch('[0-9a-f]{32}', plan[k]) for k in ('id', 'source_instance', 'target_instance'))) or (not isinstance(plan.get('audit'), dict)) or (not isinstance(plan.get('by'), str)) or (not isinstance(plan.get('at'), str)):
        raise ValueError('The retained family operation is unavailable; manual reconciliation is required.')
    return plan

def _family_apply(status, peer, own_instance, peer_instance, relationship, plan):
    marks = status.setdefault('journey', {})
    links = [x for x in marks.get('linked', []) if x.get('client') != peer]
    if plan['action'] == 'link':
        links.append({'client': peer, 'relationship': relationship, 'by': plan['by'], 'at': plan['at'], 'instance': peer_instance, 'own_instance': own_instance, 'operation': plan['id'], 'origin': plan['source']})
    marks['linked'] = links

def _family_finish(root, plan, may_open):
    source = _family_folder(root, plan['source'], may_open)
    target = _family_folder(root, plan['target'], may_open)
    _family_open(source)
    _family_open(target)
    source_status, target_status = (_status(source), _status(target))
    if _family_instance(source_status) != plan['source_instance'] or _family_instance(target_status) != plan['target_instance']:
        raise ValueError('Recorded family identity changed; manual reconciliation is required.')
    retained = _family_plan(source_status, plan['id'])
    if retained['state'] == 'pending':
        target_status.setdefault('journey', {})['family_pending'] = {'source': plan['source'], 'operation': plan['id'], 'instance': plan['target_instance']}
        _family_apply(target_status, plan['source'], plan['target_instance'], plan['source_instance'], RELATIONSHIPS[plan['relationship']], plan)
        _family_folder(root, plan['target'], may_open)
        _family_save(target, target_status)
        _family_apply(source_status, plan['target'], plan['source_instance'], plan['target_instance'], plan['relationship'], plan)
        retained['state'] = 'completed'
        source_status['journey'].pop('family_pending', None)
        _family_folder(root, plan['source'], may_open)
        _family_save(source, source_status)
    marker = target_status.get('journey', {}).get('family_pending')
    if marker and marker.get('operation') == plan['id'] and (marker.get('source') == plan['source']):
        target_status['journey'].pop('family_pending', None)
        _family_folder(root, plan['target'], may_open)
        _family_save(target, target_status)
    for label, folder in (('source', source), ('target', target)):
        if retained['audit'].get(label):
            continue
        what = ("Linked a family member's case" if plan['action'] == 'link' else "Unlinked a family member's case") + '; operation ' + plan['id']
        event = next((r for r in events.rows(events.base_path(folder.parent.parent), case=folder.name) if r.get('what') == what and r.get('action') == plan['action']), None)
        event = event or events.record('journey', plan['action'], what, case_dir=folder, who=plan['by'])
        if event:
            retained['audit'][label] = event.get('hash')
            _family_save(source, source_status)
    return source_status

def family_mark(client_dir, action, who, value, *, may_open=None, jobs_root=None):
    """Application parser or workflow helper."""
    import jobs
    root = Path(client_dir).absolute().parent
    own = Path(client_dir).name
    expected = {'client', 'relationship'} if action == 'link' else {'client'}
    if not isinstance(value, dict) or set(value) != expected:
        raise ValueError('Choose an explicit case and relationship.')
    peer = value['client']
    if not family_id(peer) or peer == own:
        raise ValueError("Choose another client's case to link.")
    relationship = value.get('relationship')
    if action == 'link' and (not isinstance(relationship, str) or relationship not in RELATIONSHIPS):
        raise ValueError('Choose an explicit family relationship.')
    queue = Path(jobs_root) if jobs_root is not None else jobs.folder_for(root)
    _family_safe(root)
    _family_safe(queue)
    with ExitStack() as locks:
        for case in sorted((own, peer)):
            locks.enter_context(jobs.case_lock(queue, case, jobs.BUSY_WAIT))
        source, target = (_family_folder(root, own, may_open), _family_folder(root, peer, may_open))
        _family_open(source)
        _family_open(target)
        statuses = [_status(source), _status(target)]
        if any((s.get('journey', {}).get('family_pending') or any((isinstance(p, dict) and p.get('state') == 'pending' for p in s.get('journey', {}).get('family_operations', []))) for s in statuses)):
            raise ValueError('An interrupted family operation remains held. Recover the recorded operation first.')
        instances = [_family_instance(s, create=action == 'link') for s in statuses]
        if action == 'unlink':
            row = next((x for x in statuses[0].get('journey', {}).get('linked', []) if x.get('client') == peer), None)
            if not row or row.get('instance') != instances[1] or row.get('own_instance') != instances[0]:
                raise ValueError('Recorded family identity changed; manual reconciliation is required.')
            relationship = row['relationship']
        for folder, status in zip((source, target), statuses):
            _family_save(folder, status)
        plan = {'id': secrets.token_hex(16), 'source': own, 'target': peer, 'source_instance': instances[0], 'target_instance': instances[1], 'action': action, 'relationship': relationship, 'by': who, 'at': clock.stamp(), 'state': 'pending', 'audit': {}}
        marks = statuses[0].setdefault('journey', {})
        marks.setdefault('family_operations', []).append(plan)
        marks['family_pending'] = {'source': own, 'operation': plan['id'], 'instance': instances[0]}
        _family_save(source, statuses[0])
        return _family_finish(root, plan, may_open)

def family_recovery_view(client_dir, *, may_open=None):
    root, own = (Path(client_dir).absolute().parent, Path(client_dir).name)
    own_dir = _family_folder(root, own, may_open)
    status = _status(own_dir)
    marker = status.get('journey', {}).get('family_pending')
    if not marker:
        plans = status.get('journey', {}).get('family_operations', [])
        if not isinstance(plans, list):
            raise ValueError('The retained family operation is unavailable; manual reconciliation is required.')
        plan = next((p for p in reversed(plans) if isinstance(p, dict) and (p.get('state') == 'pending' or not isinstance(p.get('audit'), dict) or (not all((p['audit'].get(k) for k in ('source', 'target')))))), None)
        if not plan:
            return {'state': 'none', 'can_recover': False}
        plan = _family_plan(status, plan.get('id'))
        marker = {'source': own, 'operation': plan['id'], 'instance': plan['source_instance']}
    if not isinstance(marker, dict):
        raise ValueError('The retained family operation is unavailable; manual reconciliation is required.')
    if _family_instance(status) != marker.get('instance'):
        return {'state': 'held', 'can_recover': False, 'reason': 'Recorded family identity changed; manual reconciliation is required.'}
    origin = _family_folder(root, marker.get('source'), may_open)
    plan = _family_plan(_status(origin), marker.get('operation'))
    source, target = (_family_folder(root, plan['source'], may_open), _family_folder(root, plan['target'], may_open))
    try:
        _family_open(source)
        _family_open(target)
        valid = _family_instance(_status(source)) == plan['source_instance'] and _family_instance(_status(target)) == plan['target_instance']
    except ValueError:
        valid = False
    return {'state': 'pending' if valid else 'held', 'can_recover': valid, 'operation': plan['id'], 'source': plan['source'], 'target': plan['target'], 'relationship': plan['relationship'], 'action': plan['action'], 'reason': 'Recover the same retained operation.' if valid else 'Recorded family identity changed; manual reconciliation is required.'}

def family_recover(client_dir, operation, *, may_open=None, jobs_root=None):
    import jobs
    view = family_recovery_view(client_dir, may_open=may_open)
    if not view.get('can_recover') or operation != view.get('operation'):
        raise ValueError('Choose the intact recorded family operation.')
    root = Path(client_dir).absolute().parent
    queue = Path(jobs_root) if jobs_root is not None else jobs.folder_for(root)
    _family_safe(root)
    _family_safe(queue)
    with ExitStack() as locks:
        for case in sorted((view['source'], view['target'])):
            locks.enter_context(jobs.case_lock(queue, case, jobs.BUSY_WAIT))
        current = family_recovery_view(client_dir, may_open=may_open)
        if not current.get('can_recover') or current.get('operation') != operation:
            raise ValueError('Choose the intact recorded family operation.')
        origin = _family_folder(root, current['source'], may_open)
        return _family_finish(root, _family_plan(_status(origin), operation), may_open)

def linked(client_dir: Path, marks: dict[str, Any], *, may_open=None) -> list[dict[str, Any]]:
    """Application parser or workflow helper."""
    from review.overview import journey_row
    out = []
    try:
        root = Path(client_dir).absolute().parent
        own = _family_folder(root, Path(client_dir).name, may_open)
        own_instance = _family_instance({'journey': marks})
    except (LookupError, ValueError, OSError):
        return []
    if marks.get('family_pending'):
        return []
    for x in marks.get('linked') or []:
        if not isinstance(x, dict) or x.get('own_instance') != own_instance:
            continue
        try:
            other = _family_folder(root, x.get('client'), may_open)
            other_status = _status(other)
            if other_status.get('journey', {}).get('family_pending') or _family_instance(other_status) != x.get('instance'):
                continue
            origin = _family_folder(root, x.get('origin'), may_open)
            plan = _family_plan(_status(origin), x.get('operation'))
            expected_instances = (plan['source_instance'], plan['target_instance']) if own.name == plan['source'] else (plan['target_instance'], plan['source_instance'])
            if plan['state'] != 'completed' or {own.name, other.name} != {plan['source'], plan['target']} or (own_instance, x.get('instance')) != expected_instances:
                continue
        except (LookupError, ValueError, OSError, TypeError, KeyError):
            continue
        try:
            row = journey_row(other) or {}
        except Exception:
            row = {}
        name = None
        if (other / 'fact_graph.json').exists():
            from factgraph import FactGraph
            from review.state import case_summary
            try:
                name = case_summary(FactGraph.load(other / 'fact_graph.json')).get('name')
            except (OSError, ValueError, KeyError, TypeError):
                pass
        out.append({'client': x['client'], 'relationship': x['relationship'], 'name': name or x['client'], 'stage_name': row.get('stage_name'), 'next_deadline': (row.get('deadlines') or [None])[0], 'by': x['by'], 'at': x['at']})
    return out

def client_view(j: dict[str, Any], lang: str) -> dict[str, Any]:
    """Application parser or workflow helper."""
    from portal.bank import languages
    s = settings()
    lang = lang if lang in languages() else 'en'
    texts = s['client']
    title, now = _say(texts[j['stage']], lang)
    appts, pages = ([], [])
    import client_case

    def add(kind: str, appt: dict[str, Any], where: str | None, title: str, extra: dict[str, Any] | None=None) -> None:
        """Application parser or workflow helper."""
        appts.append(appt)
        pages.append(_prepare(kind, appt, where, title, lang) | (extra or {}))
    for n in j['notices']:
        when = _d(n['appointment'])
        if n['kind'] in ('biometrics', 'interview') and when and (when.isoformat() >= j['today']):
            name, bring = _say(s['client_appointments'][n['kind']], lang)
            add(n['kind'], {'date': when.isoformat(), 'time': (n['appointment'] or '')[11:] or None, 'what': name, 'bring': bring}, n.get('where'), name, {'id': client_case.notice_id(n), 'where_confirmed': n.get('where_state') == 'confirmed', 'sheet': client_case.sheet(n['kind'], n.get('form'), j.get('track'), lang, n.get('bring') if n.get('bring_state') == 'confirmed' else None, j.get('office_phone'), n.get('bring_state') == 'unconfirmed')})
    oath = j.get('oath') or {}
    if oath.get('date') and oath['date'] >= j['today'] and s['client_appointments'].get('oath'):
        name, bring = _say(s['client_appointments']['oath'], lang)
        add('oath', {'date': oath['date'], 'time': oath.get('time'), 'what': name + (f": {oath['place']}" if oath.get('place') else ''), 'bring': bring}, oath.get('place'), name, {'id': 'oath'})
    visa = j.get('visa_interview')
    if visa and visa['date'] >= j['today'] and s['client_appointments'].get('consulate'):
        name, bring = _say(s['client_appointments']['consulate'], lang)
        add('consulate', {'date': visa['date'], 'time': visa.get('time'), 'what': name + (f": {visa['consulate']}" if visa.get('consulate') else ''), 'bring': bring}, visa.get('consulate'), name, {'id': 'visa.interview'})
    for h in j.get('hearings') or []:
        if h.get('source') and (not h.get('confirmed')):
            continue
        if h['date'] >= j['today'] and (not h.get('result')) and s['client_appointments'].get('hearing'):
            name, bring = _say(s['client_appointments']['hearing'], lang)
            add('hearing', {'date': h['date'], 'time': h.get('time'), 'what': name + (f": {h['court']}" if h.get('court') else ''), 'bring': bring}, h.get('court'), name, {'id': h.get('id')})
    order = sorted(range(len(appts)), key=lambda i: appts[i]['date'])
    path = [{'id': x['id'], 'name': _say(texts[x['id']], lang)[0], 'state': 'done' if i < j['stage_index'] else 'now' if i == j['stage_index'] else 'next'} for i, x in enumerate(j['stages']) if x['id'] in texts]
    pages_text = s.get('appointment_pages') or {}
    return {'stage': j['stage'], 'title': title, 'now': now, 'path': path, 'appointments': [appts[i] for i in order], 'next': client_case.next_step(j['stage'], lang), 'status': client_case.status_view(j.get('uscis_status') or [], lang), 'estimates': client_case.estimates(j, lang), 'milestones': client_case.milestones(j), 'happened': _client_events(j, lang), 'happened_label': _say(s['client_labels'], lang) if s.get('client_labels') else None, **({'prepare': [pages[i] for i in order], 'prepare_labels': {k: _say(v, lang) for k, v in pages_text['labels'].items()}} if pages_text else {})}

def _prepare(kind: str, appt: dict[str, Any], where: str | None, title: str, lang: str) -> dict[str, Any]:
    """Application parser or workflow helper."""
    pages = settings().get('appointment_pages') or {}
    labels = pages.get('labels') or {}
    check = 'check_court' if kind == 'hearing' else 'check_place' if where else 'place_in_letter'
    return {'kind': kind, 'title': title, 'date': appt['date'], 'time': appt['time'], 'where': where or None, 'where_note': _say(labels[check], lang) if check in labels else None, 'bring': _say(pages[kind], lang) if kind in pages else [appt['bring']]}

def _say(words: dict[str, Any], lang: str) -> Any:
    """Application parser or workflow helper."""
    return words.get(lang) or words['en']
_FILING_FORMS = {'i485': 'I-485', 'i360': 'I-360', 'family': 'I-130 / I-485', 'n400': 'N-400', 'i589': 'I-589', 'i90': 'I-90', 'i131': 'I-131', 'n600': 'N-600', 'i751': 'I-751', 'eoir28': 'EOIR-28', 'visa': 'DS-260', 'caa': 'I-485', 'cancellation': 'EOIR-42B', 'cancellation:eoir42a': 'EOIR-42A', 'i914': 'I-914', 'u_visa': 'I-918', 'u_cert': 'I-918 Supplement B', 'daca': 'I-821D / I-765', 'ead': 'I-765', 'asylee': 'I-485', 'i290b': 'I-290B', 'n336': 'N-336', 'i601a': 'I-601A', 'vawa': 'I-360', 'i730': 'I-730', 'bia': 'EOIR-26', 'n565': 'N-565', 'tps': 'I-821 / I-765', 'parole': 'I-131', 'i601': 'I-601', 'i212': 'I-212'}
_MAILED_TO_USCIS = frozenset({'i485', 'i360', 'family', 'n400', 'i589', 'i90', 'i131', 'n600', 'i751', 'ead', 'asylee', 'i290b', 'n336', 'i601a', 'caa', 'vawa', 'i914', 'u_visa', 'daca', 'i730', 'i601', 'i212', 'n565', 'tps', 'parole'})
_FILED_ELSEWHERE = {'rfe': 'mailed_rfe', 'eoir28': 'filed_court', 'visa': 'filed_visa', 'cancellation': 'filed_court_application', 'i914b': 'sent_agency', 'u_cert': 'mailed_u_cert', 'bia': 'filed_bia', 'court_bond': 'filed_court_motion', 'court_motion': 'filed_court_motion'}

def _event_key(r: dict[str, Any]) -> str | None:
    """Application parser or workflow helper."""
    return 'filed_court_application' if (r.get('filing'), r.get('variant')) == ('i589', 'in_court') else 'mailed' if r.get('filing') in _MAILED_TO_USCIS else _FILED_ELSEWHERE.get(r.get('filing'))

def client_shows(record: dict[str, Any]) -> bool:
    """Application parser or workflow helper."""
    key = _event_key(record)
    return bool(key and (settings().get('client_events') or {}).get(key))

def _client_events(j: dict[str, Any], lang: str, limit: int=5) -> list[dict[str, str]]:
    """Application parser or workflow helper."""
    words = settings().get('client_events') or {}
    kind_of = {'receipt': 'receipt', 'approval': 'approval', 'rfe': 'rfe', 'noid': 'decision', 'denial': 'decision', 'rejection': 'decision', 'transfer': 'transfer'}
    out = []
    for r in j.get('filings') or []:
        key = _event_key(r)
        if key and words.get(key) and r.get('mailed_on'):
            out.append((r['mailed_on'], _say(words[key], lang).format(form=_form_names(_forms_of(r), words, lang), date=_local(r['mailed_on'], lang))))
    for n in j.get('notices') or []:
        key = kind_of.get(n['kind'])
        if key and words.get(key) and n.get('date') and n.get('form'):
            out.append((n['date'], _say(words[key], lang).format(form=n['form'], date=_local(n['date'], lang))))
    return [{'date': d, 'text': t} for d, t in sorted(out, reverse=True)[:limit]]

def _forms_of(record: dict[str, Any]) -> list[str]:
    """Application parser or workflow helper."""
    if record.get('forms'):
        return list(record['forms'])
    named = record.get('form') or _FILING_FORMS.get(f"{record['filing']}:{record.get('variant')}") or _FILING_FORMS.get(record['filing'], str(record['filing']).upper())
    return [x.strip() for x in str(named).split(' / ') if x.strip()]

def _form_names(forms: list[str], words: dict[str, Any], lang: str) -> str:
    """Application parser or workflow helper."""
    word, joiner = (_say(words['_form_word'], lang), _say(words['_and'], lang))
    named = [f'{word} {f}' for f in forms]
    return named[0] if len(named) == 1 else ', '.join(named[:-1]) + f' {joiner} ' + named[-1]

def _local(iso: str, lang: str) -> str:
    """Application parser or workflow helper."""
    d = _d(iso)
    return (d.strftime('%m/%d/%Y') if lang == 'en' else d.strftime('%d/%m/%Y')) if d else iso

def _news_key(views: dict | None) -> tuple:
    """Application parser or workflow helper."""
    en = (views or {}).get('en') or {}
    return (en.get('stage'), tuple((a['date'] for a in en.get('appointments') or [])), tuple((h['text'] for h in en.get('happened') or [])), tuple(((p['time'], p['where']) for p in en.get('prepare') or [])))

def push_to_portal(data_root: Path, portal_root: Path, notify: bool=True, today: date | None=None) -> dict[str, Any]:
    """Application parser or workflow helper."""
    from portal.store import PortalStore
    store = PortalStore(portal_root)
    changed, sent = ([], 0)
    for client_id in store.clients():
        did, told = push_client(data_root, portal_root, client_id, notify, today, store)
        if did:
            changed.append(client_id)
        sent += told
    return {'changed': changed, 'notified': sent}

def push_client(data_root: Path, portal_root: Path, client_id: str, notify: bool=True, today: date | None=None, store=None, strict: bool=False) -> tuple[bool, int]:
    """Application parser or workflow helper."""
    from portal.bank import languages
    from portal.store import PortalStore
    store = store or PortalStore(portal_root)
    client_dir = data_root / client_id
    if not (client_dir / 'fact_graph.json').exists():
        return (False, 0)
    try:
        j = journey(client_dir, today)
    except Exception:
        if strict:
            raise
        return (False, 0)
    views = {lang: client_view(j, lang) for lang in languages()}
    before = store.journey(client_id)
    if _news_key(before) == _news_key(views):
        if before != views:
            store.save_journey(client_id, views)
            return (True, 0)
        return (False, 0)
    store.save_journey(client_id, views)
    had_pages = 'prepare' in ((before or {}).get('en') or {})
    if notify and before and (_news_key(before)[:3] != _news_key(views)[:3] or had_pages):
        from portal.notify import Notifier
        notifier = Notifier(portal_root / 'outbox.jsonl', cases_root=data_root, store=store)
        if not notifier.allowed(store.profile(client_id)):
            store.log(client_id, 'case_update', {'stage': j['stage'], 'message': 'not sent: restricted case'})
            return (True, 0)
        notifier.send(store.profile(client_id), 'case_update')
        store.log(client_id, 'case_update', {'stage': j['stage']})
        return (True, 1)
    return (True, 0)
