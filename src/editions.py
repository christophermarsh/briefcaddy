"""A new USCIS form edition: what the nightly check reads, and what it holds.

When the live check (src/maintenance.py, item type uscis_form_edition) finds that USCIS publishes a newer
edition than the template we fill, three things follow:

  - Keeping current says "USCIS changed Form I-485 on 09/18/2026; packets that use it wait for the update";
  - every packet that uses the form says the same on its packet tab, and "Ready to mail?" blocks it
    (src/prefile.py) -- unless USCIS's own page lets the old edition be used a while longer;
  - the firm is told to contact the provider about the required update; responsibility is not evidence that an update job is running.

The grace period is never inferred. The form's USCIS page carries an alert above the form details when an
edition changes (read 10/02/2026: uscis.gov/i-485, "There is no grace period for the revised edition";
uscis.gov/i-864, "USCIS is providing a 30-day grace period during which we will accept the 10/17/24 edition"
and "Beginning Oct. 1, 2026, we will only accept the 08/24/26 edition"). read_page() copies the sentences of
the alert that is about the new edition, word for word, with the page and the date it was read, and takes a
date only from a sentence that states one in one of the few shapes below. Anything else is not a grace
period: the packet waits.

  "... only accept the <new> edition ... on or after <date>" / "Beginning <date>, we will only accept the <new>"
                                      the old edition is accepted before <date>;
  "... will not process any <old> edition ... on or after <date>" (the sentence names our edition)
                                      the same;
  "... <the old edition's date> ... until <date>" / "through <date>"
                                      "until" is read as: not on <date> itself (the cautious reading); "through" includes it;
  "There is no grace period"          the old edition is not accepted at all.

A sentence counts only when it names our form (Form I-864) or names no form: an alert can be about another form. A sentence that says
USCIS will NOT reject an edition gives no refusal date. The old edition must be named by its own date: "the previous edition" is not
ours. When the alert about the new edition yields no date we can read ("on or before", "by", two-digit years, "September 18th"), the
whole alert is shown and a person reads it: the packet waits.

A date counts against the day the packet is mailed (today), as USCIS counts postmarks. A night on which the check itself fails keeps
last night's answer (carry()): an outage never releases a held packet.
"""

from __future__ import annotations

import html
import re
from datetime import date, timedelta
from typing import Any

import clock

MONTHS = {"jan": 1, "feb": 2, "mar": 3, "apr": 4, "may": 5, "jun": 6, "jul": 7, "aug": 8, "sep": 9, "oct": 10, "nov": 11, "dec": 12}
_DATE = r"(?:(?P<mon>[A-Z][a-z]{2,8})\.?\s+(?P<day>\d{1,2}),\s+(?P<year>\d{4})|(?P<m2>\d{1,2})/(?P<d2>\d{1,2})/(?P<y2>\d{4}))"
_DATE_RE = re.compile(_DATE)
_ALERT = re.compile(r"(?is)<div[^>]*class=\"[^\"]*messages__text[^\"]*\"[^>]*>(.*?)</div>")
_PUBLISHED = re.compile(r"\bOn\s+" + _DATE + r",?\s+USCIS\s+published\b")
_PAGE_EDITION = re.compile(r"Edition\s+Date\s+(\d{2}/\d{2}/\d{2})")


def mdy(iso: str | None) -> str:
    """2026-09-18 -> 09/18/2026 (the screens' date)."""
    return f"{iso[5:7]}/{iso[8:10]}/{iso[:4]}" if iso else ""


def _date_of(m: re.Match[str]) -> date | None:
    try:
        if m.group("mon"):
            return date(int(m.group("year")), MONTHS[m.group("mon")[:3].lower()], int(m.group("day")))
        return date(int(m.group("y2")), int(m.group("m2")), int(m.group("d2")))
    except (KeyError, ValueError):
        return None


def form_name(what: str) -> str:
    """"USCIS Form I-914, Supplement A (Application ...): ..." -> "Form I-914, Supplement A"; "Form I-485 edition (the main form)" -> "Form I-485"."""
    m = re.search(r"(Form [A-Z]+-\d+[A-Za-z]*(?:,? Supplement [A-Z])?)", what)
    return m.group(1) if m else what.split(" (")[0].split(":")[0]


def _text_of(fragment: str) -> str:
    fragment = re.sub(r"\s+", " ", re.sub(r"(?is)<(script|style).*?</\1>", " ", fragment))  # a line break in the page's source is not a break in the sentence
    fragment = re.sub(r"(?i)</(p|li|ul|ol|h\d)>|<br\s*/?>", "\n", fragment)
    text = html.unescape(re.sub(r"<[^>]+>", " ", fragment)).replace("\xa0", " ")
    return "\n".join(re.sub(r"[ \t]+", " ", line).strip() for line in text.split("\n") if line.strip())


def _sentences(block: str) -> list[str]:
    out = []
    for line in block.split("\n"):
        out += [s.strip() for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"“])", line) if s.strip()]
    return out


def page_edition(page: str) -> str | None:
    """The edition the form's page lists ("Edition Date 09/18/26")."""
    m = _PAGE_EDITION.search(_text_of(page))
    return m.group(1) if m else None


_FORM = re.compile(r"\bForm\s+([A-Z]{1,4}-\d+[A-Z]{0,2})(?![A-Za-z0-9])")
_REFUSE = re.compile(r"(?i)\b(reject|will not (?:accept|process)|no longer accept|not be accepted)\b")
_NOT_REJECT = re.compile(r"(?i)\b(?:not|never|n't|won't)\s+(?:\w+\s+){0,2}?reject")
_WORDS = re.compile(r"(?i)accept|reject|grace|postmarked|submitted|process")


def _form_id(name: str | None) -> str | None:
    m = _FORM.search(name or "")
    return m.group(1) if m else None


def _about_us(sentence: str, form_id: str | None) -> bool:
    """A sentence counts for our form when it names it, or names no form at all (a page can carry an alert about another form)."""
    named = {m.group(1) for m in _FORM.finditer(sentence)}
    return not named or not form_id or form_id in named


def read_alerts(page: str, ours: str | None, theirs: str, form: str | None = None) -> dict[str, Any]:
    """What the form's USCIS page says about the change to `theirs`: its alert(s) that name that edition, the
    sentences about a grace period or the old edition that are about our form, the day USCIS says the old one stops being
    accepted. `form`: our form's name ("Form I-864"). A page whose alert gives no date we can read is "unreadable": the whole
    alert is kept, for a person to read."""
    blocks = [b for b in (_text_of(m.group(1)) for m in _ALERT.finditer(page)) if theirs in b]
    if not blocks:  # the page's markup changed: the sentences of the page itself that are about the new edition
        loose = [s for line in _text_of(page).split("\n") for s in _sentences(line) if theirs in s and _WORDS.search(s)]
        blocks = ["\n".join(loose)] if loose else []
    fid = _form_id(form)
    sentences: list[str] = []
    refused: list[date] = []
    no_grace = grace_stated = False
    published = None
    for block in blocks:
        if published is None and (m := _PUBLISHED.search(block.replace("\n", " "))):
            published = _date_of(m)
        for s in _sentences(block):
            if not _about_us(s, fid):
                continue
            low = s.lower()
            found = False
            if "no grace period" in low:
                no_grace = found = True
            elif "grace period" in low:
                grace_stated = found = True
            on_or_after = re.search(r"on or after\s+" + _DATE, s)
            only_new = "only accept" in low and theirs in s
            names_old = bool(ours) and ours in s and _REFUSE.search(s) and not _NOT_REJECT.search(s)
            if on_or_after and (only_new or names_old) and (d := _date_of(on_or_after)):
                refused.append(d)
                found = True
            elif only_new and (m := re.search(r"(?i)(?:beginning|starting|effective|as of)\s+" + _DATE, s)) and (d := _date_of(m)):
                refused.append(d)
                found = True
            elif names_old and (m := re.search(r"\bafter\s+" + _DATE, s)) and (d := _date_of(m)):
                refused.append(d + timedelta(days=1))
                found = True
            elif ours and ours in s and (m := re.search(r"(?i)\b(until|through)\s+" + _DATE, s)) and (d := _date_of(m)):
                refused.append(d + timedelta(days=1) if m.group(1).lower() == "through" else d)
                found = True
            if found and s not in sentences:
                sentences.append(s)
    alert = " ".join(b.replace("\n", " ") for b in blocks)[:3000]
    return {"sentences": sentences, "no_grace": no_grace, "grace_stated": grace_stated, "ours": ours, "alert": alert,
            "unreadable": bool(alert) and not refused and not no_grace,
            "refused_from": min(refused).isoformat() if refused else None, "published": published.isoformat() if published else None}


def read_page(url: str | None, ours: str | None, theirs: str, get, previous: dict[str, Any] | None = None, today: date | None = None,
              form: str | None = None) -> dict[str, Any]:
    """The part of a live-check result that is about the change: the page read (URL, date), when USCIS changed the form, the grace words.
    `get` is maintenance's fetcher. A page that can't be read is a result too ("grace": None): nothing is assumed -- unless the
    night before read it (same new edition): then what it said is kept, marked as read that day."""
    today = today or clock.today()
    prev = previous or {}
    same = prev.get("uscis") == theirs
    noticed = prev.get("noticed") if same and prev.get("noticed") else today.isoformat()
    out: dict[str, Any] = {"page": url, "read_on": today.isoformat(), "noticed": noticed, "published": None, "grace": None, "page_error": None}
    if not url:
        out["page_error"] = "there is no USCIS page to read for this form"
        return out
    try:
        page = get(url)
    except Exception:  # noqa: BLE001 -- the change is reported either way; the grace words just aren't known
        if same and prev.get("grace") is not None:
            return out | {"read_on": prev.get("read_on"), "published": prev.get("published"), "grace": prev["grace"],
                          "page_error": f"the page could not be read again on {mdy(today.isoformat())}; this is what it said on {mdy(prev.get('read_on'))}"}
        out["page_error"] = "the page could not be read"
        return out
    grace = read_alerts(page, ours, theirs, form)
    out["published"] = grace.pop("published")
    out["grace"] = grace
    return out


def carry(previous: dict[str, Any], today: date) -> dict[str, Any]:
    """A night the check itself failed: last night's answer stands (a held packet is not released by an outage), marked as not re-checked."""
    out = previous | {"recheck_failed_on": today.isoformat()}
    if previous.get("ok") is False:
        out["finding"] = finding(previous | {"recheck_failed_on": None}) + f" It could not be re-checked on {mdy(today.isoformat())}."
    else:
        out["finding"] = (f"The check of {previous.get('form') or 'this form'}'s edition could not run on {mdy(today.isoformat())}; the last good check was "
                          f"{mdy(previous.get('checked_on')) or 'not recorded'}.")
    return out


# -- what a result means ----------------------------------------------------------------------------------


def verdict(result: dict[str, Any] | None, today: date | None = None, name: str | None = None) -> dict[str, Any] | None:
    """None when the form is current (or couldn't be checked). Otherwise {"held": True} (the packet waits) or {"held": False,
    "accepted_until": ISO} (USCIS's page still accepts the old edition before that date), with the words.
    name: the form's name when the result doesn't carry one ("I-485": written "Form I-485")."""
    if not result or result.get("ok") is not False or not result.get("uscis"):
        return None
    today = today or clock.today()
    g = result.get("grace") or {}
    refused = g.get("refused_from") if g.get("ours") == result.get("ours") else None
    accepted = bool(refused) and not g.get("no_grace") and today < date.fromisoformat(refused)
    name = result.get("form") or (f"Form {name}" if name and re.match(r"[A-Z]+-\d", name) else name) or "the form"
    agency = "The immigration court" if "EOIR" in name else "USCIS"
    if result.get("published"):
        head = f"{agency} changed {name} on {mdy(result['published'])}"
    else:
        seen = f", first seen {mdy(result['noticed'])}" if result.get("noticed") else ""
        head = f"{agency} changed {name} (new edition {result['uscis']}{seen})"
    tail = (f"; the old edition ({result.get('ours')}) is still accepted before {mdy(refused)}; review the replacement edition before that date." if accepted
            else "; packets that use it wait for the update.")
    return {"form": name, "ours": result.get("ours"), "uscis": result["uscis"], "held": not accepted,
            "accepted_until": refused if accepted else None, "headline": head + tail,
            "grace": grace_words(result), "page": result.get("page"), "read_on": result.get("read_on")}


def grace_words(result: dict[str, Any]) -> str:
    """The grace period as USCIS's page states it, copied, with where and when it was read. When the alert gives no date we can read, the whole
    alert is shown for a person to read; when the page has no alert about the change, that nothing is stated (so none is assumed)."""
    url, read = result.get("page"), mdy(result.get("read_on"))
    g = result.get("grace")
    again = f" ({result['page_error']})" if result.get("page_error") and g is not None else ""
    redo = f" It could not be re-checked on {mdy(result['recheck_failed_on'])}." if result.get("recheck_failed_on") else ""
    if g is None:
        return "No grace period is assumed: " + (result.get("page_error") or "the form's page was not read") + "." + redo
    if g.get("unreadable"):
        return (f"USCIS's page ({url}, read {read}) says: \u201c{g['alert']}\u201d We could not read a date from USCIS's sentences; a person reads them." + again + redo)
    if not g.get("sentences"):
        return f"USCIS's page ({url}, read {read}) says nothing about a grace period, so none is assumed." + again + redo
    quoted = " ".join(f"\u201c{s}\u201d" for s in g["sentences"])
    return f"USCIS's page ({url}, read {read}) says: {quoted}" + again + redo


def finding(result: dict[str, Any]) -> str:
    """The line Keeping current and the nightly report show for the form (the maintenance result's "finding")."""
    v = verdict(result)
    if v is None:
        return f"USCIS now publishes edition {result.get('uscis')}; the template we fill is {result.get('ours') or 'unknown'}."
    return f"{v['headline']} {v['grace']}"


def updating(provider_name: str, default_name: str) -> str:
    """Name the responsible provider without claiming an update is underway."""
    named = f" ({provider_name})" if provider_name and provider_name != default_name else ""
    return f"Contact your provider{named} about the required edition update."
