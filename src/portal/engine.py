"""The firm-side worker: everything the portal collected for a client, run
through the existing pipeline, on the firm's own machine (the hybrid
setup: the portal stores, this machine reads and checks -- the vision
model and client data processing never run on the internet-facing host).

For each client with new answers or uploads:
  1. answers -> facts (src/portal/bank.py), uploads -> text -> documents;
  2. batch.process_documents + derivation + cross-checks, exactly as for
     a scanned folder; the bundle is saved where the review app reads it
     (data/clients/<id>) and the I-485 re-filled;
  3. the client's to-do list is rebuilt (tasks.json) -- ONLY what is the
     client's to act on: documents still needed, photos to retake, and
     confirmations where a document and an answer disagree on who they
     are. Legal judgments (eligibility, Part 9, criminal history) are never
     shown to the client: they go to the attorney in the review app.
"""

from __future__ import annotations

import json
import re
import threading
from pathlib import Path
from typing import Any

from batch import process_documents
from classify import extract_pages, split_documents
from classify.classifier import readability

from .bank import PORTAL_DOC_ID, _upper, answers_to_facts, bank_for, languages, load_bank, required_documents
from .questions import office_answers
from .store import PortalStore
import clock
import schema_path

REPO = Path(__file__).resolve().parents[2]

# What a client may confirm themselves: who they are, as their own documents
# show it. Everything else that disagrees is the paralegal's.
CLIENT_CONFIRMABLE = {
    "applicant.dob": "dob", "applicant.given_name": "given_name", "applicant.family_name": "family_name",
    "applicant.sex": "sex", "applicant.country_of_birth": "birth_country", "applicant.citizenship": "citizenship",
    "applicant.ssn": "ssn", "applicant.marital_status": "marital_status",
}
DOC_NAMES = {"passport": {"pt": "passaporte", "es": "pasaporte", "en": "passport", "ht": "paspò"},
             "birth_certificate": {"pt": "certidão de nascimento", "es": "acta de nacimiento", "en": "birth certificate", "ht": "ak nesans (batistè)"},
             "work_permit": {"pt": "autorização de trabalho", "es": "permiso de trabajo", "en": "work permit", "ht": "pèmi travay"},
             "ssn_card": {"pt": "cartão do Social Security", "es": "tarjeta de Seguro Social", "en": "Social Security card", "ht": "kat Sosyal Sekirite"},
             "i360_approval": {"pt": "aprovação do I-360", "es": "aprobación del I-360", "en": "I-360 approval", "ht": "apwobasyon I-360"},
             "marriage_certificate": {"pt": "certidão de casamento", "es": "acta de matrimonio", "en": "marriage certificate", "ht": "ak maryaj"},
             "drivers_license": {"pt": "carteira de motorista", "es": "licencia de conducir", "en": "driver's license", "ht": "lisans kondui"},
             "i94": {"pt": "I-94", "es": "I-94", "en": "I-94", "ht": "I-94"}, "visa": {"pt": "visto", "es": "visa", "en": "visa", "ht": "viza"},
             "criminal_record": {"pt": "documento do tribunal", "es": "documento del tribunal", "en": "court document", "ht": "dokiman tribinal"},
             "sij_order": {"pt": "decisão do tribunal (SIJ)", "es": "orden del tribunal (SIJ)", "en": "court order (SIJ findings)", "ht": "lòd tribinal (SIJ)"},
             "us_passport": {"pt": "passaporte americano", "es": "pasaporte estadounidense", "en": "U.S. passport", "ht": "paspò ameriken"},
             "citizenship_certificate": {"pt": "certificado de naturalização", "es": "certificado de naturalización", "en": "naturalization certificate", "ht": "sètifika natiralizasyon"},
             "green_card": {"pt": "green card", "es": "green card", "en": "green card", "ht": "green card"},
             "us_birth_certificate": {"pt": "certidão de nascimento americana", "es": "acta de nacimiento estadounidense", "en": "U.S. birth certificate", "ht": "ak nesans ameriken"},
             "tax_return": {"pt": "declaração de imposto (1040)", "es": "declaración de impuestos (1040)", "en": "tax return (1040)", "ht": "deklarasyon taks (1040)"},
             "w2": {"pt": "W-2", "es": "W-2", "en": "W-2", "ht": "W-2"}, "pay_stub": {"pt": "contracheque", "es": "talón de pago", "en": "pay stub", "ht": "fich peman"},
             "bank_statement": {"pt": "extrato bancário", "es": "estado de cuenta", "en": "bank statement", "ht": "relve bank"},
             "lease": {"pt": "contrato de aluguel", "es": "contrato de arrendamiento", "en": "lease", "ht": "kontra lwaye"},
             "utility_bill": {"pt": "conta (luz, gás, água)", "es": "factura de servicios", "en": "utility bill", "ht": "bòdwo (limyè, gaz, dlo)"},
             "divorce_decree": {"pt": "sentença de divórcio", "es": "sentencia de divorcio", "en": "divorce decree", "ht": "papye divòs"},
             "notice_to_appear": {"pt": "papel da corte de imigração", "es": "documento de la corte de inmigración", "en": "immigration court paper", "ht": "papye tribinal imigrasyon"},
             # the question bank's long labels are for the questionnaire: a sentence about a photo names these short
             "court_disposition": {"pt": "documento do tribunal", "es": "documento del tribunal", "en": "court disposition", "ht": "dokiman tribinal"},
             "immigration_court": {"pt": "papel da corte de imigração", "es": "documento de la corte de inmigración", "en": "immigration court papers", "ht": "papye tribinal imigrasyon"}}

MESSAGES = {
    "retake": {"pt": "Não conseguimos ler a foto do seu documento ({doc}). Pode tirar outra? Documento inteiro, os 4 cantos aparecendo, boa luz, sem reflexo.",
               "es": "No pudimos leer la foto de su {doc}. ¿Puede tomar otra? Documento completo, las 4 esquinas visibles, buena luz, sin reflejo.",
               "en": "We couldn't read the photo of your {doc}. Could you take another? Whole document, all 4 corners visible, good light, no glare.",
               "ht": "Nou pa t ka li foto {doc} ou a. Èske ou ka pran yon lòt? Tout dokiman an, 4 kwen yo parèt, bon limyè, san reflè."},
    # a photo the document reader found blurry, cut off or partial (documents.json "quality"): which document and why
    # (DRAFT: the attorney and a certified translator approve it; the Haitian Creole is a machine draft)
    "retake_quality": {"pt": "A foto do seu documento ({doc}) está {reason}. Pode tirar outra? Documento inteiro, os 4 cantos aparecendo, boa luz, sem reflexo.",
                       "es": "La foto de su {doc} está {reason}. ¿Puede tomar otra? Documento completo, las 4 esquinas visibles, buena luz, sin reflejo.",
                       "en": "The photo of your {doc} is {reason}. Could you take another? Whole document, all 4 corners visible, good light, no glare.",
                       "ht": "Foto {doc} ou a {reason}. Èske ou ka pran yon lòt? Tout dokiman an, 4 kwen yo parèt, bon limyè, san reflè."},
    "moved": {"pt": "Isto parece ser {found}, não {wanted}. Guardamos como {found}.",
              "es": "Esto parece ser {found}, no {wanted}. Lo guardamos como {found}.",
              "en": "This looks like your {found}, not your {wanted}. We filed it as your {found}.",
              "ht": "Sa a sanble se {found} ou, pa {wanted} ou. Nou mete l kòm {found} ou."},
    "still_need": {"pt": " Ainda precisamos do seu documento ({wanted}).", "es": " Todavía necesitamos {wanted}.", "en": " We still need your {wanted}.",
                   "ht": " Nou toujou bezwen {wanted} ou."},
    "confirm": {"pt": "Seu documento ({doc}) mostra “{doc_value}”, mas você respondeu “{answer}”. Qual está certo?",
                "es": "Su documento ({doc}) muestra “{doc_value}”, pero usted respondió “{answer}”. ¿Cuál es correcto?",
                "en": "Your document ({doc}) shows “{doc_value}”, but you answered “{answer}”. Which is right?",
                "ht": "Dokiman ou ({doc}) montre “{doc_value}”, men ou te reponn “{answer}”. Kilès ki kòrèk?"},
}

DOC_WORD = {"pt": "documento", "es": "documento", "en": "document", "ht": "dokiman"}  # when a document has no name of its own

# why a photo has to be taken again, by the document record's quality (blurry | cut_off | partial)
RETAKE_REASONS = {
    "blurry": {"pt": "fora de foco ou tremida", "es": "borrosa", "en": "blurry", "ht": "flou"},
    "cut_off": {"pt": "cortada (falta uma parte das bordas)", "es": "cortada (falta parte de los bordes)", "en": "cut off (part of the edges is missing)",
                "ht": "koupe (yon pati nan bò yo manke)"},
    "partial": {"pt": "incompleta (falta uma página ou um lado)", "es": "incompleta (falta una página o un lado)", "en": "incomplete (a page or a side is missing)",
                "ht": "pa konplè (yon paj oswa yon bò manke)"},
}


def document_records(client_dir: Path) -> list[dict[str, Any]]:
    """The document record (data/clients/<id>/documents.json, written by src/documents.py): one entry per document with its type,
    files and quality. Nothing when the file isn't there (a client not processed yet, or a bundle from before the record existed)."""
    path = client_dir / "documents.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return []
    return [d for d in (data.get("documents") or []) if isinstance(d, dict)] if isinstance(data, dict) else []


def quality_retakes(records: list[dict[str, Any]], uploads: list[dict[str, Any]]) -> list[tuple[dict[str, Any], dict[str, Any]]]:
    """[(record, the client's upload)] for each document the reader found blurry, cut off or partial in a photo the client sent through
    the portal -- and only while that is the client's latest upload for the document: a newer photo replaces the request. A document
    the office scanned itself (no portal upload behind it) is never put back on the client."""
    out = []
    for record in records:
        if record.get("quality") not in RETAKE_REASONS:
            continue
        stored = {str(f).split("#", 1)[0] for f in record.get("files") or []}
        mine = [i for i, up in enumerate(uploads) if up.get("stored") in stored and not up.get("source")]  # the office's own scans carry a source
        if not mine:
            continue
        last = mine[-1]
        if any(later["doc_id"] == uploads[last]["doc_id"] and later.get("status") != "retake" for later in uploads[last + 1:]):
            continue
        out.append((record, uploads[last]))
    return out


def retake_tasks(records: list[dict[str, Any]], uploads: list[dict[str, Any]], requested: list[dict[str, Any]], language: str,
                 known: set[str] | None = None) -> list[dict[str, Any]]:
    """One "retake this photo" task per photo the document record calls blurry, cut off or partial (quality_retakes): which document
    and why, in every language, with the document's own line marked "send another photo". known: task ids already on the list (a photo
    that could not be read at all was asked for already)."""
    lang = language if language in languages() else "pt"
    out = []
    for record, up in quality_retakes(records, uploads):
        spec = next((s for s in requested if s["id"] == up["doc_id"]), None)
        doc_type = (spec["doc_types"][0] if spec else None) or record.get("type") or up["doc_id"]
        up.update(status="retake", quality=record["quality"])
        if f"retake:{up['id']}" in (known or ()):
            continue

        def retake_quality(lg, doc_type=doc_type, spec=spec, up=up, quality=record["quality"]):
            return _say(MESSAGES["retake_quality"], lg).format(doc=_doc_name(doc_type, spec, up, lg), reason=_say(RETAKE_REASONS[quality], lg))
        out.append({"id": f"retake:{up['id']}", "kind": "retake", "doc_id": up["doc_id"], "upload": up["id"], "quality": record["quality"],
                    "record": record.get("id"), "doc_en": _doc_name(doc_type, spec, up, "en"), "why_en": _say(RETAKE_REASONS[record["quality"]], "en"),
                    **_texts(retake_quality, lang)})
    return out


def stamp_retakes(store: PortalStore, client_id: str, tasks: list[dict[str, Any]]) -> None:
    """Each retake task keeps the day it was first asked (the review app says "Retake requested 10/02/2026"); a new one is logged."""
    asked = {t["id"]: t.get("asked_at") for t in store.tasks(client_id)}
    now = clock.stamp()
    for task in tasks:
        if task["kind"] == "retake":
            task["asked_at"] = asked.get(task["id"]) or now
            if not asked.get(task["id"]):
                store.log(client_id, "retake_requested", {"task": task["id"], "doc_id": task["doc_id"], "quality": task.get("quality")})


def sync_retakes(store: PortalStore, client_id: str, client_dir: Path) -> list[dict[str, Any]]:
    """The client's retake tasks to match the document record now, without running the pipeline: a reviewer who marks a photo
    blurry on the Documents tab has the retake asked at once (the worker would do the same on its next pass), and one who sets
    it back to readable takes it off the client's list. Only tasks that come from a record's quality (they carry the record's id) are
    touched; a photo that could not be read at all keeps its own task. Returns the retake tasks on the client's list."""
    profile, uploads, tasks = store.profile(client_id), store.uploads(client_id), store.tasks(client_id)
    requested = required_documents(store.answers(client_id), bank_for(profile))
    wanted = retake_tasks(document_records(client_dir), uploads, requested, profile.get("language", "pt"),
                          {t["id"] for t in tasks if t.get("kind") == "retake" and not t.get("record")})
    ids = {t["id"] for t in wanted}
    dropped = {t["upload"] for t in tasks if t.get("kind") == "retake" and t.get("record") and t["id"] not in ids}
    kept = [t for t in tasks if not (t.get("kind") == "retake" and t.get("record") and t["id"] not in ids)]
    have = {t["id"] for t in kept}
    kept += [t for t in wanted if t["id"] not in have]
    for up in uploads:  # a photo taken off the list is a good photo again, until the reader says otherwise
        if up["id"] in dropped and up.get("status") == "retake" and up.get("quality"):
            up.pop("quality", None)
            up["status"] = "checked"
    stamp_retakes(store, client_id, kept)
    store.update_uploads(client_id, uploads)
    store.save_tasks(client_id, kept)
    return [t for t in kept if t.get("kind") == "retake"]


def sync_confirmations(store: PortalStore, client_id: str, client_dir: Path) -> list[dict[str, Any]]:
    """The client's "which is right?" questions to match the case now, without running the pipeline: a reviewer who sets a document to
    someone else (the Social Security card to the spouse) takes the questions it caused off the client's list at once, and one who sets
    it back to the client has them asked again (documents.set_aside_for_other_people). Only the confirmations are touched. Returns
    the confirmation tasks on the client's list."""
    import copy

    import documents
    from factgraph import FactGraph

    path = client_dir / "fact_graph.json"
    if not path.exists():
        return []
    profile, tasks = store.profile(client_id), store.tasks(client_id)
    graph = FactGraph.load(path)
    documents.set_aside_for_other_people(graph, client_dir)
    wanted = [t for t in client_tasks(store.answers(client_id), copy.deepcopy(store.uploads(client_id)), graph, profile.get("language", "pt"),
                                      profile.get("confirmed"), bank_for(profile), decided_facts(client_dir), [], document_records(client_dir))
              if t.get("kind") == "confirm"]
    kept = [t for t in tasks if t.get("kind") != "confirm"] + wanted
    if [t["id"] for t in kept] != [t["id"] for t in tasks]:
        store.save_tasks(client_id, kept)
        store.log(client_id, "confirmations_rebuilt", {"tasks": [t["id"] for t in wanted]})
    return wanted


def photo_status(records: list[dict[str, Any]], uploads: list[dict[str, Any]], tasks: list[dict[str, Any]], in_portal: bool = True) -> dict[str, dict[str, Any]]:
    """{record id: what the client's side says about it} for the Documents tab, only for records that have something to say:
      "retake": "asked" (a retake task is on the client's list, with "asked_at"), "office_scan" (the office scanned it itself:
                the client is never asked) or "no_portal" (the client has no portal to ask in);
      "new_photo": {"at", "waiting"}: the client sent a newer photo of the same document; waiting = the reader has not run on it yet.
    A record with a newer photo is not "hard to read" any more as far as anyone knows: the screen shows its quality as not known."""
    out: dict[str, dict[str, Any]] = {}
    for record in records:
        stored = {str(f).split("#", 1)[0] for f in record.get("files") or []}
        mine = [i for i, up in enumerate(uploads) if up.get("stored") in stored and not up.get("source")]  # the office's own scans carry a source
        if mine:
            later = [u for u in uploads[mine[-1] + 1:] if u["doc_id"] == uploads[mine[-1]]["doc_id"] and u.get("status") != "retake"]
            if later:
                out[record["id"]] = {"new_photo": {"at": later[-1].get("uploaded_at"), "waiting": later[-1].get("status") == "received"}}
                continue
        if record.get("quality") not in RETAKE_REASONS:
            continue
        if not mine:
            out[record["id"]] = {"retake": "office_scan" if in_portal else "no_portal"}
        else:
            task = next((t for t in tasks if t.get("kind") == "retake" and t.get("record") == record["id"]), None)
            out[record["id"]] = {"retake": "asked", "asked_at": task.get("asked_at")} if task else {"retake": "not_yet"}
    return out


def new_photos(records: list[dict[str, Any]], uploads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """The photos the client sent since the reader last ran, each replacing an earlier one: [{"document", "at"}], for My work
    ("New photo from the client"). Once the reader has run on it the row is gone: the record shows the new reading."""
    out = []
    for record_id, status in photo_status(records, uploads, []).items():
        photo = status.get("new_photo")
        if photo and photo["waiting"]:
            record = next(r for r in records if r["id"] == record_id)
            out.append({"record": record_id, "type": record.get("type"), "at": photo["at"]})
    return out


def _doc_name(doc_type: str, spec: dict[str, Any] | None, up: dict[str, Any], lang: str) -> str:
    """A document as the client (or, in English, the office) names it: "passaporte", "birth certificate". A document with no name of its
    own is named, for the office, by the taxonomy's display name (src/documents.py); for the client by the question bank's label for what
    they uploaded; and by a short plain word ("court papers", "document") when that is not short: never an id and never a long label."""
    if doc_type in DOC_NAMES:
        return _say(DOC_NAMES[doc_type], lang)
    if lang == "en":
        return staff_name(up.get("doc_id") or doc_type, doc_type)
    label = (spec or next((d for d in load_bank()["documents"] if d["id"] == up["doc_id"]), None) or {}).get("label")
    text = _say(label, lang) if label else ""
    return text if text and len(text) <= SHORT_NAME and "—" not in text else _say(DOC_WORD, lang)


SHORT_NAME = 40  # characters: a document's name on the office's screens (and in a sentence to the client) stays this short


def staff_name(*ids: str) -> str:
    """A document as the office's screens name it (the Documents tab, My work): the taxonomy's display name for the first id it knows,
    else "court papers" for a court document, else "document". Short, with no dash: the bank's labels are sentences for the client."""
    import documents

    for doc_id in ids:
        if doc_id in documents.types() and doc_id != "unclassified":
            name = documents.name(doc_id)
            if name and len(name) <= SHORT_NAME and "—" not in name:
                return name
    return "court papers" if any("court" in (i or "") for i in ids) else "document"


def _say(words: dict[str, str], lang: str) -> str:
    """One of the texts above in the client's language; English when it has none (never blank)."""
    return words.get(lang) or words["en"]


def _texts(make, lang: str) -> dict[str, Any]:
    """A task's words in every language: the client can switch language after
    processing, and the task must switch with the rest of the page."""
    texts = {lg: make(lg) for lg in languages()}
    return {"text": texts.get(lang) or texts["en"], "texts": texts}


def _shown(value: Any, lang: str) -> str:
    """A value as the client reads it: dates the way they write them."""
    text = str(value)
    if re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        y, m, d = text.split("-")
        return f"{m}/{d}/{y}" if lang == "en" else f"{d}/{m}/{y}"
    return text


def _upload_texts(store: PortalStore, client_id: str) -> tuple[list[tuple[str, str]], list[dict[str, Any]], dict[str, list[str]]]:
    """(doc_id, text) per document found in the uploads -- a combined
    upload is split like any folder PDF -- and the uploads, each marked
    with what was found in it and whether it could be read; and each document's page texts (batch.split_pages)."""
    folder = store.client_dir(client_id) / "uploads"
    documents, uploads, paged = [], store.uploads(client_id), {}
    for up in uploads:
        found, _n, pages = _read_upload(folder, up)
        documents += found
        paged |= pages
    return documents, uploads, paged


def _read_upload(folder: Path, up: dict[str, Any]) -> tuple[list[tuple[str, str]], int, dict[str, list[str]]]:
    """One upload read: [(doc id, text)] (a combined file is one per part), its page count and each document's page texts; the upload is marked with what was found
    in it and whether it could be read ("checked", or "retake" for a photo nothing could be read from)."""
    try:
        pages = extract_pages(folder / up["stored"])
    except Exception:  # noqa: BLE001 -- a broken file is a retake, not a crash
        up.update(status="checked" if up.get("source") else "retake", detected="unreadable")  # the office's own scan is never asked of the client
        return [], 0, {}
    if pages:  # a filled Massachusetts SIJ judgment (CJP 37): its tick boxes, read from its own fields
        from extract.sij_order import form_block

        pages[-1] += form_block(folder / up["stored"])
    text = "\n".join(pages)
    segments = split_documents(pages)
    kinds = [k for _, _, k in segments if k != "unclassified"]
    up["detected"] = kinds[0] if kinds else "unclassified"
    up["readability"] = round(readability(text), 1)
    up["status"] = "retake" if (not kinds and up["readability"] < 3 and not up.get("source")) else "checked"
    from batch import split_pages

    docs, paged = split_pages(up["stored"], pages, segments)
    return docs, len(pages), paged


def arrived(uploads: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """What the client sent that nobody has read yet (status "received"), for the office's Documents tab and My work:
    [{"upload", "doc_id", "document" (as the office names it: "passport"), "at", "retake" (it answers a "send another photo"),
    "reading" ("now": being read on this machine, "tonight": the next run reads it)}]. The office's own scans are never listed."""
    out = []
    for up in uploads:
        if up.get("status") == "received" and not up.get("source"):
            out.append({"upload": up["id"], "doc_id": up["doc_id"], "document": _doc_name(up["doc_id"], None, up, "en"), "at": up.get("uploaded_at"),
                        "retake": bool(up.get("retake")), "reading": up.get("reading")})
    return out


_READING = threading.Lock()  # one client's new uploads are read one batch at a time (a second photo sent while the first is read waits its turn)


def _save_uploads(store: PortalStore, client_id: str, changed: list[dict[str, Any]]) -> None:
    """The changed upload records written back into the client's list as it is now (an upload that arrived meanwhile is kept)."""
    with store._lock:
        by_id = {u["id"]: u for u in changed}
        current = store.uploads(client_id)
        for u in current:
            if u["id"] in by_id:
                fresh = by_id[u["id"]]
                u.clear()
                u.update(fresh)
        store.update_uploads(client_id, current)


def read_new_uploads(store: PortalStore, client_id: str, cases_root: Path | None = None, db_path: Path | None = None, progress=None) -> dict[str, Any]:
    """The client's new photos and documents read at once, when the case is on this machine and was made from this client's uploads
    (the review app and the portal share a machine): only the new files go through the path a staff upload uses
    (inbox.reprocess_documents), the retake tasks they answer close (and one opens again if the new photo is no better), and the
    uploads say they were read. Anything else (the portal on another host, a client with no case yet) is left for the worker's next pass
    and says so on the upload ("tonight"). A file that could not be read stays for the worker. progress(step, steps, words): the job
    worker's (src/jobs.py) report of how far it is, which the office's screen shows beside "Reading now"."""
    with _READING:
        return _read_new_uploads(store, client_id, cases_root, db_path, progress or (lambda *_: None))


def _read_new_uploads(store: PortalStore, client_id: str, cases_root: Path | None, db_path: Path | None, progress) -> dict[str, Any]:
    from .notify import cases_folder

    cases = Path(cases_root) if cases_root else cases_folder()
    folder = store.client_dir(client_id) / "uploads"
    client_dir = cases / client_id
    new = [u for u in store.uploads(client_id) if u.get("status") == "received" and not u.get("source")]
    if not new:
        return {"read": 0}
    if not store.case_here(client_id, cases):
        store.mark_reading(client_id, "tonight")
        return {"read": 0, "why": "tonight"}
    _save_uploads(store, client_id, [u | {"reading": "now"} for u in new])
    try:
        import inbox

        docs, split_info, paged = [], {}, {}
        progress(1, 3, "Reading the photo" if len(new) == 1 else "Reading the photos")
        for u in new:
            found, pages, texts = _read_upload(folder, u)
            docs += found
            paged |= texts
            if found:
                split_info[u["stored"]] = {"pages": pages}
        if docs:
            progress(2, 3, "Adding it to the case" if len(new) == 1 else "Adding them to the case")
            inbox.reprocess_documents(client_dir, folder, docs, split_info, db_path, source="portal", pages=paged)
        progress(3, 3, "Updating the lists")
    except Exception as exc:  # noqa: BLE001 -- the file stays unread on the client's uploads; the worker reads it on its next pass
        _save_uploads(store, client_id, [{k: v for k, v in u.items() if k != "detected"} | {"status": "received", "reading": "tonight"} for u in new])
        store.log(client_id, "read_failed", {"error": type(exc).__name__})
        return {"read": 0, "why": "failed"}
    _save_uploads(store, client_id, [{k: v for k, v in u.items() if k != "reading"} for u in new])
    asked = {u["doc_id"] for u in new}
    kept = [t for t in store.tasks(client_id) if not (t.get("kind") == "retake" and t.get("received_at") and t.get("doc_id") in asked)]
    profile = store.profile(client_id)
    requested = required_documents(store.answers(client_id), bank_for(profile))
    kept += [unreadable_retake(u, requested, profile.get("language", "pt")) | {"asked_at": clock.stamp()}
             for u in new if u.get("status") == "retake" and not any(t["id"] == f"retake:{u['id']}" for t in kept)]  # nothing could be read from it
    store.save_tasks(client_id, kept)  # the retakes this answered are done
    sync_retakes(store, client_id, client_dir)  # and a new photo that is itself hard to read is asked again
    store.log(client_id, "uploads_read", {"uploads": [u["id"] for u in new], "at_once": True})
    return {"read": len(new)}


def decided_facts(client_dir: Path) -> dict[str, dict[str, Any]]:
    """{fact key: the reviewer's decision} for every fact the office has decided in the review app (decisions.json): a
    confirmation, a value or a blank. An acknowledgement of an alert settles no answer, so the client is still asked."""
    from review.state import load_decisions

    return {key: d for d in load_decisions(client_dir).values() if d.get("action") in ("confirm", "set", "blank")
            for key in d["item"]["facts"]}


def _skip_record(task_id: str, qid: str, key: str, decision: dict[str, Any]) -> dict[str, Any]:
    when = clock.us_date(decision.get("at")) or "earlier"  # the office's date of the decision (src/clock.py), not UTC's
    return {"task": task_id, "question": qid, "fact": key, "by": decision.get("reviewer"), "decided_at": decision.get("at"),
            "why": f"The office already decided this answer ({decision.get('reviewer') or 'a reviewer'}, {when}), so the client is not asked again."}


def skip_decided(store: PortalStore, client_id: str, decided: dict[str, dict[str, Any]]) -> list[dict[str, Any]]:
    """Takes out of the client's to-do list every confirmation the office has since decided, and records on the client's
    file that each was skipped and why. Called when a reviewer decides a card, so the next portal visit is already right."""
    by_question = {qid: key for key, qid in CLIENT_CONFIRMABLE.items()}
    kept, skipped = [], []
    for task in store.tasks(client_id):
        key = by_question.get(task.get("question")) if task.get("kind") == "confirm" else None
        if key in decided:
            skipped.append(_skip_record(task["id"], task["question"], key, decided[key]))
        else:
            kept.append(task)
    if skipped:
        store.save_tasks(client_id, kept)
        store.add_skipped_tasks(client_id, skipped)
    return skipped


def settle_decided(store: PortalStore, client_id: str, decided: dict[str, dict[str, Any]]) -> list[str]:
    """The client's answers to the office's questions that the office has now decided on their review card are settled: the
    portal's "Sent to the office" line for each goes (the office has dealt with it). A question with no card behind it is settled
    by hand on the case's Requests list (store.settle_requests)."""
    ids = [r["id"] for r in store.requests(client_id)
           if r["status"] == "answered" and not r.get("settled_at") and r.get("facts") and all(k in decided for k in r["facts"])]
    if not ids:
        return []
    by = decided[next(r for r in store.requests(client_id) if r["id"] == ids[0])["facts"][0]].get("reviewer") or "the office"
    return store.settle_requests(client_id, by, ids)


def office_has_task(spec: dict[str, Any], lang: str) -> dict[str, Any]:
    """The client's request for a paper the office has marked absent is closed: "<the paper>: The office has what it needs" in the client's language
    (client_case.label "office_has": DRAFT, the attorney and a certified translator approve it). The doc_id says which paper's row it closes."""
    import client_case

    def says(lg):
        paper = _doc_name(spec["doc_types"][0], spec, {"doc_id": spec["id"]}, lg)
        return f"{paper[:1].upper()}{paper[1:]}: {client_case.label('office_has', lg)}"
    return {"id": f"office_has:{spec['id']}", "kind": "info", "office_has": True, "doc_id": spec["id"], **_texts(says, lang)}


def sync_absences(store: PortalStore, client_id: str, client_dir: Path, by: str = "") -> int:
    """The client's list to match the papers the office has marked absent (src/absence.py), without running the pipeline: a request for such a
    paper (an open question of the office's, or a paper the questionnaire's answers call for) is closed with "The office has what it needs"; when a
    mark is taken off, or the paper arrives and lifts it, the request is open again. Only the upload and office-has tasks and the requests the
    office closed this way are touched. Returns how many of the office's requests were closed."""
    import absence

    profile = store.profile(client_id)
    bank = bank_for(profile)
    lang = profile.get("language", "pt")
    absent = absence.absent_types(client_dir)
    specs = {d["id"]: d for d in bank["documents"]}

    def shut(spec: dict[str, Any] | None) -> bool:
        return bool(spec and absent & set(spec.get("doc_types") or ()))

    requests = store.requests(client_id)
    closed = store.close_requests(client_id, [r["id"] for r in requests if r["status"] == "open" and r.get("doc_id") and shut(specs.get(r["doc_id"]))], by or "the office")
    reopened = store.reopen_closed_requests(client_id, [r["id"] for r in requests if r["status"] == "closed" and r.get("closed_by_absence")
                                                        and not shut(specs.get(r.get("doc_id") or ""))])
    requested = required_documents(store.answers(client_id), bank)
    uploads = store.uploads(client_id)
    wanted_specs = {s["id"]: s for s in requested if shut(s)}
    for r in store.requests(client_id):  # a paper the office asked for by hand and then closed: its line stays until the mark is off
        if r["status"] == "closed" and r.get("closed_by_absence") and shut(specs.get(r.get("doc_id") or "")):
            wanted_specs[r["doc_id"]] = specs[r["doc_id"]]
    tasks = [t for t in store.tasks(client_id) if not t.get("office_has") and not str(t.get("id", "")).startswith("upload:")]
    for spec in requested:
        have = sum(1 for up in uploads if up["doc_id"] == spec["id"] and up.get("status") != "retake")
        if spec["required"] and have < spec["count"] and spec["id"] not in wanted_specs:
            tasks.append({"id": f"upload:{spec['id']}", "kind": "upload", "doc_id": spec["id"], "needed": spec["count"] - have})
    tasks += [office_has_task(spec, lang) for spec in wanted_specs.values()]
    if [t["id"] for t in tasks] != [t["id"] for t in store.tasks(client_id)] or closed or reopened:
        store.save_tasks(client_id, tasks)
        store.log(client_id, "absences_synced", {"closed": closed, "reopened": reopened, "papers": sorted(wanted_specs)})
    return len(closed)


def unreadable_retake(up: dict[str, Any], requested: list[dict[str, Any]], lang: str) -> dict[str, Any]:
    """The "take this photo again" task for an upload nothing could be read from."""
    spec = next((s for s in requested if s["id"] == up["doc_id"]), None)
    doc_type = spec["doc_types"][0] if spec else up["doc_id"]

    def retake(lg):
        return _say(MESSAGES["retake"], lg).format(doc=_doc_name(doc_type, spec, up, lg))
    return {"id": f"retake:{up['id']}", "kind": "retake", "doc_id": up["doc_id"], "upload": up["id"], "doc_en": _doc_name(doc_type, spec, up, "en"),
            **_texts(retake, lang)}


def client_tasks(answers: dict[str, Any], uploads: list[dict[str, Any]], graph, language: str,
                 confirmed: dict[str, Any] | None = None, bank: dict[str, Any] | None = None,
                 decided: dict[str, dict[str, Any]] | None = None, skipped: list[dict[str, Any]] | None = None,
                 records: list[dict[str, Any]] | None = None, absent: set[str] | None = None) -> list[dict[str, Any]]:
    """absent: the document types the office marked absent (src/absence.py): the client's request for such a paper is closed ("The office has what it
    needs"), not asked again. confirmed: {question: value} the client already stood by -- not asked
    again; a disagreement that remains is the paralegal's. decided: {fact key:
    decision} the office already made in the review app -- not asked either; each
    one left out is appended to `skipped` with the reason. records: the document
    record (document_records): a photo it calls blurry, cut off or partial becomes
    a "retake this photo" task naming the document and why."""
    lang = language if language in languages() else "pt"
    tasks: list[dict[str, Any]] = []
    requested = required_documents(answers, bank or load_bank())
    by_type = {t: spec for spec in requested for t in spec["doc_types"]}

    # uploads that are something else than what was asked for are re-filed
    for up in uploads:
        wanted = next((s for s in requested if s["id"] == up["doc_id"]), None)
        found = up.get("detected")
        if wanted and found not in (None, "unclassified", "unreadable") and found not in wanted["doc_types"] and found in by_type:
            up["doc_id"] = by_type[found]["id"]
            def moved(lg, found=found, wanted=wanted):
                names = {"found": _say(DOC_NAMES[found], lg) if found in DOC_NAMES else found,
                         "wanted": _say(DOC_NAMES[wanted["doc_types"][0]], lg) if wanted["doc_types"][0] in DOC_NAMES else wanted["id"]}
                return _say(MESSAGES["moved"], lg).format(**names) + (_say(MESSAGES["still_need"], lg).format(**names) if wanted["required"] else "")
            tasks.append({"id": f"moved:{up['id']}", "kind": "info", **_texts(moved, lang)})
        if up.get("status") == "retake":
            tasks.append(unreadable_retake(up, requested, lang))

    tasks += retake_tasks(records or [], uploads, requested, lang, {t["id"] for t in tasks})

    for spec in requested:
        if absent and absent & set(spec["doc_types"]):
            tasks.append(office_has_task(spec, lang))
            continue
        have = sum(1 for up in uploads if up["doc_id"] == spec["id"] and up.get("status") != "retake")
        if spec["required"] and have < spec["count"]:
            tasks.append({"id": f"upload:{spec['id']}", "kind": "upload", "doc_id": spec["id"], "needed": spec["count"] - have})

    # who they are: a document disagrees with what they typed
    for key, qid in CLIENT_CONFIRMABLE.items():
        fact = graph.get(key)
        if fact is None or fact.status != "conflict":
            continue
        said = next((s for s in fact.sources if s.doc_id == PORTAL_DOC_ID), None)
        shown = next((s for s in fact.sources if s.doc_id != PORTAL_DOC_ID and s.doc_type != "firm_profile"), None)
        if said is None or shown is None or _upper((confirmed or {}).get(qid, "")) == _upper(said.normalized_value):
            continue
        if key in (decided or {}):  # the office settled it on a review card: never ask the client the same thing again
            if skipped is not None:
                skipped.append(_skip_record(f"confirm:{qid}", qid, key, decided[key]))
            continue
        options = [str(shown.normalized_value), str(said.normalized_value)]

        def confirm(lg, shown=shown, options=options):
            doc_name = _say(DOC_NAMES[shown.doc_type], lg) if shown.doc_type in DOC_NAMES else shown.doc_type
            return _say(MESSAGES["confirm"], lg).format(doc=doc_name, doc_value=_shown(options[0], lg), answer=_shown(options[1], lg))
        tasks.append({"id": f"confirm:{qid}", "kind": "confirm", "question": qid, **_texts(confirm, lang), "options": options,
                      "labels": [_shown(o, lang) for o in options], "labels_by_lang": {lg: [_shown(o, lg) for o in options] for lg in languages()}})
    return tasks


def process_client(store: PortalStore, client_id: str, out_root: Path | None = None, use_policies: bool = True) -> dict[str, Any]:
    import jobs
    from .communication_consent import data_gate
    cases = Path(out_root or REPO / "data" / "clients").absolute()
    with data_gate(cases.parent), jobs.case_lock(jobs.folder_for(cases), client_id):
        return _process_client(store, client_id, cases, use_policies)


def _process_client(store: PortalStore, client_id: str, out_root: Path, use_policies: bool) -> dict[str, Any]:
    """Runs one portal client through the pipeline; returns a summary."""
    import absence
    from batch import load_firm_profile
    from fill import load_field_map
    from review.state import refill, save_bundle
    from rules import ALL_RULES
    from rules.policy import load_policy_profile

    out_root = out_root or REPO / "data" / "clients"
    profile, answers = store.profile(client_id), store.answers(client_id)
    import source_association
    source_association.assert_ready(out_root / client_id)
    source_folder = store.client_dir(client_id) / "uploads"
    origins = None
    if (out_root / client_id / source_association.FILE).exists():
        source_folder, uploads, origins = source_association.engine_sources(out_root.parent.parent, store, client_id)
        documents, paged = [], {}
        for row in origins:
            found, count, page_texts = _read_upload(source_folder, row)
            if not count:
                raise ValueError("A retained original could not be read; the complete case was not replaced.")
            documents += found
            paged |= page_texts
    else:
        documents, uploads, paged = _upload_texts(store, client_id)
    policies = load_policy_profile(schema_path.path("law", "policy_sijs")) if use_policies else None
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    result = process_documents(client_id, documents, rules=ALL_RULES, policies=policies,
                               firm_profile=load_firm_profile(schema_path.path("firm", "firm_profile")),
                               answers=answers_to_facts(answers, bank_for(profile)),
                               office_answers=office_answers(store.requests(client_id), field_map), pages=paged,
                               boundary_context=__import__('document_instances').context(source_folder, out_root / client_id,
                                                                                        list({d.split('#p', 1)[0] for d, _ in documents})))
    from batch import record_documents, shadow_document_types

    import document_instances
    document_instances.invalidate(out_root / client_id, result.boundary_plans)
    document_instances.stage(out_root / client_id, result.boundary_plans)
    record_documents(result, source_folder, documents, source="portal", uploads=origins or uploads)
    if origins is not None:
        origin_by_file = {row["stored"]: row.get("source") or "portal" for row in origins}
        for row in result.documents["documents"]:
            if len({origin_by_file[name] for name in row["files"]}) != 1:
                raise ValueError("Identical originals have different origins; review the association before publishing.")
    shadow_document_types(client_id, documents, result.classifications)
    out = out_root / client_id
    save_bundle(result, out, source_folder)
    summary = refill(out, field_map, schema_path.path("template", "i485"))
    skipped: list[dict[str, Any]] = []
    import documents
    from factgraph import FactGraph

    asked_of = FactGraph.from_dict(result.graph.to_dict())  # what the client is asked about: without a document a person set to someone else
    documents.set_aside_for_other_people(asked_of, out)
    tasks = client_tasks(answers, uploads, asked_of, profile.get("language", "pt"), profile.get("confirmed"), bank_for(profile),
                         decided_facts(out), skipped, document_records(out), absence.absent_types(out))
    stamp_retakes(store, client_id, tasks)
    _save_uploads(store, client_id, uploads)
    store.save_tasks(client_id, tasks)
    sync_absences(store, client_id, out)  # the office's open requests for a paper marked absent close; one whose mark is off opens again
    store.add_skipped_tasks(client_id, skipped)
    store.log(client_id, "processed", {"tasks": len(tasks), "counts": summary["counts"]})
    _apply_track(profile, out)
    import engagement

    engagement.sync(out, store.root)  # an agreement the client signed in the portal goes on the case (it never raises)
    return {"client": client_id, "tasks": len(tasks), **summary["counts"]}


def _apply_track(profile: dict[str, Any], client_dir: Path) -> None:
    """The track the office named when it added the client (review/front_desk.py add_client) becomes the case's track once there is a case
    to carry it; a track already chosen on the case is never changed."""
    import journey

    track = profile.get("track")
    if track and not ((journey._status(client_dir).get("journey") or {}).get("track")):
        journey.mark(client_dir, "track", profile.get("added_by") or "the office", value=track, note="Named when the client was added.")


def run_worker(store: PortalStore, once: bool = False, interval: float = 5.0, use_policies: bool = True, *, cases=None, jobs_root=None) -> int:
    """Delegate to the installed worker, including this firm's other due jobs."""
    if cases is None or jobs_root is None:
        raise ValueError("Portal worker requires explicit cases and jobs folders")
    import jobs
    return jobs.work(cases, store.root, once=once, poll=interval, jobs_root=jobs_root, use_policies=use_policies)
