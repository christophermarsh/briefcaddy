"""The firm's offices, and which one a case is filed from.

Each office (the Settings page: "Main office", "Office: ...") has its own
attorney, bar admission, EOIR ID, USCIS account, address, phone, signer and
the states it files for. A case uses:

  1. the office chosen on the case (data/clients/<id>/office.json: who, when), else
  2. the office named on the firm's client list (the portal import's "office" column), else
  3. the office that files for the client's state (the I-485's physical address), else
  4. the main office.

The case's office supplies the firm's facts on every form (the G-28, the
preparer's part, the EOIR forms, the "in care of" mailing address when the
firm's address is the client's safe address) and the cover letter's
letterhead address and signer. A fact a reviewer decided is never replaced.
"""

from __future__ import annotations

import json
import re
from functools import lru_cache
from pathlib import Path
from typing import Any

import clock
import events
import schema_path
from holders import OFFICE, producer

MAIN = "main"
FIRM_DOC = "firm_profile.json"  # the doc id the firm's facts carry (batch.FIRM_PROFILE_DOC_ID)
MAILING = {"firm.street": "applicant.mailing_street", "firm.city": "applicant.mailing_city", "firm.state": "applicant.mailing_state",
           "firm.zip": "applicant.mailing_zip", "firm.business_name": "applicant.mailing_in_care_of"}


def offices() -> list[dict[str, Any]]:
    """Every office, the main one first: its id, name, states and values (the Settings page's, over the defaults)."""
    import settings

    stamp = (str(settings.PATH), settings.mtime()) + tuple(p.stat().st_mtime for p in _DEFAULTS if p.exists())
    return [dict(o) for o in _offices(stamp)]


_DEFAULTS = [schema_path.path("packet", "companion_forms"), schema_path.path("firm", "firm_profile"), schema_path.path("cover_letter", "i485")]


@lru_cache(maxsize=4)
def _offices(stamp: tuple) -> tuple:
    """offices(), read again only when the Settings page or the shipped defaults change (every case's page asks)."""
    import settings

    out = []
    for s in settings.specs():
        if s["id"] != "firm" and not s.get("office"):
            continue
        values = {f["key"]: f["value"] for f in s["fields"] if f["value"] not in (None, "")}
        states = [x.strip().upper() for x in re.split(r"[,;/\s]+", str(values.get("office.states") or "")) if x.strip()]
        out.append({"id": MAIN if s["id"] == "firm" else s["id"], "section": s["id"], "name": values.get("office.name") or s["title"],
                    "states": states, "values": values})
    return tuple(out)


def by_id(office_id: str | None) -> dict[str, Any] | None:
    return next((o for o in offices() if o["id"] == office_id), None)


def chosen(client_dir: Path) -> dict[str, Any] | None:
    path = Path(client_dir) / "office.json"
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else None


def for_case(client_dir: Path, state: str | None = None) -> dict[str, Any]:
    """The office this case is filed from (see the module docstring)."""
    every = offices()
    pick = chosen(client_dir)
    if pick and (office := next((o for o in every if o["id"] == pick.get("office")), None)):
        return office | {"why": f"chosen by {pick.get('by')}"}
    listed = _imported(Path(client_dir).name)  # the firm's client list named the office
    if listed and (office := next((o for o in every if listed.lower() in (o["id"].lower(), o["name"].lower())), None)):
        return office | {"why": "on the firm's client list"}
    state = (state or "").strip().upper()
    if state:
        office = next((o for o in every if state in o["states"]), None)
        if office:
            return office | {"why": f"files for {state}"}
    return every[0] | {"why": "the main office"}


def _imported(client_id: str) -> str | None:
    """The office the firm's client list gave this client (src/portal/admin.py import), if any."""
    import os

    root = Path(os.environ.get("PORTAL_DATA") or Path(__file__).resolve().parents[1] / "data" / "portal")
    path = root / "clients" / client_id / "profile.json"
    return (json.loads(path.read_text(encoding="utf-8")).get("office") or None) if path.exists() else None


def choose(client_dir: Path, office_id: str, by: str) -> dict[str, Any]:
    if not str(by or "").strip():
        raise ValueError("Enter your name first: every change records who made it.")
    if by_id(office_id) is None:
        raise LookupError(f"No office {office_id!r}.")
    record = {"office": office_id, "by": by.strip(), "at": clock.stamp()}
    (Path(client_dir) / "office.json").write_text(json.dumps(record, indent=1), encoding="utf-8")
    events.record("office", "chose", f"Chose the office for the case: {(by_id(office_id) or {}).get('name') or events.words(office_id)}", case_dir=client_dir, who=by.strip())
    return record


def _state_of(graph) -> str | None:
    fact = graph.get("applicant.physical_state")
    return str(fact.value) if fact is not None and fact.status == "resolved" and fact.value else None


def apply(graph, client_dir: Path):
    """The case's office's details in place of the main office's, on every firm fact the firm's details supplied."""
    office = for_case(client_dir, _state_of(graph))
    values = office["values"]
    import settings

    if settings.identity_withheld():  # a case read before the firm configured the product: the sample firm's identity goes from its facts too
        for key in settings.IDENTITY_KEYS - set(values):
            graph.set_aside({FIRM_DOC, "companion_forms.json"}, key)
    care = graph.get("applicant.mailing_in_care_of")
    firm_is_mailing = care is not None and all(s.doc_type == "firm_profile" for s in care.sources)
    for key, value in values.items():
        if not key.startswith("firm."):
            continue
        graph.replace_from(key, "firm_profile", FIRM_DOC, value)
        if firm_is_mailing and key in MAILING:  # the client's safe mailing address is the office's
            graph.replace_from(MAILING[key], "firm_profile", FIRM_DOC, value)
    return graph


def _phone(raw: str | None) -> str | None:
    digits = re.sub(r"\D", "", str(raw or ""))
    return f"({digits[:3]}) {digits[3:6]}-{digits[6:10]}" if len(digits) == 10 else raw


def letter(config: dict[str, Any], client_dir: Path, state: str | None = None) -> dict[str, Any]:
    """Fictional example or implementation helper."""
    office = for_case(client_dir, state)
    v = office["values"]
    import settings

    name = " ".join(str(settings.values("firm").get("firm.business_name") or v.get("firm.business_name") or "").split())
    head = {"name_light": "", "name_bold": name.upper(), "address": "", "tagline": str(v.get("office.tagline") or "").strip(),
            "attorneys": str(v.get("office.attorneys") or "").strip()}
    street = ", ".join(x for x in (v.get("firm.street"), v.get("firm.city"), v.get("firm.state")) if x)
    if street:
        head["address"] = f"{street}, {v.get('firm.zip') or ''}".strip(", ").upper()
    lines = [x for x in (f"Tel-{_phone(v['firm.phone'])}" if v.get("firm.phone") else None,
                         f"Fax-{v['office.fax']}" if v.get("office.fax") else None,
                         f"Email: {str(v['firm.email']).lower() if str(v['firm.email']).isupper() else v['firm.email']}" if v.get("firm.email") else None,
                         v.get("office.website")) if x]
    return config | {"letterhead": head, "signer": {"name": v.get("office.signer") or "", "lines": lines}}


NO_OFFICE = ("Save the office under Settings before this packet can be built: until the firm's name, the attorney's name and bar number, the address "
             "and the phone are saved (Settings, Main office), every box that would carry the firm stays blank.")


@producer(OFFICE)
def problems(client_dir: Path, state: str | None = None) -> list[str]:
    """What a packet can't be mailed without: the office's attorney and address."""
    office = for_case(client_dir, state)
    v = office["values"]
    import settings

    if office["id"] == MAIN and settings.identity_withheld():
        return [NO_OFFICE]
    missing = [label for key, label in (("firm.preparer_family_name", "the attorney's name"), ("firm.attorney_bar_number", "the bar number"),
                                        ("firm.street", "the street"), ("firm.city", "the city"), ("firm.zip", "the ZIP code"),
                                        ("firm.phone", "the phone")) if not v.get(key)]
    return [f"The {office['name']} office is missing {', '.join(missing)}: fill it in on the Settings page."] if missing else []
