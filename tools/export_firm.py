"""Hand a firm its data back: one client, or the whole installation, as one dated zip.

    python tools/export_firm.py --client <client id> [--out <folder or .zip>]
    python tools/export_firm.py --all [--out <folder or .zip>]
    python tools/export_firm.py --pack [--out <folder or .zip>]
    python tools/export_firm.py --everything [--out <folder or .zip>] [--by <name>]

What goes in (docs/security/exit_procedure.md says the same in plain words):

  clients/<id>/   the client's folder as it is: documents the pipeline read, fact graphs, review decisions, packets
  portal/<id>/    the portal's data for that client: profile, answers, uploads, requests, the portal log
  documents/<id>/ the case's source folder, when it lives outside the two above (meta.json says where)
  firm/           (--all, or --firm-files) the firm's settings, the upkeep log, the staff accounts WITHOUT password
                  hashes or session tokens, the staff access log, the learning store
  manifest.json and MANIFEST.md   every file with its size, sha256, format and one line saying what it is

What stays out, on purpose: password hashes and session tokens (the accounts file is rewritten with a whitelist of
fields), the portal's sign-in table (auth.json: hashed links and sessions), the portal's outbox (it holds working
sign-in links), deployment.json (it can hold the provider's keys), and the product itself (src/, schemas/).

Read only: nothing is moved, changed or deleted, and the zip is refused inside any folder it reads from or that
holds what it reads (the data and portal folders, their parents, and the folder of the accounts file).
Two limits to know. Opening a SQLite database read-only in WAL mode can still create -wal and -shm files beside
it (the learning store, if it is in WAL mode and the app is running); they are SQLite's own bookkeeping, not
data. And a case's source folder that another client's meta.json also names is NOT exported with --client (it
would carry that other client's files out); it is listed under "Please read" in MANIFEST.md, and --all
includes it.
Same folder arguments as src/review/server.py (--data, --portal, --users).

--everything is the exit made real (docs/security/exit_procedure.md): every case's records, the ledger, the staff accounts without secrets, the firm's settings
and policies, and docs/data_dictionary.md, as one dated zip with a manifest (path, size, sha256) and a README in plain words. What goes in is decided by
the data dictionary: src/records.py lists every file a case, the portal and the firm keep, and a file is in the export if and only if it is listed there
(see everything() below). The review app's "Export the firm's data" (Settings, Keeping current) runs this same code and writes under data/exports/.

--pack is a different export: the security answer pack a firm asks for before it signs (docs/security/), as one zip. The
product data statement in it is filled in with the provider's name and security contact from deployment.json
(provider.name, provider.email, provider.phone); the document in the repository keeps its [bracketed] placeholders. It
refuses to write the pack while any placeholder is left in the statement, and lists which. Nothing of any client is in it.
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import tempfile
import zipfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any, Iterator, NamedTuple

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))

SAFE_USER_FIELDS = ("name", "role", "active", "created_at")  # a whitelist: nothing else about an account leaves
FORMATS = {".json": "JSON", ".jsonl": "JSON Lines (one JSON record per line)", ".pdf": "PDF", ".jpg": "JPEG", ".jpeg": "JPEG", ".png": "PNG",
           ".txt": "plain text", ".md": "Markdown", ".csv": "CSV", ".db": "SQLite database", ".zip": "ZIP", ".tiff": "TIFF", ".tif": "TIFF"}
NOT_INCLUDED = [
    "Passwords, password hashes and session tokens of staff and of clients.",
    "The portal's sign-in table (links and sessions, stored only as hashes).",
    "The portal's outbox of unsent messages (it can hold working sign-in links).",
    "deployment.json (it can hold the provider's keys), and every key or secret kept in the server's environment.",
    "The product itself (code, form templates, rules); the firm already holds or can be sent its own copy.",
    "Backups and copies of the data kept elsewhere; the export reads only the live folders named on the command line.",
]

# What each well-known file is, for MANIFEST.md. The first match wins; anything else gets a line from its folder and format.
CLIENT_FILES = [
    ("fact_graph_raw.json", "Every fact as first read from the client's documents and answers, each with the document it came from"),
    ("fact_graph_reviewed.json", "The facts after staff decisions: what the forms are filled from"),
    ("fact_graph.json", "The facts after the system's rules were applied, with sources and confidence"),
    ("decisions.json", "Every review decision: who decided, their role, the time and the note"),
    ("evidence.json", "Where on each document each fact was found"),
    ("meta.json", "Which documents were read, how each was classified, and when the case was last processed"),
    ("reading_flags.json", "Places the document reader was unsure of and sent to a person"),
    ("flag_report.txt", "The issues found for staff, as plain text"),
    ("i485_filled.pdf", "The filled Form I-485 (a draft until an attorney signs off)"),
    ("packet.pdf", "The assembled filing packet, as built for mailing"),
    ("packet_choices.json", "Staff choices on where documents go in the packet"),
    ("companions.json", "Which companion forms were filled, and when"),
    ("status.json", "Case notes staff entered (dates and facts the documents do not show)"),
    ("case_status.json", "USCIS case status answers saved by the nightly check"),
    ("online_filing.json", "How each filing is submitted (paper or online): who chose and when"),
    ("payment.json", "How each filing is paid"),
    ("journey.json", "The case's stage in plain language"),
]
PORTAL_FILES = [
    ("profile.json", "The client's contact details, language and consent for each message channel"),
    ("answers.json", "The client's questionnaire answers as given in the portal"),
    ("uploads.json", "List of the client's uploads: size, checksum and time"),
    ("tasks.json", "What the client still has to answer or upload"),
    ("requests.json", "Questions and requests the office sent the client, and the replies"),
    ("messages.json", "The client's messages to the office and the office's answers, with who wrote them and when"),
    ("journey.json", "The case's stage in plain language, as the client sees it"),
    ("events.jsonl", "The portal's log for this client: sign-ins, answers saved, uploads, requests"),
]


STATEMENT = "product_data_statement.md"
# the provider's own decision, marked in the documents as [decided by the provider: ...]
PLACEHOLDER = re.compile(r"\[decided by the provider:[^\]]*\]")
# the statement's one line for the provider, as shipped: the bracketed placeholder and the sentence under it
STATEMENT_LINE = re.compile(r"`\[decided by the provider: the provider's legal name[^\]]*\]`\nThe provider fills this line in[^\n]*\n")


class ExportError(Exception):
    """A refusal or a problem the person running the tool should read (no traceback)."""


def provider_details() -> dict[str, str]:
    """The provider's name, email and phone from deployment.json (src/deployment.py); what is missing is left empty."""
    import deployment

    p = deployment.load()["provider"]
    name = str(p.get("name") or "").strip()
    return {"name": "" if name == deployment.DEFAULT["provider"]["name"] else name, "email": str(p.get("email") or "").strip(),
            "phone": str(p.get("phone") or "").strip()}


def fill_statement(text: str, provider: dict[str, str]) -> str:
    """The product data statement with the provider's name and security contact where the placeholder is. Raises ExportError, listing
    what is missing, when the provider's details are not filled in, or a placeholder is left (or has no place to go)."""
    missing = []
    if not provider["name"]:
        missing.append("the provider's name (provider.name in deployment.json)")
    if not provider["email"]:
        missing.append("the security contact's email (provider.email in deployment.json)")
    if missing:
        raise ExportError("the statement needs " + " and ".join(missing) + ". Fill those in and run this again.")
    contact = provider["email"] + (f", {provider['phone']}" if provider["phone"] else "")
    line = f"Provider: {provider['name']}. Security contact: {contact}.\n"
    if not STATEMENT_LINE.search(text):
        raise ExportError("the statement has no place for the provider's name any more: its line for the provider is missing from "
                          "docs/security/product_data_statement.md.")
    filled = STATEMENT_LINE.sub(lambda _m: line, text, count=1)
    left = PLACEHOLDER.findall(filled)
    if left:
        raise ExportError("placeholders are still in the statement: " + "; ".join(left))
    return filled


def build_pack(out: Path | None) -> tuple[Path, list[str]]:
    """Writes the security answer pack (docs/security/*.md) as a dated zip, the statement filled in. Returns (the zip, notes)."""
    folder = REPO / "docs" / "security"
    docs = sorted(folder.glob("*.md"))
    if not any(d.name == STATEMENT for d in docs):
        raise ExportError("docs/security/product_data_statement.md is not here.")
    target = (out or Path.cwd())
    target = target if target.suffix.lower() == ".zip" else target / f"i485-security-pack-{date.today().isoformat()}.zip"
    target = target.resolve()
    texts = {d.name: d.read_text(encoding="utf-8") for d in docs}
    texts[STATEMENT] = fill_statement(texts[STATEMENT], provider_details())  # refuses before anything is written
    if target.exists():
        raise ExportError(f"{target} already exists; nothing was written. Choose another --out.")
    target.parent.mkdir(parents=True, exist_ok=True)
    part = target.with_name(target.name + ".part")
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for name, text in texts.items():
                zf.writestr(f"security-pack/{name}", text)
        os.replace(part, target)
    finally:
        part.unlink(missing_ok=True)
    open_items = {n: len(PLACEHOLDER.findall(t)) for n, t in texts.items() if n != STATEMENT and PLACEHOLDER.search(t)}
    notes = [f"{n}: {c} decision(s) still marked [decided by the provider]" for n, c in sorted(open_items.items())]
    return target, notes


EXAMPLE_WHAT = "A labelled example: a value a reader read from a document, and the value the office confirmed or corrected (the firm's reader examples)"


def kind_of(path: str) -> str:
    return FORMATS.get(Path(path).suffix.lower(), "file")


def describe(area: str, rel: str) -> str:
    """One line for the manifest: area is clients, portal, documents or firm; rel is the path inside the client's folder."""
    name = Path(rel).name
    if area == "documents":
        return f"A document from the case's source folder ({kind_of(rel)})"
    if area == "portal" and rel.startswith("uploads/"):
        return "A document the client uploaded in the portal (converted to PDF)"
    table = CLIENT_FILES if area == "clients" else PORTAL_FILES
    for known, text in table:
        if name == known:
            return text
    if area == "clients":
        if re.fullmatch(r"packet_.+\.json", name):
            return "The packet's contents list (forms, exhibits, checklist) for one filing"
        if name.endswith("_filled.pdf"):
            return "A filled USCIS form (a draft until signed off)"
        if name.endswith("_response.pdf"):
            return "A prepared response for the file (a draft until signed off)"
        if re.fullmatch(r"online_bundle_.+\.(zip|json)", name):
            return "Files prepared for USCIS's online filing"
        if rel.startswith(("source/", "uploads/")):
            return "A document the pipeline read (" + kind_of(rel) + ")"
    return f"A file in the client's folder ({kind_of(rel)})" if area in ("clients", "portal") else f"A file ({kind_of(rel)})"


class Entry:
    """One file going into the zip: a path on disk, or bytes made here (accounts file, database copy)."""

    def __init__(self, arcname: str, what: str, source: Path | None = None, data: bytes | None = None):
        self.arcname, self.what, self.source, self.data = arcname, what, source, data


def walk(folder: Path, skipped: list[str]) -> Iterator[tuple[Path, str]]:
    """Every regular file under folder with its path relative to it. Symbolic links and half-written temp files are
    reported, never followed: a link could point anywhere, and a .tmp file is a write in progress."""
    for path in sorted(folder.rglob("*")):
        rel = path.relative_to(folder).as_posix()
        if path.is_symlink():
            skipped.append(f"{rel}: a link, not followed")
        elif path.is_file():
            if path.suffix == ".tmp":
                skipped.append(f"{rel}: a write in progress, left out")
            else:
                yield path, rel


def safe_accounts(users_path: Path) -> bytes:
    """The staff accounts file with only the whitelisted fields: no salt, hash, failure count, lock time or sessions."""
    data = json.loads(users_path.read_text(encoding="utf-8"))
    users = {email: {k: u[k] for k in SAFE_USER_FIELDS if k in u} for email, u in sorted((data.get("users") or {}).items())}
    return (json.dumps({"users": users}, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def copy_sqlite(path: Path) -> bytes:
    """A consistent copy of a SQLite file that may be open in the running app (SQLite's own backup, not a file copy)."""
    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / "copy.db"
        src = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
        try:
            dst = sqlite3.connect(target)
            try:
                src.backup(dst)
            finally:
                dst.close()
        finally:
            src.close()
        return target.read_bytes()


def check_client_name(client: str) -> str:
    if not client or client in (".", "..") or "/" in client or "\\" in client or client != client.strip():
        raise ExportError(f"'{client}' is not a client id.")
    return client


def inside(path: Path, folder: Path) -> bool:
    return path == folder or folder in path.parents


def shared_source(data: Path, cid: str, where: Path) -> bool:
    """True when another client's meta.json names a source folder that is, contains or sits inside this one."""
    for other in sorted(p for p in data.iterdir() if p.is_dir() and p.name != cid) if data.is_dir() else []:
        try:
            theirs = Path(json.loads((other / "meta.json").read_text(encoding="utf-8")).get("source_folder") or "")
            theirs = theirs.resolve() if theirs.as_posix() not in ("", ".") else None
        except (OSError, ValueError):
            continue
        if theirs is not None and (inside(theirs, where) or inside(where, theirs)):
            return True
    return False


def gather(args: argparse.Namespace) -> tuple[list[Entry], list[str], list[str], list[Path]]:
    """(entries, skipped, warnings, the folders and files read). Nothing is written here."""
    data, portal = args.data.resolve(), args.portal.resolve()
    skipped: list[str] = []
    warnings: list[str] = []
    entries: list[Entry] = []
    read: list[Path] = []

    if args.all:
        ids = {p.name for folder in (data, portal / "clients") if folder.is_dir() for p in folder.iterdir() if p.is_dir()}
        if not ids:
            raise ExportError(f"Nothing to export: no client folders in {data} or {portal / 'clients'}.")
    else:
        ids = {check_client_name(args.client)}
        if not ((data / args.client).is_dir() or (portal / "clients" / args.client).is_dir()):
            raise ExportError(f"No client '{args.client}' in {data} or {portal / 'clients'}.")

    for cid in sorted(ids):
        folders = {"clients": data / cid, "portal": portal / "clients" / cid}
        for area, folder in folders.items():
            if folder.is_dir() and not folder.is_symlink():
                read.append(folder)
                for path, rel in walk(folder, skipped):
                    entries.append(Entry(f"{area}/{cid}/{rel}", describe(area, rel), source=path))
            elif folder.is_symlink():
                skipped.append(f"{area}/{cid}: a link, not followed")
        # the case's labelled examples of what the readers read (src/reader_examples.py): case data, so they go with the case
        import reader_examples

        examples = reader_examples.case_folder(data / cid)
        if examples.is_dir() and not examples.is_symlink():
            read.append(examples)
            for path, rel in walk(examples, skipped):
                entries.append(Entry(f"reader_examples/{cid}/{rel}", EXAMPLE_WHAT, source=path))
        # the case's source folder, when meta.json points outside the two folders above
        meta_path = folders["clients"] / "meta.json"
        if meta_path.is_file():
            try:
                where = Path(json.loads(meta_path.read_text(encoding="utf-8")).get("source_folder") or "")
            except (OSError, ValueError):
                where = Path("")
            if where.as_posix() not in ("", "."):
                try:
                    where = where.resolve()
                except OSError:
                    where = Path("")
            if where.as_posix() in ("", ".") or not where.is_dir():
                if where.as_posix() not in ("", "."):
                    warnings.append(f"{cid}: the case's source folder is not on this machine ({where}); only what is in the client's own folders is included.")
            elif any(inside(where, f.resolve()) for f in folders.values()):
                pass  # already exported with the client's own folders
            elif inside(data, where) or inside(portal, where):
                warnings.append(f"{cid}: the case's source folder ({where}) contains the data folders; left out.")
            elif not args.all and shared_source(data, cid, where):
                warnings.append(f"{cid}: the case's source folder ({where}) is also named by another client's record; left out of this export so that client's files do not leave with it. Use --all, or copy it by hand after checking whose files are in it.")
            else:
                read.append(where)
                for path, rel in walk(where, skipped):
                    entries.append(Entry(f"documents/{cid}/{rel}", describe("documents", rel), source=path))

    if args.all or args.firm_files:
        for label, path, what in (
            ("settings.json", args.settings, "The firm's settings: fees, Visa Bulletin month, poverty guidelines, the firm's details"),
            ("maintenance_log.json", args.maintenance_log, "Who checked which upkeep item, and when"),
            (args.users.stem + "_access.jsonl", args.users.with_name(args.users.stem + "_access.jsonl"),
             "Staff access log: sign-ins, failures, sign-outs, password changes, account changes (no passwords)"),
        ):
            if path.is_file():
                read.append(path.resolve())
                entries.append(Entry(f"firm/{label}", what, source=path))
        if args.users.is_file():
            read.append(args.users.resolve())
            entries.append(Entry("firm/users.json", "Staff accounts: email, name, role, active or not. Password hashes and session tokens are NOT included", data=safe_accounts(args.users)))
        if args.learning.is_file():
            read.append(args.learning.resolve())
            try:
                entries.append(Entry("firm/learning.db", "The learning store: corrections staff approved, as a SQLite database", data=copy_sqlite(args.learning)))
            except sqlite3.Error as exc:
                warnings.append(f"The learning store could not be copied ({exc}); close the app and copy that file by hand.")
    return entries, skipped, warnings, read


def write_zip(entries: list[Entry], zip_path: Path, scope: str, skipped: list[str], warnings: list[str], not_included: list[str] | None = None,
              progress=None) -> tuple[int, str]:
    """Writes the zip (to a .part file, then renamed). Returns (the number of files including the two manifests, the zip's sha256).
    progress(done, total): called after each file (the review app's screen shows it)."""
    files: list[dict[str, Any]] = []
    fd, name = tempfile.mkstemp(dir=zip_path.parent, prefix=zip_path.name + ".", suffix=".part")  # its own temp file (two exports at once never share one), owner-only
    os.close(fd)
    part = Path(name)
    try:
        with zipfile.ZipFile(part, "w", zipfile.ZIP_DEFLATED) as zf:
            for number, e in enumerate(sorted(entries, key=lambda x: x.arcname), start=1):
                if progress is not None:
                    progress(number - 1, len(entries))
                digest, size = hashlib.sha256(), 0
                if e.source is not None:
                    st = e.source.stat()
                    stamp = datetime.fromtimestamp(st.st_mtime).timetuple()[:6]
                    info = zipfile.ZipInfo(e.arcname, date_time=stamp if stamp[0] >= 1980 else (1980, 1, 1, 0, 0, 0))
                    info.compress_type = zipfile.ZIP_DEFLATED
                    with open(e.source, "rb") as src, zf.open(info, "w", force_zip64=True) as out:
                        for chunk in iter(lambda: src.read(1 << 20), b""):
                            digest.update(chunk)
                            size += len(chunk)
                            out.write(chunk)
                else:
                    assert e.data is not None
                    digest.update(e.data)
                    size = len(e.data)
                    info = zipfile.ZipInfo(e.arcname, date_time=datetime.now().timetuple()[:6])
                    info.compress_type = zipfile.ZIP_DEFLATED
                    zf.writestr(info, e.data)
                files.append({"path": e.arcname, "bytes": size, "sha256": digest.hexdigest(), "format": kind_of(e.arcname), "what": e.what})
            made = datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")
            try:
                from version import VERSION as version
            except Exception:  # noqa: BLE001 -- the version is a courtesy, never a reason to stop an export
                version = "unknown"
            manifest = {"made": made, "scope": scope, "product_version": version, "file_count": len(files), "files": files,
                        "not_included": not_included if not_included is not None else NOT_INCLUDED, "left_out": skipped, "warnings": warnings,
                        "note": "manifest.json and MANIFEST.md describe every other file; they do not list themselves."}
            zf.writestr("manifest.json", json.dumps(manifest, indent=2, ensure_ascii=False) + "\n")
            zf.writestr("MANIFEST.md", manifest_md(manifest))
        os.replace(part, zip_path)
    finally:
        part.unlink(missing_ok=True)
    sha = hashlib.sha256()
    with open(zip_path, "rb") as f:
        for chunk in iter(lambda: f.read(1 << 20), b""):
            sha.update(chunk)
    return len(files) + 2, sha.hexdigest()


def manifest_md(m: dict[str, Any]) -> str:
    def cell(text: str) -> str:
        return str(text).replace("|", "/").replace("\n", " ")

    lines = ["# What is in this export", "",
             f"Made {m['made']} (UTC). Scope: {m['scope']}. {m['file_count']} files, listed below with their format and what each is.",
             "Each file's size and sha256 are in manifest.json, so a copy can be checked against it.", "",
             "| File | What it is | Format |", "|---|---|---|"]
    lines += [f"| {cell(f['path'])} | {cell(f['what'])} | {f['format']} |" for f in m["files"]]
    lines += ["", "## Not included, on purpose", ""] + [f"- {t}" for t in m["not_included"]]
    if m["left_out"]:
        lines += ["", "## Left out while reading", ""] + [f"- {cell(t)}" for t in m["left_out"]]
    if m["warnings"]:
        lines += ["", "## Please read", ""] + [f"- {cell(t)}" for t in m["warnings"]]
    return "\n".join(lines) + "\n"


# -- --everything: the firm's data, as the data dictionary lists it --------------------------------------------------------------------------------------------

EXPORT_FOLDER = "exports"  # data/exports/: where the review app and --everything write by default
README = """\
This is a copy of everything the firm's case system keeps. It was made on {date} by {who}.

You do not need our software to read any of it. Every file is in an ordinary format that other programs open: JSON (plain text, one record),
JSON Lines (plain text, one record a line), PDF, JPEG, PNG, CSV, and SQLite (a database file any SQLite tool opens). docs/data_dictionary.md,
copied here as data_dictionary.md, says what every kind of record is, where it lives, what each field means, the record's version, and which
fields hold a person's data.

What is in this zip

  index.html           START HERE. Open it in any browser: every case with the client's name and kind of case, each case's records (with their version) and
                       documents as links, the facts read from the documents, the firm's own records, the ledger, and what is not in this zip and why.
                       It needs no software and no scripts to read.
  README.txt           this page
  data_dictionary.md   every kind of record: where it lives, who writes it, each field and what it means, its version, where a person's data is
  manifest.json        every file in the zip with its size in bytes and its sha256, so a copy can be checked against it (MANIFEST.md says the same in words)
  MANIFEST.md          the same list for a person to read: each file and what it is
  cases/<case id>/     one folder for each case: the facts read from its documents (fact graphs, with the source of every value), the record of each
                       document, every review decision with who made it and when, where the case stands, who may open it, the filled forms and
                       the packets as they were built, and the case's original documents in cases/<case id>/source, uploads or documents
  portal/<client id>/  what each client gave through the portal: their details and consent, their answers, the documents they uploaded, the
                       office's requests and messages, and the portal's own log
  firm/                the firm's settings, its policies and the approvals of its rules, the upkeep log, the staff accounts (name, role, whether
                       active: no passwords, no codes, no keys), the learning store, the overnight run's records, the notice inbox (the scanned notices
                       waiting to be placed), the Clio connection's state without its secrets, and the hand-filled references
  logs/                the staff access log (sign-ins, account changes) and the log of who viewed what
  ledger/              the event ledger: who changed what on which case, and when, one file a month (events-YYYY-MM.jsonl) and the same month as a table
                       (events-YYYY-MM.html), its daily seals (ledger_anchors.jsonl) and the purges that blanked a case's rows (ledger_redactions.jsonl)

Restricted cases (VAWA, T, U and asylum cases, and any case an attorney restricted) are in this zip. They are the firm's data. Whoever holds the
zip can read them: keep it on encrypted storage and send it only over an encrypted channel. The zip itself is not encrypted.

What is not in this zip, on purpose

{left_out}

To check a file against the manifest, compute its sha256 (for example `sha256sum <file>`) and compare it with the manifest's.
"""


class Where(NamedTuple):
    """Where each of the firm's files is (the review app's own paths, or the defaults beside the data folder)."""

    clients: Path
    portal: Path
    users: Path
    settings: Path
    maintenance_log: Path
    learning: Path
    policies: Path
    rules: Path
    views: Path
    ledger: Path
    exports: Path


def default_where(clients: Path, portal: Path, users: Path, settings: Path | None = None, maintenance_log: Path | None = None, learning: Path | None = None) -> Where:
    """The firm's files, as the review app finds them: the environment's paths where it names one, else beside the data folder (the clients folder's parent)."""
    import events

    firm = clients.resolve().parent
    return Where(
        clients=clients, portal=portal, users=users,
        settings=settings or Path(os.environ.get("I485_SETTINGS") or firm / "settings.json"),
        maintenance_log=maintenance_log or Path(os.environ.get("I485_MAINTENANCE_LOG") or firm / "maintenance_log.json"),
        learning=learning or firm / "learning.db",
        policies=Path(os.environ.get("I485_POLICIES_FIRM") or firm / "policies_firm.json"),
        rules=Path(os.environ.get("I485_RULES_APPROVED") or firm / "rules_approved.json"),
        views=users.with_name("review_views.jsonl"), ledger=events.base_path(firm), exports=firm / EXPORT_FOLDER)


def where_from(args: argparse.Namespace) -> Where:
    return default_where(args.data, args.portal, args.users, args.settings, args.maintenance_log, args.learning)


def listed(rel: str, patterns: list[str]) -> bool:
    """rel (a path inside a case's or a client's folder, with /) is one of the dictionary's files: a pattern matches a path of the same depth, name by
    name, so "*.pdf" is a PDF in the folder itself and "source/*" is a file directly in source/."""
    import fnmatch

    parts = rel.split("/")
    return any(len(p.split("/")) == len(parts) and all(fnmatch.fnmatchcase(a, b) for a, b in zip(parts, p.split("/"))) for p in patterns)


def everything_entries(w: Where, dictionary: Path) -> tuple[list[Entry], list[str], list[str]]:
    """(entries, left out while reading, warnings): every file src/records.py lists, and nothing else. Nothing is written here."""
    import events
    import records

    skipped: list[str] = []
    warnings: list[str] = []
    entries: list[Entry] = []
    case_patterns, portal_patterns = records.patterns("case"), records.patterns("portal")
    clients, portal = w.clients.resolve(), w.portal.resolve()
    cases = sorted(p.name for p in clients.iterdir() if p.is_dir() and not p.is_symlink()) if clients.is_dir() else []
    for cid in cases:
        folder = clients / cid
        for path, rel in walk(folder, skipped):
            if listed(rel, case_patterns):
                entries.append(Entry(f"cases/{cid}/{rel}", records.describe("case", rel), source=path))
        meta = folder / "meta.json"
        where = None
        if meta.is_file():
            try:
                named = json.loads(meta.read_text(encoding="utf-8")).get("source_folder") or ""
                where = Path(named).resolve() if named else None
            except (OSError, ValueError):
                where = None
        if where is not None and where.is_dir() and not inside(where, folder) and not inside(where, portal) and not inside(where, clients) and not inside(clients, where):
            for path, rel in walk(where, skipped):  # the case's original documents kept outside the data folder
                if path.suffix.lower() in (".pdf", ".jpg", ".jpeg", ".png", ".tif", ".tiff"):
                    entries.append(Entry(f"cases/{cid}/documents/{rel}", "A document from the case's source folder (" + kind_of(rel) + ")", source=path))
        elif where is not None and not where.is_dir() and not inside(where, portal):
            warnings.append(f"{cid}: the case's source folder is not on this machine; only the documents inside the case's own folders and the portal's are included.")
    for client in sorted(p.name for p in portal.joinpath("clients").iterdir() if p.is_dir() and not p.is_symlink()) if portal.joinpath("clients").is_dir() else []:
        for path, rel in walk(portal / "clients" / client, skipped):
            if listed(rel, portal_patterns):
                entries.append(Entry(f"portal/{client}/{rel}", records.describe("portal", rel), source=path))
    for arc, path, what in (
        ("firm/settings.json", w.settings, "The firm's settings"), ("firm/policies_firm.json", w.policies, "The firm's edits to its policies"),
        ("firm/rules_approved.json", w.rules, "Every attorney approval of a rule or firm policy"), ("firm/maintenance_log.json", w.maintenance_log, "The upkeep log"),
        ("logs/" + w.users.stem + "_access.jsonl", w.users.with_name(w.users.stem + "_access.jsonl"), "The staff access log"),
        ("logs/review_views.jsonl", w.views, "The view log: who opened which case, scan or document"),
    ):
        if path.is_file():
            entries.append(Entry(arc, what, source=path))
    if w.users.is_file():
        entries.append(Entry("firm/review_users.json", "Staff accounts: name, role, active or not and the date made. Passwords, codes, recovery codes and sessions are NOT included",
                             data=safe_accounts(w.users)))
    if w.learning.is_file():
        try:
            entries.append(Entry("firm/learning.db", "The learning store: corrections staff approved, as a SQLite database", data=copy_sqlite(w.learning)))
        except sqlite3.Error as exc:
            warnings.append(f"The learning store could not be copied ({exc}); close the app and copy that file by hand.")
    # the firm's other files the dictionary lists (the overnight run's records, the notice inbox, Getting started, the Clio connection's state, the references): found by
    # their patterns beside the data folder, so a file is in the export if and only if it is listed; one already added above (settings, the accounts) is skipped
    firm = clients.parent
    taken = {e.source.resolve() for e in entries if e.source is not None} | {w.users.resolve(), w.learning.resolve()}
    skip_folders = {"clients", "portal", EXPORT_FOLDER}
    candidates: list[tuple[Path, str]] = []
    if firm.is_dir():
        for child in sorted(firm.iterdir()):
            if child.is_symlink():
                skipped.append(f"{child.name}: a link, not followed")
            elif child.is_file():
                candidates.append((child, child.name))
            elif child.is_dir() and child.name not in skip_folders:
                candidates += [(p, f"{child.name}/{r}") for p, r in walk(child, skipped)]
        # Mirrors are in the configured document root, beside processed data.
        from purge import _documents_root
        from connectors.sync import _plain
        state = _documents_root(firm) / "sync_state.json"
        _plain(state)
        if state.is_file():
            candidates.append((state, "clients/sync_state.json"))
        elif (clients / "sync_state.json").is_file():
            # Preserve export of older installations' catalogued location;
            # never duplicate an actual configured mirror-state record.
            _plain(clients / "sync_state.json")
            candidates.append((clients / "sync_state.json", "clients/sync_state.json"))
    firm_patterns = records.patterns("firm")
    for path, rel in candidates:
        if listed(rel, firm_patterns) and path.resolve() not in taken:
            entries.append(Entry("firm/" + rel.removeprefix("clients/"), records.describe("firm", rel), source=path))
            taken.add(path.resolve())
    # a prospect's answers on the portal are the firm's data even when nobody opened the prospect (the copy in prospects/<id>/ is made when staff open it, src/prospects.py sync):
    # the portal's own file, which is the later of the two, goes in under the prospect's name
    import prospects

    held = portal / "prospects" / "clients"
    if held.is_dir():
        for one in sorted(p for p in held.iterdir() if p.is_dir() and not p.is_symlink()):
            # Internal prospect approvals/evidence belong in the firm's data
            # handoff, not the attorney-selected client release. Keep secrets,
            # attempts and temporary files excluded by the catalog/walker.
            for path, rel in walk(one, skipped):
                if rel in ("communication_consent.json", "requests.json") or rel.startswith(("consent-evidence/", "language-evidence/")):
                    if listed(rel, portal_patterns):
                        entries.append(Entry(f"firm/prospects/{one.name}/portal/{rel}", records.describe("portal", rel), source=path))
            answers = one / "answers.json"
            if answers.is_file() and not answers.is_symlink() and (prospects.folder(clients) / one.name).is_dir():
                arc = f"firm/prospects/{one.name}/answers.json"
                entries = [e for e in entries if e.arcname != arc]
                entries.append(Entry(arc, records.describe("firm", f"prospects/{one.name}/answers.json"), source=answers))
    for path in events.files(w.ledger):
        entries.append(Entry(f"ledger/{path.name}", "The event ledger for one month: who changed what, on which case, and when", source=path))
    import ledger_seal

    seals = ledger_seal.anchors_path(w.ledger)  # the daily seals: what shows the ledger was not changed afterward (the firm keeps them apart, printed)
    if seals.is_file() and not seals.is_symlink():
        entries.append(Entry(f"ledger/{seals.name}", "The ledger's daily seals: each finished day's last hash and row count (python tools/verify_ledger.py checks the ledger against them)", source=seals))
    blanks = events.redactions_path(w.ledger)  # the purges that blanked rows (src/purge.py): without it the check cannot account for a purged case's blanked rows
    if blanks.is_file() and not blanks.is_symlink():
        entries.append(Entry(f"ledger/{blanks.name}", "The purges that blanked a case's rows in the ledger: each purge's id, how many rows and a digest of their hashes", source=blanks))
    entries.append(Entry("data_dictionary.md", "The data dictionary: every record, its fields, its version and where a person's data is", source=dictionary))
    return entries, skipped, warnings


def claim(explicit: Path | None, folder: Path, stamp: str) -> Path:
    """The file this export will be, made empty and owner-only (0600) by one atomic step, so two exports at the same moment (the screen and a command, or two
    commands) never take the same name: the one that loses the race takes the next. The zip is written to a temp file of its own and renamed over it."""
    n = 1
    while True:
        target = explicit or (folder / (f"i485-firm-data-{stamp}.zip" if n == 1 else f"i485-firm-data-{stamp}-{n}.zip"))
        try:
            os.close(os.open(target, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600))
            return target
        except FileExistsError:
            if explicit is not None:
                raise ExportError(f"{target} already exists; nothing was written. Choose another --out.") from None
            n += 1


def everything(w: Where, out: Path | None = None, *, who: str, role: str | None = None, via: str = "staff", dictionary: Path | None = None, access_log=None,
               progress=None) -> dict[str, Any]:
    """Writes the whole export: every file the dictionary lists, the ledger, the dictionary, a manifest and a README, as one dated zip in `out` (default:
    data/exports/). The ledger and the access log say who exported, before the files are gathered (so the zip's own copies carry the row) and after.
    access_log(files): writes the access log's row (the review app passes its Accounts.log with the signed-in email; the command line uses the accounts file
    when there is one). Returns {path, name, files, bytes, sha256, warnings}. Raises ExportError, with nothing written, when it cannot."""
    import clock
    import events
    import records

    dictionary = dictionary or REPO / "docs" / "data_dictionary.md"
    if not dictionary.is_file():
        raise ExportError("docs/data_dictionary.md is not here: run python tools/data_dictionary.py first.")
    if not w.clients.is_dir() and not w.portal.is_dir():
        raise ExportError(f"Neither {w.clients} nor {w.portal} exists: nothing to export.")
    folder = (out if out is not None and out.suffix.lower() != ".zip" else out.parent if out is not None else w.exports).resolve()
    read_folders = [w.clients.resolve(), w.portal.resolve()]
    for r in read_folders:
        if inside(folder, r):
            raise ExportError(f"Refusing to write the export inside {r}: choose a folder outside the cases and the portal (--out).")
    stamp = clock.today().isoformat()
    folder.mkdir(parents=True, exist_ok=True)
    target = claim(out.resolve() if out is not None and out.suffix.lower() == ".zip" else None, folder, stamp)
    try:
        with events.acting(who, role, via):
            events.record("export", "started", "Started an export of the firm's data", home=w.ledger.parent, who=who, role=role, via=via)
            if access_log is not None:
                access_log(0)
            entries, skipped, warnings = everything_entries(w, dictionary)
            import export_reader
            import version

            for arc, page in export_reader.build(entries, date=stamp, who=who, version=version.VERSION, clients=w.clients, portal=w.portal).items():  # the export reads itself
                entries.append(Entry(arc, "The export's first page: every case, record and document as a link, the firm's records, the ledger and what is not here" if arc == "index.html"
                                     else "The ledger for one month as a table", data=page))
            left_out = "\n".join(f"  {what}: {why}" for what, why in records.NEVER_EXPORTED)
            entries.append(Entry("README.txt", "What this zip is, what each folder holds and what is not in it",
                                 data=README.format(date=stamp, who=who, left_out=left_out).encode("utf-8")))
            count, sha = write_zip(entries, target, "everything the firm keeps", skipped, warnings, [f"{what}: {why}" for what, why in records.NEVER_EXPORTED], progress)
            size = target.stat().st_size
            events.record("export", "exported", f"Exported the firm's data: {count} files", home=w.ledger.parent, who=who, role=role, via=via)
            if access_log is not None:
                access_log(count)
    except BaseException:
        if target.exists() and target.stat().st_size == 0:
            target.unlink(missing_ok=True)  # the name this export claimed and never filled: nothing half-made is left under it
        raise
    return {"path": str(target), "name": target.name, "files": count, "bytes": size, "sha256": sha, "warnings": warnings}


def verify_zip(path: Path) -> list[str]:
    """What is wrong with an export, from its own manifest: a file missing, one that is not on the list, a size or sha256 that does not match, the count. [] when it all matches
    (python tools/export_firm.py --verify <zip>: what IT does with an export before it relies on it)."""
    problems: list[str] = []
    with zipfile.ZipFile(path) as z:
        bad = z.testzip()
        if bad:
            problems.append(f"{bad}: damaged in the zip")
        try:
            manifest = json.loads(z.read("manifest.json").decode("utf-8"))
        except (KeyError, ValueError):
            return problems + ["manifest.json is missing or is not readable: nothing to check the files against"]
        listed = {f["path"]: f for f in manifest.get("files") or []}
        members = {n for n in z.namelist() if n not in ("manifest.json", "MANIFEST.md")}
        for name in sorted(members - set(listed)):
            problems.append(f"{name}: in the zip but not on the manifest")
        for name in sorted(set(listed) - members):
            problems.append(f"{name}: on the manifest but missing from the zip")
        for name in sorted(members & set(listed)):
            digest, size = hashlib.sha256(), 0
            with z.open(name) as f:
                for chunk in iter(lambda: f.read(1 << 20), b""):
                    digest.update(chunk)
                    size += len(chunk)
            if digest.hexdigest() != listed[name]["sha256"] or size != listed[name]["bytes"]:
                problems.append(f"{name}: does not match the manifest (damaged or changed)")
        if manifest.get("file_count") != len(listed):
            problems.append(f"the manifest says {manifest.get('file_count')} files and lists {len(listed)}")
    return problems


def zip_target(out: Path | None, scope: str) -> Path:
    name = f"i485-export-{scope}-{date.today().isoformat()}.zip"
    out = out or Path.cwd()
    if out.suffix.lower() == ".zip":
        return out
    return out / name


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Export one client, or the whole installation, to a dated zip (read only).")
    who = ap.add_mutually_exclusive_group(required=True)
    who.add_argument("--everything", action="store_true", help="everything the firm keeps, as the data dictionary lists it, with the ledger and the dictionary, as one zip")
    who.add_argument("--pack", action="store_true", help="the security answer pack (docs/security) as a zip, with the provider's name and security contact filled in")
    who.add_argument("--client", help="one client id (the folder name in the data folder)")
    who.add_argument("--all", action="store_true", help="every client, and the firm's own files")
    who.add_argument("--verify", type=Path, metavar="ZIP", help="check an export against its own manifest: every file's size and sha256, and that nothing is missing or extra")
    ap.add_argument("--out", type=Path, help="a folder (the zip is named for you) or a .zip path; default: the current folder")
    ap.add_argument("--data", default=REPO / "data" / "clients", type=Path, help="the clients' data folder (same as the review app)")
    ap.add_argument("--portal", default=Path(os.environ.get("PORTAL_DATA", REPO / "data" / "portal")), type=Path, help="the client portal's data folder")
    ap.add_argument("--users", default=REPO / "data" / "review_users.json", type=Path, help="the staff accounts file (written without password hashes)")
    ap.add_argument("--firm-files", action="store_true", help="with --client: also include the firm's settings, upkeep log, staff accounts and learning store")
    ap.add_argument("--settings", type=Path, help="the firm's settings file (default: next to the data folder, or I485_SETTINGS)")
    ap.add_argument("--maintenance-log", type=Path, help="the upkeep log (default: next to the data folder, or I485_MAINTENANCE_LOG)")
    ap.add_argument("--learning", type=Path, help="the learning store (default: next to the data folder)")
    ap.add_argument("--by", default="the command line", help="with --everything: who is exporting (the ledger and the access log record it)")
    args = ap.parse_args(argv)
    firm = args.data.parent
    args.settings = args.settings or Path(os.environ.get("I485_SETTINGS") or firm / "settings.json")
    args.maintenance_log = args.maintenance_log or Path(os.environ.get("I485_MAINTENANCE_LOG") or firm / "maintenance_log.json")
    args.learning = args.learning or firm / "learning.db"
    return args


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    if args.verify:
        try:
            problems = verify_zip(args.verify)
        except (OSError, zipfile.BadZipFile) as exc:
            print(f"Not checked: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 2
        for p in problems:
            print(p)
        print("The export matches its manifest." if not problems else f"{len(problems)} problem(s): do not rely on this copy.")
        return 1 if problems else 0
    if args.everything:
        access = None
        if args.users.is_file():  # the staff access log records who exported (the review app writes the same row with the signed-in person's email)
            from review.auth import Accounts

            accounts = Accounts(args.users)
            access = lambda files: accounts.log("data_exported", "", by=args.by, files=files)  # noqa: E731
        try:
            done = everything(where_from(args), out=args.out, who=args.by, role=None, via="tool", access_log=access)
        except ExportError as exc:
            print(f"Not exported: {exc}", file=sys.stderr)
            return 2
        except (OSError, ValueError) as exc:
            print(f"Not exported: {type(exc).__name__}: {exc}", file=sys.stderr)
            return 1
        print(f"Wrote {done['path']}")
        print(f"Files: {done['files']} (including manifest.json and MANIFEST.md)")
        print(f"sha256: {done['sha256']}")
        for w in done["warnings"]:
            print(f"Note: {w}")
        return 0
    if args.pack:
        try:
            target, notes = build_pack(args.out)
        except ExportError as exc:
            print(f"Not exported: {exc}", file=sys.stderr)
            return 2
        print(f"Wrote {target}")
        for n in notes:
            print(f"Note: {n} (the provider's to decide before the pack goes out)")
        return 0
    try:
        if not args.data.is_dir() and not args.portal.is_dir():
            raise ExportError(f"Neither {args.data} nor {args.portal} exists: check --data and --portal.")
        entries, skipped, warnings, read = gather(args)
        scope = "all" if args.all else check_client_name(args.client)
        target = zip_target(args.out, scope).resolve()
        # never write into what is being read, or the folders that hold it: the data and portal folders and their parents,
        # the folder of the accounts file, the folder of any firm file, and every source folder read
        guarded = ([args.data.resolve(), args.data.resolve().parent, args.portal.resolve(), args.portal.resolve().parent, args.users.resolve().parent]
                   + [p.resolve().parent for p in read if p.is_file()] + [p.resolve() for p in read if p.is_dir()])
        for folder in guarded:
            if inside(target, folder):
                raise ExportError(f"Refusing to write the export inside {folder}: choose a folder outside the firm's data (--out).")
        if target.exists():
            raise ExportError(f"{target} already exists; nothing was written. Choose another --out.")
        target.parent.mkdir(parents=True, exist_ok=True)
        count, sha = write_zip(entries, target, "the whole installation" if args.all else f"client {scope}", skipped, warnings)
    except ExportError as exc:
        print(f"Not exported: {exc}", file=sys.stderr)
        return 2
    except (OSError, ValueError) as exc:  # a folder that cannot be read, or an accounts file that is not JSON
        print(f"Not exported: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(f"Wrote {target}")
    print(f"Files: {count} (including manifest.json and MANIFEST.md)")
    print(f"sha256: {sha}")
    for w in warnings:
        print(f"Note: {w}")
    if skipped:
        print(f"{len(skipped)} item(s) left out: see MANIFEST.md in the zip.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
