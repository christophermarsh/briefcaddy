"""Reads the Part 14 explanations out of the firm's own past filed I-485s into the wording library, as candidates (brief L3). Run on the firm's machine.

    python tools/import_wordings.py --by "Ana Attorney"                    # every filled I-485 in data/reference
    python tools/import_wordings.py --by "Ana Attorney" --references D:\\filed   # another folder of past filings
    python tools/import_wordings.py --by "Ana Attorney" --dry-run          # say what would be read, write nothing
    python tools/import_wordings.py --by "Ana Attorney" --office ma         # the office the wordings are for (default: the main office)

The folder is the accuracy tool's: the firm's own filled forms, one PDF per case, named for the case (`<case>.pdf` for the I-485; a PDF for another
form, `<case>.<form>.pdf`, is left alone: docs/deployment.md, "The firm's own hand-filled references"). A form that was printed and scanned has no
boxes to read and is set aside, said in words.

For each form: the edition it prints (src/fill/where.py, from the form's own pages); every entry on its Additional Information pages (Part 14: the
form's own Page Number, Part Number and Item Number boxes and the text under them, read as the product's own filler writes them: src/fill/continuation.py),
an entry that continues on the next copy of the page joined to its first; the item the entry explains, found by the edition's own numbers (an older
edition's item numbers are put on the current edition's by the table in schemas/firm/part14_item_map.json, one pair of editions at a time: an item with no pair
is kept under "to place" for an attorney to put on its answer); then the text with its slots (src/wordings.py abstract): the form's own boxes for the
client's name, A-Number, date of birth, addresses and the I-360 receipt and arrival, every date, place, name, number and receipt it can recognise, each
replaced by a blank, none kept as text. What is written is a candidate: "from a past filing, not yet approved", with the name of the file it came from (a path,
never a name read out of the form). Settings, Firm wordings lists each for an attorney to approve, edit, place or set aside; nothing is offered on a case until
approved.

It reads only the firm's references folder. It never opens a case folder (data/clients), changes no form, no case and no rule, and is never run by the product
itself or by its tests against real files (tests/test_import_wordings.py builds made-up filled forms).
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402

import clock  # noqa: E402
import events  # noqa: E402
import part14_explain as px  # noqa: E402
import wordings  # noqa: E402
from fill import continuation as cont  # noqa: E402
from fill import where  # noqa: E402

MAP = schema_path.path("firm", "part14_item_map")
MIN_TEXT = 15  # characters: a box with less than a sentence in it is not an explanation
# The client's own boxes on the form (the product's own names for them): what identifies the people in a past filing, read from the form itself, never a case folder
IDENTITY = ("applicant.family_name", "applicant.given_name", "applicant.middle_name", "applicant.a_number", "applicant.dob", "applicant.physical_street", "applicant.physical_city",
            "applicant.physical_zip", "applicant.physical_apt", "applicant.mailing_street", "applicant.mailing_city", "applicant.mailing_zip", "applicant.mailing_apt",
            "applicant.mailing_in_care_of", "applicant.daytime_phone", "applicant.mobile_phone", "applicant.email", "applicant.father_family_name", "applicant.father_given_name",
            "applicant.mother_family_name", "applicant.mother_given_name", "applicant.other_name1_family", "applicant.other_name1_given", "applicant.other_name2_family",
            "applicant.other_name2_given", "applicant.principal_family_name", "applicant.principal_given_name", "applicant.principal_a_number",
            "applicant.prior_address_street", "applicant.prior_address_city", "applicant.prior_address_zip", "applicant.employer1_name", "applicant.employer1_street",
            "applicant.employer1_city", "applicant.i94_number", "applicant.child1_family_name", "applicant.child1_given_name", "applicant.child2_family_name",
            "applicant.child2_given_name")
# the explanations' own slots, read from the form's boxes: the I-360's receipt, the last arrival, the I-94's last day
SLOT_BOXES = {"i360_receipt": "applicant.i360_receipt_number", "arrival_city": "applicant.last_arrival_city", "arrival_state": "applicant.last_arrival_state",
              "arrival_date": "applicant.last_arrival_date", "admit_until": "applicant.i94_admit_until_date"}


# -- what is on the form -------------------------------------------------------------------------------------------------------------------


def item_map() -> dict[str, Any]:
    """schemas/firm/part14_item_map.json: for each pair of editions of a form, the older edition's item numbers on the newer's."""
    return json.loads(MAP.read_text(encoding="utf-8"))


def _nearest(box, among, limit: float = 1e9):
    best = min(among, key=lambda o: abs(o.rect[0] - box.rect[0]) + abs(o.rect[1] - box.rect[1]), default=None)
    return best if best and abs(best.rect[0] - box.rect[0]) + abs(best.rect[1] - box.rect[1]) <= limit else None


def page_entries(reader, index: int) -> list[dict[str, str]]:
    """The entries on one page of a filled form that has the Additional Information boxes: [{page, part, item, text}] as the person (or the product) filled
    them, top to bottom, the left column first. The same reading of a page as the filler's (src/fill/continuation.py), on a form that is already filled."""
    found = []
    for annot in reader.pages[index].get("/Annots") or []:
        annot = annot.get_object()
        box = cont._box(annot)
        if box is not None:
            found.append((box, annot))
    kinds = {b.name: cont._kind(b) for b, _ in found}
    value = {b.name: str(cont._inherited(a, "/V") or "").replace("\r", "\n").strip() for b, a in found}
    boxes = [b for b, _ in found]
    parts = [b for b in boxes if kinds[b.name] == "part"]
    texts = [b for b in boxes if kinds[b.name] == "text"]
    items = [b for b in boxes if kinds[b.name] == "item"]
    pages = [b for b in boxes if kinds[b.name] == "page"]
    entries = []
    for part in parts:
        item, page = _nearest(part, items), _nearest(part, pages, 200)
        left = min((b for b in (page, part, item) if b), key=lambda b: b.rect[0])
        under = [t for t in texts if t.rect[0] - 3 <= left.rect[0] <= t.rect[2] and t.rect[3] <= left.rect[1] + 3]
        if not under:
            continue
        text = max(under, key=lambda t: t.rect[3])
        entries.append((left, {"page": value.get(page.name, "") if page else "", "part": value.get(part.name, ""), "item": value.get(item.name, "") if item else "",
                               "text": value.get(text.name, "")}))
    entries.sort(key=lambda e: (0 if e[0].rect[0] < cont.WIDTH / 2 else 1, -e[0].rect[1]))
    return [e for _, e in entries]


def _joined(entries: list[dict[str, str]]) -> list[dict[str, str]]:
    """An entry that goes on in the next box, headed "(continued)" with the same page, part and item, is one entry."""
    out: list[dict[str, str]] = []
    for e in entries:
        text = e["text"].strip()
        if not text:
            continue
        again = text.lower().startswith("(continued)")
        if again and out and (out[-1]["page"], out[-1]["part"], out[-1]["item"]) == (e["page"], e["part"], e["item"]):
            out[-1]["text"] += " " + text[len("(continued)"):].strip()
        else:
            out.append(dict(e, text=text))
    return out


def clean(text: str) -> str:
    """The text as a person reads it: lines the box wrapped joined into one paragraph, the blank lines between paragraphs kept."""
    paragraphs = [" ".join(p.split()) for p in text.replace("\r", "\n").split("\n\n")]
    return "\n\n".join(p for p in paragraphs if p)


def read_form(pdf: Path) -> dict[str, Any]:
    """{edition, entries, identity (the form's own boxes for who the client is), slots (its boxes for the explanations' slots), reason}: reason is why nothing
    could be read (not a form with boxes, no edition printed, no Additional Information page), else empty."""
    import compare

    out: dict[str, Any] = {"edition": None, "entries": [], "identity": {}, "slots": {}, "reason": ""}
    try:
        reader = cont._reader(pdf)
        if not (reader.get_fields() or {}):
            out["reason"] = "it has no boxes to read (a form that was printed and scanned): fill it in on screen and save it as it was"
            return out
        out["edition"] = where.template_edition(pdf)
        if not out["edition"]:
            out["reason"] = "the edition it prints could not be found, so its item numbers cannot be trusted"
            return out
        entries = []
        for index in range(len(reader.pages)):
            entries += page_entries(reader, index)
        values = compare.read_fields(pdf, reader=reader)
    except Exception as exc:  # noqa: BLE001 -- a file that cannot be opened is said in words, never an exception's
        out["reason"] = f"it could not be opened as a form ({type(exc).__name__})"
        return out
    out["entries"] = [dict(e, text=clean(e["text"])) for e in _joined(entries)]
    fmap = json.loads(schema_path.path("field_map", "i485", where.SCHEMAS).read_text(encoding="utf-8"))["fact_to_acroform"]

    def box(key: str) -> str:
        spec = fmap.get(key)
        name = where._first_field(spec) if spec else None
        return (values.get(compare.short_name(name), ("", ""))[0] if name else "").strip()

    out["identity"] = {k: v for k in IDENTITY if (v := box(k))}
    out["slots"] = {slot: v for slot, key in SLOT_BOXES.items() if (v := box(key))}
    if not out["entries"]:
        out["reason"] = "no explanation is written in its Additional Information boxes"
    return out


# -- which item, which voice, which wording ----------------------------------------------------------------------------------------------------


def place_item(edition: str, part: str, item: str) -> tuple[dict[str, Any] | None, str]:
    """(the answer on the current edition the printed item is, how it was found): by the edition's own numbers when the form is the current edition, through the
    table for the pair of editions when it is an older one, else None."""
    current = px.edition()
    part, item = str(part or "").strip(), str(item or "").strip().rstrip(".")
    if edition == current:
        new = item
        how = "the form's own numbers"
    else:
        pair = (item_map()["forms"].get("i485") or {}).get(edition)
        if not pair or pair.get("to") != current:
            return None, f"no table for the {edition} edition's item numbers"
        new = ((pair.get("items") or {}).get(part) or {}).get(item)
        if not new:
            return None, f"item {item} of the {edition} edition is not in the table"
        how = f"the table for the {edition} edition"
    found = next((x for x in px.listed(current) if x["part"] == part and x["item"] == new), None)
    return (found, how) if found else (None, f"no answer the form says to explain is at Part {part}, item {new}")


def voice_of(text: str) -> str:
    """client ("Yes, I ..." or "I ..."), office ("The applicant ...") or "" when it cannot be told."""
    t = " ".join(text.split()).lower()
    if t.startswith(("yes, i ", "i ", "no, i ")) or t.startswith("yes. i "):
        return "client"
    if t.startswith(("the applicant", "yes, the applicant", "applicant ")):
        return "office"
    return ""


def _templates(key: str | None) -> list[str]:
    """The firm's shipped wordings for the answer, in both voices (they say which words a person wrote each time)."""
    data = px.shipped()
    out = []
    for w in data["wordings"]:
        if key and w["item"] == key:
            out += list(w["text"].values())
    out += [t for s in data["sentences"].values() for t in s["text"].values()]
    return out


def candidate(entry: dict[str, str], form: dict[str, Any], file: str, office: dict[str, Any]) -> dict[str, Any]:
    """One entry as a wording to approve: its slots in place of every date, place, name, number and receipt it can recognise (none kept as text)."""
    spot, how = place_item(form["edition"], entry["part"], entry["item"])
    key = spot["key"] if spot else None
    done = wordings.abstract(entry["text"], facts=form["slots"], graph=None, restricted=True, extra_identity=form["identity"], templates=_templates(key))
    voice = voice_of(entry["text"])
    now = clock.stamp()
    current = px.edition()
    rec = {"form": wordings.FORM, "edition": current if spot else form["edition"], "key": key, "part": spot["part"] if spot else entry["part"], "item": spot["item"] if spot else entry["item"],
           "page": spot["page"] if spot else entry["page"], "voice": voice, "office": office["id"], "office_name": office["name"], "text": done["text"], "slots": done["slots"],
           "pattern": {"present": [], "absent": []}, "status": "candidate", "origin": "past_filing", "created": now, "approved": None, "parent": None, "number": 1,
           "uses": [], "edits": [], "from_file": file, "printed_as": {"edition": form["edition"], "part": entry["part"], "item": entry["item"], "page": entry["page"]},
           "placed_by": how if spot else "", "history": [{"at": now, "who": "The importer", "what": "read from a past filing: not approved yet"}]}
    rec["id"] = wordings._wording_id(rec["form"], rec["edition"] or "", key or "", voice, done["text"])
    problem = wordings.leaks(done["text"], None, restricted=True, extra_identity=form["identity"])
    return rec | ({"problem": problem} if problem else {})


def run(references: Path, clients: Path, by: str, office_id: str | None = None, dry_run: bool = False, log=print) -> dict[str, Any]:
    """Reads every filled I-485 in the references folder; writes a candidate for each explanation (unless dry_run). {files, entries, written, to_place, duplicates,
    set_aside [(file, why)]}."""
    import offices

    base = wordings.root(clients)
    every = offices.offices()
    office = next((o for o in every if o["id"] == office_id), None) if office_id else every[0]
    if office is None:
        raise ValueError(f"No office {office_id!r}: the offices are {', '.join(o['id'] for o in every)}.")
    result: dict[str, Any] = {"files": 0, "entries": 0, "written": 0, "to_place": 0, "duplicates": 0, "set_aside": []}
    known = {r["id"] for r in wordings.every(base)}
    names = sorted(p for p in Path(references).glob("*.pdf") if p.name.count(".") == 1) if Path(references).is_dir() else []
    for pdf in names:  # the I-485 is <case>.pdf: another form is <case>.<form>.pdf, left alone
        form = read_form(pdf)
        if form["reason"]:
            result["set_aside"].append((pdf.name, form["reason"]))
            log(f"Set aside {pdf.name}: {form['reason']}.")
            continue
        result["files"] += 1
        for entry in form["entries"]:
            if len(entry["text"]) < MIN_TEXT:
                continue
            result["entries"] += 1
            rec = candidate(entry, form, pdf.name, office)
            if rec.pop("problem", None):
                result["set_aside"].append((pdf.name, "an entry still held something that identifies a person after its blanks were made: it was not kept"))
                continue
            if rec["id"] in known:
                result["duplicates"] += 1
                continue
            known.add(rec["id"])
            result["written" if rec["key"] else "to_place"] += 1
            if not dry_run:
                wordings._write(base, rec)
        log(f"Read {pdf.name} ({form['edition']} edition): {len(form['entries'])} entr{'y' if len(form['entries']) == 1 else 'ies'}.")
    if not dry_run and (result["written"] or result["to_place"]):
        events.record("wordings", "imported", f"Read {result['written'] + result['to_place']} wording{'s' if result['written'] + result['to_place'] != 1 else ''} from "
                      f"{result['files']} past filed form{'s' if result['files'] != 1 else ''} (not approved yet)", home=base.parent, who=by or "The importer", role="staff", via="tool",
                      version=wordings.VERSION)
    return result


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--clients", type=Path, default=REPO / "data" / "clients", help="where the case folders are (default data/clients): only to find the library beside them; no case is opened")
    ap.add_argument("--references", type=Path, help="the folder of the firm's past filed forms (default data/reference, or I485_REFERENCE)")
    ap.add_argument("--office", help="the office the wordings are for, by its id (default: the main office)")
    ap.add_argument("--by", default="", help="who ran it, for the event ledger")
    ap.add_argument("--dry-run", action="store_true", help="say what would be read; write nothing")
    args = ap.parse_args(argv)
    logging.getLogger("pypdf").setLevel(logging.ERROR)
    import accuracy

    references = args.references or accuracy.reference_dir(args.clients)
    if not references.is_dir():
        print(f"No folder of past filings at {references}: put the firm's filled I-485s there (docs/deployment.md) or name the folder with --references.", file=sys.stderr)
        return 1
    try:
        r = run(references, args.clients, args.by, args.office, args.dry_run)
    except ValueError as exc:
        print(str(exc), file=sys.stderr)
        return 1
    verb = "Would write" if args.dry_run else "Wrote"
    print(f"{verb} {r['written']} wording{'s' if r['written'] != 1 else ''} for an attorney to approve and {r['to_place']} to place, from {r['files']} form{'s' if r['files'] != 1 else ''} "
          f"({r['entries']} entr{'y' if r['entries'] == 1 else 'ies'}; {r['duplicates']} already in the library).")
    print("An attorney approves them on Settings, Firm wordings: none is offered on a case until then.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
