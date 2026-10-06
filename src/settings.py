"""The firm's settings: changed on the review app's Settings page, never by
editing files. schemas/ holds the shipped defaults; data/settings.json holds
what the firm set -- each section's values, who set them and when, and the
earlier values -- laid over the defaults by every loader that reads them:

  visa_bulletin_eb4     the month's EB-4 cut-offs (SIJ I-485s)       fill/cover_letter.load_config
  visa_bulletin_family  the month's F1-F4 cut-offs                    preference.settings
  firm                  the main office: the attorney's details on     fill/companion.load_profile, batch.load_firm_profile
                        every form, the letterhead and the signer      (and src/offices.py for each case's office)
  office_2, office_3... the firm's other offices, the same fields      src/offices.py
  enotice               who gets the G-1145 receipt e-mail             enotice
  payment               whose card the G-1450 names                   payment.settings
  fees                  USCIS and EOIR fees (Form G-1055, EOIR)       fees.load
  poverty               the HHS poverty guidelines (Form I-864P)      family.settings
  sign_in               minutes without use before staff sign in again review/auth.session_idle
                        (and, from the Staff section, who must use a    review/auth.second_factor
                        code and whether a device may be remembered)
  drafting              grammar smoothing of a declaration (off),      drafting.smoothing_on
                        questions about a case (off)                   case_questions.is_on
                        Find across the firm (off)                     find.is_on
  translators          who may sign a certificate of translation      translation.translators (a list, not a set of values:
                        (name, languages, firm or outside)              each added with who and when, like the offices)
  closures              the days the courts are closed that the       closures.firm_added (the month view on What's due)
                        official pages did not list (one a line)
  firm/office.time_zone the firm's time zone: every today and stamp   clock.zone

Values use "/" for their place in the default ("eb4_cutoff/MEXICO",
"paper/i485"); the firm section's keys are the form facts ("firm.eoir_id").
"""

from __future__ import annotations

import json
import os
import re
from datetime import date
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
from portal.communication_consent import data_mutation
from law_app.adapters.persistence.filesystem.portal import _atomic

REPO = Path(__file__).resolve().parents[1]

PATH = Path(os.environ.get("I485_SETTINGS") or REPO / "data" / "settings.json")
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
CHARTS = ["Dates for Filing", "Final Action Dates"]
USCIS_CHARTS = "https://www.uscis.gov/green-card/green-card-processes-and-procedures/visa-availability-priority-dates/adjustment-of-status-filing-charts-from-the-visa-bulletin"
VISA_BULLETIN = "https://travel.state.gov/content/travel/en/legal/visa-law0/visa-bulletin.html"
IDLE_DEFAULT, IDLE_MIN, IDLE_MAX = 30, 5, 600  # the Sign-in section's minutes (review/auth.session_idle)


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}


def load() -> dict[str, Any]:
    return _json(PATH)


def values(section: str) -> dict[str, Any]:
    return (load().get(section) or {}).get("values") or {}


def mtime() -> float:
    return PATH.stat().st_mtime if PATH.exists() else 0.0


def firm_name() -> str:
    """The firm's name as the firm saved it (Main office, Firm name; the installer asks for it), else "". The shipped defaults are not
    a name: a screen, a tab title, the authenticator's label say "Case Review" alone until the firm has set its own."""
    try:
        return " ".join(str(values("firm").get("firm.business_name") or "").split())
    except (OSError, ValueError):
        return ""


def firm_mark(name: str) -> str:
    """The firm's initials for the round mark in the top bar: "Georges | Cote" -> "G|C", "Exemplo Law LLP" -> "EL", no name -> "CR"."""
    parts = [w.strip() for w in name.split("|") if w.strip()]
    initials = "|".join(w[0] for w in parts) if len(parts) > 1 else "".join(w[0] for w in name.split() if w[0].isalpha())[:2]
    return initials.upper() or "CR"


def overlay(section: str, default: dict[str, Any]) -> dict[str, Any]:
    """The default with the firm's values for this section laid over it ("a/b/c" puts default[a][b][c])."""
    out = json.loads(json.dumps(default))
    for key, value in values(section).items():
        if section == "firm":
            out[key] = value
            continue
        node, parts = out, key.split("/")
        for part in parts[:-1]:
            node = node.setdefault(part, {})
        node[parts[-1]] = value
    return out


# -- what the page shows ---------------------------------------------------------------------------------------------

def _get(node: Any, path: str) -> Any:
    for part in path.split("/"):
        node = node.get(part) if isinstance(node, dict) else None
    return node


def _months() -> list[str]:
    today = clock.today()  # the office's month, not the server's (src/clock.py)
    out = []
    for back in range(-1, 3):  # next month, this month, the two before
        m = today.month - back
        y = today.year + (m - 1) // 12
        out.append(f"{MONTHS[(m - 1) % 12]} {y}")
    return out


US_ZONES = (("America/New_York", "Eastern (New York, Boston, Miami)"), ("America/Chicago", "Central (Chicago, Houston)"),
            ("America/Denver", "Mountain (Denver)"), ("America/Phoenix", "Arizona (Phoenix, no daylight saving)"),
            ("America/Los_Angeles", "Pacific (Los Angeles)"), ("America/Anchorage", "Alaska (Anchorage)"),
            ("Pacific/Honolulu", "Hawaii (Honolulu)"), ("America/Puerto_Rico", "Puerto Rico"))


def _zone_options() -> list[list[str]]:
    """The time zones the Settings page offers: the U.S. ones first, then every other zone the machine knows."""
    first = [[z, label] for z, label in US_ZONES if clock.valid(z)]
    named = {z for z, _ in first}
    return first + [[z, z.replace("_", " ")] for z in sorted(clock.zones()) if z not in named and "/" in z and not z.startswith(("Etc/", "SystemV/"))]


OFFICE_FIELDS = (
    ("office.name", "Office name (e.g. Chelsea, MA)"), ("office.states", "States this office files for (e.g. MA, NH)"),
    ("firm.preparer_given_name", "Attorney: first name"), ("firm.preparer_family_name", "Attorney: last name"),
    ("firm.attorney_bar_number", "Bar number"), ("firm.licensing_authority", "Bar admission (e.g. Supreme Judicial Court of Massachusetts)"),
    ("firm.eoir_id", "EOIR ID (immigration court and BIA)"), ("firm.uscis_online_account_number", "Attorney's USCIS online account number"),
    ("firm.business_name", "Firm name"), ("firm.street", "Street"), ("firm.city", "City"), ("firm.state", "State"), ("firm.zip", "ZIP code"),
    ("firm.phone", "Phone"), ("office.fax", "Fax"), ("firm.email", "Email"),
    ("office.signer", "Who signs the cover letters (e.g. Ana B. Exemplo, Esq.)"), ("office.website", "Website on the letters"),
    ("office.tagline", "Line under the firm's name on the letters (e.g. Attorneys at law)"),
    ("office.attorneys", "Attorneys listed at the top of the letters (one line, separated by commas)"),
)

# What identifies the firm and its attorney on a form or a letter. The product ships a sample firm's values for these (schemas/firm/firm_profile.json,
# the packet's companion forms and the I-485 cover letter under schemas/), marked "sample". They are never a firm's own: until the firm saves its own
# Implementation note.
IDENTITY_KEYS = frozenset({"firm.business_name",
    "firm.preparer_family_name", "firm.preparer_given_name", "firm.attorney_bar_number", "firm.uscis_online_account_number", "firm.phone", "firm.email",
    "firm.street", "firm.city", "firm.state", "firm.zip", "firm.licensing_authority", "firm.eoir_id",
    "applicant.mailing_in_care_of", "applicant.mailing_street", "applicant.mailing_city", "applicant.mailing_state", "applicant.mailing_zip",
    "applicant.mailing_same_as_physical"})
# How long the office keeps a closed case's file (src/engagement.py retention): the firm's own setting, since no state rule was read on its official
# page that gives the period (engagement.RULES). Whole years; none until the attorney sets it.
RETENTION_NOTE = ("Whole years after a case is closed, declined, withdrawn or transferred. The product does not work this out from a rule: the attorney "
                  "sets the period the firm keeps. Cases past it are listed on Keeping current; nothing is deleted.")


def _retention_field() -> dict[str, Any]:
    return {"key": "office.retention_years", "label": "Keep a closed case's file for (years)", "type": "years", "default": None, "note": RETENTION_NOTE}


def _voice_field() -> dict[str, Any]:
    """The office's policy for how Part 14 entries are worded (src/part14_voice.py): the attorney approves it like a practice."""
    import part14_voice

    return {"key": part14_voice.KEY, "label": "Part 14 entries are written", "type": "choice", "default": None,
            "options": [["", "Not set: the client's voice until it is"]] + [[k, v] for k, v in part14_voice.VOICES.items()],
            "note": "Which voice the explanations in Part 14 use for every case this office files: the client's (\"Yes, I ...\") or the office's "
                    "(\"The applicant ...\"). The attorney approves the choice on Keeping current, like a practice, and a change needs approval again. "
                    "One voice for the whole packet."}


G28_GROUP = "The G-28: this office's choices (the attorney approves them below)"
DECIDES = " The attorney decides whether it is right for the firm."
APPROVAL_NOTE = (" Changing any of these switches in any office, or adding an office, means an attorney approves the G-28 choices again, for every office: "
                 "until then every case's G-28 carries the client's own address and a blank Part 4.")


def _unit_fields() -> list[dict[str, Any]]:
    """The office address's suite, floor or apartment line, when it has one: the G-28's Part 1, item 3.b and Part 3, item 13.b carry it; blank otherwise."""
    return [{"key": "firm.unit_type", "label": "Address: suite, floor or apartment (if it has one)", "type": "choice", "default": None,
             "options": [["", "None"], ["STE", "Suite"], ["FLR", "Floor"], ["APT", "Apartment"]]},
            {"key": "firm.apt", "label": "Address: its number (e.g. 200)", "type": "text", "default": None}]


def _g28_fields() -> list[dict[str, Any]]:
    """The office's choices for the G-28 (src/g28.py): the client's mailing address, and Part 4's items 1.a, 1.b and 1.c, each with the form's own words
    beside it. Each is a starting point for every case of the office: a paralegal confirms it on the case's G-28 card."""
    import g28

    r = g28.register()
    out = [{"key": "office.g28_mail", "label": "On the G-28, the client's mailing address (Part 3, item 13) is this office's address", "type": "choice", "default": "off",
            "group": G28_GROUP, "options": [["off", "Off: the client's own address"], ["on", "On: this office's address"]],
            "note": "The form's own note: \"" + r["mailing_note"] + "\"" + DECIDES + APPROVAL_NOTE}]
    for item in g28.ITEMS:
        p = r["part4"][item]
        out.append({"key": f"office.g28_{item}", "label": f"{p['item']}: {p['short'][:1].lower()}{p['short'][1:]}", "type": "choice", "default": "off", "group": G28_GROUP,  # "I-94", never "i-94"
                    "options": [["off", "Off: leave the box blank"], ["on", "On: mark the box"]],
                    "note": f"The form's own words: \"{p['words']}\"" + (f" {p['note']}" if p.get("note") else "") + DECIDES})
    return out


DETAILS = ("firm.business_name", "firm.preparer_family_name", "firm.attorney_bar_number", "firm.street", "firm.city", "firm.zip", "firm.phone")  # what the Main office must hold


def details_saved() -> bool:
    """The firm saved its own Main office: the attorney's name and bar number, the address and the phone."""
    saved = values("firm")
    return all(str(saved.get(k) or "").strip() for k in DETAILS)


def identity_withheld() -> bool:
    """Fictional example or implementation helper."""
    return not details_saved()


def shipped(defaults: dict[str, Any]) -> dict[str, Any]:
    """The shipped firm facts, without the sample firm's identity while it is withheld (identity_withheld)."""
    if not identity_withheld():
        return defaults
    return {k: v for k, v in defaults.items() if k not in IDENTITY_KEYS}


def offices_saved() -> list[str]:
    """The firm's other offices (section ids), in the order they were added; the main office is the "firm" section."""
    return list(load().get("_offices") or [])


@data_mutation(lambda: Path(PATH).parent)
def add_office(who: str) -> str:
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    data = load()
    ids = list(data.get("_offices") or [])
    n = 2
    while f"office_{n}" in ids:
        n += 1
    oid = f"office_{n}"
    data["_offices"] = ids + [oid]
    data[oid] = {"values": {}, "updated_by": who.strip(), "updated_at": clock.stamp(), "history": []}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic(PATH, data)
    events.record("settings", "added", "Added an office", home=PATH.parent, who=who.strip())
    return oid


@data_mutation(lambda: Path(PATH).parent)
def remove_office(oid: str, who: str) -> None:
    data = load()
    if oid not in (data.get("_offices") or []):
        raise LookupError(f"No office {oid!r}.")
    data["_offices"] = [o for o in data["_offices"] if o != oid]
    data.setdefault("_removed", []).append({"id": oid, "values": (data.pop(oid, {}) or {}).get("values"), "by": who,
                                            "at": clock.stamp()})
    _atomic(PATH, data)
    events.record("settings", "removed", "Removed an office", home=PATH.parent, who=who.strip())


# -- the translators (src/translation.py) --------------------------------------------------------------------------
# A certificate of translation names the translator (8 CFR 103.2(b)(3)): the attorney keeps the list here and picks one per
# document. Languages are the ones a document is translated from into English.
TRANSLATOR_LANGUAGES = {"pt": "Portuguese", "es": "Spanish", "fr": "French", "ht": "Haitian Creole"}
TRANSLATOR_KINDS = {"firm": "Works for the firm", "outside": "Outside translator"}


def translators() -> list[dict[str, Any]]:
    """The firm's translators, in the order they were added: {id, name, languages, kind, competence, organization, address,
    added_by, added_at}."""
    return [dict(t) for t in load().get("_translators") or []]


@data_mutation(lambda: Path(PATH).parent)
def add_translator(who: str, name: str, languages: list[str], kind: str, competence: str = "", organization: str = "", address: str = "") -> dict[str, Any]:
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    name = re.sub(r"\s+", " ", str(name or "")).strip()
    if not name:
        raise ValueError("Enter the translator's full name: it is printed on the certificate.")
    langs = [c for c in dict.fromkeys(languages or []) if c in TRANSLATOR_LANGUAGES]
    if not langs:
        raise ValueError("Choose at least one language the translator translates into English.")
    if kind not in TRANSLATOR_KINDS:
        raise ValueError("Say whether the translator works for the firm or is an outside translator.")
    data = load()
    rows = list(data.get("_translators") or [])
    n = 1
    while f"t{n}" in {t["id"] for t in rows} | {r["id"] for r in data.get("_removed") or []}:
        n += 1
    row = {"id": f"t{n}", "name": name, "languages": langs, "kind": kind, "competence": str(competence or "").strip()[:600],
           "organization": str(organization or "").strip()[:120], "address": str(address or "").strip()[:240],
           "added_by": who.strip(), "added_at": clock.stamp()}
    data["_translators"] = rows + [row]
    PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic(PATH, data)
    events.record("settings", "added", "Added a translator", home=PATH.parent, who=who.strip())
    return row


@data_mutation(lambda: Path(PATH).parent)
def remove_translator(tid: str, who: str) -> None:
    """Takes a translator off the list (a certificate already made keeps the name it was made with)."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    data = load()
    row = next((t for t in data.get("_translators") or [] if t["id"] == tid), None)
    if row is None:
        raise LookupError("No such translator.")
    data["_translators"] = [t for t in data["_translators"] if t["id"] != tid]
    data.setdefault("_removed", []).append({"id": tid, "translator": row, "by": who.strip(), "at": clock.stamp()})
    _atomic(PATH, data)
    events.record("settings", "removed", "Removed a translator", home=PATH.parent, who=who.strip())


def specs() -> list[dict[str, Any]]:
    """Every section and field the Settings page edits, with the current value (the firm's, else the default)."""
    letter = _json(schema_path.path("cover_letter", "i485"))
    family_vb = _json(schema_path.path("law", "visa_bulletin_family"))
    fees = _json(schema_path.path("law", "fees"))
    poverty = (_json(schema_path.path("law", "family_settings")).get("poverty_guidelines") or {})
    firm = shipped(_json(schema_path.path("packet", "companion_forms")).get("firm", {}) | _json(schema_path.path("firm", "firm_profile")).get("facts", {}))
    payment = _json(schema_path.path("firm", "payment"))
    signer = (letter.get("signer") or {})
    lines = signer.get("lines") or []
    sample = False  # Implementation note.
    main = firm | {"office.name": f"{str(firm.get('firm.city') or '').title()}, {firm.get('firm.state') or ''}".strip(", ") or None,
                   "office.states": firm.get("firm.state"), "office.signer": signer.get("name") if sample else None,
                   "office.fax": next((re.sub(r"^Fax-", "", x) for x in lines if x.startswith("Fax")), None) if sample else None,
                   "office.website": next((x for x in lines if x.upper().startswith("WWW.")), None) if sample else None,
                   "office.tagline": (letter.get("letterhead") or {}).get("tagline") if sample else None,
                   "office.attorneys": (letter.get("letterhead") or {}).get("attorneys") if sample else None}
    eb4 = letter.get("visa_bulletin") or {}

    def field(key: str, label: str, kind: str, default: Any, **extra: Any) -> dict[str, Any]:
        return {"key": key, "label": label, "type": kind, "default": default} | extra

    sections = [
        {"id": "visa_bulletin_eb4", "title": "Visa Bulletin: EB-4 (SIJ green cards)", "cadence": "Every month",
         "help": "Which chart USCIS accepts this month, and the EB-4 cut-off for each area ('Current' when the bulletin says C). SIJ green card "
                 "packets stay drafts until this matches the current month.",
         "links": [["USCIS: which chart this month", USCIS_CHARTS], ["Department of State: the Visa Bulletin", VISA_BULLETIN]],
         "fields": [field("month", "Month", "month", eb4.get("month"), options=_months()),
                    field("chart", "Chart USCIS accepts", "choice", eb4.get("chart"), options=CHARTS)]
         + [field(f"eb4_cutoff/{area}", area.title(), "cutoff", v) for area, v in (eb4.get("eb4_cutoff") or {}).items()]},
        {"id": "visa_bulletin_family", "title": "Visa Bulletin: family (F1 to F4)", "cadence": "Every month",
         "help": "The family-sponsored cut-offs, for each category and area. Cases waiting for a priority date compare against this; until it "
                 "matches the current month they say the bulletin isn't set.",
         "links": [["USCIS: which chart this month", USCIS_CHARTS], ["Department of State: the Visa Bulletin", VISA_BULLETIN]],
         "fields": [field("month", "Month", "month", family_vb.get("month"), options=_months()),
                    field("chart", "Chart USCIS accepts", "choice", family_vb.get("chart"), options=CHARTS)]
         + [field(f"cutoff/{cat}/{area}", area.title(), "cutoff", v, group=cat)
            for cat, areas in (family_vb.get("cutoff") or {}).items() for area, v in areas.items()]},
        {"id": "firm", "title": "Main office", "cadence": "When something changes",
         "help": "What every form says about the firm and the attorney who signs (the G-28, the EOIR-27 and EOIR-28, the preparer's part of "
                 "each form), and the letterhead and signer of the cover letters. A case uses the office for its client's state; another "
                 "office can be chosen on the case.",
         "links": [],
         "fields": [field(k, label, "text", main.get(k)) for k, label in OFFICE_FIELDS] + _unit_fields() + [_retention_field(), _voice_field()] + _g28_fields()
         + [field(clock.FIELD, "Time zone", "zone", clock.DEFAULT_ZONE, options=_zone_options(),
                  note="Every date, deadline and fee change follows this clock, wherever the server or a laptop is. All of the firm's offices share it.")]},
        {"id": "payment", "title": "Paying USCIS", "cadence": "When the firm decides",
         "help": "Every packet with a fee carries a pre-filled card authorization (Form G-1450) for each payment. The card number, expiry and "
                 "signature are always written by hand.",
         "links": [["USCIS: paying by card", "https://www.uscis.gov/pay-with-a-credit-card"]],
         "fields": [field("card_holder", "The card holder's name and address on the G-1450", "choice",
                          payment.get("card_holder"), options=[["", "Written by hand"], ["client", "The client's (their own card)"]])]},
        {"id": "enotice", "title": "Receipt e-mails (Form G-1145)", "cadence": "When the firm decides",
         "help": "Every package mailed to a USCIS lockbox can carry a Form G-1145: USCIS then e-mails the receipt number within 24 hours "
                 "of accepting it, before the mailed receipt notice. Choose who receives it.",
         "links": [["USCIS: Form G-1145", "https://www.uscis.gov/g-1145"]],
         "fields": [field("recipient", "Who receives the e-mail", "choice", "office",
                          options=[["office", "The case's office (its e-mail on the Settings page)"], ["client", "The client (their e-mail and mobile)"],
                                   ["none", "Nobody: no G-1145"]])]},
        {"id": "fees", "title": "Filing fees", "cadence": "With each new Form G-1055 (and EOIR's fee page)",
         "help": "The amounts every cover letter, card authorization and check uses. Copy them from the official fee schedule, never from memory.",
         "links": [["USCIS fee schedule (Form G-1055)", "https://www.uscis.gov/g-1055"],
                   ["EOIR: appeals, motions and fees", "https://www.justice.gov/eoir/types-appeals-motions-and-required-fees"],
                   ["EOIR: forms and application fees (cancellation of removal)", "https://www.justice.gov/eoir/eoir-forms"]],
         "fields": [field("edition", "G-1055 edition", "text", fees.get("edition")), field("checked", "Checked on", "date", fees.get("checked"))]
         + [field(f"{table}/{k}", f"{k.replace('_', ' ')}", "money", v, group={"paper": "USCIS (paper filing)", "pl_119_21": "Pub. L. 119-21 (separate payment)",
                                                                                "online": "USCIS (online filing, by PDF upload)", "eoir": "EOIR"}[table],
                  note=(fees.get("notes") or {}).get(k) if table != "online" else None)  # the paper notes don't describe the online amount
            for table in ("paper", "pl_119_21", "online", "eoir") for k, v in (fees.get(table) or {}).items()]},
        {"id": "poverty", "title": "Poverty guidelines (Form I-864P)", "cadence": "Every year (usually March)",
         "help": "The sponsor's minimum income for the I-864, and 150% for a fee waiver. Household sizes 2 to 8 and each additional person.",
         "links": [["USCIS: Form I-864P", "https://www.uscis.gov/i-864p"]],
         "fields": [field("effective", "Effective from", "date", poverty.get("effective"))]
         + [field(f"{region}/{col}/{n}", f"{n if n != 'each_additional' else 'each additional'}", "money", v,
                  group=f"{region.title()} · {'100%' if col == 'p100' else '125%'}")
            for region in ("contiguous", "alaska", "hawaii") for col in ("p100", "p125") for n, v in ((poverty.get(region) or {}).get(col) or {}).items()]},
        {"id": "translators", "title": "Translators", "cadence": "When a translator joins or leaves",
         "help": "Who may sign a certificate of translation. Every document in a foreign language that goes to USCIS needs a full English translation "
                 "and the translator's own certification that it is complete and accurate and that they are competent to translate it. "
                 "The attorney picks the translator for each document on the filing packet page; the translator's name and statement are printed on the certificate.",
         "links": [["8 CFR 103.2(b)(3): translations", "https://www.ecfr.gov/current/title-8/section-103.2"]],
         "fields": [], "translators": [t | {"language_names": [TRANSLATOR_LANGUAGES[c] for c in t["languages"] if c in TRANSLATOR_LANGUAGES],
                                            "kind_name": TRANSLATOR_KINDS.get(t["kind"], t["kind"])} for t in translators()],
         "translator_languages": [[c, n] for c, n in TRANSLATOR_LANGUAGES.items()], "translator_kinds": [[c, n] for c, n in TRANSLATOR_KINDS.items()]},
        {"id": "closures", "title": "Court closures the firm adds", "cadence": "Every year",
         "help": "The month view on What's due marks the federal holidays and the court closures the product could read from the official pages. "
                 "Add here a day it does not list: a court's own closure, or a state's days when its page could not be read. One day a line: "
                 "the date as MM/DD/YYYY, then what the day is (for example 12/24/2026 Courthouse closed).",
         "links": [["OPM: federal holidays", "https://www.opm.gov/policy-data-oversight/pay-leave/federal-holidays/"],
                   ["Massachusetts: trial court legal holidays", "https://www.mass.gov/info-details/trial-court-legal-holidays"],
                   ["Florida courts: court holidays", "https://6dca.flcourts.gov/Clerk-s-Office/Court-Holidays"]],
         "fields": [field("added", "Days the courts are closed, one a line", "lines", "")]},
        {"id": "drafting", "title": "Drafting and models", "cadence": "When the firm decides",
         "help": "What the model on this computer may do. Nothing here leaves the firm's machine, and each is off unless an attorney switches it on. "
                 "The client's declaration is assembled from the client's own answers, word for word. With grammar smoothing on, the model may "
                 "correct the grammar of one paragraph at a time; a change is used only if every word it adds is a small grammar word and nothing "
                 "of the client's is left out, and the card shows each paragraph before and after. With questions about a case on, staff who may "
                 "open a case can ask about it on the case page: answers are built from the case's own record, and every sentence shows where it "
                 "came from. With Find across the firm on, staff can ask a question in plain words on the Search page and get passages of the firm's own "
                 "records (pages, their English translations, review decisions, notes, approved wordings), each from a case they may open; names, "
                 "numbers, dates and addresses are hidden in the index, and who asked what is kept for the attorney. "
                 "Each practice below is the attorney's to approve; a change to its words needs approval again.",
         "links": [],
         "fields": [field("grammar_smoothing", "Grammar smoothing", "choice", "off",
                          options=[["off", "Off: the client's words exactly as answered"], ["on", "On: grammar only, shown before and after"]]),
                    # the question box and the summary for the attorney on the case page (src/case_questions.py)
                    field("case_questions", "Questions about a case answered by the local model", "choice", "off",
                          options=[["off", "Off: nobody can ask"],
                                   ["on", "On: answers built from the case's own record, every sentence with where it came from"]]),
                    # Find across the firm on the Search page (src/find.py): the index of meaning; the attorney switches it off in one click there too
                    field("find_across", "Find across the firm (questions in plain words over every case)", "choice", "off",
                          options=[["off", "Off: nobody can ask, and nothing new is indexed"],
                                   ["on", "On: passages from the firm's own records, each only to people who may open its case"]])]},
        {"id": "sign_in", "title": "Sign-in", "cadence": "When the firm decides",
         "help": f"How long the review app stays signed in on a computer nobody is using. Each page opened or button pressed starts the time "
                 f"again; after it runs out, the person signs in again. From {IDLE_MIN} to {IDLE_MAX} minutes; the system ships with {IDLE_DEFAULT}.",
         "links": [],
         "fields": [field("idle_minutes", "Sign out after this many minutes without use", "minutes", IDLE_DEFAULT),
                    # the code from an authenticator app (review/auth.py): set on the Staff section, not here (staff=True)
                    field("code_everyone", "Who must use a code from an authenticator app", "choice", "no", staff=True,
                          options=[["no", "Attorneys"], ["yes", "Everyone"]]),
                    field("remember_device", "Remember a device for 30 days after a code", "choice", "yes", staff=True,
                          options=[["yes", "Allowed"], ["no", "Never: a code at every sign-in"]])]},
    ]
    saved = load()
    for oid in saved.get("_offices") or []:
        values = (saved.get(oid) or {}).get("values") or {}
        sections.append({"id": oid, "title": "Office: " + (values.get("office.name") or "new office"), "cadence": "When something changes",
                         "help": "Another office of the firm: its attorney, address and signer. Cases for the states it files for use it.",
                         "links": [], "office": True,
                         "fields": [field(k, label, "text", main.get("firm.business_name") if k == "firm.business_name" else None)
                                    for k, label in OFFICE_FIELDS] + _unit_fields() + [_retention_field(), _voice_field()] + _g28_fields()})
    for s in sections:
        mine = saved.get(s["id"]) or {}
        for f in s["fields"]:
            f["value"] = (mine.get("values") or {}).get(f["key"], f["default"])
        s["updated_by"], s["updated_at"] = mine.get("updated_by"), mine.get("updated_at")
    return sections


def _check(f: dict[str, Any], raw: Any) -> Any:
    value = "" if raw is None else str(raw).strip()
    if value == "":
        return None
    kind = f["type"]
    if kind == "cutoff":
        if value.upper() in ("C", "CURRENT"):
            return "C"
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{f['label']}: a date (YYYY-MM-DD) or 'Current'.")
        date.fromisoformat(value)
        return value
    if kind == "date":
        if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError(f"{f['label']}: a date (YYYY-MM-DD).")
        date.fromisoformat(value)
        return value
    if kind == "money":
        digits = re.sub(r"[$,\s]", "", value)
        if not digits.isdigit():
            raise ValueError(f"{f['label']}: whole dollars, like 1440.")
        return int(digits)
    if kind == "years":
        if not value.isdigit() or not 1 <= int(value) <= 50:
            raise ValueError(f"{f['label']}: whole years, from 1 to 50.")
        return int(value)
    if kind == "minutes":
        if not value.isdigit() or not IDLE_MIN <= int(value) <= IDLE_MAX:
            raise ValueError(f"{f['label']}: whole minutes, from {IDLE_MIN} to {IDLE_MAX}.")
        return int(value)
    if kind == "lines":  # the firm's closed days (src/closures.py): one a line, kept as typed once every line is read
        import closures

        return "\n".join(f"{date.fromisoformat(d['date']).strftime('%m/%d/%Y')} {d['name']}" for d in closures.parse_lines(value)) or None
    if kind == "zone":
        if not clock.valid(value):
            raise ValueError(f"{f['label']}: choose a time zone from the list.")
        return value
    if kind == "month" and not re.fullmatch(r"(%s) \d{4}" % "|".join(MONTHS), value):
        raise ValueError(f"{f['label']}: a month like 'October 2026'.")
    if kind == "choice":
        allowed = [o[0] if isinstance(o, list) else o for o in f.get("options", [])]
        if value not in allowed:
            raise ValueError(f"{f['label']}: choose one of the options.")
    return value


@data_mutation(lambda: Path(PATH).parent)
def save(section: str, new: dict[str, Any], who: str) -> dict[str, Any]:
    """Checks every value, then records the section with who set it and when; the earlier values are kept."""
    if not str(who or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    spec = next((s for s in specs() if s["id"] == section), None)
    if spec is None:
        raise LookupError(f"No settings section {section!r}.")
    by_key = {f["key"]: f for f in spec["fields"]}
    unknown = [k for k in new if k not in by_key]
    if unknown:
        raise ValueError(f"Not a setting here: {', '.join(unknown)}.")
    checked = {k: _check(by_key[k], v) for k, v in new.items()}
    data = load()
    mine = data.get(section) or {}
    merged = {k: v for k, v in ((mine.get("values") or {}) | checked).items() if v is not None or by_key[k]["default"] is not None}
    history = (mine.get("history") or []) + ([{"values": mine.get("values"), "by": mine.get("updated_by"), "at": mine.get("updated_at")}]
                                               if mine.get("values") else [])
    data[section] = {"values": merged, "updated_by": who.strip(), "updated_at": clock.stamp(), "history": history[-20:]}
    PATH.parent.mkdir(parents=True, exist_ok=True)
    _atomic(PATH, data)
    events.record("settings", "changed", f"Changed {spec['title']}: " + ", ".join(str(by_key[k].get("label") or events.words(k)) for k in new), home=PATH.parent, who=who.strip())
    return next(s for s in specs() if s["id"] == section)
