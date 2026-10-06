"""Paper or online: which of our filings the attorney can file by PDF upload
in the firm's USCIS online account, and the bundle to upload when they do.

USCIS's page "Forms Available to File Online" (schemas/law/online_filing.json,
each rule with its sentence) lists what an attorney can upload as a filled
PDF and under which limits: an I-485 only together with its I-130 (or
I-140), an I-765 only in some categories, an N-400 never with a reduced fee
or a fee waiver, advance parole only on an I-485 whose receipt begins IOE.
This module never automates USCIS's website: it decides whether a case may
go online, and builds what a person uploads by hand.

  eligibility()   paper or online for this case, and why;
  status()        what the packet panel shows: the choice, the reason, the
                  online fee next to the paper one (Form G-1055's "Online
                  Filing" lines, schemas/law/fees.json "online"; never assumed);
  guide()         what USCIS's own pages say to upload for this filing's
                  forms (its eight steps, each form page's notes and
                  evidence checklist, with page and date), shown beside
                  the bundle;
  choose()        the attorney's or paralegal's choice for one filing
                  (online_filing.json in the client's folder);
  build_bundle()  the folder to upload, zipped: the filled forms flagged
                  "print, sign in ink, scan" (USCIS wants the client's
                  wet-ink signature on the uploaded PDF), the G-28, the
                  evidence as one PDF per exhibit within USCIS's 12 MB per
                  file, and a checklist page for the paralegal: what goes
                  where, in order. No G-1450 and no G-1145: the fee is paid
                  in the account, and the account shows the receipt.

After submitting, the filing is recorded like a mailing (src/prefile.py,
carrier "USCIS online account"); the receipt number is added when the case
card shows it (prefile.add_receipt).
"""

from __future__ import annotations

import hashlib
import io
import json
import re
import zipfile
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events
import schema_path


DATA = schema_path.path("law", "online_filing")
CHOICES = "online_filing.json"  # in the client's folder: {filing: {mode, by, at}}
PAPER, ONLINE = "paper", "online"
CARRIER = "USCIS online account"  # how prefile.record_filing names a filing made here (prefile.CARRIERS)
SIGNERS = {"client": "the client", "attorney": "the attorney", "petitioner": "the petitioner", "spouse": "the spouse"}


def rules(path: Path = DATA) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _value(graph, key: str) -> Any:
    fact = graph.get(key) if graph is not None else None
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def _today() -> date:
    return clock.today()


def _graph(client_dir: Path, schema: dict[str, Any]):
    import packet

    if not any((client_dir / n).exists() for n in ("fact_graph.json", "fact_graph_reviewed.json")):
        return None
    return packet._case_graph(client_dir, schema)


def _fee(filing: str, graph, today: date) -> Any:
    """The filing's own USCIS fee (its module's fee(): an amount, or a tuple whose first item is the filing fee)."""
    import filing_questions

    mod = filing_questions.module(filing)
    if mod is None or not hasattr(mod, "fee") or graph is None:
        return None
    got = mod.fee(graph, today)
    return got[0] if isinstance(got, tuple) else got


def _question(filing: str, key: str) -> str:
    """The filing's own question for a fact, as its panel words it ("Part 1 · Which document")."""
    import filing_questions

    mod = filing_questions.module(filing)
    label = next((row[1] for row in (filing_questions.questions(mod) if mod else []) if row[0] == key), None)
    return f"the question \"{label.split(' · ')[-1]}\"" if label else "a question not answered yet"


def eligibility(filing: str, client_dir: Path | None = None, schema: dict[str, Any] | None = None, graph=None,
                today: date | None = None) -> dict[str, Any]:
    """{"online": True | False | None, "why", "rule" (the sentence it rests on), "source", "updated"}.
    True: this case may be filed by PDF upload; False: paper only; None: not a USCIS filing."""
    import packet

    data = rules()
    entry = (data["filings"].get(filing) or
             {"online": False, "why": "Not on USCIS's list of forms an attorney can file by PDF upload: it is filed by mail."})
    out = {"online": entry.get("online"), "why": entry.get("why", ""), "rule": entry.get("_source"), "source": data["source"],
           "updated": data["page_updated"], "notes": list(entry.get("notes") or []),
           "how": entry.get("how")}  # "online_only": USCIS takes it only through its own online form, never on paper (the FOIA request)
    if entry.get("online") is not True:
        return out
    today = today or _today()
    if schema is None:
        schema = packet.for_case(packet.load_filing(filing), client_dir) if client_dir is not None else packet.load_filing(filing)
    if graph is None and client_dir is not None and any(r.get("fact") or r.get("fee_zero") for r in entry.get("rules") or []):
        graph = _graph(client_dir, schema)
    for rule in entry.get("rules") or []:
        why = rule["why"]
        if "variant" in rule:
            if schema.get("variant") == rule["variant"]:
                return out | {"online": False, "why": why}
            continue
        if rule.get("fee_waiver"):
            if schema.get("fee_waiver"):
                return out | {"online": False, "why": why}
            continue
        got = _value(graph, rule["fact"])
        if "not_in" in rule or "prefix_not" in rule:
            if got is None:  # not answered yet: paper until it is, never a guess
                return out | {"online": False, "why": f"Paper or online depends on {_question(filing, rule['fact'])}: answer it first. {why}", "pending": True}
            if "not_in" in rule and got not in rule["not_in"]:
                return out | {"online": False, "why": why}
            if "prefix_not" in rule and not str(got).upper().replace(" ", "").startswith(rule["prefix_not"]):
                return out | {"online": False, "why": why}
        elif "is" in rule and got in rule["is"]:
            if not rule.get("fee_zero"):
                return out | {"online": False, "why": why}
            fee = _fee(filing, graph, today)
            if fee == 0:
                return out | {"online": False, "why": why}
            if fee is None:
                return out | {"online": False, "why": f"Paper or online depends on the fee, not settled yet: answer the filing's questions first. {why}",
                              "pending": True}
    file_as = entry.get("file_as") or {}
    return out | {"why": "USCIS takes this filing by PDF upload in the firm's USCIS online account.",
                  "file_as": file_as.get(schema.get("variant") or "", file_as.get("default"))}


# -- the choice -----------------------------------------------------------------------------------


def chosen(client_dir: Path, filing: str) -> str:
    return ((_read(client_dir / CHOICES, {}) or {}).get(filing) or {}).get("mode") or PAPER


def mode(client_dir: Path, filing: str, schema: dict[str, Any] | None = None) -> str:
    """How this filing goes now: online only while it is chosen AND still allowed (a case can change after the choice)."""
    if chosen(client_dir, filing) != ONLINE:
        return PAPER
    return ONLINE if eligibility(filing, client_dir, schema)["online"] is True else PAPER


def _title(filing: str) -> str:
    import packet

    return packet.filing_title(filing)


def choose(client_dir: Path, filing: str, value: str, who: str) -> dict[str, Any]:
    """Records paper or online for one filing; online only when USCIS allows it for this case."""
    if not who:
        raise ValueError("Enter your name first: the choice records who made it.")
    if value not in (PAPER, ONLINE):
        raise ValueError("Paper or online.")
    if value == ONLINE:
        e = eligibility(filing, client_dir)
        if e["online"] is not True:
            raise ValueError(f"This filing can't be filed online: {e['why']}")
    data = _read(client_dir / CHOICES, {}) or {}
    data[filing] = {"mode": value, "by": who, "at": clock.stamp()}
    data.setdefault("_log", []).append({"filing": filing, "mode": value, "by": who, "at": data[filing]["at"]})
    (client_dir / CHOICES).write_text(json.dumps(data, indent=1), encoding="utf-8")
    events.record("packet", "chose_" + value, f"Chose to file {_title(filing)} {'online' if value == ONLINE else 'on paper'}", case_dir=client_dir, who=who)
    return data[filing]


# -- fees -------------------------------------------------------------------------------------------


def online_fees(filing: str, pays: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """Each payment with its online amount: [{form, what, paper, online}]. online is None when Form G-1055 gives
    no online figure for it here (the account shows the amount; the paper one is never assumed). A Pub. L. 119-21
    fee has one figure for both (the same G-1055 line)."""
    import fees

    data = fees.load(today or _today())
    paper, online = data.get("paper") or {}, data.get("online") or {}
    names = (rules()["filings"].get(filing) or {}).get("fees") or []
    out = []
    for p in pays:
        if "Pub. L." in p["what"]:
            out.append({"form": p["form"], "what": p["what"], "paper": p["amount"], "online": p["amount"]})
            continue
        form = p["form"].lower().replace("-", "")
        # the paper fee this payment is (same form, same amount): its online amount, when every match agrees
        found = {online[k] for k in names if k.startswith(form) and paper.get(k) == p["amount"] and k in online}
        out.append({"form": p["form"], "what": p["what"], "paper": p["amount"], "online": found.pop() if len(found) == 1 else None})
    return out


def _fees_for(filing: str, pays: list[dict[str, Any]], today: date | None = None) -> list[dict[str, Any]]:
    """online_fees(), and for a filing whose packet makes no payment of its own (no fee() in its module; none today, since the
    I-751 got its rule on 10/02/2026) the G-1055 amounts its entry names, marked for the attorney to confirm no exemption applies."""
    import fees
    import filing_questions

    if pays:
        return online_fees(filing, pays, today)
    mod = filing_questions.module(filing)
    if mod is None or hasattr(mod, "fee"):
        return []
    data = fees.load(today or _today())
    paper, online = data.get("paper") or {}, data.get("online") or {}
    names = (rules()["filings"].get(filing) or {}).get("fees") or []
    return [{"form": re.sub(r"^([a-z])(\d+)", lambda m: f"{m.group(1).upper()}-{m.group(2)}", k), "what": "filing fee", "paper": paper.get(k),
             "online": online[k], "confirm": True} for k in names if k in online]


def money(n: Any) -> str:
    return f"${n:,}" if isinstance(n, int) else "the amount the account shows"


# -- what the packet panel shows ---------------------------------------------------------------------


def bundle_paths(client_dir: Path, filing: str) -> tuple[Path, Path]:
    """(the zip, its manifest) in the client's folder."""
    return client_dir / f"online_bundle_{filing}.zip", client_dir / f"online_bundle_{filing}.json"


def guide(filing: str) -> dict[str, Any] | None:
    """What USCIS itself says to upload for this filing's forms (schemas/law/online_filing.json "upload_guide": USCIS's eight steps
    for a PDF upload, each form's page notes and its checklist of required initial evidence, every line with its page and date).
    None for a filing USCIS doesn't take by upload. USCIS publishes no per-form upload screens: `no_per_form_steps` says so."""
    data = rules()
    entry, g = data["filings"].get(filing) or {}, data.get("upload_guide") or {}
    if entry.get("online") is not True or filing not in (g.get("forms") or {}):
        return None
    return {"steps": g["steps"], "steps_page": g["steps_page"], "steps_updated": g["steps_updated"], "read_on": g["read_on"], "forms": g["forms"][filing],
            "no_per_form_steps": "USCIS publishes the same eight steps for every form and no per-form upload screens; what follows is what its pages say about this filing's forms."}


def status(client_dir: Path, filing: str, schema: dict[str, Any] | None = None, pays: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    """The packet panel's "Paper or online" box: allowed or not and why, the choice, the fees both ways, the bundle."""
    import packet

    schema = schema or packet.for_case(packet.load_filing(filing), client_dir)
    e = eligibility(filing, client_dir, schema)
    if pays is None:
        pays = packet._payments(client_dir, schema) if any((client_dir / n).exists() for n in ("fact_graph.json", "fact_graph_reviewed.json")) else []
    choice = chosen(client_dir, filing)
    _zip, manifest = bundle_paths(client_dir, filing)
    return {"allowed": e["online"], "how": e.get("how"), "why": e["why"], "rule": e["rule"], "pending": bool(e.get("pending")), "source": e["source"],
            "updated": e["updated"], "file_as": e.get("file_as"), "notes": e["notes"],
            "mode": ONLINE if choice == ONLINE and e["online"] is True else PAPER, "chosen": choice,
            "fees": _fees_for(filing, pays) if e["online"] is True else [],
            "bundle": _read(manifest, None), "limit_mb": rules()["upload"]["max_bytes"] // 1_000_000,
            "guide": guide(filing)}


# -- the bundle ---------------------------------------------------------------------------------------


def _name(text: str, limit: int = 90) -> str:
    """A file name anyone can open: letters, digits and plain punctuation."""
    clean = re.sub(r"\s+", " ", re.sub(r"[^A-Za-z0-9 .,()'&+-]", " ", text)).strip()
    if len(clean) > limit:  # cut at a word, and close what the cut left open
        clean = clean[:limit].rsplit(" ", 1)[0].rstrip(" .,")
        clean += ")" * (clean.count("(") - clean.count(")"))
    return clean.rstrip(" .,") or "document"


def _pdf_bytes(pages: list) -> bytes:
    from pypdf import PdfWriter

    w = PdfWriter()
    for page in pages:
        w.add_page(page)
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def split_by_size(pages: list, limit: int) -> tuple[list[bytes], list[int]]:
    """The pages as few PDFs as possible, each at most `limit` bytes; returns (the PDFs, the 1-based pages that
    are too large even alone: they still go in, alone, and the checklist asks for a lighter scan)."""
    whole = _pdf_bytes(pages)
    if len(whole) <= limit:
        return [whole], []
    parts, current, too_large = [], [], []
    for n, page in enumerate(pages, start=1):
        if len(_pdf_bytes([page])) > limit:
            if current:
                parts.append(_pdf_bytes(current))
                current = []
            parts.append(_pdf_bytes([page]))
            too_large.append(n)
            continue
        if current and len(_pdf_bytes(current + [page])) > limit:
            parts.append(_pdf_bytes(current))
            current = []
        current.append(page)
    if current:
        parts.append(_pdf_bytes(current))
    return parts, too_large


def _sign_line(form: dict[str, Any]) -> str:
    who = []
    for signer, (_field, label) in form["signatures"].items():
        page = (form["layout"] or {}).get(signer)
        who.append(f"{SIGNERS.get(signer, 'the ' + signer)} signs {label}{'' if signer == 'attorney' else ' in ink'}"
                   + (f" (form page {page})" if page else ""))
    return "; ".join(who)


def _steps(e: dict[str, Any], forms: list[dict[str, Any]], evidence: list[dict[str, Any]], fees_due: list[dict[str, Any]],
           handwork: list[dict[str, Any]], fee_waiver: bool, limit_mb: int, fee_known: bool = True) -> list[dict[str, str]]:
    """The paralegal's checklist, in the order the work and the account go: what to settle before printing, the fee
    waiver (the account asks first), each form signed and scanned, the evidence, what is added by hand, the fee, Submit."""
    steps = [{"kind": "account", "text": f"In the firm's USCIS online account (the attorney's): File a Form Online, choose Form {e.get('file_as') or forms[0]['short']}, "
                                         "then Upload a Filled-Out PDF Form. Read each prompt."}]
    steps += [h for h in handwork if h["kind"] in ("missing", "check")]  # settled before anything is printed
    if fee_waiver:
        i912 = next((f for f in forms if f["id"] == "i912"), None)
        steps.append({"kind": "sign", "text": "Fee waiver: when the account asks about the fee, answer its questions and upload the signed Form I-912 scan"
                                              + (f" ({_sign_line(i912)})" if i912 else "") + " and every piece of its evidence: USCIS rejects the form without it."})
    for f in forms:
        if f["id"] == "i912":
            continue
        g28 = f["id"].startswith("g28")
        steps.append({"kind": "sign", "text": f"{f['short']}: print it (the bundle's forms folder); {_sign_line(f)}. Scan the whole signed form into one PDF "
                                              f"({limit_mb} MB at most) and upload the scan, never the unsigned form"
                                              + (": USCIS requires a G-28 with every attorney's PDF upload." if g28 else ".")})
    if not any(f["id"].startswith("g28") for f in forms):
        steps.append({"kind": "sign", "text": "Form G-28, signed by the attorney and the client: scan it and upload it with the form. USCIS requires a G-28 "
                                              "with every attorney's PDF upload."})
    if evidence:  # by exhibit letter: the bundle's evidence files start with it
        steps.append({"kind": "upload", "text": "Upload the evidence (the bundle's evidence folder), one file per category, in this order: "
                                                + "; ".join(x["label"] for x in evidence) + "."})
    steps += [h for h in handwork if h["kind"] not in ("missing", "check")]  # photos, the medical exam: uploaded with the evidence
    if fees_due:
        steps.append({"kind": "pay", "text": "Pay in the account (Pay.gov, by card or bank account), at USCIS's online fees: "
                                             + "; ".join(f"Form {p['form']} {money(p['online'])}" + ("" if p["online"] is not None else " (Form G-1055 gives no online figure: the attorney confirms)")
                                                         + (" (unless the case is fee-exempt: the attorney confirms)" if p.get("confirm") else "")
                                                         for p in fees_due) + ". No Form G-1450: nothing is mailed."})
    elif not fee_known:
        steps.append({"kind": "pay", "text": "The fee isn't settled yet: answer the filing's questions and rebuild the bundle."})
    else:
        steps.append({"kind": "pay", "text": "No fee is paid for this filing." if not fee_waiver else "The fee waiver replaces the fee: nothing is paid unless a fee can't be waived."})
    steps.append({"kind": "check", "text": "Before Submit: each form uploaded is the signed scan (the client can't sign online when an attorney files). "
                                           "Nothing can be changed after Submit."})
    steps.append({"kind": "record", "text": "After Submit: the confirmation has no receipt number. Record the filing on this case (Filed online). "
                                            "When the receipt number shows on the case card (USCIS: up to 30 days), add it on this case."})
    return steps


_PAPER_ONLY = re.compile(r"G-1450|G-1145|cover letter|sealed envelope|photos|Form G-28, signed|lockbox|(?<![-\w])mail", re.I)


def _handwork(plan: dict[str, Any], e: dict[str, Any]) -> list[dict[str, str]]:
    """What is still done by hand, for the upload: the packet's own checklist without the mailing's lines
    (signatures are their own steps), plus USCIS's online instructions (photos scanned, the I-693 opened)."""
    out = [c | {} for c in plan["checklist"] if c["kind"] != "sign" and not _PAPER_ONLY.search(c["text"])]
    text = " ".join(c["text"] for c in plan["checklist"])
    if re.search(r"photos", text, re.I):
        out.append({"kind": "attach", "text": "Passport-style photos: scan them, or photograph them with a phone, and upload them where the account asks."})
    if "I-693" in text:
        out.append({"kind": "attach", "text": "Form I-693 (medical exam): open the civil surgeon's sealed envelope, scan the signed I-693 and upload it with the "
                                              "I-485. Keep the original and the envelope until USCIS decides the case (USCIS may ask for them)."})
    out += [{"kind": "check", "text": n} for n in e["notes"] if "I-693" not in n]
    return out


def _checklist_pdf(steps: list[dict[str, str]], summary: dict[str, Any], title: str, draft: bool, built_on: date) -> bytes:
    from pypdf import PdfWriter

    from fill.continuation import HEIGHT, MARGIN, WIDTH, _Page
    from packet import _watermark, _who, _wrap

    def latin(s: str) -> str:
        return s.replace("’", "'").replace("“", '"').replace("”", '"').encode("latin-1", "replace").decode("latin-1")

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
    p.text(MARGIN, y, latin(f"Online filing by PDF upload: {title}"), "F2", 13)
    y -= 16
    p.text(MARGIN, y, latin(f"For the paralegal. Built {built_on.strftime('%m/%d/%Y')}. Upload in this order; tick each line."), "F3", 9)
    y -= 22
    for n, step in enumerate(steps, start=1):
        lines = _wrap(latin(step["text"]), 9.5, WIDTH - 2 * MARGIN - 40)
        if y - 13 * len(lines) < 60:
            y = new_page()
        p.box(MARGIN, y - 2, 9, 9)
        p.text(MARGIN + 16, y, f"{n}.", "F2", 9.5)
        for i, line in enumerate(lines):
            p.text(MARGIN + 34, y - 13 * i, line, "F3", 9.5)
        y -= 13 * len(lines) + 7
    for n, page in enumerate(pages, start=1):
        page.text(WIDTH - MARGIN - 60, 40, f"Checklist {n} of {len(pages)}", "F3", 8)
    w = PdfWriter()
    for page in pages:
        w.add_page(page.to_page(w))
    buf = io.BytesIO()
    w.write(buf)
    return buf.getvalue()


def build_bundle(client_dir: Path, row: dict[str, Any], who: str, schema: dict[str, Any] | None = None) -> dict[str, Any]:
    """Builds the packet (every form re-filled from the reviewed case), then the online bundle from it:
    online_bundle_<filing>.zip and its manifest. Refuses a filing USCIS doesn't take online for this case."""
    import packet

    schema = packet.for_case(schema or packet.load_schema(), client_dir)
    filing = schema.get("filing", "i485")
    e = eligibility(filing, client_dir, schema)
    if e["online"] is not True:
        raise ValueError(f"This filing can't be filed online: {e['why']}")
    paper = packet.build(client_dir, row, who, schema)  # fills every form from the case, exactly as the paper packet would be
    plan = packet.plan(client_dir, row, schema)
    limit = rules()["upload"]["max_bytes"]
    limit_mb = limit // 1_000_000
    files: list[dict[str, Any]] = []
    entries: list[tuple[str, bytes]] = []

    forms = [f for f in plan["forms"] if (client_dir / f["file"]).exists()]
    for n, f in enumerate(forms, start=1):
        data = (client_dir / f["file"]).read_bytes()
        name = f"1 Forms to print, sign and scan/{n} {_name(f['short'])} (print, sign in ink, scan).pdf"
        entries.append((name, data))
        files.append({"path": name, "bytes": len(data), "kind": "form", "form": f["short"], "signatures": _sign_line(f)})

    evidence, too_large = [], []
    for ex in plan["exhibits"]:
        pages = [pg for doc in ex["files"] for pg in packet._source_pages(doc)]
        parts, big = split_by_size(pages, limit)
        for i, data in enumerate(parts, start=1):
            label = f"{ex['letter']} {_name(ex['title'], 70)}" + (f" (part {i} of {len(parts)})" if len(parts) > 1 else "")
            name = f"2 Evidence to upload/{label}.pdf"
            entries.append((name, data))
            files.append({"path": name, "bytes": len(data), "kind": "evidence", "exhibit": ex["letter"], "title": ex["title"]})
            evidence.append({"label": f"Exhibit {ex['letter']}, {ex['title']}" + (f" (part {i} of {len(parts)})" if len(parts) > 1 else "")})
        too_large += [f"Exhibit {ex['letter']}, page {n}" for n in big]

    pays = _fees_for(filing, plan.get("payments") or [])
    handwork = _handwork(plan, e)
    if too_large:
        handwork.insert(0, {"kind": "missing", "text": f"Over USCIS's {limit_mb} MB per file even alone: {', '.join(too_large)}. Scan it again at a lower "
                                                       "resolution (or in black and white) and upload that instead."})
    import filing_questions

    mod = filing_questions.module(filing)  # a filing whose fee its own questions decide: unknown until they are answered
    fee_known = pays or mod is None or not hasattr(mod, "fee") or _fee(filing, _graph(client_dir, schema), _today()) is not None
    steps = _steps(e, forms, evidence, pays, handwork, bool(schema.get("fee_waiver")), limit_mb, bool(fee_known))
    draft = paper["draft"]
    sheet = _checklist_pdf(steps, plan["summary"], schema.get("title", filing), draft, _today())
    entries.insert(0, ("0 Checklist (read first).pdf", sheet))
    files.insert(0, {"path": "0 Checklist (read first).pdf", "bytes": len(sheet), "kind": "checklist"})

    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as z:
        for name, data in entries:
            z.writestr(name, data)
    blob = buf.getvalue()
    zip_path, manifest_path = bundle_paths(client_dir, filing)
    zip_path.write_bytes(blob)
    manifest = {"built_at": clock.stamp(), "built_by": who, "filing": filing, "draft": draft, "problems": paper["problems"],
                "file_as": e.get("file_as"), "files": files, "steps": steps, "fees": pays, "too_large": too_large, "limit_bytes": limit,
                "sha256": hashlib.sha256(blob).hexdigest(), "packet_sha256": paper.get("sha256"), "rule_page_updated": e["updated"]}
    manifest_path.write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    events.record("packet", "built", f"Built the online-filing files for {_title(filing)}" + (" (a draft)" if draft else ""), case_dir=client_dir, who=who)
    return manifest
