"""The EOIR-26A (Fee Waiver Request) from the client's own answers -- Form EOIR-26A, Rev. Aug. 2022 (EOIR's forms page, "Updated October 1, 2026",
read 10/03/2026: "EOIR 26A (Revised Aug. 2022)"; the form's own text is on schemas/forms/eoir26a/template.pdf), filed with a motion to the immigration judge
(src/court_motion.py) or with an appeal to the Board (src/bia.py) when the attorney asks for the fee to be waived (8 CFR 1003.24(d)).

What the form asks, and what this module does about each part (the lines the product holds word for word are schemas/forms/eoir26a/text.json "form_lines"):

  The affidavit (page 1): "to be signed by the respondent, not the respondent's attorney": the client signs it on their phone in the portal (the typed
    name and the declaration, with the signed-copy record the engagement letter keeps: the date, the time, the address the request came from, the
    language shown), and, when the attorney switches it on for the case, a signature drawn on the phone too; or on paper, the office uploading the
    signed form. Which of these the court accepts is the attorney's to decide: nothing here says so.
  Item 1 and 2: nine lines of monthly money, "even if the answer is $0.00": the client's figures only (the portal's "Your monthly money"), never a
    guess; a line not answered is left empty and the totals are not made. The totals and the difference (item 3) are this module's arithmetic, in
    dollars and cents, shown on the card with each figure's source (the client's answer and its date, or the person who typed it).
  Item 4: a sentence the paralegal edits and the attorney approves per case (a decision, with Undo): suggested from the firm's wording (L3's library
    when it is on master; today the shipped DRAFT wordings), every number in it from the figures above.
  The attorney's attestation (page 2): the attorney's name, the EOIR ID the office typed under Settings (never in code), and the date; counter-signed
    on the case page by an attorney (typed name, recorded), as the engagement letter is. Refused while the EOIR ID is blank.

The record is eoir26a.json in the case's folder (one request; a voided one is kept in history); the client's side is fee_waiver.json in the portal's
folder for the client (src/portal/store.py). The filled form (eoir26a_filled.pdf) gets the typed or drawn signature placed in its boxes and the
signing record (an audit page) after its last page, as the signing services do (finish).
"""

from __future__ import annotations

import base64
import binascii
import hashlib
import io
import json
import os
import re
import sys
import threading
from decimal import ROUND_HALF_UP, Decimal
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
from holders import ATTORNEY, CLIENT, OFFICE, held, producer

REPO = Path(__file__).resolve().parents[1]
SHIPPED = schema_path.path("form_text", "eoir26a")
FILE = "eoir26a.json"
FOLDER = "eoir26a"
VERSION = 1
LANGS = ("pt", "es", "en", "ht")
LANGUAGE_NAMES = {"en": "English", "pt": "Portuguese", "es": "Spanish", "ht": "Haitian Creole"}
PORTAL_DOC_ID = "portal questionnaire"  # src/portal/bank.py PORTAL_DOC_ID: where a figure the client typed comes from
HARDSHIP_KEY = "eoir26a.hardship"
HARDSHIP_ITEM = "eoir26a:hardship"
MAX_AMOUNT = Decimal("1000000")
MAX_DRAWING = 300 * 1024  # bytes of the drawn signature's image
EOIR_ID_FIRST = "Type the attorney's EOIR ID under Settings."
_LOCK = threading.RLock()  # a step that holds it reads the case (case_graph), which syncs the client's side under the same lock
_cache: dict[str, Any] = {}


# -- what ships ----------------------------------------------------------------------------------------------------------------


def shipped() -> dict[str, Any]:
    """schemas/forms/eoir26a/text.json, read again when it changes."""
    stamp = SHIPPED.stat().st_mtime
    if _cache.get("stamp") != stamp:
        _cache.update(stamp=stamp, data=json.loads(SHIPPED.read_text(encoding="utf-8")))
    return _cache["data"]


def lines() -> list[dict[str, Any]]:
    """The form's nine lines: id (the question's), fact, field (the form's box), group (income or expense) and the row's words on the form."""
    return shipped()["lines"]


def short_label(line: dict[str, Any]) -> str:
    """The form's row without its examples: "Utilities", "Income from real property", "All other income"."""
    return line["form_row"].split(" (")[0].split(", including but not limited")[0]


def by_fact() -> dict[str, dict[str, Any]]:
    return {x["fact"]: x for x in lines()}


# -- money: what the client typed, in dollars and cents ----------------------------------------------------------------------


def parse_money(value: Any) -> Decimal | None:
    """An amount as a client writes it ("1,234.56", "1.234,56", "$1200", "0", "12,5") as dollars and cents, else None (not a number, negative, or over
    $1,000,000.00). A comma or a period followed by three digits and nothing else is a thousands mark ("1,200", "1.200"); followed by one or two digits
    it is the decimal mark; with both, the last one is the decimal mark."""
    text = re.sub(r"[\s$ ]", "", str(value if value is not None else ""))
    if not text or not re.fullmatch(r"\d[\d.,]*", text):
        return None
    marks = [c for c in text if c in ".,"]
    if not marks:
        number = text
    elif "." in text and "," in text:
        decimal = "." if text.rfind(".") > text.rfind(",") else ","
        thousands = "," if decimal == "." else "."
        whole, _, cents = text.rpartition(decimal)
        if decimal in whole or not re.fullmatch(r"\d{1,3}(" + re.escape(thousands) + r"\d{3})*", whole) or not re.fullmatch(r"\d{1,2}", cents):
            return None
        number = whole.replace(thousands, "") + "." + cents
    elif len(marks) > 1:  # "1.200.300": thousands marks only
        if not re.fullmatch(r"\d{1,3}([.,]\d{3})+", text) or len(set(marks)) > 1:
            return None
        number = re.sub(r"[.,]", "", text)
    else:
        whole, mark, tail = text.partition(marks[0])
        if len(tail) == 3 and 1 <= len(whole) <= 3 and not whole.startswith("0"):
            number = whole + tail  # a thousands mark
        elif 1 <= len(tail) <= 2:
            number = whole + "." + tail
        else:
            return None
    amount = Decimal(number).quantize(Decimal("0.01"), ROUND_HALF_UP)
    return amount if 0 <= amount <= MAX_AMOUNT else None


def fmt(amount: Decimal) -> str:
    """1,234.56 (a minus sign in front when it is below zero): how a box on the form is written."""
    return f"{amount:,.2f}"


def usd(amount: Decimal) -> str:
    """$1,234.56, or -$320.00."""
    return ("-" if amount < 0 else "") + f"${abs(amount):,.2f}"


def totals_of(amounts: dict[str, Decimal | None]) -> dict[str, Any]:
    """The form's totals from the nine amounts (by line id): item 1.A (the four income lines), item 2.B (the five expense lines) and item 3 (income minus
    expenses). A total is made only when every one of its lines is there: a line the client has not answered is never taken as $0.00.
    {"income", "expense", "difference": Decimal or None, "missing": [line ids], "complete": all nine are there}."""
    out: dict[str, Any] = {}
    for group in ("income", "expense"):
        mine = [x["id"] for x in lines() if x["group"] == group]
        out[group] = sum((amounts[i] for i in mine), Decimal("0.00")) if all(amounts.get(i) is not None for i in mine) else None
    out["difference"] = out["income"] - out["expense"] if out["income"] is not None and out["expense"] is not None else None
    out["missing"] = [x["id"] for x in lines() if amounts.get(x["id"]) is None]
    out["complete"] = not out["missing"]
    return out


def amounts_from_graph(graph) -> dict[str, Decimal | None]:
    return {x["id"]: parse_money(_value(graph, x["fact"])) for x in lines()}


def _value(graph, key: str) -> Any:
    fact = graph.get(key)
    return fact.value if fact is not None and fact.status == "resolved" and fact.value not in (None, "") else None


def totals(graph) -> dict[str, Any]:
    return totals_of(amounts_from_graph(graph))


def usd_for(amount: Decimal, lang: str = "en") -> str:
    """usd() as a reader of that language writes it: Portuguese puts the decimal comma and the thousands point ($1.234,56); the others as the form does."""
    text = usd(amount)
    return text.replace(",", "\0").replace(".", ",").replace("\0", ".") if lang == "pt" else text


def words(t: dict[str, Any], lang: str = "en") -> dict[str, str | None]:
    """The totals as dollars: {"income", "expense", "difference"} (None for one not made yet)."""
    return {k: usd_for(t[k], lang) if t[k] is not None else None for k in ("income", "expense", "difference")}


# -- the facts the form is filled from -----------------------------------------------------------------------------------------


def _portal(client_dir: Path, portal_root: Path | None = None) -> Path:
    from review.state import _portal_for

    return Path(portal_root) if portal_root else _portal_for(Path(client_dir))


def _client_file(client_dir: Path, portal_root: Path | None, name: str) -> Path:
    return _portal(client_dir, portal_root) / "clients" / Path(client_dir).name / name


def portal_answers(client_dir: Path, portal_root: Path | None = None) -> dict[str, Any]:
    """The client's answers on their page (empty when there is no portal client): the nine lines, as the client typed them."""
    path = _client_file(client_dir, portal_root, "answers.json")
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        return {}
    return data if isinstance(data, dict) else {}


def answered_on(client_dir: Path, qid: str, portal_root: Path | None = None) -> str | None:
    """The office's date (YYYY-MM-DD) of the client's latest save of this answer, from the portal's own log (events.jsonl)."""
    path = _client_file(client_dir, portal_root, "events.jsonl")
    last = None
    try:
        for row in path.read_text(encoding="utf-8").splitlines() if path.exists() else []:
            try:
                item = json.loads(row)
            except ValueError:
                continue
            if item.get("event") == "answers_saved" and qid in (item.get("questions") or []):
                last = item.get("at")
    except OSError:
        return None
    day = clock.local_date(last) if last else None
    return day.isoformat() if day else None


def figures_into(graph, client_dir: Path, portal_root: Path | None = None) -> list[str]:
    """The client's nine answers from their page into the graph as their own statements (tier 3), read now: a figure a person typed or confirmed in
    the review app stays. Returns the fact keys it filled."""
    answers, filled = portal_answers(client_dir, portal_root), []
    for line in lines():
        raw = answers.get(line["id"])
        amount = parse_money(raw)
        fact = graph.get(line["fact"])
        if amount is None or (fact is not None and fact.status == "resolved" and fact.value not in (None, "")):
            continue
        graph.add_source(line["fact"], PORTAL_DOC_ID, "questionnaire", str(raw), f"{amount:.2f}", 0.95, tier=3)
        filled.append(line["fact"])
    return filled


def case_graph(client_dir: Path, portal_root: Path | None = None):
    """The reviewed case with the client's figures and this module's own facts: what the card and the form are made from."""
    from factgraph import FactGraph
    from review.state import reviewed_graph

    client_dir = Path(client_dir)
    graph = reviewed_graph(client_dir) if (client_dir / "fact_graph.json").exists() else FactGraph(client_dir.name)  # a client not processed yet has the figures too
    from_case(client_dir, graph, portal_root)
    return graph


def asked_by(graph) -> str | None:
    """"motion" or "appeal" when the attorney asked for the fee to be waived on the case's court motion (ijmotion.fee_waiver) or appeal (bia.fee_waiver)."""
    if _value(graph, "ijmotion.fee_waiver") == "Yes":
        return "motion"
    if _value(graph, "bia.fee_waiver") == "Yes":
        return "appeal"
    return None


def names(graph, put) -> None:
    """The respondent's name on the form (last, first, middle; and as the affidavit prints it), from the client's name."""
    v = lambda k: _value(graph, k)  # noqa: E731
    given = " ".join(y for y in (v("applicant.given_name"), v("applicant.middle_name")) if y)
    put("eoir26a.respondent_name", ", ".join(x for x in (v("applicant.family_name"), given) if x) or None, "the client's name (Form EOIR-26A)")
    put("eoir26a.print_name", " ".join(x for x in (given, v("applicant.family_name")) if x) or None, "the client's name (Form EOIR-26A)")
    put("companion.preparer_full_name", attorney_name(graph) or None, "the case's office: the attorney (page 2 of Form EOIR-26A)")  # also on the form the office prints to be signed on paper


def legal_name(graph) -> str:
    """Fictional example or implementation helper."""
    v = lambda k: _value(graph, k)  # noqa: E731
    return " ".join(str(x).strip() for x in (v("applicant.given_name"), v("applicant.middle_name"), v("applicant.family_name")) if x and str(x).strip())


def signs_as_client(typed: str, legal: str) -> bool:
    """Whether the typed name is the client's legal name: the names timeline's strict comparison (accents, capitals, punctuation and the particles
    de, da, do, dos, das, e, del, y set aside; no letter is forgiven)."""
    from name_events import same_name

    return bool(legal) and same_name(typed, legal)


def from_case(client_dir: Path, graph, portal_root: Path | None = None) -> None:
    """The form's facts from the case as it is now: the client's figures, the totals and the difference (only when all nine lines are in), the name, and
    the dates of the signature and the attestation (only while each still covers the figures and the sentence on the case). Called when a filing that
    carries the form reads the case (src/filing_questions.py derive)."""
    from filing_questions import putter

    put = putter(graph, "eoir26a")
    figures_into(graph, client_dir, portal_root)
    sync(client_dir, portal_root)
    names(graph, put)
    t = totals(graph)
    for key, group, why in (("eoir26a.income_total", "income", "the four income lines added (item 1.A)"), ("eoir26a.expense_total", "expense", "the five expense lines added (item 2.B)"),
                            ("eoir26a.difference", "difference", "total income minus total expenses (item 3)")):
        put(key, fmt(t[group]) if t[group] is not None else None, why)  # a total is made only when every line in it is answered
    req = read(client_dir).get("request") or {}
    sig, att = req.get("signature"), req.get("attestation")
    if sig and covers_now(sig, graph):
        day = _signed_day(sig)
        put("eoir26a.signed_date", day.strftime("%m/%d/%Y") if day else None, "the day the client signed")
    if att and covers_now(att, graph) and sig and covers_now(sig, graph):
        put("eoir26a.attorney_date", clock.local_date(att.get("at")).strftime("%m/%d/%Y"), "the day the attorney attested")


def _signed_day(sig: dict[str, Any]):
    from datetime import date

    if sig.get("how") == "paper":
        try:
            return date.fromisoformat(str(sig.get("on")))
        except ValueError:
            return None
    return clock.local_date(sig.get("at"))


def sentence_text(graph) -> str:
    """Item 4 as it is approved on the case (the attorney's decision), "" when none or approved empty."""
    return _value(graph, HARDSHIP_KEY) or ""


def snapshot(graph) -> dict[str, Any]:
    """What a signature or an attestation covers: the nine amounts and item 4's sentence, as they are on the case when it is made."""
    return {"figures": {k: (f"{v:.2f}" if v is not None else None) for k, v in amounts_from_graph(graph).items()}, "sentence": sentence_text(graph)}


def covers_now(record: dict[str, Any], graph) -> bool:
    """True while the figures and the sentence on the case are the ones this signature (or attestation) was made over."""
    c = record.get("covers") or {}
    return bool(c) and c == snapshot(graph)


# -- the case's record ---------------------------------------------------------------------------------------------------------------


def read(client_dir: Path) -> dict[str, Any]:
    path = Path(client_dir) / FILE
    try:
        data = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    except (OSError, ValueError):
        data = {}
    return {"version": VERSION, "request": None, "history": []} | (data if isinstance(data, dict) else {})


def _save(client_dir: Path, rec: dict[str, Any], action: str, what: str, who: str | None = None, role: str | None = None) -> None:
    rec["version"] = VERSION
    path = Path(client_dir) / FILE
    tmp = path.with_suffix(".json.tmp")
    tmp.write_text(json.dumps(rec, indent=1, ensure_ascii=False), encoding="utf-8")
    os.replace(tmp, path)
    # The ledger row names the case: the review app's lists follow the ledger (src/review/roster.py), which is how a client's own signing in the portal (another process)
    # reaches them. The review app's own routes tell the lists before they answer (ReviewApp._requery, the handler's _send).
    events.record("packet", action, what, case_dir=client_dir, who=who, role=role, version=VERSION)


def _private(path: Path, data: bytes) -> None:
    """A signature image or a signed scan written readable by the owner only (0600), as the roster and the vault are."""
    path.write_bytes(data)
    try:
        os.chmod(path, 0o600)
    except OSError:  # a file system with no modes (a share on Windows): the folder's own rights stand
        pass


def _need(who: str) -> str:
    who = str(who or "").strip()
    if not who:
        raise ValueError("Enter your name first: every change records who made it.")
    return who


def _attorney(role: str | None, what: str) -> None:
    if role == "paralegal":
        raise PermissionError(f"Only an attorney {what}.")


def _portal_store(client_dir: Path, portal_root: Path | None):
    from portal.store import PortalStore

    root = _portal(client_dir, portal_root)
    if not (root / "clients" / Path(client_dir).name / "profile.json").exists():
        return None
    return PortalStore(root)


def _request(rec: dict[str, Any], client_dir: Path | None = None, who: str = "", role: str | None = None, graph=None) -> dict[str, Any]:
    """The case's request; made here (not sent to the client's page) when the office types the figures or uses paper: client_dir is then given."""
    if not rec.get("request"):
        if client_dir is None:
            raise ValueError("No fee waiver request is made for this case yet.")
        kind = asked_by(graph if graph is not None else case_graph(client_dir)) or "by_hand"
        rec["request"] = {"id": _next_id(rec), "kind": kind, "made_by": who, "made_role": role, "made_at": clock.stamp(), "sent": None, "opened": None, "viewed": None,
                          "sentence": None, "sentence_draft": None, "signature": None, "attestation": None}
    return rec["request"]


# -- asking the client, and what the client sees --------------------------------------------------------------------------------------


def _next_id(rec: dict[str, Any]) -> str:
    return f"F{len(rec.get('history') or []) + 1}"


def send(client_dir: Path, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """Puts "Your monthly money" on the client's page, with the "there's news, sign in" message (no case details in it). Any staff member may send it:
    asked for by the attorney on the motion or the appeal (the switch), or by hand."""
    import engagement

    who = _need(who)
    client_dir = Path(client_dir)
    with _LOCK:
        rec = read(client_dir)
        if (rec.get("request") or {}).get("sent"):
            raise ValueError("The questions are on the client's page already.")
        store = _portal_store(client_dir, portal_root)
        if store is None:
            raise LookupError("This client is not in the portal: type the figures here, or print the form and record the paper signature.")
        request = _request(rec, client_dir, who, role, case_graph(client_dir, portal_root))
        cid = client_dir.name
        store.save_fee_waiver(cid, {"request": {"id": request["id"], "at": request["made_at"], "kind": request["kind"]}})
        store.log(cid, "fee_waiver_sent", {"by": who})
        request["sent"] = {"by": who, "at": clock.stamp(), "delivery": engagement._tell_client(store, cid, client_dir.parent)}
        _save(client_dir, rec, "sent", "Put the fee waiver questions (EOIR-26A) on the client's page", who, role)
    return view(client_dir, portal_root, role)


def set_figures(client_dir: Path, values: dict[str, Any], who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """Figures a staff member types (a client with no portal, or a correction): the line's id -> the amount. Recorded as a review decision with who and when;
    a figure typed here replaces the client's own on the form, and a signature made before it no longer covers the case (the card says so)."""
    from review.state import record_decision

    who = _need(who)
    by_id = {x["id"]: x for x in lines()}
    unknown = [k for k in values if k not in by_id]
    if unknown:
        raise ValueError("That is not a line of the form.")
    parsed = {}
    for key, raw in values.items():
        if raw in (None, ""):
            continue
        amount = parse_money(raw)
        if amount is None:
            raise ValueError(f"“{by_id[key]['form_row'][:40]}”: write an amount in dollars (0 if nothing), such as 1,200.00.")
        parsed[key] = amount
    if not parsed:
        raise ValueError("Type at least one amount.")
    for key, amount in parsed.items():
        line = by_id[key]
        item = {"id": f"{FOLDER}:{line['fact']}", "kind": "eoir26a", "level": "review", "title": "The fee waiver request's " + line["group"] + " line", "group": "paralegal",
                "actions": ["set", "blank"], "facts": [{"key": line["fact"], "label": "The fee waiver request, " + line["group"] + " line", "input": {"type": "text"}}]}
        record_decision(Path(client_dir), item, {"action": "set", "values": {line["fact"]: f"{amount:.2f}"}, "reviewer": who, **({"role": role} if role else {}),
                                                 "note": "typed by staff for the fee waiver request"})
    return view(client_dir, portal_root, role)


# -- item 4's sentence --------------------------------------------------------------------------------------------------------------------


def suggestion(graph) -> dict[str, Any] | None:
    """The shipped DRAFT wording for item 4 that fits the client's own figures, filled from them: {"wording", "slots", "text": {language: sentence}}.
    None until all nine lines are in. Which wording follows the sign of the difference (item 3); every number in it is the product's arithmetic."""
    t = totals(graph)
    if not t["complete"]:
        return None
    when = "difference_below_zero" if t["difference"] < 0 else "difference_zero" if t["difference"] == 0 else "difference_above_zero"
    wording = next(w for w in shipped()["hardship"]["wordings"] if w["when"] == when)
    def slots_for(lang: str) -> dict[str, str]:
        return {"income_total": usd_for(t["income"], lang), "expense_total": usd_for(t["expense"], lang), "difference": usd_for(t["difference"], lang)}

    return {"wording": wording["id"], "slots": slots_for("en"), "text": {lg: text.format(**slots_for(lg)) for lg, text in wording["text"].items()}}


def _sentence_check(text: str) -> str:
    text = " ".join(str(text or "").split())
    limit = shipped()["hardship"]["max_characters"]
    if len(text) > limit:
        raise ValueError(f"Item 4 has room for about {limit} characters on the form: this is {len(text)}. Shorten it (a longer account goes on an added page with the client's name and A-Number on it).")
    return text


def edit_sentence(client_dir: Path, text: str, who: str, role: str | None = None) -> dict[str, Any]:
    """The paralegal's edit of item 4: a draft for the attorney to approve. Nothing reaches the form until then."""
    who = _need(who)
    text = _sentence_check(text)
    if not text:
        raise ValueError("Write the sentence first, or ask the attorney to approve item 4 empty.")
    with _LOCK:
        rec = read(client_dir)
        request = _request(rec, Path(client_dir), who, role)
        request["sentence_draft"] = {"text": text, "by": who, "role": role, "at": clock.stamp()}
        _save(client_dir, rec, "edited", "Edited item 4 of the fee waiver request (EOIR-26A) for the attorney to approve", who, role)
    return view(client_dir, None, role)


def approve_sentence(client_dir: Path, text: str, blank: bool, who: str, role: str | None = None) -> dict[str, Any]:
    """The attorney approves item 4 for this case: exactly this text (or empty: item 4 stays blank). A decision on the case, recorded with the name, the
    day and the text, and undone with Undo (the Decision log or undo_sentence)."""
    from review.state import record_decision

    who = _need(who)
    _attorney(role, "approves item 4 of the fee waiver request")
    client_dir = Path(client_dir)
    text = _sentence_check(text)
    if not blank and not text:
        raise ValueError("Write the sentence first, or choose to leave item 4 empty.")
    with _LOCK:
        rec = read(client_dir)
        graph = case_graph(client_dir)
        request = _request(rec, client_dir, who, role, graph)
        spec = {"type": "text", "multiline": True, "maxlen": shipped()["hardship"]["max_characters"]}
        item = {"id": HARDSHIP_ITEM, "kind": "eoir26a", "level": "review", "title": "Item 4 of the fee waiver request (EOIR-26A)", "group": "attorney",
                "actions": ["set", "blank"], "facts": [{"key": HARDSHIP_KEY, "label": "Item 4 of the fee waiver request", "input": spec}]}
        record_decision(client_dir, item, {"action": "blank" if blank else "set", "values": {} if blank else {HARDSHIP_KEY: text}, "reviewer": who,
                                           **({"role": role} if role else {}), "note": "approved for this case"})
        suggested = suggestion(graph)
        own = bool(suggested) and not blank and text == suggested["text"]["en"]
        request["sentence"] = {"state": "blank" if blank else "approved", "by": who, "role": role, "at": clock.stamp(),
                               "wording": suggested["wording"] if own else None, "slots": suggested["slots"] if own else None,
                               "edited": bool(not blank and not own), "over": snapshot(graph)["figures"]}  # the figures item 4 was approved beside
        _save(client_dir, rec, "approved", "Approved item 4 of the fee waiver request (EOIR-26A)" if not blank else "Approved item 4 of the fee waiver request empty", who, role)
    return view(client_dir, None, role)


STALE_ITEM4 = "The figures changed after item 4 was approved: an attorney approves item 4 again."


def sentence_stale(request: dict[str, Any], graph) -> bool:
    """True when item 4 was approved as a sentence (it names totals) beside figures that are not the case's figures now: it would say one thing beside
    items 1 to 3 that say another. An item 4 approved empty names nothing and cannot go stale."""
    approved = request.get("sentence")
    return bool(approved) and approved.get("state") == "approved" and approved.get("over") != snapshot(graph)["figures"]


def undo_sentence(client_dir: Path, who: str, role: str | None = None) -> dict[str, Any]:
    """Takes the approval back: item 4 is not approved again until an attorney approves it."""
    from review.state import undo_decision

    who = _need(who)
    _attorney(role, "takes back the approval of item 4")
    with _LOCK:
        rec = read(client_dir)
        request = _request(rec)
        undo_decision(Path(client_dir), HARDSHIP_ITEM, who, role)
        request["sentence"] = None
        _save(client_dir, rec, "undone", "Took back the approval of item 4 of the fee waiver request (EOIR-26A)", who, role)
    return view(client_dir, None, role)


# -- the signing -----------------------------------------------------------------------------------------------------------------------------


def _client_texts(graph, text: str) -> dict[str, str] | None:
    """The approved sentence in each language, when it is exactly a shipped wording filled from this case's figures (the translations are the firm's
    DRAFT wordings); None when the attorney changed it: the client then reads the English alone, and the office explains it."""
    s = suggestion(graph)
    return dict(s["text"]) if s and text == s["text"]["en"] else None


def open_signing(client_dir: Path, drawn: bool, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The attorney opens the signing on the client's page: the affidavit and item 4's approved sentence, over the client's own figures. drawn: the
    attorney also allows a signature drawn on the phone (the typed name and the declaration are always there). Refused until all nine lines are in and
    item 4 is decided: the client signs what is filed."""
    import engagement

    who = _need(who)
    _attorney(role, "opens the signing of the fee waiver request (the office prepares it; the attorney puts it in front of the client)")
    client_dir = Path(client_dir)
    with _LOCK:
        rec = read(client_dir)
        request = _request(rec)
        if request.get("signature"):
            raise ValueError("The client has signed already.")
        graph = case_graph(client_dir, portal_root)
        t = totals(graph)
        if not t["complete"]:
            raise ValueError(f"The client's figures are not all in yet ({9 - len(t['missing'])} of 9 lines): every line is answered, $0.00 where it is nothing.")
        if not request.get("sentence"):
            raise ValueError("Approve item 4 first (or approve it empty): the client signs what is filed.")
        if sentence_stale(request, graph):
            raise ValueError(STALE_ITEM4)
        store = _portal_store(client_dir, portal_root)
        if store is None:
            raise LookupError("This client is not in the portal: print the form and record the paper signature instead.")
        text = sentence_text(graph)
        legal = legal_name(graph)
        if not legal:
            raise ValueError("The client's name is not on the case yet: the client signs as the name the case holds, so settle the name first.")
        cid = client_dir.name
        data = store.fee_waiver(cid)
        # the client signs the figures that are on the case now (their own, or a staff member's correction): they are shown to them and kept with the signature
        store.save_fee_waiver(cid, data | {"request": {"id": request["id"], "at": request["made_at"], "kind": request["kind"]},
                                           "signing": {"opened_at": clock.stamp(), "drawn": bool(drawn), "figures": snapshot(graph)["figures"], "name": legal,
                                                       "sentence": {"en": text, "texts": _client_texts(graph, text)} if text else None}})
        store.log(cid, "fee_waiver_opened", {"by": who})
        request["opened"] = {"by": who, "role": role, "at": clock.stamp(), "drawn": bool(drawn),
                             "delivery": engagement._tell_client(store, cid, client_dir.parent)}
        _save(client_dir, rec, "opened", "Opened the fee waiver request on the client's page to sign", who, role)
    return view(client_dir, portal_root, role)


def reopen(client_dir: Path, why: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The attorney voids the client's signature and the attestation (the figures or the sentence changed, or the client asks to sign again): the request
    goes to history with the reason, a new one is made over the same answers, and the client's page asks for the signature again once the signing is
    opened."""
    who = _need(who)
    _attorney(role, "voids a signature on the fee waiver request")
    why = " ".join(str(why or "").split())
    if not why:
        raise ValueError("Say why first: it is kept on the case.")
    client_dir = Path(client_dir)
    with _LOCK:
        rec = read(client_dir)
        request = _request(rec)
        rec["history"] = (rec.get("history") or []) + [request | {"voided": {"by": who, "role": role, "at": clock.stamp(), "why": why}}]
        store = _portal_store(client_dir, portal_root)
        if store is not None:
            store.save_fee_waiver(client_dir.name, {"request": {"id": _next_id(rec), "at": clock.stamp(), "kind": request["kind"]}})
        rec["request"] = request | {"id": _next_id(rec), "opened": None, "viewed": None, "signature": None, "attestation": None, "made_at": clock.stamp()}
        _save(client_dir, rec, "reopened", "Voided the signature on the fee waiver request (EOIR-26A)", who, role)
    return view(client_dir, portal_root, role)


def sync(client_dir: Path, portal_root: Path | None = None) -> bool:
    """What the client did on their page onto the case: when they opened the request, and their signature (the typed name, the date, the time, the
    address the request came from, the language shown and the figures they saw; the drawn image copied beside the record). Called by the review app
    when the case is opened and by the portal when the client signs. True when something new was recorded. Never raises."""
    try:
        client_dir = Path(client_dir)
        path = _client_file(client_dir, portal_root, "fee_waiver.json")
        if not path.exists() or not (client_dir / FILE).exists():
            return False
        data = json.loads(path.read_text(encoding="utf-8")) or {}
        with _LOCK:
            rec = read(client_dir)
            request = rec.get("request")
            if not request or (data.get("request") or {}).get("id") != request["id"]:
                return False
            changed = viewed = False
            if data.get("viewed") and not request.get("viewed"):
                request["viewed"], changed = data["viewed"], True
                viewed = True
            refused = [d for d in data.get("name_refused") or [] if d.get("request") == request["id"]]
            if len(refused) > len(request.get("name_refused") or []):  # the client typed a name that is not theirs: refused on the phone, said to staff
                request["name_refused"], changed = [{"at": d.get("at")} for d in refused], True
                _save(client_dir, rec, "name_refused", "The client tried to sign the fee waiver request (EOIR-26A) as another name: refused", who="The client", role="client")
            signed = data.get("signed")
            if signed and not request.get("signature") and signed.get("request") == request["id"]:
                sig = {k: signed.get(k) for k in ("how", "typed_name", "case_name", "at", "address", "language", "covers")}
                if (signed.get("drawn") or {}).get("file"):
                    src = path.parent / "fee_waiver" / signed["drawn"]["file"]
                    if src.is_file():
                        (client_dir / FOLDER).mkdir(exist_ok=True)
                        _private(client_dir / FOLDER / signed["drawn"]["file"], src.read_bytes())
                        sig["drawn"] = signed["drawn"]
                request["signature"], changed = sig, True
                _save(client_dir, rec, "signed", "The client signed the fee waiver request (EOIR-26A) in the portal", who="The client", role="client")
            elif viewed:
                _save(client_dir, rec, "viewed", "The client opened the fee waiver request (EOIR-26A)", who="The client", role="client")
            return changed
    except Exception as exc:  # noqa: BLE001 -- the portal keeps the signature; the next opening of the case records it
        sys.stderr.write(f"fee waiver signature not recorded on the case ({type(exc).__name__})\n")
        return False


def paper(client_dir: Path, data: bytes, on: str, who: str, role: str | None = None, portal_root: Path | None = None) -> dict[str, Any]:
    """The respondent signed the form on paper: the office uploads the signed scan (PDF, JPEG or PNG; a photo is kept as a PDF) and the day it was signed.
    The scan is what goes in the packet, as the respondent signed it. The figures and the sentence on the case now are what the paper was printed from."""
    from datetime import date

    from portal.store import MAX_UPLOAD, file_kind, image_to_pdf

    who = _need(who)
    client_dir = Path(client_dir)
    with _LOCK:
        rec = read(client_dir)
        graph = case_graph(client_dir, portal_root)
        request = _request(rec, client_dir, who, role, graph)
        if request.get("signature"):
            raise ValueError("The client has signed this request already: void it first to sign again.")
        if not totals(graph)["complete"]:
            raise ValueError("Every line of the form has an amount first ($0.00 where it is nothing): the paper is printed from them.")
        if not request.get("sentence"):
            raise ValueError("Approve item 4 first (or approve it empty): the paper is printed from it.")
        if sentence_stale(request, graph):
            raise ValueError(STALE_ITEM4)
        try:
            day = date.fromisoformat(str(on or "").strip())
        except ValueError:
            raise ValueError("Choose the date the client signed from the calendar.") from None
        if day > clock.today():
            raise ValueError("The date the client signed can't be in the future.")
        if not data or len(data) > MAX_UPLOAD:
            raise ValueError("Add the scan of the signed form (15 MB at most).")
        kind = file_kind(data)
        if kind is None:
            raise ValueError("The scan must be a PDF, JPG or PNG.")
        pdf = data if kind == "application/pdf" else image_to_pdf(data)
        (client_dir / FOLDER).mkdir(exist_ok=True)
        name = f"signed-on-paper-{request['id']}.pdf"
        _private(client_dir / FOLDER / name, pdf)
        request["signature"] = {"how": "paper", "on": day.isoformat(), "file": name, "sha256": hashlib.sha256(pdf).hexdigest(), "by": who, "role": role,
                                "at": clock.stamp(), "covers": snapshot(graph)}
        _save(client_dir, rec, "signed", "Recorded the client's signature on the paper fee waiver request (EOIR-26A)", who, role)
        store = _portal_store(client_dir, portal_root)
        if store is not None and (store.fee_waiver(client_dir.name).get("request") or {}).get("id") == request["id"]:  # the client's page stops asking
            store.save_fee_waiver(client_dir.name, store.fee_waiver(client_dir.name) | {"signed": {"request": request["id"], "how": "paper", "at": day.isoformat()}})
    return view(client_dir, portal_root, role)


# -- the attorney's attestation --------------------------------------------------------------------------------------------------------------


def attorney_name(graph) -> str:
    return " ".join(x for x in (_value(graph, "firm.preparer_given_name"), _value(graph, "firm.preparer_family_name")) if x)


PAPER_PAGE_2 = "I signed and dated page 2 of the paper form"


def attest(client_dir: Path, typed_name: str, who: str, role: str | None = None, portal_root: Path | None = None, paper_page_2: bool = False) -> dict[str, Any]:
    """The attorney's attestation box (page 2): the attorney's name and the EOIR ID from the case's office (Settings, never typed here) and the date,
    counter-signed with the attorney's typed name. After the client's signature; refused while the EOIR ID is blank. When the client signed on paper
    the scan is what is filed and nothing is placed on it, so the attorney confirms (paper_page_2) that they signed and dated page 2 of the paper
    form by hand before the scan was made; the confirmation is recorded with the attestation."""
    who = _need(who)
    _attorney(role, "attests to the fee waiver request")
    client_dir = Path(client_dir)
    with _LOCK:
        rec = read(client_dir)
        request = _request(rec)
        graph = case_graph(client_dir, portal_root)
        if not request.get("signature") or not covers_now(request["signature"], graph):
            raise ValueError("The client signs first, over the figures and the sentence that are on the case now.")
        eoir_id = str(_value(graph, "firm.eoir_id") or "").strip()
        if not eoir_id:
            raise ValueError(EOIR_ID_FIRST)
        on_paper = request["signature"].get("how") == "paper"
        if on_paper and not paper_page_2:
            raise ValueError(f"The client signed on paper: confirm “{PAPER_PAGE_2}” first. The scan in the packet is the paper as it was signed.")
        printed = attorney_name(graph)
        if not printed:
            raise ValueError("Type the attorney's name under Settings.")
        typed = " ".join(str(typed_name or "").split())
        if len(typed) < 3:
            raise ValueError("Type your full name, as you sign.")
        if len(typed) > 120:
            raise ValueError("That name is too long (120 characters at most): type it as you sign.")
        request["attestation"] = {"typed_name": typed, "by": who, "role": role, "at": clock.stamp(), "eoir_id": eoir_id, "printed_name": printed,
                                  "covers": snapshot(graph), **({"paper_page_2": {"confirmed": PAPER_PAGE_2, "at": clock.stamp()}} if on_paper else {})}
        _save(client_dir, rec, "attested", "Attested to the fee waiver request (EOIR-26A) for the firm", who, role)
    return view(client_dir, portal_root, role)


# -- the gate: what holds the packet ---------------------------------------------------------------------------------------------------------


@producer(CLIENT)
def problems(client_dir: Path, graph) -> list[str]:
    """What keeps the court motion's or the appeal's packet from being final while a fee waiver is asked for: the figures, item 4, the client's signature
    (over what is on the case now) and the attorney's attestation. Nothing when no fee waiver is asked."""
    if asked_by(graph) is None:
        return []
    out = []
    t = totals(graph)
    if not t["complete"]:
        out.append(f"The fee waiver request (Form EOIR-26A) has {9 - len(t['missing'])} of 9 lines of monthly income and expenses: every line is answered, "
                   "$0.00 where it is nothing. Send the questions to the client on the Fee waiver tab, or type the figures there.")
    rec = read(client_dir)
    request = rec.get("request") or {}
    if t["complete"] and not request.get("sentence"):
        out.append(held(ATTORNEY, "Item 4 of the fee waiver request (Form EOIR-26A) is not approved: an attorney approves the sentence (or approves it empty) on the Fee waiver tab.",
                        via="eoir26a"))
    elif t["complete"] and sentence_stale(request, graph):
        out.append(held(ATTORNEY, STALE_ITEM4, via="eoir26a"))
    sig, att = request.get("signature"), request.get("attestation")
    if not sig:
        out.append("The client has not signed the fee waiver request (Form EOIR-26A): the portal, or the signed paper scan, on the Fee waiver tab.")
    elif not covers_now(sig, graph):
        out.append("The figures or item 4 of the fee waiver request changed after the client signed it: an attorney voids the signature and the client signs again.")
    if sig and covers_now(sig, graph):
        if not att:
            out.append(held(ATTORNEY, "The attorney has not attested to the fee waiver request (Form EOIR-26A): an attorney counter-signs it on the Fee waiver tab.",
                            via="eoir26a"))
        elif not covers_now(att, graph):
            out.append(held(ATTORNEY, "The figures or item 4 changed after the attorney attested: attest again.", via="eoir26a"))
    if not _value(graph, "firm.eoir_id"):
        out.append(held(OFFICE, EOIR_ID_FIRST))
    return out


def hold_notes(graph) -> str:
    """The packet's note about the form: what it is and who signs it."""
    return ("Form EOIR-26A: the client's own figures, the arithmetic and the signing record are on the Fee waiver tab. The affidavit is the client's to sign, "
            "not the attorney's; which kind of signature the court accepts is the attorney's to decide.")


# -- the form's marks and the signing record (after the fill) -------------------------------------------------------------------------------------


def finish(path: Path, graph, client_dir: Path, portal_root: Path | None = None) -> None:
    """The filled form with the client's signature in its box (the typed name as "/s/ Name", or the drawn image), the attorney's typed signature in
    theirs, and the signing record after the last page; a form signed on paper is the signed scan, as the respondent signed it, with the record after it.
    Marks are placed only while the signature still covers the figures and the sentence on the case. Nothing is added to a form with no signing yet."""
    from pypdf import PdfReader, PdfWriter

    from review.bundle import _Sheet

    client_dir = Path(client_dir)
    sync(client_dir, portal_root)
    request = read(client_dir).get("request")
    if not request or not request.get("signature"):
        return
    sig, att = request["signature"], request.get("attestation")
    ok = covers_now(sig, graph)
    if sig.get("how") == "paper" and ok and (client_dir / FOLDER / str(sig.get("file"))).is_file():
        writer = PdfWriter()
        scan = PdfReader(str(client_dir / FOLDER / sig["file"]))
        if scan.is_encrypted:
            scan.decrypt("")
        for page in scan.pages:
            writer.add_page(page)
    else:
        writer = PdfWriter(clone_from=str(path))  # the form with its boxes: they stay boxes
        if ok and sig.get("how") == "portal":
            over = _Sheet()
            drawn = sig.get("drawn") or {}
            image = client_dir / FOLDER / str(drawn.get("file") or "")
            if drawn.get("file") and image.is_file():
                _place_drawing(over, image)  # the drawing is the mark; the typed name is on the signing record and in the print-name box
            else:
                over.text(58, 476, f"/s/ {sig.get('case_name') or sig.get('typed_name')}", "F3", 11)  # the client's legal name only (equal to the typed one)
            writer.pages[0].merge_page(over.to_page(writer))
        if att and ok and covers_now(att, graph):
            over = _Sheet()
            over.text(22, 158, f"/s/ {att.get('typed_name')}"[:34], "F3", 10)
            writer.pages[1].merge_page(over.to_page(writer))
    for page in audit_pages(request, graph, client_dir):
        writer.add_page(page.to_page(writer))
    with open(path, "wb") as fh:
        writer.write(fh)


def _place_drawing(sheet, image_path: Path) -> None:
    """The drawn signature, trimmed to its ink, in the respondent's signature box (and the little room above it): scaled to fit, never stretched."""
    from PIL import Image, ImageOps

    from review.bundle import _jpeg

    im = Image.open(image_path).convert("L")
    box = ImageOps.invert(im).getbbox()
    if box:
        im = im.crop(box)
    jpeg, size, w, h = _jpeg(im, 250, 30)  # the box is 23 points high and the label above it starts 8 points over its top
    sheet.image(jpeg, size, 58, 467 + (30 - h) / 2, w, h)


def _time(at: Any) -> str:
    t = clock.local(at)
    return t.strftime("%I:%M %p").lstrip("0") if t else "not recorded"


def _day(at: Any) -> str:
    d = clock.local_date(at)
    return d.strftime("%m/%d/%Y") if d else "not recorded"


def how_signed(sig: dict[str, Any] | None) -> str:
    """"Typed name in the portal", "Typed name and drawn signature in the portal" or "Signed on paper": which way this case's form was signed."""
    if not sig:
        return "Not signed yet"
    if sig.get("how") == "paper":
        return "Signed on paper (the scan is what is filed)"
    return "Typed name and drawn signature in the portal" if sig.get("drawn") else "Typed name in the portal"


def audit_pages(request: dict[str, Any], graph, client_dir: Path) -> list:
    """The signing record: made from the case's own record, as a signing service's audit page is: who made the request, when it was sent, opened and
    signed, how, from which address, in which language, and what was signed. It records the signing; it is not a signature."""
    from review.bundle import _jpeg, _wrap
    from review.bundle import _Sheet
    from fill.continuation import HEIGHT, MARGIN, WIDTH

    sig, att = request.get("signature") or {}, request.get("attestation")
    t = totals(graph)
    pages: list = []
    state = {"y": 0.0, "p": None}

    def new():
        p = _Sheet()
        pages.append(p)
        state["p"], state["y"] = p, HEIGHT - 56
        return p

    new()

    def line(text: str, font: str = "F3", size: float = 9.5, gap: float = 3, indent: float = 0) -> None:
        for part in _wrap(text, size, WIDTH - 2 * MARGIN - indent):
            if state["y"] < 70:
                new()
            state["p"].text(MARGIN + indent, state["y"], part, font, size)
            state["y"] -= size + 3
        state["y"] -= gap

    zone = clock.zone_name()
    name = _value(graph, "eoir26a.print_name") or _value(graph, "applicant.given_name") or ""
    line("Signing record: Form EOIR-26A (Fee Waiver Request)", "F2", 14, 6)
    line("Made by the case system from its own record. It records how the form was signed; it is not a signature. Times are the office's "
         f"time ({zone}).", gap=8)
    line("The respondent", "F2", 10.5)
    line(f"{name}   A-Number: {_value(graph, 'applicant.a_number') or 'not on the case'}", gap=6)
    line("The request", "F2", 10.5)
    line(f"Made by {request.get('made_by')} on {_day(request.get('made_at'))} at {_time(request.get('made_at'))}.")
    sent = request.get("sent")
    line(f"Sent to the respondent's page by {sent['by']} on {_day(sent['at'])} at {_time(sent['at'])}." if sent else "Not sent to the respondent's page: the office typed the figures or used paper.")
    opened = request.get("opened")
    line((f"Signing opened by {opened['by']} on {_day(opened['at'])} at {_time(opened['at'])}"
          + ("; a drawn signature was allowed" if opened.get("drawn") else "; the typed name and the declaration") + ".") if opened else "Signing was not opened on the respondent's page.")
    line(f"Opened by the respondent: {_day(request['viewed'])} at {_time(request['viewed'])}." if request.get("viewed") else "Opened by the respondent: not recorded.", gap=6)
    line("The signature", "F2", 10.5)
    if sig.get("how") == "paper":
        line(f"Signed on paper on {_day(sig.get('on'))}. The scan was recorded by {sig.get('by')} on {_day(sig.get('at'))} at {_time(sig.get('at'))} "
             f"(file fingerprint {str(sig.get('sha256'))[:16]}).", gap=6)
    else:
        lang = LANGUAGE_NAMES.get(sig.get("language") or "en", sig.get("language"))
        line(f"Signed by the respondent in the portal: typed name “{sig.get('typed_name')}”"
             + (f" (the name the case holds: “{sig.get('case_name')}”)" if sig.get("case_name") else "") + f", on {_day(sig.get('at'))} at {_time(sig.get('at'))}, "
             f"from the internet address {sig.get('address') or 'not known'}, reading the affidavit in {lang}"
             + (" with the English beside it" if sig.get("language") not in (None, "en") else "") + ". The respondent ticked “I have read and agree” and typed their name.")
        if sig.get("drawn"):
            line(f"A signature drawn on the phone is on the form and below (image fingerprint {str(sig['drawn'].get('sha256'))[:16]}).")
            image = client_dir / FOLDER / str(sig["drawn"].get("file"))
            if image.is_file():
                from PIL import Image, ImageOps

                im = Image.open(image).convert("L")
                box = ImageOps.invert(im).getbbox()
                im = im.crop(box) if box else im
                jpeg, size, w, h = _jpeg(im, 220, 50)
                if state["y"] - h < 70:
                    new()
                state["p"].image(jpeg, size, MARGIN + 6, state["y"] - h, w, h)
                state["y"] -= h + 8
        state["y"] -= 3
    line("What the signature covers (the figures the respondent saw)", "F2", 10.5)
    covered = (sig.get("covers") or {}).get("figures") or {}
    for item in lines():
        amount = covered.get(item["id"])
        line(f"{'Income' if item['group'] == 'income' else 'Expense'}: {short_label(item)}: {usd(Decimal(amount)) if amount is not None else 'not answered'}", size=9, gap=0, indent=8)
    line(f"Total monthly income {usd(t['income']) if t['income'] is not None else 'not made'}; total monthly expenses "
         f"{usd(t['expense']) if t['expense'] is not None else 'not made'}; difference {usd(t['difference']) if t['difference'] is not None else 'not made'}.", gap=3)
    text = (sig.get("covers") or {}).get("sentence")
    line("Item 4, as signed: " + (f"“{text}”" if text else "left empty."), gap=6)
    line("The attorney's attestation", "F2", 10.5)
    if att:
        line(f"Attested for the firm by {att.get('printed_name')} (EOIR ID {att.get('eoir_id')}) on {_day(att.get('at'))}; counter-signed with the typed name "
             f"“{att.get('typed_name')}” at {_time(att.get('at'))}, recorded by {att.get('by')}.")
        if att.get("paper_page_2"):
            line(f"The attorney confirmed on {_day(att['paper_page_2'].get('at'))} at {_time(att['paper_page_2'].get('at'))}: “{PAPER_PAGE_2}”. "
                 "The signature on page 2 is the one on the paper scan.")
    else:
        line("Not attested yet.")
    for n, p in enumerate(pages):
        p.text(WIDTH - MARGIN - 56, 22, f"Record {n + 1} of {len(pages)}", "F3", 7)
    return pages


# -- the case page's card -----------------------------------------------------------------------------------------------------------------------


def _source(client_dir: Path, graph, line: dict[str, Any], portal_root: Path | None) -> dict[str, Any] | None:
    fact = graph.get(line["fact"])
    if fact is None or fact.status != "resolved" or fact.value in (None, ""):
        return None
    review = getattr(fact, "review", None)
    if review is not None and review.resolved_by:
        from review.state import load_decisions

        decision = load_decisions(Path(client_dir)).get(f"{FOLDER}:{line['fact']}") or {}
        day = clock.local_date(decision.get("at")) if decision.get("at") else None
        return {"how": "typed", "by": review.resolved_by, "on": day.isoformat() if day else None}
    if any(s.doc_id == PORTAL_DOC_ID for s in fact.sources):
        return {"how": "portal", "by": "the client", "on": answered_on(client_dir, line["id"], portal_root)}
    return {"how": "other", "by": None, "on": None}


def view(client_dir: Path, portal_root: Path | None = None, role: str | None = None) -> dict[str, Any]:
    """The case page's Fee waiver request (EOIR-26A) card: every figure with its source, the arithmetic, item 4, the signing, the attestation, and what
    still holds the packet."""
    client_dir = Path(client_dir)
    sync(client_dir, portal_root)
    graph = case_graph(client_dir, portal_root)
    rec = read(client_dir)
    request = rec.get("request")
    t = totals(graph)
    figures = []
    for item in lines():
        amount = parse_money(_value(graph, item["fact"]))
        figures.append({"id": item["id"], "group": item["group"], "label": short_label(item), "title": item["form_row"], "amount": fmt(amount) if amount is not None else None,
                        "source": _source(client_dir, graph, item, portal_root)})
    arithmetic = []
    if t["income"] is not None:
        arithmetic.append("Item 1.A, total income: " + " + ".join(fmt(parse_money(_value(graph, x["fact"]))) for x in lines() if x["group"] == "income") + f" = {usd(t['income'])}")
    if t["expense"] is not None:
        arithmetic.append("Item 2.B, total expenses: " + " + ".join(fmt(parse_money(_value(graph, x["fact"]))) for x in lines() if x["group"] == "expense") + f" = {usd(t['expense'])}")
    if t["difference"] is not None:
        arithmetic.append(f"Item 3, the difference: {usd(t['income'])} - {usd(t['expense'])} = {usd(t['difference'])}")
    suggested = suggestion(graph)
    sig, att = (request or {}).get("signature"), (request or {}).get("attestation")
    approved = (request or {}).get("sentence")
    store = _portal_store(client_dir, portal_root)
    eoir_id = str(_value(graph, "firm.eoir_id") or "").strip()
    asked = asked_by(graph)
    steps = [
        {"id": "asked", "title": "The attorney asked for a fee waiver" if asked else "A fee waiver is not asked for on a motion or an appeal yet",
         "done": bool(asked or request), "detail": {"motion": "On the motion to the judge.", "appeal": "On the appeal to the Board.", None: ""}[asked]},
        {"id": "figures", "title": f"The client's nine monthly lines ({9 - len(t['missing'])} of 9)", "done": t["complete"]},
        {"id": "sentence", "title": "Item 4 approved by an attorney", "done": bool(approved) and not sentence_stale(request or {}, graph),
         "detail": STALE_ITEM4 if sentence_stale(request or {}, graph) else ""},
        {"id": "signed", "title": "The client signed", "done": bool(sig) and covers_now(sig, graph), "detail": how_signed(sig)},
        {"id": "attested", "title": "The attorney attested", "done": bool(att) and covers_now(att, graph) and bool(sig) and covers_now(sig, graph)},
    ]
    return {
        "asked": asked, "in_portal": store is not None, "role": role, "figures": figures, "arithmetic": arithmetic, "totals": words(t), "complete": t["complete"],
        "request": None if not request else {k: request.get(k) for k in ("id", "kind", "made_by", "made_at", "sent", "opened", "viewed")},
        "name_refused": [f"The client tried to sign as another name on {_day(d.get('at'))}." for d in (request or {}).get("name_refused") or []],
        "sentence": {"suggested": suggested["text"]["en"] if suggested else None, "wording": suggested["wording"] if suggested else None,
                     "draft": (request or {}).get("sentence_draft"), "approved": approved | {"text": sentence_text(graph)} if approved else None,
                     "max": shipped()["hardship"]["max_characters"], "stale": sentence_stale(request or {}, graph), "stale_words": STALE_ITEM4},
        "signature": None if not sig else {"how": sig.get("how"), "words": how_signed(sig), "at": sig.get("at") or sig.get("on"), "typed_name": sig.get("typed_name"),
                                           "case_name": sig.get("case_name"), "current": covers_now(sig, graph), "paper": bool(sig.get("file"))},
        "attestation": None if not att else {k: att.get(k) for k in ("typed_name", "by", "at", "eoir_id", "printed_name", "paper_page_2")} | {"current": covers_now(att, graph)},
        "attorney": {"name": attorney_name(graph), "eoir_id_set": bool(eoir_id), "eoir_id": eoir_id or None},
        "steps": steps, "problems": problems(client_dir, graph), "history": rec.get("history") or [],
        "can_approve": role != "paralegal", "can_open": role != "paralegal", "can_attest": role != "paralegal",
        "fee_waiver_lines": shipped()["form_lines"], "hold": hold_notes(graph),
    }


def pdf_path(client_dir: Path, graph=None, portal_root: Path | None = None) -> Path:
    """The form as it stands now (the case's eoir26a_filled.pdf, filled again from the case first): for the office to open, or to print for a paper signature."""
    from fill.companion import fill_companions, load_profile

    client_dir = Path(client_dir)
    graph = graph if graph is not None else case_graph(client_dir, portal_root)
    profile = load_profile()
    profile["forms"] = {"eoir26a": profile["forms"]["eoir26a"]}
    fill_companions(graph, client_dir, profile)
    return client_dir / profile["forms"]["eoir26a"]["output"]


# -- the client's side: the section and the signing on their page ------------------------------------------------------------------------------


def questions() -> dict[str, dict[str, Any]]:
    return {q["id"]: q for q in shipped()["section"]["questions"]}


def _help(kind: str | None = None) -> dict[str, Any]:
    """The section's "why we ask" (for an appeal the Board decides, not the judge) and each line's tip."""
    s = shipped()["section"]
    return {"sections": {"money": {"why": s["why_appeal"] if kind == "appeal" else s["why"], "icon": s["icon"], "minutes": s["minutes"]}},
            "questions": {q["id"]: q["tip"] for q in s["questions"]}, "faq": []}


def client_view(store, client_id: str, lang: str) -> dict[str, Any] | None:
    """What the client's page shows for the request: the section in their language (the nine lines with their help), the arithmetic as they have answered,
    and, once the attorney opens the signing, the affidavit in their language with the English beside it, item 4's sentence and the signature boxes.
    None when the office has not sent it (or the case ended)."""
    from portal.bank import localized

    fw = store.fee_waiver(client_id)
    request = fw.get("request")
    if not request or store.engagement(client_id).get("ended"):
        return None
    answers = store.answers(client_id)
    s = shipped()["section"]
    kind = request.get("kind")
    plain = {"sections": [{**{k: v for k, v in s.items() if k not in ("example", "intro_appeal", "why_appeal")}, "intro": s["intro_appeal"] if kind == "appeal" else s["intro"],
                           "questions": [{k: v for k, v in q.items() if k != "tip"} for q in s["questions"]]}]}
    section = localized(plain, lang, answers, _help(kind))[0]
    pick = lambda t: t.get(lang) or t["en"]  # noqa: E731
    section["example"] = pick(s["example"])
    signed, signing = fw.get("signed"), fw.get("signing")
    amounts = _signing_amounts(signing) if signing else {x["id"]: parse_money(answers.get(x["id"])) for x in lines()}  # once the signing is open: the figures to sign
    t = totals_of(amounts)
    sentence = (signing or {}).get("sentence")
    declaration = shipped()["declaration"]
    return {"id": request["id"], "section": section, "missing": t["missing"], "totals": words(t, lang), "complete": t["complete"],
            "open": bool(signing) and not signed, "drawn": bool((signing or {}).get("drawn")), "locked": bool(signing) or bool(signed),
            "signed_on": clock.local_date(signed["at"]).isoformat() if signed and clock.local_date(signed.get("at")) else None,
            "declaration": {"en": declaration["en"], "own": declaration.get(lang) if lang != "en" else None},
            "sentence": None if not signing else ({"en": sentence["en"], "own": (sentence.get("texts") or {}).get(lang) if lang != "en" else None} if sentence else {"en": "", "own": None}),
            "figures": [{"id": x["id"], "group": x["group"], "label": next(q for q in section["questions"] if q["id"] == x["id"])["label"],
                         "amount": usd_for(amounts[x["id"]], lang) if amounts[x["id"]] is not None else None} for x in lines()]}


def check_drawing(data_url: Any) -> bytes:
    """The drawn signature as the page sends it ("data:image/png;base64,..."): a real, small PNG with ink in it, flattened on white and re-saved (nothing
    else in the file is kept). Raises ValueError with a code the page words in the client's language."""
    from PIL import Image, ImageOps

    text = str(data_url or "")
    if not text.startswith("data:image/png;base64,") or len(text) > MAX_DRAWING * 4 // 3 + 100:
        raise ValueError("drawing_invalid")
    try:
        raw = base64.b64decode(text.split(",", 1)[1], validate=True)
    except (binascii.Error, ValueError):
        raise ValueError("drawing_invalid") from None
    if len(raw) > MAX_DRAWING or raw[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("drawing_invalid")
    try:
        im = Image.open(io.BytesIO(raw))
        if im.width > 2000 or im.height > 1000 or im.width < 40 or im.height < 10:
            raise ValueError("drawing_invalid")
        im = im.convert("RGBA")
    except (OSError, Image.DecompressionBombError):
        raise ValueError("drawing_invalid") from None
    flat = Image.new("RGB", im.size, "white")
    flat.paste(im, mask=im.split()[3])
    if not ImageOps.invert(flat.convert("L")).getbbox():
        raise ValueError("drawing_blank")
    out = io.BytesIO()
    flat.save(out, format="PNG", optimize=True)
    return out.getvalue()


def view_by_client(store, client_id: str) -> bool:
    """The client opened the request: the first time is kept. True when this was the first time."""
    request = (store.fee_waiver(client_id) or {}).get("request")
    return bool(request) and store.note_fee_waiver_viewed(client_id, request["id"])


def sign_in_portal(store, client_id: str, body: dict[str, Any], address: str, lang: str, legal: str | None = None) -> dict[str, Any]:
    """The client signs: they ticked "I have read and agree" and typed their name (and drew it, when the attorney allowed that). Kept: the typed name, the
    time, the address the request came from, the language they read it in, and the figures and the sentence they saw. Raises ValueError with a code the
    page words in the client's language."""
    fw = store.fee_waiver(client_id)
    request, signing = fw.get("request"), fw.get("signing")
    if not request or not signing or fw.get("signed") or str(body.get("request") or "") != request["id"]:
        raise LookupError("unknown request")
    typed = " ".join(str(body.get("signature") or "").split())
    if body.get("agree") is not True or len(typed) < 3:
        raise ValueError("agree_first")
    if len(typed) > 120:
        raise ValueError("name_too_long")
    amounts = _signing_amounts(signing)
    if not totals_of(amounts)["complete"]:
        raise ValueError("money_incomplete")
    legal = " ".join(str(signing.get("name") or "").split())  # the name the case held when the attorney opened the signing
    if not signs_as_client(typed, legal):  # the affidavit is the respondent's: another name signs nothing, and staff are told
        store.refuse_fee_waiver_name(client_id, request["id"])
        raise ValueError("name_not_client")
    drawing = None
    if body.get("drawing"):
        if not signing.get("drawn"):
            raise ValueError("drawing_not_allowed")
        drawing = check_drawing(body["drawing"])
    sentence = (signing.get("sentence") or {}).get("en") or ""
    signed = {"typed_name": typed, "case_name": legal, "address": address, "language": lang if lang in LANGS else "en",
              "covers": {"figures": {k: f"{v:.2f}" for k, v in amounts.items()}, "sentence": sentence}}
    return store.sign_fee_waiver(client_id, request["id"], signed, drawing)


def _signing_amounts(signing: dict[str, Any]) -> dict[str, Decimal | None]:
    """The figures the signing was opened over (the case's, when the attorney opened it): what the client sees and signs."""
    figures = signing.get("figures") or {}
    return {x["id"]: parse_money(figures.get(x["id"])) for x in lines()}
