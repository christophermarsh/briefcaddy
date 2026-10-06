"""Local decision models through Ollama's /v1/systemone endpoint (Ollama
0.35+; e.g. Nimble): a state plus named, typed questions -> each answer with
probabilities. Same transport as the vision model (src/vision/ollama.py):
plain HTTP to localhost, or under WSL with Windows Ollama, Windows' own
curl.exe -- Ollama is never exposed on the network.
"""

from __future__ import annotations

import os
import time
from pathlib import Path
from typing import Any

DEFAULT_URL = os.environ.get("OLLAMA_URL", "http://localhost:11434")

# What kind of document is this? -- the choices and the words the model reads.
# Tuning this wording is the cheapest way to improve the model (docs/learning.md);
# version 2 (tools/wordings/document_types_v2.json) gives each type its tell-tale
# features -- on the trial set: 27 vs 26 of 31 right on cut-off pages.
DOCUMENT_TYPES = {
    "passport": "A passport: the identity page with the machine-readable lines (P<...), or any passport page that holds visas or entry and exit stamps (ADMITTED, DHS/CBP, dates, port codes)",
    "visa": "A U.S. visa foil printed in a passport: UNITED STATES OF AMERICA, VISA, a visa type or class such as B1/B2, annotation, machine-readable lines starting V<",
    "i94": "A Form I-94 arrival/departure record printout from the CBP website: Admission (I-94) Record Number, Class of Admission, Admit Until Date",
    "i360_approval": "A USCIS Form I-797 Notice of Action approving a Form I-360 petition (Special Immigrant Juvenile): Approval Notice, I360, a receipt number such as IOE..., EAC..., WAC...",
    "uscis_notice": "Any other USCIS Form I-797 notice: a receipt notice, biometrics appointment, request for evidence or transfer notice",
    "birth_certificate": "A birth certificate (for example a Brazilian Certidao de Nascimento from a Cartorio) or its English translation",
    "translation_certification": "A translator's signed statement that a translation is complete and accurate (I certify that I am competent to translate ...)",
    "marriage_certificate": "A marriage certificate (Certidao de Casamento) or its translation",
    "ssn_card": "A Social Security card from the Social Security Administration: a nine-digit number written like 123-45-6789, the holder's name, a signature line; it may say VALID FOR WORK ONLY WITH DHS AUTHORIZATION",
    "drivers_license": "A driver's license, learner's permit or state ID card from a state motor vehicle agency (RMV, DMV): license class, date of birth, height, eye color",
    "work_permit": "An Employment Authorization Document (EAD, Form I-766) card from USCIS: EMPLOYMENT AUTHORIZATION CARD, USCIS# or A-number, a category code such as C14 or C09, Card Expires",
    "notice_to_appear": "A Notice to Appear (Form I-862) or another immigration court paper: an EOIR hearing notice or an order of an immigration judge",
    "i213": "A DHS Record of Deportable/Inadmissible Alien (Form I-213, or a CBP encounter record on that form): Date, Place, Time, and Manner of Last Entry, Method of Location/Apprehension, a narrative",
    "sij_order": "A state court's Special Immigrant Juvenile order or findings (probate, family or juvenile court): reunification with one or both parents not viable due to abuse, neglect or abandonment, and not in the child's best interest to return to the home country",
    "us_passport": "A United States passport (the U.S. citizen relative's): machine-readable line P<USA, nationality UNITED STATES OF AMERICA",
    "citizenship_certificate": "A U.S. Certificate of Naturalization or Certificate of Citizenship (Form N-550, N-570, N-560, N-561) or a Consular Report of Birth Abroad (FS-240)",
    "green_card": "A U.S. Permanent Resident Card (green card): USCIS#, Category, Resident Since, Card Expires",
    "us_birth_certificate": "A U.S. state's birth certificate (certificate of live birth, certification of vital record)",
    "tax_return": "A U.S. federal income tax return (Form 1040) or IRS tax return transcript",
    "w2": "A Form W-2, Wage and Tax Statement",
    "pay_stub": "A pay stub / earnings statement from an employer",
    "bank_statement": "A bank account statement",
    "lease": "A residential lease or rental agreement",
    "utility_bill": "A utility bill (electricity, gas, water, internet)",
    "divorce_decree": "A divorce judgment or decree (or a dissolution of marriage)",
    "criminal_record": "A criminal court docket, police report, arrest record or case disposition (dismissal, conviction)",
    "intake_questionnaire": "The law firm's intake questionnaire filled in by the client",
    "other": "Something else",
}


class DecisionModelError(RuntimeError):
    pass


def decide(state: Any, questions: dict[str, dict[str, Any]], model: str, url: str = DEFAULT_URL, timeout: float = 120) -> tuple[dict[str, Any], float]:
    """(answers, seconds). Raises DecisionModelError when the model can't be reached."""
    from vision.ollama import _is_wsl, _post_http, _post_via_windows_curl

    payload = {"model": model, "state": state, "questions": questions, "keep_alive": "10m"}
    endpoint = url.rstrip("/") + "/v1/systemone"
    started = time.time()
    import jobs

    try:
        with jobs.gpu_lock():  # one use of the model at a time, whichever process asks (src/jobs.py)
            try:
                body = _post_http(endpoint, payload, timeout)
            except OSError:
                if not _is_wsl():
                    raise
                body = _post_via_windows_curl(endpoint, payload, timeout, Path(".ocr_tmp"))
    except Exception as exc:  # noqa: BLE001 -- reported as one error the caller can skip
        raise DecisionModelError(f"{model}: {type(exc).__name__}: {exc}") from exc
    if "answers" not in body:
        raise DecisionModelError(f"{model}: {body.get('error', 'no answers')}")
    return body["answers"], time.time() - started


def document_type(text: str, model: str, max_chars: int = 6000, types: dict[str, str] | None = None) -> tuple[str, float, float]:
    """(type, probability of that type, seconds) for a document's text."""
    answers, secs = decide({"document_text": text[:max_chars]},
                           {"kind": {"type": "choice", "instructions": "What kind of document is this?", "criteria": types or DOCUMENT_TYPES}},
                           model)
    kind = answers["kind"]
    return kind["choice"], float((kind.get("probabilities") or {}).get(kind["choice"], kind.get("confidence", 0)) or 0), secs
