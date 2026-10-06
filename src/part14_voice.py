"""The voice Part 14 entries are written in: the client's ("Yes, I previously worked without employment authorization ...") or the
office's ("The applicant previously worked without employment authorization ...").

The firm's own filed I-485s use one or the other, and which one is the firm's choice, not the product's: an office policy on the
Settings page (the office's section, "Part 14 entries are written"), approved by the attorney like a practice (src/rules/approval.py,
id PRACTICE_ID), so a change to the choice needs the attorney's approval again. One voice per packet: the case's office decides
(src/offices.py).

Until an office has set the choice and the attorney has approved it, the entries the product suggests are written in the client's
voice, as DRAFT, and the Part 14 card says the policy is not set. The overflow entries the questionnaire makes (more addresses,
jobs, children: src/assemble.py) are lists and have no voice; they are unaffected. The explanations for a "Yes" (the next piece of
the Part 14 work) take their voice from voice() below.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

PRACTICE_ID = "PRACTICE:PART14-VOICE"
KEY = "office.part14_voice"
VOICES = {"client": "In the client's voice (\"Yes, I ...\")", "office": "In the office's voice (\"The applicant ...\")"}
DEFAULT = "client"  # what a suggested entry is written in until the policy is set and approved
PRACTICE_NAME = "How Part 14 entries are written"
PRACTICE = ("Each office chooses one voice for the entries written in Part 14 of a form: the client's (\"Yes, I ...\") or the office's "
            "(\"The applicant ...\"). A packet uses the voice of the office the case is filed from, one voice throughout. Until the office has "
            "chosen and the attorney has approved the choice here, the entries the product suggests are written in the client's voice and "
            "marked DRAFT. The choice changes only how a sentence is worded: the facts in it and where each came from are the same in either "
            "voice, and a person approves every entry for the case. Entries the questionnaire makes (more addresses, jobs and children than "
            "the form has room for) are lists and have no voice.")
PRACTICE_SOURCE = ("The firm's own practice, written for the attorney's approval: no statute or rule chooses between the two voices. "
                   "The form's own instructions only say what Part 14 is for (the I-485 instructions, Part 14. Additional Information).")


def choices() -> dict[str, str | None]:
    """{office id: its choice ("client", "office" or None when not set)}: what the approval holds for."""
    import offices

    return {o["id"]: (o["values"].get(KEY) or None) for o in offices.offices()}


def stamp() -> tuple:
    """What the approval depends on (the approval's catalog reads it again when one changes): the Settings page."""
    import settings

    return (settings.mtime(),)


def practice_entry() -> dict[str, Any]:
    """The practice as the approval catalog lists it (src/rules/approval.py): its hash covers every office's choice, so changing one asks for
    the approval again."""
    text = {"plain_text": PRACTICE, "source": PRACTICE_SOURCE, "choices": sorted((k, v or "") for k, v in choices().items())}
    return {"id": PRACTICE_ID, "kind": "practice", "code": PRACTICE_ID.split(":", 1)[1], "name": PRACTICE_NAME,
            "plain_text": PRACTICE, "source": PRACTICE_SOURCE, "hash": hashlib.sha256(json.dumps(text, sort_keys=True).encode("utf-8")).hexdigest()}


def practice() -> dict[str, Any]:
    """The attorney's approval of the practice: {state, text, plain_text, id}."""
    from review.state import rule_info

    info = rule_info(PRACTICE_ID)
    return {"state": info["approval"]["state"], "text": info["approval_text"], "plain_text": info["plain_text"], "id": PRACTICE_ID}


def voice(client_dir: Path) -> dict[str, Any]:
    """The voice for a case's packet: {voice: "client" | "office" (the one to write in), chosen: what the office set (or None),
    approved: bool, note: what the Part 14 card says}. The voice is the office's choice only when it is set and the attorney's
    approval holds for it; otherwise the client's, and the note says the policy is not set (or not approved yet)."""
    import offices

    office = offices.for_case(Path(client_dir))
    chosen = office["values"].get(KEY) or None
    approved = practice()["state"] == "approved"
    if chosen not in VOICES:
        return {"voice": DEFAULT, "chosen": None, "approved": approved, "office": office["name"],
                "note": f"The voice for Part 14 entries is not set for {office['name']}: entries are written in the client's voice (\"Yes, I ...\") "
                        "until the attorney sets it on Settings and approves it."}
    if not approved:
        return {"voice": DEFAULT, "chosen": chosen, "approved": False, "office": office["name"],
                "note": f"{office['name']} chose to write Part 14 entries {VOICES[chosen].lower()}, but the attorney has not approved it yet "
                        "(Keeping current): entries are written in the client's voice until then."}
    return {"voice": chosen, "chosen": chosen, "approved": True, "office": office["name"],
            "note": f"Part 14 entries are written {VOICES[chosen].lower()} (the office's policy, approved)."}
