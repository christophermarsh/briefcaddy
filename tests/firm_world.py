"""A made-up firm for the data-openness tests (tests/test_query_layer.py, test_export_everything.py, test_events.py): case folders with the records the
product keeps (the ones docs/data_dictionary.md lists), a portal folder, staff accounts, settings, a ledger. Built by writing the records directly, never by
processing documents, so two thousand cases take seconds. Everyone here is made up ("Ana Clara Exemplo Souza", "Rosa Exemplo").
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path

CITIES = ("Boston", "Miami", "Orlando", "Worcester")
TYPES = ("passport", "birth_certificate", "advance_parole", "i94", "sij_order", "police_clearance")
QUALITIES = ("readable", "blurry", "readable", "cut_off", "unknown")
TRACKS = ("sij", "family", "asylum", "vawa")


def fact(key: str, value, tier: int = 1, sources: int = 1, status: str = "resolved") -> dict:
    return {"fact_key": key, "tier": tier, "status": status, "value": value, "derived_by": None, "derived_from": [], "resolution": None, "missing_reason": None, "review": None,
            "sources": [{"doc_id": f"d{n}.pdf", "doc_type": "passport", "raw_value": str(value), "normalized_value": value, "confidence": 0.9, "extracted_at": "2026-09-01T10:00:00+00:00",
                         "from_facts": []} for n in range(sources)]}


def document(case: str, n: int, *, type_: str | None = None, person: str = "applicant", confidential=None, expires: str | None = None) -> dict:
    kind = type_ or TYPES[n % len(TYPES)]
    doc_id = hashlib.sha256(f"{case}/{n}".encode()).hexdigest()[:16]
    return {"id": doc_id, "files": [f"{kind}-{n}.pdf"], "doc_ids": [f"{kind}-{n}.pdf"], "pages": [1], "type": kind, "confidence": 0.9, "person": person, "person_set_by": None,
            "person_basis": "named", "language": "en", "language_basis": "text", "language_country": None, "issued": "2025-01-15", "expires": expires, "issued_kind": "issued",
            "expires_kind": "expires" if expires else None, "dates_read": True, "identifiers": {"a_number": "", "receipt": "", "passport": "", "ssn_last4": ""},
            "quality": QUALITIES[n % len(QUALITIES)], "quality_set_by": None, "quality_basis": "measured", "quality_measures": None, "hash": doc_id * 4, "source": "folder",
            "added": "2026-09-01T10:00:00+00:00", "roles": [], "found": {}, "tags": [], "confidential": confidential, "text": f"made-up text {n}", "translated": None}


def make_case(clients: Path, case: str, *, docs: int = 3, facts: int = 6, decisions: int = 2, filings: int = 0, restricted: str | None = None, expires: str | None = None) -> Path:
    """One case folder with a fact graph, document records, decisions, a status record and meta. restricted: "1367" or "208.6" gives it the law's marks."""
    d = clients / case
    d.mkdir(parents=True, exist_ok=True)
    keys = ["applicant.given_name", "applicant.family_name", "applicant.date_of_birth", "applicant.country_of_birth", "applicant.physical_state", "applicant.a_number"]
    graph = {"client_id": case, "facts": {k: fact(k, "MADE UP " + k.split(".")[-1].upper(), tier=1 if i % 3 else 3, sources=1 + i % 2) for i, k in enumerate(keys[:facts])}}
    graph["facts"]["applicant.missing_thing"] = fact("applicant.missing_thing", None, status="missing", sources=0)
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"client_id": case, "source_folder": str(d / "source"), "processed_at": "2026-10-01T02:00:00+00:00", "classifications": {}}), encoding="utf-8")
    records = [document(case, n, confidential=restricted, expires=expires if n == 0 else None) for n in range(docs)]
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": records}), encoding="utf-8")
    log = {}
    for n in range(decisions):
        item = {"id": f"item-{n}", "kind": "reading", "level": "review", "title": "A made-up item", "group": "g", "facts": [keys[n % len(keys)]]}
        step = {"action": "confirm", "values": {}, "reviewer": "Jane Paralegal" if n % 2 else "Sam Attorney", "note": "made-up note", "at": f"2026-10-0{1 + n % 3}T14:00:00+00:00"}
        log[item["id"]] = step | {"item": item, "history": [step]}
    if decisions:
        (d / "decisions.json").write_text(json.dumps(log), encoding="utf-8")
    status = {"filings": [{"filing": "i485", "title": "Form I-485", "mailed_on": "2026-09-01", "carrier": "USPS", "tracking": "9400111899223344556677", "by": "Sam Attorney",
                           "at": "2026-09-01T15:00:00+00:00"} for _ in range(filings)]} if filings else {}
    if restricted:
        status["journey"] = {"track": {"value": "vawa" if restricted == "1367" else "asylum", "by": "Sam Attorney", "at": "2026-09-01T15:00:00+00:00", "note": None}}
    if status:
        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    (d / "source").mkdir(exist_ok=True)
    (d / "source" / "passport-0.pdf").write_bytes(b"%PDF-1.4 made up passport")
    return d


def make_marked_case(clients: Path, case: str = "zelda-markerton") -> Path:
    """A case named for its client (as the front desk names a folder), with a receipt and a passport number inside fact keys, and a timeline with a hearing, a move, a request
    from USCIS, an expiring document and a deadline at the client's age: every place a person's data can get into a key, an id or a deadline's text."""
    d = make_case(clients, case, decisions=1, expires="2032-01-10")
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    for key, value in {"folder.uscis_case.IOE0999000123.approval_20250820": "APPROVAL 08/20/2025", "folder.passport.XX0001234": "XX0001234"}.items():
        graph["facts"][key] = fact(key, value)
    (d / "fact_graph.json").write_text(json.dumps(graph), encoding="utf-8")
    decisions = json.loads((d / "decisions.json").read_text(encoding="utf-8"))
    step = {"action": "confirm", "values": {}, "reviewer": "Jane Paralegal", "note": "n", "at": "2026-10-02T14:00:00+00:00"}
    item = {"id": "folder.passport.XX0001234", "kind": "reading", "level": "review", "title": "t", "group": "g", "facts": ["folder.passport.XX0001234"]}
    decisions[item["id"]] = step | {"item": item, "history": [step]}
    (d / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    deadlines = [
        {"id": "hearing.2026-12-01.0", "date": "2026-12-01", "what": "Master calendar hearing at 9:00 AM (1 EXAMPLE PLAZA, ROOM 100, BOSTON, MA 02110)", "owner": "client"},
        {"id": "hearing.2026-12-01.0.filings", "date": "2026-11-20", "what": "Immigration court (COURT-MARKER Boston, Judge JUDGE-MARKER): filings due", "owner": "attorney"},
        {"id": "moved.2026-09-01.0.ar11", "date": "2026-09-11", "what": "Tell USCIS of the move (Form AR-11)", "owner": "paralegal"},
        {"id": "IOE0999000123.rfe.2026-09-20", "date": "2026-09-20", "what": "Answer the request for evidence on I-360 IOE0999000123", "owner": "attorney"},
        {"id": "age_21", "date": "2027-03-13", "what": "File the I-360 before the 21st birthday", "owner": "attorney"},
        {"id": "doc.passport", "date": "2032-01-10", "what": "Passport expires 01/10/2032", "owner": "paralegal", "expiry": {"document": {"type_name": "Passport"}}},
    ]
    (d / "journey_summary.json").write_text(json.dumps({"signature": [1], "row": {"track": "sij", "track_name": "Special Immigrant Juvenile", "stage": "i360_ready",
                                                                                   "stage_name": "I-360 ready", "deadlines": deadlines}}), encoding="utf-8")
    return d


def make_cases(clients: Path, n: int) -> list[str]:
    """n ordinary cases, a few with filings and a few with a document that expires, by a pattern (nothing random: two runs build the same firm)."""
    ids = []
    for i in range(n):
        case = f"case-{i:05d}"
        make_case(clients, case, docs=2 + i % 4, decisions=i % 4, filings=1 if i % 7 == 0 else 0, expires="2027-01-01" if i % 5 == 0 else None)
        ids.append(case)
    return ids


def make_firm(root: Path, *, cases: int = 3) -> dict[str, Path]:
    """A made-up installation under root/data: cases (one restricted by law), a portal client, staff accounts, settings, policies, the upkeep log, a ledger."""
    data = root / "data"
    clients, portal = data / "clients", data / "portal"
    clients.mkdir(parents=True)
    for i in range(cases):
        make_case(clients, f"ana-exemplo-{i}" if i else "ana-exemplo", filings=1 if i == 0 else 0)
    make_case(clients, "rosa-exemplo", restricted="1367")
    (clients / "ana-exemplo" / "packet.pdf").write_bytes(b"%PDF-1.4 made up packet")
    (clients / "ana-exemplo" / "i485_filled.pdf").write_bytes(b"%PDF-1.4 made up form")
    (clients / "ana-exemplo" / "packet_i360.json").write_text("{}", encoding="utf-8")
    (clients / "ana-exemplo" / "flag_report.txt").write_text("nothing", encoding="utf-8")
    for cache in ("overview.json", "journey_summary.json", "confidentiality.json"):  # caches: never in an export
        (clients / "ana-exemplo" / cache).write_text("{}", encoding="utf-8")
    (clients / "ana-exemplo" / "secret.key").write_text("NOT DATA", encoding="utf-8")  # a file nothing lists: never in an export
    pa = portal / "clients" / "ana-exemplo"
    (pa / "uploads").mkdir(parents=True)
    (pa / "profile.json").write_text(json.dumps({"id": "ana-exemplo", "name": "Ana Clara Exemplo Souza", "email": "ana@example.com", "language": "pt", "status": "started"}), encoding="utf-8")
    (pa / "answers.json").write_text(json.dumps({"applicant.given_name": "ANA"}), encoding="utf-8")
    (pa / "events.jsonl").write_text('{"at": "2026-09-01T10:00:00+00:00", "event": "signed_in"}\n', encoding="utf-8")
    (pa / "uploads" / "passport-0123456789abcdef.pdf").write_bytes(b"%PDF-1.4 made up upload")
    (portal / "auth.json").write_text(json.dumps({"links": {"ab" * 32: {}}, "sessions": {}}), encoding="utf-8")
    (portal / "outbox.jsonl").write_text('{"body": "https://portal.example/?t=WORKING-LINK"}\n', encoding="utf-8")
    users = data / "review_users.json"
    users.write_text(json.dumps({"users": {"sam@firm.example": {"name": "Sam Attorney", "role": "attorney", "active": True, "created_at": "2026-01-01T09:00:00+00:00",
                                                                  "salt": "cd34" * 8, "hash": "ab12" * 16, "totp": {"secret": "SEALED-SECRET", "recovery": ["RECOVERY-HASH"]}}},
                                 "sessions": {"ef56" * 16: {"email": "sam@firm.example"}}}), encoding="utf-8")
    users.with_name("review_users_access.jsonl").write_text('{"at": "2026-10-01T09:00:00+00:00", "event": "signed_in", "email": "sam@firm.example"}\n', encoding="utf-8")
    users.with_name("review_views.jsonl").write_text('{"at": "2026-10-01T09:01:00+00:00", "email": "sam@firm.example", "client": "ana-exemplo", "kind": "case"}\n', encoding="utf-8")
    (data / "settings.json").write_text(json.dumps({"firm": {"values": {"firm_name": "Exemplo Law"}, "updated_by": "Sam Attorney", "updated_at": "2026-10-01T09:00:00+00:00", "history": []}}), encoding="utf-8")
    (data / "policies_firm.json").write_text(json.dumps({"edits": {}}), encoding="utf-8")
    (data / "rules_approved.json").write_text("{}", encoding="utf-8")
    (data / "maintenance_log.json").write_text("{}", encoding="utf-8")
    (data / "deployment.json").write_text(json.dumps({"provider": {"name": "X", "key": "PROVIDER-KEY"}}), encoding="utf-8")  # never in an export
    # what the product keeps beside the cases: the overnight run's records, the notice inbox, who has seen Getting started, the Clio connection, the references
    (data / "batch_state.json").write_text(json.dumps({"ana-exemplo": {"status": "done", "at": "2026-10-01T03:00:00-04:00"}}), encoding="utf-8")
    (data / "batch_progress.json").write_text(json.dumps({"started_at": "2026-10-01T02:00:00-04:00", "total": 1, "done": 1}), encoding="utf-8")
    (data / "batch_log.jsonl").write_text('{"started_at": "2026-10-01T02:00:00-04:00", "done": 1, "failed": 0}\n', encoding="utf-8")
    (data / "batch_report.txt").write_text("Overnight run: processed 1\n", encoding="utf-8")
    (data / "case_status_run.json").write_text(json.dumps({"at": "2026-10-01T04:00:00-04:00", "configured": False}), encoding="utf-8")
    (data / "accuracy_history.jsonl").write_text('{"night": "2026-10-01", "boxes": 10}\n', encoding="utf-8")
    (data / "getting_started.json").write_text(json.dumps({"seen": {"sam@firm.example": "2026-10-01T09:00:00-04:00"}}), encoding="utf-8")
    (data / "maintenance_status.json").write_text(json.dumps({"at": "2026-10-01T03:30:00-04:00", "results": {}}), encoding="utf-8")  # rebuilt every night: never in an export
    (data / "backup_log.json").write_text(json.dumps({"last_backup": "2026-10-01"}), encoding="utf-8")  # about the firm's backups: never in an export
    for sub in ("waiting", "not_ours", "originals"):
        (data / "inbox" / sub).mkdir(parents=True)
    (data / "inbox" / "queue.json").write_text("[]", encoding="utf-8")
    (data / "inbox" / "inbox_log.jsonl").write_text('{"event": "routed"}\n', encoding="utf-8")
    (data / "inbox" / "waiting" / "n-1.pdf").write_bytes(b"%PDF-1.4 made up notice")
    (data / "inbox" / ".reading").write_text("lock", encoding="utf-8")  # a lock file: never in an export
    (data / "clio").mkdir()
    (data / "clio" / "state.json").write_text(json.dumps({"matters": {"1": {"case": "ana-exemplo"}}}), encoding="utf-8")
    (data / "clio" / "settings.json").write_text(json.dumps({"on": True}), encoding="utf-8")
    (data / "clio" / "secrets.enc").write_bytes(b"SEALED-CLIO-TOKEN")  # the vault and its key: never in an export
    (data / "clio" / "vault.key").write_text("CLIO-VAULT-KEY", encoding="utf-8")
    (data / "reference").mkdir()
    (data / "reference" / "case-a.pdf").write_bytes(b"%PDF-1.4 made up hand-filled form")
    (data / "reference" / "case-a.marks.json").write_text("{}", encoding="utf-8")
    (clients / "sync_state.json").write_text(json.dumps({"clients": {}}), encoding="utf-8")
    ana = clients / "ana-exemplo"
    for folder, name in (("translations", "translation-0123456789abcdef.pdf"), ("declarations", "declaration-i485.pdf")):
        (ana / folder).mkdir()
        (ana / folder / name).write_bytes(b"%PDF-1.4 made up " + folder.encode())
    (ana / "declarations.json").write_text(json.dumps({"filings": {}}), encoding="utf-8")
    (ana / "translations.json").write_text(json.dumps({"version": 1, "documents": {}}), encoding="utf-8")
    (ana / "packet_review_bundle.pdf").write_bytes(b"%PDF-1.4 made up bundle")
    (ana / "packet_review_bundle.json").write_text("{}", encoding="utf-8")
    return {"data": data, "clients": clients, "portal": portal, "users": users}
