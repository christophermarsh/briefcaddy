"""What a paralegal does every day, on a screen instead of the command line (docs/design_plan.md Part 10.3): add a client, send
the portal link, add a scan to a case, choose the questionnaire. Each of these calls the code the command line uses
(src/portal/admin.py, src/portal/store.py, src/inbox.py) rather than a copy of it, and records who did it and when.

The review app (review/server.py) holds its lock around each call and passes the signed-in person's name; the role rules
(who may show a sign-in link) are decided there.
"""

from __future__ import annotations

import io
import re
import unicodedata
from pathlib import Path
from typing import Any

import clock
import events
import restricted

CHANNELS = ("email", "sms", "whatsapp")
CHANNEL_NAMES = {"email": "email", "sms": "text messages", "whatsapp": "WhatsApp messages"}
QUESTIONNAIRE_NAMES = {"i485": "Green card (I-485, SIJ and family)", "n400": "Citizenship (N-400)"}
QUESTIONNAIRE_NAMES["parole"] = "Humanitarian parole for someone abroad (I-131 and I-134)"
# the tracks journey.mark accepts, in the words the case page uses
TRACKS = [("sij", "Special Immigrant Juvenile"), ("family", "Family-based"), ("naturalization", "Citizenship"), ("asylum", "Asylum"),
          ("caa", "Cuban Adjustment Act"), ("vawa", "VAWA self-petition"), ("t_visa", "T visa (trafficking victim)"),
          ("u_visa", "U visa (crime victim)"), ("daca", "Deferred Action for Childhood Arrivals (DACA)")]
_EMAIL = re.compile(r"[^@\s]+@[^@\s]+\.[^@\s]{2,}")


def questionnaires() -> list[dict[str, str]]:
    """The questionnaires the portal has a bank for: the green card's, and each filing of its own (portal/bank.py FILING_BANKS)."""
    from portal.bank import FILING_BANKS

    # the first-contact questions are a prospect's (src/prospects.py), never a choice for a client
    return [{"id": f, "name": QUESTIONNAIRE_NAMES.get(f, f.upper())} for f in ("i485", *FILING_BANKS) if f != "first_contact"]


def form_options(offices_: list[dict[str, Any]]) -> dict[str, Any]:
    """What the Add a client form offers: the portal's languages, the offices, the questionnaires, the tracks, and the conflict search's choices
    (src/conflicts.py: the decisions and their DRAFT words, the roles the other side can have)."""
    from portal.bank import language_names

    import conflicts

    return {"conflict": {"decisions": [{"id": k, "name": v} for k, v in conflicts.DECISIONS.items()],
                         "roles": [{"id": k, "name": v} for k, v in conflicts.PARTY_ROLES.items()], "waiver": conflicts.WAIVER},
            "languages": [{"id": k, "name": v} for k, v in language_names().items()],
            "offices": [{"id": o["id"], "name": o["name"]} for o in offices_] if len(offices_) > 1 else [],
            "questionnaires": questionnaires(), "channels": [{"id": c, "name": CHANNEL_NAMES[c]} for c in CHANNELS],
            "contact_help": "Use a distinct safe email for each person who needs the portal. Full phone numbers start with + and a country code; local/shared numbers remain office contact only. Automated phone access requires current service-message signoff and separate verification of a unique full number.",
            # a protected kind (VAWA, T, U, asylum): the form says the client is restricted at once and the invitation tick is off
            "tracks": [{"id": k, "name": v} | ({"protected": restricted.law_words(restricted.kind_law(k))} if restricted.kind_law(k) else {}) for k, v in TRACKS]}


def _slug(name: str) -> str:
    text = unicodedata.normalize("NFKD", name).encode("ascii", "ignore").decode().lower()
    return re.sub(r"[^a-z0-9]+", "-", text).strip("-")[:40].strip("-")


def new_client_id(store, data_root: Path, name: str) -> str:
    """"Ana Clara Exemplo Souza" -> ana-clara-exemplo-souza; -2, -3 when a client or a case folder has it already; "client-" in front of a name that would begin like a prospect's id."""
    from portal.store import CLIENT_ID
    from portal.communication_consent import gate
    from portal.contact_transitions import require_complete_enrollments
    try:
        scope = store.communication_scope()
    except ValueError:
        scope = None
        if Path(store.root).parent.name == "data":
            raise  # inherited foreign installation is not a utility-store exemption
    if scope is not None:
        if Path(data_root).resolve() != scope.cases:
            raise ValueError("Client allocation belongs to another installation.")
        with gate(scope):
            require_complete_enrollments(scope)

    base = _slug(name)
    if not CLIENT_ID.fullmatch(base):
        raise ValueError("Enter the client's full name.")
    import conflicts
    import prospects

    if base.startswith(prospects.PREFIX):  # "prospect-..." is a prospect's form of id (src/prospects.py): a client never has one, so no row of a prospect can be a client's
        base = "client-" + base
    clients = set(store.clients())
    folders = {p.name for p in Path(data_root).iterdir() if p.is_dir()}
    out, n = base, 2
    # a folder an add left part way (the conflict check and no client: src/conflicts.py orphan) is taken up again under its own id
    while out in clients or (prospects.folder(data_root) / out).exists() or (out in folders and not conflicts.orphan(Path(data_root) / out, portal=store.root)):
        out, n = f"{base}-{n}", n + 1
    return out


def add_client(store, data_root: Path, body: dict[str, Any], by: str, role: str | None = None, may_see=None, carry_from: Path | None = None, *, actor_email=None) -> dict[str, Any]:
    """Serialize allocation through case/profile publication across app processes."""
    with store._communication_gate():
        if carry_from is not None:
            from portal.promotion import create_target
            scope = store.communication_scope()
            if (Path(data_root).resolve() != scope.cases or not isinstance(actor_email, str)
                    or Path(carry_from).resolve() != scope.data / "prospects" / Path(carry_from).name):
                raise PermissionError("Prospect promotion requires the current authorized staff principal and canonical source.")
            return create_target(scope, store, Path(carry_from).name, body, actor_email=actor_email)
        return _add_client(store, data_root, body, by, role, may_see, carry_from)


def _add_client(store, data_root: Path, body: dict[str, Any], by: str, role: str | None = None, may_see=None, carry_from: Path | None = None) -> dict[str, Any]:
    """A new client in the portal, as `admin.py import` makes one: the profile (name, phone, email, language, consent per channel), the
    questionnaire (profile "filing") and the office; the portal then holds their answers and the case folder follows when they
    are processed. The case's track, when known, waits on the profile for the case folder (portal/engine.py applies it). Recorded
    with who and when. The client is not told anything yet: Invite does that.

    Never without the conflict search (src/conflicts.py): body["conflict"] holds the search the person ran for this name and what they decided
    ({search, decision, reason}). "declined": the client is not added (the decision is kept in the conflict log). "undecided": the client is added
    and held out of invitations until an attorney decides. role, may_see: who is adding (a waiver is an attorney's; "no conflict" over a hit on a
    case this person may not open is refused)."""
    from portal.bank import language_code, languages

    import conflicts
    import offices

    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: adding a client records who added them.")
    name = re.sub(r"\s+", " ", str(body.get("name") or "")).strip()
    if len(name) < 2:
        raise ValueError("Enter the client's full name.")
    from portal.contact_access import enrollment_contacts
    try:
        scope = store.communication_scope()
    except ValueError:
        if store.root.absolute().parent.name == "data":
            raise
        scope = None  # historical utility stores cannot dispatch/authenticate
    contact = enrollment_contacts(scope, body.get("email") or "", body.get("phone") or "")
    email, phone = contact["email"], contact["phone"]
    language = language_code(str(body.get("language") or "pt"))
    if language is None:
        raise ValueError(f"Choose the client's language: {', '.join(languages())}.")
    consent = {c: bool((body.get("consent") or {}).get(c)) for c in CHANNELS}
    for channel, address in (("email", email), ("sms", phone), ("whatsapp", phone)):
        if consent[channel] and not address:
            raise ValueError(f"The client agreed to receive {CHANNEL_NAMES[channel]}, but there is no {'email address' if channel == 'email' else 'phone number'} to send to.")
    filing = str(body.get("filing") or "i485")
    if filing not in {q["id"] for q in questionnaires()}:
        raise ValueError("Choose a questionnaire from the list.")
    office = str(body.get("office") or "").strip()
    if office and offices.by_id(office) is None:
        raise ValueError("Choose the office from the list.")
    track = str(body.get("track") or "").strip()
    if track and track not in {t[0] for t in TRACKS}:
        raise ValueError("Choose the case's track from the list.")
    if scope is None and email and store.find_by_contact(email):
        raise ValueError("That contact cannot be used for a new portal identity.")
    # the conflict search and what the person decided, before anything of the client exists
    search, decision = conflicts.for_new_client(data_root, body.get("conflict"), name, by=by, role=role, may_see=may_see)
    if decision["decision"] == "declined":
        conflicts._log_decision(Path(data_root), decision, None, "add")
        return {"declined": True, "name": name, "note": conflicts.DECLINED}
    client_id = new_client_id(store, data_root, name)
    folder = Path(data_root) / client_id
    # an earlier add of this name that stopped part way left this folder (new_client_id takes its id up again): what it holds goes on, said so
    taken_up = folder.exists()
    restricted_before = taken_up and restricted.is_restricted(folder)
    # VAWA, T, U or asylum (8 U.S.C. 1367, 8 CFR 208.6): restricted before the client exists anywhere else, so no list, count or
    # message ever has them unprotected (src/restricted.py protect_new). The record first: if adding stops part way, the case
    # folder that names the client is already closed.
    found = restricted.kind_law(track) if track else None
    if found:
        restricted.protect_new(folder, found, dict(TRACKS)[track], "Added as", by)
    if carry_from is not None:  # added from a first call that was restricted (src/prospects.py): the case is closed the same way before anything else of it is written
        restricted.carry_over(carry_from, folder, by)
    conflicts.record_new(folder, search, decision)  # the conflict check on the new case, and the client in the people index
    setup = {"filing": filing, "added_by": by, "added_at": clock.stamp(),
             **({"office": office} if office else {}), **({"track": track} if track else {})}
    store.add_client(client_id, name, phone=phone, email=email, language=language, consent=consent, by=by, initial_profile=setup)
    out = {"id": client_id, "name": name, "filing": filing, **{k: contact[k] for k in ("phone_access", "phone_note") if k in contact}}
    if found:
        out |= {"restricted": True, "law": restricted.law_words(found), "note": restricted.INVITE_HELD}
    elif carry_from is not None and restricted.is_restricted(folder):  # closed because the first call it began as was (src/prospects.py)
        out |= {"restricted": True, "law": "", "note": restricted.INVITE_HELD, "carried_restriction": True}
    elif restricted_before:  # erring toward closed: the earlier record's restriction stands, and the person adding is told why
        out |= {"restricted": True, "inherited": True, "law": "", "note": INHERITED}
        events.record("access", "kept", "Kept the restriction an earlier record for this name had: an add had stopped part way", case_dir=folder, who=by)
    if taken_up:
        out["taken_up"] = TAKEN_UP
        events.record("conflicts", "resumed", "Took up an earlier conflict check for this name, left by an add that stopped part way", case_dir=folder, who=by)
    return out | ({"held": True, "held_note": conflicts.HELD} if decision["decision"] == "undecided" else {})


# said when Add a client takes up a folder an earlier add of the same name left (src/conflicts.py orphan)
INHERITED = ("Added as a restricted client, because an earlier record for this name was restricted; an attorney can lift it. Only attorneys, and the "
             "staff they name, can open it, and nothing is sent to the client by itself.")
TAKEN_UP = "An earlier add of this name had stopped part way: its conflict check goes on under this client, with the earlier decisions kept."


def invite(store, client_id: str, by: str, again: bool = False, cases_root: Path | None = None) -> dict[str, Any]:
    """The portal link by every channel the client agreed to (the same send as `admin.py invite`: the message carries the firm's name and
    the link, no case details). Returns what really happened ("Queued, no mail server configured" while there is no provider).
    cases_root: the case folders, where a restricted case's record is (the review app passes its own); a restricted client gets nothing."""
    from portal.admin import send
    from portal.notify import Notifier, delivery

    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: the invitation records who sent it.")
    profile = store.profile(client_id)
    if profile.get("status") == "submitted":
        raise ValueError("This client already sent their questionnaire: there is nothing to invite them to.")
    if cases_root is not None:  # a restricted case gets its own answer below (the office reaches the client by hand); else a conflict check waiting
        import conflicts

        case = Path(cases_root) / client_id
        if conflicts.held(case) and not restricted.is_restricted(case):
            raise ValueError("Not sent. " + conflicts.hold_words(case))
    sent = send(store, Notifier(store.root / "outbox.jsonl", cases_root=cases_root, store=store), client_id, "invite", by=by, again=again)
    result = delivery(sent)
    attempt = {"by": by, "at": clock.stamp(), "result": result}
    store.update_profile(client_id, last_invite_attempt=attempt)
    # Only accepted delivery establishes a successful invitation. Preserve an
    # earlier success when a later explicit attempt is held, queued or uncertain.
    if any(row.get("result") == "sent" for row in sent):
        store.update_profile(client_id, last_invite_at=attempt["at"], last_invite_by=by, last_invite=result)
        store.log(client_id, "invited", {"by": by})
    return {"sent": sent, "delivery": result}


def show_link(store, client_id: str, by: str, base_url: str, cases_root: Path | None = None, *, actor_email: str | None = None) -> dict[str, Any]:
    """A fresh sign-in link on the screen, for a client sitting next to the paralegal. It works once, for 72 hours, like any other, and
    showing it is logged on the client's record (who, when): it is as good as a password for that long."""
    from portal.store import LINK_TTL

    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: showing a client's link records who did it.")
    held = None
    if cases_root is not None:  # the conflict check (src/conflicts.py): no link at all for a declined client; a held one is said first
        import conflicts

        decision = (conflicts.record_of(Path(cases_root) / client_id) or {}).get("decision") or {}
        if decision.get("decision") == "declined":
            day = clock.parse(decision.get("at"))
            raise ValueError(f"This client was declined{' on ' + f'{day:%m/%d/%Y}' if day else ''} after the conflict search; change the decision first "
                             "(Settings, Conflict checks).")
        if decision.get("decision") == "undecided":
            held = ("This client is held: the conflict check is waiting for an attorney's decision. Record the decision first (Settings, Conflict "
                    "checks) unless you mean to hand the link over anyway.")
        import engagement

        if engagement.end_info(Path(cases_root) / client_id) or store.profile(client_id).get("declined_on"):  # declined, withdrawn, transferred, closed
            raise ValueError("This client's case with the office has ended: no sign-in link is made. An attorney opens the case again first "
                             "(Agreement and closing).")
    from portal.communication_consent import manual_bootstrap
    if not actor_email:
        raise PermissionError("A signed-in staff principal is required for assisted consent access.")
    token = manual_bootstrap(store.communication_scope(), store, client_id, actor_email=actor_email)
    store.log(client_id, "link_shown", {"by": by})
    return {"url": f"{base_url.rstrip('/')}/l/{token}", "hours": int(LINK_TTL.total_seconds() // 3600)} | ({"held_note": held} if held else {})


def questionnaire_name(filing: str) -> str:
    """"Green card (I-485, SIJ and family)" for "i485": the questionnaire's name as the screens write it."""
    return next((q["name"] for q in questionnaires() if q["id"] == filing), filing)


def change_warning(store, client_id: str) -> str | None:
    """What the screen asks before changing the questionnaire of a client who has started answering, else None. The client's saved
    answers are never deleted (one answers file, src/portal/store.py): those of the old questionnaire stay with it."""
    profile = store.profile(client_id)
    if not store.answers(client_id):
        return None
    before = questionnaire_name(profile.get("filing") or "i485")
    return (f"{profile.get('name') or 'This client'} has started answering the {before} questionnaire. Change it anyway? "
            f"Their saved answers are kept with the {before} questionnaire and come back if it is changed back; the new one starts with "
            "the answers the two share (name, date of birth, addresses and the like).")


def set_questionnaire(store, client_id: str, filing: str, by: str, confirmed: bool = False) -> dict[str, Any]:
    """Which questionnaire the client answers in the portal (admin.py `filing`), with who changed it and from what. A client who
    has started answering is changed only once the person confirmed (change_warning: the screen asks first)."""
    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every change records who made it.")
    if filing not in {q["id"] for q in questionnaires()}:
        raise ValueError("Choose a questionnaire from the list.")
    profile = store.profile(client_id)
    before = profile.get("filing") or "i485"
    if before == filing:
        raise ValueError("The client already answers that questionnaire.")
    if profile.get("status") == "submitted":
        raise ValueError("This client already sent their questionnaire, so it can't be changed here: their answers are for the one they answered. "
                         "Ask the attorney how to handle the change.")
    warning = change_warning(store, client_id)
    if warning and not confirmed:
        raise ValueError(warning)
    store.update_profile(client_id, filing=filing, filing_changed={"by": by, "at": clock.stamp(), "from": before, "to": filing})
    store.log(client_id, "questionnaire_changed", {"by": by, "from": before, "to": filing})
    store.enqueue(client_id)  # the client's list of things to do follows the new questionnaire at the next pass
    return {"filing": filing, "from": before}


# -- documents added by the office ------------------------------------------------------------------


def _scan_name(day: str, original: str) -> str:
    stem = re.sub(r"[^A-Za-z0-9._-]+", "_", Path(original or "scan").stem).strip("._") or "scan"
    return f"scan_{day}_{stem[:60]}.pdf"


def stage_upload(clients_root: Path, store, client_id: str, original: str, data: bytes, by: str) -> dict[str, Any]:
    """The quick part of adding a scan: checked as the portal checks a client's (PDF, JPEG or PNG by its first bytes, 15 MB) and stored in the
    case's own document folder as the office's scan (source "folder": never the client's upload, so a hard-to-read one is not asked of the client
    again). Returns what read_staff_upload needs ({name, pages, by, before, meta_mtime, newest, case: whether the case exists yet}); nothing is read here,
    so the review app answers at once and the job worker (src/jobs.py) reads."""
    import inbox

    by = (by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every document added records who added it.")
    data, n_pages = _prepare_scan(data)

    client_dir = Path(clients_root) / client_id
    if not (client_dir / "fact_graph.json").exists():  # a client who is only in the portal so far: the scan goes in the portal's folder
        store.profile(client_id)  # a client the portal knows, or LookupError
        folder = store.client_dir(client_id) / "uploads"
        folder.mkdir(parents=True, exist_ok=True)
        path = inbox._unique(folder, _scan_name(clock.today().isoformat(), original))
        path.write_bytes(data)
        inbox._portal_upload(folder, path.name, data, source="folder", by=by)
        return {"name": path.name, "pages": n_pages, "by": by, "case": False}
    folder = Path(inbox._read(client_dir / "meta.json", {}).get("source_folder") or "")
    if not str(folder) or not folder.is_dir():
        raise ValueError("The case's document folder can't be reached from this machine: add the document where the folder is.")
    from overnight import source_signature

    before = source_signature(folder)
    meta_path = client_dir / "meta.json"
    meta_mtime = meta_path.stat().st_mtime if meta_path.exists() else None
    newest = max((p.stat().st_mtime for p in folder.glob("*.pdf")), default=0.0)
    path = inbox._unique(folder, _scan_name(clock.today().isoformat(), original))
    path.write_bytes(data)
    inbox._portal_upload(folder, path.name, data, source="folder", by=by)
    return {"name": path.name, "pages": n_pages, "by": by, "case": True, "before": before, "meta_mtime": meta_mtime, "newest": newest}


def _prepare_scan(data: bytes) -> tuple[bytes, int]:
    """The shared existing PDF/photo validation, before choosing an identity."""
    from portal.store import MAX_UPLOAD, file_kind, image_to_pdf, heic_photo
    from pypdf import PdfReader

    if not data:
        raise ValueError("That file is empty.")
    if len(data) > MAX_UPLOAD:
        raise ValueError(f"That file is larger than {MAX_UPLOAD // (1024 * 1024)} MB: scan it again at a lower resolution.")
    kind = file_kind(data)
    if kind is None and heic_photo(data):
        kind = "image/heic"
    if kind is None:
        raise ValueError("Choose a PDF, JPEG, PNG or HEIC photo.")
    if kind != "application/pdf":
        data = image_to_pdf(data)  # one format downstream, as the portal does
    try:
        reader = PdfReader(io.BytesIO(data))
        if reader.is_encrypted:
            raise ValueError("That PDF is locked with a password: save an unlocked copy and add that.")
        n_pages = len(reader.pages)
    except ValueError:
        raise
    except Exception:  # noqa: BLE001 -- a damaged file is the paralegal's to replace
        raise ValueError("That file can't be opened as a PDF: scan it again.") from None
    if not n_pages:
        raise ValueError("That PDF has no pages.")

    return data, n_pages


def accept_staff_upload(clients_root: Path, store, client_id: str, original: str, data: bytes, attempt: str, **trusted):
    """Retry-safe additive backend; see staff_upload_recovery for trusted args."""
    from .staff_upload_recovery import accept
    return accept(clients_root, store, client_id, original, data, attempt, **trusted)


def staff_upload_outcome(clients_root: Path, store, client_id: str, attempt: str, **trusted):
    """Authorized read-only receipt/job observation, never a processing retry."""
    from .staff_upload_recovery import outcome
    return outcome(clients_root, store, client_id, attempt, **trusted)


def read_staff_upload(clients_root: Path, store, client_id: str, staged: dict[str, Any], by: str, progress=None, db_path: Path | None = None,
                      state_path: Path | None = None, pending=()) -> dict[str, Any]:
    """The slow part of adding a scan: only the new file is read the way the inbox reads a new document, its facts added to the case, its
    record, the index and the filled I-485 brought up to date (the job worker runs this, under the case's lock). A client with no case yet
    has the portal's own processing make it from everything in the portal's folder. Returns {name, documents: [type], processed}."""
    from classify import extract_pages, split_documents

    import inbox

    progress = progress or (lambda *_: None)
    clients_root = Path(clients_root)
    if "staff_receipt" in staged:
        from .staff_upload_recovery import validate_for_read
        validate_for_read(clients_root, store, client_id, staged, by)
    out: dict[str, Any] = {"name": staged["name"], "pages": staged.get("pages"), "by": by}
    if not staged.get("case"):
        from portal.engine import process_client

        out["case_made"] = True
        progress(1, 2, "Making the case from what the client sent")
        try:
            process_client(store, client_id, out_root=clients_root)
            out["processed"] = True
            _credit(clients_root / client_id, staged["name"], by)
        except Exception as exc:  # noqa: BLE001 -- the scan is in the portal's folder; the worker makes the case on its next pass
            store.enqueue(client_id)
            out |= {"processed": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
        return out
    client_dir = clients_root / client_id
    meta_path = client_dir / "meta.json"
    folder = Path(inbox._read(meta_path, {}).get("source_folder") or "")
    path = folder / staged["name"]
    try:
        progress(1, 3, "Reading the scan")
        pages = extract_pages(path)
        if pages:  # a filled Massachusetts SIJ judgment (CJP 37): its tick boxes, read from its own fields (as the batch does)
            from extract.sij_order import form_block

            pages[-1] += form_block(path)
        segments = split_documents(pages)
        from batch import split_pages

        docs, paged = split_pages(path.name, pages, segments)
        progress(2, 3, "Adding it to the case")
        done = inbox.reprocess_documents(client_dir, folder, docs, {path.name: {"pages": staged["pages"]}}, db_path, source="folder", who=by, pages=paged)
        out |= {"processed": True, "type": done["type"], "types": sorted({k for _, _, k in segments if k != "unclassified"}) or ["unclassified"]}
        _credit(client_dir, path.name, by)
        progress(3, 3, "Updating the lists")
    except Exception as exc:  # noqa: BLE001 -- the file is on the case: the next full run of the case reads it
        out |= {"processed": False, "error": f"{type(exc).__name__}: {exc}"[:300]}
    inbox._keep_overnight_honest(client_id, folder, meta_path, staged.get("meta_mtime"), staged.get("newest") or 0.0, staged.get("before") or "", state_path,
                                 bool(out.get("processed")), pending=pending, this=staged["name"])
    return out


def staff_upload(clients_root: Path, store, client_id: str, original: str, data: bytes, by: str, db_path: Path | None = None,
                 state_path: Path | None = None) -> dict[str, Any]:
    """A scan or a photo the office adds to a case, stored and read in one call (stage_upload, then read_staff_upload). The review app stores it and
    leaves the reading to the job worker; this is the whole of it for a command line or a test."""
    staged = stage_upload(clients_root, store, client_id, original, data, by)
    return read_staff_upload(clients_root, store, client_id, staged, by, db_path=db_path, state_path=state_path)


def _credit(client_dir: Path, file_name: str, by: str) -> None:
    """Who added the document and when, on its record (documents.json), kept through every reprocessing."""
    import documents

    data = documents.read(client_dir)
    if not data:
        return
    for record in data["documents"]:
        if (record.get("files") or [None])[0] == file_name and not record.get("added_by"):
            record["added_by"] = {"who": by, "at": clock.stamp()}
    documents.save(client_dir, data)
