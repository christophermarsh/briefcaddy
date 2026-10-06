"""The client's case, in their words (brief I3): what the client's phone shows beside the stage.

    USCIS's own words       the status line USCIS's Case Status API gave for each receipt, quoted as it came, with the day it said it
                            (src/case_status.py). Never paraphrased and never translated by the product: the client reads USCIS's English and a
                            sentence in their own language that says so. Nothing without the firm's API keys, and nothing from the sandbox.
    USCIS's estimate        the processing-time figure copied from USCIS's page into schemas/registers/processing_times.json with the day it was read, shown
                            untouched. No entry, no line: a time is never worked out here.
    what happens next       one paragraph per stage (schemas/registers/client_next_steps.json): what usually happens and what the client can do. DRAFT.
    preparation sheets      what to bring and what the day looks like, per form, for a fingerprint appointment and an interview
                            (schemas/registers/client_case.json), from USCIS's own pages with the dates read; where the case holds the appointment notice's
                            own list of what to bring, that list is quoted as written. DRAFT. Drawn as a PDF for the phone and for the office to print.
    how was this step       one tap after a milestone (the agreement signed, documents sent, the application mailed, biometrics done, the interview
                            done, a decision): three faces and an optional sentence. Kept in the portal's own folder, copied onto the case as
                            feedback.jsonl, counted in Reports. Never used for anything automatic.
    answers a week old      an answer to a question with no review card behind it stays "Sent to the office" until someone marks it done; after
                            SEVEN_DAYS the line becomes "The office has your answer" and My work lists it under "Answers waiting a week".

Every client sentence is DRAFT for the attorney and a certified translator (docs/attorney_review.md); the Haitian Creole is a machine draft.
"""

from __future__ import annotations

import json
import re
import textwrap
import threading
import time
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import events
import schema_path


CASE = schema_path.path("register", "client_case")
NEXT = schema_path.path("register", "client_next_steps")
TIMES = schema_path.path("register", "processing_times")
FEEDBACK = "feedback.jsonl"  # in the case's folder: one JSON row per answer
FACES = ("good", "ok", "bad")
MAX_COMMENT = 500  # characters in the optional sentence
ASK_DAYS = 30  # a milestone older than this is not asked about any more: the question is for the step just done
SEVEN_DAYS = 7  # an answer with no review card behind it, unsettled this long, stops saying "Sent to the office"
LANGS = ("pt", "es", "en", "ht")

MONTHS = {
    "pt": ["janeiro", "fevereiro", "março", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"],
    "es": ["enero", "febrero", "marzo", "abril", "mayo", "junio", "julio", "agosto", "septiembre", "octubre", "noviembre", "diciembre"],
    "en": ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"],
    "ht": ["janvye", "fevriye", "mas", "avril", "me", "jen", "jiyè", "out", "septanm", "oktòb", "novanm", "desanm"],
}
WEEKDAYS = {  # Monday first (date.weekday())
    "pt": ["segunda-feira", "terça-feira", "quarta-feira", "quinta-feira", "sexta-feira", "sábado", "domingo"],
    "es": ["lunes", "martes", "miércoles", "jueves", "viernes", "sábado", "domingo"],
    "en": ["Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday"],
    "ht": ["lendi", "madi", "mèkredi", "jedi", "vandredi", "samdi", "dimanch"],
}


# -- the files ---------------------------------------------------------------------------------------------------------


@lru_cache(maxsize=8)
def _load(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def data(path: Path = CASE) -> dict[str, Any]:
    return _load(str(path), path.stat().st_mtime)


def say(words: dict[str, str], lang: str) -> str:
    """The client's language, or English when this text has none yet (never blank)."""
    return words.get(lang) or words["en"]


def label(key: str, lang: str) -> str:
    return say(data()["labels"][key], lang)


def _d(value: Any) -> date | None:
    try:
        return date.fromisoformat(str(value)[:10])
    except ValueError:
        return None


def day_words(iso: Any, lang: str, weekday: bool = False) -> str:
    """The day as the portal writes it for the client (portal.html longDate): "segunda-feira, 5 de outubro de 2026", "lunes, 5 de octubre de 2026",
    "Monday, October 5, 2026", "lendi 5 oktòb 2026"."""
    d = _d(iso)
    if d is None:
        return str(iso or "")
    lang = lang if lang in MONTHS else "en"
    month, wd = MONTHS[lang][d.month - 1], WEEKDAYS[lang][d.weekday()]
    if lang == "en":
        return f"{wd + ', ' if weekday else ''}{month} {d.day}, {d.year}"
    if lang == "ht":
        return f"{wd + ' ' if weekday else ''}{d.day} {month} {d.year}"
    return f"{wd + ', ' if weekday else ''}{d.day} de {month} de {d.year}"


def clock_words(text: Any, lang: str) -> str | None:
    """An appointment's time as the notice has it ("9:30 AM"), written the way the client's language writes a time ("09:30"); never converted to another zone."""
    m = re.match(r"^\s*(\d{1,2}):(\d{2})\s*([AaPp])\.?[Mm]\.?\s*$", str(text or ""))
    if not m:
        return str(text or "").strip() or None  # a time the notice words another way is shown as it is
    if lang == "en":
        return f"{int(m.group(1))}:{m.group(2)} {m.group(3).upper()}M"
    return f"{(int(m.group(1)) % 12) + (12 if m.group(3) in 'Pp' else 0):02d}:{m.group(2)}"


# -- USCIS's own words ---------------------------------------------------------------------------------------------------

_keys = {"at": 0.0, "ready": False}
_keys_lock = threading.Lock()


def keys_ready() -> bool:
    """The firm's Case Status API keys are on this server (src/case_status.py config), looked at once a minute at most."""
    with _keys_lock:
        if time.monotonic() - _keys["at"] > 60:
            try:
                import case_status

                _keys["ready"] = bool(case_status.config().get("ready"))
            except Exception:  # noqa: BLE001 -- no answer is no keys
                _keys["ready"] = False
            _keys["at"] = time.monotonic()
        return bool(_keys["ready"])


def status_records(client_dir: Path, ready: bool | None = None) -> list[dict[str, Any]]:
    """USCIS's last answer for each receipt the case holds, as USCIS gave it: [{receipt, form, text, desc, checked}], newest first. Empty without the
    firm's API keys, for an answer that was an error, and for the sandbox's (staging receipts: never a real client's)."""
    if not (keys_ready() if ready is None else ready):
        return []
    import case_status

    out = []
    for receipt, r in case_status.records(Path(client_dir)).items():
        if not r.get("text") or r.get("environment") != "production":
            continue
        out.append({"receipt": receipt, "form": r.get("form"), "text": r["text"], "desc": r.get("desc") or "", "checked": clock.day(r.get("checked_at")) or None})
    return sorted(out, key=lambda x: (x["checked"] or "", x["receipt"]), reverse=True)


def status_view(records: list[dict[str, Any]], lang: str) -> dict[str, Any] | None:
    """The client's "What USCIS says about your case": each line exactly as USCIS returned it (English), with "USCIS said on <day>" and the sentence,
    in the client's language, that this is USCIS's English. The words of USCIS are never in the client's language: nothing of ours stands in for them."""
    if not records:
        return None
    forms = [r.get("form") for r in records]
    items = [{"form": r.get("form"), "receipt": r["receipt"] if forms.count(r.get("form")) > 1 else None, "text": r["text"], "desc": r["desc"],
              "said_on": label("status_said_on", lang).format(date=day_words(r["checked"], lang)) if r.get("checked") else None} for r in records]
    return {"title": label("status_title", lang), "in_english": label("status_in_english", lang), "items": items}


def estimates(j: dict[str, Any], lang: str, path: Path = TIMES) -> list[str]:
    """USCIS's published processing-time estimate for a form the case holds a receipt for, only from schemas/registers/processing_times.json (copied from USCIS's
    page with the day it was read), the figure untouched. A case with no matching entry gets nothing."""
    try:
        held = json.loads(path.read_text(encoding="utf-8")).get("entries") or []
    except (OSError, ValueError):
        return []
    out = []
    for e in held:
        if not (e.get("form") and e.get("office") and e.get("months") and e.get("read_on")):
            continue  # an entry missing any of what it says is not shown
        prefixes = [str(p).upper() for p in e.get("receipt_prefixes") or []]
        if any(r.get("form") == e["form"] and not r.get("closed") and (not prefixes or str(r.get("receipt") or "")[:3].upper() in prefixes)
               for r in j.get("receipts") or []):
            out.append(label("estimate", lang).format(date=day_words(e["read_on"], lang), months=str(e["months"]), office=str(e["office"])))
    return out


# -- what happens next ---------------------------------------------------------------------------------------------------


def next_step(stage: str, lang: str) -> dict[str, str] | None:
    """{"label": "What happens next", "text": the paragraph} for the stage, in the client's language (English when it has none yet); None for a stage with none."""
    n = data(NEXT)
    words = (n["stages"] or {}).get(stage)
    return {"label": say(n["labels"]["next"], lang), "text": say(words, lang)} if words else None


# -- the appointments the case holds -------------------------------------------------------------------------------------


def notice_id(n: dict[str, Any]) -> str:
    """The id an appointment from a notice goes by: its receipt, its kind and the notice's day (the calendar feed's deadline id, src/journey.py)."""
    return f"{n.get('receipt')}.{n.get('kind')}.{n.get('date')}"


def apply_reads(notices: list[dict[str, Any]], marks: dict[str, Any]) -> None:
    """What a person said about two things the notice reader took from an appointment notice (both UNVERIFIED rules in src/extract/uscis_notice.py): the
    place and the list of what to bring. Each notice gets where_state and bring_state: "none" (nothing was read), "unconfirmed" (read, no person has said
    it is right: it reaches no client), "confirmed" (a person checked it against the paper: the place may be sent in a reminder, the list shown on the
    sheet) or "rejected" (a person said it was read wrongly). A confirmation holds only for what the person looked at: when the notice is read again and the
    words differ, it is unconfirmed again. A place a person typed is the place (confirmed). where_read keeps what the reader took. marks: src/journey.py's
    marks (status.json, journey: bring and places, by notice id)."""
    for n in notices:
        n["where_read"] = n.get("where")
        if n.get("kind") not in ("biometrics", "interview"):
            n["where_state"] = n["bring_state"] = "none"
            continue
        key = notice_id(n)
        said = (marks.get("places") or {}).get(key) or {}
        read = n.get("where")
        if said.get("typed"):
            n["where"], n["where_state"] = said["typed"], "confirmed"  # a person typed the address: it is theirs
        elif not read:
            n["where_state"] = "none"
        elif said.get("read") == read and said.get("state") in ("confirmed", "rejected"):
            n["where_state"] = said["state"]
            if said["state"] == "rejected":
                n["where"] = None  # said to be read wrongly: not shown to the client at all
        else:
            n["where_state"] = "unconfirmed"
        lines = n.get("bring")
        said = (marks.get("bring") or {}).get(key) or {}
        n["bring_state"] = ("none" if not lines else said["state"] if said.get("lines") == lines and said.get("state") in ("confirmed", "rejected") else "unconfirmed")


def reads(j: dict[str, Any]) -> list[dict[str, Any]]:
    """For the staff screen: each appointment notice still to come, with what was read from it (the place, the list) and where each stands:
    {id, kind, date, form, where, where_state, bring, bring_state}."""
    out = []
    for n in j.get("notices") or []:
        when = _d(n.get("appointment"))
        if n.get("kind") in ("biometrics", "interview") and when and when.isoformat() >= j["today"]:
            out.append({"id": notice_id(n), "kind": n["kind"], "date": when.isoformat(), "form": n.get("form"), "where": n.get("where_read"), "where_now": n.get("where"),
                        "where_state": n.get("where_state"), "bring": n.get("bring"), "bring_state": n.get("bring_state")})
    return sorted(out, key=lambda r: (r["date"], r["id"]))


def upcoming(j: dict[str, Any]) -> list[dict[str, Any]]:
    """The fingerprint appointments, interviews and confirmed court hearings still to come, soonest first: {id, kind, date, time, where, where_confirmed, form,
    receipt, bring}. Read from the notices and the hearings the office confirmed (a hearing read from a notice and not yet checked by a person is never shown
    to the client: src/journey.py client_view). Only the newest notice of a receipt and a kind counts (a rescheduled appointment replaces the old one).
    where_confirmed: a person entered or checked the place (a hearing's court; a place a person confirmed or typed), else it is what the reader took from the
    notice. bring is the notice's own list only once a person confirmed it (apply_reads)."""
    today = j["today"]
    out = []
    newest: dict[tuple, str] = {}
    for n in j.get("notices") or []:
        k = (n.get("receipt"), n.get("kind"))
        newest[k] = max(newest.get(k, ""), str(n.get("date") or ""))
    for n in j.get("notices") or []:
        when = _d(n.get("appointment"))
        if n.get("kind") in ("biometrics", "interview") and when and when.isoformat() >= today and str(n.get("date") or "") == newest[(n.get("receipt"), n.get("kind"))]:
            out.append({"id": notice_id(n), "kind": n["kind"], "date": when.isoformat(), "time": str(n["appointment"])[11:].strip() or None,
                        "where": n.get("where"), "where_confirmed": n.get("where_state") == "confirmed", "form": n.get("form"), "receipt": n.get("receipt"),
                        "bring": (n.get("bring") or None) if n.get("bring_state") == "confirmed" else None, "bring_pending": n.get("bring_state") == "unconfirmed"})
    for h in j.get("hearings") or []:
        if h.get("source") and not h.get("confirmed"):
            continue
        if h["date"] >= today and not h.get("result"):
            out.append({"id": h.get("id"), "kind": "hearing", "date": h["date"], "time": h.get("time"), "where": h.get("court"), "where_confirmed": bool(h.get("court")),
                        "form": None, "receipt": None, "bring": None, "bring_pending": False})
    return sorted(out, key=lambda a: (a["date"], a["time"] or ""))


def office_phone(client_dir: Path) -> str | None:
    """The case's office's phone as Settings has it (never the shipped sample firm's, which Settings withholds once the firm has configured the product)."""
    try:
        import offices

        phone = offices.for_case(Path(client_dir))["values"].get("firm.phone")
        return offices._phone(phone) if phone else None
    except Exception:  # noqa: BLE001 -- no phone line rather than a failed page
        return None


# -- the preparation sheets ----------------------------------------------------------------------------------------------


def sheet_key(kind: str, form: str | None, track: str | None) -> str:
    """Which sheet of the kind ("biometrics" or "interview") a notice's form calls for: the sheet for its form, the family sheet for a family case's
    green card or petition, else the kind's general sheet."""
    d = data()
    key = (d["notice_forms"].get(str(form or "").upper()))
    if track == "family" and form in ("I-485", "I-130"):
        key = "family"
    if key == "sij" and track != "sij":
        key = None  # an I-360 is also a VAWA self-petition and others: the SIJ page speaks only of SIJ cases
    sheets = d["sheets"][kind]
    return key if key in sheets else "default" if "default" in sheets else "other"


def sheet(kind: str, form: str | None, track: str | None, lang: str, letter_list: list[str] | None = None, phone: str | None = None,
          letter_pending: bool = False) -> dict[str, Any] | None:
    """The sheet for one appointment, in the client's language: {title, day, bring, letter_list, ...}. kind is "biometrics" or "interview"; a hearing has
    no sheet here (it has its own page: schemas/registers/journey.json appointment_pages). The notice's own list, when the case holds one, is quoted as written."""
    d = data()
    if kind not in d["sheets"]:
        return None
    key = sheet_key(kind, form, track)
    s = d["sheets"][kind][key]
    number = d["forms"].get(key) if key in d["forms"] else None
    if key == "default" and form:
        number = form
    word = ""
    if number:
        import journey

        events_words = journey.settings().get("client_events") or {}
        word = f" ({say(events_words['_form_word'], lang)} {number})"
    read = max((d["sources"][x]["read_on"] for x in s["sources"] if d["sources"][x].get("read_on")), default=None)
    return {"kind": kind, "key": key, "title": label(f"sheet_title_{kind}", lang) + word,
            "day": [say(d["lines"][i], lang) for i in s["day"]], "day_label": label("sheet_day", lang),
            "bring": [say(d["lines"][i], lang) for i in s["bring"]] + ([label("sheet_notice_lists", lang)] if letter_pending and not letter_list else []),
            "letter_label": label("sheet_from_letter", lang) if letter_list else None, "letter_list": list(letter_list) if letter_list else None,
            "letter_first": label("sheet_letter_first", lang),
            "source": label("sheet_source", lang).format(date=day_words(read, lang)) if read else None,
            "phone": label("sheet_phone", lang).format(phone=phone) if phone else None,
            "machine": label("sheet_machine", lang) if lang == "ht" else None,
            "download": label("sheet_download", lang), "open": label("sheet_open", lang)}


def sheet_pdf(sheet_data: dict[str, Any], appointment: dict[str, Any], labels: dict[str, str], firm: str = "", draft: bool = False) -> bytes:
    """One preparation sheet as a PDF (the standard fonts of the packet's own page drawing: nothing beyond pypdf). appointment: {when, where, note}, already in
    the client's words (where only when a person checked or typed the address; note says to check it, or that it is on the letter); labels: the portal's When / Where / What to bring. draft: the office's printout says the wording is a draft until the
    attorney approves it; the client's own copy does not."""
    import io

    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, WIDTH, _Page

    pages: list[Any] = []
    state = {"y": 0.0}
    width, size, lead = WIDTH - 2 * MARGIN, 11, 15

    def new_page() -> Any:
        p = _Page()
        pages.append(p)
        if draft:
            p.text(MARGIN, HEIGHT - 28, "DRAFT: the firm's attorney has not approved this wording yet. The Haitian Creole is a machine translation.", "F3", 8)
        state["y"] = HEIGHT - 56
        return p

    page = [new_page()]

    def line(text: str, font: str = "F3", fsize: float = size, gap: float = 0, indent: float = 0, bullet: bool = False) -> None:
        for n, part in enumerate(textwrap.wrap(str(text), max(10, int((width - indent) / (fsize * 0.5)))) or [""]):
            if state["y"] < 70:
                page[0] = new_page()
            if bullet and n == 0:
                page[0].text(MARGIN + indent - 10, state["y"], "-", font, fsize)
            page[0].text(MARGIN + indent, state["y"], part, font, fsize)
            state["y"] -= lead if fsize >= size else fsize + 3
        state["y"] -= gap

    if firm:
        line(firm, "F2", 10, gap=4)
    line(sheet_data["title"], "F2", 16, gap=10)
    page[0].line(MARGIN, state["y"] + 8, WIDTH - MARGIN, state["y"] + 8)
    state["y"] -= 8
    line(labels["when"], "F2", 12)
    line(appointment["when"], gap=6)
    if appointment.get("where") or appointment.get("note"):
        line(labels["where"], "F2", 12)
        if appointment.get("where"):
            line(appointment["where"])
        if appointment.get("note"):
            line(appointment["note"], "F3", 10)  # "Check this address on your letter." under an address a person checked; else "The address is on your letter. Check it there."
        state["y"] -= 6
    line(sheet_data["day_label"], "F2", 12)
    for x in sheet_data["day"]:
        line(x, indent=14, bullet=True, gap=2)
    state["y"] -= 6
    line(labels["bring"], "F2", 12)
    for x in sheet_data["bring"]:
        line(x, indent=14, bullet=True, gap=2)
    if sheet_data.get("letter_list"):
        state["y"] -= 6
        line(sheet_data["letter_label"], "F2", 11)
        for x in sheet_data["letter_list"]:
            line(x, indent=14, bullet=True, gap=2)
    state["y"] -= 6
    line(sheet_data["letter_first"], "F3", 10, gap=6)
    if sheet_data.get("phone"):
        line(sheet_data["phone"], "F2", 11, gap=6)
    if sheet_data.get("source"):
        line(sheet_data["source"], "F3", 8)
    if sheet_data.get("machine"):
        line(sheet_data["machine"], "F3", 8)
    writer = PdfWriter()
    for p in pages:
        writer.add_page(p.to_page(writer))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


# -- milestones and "how was this step" -------------------------------------------------------------------------------------


def milestones(j: dict[str, Any]) -> list[dict[str, str]]:
    """The steps the firm's records show as done, each with the day: the application mailed (a filing that goes to USCIS), the fingerprint appointment
    and the interview once their day has passed, a decision (an approval or a denial notice). [{id, kind, date}], newest first. The agreement and the
    documents are the portal's own (src/portal/app.py)."""
    import journey

    out = []
    for r in j.get("filings") or []:
        if r.get("filing") in journey._MAILED_TO_USCIS and r.get("mailed_on"):
            out.append({"id": f"mailed.{r['filing']}.{r['mailed_on']}", "kind": "mailed", "date": str(r["mailed_on"])[:10]})
    for n in j.get("notices") or []:
        when = _d(n.get("appointment"))
        if n.get("kind") in ("biometrics", "interview") and when and when.isoformat() < j["today"]:
            out.append({"id": f"{n['kind']}.{n.get('receipt')}.{when.isoformat()}", "kind": n["kind"], "date": when.isoformat()})
        elif n.get("kind") in ("approval", "denial") and n.get("date"):
            out.append({"id": f"decision.{n.get('receipt')}.{n['date']}", "kind": "decision", "date": str(n["date"])[:10]})
    seen, unique = set(), []
    for m in sorted(out, key=lambda m: (m["date"], m["id"]), reverse=True):
        if m["id"] not in seen:
            seen.add(m["id"])
            unique.append(m)
    return unique


def feedback_prompt(candidates: list[dict[str, str]], answered: set[str], today: date, lang: str) -> dict[str, Any] | None:
    """The one milestone to ask about now: the latest unanswered one whose day has come and is not older than ASK_DAYS. candidates: [{id, kind, date}]."""
    for m in sorted(candidates, key=lambda m: (m["date"], m["id"]), reverse=True):
        d = _d(m["date"])
        if m["id"] in answered or d is None or d > today or (today - d).days > ASK_DAYS:
            continue
        d_labels = {k: label(k, lang) for k in ("feedback_prompt", "feedback_good", "feedback_ok", "feedback_bad", "feedback_note", "feedback_send", "feedback_thanks", "feedback_private")}
        return {"id": m["id"], "kind": m["kind"], "date": m["date"], "step": label(f"milestone_{m['kind']}", lang), "labels": d_labels}
    return None


def clean_feedback(body: Any) -> tuple[dict[str, Any] | None, str | None]:
    """(a cleaned answer, None) or (None, the reason): a step id, one of the three faces, an optional sentence of at most MAX_COMMENT characters."""
    if not isinstance(body, dict):
        return None, "invalid"
    step, face = str(body.get("step") or ""), str(body.get("face") or "")
    comment = " ".join(str(body.get("comment") or "").split())
    if not re.fullmatch(r"[A-Za-z0-9_.-]{1,80}", step) or face not in FACES:
        return None, "invalid"
    if len(comment) > MAX_COMMENT:
        return None, "too_long"
    return {"step": step, "face": face, "comment": comment}, None


def read_feedback(client_dir: Path) -> list[dict[str, Any]]:
    path = Path(client_dir) / FEEDBACK
    out = []
    if path.exists():
        for line in path.read_text(encoding="utf-8").splitlines():
            try:
                row = json.loads(line)
            except ValueError:
                continue
            if isinstance(row, dict):
                out.append(row)
    return out


def sync_feedback(client_dir: Path, portal_root: Path) -> int:
    """The client's answers from the portal's own folder onto the case (feedback.jsonl), each once. Called when the case is opened, by the overnight run
    and by Reports; a case with no folder here yet keeps them in the portal until it has one. Returns how many were copied. Never raises."""
    try:
        client_dir = Path(client_dir)
        theirs = Path(portal_root) / "clients" / client_dir.name / "feedback.json"
        if not theirs.exists() or not (client_dir / "fact_graph.json").exists():
            return 0
        given = json.loads(theirs.read_text(encoding="utf-8"))
        have = {r.get("step") for r in read_feedback(client_dir)}
        fresh = [r for r in given if isinstance(r, dict) and r.get("step") not in have]
        if not fresh:
            return 0
        with open(client_dir / FEEDBACK, "a", encoding="utf-8") as f:
            for r in fresh:
                f.write(json.dumps({k: r.get(k) for k in ("step", "kind", "face", "comment", "at", "language")}, ensure_ascii=False) + "\n")
        events.record("portal", "copied", f"Copied {len(fresh)} answer(s) about how a step went onto the case", case_dir=client_dir,
                      default_who=("The product", "system", "system"))
        return len(fresh)
    except Exception as exc:  # noqa: BLE001 -- the portal keeps them; the next sync tries again
        import sys

        sys.stderr.write(f"feedback not copied onto the case ({type(exc).__name__})\n")
        return 0


def sync_all_feedback(data_root: Path, portal_root: Path | None, touched=None) -> int:
    """sync_feedback for every client with feedback. touched(case id): called for each case that gained answers (the review app's lists read that case again)."""
    if portal_root is None or not (Path(portal_root) / "clients").exists():
        return 0
    n = 0
    for folder in sorted((Path(portal_root) / "clients").iterdir()):
        if (folder / "feedback.json").exists():
            got = sync_feedback(Path(data_root) / folder.name, portal_root)
            n += got
            if got and touched is not None:
                touched(folder.name)
    return n


STEP_NAMES = {"agreement": "Signing the agreement", "documents": "Sending the documents", "mailed": "Mailing the application", "biometrics": "Fingerprint appointment",
              "interview": "Interview", "decision": "Decision"}  # the firm's own screens (Reports), in English
FACE_NAMES = {"good": "Good", "ok": "Okay", "bad": "Not good"}


def step_name(step: str) -> str:
    """A milestone id ("biometrics.IOE0000000000.2026-09-01") as the firm's screens say it, never the id."""
    return STEP_NAMES.get(str(step).split(".", 1)[0], "Another step")


# -- an answer the office has had for a week ----------------------------------------------------------------------------------


def stale_answers(requests: list[dict[str, Any]], today: date | None = None) -> set[str]:
    """The ids of the answers that have waited SEVEN_DAYS or more for the office to mark them done: answered, not settled, and with no review card behind
    them (no facts: a free-text answer or a requested document). The day is the office's own (src/clock.py)."""
    today = today or clock.today()
    out = set()
    for r in requests:
        if r.get("status") == "answered" and not r.get("settled_at") and not r.get("facts"):
            day = clock.local_date(r.get("answered_at"))
            if day is not None and (today - day).days >= SEVEN_DAYS:
                out.add(r["id"])
    return out


def answers_waiting(portal_folder: Path, today: date | None = None) -> list[dict[str, Any]]:
    """My work's "Answers waiting a week" for one client: [{id, at, days, text}] (what the office asked, cut short)."""
    today = today or clock.today()
    path = Path(portal_folder) / "requests.json"
    try:
        requests = json.loads(path.read_text(encoding="utf-8")) if path.exists() else []
    except (OSError, ValueError):
        return []
    stale = stale_answers(requests, today)
    return [{"id": r["id"], "at": r.get("answered_at"), "days": (today - clock.local_date(r["answered_at"])).days, "text": " ".join(str(r.get("text") or "").split())[:160]}
            for r in requests if r["id"] in stale]
