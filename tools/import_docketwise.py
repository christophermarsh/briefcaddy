"""Bring a firm's Docketwise cases over: one client folder and one portal client per matter, with their documents.

    python tools/import_docketwise.py --contacts contacts.csv --matters matters.csv --documents <folder or .zip> \\
        --out <clients folder> --portal <portal data folder> [--dry-run] [--merge] [--language pt|es|en|ht]

What it reads (all of it is the firm's own export; nothing is fetched from Docketwise):
  --contacts   the Contacts tab's export (Export Contacts, a CSV)
  --matters    the Matters tab's export (a CSV)
  --documents  the files, in one folder per matter (the folder named by the matter's id, number or title), or a .zip of that
  --mapping    which column holds what (default schemas/firm/import_docketwise_columns.json). Docketwise's help pages do not
               list the export's columns, so every column name in that file is a guess until the firm's own export has
               been compared with it; the report says which headings were found and which were not.

What it makes, per matter:
  <out>/<name>-dw<matter id>/source/*.pdf    the documents (photos become one-page PDFs, as the portal does); the overnight
                                             run reads this folder, and the record of each document says "docketwise"
  <out>/<name>-dw<matter id>/docketwise_import.json   which matter it came from and which file is which (so a second run adds nothing twice)
  <portal>/clients/<same id>/profile.json    name, phone, email, language, office, and the matter's id; consent for each channel
                                             only when the export has that column, else "not asked": the portal sends nothing.
                                             The language is the export's when it names one the portal speaks; otherwise the one
                                             chosen with --language (Portuguese when none is chosen). The report says which each got and why.
  <out>/import_report.md                     what mapped, what did not, what was skipped and why, in plain words
  <cases>/<same id>/access.json              a VAWA, T, U or asylum matter (by its type) only: restricted from the start, before
                                             its portal client exists (src/restricted.py protect_new); the report names each one

Safe by construction: a dry run prints the report and writes nothing; a client that already exists is never overwritten
(a second run on the same matter leaves it alone; --merge adds only documents it has not placed before); a folder that
exists but did not come from this matter is refused; nothing is sent to anyone.
Then run the overnight pipeline on the new cases (the command is printed at the end).
"""

from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import re
import sys
import tempfile
import unicodedata
import zipfile
from dataclasses import dataclass, field
from pathlib import Path, PurePosixPath
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402

import clock  # noqa: E402
import conflicts  # noqa: E402
import restricted  # noqa: E402
from portal.bank import language_code, language_names  # noqa: E402
from portal.notify import cases_folder  # noqa: E402
from portal.store import CLIENT_ID, PortalStore, image_to_pdf  # noqa: E402

# Where this tool's words and names come from. tools/import_clio.py is the same engine for another system's export: it swaps this in (use())
# for the length of its run, so one engine reads both and the two cannot drift. Every line the person reads says which system it is.
SOURCES = {
    "docketwise": {"name": "Docketwise", "key": "docketwise", "prefix": "dw", "state": "docketwise_import.json",
                   "mapping": schema_path.path("firm", "import_docketwise_columns"), "tool": "tools/import_docketwise.py",
                   "export_how": "the {what} tab's export button",
                   "unpublished": "Docketwise does not publish the headings its export uses",
                   "consent_note": "Docketwise's own pages do not say its export carries consent",
                   "guess_note": "Docketwise does not publish the names its export uses, so these are guesses until compared with the firm's own files"},
}
SRC = SOURCES["docketwise"]
MAPPING = SRC["mapping"]
STATE = SRC["state"]
REPORT = "import_report.md"
READABLE = {".pdf", ".jpg", ".jpeg", ".png"}
CHANNELS = ("email", "sms", "whatsapp")


def use(source: dict) -> dict:
    """Make `source` (a SOURCES entry) the system this run reads; returns the one that was in use, for restoring."""
    global SRC, MAPPING, STATE
    before = SRC
    SRC, MAPPING, STATE = source, source["mapping"], source["state"]
    return before


class ImportProblem(Exception):
    """Something the person running the tool should read (no traceback)."""


# -- reading the export ----------------------------------------------------------------------------------------


def norm(text: Any) -> str:
    """A heading or a name for comparing: no accents, no capitals, no spaces, underscores or hyphens."""
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "", folded.lower())


def slug(text: Any, limit: int = 48) -> str:
    folded = unicodedata.normalize("NFKD", str(text or "")).encode("ascii", "ignore").decode()
    return re.sub(r"[^a-z0-9]+", "_", folded.lower()).strip("_")[:limit] or "client"


def read_csv(path: Path, what: str) -> tuple[list[str], list[dict[str, str]]]:
    """(the headings as written, one dict per row keyed by those headings). A byte-order mark and ; or tab separators are tolerated."""
    if not path.is_file():
        raise ImportProblem(f"{what}: {path} is not a file. Export it from {SRC['name']} ({SRC['export_how'].format(what=what.lower())}) and give its path.")
    text = path.read_text(encoding="utf-8-sig", errors="replace")
    first = text.splitlines()[0] if text.strip() else ""
    delimiter = max(",;\t", key=first.count) if first else ","
    reader = csv.DictReader(io.StringIO(text), delimiter=delimiter)
    headings = [h.strip() for h in (reader.fieldnames or []) if h and h.strip()]
    if not headings:
        raise ImportProblem(f"{what}: {path} has no heading row.")
    rows = [{(k or "").strip(): (v or "").strip() for k, v in row.items() if k} for row in reader]
    return headings, [r for r in rows if any(r.values())]


def load_mapping(path: Path) -> dict[str, Any]:
    try:
        mapping = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise ImportProblem(f"The column mapping {path} could not be read ({type(exc).__name__}).") from exc
    for part in ("contacts", "matters"):
        if not isinstance(mapping.get(part), dict):
            raise ImportProblem(f"The column mapping {path} has no '{part}' section.")
    return mapping


@dataclass
class Table:
    """One CSV with its column mapping resolved: which heading serves each field, and what the file has that nothing used."""

    headings: list[str]
    rows: list[dict[str, str]]
    found: dict[str, str] = field(default_factory=dict)      # field -> the heading used
    missing: list[str] = field(default_factory=list)         # fields with no heading in this file
    unused: list[str] = field(default_factory=list)          # headings no field looks for

    @classmethod
    def build(cls, headings: list[str], rows: list[dict[str, str]], spec: dict[str, Any]) -> "Table":
        by_norm = {norm(h): h for h in headings}
        found, missing = {}, []
        for name, aliases in spec.items():
            if name.startswith("_"):
                continue
            hit = next((by_norm[norm(a)] for a in aliases if norm(a) in by_norm), None)
            if hit is None:
                missing.append(name)
            else:
                found[name] = hit
        used = set(found.values())
        return cls(headings, rows, found, missing, [h for h in headings if h not in used])

    def get(self, row: dict[str, str], name: str) -> str:
        heading = self.found.get(name)
        return row.get(heading, "").strip() if heading else ""


# -- the plan ------------------------------------------------------------------------------------------------------


@dataclass
class Outcome:
    matter_id: str
    title: str
    client_id: str = ""
    name: str = ""                   # the client's name, for the report (the paralegal knows people, not folder ids)
    language_defaulted: bool = False  # the export gave no language the portal speaks: the chosen one (Portuguese when none was chosen)
    language_said: str = ""           # what the export did say, if anything
    language: str = ""                # the portal language the client got (a code), set when the case is made
    consent_missing: bool = False     # the export gave no consent answer: nothing will be sent
    result: str = ""               # created | finished | already | merged | refused | skipped
    why: str = ""
    documents_placed: list[str] = field(default_factory=list)
    documents_skipped: list[str] = field(default_factory=list)   # "name: reason"
    notes: list[str] = field(default_factory=list)               # things to check, for this case
    protected: str | None = None     # "1367" or "208.6" by its type (restricted.kind_law), or "firm" (--protected-type)
    kind: str = ""                   # the matter's type as the export writes it
    lifted: bool = False             # protected by its type, but an attorney has lifted the restriction since
    conflict: dict[str, int] | None = None  # the conflict search's counts (hits, strong, adverse) for a new case, recorded as not yet decided


def local_id(name: str, matter_id: str) -> str:
    """'Ana Clara Souza' / matter 4101 -> 'ana_clara_souza-dw4101': the same pattern connectors/sync.py gives a mirrored client
    (documents.source_of reads the 'dw' to say where its documents came from; Clio's is 'cl'). A matter id that is not a number becomes a short hash."""
    tail = matter_id if matter_id.isdigit() else hashlib.sha1(matter_id.encode()).hexdigest()[:10]
    return f"{slug(name)}-{SRC['prefix']}{tail}"


def person_name(contacts: Table, contact: dict[str, str] | None, fallback: str) -> str:
    if contact:
        parts = [contacts.get(contact, k) for k in ("first_name", "middle_name", "last_name")]
        joined = " ".join(p for p in parts if p)
        if joined:
            return joined
        if contacts.get(contact, "full_name"):
            return contacts.get(contact, "full_name")
    return fallback


def consent_from(contacts: Table, contact: dict[str, str] | None, mapping: dict[str, Any]) -> tuple[dict[str, bool], bool]:
    """(consent per channel, whether the export said anything about consent for this person). A channel is yes only when the
    export has its column and the cell is a recognised yes; a blank or unrecognised cell is 'not asked', never a yes."""
    yes, no = {norm(x) for x in mapping.get("yes", [])}, {norm(x) for x in mapping.get("no", [])}
    consent, asked = {}, False
    for ch in CHANNELS:
        value = norm(contacts.get(contact, f"{ch}_ok")) if contact else ""
        consent[ch] = value in yes
        asked = asked or value in yes or value in no
    return consent, asked


def folder_keys(matter_id: str, number: str, title: str) -> list[str]:
    return [k for k in {norm(matter_id), norm(number), norm(title)} if k]


def match_folders(folders: list[str], matters: dict[str, dict[str, str]]) -> tuple[dict[str, str], dict[str, str]]:
    """({folder: matter id}, {folder: why it was not placed}). A folder belongs to the matter whose id, number or title it is
    named for; failing that, to the one matter whose id appears in its name as a whole word ('4101 Ana Sample', 'Ana Sample (4101)')."""
    exact: dict[str, set[str]] = {}
    for mid, m in matters.items():
        for key in folder_keys(mid, m.get("number", ""), m.get("title", "")):
            exact.setdefault(key, set()).add(mid)
    matched, unplaced = {}, {}
    for folder in folders:
        hits = exact.get(norm(folder), set())
        if not hits:
            words = {w for w in re.split(r"[^A-Za-z0-9]+", folder) if w}
            hits = {mid for mid in matters if mid in words}
        if len(hits) == 1:
            matched[folder] = next(iter(hits))
        elif hits:
            unplaced[folder] = "two matters have this name or number, so it was not placed; rename the folder to the matter's id"
        else:
            unplaced[folder] = "no matter in the matters file goes with this folder"
    return matched, unplaced


def _safe_member(name: str) -> PurePosixPath | None:
    """The path inside the zip, or None when it could land outside the folder: absolute, climbing out with .., or any part with a colon
    (on Windows 'D:/x.pdf' and the drive-relative 'D:x.pdf' leave the folder, and 'a:b.pdf' names a hidden data stream)."""
    p = PurePosixPath(name.replace("\\", "/"))
    return None if p.is_absolute() or ".." in p.parts or not p.parts or any(":" in part for part in p.parts) else p


def unpack(zip_path: Path, into: Path) -> list[str]:
    """Extracts the documents a zip holds (readable types only, no path may leave the folder). Returns what was left out."""
    left_out = []
    with zipfile.ZipFile(zip_path) as zf:
        for info in zf.infolist():
            if info.is_dir():
                continue
            rel = _safe_member(info.filename)
            if rel is None or (info.external_attr >> 16) & 0o170000 == 0o120000:
                left_out.append(f"{info.filename}: an unsafe path or a link, not opened")
                continue
            if rel.suffix.lower() not in READABLE:
                continue  # listed with the folder's own unreadable files below
            target = into.joinpath(*rel.parts)
            target.parent.mkdir(parents=True, exist_ok=True)
            with zf.open(info) as src, open(target, "wb") as dst:
                for chunk in iter(lambda: src.read(1 << 20), b""):
                    dst.write(chunk)
    return left_out


def files_by_folder(root: Path) -> tuple[dict[str, list[Path]], list[Path], list[Path]]:
    """({top-level folder name: its files, however deep}, files loose in the root, files of a type the pipeline cannot read)."""
    by_folder: dict[str, list[Path]] = {}
    loose, unreadable = [], []
    for path in sorted(root.rglob("*")):
        if not path.is_file() or path.is_symlink() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in READABLE:
            unreadable.append(path)
            continue
        rel = path.relative_to(root)
        if len(rel.parts) == 1:
            loose.append(path)
        else:
            by_folder.setdefault(rel.parts[0], []).append(path)
    return by_folder, loose, unreadable


def dest_name(path: Path, root: Path, taken: set[str]) -> str:
    """The name a document gets in the case folder: its place under the matter's folder, readable, always .pdf and never twice."""
    parts = path.relative_to(root).parts[1:]
    stem = " - ".join(re.sub(r"[^\w\- ]+", "_", unicodedata.normalize("NFKC", p)).strip() for p in (*parts[:-1], Path(parts[-1]).stem))
    name = (stem[:90].strip() or "document") + ".pdf"
    if name.lower() in taken:
        base = name[:-4]
        name = f"{base} ({len(taken) + 1}).pdf"
    return name


def as_pdf(path: Path) -> bytes:
    data = path.read_bytes()
    if path.suffix.lower() == ".pdf":
        return data
    return image_to_pdf(data)  # a phone photo becomes a one-page PDF, as the portal does it


# -- existing clients ---------------------------------------------------------------------------------------------


def portal_index(portal: Path) -> dict[str, str]:
    """{the system's matter id: portal client id} for the clients already here. Read only: it never creates the portal's folders."""
    found = {}
    for profile in sorted((portal / "clients").glob("*/profile.json")) if (portal / "clients").is_dir() else []:
        try:
            data = json.loads(profile.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        if data.get(f"{SRC['key']}_matter_id"):
            found[str(data[f"{SRC['key']}_matter_id"])] = profile.parent.name
    return found


def state_of(folder: Path) -> dict[str, Any] | None:
    try:
        return json.loads((folder / STATE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None


def write_json(path: Path, data: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def known_contacts(portal: Path) -> dict[str, str]:
    """{email or last ten digits of a phone: client id} for the portal's clients, to point out the same person twice."""
    out = {}
    for profile in sorted((portal / "clients").glob("*/profile.json")) if (portal / "clients").is_dir() else []:
        try:
            data = json.loads(profile.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            continue
        for key in contact_keys(data.get("email", ""), data.get("phone", "")):
            out.setdefault(key, profile.parent.name)
    return out


def contact_keys(email: str, phone: str) -> list[str]:
    digits = re.sub(r"\D", "", phone or "")
    return ([email.strip().lower()] if email.strip() else []) + ([digits[-10:]] if len(digits) >= 10 else [])


# -- the run ---------------------------------------------------------------------------------------------------------


@dataclass
class Run:
    outcomes: list[Outcome] = field(default_factory=list)
    contacts: Table | None = None
    matters: Table | None = None
    unplaced_folders: dict[str, str] = field(default_factory=dict)
    loose_files: list[str] = field(default_factory=list)
    unreadable: list[str] = field(default_factory=list)
    zip_left_out: list[str] = field(default_factory=list)
    people_with_several: dict[str, list[Outcome]] = field(default_factory=dict)
    dry_run: bool = False
    merge: bool = False
    default_language: str = "pt"      # for a client whose export row names no language the portal speaks
    language_chosen: bool = False     # the person running the tool chose it (--language), rather than the Portuguese default
    out: Path = Path(".")
    portal: Path = Path(".")
    cases: Path = Path(".")  # the review app's case folders, where a protected matter's restriction is recorded
    protected_types: set[str] = field(default_factory=set)  # matter types the firm names protected (--protected-type), compared by norm()
    indexed: bool = False  # the people index was brought up to date once for this run (each new case is added to it as it is made)


def run_import(contacts_csv: Path, matters_csv: Path, documents: Path | None, out: Path, portal: Path, mapping_path: Path = MAPPING,
               dry_run: bool = False, merge: bool = False, include_archived: bool = False, only_type: str | None = None,
               cases: Path | None = None, protected_types: list[str] | None = None, language: str | None = None) -> Run:
    """Reads the export, decides what each matter becomes, and (unless dry_run) does it. The Run says what happened; report() writes it up.
    cases: the case folders the review app and the portal's messages read (default: the installation's, src/portal/notify.py
    cases_folder); a VAWA, T, U or asylum matter is restricted there before its portal client is made.
    language: the portal language for a client whose row names none (a code or a name: "es", "Spanish"); Portuguese when not given."""
    chosen = None
    if language:
        chosen = language_code(language)
        if chosen is None:
            raise ImportProblem(f"The language {language!r} is not one the portal speaks. Choose one of: " + ", ".join(f"{n} ({c})" for c, n in language_names().items()) + ".")
    mapping = load_mapping(mapping_path)
    c_head, c_rows = read_csv(contacts_csv, "Contacts")
    m_head, m_rows = read_csv(matters_csv, "Matters")
    contacts, matters = Table.build(c_head, c_rows, mapping["contacts"]), Table.build(m_head, m_rows, mapping["matters"])
    run = Run(contacts=contacts, matters=matters, dry_run=dry_run, merge=merge, out=out, portal=portal, cases=Path(cases) if cases else cases_folder(),
              protected_types={norm(t) for t in protected_types or () if norm(t)}, default_language=chosen or "pt", language_chosen=chosen is not None)
    if "id" not in matters.found:
        raise ImportProblem("The matters file needs a column with each matter's id, and none of its headings is one the importer knows. "
                            f"It looks for: {', '.join(mapping['matters'].get('id') or [])}. {SRC['unpublished']}, "
                            f"so tell us which heading in your file holds the matter's id and we will add it. The file's headings are: {', '.join(matters.headings)}.")

    by_id = {contacts.get(r, "id"): r for r in contacts.rows if contacts.get(r, "id")}
    by_name: dict[str, list[dict[str, str]]] = {}
    for r in contacts.rows:
        by_name.setdefault(norm(person_name(contacts, r, "")), []).append(r)

    # every matter's id, number and title, for matching document folders (a folder of a skipped matter is then said to be that, not unknown)
    wanted = {matters.get(r, "id"): {"number": matters.get(r, "number"), "title": matters.get(r, "title")} for r in matters.rows if matters.get(r, "id")}
    seen: set[str] = set()
    plans: list[tuple[Outcome, dict[str, str], dict[str, str] | None, str]] = []
    for row in matters.rows:
        mid, title = matters.get(row, "id"), matters.get(row, "title")
        out_ = Outcome(mid, title or mid or f"Row {len(run.outcomes) + 1} of the matters file")
        run.outcomes.append(out_)
        if not mid:
            out_.result, out_.why = "skipped", "the row has no matter id"
            continue
        if mid in seen:
            out_.result, out_.why = "skipped", "the same matter id appears twice in the matters file; only the first was used"
            continue
        seen.add(mid)
        archived = norm(matters.get(row, "archived")) in {norm(x) for x in mapping.get("archived_yes", mapping.get("yes", []))}
        if archived and not include_archived:
            out_.result, out_.why = "skipped", f"{SRC.get('archived_word', 'archived')} in {SRC['name']} (use the include-archived option to bring those matters over)"
            continue
        if only_type and norm(matters.get(row, "type")) != norm(only_type):
            out_.result, out_.why = "skipped", f"its type is {matters.get(row, 'type') or 'not given'}, not {only_type}"
            continue
        contact, note = None, ""
        cid, cname = matters.get(row, "client_id"), matters.get(row, "client_name")
        if cid and cid in by_id:
            contact = by_id[cid]
        elif cname and len(by_name.get(norm(cname), [])) == 1:
            contact = by_name[norm(cname)][0]
        elif cname and len(by_name.get(norm(cname), [])) > 1:
            out_.result, out_.why = "skipped", f"{len(by_name[norm(cname)])} contacts are named {cname} and the matter does not say which; add the contact's id to the matters file"
            continue
        name = (cname if SRC.get("name_from_matter") and cname else "") or person_name(contacts, contact, cname)  # Clio: the matter's client name, as a connection to Clio takes it
        if not name:
            out_.result = "skipped"
            out_.why = (f"its client (contact {cid}) is not in the contacts file and the matter gives no client name" if cid
                        else "no client name could be found for this matter")
            continue
        if contact is None:
            note = f"no contact row was found for this matter, so the client has a name only (no phone, email or language from {SRC['name']})"
        cid_local = local_id(name, mid)
        if not CLIENT_ID.fullmatch(cid_local):
            out_.result, out_.why = "skipped", f"the client's folder name came out as {cid_local!r}, which the portal cannot use"
            continue
        out_.client_id = cid_local
        out_.name = name
        if note:
            out_.notes.append(note)
        plans.append((out_, row, contact, name))

    # who has more than one matter (each matter is its own case here, so the portal's sign-in by phone or email finds only one)
    owners: dict[str, list[Outcome]] = {}
    for out_, _row, contact, name in plans:
        key = (contacts.get(contact, "id") or norm(name)) if contact else norm(name)
        owners.setdefault(key, []).append(out_)
    for key, mine in owners.items():
        if len(mine) > 1:
            run.people_with_several[key] = mine

    # the documents
    tmp = None
    by_folder: dict[str, list[Path]] = {}
    doc_root: Path | None = None
    if documents is not None:
        if not documents.exists():
            raise ImportProblem(f"The documents folder {documents} does not exist.")
        doc_root = documents
        if documents.is_file():
            if documents.suffix.lower() != ".zip":
                raise ImportProblem(f"{documents} is a file but not a .zip: give the folder of documents, or the zip of it.")
            tmp = tempfile.TemporaryDirectory(prefix=f"{SRC['key']}-import-")
            doc_root = Path(tmp.name)
            try:
                run.zip_left_out = unpack(documents, doc_root)
            except zipfile.BadZipFile as exc:
                tmp.cleanup()
                raise ImportProblem(f"{documents} is not a readable zip file.") from exc
            kids = list(doc_root.iterdir())
            if len(kids) == 1 and kids[0].is_dir() and not match_folders([kids[0].name], wanted)[0]:
                doc_root = kids[0]  # the zip's one wrapping folder (not itself a matter's): the matters' folders are inside it
        by_folder, loose, unreadable = files_by_folder(doc_root)
        run.loose_files = [p.name for p in loose]
        run.unreadable = [p.relative_to(doc_root).as_posix() for p in unreadable]
    try:
        matched, run.unplaced_folders = match_folders(sorted(by_folder), wanted)
        files_of: dict[str, list[Path]] = {}
        planned = {o.matter_id for o, *_ in plans}
        for folder, mid in matched.items():
            if mid in planned:
                files_of.setdefault(mid, []).extend(by_folder[folder])
            else:
                run.unplaced_folders[folder] = "its matter was skipped (see the skipped list), so its documents were not placed"
        index = known_contacts(portal)
        known = portal_index(portal)
        for out_, row, contact, name in plans:
            apply_one(run, out_, row, contact, name, files_of.get(out_.matter_id, []), doc_root, mapping, known, index)
    finally:
        if tmp is not None:
            tmp.cleanup()
    return run


def protection(run: Run, kind: str) -> str | None:
    """Why a matter of this type is restricted from the start: the law by its words (restricted.kind_law: "1367", "208.6"), or the
    firm's own naming of it (--protected-type: "firm"), else None."""
    return restricted.kind_law(kind) or (restricted.FIRM_KIND if kind and norm(kind) in run.protected_types else None)


def protection_words(found: str | None) -> str:
    return "named protected by the firm (the protected-type option)" if found == restricted.FIRM_KIND else restricted.law_words(found)


def apply_one(run: Run, out_: Outcome, row: dict[str, str], contact: dict[str, str] | None, name: str, files: list[Path], doc_root: Path | None,
              mapping: dict[str, Any], known: dict[str, str], index: dict[str, str]) -> None:
    """One matter: refuse, leave alone, finish, add documents to, or create its case (dry_run: only say which)."""
    contacts, matters = run.contacts, run.matters
    assert contacts is not None and matters is not None
    case = run.out / out_.client_id
    state = state_of(case)
    owner = known.get(out_.matter_id)                 # a portal client that already carries this matter's id
    profile_path = run.portal / "clients" / out_.client_id / "profile.json"
    if owner is not None and owner != out_.client_id:
        out_.result, out_.why = "refused", f"this matter is already in the portal as client {owner}, under another name; nothing was changed"
        return
    if state is not None and str(state.get("matter_id")) != out_.matter_id:
        out_.result, out_.why = "refused", "a folder with this name exists and belongs to another matter; nothing was changed"
        return
    ours = state is not None or owner == out_.client_id
    if not ours and (case.exists() or profile_path.exists()):
        out_.result, out_.why = "refused", (f"a client with this name already exists here and did not come from this {SRC['name']} matter; "
                                            "it was not overwritten and nothing was added to it")
        return
    complete = owner == out_.client_id                # the profile carries the matter's id: the case was made whole
    out_.kind = matters.get(row, "type")
    out_.protected = protection(run, out_.kind)
    if out_.protected and not run.dry_run:
        # restricted before the client exists in the portal: no list shows them, no message reaches them (src/restricted.py). Also for
        # a matter an earlier import brought in before this was done; never again once a record exists (an attorney's choice stands)
        restricted.protect_new(run.cases / out_.client_id, out_.protected, out_.kind, f"Imported from {SRC['name']}, matter type", f"the {SRC['name']} import")
        out_.lifted = not restricted.is_restricted(run.cases / out_.client_id)
    if complete and not run.merge:
        out_.result, out_.why = "already", "already imported from this matter; left as it is (the merge option adds new documents)"
        return
    if not complete and not (run.cases / out_.client_id / conflicts.FILE).exists():
        # the conflict search before the case exists (src/conflicts.py): logged, and recorded as not yet decided, so the client is held out of
        # invitations until an attorney decides on the screen (Settings, Conflict checks). A search that cannot run makes no case.
        try:
            record = conflicts.hold_new(run.cases, out_.client_id, {"name": name}, by=f"the {SRC['name']} import", purpose="import", via="importer",
                                        refresh=not run.indexed, dry_run=run.dry_run)
            run.indexed = True
            out_.conflict = conflicts.counts(record, run.cases)
        except Exception as exc:  # noqa: BLE001 -- said in the report; nothing of the case is made
            out_.result, out_.why = "refused", f"the conflict search could not run ({type(exc).__name__}), so the case was not made; run the import again"
            return
    out_.result = "merged" if complete else ("finished" if ours else "created")
    out_.why = "already imported; only new documents were added" if complete else ("an earlier import of this matter stopped part way and was completed" if ours else "")

    # the profile's fields (only when the case is being made: a merge never touches the profile)
    email, phone = (contacts.get(contact, "email"), contacts.get(contact, "phone")) if contact else ("", "")
    said = contacts.get(contact, "language") if contact else ""
    language = language_code(said) if said else None
    consent, asked = consent_from(contacts, contact, mapping)
    out_.language = language or run.default_language
    office = (contacts.get(contact, "office") if contact else "") or matters.get(row, "office")
    if not complete:
        out_.language_defaulted, out_.language_said = language is None, said
        out_.consent_missing = not asked
        if office:
            from offices import offices

            if not any(office.lower() in (o["id"].lower(), o["name"].lower()) for o in offices()):
                out_.notes.append(f"the office {office!r} is not one on the Settings page, so the office is left blank (the office for the client's state is used)")
                office = ""
        for key in contact_keys(email, phone):
            twin = index.get(key)
            if twin and twin != out_.client_id:
                out_.notes.append(f"the same phone or email belongs to client {twin} already: check that they are not the same person entered twice")
                break

    # the documents: what is new, by the file's own hash (so a second run places nothing twice)
    placed_before = dict(state["files"]) if state else {}
    todo: list[tuple[str, str, Path]] = []
    for path in files:
        digest = hashlib.sha256(path.read_bytes()).hexdigest()
        label = path.relative_to(doc_root).as_posix() if doc_root else path.name
        if digest in placed_before:
            out_.documents_skipped.append(f"{label}: already placed")
        elif any(digest == d for d, _, _ in todo):
            out_.documents_skipped.append(f"{label}: the same file twice")
        else:
            taken = {n.lower() for n in placed_before.values()} | {n.lower() for _, n, _ in todo}
            todo.append((digest, dest_name(path, doc_root, taken), path))

    if run.dry_run:
        out_.documents_placed = [n for _, n, _ in todo]
        return

    store = PortalStore(run.portal)
    source = case / "source"
    source.mkdir(parents=True, exist_ok=True)
    if state is None:  # the record first: if this stops part way, the next run knows whose folder this is and finishes it
        state = {"matter_id": out_.matter_id, "contact_id": contacts.get(contact, "id") if contact else "", "imported_at": clock.stamp(), "files": {}}
        write_json(case / STATE, state)
    if not complete:
        store.add_client(out_.client_id, name, phone=phone, email=email, language=language or run.default_language, consent=consent)
        key = SRC["key"]
        extra: dict[str, Any] = {f"{key}_matter_id": out_.matter_id, f"{key}_matter_title": out_.title, "imported_from": key,
                                 "imported_at": clock.stamp(), "consent_asked": asked, "language_from_export": bool(language)}
        if contact and contacts.get(contact, "id"):
            extra[f"{key}_contact_id"] = contacts.get(contact, "id")
        if matters.get(row, "type"):
            extra[f"{key}_matter_type"] = matters.get(row, "type")
        if office:
            extra["office"] = office
        store.update_profile(out_.client_id, **extra)
        store.log(out_.client_id, f"imported_from_{key}", {"by": "firm"})
    for digest, name_, path in todo:
        try:
            data = as_pdf(path)
        except Exception as exc:  # noqa: BLE001 -- one bad photo must not stop the case
            out_.documents_skipped.append(f"{path.name}: could not be read as a picture or PDF ({type(exc).__name__})")
            continue
        part = source / f".{name_}.part"
        part.write_bytes(data)
        os.replace(part, source / name_)
        state["files"][digest] = name_
        write_json(case / STATE, state)
        out_.documents_placed.append(name_)


# -- the report ------------------------------------------------------------------------------------------------------------


def plural(n: int, one: str, many: str | None = None) -> str:
    return f"{n} {one if n == 1 else (many or one + 's')}"


def who(o: Outcome) -> str:
    """'Ana Clara Exemplo Souza (matter Souza SIJ)': a person and their matter, as the paralegal knows them, never the folder id."""
    return f"{o.name} (matter {o.title})" if o.name else o.title


def have(n: int) -> str:
    return "has" if n == 1 else "have"


# what the report calls each field of the export (the paralegal reads these, not the mapping file's names)
LABELS = {"id": "id", "first_name": "first name", "middle_name": "middle name", "last_name": "last name", "full_name": "full name", "email": "email",
          "phone": "phone", "language": "language", "email_ok": "consent to email", "sms_ok": "consent to text", "whatsapp_ok": "consent to WhatsApp",
          "office": "office", "number": "matter number", "title": "matter title", "client_id": "the client's contact id", "client_name": "client name",
          "type": "matter type", "status": "status", "archived": "archived"}


def report(run: Run) -> str:
    c, m = run.contacts, run.matters
    assert c is not None and m is not None
    made = [o for o in run.outcomes if o.result in ("created", "finished")]
    merged = [o for o in run.outcomes if o.result == "merged"]
    already = [o for o in run.outcomes if o.result == "already"]
    refused = [o for o in run.outcomes if o.result == "refused"]
    skipped = [o for o in run.outcomes if o.result == "skipped"]
    docs = sum(len(o.documents_placed) for o in run.outcomes)
    verb = "would be" if run.dry_run else "were"

    def was(n: int) -> str:
        """'would be' in a practice run, else 'was' or 'were' to agree with the count."""
        return "would be" if run.dry_run else ("was" if n == 1 else "were")

    lines = [f"# {SRC['name']} import" + (": practice run, nothing was written" if run.dry_run else ""), "",
             f"Made {clock.us_date(clock.stamp())}. The {SRC['name']} export gave {plural(len(c.rows), 'contact')} and {plural(len(m.rows), 'matter')}.", "",
             "## What happened", "",
             f"- Cases {verb} created: {len(made)}" + (" (the ones an earlier run left half done are counted here)" if any(o.result == "finished" for o in made) else ""),
             f"- Cases that already existed from an earlier import, left as they were: {len(already)}",
             f"- Cases that already existed and {verb} given new documents only: {len(merged)}",
             f"- Matters not imported because a client with that name was already here: {len(refused)}",
             f"- Matters skipped: {len(skipped)}",
             f"- Documents {verb} placed: {docs}", ""]
    protected = [o for o in run.outcomes if o.protected and not o.lifted and o.result in ("created", "finished", "merged", "already")]
    lifted = [o for o in run.outcomes if o.protected and o.lifted and o.result in ("created", "finished", "merged", "already")]
    if protected:  # named first, before anything else: a VAWA, T, U or asylum client is the one an invitation could put at risk
        lines += ["## Restricted: protected cases, not invited", "",
                  f"{plural(len(protected), 'matter')} {'is' if len(protected) == 1 else 'are'} a kind of case the law keeps confidential "
                  "(VAWA, T or U visa: 8 U.S.C. 1367; asylum, withholding of removal, Convention Against Torture protection, refugee "
                  "admission: 8 CFR 208.6), or one the firm named protected. "
                  + ("Each would be restricted from the start" if run.dry_run else "Each is restricted")
                  + ": only attorneys, and the staff an attorney names, can open it, and it is left out of every list and report for everyone else. "
                  "No invitation was sent, and none goes out by itself, even once the firm's invitations are switched on: the office gives "
                  "the client their sign-in link in person.", ""]
        lines += [f"- {who(o)}: matter type {o.kind}, {protection_words(o.protected)}" for o in protected] + [""]
    if lifted:  # an attorney's choice since an earlier import: said, never undone
        lines += ["## Protected by type, restriction lifted by an attorney", "",
                  "These matters' types are a protected kind, but an attorney lifted the restriction after an earlier import. This import "
                  "left that as it is: they are not restricted now, and their invitations follow the ordinary rules.", ""]
        lines += [f"- {who(o)}: matter type {o.kind}" for o in lifted] + [""]
    searched = [o for o in run.outcomes if o.conflict is not None and o.result in ("created", "finished")]
    if searched:  # every new case waits for an attorney's conflict decision: said before the rest, since nobody is invited until then
        with_hits = [o for o in searched if o.conflict["hits"] or o.conflict.get("hidden")]
        lines += ["## Conflict search: an attorney decides before anyone is invited", "",
                  f"The conflict search {'would run' if run.dry_run else 'ran'} for {plural(len(searched), 'new client')} against every person on every other case. "
                  + ("Each would be recorded" if run.dry_run else "Each is recorded") + " as not yet decided, and no invitation goes to the client until an "
                  "attorney decides under Settings, Conflict checks, where the hits are shown in full. What a hit means is the attorney's call.", ""]
        lines += [f"- {who(o)}: {plural(o.conflict['hits'], 'hit')}" + (f", {o.conflict['strong']} strong" if o.conflict["strong"] else "")
                  + (f", {o.conflict['adverse']} on the other side of a case" if o.conflict["adverse"] else "")
                  + (", and a hit on a restricted case, which only an attorney sees" if o.conflict.get("hidden") else "") for o in with_hits]
        lines += ([f"- {plural(len(searched) - len(with_hits), 'other client')}: no hits"] if len(searched) > len(with_hits) else []) + [""]
    # every type the export holds, and how the importer took it: a protected kind written in other words ("Humanitarian") is caught by eye
    seen: dict[str, int] = {}
    for row in m.rows:
        kind = m.get(row, "type")
        seen[kind] = seen.get(kind, 0) + 1
    if seen:
        lines += ["## Matter types in the export", "",
                  "Each type the matters file holds, and whether the importer took it as a protected kind. If a type below is a VAWA, T or U "
                  "visa, asylum, withholding, Convention Against Torture or refugee case in other words, tell the attorney before anyone is "
                  "invited, and run the import again with the protected-type option naming that type: those matters are then restricted "
                  "from the start too.", ""]
        for kind, n in sorted(seen.items(), key=lambda kv: kv[0].casefold()):
            found = protection(run, kind)
            lines.append(f"- {kind or 'No type given'} ({plural(n, 'matter')}): "
                         + (f"protected, {protection_words(found)}" if found else "not taken as protected"))
        lines.append("")
    if made or merged:
        lines += ["## Cases " + ("to be created" if run.dry_run else "created or added to"), ""]
        for o in made + merged:
            lines.append(f"- {who(o)}: {plural(len(o.documents_placed), 'document')}" + (f"; {o.why}" if o.result != "created" else ""))
        lines.append("")
    for heading, group in (("Left alone, already imported", already), ("Not imported: a client with that name exists", refused), ("Skipped", skipped)):
        if group:
            lines += [f"## {heading}", ""] + [f"- {who(o)}" + (f" (matter number {o.matter_id})" if o.matter_id else "") + f": {o.why}" for o in group] + [""]

    lines += ["## Things to check", ""]
    checks = []
    missing_lang = [o for o in made if o.language_defaulted]
    names = language_names()
    if missing_lang:
        got = names.get(run.default_language, run.default_language)
        checks.append(f"Language: {plural(len(missing_lang), 'client')} {'would get' if run.dry_run else 'got'} {got}, because the export gave no language the portal speaks"
                      + (f" and {got} is the language chosen for those clients. " if run.language_chosen
                         else " and no other language was chosen (Portuguese is the default; the language option chooses another for them). ")
                      + "Set the right one before the first invitation:")
        checks += [f"  {who(o)}" + (f": the export says {o.language_said!r}" if o.language_said else ": none given") for o in missing_lang]
    no_consent = [o for o in made if o.consent_missing]
    if no_consent:
        checks.append(f"Consent: {plural(len(no_consent), 'client')} {have(len(no_consent))} no consent answer, so the portal will send nothing until the office records consent "
                      f"(on purpose: {SRC['consent_note']}):")
        checks += [f"  {who(o)}" for o in no_consent]
    for o in made + merged:
        checks += [f"{who(o)}: {n}." for n in o.notes]
    if run.people_with_several:
        checks.append(f"Several matters: {plural(len(run.people_with_several), 'person', 'people')} {have(len(run.people_with_several))} more than one matter. Each matter is its own case here, "
                      "and the portal finds a client by phone or email, so only one of a person's cases can be reached that way. "
                      "Decide with the attorney which case the client signs in to:")
        for mine in run.people_with_several.values():
            checks.append(f"  {mine[0].name}: " + "; ".join(o.title for o in mine))
    if run.unplaced_folders:
        checks += [f"The documents folder {f!r} {was(1)} left out: {why}." for f, why in sorted(run.unplaced_folders.items())]
    if run.loose_files:
        checks.append(f"{plural(len(run.loose_files), 'file')} {'is' if len(run.loose_files) == 1 else 'are'} loose in the documents folder, not in a matter's folder, and {was(len(run.loose_files))} left out: "
                      + ", ".join(sorted(run.loose_files)[:10]) + (" and more" if len(run.loose_files) > 10 else ""))
    if run.unreadable:
        checks.append(f"{plural(len(run.unreadable), 'file')} {'is' if len(run.unreadable) == 1 else 'are'} not a PDF or a photo (the pipeline cannot read them) and {was(len(run.unreadable))} left out: "
                      + ", ".join(run.unreadable[:10]) + (" and more" if len(run.unreadable) > 10 else ""))
    for left in run.zip_left_out:
        checks.append(left)
    for o in run.outcomes:
        for d in o.documents_skipped:
            if "already placed" in d or "the same file twice" in d:
                continue
            checks.append(f"{who(o)}: {d}")
    lines += [f"- {c_}" for c_ in checks] or ["- Nothing."]
    lines.append("")

    if made:  # every client's language and where it came from, not only the ones that fell back
        lines += ["## Each client's language", ""]
        for o in made:
            why = (f"as the export says ({o.language_said})" if not o.language_defaulted
                   else "because the export gave no language the portal speaks" + (" and it is the one chosen" if run.language_chosen else " (the default)"))
            lines.append(f"- {who(o)}: {names.get(o.language, o.language)}, {why}")
        lines.append("")
    lines += ["## What the importer found in the export", "",
              f"The importer looks for columns by name, and {SRC['guess_note']}.", ""]
    for label, table in (("Contacts", c), ("Matters", m)):
        lines += [f"### {label} file", "",
                  "- Found: " + (", ".join(f"{LABELS.get(field_, field_)} (column '{head}')" for field_, head in table.found.items()) or "nothing"),
                  "- Looked for and not in the file: " + (", ".join(LABELS.get(f, f) for f in table.missing) or "nothing"),
                  "- In the file and not used: " + (", ".join(table.unused) or "nothing"), ""]
    if "language" in c.missing or "office" in c.missing:
        lines += ["Whatever is listed as not in the file is left blank, and the importer says so against each client above. "
                  "If the export does have the information under another heading, ask whoever set up the import to match that heading.", ""]
    return "\n".join(lines).rstrip() + "\n"


def write_report(run: Run, text: str) -> Path:
    path = run.out / REPORT
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():  # keep the last report: each run is a record of what changed
        stamp = re.sub(r"[^0-9]", "", clock.stamp("seconds"))[:14]
        older = path.with_name(f"import_report_{stamp}.md")
        n = 1
        while older.exists():  # two runs in one second
            n += 1
            older = path.with_name(f"import_report_{stamp}_{n}.md")
        os.replace(path, older)
    path.write_text(text, encoding="utf-8")
    return path


def overnight_command(out: Path) -> str:
    return f"python src/overnight.py --clients-root \"{out}\""


def parse(argv: list[str] | None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description=f"Bring a firm's {SRC['name']} export (contacts, matters, documents) into client folders and the portal.")
    ap.add_argument("--contacts", type=Path, required=True, help="the Contacts tab's export (CSV)")
    ap.add_argument("--matters", type=Path, required=True, help="the Matters tab's export (CSV)")
    ap.add_argument("--documents", type=Path, help="the documents: one folder per matter (named by the matter's id, number or title), or a .zip of that")
    ap.add_argument("--out", type=Path, required=True, help="the clients folder: one folder per case is made inside it (the overnight run reads it)")
    ap.add_argument("--portal", type=Path, default=Path(os.environ.get("PORTAL_DATA", REPO / "data" / "portal")), help="the client portal's data folder")
    ap.add_argument("--mapping", type=Path, default=MAPPING, help=f"which column holds what (default: {MAPPING.relative_to(REPO).as_posix()})")
    ap.add_argument("--dry-run", action="store_true", help="print the report and write nothing")
    ap.add_argument("--merge", action="store_true", help="for a matter already imported: add documents not placed before (nothing else changes)")
    ap.add_argument("--include-archived", action="store_true", help=f"also bring matters {SRC['name']} marks as archived")
    ap.add_argument("--type", dest="only_type", help="only matters of this type (as the matters file writes it)")
    ap.add_argument("--cases", type=Path, help="the review app's case folders (default: I485_CASES, else data/clients): a VAWA, T, U or asylum "
                                               "matter is restricted there from the start")
    ap.add_argument("--protected-type", dest="protected_types", action="append", default=[],
                    help="a matter type the firm treats as protected though its words are not ones the importer knows (for example "
                         "'Humanitarian'); its matters are restricted from the start and never invited. Repeat for each type.")
    ap.add_argument("--language", help="the portal language for clients whose row names none: pt, es, en or ht, or its name (default: Portuguese). "
                                       "The report says which language each client got and why")
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse(argv)
    try:
        run = run_import(args.contacts, args.matters, args.documents, args.out, args.portal, args.mapping, args.dry_run, args.merge,
                         args.include_archived, args.only_type, args.cases, args.protected_types, args.language)
        text = report(run)
        if args.dry_run:
            print(text)
            print("Practice run: nothing was written. Run again without --dry-run to import.")
            return 0
        path = write_report(run, text)
    except ImportProblem as exc:
        print(f"Not imported: {exc}", file=sys.stderr)
        return 2
    except OSError as exc:
        print(f"Not imported: {type(exc).__name__}: {exc}", file=sys.stderr)
        return 1
    print(text)
    print(f"The report is saved as {path}")
    new = [o.client_id for o in run.outcomes if o.result in ("created", "finished", "merged")]
    if new:
        print(f"Next: have the overnight run read the {len(new)} case(s), now or tonight:\n  {overnight_command(args.out)} --dry-run   (what it would do)\n  {overnight_command(args.out)}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
