"""The client's portal answers as one readable page, for "Open" on a card
whose answer came from the portal (there is no scan of a typed answer).

Each question in English, the client's answer in English words (option
labels, not codes), the I-485 item it feeds, and the wording the client
actually read when they answered in another language. Questions the
client was never shown (a "show if" that didn't hold) are left out.

The office's own questions ("Ask the client", portal/questions.py) follow, the
same way: the English the office wrote, the words the client read, the answer
in words (a date as MM/DD/YYYY), anchored at the facts they were asked about.
"""

from __future__ import annotations

import html
from urllib.parse import urlencode
from typing import Any

import clock

from portal.bank import UNSURE, language_names, visible
from portal.questions import asked_line, reply_words


def _en(text: Any) -> str:
    return (text.get("en") or next(iter(text.values()), "")) if isinstance(text, dict) else str(text or "")


def _date(value: str) -> str:
    return f"{value[5:7]}/{value[8:10]}/{value[:4]}" if len(value) == 10 and value[4] == "-" else value


def _scalar(question: dict[str, Any], value: Any) -> str:
    if value == UNSURE:
        return "I'm not sure"
    options = {o["value"]: _en(o["label"]) for o in question.get("options", [])}
    if value in options:
        return options[value]
    if question.get("type") == "date" or (isinstance(value, str) and len(value) == 10 and value[4:5] == "-"):
        return _date(str(value))
    return str(value)


def answer_text(question: dict[str, Any], value: Any) -> list[str]:
    """The answer as lines of plain English."""
    kind = question.get("type")
    if value == UNSURE:
        return ["I'm not sure"]
    if kind == "checklist" and isinstance(value, dict):
        if value.get("none"):
            return ["None of these"]
        labels = {o["value"]: _en(o["label"]) for o in question.get("options", [])}
        lines = [f"Ticked: {labels.get(v, v)}" for v in value.get("selected") or []]
        return lines + ([f"Explained: {value['explain']}"] if value.get("explain") else [])
    if kind in ("height", "weight") and isinstance(value, dict):
        return [str(value.get("form") or "") + (f" ({value['cm']} cm)" if value.get("cm") else f" ({value['kg']} kg)" if value.get("kg") else "")]
    fields = {f["id"]: f for f in question.get("fields", [])}

    def one(entry: dict[str, Any]) -> str:
        return " · ".join(f"{_en(fields[k]['label']) if k in fields else k.replace('_', ' ')}: {_scalar(fields.get(k, {}), v)}"
                          for k, v in entry.items() if v not in (None, ""))

    if isinstance(value, list):
        return [f"{n}. {one(e) if isinstance(e, dict) else _scalar(question, e)}" for n, e in enumerate(value, 1)]
    if isinstance(value, dict):
        return [one(value)]
    return [_scalar(question, value)]


def facts_of(question: dict[str, Any]) -> list[str]:
    keys = [question["fact"]] if question.get("fact") else []
    keys += list((question.get("facts") or {}).values())
    keys += [f for o in question.get("options", []) for f in o.get("facts", [])]
    return keys


def progress(answers: dict[str, Any], bank: dict[str, Any]) -> dict[str, int]:
    """How far along the client is: the questions they can see now, and how many have an answer."""
    shown = [q for s in bank["sections"] for q in s["questions"] if visible(q, answers)]
    return {"answered": sum(1 for q in shown if answers.get(q["id"]) not in (None, "", [], {})), "total": len(shown)}


def office_questions(requests: list[dict[str, Any]]) -> str:
    """The questions the office sent the client through the portal, as one section (drafts and skipped ones are not shown:
    the client never saw them)."""
    e, rows = html.escape, []
    for r in requests:
        if r.get("status") not in ("open", "answered"):
            continue
        anchors = "".join(f'<span id="{e(k)}"></span>' for k in r.get("facts") or [])
        asked = f'<div class="asked">{e(asked_line(r))}</div>' if asked_line(r) else ""
        when = clock.day(r.get("asked_at"))
        who = f'<div class="ref">Asked by {e(r.get("by") or "the office")}' + (f" on {e(_date(when))}" if when else "") + "</div>"
        said = reply_words(r) or ("Sent the document" if r.get("upload") else "")
        answer = f"<div>{e(said)}</div>" if said else '<div class="none">No answer yet</div>'
        rows.append(f'<div class="row">{anchors}<div class="q">{e(r.get("text_en") or r.get("text") or "")}{asked}{who}</div><div class="a">{answer}</div></div>')
    return f'<section><h2>Questions from the office</h2>{"".join(rows)}</section>' if rows else ""


def render(name: str, answers: dict[str, Any], bank: dict[str, Any], language: str, submitted: str | None,
           requests: list[dict[str, Any]] | None = None, *, client_id: str | None = None) -> str:
    e = html.escape
    lang = language_names().get(language) if language != "en" else None  # "Asked in Haitian Creole: ..."
    parts = []
    for section in bank["sections"]:
        rows = []
        for q in section["questions"]:
            if not visible(q, answers):
                continue
            value = answers.get(q["id"])
            anchors = "".join(f'<span id="{e(k)}"></span>' for k in facts_of(q))
            said = answer_text(q, value) if value not in (None, "", [], {}) else []
            asked = f'<div class="asked">Asked in {lang}: {e(q["label"].get(language, ""))}</div>' if lang and isinstance(q["label"], dict) and q["label"].get(language) else ""
            form = f'<div class="ref">On the I-485: {e(q["i485"])}</div>' if q.get("i485") else ""
            answer = "".join(f"<div>{e(line)}</div>" for line in said) or '<div class="none">No answer</div>'
            unsure = " unsure" if value == UNSURE else ""
            rows.append(f'<div class="row{unsure}">{anchors}<div class="q">{e(_en(q["label"]))}{asked}{form}</div><div class="a">{answer}</div></div>')
        if rows:
            parts.append(f'<section><h2>{e(_en(section["title"]))}</h2>{"".join(rows)}</section>')
    parts.append(office_questions(requests or []))
    done = progress(answers, bank)
    download = (f'<p><a href="/api/questionnaire.pdf?{e(urlencode({"client": client_id}))}">Download questionnaire PDF</a></p>'
                if submitted and client_id else "")
    when = (f" Submitted {e(clock.us_date(submitted) or _date(submitted[:10]))}." if submitted else
            f'</p><p class="draft"><b>Not submitted yet.</b> These are the answers saved so far ({done["answered"]} of {done["total"]} '
            "questions answered). The client can still change them, and nothing here is on a form until they submit.")
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>Client's answers</title><style nonce="%%NONCE%%">
:root {{ --bg: #f6f7fb; --panel: #fff; --ink: #14161f; --muted: #5d6475; --line: #e3e6ee; --hi: #fff6db; --hi-line: #f0d48a; }}
@media (prefers-color-scheme: dark) {{ :root {{ --bg: #0e1016; --panel: #171a23; --ink: #e8eaf2; --muted: #9aa1b2; --line: #2a2f3c; --hi: #33290f; --hi-line: #6b5520; }} }}
body {{ margin: 0; background: var(--bg); color: var(--ink); font: 15px/1.5 Inter, ui-sans-serif, system-ui, -apple-system, "Segoe UI", sans-serif; }}
main {{ max-width: 980px; margin: 0 auto; padding: 24px 16px 60px; }}
h1 {{ font-size: 22px; margin: 0 0 4px; }} .sub {{ color: var(--muted); margin: 0 0 20px; }}
section {{ background: var(--panel); border: 1px solid var(--line); border-radius: 12px; margin: 0 0 16px; overflow: hidden; }}
h2 {{ font-size: 13px; letter-spacing: .06em; text-transform: uppercase; color: var(--muted); margin: 0; padding: 12px 16px; border-bottom: 1px solid var(--line); }}
.row {{ display: grid; grid-template-columns: minmax(0, 1.3fr) minmax(0, 1fr); gap: 16px; padding: 12px 16px; border-bottom: 1px solid var(--line); }}
.row:last-child {{ border-bottom: 0; }} .row:target, .row.hit {{ background: var(--hi); box-shadow: inset 3px 0 0 var(--hi-line); }}
.row.unsure .a {{ font-weight: 650; }}
.a {{ font-weight: 560; overflow-wrap: anywhere; }} .none {{ color: var(--muted); font-weight: 400; }}
.draft {{ background: var(--hi); border: 1px solid var(--hi-line); border-radius: 10px; padding: 10px 14px; margin: -8px 0 20px; }}
.asked, .ref {{ color: var(--muted); font-size: 13px; margin-top: 2px; }}
@media (max-width: 640px) {{ .row {{ grid-template-columns: 1fr; gap: 4px; }} }}
</style></head><body><main>
<h1>{e(name)}: answers from the client portal</h1>
{download}
<p class="sub">What the client typed, question by question, in English.{when} Answers the firm changed later show on the review cards, not here.</p>
{"".join(parts) or '<p>The client has not answered any questions yet.</p>'}
</main><script nonce="%%NONCE%%">
// opened from a card: highlight and scroll to that question
const id = decodeURIComponent(location.hash.slice(1)), at = id && document.getElementById(id);
if (at) {{ const row = at.closest(".row"); row.classList.add("hit"); row.scrollIntoView({{ block: "center" }}); }}
</script></body></html>"""
