"""Rebuild this case: the forms filled again with this release, and the boxes that changed shown to a person before the packet counts as ready.

A release can change what a form says (a new rule, a document read another way, a value the case settles). A packet built before it still says
what it said. After a release the case's Filing packet tab offers "Rebuild the forms": the rules this release has are applied to the case's facts
again, the forms and the packet are filled again, and the boxes that differ from what the packet was built with (read from the packet itself) are shown, the
old value and the new, each with the reason the product gives. The packet is a DRAFT until a person confirms that card. Nothing is automatic: the rebuild
is a button, the confirmation is a person's, and the documents are not read again here (the overnight run reads them again; this uses what the case holds).

The previous forms are kept until the card is confirmed: before it changes anything the rebuild copies the case's forms, the packet and its record, the facts and the
rule list into data/clients/<id>/rebuild_previous/. "Keep the previous forms" puts every one of them back (and a rebuild that fails does the same by itself);
confirming drops the copies. The rules this release has are applied the way the overnight run applies them (every rule in the product, whether or not an
attorney has approved it: approval is a label on the card a person signs off, not a gate; docs/decisions.md, K5).

The case's record is data/clients/<id>/rebuild.json (the data dictionary, src/records.py, has every field):

    {"version": 1,
     "rules_added": [{"to_version", "rules": [...]}],       the rules a rebuild brought the case, so a second filing's rebuild still says which were new
     "filings": {"<filing>": {
         "pending":   {"at", "by", "role", "from_version", "to_version", "changes": [...] | null, "confirmed": {"by", "role", "at"} | null} | null,
         "history":   [{"at", "by", "role", "action", "from_version", "to_version", "changed"}]}}}

Every rebuild, confirmation and return to the previous forms is one row in the event ledger (kind "packet").
"""

from __future__ import annotations

import json
import os
import re
from pathlib import Path
from typing import Any

import accuracy
import audit_fill
import clock
import compare
import events
import schema_path
from holders import OFFICE, producer


FILE = "rebuild.json"
VERSION = 1
UNCONFIRMED = "The forms were rebuilt after a new release and the boxes that changed have not been confirmed."


# ---------------------------------------------------------------------------------------------- the record

def _read(client_dir: Path) -> dict[str, Any]:
    try:
        data = json.loads((Path(client_dir) / FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        data = {}
    if not isinstance(data, dict):
        data = {}
    return {"version": VERSION, "rules_added": [e for e in data.get("rules_added") or [] if isinstance(e, dict)],
            "filings": {k: v for k, v in (data.get("filings") or {}).items() if isinstance(v, dict)}}


def _write(client_dir: Path, data: dict[str, Any]) -> None:
    path = Path(client_dir) / FILE
    tmp = path.with_suffix(f".{os.getpid()}.tmp")
    tmp.write_text(json.dumps(data, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)


def _filing(data: dict[str, Any], filing: str) -> dict[str, Any]:
    f = data["filings"].setdefault(filing, {})
    f.pop("as_built", None)  # an earlier shape of this record kept the boxes here; the packet itself holds them
    f.setdefault("pending", None)
    f.setdefault("history", [])
    return f


# ---------------------------------------------------------------------------------------------- what a packet was built with

def box_values(client_dir: Path, forms: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """{form id: {box: value}} for each filled form of a packet, the boxes with something in them (the comparison engine's names: src/compare.py)."""
    out: dict[str, dict[str, str]] = {}
    for f in forms:
        path = Path(client_dir) / str(f.get("file") or "")
        if not str(path).lower().endswith(".pdf") or not path.is_file():
            continue
        try:
            out[f["id"]] = {k: v for k, (v, _tip) in compare.read_fields(path).items() if v}
        except Exception:  # noqa: BLE001 -- a form that cannot be read as a form has no boxes to keep
            continue
    return out


def packet_boxes(client_dir: Path, schema: dict[str, Any], forms: list[dict[str, Any]]) -> dict[str, dict[str, str]]:
    """What the packet was built with: each form's boxes as the packet's own PDF holds them (src/packet.py puts every form's fields in it, each form but the I-485 under
    its id), so a refill made since (the overnight run's) does not change what is compared. Read when a rebuild is asked for, never at every build. A packet
    whose file is gone or cannot be read: the forms as they are in the case now."""
    import packet
    from pypdf import PdfReader

    pdf = Path(client_dir) / schema.get("packet_pdf", packet.PACKET_PDF)
    try:
        reader = PdfReader(str(pdf))
        if reader.is_encrypted:
            reader.decrypt("")
        ids = [f["id"] for f in forms]
        out = {}
        for fid in ids:
            others = tuple(f"{o}_" for o in ids if o != fid)
            found = compare.read_fields(None, prefix=f"{fid}_", reader=reader) if fid != "i485" else compare.read_fields(None, exclude=others, reader=reader)
            out[fid] = {k: v for k, (v, _tip) in found.items() if v}
        if any(out.values()):
            return out
    except Exception:  # noqa: BLE001 -- the packet's file is not there or is no form: the case's own forms stand in
        pass
    return box_values(client_dir, forms)


def built_version(manifest: dict[str, Any] | None) -> str:
    return str((manifest or {}).get("version") or "")


def _vkey(version_: str) -> tuple[int, ...]:
    return tuple(int(x) for x in re.findall(r"\d+", str(version_ or "")))


def added_since(data: dict[str, Any], built: str, fresh: set[str]) -> set[str]:
    """The rules new since the release a packet was built with: the ones this rebuild brought the case, and the ones an earlier rebuild of another filing did
    (the case's own rule list is the release's after the first, so it alone cannot say)."""
    return set(fresh) | {r for e in data.get("rules_added") or [] if _vkey(e.get("to_version")) > _vkey(built) for r in e.get("rules") or []}


# ---------------------------------------------------------------------------------------------- the previous forms, kept until a person confirms

PREVIOUS = "rebuild_previous"
KEPT = ("meta.json", "fact_graph.json", "fact_graph_reviewed.json", "flag_report.txt", "companions.json")


def _names(client_dir: Path, schema: dict[str, Any], forms: list[dict[str, Any]]) -> list[str]:
    """The files a rebuild writes: the case's facts and rule list, the flag report, each form, the packet, its record."""
    names = [*KEPT, schema.get("manifest", "packet.json"), schema.get("packet_pdf", "packet.pdf"), *[str(f["file"]) for f in forms]]
    return list(dict.fromkeys(n for n in names if (Path(client_dir) / n).is_file()))


def keep_previous(client_dir: Path, filing: str, schema: dict[str, Any], forms: list[dict[str, Any]]) -> None:
    """Copies the files a rebuild writes into rebuild_previous/, named <filing>.<file>, and lists them."""
    import shutil

    folder = Path(client_dir) / PREVIOUS
    folder.mkdir(exist_ok=True)
    try:
        os.chmod(folder, 0o700)  # working copies of a client's forms: owner-only, as the data statement promises
    except OSError:
        pass
    names = _names(client_dir, schema, forms)
    for n in names:
        target = folder / f"{filing}.{n}"
        shutil.copy2(Path(client_dir) / n, target)
        try:
            os.chmod(target, 0o600)
        except OSError:
            pass
    index = folder / f"{filing}.index.json"
    index.write_text(json.dumps({"files": names}), encoding="utf-8")
    try:
        os.chmod(index, 0o600)
    except OSError:
        pass


def previous_kept(client_dir: Path, filing: str) -> bool:
    return (Path(client_dir) / PREVIOUS / f"{filing}.index.json").is_file()


def drop_previous(client_dir: Path, filing: str) -> None:
    """The copies are no longer needed (confirmed, or put back)."""
    folder = Path(client_dir) / PREVIOUS
    for p in folder.glob(f"{filing}.*") if folder.is_dir() else []:
        p.unlink(missing_ok=True)
    try:
        folder.rmdir()  # only when no other filing's copies are in it
    except OSError:
        pass


def put_back(client_dir: Path, filing: str) -> list[str]:
    """Every file kept before the rebuild goes back where it was; the copies are dropped. Returns the file names. Nothing kept: nothing is touched."""
    import shutil

    folder = Path(client_dir) / PREVIOUS
    try:
        names = json.loads((folder / f"{filing}.index.json").read_text(encoding="utf-8"))["files"]
    except (OSError, ValueError, KeyError):
        return []
    for n in names:
        shutil.copyfile(folder / f"{filing}.{n}", Path(client_dir) / n)  # the contents only: the file keeps the mode it has
    drop_previous(client_dir, filing)
    return names


# ---------------------------------------------------------------------------------------------- the reasons

def facts_of(graph) -> dict[str, dict[str, Any]]:
    """What the reasons need of a case's facts: each fact's value, status, rule and the documents behind it. Never kept: read twice, before and after."""
    return {k: {"value": f.value, "status": f.status, "derived_by": f.derived_by, "sources": sorted({s.doc_id or "" for s in f.sources})}
            for k, f in graph.all_facts().items()}


def reason(key: str, old: dict[str, dict[str, Any]], new: dict[str, dict[str, Any]], added: set[str]) -> tuple[str, str]:
    """(a code, the reason in words) a box changed, from the fact behind it before and after; the words are the product's own."""
    from review.state import rule_info

    o, n = old.get(key) if key else None, new.get(key) if key else None
    if not key or (o is None and n is None):
        return "written", "The way the form writes this box changed in this release."
    rule = (n or {}).get("derived_by")
    if rule and (o is None or o.get("derived_by") != rule or o.get("value") != n.get("value")):
        name = rule_info(rule)["name"]
        if rule in added:
            return "rule", f"A rule new in this release: {name}."
        return "rule", f"A rule gives a different answer now: {name}."
    if o is not None and n is not None and o["status"] != "resolved" and n["status"] == "resolved":
        return "settled", "A value that was open is settled now."
    if (o or {}).get("sources") != (n or {}).get("sources"):
        return "document", "A document is read another way."
    if (o or {}).get("value") != (n or {}).get("value"):
        return "fact", "A fact the box is built from changed."
    return "written", "The way the form writes this box changed in this release."


def _box_facts(form: str) -> dict[str, str]:
    """{box short name: the fact behind it} for one form (src/accuracy.py facts_by_box); empty for a form built for one person only."""
    from fill import load_field_map
    from fill.companion import field_map_for, load_profile

    if form == "i485":
        return accuracy.facts_by_box(load_field_map(schema_path.path("field_map", "i485")), schema_path.path("template", "i485"))
    spec = load_profile()["forms"].get(form)
    return accuracy.facts_by_box(field_map_for(spec), schema_path.named(spec["template"])) if spec else {}


def changes(client_dir: Path, forms: list[dict[str, Any]], old: dict[str, dict[str, str]], new: dict[str, dict[str, str]],
            old_facts: dict[str, dict[str, Any]], new_facts: dict[str, dict[str, Any]], added: set[str], boxes: audit_fill.Boxes) -> list[dict[str, Any]]:
    """The boxes that differ between what the packet was built with and what it holds now: the form, the box in the form's own words, the old value, the new, the
    reason. A box a form repeats (the A-Number at the top of every page) is one line."""
    out: list[dict[str, Any]] = []
    files = {f["id"]: Path(client_dir) / f["file"] for f in forms}
    for form in sorted(set(old) | set(new)):
        try:
            tips = {k: tip for k, (_v, tip) in compare.read_fields(files[form]).items()} if form in files else {}
        except Exception:  # noqa: BLE001
            tips = {}
        try:
            by_box = _box_facts(form)
        except Exception:  # noqa: BLE001
            by_box = {}
        seen: set[tuple[str, str, str, str]] = set()
        for field in sorted(set(old.get(form, {})) | set(new.get(form, {}))):
            a, b = old.get(form, {}).get(field, ""), new.get(form, {}).get(field, "")
            if compare.normalize(a) == compare.normalize(b):
                continue
            key = by_box.get(field) or by_box.get(compare._WIDGET_INDEX.sub("", field), "")
            dedupe = (form, key or audit_fill._ident("", field), a, b)
            if dedupe in seen:
                continue
            seen.add(dedupe)
            ref, label = boxes.name(form, key, field, tips.get(field, ""))
            code, why = reason(key, old_facts, new_facts, added)
            out.append({"form": audit_fill.form_name(form), "box": f"{ref}: {label}" if ref and label else ref or label or field, "key": key,
                        "old": accuracy.shown(a), "new": accuracy.shown(b), "why": code, "reason": why})
    return out


# ---------------------------------------------------------------------------------------------- the gate

@producer(OFFICE)
def problems(client_dir: Path, filing: str) -> list[str]:
    """A packet is not ready while the boxes a rebuild changed have not been confirmed (src/packet.py plan)."""
    pending = ((_read(client_dir)["filings"].get(filing) or {}).get("pending")) or None
    return [UNCONFIRMED] if pending and not pending.get("confirmed") else []


def notes(client_dir: Path) -> list[str]:
    """One sentence for each filing whose rebuilt forms wait for a person (the case page)."""
    import packet

    out = []
    for filing, f in sorted(_read(client_dir)["filings"].items()):
        if (f.get("pending") or {}) and not f["pending"].get("confirmed"):
            out.append(f"The rebuilt forms for {packet.filing_title(filing)} need your confirmation: open the Filing packet tab.")
    return out


# ---------------------------------------------------------------------------------------------- the card

def card(client_dir: Path, filing: str, manifest: dict[str, Any] | None, private=None) -> dict[str, Any] | None:
    """The card for the Filing packet tab: None when no packet was built (nothing to rebuild). private(key, label): whether a value is a number the screen never
    prints in full (the review app masks it)."""
    import version

    if not manifest:
        return None
    f = _filing(_read(client_dir), filing)
    pending = f["pending"]
    built = built_version(manifest)
    stale = built != version.VERSION
    shown = None
    if pending:
        mask = private or (lambda key, label: False)
        from review.state import mask_number

        rows = [{"form": c["form"], "box": c["box"], "old": mask_number(c["old"]) if mask(c.get("key", ""), c["box"]) else c["old"],
                 "new": mask_number(c["new"]) if mask(c.get("key", ""), c["box"]) else c["new"], "reason": c["reason"], "why": c["why"]}
                for c in pending.get("changes") or []]
        shown = {"by": pending.get("by"), "at": pending.get("at"), "from_version": pending.get("from_version") or "", "to_version": pending.get("to_version"),
                 "changes": rows, "confirmed": pending.get("confirmed")}
    return {"title": "The forms and this release", "version": version.VERSION, "built_version": built, "built_at": manifest.get("built_at"), "stale": stale,
            "offer": stale and not (pending and not pending.get("confirmed")), "pending": shown,
            "previous": previous_kept(client_dir, filing) and bool(pending) and not pending.get("confirmed"),  # the previous forms are kept and can be put back
            "shows": "The forms and the packet shown are the rebuilt ones. The previous forms are kept until you confirm; \"Keep the previous forms\" puts them back.",
            "history": [{k: h.get(k) for k in ("at", "by", "action", "from_version", "to_version", "changed")} for h in f["history"][-8:]]}


# ---------------------------------------------------------------------------------------------- the writes

def refresh_derivation(client_dir: Path) -> set[str]:
    """The rules this release has, applied to the case's facts again: the case's recorded rule list becomes the release's, and the saved facts are worked out again
    from the raw ones (as src/inbox.py does for a new document). Returns the ids of the rules the case did not have. A case with no raw facts is left as it is."""
    from batch import derive
    from factgraph import FactGraph
    from review.state import _derivation, _read as read_json, _write as write_json
    from rules import ALL_RULES

    meta_path, raw = Path(client_dir) / "meta.json", Path(client_dir) / "fact_graph_raw.json"
    meta = read_json(meta_path, {})
    if "derivation" not in meta or not raw.exists():
        return set()
    spec = meta["derivation"]
    now = [r.rule_id for r in ALL_RULES]
    added = {r for r in now if r not in (spec.get("rules") or [])}
    write_json(meta_path, meta | {"derivation": spec | {"rules": now}})
    graph = FactGraph.load(raw)
    rules, policies = _derivation(meta["derivation"] | {"rules": now})
    derive(graph, rules, policies)
    graph.save(Path(client_dir) / "fact_graph.json")
    return added


def _who(by: str) -> str:
    by = str(by or "").strip()
    if not by:
        raise ValueError("Enter your name first: every rebuild records who did it.")
    return by


def start(client_dir: Path, filing: str, schema: dict[str, Any], who: str, role: str | None, build, boxes: audit_fill.Boxes, private=None) -> dict[str, Any]:
    """Rebuild the forms of one filing with this release: returns the card. build(): fills the forms and the packet again (the review app's refill and
    packet.build), called once. The packet it builds is a DRAFT until the card is confirmed."""
    import packet
    import version

    client_dir = Path(client_dir)
    who = _who(who)
    manifest = packet._read(client_dir / schema.get("manifest", "packet.json"), None)
    if not manifest:
        raise ValueError("Build the packet first: there is nothing built to rebuild yet.")
    data = _read(client_dir)
    f = _filing(data, filing)
    if f["pending"] and not f["pending"].get("confirmed"):
        raise ValueError("The last rebuild is waiting for a person to confirm the boxes that changed.")
    old_version = built_version(manifest)
    forms = [x for x in manifest.get("forms") or [] if isinstance(x, dict) and x.get("id") and x.get("file")]
    old = packet_boxes(client_dir, schema, forms)  # from the packet itself, before anything is written
    old_facts = facts_of(packet._case_graph(client_dir, schema))
    keep_previous(client_dir, filing, schema, forms)  # nothing is lost: "Keep the previous forms" puts every file back, and so does a rebuild that fails
    try:
        fresh = refresh_derivation(client_dir)
        data = _read(client_dir)
        f = _filing(data, filing)
        f["pending"] = {"at": clock.stamp(), "by": who, "role": role, "from_version": old_version, "to_version": version.VERSION, "changes": None, "confirmed": None}
        _write(client_dir, data)  # from here the packet is built as a draft (packet.plan asks problems)
        build()
        added = added_since(data, old_version, fresh)
        new = packet_boxes(client_dir, schema, forms)
        found = changes(client_dir, forms, old, new, old_facts, facts_of(packet._case_graph(client_dir, schema)), added, boxes)
    except Exception:
        put_back(client_dir, filing)
        data = _read(client_dir)
        _filing(data, filing)["pending"] = None
        _write(client_dir, data)
        raise
    stamp = clock.stamp()
    data = _read(client_dir)
    f = _filing(data, filing)
    if fresh:
        data["rules_added"].append({"filing": filing, "to_version": version.VERSION, "rules": sorted(fresh)})
    f["history"].append({"at": stamp, "by": who, "role": role, "action": "rebuilt", "from_version": old_version, "to_version": version.VERSION, "changed": len(found)})
    if found:
        f["pending"] = {"at": stamp, "by": who, "role": role, "from_version": old_version, "to_version": version.VERSION, "changes": found, "confirmed": None}
        said = f"{len(found)} box{'es' if len(found) != 1 else ''} changed"
    else:
        f["pending"] = None  # nothing differs: nothing to confirm, nothing to keep
        drop_previous(client_dir, filing)
        said = "no box changed"
    _write(client_dir, data)
    events.record("packet", "rebuilt", f"Rebuilt the forms for {packet.filing_title(filing)} with release {version.VERSION}: {said}", case_dir=client_dir, who=who, role=role)
    return card(client_dir, filing, packet._read(client_dir / schema.get("manifest", "packet.json"), None), private)


def confirm(client_dir: Path, filing: str, schema: dict[str, Any], who: str, role: str | None, build, private=None) -> dict[str, Any]:
    """A person looked at the boxes that changed and confirms them; the previous forms are dropped and the packet is built again so it is no longer a draft."""
    import packet

    client_dir = Path(client_dir)
    who = _who(who)
    data = _read(client_dir)
    f = _filing(data, filing)
    if not f["pending"] or f["pending"].get("confirmed"):
        raise ValueError("There is nothing waiting for your confirmation.")
    stamp = clock.stamp()
    f["pending"]["confirmed"] = {"by": who, "role": role, "at": stamp}
    f["history"].append({"at": stamp, "by": who, "role": role, "action": "confirmed", "from_version": f["pending"].get("from_version"),
                         "to_version": f["pending"].get("to_version"), "changed": len(f["pending"].get("changes") or [])})
    _write(client_dir, data)
    events.record("packet", "confirmed", f"Confirmed the boxes the rebuilt forms changed for {packet.filing_title(filing)}", case_dir=client_dir, who=who, role=role)
    build()
    drop_previous(client_dir, filing)
    return card(client_dir, filing, packet._read(client_dir / schema.get("manifest", "packet.json"), None), private)


def decline(client_dir: Path, filing: str, schema: dict[str, Any], who: str, role: str | None, private=None) -> dict[str, Any]:
    """"Keep the previous forms": every file the rebuild changed goes back as it was (the forms, the packet and its record, the facts and the case's rule list), nothing
    is confirmed, and the case is offered the rebuild again. Only while the rebuilt boxes wait for a person."""
    import packet

    client_dir = Path(client_dir)
    who = _who(who)
    data = _read(client_dir)
    f = _filing(data, filing)
    pending = f["pending"]
    if not pending or pending.get("confirmed") or not previous_kept(client_dir, filing):
        raise ValueError("There is nothing to put back: the previous forms are kept only until the changes are confirmed.")
    put_back(client_dir, filing)
    stamp = clock.stamp()
    f["history"].append({"at": stamp, "by": who, "role": role, "action": "kept_previous", "from_version": pending.get("from_version"), "to_version": pending.get("to_version"),
                         "changed": len(pending.get("changes") or [])})
    f["pending"] = None
    data["rules_added"] = [e for e in data["rules_added"] if not (e.get("filing") == filing and e.get("to_version") == pending.get("to_version"))]
    _write(client_dir, data)
    events.record("packet", "kept_previous", f"Kept the previous forms for {packet.filing_title(filing)}: the rebuild with release {pending.get('to_version')} was put back",
                  case_dir=client_dir, who=who, role=role)
    return card(client_dir, filing, packet._read(client_dir / schema.get("manifest", "packet.json"), None), private)
