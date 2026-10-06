"""Answering a USCIS request for evidence (RFE) or notice of intent to deny
(NOID): the request's own checklist, the response packet, the mailing.

An RFE is the most deadline-bound thing a paralegal handles: a fixed due
date, a list of items to answer one by one, and one chance -- USCIS decides
on what arrives. So the response is built the way an experienced paralegal
builds it:

  1. the requested items, typed (or pasted) from the notice, one per line --
     each one answered by documents from the client's folder, an
     explanation, or both (a document the client still has to send is
     asked for through the portal);
  2. the address the notice says to send the response to (it is on the
     notice, not on any lockbox chart);
  3. the response packet: the firm's letter listing every item and the
     exhibit that answers it, the RFE notice itself on top of the evidence
     (as USCIS instructs), then one exhibit per item;
  4. the checks before mailing (every item answered, the address, the due
     date with time to mail, built after the last change), then the
     mailing record -- which also closes the request on the case timeline.

Nothing here reads the notice's requests for you: the wording USCIS uses
varies and a missed item costs the case, so a person copies each item from
the notice and the attorney reviews the list.
"""

from __future__ import annotations

import hashlib
import io
import json
import re
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import clock
import events

SETTINGS_KEY = "rfe_mail_days_before"


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _dir(client_dir: Path) -> Path:
    d = client_dir / "rfe"
    d.mkdir(exist_ok=True)
    return d


def key_of(n: dict[str, Any]) -> str:
    return f"{n['receipt']}_{n['kind']}_{n['date'] or 'undated'}"


def _graph(client_dir: Path):
    from review.state import reviewed_graph

    return reviewed_graph(client_dir)


def requests(client_dir: Path, graph=None) -> list[dict[str, Any]]:
    """Every RFE / NOID in the folder, newest first, with whether it was answered."""
    import journey

    graph = graph if graph is not None else _graph(client_dir)
    ns = journey.notices(graph)
    mailed = {r.get("rfe_key") for r in _read(client_dir / "status.json", {}).get("filings") or [] if r.get("filing") == "rfe"}
    out = []
    for n in ns:
        if n["kind"] not in ("rfe", "noid"):
            continue
        key = key_of(n)
        out.append(n | {"key": key, "label": f"{n['form'] or 'USCIS'} {journey.KIND_NAMES[n['kind']]} of {journey.us(n['date'])} ({n['receipt']})",
                        "answered": key in mailed, "decided": journey._answered(n, ns)})
    return sorted(out, key=lambda n: n["date"] or "", reverse=True)


def _request(client_dir: Path, key: str, graph=None) -> dict[str, Any]:
    found = next((r for r in requests(client_dir, graph) if r["key"] == key), None)
    if found is None:
        raise ValueError("That request isn't in the client's folder (re-read the folder if the notice was just added).")
    return found


def _documents(client_dir: Path) -> dict[str, dict[str, Any]]:
    """Every document in the client's folder (split documents too), for choosing exhibits."""
    from packet import _pages_of

    meta = _read(client_dir / "meta.json", {})
    folder = Path(meta.get("source_folder") or "")
    out = {}
    for doc, kind in sorted((meta.get("classifications") or {}).items()):
        name, pages = _pages_of(doc)
        out[doc] = {"doc": doc, "type": kind, "path": str(folder / name), "pages": [p + 1 for p in pages] if pages else None,
                    "exists": (folder / name).is_file()}
    return out


def state(client_dir: Path, key: str) -> dict[str, Any]:
    return _read(_dir(client_dir) / f"{key}.json", {"items": [], "send_to": [], "due": None})


def save(client_dir: Path, key: str, items: list[dict[str, Any]], send_to: list[str] | str, due: str | None, who: str) -> dict[str, Any]:
    """The response as the paralegal set it up: the items (text, documents, explanation), the address, the due date."""
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    request = _request(client_dir, key)
    docs = _documents(client_dir)
    clean = []
    for it in items:
        text = re.sub(r"\s+", " ", str(it.get("text") or "")).strip()
        if not text:
            continue
        unknown = [d for d in it.get("docs") or [] if d not in docs]
        if unknown:
            raise ValueError(f"Not in the client's folder: {', '.join(unknown)}.")
        clean.append({"text": text, "docs": list(dict.fromkeys(it.get("docs") or [])), "note": str(it.get("note") or "").strip()})
    lines = [x.strip() for x in (send_to.splitlines() if isinstance(send_to, str) else send_to or []) if x and x.strip()]
    if due:
        try:
            date.fromisoformat(due)
        except ValueError:
            raise ValueError("The due date: YYYY-MM-DD.") from None
    data = {"items": clean, "send_to": [x.upper() for x in lines], "due": due or request.get("due"),
            "updated_by": who, "updated_at": clock.stamp()}
    (_dir(client_dir) / f"{key}.json").write_text(json.dumps(data, indent=1), encoding="utf-8")
    events.record("packet", "saved", "Saved the response to a USCIS request", case_dir=client_dir, who=who)
    return plan(client_dir, key)


def mail_by(due: str | None) -> str | None:
    """The day to mail by: the due date less the firm's mailing margin (USCIS must RECEIVE it by the due date)."""
    import journey

    if not due:
        return None
    days = journey.settings()["deadlines"].get(SETTINGS_KEY, 7)
    return (date.fromisoformat(due) - timedelta(days=days)).isoformat()


def plan(client_dir: Path, key: str, today: date | None = None) -> dict[str, Any]:
    today = today or clock.today()
    request = _request(client_dir, key)
    st = state(client_dir, key)
    docs = _documents(client_dir)
    due = st.get("due") or request.get("due")
    items, problems = [], []
    for n, it in enumerate(st["items"]):
        letter = chr(ord("A") + n) if n < 26 else f"A{n - 25}"
        files = [docs[d] for d in it["docs"] if d in docs]
        items.append(it | {"exhibit": letter if files else None, "files": files})
        if not files and not it.get("note"):
            problems.append(f"Item {n + 1} isn't answered yet: choose its documents or write the explanation.")
        if any(not f["exists"] for f in files):
            problems.append(f"Item {n + 1}: a chosen document is no longer in the client's folder.")
    if not items:
        problems.append("Copy each item USCIS asks for from the notice: one per line.")
    if not st.get("send_to"):
        problems.append("Enter the address the notice says to send the response to (it is printed on the notice).")
    if not due:
        problems.append("Enter the response due date from the notice.")
    elif due < today.isoformat():
        problems.append(f"The due date ({due}) has passed: the attorney decides what to do now.")
    notice = docs.get(request["doc"])
    if notice is None or not notice["exists"]:
        problems.append("The notice itself isn't in the client's folder: it goes on top of the response.")
    manifest = _read(_dir(client_dir) / f"{key}_manifest.json", None)
    return {"request": request, "items": items, "send_to": st.get("send_to") or [], "due": due, "mail_by": mail_by(due),
            "problems": problems, "ready": not problems, "documents": list(docs.values()), "built": manifest,
            "updated_by": st.get("updated_by"), "updated_at": st.get("updated_at")}


def build(client_dir: Path, key: str, who: str, today: date | None = None) -> dict[str, Any]:
    """Writes rfe/<key>_response.pdf: the letter, the notice, then one exhibit per answered item."""
    from pypdf import PdfReader, PdfWriter

    from fill.cover_letter import case_facts, load_config, render_response
    from packet import _separator, _source_pages

    if not who:
        raise ValueError("Enter your name first: the response records who built it.")
    today = today or clock.today()
    p = plan(client_dir, key, today)
    request, draft = p["request"], not p["ready"]
    facts = case_facts(client_dir)
    import offices

    config = offices.letter(load_config(), client_dir, facts.get("physical_state"))
    kind = "Notice of Intent to Deny" if request["kind"] == "noid" else "Request for Evidence"
    re_lines = [f"Response to {kind} dated {_long(request['date'])}", f"Receipt Number: {request['receipt']}"] + \
               ([f"Form: {request['form']}"] if request.get("form") else [])
    intro = (f"On behalf of our client, we respond to the {kind} issued on {_long(request['date'])} for the receipt number above. "
             f"The {kind} notice is enclosed on top of this response, as instructed. Each item requested is answered below, with the exhibit that responds to it:")
    letter = PdfReader(io.BytesIO(render_response(config, facts, today, draft, p["send_to"], re_lines, intro,
                                                  [{"text": it["text"], "exhibit": it["exhibit"], "note": it["note"],
                                                    "titles": [_title(f) for f in it["files"]]} for it in p["items"]])))
    summary = {"name": " ".join(x for x in (facts.get("given_name"), facts.get("family_name")) if x), "a_number": facts.get("a_number")}
    writer = PdfWriter()
    for page in letter.pages:
        writer.add_page(page)
    notice = next((d for d in p["documents"] if d["doc"] == request["doc"]), None)
    if notice and notice["exists"]:
        writer.add_page(_separator(summary, kind, f"The {kind} notice ({request['receipt']}, {_long(request['date'])})").to_page(writer))
        for page in _source_pages(notice):
            writer.add_page(page)
    for n, it in enumerate(p["items"], start=1):
        if not it["files"]:
            continue
        writer.add_page(_separator(summary, f"Exhibit {it['exhibit']}", f"Item {n}: {it['text']}").to_page(writer))
        for f in it["files"]:
            for page in _source_pages(f):
                writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    (_dir(client_dir) / f"{key}_response.pdf").write_bytes(data)
    manifest = {"built_at": clock.stamp(), "built_by": who, "draft": draft, "problems": p["problems"],
                "pages": len(writer.pages), "sha256": hashlib.sha256(data).hexdigest(), "mail_to": p["send_to"], "due": p["due"],
                "items": [{"text": it["text"], "exhibit": it["exhibit"], "docs": it["docs"]} for it in p["items"]]}
    (_dir(client_dir) / f"{key}_manifest.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    events.record("packet", "built", "Built the response to a USCIS request" + (" (a draft)" if draft else ""), case_dir=client_dir, who=who)
    return manifest


def _long(iso: str | None) -> str:
    from fill.cover_letter import long_date

    return long_date(date.fromisoformat(iso)) if iso else "[date]"


def _title(f: dict[str, Any]) -> str:
    name = re.sub(r"\.pdf$", "", f["doc"].split("#")[0], flags=re.I)
    return name + (f" (pages {f['pages'][0]}-{f['pages'][-1]})" if f.get("pages") and len(f["pages"]) > 1 else
                   f" (page {f['pages'][0]})" if f.get("pages") else "")


def check(client_dir: Path, key: str, today: date | None = None) -> dict[str, Any]:
    """Ready to mail? for a response -- the same shape as src/prefile.py's checks."""
    from prefile import _check

    today = today or clock.today()
    p = plan(client_dir, key, today)
    built = p["built"]
    checks = []
    if not built:
        return {"filing": f"rfe:{key}", "ready": False, "checks": [_check("built", "fail", "Response", "Not built yet: build the response first.")]}
    at = clock.parse(built["built_at"])
    changed = [x for x in (_dir(client_dir) / f"{key}.json", client_dir / "meta.json") if x.exists() and datetime.fromtimestamp(x.stat().st_mtime, timezone.utc) > at]
    checks.append(_check("fresh", "fail" if changed else "pass", "Up to date",
                         "The items or the folder changed after the response was built: rebuild it." if changed else "Built after the last change."))
    checks.append(_check("answered", "fail" if p["problems"] else "pass", "Every item answered",
                         " ".join(p["problems"]) if p["problems"] else f"{len(p['items'])} item(s), each with its documents or explanation."))
    if p["due"]:
        days = (date.fromisoformat(p["due"]) - today).days
        level = "fail" if days < 0 else "warn" if p["mail_by"] and today.isoformat() > p["mail_by"] else "pass"
        checks.append(_check("due", level, "On time",
                             f"Due {journey_us(p['due'])}: {days} day(s) left. "
                             + ("Late: the attorney decides." if days < 0 else
                                "Past the mail-by date: send by overnight courier with tracking." if level == "warn" else f"Mail by {journey_us(p['mail_by'])}.")))
    if p["send_to"]:
        checks.append(_check("address", "info", "Mailing address", "To: " + ", ".join(p["send_to"]) + ", as printed on the notice."))
    checks.append(_check("signatures", "info", "Signatures", "The attorney signs the letter (page 1). The notice goes on top of the evidence, as USCIS asks."))
    return {"filing": f"rfe:{key}", "ready": not any(c["level"] == "fail" for c in checks), "checks": checks,
            "built_at": built["built_at"], "mail_to": p["send_to"], "sha256": built.get("sha256")}


def journey_us(iso: str | None) -> str:
    import journey

    return journey.us(iso)


def record(client_dir: Path, key: str, mailed_on: str, carrier: str, tracking: str, who: str, override: str | None = None,
           role: str | None = None, today: date | None = None) -> dict[str, Any]:
    """The response's mailing -- it also closes the request on the case timeline."""
    import journey
    from prefile import append_record, validate_mailing

    mailed = validate_mailing(mailed_on, carrier, tracking, who, role, today)
    result = check(client_dir, key, today)
    failed = [c for c in result["checks"] if c["level"] == "fail"]
    if failed and not str(override or "").strip():
        raise ValueError("Not ready to mail: " + "; ".join(f"{c['title']}: {c['text']}" for c in failed)
                         + ": fix these, or give the reason it's mailed anyway.")
    request = _request(client_dir, key)
    built = plan(client_dir, key, today)["built"] or {}
    rec = append_record(client_dir, {
        "filing": "rfe", "rfe_key": key, "form": request.get("form"), "title": f"Response to the {request['label']}", "mailed_on": mailed.isoformat(), "carrier": carrier,
        "tracking": str(tracking or "").strip(), "mail_to": built.get("mail_to"), "packet_sha256": built.get("sha256"), "built_at": built.get("built_at"),
        "override": str(override).strip() if failed else None, "failed_checks": [c["title"] for c in failed]}, who, main_filing=False)
    journey.mark(client_dir, "done", who, item=f"{request['receipt']}.{request['kind']}.{request['date']}",
                 note=f"response mailed {mailed.isoformat()} ({carrier} {rec['tracking']})".strip())
    return rec
