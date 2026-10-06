"""The catalog of every record the product keeps: where it lives, who writes it, its version, and each field with its type and meaning.

docs/data_dictionary.md is made from this module (tools/data_dictionary.py) and from the SQL in src/index.py and src/query.py (each column's
sentence is the comment beside it; each table's is in the module's TABLES). The export of the firm's data (tools/export_firm.py --everything) lists
a case's files by the patterns here, so a file is in the export if and only if this catalog lists it. A test (tests/test_data_dictionary.py)
regenerates the dictionary and fails when it differs from the committed one, and others read the records the product really writes and fail on a
field that is not in this catalog: the dictionary cannot go stale.

A record is a dict:

    id          the section's key (the ledger's kind where there is one: src/events.py KINDS)
    title       what it is, in a few words
    area        case (a folder per case in data/clients/), portal (a folder per client in data/portal/clients/), firm (one file for the firm), logs
    files       the file names or patterns (relative to the case's folder, the portal client's folder, or the firm's data folder)
    where       the path as the firm's IT person sees it
    format      JSON, JSON Lines, SQLite
    written_by  who writes it, and where in the code
    version     the record's own version field (the number it carries), or None when it has no version field (the ledger then says 1)
    versions    [(version, what it was)] oldest first: what changed in each version, and what was added to a version without raising it
    fields      [(name, type, meaning, flags)]: flags "person" for a field that holds a person's data, "secret" for one that is never exported
    exported    whether the export of the firm's data includes it (a record that holds secrets or only copies others does not)

Where a record is a list or a dict of rows, the field names are those of one row, and "rows" says what the rows are.
"""

from __future__ import annotations

from typing import Any

from . import audit_records, case_records, firm_records, portal_records
from .flags import PERSON, SECRET  # noqa: F401 - public catalog flags

# Each family owns its metadata. Explicit positions preserve the original
# interleaved ordering, including first-match file lookup and export patterns.
RECORDS: list[dict[str, Any]] = [
    record for _, record in sorted([
        *case_records.RECORDS.items(), *portal_records.RECORDS.items(),
        *firm_records.RECORDS.items(), *audit_records.RECORDS.items(),
    ])
]

# The attributed case receipt stores the same actual schema as the portal
# counterpart; keep both dictionary tables tied to the producer's fields.
next(r for r in RECORDS if r["id"] == "portal_promotion_case")["fields"] = list(next(r for r in RECORDS if r["id"] == "portal_promotion")["fields"])


# -- the SQLite files (their tables are in src/index.py and src/query.py: each column's sentence is its comment there) ------------------------------
DATABASES = [
    {"id": "index", "title": "The search index (index.db)", "where": "data/index.db", "module": "index", "version_const": "SCHEMA_VERSION",
     "written_by": "The overnight run, process_clients and the review app's Search (src/index.py). A copy built from documents.json: delete it and it is built again.",
     "holds_person": True, "exported": False,
     "note": "It holds the text of every document in every case, protected cases included, the client's name and the identifiers read from each document, so it is as sensitive as the cases themselves. "
             "It is protected and backed up with them, and the export of the firm's data leaves it out because it is only a copy."},
    {"id": "query", "title": "The query layer (query.db)", "where": "data/query.db", "module": "query", "version_const": "SCHEMA_VERSION",
     "written_by": "The overnight run, process_clients, the review app when a case's records change (src/query.py). A copy built from the case files: delete it and it is built again.",
     "holds_person": True, "exported": False,
     "note": "One table holds a person's own data on purpose: people, the people index the conflict search reads (every spelling of the name of every person a case "
             "names, their dates of birth, A-Numbers, passport numbers and countries, and where each came from). In every other table no fact's value, no name, date of "
             "birth, address, number read from a document, note, deadline text or document text is in it, and a receipt or passport number "
             "inside a fact key or a deadline's id is replaced by #1, #2. What else is in it that is a person's: the case id (the client's folder name, which can be a name), "
             "the dates of documents (issued, expires), of deadlines (due, never a date of birth plus years) and of filings (mailed_on), and the kind of each document and "
             "whose it is. Its permission is 0600 (owner only), like index.db's, and it is opened read-only by the product and by the firm's own tools."},
    {"id": "find", "title": "Find across the firm's index (find.db)", "where": "data/find.db", "module": "find", "version_const": "SCHEMA_VERSION",
     "written_by": "The overnight run and the review app's follower when an attorney has switched Find across the firm on, and tools/find_index.py (src/find.py). "
                   "A copy built from the case files and the wording library: delete it and it is built again.",
     "holds_person": True, "exported": False,
     "note": "It holds passages of every page, English translation, review decision, case note and approved wording of every case, protected cases included, each "
             "with its embedding (a list of numbers from the firm's own model). Every passage was masked before it was embedded: names, dates, A-Numbers, receipt, "
             "passport and Social Security numbers, street addresses, phone numbers and e-mail addresses are replaced by a label. What is left is the firm's work "
             "product (its notes and decisions) and the case id (which can be a name). Its permission is 0600 (owner only), it is backed up with the cases, a case "
             "recorded destroyed or taken away leaves it at once, and no export carries it, the client's least of all."},
]

# Working files that hold a person's data and are not records (a copy, or work in progress): owner-only (0600, their folder 0700), protected and backed up with the cases, never exported.
WORKING_FILES = [
    {"id": "portal_promotion_partial", "title": "Interrupted reciprocal promotion publication", "where": "Own main/prospect portal client or reserved target case portal_promotion.json.<16hex>.tmp",
     "format": "JSON working file", "written_by": "portal.promotion atomic publication under the existing installation communication gate.",
     "holds": "Same internal operation, contact and identity metadata as the canonical receipt. Current-authorized exact-operation recovery establishes canonical denial before attributed cleanup; corrupt or foreign residues remain unresolved. Pending Q1 refuses either identity before destruction. Completed Q1 removes own subtree; source-only purge leaves the target historical receipt. Export/backup and client release exclude uncommitted writes.", "exported": False},
    {"id": "drive_settings_partial", "title": "Interrupted installation Drive settings writes", "where": "data/drive/settings.json.<process id>.<thread id>.tmp and data/drive/settings.lock",
     "format": "JSON working files and OS lock", "written_by": "src/connectors/drive_settings.py through jobs._write while holding case then settings locks.",
     "holds": "A possibly incomplete full provider/mapping/actor/retry record. Excluded from export and backup. Q1 removes only complete, validated exact-producer temporary records attributable to the purged case; damaged or unassignable residues remain unresolved for operator review.", "exported": False},
    {"id": "communication_partial", "title": "Interrupted communication records and gates", "where": "Own portal client communication_consent.json.<16hex>.tmp, portal_access.json.<16hex>.tmp and communication-attempts/*.json.<16hex>.tmp; data/communication-stop/queue.json.<16hex>.tmp, receipts/<64hex>.json.<16hex>.tmp and destinations/<64hex>.json.<16hex>.tmp; communication.lock and .ingest.lock",
     "format": "JSON working files and OS locks", "written_by": "Communication consent, dispatch, STOP and lifecycle atomic publication under existing installation gates.",
     "holds": "Same internal approval/dispatch/STOP metadata as canonical records. Own-client residue is removed with Q1 subtree. Shared residue is removed only with exact selected store/client association and intact canonical survivor proof; damaged or unassignable residue remains unresolved. Lock files are never deleted. Export/backup/client release exclude uncommitted writes.", "exported": False},
    {"id": "portal_processing_partial", "title": "Interrupted portal intake and job writes",
     "where": "data/jobs/portal-job-<client SHA256>-<nonce>.tmp, data/jobs/done/portal-job-<client SHA256>-<nonce>.tmp; data/portal/queue/portal-queue-<client SHA256>-<nonce>.tmp; client's processing-receipts/*.tmp",
     "format": "JSON working files, possibly incomplete", "written_by": "src/portal/queue_bridge.py and new-format portal_process jobs in src/jobs.py.",
     "holds": "Explicit storage roots, client/generation/policy bindings and processing counters; queue marker residue holds generation/time. Digest-prefixed names permit exact client-scoped Q1 cleanup without parsing partial JSON. Receipt residue is removed with the client subtree. Excluded from export and backup; other clients and legacy/default job temporary files are preserved.",
     "exported": False},
    {"id": "case_assignment_partial", "title": "An interrupted staff assignment write", "where": "data/clients/<case id>/case_assignment.json.part", "format": "JSON working file",
     "written_by": "src/case_assignment.py while atomically replacing assignment state and history under the case lock.",
     "holds": "Staff identity snapshots, optional transfer reasons and operation retry handles; protected like the case. Uncommitted residue, excluded from export/backup and removed with the case by Q1 purge.", "exported": False},
    {"id": "roster", "title": "The lists' saved copy of every case (roster.json)", "where": "data/roster.json", "format": "JSON object",
     "written_by": "The review app and the overnight run (src/review/roster.py). A copy built from the case folders: delete it and it is built again.",
     "holds": "Every case's name, A-Number, date of birth, country of birth, I-360 receipt number and state, the staff e-mail addresses named on each restricted case, the case's office, the "
              "first 160 characters of each unanswered client message, the reason for each retake, and each case's day plan (the steps to a signed packet, who holds each, and the "
              "three to do first, in the words of the packet's lines and the review cards' titles), and each case's staff assignment summary (identity, revision and pending audit status). Restricted cases are in it, marked, so it is as sensitive as the cases themselves.",
     "exported": False},
    {"id": "posture", "title": "The computer's posture (posture.json)", "where": "data/posture.json", "format": "JSON object",
     "written_by": "The review app at its start and the overnight run once a day (src/posture.py). A reading of the computer it was made on: delete it and it is read again.",
     "holds": "For each of five duties (the disk encrypted, the screen locked, a recent backup on another device, the system updated, the firewall on): the result (on, off, not known), the "
              "reason in words, the side that answered (Windows, Windows reached from WSL, Linux), the read-only command that was run and the line it printed, and the time. The line "
              "can hold the folder the last backup was written to and the drive or device the data is on; it holds no client's data. Owner-only (0600).",
     "exported": False},
    {"id": "jobs", "title": "The job queue (jobs/)", "where": "data/jobs/", "format": "JSON, one file for each job, and lock files",
     "written_by": "The review app, the client portal and the job worker (src/jobs.py): a reading waiting for the worker, running, or finished in the last 14 days.",
     "holds": "For each job: the case id (the client's folder name, which can be a name), who started it, the file name of the scan or photo, and how far it is, in words. The scan itself is on the case, "
              "not here. Selected Drive jobs also hold this case's preview (remote folder/document IDs, names, versions, mapping and current actor email). Durable group/phase proofs are catalogued separately and retained until case cleanup.",
     "exported": False},
]

# Files that are never in an export, with why: the dictionary says so and the export test checks it.
NEVER_EXPORTED = [
    ("deployment.json", "It can hold the provider's keys."),
    ("Every key file and secret store (the staff accounts' app-secret key, the Clio vault and its key)", "A key is not data, and a copy of the data must not open the firm's other systems."),
    ("The accounts file's password hashes, authenticator secrets, recovery codes, remembered computers and sessions", "The export carries each account's name, role, active state and date only."),
    ("The portal's sign-in table, outbox and queue", "They hold working sign-in links."),
    ("The portal's own folder for prospects (portal/prospects/)", "Sign-in links and the portal's working copy of what a prospect answered: the answers are copied to the prospect's own folder "
                                                              "(prospects/<id>/answers.json), which is exported."),
    ("The calendar addresses (calendar_feeds.json)", "Each is the hash of a secret that opens a person's calendar; a person makes a new address instead."),
    ("index.db and query.db", "Copies, built again from the cases that are exported."),
    ("find.db (Find across the firm's index)", "A copy, built again from the cases that are exported; it holds the firm's notes and decisions as passages, so it never leaves the firm's machine."),
    ("The caches (overview.json, journey_summary.json, confidentiality.json)", "Built again from the records."),
    ("Backups", "A backup is a copy of the data kept elsewhere."),
    ("The last night's check of the official sources and the backup log (maintenance_status.json, backup_log.json)", "Rebuilt every night, or about backups the firm's IT holds: a restore would carry a stale copy."),
    ("The computer's posture (posture.json)", "A reading of the computer it was made on, read again every night: a copy would describe another machine."),
    ("The boxes the office changes (audit_fill.json and its checkpoint)", "It holds the values of the boxes the office changed on each case, grouped by box; it is counted again from the cases that are exported."),
    ("The exports folder (exports/)","An export of the data is a copy of it: it is not exported again."),
    ("Lock files, half-written files (*.tmp, *.part) and files set aside as damaged (*.damaged)", "Not records."),
    ("The job queue (jobs/) and the review app's saved copy of every case's row (roster.json)", "Working files: scans and case evidence are exported from the case. Job reservations and selected Drive recovery proofs prevent duplicate work; they are backed up and retained until case cleanup, but are not portable case evidence. The saved row is rebuilt by the overnight run."),
]

# What a backup leaves out (src/backups.py: SKIP_NAMES, SKIP_SUFFIXES, EXPORTS; links are never followed). The restore drill (src/backups.py drill) compares a restore with the live install
# file by file and holds a live file against the restore only when this list does not say a backup leaves it out. tests/test_restore_drill.py checks the list against what a backup does.
NOT_BACKED_UP = [
    ("backup_log.json", "It records the backups: a restore would carry a stale copy."),
    ("posture.json", "It is a reading of the computer it was made on (src/posture.py): a restore onto another computer would carry a stale one."),
    ("The exports folder (exports/)", "An export of the data is a copy of it, not data."),
    ("Lock files (*.lock)", "They say a program is running, which a restored copy is not."),
    ("Half-written files (*.tmp, *.part)", "A write in progress: the file it was making is in the backup as it was."),
    ("SQLite's -wal and -shm files", "A database is copied whole with SQLite's own backup, which is consistent while the app runs."),
]
NOT_BACKED_UP_PATTERNS = ["backup_log.json", "posture.json", "exports/*", "*.lock", "*.tmp", "*.part", "*-wal", "*-shm"]


def not_backed_up(rel: str) -> bool:
    """Whether a backup leaves out the file at this path inside the data folder (the catalog's NOT_BACKED_UP)."""
    import fnmatch

    base = rel.rsplit("/", 1)[-1]
    return any(fnmatch.fnmatchcase(rel, p) or fnmatch.fnmatchcase(base, p) for p in NOT_BACKED_UP_PATTERNS if "/" not in p) or any(
        fnmatch.fnmatchcase(rel, p) for p in NOT_BACKED_UP_PATTERNS if "/" in p)


# What a file in each place may be named for the walk and the export (the same name-by-name patterns as `files`): the files that are never exported.
NEVER_PATTERNS = {
    "firm": ["deployment.json", "*.key", "maintenance_status.json", "backup_log.json", "posture.json", "audit_fill.json", "audit_fill.partial.json", "index.db", "index.db-*", "query.db", "query.db-*", "find.db", "find.db-*", "exports/*", "clio/secrets.enc",
             "clio/vault.key", "clio/*.lock", "drive/settings.lock", "drive/settings.json.*.tmp", "*.damaged", "*.lock", "*.tmp", "*.part", "inbox/.reading", "portal/auth.json", "portal/outbox.jsonl", "portal/queue/*", "calendar_feeds.json",
             "jobs/*", "jobs/done/portal-job-*.tmp", "jobs/drive-receipts/*.lock", "jobs/drive-receipts/*.tmp", "roster.json",
             "portal/prospects/auth.json", "portal/prospects/queue/*", "portal/prospects/clients/*/profile.json", "portal/prospects/clients/*/answers.json",
             "portal/prospects/clients/*/events.jsonl", "portal/prospects/clients/*/engagement.json"],
    "case": ["overview.json", "journey_summary.json", "confidentiality.json", "*.tmp", "*.part", "*.lock", "staff-upload-receipts/*.tmp"],
    "portal": ["*.tmp", "*.part", "*.lock", "uploads/*.part", "capture-evidence/*.part", "upload-receipts/*.tmp", "processing-receipts/*.tmp"],
}


def by_id(record_id: str) -> dict[str, Any]:
    return next(r for r in RECORDS if r["id"] == record_id)


def patterns(area: str, exported_only: bool = True) -> list[str]:
    """The file patterns of one area (case, portal, firm, logs) that an export includes."""
    return [p for r in RECORDS if r["area"] == area and (r["exported"] or not exported_only) for p in r["files"]]


def describe(area: str, path: str) -> str:
    """One line saying what a file in an export is: the title of the record that lists it."""
    for r in RECORDS:
        if (r["area"] == area or (area == "firm" and r["area"] == "logs")) and any(_fits(path, p) for p in r["files"]):
            return r["title"]
    return "A file"


def record_of(area: str, path: str) -> dict[str, Any] | None:
    """The record that lists a file (area: case, portal, firm or logs; path: inside the case's folder, the portal client's folder or the data folder), or None."""
    for r in RECORDS:
        if (r["area"] == area or (area == "firm" and r["area"] == "logs")) and any(_fits(path, p) for p in r["files"]):
            return r
    return None


def _fits(rel: str, pattern: str) -> bool:
    import fnmatch

    a, b = rel.split("/"), pattern.split("/")
    return len(a) == len(b) and all(fnmatch.fnmatchcase(x, y) for x, y in zip(a, b))


def coverage(area: str, rel: str) -> str | None:
    """"listed" when the dictionary lists the file (area: case, portal or firm, rel: the path inside the case's folder, the portal client's folder or the data folder),
    "never" when it is one the dictionary says is never exported, None for a file nothing accounts for (tests/test_export_everything.py walks a world and fails on one)."""
    names = [r for r in RECORDS if r["area"] == area or (area == "firm" and r["area"] == "logs")]
    if any(_fits(rel, f) for r in names if r["exported"] for f in r["files"]):
        return "listed"
    if any(_fits(rel, f) for r in names if not r["exported"] for f in r["files"]) or any(_fits(rel, f) for f in NEVER_PATTERNS.get(area, [])):
        return "never"
    return None
