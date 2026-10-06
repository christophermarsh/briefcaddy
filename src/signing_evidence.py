"""Firm evidence for portal engagement signing, using the existing R2 ledger only.

Snapshots are created once. Missing or changed snapshots are never rebuilt.
A same-day signature remains unverifiable until an R2 daily anchor covers it.
"""
from __future__ import annotations

import hashlib
import io
import json
import re
import stat
from datetime import date

import clock
from pathlib import Path

import events
import ledger_seal
import oslock

READ_AND_AGREE = {"en": "I have read and agree", "pt": "Li e concordo", "es": "Leí y acepto", "ht": "Mwen li e mwen dakò"}

CONSENT_DRAFT = {
    "en": "I agree to sign this agreement electronically.",
    "pt": "Concordo em assinar este acordo eletronicamente.",
    "es": "Acepto firmar este acuerdo electrónicamente.",
    "ht": "Mwen dakò siyen akò sa a elektwonikman.",
}


def canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, ensure_ascii=False, separators=(",", ":")).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def paths(client_dir: Path, letter_id: str) -> tuple[Path, Path, Path]:
    if not re.fullmatch(r"[A-Za-z0-9_-]+", str(letter_id)):
        raise LookupError("Unknown agreement.")
    folder = Path(client_dir) / "engagement"
    for part in (folder, *folder.parents):
        if part.exists() and (part.is_symlink() or getattr(part.lstat(), "st_file_attributes", 0) &
                              getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
            raise ValueError("Signing evidence path cannot use a link or reparse point.")
    return tuple(folder / f"signed-{letter_id}.{suffix}" for suffix in ("pdf", "json", "evidence.json"))


def retain(client_dir: Path, letter: dict, shown: dict, signature: dict) -> dict:
    """Retain the exact shown text and server-captured evidence before any countersignature.

    The ledger commits the retained PDF and the canonical signing record. The
    evidence sidecar is a reference to that row, never a second hash chain.
    """
    import engagement

    pdf, snapshot, evidence = paths(client_dir, letter["id"])
    if any(p.exists() for p in (pdf, snapshot, evidence)):
        raise ValueError("A signing snapshot already exists; inspect it, never replace it.")
    frozen = dict(letter) | {"texts": shown["texts"], "titles": shown["titles"], "signature": signature}
    frozen.pop("countersignature", None)
    ordinary = engagement.pdf_path(client_dir, frozen)
    if ordinary.exists() and (ordinary.is_symlink() or getattr(ordinary.lstat(), "st_file_attributes", 0) &
                              getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024)):
        raise ValueError("Agreement PDF cannot use a link or reparse point.")
    rendered = engagement.render(client_dir, frozen).read_bytes()
    record = {"version": 1, "case": Path(client_dir).name, "letter": shown, "signature": signature,
              "letter_sha256": digest(rendered)}
    raw = canonical(record)
    pdf.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    for path, content in ((pdf, rendered), (snapshot, raw)):
        with path.open("xb") as f:
            f.write(content)
        path.chmod(0o600)
    commitment = f"Retained signed engagement letter: PDF {digest(rendered)}; signing record {digest(raw)}"
    row = events.record("engagement", "signing_evidence", commitment, case_dir=client_dir,
                        who="The client", role="client", via="portal")
    meta = {"version": 1, "letter_sha256": digest(rendered), "record_sha256": digest(raw),
            "ledger_hash": (row or {}).get("hash"), "ledger_at": (row or {}).get("at")}
    with evidence.open("xb") as f:
        f.write(canonical(meta))
    evidence.chmod(0o600)
    return meta


def _complete_anchors(base: Path) -> bool:
    """R2 skips malformed JSON while reading; certificates must fail closed."""
    path = ledger_seal.anchors_path(base)
    if not path.is_file() or path.is_symlink():
        return False
    raw = path.read_bytes()
    if raw and not raw.endswith(b"\n"):
        return False
    days = set()
    for line in raw.splitlines():
        if not line.strip():
            continue
        anchor = json.loads(line)
        if not isinstance(anchor, dict):
            return False
        day, at, h, rows = (anchor.get(k) for k in ("day", "at", "hash", "rows"))
        if (not isinstance(day, str) or date.fromisoformat(day).isoformat() != day or day in days
                or not isinstance(at, str) or not clock.parse(at) or at[:10] != day
                or not isinstance(h, str) or not re.fullmatch(r"[0-9a-f]{64}", h)
                or not isinstance(rows, int) or isinstance(rows, bool) or rows < 1):
            return False
        days.add(day)
    return bool(days)


def verify(client_dir: Path, letter_id: str) -> dict:
    """Return only this client's signer evidence and opaque R2 references.

    Never expose global ledger diagnostics, neighboring rows, or staff names.
    Hold R2's writer lock while checking the chain and its covering anchor.
    """
    fail = {"ok": False, "status": "unverifiable", "reason": "Signing record unavailable or changed."}
    try:
        pdf, snapshot, evidence = paths(client_dir, letter_id)
        for path in (pdf, snapshot, evidence):
            if path.is_symlink() or not path.is_file() or getattr(path.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 1024):
                return fail
        raw, document = snapshot.read_bytes(), pdf.read_bytes()
        record, meta = json.loads(raw), json.loads(evidence.read_bytes())
        if (not isinstance(record, dict) or not isinstance(meta, dict)
                or not isinstance(record.get("letter"), dict) or not isinstance(record.get("signature"), dict)
                or not isinstance(meta.get("ledger_hash"), str) or not re.fullmatch(r"[0-9a-f]{64}", meta["ledger_hash"])
                or not isinstance(meta.get("ledger_at"), str)):
            return fail
        if (record.get("case") != Path(client_dir).name or record.get("letter", {}).get("id") != letter_id
                or meta.get("letter_sha256") != digest(document) or record.get("letter_sha256") != digest(document)
                or meta.get("record_sha256") != digest(raw)):
            return fail
        sig = record["signature"]
        own_evidence = {"signer": sig.get("typed_name"), "signed_at": sig.get("at"), "address": sig.get("address"),
                        "language": sig.get("language"), "consent": sig.get("consent"),
                        "letter_sha256": digest(document), "record_sha256": digest(raw),
                        "ledger_hash": meta.get("ledger_hash"), "ledger_at": meta.get("ledger_at")}
        base = events.base_path(Path(client_dir).parent.parent)
        commitment = f"Retained signed engagement letter: PDF {digest(document)}; signing record {digest(raw)}"
        with oslock.locked(events.lock_path(base), timeout=events.LOCK_WAIT, poll=0.002):
            if not _complete_anchors(base):
                return fail | own_evidence | {"reason": "Daily anchor file is missing or malformed. Retained signer details are unverified."}
            result = ledger_seal.verify(base)
            if not result["ok"]:
                return fail | own_evidence | {"reason": "The firm's ledger does not verify. Signer details below are unverified retained evidence."}
            rows = list(events.rows(base))
            own = next((i for i, r in enumerate(rows) if r.get("hash") == meta.get("ledger_hash")), None)
            if own is None:
                return fail
            row = rows[own]
            if (row.get("case") != Path(client_dir).name or row.get("kind") != "engagement"
                    or row.get("action") != "signing_evidence" or row.get("what") != commitment
                    or row.get("at") != meta.get("ledger_at")):
                return fail
            positions = {r.get("hash"): i for i, r in enumerate(rows) if r.get("hash")}
            anchor = next((a for a in ledger_seal.read_anchors(base) if positions.get(a["hash"], -1) >= own), None)
            if anchor is None:
                return fail | own_evidence | {"reason": "Awaiting a daily ledger anchor covering this signature. Signer details below are unverified retained evidence."}
        sig = record["signature"]
        return {"ok": True, "status": "verified", "reason": "Retained letter and signing record match the R2 chain and daily anchor.",
                "signer": sig.get("typed_name"), "signed_at": sig.get("at"), "address": sig.get("address"),
                "language": sig.get("language"), "consent": sig.get("consent"), "letter_sha256": digest(document),
                "record_sha256": digest(raw), "ledger_hash": meta["ledger_hash"], "ledger_at": meta["ledger_at"],
                "anchor": {k: anchor[k] for k in ("day", "hash", "at")}}
    except (OSError, ValueError, KeyError, TypeError, TimeoutError, AttributeError):
        return fail


def certificate(client_dir: Path, letter_id: str) -> bytes:
    """A certificate page with live verification, never an assertion of legal validity."""
    from pypdf import PdfWriter
    from fill.continuation import MARGIN
    from review.bundle import _Sheet, _wrap

    result = verify(client_dir, letter_id)
    lines = ["Firm signing evidence certificate", "DRAFT wording pending attorney approval.",
             "Firm record only; no conclusion about legal validity or USCIS electronic signatures.",
             "Verification: " + result["status"], result["reason"]]
    if "signer" in result:
        lines += [f"Typed signer: {result['signer']}", f"Signed at (recorded server time): {result['signed_at']}",
                  f"Recorded network address: {result['address'] or 'not recorded'}",
                  f"Language shown: {result['language']}", "Consent recorded: " + json.dumps(result["consent"], ensure_ascii=False),
                  "Signed letter SHA256: " + result["letter_sha256"], "Signing record SHA256: " + result["record_sha256"],
                  "Own ledger row: " + result["ledger_hash"], "Ledger row time: " + result["ledger_at"],
                  "Daily anchor: " + json.dumps(result.get("anchor"), ensure_ascii=False),
                  "A typed name and recorded network address do not independently establish identity."]
    writer, page, y = PdfWriter(), _Sheet(), 740
    for text in lines:
        for part in _wrap(text, 10, 510):
            if y < 55:
                writer.add_page(page.to_page(writer))
                page, y = _Sheet(), 740
            page.text(MARGIN, y, part, "F3", 10)
            y -= 14
        y -= 8
    writer.add_page(page.to_page(writer))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()
