"""The export reads itself: index.html at the root of every export of the firm's data (tools/export_firm.py --everything).

One plain HTML file, read in any browser with scripts off and with no resource from anywhere else: its style is inside it, its links are to the files in
the same zip, and a small script (search, and "open all") only adds to it. It lists every case with the client's name and kind of case, and for each case
every record (its title from the data dictionary, its schema version, a link to the file, and for the facts the values themselves) and every document as a
link; the portal's records for the client; the firm's own records (settings with its offices, staff, the upkeep log, policies, wordings, practices, the
logs); the event ledger by month, each month a table of its own beside it (ledger/events-YYYY-MM.html); and what the product keeps that is not in the
export, and why.

It is made from the catalog (src/records.py), never from a list of its own: every record's title, file patterns, versions and fields are the catalog's, so a
changed record shape changes the page without a change here (tests/test_export_reader.py changes the catalog and reads the page again). A case's files are matched
to records by the same patterns the export itself uses.

The attorney who exports sees everything, so a restricted case's values are on the page; the page says, per case, that it was restricted, by whom and why, and
who may open it. It is the attorney's page: keep the export on encrypted storage.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any, Callable
from urllib.parse import quote

import clock
import records

TRACK_NAMES = {"sij": "Special Immigrant Juvenile", "family": "Family-based", "naturalization": "Citizenship", "asylum": "Asylum", "caa": "Cuban Adjustment Act or HRIFA",
               "vawa": "VAWA self-petition", "t_visa": "T visa", "u_visa": "U visa", "daca": "DACA"}
DOCUMENT_SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff")
STYLE = """
body{font:15px/1.5 system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;margin:0;color:#1d2330;background:#f6f7f9}
main{max-width:1040px;margin:0 auto;padding:24px 18px 80px}
h1{font-size:26px;margin:0 0 4px}h2{font-size:20px;margin:36px 0 8px;border-bottom:2px solid #d7dbe3;padding-bottom:4px}h3{font-size:16px;margin:18px 0 6px}
a{color:#0b5cad}code{background:#e9ecf2;padding:1px 5px;border-radius:3px;font-size:13px}
table{border-collapse:collapse;width:100%;background:#fff;margin:6px 0 12px}th,td{border:1px solid #d7dbe3;padding:5px 8px;text-align:left;vertical-align:top}th{background:#eef0f5}
details{background:#fff;border:1px solid #d7dbe3;border-radius:6px;margin:8px 0;padding:6px 12px}summary{cursor:pointer;font-weight:600}
.sub{color:#5b6475;font-size:13px}.badge{display:inline-block;background:#fde7e4;color:#8a1c12;border:1px solid #f1b9b2;border-radius:10px;padding:0 8px;font-size:12px;margin-left:6px}
.notice{background:#fff8e1;border:1px solid #f0d98a;border-radius:6px;padding:8px 12px}
input#find{width:100%;padding:8px;font-size:15px;border:1px solid #b9c0cd;border-radius:6px;box-sizing:border-box}
@media print{details{border:0}details>*{display:block}}
"""
SCRIPT = """
document.getElementById('find').addEventListener('input', function (e) {
  var q = e.target.value.toLowerCase();
  document.querySelectorAll('details.case').forEach(function (d) { d.style.display = !q || d.dataset.find.indexOf(q) >= 0 ? '' : 'none'; });
});
document.getElementById('openall').addEventListener('click', function () {
  document.querySelectorAll('details.case').forEach(function (d) { d.open = true; });
});
"""


def esc(value: Any) -> str:
    return html.escape("" if value is None else str(value), quote=True)


def href(*parts: str) -> str:
    return "/".join(quote(p, safe="") for p in "/".join(parts).split("/"))


def _json(source: Path | None, data: bytes | None) -> Any:
    try:
        raw = data if data is not None else source.read_bytes() if source is not None else b""
        return json.loads(raw.decode("utf-8"))
    except (OSError, ValueError):
        return None


def _mdy(value: Any) -> str:
    return clock.us_date(value) or str(value or "")


def schema_version(record: dict[str, Any] | None, content: Any) -> str:
    """The record's schema version as this file carries it: its own `version` field when the record has one (and the catalog's latest when they differ), else
    "no version field" with the shape the catalog calls version 1 (the ledger counts a record with none as 1)."""
    if record is None:
        return ""
    latest = record["versions"][-1][0] if record.get("versions") else 1
    if record.get("version") is None:
        return f"no version field (shape {latest})"
    have = content.get("version") if isinstance(content, dict) else None
    if have is None:
        return f"version not stated (the catalog's latest is {latest})"
    return f"version {have}" + ("" if have == latest else f" (the catalog's latest is {latest})")


def fact_label(key: str) -> str:
    import events

    words = events.words(key)
    return words[:1].upper() + words[1:] if words else key


def _name_and_kind(case: str, files: dict[str, Any], portal_root: Path | None) -> tuple[str, str]:
    """(the client's name, the case's kind): the name from the facts the readers settled, else the portal's profile, else the case's id; the kind from what an attorney set on
    the case, else what the dashboard last worked out, else "not worked out yet"."""
    name = ""
    graph = files.get("fact_graph.json")
    if isinstance(graph, dict):
        found = {k: (f or {}).get("value") for k, f in (graph.get("facts") or {}).items() if isinstance(f, dict) and (f.get("status") in (None, "resolved")) and f.get("value")}
        name = " ".join(str(found[k]) for k in ("applicant.given_name", "applicant.middle_name", "applicant.family_name") if found.get(k))
        name = " ".join(w.capitalize() for w in name.split())
    if not name and portal_root is not None:
        profile = _json(portal_root / "clients" / case / "profile.json", None)
        name = str((profile or {}).get("name") or "").strip() if isinstance(profile, dict) else ""
    track = (((files.get("status.json") or {}).get("journey") or {}).get("track") or {}).get("value") if isinstance(files.get("status.json"), dict) else None
    named = ""
    if not track:
        row = (files.get("journey_summary.json") or {}).get("row") if isinstance(files.get("journey_summary.json"), dict) else None
        named = (row or {}).get("track_name") or ""
        track = (row or {}).get("track")
    return name or case, TRACK_NAMES.get(track or "") or named or "Kind of case not worked out yet"


def _restriction(case_dir: Path | None, files: dict[str, Any]) -> str:
    """"" for an ordinary case, else one sentence: that the case was restricted, by whom and why, and who may open it."""
    if case_dir is None:
        return ""
    import restricted

    rec = restricted.record(case_dir)
    marked = rec["marked"] if (rec["marked"] or {}).get("on") else None
    law = None
    cache = _json(case_dir / "confidentiality.json", None)
    if isinstance(cache, dict):
        law = cache.get("law")
    if law is None:
        import documents

        status = files.get("status.json") if isinstance(files.get("status.json"), dict) else {}
        track = ((status.get("journey") or {}).get("track") or {}).get("value")
        law = documents._TRACKS.get(track) or next((documents._FILINGS[f["filing"]] for f in status.get("filings") or [] if f.get("filing") in documents._FILINGS), None)
    if not marked and not law:
        return ""
    parts = []
    if law:
        parts.append("restricted by law (" + (restricted.LAW_WORDS.get(law) or str(law)) + ")")
    if marked:
        parts.append(f"marked restricted by {marked.get('by') or 'an attorney'}" + (f" on {_mdy(marked.get('at'))}" if marked.get("at") else "")
                     + (f": {marked['reason']}" if marked.get("reason") else ""))
    people = [p.get("name") or p.get("email") for p in rec["people"] if p.get("name") or p.get("email")]
    who = "the attorneys" + (f" and {', '.join(str(p) for p in people)}" if people else "")
    return "This case was " + "; ".join(parts) + f". Who may open it: {who}."


# -- the page --------------------------------------------------------------------------------------------------------------------------------------


def glossary() -> str:
    """What every record is, from the catalog: its title, where it lives, each version, and each field with what it means (a field that holds a person's data is marked)."""
    out = ["<h2 id=\"records\">What each kind of record is</h2>", "<p class=\"sub\">Made from the product's data dictionary: the same words the product's own documentation uses.</p>"]
    for area, heading in (("case", "In each case's folder"), ("portal", "In each client's portal folder"), ("firm", "The firm's own files"), ("logs", "Logs")):
        out.append(f"<h3>{esc(heading)}</h3>")
        for r in (r for r in records.RECORDS if r["area"] == area):
            versions = "".join(f"<li>Version {esc(v)}: {esc(text)}</li>" for v, text in r.get("versions") or [])
            rows = "".join(f"<tr><td><code>{esc(n)}</code></td><td>{esc(meaning)}</td><td>{'a person&#8217;s data' if 'person' in flags else ''}{' (never exported)' if 'secret' in flags else ''}</td></tr>"
                           for n, _t, meaning, flags in r.get("fields") or [])
            out.append(f"<details id=\"r-{esc(r['id'])}\"><summary>{esc(r['title'])} <span class=\"sub\">{esc(', '.join(r['files']))}</span></summary>"
                       f"<p class=\"sub\">Written by: {esc(r.get('written_by'))}</p><ul>{versions}</ul>"
                       f"<table><tr><th>Field</th><th>What it is</th><th></th></tr>{rows}</table></details>")
    return "\n".join(out)


def _case_section(case: str, rels: list[tuple[str, Any]], portal: list[tuple[str, Any]], case_dir: Path | None, portal_root: Path | None) -> str:
    contents: dict[str, Any] = {}
    for rel, entry in rels:
        if rel.endswith(".json") and "/" not in rel:
            contents[rel] = _json(entry.source, entry.data)
    name, kind = _name_and_kind(case, contents, portal_root)
    restriction = _restriction(case_dir, contents)
    documents = {}
    for rec in (contents.get("documents.json") or {}).get("documents") or [] if isinstance(contents.get("documents.json"), dict) else []:
        for f in rec.get("files") or []:
            documents[f] = rec.get("type")
    record_rows, document_rows = [], []
    for rel, entry in rels:
        link = f'<a href="{href("cases", case, rel)}">{esc(rel)}</a>'
        if rel.lower().endswith(DOCUMENT_SUFFIXES):
            base = rel.rsplit("/", 1)[-1]
            document_rows.append(f"<tr><td>{link}</td><td>{esc((documents.get(base) or '').replace('_', ' '))}</td><td>{esc(entry.what)}</td></tr>")
            continue
        r = records.record_of("case", rel)
        record_rows.append(f"<tr><td>{esc(r['title'] if r else entry.what)}"
                           + (f" <a class=\"sub\" href=\"#r-{esc(r['id'])}\">its fields</a>" if r else "") + f"</td><td>{esc(schema_version(r, contents.get(rel)))}</td><td>{link}</td></tr>")
    facts = ""
    graph = contents.get("fact_graph.json")
    if isinstance(graph, dict) and graph.get("facts"):
        rows = "".join(f"<tr><td>{esc(fact_label(k))}</td><td>{esc(f.get('value'))}</td><td>{esc(f.get('status'))}</td></tr>"
                       for k, f in sorted(graph["facts"].items()) if isinstance(f, dict) and f.get("value") not in (None, ""))
        facts = f"<h3>Facts read from the documents</h3><table><tr><th>Fact</th><th>Value</th><th>Status</th></tr>{rows}</table>"
    portal_rows = "".join(f"<tr><td><a href=\"{href('portal', case, rel)}\">{esc(rel)}</a></td><td>{esc(entry.what)}</td></tr>" for rel, entry in portal)
    return (f"<details class=\"case\" id=\"case-{esc(case)}\" data-find=\"{esc((name + ' ' + case + ' ' + kind).lower())}\"><summary>{esc(name)} <span class=\"sub\">{esc(kind)} &middot; {esc(case)}</span>"
            + ('<span class="badge">restricted</span>' if restriction else "") + "</summary>"
            + (f"<p class=\"notice\">{esc(restriction)}</p>" if restriction else "")
            + f"<h3>Records</h3><table><tr><th>Record</th><th>Version</th><th>File</th></tr>{''.join(record_rows)}</table>" + facts
            + (f"<h3>Documents</h3><table><tr><th>File</th><th>Kind</th><th>What it is</th></tr>{''.join(document_rows)}</table>" if document_rows else "<p class=\"sub\">No documents in this case's folders.</p>")
            + (f"<h3>What the client gave through the portal</h3><table><tr><th>File</th><th>What it is</th></tr>{portal_rows}</table>" if portal_rows else "") + "</details>")


def _lines(entry: Any) -> int:
    raw = entry.source.read_bytes() if entry.source is not None else (entry.data or b"")
    return sum(1 for line in raw.splitlines() if line.strip())


def ledger_pages(ledger: list[tuple[str, Any]]) -> dict[str, bytes]:
    """{ledger/events-YYYY-MM.html: a table of that month's rows}, one page for each month's file, beside it in the zip."""
    pages: dict[str, bytes] = {}
    for arc, entry in ledger:
        if not arc.endswith(".jsonl") or not arc.rsplit("/", 1)[-1].startswith("events-"):
            continue
        rows = []
        for line in (entry.source.read_text(encoding="utf-8") if entry.source is not None else (entry.data or b"").decode("utf-8")).splitlines():
            try:
                r = json.loads(line)
            except ValueError:
                continue
            if isinstance(r, dict):
                rows.append("<tr>" + "".join(f"<td>{esc(c)}</td>" for c in (r.get("at"), r.get("who"), r.get("role"), r.get("via"), r.get("case") or "(the firm's own records)",
                                                                           r.get("kind"), r.get("action"), r.get("what"))) + "</tr>")
        name = arc.rsplit("/", 1)[-1].removesuffix(".jsonl")
        pages[f"ledger/{name}.html"] = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>The ledger, {esc(name[7:])}</title>"
                                        f"<style>{STYLE}</style></head><body><main><p><a href=\"../index.html#ledger\">Back to the export's first page</a></p><h1>The ledger, {esc(name[7:])}</h1>"
                                        f"<p class=\"sub\">{len(rows)} rows. Who changed what, on which case, and when: a short sentence for each change, never a value. The file is <a href=\"{esc(arc.rsplit('/', 1)[-1])}\">{esc(arc.rsplit('/', 1)[-1])}</a>.</p>"
                                        "<table><tr><th>When</th><th>Who</th><th>Role</th><th>How</th><th>Case</th><th>Record</th><th>Action</th><th>What changed</th></tr>"
                                        + "".join(rows) + "</table></main></body></html>").encode("utf-8")
    return pages


def build(entries: list[Any], *, date: str, who: str, version: str, clients: Path | None = None, portal: Path | None = None,
          progress: Callable[[str], None] | None = None) -> dict[str, bytes]:
    """{path in the zip: bytes}: index.html and a page for each month of the ledger. entries: the export's entries (arcname, what, source, data) as tools/export_firm.py
    gathered them, before the page is added. clients, portal: the case folders and the portal's folder, for the restriction record and the client's name."""
    cases: dict[str, list[tuple[str, Any]]] = {}
    portals: dict[str, list[tuple[str, Any]]] = {}
    firm: list[Any] = []
    ledger: list[tuple[str, Any]] = []
    for e in entries:
        top, _, rest = e.arcname.partition("/")
        if top == "cases":
            cid, _, rel = rest.partition("/")
            cases.setdefault(cid, []).append((rel, e))
        elif top == "portal":
            cid, _, rel = rest.partition("/")
            portals.setdefault(cid, []).append((rel, e))
        elif top in ("firm", "logs"):
            firm.append(e)
        elif top == "ledger":
            ledger.append((e.arcname, e))
    pages = ledger_pages(ledger)
    case_html = []
    for cid in sorted(cases):
        case_html.append(_case_section(cid, cases[cid], portals.get(cid, []), (clients / cid) if clients is not None and (clients / cid).is_dir() else None, portal))
        if progress:
            progress(cid)
    only_portal = sorted(set(portals) - set(cases))
    firm_rows = []
    for e in sorted(firm, key=lambda e: e.arcname):
        rel = e.arcname.partition("/")[2]
        area = "logs" if e.arcname.startswith("logs/") else "firm"
        r = records.record_of(area, rel)
        content = _json(e.source, e.data) if e.arcname.endswith(".json") else None
        firm_rows.append(f"<tr><td>{esc(r['title'] if r else e.what)}" + (f" <a class=\"sub\" href=\"#r-{esc(r['id'])}\">its fields</a>" if r else "")
                         + f"</td><td>{esc(schema_version(r, content))}</td><td><a href=\"{href(e.arcname)}\">{esc(e.arcname)}</a></td></tr>")
    ledger_rows = []
    for arc, e in ledger:
        name = arc.rsplit("/", 1)[-1]
        if name.startswith("events-") and name.endswith(".jsonl"):
            n = _lines(e)
            month = name[7:-6]
            ledger_rows.append(f"<tr><td>{esc(month)}</td><td>{n}</td><td><a href=\"{href('ledger', name[:-6] + '.html')}\">read as a table</a></td><td><a href=\"{href(arc)}\">{esc(name)}</a></td></tr>")
        else:
            ledger_rows.append(f"<tr><td colspan=\"2\">{esc(e.what)}</td><td></td><td><a href=\"{href(arc)}\">{esc(name)}</a></td></tr>")
    left_out = "".join(f"<li><b>{esc(what)}</b>: {esc(why)}</li>" for what, why in records.NEVER_EXPORTED)
    databases = "".join(f"<li><b>{esc(d['title'])}</b> ({esc(d['where'])}): {esc(d['written_by'])}</li>" for d in records.DATABASES)
    title = f"The firm's records, exported {_mdy(date)}"
    page = (f"<!doctype html><html lang=\"en\"><head><meta charset=\"utf-8\"><meta name=\"viewport\" content=\"width=device-width,initial-scale=1\"><title>{esc(title)}</title><style>{STYLE}</style></head>"
            f"<body><main><h1>{esc(title)}</h1>"
            f"<p class=\"sub\">Made on {esc(_mdy(date))} by {esc(who)} from version {esc(version)} of the case system. "
            f"{len(cases)} case{'s' if len(cases) != 1 else ''}. This page and the files it links to need no software to read: open it in any browser, with scripts off if you like.</p>"
            "<p class=\"notice\">This export holds every case, restricted ones included: they are the firm's data. Keep it on encrypted storage and send it only over an encrypted channel.</p>"
            "<p><a href=\"#cases\">Cases</a> &middot; <a href=\"#firm\">The firm's own records</a> &middot; <a href=\"#ledger\">The ledger</a> &middot; <a href=\"#left-out\">What is not here</a> &middot; <a href=\"#records\">What each kind of record is</a></p>"
            f"<h2 id=\"cases\">Cases ({len(cases)})</h2><p><input id=\"find\" type=\"search\" placeholder=\"Find a client or a kind of case (needs scripts; the list below is complete without them)\"> "
            "<button id=\"openall\" type=\"button\">Open every case</button></p>" + "\n".join(case_html)
            + (f"<h3>Clients in the portal with no case folder yet ({len(only_portal)})</h3><ul>" + "".join(
                f"<li>{esc(c)}: " + ", ".join(f'<a href="{href("portal", c, rel)}">{esc(rel)}</a>' for rel, _e in portals[c]) + "</li>" for c in only_portal) + "</ul>" if only_portal else "")
            + f"<h2 id=\"firm\">The firm's own records</h2><table><tr><th>Record</th><th>Version</th><th>File</th></tr>{''.join(firm_rows)}</table>"
            f"<h2 id=\"ledger\">The ledger</h2><p class=\"sub\">Who changed what, on which case, and when, a file for each month. Each row is chained to the one before it, and the daily seals say the ledger was not changed afterward (<code>python tools/verify_ledger.py --data &lt;the ledger folder&gt;</code>).</p>"
            f"<table><tr><th>Month</th><th>Rows</th><th>Table</th><th>File</th></tr>{''.join(ledger_rows)}</table>"
            f"<h2 id=\"left-out\">What the product keeps that is not in this export, and why</h2><ul>{left_out}</ul><p class=\"sub\">Databases the product keeps and does not export:</p><ul>{databases}</ul>"
            f"{glossary()}"
            f"<script>{SCRIPT}</script></main></body></html>")
    pages["index.html"] = page.encode("utf-8")
    return pages
