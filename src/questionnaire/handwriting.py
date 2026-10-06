"""Application parser or rule helper."""
from __future__ import annotations
import difflib
import json
import re
from dataclasses import dataclass, field
from datetime import date
from typing import Any
from classify.ocr import OcrWord
from .languages import COUNTRIES, DATE_FILLERS, EYE_COLORS, HAIR_COLORS, LANGUAGE_NAMES, MONTHS, NO_WORDS, PRESENT_WORDS, TEMPLATE_WORDS, YES_WORDS, fold, translate_occupation
BLANK_INK = 0.1
US_COUNTRY_WORDS = ('', 'USA', 'US', 'EUA', 'ESTADOS UNIDOS', 'UNITED STATES')
EXACT_KEYS = ('zip', 'apt', 'state', 'date_from', 'date_to', 'postal_code')
EXACT_KINDS = ('number', 'date', 'height', 'weight', 'hair_color', 'eye_color')
KINDS: dict[str, dict[str, Any]] = {'text': {'props': ['value'], 'hint': 'the answer text'}, 'number': {'props': ['value'], 'hint': 'the number written (digits only; a written-out word like ZERO/NENHUM means 0)'}, 'date': {'props': ['value'], 'hint': 'the date exactly as written'}, 'us_address': {'props': ['street', 'apt', 'city', 'state', 'zip'], 'hint': 'a US address: street number and name, apartment/unit number if any, city, 2-letter state, 5-digit ZIP'}, 'foreign_address': {'props': ['street', 'city', 'province', 'postal_code', 'country', 'date_from', 'date_to'], 'hint': "a foreign address with the dates lived there ('De' = from, 'até'/'ate' = to)"}, 'address_history': {'props': ['street', 'apt', 'city', 'state', 'zip', 'date_from'], 'hint': "a US address and the date the person started living there ('De' = from)"}, 'employer': {'props': ['employer', 'occupation', 'street', 'city', 'state', 'zip', 'country', 'date_from', 'date_to'], 'hint': "an employer or school ('Nome da empresa'), job title ('Cargo'), its address ('Endereço'), and dates ('De' = from, 'até'/'ate' = to; 'agora' = present)"}, 'us_place': {'props': ['city', 'state'], 'hint': 'a US city and state'}, 'height': {'props': ['value'], 'hint': "a height, e.g. 5'3 (feet and inches) or 1,60 (meters)"}, 'weight': {'props': ['value', 'unit'], 'hint': 'a body weight number and its unit if written (lbs/libras or kg/quilos)'}, 'hair_color': {'props': ['value'], 'hint': 'a hair color word'}, 'eye_color': {'props': ['value'], 'hint': 'an eye color word'}, 'person_name': {'props': ['value'], 'hint': "a person's full name, every word as written"}, 'address_range': {'props': ['street', 'apt', 'city', 'state', 'zip', 'country', 'date_from', 'date_to'], 'hint': "an address (in the U.S. or abroad) and the dates the person lived there ('De' = from, 'até'/'ate' = to; 'agora' = present)"}, 'child': {'props': ['name', 'a_number', 'dob', 'country'], 'hint': "a child's full name, A-Number if any ('A#'), date of birth (day 'de' month 'de' year) and country of birth"}, 'yes_no_text': {'props': ['value'], 'hint': 'a written yes/no answer (Sim or Não), possibly with a short explanation'}}
US_STATES = {'ALABAMA': 'AL', 'ALASKA': 'AK', 'ARIZONA': 'AZ', 'ARKANSAS': 'AR', 'CALIFORNIA': 'CA', 'COLORADO': 'CO', 'CONNECTICUT': 'CT', 'DELAWARE': 'DE', 'FLORIDA': 'FL', 'GEORGIA': 'GA', 'HAWAII': 'HI', 'IDAHO': 'ID', 'ILLINOIS': 'IL', 'INDIANA': 'IN', 'IOWA': 'IA', 'KANSAS': 'KS', 'KENTUCKY': 'KY', 'LOUISIANA': 'LA', 'MAINE': 'ME', 'MARYLAND': 'MD', 'MASSACHUSETTS': 'MA', 'MICHIGAN': 'MI', 'MINNESOTA': 'MN', 'MISSISSIPPI': 'MS', 'MISSOURI': 'MO', 'MONTANA': 'MT', 'NEBRASKA': 'NE', 'NEVADA': 'NV', 'NEW HAMPSHIRE': 'NH', 'NEW JERSEY': 'NJ', 'NEW MEXICO': 'NM', 'NEW YORK': 'NY', 'NORTH CAROLINA': 'NC', 'NORTH DAKOTA': 'ND', 'OHIO': 'OH', 'OKLAHOMA': 'OK', 'OREGON': 'OR', 'PENNSYLVANIA': 'PA', 'RHODE ISLAND': 'RI', 'SOUTH CAROLINA': 'SC', 'SOUTH DAKOTA': 'SD', 'TENNESSEE': 'TN', 'TEXAS': 'TX', 'UTAH': 'UT', 'VERMONT': 'VT', 'VIRGINIA': 'VA', 'WASHINGTON': 'WA', 'WEST VIRGINIA': 'WV', 'WISCONSIN': 'WI', 'WYOMING': 'WY', 'DISTRICT OF COLUMBIA': 'DC'}
USPS_SUFFIXES = {'ROAD': 'RD', 'STREET': 'ST', 'AVENUE': 'AVE', 'BOULEVARD': 'BLVD', 'DRIVE': 'DR', 'LANE': 'LN', 'COURT': 'CT', 'PLACE': 'PL', 'TERRACE': 'TER', 'CIRCLE': 'CIR', 'PARKWAY': 'PKWY', 'HIGHWAY': 'HWY', 'SQUARE': 'SQ', 'TURNPIKE': 'TPKE', 'STR': 'ST', 'AV': 'AVE'}
STREET_TYPES = ['RUA', 'AVENIDA', 'AV', 'TRAVESSA', 'ALAMEDA', 'ESTRADA', 'RODOVIA', 'PRACA', 'LARGO', 'BECO', 'CALLE', 'CARRERA', 'AVENUE', 'BOULEVARD', 'CHEMIN', 'ROUTE']

def tidy_foreign_parts(kind: str, values: dict[str, str]) -> dict[str, str]:
    """Application parser or rule helper."""
    from extract import geo
    from extract.places import BR_UF, CEP, br_state_from_cep, country_name
    v = {k: str(x).strip() if x is not None and str(x).lower() != 'null' else '' for k, x in values.items()}
    postal_key = 'postal_code' if kind == 'foreign_address' else 'zip'
    region_key = 'province' if kind == 'foreign_address' else 'state'
    if v.get(region_key) and country_name(v[region_key]):
        v['country'] = v.get('country') or country_name(v[region_key])
        v[region_key] = ''
    if v.get('country') and country_name(v['country']):
        v['country'] = country_name(v['country'])
    elsewhere = geo.code(v.get('country')) not in (None, 'BR')
    if elsewhere:
        return _tidy_elsewhere(v, postal_key, region_key)
    for key in ('city', 'street', region_key, postal_key):
        m = CEP.search(v.get(key, ''))
        if m:
            v[postal_key] = m.group(1) + m.group(2)
            if key != postal_key:
                v[key] = CEP.sub('', v[key]).strip(' ,-')
            break
    if ',' in v.get('city', ''):
        city, _, rest = v['city'].partition(',')
        rest_folded = fold(rest).strip(' .')
        from extract.places import BR_STATE_NAMES
        if rest_folded in BR_STATE_NAMES or rest_folded in BR_UF or country_name(rest_folded):
            v['city'] = city.strip()
            if country_name(rest_folded):
                v['country'] = v.get('country') or country_name(rest_folded)
            else:
                v[region_key] = v.get(region_key) or BR_UF.get(rest_folded, rest_folded)
    from extract.places import br_states_for_city
    if not v.get('city') and ',' in v.get('street', ''):
        head, _, tail = v['street'].rpartition(',')
        tail_state = BR_UF.get(fold(tail).strip(' .'), fold(tail).strip(' .'))
        if tail_state in br_states_for_city(head.split(',')[-1]):
            v['city'], v[region_key] = (fold(head.split(',')[-1]).strip(), v.get(region_key) or tail_state)
            v['street'] = ','.join(head.split(',')[:-1]).strip(' ,')
    if v.get('city') and v.get('street'):
        repeated = re.compile(f"[\\s,.-]+{re.escape(fold(v['city']))}(?:[\\s,.-]+[A-Z]{{2}})?[\\s,.]*$")
        if repeated.search(fold(v['street'])):
            v['street'] = repeated.sub('', fold(v['street'])).strip(' ,-')
    for key in ('city', region_key):
        if re.search('\\d', v.get(key, '')):
            v[key] = ''
    if v.get(postal_key) and (not re.search('\\d', v[postal_key])):
        place = v.pop(postal_key)
        if fold(place) not in fold(v.get('street', '')):
            v['street'] = f"{v.get('street', '')}, {place}".strip(' ,')
    if re.search('\\s[-–]\\s', v.get('street', '')):
        v['street'] = re.sub('\\s+[-–]\\s+', ', ', v['street']).strip(' ,')
    cep_state = br_state_from_cep(v.get(postal_key, ''))
    if cep_state:
        v['country'] = v.get('country') or 'BRAZIL'
        if v['country'] == 'BRAZIL':
            v[region_key] = v.get(region_key) or cep_state
    if not v.get(region_key) and v.get('city') and (v.get('country') == 'BRAZIL'):
        from extract.places import br_states_for_city
        states = br_states_for_city(v['city'])
        if len(states) == 1:
            v[region_key] = states[0]
    if v.get(region_key) and fold(v[region_key]) in BR_UF and (v.get('country') in ('', 'BRAZIL')):
        v[region_key] = BR_UF[fold(v[region_key])]
    return {k: x for k, x in v.items() if x}

def _tidy_elsewhere(v: dict[str, str], postal_key: str, region_key: str) -> dict[str, str]:
    """Application parser or rule helper."""
    from extract import geo
    country = v['country']
    info = geo.postal(country) or {}
    keys = ['city', region_key, postal_key] + (['street'] if re.search('[A-Z]', info.get('pattern', '').replace('\\d', '')) else [])
    for key in keys:
        found = geo.postal_search(country, v.get(key, ''))
        if found:
            v[postal_key] = found
            if key != postal_key:
                v[key] = re.sub(f"(?i)\\b{re.escape(found)}\\b|\\b{re.escape(found.replace('HT', ''))}\\b", '', v[key]).strip(' ,-')
            break
    if v.get(postal_key) and geo.postal_ok(country, v[postal_key]):
        v[postal_key] = geo.normalize_postal(country, v[postal_key])
    if ',' in v.get('city', '') or re.search('\\s[-–]\\s', v.get('city', '')):
        split = geo.split_place(country, v['city'])
        if split:
            v['city'], v[region_key] = (split[0], v.get(region_key) or split[1])
    if not v.get('city') and ',' in v.get('street', ''):
        head, _, tail = v['street'].rpartition(',')
        reg = geo.region(country, tail, fuzzy=False)
        town = head.split(',')[-1]
        if reg and reg in geo.regions_for_place(country, town):
            v['city'], v[region_key] = (fold(town).strip(), v.get(region_key) or reg)
            v['street'] = ','.join(head.split(',')[:-1]).strip(' ,')
    if v.get('city') and v.get('street'):
        repeated = re.compile(f"[\\s,.-]+{re.escape(fold(v['city']))}[\\s,.]*$")
        if repeated.search(fold(v['street'])):
            v['street'] = repeated.sub('', fold(v['street'])).strip(' ,-')
    for key in ('city', region_key):
        if re.search('\\d', v.get(key, '')):
            v[key] = ''
    if v.get(postal_key) and (not re.search('\\d', v[postal_key])):
        place = v.pop(postal_key)
        if fold(place) not in fold(v.get('street', '')):
            v['street'] = f"{v.get('street', '')}, {place}".strip(' ,')
    if v.get(region_key):
        v[region_key] = geo.region(country, v[region_key]) or v[region_key]
    else:
        named = geo.region_from_postal(country, v.get(postal_key))
        likely = geo.likely_region(country, v.get('city'))
        if named or likely:
            v[region_key] = named or likely[0]
    return {k: x for k, x in v.items() if x}

def fix_street_type(street: str) -> str:
    """Application parser or rule helper."""
    first, _, rest = street.partition(' ')
    if fold(first) in STREET_TYPES or len(first) < 2:
        return street
    close = difflib.get_close_matches(fold(first), STREET_TYPES, n=1, cutoff=0.66)
    return f'{close[0]} {rest}'.strip() if close else street

def usps_street(street: str) -> str:
    words = re.sub('[.,]', ' ', street.upper()).split()
    return ' '.join((USPS_SUFFIXES.get(w, w) for w in words))
_UNIT = re.compile('[\\s,]+(?:APT|APARTMENT|APTO|UNIT|#)\\.?\\s*#?\\s*([A-Z0-9-]+)\\b.*$', re.I)

def split_street(values: dict[str, str]) -> None:
    """Application parser or rule helper."""
    street = values.get('street', '')
    for tail in (values.get('zip'), values.get('state'), values.get('city')):
        if tail:
            street = re.sub(f'[\\s,]+{re.escape(tail)}\\b.*$', '', street, flags=re.I)
    unit = _UNIT.search(street)
    if unit:
        values.setdefault('apt', unit.group(1).upper())
        if not values['apt']:
            values['apt'] = unit.group(1).upper()
        street = street[:unit.start()]
    values['street'] = street.strip(' ,')

def us_state_code(raw: str) -> str | None:
    raw = re.sub('[^A-Z ]', '', raw.upper()).strip()
    if raw in US_STATES.values():
        return raw
    best = difflib.get_close_matches(raw, US_STATES.keys(), n=1, cutoff=0.8)
    return US_STATES[best[0]] if best else None

@dataclass
class FieldReading:
    field_id: str
    status: str
    values: dict[str, str] = field(default_factory=dict)
    reads: list[dict[str, Any]] = field(default_factory=list)
    reason: str = ''
    amended: dict[str, dict[str, Any]] = field(default_factory=dict)

def _lines(words: list[OcrWord]) -> list[list[OcrWord]]:
    grouped: dict[tuple, list[OcrWord]] = {}
    for w in words:
        grouped.setdefault(w.line_id, []).append(w)
    return sorted(grouped.values(), key=lambda ws: min((w.top for w in ws)))

def locate(pages: list[tuple[Any, list[OcrWord]]], spec: dict[str, Any]) -> tuple[int, tuple[int, int, int, int]] | None:
    found = locate_span(pages, spec)
    return None if found is None else found[:2]

def _center(line: list[OcrWord]) -> float:
    return sorted((w.top + w.height / 2 for w in line))[len(line) // 2]

def locate_span(pages: list[tuple[Any, list[OcrWord]]], spec: dict[str, Any]):
    """Application parser or rule helper."""
    found = _locate(pages, spec)
    if found is None and spec.get('fallback'):
        found = _locate(pages, {k: v for k, v in spec.items() if k not in ('after', 'fallback')} | spec['fallback'])
    return found

def _locate(pages: list[tuple[Any, list[OcrWord]]], spec: dict[str, Any]):
    anchor = re.compile(spec['anchor'])
    after = re.compile(spec['after']) if spec.get('after') else None
    before = re.compile(spec['before']) if spec.get('before') else None
    seen_section = after is None
    remaining = spec.get('occurrence', 1)
    for page_index, (gray, words) in enumerate(pages):
        lines = _lines(words)
        for i, line in enumerate(lines):
            text = ' '.join((w.text for w in line))
            if not seen_section:
                if after.search(text):
                    seen_section = True
                else:
                    continue
            if before is not None and seen_section and before.search(text) and (not (after and after.search(text))):
                return None
            match = anchor.search(text)
            if not match:
                continue
            remaining -= 1
            if remaining > 0:
                continue
            pos, end_word = (0, line[-1])
            for w in line:
                if pos + len(w.text) >= match.end():
                    end_word = w
                    break
                pos += len(w.text) + 1
            height = max(1, int(sorted((w.height for w in line))[len(line) // 2]))
            label_words, p = ([], 0)
            for w in line:
                if p < match.end() and p + len(w.text) > match.start():
                    label_words.append(w)
                p += len(w.text) + 1
            label_words = label_words or line
            label_mid = sorted((w.top + w.height / 2 for w in label_words))[len(label_words) // 2]
            above, below = (0.35, 0.35) if spec.get('whole_line') else (0.9, 0.4)
            history_row = spec.get('lines', 1) > 1 or spec.get('whole_line')
            if history_row:
                later, section_end = (lines[i + 1:], None)
                if before is not None:
                    cut = next((k for k, ln in enumerate(later) if before.search(' '.join((w.text for w in ln)))), None)
                    if cut is not None:
                        section_end = min((w.top for w in later[cut])) - int(0.2 * height)
                        later = later[:cut]
                rows = [line] + [ln for ln in later if len(ln) >= 2 and _center(ln) > label_mid + 0.6 * height]
                span = sorted(rows, key=_center)[:spec.get('lines', 1)]
                top = min((w.top for w in label_words)) - int(above * height)
                last = span[-1] if len(span) > 1 else line
                bottom = sorted((w.top + w.height for w in last))[3 * len(last) // 4] + int(below * height)
            else:
                span = lines[i:i + 1]
                top = min((w.top for w in span[0])) - int(above * height)
                bottom = max((w.top + w.height for w in span[-1])) + int(below * height)
            following = next((ln for ln in lines[i + 1:] if anchor.search(' '.join((w.text for w in ln))) and _center(ln) > label_mid + 0.6 * height), None)
            if history_row and following is not None:
                m2 = anchor.search(' '.join((w.text for w in following)))
                q, next_label = (0, [])
                for w in following:
                    if q < m2.end() and q + len(w.text) > m2.start():
                        next_label.append(w)
                    q += len(w.text) + 1
                bottom = min((w.top for w in next_label or following)) - int(0.25 * height)
            elif history_row and len(span) > 1:
                below_rows = [ln for ln in later if _center(ln) > _center(span[-1]) + 0.6 * height]
                if below_rows and _center(below_rows[0]) - _center(span[-1]) < 2.5 * height:
                    bottom = max(bottom, sorted((w.top + w.height for w in below_rows[0]))[3 * len(below_rows[0]) // 4] + int(below * height))
                    if section_end is not None:
                        bottom = min(bottom, section_end)
            if len(span) < spec.get('lines', 1):
                bottom += int((spec.get('lines', 1) - len(span)) * 2.2 * height)
            if history_row and section_end is not None:
                bottom = min(bottom, section_end)
            left = 0 if spec.get('whole_line') else end_word.left + end_word.width if spec.get('lines', 1) == 1 else 0
            label_chars = match.end() - pos
            if spec.get('lines', 1) == 1 and (not spec.get('whole_line')) and (0 < label_chars < len(end_word.text)):
                printed = sorted((w.width / len(w.text) for w in line if w is not end_word and len(w.text) >= 3))
                char_w = printed[len(printed) // 2] if printed else end_word.width / len(end_word.text)
                left = end_word.left + int(min(end_word.width, char_w * label_chars))
            box = (max(0, left), max(0, top), gray.width, min(gray.height, bottom))
            return (page_index, box, _center(line))
    return None

def answer_ink(gray: Any, box: tuple[int, int, int, int]) -> float:
    """Application parser or rule helper."""
    import numpy as np
    arr = np.asarray(gray.crop(box)) < 128
    h, w = arr.shape
    if h == 0 or w == 0:
        return 0.0
    arr = arr.copy()
    arr[arr.sum(axis=1) > 0.3 * w, :] = False
    arr[:, arr.sum(axis=0) > 0.6 * h] = False
    return float(arr.sum()) / (h * h)

def _drop_repeated_history_lines(passes: list) -> list:
    """Application parser or rule helper."""
    key_parts = {'prior_address': 'street', 'prior_employer': 'employer', 'child': 'name'}

    def key(group: str, read: dict) -> str:
        raw = fold(str(read.get(key_parts[group]) or '')).split(',')[0]
        if raw.strip() in ('', 'NULL', 'NONE'):
            return ''
        if group == 'prior_address':
            raw = usps_street(raw)
        k = re.sub('[^A-Z0-9]', '', raw)[:12]
        if group == 'prior_employer':
            k += re.sub('\\D', '', str(read.get('date_from') or ''))
        return k
    seen: dict[str, set[str]] = {}
    out = []
    for spec, located, typed_here, reading in passes:
        group = re.fullmatch('(prior_address|prior_employer|child)_\\d', spec['id'])
        if group and reading.reads:
            keys = [k for k in (key(group.group(1), r) for r in reading.reads) if k]
            earlier = seen.setdefault(group.group(1), set())
            if keys and all((k in earlier for k in keys)):
                reading = FieldReading(spec['id'], 'blank', reads=reading.reads, reason='repeats the line above (blank line)')
            elif reading.status == 'ok' and keys:
                earlier.add(keys[0])
            elif keys:
                earlier.update(keys)
        out.append((spec, located, typed_here, reading))
    return out

def ambiguous_dates(reading: 'FieldReading', spec: dict[str, Any]) -> dict[str, list[str]]:
    """Application parser or rule helper."""
    out: dict[str, list[str]] = {}
    for sub, value in (reading.values or {}).items():
        if not (sub.startswith('date') or spec.get('kind') == 'date') or not re.fullmatch('\\d{4}-\\d{2}-\\d{2}', str(value)):
            continue
        y, m, d = (int(x) for x in str(value).split('-'))
        other = _valid(y, d, m)
        if other and other != value and (d <= 12):
            out[sub] = [str(value), other]
    return out

def _valid(y: int, mo: int, d: int) -> str | None:
    try:
        return date(y, mo, d).isoformat()
    except ValueError:
        return None

def parse_date(raw: str | None, order: str='dmy') -> str | None:
    """Application parser or rule helper."""
    if not raw:
        return None
    from extract.base import normalize_date
    if re.search('[A-Za-z]{3,}', raw) and (not re.search('\\b(de|DE|De)\\b', raw)):
        english = normalize_date(raw.title())
        if english:
            return english
    s = re.sub('\\b(' + '|'.join(DATE_FILLERS) + ')\\b', ' ', re.sub('_+', ' ', fold(raw))).replace('.', '/').replace('-', '/')
    s = re.sub('\\s*/[\\s/]*', '/', s)
    m = re.search('(\\d{1,2})\\s*/?\\s+(\\d{1,2})\\s*/?\\s+(\\d{4})|(\\d{1,2})\\s*/\\s*(\\d{1,2})\\s*/\\s*(\\d{4})', s)
    if m:
        a, b, y = (int(g) for g in (m.groups()[:3] if m.group(1) else m.groups()[3:]))
        brazilian, us = (_valid(y, b, a), _valid(y, a, b))
        first, second = (us, brazilian) if order == 'mdy' else (brazilian, us)
        return first or second
    m = re.search('(\\d{1,2})\\s+([A-Z]+)\\s+(\\d{4})', s)
    if not m or m.group(2) not in MONTHS:
        return None
    return _valid(int(m.group(3)), MONTHS[m.group(2)], int(m.group(1)))

def check_us_zip(values: dict[str, str]) -> tuple[bool, str]:
    """Application parser or rule helper."""
    import zipcodes
    zip5 = re.sub('\\D', '', values.get('zip', ''))[:5]
    if not zip5 and values.get('city') and values.get('state'):
        state = us_state_code(values['state'])
        if state and zipcodes.filter_by(city=values['city'].title(), state=state):
            values['state'] = state
            values['city'] = values['city'].upper()
            return (True, '')
        return (False, f"no ZIP, and {values.get('city')!r}, {values.get('state')!r} is ficticioq a known US city")
    if not zip5 and (not values.get('state')) and values.get('street'):
        values['city'] = values.get('city', '').upper()
        return (True, '')
    if len(zip5) != 5 or not zipcodes.is_real(zip5):
        return (False, f"ZIP {values.get('zip')!r} is ficticioq a real US ZIP")
    info = zipcodes.matching(zip5)[0]
    values['zip'] = zip5
    city = values.get('city', '')
    official = info['city'].upper()
    if city and difflib.SequenceMatcher(None, city.upper(), official).ratio() < 0.7:
        return (False, f'city {city!r} does ficticioq match ZIP {zip5} ({official})')
    values['city'] = official
    values['state'] = info['state']
    return (True, '')

def _norm_text(v: Any) -> str:
    if v is None:
        return ''
    return re.sub('\\s+', ' ', str(v)).strip().upper()

def normalize(kind: str, raw: dict[str, Any], order: str='dmy') -> tuple[dict[str, str], str]:
    """Application parser or rule helper."""
    values = {k: _norm_text(raw.get(k)) for k in KINDS[kind]['props']}
    values = {k: v for k, v in values.items() if v and v not in ('NULL', 'NONE', 'N/A') and set(re.findall('[A-Z0-9]+', v)) - {'NULL', 'NONE', 'DE', 'A'}}
    if not values:
        return (values, '')
    for key in [k for k in values if k.startswith('date') or (k == 'value' and kind == 'date')]:
        if key == 'date_to' and any((word in fold(values[key]) for word in PRESENT_WORDS)):
            values[key] = 'PRESENT'
            continue
        parsed = parse_date(values[key], order)
        if parsed is None:
            return (values, f'{key} {values[key]!r} is ficticioq a valid date')
        values[key] = parsed
    if kind == 'number':
        raw_number = values.get('value', '')
        if re.fullmatch('(ZERO|NENHUM|NENHUMA|NUNCA)\\b.*', raw_number):
            raw_number = '0'
        digits = re.sub('\\D', '', raw_number)
        if not digits:
            return (values, f"{values.get('value')!r} is ficticioq a number")
        values['value'] = str(int(digits))
    if kind == 'us_place':
        if ',' in values.get('city', ''):
            city, _, rest = values['city'].partition(',')
            values['city'] = city.strip()
            values.setdefault('state', rest.strip())
        code = us_state_code(values.get('state', ''))
        if code is None:
            return (values, f"state {values.get('state')!r} is ficticioq a US state")
        values['state'] = code
    if kind == 'height':
        from extract.base import cm_to_feet_inches
        raw_height = values.get('value', '')
        m = re.search('([4-7])\\s*[\'’´`\\"]+\\s*-?\\s*(\\d{1,2})', raw_height)
        metric = re.search('\\b1\\s*[,.]\\s*(\\d{2})\\b', raw_height)
        if m and int(m.group(2)) < 12:
            values['value'] = f'''{m.group(1)}'{int(m.group(2))}"'''
        elif metric:
            values['value'] = cm_to_feet_inches(100 + int(metric.group(1)))
        else:
            return (values, f'height {raw_height!r} ficticioq understood')
    if kind == 'weight':
        digits = re.sub('[^\\d]', '', values.get('value', ''))
        if not digits:
            return (values, f"weight {values.get('value')!r} is ficticioq a number")
        unit = values.pop('unit', '')
        if unit.startswith(('KG', 'KL', 'QUIL', 'KILO')):
            from extract.base import kg_to_lbs
            values['value'] = str(kg_to_lbs(float(digits)))
        elif unit and (not unit.startswith(('LB', 'LIB', 'POUND'))):
            return (values, f'weight unit {unit!r} ficticioq understood')
        else:
            values['value'] = str(int(digits))
        if not 60 <= int(values['value']) <= 400:
            return (values, f"weight {values['value']} lbs is implausible")
    if kind == 'yes_no_text':
        first = (re.findall('[A-Z]+', fold(values.get('value', ''))) or [''])[0]
        if first in YES_WORDS:
            values['value'] = 'Yes'
        elif first in NO_WORDS:
            values['value'] = 'No'
        else:
            return (values, f"{values.get('value')!r} is ficticioq a clear yes/no")
    if kind == 'hair_color':
        color = fold(values.get('value', ''))
        mapped = next((v for k, v in HAIR_COLORS.items() if re.search(f'\\b{k}\\b', color)), None)
        if mapped is None:
            return (values, f'hair color {color!r} ficticioq in the known vocabulary')
        values['value'] = mapped
    if kind == 'foreign_address' or (kind == 'employer' and values.get('country') not in (None, '', 'USA', 'US')) or (kind == 'employer' and re.fullmatch('\\d{5}-?\\d{3}', values.get('zip', ''))):
        values.update(tidy_foreign_parts(kind, values))
        for key in [k for k in list(values) if not values[k]]:
            del values[key]
    if kind == 'foreign_address' and values.get('street'):
        values['street'] = fix_street_type(values['street'])
    if kind == 'address_range':
        us_zip = re.fullmatch('\\d{5}(-\\d{4})?', values.get('zip', '')) and us_state_code(values.get('state', '')) is not None
        if us_zip:
            ok, why = check_us_zip(values)
            if not ok:
                return (values, why)
            if values.get('street'):
                split_street(values)
                values['street'] = usps_street(values['street'])
            values['country'] = 'USA'
        else:
            values.update(tidy_foreign_parts('foreign_address', {**values, 'province': values.get('state', ''), 'postal_code': values.get('zip', '')}))
            values.pop('zip', None)
            values.pop('state', None)
            if not (values.get('city') or values.get('street')):
                return (values, 'no address to read')
    if kind == 'child':
        from extract.names import fold_name, looks_like_name
        name = fold_name(values.get('name', ''))
        if not looks_like_name(name):
            return (values, f"{values.get('name')!r} is ficticioq a full name")
        values['name'] = name
        if values.get('a_number'):
            digits = re.sub('\\D', '', values['a_number'])
            if len(digits) not in (8, 9):
                values.pop('a_number')
            else:
                values['a_number'] = digits.zfill(9)
        if values.get('dob'):
            parsed = parse_date(values['dob'], order)
            if parsed is None:
                return (values, f"date of birth {values['dob']!r} is ficticioq a valid date")
            values['dob'] = parsed
        if values.get('country'):
            values['country'] = COUNTRIES.get(fold(values['country']).strip(' .'), values['country'])
    if kind in ('employer', 'address_range', 'foreign_address') and re.fullmatch('\\d{4}-\\d{2}-\\d{2}', values.get('date_from', '')) and re.fullmatch('\\d{4}-\\d{2}-\\d{2}', values.get('date_to', '')) and (values['date_to'] < values['date_from']):
        return (values, 'the dates run backwards (from {} to {})'.format(*(f'{d[5:7]}/{d[8:]}/{d[:4]}' for d in (values['date_from'], values['date_to']))))
    if kind == 'eye_color':
        color = fold(values.get('value', ''))
        mapped = next((v for k, v in sorted(EYE_COLORS.items(), key=lambda kv: -len(kv[0])) if re.search(f'(?<![A-Z]){k}(?![A-Z])', color)), None)
        if mapped is None:
            return (values, f'eye color {color!r} ficticioq in the known vocabulary')
        values['value'] = mapped
    if kind == 'person_name':
        from extract.names import fold_name, looks_like_name
        name = fold_name(values.get('value', ''))
        if not looks_like_name(name):
            return (values, f"{values.get('value')!r} is ficticioq a full name")
        values['value'] = name
    is_us_employer = kind == 'employer' and values.get('zip') and (values.get('country', '') in US_COUNTRY_WORDS) and (not re.fullmatch('\\d{5}-?\\d{3}', values['zip'])) and (values.get('country') != 'BRAZIL')
    if is_us_employer:
        values['country'] = 'USA'
    if kind in ('us_address', 'address_history') or is_us_employer:
        ok, why = check_us_zip(values)
        if not ok:
            return (values, why)
        if values.get('street'):
            split_street(values)
            values['street'] = usps_street(values['street'])
        if values.get('apt'):
            values['apt'] = re.sub('^(APT|APARTMENT|APTO|UNIT|NO|N)\\.?\\s*#?\\s*|^#\\s*', '', values['apt']).strip() or values['apt']
    return (values, '')

def _alnum(v: str) -> str:
    return re.sub('[^A-Z0-9]', '', v)

def reads_agree(kind: str, a: dict[str, str], b: dict[str, str]) -> bool:
    """Application parser or rule helper."""
    if set(a) != set(b):
        return False
    for key in a:
        x, y = (_alnum(a[key]), _alnum(b[key]))
        if kind in EXACT_KINDS or key in EXACT_KEYS:
            if x != y:
                return False
        elif not (x.startswith(y) or y.startswith(x) or difflib.SequenceMatcher(None, x, y).ratio() >= 0.85):
            return False
    return True

def _prefer(a: dict[str, str], b: dict[str, str], kind: str='text') -> dict[str, str]:
    """Application parser or rule helper."""
    pick = max if kind == 'person_name' else min
    return {k: pick(a[k], b[k], key=len) for k in a}

def _prompt(spec: dict[str, Any]) -> str:
    kind = KINDS[spec.get('kind', 'text')]
    return f"This image is part of a scanned {LANGUAGE_NAMES.get(spec.get('_language', 'pt'), 'Portuguese')} immigration intake form: a printed label followed by a HANDWRITTEN answer. The printed label asks for: {spec['describe']}. Expected answer: {kind['hint']}.\nTranscribe ONLY the handwritten answer, character by character, exactly as written: original spelling and accents, no translation, no correction, no guessing. Ignore faint MIRRORED/backwards text -- that is ink bleeding through from the back of the page, ficticioq an answer. A word written ABOVE the line (often with a caret) is an insertion: put it where it is inserted; a crossed-out word is deleted -- leave it out. Use null for anything blank or ficticioq readable with certainty."

def _schema(kind: str) -> dict[str, Any]:
    props = KINDS[kind]['props']
    return {'type': 'object', 'properties': {p: {'type': ['string', 'null']} for p in props}, 'required': props}
AMEND_KINDS = ('employer', 'person_name', 'child', 'address_range', 'address_history', 'foreign_address')
_STRUCK = re.compile('\\[\\[\\s*(.+?)\\s*\\]\\]')
_ADDED = re.compile('<<\\s*(.+?)\\s*>>')
_INSERTED = re.compile("[A-Za-zÀ-ÿ][A-Za-zÀ-ÿ' .-]{1,38}")

def _amend_prompt(spec: dict[str, Any]) -> str:
    kind = KINDS[spec.get('kind', 'text')]
    return f"This image is part of a scanned form: a printed label followed by a HANDWRITTEN answer. The printed label asks for: {spec['describe']}. Expected answer: {kind['hint']}.\nTranscribe ONLY the handwritten answer, character by character, exactly as written, in the same parts as before. This time do ficticioq apply any correction the writer made: write every word that is crossed out or scribbled over inside double square brackets, like [[word]], at the place in the answer where it stands, and leave the word written above the line OUT of the answer. Put any word the writer wrote above the line instead of on it (small, between the printed lines, with or without a caret) in written_above_the_line, the word only. Use null when there is none."

def amendments(kind: str, raw: dict[str, Any], reads: list[dict[str, Any]], values: dict[str, str]) -> dict[str, dict[str, Any]]:
    """Application parser or rule helper."""
    above = _norm_text(raw.get('written_above_the_line'))
    above = above if above not in ('', 'NULL', 'NONE') and _INSERTED.fullmatch(above) else ''
    out: dict[str, dict[str, Any]] = {}
    for sub in KINDS[kind]['props']:
        marked = raw.get(sub)
        if not isinstance(marked, str) or '[[' not in marked:
            continue
        struck = [w for w in _STRUCK.findall(marked) if _alnum(_norm_text(w))]
        if not struck:
            continue
        plain = _norm_text(_STRUCK.sub('\\1', marked))
        firsts = [_norm_text(c) for c in [values.get(sub)] + [r.get(sub) for r in reads if isinstance(r, dict)] if c]
        before = next((c for c in firsts if _alnum(fold(c)) == _alnum(fold(plain))), None)
        if before is None:
            continue
        added = [above] if above and len(struck) == 1 else []
        parts = [(added[0] if added else '') if _STRUCK.fullmatch(piece) else piece for piece in re.split('(\\[\\[.+?\\]\\])', marked)]
        after = _norm_text(' '.join(parts))
        if after and _alnum(after) != _alnum(before):
            out[sub] = {'before': before, 'after': after, 'crossed_out': [_norm_text(w) for w in struck], 'added': [_norm_text(w) for w in added]}
    return out

def second_look(spec: dict[str, Any], gray: Any, box: tuple[int, int, int, int], reads: list[dict[str, Any]], values: dict[str, str], model_call) -> dict[str, dict[str, Any]]:
    """Application parser or rule helper."""
    from PIL import Image
    kind = spec.get('kind', 'text')
    if kind not in AMEND_KINDS or not reads:
        return {}
    left, top, right, bottom = box
    crop = gray.crop((max(0, left), max(0, top), right, min(gray.height, bottom)))
    crop = crop.resize((crop.width * 2, crop.height * 2), Image.LANCZOS)
    schema = _schema(kind)
    schema['properties']['written_above_the_line'] = {'type': ['string', 'null']}
    schema['required'] = schema['required'] + ['written_above_the_line']
    try:
        raw = model_call(_amend_prompt(spec), crop, schema)
    except Exception:
        return {}
    return amendments(kind, raw, reads, values) if isinstance(raw, dict) else {}

def read_field(pages: list[tuple[Any, list[OcrWord]]], spec: dict[str, Any], model_call) -> FieldReading:
    """Application parser or rule helper."""
    from PIL import Image
    kind = spec.get('kind', 'text')
    located = locate(pages, spec)
    if located is None:
        return FieldReading(spec['id'], 'ficticioq_found', reason='printed label ficticioq found on any page')
    page_index, box = located
    gray = pages[page_index][0]
    if answer_ink(gray, box) < spec.get('blank_ink', BLANK_INK):
        return FieldReading(spec['id'], 'blank', reason='no handwriting in the answer area')
    reads: list[dict[str, Any]] = []
    for scale, pad in ((2, 0), (3, 12)):
        left, top, right, bottom = box
        crop = gray.crop((max(0, left - pad), max(0, top - pad), right, min(gray.height, bottom + pad)))
        crop = crop.resize((crop.width * scale, crop.height * scale), Image.LANCZOS)
        try:
            raw = model_call(_prompt(spec), crop, _schema(kind))
        except Exception as exc:
            return FieldReading(spec['id'], 'unread', reads=reads, reason=f'model gave no usable answer: {exc}')
        reads.append(raw)
        _, error = normalize(kind, raw)
        if error:
            break
    reading = judge_reads(spec, reads)
    reading.amended = second_look(spec, gray, box, reads, reading.values, model_call)
    if reading.status == 'ok':
        for sub, a in reading.amended.items():
            reading.values[sub] = a['after']
    return reading

def judge_reads(spec: dict[str, Any], reads: list[dict[str, Any]], order: str='dmy') -> FieldReading:
    """Application parser or rule helper."""
    kind = spec.get('kind', 'text')
    normalized = []
    for raw in reads:
        values, error = normalize(kind, raw, order)
        if error:
            return FieldReading(spec['id'], 'invalid', values=values, reads=reads, reason=error)
        normalized.append(values)
    if len(normalized) < 2:
        return FieldReading(spec['id'], 'unread', reads=reads, reason='fewer than two reads')
    if not normalized[0] and (not normalized[1]):
        return FieldReading(spec['id'], 'blank', reads=reads, reason='both reads found nothing written')
    if not reads_agree(kind, normalized[0], normalized[1]):
        return FieldReading(spec['id'], 'disagree', values=normalized[0], reads=reads, reason='two independent reads differ')
    return FieldReading(spec['id'], 'ok', values=_prefer(normalized[0], normalized[1], spec.get('kind', 'text')), reads=reads)

def to_english(fact_key: str, value: str, language: str='pt') -> str:
    """Application parser or rule helper."""
    if fact_key.endswith('occupation'):
        return translate_occupation(value, language)
    if fact_key.endswith('foreign_street'):
        return fix_street_type(value)
    if fact_key.endswith(('country', 'country_of_birth')):
        return COUNTRIES.get(fold(value).strip(' .'), value)
    return value

def _typed_in(typed, page: int, box, pad: int=12, words: list[OcrWord] | None=None, anchor_center: float | None=None, single_line: bool=False) -> list[str]:
    """Application parser or rule helper."""
    x0, y0, x1, y1 = box
    on_page = [t for t in typed if t.page == page]
    hits = [t for t in on_page if t.box[0] < x1 + pad and t.box[2] > x0 - pad and (t.box[1] < y1 + pad) and (t.box[3] > y0 - pad)]
    if single_line and words is not None and (anchor_center is not None):
        printed = [_center(line) for line in _lines(words)]

        def nearest(t):
            cy = (t.box[1] + t.box[3]) / 2
            return min(printed, key=lambda c: abs(c - cy)) if printed else None
        hits = [t for t in hits if nearest(t) is not None and abs(nearest(t) - anchor_center) < 15]
    return [t.text for t in hits]

def check_against_typed(reading: FieldReading, typed_text: list[str]) -> FieldReading:
    """Application parser or rule helper."""
    words = [fold(t) for t in re.findall('[A-Z0-9À-Ü]+', ' '.join(typed_text).upper())]
    typed_tokens = set(words)
    from extract.places import BR_UF
    for code in [w for w in words if w in BR_UF]:
        typed_tokens |= set(BR_UF[code].split()) | {'BRAZIL', 'BRASIL'}
    joined = {''.join(words[i:j]) for i in range(len(words)) for j in range(i + 2, min(len(words), i + 4) + 1)}
    for read in reading.reads[:1]:
        for key, value in read.items():
            if not value or str(value).lower() == 'null':
                continue
            if key == 'country' and str(value).upper() in US_COUNTRY_WORDS:
                continue
            for token in re.findall('[A-Z0-9À-Ü]+', str(value).upper()):
                if fold(token) in typed_tokens or fold(token) in TEMPLATE_WORDS or fold(token) in joined:
                    continue
                if token.isdigit() and token.lstrip('0') in {t.lstrip('0') for t in typed_tokens}:
                    continue
                return FieldReading(reading.field_id, 'disagree', reading.values, reading.reads, f"model read {key}={value!r}, but {token!r} is ficticioq in the typed answer {' | '.join(typed_text)!r}")
    return reading

def typed_foreign_place(typed_text: list[str]) -> dict[str, str] | None:
    """Application parser or rule helper."""
    from extract.places import BR_UF
    words = [t for t in typed_text if not re.fullmatch('\\d{1,4}', t.strip())]
    m = re.fullmatch("\\s*([A-Za-zÀ-ÿ' ]+?)\\s*[-,/]?\\s+([A-Za-z]{2})\\.?\\s*", ' '.join(words))
    if not m or m.group(2).upper() not in BR_UF:
        return None
    return {'city': fold(m.group(1)).strip(), 'province': BR_UF[m.group(2).upper()], 'country': 'BRAZIL'}

def read_typed_foreign(spec: dict[str, Any], typed_text: list[str], pages, model_call) -> FieldReading:
    """Application parser or rule helper."""
    place = typed_foreign_place(typed_text) or {}
    reading = read_field(pages, spec, model_call)
    reads = [{k: v for k, v in r.items() if k.startswith('date')} | place for r in reading.reads]
    if len(reads) < 2:
        return FieldReading(spec['id'], reading.status, place, reading.reads, reading.reason)
    dates_only = FieldReading(spec['id'], 'ok', reads=[{k: v for k, v in r.items() if k.startswith('date')} for r in reading.reads])
    guard = check_against_typed(dates_only, typed_text)
    judged = judge_reads(spec, reads)
    if guard.status != 'ok':
        judged.status, judged.reason = (guard.status, guard.reason)
    if judged.status == 'ok':
        judged.reason = 'place typed by the client (city + Brazilian state code); dates read by the model'
    return judged

def read_typed(spec: dict[str, Any], typed_text: list[str], order: str='dmy') -> FieldReading:
    """Application parser or rule helper."""
    kind = spec.get('kind', 'text')
    if kind == 'date':
        pieces = [t for t in typed_text if re.search('\\d', t) or fold(t).strip(' .') in MONTHS or fold(t).strip() in DATE_FILLERS]
        typed_text = pieces or typed_text
    joined = ' '.join(typed_text)
    raw = {'value': joined}
    if kind == 'weight':
        unit = re.search('[A-Za-z]+', joined)
        raw = {'value': re.sub('[^\\d]', '', joined.split(',')[0]), 'unit': unit.group(0) if unit else ''}
    reading = judge_reads(spec, [raw, dict(raw)], order)
    if reading.status == 'ok':
        reading.reason = 'typed by the client (no model needed)'
    return reading
_NUMERIC_DATE = re.compile('\\b(\\d{1,2})\\s*[/.\\-]\\s*(\\d{1,2})\\s*[/.\\-]\\s*(\\d{4})\\b|\\b(\\d{1,2})\\s+DE\\s+(\\d{1,2})\\s+DE\\s+(\\d{4})\\b')

def date_order(readings: list[FieldReading], specs: dict[str, dict]) -> tuple[str, int, int]:
    """Application parser or rule helper."""
    dmy = mdy = 0
    for reading in readings:
        for raw in reading.reads[:1]:
            for key, value in raw.items():
                if not value or not (key.startswith('date') or specs[reading.field_id].get('kind') == 'date'):
                    continue
                for m in _NUMERIC_DATE.finditer(str(value).upper()):
                    a, b, y = (int(g) for g in (m.groups()[:3] if m.group(1) else m.groups()[3:]))
                    as_dmy, as_mdy = (_valid(y, b, a), _valid(y, a, b))
                    dmy += bool(as_dmy and (not as_mdy))
                    mdy += bool(as_mdy and (not as_dmy))
    return ('mdy' if mdy > dmy else 'dmy', dmy, mdy)

def read_text_fields(pages: list[tuple[Any, list[OcrWord]]], specs: list[dict[str, Any]], model_call, evidence: dict[str, dict[str, Any]] | None=None, typed: list | None=None, language: str='pt'):
    """Application parser or rule helper."""
    from extract.base import ExtractedField
    typed = typed or []
    digital_pages = {t.page for t in typed}
    specs = [dict(spec, _language=language) for spec in specs]
    fields: list[ExtractedField] = []
    unread: dict[str, str] = {}
    passes = []
    for spec in specs:
        span = locate_span(pages, spec)
        located = span[:2] if span else None
        typed_here: list[str] = []
        if span and span[0] in digital_pages:
            typed_here = _typed_in(typed, span[0], span[1], words=pages[span[0]][1], anchor_center=span[2], single_line=spec.get('lines', 1) == 1)
            if not typed_here:
                reading = FieldReading(spec['id'], 'blank', reason='no typed answer in this field')
            elif KINDS[spec.get('kind', 'text')]['props'][0] == 'value':
                reading = read_typed(spec, typed_here)
            elif spec.get('kind') == 'foreign_address' and typed_foreign_place(typed_here):
                reading = read_typed_foreign(spec, typed_here, pages, model_call)
            else:
                reading = check_against_typed(read_field(pages, spec, model_call), typed_here)
        else:
            reading = read_field(pages, spec, model_call)
        passes.append((spec, located, typed_here, reading))
    order, dmy_votes, mdy_votes = date_order([r for *_, r in passes], {s['id']: s for s in specs})
    if order == 'mdy':
        rejudged = []
        for spec, located, typed_here, reading in passes:
            if len(reading.reads) >= 2:
                amended = reading.amended
                reading = judge_reads(spec, reading.reads, order)
                reading.amended = amended
                if typed_here:
                    reading = check_against_typed(reading, typed_here)
                if reading.status == 'ok':
                    reading.reason = f'dates read month-first: this client wrote {mdy_votes} unambiguous date(s) that way, {dmy_votes} day-first'
                    for sub, a in amended.items():
                        reading.values[sub] = a['after']
            rejudged.append((spec, located, typed_here, reading))
        passes = rejudged
    passes = _drop_repeated_history_lines(passes)
    for spec, located, typed_here, reading in passes:
        if evidence is not None:
            evidence[spec['id']] = {'kind': 'text', 'input_kind': spec.get('kind', 'text'), 'describe': spec['describe'], 'facts': spec['facts'], 'page': located[0] if located else None, 'box': list(located[1]) if located else None, 'status': reading.status, 'values': reading.values, 'reads': reading.reads, 'reason': reading.reason, 'typed': typed_here, 'date_order': order, 'ambiguous': ambiguous_dates(reading, spec) if dmy_votes and mdy_votes else {}, 'amended': reading.amended}
        if reading.status == 'blank':
            if spec.get('record_blank'):
                fields.append(ExtractedField(f"questionnaire.blank.{spec['id']}", '', 'Yes', 0.7))
            continue
        if reading.status != 'ok':
            shown = '; '.join((json.dumps({k: v for k, v in r.items() if v}, ensure_ascii=False) for r in reading.reads))
            unread[spec['id']] = f'handwriting {reading.status}: {reading.reason}' + (f' -- reads: {shown}' if shown else '')
            continue
        for sub, fact_key in spec['facts'].items():
            value = reading.values.get(sub)
            if not value:
                continue
            fields.append(ExtractedField(fact_key, value, to_english(fact_key, value, language), 0.7))
            if sub == 'apt' and fact_key.endswith('_apt'):
                fields.append(ExtractedField(fact_key[:-len('_apt')] + '_unit_type', 'APT', 'APT', 0.7))
    return (fields, unread)

def ollama_model_call(prompt: str, image: Any, schema: dict[str, Any]) -> dict[str, Any]:
    from vision import generate
    return json.loads(generate(prompt, [image], json_schema=schema, work_dir='clients/.work'))
