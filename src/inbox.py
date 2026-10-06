"""The notice inbox: the day's USCIS and immigration court mail, scanned into
one folder, each notice put on its case (docs/design_plan.md Part 3).

    data/inbox/              the paralegal scans the day's mail here (PDF, or a phone photo: JPG, PNG);
                             I485_INBOX points elsewhere (a connector's inbox folder can drop files here too)
    data/inbox/waiting/      what couldn't be matched, one PDF per notice, until a reviewer places it
    data/inbox/not_ours/     what a reviewer said isn't the firm's, with a note (who, when, why)
    data/inbox/originals/    a combined scan or a photo once its notices were split out of it
    data/inbox/queue.json    the waiting notices with the reader's guess
    data/inbox/inbox_log.jsonl  every notice routed, placed, queued or set aside: who, when, where

process_inbox(clients_root, inbox) reads every file in the folder: it is
split into its documents (classify.split_documents, then one part per
notice: a new receipt number, or court mail for another A-Number, starts a
new one), each part is read (extract/uscis_notice.py, extract/eoir_notice.py)
and matched to a case:

  1. by the receipt number, against the identifiers in the firm-wide index
     (src/index.py) and the receipts recorded on the cases' filings: one
     case, routed; several, it waits for a person;
  2. a new receipt number (or court mail, which has none): by the A-Number,
     and the name on the notice must be read and be the client's: one case,
     routed; several, no name read, or a name that disagrees, it waits (a
     single misread digit must not put a notice on another client's case);
  3. a name alone never routes (two clients can share one): it waits, with
     the matching cases offered first in the "this belongs to" picker.

A routed notice is moved into the case's document folder as
inbox_<date>_<original>.pdf (one part per notice when a combined scan held
several) and the case is reprocessed for that one document only
(batch.process_documents on the new file, its facts added to the case's
graph, then its document record, the index and the timeline): the case's
other documents are not read again. An immigration court hearing notice
starts the hearing record src/journey.py keeps, marked as read from the
notice until a person confirms it. An RFE or NOID lands with its due date on
the timeline and its response builder ready (src/rfe.py); nothing reads what
the request asks (rfe.py's rule: a person types each item from the notice).

Nothing is deleted: a matched file is moved, never copied; what isn't
matched stays in the inbox (waiting/) until a reviewer places it or sets it
aside as not ours. The overnight run reads the inbox before the timelines
(src/overnight.py), and My work has "Read the inbox now".
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import re
import tempfile
import time
from contextlib import closing
from datetime import date

import clock
import events
from pathlib import Path
from typing import Any
import schema_path

REPO = Path(__file__).resolve().parent.parent
SUFFIXES = (".pdf", ".jpg", ".jpeg", ".png")
# what the notice reader reads: the I-797 in all its sub-types (classify.classifier._subclassify_notice), and the court's notice
USCIS_TYPES = {"uscis_notice", "i360_approval", "i765_approval", "i130_approval", "i485_receipt", "i526_approval", "i590_approval", "i730_approval"}
COURT_TYPE = "eoir_hearing_notice"
WHO = "Notice inbox"  # who routed it, on the case's records, when no person did
QUEUE, LOG, LOCK = "queue.json", "inbox_log.jsonl", ".reading"
WAITING, NOT_OURS, ORIGINALS = "waiting", "not_ours", "originals"
STALE_LOCK = 2 * 3600  # seconds: a run that died leaves its lock; the next one takes it over
_I797 = re.compile(r"[I1]-?797|NOTICE\s+OF\s+ACTION|Receipt\s+Number", re.I)


class InboxBusy(ValueError):
    """Another reading of the inbox is under way."""


def default_path(clients_root: str | Path) -> Path:
    """I485_INBOX, else data/inbox next to the client folders."""
    env = os.environ.get("I485_INBOX")
    return Path(env) if env else Path(clients_root).resolve().parent / "inbox"


def _now() -> str:
    return clock.stamp()


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _write(path: Path, data: Any) -> None:
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _log(inbox: Path, entry: dict[str, Any]) -> None:
    with open(inbox / LOG, "a", encoding="utf-8") as f:
        f.write(json.dumps({"at": _now()} | entry, ensure_ascii=False) + "\n")


def _setup(inbox: Path) -> None:
    for sub in ("", WAITING, NOT_OURS, ORIGINALS):
        (inbox / sub).mkdir(parents=True, exist_ok=True)


class _Lock:
    """One reading at a time (the overnight run and the button may meet)."""

    def __init__(self, inbox: Path):
        self.path = inbox / LOCK

    def __enter__(self):
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        except FileExistsError:
            if time.time() - self.path.stat().st_mtime < STALE_LOCK:
                raise InboxBusy("The inbox is being read right now: try again in a few minutes.") from None
            self.path.unlink(missing_ok=True)
            fd = os.open(self.path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        os.write(fd, f"{os.getpid()} {_now()}".encode())
        os.close(fd)
        return self

    def __exit__(self, *exc):
        self.path.unlink(missing_ok=True)


# -- reading a scan -------------------------------------------------------------------


def _pdf_bytes(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        return data
    from portal.store import image_to_pdf

    return image_to_pdf(data)  # a phone photo of a notice: one format downstream


def _pages_of(data: bytes) -> list[str]:
    from classify import extract_pages

    with tempfile.TemporaryDirectory() as tmp:
        path = Path(tmp) / "scan.pdf"
        path.write_bytes(data)
        return extract_pages(path)


def _subset(data: bytes, first: int, last: int) -> bytes:
    from pypdf import PdfReader, PdfWriter

    reader, writer = PdfReader(io.BytesIO(data)), PdfWriter()
    for i in range(first, last + 1):
        writer.add_page(reader.pages[i])
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def _mail_key(page: str) -> tuple | None:
    """What says a page begins a piece of mail: a USCIS notice's receipt number (and its notice date, when printed),
    or the court's mail and the A-Number on it. None for a page that only continues one."""
    from extract import eoir_notice, uscis_notice

    court = eoir_notice.parse(page)
    if court is not None and court.kind in ("hearing", "decision"):
        return ("court", court.a_number or "")
    if _I797.search(page):
        n = uscis_notice.parse(page)
        if n.receipt:
            return ("uscis", n.receipt, n.notice_date)
    return None


def _same_mail(a: tuple | None, b: tuple | None) -> bool:
    if a is None or b is None or a[:2] != b[:2]:
        return False
    return a[0] != "uscis" or not (a[2] and b[2]) or a[2] == b[2]  # page 2 of a notice repeats the receipt, often without the date


def parts(pages: list[str]) -> list[tuple[int, int, str]]:
    """[(first page, last page, type)]: the documents in a scan, each notice its own part. split_documents keeps a run of
    pages of one type together (three notices are one "USCIS notice" to it); a new receipt number or court mail for
    another A-Number begins a new part here."""
    from classify import classify_text, split_documents

    out: list[list] = []
    for first, last, _kind in split_documents(pages) if pages else []:
        current = [first, first]
        key = _mail_key(pages[first])
        for i in range(first + 1, last + 1):
            k = _mail_key(pages[i])
            if k is not None and not _same_mail(k, key):
                out.append(current)
                current, key = [i, i], k
            else:
                current[1] = i
                key = key or k
        out.append(current)
    return [(a, b, classify_text("\n".join(pages[a:b + 1])).doc_type) for a, b in out]


def read(text: str, doc_type: str) -> dict[str, Any]:
    """The reader's guess, as the queue shows it and the matching uses it. For an RFE or NOID: that it exists and its
    due date, nothing of what it asks (src/rfe.py)."""
    from extract import eoir_notice, uscis_notice

    court = eoir_notice.parse(text)
    if doc_type == COURT_TYPE or (court is not None and court.kind in ("hearing", "decision")):
        court = court or eoir_notice.CourtMail(kind="hearing")
        out = {"court": True, "kind": court.kind if court.kind in ("hearing", "decision") else "hearing", "receipt": None, "form": None,
               "a_number": court.a_number, "names": [court.name] if court.name else []}
        if out["kind"] == "hearing":
            out["hearing"] = {"date": court.date, "time": court.time, "kind": court.hearing_kind, "place": court.place}
            if court.dates:  # a hearing cancelled and another set, with no "scheduled for ... on": a person says which
                out["hearing"]["dates"] = court.dates
        return out
    n = uscis_notice.parse(text)
    if n.receipt and (doc_type in USCIS_TYPES or _I797.search(text)):
        return {"court": False, "kind": n.kind, "receipt": n.receipt, "form": n.form, "date": n.notice_date or n.received_date,
                "due": n.due_date if n.kind in ("rfe", "noid") else None, "appointment": n.appointment,
                "a_number": uscis_notice.person_a_number(text), "names": uscis_notice.person_names(text)}
    return {"court": False, "kind": None, "type": doc_type, "receipt": None, "a_number": None, "names": []}


def describe(r: dict[str, Any]) -> str:
    """The reader's guess in a line, for the queue and the morning report."""
    import journey

    if r.get("court"):
        if r["kind"] == "decision":
            return "Immigration court decision or order"
        h = r.get("hearing") or {}
        what = (h.get("kind") or "").lower()
        if h.get("dates"):
            return "Immigration court hearing notice" + (f": {what} hearing" if what else "") \
                + f" (more than one hearing date on the letter: {', '.join(journey.us(d) for d in h['dates'])})"
        return "Immigration court hearing notice" + (f": {what} hearing" if what else "") + (f" on {journey.us(h['date'])}" if h.get("date") else "") \
            + (f" at {h['time']}" if h.get("time") else "") + ("" if h.get("date") else " (the date wasn't read)")
    if not r.get("kind"):
        from documents import name

        return f"Not a notice the reader knows ({name(r.get('type') or 'unclassified')})"
    what = f"{r.get('form') or 'USCIS'} {journey.KIND_NAMES.get(r['kind'], 'notice')} (receipt {r['receipt']})"
    if r.get("date"):
        what += f", notice of {journey.us(r['date'])}"
    if r["kind"] in ("rfe", "noid"):
        what += f", response due {journey.us(r['due'])}" if r.get("due") else ", the due date wasn't read: take it from the notice"
    if r.get("appointment"):
        what += f", appointment {journey.us(r['appointment'][:10])}{(' ' + r['appointment'][11:]) if len(r['appointment']) > 10 else ''}"
    return what


# -- finding the case ---------------------------------------------------------------------


def _name_words(name: str) -> set[str]:
    from documents import _tokens

    return _tokens(name)


def name_matches(notice_name: str, client_name: str) -> bool:
    """Every word of the name on the notice is one of the client's ("SOUZA, ANA CLARA" and Ana Clara Exemplo Souza),
    and there are two at least (a family name alone is no match)."""
    words = _name_words(notice_name)
    return len(words) >= 2 and words <= _name_words(client_name)


class Directory:
    """The firm's cases as the inbox sees them, read once per run: names, and who holds a receipt number or an A-Number
    (the firm-wide index, src/index.py, every case included: restricted ones are routed like any other)."""

    def __init__(self, clients_root: Path, db_path: Path | None = None, refresh: bool = True):
        import index

        self.root = Path(clients_root)
        self.db_path = Path(db_path) if db_path else index.default_path(self.root)
        if refresh:
            try:
                index.rebuild_changed(self.root, self.db_path)  # a case processed since the last refresh
            except Exception:  # noqa: BLE001 -- matching uses what the index has
                pass
        self.cases = {p.name for p in self.root.iterdir() if (p / "fact_graph.json").exists()} if self.root.is_dir() else set()
        self._names: dict[str, str] | None = None
        self._filed: dict[str, set[str]] | None = None

    def _query(self, sql: str, args: tuple) -> list:
        import index

        if not self.db_path.exists():
            return []
        try:
            with closing(index.connect(self.db_path)) as db:
                return db.execute(sql, args).fetchall()
        except Exception:  # noqa: BLE001 -- a damaged index: the nightly run builds it again; matching falls back to waiting
            return []

    def names(self) -> dict[str, str]:
        if self._names is None:
            import index

            got = {r["case_id"]: r["name"] for r in self._query("select case_id, name from cases", ())}
            self._names = {c: got.get(c) or index._name_and_state(self.root / c)[0] for c in sorted(self.cases)}
        return self._names

    def by_receipt(self, receipt: str) -> set[str]:
        import index

        code = index.norm_code(receipt)
        found = {r["case_id"] for r in self._query("select distinct case_id from documents where receipt = ?", (code,))}
        if self._filed is None:  # a receipt typed on a filing record (an online filing: src/online_filing.py)
            self._filed = {}
            for c in self.cases:
                for f in _read(self.root / c / "status.json", {}).get("filings") or []:
                    if isinstance(f, dict) and f.get("receipt"):
                        self._filed.setdefault(index.norm_code(f["receipt"]), set()).add(c)
        return (found | self._filed.get(code, set())) & self.cases

    def by_a_number(self, a_number: str) -> set[str]:
        import index

        digits = index.norm_a_number(a_number)
        if not digits:
            return set()
        return {r["case_id"] for r in self._query("select distinct case_id from documents where a_number = ?", (digits,))} & self.cases

    def by_name(self, names: list[str]) -> set[str]:
        return {c for c, n in self.names().items() if any(name_matches(x, n) for x in names)}

    def find(self, text: str, limit: int = 20) -> list[dict[str, str]]:
        """The picker: cases whose name or folder has the words typed, or whose documents carry the A-Number or receipt typed."""
        import index

        q = (text or "").strip()
        if not q:
            return []
        hits: list[str] = []
        ident = index._identifier(q)
        if ident:
            hits += sorted(self.by_a_number(ident[0]) | self.by_receipt(ident[1]))
        words = _name_words(q)
        for c, n in self.names().items():
            hay = _name_words(n) | _name_words(c.replace("-", " ").replace("_", " "))
            if c not in hits and ((words and all(any(h.startswith(w) for h in hay) for w in words)) or q.lower() in c.lower()):
                hits.append(c)
        return [{"case": c, "name": self.names().get(c) or c} for c in hits[:limit]]


def match(r: dict[str, Any], directory: Directory) -> dict[str, Any]:
    """{"status": "routed" | "ambiguous" | "unmatched", "case", "how", "why", "candidates"}: the routing rule (module docstring).
    A court letter with more than one hearing date and no "scheduled for ... on" waits for a person even when its case is known."""
    found = _match(r, directory)
    dates = (r.get("hearing") or {}).get("dates")
    if found["status"] == "routed" and dates:
        import journey

        return {"status": "ambiguous", "candidates": [found["case"]],
                "why": f"The letter gives more than one hearing date ({', '.join(journey.us(d) for d in dates)}): read which one is the hearing, "
                       "place it, then enter the hearing on the case by hand."}
    return found


def _match(r: dict[str, Any], directory: Directory) -> dict[str, Any]:
    names = [n for n in r.get("names") or [] if n]
    if not r.get("kind") and not r.get("court"):
        return {"status": "unmatched", "why": "This isn't a USCIS or court notice the reader knows: place it by hand or set it aside.", "candidates": []}
    if r.get("receipt"):
        hits = directory.by_receipt(r["receipt"])
        if len(hits) == 1:
            return {"status": "routed", "case": next(iter(hits)), "how": "receipt number"}
        if hits:
            return {"status": "ambiguous", "why": f"{len(hits)} cases hold receipt number {r['receipt']}: choose the one this notice belongs to.",
                    "candidates": sorted(hits)}
    if r.get("a_number"):
        hits = directory.by_a_number(r["a_number"])
        named = {c for c in hits if any(name_matches(n, directory.names().get(c, "")) for n in names)}
        if len(hits) == 1:
            case = next(iter(hits))
            if named:
                return {"status": "routed", "case": case, "how": "A-Number and name"}
            if not names:  # one misread digit of nine and nothing to cross-check it with: a person looks
                return {"status": "ambiguous", "candidates": [case],
                        "why": "The A-Number is on one case, but no name was read from the notice to confirm it: check before placing it."}
            return {"status": "ambiguous", "why": f"The A-Number is on one case, but the name on the notice ({'; '.join(names)}) isn't that client's: check before placing it.",
                    "candidates": [case]}
        if len(named) == 1:
            return {"status": "routed", "case": next(iter(named)), "how": "A-Number and name"}
        if hits:
            return {"status": "ambiguous", "why": f"{len(hits)} cases have this A-Number: choose the one this notice belongs to.",
                    "candidates": sorted(named or hits)}
    by_name = directory.by_name(names) if names else set()
    if by_name:
        return {"status": "ambiguous", "candidates": sorted(by_name),
                "why": "The name on the notice matches " + ("one case" if len(by_name) == 1 else f"{len(by_name)} cases")
                       + ", but no receipt number or A-Number confirms it: check before placing it."}
    return {"status": "unmatched", "candidates": [],
            "why": "No case has this receipt number" + (", A-Number" if r.get("a_number") else "") + (" or name" if names else "")
                   + ": a new case, a case not processed yet, or not the firm's."}


# -- putting a notice on its case -------------------------------------------------------------


def _safe(name: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(name).stem).strip("._") or "scan"
    return stem[:60]


def _unique(folder: Path, name: str) -> Path:
    path, n = folder / name, 2
    while path.exists():
        path = folder / f"{Path(name).stem}-{n}{Path(name).suffix}"
        n += 1
    return path


def _portal_upload(folder: Path, stored: str, data: bytes, source: str = "scan_inbox", by: str | None = None) -> None:
    """A portal client's documents are its uploads (src/portal/engine.py reads uploads.json): the notice is listed there
    too, so the portal's next processing keeps it. The client never sees it (it answers no request of theirs), and its
    "source" (scan_inbox, or folder for a scan a staff member added in the review app) keeps it out of the client's
    retakes. by: who added it, for a staff upload."""
    if folder.name != "uploads" or not (folder.parent / "profile.json").exists():
        return
    from portal.store import PortalStore

    store = PortalStore(folder.parent.parent.parent)
    client = folder.parent.name
    record = {"id": Path(stored).stem, "doc_id": source, "filename": stored, "stored": stored, "size": len(data),
              "sha256": hashlib.sha256(data).hexdigest(), "uploaded_at": _now(), "status": "checked", "source": source,
              **({"by": by} if by else {})}
    store.update_uploads(client, store.uploads(client) + [record])


def reprocess(client_dir: Path, folder: Path, doc_id: str, text: str, pages: int, db_path: Path | None = None) -> dict[str, Any]:
    """The case, brought up to date with one new document (reprocess_documents)."""
    return reprocess_documents(client_dir, folder, [(doc_id, text)], {doc_id: {"pages": pages}}, db_path)


def reprocess_documents(client_dir: Path, folder: Path, documents_: list[tuple[str, str]], split_info: dict[str, dict[str, Any]],
                        db_path: Path | None = None, source: str = "scan_inbox", who: str | None = None,
                        pages: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """_reprocess_documents under the case's lock (src/jobs.py): the job worker, the overnight run and the review app take turns on a case, so no write is lost."""
    import jobs

    with jobs.case_lock(jobs.folder_for(client_dir.parent), client_dir.name):
        return _reprocess_documents(client_dir, folder, documents_, split_info, db_path, source, who, pages)


def _reprocess_documents(client_dir: Path, folder: Path, documents_: list[tuple[str, str]], split_info: dict[str, dict[str, Any]],
                         db_path: Path | None, source: str, who: str | None, pages: dict[str, list[str]] | None = None) -> dict[str, Any]:
    """The case, brought up to date with new documents and nothing else read again: their facts are added to the case's
    graph (re-derived as the case was), then their classification, their document records, the case's row in the index,
    the filled I-485 and the timeline's cache. documents_: (doc id, text) per document (a combined file is one per part,
    "file.pdf#p1-2"); split_info: {file name or doc id: {"pages": n}}; source: where the records say they came from
    (documents.SOURCES). Staff uploads in the review app (review/operator.py) come through here too. who: the person who added it
    (the event ledger's row, src/events.py); none: the notice inbox, or the reader for a client's own photo."""
    import documents
    import index
    import document_instances
    from batch import derive, process_documents
    from factgraph import FactGraph
    from review.state import _derivation

    case = client_dir.name
    names = list({d.split("#p", 1)[0] for d, _ in documents_})
    if pages is None:  # notice routing used to pass joined text, losing physical boundaries
        from classify import extract_pages
        pages = {name: extract_pages(folder / name) for name in names}
        documents_ = [(name, "\n".join(pages[name])) for name in names]
    result = process_documents(case, documents_, pages=pages, boundary_context=document_instances.context(folder, client_dir, names))
    document_instances.invalidate(client_dir, result.boundary_plans)
    document_instances.stage(client_dir, result.boundary_plans)
    from batch import record_documents
    record_documents(result, folder, result.document_texts, source=source)
    built = result.documents
    if built is None:
        raise ValueError("Document records could not be saved; review and read this source again.")
    meta = _read(client_dir / "meta.json", {})
    raw_path = client_dir / "fact_graph_raw.json"
    base = FactGraph.load(raw_path if raw_path.exists() else client_dir / "fact_graph.json")
    replaced = {s.doc_id for f in base.all_facts().values() for s in f.sources if s.doc_id.split("#p", 1)[0] in names}
    document_instances.without_sources(base, replaced)
    for key, fact in (result.raw_graph or result.graph).all_facts().items():
        for s in fact.sources:
            base.add_source(key, s.doc_id, s.doc_type, s.raw_value, s.normalized_value, s.confidence, tier=fact.tier, page=s.page,
                            instance_id=s.instance_id, subject_role=s.subject_role,
                            evidence_version=s.evidence_version, input_evidence=s.input_evidence,
                            read_manifest=s.read_manifest, reading_issues=s.reading_issues)
    if raw_path.exists():
        base.save(raw_path)
        graph = FactGraph.from_dict(base.to_dict())
        rules, policies = _derivation(meta.get("derivation") or {})
        derive(graph, rules, policies)
        graph.save(client_dir / "fact_graph.json")
    else:  # a bundle from before the raw graph was kept: the facts go straight into the saved graph
        base.save(client_dir / "fact_graph.json")
    meta["classifications"] = {k: v for k, v in meta.get("classifications", {}).items() if k.split("#p", 1)[0] not in names}
    for doc_id, found in result.classifications.items():
        meta.setdefault("classifications", {})[doc_id] = found.doc_type
    doc_type = next(iter(result.classifications.values())).doc_type
    (client_dir / "meta.json").write_text(json.dumps(meta, indent=2, ensure_ascii=False, default=str), encoding="utf-8")

    data = documents.read(client_dir) or {"version": documents.VERSION, "built": None, "documents": []}
    # Replace only records for this original, retaining unrelated uploads and
    # explicit human metadata on unchanged instances through the normal merge.
    incoming = documents.merge(data, built)["documents"]
    data["documents"] = [r for r in data["documents"] if not any(d.split("#p", 1)[0] in names for d in r.get("doc_ids", []))]
    have = {r["id"]: r for r in data["documents"]}
    for r in incoming:
        if r["id"] in have:  # the same scan twice: one record, two files
            twin = have[r["id"]]
            twin["files"] = twin["files"] + [f for f in r["files"] if f not in twin["files"]]
            twin["doc_ids"] = (twin.get("doc_ids") or []) + [d for d in r["doc_ids"] if d not in (twin.get("doc_ids") or [])]
        else:
            data["documents"].append(r)
    data["built"] = built["built"]
    data.setdefault("boundary_plans", {}).update(result.boundary_plans)
    data["boundary_processing"] = [n for n in data.get("boundary_processing", []) if n not in result.boundary_plans]
    documents.save(client_dir, documents.with_case_confidentiality(client_dir, data))
    events.record("imports", "added", f"Added {len(documents_)} document(s) to the case from {documents.SOURCE_WORDS.get(source, source)}", case_dir=client_dir,
                  who=who, version=documents.VERSION,
                  default_who=("The document reader", "system", "system") if source == "portal" else (WHO, "system", "importer"))
    out = {"type": doc_type, "refilled": False}
    try:  # the flag report and the filled I-485, as a full run leaves them (seconds; never stops the routing)
        from fill import load_field_map
        from review.state import refill

        refill(client_dir, load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))
        out["refilled"] = True
    except Exception as exc:  # noqa: BLE001
        out["refill_error"] = type(exc).__name__
    try:
        out["indexed"] = index.rebuild(client_dir, db_path)
    except Exception as exc:  # noqa: BLE001 -- the nightly run catches up on the index
        out["index_error"] = type(exc).__name__
    try:  # the query layer's rows for the case (src/query.py): the nightly run catches up if this fails
        import query

        query.rebuild(client_dir)
    except Exception as exc:  # noqa: BLE001
        out["query_error"] = type(exc).__name__
    try:  # the timeline: the dashboard's cache, so My work and the case show the notice at once
        from review.overview import journey_row

        journey_row(client_dir)
    except Exception as exc:  # noqa: BLE001
        out["journey_error"] = type(exc).__name__
    return out


def _hearing(client_dir: Path, r: dict[str, Any], doc_id: str) -> str | None:
    """The hearing a court notice announces, on the case's timeline as read from the notice (a person confirms it)."""
    import journey

    h = r.get("hearing") or {}
    if not r.get("court") or r.get("kind") != "hearing" or not h.get("date"):
        return None
    marks = (_read(client_dir / "status.json", {}).get("journey") or {})
    same = next((x for x in marks.get("hearings") or [] if x.get("date") == h["date"] and (x.get("time") or None) == (h.get("time") or None)), None)
    if same:  # entered already (by hand, or from another copy of the notice)
        return same["id"]
    status = journey.mark(client_dir, "hearing", WHO, value={"date": h["date"], "time": h.get("time"), "kind": h.get("kind") or "Other",
                                                             "court": h.get("place"), "source": doc_id},
                          note=None if h.get("kind") else "The kind of hearing wasn't read: check it on the notice.")
    return status["journey"]["hearings"][-1]["id"]


def route(clients_root: Path, inbox: Path, case: str, data: bytes, text: str, r: dict[str, Any], name: str, how: str,
          who: str = WHO, db_path: Path | None = None, state_path: Path | None = None) -> dict[str, Any]:
    """The notice into the case's document folder as `name`, and the case brought up to date with it (under the case's lock: src/jobs.py)."""
    import jobs

    client_dir = Path(clients_root) / case
    if not (client_dir / "fact_graph.json").exists():
        raise LookupError("unknown client")
    with jobs.case_lock(jobs.folder_for(client_dir.parent), case):
        return _route(clients_root, inbox, case, data, text, r, name, how, who, db_path, state_path)


def _route(clients_root: Path, inbox: Path, case: str, data: bytes, text: str, r: dict[str, Any], name: str, how: str, who: str, db_path: Path | None,
           state_path: Path | None) -> dict[str, Any]:
    import rfe

    client_dir = Path(clients_root) / case
    folder = Path(_read(client_dir / "meta.json", {}).get("source_folder") or "")
    if not str(folder) or not folder.is_dir():
        raise ValueError("The case's document folder can't be reached from this machine: place it again once it can.")
    from overnight import source_signature

    before = source_signature(folder)
    meta_path = client_dir / "meta.json"
    meta_mtime = meta_path.stat().st_mtime if meta_path.exists() else None
    newest = max((p.stat().st_mtime for p in folder.glob("*.pdf")), default=0.0)
    path = _unique(folder, name)
    path.write_bytes(data)
    _portal_upload(folder, path.name, data)
    entry = {"event": "routed" if who == WHO else "placed", "case": case, "by": who, "how": how, "file": path.name, "read": r,
             "what": describe(r)}
    try:
        from pypdf import PdfReader

        done = reprocess(client_dir, folder, path.name, text, len(PdfReader(io.BytesIO(data)).pages), db_path)
        entry["processed"] = True
        entry["type"] = done["type"]
        hearing = _hearing(client_dir, r, path.name)
        if hearing:
            entry["hearing"] = hearing
    except Exception as exc:  # noqa: BLE001 -- the file is on the case: the next full run of the case reads it
        entry.update(processed=False, error=f"{type(exc).__name__}: {exc}"[:300])
    if r.get("kind") in ("rfe", "noid"):
        entry["rfe_key"] = rfe.key_of({"receipt": r["receipt"], "kind": r["kind"], "date": r.get("date")})
    _keep_overnight_honest(case, folder, meta_path, meta_mtime, newest, before, state_path, bool(entry.get("processed")))
    entry["name"] = _client_name(client_dir)
    _log(inbox, entry)
    return entry


def _keep_overnight_honest(case: str, folder: Path, meta_path: Path, meta_mtime: float | None, newest: float, before: str,
                           state_path: Path | None, processed: bool, pending=(), this: str | None = None) -> None:
    """The overnight run (src/overnight.py choose) re-runs a folder whose signature changed since its last run, and takes a
    case with no record yet as processed when meta.json is newer than every PDF. The inbox's own file is read already, but
    nothing else in the folder may look read because of it:
      - a record that matched the folder before the notice came: its signature now includes the notice;
      - no record, and the case was up to date before (meta.json newer than every PDF): a record is written, so the
        notice doesn't make the whole folder run again;
      - otherwise the record is left alone (another document waits for its run).
    meta.json keeps its old time either way, so a document added before the notice still looks unread to the
    "processed before" check.

    pending: scans the office added to this case that no job has read (waiting, running, failed or dead). The signature written is the signature of what has been read:
    it leaves them out, so a scan whose job fails or dies is a change the overnight run still sees (a scan is never lost). `this` is the file this job read: with it the
    record is checked against the folder as it was before it, not against a signature taken when the scan was staged (another scan may have landed since)."""
    from overnight import source_signature

    skip = set(pending or ())
    if meta_mtime is not None and meta_path.exists():
        when = meta_mtime
        if skip:  # meta.json must not look newer than a scan nobody has read ("processed before" in the overnight run's choice)
            waiting = [(folder / n).stat().st_mtime for n in skip if (folder / n).exists()]
            if waiting:
                when = min(when, min(waiting)) - 1
        os.utime(meta_path, (when, when))
    if state_path is None or not processed:
        return
    state = _read(state_path, {})
    record = state.get(case)
    if record is not None:
        expected = source_signature(folder, exclude=skip | {this}) if this else before
        if record.get("signature") != expected:
            return
        record["signature"] = source_signature(folder, exclude=skip)
    elif meta_mtime is not None and meta_mtime >= newest and not skip:
        state[case] = {"status": "done", "at": _now(), "version": None, "why": "processed earlier", "signature": source_signature(folder)}
    else:
        return
    _write(state_path, state)


def _client_name(client_dir: Path) -> str:
    import index

    return index._name_and_state(client_dir)[0]


# -- the run ----------------------------------------------------------------------------------


def _queue(inbox: Path) -> list[dict[str, Any]]:
    return _read(inbox / QUEUE, [])


def _wait(inbox: Path, data: bytes, r: dict[str, Any], found: dict[str, Any], original: str, pages: list[int], of: int, name: str,
          directory: Directory) -> dict[str, Any]:
    queue = _queue(inbox)
    digest = hashlib.sha256(data).hexdigest()
    same = next((q for q in queue if q["hash"] == digest), None)
    if same:  # the same scan dropped in twice
        return same
    path = _unique(inbox / WAITING, name)
    path.write_bytes(data)
    entry = {"id": digest[:12], "hash": digest, "file": path.name, "original": original, "pages": pages, "of": of, "scanned": clock.today().isoformat(),
             "read": r, "what": describe(r), "status": found["status"], "why": found["why"],
             "candidates": [{"case": c, "name": directory.names().get(c) or c} for c in found.get("candidates") or []], "queued_at": _now()}
    _write(inbox / QUEUE, queue + [entry])
    _log(inbox, {"event": "queued", "id": entry["id"], "what": entry["what"], "why": entry["why"], "read": r})
    return entry


def _retry(clients_root: Path, inbox: Path, directory: Directory, db_path: Path | None, state_path: Path | None) -> list[dict[str, Any]]:
    """The notices still waiting, matched again: the case may have been processed since (a new client's first run)."""
    from classify import extract_pages

    routed = []
    for entry in list(_queue(inbox)):
        found = match(entry["read"], directory)
        if found["status"] != "routed":
            continue
        path = inbox / WAITING / entry["file"]
        if not path.exists():
            continue
        try:
            text = "\n".join(extract_pages(path))
            done = route(clients_root, inbox, found["case"], path.read_bytes(), text, entry["read"], path.name, found["how"],
                         db_path=db_path, state_path=state_path)
        except (LookupError, ValueError):
            continue
        path.unlink()  # moved: its copy is in the case's folder
        _write(inbox / QUEUE, [q for q in _queue(inbox) if q["id"] != entry["id"]])
        routed.append(done)
    return routed


def process_inbox(clients_root: str | Path, inbox: str | Path | None = None, *, db_path: str | Path | None = None,
                  state_path: str | Path | None = None, today: date | None = None, progress=None) -> dict[str, Any]:
    """Reads every file in the inbox folder: each notice routed to its case or put in the queue (module docstring).
    Returns {"files", "routed": [log entries], "queued": [queue entries], "waiting": every notice waiting, "errors"}.
    progress(step, steps, words): the job worker's report of how far it is (src/jobs.py), one step for each file."""
    clients_root = Path(clients_root)
    inbox = Path(inbox) if inbox else default_path(clients_root)
    db_path = Path(db_path) if db_path else None
    state_path = Path(state_path) if state_path else None
    today = today or clock.today()
    _setup(inbox)
    out: dict[str, Any] = {"files": 0, "routed": [], "queued": [], "errors": []}
    with _Lock(inbox):
        directory = Directory(clients_root, db_path)
        out["routed"] += _retry(clients_root, inbox, directory, db_path, state_path)
        todo = sorted(p for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES)
        for n, path in enumerate(todo, 1):
            if progress:
                progress(n, len(todo), f"Reading notice {n} of {len(todo)}")
            out["files"] += 1
            try:
                data = _pdf_bytes(path)
                pages = _pages_of(data)
            except Exception as exc:  # noqa: BLE001 -- an unreadable file stays where it is, named in the report
                out["errors"].append({"file": path.name, "error": type(exc).__name__})
                continue
            found_parts = parts(pages) or [(0, max(0, len(pages) - 1), "unclassified")]
            whole = len(found_parts) == 1 and path.suffix.lower() == ".pdf"
            stem = f"inbox_{today.isoformat()}_{_safe(path.name)}"
            for n, (first, last, doc_type) in enumerate(found_parts, 1):
                part = data if whole else _subset(data, first, last)
                text = "\n".join(pages[first:last + 1])
                r = read(text, doc_type)
                found = match(r, directory)
                name = f"{stem}.pdf" if len(found_parts) == 1 else f"{stem}_part{n}.pdf"
                if found["status"] == "routed":
                    try:
                        out["routed"].append(route(clients_root, inbox, found["case"], part, text, r, name, found["how"], db_path=db_path, state_path=state_path))
                        continue
                    except (LookupError, ValueError) as exc:
                        found = {"status": "unmatched", "why": str(exc), "candidates": [found["case"]]}
                out["queued"].append(_wait(inbox, part, r, found, path.name, list(range(first + 1, last + 2)), len(pages), name, directory))
            if whole:
                path.unlink()  # moved: to the case's folder, or to waiting/
            else:  # split or converted: the original is kept beside the inbox, never deleted
                os.replace(path, _unique(inbox / ORIGINALS, f"{today.isoformat()}_{path.name}"))
    out["waiting"] = len(_queue(inbox))
    return out


def report_line(result: dict[str, Any]) -> str:
    """The morning report's line."""
    routed, waiting = len(result.get("routed") or []), result.get("waiting", 0)
    line = f"Notice inbox: {routed} notice{'' if routed == 1 else 's'} routed, {waiting} waiting for a person."
    if result.get("errors"):  # no file names here: the toast and the morning report are read by everyone (the files stay in the folder)
        n = len(result["errors"])
        line += f" {n} file{' ' if n == 1 else 's '}couldn't be read and stay{'s' if n == 1 else ''} in the inbox."
    return line


# -- the reviewer's side: the queue, placing a notice, "not ours" -------------------------------------------


def _take(inbox: Path, entry_id: str) -> dict[str, Any]:
    entry = next((q for q in _queue(inbox) if q["id"] == entry_id), None)
    if entry is None:
        raise LookupError("That notice isn't waiting any more (placed by someone else, or read again).")
    return entry


def place(clients_root: str | Path, inbox: str | Path, entry_id: str, case: str, who: str, *, db_path: str | Path | None = None,
          state_path: str | Path | None = None) -> dict[str, Any]:
    """A reviewer's choice: the waiting notice belongs to `case`. Recorded with who and when; the case is brought up to date."""
    from classify import extract_pages

    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    clients_root, inbox = Path(clients_root), Path(inbox)
    with _Lock(inbox):
        entry = _take(inbox, entry_id)
        if not (clients_root / case / "fact_graph.json").exists():
            raise ValueError("Choose the case from the list.")
        path = inbox / WAITING / entry["file"]
        text = "\n".join(extract_pages(path))
        done = route(clients_root, inbox, case, path.read_bytes(), text, entry["read"], path.name, f"placed by {who}", who=who,
                     db_path=Path(db_path) if db_path else None, state_path=Path(state_path) if state_path else None)
        path.unlink()
        _write(inbox / QUEUE, [q for q in _queue(inbox) if q["id"] != entry_id])
    return done


def not_ours(inbox: str | Path, entry_id: str, note: str, who: str) -> dict[str, Any]:
    """Not the firm's mail (a former client, another firm's client): set aside in not_ours/ with the reviewer's note."""
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    note = re.sub(r"\s+", " ", note or "").strip()
    if not note:
        raise ValueError("Say why it isn't ours (e.g. 'a former client', 'another firm's client').")
    inbox = Path(inbox)
    with _Lock(inbox):
        entry = _take(inbox, entry_id)
        target = _unique(inbox / NOT_OURS, entry["file"])
        os.replace(inbox / WAITING / entry["file"], target)
        record = {"file": target.name, "note": note, "by": who, "at": _now(), "read": entry["read"], "what": entry["what"], "original": entry["original"]}
        (inbox / NOT_OURS / f"{target.stem}.json").write_text(json.dumps(record, indent=1, ensure_ascii=False), encoding="utf-8")
        _write(inbox / QUEUE, [q for q in _queue(inbox) if q["id"] != entry_id])
        _log(inbox, {"event": "not_ours", "id": entry_id, "by": who, "note": note, "what": entry["what"], "read": entry["read"]})
    return record


def waiting_file(inbox: str | Path, entry_id: str) -> Path:
    """The waiting notice's scan, for the reviewer to look at before placing it."""
    inbox = Path(inbox)
    return inbox / WAITING / _take(inbox, entry_id)["file"]


def view(clients_root: str | Path, inbox: str | Path | None = None, days: int = 30) -> dict[str, Any]:
    """The Inbox page: what waits for a person, and what was routed, placed or set aside lately (newest first)."""
    clients_root = Path(clients_root)
    inbox = Path(inbox) if inbox else default_path(clients_root)
    queue = _queue(inbox) if inbox.exists() else []
    recent = []
    if (inbox / LOG).exists():
        cutoff = time.time() - days * 86400
        for line in (inbox / LOG).read_text(encoding="utf-8").splitlines():
            try:
                e = json.loads(line)
            except ValueError:
                continue
            if e.get("event") in ("routed", "placed", "not_ours") and (clock.parse(e["at"]) or clock.now()).timestamp() >= cutoff:
                recent.append({k: e.get(k) for k in ("at", "event", "case", "name", "by", "how", "what", "note", "rfe_key", "hearing", "processed")}
                              | {"kind": (e.get("read") or {}).get("kind"), "court": bool((e.get("read") or {}).get("court"))})
    waiting = [{k: q.get(k) for k in ("id", "scanned", "pages", "of", "what", "status", "why", "candidates", "queued_at")}
               | {"names": (q.get("read") or {}).get("names") or [], "a_number": (q.get("read") or {}).get("a_number"),
                  "form": (q.get("read") or {}).get("form")} for q in queue]  # the form: a protected one waits for an attorney (src/restricted.py)
    return {"waiting": waiting, "recent": list(reversed(recent))[:60], "counts": {"waiting": len(waiting)},
            "files_unread": sum(1 for p in inbox.iterdir() if p.is_file() and p.suffix.lower() in SUFFIXES) if inbox.exists() else 0}


def counts(clients_root: str | Path, inbox: str | Path | None = None) -> dict[str, int]:
    """For My work: notices waiting for a person, and files not read yet."""
    v = view(clients_root, inbox, days=1)
    return {"waiting": v["counts"]["waiting"], "unread": v["files_unread"]}
