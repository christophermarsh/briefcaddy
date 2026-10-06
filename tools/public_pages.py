"""Writes docs/public/: the pages a firm that is deciding whether to buy can read, made from the code and the registers, never typed.

    python tools/public_pages.py                 write every page (and build the sample packet and review bundle)
    python tools/public_pages.py --no-sample     write the pages; keep the sample PDFs already in docs/public/sample
    python tools/public_pages.py --check         fail (exit 1) when a page in docs/public differs from what the code says now
    python tools/public_pages.py --out <folder>  write to another folder (to publish with the provider's name filled in)

The pages:
  what_it_does.md    the filings by track, what each packet checks, what is read locally, what the register checks, what it does not do
  security.md        what is true today (the product data statement's lines), what leaves the installation, what is not there yet
  data_statement.md  the product data statement as the pack's export (tools/export_firm.py --pack) writes it, with the provider's name
  price.md           owner-approved structured commercial source, or the owner's placeholder while draft
  sample/            a review bundle and a packet built from one made-up case, every page marked SAMPLE, and a README that says what they are
  accuracy.md        written by tools/accuracy_report.py; this tool only checks its stamp matches the version
  README.md          which page is made by what

Product numbers and lists come from a function, a schema or a register item (src/packet.py,
src/journey.py, src/maintenance.py, schemas/**/*.json) or from a sentence the product's own papers already carry (docs/security/, docs/design_plan.md);
a sentence the code cannot show is not on a page. A page that would say something false (a connector switched on, a missing source line) is refused
at generation, not written. Every page carries the software version and its release date (src/version.py), so a release changes every page, and
tests/test_public_pages.py fails when a page in docs/public is not what this tool writes from the tip. Commercial prices and service terms
come only from an explicit owner-approved source; the shipped draft never publishes its provisional assumptions.

The provider's name and security contact are read from deployment.json when the installation has them (src/deployment.py); without them the data
statement says "[the provider's name]". The check always compares with the repository's own copy (no provider details), so an installation that
fills them in writes its pages to a folder of its own with --out.
"""

from __future__ import annotations

import argparse
import collections
import inspect
import html
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402
sys.path.insert(0, str(REPO / "tools"))

PUBLIC = REPO / "docs" / "public"
SAMPLE_DIR = "sample"
SAMPLE_PACKET, SAMPLE_BUNDLE = "sample_packet.pdf", "sample_review_bundle.pdf"
PRICE_SENTENCE = "The owner writes this page."
PROVIDER_PLACEHOLDERS = {"name": "[the provider's name]", "email": "[the provider's security contact]", "phone": ""}

# Names a public page never carries (the competitors the positioning notes list, and the practice-management systems the firm connects to):
# a page naming one is refused at generation. tests/test_public_pages.py greps every page for the same list.
COMPETITORS = ("clio", "filevine", "docketwise", "prolexis", "caseblink", "visalaw", "quickfiling", "instafill", "cerenade", "eimmigration", "mitratech",
               "lawlogix", "imagility", "prima.law", "inszoom", "lawmatics", "mycase", "smokeball", "equifax", "camplegal")
# Words and marks a public page never carries: puffery, a claim of certification, and the ways the notes write a dash.
FORBIDDEN = ("cutting edge", "cutting-edge", "best", "ai-powered", "compliant", "complies", "soc 2 certified", "guarantee", "meets", "state of the art",
             "world-class", "revolutionary", "seamless")
FORBIDDEN_MARKS = ("—", "–", " -- ")


class PageError(Exception):
    """A page that would say something the code cannot show: nothing is written."""


# -- reading the product's own papers ------------------------------------------------------------------------------------------


def _read(rel: str | Path) -> str:
    return (REPO / rel).read_text(encoding="utf-8").replace("\r\n", "\n")


def _json(rel: str | Path) -> Any:
    return json.loads(_read(rel))


def stamp() -> str:
    import clock
    import version

    return f"Software version {version.VERSION}, released {clock.us_date(version.RELEASED)}."


def section(text: str, heading: str, level: int = 2) -> str:
    """The body under "## heading" up to the next heading of the same level or higher."""
    marks = "#" * level
    m = re.search(rf"(?ms)^{marks} {re.escape(heading)}\s*$(.*?)(?=^#{{1,{level}}} |\Z)", text)
    if not m:
        raise PageError(f"the heading {heading!r} is not in the paper this page is made from")
    return m.group(1).strip("\n")


def table_rows(body: str) -> list[list[str]]:
    """The rows of the first markdown table in body, without the header and the rule under it."""
    rows = [[c.strip() for c in line.strip().strip("|").split("|")] for line in body.splitlines() if line.lstrip().startswith("|")]
    return [r for r in rows[2:]]


def plain(text: str) -> str:
    """A sentence for a public page: no bold marks, no code spans (a parenthesis that holds only code goes with it), one space between words."""
    text = re.sub(r"\s*\((?:[^()`]*`[^`]*`[^()`]*)+\)", "", text)
    text = re.sub(r"`[^`]*`", "", text).replace("**", "")
    text = re.sub(r"\[decided by the provider:[^\]]*\]", "", text)
    return re.sub(r"\s+", " ", text).strip()


def first_sentence(text: str) -> str:
    text = plain(text)
    m = re.match(r"(.+?[.!?])(?:\s|$)", text)
    return m.group(1) if m else text


def sentences(text: str) -> list[str]:
    return [s for s in re.split(r"(?<=[.!?])\s+(?=[A-Z\"(])", plain(text)) if s]


def drop_citations(title: str) -> str:
    """A paper's name without a parenthesis that cites a rule ("(8 CFR 214.204)"): the page names papers, it does not cite law."""
    pattern = re.compile(r"\s*\((?:[^()]|\([^()]*\))*(?:CFR|U\.S\.C\.)(?:[^()]|\([^()]*\))*\)")
    while pattern.search(title):
        title = pattern.sub("", title)
    return re.sub(r"\s+", " ", title).strip(" ,;:")


def tidy(text: str) -> str:
    """Dashes the notes use between clauses become the colon or comma a person writes."""
    return text.replace(" -- ", ": ").replace("—", ",").replace("–", "-")


def join_words(items: list[str], last: str = "and") -> str:
    items = [i for i in items if i]
    return items[0] if len(items) == 1 else (", ".join(items[:-1]) + f" {last} " + items[-1]) if items else ""


def noun(n: int, one: str, many: str | None = None) -> str:
    return f"{n:,} {one if n == 1 else (many or one + 's')}"


# -- what_it_does: the filings, by track --------------------------------------------------------------------------------------------


def filing_title(filing: str) -> str:
    import packet

    return packet.filing_title(filing)


def filings() -> list[dict[str, Any]]:
    """Every filing the product prepares (src/packet.py FILINGS), in its order: {id, title, forms, required, signers}. The forms are the packet's own
    names for them, the required papers are the exhibits its schema holds the packet back for, the signers are who its forms have sign."""
    import packet

    out = []
    for fid in packet.FILINGS:
        schema = packet.load_filing(fid)
        forms = packet.forms_in(schema)
        signers = []
        for _id, form in forms:
            for who in (form.get("signatures") or {}):
                word = packet.SIGNERS.get(who, f"The {who}").replace("The ", "the ", 1)
                if word not in signers:
                    signers.append(word)
        out.append({"id": fid, "title": tidy(filing_title(fid)), "forms": [form["short"] for _id, form in forms],
                    "required": [tidy(drop_citations(ex["title"])) for ex in schema.get("exhibits", []) if ex.get("required")], "signers": signers})
    return out


def tracks() -> list[dict[str, Any]]:
    """The case paths (schemas/registers/journey.json "stages") with the filings their stages lead to (its "next_filings"), each filing once, in the order the path reaches it."""
    import journey

    cfg = journey.settings()
    out = []
    for track, stage_list in cfg["stages"].items():
        if track not in journey._TRACK_WORDS:
            raise PageError(f"the case path {track!r} has no plain name in the journey's track words")
        ids: list[str] = []
        for stage in stage_list:
            for item in cfg["next_filings"].get(stage, []):
                if item[0] not in ids:
                    ids.append(item[0])
        out.append({"track": track, "name": journey._TRACK_WORDS[track], "filings": ids})
    return out


def declaration_filings() -> list[str]:
    import drafting

    return list(drafting.FILINGS)


def declaration_sentences() -> list[str]:
    """The two things the declaration's practice (src/drafting.py PRACTICE) says that a firm asks about, in the practice's own words."""
    import drafting

    every = sentences(drafting.PRACTICE)
    found = [next((s for s in every if s.startswith(start)), None) for start in ("Nothing else is added", "Only an attorney marks")]
    if None in found:
        raise PageError("the declaration's practice no longer carries the sentences this page quotes")
    return [str(s) for s in found]


READY_EXAMPLES = ("hasn't been filled yet", "review card", "Missing:")  # a word in each of the three messages: not filled, a card open, a paper missing


def signoff_sentence() -> str:
    """The data statement's own line on who signs off (its item 4)."""
    line = next((t["span"] for t in true_today() if t["span"].startswith("Legal sign-off")), None)
    if not line:
        raise PageError("the data statement no longer says who signs off")
    return line


def readiness_examples() -> list[str]:
    """What a packet says when it is not ready, in its own words. Asked of packet.plan() on an empty case folder for the I-485 with one blocking card, so the words are
    the code's; a message the code no longer gives is not on the page."""
    import packet

    schema = packet.load_filing("i485")
    with tempfile.TemporaryDirectory() as tmp:
        problems = packet.plan(Path(tmp), {"blocking": 1}, schema)["problems"]
    out = []
    for needle in READY_EXAMPLES:
        message = next((p for p in problems if needle in p), None)
        if message is None:
            raise PageError(f"the packet no longer says anything with {needle!r} when it is not ready")
        out.append(tidy(message))
    return out


READ_BY_ITSELF = ("text", "form_footer", "notice_case_type")  # how the classifier knows a kind (schemas/registers/document_types.json "recognized")


def document_types() -> dict[str, list[str]]:
    """The kinds of document (schemas/registers/document_types.json): {"read": the ones the classifier recognises by itself, "placed": the ones it knows by name and a person
    places}. The browser's print header and the fallback "not recognized yet" are neither."""
    types = _json(schema_path.path("register", "document_types"))["types"]
    return {"read": [t["name"] for t in types if t["recognized"] in READ_BY_ITSELF], "placed": [t["name"] for t in types if t["recognized"] == "not_yet"]}


def portal_languages() -> list[str]:
    from portal import bank

    names = bank.language_names()
    return [names[c] for c in bank.languages()]


def translation_sentences() -> list[str]:
    """What the portal's wording status says, from schemas/questions/intake.json: every language a draft, Haitian Creole a machine draft."""
    status = _json(schema_path.path("question", "intake"))["translation_status"]
    out = []
    if all("draft" in v.lower() for v in status.values()):
        out.append("All of the client's wording is a draft until an attorney approves it.")
    if "machine draft" in status.get("ht", "").lower():
        out.append("The Haitian Creole is a machine draft, not certified, and waits for a certified translator and an attorney's review.")
    return out


def local_only() -> tuple[str, list[str]]:
    """The statement that the reader and the translator send nothing out, and the parts that run only on the firm's machine (docs/security/subprocessors.md)."""
    text = _read("docs/security/subprocessors.md")
    short = section(text, "The short answer")
    lead = re.search(r"\*\*(The document reader and the translator run on the firm's own installation and send nothing out\.)\*\*", short)
    if not lead:
        raise PageError("the list of outside services no longer says that the reader and the translator send nothing out")
    parts = [plain(m).rstrip(".") for m in re.findall(r"(?m)^- \*\*(.+?)\*\*", section(text, "Local only: the reader and the translator"))]
    parts = [re.sub(r"\.$", "", p) for p in parts if "At install" not in p]
    return lead.group(1), [p for p in parts if p and not p.startswith("One caveat")]


# -- what_it_does: the register ---------------------------------------------------------------------------------------------------------

LIVE_KINDS = {"uscis_form_edition": "Form editions", "page_updated": "Official pages that hold mailing addresses and rules",
              "g1055_edition": "The fee schedule", "i864p_effective": "The poverty guidelines for the affidavit of support",
              "links": "Post office look-up links", "tps_pages": "The Temporary Protected Status pages",
              "api_catalog": "Whether USCIS offers a way for a program to file a form"}


def register() -> dict[str, Any]:
    """The upkeep register (schemas/registers/maintenance.json) in counts and names: its items by cadence and by who keeps them current, and the ones the overnight run
    looks up itself on the official page (the check types src/maintenance.py live_checks handles), by kind with the items' names."""
    import maintenance

    items = _json(schema_path.path("register", "maintenance"))["items"]
    live_types = set(re.findall(r'check\["type"\] == "(\w+)"', inspect.getsource(maintenance.live_checks)))
    unnamed = sorted(live_types - set(LIVE_KINDS))
    if unnamed:
        raise PageError(f"the overnight run checks a new kind of source with no plain name on the public page: {', '.join(unnamed)}")
    live: dict[str, list[str]] = collections.OrderedDict((k, []) for k in LIVE_KINDS if k in live_types)
    for item in items:
        if item["check"]["type"] in live_types:
            live[item["check"]["type"]].append(item_name(item))
    return {"total": len(items), "cadence": dict(collections.Counter(i["cadence"] for i in items)),
            "party": dict(collections.Counter(i.get("party", "provider") for i in items)),
            "live": {LIVE_KINDS[k]: sorted(v) for k, v in live.items()}, "live_total": sum(len(v) for v in live.values())}


def item_name(item: dict[str, Any]) -> str:
    """An item's short name: a form's number for a form edition, else the words of the item before its first colon or bracket."""
    what = item["what"]
    if item["check"]["type"] == "uscis_form_edition":
        m = re.search(r"Form ([A-Z]{1,4}-\d+[A-Z]*)(?:,? (Supplement [A-Z]))?", what)
        if m:
            return m.group(1) + (f" {m.group(2)}" if m.group(2) else "")
    name = what
    while re.search(r"\s\([^()]*\)", name):  # a parenthesis that closes goes with what is in it ("245(i)" is a name's own)
        name = re.sub(r"\s\([^()]*\)", "", name)
    name = re.split(r":|;|\s\(", name)[0]  # then the item's first clause, and never a fragment that opens a bracket
    name = tidy(re.sub(r"\s+", " ", name)).strip(" ,;:-")
    if len(name) > 100 and ", " in name[:100]:  # a name that runs on is cut at its last comma inside 100 characters
        name = name[:100].rsplit(", ", 1)[0]
    return name if name.endswith("U.S.") else name.rstrip(".")


# -- what_it_does: what it does not do ---------------------------------------------------------------------------------------------------


def does_not_do() -> list[str]:
    """The topics of docs/design_plan.md "What I'd say no to": each sentence's words before its colon. The reasons stay in the plan."""
    body = " ".join(section(_read("docs/design_plan.md"), "What I'd say no to").split())
    topics = []
    for s in re.split(r"(?<=\.)\s+(?=[A-Z])", body):
        if ": " in s:
            topics.append(s.split(": ")[0].strip())
    if not topics:
        raise PageError("docs/design_plan.md no longer lists what the product says no to")
    return [t for t in topics if not now_done(t)]


def what_the_product_does_now() -> dict[str, bool]:
    """What a function shows the product does, by the word the plan uses for it: a client signs the agreement with a typed name (src/engagement.py), a calendar feed
    (src/calendar_feed.py), prospects and notes (src/prospects.py)."""
    import calendar_feed
    import engagement
    import prospects

    return {"e-signature": hasattr(engagement, "countersign") and hasattr(engagement, "signature_words"), "calendar": hasattr(calendar_feed, "build"),
            "crm": hasattr(prospects, "store") and hasattr(prospects, "read")}


def now_done(topic: str) -> bool:
    """A topic of the plan's "no" list that names, with no narrowing in a parenthesis, something a function shows the product does now: it is not on the page."""
    if "(" in topic:
        return False
    low = topic.lower()
    return any(word in low and done for word, done in what_the_product_does_now().items())


CONNECTION_NAMES = {"filevine": "a second practice-management system", "google_drive": "Google Drive", "microsoft": "Microsoft 365 documents",
                    "google": "a Google account", "folder": None, "none": None, "local": None}
KINDS = (("documents", "documents from"), ("results", "results to"), ("sign_in", "staff sign-in with"))


def not_switched_on() -> list[str]:
    """schemas/registers/connectors.json: the connections that are built and tested and not switched on, in plain words. Refused when one is switched on: the page would be false."""
    cfg = _json(schema_path.path("register", "connectors"))
    if "NOT switched on" not in cfg["_status"]:
        raise PageError("schemas/registers/connectors.json no longer says its connectors are not switched on")
    out = []
    for key, label in KINDS:
        names = []
        for option in cfg["_options"][key]:
            if option not in CONNECTION_NAMES:
                raise PageError(f"a connector option {option!r} has no plain name for the public page")
            if CONNECTION_NAMES[option] is None:
                continue
            if cfg["active"][key] == option:
                raise PageError(f"the connector {option!r} is switched on in schemas/registers/connectors.json: the page says none is")
            name = "Microsoft 365" if key == "sign_in" and option == "microsoft" else CONNECTION_NAMES[option]
            name = "a Google account" if key == "sign_in" and option == "google" else name
            names.append(name)
        if names:
            out.append(f"{label} {join_words(list(dict.fromkeys(names)), 'or')}")
    return out


def render_what_it_does() -> str:
    fs = filings()
    by_id = {f["id"]: f for f in fs}
    paths = tracks()
    listed = [i for t in paths for i in t["filings"]]
    reg = register()
    lead, parts = local_only()
    lines = ["# What the product does, and what it does not", "", stamp(), "",
             "The filings, documents, checks and counts on this page are read from the product's own code and records when the page is made. "
             + signoff_sentence(), "",
             "## The filings the product prepares", "",
             f"The product prepares {noun(len(fs), 'kind')} of filing, each with its own packet.", "", "### By case path", ""]
    for t in paths:
        if t["filings"]:
            lines += [f"**{t['name']}**", ""] + [f"- {by_id[i]['title']}" for i in t["filings"]] + [""]
    other = [f["id"] for f in fs if f["id"] not in listed]
    lines += ["### Other filings", "",
              f"{noun(len(other), 'more filing')} {'is' if len(other) == 1 else 'are'} not in any case path's list of next filings:", ""] + [f"- {by_id[i]['title']}" for i in other] + [""]
    decl = declaration_filings()
    lines += ["### The client's declaration", "",
              f"For {noun(len(decl), 'filing')} the product also assembles the client's declaration: {'; '.join(by_id[i]['title'] for i in decl)}. "
              + " ".join(declaration_sentences()), ""]
    lines += ["## What each packet checks before it may be mailed", "",
              "Three of the messages a packet gives when it is not ready, in its own words, with example numbers:", ""]
    lines += [f"- \"{message}\"" for message in readiness_examples()] + [""]
    lines += ["| Filing | Forms in the packet | Held back until the folder has | Signed by |", "|---|---|---|---|"]
    for f in fs:
        lines.append(f"| {f['title']} | {', '.join(f['forms'])} | {'; '.join(f['required']) or 'No paper listed'} | {', '.join(f['signers']) or 'No signature listed'} |")
    kinds = document_types()
    lines += ["", "## What is read, and where", "",
              f"The product recognises {noun(len(kinds['read']), 'kind')} of document by itself: {'; '.join(kinds['read'])}.", "",
              f"It knows {noun(len(kinds['placed']), 'more kind')} by name, and a person places those: {'; '.join(kinds['placed'])}.", "",
              f"The client portal is in {join_words(portal_languages())}. " + " ".join(translation_sentences()), "", lead, "",
              "These parts run only on the firm's own machine:", ""]
    lines += [f"- {p}" for p in parts] + [""]
    lines += ["## What the upkeep register checks", "",
              f"The register lists everything that goes out of date: form editions, mailing addresses, fees and rules. It holds {noun(reg['total'], 'item')}. "
              f"Each has an owner, a source and a cadence: {noun(reg['cadence'].get('monthly', 0), 'item')} monthly, {noun(reg['cadence'].get('quarterly', 0), 'item')} "
              f"quarterly and {noun(reg['cadence'].get('yearly', 0), 'item')} yearly" + (f", and {reg['cadence']['not set']} more (the keys and secrets) on a cadence the attorney chooses, never guessed" if reg['cadence'].get('not set') else "")
              + f". The provider keeps {noun(reg['party'].get('provider', 0), 'item')} current, "
              f"the firm keeps {noun(reg['party'].get('firm', 0), 'item')} and whoever runs the server keeps {noun(reg['party'].get('host', 0), 'item')}.", "",
              f"Every night the product looks up {reg['live_total']:,} of them on the official page and says when one has changed. By kind:", ""]
    for kind, names in reg["live"].items():
        lines += [f"**{kind}** ({len(names)}): {'; '.join(names)}.", ""]
    lines += ["## What it does not do", ""] + [f"- {t}" for t in does_not_do()]
    lines += ["", "Connections to the firm's other systems are built and tested, and none is switched on:", ""] + [f"- {c}" for c in not_switched_on()] + [""]
    return "\n".join(lines)


# -- security ----------------------------------------------------------------------------------------------------------------------------

MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
STATEMENT = "docs/security/product_data_statement.md"
SUBPROCESSORS = "docs/security/subprocessors.md"
ABOUT_NAMES = {"Clio": "The firm's own practice-management account", "Filevine": "A second practice-management system",
               "Microsoft 365 (SharePoint and OneDrive, through Microsoft Graph)": "Microsoft 365 documents",
               "Sign-in with Google Workspace or Microsoft 365": "Staff sign-in with Google or Microsoft 365"}
# Sections of the statement the security page does not carry, and why. Every other section of the statement is on the page under its own heading.
STATEMENT_LEFT_OUT = {"Open decisions": "the provider's own list of what it still has to decide (its legal name, whether its attorney wants a line reworded): not a statement about the product",
                      "Sources": "the product's file names and a bar opinion: a page that names no file has nothing to say here"}
# A line of "What the product does" that says a thing is not so, with an exception the statement makes in another place: (item, that section, that paragraph's lead).
EXCEPTIONS = {5: ("The client's case: feedback and appointment reminders", "Appointment reminders, by channel.")}
_SENTENCE = re.compile(r"(?<=[.!?])\s+(?=[A-Z\"(`])")
_CODE_PAREN = re.compile(r"\s*\((?:[^()`]*`[^`]*`[^()`]*)+\)")


def statement_sections(text: str | None = None) -> dict[str, str]:
    """{heading: body} for every "##" section of the product data statement, in order."""
    text = text or _read(STATEMENT)
    parts = re.split(r"(?m)^## (.+)$", text)
    return {parts[i].strip(): parts[i + 1].strip("\n") for i in range(1, len(parts), 2)}


def statement_dates(text: str | None = None) -> dict[str, Any]:
    """The statement's own dates: {"date": MM/DD/YYYY of the statement, "rechecked": {item number: (MM/DD/YYYY, qualifier or None)}}. The paragraph says "Statement date:
    October 2, 2026 (item 3's second factor and the matching line under "What is not there yet": October 3, 2026; item 15, October 3, 2026)"."""
    text = text or _read(STATEMENT)
    para = re.search(r"Statement date:(.+?)\n\n", text, re.S)
    if not para:
        raise PageError("the product data statement has no date")
    body = " ".join(para.group(1).split())
    month = "|".join(MONTHS)
    first = re.match(rf"\s*({month}) (\d{{1,2}}), (\d{{4}})", body)
    if not first:
        raise PageError("the product data statement's date cannot be read")

    def us(m: tuple[str, str, str]) -> str:
        return f"{MONTHS.index(m[0]) + 1:02d}/{int(m[1]):02d}/{m[2]}"

    out: dict[str, Any] = {"date": us(first.groups()), "rechecked": {}}
    inner = re.search(r"\((.+)\)", body)
    for clause in (inner.group(1).split(";") if inner else []):
        item = re.search(r"item (\d+)", clause)
        when = re.search(rf"({month}) (\d{{1,2}}), (\d{{4}})", clause)
        if item and when:
            out["rechecked"][int(item.group(1))] = (us(when.groups()), "second factor" if "second factor" in clause else None)
    return out


def statement_date(text: str | None = None) -> str:
    return statement_dates(text)["date"]


def words_of_code(sentence: str) -> str | None:
    """A sentence of the statement for a public page: a parenthesis that holds only code goes, a file or a folder named in the sentence becomes "a file" or "a folder",
    a pointer to a numbered item or "below" goes. None when code is left that cannot be said in words."""
    s = _CODE_PAREN.sub("", sentence)
    if s.lstrip(" -*\"(").startswith("`"):  # a sentence about a file by its name has nothing left to say without the name
        return None
    s = re.sub(r"`[^`]*/`|`[^`]*/[^`]*[^/]`(?!\w)", lambda m: "a folder" if m.group(0).endswith("/`") else "a file", s)
    s = re.sub(r"`[^`]*\.(?:json|jsonl|db|md|pdf)`", "a file", s)
    if "`" in s:
        return None
    s = plain(s)
    s = re.sub(r"\s*\((?:item \d+|below|above)\)", "", s)
    return re.sub(r"\s+([,;.])", r"\1", s).strip()


def carry(raw: str) -> str:
    """The sentences of raw, each as words_of_code says it, one that cannot be said without a code name left out."""
    out = []
    for s in _SENTENCE.split(" ".join(raw.split())):
        said = words_of_code(s)
        if said:
            out.append(said)
    return " ".join(out)


def lead_spans(block: str) -> list[re.Match]:
    """The bold sentences in a block that head a line: they start with a capital and end with a full stop or a colon."""
    return [m for m in re.finditer(r"\*\*(.+?)\*\*", block, re.S) if plain(m.group(1))[:1].isupper() and plain(m.group(1))[-1:] in ".:"]


def paragraph_with_lead(text: str, lead: str) -> str:
    sections = statement_sections(text)
    for heading, body in sections.items():
        for para in body.split("\n\n"):
            m = re.match(r"\*\*(.+?)\*\*", para.strip())
            if m and plain(m.group(1)) == lead:
                return para
    raise PageError(f"the statement has no paragraph that starts {lead!r}")


def true_today(text: str | None = None) -> list[dict[str, Any]]:
    """The statement's lines about what the product does (its numbered items): [{"item", "span", "line"}]: each bold sentence that heads an item, with the sentences that
    follow it in the statement (code names said in words), the exception the statement makes elsewhere where it makes one, and the date an item was re-checked."""
    text = text or _read(STATEMENT)
    dates = statement_dates(text)
    out = []
    for block in re.split(r"(?m)^(?=\d+\. )", statement_sections(text)["What the product does"]):
        m = re.match(r"(\d+)\. ", block)
        if not m:
            continue
        item = int(m.group(1))
        leads = lead_spans(block)
        for k, span_match in enumerate(leads):
            span = plain(span_match.group(1))
            end = leads[k + 1].start() if k + 1 < len(leads) else len(block)
            after = carry(block[span_match.end():end].lstrip(". :"))
            line = f"{span[:-1]}: {after}" if span.endswith(":") and after else (span[:-1] + "." if span.endswith(":") else f"{span} {after}".strip())
            if item in EXCEPTIONS:
                heading, lead = EXCEPTIONS[item]
                paragraph = paragraph_with_lead(text, lead)
                again = [words_of_code(s) for s in _SENTENCE.split(" ".join(re.sub(r"^\*\*.+?\*\*", "", paragraph.strip()).split()))][:2]
                line += f" Exception, from \"{lead.rstrip('.')}\": " + " ".join(s for s in again if s)
            if item in dates["rechecked"] and (dates["rechecked"][item][1] is None or dates["rechecked"][item][1] in span.lower()):
                line += f" (checked again {dates['rechecked'][item][0]})"
            out.append({"item": item, "span": span, "line": line})
    return out


def other_sections(text: str | None = None) -> dict[str, list[dict[str, str]]]:
    """Every section of the statement that is not the numbered items, the "not there yet" list, the certificate paragraph or one left out: {heading: [{"lead", "line"}]},
    one line for each paragraph, with its bold lead where it has one."""
    text = text or _read(STATEMENT)
    out = {}
    for heading, body in statement_sections(text).items():
        if heading in STATEMENT_LEFT_OUT or heading in ("What the product does", "What is not there yet", "Why this is not a certification", "What the firm can ask for"):
            continue
        lines = []
        for para in body.split("\n\n"):
            para = para.strip()
            lead = re.match(r"\*\*(.+?)\*\*", para)
            rest = para[lead.end():] if lead else para
            said = carry(rest)
            if said:
                lines.append({"lead": plain(lead.group(1)) if lead else "", "line": f"{plain(lead.group(1))} {said}" if lead else said})
        out[heading] = lines
    return out


def backup_line(text: str | None = None) -> str:
    """The backup tool's line from the list of outside services (docs/security/subprocessors.md, the backup storage row): encrypted, with a test restore."""
    row = next((s for s in services(text) if s["name"].startswith("Backup")), None)
    if not row or "encrypted" not in row["receives"]:
        raise PageError("the list of outside services no longer says the backups are encrypted")
    return row["receives"].replace("(", "").replace(")", "")


def services(text: str | None = None) -> list[dict[str, str]]:
    """The outside services (docs/security/subprocessors.md "The list" and "Written, not connected"): [{"name", "off", "kind", "receives"}]. A competitor's name is
    replaced. A service that exists only in hosted mode, which is not live, says it is not used in the on-premises installation."""
    text = text or _read(SUBPROCESSORS)
    out = []
    for title, kind in (("The list", "used"), ("Written, not connected (send nothing today)", "written")):
        for row in table_rows(section(text, title)):
            raw = re.match(r"\*\*(.+?)\*\*", row[0])
            name = plain(raw.group(1) if raw else row[0])
            name = ABOUT_NAMES.get(name, name.split(":")[0].strip())
            if kind == "written":
                off = "Not connected: sends nothing"
            elif "hosted mode" in (row[0] + row[2]).lower():
                off = "Hosted mode only, which is not live: not used in an installation on the firm's own machine"
            else:
                off = first_sentence(row[3])
            out.append({"name": name, "off": off, "kind": kind, "receives": first_sentence(row[1]) if name.startswith("Backup") else ""})
    return out


def service_caveats(text: str | None = None) -> list[str]:
    """What the list of outside services says is not so, or not checked: the reader's address being a setting, what was not read or watched, and the message file
    that holds working links when no mail or text service is set up. (The mail server's certificate was a caveat until the product checked it: src/portal/notify.py
    checks the certificate and the name before any message is sent, so the list must say so and the page repeats no caveat about it.)"""
    text = text or _read(SUBPROCESSORS)
    cert = re.search(r"the mail server's certificate and name are checked", text)
    caveat = re.search(r"\*\*One caveat, exactly:\*\*(.+?)(?=\n\n|\n- \*\*)", text, re.S)
    unchecked = re.search(r"\*\*What was not checked\.\*\*(.+?)\n\n", text, re.S)
    message_file = section(text, "Warning: the local message file")
    if not (cert and caveat and unchecked and message_file):
        raise PageError("the list of outside services no longer carries the caveats the security page repeats")
    first = " ".join(unchecked.group(1).replace("**not**", "not").split())
    first = re.sub(r"^I read this product's own code\. ", "", first)
    first = first.replace("I did not read", "The provider did not read").replace("and I did not watch", "and did not watch")
    reader = "On the document reader: " + carry(re.sub(r"^\s*the address", "The address", caveat.group(1)))
    return [reader, carry(first), carry(message_file)]


def not_yet(readme: str | None = None, statement: str | None = None) -> list[dict[str, str]]:
    """What is not there yet. The statement's own list ("What is not there yet": every sentence of every bullet, code names said in words), then the rows of docs/security/
    README.md "What is still the owner's" that name a thing, and, for a row the pack says nothing about, that it says nothing. [{"line", "from"}]."""
    out = []
    for bullet in re.findall(r"(?ms)^- (.+?)(?=^- |\Z)", statement_sections(statement)["What is not there yet"]):
        said = carry(bullet)
        if said:
            out.append({"line": said, "from": "statement"})
    rows = table_rows(section(readme or _read("docs/security/README.md"), "What is still the owner's").split("###")[0])
    for row in rows:
        name = re.match(r"\*\*(.+?)\*\*", row[0])
        label = plain(name.group(1)) if name else ""
        if not label or "`" in name.group(1) or label.lower().startswith("the provider's"):
            continue
        label = label[:1].upper() + label[1:]
        if "stated nowhere in this pack" in " ".join(row):
            out.append({"line": f"{label}: the security pack says nothing about it.", "from": "silent"})
        else:
            out.append({"line": label, "from": "owner"})
    return out


def owner_heading(readme: str | None = None) -> str:
    """The security pack's own heading for the list of what is still the owner's."""
    m = re.search(r"(?m)^## (What is still the owner's)$", readme or _read("docs/security/README.md"))
    if not m:
        raise PageError("the security pack no longer has a list of what is still the owner's")
    return m.group(1)


def what_the_firm_can_ask_for(text: str | None = None) -> str:
    return carry(statement_sections(text)["What the firm can ask for"])


def not_a_certificate(text: str | None = None) -> str:
    last = sentences(statement_sections(text)["Why this is not a certification"])[-1]
    if "not an audit" not in last:
        raise PageError("the statement no longer says it is not an audit")
    return last


def render_security() -> str:
    text = _read(STATEMENT)
    sub = _read(SUBPROCESSORS)
    lead = local_only()[0]
    sv = services(sub)
    dates = statement_dates(text)
    again = {}
    for item, (when, _q) in dates["rechecked"].items():
        again.setdefault(when, []).append(item)
    rechecked = "; ".join(f"item{'s' if len(items) > 1 else ''} {join_words([str(i) for i in sorted(items)])} checked again {when}" for when, items in again.items())
    lines = ["# Security: what is true today", "", stamp(), "",
             f"This page is made from the product's own statement of how it keeps client data, dated {dates['date']}" + (f", with {rechecked}" if rechecked else "")
             + ". Every line is a line of that statement. The backup line and the table of services come from the product's list of outside services instead. "
             "The statement itself is on the data statement page.", "", "## What the product does", ""]
    lines += [f"- {t['line']}" for t in true_today(text)] + [""]
    for heading, paras in other_sections(text).items():
        lines += [f"## {heading}", ""] + [f"- {p['line']}" for p in paras] + [""]
    lines += ["## What leaves the installation, and to whom", "", lead, "", "From the product's list of outside services:", "",
              f"- {backup_line(sub)}", ""]
    lines += ["| Service | Can the firm switch it off? |", "|---|---|"]
    lines += [f"| {s['name']} | {s['off']} |" for s in sv if s["kind"] == "used"]
    lines += ["", "Written, not connected (send nothing today):", ""]
    lines += [f"- {s['name']}" for s in sv if s["kind"] == "written"] + [""]
    lines += ["What the list says is not so, or was not checked:", ""] + [f"- {c}" for c in service_caveats(sub)] + [""]
    lines += ["## What is not there yet", ""]
    missing = not_yet(None, text)
    lines += [f"- {n['line']}" for n in missing if n["from"] == "statement"]
    lines += ["", f"{owner_heading()}:", ""] + [f"- {n['line']}" for n in missing if n["from"] != "statement"]
    lines += ["", "This page gives no date for any of them. The provider will say when each is done.", "",
              "## What the firm can ask for", "", what_the_firm_can_ask_for(text), "",
              "## Why this is not a certification", "", not_a_certificate(text), ""]
    return "\n".join(lines)


# -- the data statement, price, README -------------------------------------------------------------------------------------------------


def render_data_statement(provider: dict[str, str] | None = None) -> str:
    """The statement as tools/export_firm.py --pack writes it into the pack, with the provider's line filled in (the placeholders where there is none)."""
    import export_firm

    filled = export_firm.fill_statement(_read(STATEMENT), provider or PROVIDER_PLACEHOLDERS)
    head, _, rest = filled.partition("\n")
    return f"{head}\n\n{stamp()}\n{rest}"


def commercial_terms(source: Path | None = None) -> dict:
    """Explicit source preserves owner terms; draft proposals never become offers."""
    path = source or REPO / "docs" / "commercial_terms.json"
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"), parse_constant=lambda _v: (_ for _ in ()).throw(ValueError("nonfinite number")))
    except (OSError, ValueError) as exc:
        raise PageError("commercial source cannot be read as finite JSON") from exc
    if not isinstance(data, dict) or data.get("version") != 1 or data.get("status") not in ("draft", "owner_approved") or type(data.get("approved")) is not bool:
        raise PageError("versioned commercial source and explicit approval state required")
    if data["status"] == "draft":
        if data["approved"]:
            raise PageError("draft commercial source cannot claim approval")
        return data
    if not data["approved"] or any(not isinstance(data.get(k), str) or not data[k].strip() for k in ("approved_by", "approval_record")):
        raise PageError("public terms require actual named owner approval and its record")
    terms = data.get("terms")
    texts = ("provider_legal_name", "scope", "installation", "training", "support_contact", "support_hours", "first_response", "hardware", "license", "payment", "exit")
    if not isinstance(terms, dict) or any(not isinstance(terms.get(k), str) or not terms[k].strip() or len(terms[k]) > 4000 or terms[k].strip().lower() in {"pending", "tbd", "unconfirmed", "not decided"} or "[decided by" in terms[k].lower() for k in texts):
        raise PageError("public terms require complete provider, scope, support, hardware, agreement and exit inputs")
    if any(type(terms.get(k)) is not int or terms[k] < 0 for k in ("pilot_fee_usd", "first_year_total_usd", "renewal_monthly_usd")) or type(terms.get("named_users")) is not int or terms["named_users"] < 1 or type(terms.get("pilot_credited")) is not bool:
        raise PageError("public amounts/users/credit must be explicit typed owner inputs")
    if terms["pilot_credited"] and terms["pilot_fee_usd"] > terms["first_year_total_usd"]:
        raise PageError("credited pilot exceeds first-year total")
    return data


def commercial_words(value: str) -> str:
    """Render owner fields as text, never active HTML/Markdown."""
    value = html.escape(value, quote=False).replace("\r", " ").replace("\n", " ")
    return re.sub(r"([\\`*_{}\[\]()#+.!|>~-])", r"\\\1", value)


def render_price(source: Path | None = None) -> str:
    data = commercial_terms(source)
    if not data["approved"]:
        return PRICE_SENTENCE + "\n"
    t = data["terms"]
    lines = ["# Price and included service", "", "Provider: " + commercial_words(t["provider_legal_name"]), "",
             f"Pilot: ${t['pilot_fee_usd']:,} USD. First-year total: ${t['first_year_total_usd']:,} USD.",
             "The pilot payment is credited toward the first-year total if the firm chooses expansion." if t["pilot_credited"] else "The pilot payment is separate from the first-year total.",
             f"Renewal: ${t['renewal_monthly_usd']:,} USD per month. Named staff scope: {t['named_users']:,}.", ""]
    for label, key in (("Work included", "scope"), ("Installation", "installation"), ("Training", "training"), ("Support contact", "support_contact"), ("Support hours", "support_hours"), ("First response", "first_response"), ("Hardware and other costs", "hardware"), ("Agreement", "license"), ("Payment and expansion", "payment"), ("Exit", "exit")):
        lines.extend([label + ": " + commercial_words(t[key]), ""])
    return "\n".join(lines)


PAGES = [("what_it_does.md", "What the product does, and what it does not", "this tool, from the filings, the case paths, the register and the design plan"),
         ("security.md", "Security: what is true today", "this tool, from the product data statement, the list of outside services and the owner's list"),
         ("accuracy.md", "How accurate the filling is, and how we show you", "the accuracy tool (python tools/accuracy_report.py); this tool checks its stamp"),
         ("data_statement.md", "How the product keeps client data (the statement)", "this tool, as the security pack's export writes it"),
         ("price.md", "Price", "this tool reads an explicit owner commercial source; draft terms remain the placeholder"),
         (f"{SAMPLE_DIR}/README.md", "The sample packet and review bundle", "this tool, which builds a made-up case and reads the two PDFs it made")]


def render_index() -> str:
    lines = ["# The public pages", "", stamp(), "",
             "**This is the owner's index of the pages below. It is not for publishing:** it names files and commands.", "",
             "These are the pages a firm that is deciding whether to buy can read. Nothing serves them: they are files the owner publishes however they choose. "
             "Every page carries the software version and the release date above, except the price page. Draft commercial terms leave its placeholder; approved owner terms use their explicit source.", "",
             "Commercial terms live in docs/commercial_terms.json, separately from installation settings. Keep actual owner terms in that source or pass --commercial-source <file> every time. The shipped draft never publishes its provisional prices. Existing custom price text is preserved until an explicit source is supplied.", "",
             "A release changes the version, so every page is made again at each release: run `python tools/public_pages.py`, read what changed (`git diff docs/public`), "
             "then publish. `python tools/public_pages.py --check` says which page is out of date, and a test fails while one is. To publish with the provider's name "
             "and security contact filled in, run it with `--out <a folder of your own>` on the installation that holds them.", "",
             "| Page | What it is | Made by |", "|---|---|---|"]
    lines += [f"| {name} | {what} | {by} |" for name, what, by in PAGES]
    lines += ["", f"The sample folder also holds two PDF files, the sample packet and the sample review bundle: {SAMPLE_PACKET}, {SAMPLE_BUNDLE}. They say the day they were built. "
              "Build them again (run the generator in full) when a filing or the demonstration case changes; the check compares what the sample's page says with what the "
              "two files say, and a test builds the sample again and compares.", ""]
    return "\n".join(lines)


# -- the sample: a made-up case, built by the product's own packet and review bundle code -----------------------------------------------------

SAMPLE_FIRM = {"office.name": "Springfield, MA", "office.states": "MA", "firm.preparer_given_name": "Marcela", "firm.preparer_family_name": "Exemplo",
               "firm.attorney_bar_number": "000000", "firm.licensing_authority": "A made-up bar", "firm.eoir_id": "000000000",
               "firm.uscis_online_account_number": "000000000000", "firm.business_name": "Exemplo Immigration Law LLP", "firm.street": "1 Example Street",
               "firm.city": "Springfield", "firm.state": "MA", "firm.zip": "01103", "firm.phone": "5550100", "office.fax": "555-0101",
               "firm.email": "office@example.com", "office.signer": "Marcela Exemplo, Esq.", "office.website": "www.example.com",
               "office.tagline": "A made-up firm for a sample", "office.attorneys": "Marcela Exemplo, Esq.",
               "office.g28_mail": "on"}  # Implementation note.
SAMPLE_ATTORNEY = "Marcela Exemplo (the sample's attorney)"
SAMPLE_BUILDER = "The sample builder"
SAMPLE_ENV = {"I485_SETTINGS": "settings.json", "I485_MAINTENANCE_LOG": "maintenance_log.json", "I485_DEPLOYMENT": "deployment.json",
              "I485_LIVE_STATUS": "maintenance_status.json", "I485_RULES_APPROVED": "rules_approved.json", "I485_POLICIES_FIRM": "policies_firm.json",
              "I485_INDEX": "index.db", "I485_EVENTS": "events.jsonl", "I485_QUERY_DB": "query.db", "I485_BACKUP_LOG": "backup_log.json",
              "I485_INBOX": "inbox", "I485_CASES": "clients", "I485_ACCURACY_HISTORY": "accuracy_history.jsonl", "I485_REFERENCE": "reference"}


def build_sample(out: Path) -> None:
    """Builds the sample packet and review bundle into out, in a fresh process whose every data folder is a scratch one (nothing of this machine's is read or
    written), and with the clock set to the release date so the dates on the pages are the release's. The two files are built in the scratch folder and copied to out
    only when the build and its checks succeed: a refusal leaves what is in out as it was."""
    out = Path(out)
    with tempfile.TemporaryDirectory(prefix="i485-sample-") as scratch:
        built = Path(scratch) / "built"
        built.mkdir()
        env = {**os.environ, **{k: str(Path(scratch) / v) for k, v in SAMPLE_ENV.items()}, "I485_SHADOW": "0", "I485_LIVE_CHECKS": "0", "I485_ACCURACY": "0",
               "I485_QUERY_REFRESH": "0", "PYTHONPATH": str(REPO / "src")}
        (Path(scratch) / "clients").mkdir()
        done = subprocess.run([sys.executable, str(Path(__file__).resolve()), "--sample-worker", str(built), "--scratch", scratch], env=env,
                              capture_output=True, text=True, cwd=str(REPO))
        if done.returncode:
            raise PageError("the sample could not be built:\n" + (done.stderr or done.stdout)[-2000:])
        out.mkdir(parents=True, exist_ok=True)
        for name in (SAMPLE_PACKET, SAMPLE_BUNDLE):
            shutil.copyfile(built / name, out / name)


def _watermark_pdf(source: Path, target: Path, title: str, built_on: str, reasons: list[str] | None = None) -> None:
    """The product's PDF with SAMPLE across every page and a line at the top, under what is already there. The Info record says the day it was built and, for the
    packet, what was open when the product marked it DRAFT."""
    from pypdf import PdfReader, PdfWriter

    from fill.continuation import HEIGHT, WIDTH, _Page

    writer = PdfWriter(clone_from=PdfReader(str(source)))
    for page in writer.pages:
        overlay = _Page()
        overlay.ops.append("q 0.86 g BT /F2 120 Tf 0.766 0.643 -0.643 0.766 70 150 Tm (SAMPLE) Tj ET Q")
        overlay.ops.append(f"BT /F2 9 Tf 0.3 g {WIDTH / 2 - 150:.1f} {HEIGHT - 16:.1f} Td (SAMPLE: a made-up client, not a real filing) Tj ET")
        page.merge_page(overlay.to_page(writer), over=False)  # under what is on the page: the mark shows through the white, the filled boxes stay readable
    writer.add_metadata({"/Title": title, "/Subject": f"SAMPLE. Made-up client. Built on {built_on}.", "/Author": "A made-up firm",
                         "/Keywords": json.dumps(reasons or [], ensure_ascii=False)})
    with open(target, "wb") as f:
        writer.write(f)


def _real_identity() -> list[str]:
    """What the shipped firm profile and letter say about the real firm: none of it may stand in the sample."""
    facts = _json(schema_path.path("firm", "firm_profile"))["facts"]
    letter = _json(schema_path.path("cover_letter", "i485"))
    raw = [str(v) for v in facts.values()] + [letter["letterhead"]["address"], letter["signer"]["name"], *letter["signer"]["lines"]]
    raw += [p.replace("*", "").strip() for p in letter["letterhead"]["attorneys"].split(",") if "Esq" not in p]
    raw += [re.sub(r",?\s*Esq\.?\*?", "", p).strip() for p in letter["letterhead"]["attorneys"].split(", ")]
    return sorted({r.lower() for r in raw if len(r) >= 5 and r.lower() not in ("yes", "no")} - {"chelsea"})


def sample_worker(out: Path, scratch: Path) -> None:
    """Runs in the fresh process build_sample starts."""
    import json as _json_mod
    from datetime import datetime

    import clock
    import packet
    import settings
    import version
    from fill import load_field_map
    from fill.continuation import _Page  # noqa: F401 -- imported here so a broken drawing module fails before anything is built
    from portal import demo
    from portal.store import PortalStore
    from review import bundle
    from review.overview import review_row
    from review.state import Catalog, refill
    from rules import approval

    day = datetime.fromisoformat(version.RELEASED)
    clock._now_override = datetime(day.year, day.month, day.day, 12, 0)
    settings.save("firm", SAMPLE_FIRM, SAMPLE_BUILDER)
    scratch = Path(scratch)
    demo.seed(PortalStore(scratch / "portal"), scratch / "clients")
    case = scratch / "clients" / demo.DEMO_ID
    template = schema_path.path("template", "i485")
    field_map = load_field_map(schema_path.path("field_map", "i485"))
    policies = _json_mod.loads((schema_path.path("law", "policy_sijs")).read_text(encoding="utf-8"))["policies"]
    catalog = Catalog(field_map, template, policies)
    refill(case, field_map, template)
    import g28

    approval.approve(g28.PRACTICE_ID, SAMPLE_ATTORNEY, "attorney")  # Implementation note.
    g28.confirm(case, SAMPLE_BUILDER, "paralegal")  # the G-28's card, looked at as a paralegal does before a packet (src/g28.py); the bundle then says who
    schema = packet.load_filing("i485")
    packet.build(case, review_row(case, field_map, template, catalog), SAMPLE_BUILDER, schema)
    for rule in bundle.rows(case, "i485")["rules"]:  # the generator enters the sample attorney's approval of every rule the packet used, on the release date
        approval.approve(rule["id"], SAMPLE_ATTORNEY, "attorney")
    bundle.build(case, "i485", SAMPLE_BUILDER)
    manifest = _json_mod.loads((case / schema.get("manifest", "packet.json")).read_text(encoding="utf-8"))
    reasons = [tidy(p) for p in manifest.get("problems") or []] if manifest.get("draft") else []
    built_on = f"{day.month:02d}/{day.day:02d}/{day.year}"
    _watermark_pdf(case / packet.PACKET_PDF, out / SAMPLE_PACKET, "Sample packet (a made-up client)", built_on, reasons)
    _watermark_pdf(bundle.paths(case, "i485")[0], out / SAMPLE_BUNDLE, "Sample review bundle (a made-up client)", built_on)
    from pypdf import PdfReader

    seen = "\n".join((p.extract_text() or "") for name in (SAMPLE_PACKET, SAMPLE_BUNDLE) for p in PdfReader(str(out / name)).pages).lower()
    leaked = [t for t in _real_identity() if t in seen]
    if leaked:
        raise RuntimeError("the sample carries the shipped firm's own details: " + ", ".join(leaked))


def pdf_text(path: Path) -> list[str]:
    from pypdf import PdfReader

    return [" ".join((p.extract_text() or "").split()) for p in PdfReader(str(path)).pages]


def sample_facts(folder: Path) -> dict[str, Any]:
    """What the two sample PDFs say about themselves (read from the files): the version, the pages, the forms, the answers, the sources, the rules and their approvals."""
    from pypdf import PdfReader

    facts: dict[str, Any] = {}
    for key, name in (("packet", SAMPLE_PACKET), ("bundle", SAMPLE_BUNDLE)):
        path = Path(folder) / name
        if not path.exists():
            raise PageError(f"the sample file {name} is not in {folder}: run python tools/public_pages.py")
        reader = PdfReader(str(path))
        meta = reader.metadata or {}
        built = re.search(r"Built on (\d{2}/\d{2}/\d{4})\.", str(meta.get("/Subject") or ""))
        try:
            reasons = json.loads(str(meta.get("/Keywords") or "[]"))
        except ValueError:
            reasons = []
        facts[key] = {"pages": len(reader.pages), "built": built.group(1) if built else None, "reasons": reasons, "text": pdf_text(path)}
    text = " ".join(facts["bundle"]["text"])
    header = re.search(r"Review bundle: (.+?), (A-\d+) · (.+?) Review bundle:", text) or re.search(r"Review bundle: (.+?), (A-\d+) · ([^R]+)", text)
    forms = re.search(r"Forms: (.+?) This bundle", text)
    built = re.search(r"Packet built (\d{2}/\d{2}/\d{4}) by .+?, (\d+) pages(, marked DRAFT)?", text)
    answers = re.search(r"(\d+) answers filled on the forms", text)
    kinds = re.search(r"private numbers show their last four digits only\. (.+?)\. Built only", text)
    facts["packet"]["exhibits"] = [(m.group(1), m.group(2).strip()) for t in facts["packet"]["text"]
                                   if (m := re.search(r"A-Number: A-\d+ Exhibit ([A-Z]) (.+?)(?: SAMPLE|$)", t))]
    facts["bundle"] |= {
        "client": header.group(1) if header else None, "a_number": header.group(2) if header else None, "title": plain(header.group(3)) if header else None,
        "forms": [f.strip() for f in forms.group(1).split(",")] if forms else [], "answers": int(answers.group(1)) if answers else None,
        "kinds": kinds.group(1) if kinds else None, "approved": len(re.findall(r"Approved by .+? on \d{2}/\d{2}/\d{4} for volume use", text)),
        "unapproved": text.count("Not yet approved"), "approval_dates": sorted(set(re.findall(r"Approved by .+? on (\d{2}/\d{2}/\d{4}) for volume use", text))),
        "packet_pages": int(built.group(2)) if built else None, "packet_draft": bool(built and built.group(3)), "built_on": built.group(1) if built else None}
    return facts


def render_sample_readme(folder: Path | None = None) -> str:
    f = sample_facts(folder or PUBLIC / SAMPLE_DIR)
    p, b = f["packet"], f["bundle"]
    for missing, what in ((b["client"], "the client"), (b["answers"], "the number of answers"), (b["forms"], "the forms")):
        if not missing:
            raise PageError(f"the sample review bundle no longer says {what} in the words this page reads")
    lines = ["# The sample packet and the sample review bundle", "", stamp(), "",
             f"These two PDF files were built by the product from one made-up case, {b['client']} ({b['a_number']}), a client who does not exist. "
             "Every name, number and address in them is invented, except USCIS's own addresses and the forms' printed text. Every page carries the word SAMPLE across it. "
             f"The firm and the attorney on them are made up as well. They were built on {p['built']}.", "",
             "## The packet", "",
             f"The packet is what the product builds to be printed and mailed, here the {b['title']}: {noun(p['pages'], 'page')}. It holds the forms "
             f"{join_words(b['forms'])} and {noun(len(p['exhibits']), 'lettered exhibit')} of the client's documents, each behind a separator page: "
             f"{'; '.join(f'{letter}, {title}' for letter, title in p['exhibits'])}."
             + (" It is marked DRAFT: the product marks a packet DRAFT while any check that holds it back is open." if b["packet_draft"] else ""), ""]
    if b["packet_draft"] and p["reasons"]:
        lines += ["These checks were open when the product built it:", ""] + [f"- {r}" for r in p["reasons"]] + [""]
    lines += ["## The review bundle", "",
              f"The review bundle sits behind the packet and is never mailed: {noun(b['pages'], 'page')}. It lists {noun(b['answers'], 'answer')} filled on the forms, "
              "each with where it came from: the part of the scan the answer was read from, the client's own answer in the portal, a rule, a reviewer's decision, "
              f"the firm's policy or the firm's own details. Count by kind: {b['kinds']}.", ""]
    if b["approved"] or b["unapproved"]:
        dated = f", dated {join_words(b['approval_dates'])}" if b["approval_dates"] else ""
        lines += [f"At the end it lists every rule and policy the packet used, each with the attorney's approval for use on every case. In this sample "
                  f"{noun(b['approved'], 'rule')} {'is' if b['approved'] == 1 else 'are'} approved by the sample's attorney{dated}, entered by the sample builder, "
                  "not by a person's review"
                  + (f" and {b['unapproved']} {'is' if b['unapproved'] == 1 else 'are'} marked \"Not yet approved\"" if b["unapproved"] else "") + ".", ""]
    if b["packet_pages"] != p["pages"]:
        raise PageError("the review bundle names a packet of a different length than the sample packet")
    lines += ["## What to look for", "",
              "- In the review bundle, each row is one answer on a form. Pick any row and read where it came from.",
              f"- The bundle names the packet it sits behind: the one built on {b['built_on']}, {noun(b['packet_pages'], 'page')}, the length of the sample packet.", ""]
    return "\n".join(lines)


# -- the whole set ------------------------------------------------------------------------------------------------------------------


def accuracy_problem(page: Path | None = None) -> str | None:
    """The accuracy page is made by tools/accuracy_report.py. None when it says the software version this tool is run on, else a line saying what to run."""
    import version

    path = page or PUBLIC / "accuracy.md"
    if not path.exists():
        return "docs/public/accuracy.md is not there: run python tools/accuracy_report.py"
    m = re.search(r"software version (\d+\.\d+\.\d+)\.", path.read_text(encoding="utf-8"))
    if not m or m.group(1) != version.VERSION:
        return f"docs/public/accuracy.md was written for version {m.group(1) if m else 'unknown'}, the software is {version.VERSION}: run python tools/accuracy_report.py"
    return None


def refuse(name: str, text: str) -> None:
    """A page that carries a competitor's name, a word the pages never use or a dash is not written."""
    low = text.lower()
    for word in COMPETITORS:
        if re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low):
            raise PageError(f"{name} names {word!r}: a public page names no competitor")
    for word in FORBIDDEN:
        if re.search(rf"(?<![a-z-]){re.escape(word)}(?![a-z-])", low):
            raise PageError(f"{name} says {word!r}")
    for mark in FORBIDDEN_MARKS:
        if mark in text:
            raise PageError(f"{name} holds {mark!r}")


def render_all(provider: dict[str, str] | None = None, sample_folder: Path | None = None, commercial_source: Path | None = None) -> dict[str, str]:
    """{path inside docs/public: text} for every page this tool writes (the accuracy page and the two PDFs are not among them)."""
    pages = {"README.md": render_index(), "what_it_does.md": render_what_it_does(), "security.md": render_security(),
             "data_statement.md": render_data_statement(provider), "price.md": render_price(commercial_source),
             f"{SAMPLE_DIR}/README.md": render_sample_readme(sample_folder)}
    for name, text in pages.items():
        refuse(name, text)
    return pages


def provider_from_deployment() -> dict[str, str] | None:
    import export_firm

    p = export_firm.provider_details()
    return p if p["name"] and p["email"] else None


def check(folder: Path = PUBLIC, commercial_source: Path | None = None, include_accuracy: bool = True) -> list[str]:
    """What is out of date in folder: one line for each page that differs from what the code says now, and the accuracy page if it is for another release. The sample
    PDFs are not compared byte for byte or by version: they say the day they were built, and the sample's README is made from what they say."""
    problems = []
    for name, want in render_all(sample_folder=Path(folder) / SAMPLE_DIR, commercial_source=commercial_source).items():
        path = Path(folder) / name
        have = path.read_text(encoding="utf-8").replace("\r\n", "\n") if path.exists() else None
        if have != want:
            problems.append(f"docs/public/{name} is {'missing' if have is None else 'out of date'}")
    if include_accuracy:
        problems += [p for p in (accuracy_problem(Path(folder) / "accuracy.md"),) if p]
    return problems


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--check", action="store_true", help="fail when a page in docs/public is not what the code says now")
    ap.add_argument("--no-sample", action="store_true", help="keep the sample PDFs already in the folder (the sample's README is still made)")
    ap.add_argument("--out", type=Path, help="write to this folder instead of docs/public")
    ap.add_argument("--commercial-source", type=Path, help="explicit owner commercial JSON source; shipped draft publishes no prices")
    ap.add_argument("--sample-worker", type=Path, help=argparse.SUPPRESS)
    ap.add_argument("--scratch", type=Path, help=argparse.SUPPRESS)
    args = ap.parse_args(argv)
    if args.sample_worker:
        sample_worker(args.sample_worker, args.scratch)
        return 0
    if args.check:
        problems = check(args.out or PUBLIC, commercial_source=args.commercial_source, include_accuracy=args.out is None)
        for line in problems:
            print(line)
        print("The public pages are up to date." if not problems else "Run: python tools/public_pages.py")
        return 1 if problems else 0
    out = args.out or PUBLIC
    commercial_terms(args.commercial_source)  # validate before any artifact write
    price = out / "price.md"
    if not args.commercial_source and price.exists():
        prior = price.read_text(encoding="utf-8").replace("\r\n", "\n")
        if prior not in (PRICE_SENTENCE + "\n", render_price()):
            raise PageError("existing owner price preserved; put terms in an explicit commercial source and pass --commercial-source")
    sample = out / SAMPLE_DIR
    if not args.no_sample:
        build_sample(sample)
        print(f"Built the sample in {sample}")
    elif out != PUBLIC:
        sample.mkdir(parents=True, exist_ok=True)
        for name in (SAMPLE_PACKET, SAMPLE_BUNDLE):
            (sample / name).write_bytes((PUBLIC / SAMPLE_DIR / name).read_bytes())
    for name, text in render_all(provider_from_deployment() if args.out else None, sample, args.commercial_source).items():
        path = out / name
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8", newline="\n")
        print(f"Wrote {path}")
    late = accuracy_problem(out / "accuracy.md") if out == PUBLIC else None
    if late:
        print(late)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
