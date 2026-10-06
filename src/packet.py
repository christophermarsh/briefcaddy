"""The ready-to-file packet: one PDF a paralegal prints, has signed, and mails.

    index sheet (who, what, which pages) -> the filled I-485 (+ copies of
    its Part 14 page) -> exhibits in USCIS evidence order

Which documents go in, and where, is schemas/packets/i485.json; a paralegal can
move a document into another exhibit or leave it out (packet_choices.json
in the client's folder), and the index lists what was left out so nothing
disappears silently. A completed USCIS form found in the folder (an
earlier filing) is never put in the packet (docs/decisions.md).

Alongside the PDF goes a "before you mail it" checklist -- signatures,
photos, the sealed medical exam, missing evidence -- shown in the review
app, never mailed. The packet is marked DRAFT on its index sheet until
every review item is closed and every required exhibit is present.

Nothing here reads new facts: the packet is built from the pipeline's own
output (meta.json, i485_filled.pdf) and the client's source files.

Each filing has its own schema (FILINGS): the I-485 packet (packet.json)
and, the step before it for an SIJ client, the I-360 petition
(packet_i360.json, src/i360.py) -- its own forms, exhibits, cover letter and
output files, built by the same code.
"""

from __future__ import annotations

import hashlib
import importlib
import io
import json
import re
import textwrap
from datetime import date
from pathlib import Path
from typing import Any

from pypdf import PdfReader, PdfWriter

from batch import PRIOR_FORM_TYPES
from fill.continuation import COPY_MARK, HEIGHT, MARGIN, WIDTH, _Page
import clock
import events
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, holder_of

REPO = Path(__file__).resolve().parents[1]
SCHEMA = schema_path.path("packet", "i485")
PACKET_PDF = "packet.pdf"
CHOICES = "packet_choices.json"
FILINGS = {"i485": "packet.json", "i360": "packet_i360.json", "family": "packet_family.json", "n400": "packet_n400.json", "i589": "packet_i589.json",
           "i90": "packet_i90.json", "i131": "packet_i131.json", "n600": "packet_n600.json", "i751": "packet_i751.json",
           "eoir28": "packet_eoir28.json", "visa": "packet_visa.json", "ead": "packet_ead.json",
           "address": "packet_address.json", "asylee": "packet_asylee.json",
           "i290b": "packet_i290b.json", "n336": "packet_n336.json", "bia": "packet_bia.json", "i601a": "packet_i601a.json", "i912": "packet_i912.json", "expedite": "packet_expedite.json",
           "cancellation": "packet_cancellation.json"}
FILINGS["caa"] = "packet_caa.json"  # a green card under the Cuban Adjustment Act or as a HRIFA dependent (src/cuban_adjustment.py)
FILINGS["vawa"] = "packet_vawa.json"  # the VAWA self-petition (src/vawa.py)
FILINGS["i914"] = "packet_i914.json"  # the T visa (src/t_visa.py)
FILINGS["i914b"] = "packet_i914b.json"  # its Supplement B request
FILINGS["u_cert"] = "packet_u_cert.json"  # the U visa certification request
FILINGS["u_visa"] = "packet_u_visa.json"  # the U visa petition
FILINGS["daca"] = "packet_daca.json"  # a DACA renewal: I-821D, I-765 (c)(33), I-765WS (src/daca.py)
FILINGS["i730"] = "packet_i730.json"  # the relative petition (src/i730.py)
FILINGS["court_bond"] = "packet_court_bond.json"  # a bond request to the immigration judge (src/bond.py)
FILINGS["court_motion"] = "packet_court_motion.json"  # a motion to reopen or reconsider before the judge (src/court_motion.py)
FILINGS["i601"] = "packet_i601.json"  # the waiver of inadmissibility, with the I-212 when they go together (src/inadmissibility_waiver.py)
FILINGS["i212"] = "packet_i212.json"  # permission to reapply after removal (src/reapply.py)
FILINGS["n565"] = "packet_n565.json"  # a replacement naturalization or citizenship certificate (src/n565.py)
FILINGS["g639"] = "packet_g639.json"  # a FOIA request for the client's USCIS file (src/g639.py)
FILINGS["tps"] = "packet_tps.json"  # a TPS registration or re-registration: I-821 with the I-765 (src/tps.py)
FILINGS["parole"] = "packet_parole.json"  # humanitarian parole for someone outside the U.S.: I-131 and I-134 for each person (src/parole.py)
LEAVE_OUT = "leave_out"
# The signature boxes, by AcroForm field name: their pages are read from the
# filled form itself, so a new edition that moves them stays right.
APPLICANT_SIGNATURE_FIELD = "Pt3Line7a_Signature"
PREPARER_SIGNATURE_FIELD = "P12Line6_SignaturePreparer"
I485 = {"short": "I-485", "title": "Form I-485, Application to Register Permanent Residence or Adjust Status", "output": "i485_filled.pdf",
        "signatures": {"client": [APPLICANT_SIGNATURE_FIELD, "Part 10, the applicant's signature"],
                       "attorney": [PREPARER_SIGNATURE_FIELD, "Part 12, the preparer's signature"]}}


def forms_in(schema: dict[str, Any]) -> list[tuple[str, dict[str, Any]]]:
    """The USCIS forms in the packet, in order: the I-485 and its companion
    forms (src/fill/companion.py)."""
    from fill.companion import load_profile

    companions = load_profile()["forms"]
    generated = schema.get("generated") or {}  # pages the filing's module writes itself (the visa answer sheet)
    return [(fid, generated[fid] if fid in generated else I485 if fid == "i485" else companions[fid] if fid in companions else instance(fid, companions))
            for fid in schema.get("forms", ["i485"])]


def instance(fid: str, companions: dict[str, Any]) -> dict[str, Any]:
    """A form filed once per person -- "i864a_2", the second household member's I-864A; "i730_1", the first relative's I-730 --
    as its own copy of the companion form, with its own file, name and title."""
    m = re.fullmatch(r"(?P<base>[a-z0-9_]+)_(?P<n>\d+)", fid)
    if not m or m["base"] not in companions:
        raise KeyError(f"{fid} is not a form")
    form, n = companions[m["base"]], m["n"]
    short = f"{form['short'][:-1]}, {n})" if form["short"].endswith(")") else f"{form['short']} ({n})"
    return {**form, "output": f"{fid}_filled.pdf", "short": short, "title": f"{form['title']} ({n})", "instance_of": m["base"], "instance": int(n)}


def form_output(fid: str) -> str:
    """The filled file of a companion form, or of one person's copy of it (the review app's "Open")."""
    from fill.companion import load_profile

    companions = load_profile()["forms"]
    return companions[fid]["output"] if fid in companions else instance(fid, companions)["output"]


def _person_module(schema: dict[str, Any]):
    """The module that knows a filing's people (person_graph, case_schema): the family packet's (src/family.py) or the filing's own."""
    import filing_questions

    if schema.get("filing") == "family":
        import family

        return family
    return filing_questions.module(schema.get("filing", ""))


def _read(path: Path, default: Any) -> Any:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else default


def load_schema(path: Path = SCHEMA) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def filing_title(filing: str | None) -> str:
    """The filing in the words a person reads ("Provisional unlawful presence waiver (I-601A)"), never its id (the I-485's schema has no title of its own)."""
    try:
        title = load_filing(filing).get("title")
    except Exception:  # noqa: BLE001 -- a sentence for the event ledger must never stop a packet
        title = None
    return title or ("Form I-485 (adjustment of status)" if (filing or "i485") == "i485" else "a filing")


def load_filing(filing: str | None) -> dict[str, Any]:
    """The schema for a filing: "i485" (default) or "i360"."""
    if (filing or "i485") not in FILINGS:
        raise ValueError(f"unknown filing {filing!r} (expected one of {', '.join(FILINGS)})")
    return load_schema(schema_path.path("packet", filing or "i485"))


def _enotice_check(schema: dict[str, Any]) -> list[dict[str, str]]:
    """The G-1145 line of the checklist, when this packet carries one (src/enotice.py)."""
    import enotice

    return [{"kind": "attach", "text": "Form G-1145 (filled): clipped on top of the package, so USCIS e-mails the receipt number."}] \
        if enotice.wanted(schema) else []


def _client_state(client_dir: Path) -> str | None:
    """The client's state, for the office that files for it (src/offices.py)."""
    from review.state import _read

    facts = (_read(Path(client_dir) / "fact_graph.json", {}) or {}).get("facts") or {}
    fact = facts.get("applicant.physical_state") or {}
    return fact.get("value") if fact.get("status") == "resolved" else None


def _letter_config(schema: dict[str, Any], client_dir: Path | None = None) -> dict[str, Any]:
    """The cover letter's settings -- and for a filing with its own module
    (src/filing_questions.py), what the case decides: the address (by the
    document type, the state, the receipt number), the fee, the subject."""
    from fill.cover_letter import load_config

    name = schema.get("cover_letter")
    config = load_config(schema_path.named(name)) if isinstance(name, str) else load_config()
    if client_dir is not None:  # the case's office: its letterhead address and signer (src/offices.py)
        import offices

        config = offices.letter(config, client_dir, _client_state(client_dir))
    import filing_questions

    mod = filing_questions.module(schema.get("filing", ""))
    if client_dir is not None and mod is not None and hasattr(mod, "letter"):
        config = config | mod.letter(filing_questions.graph_for(schema["filing"], client_dir, _today()), _today())
        drop = set(config.pop("drop_documents", []))  # e.g. no photos with a reentry permit
        config["documents"] = [d for d in config.get("documents", []) if d[0] not in drop]
    if client_dir is not None and "{case_paragraph}" in str(config.get("intro") or "") and (client_dir / "fact_graph.json").exists():
        import drafting  # the facts this case's letter states, each from a fact with its source (the I-589, I-918, I-914, VAWA I-360)

        config["case_paragraph"] = drafting.cover_paragraph(client_dir, schema.get("filing", ""))["text"]
    if client_dir is not None and schema.get("filing") == "family" and (client_dir / "fact_graph.json").exists():  # Supplement A, I-864EZ, I-864A
        import family

        config = family.letter(config, _case_graph(client_dir, schema), schema, _today())
    if schema.get("fee_waiver"):  # src/fee_waiver.py: the I-912 goes inside, the waived fee isn't paid
        import fee_waiver

        form = fee_waiver.ELIGIBLE.get(schema.get("filing", ""), "the application")
        kept = [p for p in (_payments(client_dir, schema) if client_dir is not None else []) if "Pub. L." in p["what"]]
        config = config | {"fees": f"A request for a fee waiver (Form I-912), with its supporting evidence, is enclosed for Form {form}."
                                   + (f" The Pub. L. 119-21 fee of ${kept[0]['amount']:,}, which cannot be waived, is paid by the enclosed Form G-1450."
                                      if kept else ""),
                           "no_payment": not kept, "fees_when": [], "forms": {**config.get("forms", {}), "i912": "Applicant’s I-912 - Request for Fee Waiver"},
                           "form_order": [*config.get("form_order", []), "i912"]}
    return config


def _letter(n: int) -> str:
    return chr(ord("A") + n) if n < 26 else f"A{chr(ord('A') + n - 26)}"


def _pages_of(doc: str) -> tuple[str, range | None]:
    """'file.pdf#p3-4' -> ('file.pdf', pages 2..3, 0-based); 'file.pdf' -> ('file.pdf', None)."""
    name, _, part = doc.partition("#")
    m = re.fullmatch(r"p(\d+)(?:-(\d+))?", part)
    if not m:
        return name, None
    first = int(m.group(1)) - 1
    return name, range(first, int(m.group(2) or m.group(1)))


def _order_key(order: list[str], doc: str, doc_type: str) -> tuple:
    # originals before their translations; the translator's certificate last
    translation = 1 if re.search(r"transl|tradu", doc, re.I) else 0
    return (order.index(doc_type) if doc_type in order else len(order), translation, doc.lower())


def variant_of(filing: str | None, client_dir: Path) -> str | None:
    """Which of a filing's variants this case files now (its schema's "variants"), from the reviewed case:
      i589 -- "in_court": in immigration court the I-589 goes to the judge, not USCIS (DHS's instructions for applications in court);
      family -- "petition_only": a preference case whose date isn't current files the I-130 alone (it sets the priority date);
                "i485_only": the I-130 is already on file, the I-485 follows it (src/preference.py);
      cancellation -- "eoir42a": a permanent resident applies on the EOIR-42A, not the EOIR-42B (src/cancellation.py)."""
    from review.state import reviewed_graph

    if filing == "cancellation":
        import cancellation

        return "eoir42a" if cancellation.is_42a(cancellation.derive(reviewed_graph(client_dir), _today())) else None
    if filing == "i589":
        import asylum

        return "in_court" if asylum.where_to_file(asylum.derive(reviewed_graph(client_dir)))["with"] == "court" else None
    if filing == "family":
        import family
        import preference

        return preference.packet_variant(family.derive(reviewed_graph(client_dir)), _today())
    if filing == "caa":  # "no_ead": no work permit asked for in the same envelope (src/cuban_adjustment.py)
        import cuban_adjustment

        return cuban_adjustment.packet_variant(reviewed_graph(client_dir))
    if filing == "vawa":  # "concurrent": the I-485 in the same envelope; "i485_only": the I-485 on an I-360 already filed (src/vawa.py)
        import vawa

        return vawa.variant(reviewed_graph(client_dir))
    return None


def for_case(schema: dict[str, Any], client_dir: Path | None) -> dict[str, Any]:
    """The filing as this case files it: the schema with its variant for the case (cover letter, forms, hand work) laid over it,
    and a fee waiver request (Form I-912, src/fee_waiver.py) inside it when one was prepared for this filing."""
    variants = schema.get("variants") or {}
    base = {k: v for k, v in schema.items() if k != "variants"}
    known = client_dir is not None and (client_dir / "fact_graph.json").exists()
    chosen = variant_of(schema.get("filing"), client_dir) if variants and known else None
    if chosen in variants:
        base = base | variants[chosen] | {"variant": chosen}
    import filing_questions

    mod = filing_questions.module(schema.get("filing", ""))
    if known and mod is not None and hasattr(mod, "case_forms"):  # forms the case decides (src/t_visa.py: a Supplement A per family member, the I-192)
        from review.state import reviewed_graph

        base = base | {"forms": mod.case_forms(base.get("forms", []), filing_questions.derive(mod, client_dir, reviewed_graph(client_dir), _today()))}
    if known and mod is not None and hasattr(mod, "forms_for"):  # forms only some cases file (the U petition's Supplements A, its I-192)
        from review.state import reviewed_graph

        base = base | {"forms": mod.forms_for(reviewed_graph(client_dir), base.get("forms", []))}
    people = _person_module(base)
    if known and people is not None and hasattr(people, "case_schema"):  # the forms this case adds: Supplement A, an I-864A per member, an I-730 per relative
        base = people.case_schema(base, _case_graph(client_dir, base), _today())
    import fee_waiver

    if known and schema.get("filing") in fee_waiver.ELIGIBLE:
        from review.state import reviewed_graph

        if fee_waiver.applies(schema["filing"], reviewed_graph(client_dir)):
            base = base | {"fee_waiver": True, "forms": [*base.get("forms", []), "i912"],
                           "handwork": [*base.get("handwork", []), {"kind": "attach", "text": "The fee waiver's evidence (Form I-912): the benefit letter, "
                                                                    "the tax transcripts or income proof, or the hardship evidence: each basis claimed."}]}
    return base


def plan(client_dir: Path, row: dict[str, Any], schema: dict[str, Any] | None = None, _building: bool = False, light: bool = False) -> dict[str, Any]:
    """What the packet would hold right now, without building it.

    row is the client's dashboard row (review/overview.review_row): its
    summary and open-item counts decide whether the packet is ready.

    light: only what holds the packet ("ready", "problems", "held"): the forms' page layouts, the payments, the blank boxes, the checklist and the
    translations' status, which nothing in "problems" reads and which cost a PDF reading for each form, are left empty (the day plan, src/day_plan.py,
    reads this for every case; tests/test_day_plan.py checks that its problems are the full plan's)."""
    schema = for_case(schema or load_schema(), client_dir)
    meta = _read(client_dir / "meta.json", {})
    choices = _read(client_dir / schema.get("choices", CHOICES), {}).get("files", {})
    folder = Path(meta.get("source_folder") or "")
    by_type = {t: ex["id"] for ex in schema["exhibits"] for t in ex["types"]}
    exhibits = {ex["id"]: {**ex, "files": []} for ex in schema["exhibits"]}
    left_out, unsorted, gone = [], [], []
    # The document record (src/documents.py): the same file uploaded twice goes in once, and the exhibit
    # roles a document fills place it. A reviewer's tag ("this screenshot is a joint lease": good_faith)
    # goes to the exhibit named for the role or listing it under "roles"; a type's own roles
    # (schemas/registers/document_types.json) only to an exhibit that lists them under "roles" -- an exhibit's id
    # alone ("presence" in the T visa packet: presence on account of trafficking) is not a legal category.
    import documents

    records = documents.by_doc(client_dir) if (client_dir / documents.FILE).exists() else {}
    dupes = documents.duplicates(client_dir, exists=lambda d: (folder / _pages_of(d)[0]).is_file())  # the copies still in the folder
    own_only = schema.get("filing", "i485") in ("i485", "i360")  # the client's own green card and SIJ petition: another person's documents are not exhibits

    def by_role(roles: list[str], named: bool) -> str | None:
        return next((ex["id"] for role in roles for ex in schema["exhibits"] if (named and ex["id"] == role) or role in ex.get("roles", [])), None)

    for doc, doc_type in sorted((meta.get("classifications") or {}).items()):
        name, pages = _pages_of(doc)
        entry = {"doc": doc, "type": doc_type, "path": str(folder / name), "pages": [p + 1 for p in pages] if pages else None}
        record = records.get(doc) or {}
        tagged = by_role([t["role"] for t in record.get("tags") or [] if t.get("role")], named=True)
        if doc_type in PRIOR_FORM_TYPES:
            left_out.append({**entry, "why": "A completed USCIS form from an earlier filing, never part of this packet. The attorney decides what to do with it.",
                             "locked": True})
            continue
        if not (folder / name).is_file():
            gone.append({**entry, "why": "No longer in the client's folder: it was moved or deleted after processing.", "locked": True})
            continue
        if doc in dupes:
            left_out.append({**entry, "why": "The same file was sent twice: the other copy goes in the packet.", "locked": True})
            continue
        if own_only and record.get("person_set_by") and record.get("person") not in ("applicant", "unknown", None, ""):
            # a person said this is someone else's (the spouse's Social Security card): the green card and SIJ packets hold the client's own papers
            left_out.append({**entry, "why": f"Set to {documents.person_words(record['person'])} by {record['person_set_by'].get('who') or 'a person'}: it goes only "
                                             "where a filing asks for that person's documents, never in the client's own packet.", "locked": True})
            continue
        choice = choices.get(doc)
        if choice == LEAVE_OUT:
            left_out.append({**entry, "why": "Left out by the paralegal."})
        elif choice in exhibits:
            exhibits[choice]["files"].append({**entry, "moved": True})
        elif tagged:  # a reviewer said what it shows ("this screenshot is a joint lease")
            exhibits[tagged]["files"].append({**entry, "tagged": True})
        elif doc_type in by_type:
            exhibits[by_type[doc_type]]["files"].append(entry)
        elif doc_type in schema["leave_out"]:
            left_out.append({**entry, "why": schema["leave_out"][doc_type]})
        elif role_exhibit := by_role(documents.type_info(doc_type).get("roles") or [], named=False):
            exhibits[role_exhibit]["files"].append(entry)
        elif not schema["exhibits"]:  # a form filed alone (the EOIR-28)
            left_out.append({**entry, "why": "This filing goes without supporting documents."})
        else:
            unsorted.append({**entry, "why": "Not recognised: choose an exhibit or leave it out."})

    import translation

    for ex in exhibits.values():  # a signed translation and its certificate go in beside the original they translate (src/translation.py)
        ex["files"] += translation.signed_entries(client_dir, ex["files"], records)
    import drafting

    declared = drafting.exhibit_for(schema, exhibits)  # the client's declaration, once the attorney marked it final (src/drafting.py)
    if declared:
        exhibits[declared]["files"] += drafting.exhibit_entries(client_dir, schema.get("filing", ""))
    for ex in exhibits.values():
        ex["files"].sort(key=lambda f: _order_key(ex["types"], f["doc"], f["type"]))
    present = [ex for ex in exhibits.values() if ex["files"]]
    for n, ex in enumerate(present):
        ex["letter"] = _letter(n)
    missing = [ex for ex in exhibits.values() if ex.get("required") and not ex["files"]]
    # The gate and "the client has no such document" (src/absence.py): a required paper marked absent, with a reason, passes only where this filing's
    # schema says it may be absent (its exhibit's "may_be_absent", each paper with the instruction line it rests on); every other stays missing and says why.
    import absence

    marked = absence.marks(client_dir) if (client_dir / "decisions.json").exists() else {}
    verdicts = {ex["id"]: absence.judge(ex, marked) for ex in missing}
    not_available = [{"id": ex["id"], "title": ex["title"], "papers": [absence.name(p) for p in verdicts[ex["id"]]["papers"]]}
                     for ex in missing if verdicts[ex["id"]]["state"] == "pass"]
    missing = [ex for ex in missing if verdicts[ex["id"]]["state"] != "pass"]

    filled = client_dir / schema.get("requires", "i485_filled.pdf")
    forms = []
    for fid, form in forms_in(schema):
        path = client_dir / form["output"]
        forms.append({"id": fid, "short": form["short"], "title": form["title"], "file": form["output"], "exists": path.exists(),
                      "signatures": form["signatures"], "attorney_completes": form.get("attorney_completes", []),
                      "layout": form_layout(path, form["signatures"]) if path.exists() and not light else None,
                      # a court filing's proposed order and proof of service go below the evidence (Practice Manual 3.2(e), 3.3(c)(i))
                      "after_exhibits": fid in schema.get("after_exhibits", [])})
    layout = next((f["layout"] for f in forms if f["id"] == "i485" and f["layout"]), None) or {"client": 0, "attorney": 0, "continuation": 0}
    open_items = {k: int(row.get(k) or 0) for k in ("blocking", "fix", "check", "attorney")}
    open_total = sum(open_items.values())
    problems = []
    import document_instances
    problems += [held(OFFICE, message) for message in document_instances.problems(client_dir)]
    problems += [held(OFFICE, message) for message in __import__('subject_attribution').problems(client_dir)]
    problems += [held(OFFICE, message) for message in __import__('critical_review').problems(client_dir)]
    if not filled.exists():
        problems.append(held(OFFICE, schema.get("requires_message", "The I-485 hasn't been filled yet: process the client first.")))
    if schema.get("filing") == "i360":  # the 21st birthday, Part 8's answers, an I-360 already approved
        import i360

        problems += i360.problems(client_dir)
    if schema.get("filing") == "family":  # the petition's questions, the petitioner's proof, the sponsor's income, the fees
        import family

        problems += family.problems(client_dir)
    if schema.get("filing") == "n400":  # when the client can file, trips, Part 9, the folder vs the answers (src/naturalization.py)
        import naturalization

        problems += naturalization.problems(client_dir)
    if schema.get("filing", "i485") == "i485" and (client_dir / "fact_graph.json").exists():  # in court, the I-485 is the judge's (src/journey.py)
        import journey

        problems += journey.i485_court_problem(client_dir)
    if schema.get("filing") == "i589":  # what USCIS rejects outright, the 1-year deadline, where it is filed (src/asylum.py)
        import asylum

        problems += asylum.problems(client_dir)
    import filing_questions

    if filing_questions.module(schema.get("filing", "")):  # I-90, I-131, N-600, I-751 (src/filing_questions.py)
        problems += filing_questions.problems(schema["filing"], client_dir)
    if schema.get("fee_waiver"):  # the I-912 inside this filing
        problems += filing_questions.problems("i912", client_dir)
    problems += drafting.problems(client_dir, schema.get("filing", ""))  # the declaration marked final: signed? changed since?
    if "i485" in schema.get("forms", ["i485"]) and filled.exists():  # every Part 9 Yes the form says to explain has an approved explanation (src/part14_explain.py)
        import part14_explain

        problems += part14_explain.problems(client_dir)
    if open_total:  # the cards the paralegal works are the office's; cards only an attorney may settle are the attorney's (src/approvals.py lists those, so the day's list counts them there)
        office_cards = open_items["fix"] + open_items["check"]
        text = f"{open_total} review card{'s' if open_total != 1 else ''} still open ({', '.join(f'{v} {k}' for k, v in open_items.items() if v)})."
        problems.append(held(OFFICE, text, n=office_cards) if office_cards else held(ATTORNEY, text, via="card", n=0))
    problems += [held(OFFICE, f"The card \"{title}\" is not saved yet: the client's name on every form waits on it.") for title in row.get("names_open") or []]
    problems += [held(CLIENT, f"Missing: {ex['title']}." + (f" {verdicts[ex['id']]['sentence']}" if verdicts[ex["id"]].get("sentence") else ""),  # a paper the client sends
                      say=f"Missing: {ex['title']}.") for ex in missing]
    problems += [held(OFFICE, f"Not found in the client's folder: {f['doc']}.", say="A document was moved or deleted after the case was processed.") for f in gone]
    import offices

    problems += offices.problems(client_dir, _client_state(client_dir))  # the office the case is filed from: its attorney and address
    import g28

    g28_forms = [f["id"] for f in forms if f["id"] not in (schema.get("generated") or {})]
    problems += g28.problems(client_dir, g28_forms)  # the G-28's card, confirmed (src/g28.py)
    if not _building and (stale := g28.stale_since(client_dir, g28_forms, _read(client_dir / schema.get("manifest", "packet.json"), None))):
        problems.append(held(OFFICE, stale))  # a packet built before a change to the G-28's choices: rebuilt before it is ready
    import rebuild

    problems += rebuild.problems(client_dir, schema.get("filing", "i485"))  # forms rebuilt after a release: the boxes that changed, confirmed (src/rebuild.py)
    if unsorted:
        problems.append(held(OFFICE, f"{len(unsorted)} document{'s' if len(unsorted) != 1 else ''} not sorted into an exhibit or left out."))
    letter_on, visa_month, letter_config = bool(schema.get("cover_letter")), None, None
    if letter_on:  # the priority date must be current this month; the address must be known (src/fill/cover_letter.py)
        from fill.cover_letter import case_facts, gaps, mail_to, priority

        letter_config = _letter_config(schema, client_dir)
        facts = case_facts(client_dir)
        pd, letter_problems = priority(facts, letter_config, _today())
        problems += letter_problems + mail_to(letter_config, facts)[1]
        problems += gaps({"forms": forms, "exhibits": present}, facts, letter_config)  # a letter never goes out with a bracketed gap
        visa_month = pd["month"]
    import payment

    pays = _payments(client_dir, schema) if filled.exists() and not light else []
    blanks = _blank_boxes(client_dir, schema) if filled.exists() and not light else []
    held_lines = [{"text": str(p), "holder": holder_of(p)} for p in problems]  # beside each line, who can clear it (src/holders.py): the lines themselves are the index sheet's and do not change
    if light:
        return {"summary": row.get("summary") or {}, "ready": not problems, "problems": problems, "held": held_lines, "filing": schema.get("filing", "i485")}

    return {
        "summary": row.get("summary") or {},
        "ready": not problems,
        "problems": problems,
        "held": held_lines,
        "exhibits": present,
        "missing": [{"id": ex["id"], "title": ex["title"], **({"why": verdicts[ex["id"]]["sentence"]} if verdicts[ex["id"]].get("sentence") else {})} for ex in missing],
        "not_available": not_available,  # required papers the client has none of, which this filing may do without (the checklist says so)
        "blank_boxes": blanks,  # every box of the filled forms with neither a value nor a mark, by form and item (src/absence.py)
        "left_out": left_out + gone,
        "unsorted": unsorted,
        "choices": [{"id": ex["id"], "title": ex["title"]} for ex in schema["exhibits"]],
        "continuation_sheets": layout["continuation"],
        "layout": layout,
        "forms": forms,
        "cover_letter": letter_on,
        "index_sheet": schema.get("index_sheet", True),
        "checklist": (_enotice_check(schema)
                      + payment.checklist([p | {"short": f"G-1450 ({p['form']}, ${p['amount']:,})"} for p in pays])
                      + (_letter_checklist(visa_month, letter_config) if letter_on else [])
                      + _checklist(present, missing, unsorted, forms, _read(client_dir / "companions.json", {}), schema,
                                   translation.checklist(client_dir, present, records), {ex["id"]: v.get("sentence") for ex in missing for v in [verdicts[ex["id"]]]})
                      + absence.checklist(schema, marked)
                      + [{"kind": "missing", "text": f"{b['form']}{', ' + b['where'] if b['where'] else ''}: {b['what']}: blank, with no value and no mark. {b['fix']}"}
                         for b in blanks if b["form_id"] == "i485"]  # the companion forms' blanks are listed by _checklist
                      + drafting.checklist(client_dir, schema.get("filing", ""))) if filled.exists() else [],
        "translations": translation.status(client_dir, present, records),  # foreign-language documents: the translation, the certificate, who signed
        "payments": [{"form": p["form"], "amount": p["amount"], "what": p["what"]} for p in pays],
        "built": _read(client_dir / schema.get("manifest", "packet.json"), None),
        "filing": schema.get("filing", "i485"),
        "filing_title": schema.get("title", "I-485 packet"),
    }


def _blank_boxes(client_dir: Path, schema: dict[str, Any]) -> list[dict[str, str]]:
    """Every box of the filled forms with neither a value nor a mark, by form and item (src/absence.py): the paper's boxes the filing asks about, what the
    I-485's completeness check lists, and what each companion form left blank. A list that cannot be worked out is empty: never a failed packet screen."""
    import absence

    try:
        graph = _case_graph(client_dir, schema) if "i485" in schema.get("forms", []) else None
        return absence.blank_boxes(client_dir, schema, _read(client_dir / "companions.json", {}), graph)
    except Exception:  # noqa: BLE001
        return []


def form_layout(pdf: Path, signatures: dict[str, list[str]] | None = None) -> dict[str, int]:
    """1-based form pages of each signature box ({signer: page}), and how
    many copies of the form's Part 14 page follow it."""
    signatures = signatures or I485["signatures"]
    reader = PdfReader(str(pdf))
    out = {signer: 0 for signer in signatures} | {"continuation": 0}
    exact: dict[str, int] = {}
    for n, page in enumerate(reader.pages, start=1):
        widgets = [a.get_object() for a in page.get("/Annots") or []]
        names = {str(w.get("/T") or (w.get("/Parent").get_object().get("/T") if w.get("/Parent") else "") or "") for w in widgets}
        joined = " ".join(names)
        for signer, (field, _label) in signatures.items():
            if field in names:  # the box itself: the N-400 has three "P12_SignatureApplicant" boxes on two pages
                exact.setdefault(signer, n)
            if field.split("[")[0] in joined:
                out[signer] = n
        if any(COPY_MARK in n for n in names):  # a copy of the form's own Part 14 page (fill/continuation.py)
            out["continuation"] += 1
    return out | exact


_FACT_NAMES = {"ssn": "the Social Security number", "a_number": "the A-Number", "dob": "the date of birth", "i94_number": "the I-94 number",
               "travel_document_number": "the passport number", "i94_arrival_date": "the date of last entry", "last_arrival_date": "the date of last entry",
               "mail_street": "the client's mailing address on the G-28 (item 13)", "mail_city": "the client's mailing city on the G-28 (item 13)",
               "mail_state": "the client's mailing state on the G-28 (item 13)", "mail_zip": "the client's mailing ZIP code on the G-28 (item 13)"}


def _today() -> date:
    return clock.today()


def _letter_checklist(month: str | None, config: dict[str, Any] | None = None) -> list[dict[str, str]]:
    """The cover letter says these are enclosed: they're printed and added by hand."""
    config = config or {"visa_bulletin": {}}
    if config.get("checklist"):
        return [{k: v for k, v in c.items() if k != "unless"} | {"text": c["text"].replace("{month}", month or "this month")}
                for c in config["checklist"] if not (c.get("unless") and config.get(c["unless"]))]  # no fee line when nothing is paid
    out = [{"kind": "sign", "text": "Cover letter: the attorney signs it."}]
    if "visa_bulletin" in config:
        out.append({"kind": "attach", "text": f"Print and enclose the USCIS filing-chart page and the Department of State Visa Bulletin for {month or 'this month'} (the letter says they are enclosed).",
                    "links": [["USCIS filing charts", "https://www.uscis.gov/green-card/green-card-processes-and-procedures/visa-availability-priority-dates/"
                                                      "adjustment-of-status-filing-charts-from-the-visa-bulletin"],
                              ["Visa Bulletin", "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"]]})
    out.append({"kind": "attach", "text": "Print and enclose the USCIS Fee Calculator page showing that no fee is due (the letter says it is enclosed).",
                "links": [["USCIS Fee Calculator", "https://www.uscis.gov/feecalculator"]]})
    return out


SIGNERS = {"supporter": "The financial supporter", "client": "The client", "attorney": "The attorney", "petitioner": "The petitioner", "spouse": "The spouse",
           "member": "The family member", "certifier": "The certifying official", "household member": "The household member"}


def _checklist(exhibits: list[dict], missing: list[dict], unsorted: list[dict], forms: list[dict], companions: dict,
               schema: dict[str, Any] | None = None, translations: list[dict] | None = None, why_missing: dict[str, str | None] | None = None) -> list[dict[str, str]]:
    """What the packet can't do by itself: ink, photos, the medical exam --
    and what the companion forms couldn't fill from the case. The hand work
    is the filing's own ("handwork" in its schema); the I-485 packet's is the
    default. translations: the lines for the foreign-language documents (src/translation.py checklist), which
    stand in for the generic birth certificate line when they cover it."""
    items = []
    for form in forms:
        for signer, (_field, label) in form["signatures"].items():
            page = (form["layout"] or {}).get(signer)
            where = f" (form page {page})" if page else ""
            who = SIGNERS.get(signer, f"The {signer}")
            items.append({"kind": "sign", "text": f"{form['short']}: {who} signs and dates {label}{'' if signer == 'attorney' else ' in ink'}{where}."})
        continuation = (form["layout"] or {}).get("continuation")
        if continuation:
            items.append({"kind": "sign", "text": f"{form['short']}: The client signs and dates each copy of the form's Part 14 page ({continuation} cop{'ies' if continuation != 1 else 'y'}, right after the form)."})
    ids = {f["id"] for f in forms}
    import filing_questions

    filing = (schema or {}).get("filing", "")
    mod = filing_questions.module(filing)
    older = {"i360": "i360", "family": "family", "n400": "naturalization", "i589": "asylum"}.get(filing)  # the panels that came first
    rows = filing_questions.questions(mod) if mod else importlib.import_module(older).QUESTIONS if older else []
    asked = {row[0]: row[1].replace(" -- ", ": ") for row in rows}

    def blank(short: str, key: str) -> str:
        if key in asked:  # one of the filing's own questions: say which, and where it is answered
            return f"{short}: not answered yet. \"{asked[key]}\". Answer it in the questions above and rebuild."
        name = _FACT_NAMES.get(key.split(".", 1)[-1], key.split(".", 1)[-1].replace("_", " "))
        if key.startswith("firm."):  # the same for every client: set once for the firm
            return f"{short}: the firm's {name} isn't set: add it on the Settings page (The firm and the attorney), or fill it in by hand."
        import absence

        if absence.paper_of(key):  # a box that asks about a paper: the mark on the Documents tab answers it
            return (f"{short}: no settled answer yet for {absence.box_label(key)}: add the paper to the folder, or record on the Documents tab that the client has none, "
                    "and rebuild; or fill it in by hand.")
        return f"{short}: no settled answer yet for {name}: settle it in review and rebuild, or fill it in by hand."

    for form in forms:
        if form["id"] == "i485":
            continue
        result = companions.get(form["id"]) or {}
        items += [{"kind": "check", "text": f"{form['short']}: {text}"} for text in form["attorney_completes"]]
        left = result.get("left_blank", [])
        unasked = [key for key in left if key in asked]  # the filing's own questions: one line, the panel above lists them
        if len(unasked) > 3:
            first = "; ".join(f"\"{asked[key]}\"" for key in unasked[:3])
            items.append({"kind": "missing", "text": f"{form['short']}: {len(unasked)} questions not answered yet (marked Needed above), e.g. {first}. "
                                                     "Answer them and rebuild."})
            left = [key for key in left if key not in asked]
        items += [{"kind": "missing", "text": blank(form["short"], key)} for key in left]
        items += [{"kind": "missing", "text": f"{form['short']}: the answer for box {name} was too long: enter it by hand."} for name in result.get("too_long", [])]
    if not any(i == "g28" or i.startswith("g28_") for i in ids) and (schema or {}).get("g28", True):
        items.append({"kind": "attach", "text": "Form G-28, signed by the attorney and the client, on top of the packet."})
    schema = schema or {}
    if "handwork" in schema:
        items += [dict(h) for h in schema["handwork"]]
    elif schema.get("filing", "i485") == "i485":
        photos = 2 * (1 + ("i765" in ids))
        items += [
            {"kind": "attach", "text": f"{'Four' if photos == 4 else 'Two'} identical passport-style photos, taken in the last 30 days"
                                       f"{' (two for the I-485, two for the I-765)' if photos == 4 else ''}; the client's name and A-Number lightly in pencil on the back."},
            {"kind": "attach", "text": "Form I-693 (medical exam), in the civil surgeon's sealed envelope. USCIS rejects an I-485 filed without it."},
            {"kind": "check", "text": "Filing fee: SIJ-based I-485s have no fee under the USCIS fee rule of April 1, 2024. The attorney confirms before mailing."},
        ]
    birth = next((ex for ex in exhibits if ex["id"] == "birth"), None)
    items += [{k: v for k, v in i.items() if k != "type"} for i in translations or []]
    covered = {i.get("type") for i in translations or []}
    if birth and "birth_certificate" not in covered and not any(f["type"] == "translation_certification" or re.search(r"transl|tradu", f["doc"], re.I) for f in birth["files"]):
        items.append({"kind": "missing", "text": "No English translation of the birth certificate was found. A foreign-language document needs a full English translation and the translator's certification."})
    items += [{"kind": "missing", "text": f"Missing from the folder: {ex['title']}." + (f" {(why_missing or {}).get(ex['id'])}" if (why_missing or {}).get(ex["id"]) else "")}
              for ex in missing]
    items += [{"kind": "check", "text": f"Not sorted: {f['doc']}: put it in an exhibit or leave it out."} for f in unsorted]
    return items


# -- drawing -------------------------------------------------------------------


def _wrap(text: str, size: float, width: float) -> list[str]:
    # Helvetica averages about half its size per character
    return textwrap.wrap(text, max(10, int(width / (size * 0.5)))) or [""]


def _watermark(p: _Page, text: str) -> None:
    # 40 degrees across the middle of the page, light grey
    p.ops.append(f"q 0.85 g BT /F2 44 Tf 0.766 0.643 -0.643 0.766 110 190 Tm ({text}) Tj ET Q")


def _us_date(value: Any) -> str | None:
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(value or ""))
    return f"{m.group(2)}/{m.group(3)}/{m.group(1)}" if m else value


def _who(p: _Page, summary: dict[str, Any]) -> None:
    a = re.sub(r"\D", "", summary.get("a_number") or "")
    p.text(MARGIN, HEIGHT - 40, f"{summary.get('name') or ''}   A-Number: {('A-' + a) if a else 'NONE'}", "F3", 9)


def _index_pages(packet: dict[str, Any], rows: list[tuple[str, str, str]], draft: bool) -> list[_Page]:
    summary = packet["summary"]
    pages, p, y = [], None, 0.0

    def new_page() -> float:
        nonlocal p
        p = _Page()
        pages.append(p)
        if draft:
            _watermark(p, "DRAFT - NOT FOR FILING")
        _who(p, summary)
        return HEIGHT - 70

    y = new_page()
    p.text(MARGIN, y, "Form I-485, Application to Register Permanent Residence or Adjust Status", "F2", 13)
    y -= 18
    p.text(MARGIN, y, "Based on an approved Form I-360, Special Immigrant Juvenile", "F3", 11)
    y -= 30
    for label, value in (("Applicant", summary.get("name")), ("A-Number", summary.get("a_number")), ("Date of birth", _us_date(summary.get("dob"))),
                         ("Country of birth", summary.get("country")), ("Form I-360 receipt number", summary.get("i360_receipt"))):
        p.text(MARGIN, y, label, "F3", 9)
        p.text(MARGIN + 150, y, str(value or "-"), "F2", 10)
        y -= 15
    y -= 16
    p.text(MARGIN, y, "Index of documents", "F2", 12)
    y -= 8
    p.line(MARGIN, y, WIDTH - MARGIN, y)
    y -= 16
    for tab, title, pages_text in rows:
        lines = _wrap(title, 10, WIDTH - 2 * MARGIN - 150)
        if y - 14 * len(lines) < 70:
            y = new_page()
        p.text(MARGIN, y, tab, "F2", 10)
        for i, line in enumerate(lines):
            p.text(MARGIN + 70, y - 14 * i, line, "F3", 10)
        p.text(WIDTH - MARGIN - 70, y, pages_text, "F3", 10)
        y -= 14 * len(lines) + 6
    for n, page in enumerate(pages, start=1):
        page.text(WIDTH - MARGIN - 60, 40, f"Index {n} of {len(pages)}", "F3", 8)
    return pages


def _separator(summary: dict[str, Any], tab: str, title: str) -> _Page:
    p = _Page()
    _who(p, summary)
    p.text(MARGIN, HEIGHT / 2 + 40, tab, "F2", 36)
    for i, line in enumerate(_wrap(title, 14, WIDTH - 2 * MARGIN)):
        p.text(MARGIN, HEIGHT / 2 - 10 - 20 * i, line, "F3", 14)
    return p


def _source_pages(entry: dict[str, Any]) -> list:
    reader = PdfReader(entry["path"])
    if reader.is_encrypted:
        reader.decrypt("")
    wanted = [n - 1 for n in entry["pages"]] if entry.get("pages") else range(len(reader.pages))
    return [reader.pages[i] for i in wanted]


def _case_graph(client_dir: Path, schema: dict[str, Any]):
    """The reviewed case as this filing sees it: every review decision, then the filing's own derived facts."""
    from factgraph import FactGraph

    from review.state import reviewed_graph

    # every review decision, as of now (the I-360's answers are decisions too)
    graph = reviewed_graph(client_dir) if (client_dir / "fact_graph.json").exists() else FactGraph.load(client_dir / "fact_graph_reviewed.json")
    if schema.get("filing") == "family":  # the petitioner and the relationship (src/family.py)
        from family import derive

        graph = derive(graph)
    if schema.get("filing") == "n400":  # the N-400's own facts: histories, trips, the spouse (src/naturalization.py)
        from naturalization import derive as n400_derive

        graph = n400_derive(graph)
    if schema.get("filing") == "i589":  # the I-589's facts; long answers move to supplement sheets (src/asylum.py)
        from asylum import derive as asylum_derive

        graph = asylum_derive(graph)
    import filing_questions

    mod = filing_questions.module(schema.get("filing", ""))
    graph = filing_questions.derive(mod, client_dir, graph, _today()) if mod else graph
    if schema.get("fee_waiver"):  # the I-912 inside this filing (src/fee_waiver.py)
        import fee_waiver

        fee_waiver.from_case(client_dir, graph)  # the client's monthly answers (src/eoir26a.py): asked once
        graph = fee_waiver.derive(graph, _today())
    return graph


def _payments(client_dir: Path, schema: dict[str, Any], graph=None) -> list[dict[str, Any]]:
    """What this packet pays, one payment each (src/payment.py); nothing for a case not processed yet."""
    if graph is None and not any((client_dir / f).exists() for f in ("fact_graph.json", "fact_graph_reviewed.json")):
        return []
    import payment

    return payment.payments(schema, client_dir, graph if graph is not None else _case_graph(client_dir, schema), _today())


def _fill_companions(client_dir: Path, schema: dict[str, Any]):
    """The G-28 and I-765 from the same reviewed case as the I-485 (every
    review decision included); what they couldn't fill goes in companions.json.
    Returns the case's graph as the filing sees it."""
    from fill.companion import fill_companions, load_profile

    wanted = [fid for fid in schema.get("forms", []) if fid != "i485" and fid not in (schema.get("generated") or {})]
    if not wanted and not schema.get("generated"):
        return None  # nothing to fill: the payments read the case themselves (_payments)
    graph = _case_graph(client_dir, schema)
    import g28

    if g28.governed(wanted):  # the G-28's mailing address and Part 4 are the case's card's: its address, its three choices (src/g28.py)
        g28.apply(graph, client_dir)
    profile = load_profile()
    companions = profile["forms"]
    profile["forms"] = {fid: form for fid, form in companions.items() if fid in wanted}
    import filing_questions

    mod = filing_questions.module(schema.get("filing", ""))
    if mod and hasattr(mod, "render"):  # the filing's own pages (src/visa.py: the DS-260 answer sheet and the NVC checklist)
        mod.render(client_dir, graph, _today())
    results = fill_companions(graph, client_dir, profile) if profile["forms"] else {}
    people = _person_module(schema)
    for fid in (f for f in wanted if f not in companions):  # one form per person: filled from that person's answers (person_graph)
        form = instance(fid, companions)
        own = people.person_graph(graph, form["instance_of"], form["instance"]) if people is not None and hasattr(people, "person_graph") else graph
        results |= fill_companions(own, client_dir, {**profile, "forms": {fid: form}})
    if schema.get("filing") == "i589":
        from asylum import attach_supplements

        attach_supplements(client_dir, graph)
    done = _read(client_dir / "companions.json", {})
    (client_dir / "companions.json").write_text(json.dumps(done | results, indent=1), encoding="utf-8")
    return graph


def _own_fields(pdf: Path, prefix: str) -> PdfReader:
    """Every USCIS form names its fields form1[0]...: two forms in one PDF
    would share boxes (a name typed on one showing on the other). Renaming
    this form's top-level fields keeps each form's boxes its own."""
    from pypdf.generic import NameObject, TextStringObject

    reader = PdfReader(str(pdf))  # the names are changed in this reading of the file, which only the packet's writer takes pages from: no copy of the form, no second file
    acroform = reader.trailer["/Root"].get("/AcroForm")
    for ref in (acroform.get_object().get("/Fields") or []) if acroform is not None else []:
        field = ref.get_object()
        field[NameObject("/T")] = TextStringObject(f"{prefix}_{field.get('/T') or 'form'}")
    return reader


def build(client_dir: Path, row: dict[str, Any], who: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Writes packet.pdf and packet.json (what's in it, and whether it was
    ready) in the client's folder; returns the manifest."""
    schema = for_case(schema or load_schema(), client_dir)
    filled = client_dir / schema.get("requires", "i485_filled.pdf")
    if not filled.exists():
        raise ValueError(schema.get("requires_message", "The I-485 hasn't been filled yet: process the client first."))
    graph = _fill_companions(client_dir, schema)
    packet = plan(client_dir, row, schema, _building=True)
    draft = not packet["ready"]
    separators = schema.get("separator_sheets", True)
    import payment

    pays = payment.render(client_dir, graph, _payments(client_dir, schema, graph))
    first_paid = next((f["id"] for f in packet["forms"] if any(p["form_id"] == f["id"] for p in pays)), None)
    on_top = [p for p in pays if p["form_id"] == first_paid or p["form_id"] not in {f["id"] for f in packet["forms"]}]

    def page_of(p: dict[str, Any]) -> tuple:
        return (p["short"], p["title"], len(PdfReader(str(client_dir / p["file"])).pages), None,
                {"id": p["id"], "short": p["short"], "title": p["title"], "file": p["file"], "signatures": {}, "layout": None})

    # (tab, title, page count, exhibit or None, form or None)
    top = [page_of(p) for p in on_top]  # USCIS: the G-1450 goes on top of what it pays for
    import enotice

    case = graph if graph is not None else (_case_graph(client_dir, schema) if any(
        (client_dir / n).exists() for n in ("fact_graph.json", "fact_graph_reviewed.json")) else None)
    notice = enotice.render(client_dir, case, schema) if case is not None else None
    if notice:  # the G-1145: clipped to the first page of the package (src/enotice.py)
        top.insert(0, page_of(notice))
    sections, closing = [], []
    for form in packet["forms"]:
        title = form["title"] + (f", with {packet['continuation_sheets']} cop{'y' if packet['continuation_sheets'] == 1 else 'ies'} of the Part 14 page" if form["id"] == "i485" and packet["continuation_sheets"] else "")
        into = closing if form.get("after_exhibits") else sections  # a court filing's proposed order and proof of service: at the bottom
        into += [page_of(p) for p in pays if p["form_id"] == form["id"] and p not in on_top]
        into.append((form["short"], title, len(PdfReader(str(client_dir / form["file"])).pages), None, form))
    for ex in packet["exhibits"]:
        count = sum(len(_source_pages(f)) for f in ex["files"])
        sections.append((f"Exhibit {ex['letter']}", ex["title"], count + (1 if separators else 0), ex, None))
    sections += closing

    # The index's own length decides every page number after it: lay it out
    # with placeholder page ranges, then again with the real ones.
    letter = None
    if packet["cover_letter"]:  # on top, as the firm files it (src/fill/cover_letter.py)
        from fill.cover_letter import case_facts, render

        letter = PdfReader(io.BytesIO(render(packet, case_facts(client_dir), _letter_config(schema, client_dir), _today(), draft)))
    top_pages = sum(s[2] for s in top)
    lead = top_pages + (len(letter.pages) if letter else 0)
    rows = [(tab, title, "") for tab, title, *_ in sections]
    index_len = len(_index_pages(packet, rows, draft)) if packet["index_sheet"] else 0
    start, rows, ranges = lead + index_len + 1, [], {}
    for tab, title, count, *_ in sections:
        ranges[tab] = (start, start + count - 1)
        rows.append((tab, title, f"pp. {start}-{start + count - 1}" if count > 1 else f"p. {start}"))
        start += count
    index = _index_pages(packet, rows, draft) if packet["index_sheet"] else []
    assert len(index) == index_len

    writer = PdfWriter()
    for _tab, _title, _count, _ex, form in top:
        writer.append(_own_fields(client_dir / form["file"], form["id"]))
    for page in letter.pages if letter else []:
        writer.add_page(page)
    for page in index:
        writer.add_page(page.to_page(writer))
    for tab, title, _count, ex, form in sections:
        if form is not None:  # keeps the form's fields and their filled-in appearance
            writer.append(_own_fields(client_dir / form["file"], form["id"]) if form["id"] != "i485" else str(client_dir / form["file"]))
            continue
        if separators:
            writer.add_page(_separator(packet["summary"], tab, title).to_page(writer))
        for f in ex["files"]:
            for page in _source_pages(f):
                writer.add_page(page)
    out = io.BytesIO()
    writer.write(out)
    data = out.getvalue()
    (client_dir / schema.get("packet_pdf", PACKET_PDF)).write_bytes(data)

    import g28
    import version

    manifest = {
        "built_at": clock.stamp(), "built_by": who, "version": version.VERSION, "draft": draft, "problems": packet["problems"],  # version: the release that built it (src/rebuild.py)
        "pages": lead + len(index) + sum(s[2] for s in sections), "sha256": hashlib.sha256(data).hexdigest(),
        "sections": [{"tab": tab, "title": title, "first_page": 1 + sum(s[2] for s in top[:i]), "last_page": sum(s[2] for s in top[:i + 1]), "files": []}
                     for i, (tab, title, *_rest) in enumerate(top)]
                    + ([{"tab": "Cover letter", "title": "Cover letter", "first_page": top_pages + 1, "last_page": lead, "files": []}] if lead > top_pages else []) + [{"tab": tab, "title": title, "first_page": ranges[tab][0], "last_page": ranges[tab][1],
                      "files": [{"doc": f["doc"], "sha256": hashlib.sha256(Path(f["path"]).read_bytes()).hexdigest()} for f in (ex["files"] if ex else [])]}
                     for tab, title, _, ex, _form in sections],
        "left_out": [{"doc": f["doc"], "why": f["why"]} for f in packet["left_out"] + packet["unsorted"]],
        # where each person signs, in the printed packet: [{form, who, what, page}]
        "signatures": [{"form": form["short"], "who": signer, "what": label, "page": ranges[form["short"]][0] + form["layout"][signer] - 1}
                       for form in packet["forms"] if form["layout"] for signer, (_f, label) in form["signatures"].items() if form["layout"].get(signer)],
        "checklist": packet["checklist"],
        "payments": [{"form": p["form"], "amount": p["amount"], "what": p["what"], "file": p["file"]} for p in pays],
        # what the filing record and the pre-mailing check read (src/prefile.py)
        "filing": schema.get("filing", "i485"),
        "forms": [{"id": f["id"], "short": f["short"], "file": f["file"]} for f in packet["forms"]],
        "g28": g28.built_with(client_dir, [f["id"] for f in packet["forms"] if f["id"] not in (schema.get("generated") or {})]),  # what the G-28 was filled with (src/g28.py)
        **_mailing(client_dir, schema, packet),
    }
    (client_dir / schema.get("manifest", "packet.json")).write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    events.record("packet", "built", f"Built the packet for {schema.get('title') or filing_title(schema.get('filing'))}" + (" (a draft)" if manifest.get("draft") else ""),
                  case_dir=client_dir, who=who)
    return manifest


def _mailing(client_dir: Path, schema: dict[str, Any], packet: dict[str, Any]) -> dict[str, Any]:
    """Where the packet goes and the fee its letter states -- recorded with the packet."""
    if not schema.get("cover_letter"):
        return {}
    from fill.cover_letter import case_facts, fee_text, fees_for, mail_to

    config, facts = _letter_config(schema, client_dir), case_facts(client_dir)
    return {"mail_to": mail_to(config, facts)[0], "fee": fee_text(fees_for(config, facts))}


def choose(client_dir: Path, doc: str, exhibit: str | None, who: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Moves one document into an exhibit, leaves it out (LEAVE_OUT), or back
    to its default place (None)."""
    schema = schema or load_schema()
    meta = _read(client_dir / "meta.json", {})
    classes = meta.get("classifications") or {}
    if doc not in classes:
        raise LookupError("unknown document")
    if classes[doc] in PRIOR_FORM_TYPES:
        raise ValueError("A completed USCIS form from an earlier filing never goes in the packet.")
    if exhibit not in (None, LEAVE_OUT) and exhibit not in {ex["id"] for ex in schema["exhibits"]}:
        raise ValueError("unknown exhibit")
    choices = _read(client_dir / schema.get("choices", CHOICES), {"files": {}})
    if exhibit is None:
        choices["files"].pop(doc, None)
    else:
        choices["files"][doc] = exhibit
    choices.setdefault("log", []).append({"at": clock.stamp(), "by": who, "doc": doc, "to": exhibit})
    (client_dir / schema.get("choices", CHOICES)).write_text(json.dumps(choices, indent=1), encoding="utf-8")
    events.record("packet", "moved" if exhibit not in (None, LEAVE_OUT) else "left_out" if exhibit == LEAVE_OUT else "reset",
                  "Moved a document to another exhibit" if exhibit not in (None, LEAVE_OUT) else "Left a document out of the packet" if exhibit == LEAVE_OUT
                  else "Put a document back in its usual place", case_dir=client_dir, who=who)
    return choices


def main(argv: list[str] | None = None) -> None:
    """python src/packet.py <client_id> [--data data/clients] -- build from the command line."""
    import argparse

    from fill import load_field_map
    from review.overview import review_row
    from review.state import Catalog, refill

    parser = argparse.ArgumentParser(description="Build a client's ready-to-file I-485 packet")
    parser.add_argument("client")
    parser.add_argument("--data", default=REPO / "data" / "clients", type=Path)
    parser.add_argument("--by", default="command line")
    args = parser.parse_args(argv)
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    template = schema_path.path("template", "i485")
    policies = _read(schema_path.path("law", "policy_sijs"), {}).get("policies", [])
    client_dir = args.data / args.client
    refill(client_dir, field_map, template)  # the form must carry every decision made so far
    manifest = build(client_dir, review_row(client_dir, field_map, template, Catalog(field_map, template, policies)), args.by)
    print(f"{client_dir / PACKET_PDF}: {manifest['pages']} pages{': DRAFT' if manifest['draft'] else ''}")
    for problem in manifest["problems"]:
        print("  -", problem)


if __name__ == "__main__":
    main()
