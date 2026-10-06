"""The firm's own intake questionnaire (schemas/questions/intake.json): which
questions show, whether an answer is valid, how answers become facts, and
which documents the answers call for.

Answers become the SAME fact keys the scanned-questionnaire readers
produce (src/questionnaire), recorded as Tier 3 client statements -- so
assembly, rules, policies, cross-checks, completeness and the review app
work unchanged. The difference is that nothing has to be read: the client
types structured answers, checked as they type.
"""

from __future__ import annotations

import json
import re
import unicodedata
from functools import lru_cache
from pathlib import Path
from typing import Any

from extract.base import ExtractedField, cm_to_feet_inches, kg_to_lbs
import clock
import schema_path

BANK_PATH = schema_path.path("question", "intake")
HELP_PATH = schema_path.path("question_help", "portal")  # why we ask, tips, common questions (client-facing)
PORTAL_DOC_ID = "portal questionnaire"
UNSURE = "Unsure"
UNSURE_PREFIX = "questionnaire.unsure."  # + the fact key the client couldn't answer; value: the question asked


@lru_cache(maxsize=8)
def _read_json(path: str, mtime: float) -> dict[str, Any]:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def _load(path: str | Path) -> dict[str, Any]:
    """Cached, but re-read when the file changes -- edits to the questions
    or the help text show up without restarting the portal."""
    return _read_json(str(path), Path(path).stat().st_mtime)


FILING_BANKS = {"n400": "n400"}  # a filing's own questions, on top of the reused sections
FILING_BANKS["parole"] = "parole"  # humanitarian parole: the supporter's and the person's questions (src/parole.py)
FILING_BANKS["first_contact"] = "first_contact"  # a prospect's first questions, before there is a client (src/prospects.py)


def load_bank(path: str | Path = BANK_PATH, filing: str | None = None) -> dict[str, Any]:
    """The questions a client answers. filing (the client's profile "filing"):
    None / "i485" -- the green-card questionnaire; "n400" -- citizenship: the
    sections about the client that every filing shares (who they are,
    addresses, work, marriage, children, description), then the N-400's own
    (schemas/questions/n400.json)."""
    base = _load(path)
    if filing not in FILING_BANKS:
        return base
    extra = _load(schema_path.path("question", FILING_BANKS[filing], Path(path).parent.parent))
    sections = {s["id"]: s for s in base["sections"]}
    documents = {d["id"]: d for d in base["documents"]}
    return base | {"filing": filing, "sections": [sections[i] for i in extra["reuse_sections"] if i in sections] + extra["sections"],
                   "documents": extra["documents"] + [documents[i] for i in extra.get("reuse_documents", []) if i in documents]}


def bank_for(profile: dict[str, Any]) -> dict[str, Any]:
    return load_bank(filing=profile.get("filing"))


def load_help(path: str | Path = HELP_PATH) -> dict[str, Any]:
    return _load(path) if Path(path).exists() else {"sections": {}, "questions": {}, "faq": []}


def languages() -> tuple[str, ...]:
    """The languages a client can read the portal in, in the order the picker
    lists them (question_bank.json "languages"). Every client-facing text
    names each of them; one that doesn't is shown in English, never blank."""
    return tuple(load_bank()["languages"])


def language_names() -> dict[str, str]:
    """{code: the staff's word for it}: "ht" -> "Haitian Creole" (question_bank.json "language_names")."""
    return dict(load_bank().get("language_names") or {})


# How a language may be written in the firm's client list (the CSV "language" column): the code, or its name.
LANGUAGE_WORDS = {"pt": ("portuguese", "portugues", "por", "brazilian"), "es": ("spanish", "espanol", "castellano", "spa"),
                  "en": ("english", "ingles", "eng"),
                  "ht": ("haitian creole", "haitian", "creole", "kreyol", "kreyol ayisyen", "creole haitien", "hat")}


def language_code(text: str) -> str | None:
    """"ht", "HT", "Kreyòl", "Haitian Creole" -> "ht"; None when it isn't a portal language."""
    word = re.sub(r"\s+", " ", _fold(str(text or "")).strip().lower())
    if word in languages():
        return word
    return next((code for code, words in LANGUAGE_WORDS.items() if word in words and code in languages()), None)


def all_questions(bank: dict[str, Any]) -> dict[str, dict[str, Any]]:
    return {q["id"]: q for section in bank["sections"] for q in section["questions"]}


# --- conditions ----------------------------------------------------------------


def holds(condition: Any, answers: dict[str, Any]) -> bool:
    """{"q": id, "eq"|"in"|"gt"|"filled": ...}, {"any": [...]}, {"all": [...]},
    "always", "optional" (= not required)."""
    if condition in (None, "always"):
        return True
    if condition == "optional":
        return False
    if "any" in condition:
        return any(holds(c, answers) for c in condition["any"])
    if "all" in condition:
        return all(holds(c, answers) for c in condition["all"])
    value = answers.get(condition["q"])
    if "eq" in condition:
        return value == condition["eq"]
    if "in" in condition:
        return value in condition["in"]
    if "gt" in condition:
        try:
            return int(value) > int(condition["gt"])
        except (TypeError, ValueError):
            return False
    if "filled" in condition:
        return bool(value) == bool(condition["filled"])
    if "picked" in condition:  # a checklist with something ticked
        return bool(isinstance(value, dict) and value.get("selected")) == bool(condition["picked"])
    return False


def visible(question: dict[str, Any], answers: dict[str, Any]) -> bool:
    return holds(question.get("show_if"), answers)


# --- validation (what the client typed -> a clean value, or why not) -------------


def _fold(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


_ISO = re.compile(r"\d{4}-\d{2}-\d{2}")


def clean(question: dict[str, Any], value: Any) -> tuple[Any, str]:
    """-> (clean value, "" or a message in plain words). Empty values are
    allowed here; required-ness is checked at submit time."""
    kind = question["type"]
    if value in (None, "", [], {}):
        return None, ""
    if value == UNSURE and question.get("allow_unsure"):
        return UNSURE, ""
    if kind in ("text", "textarea", "country"):
        text = re.sub(r"\s+", " ", str(value)).strip()
        return (text[:500], "") if text else (None, "")
    if kind == "date":
        text = str(value).strip()
        if not _ISO.fullmatch(text):
            return None, "invalid_date"
        from datetime import date

        try:
            parsed = date.fromisoformat(text)
        except ValueError:
            return None, "invalid_date"
        if parsed.year < 1900 or parsed > clock.today().replace(year=clock.today().year + 15):
            return None, "invalid_date"
        return text, ""
    if kind in ("yes_no", "choice"):
        allowed = [o["value"] for o in question.get("options", [])]
        return (value, "") if value in allowed else (None, "invalid_choice")
    if kind == "number":
        try:
            number = int(str(value).strip())
        except ValueError:
            return None, "invalid_number"
        return (number, "") if 0 <= number <= 999 else (None, "invalid_number")
    if kind == "money":  # an amount a month, dollars and cents: the fee waiver request's lines (src/eoir26a.py reads it as the client writes it)
        import eoir26a

        if value == UNSURE and question.get("allow_unsure"):
            return UNSURE, ""
        amount = eoir26a.parse_money(value)
        return (f"{amount:.2f}", "") if amount is not None else (None, "invalid_money")
    if kind == "email":
        text = str(value).strip()
        return (text.lower(), "") if re.fullmatch(r"[^@\s]+@[^@\s]+\.[A-Za-z]{2,}", text) else (None, "invalid_email")
    if kind == "phone":
        digits = re.sub(r"\D", "", str(value))
        if len(digits) == 10:
            digits = "1" + digits
        return (f"+{digits}", "") if 11 <= len(digits) <= 15 else (None, "invalid_phone")
    if kind == "a_number":
        digits = re.sub(r"\D", "", str(value))
        return (digits.zfill(9), "") if 8 <= len(digits) <= 9 else (None, "invalid_a_number")
    if kind == "ssn":
        digits = re.sub(r"\D", "", str(value))
        return (f"{digits[:3]}-{digits[3:5]}-{digits[5:]}", "") if len(digits) == 9 else (None, "invalid_ssn")
    if kind == "height":
        # Clients answer in whichever unit they know (most know centimetres);
        # the form wants feet and inches. What they typed is kept beside it.
        if not isinstance(value, dict):
            return None, "invalid_height"
        try:
            if value.get("cm") not in (None, ""):
                cm = int(round(float(str(value["cm"]).replace(",", "."))))
                if cm < 3:  # "1,65" -- metres
                    cm = int(round(float(str(value["cm"]).replace(",", ".")) * 100))
                if not 90 <= cm <= 230:
                    return None, "invalid_height"
                return {"cm": cm, "form": cm_to_feet_inches(cm)}, ""
            feet, inches = int(value.get("feet")), int(value.get("inches") or 0)
        except (TypeError, ValueError):
            return None, "invalid_height"
        if not (3 <= feet <= 7 and 0 <= inches <= 11):
            return None, "invalid_height"
        return {"feet": feet, "inches": inches, "form": f"{feet}'{inches}\""}, ""
    if kind == "weight":
        if not isinstance(value, dict):
            return None, "invalid_weight"
        try:
            if value.get("kg") not in (None, ""):
                kg = float(str(value["kg"]).replace(",", "."))
                if not 25 <= kg <= 250:
                    return None, "invalid_weight"
                kg = round(kg, 1)
                return {"kg": int(kg) if kg == int(kg) else kg, "form": str(int(kg_to_lbs(kg)))}, ""
            lb = int(round(float(str(value.get("lb")).replace(",", "."))))
        except (TypeError, ValueError):
            return None, "invalid_weight"
        return ({"lb": lb, "form": str(lb)}, "") if 55 <= lb <= 550 else (None, "invalid_weight")
    if kind in ("us_address", "foreign_address", "group"):
        if not isinstance(value, dict):
            return None, "invalid"
        parts = {k: re.sub(r"\s+", " ", str(v)).strip() for k, v in value.items() if v not in (None, "")}
        if kind == "us_address" and parts.get("zip") and not re.fullmatch(r"\d{5}", parts["zip"]):
            return parts, "invalid_zip"
        if kind == "foreign_address":
            from extract.places import is_us_country
            if is_us_country(parts.get("country")):
                return parts, "foreign_country_us"
        for k in ("date_from", "date_to"):
            if parts.get(k) and not _ISO.fullmatch(parts[k]):
                return parts, "invalid_date"
        if parts.get("date_from") and parts.get("date_to") and parts["date_to"] < parts["date_from"]:
            return parts, "dates_backwards"
        return parts or None, ""
    if kind == "checklist":
        # {"none": true} -- none of these apply; {"selected": [...]} -- these do; "Unsure".
        if value == UNSURE:
            return UNSURE, ""
        if not isinstance(value, dict):
            return None, "invalid"
        if value.get("none"):
            return {"none": True}, ""
        allowed = [o["value"] for o in question.get("options", [])]
        picked = [v for v in allowed if v in (value.get("selected") or [])]
        return ({"selected": picked}, "") if picked else (None, "")
    if kind == "repeat":
        if not isinstance(value, list):
            return None, "invalid"
        rows = []
        for row in value[: question.get("max", 10)]:
            if not isinstance(row, dict):
                continue
            row_clean = {}
            for field in question["fields"]:
                v, err = clean(field, row.get(field["id"]))
                if err:
                    return value, err
                if v is not None:
                    row_clean[field["id"]] = v
            if row_clean.get("date_from") and row_clean.get("date_to") and row_clean["date_to"] < row_clean["date_from"]:
                return value, "dates_backwards"
            if row_clean:
                rows.append(row_clean)
        return rows or None, ""
    return None, "invalid"


def answer_checks(bank: dict[str, Any], answers: dict[str, Any]) -> dict[str, str]:
    from extract.places import is_us_country
    return {qid: "foreign_country_us" for qid, q in all_questions(bank).items()
            if q.get("type") == "foreign_address" and visible(q, answers)
            and isinstance(answers.get(qid), dict) and is_us_country(answers[qid].get("country"))}


def missing_required(bank: dict[str, Any], answers: dict[str, Any]) -> list[str]:
    """Visible required questions without an answer -- what blocks submitting."""
    out = []
    for qid, question in all_questions(bank).items():
        if question.get("required", True) and visible(question, answers) and answers.get(qid) in (None, "", [], {}):
            out.append(qid)
    return list(dict.fromkeys(out + list(answer_checks(bank, answers))))


# --- answers -> facts (the pipeline's own keys and conventions) -----------------------


def _upper(text: str) -> str:
    return re.sub(r"\s+", " ", _fold(str(text)).upper()).strip()


def _country(text: str) -> str:
    from questionnaire.languages import COUNTRIES, fold

    return COUNTRIES.get(fold(str(text)).strip(" ."), _upper(text))


def _fact_value(kind: str, part: str | None, value: Any) -> str:
    if kind == "date" or (part and part.startswith("date")) or part == "dob":
        return str(value)
    if kind == "country" or part == "country":
        return _country(value)
    if part == "state" and isinstance(value, str) and len(value.strip()) > 2:
        from questionnaire.handwriting import us_state_code

        return us_state_code(value) or _upper(value)
    if kind in ("height", "weight"):
        return value["form"] if isinstance(value, dict) else str(value)
    if kind in ("yes_no", "choice", "number", "phone", "email", "ssn", "a_number", "money"):
        return str(value)
    return _upper(value)


def _as_typed(value: Any) -> Any:
    """What the client typed, for the fact's raw value ("165 cm")."""
    if isinstance(value, dict) and "form" in value:
        for unit in ("cm", "kg", "lb"):
            if unit in value:
                return f"{value[unit]} {unit}"
        return f"{value['feet']} ft {value['inches']} in"
    return value


YES_PREFIX = "questionnaire.yes."  # + the fact key a client ticked in a checklist; value: what they ticked


def _checklist_facts(question: dict[str, Any], value: Any, add) -> None:
    """None of these -> every item behind the checklist No (the client's own
    answer). Ticked -> those items stay blank and the attorney sees what was
    ticked (one tick can cover several form items, so it is never turned
    into Yes boxes automatically); the rest No. Not sure -> all blank."""
    options = question.get("options", [])
    picked = set(value.get("selected") or []) if isinstance(value, dict) else set()
    ticked = {f for o in options if o["value"] in picked for f in o["facts"]}
    for o in options:
        ref = f"{o['label']['en']} (Part 9, Item {o['items']})"
        for fact in o["facts"]:
            if value == UNSURE:
                add(UNSURE_PREFIX + fact, UNSURE, ref)
            elif o["value"] in picked:
                add(YES_PREFIX + fact, "ticked", ref)
            elif fact not in ticked and (isinstance(value, dict) and (value.get("none") or picked)):
                # the answer says what the client was shown: the review screen has no scan of a portal answer
                add(fact, ("ticked 'none of these' for: " if value.get("none") else "didn't tick: ") + o["label"]["en"], "No")


def answers_to_facts(answers: dict[str, Any], bank: dict[str, Any] | None = None) -> list[ExtractedField]:
    bank = bank or load_bank()
    fields: list[ExtractedField] = []

    def add(key: str, raw: Any, value: Any) -> None:
        if value not in (None, ""):
            fields.append(ExtractedField(key, str(raw), value, 0.95))

    for qid, question in all_questions(bank).items():
        value = answers.get(qid)
        if value in (None, "", [], {}) or not visible(question, answers):
            continue
        kind = question["type"]
        if kind == "checklist":
            _checklist_facts(question, value, add)
            continue
        if value == UNSURE:  # never a Yes or a No on the form: the attorney asks the client
            add(UNSURE_PREFIX + question["fact"], UNSURE, question["label"]["en"])
            continue
        if question.get("fact"):
            add(question["fact"], _as_typed(value), _fact_value(kind, None, value))
        for part, key in (question.get("facts") or {}).items():
            if isinstance(value, dict) and value.get(part):
                add(key, value[part], _fact_value(kind, part, value[part]))
        if kind == "repeat" and question.get("fact_pattern"):
            repeat_fields = {f['id']: f for f in question.get('fields', [])}
            for n, row in enumerate(value, start=1):
                for part, v in row.items():
                    key = question["fact_pattern"].format(n=n, part=part)
                    if v == UNSURE and repeat_fields.get(part, {}).get('allow_unsure'):
                        add(UNSURE_PREFIX + key, UNSURE, repeat_fields[part]['label']['en'])
                    else:
                        add(key, v, _fact_value("text", part, v))

    # Answers that mean something to the pipeline beyond their own key.
    if answers.get("used_other_names") == "No":
        add("questionnaire.blank.other_names", "No", "Yes")  # -> policy NA-OTHER-NAMES (Part 1, Item 2: N/A)
    if answers.get("entry_how"):
        add("questionnaire.entered_via_border", answers["entry_how"], "Yes" if answers["entry_how"] == "border" else "No")
    home = answers.get("home_address") or {}
    if isinstance(home, dict) and home.get("apt"):
        add("applicant.physical_unit_type", "APT", "APT")
    if answers.get("other_dob") == "No":
        add("questionnaire.blank.other_dobs", "No", "Yes")
    return fields


# --- documents the answers call for --------------------------------------------------


def required_documents(answers: dict[str, Any], bank: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """[{doc spec + "required" + "count"}] for every document the answers
    call for -- one court disposition per criminal case listed, and so on.
    A document is REQUIRED only when the client's answers say they have it
    (has_ssn = Yes -> the SSN card); otherwise it may be OFFERED ("if you
    have it": passport, I-360 approval, a visa...), which never blocks
    anything. Required ones come first."""
    bank = bank or load_bank()
    required, offered = [], []
    for spec in bank["documents"]:
        rule = spec.get("required_if", "always")
        offer = spec.get("offer_if", "always" if rule == "optional" else None)
        if rule not in ("never", "optional") and holds(rule, answers):
            count = max(1, len(answers.get(spec["per"]) or [])) if spec.get("per") else 1
            required.append(spec | {"required": True, "count": count})
        elif offer is not None and holds(offer, answers):
            offered.append(spec | {"required": False, "count": 1})
    return required + offered


def localized(bank: dict[str, Any], lang: str, answers: dict[str, Any], help: dict[str, Any] | None = None) -> list[dict[str, Any]]:
    """The visible questionnaire in one language, for the client's screen,
    with the help that goes with it (schemas/questions/help/portal.json)."""
    help = load_help() if help is None else help

    def text(t):
        return t.get(lang) or t.get("en") if isinstance(t, dict) else t

    def one(q):
        out = {k: v for k, v in q.items() if k not in ("label", "help", "options", "fields", "fact", "facts", "fact_pattern")}
        out["label"] = text(q["label"])
        if q.get("help"):
            out["help"] = text(q["help"])
        if q.get("options"):
            out["options"] = [{"value": o["value"], "label": text(o["label"])} for o in q["options"]]  # not the form items behind them
        if q.get("fields"):
            out["fields"] = [one(f) for f in q["fields"]]
        if help["questions"].get(q["id"]):
            out["tip"] = text(help["questions"][q["id"]])
        return out

    def section(s):
        extra = help["sections"].get(s["id"], {})
        return {"id": s["id"], "title": text(s["title"]), "intro": text(s["intro"]) if s.get("intro") else None,
                "why": text(extra["why"]) if extra.get("why") else None, "icon": extra.get("icon"), "minutes": extra.get("minutes"),
                "questions": [one(q) for q in s["questions"] if visible(q, answers)]} | ({"example": text(s["example"])} if s.get("example") else {})

    return [section(s) for s in bank["sections"]]


def localized_scan_guide(lang: str, help: dict[str, Any] | None = None) -> dict[str, Any] | None:
    guide = (load_help() if help is None else help).get("scan_guide")
    if not guide:
        return None
    pick = lambda t: t.get(lang) or t["en"]  # noqa: E731
    return {"title": pick(guide["title"]), "steps": [{"who": pick(s["who"]), "how": pick(s["how"])} for s in guide["steps"]]}


def office_words(lang: str) -> str:
    """"the Exemplo & Lima office" in the client's language: the firm's name from Settings, else "the office" (DRAFT: the attorney and a certified
    translator; the Haitian Creole is a machine draft). The {office} in an answer of schemas/questions/help/portal.json."""
    import settings

    name = settings.firm_name()
    words = {"pt": ("o escritório {n}", "o escritório"), "es": ("la oficina {n}", "la oficina"), "en": ("the {n} office", "the office"),
             "ht": ("biwo {n}", "biwo a")}.get(lang) or ("the {n} office", "the office")
    return words[0].format(n=name) if name else words[1]


def localized_faq(lang: str, help: dict[str, Any] | None = None) -> list[dict[str, str]]:
    help = load_help() if help is None else help
    return [{"q": f["q"].get(lang) or f["q"]["en"], "a": (f["a"].get(lang) or f["a"]["en"]).replace("{office}", office_words(lang))} for f in help.get("faq", [])]
