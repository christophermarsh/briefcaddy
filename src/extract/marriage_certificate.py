"""Application parser or workflow helper."""
from __future__ import annotations
import re
from .base import ExtractedField, normalize_date
from .names import fold_name, looks_like_name
_DATE = re.compile('Date of Marriage:\\s*([A-Z]+\\s+\\d{1,2},\\s*\\d{4}|\\d{1,2}/\\d{1,2}/\\d{4})', re.I)
_PLACE = re.compile("Place of Marriage:\\s*([A-Z .'-]+,\\s*[A-Z]{2})\\b", re.I)
_NAMES = re.compile("Name:\\s*([A-Z][A-Z .'-]*?)(?=\\s*Name:|[^A-Z .'-]|$)")
_DATE_VALUE = re.compile('([A-Z]+\\s+\\d{1,2},\\s*\\d{4}|\\d{1,2}/\\d{1,2}/\\d{4})')
_ADDRESS = re.compile("\\d+\\s+[A-Z0-9 .'-]+?,\\s*(?:#\\s*\\w+,\\s*|(?:APT|UNIT)\\.?\\s*\\w+,\\s*)?[A-Z .'-]+?,\\s*[A-Z]{2}(?![A-Z])")
_ORDINALS = {'FIRST': 1, 'SECOND': 2, 'THIRD': 3, 'FOURTH': 4, 'FIFTH': 5, '1ST': 1, '2ND': 2, '3RD': 3}
_AFTER_LABEL = re.compile('(?i)\\b(?:(surname|last\\s+name)|(?:full\\s+)?name)\\s+after\\s+marriage\\s*[:;]?')
_AFTER_VALUE = re.compile("\\s*(?:[^\\w\\s-]{1,2}\\s+)?([A-ZÀ-ÖØ-Þ][A-ZÀ-ÖØ-Þ .'-]*[A-ZÀ-ÖØ-Þ])")
_UNCHANGED = {'SAME', 'NO CHANGE', 'UNCHANGED'}
_NOT_STATED = {'NONE', 'N A', 'NA', 'N', 'A'}
_PARTY = re.compile('(?i)\\bparty\\s+([ab])\\b')

def _columns(text: str) -> tuple[str, str] | None:
    headers = []
    for line in text.splitlines():
        heading = re.fullmatch('(?i)\\s*party\\s+([ab])\\s*(?:\\|\\s*)?party\\s+([ab])\\s*', line)
        if heading:
            headers.append(tuple(('party_' + value.lower() for value in heading.groups())))
    distinct = set(headers)
    if len(distinct) > 1 or any((a == b for a, b in distinct)):
        return None
    return headers[0] if headers else ('party_a', 'party_b')

def _full_name_readable(value: str) -> bool:
    """Application parser or workflow helper."""
    if looks_like_name(value):
        return True
    tokens = value.split()
    trailing = 0
    for token in reversed(tokens):
        if len(token) != 1:
            break
        trailing += 1
    return 1 <= trailing <= 2 and len(tokens[0]) >= 2 and looks_like_name(' '.join(tokens[:-trailing]))

def _names_after(text: str) -> list[ExtractedField]:
    """Application parser or workflow helper."""
    attempts: dict[str, list[tuple[str, str, str, str]]] = {'party_a': [], 'party_b': []}
    columns = _columns(text)
    context = None
    for line in text.splitlines():
        markers = list(_PARTY.finditer(line))
        if re.fullmatch('(?i)\\s*party\\s+[ab]\\s*[:;]?\\s*', line):
            context = 'party_' + markers[0].group(1).lower()
        elif len(markers) > 1:
            context = None
        hits = list(_AFTER_LABEL.finditer(line))
        for j, hit in enumerate(hits):
            end = hits[j + 1].start() if j + 1 < len(hits) else len(line)
            prefix_start = hits[j - 1].end() if j else 0
            prefixes = list(_PARTY.finditer(line[prefix_start:hit.start()]))
            slot = 'party_' + prefixes[-1].group(1).lower() if len(prefixes) == 1 else None
            if slot is None and (not prefixes):
                slot = columns[j] if len(hits) == 2 and context is None and columns else context if len(hits) == 1 else None
            if slot is None or len(hits) > 2 or columns is None:
                for party in attempts:
                    attempts[party].append(('', '', line.strip(), 'ambiguous'))
                continue
            next_marker = _PARTY.search(line, hit.end(), end)
            value_end = next_marker.start() if next_marker else end
            raw = line[hit.end():value_end].strip()
            m = _AFTER_VALUE.match(raw)
            if m and m.end() < len(raw) and (raw[m.end()].isalpha() or any((c.isupper() or c.isdigit() for c in raw[m.end():]))):
                m = None
            if m and raw[m.end():].strip(' ='):
                m = None
            value = fold_name(m.group(1)) if m else ''
            if value in _UNCHANGED:
                which, state = ('name_unchanged', 'unchanged')
            elif value and value not in _NOT_STATED and (bool(hit.group(1)) or _full_name_readable(value)):
                which, state = ('surname_after' if hit.group(1) else 'name_after', 'explicit')
            else:
                which = ''
                state = 'not_stated' if not raw or set(raw) <= set('- ') or fold_name(raw) in _NOT_STATED else 'unreadable'
            attempts[slot].append((which, value, line.strip(), state))
    out = []
    for slot, reads in attempts.items():
        distinct = {(which, value, state) for which, value, _, state in reads}
        state = reads[0][3] if len(distinct) == 1 else 'ambiguous' if reads else 'not_extracted'
        raw = '\n'.join(dict.fromkeys((read[2] for read in reads))) or 'Post-marriage name field not located in extracted text'
        issues = ['Post-marriage name read needs a source check: ' + state] if state in {'ambiguous', 'unreadable'} else []
        out.append(ExtractedField(f'marriage.{slot}.after_read_state', raw, state, 0.85, reading_issues=issues))
        if state in {'explicit', 'unchanged'}:
            which, value, printed, _ = reads[0]
            out.append(ExtractedField(f'marriage.{slot}.{which}', printed, value, 0.85))
    return out

def _hFicticioA(text: str, label: str) -> list[str]:
    """Application parser or workflow helper."""
    out: list[str] = []
    for line in text.splitlines():
        hits = list(re.finditer(label, line))
        if not hits:
            continue
        for j, hit in enumerate(hits):
            end = hits[j + 1].start() if j + 1 < len(hits) else len(line)
            out.append(line[hit.end():end])
    return out

def _block_values(text: str, label: str) -> dict[str, list[str]]:
    """Application parser or workflow helper."""
    out: dict[str, list[str]] = {}
    context = None
    for line in text.splitlines():
        heading = re.fullmatch('(?i)\\s*party\\s+([ab])\\s*[:;]?\\s*', line)
        if heading:
            context = 'party_' + heading.group(1).lower()
        elif len(list(_PARTY.finditer(line))) > 1:
            context = None
        if context is not None:
            hits = list(re.finditer(label, line))
            if len(hits) == 1:
                out.setdefault(context, []).append(line[hits[0].end():])
    return out

def _parent(raw: str) -> tuple[str, str]:
    """Application parser or workflow helper."""
    name, _, paren = raw.partition('(')
    surname = fold_name(re.split('[)\\]}|]', paren)[0]) if paren else ''
    return (fold_name(name), surname)

def extract(text: str) -> list[ExtractedField]:
    fields = [ExtractedField('applicant.marital_status', 'Certificate of Marriage', 'Married', 0.9)]
    date_match = _DATE.search(text)
    if date_match:
        normalized = normalize_date(date_match.group(1))
        if normalized:
            fields.append(ExtractedField('applicant.marriage_date', date_match.group(1), normalized, 0.9))
    place = _PLACE.search(text)
    if place:
        fields.append(ExtractedField('applicant.marriage_place', place.group(1), place.group(1).strip().upper(), 0.85))
    names = [n.strip().upper() for n in _NAMES.findall(text)][:2]
    block_names = _block_values(text, 'Name:\\s*')
    block_layout = bool(block_names)
    named_parties = {slot: fold_name(re.sub("[^A-Z .'-].*$", '', values[0])) for slot, values in block_names.items() if len(values) == 1}
    if len(names) == 2:
        fields.append(ExtractedField('applicant.marriage_party_names', ' | '.join(names), ' | '.join(names), 0.85))
    parties = _columns(text) or ()
    for slot, name in named_parties.items() if block_layout else zip(parties, names):
        fields.append(ExtractedField(f'marriage.{slot}.name', name, fold_name(name), 0.85))
    block_dobs = _block_values(text, 'Date of Birth:')
    dob_rows = [(slot, values[0]) for slot, values in block_dobs.items() if len(values) == 1] if block_layout else zip(parties, _hFicticioA(text, 'Date of Birth:'))
    for slot, raw in dob_rows:
        m = _DATE_VALUE.search(raw)
        iso = normalize_date(m.group(1)) if m else None
        if iso:
            fields.append(ExtractedField(f'marriage.{slot}.dob', m.group(1), iso, 0.85))
    row_text = '' if block_layout else text
    for slot, raw in zip(parties, _hFicticioA(row_text, 'Place of Birth:')):
        place, comma, country = re.sub("[^A-Za-z ,'-].*$", '', raw.strip()).partition(',')
        country = ' '.join((t for t in fold_name(country).split() if len(t) > 1))
        value = f'{fold_name(place)}, {country}'
        if comma and country:
            fields.append(ExtractedField(f'marriage.{slot}.birthplace', raw.strip(), value, 0.85))
    for slot, raw in zip(parties, _hFicticioA(row_text, 'Occupation:')):
        value = fold_name(re.sub("[^A-Za-z /&'-].*$", '', raw.strip()))
        if value:
            fields.append(ExtractedField(f'marriage.{slot}.occupation', raw.strip(), value, 0.8))
    for line in row_text.splitlines():
        if re.search('(?i)residence', line):
            for slot, m in zip(parties, _ADDRESS.finditer(line.upper())):
                fields.append(ExtractedField(f'marriage.{slot}.residence', m.group(0), re.sub('\\s+', ' ', m.group(0)).strip(), 0.8))
            break
    parent_rows = _hFicticioA(row_text, '[NM]ame of Parent[:;]?')
    for i, raw in enumerate(parent_rows[:4]):
        slot, n = (parties[i % 2], i // 2 + 1)
        name, surname = _parent(raw)
        if looks_like_name(name):
            fields.append(ExtractedField(f'marriage.{slot}.parent{n}_name', raw.strip(), name, 0.75))
            if surname:
                fields.append(ExtractedField(f'marriage.{slot}.parent{n}_surname', raw.strip(), surname, 0.75))
    number_rows = '\n'.join((line for line in row_text.splitlines() if re.search('(?i)widowed|divorced|number of', line)))
    for slot, raw in zip(parties, _hFicticioA(number_rows, 'Marriage:\\s*')):
        word = (re.findall('[A-Z0-9]+', raw.upper()) or [''])[0]
        if word in _ORDINALS:
            fields.append(ExtractedField(f'marriage.{slot}.marriage_number', raw.strip(), str(_ORDINALS[word]), 0.85))
    fields.extend(_names_after(text))
    return fields
