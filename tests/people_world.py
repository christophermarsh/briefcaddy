"""A made-up firm for the people index and the conflict search (tests/test_people_index.py, tests/test_conflict_search.py): case folders written
directly (fact graph, documents, decisions, the case page's people, a VAWA case), never by processing documents. Everyone here is made up.

    case-ana     Ana Clara Exemplo Souza, an SIJ case: her passport and birth certificate (two spellings of her surname), her father Jose Exemplo
                 Souza (the parent the SIJ order names), her mother Maria Exemplo Lima, a petitioner recorded on the case page
    case-rosa    Rosa Exemplo Vawa, a VAWA self-petition (restricted by law): her abuser Carlos Abusador Exemplo, also her spouse; A-Number A055500111
    case-bia     Beatriz Exemplo Lima, a family case: the petitioner Paulo Peticionario Exemplo, and a document of a child
"""

from __future__ import annotations

import json
from pathlib import Path

ROSA_SECRETS = ("Rosa", "ROSA", "Vawa", "VAWA", "Abusador", "ABUSADOR", "055500111", "A055500111", "1988-07-09", "07/09/1988", "case-rosa", "1979-11-30", "11/30/1979",
                "RX9988776")


def source(doc_id: str, doc_type: str, raw, normalized=None, tier: int = 1) -> dict:
    return {"doc_id": doc_id, "doc_type": doc_type, "raw_value": str(raw), "normalized_value": raw if normalized is None else normalized, "confidence": 0.9,
            "extracted_at": "2026-09-01T10:00:00+00:00", "from_facts": []}


def fact(key: str, *sources: dict, value=None, tier: int = 1) -> dict:
    value = value if value is not None else sources[0]["normalized_value"]
    return {"fact_key": key, "tier": tier, "status": "resolved", "value": value, "derived_by": None, "derived_from": [], "resolution": None, "missing_reason": None,
            "review": None, "sources": list(sources)}


def doc(doc_id: str, type_: str, person: str, *, a_number: str = "", passport: str = "") -> dict:
    return {"id": doc_id, "files": [f"{doc_id}.pdf"], "doc_ids": [f"{doc_id}.pdf"], "pages": [1], "type": type_, "confidence": 0.9, "person": person, "person_set_by": None,
            "person_basis": "named", "language": "en", "issued": None, "expires": None, "identifiers": {"a_number": a_number, "receipt": "", "passport": passport, "ssn_last4": ""},
            "quality": "readable", "hash": doc_id * 4, "source": "folder", "added": "2026-09-01T10:00:00+00:00", "roles": [], "tags": [], "confidential": None,
            "text": "made-up text", "translated": None}


def write_case(root: Path, case: str, facts: dict, docs: list, *, status: dict | None = None, decisions: dict | None = None) -> Path:
    d = root / case
    d.mkdir(parents=True, exist_ok=True)
    (d / "fact_graph.json").write_text(json.dumps({"client_id": case, "facts": facts}), encoding="utf-8")
    (d / "meta.json").write_text(json.dumps({"client_id": case, "classifications": {}}), encoding="utf-8")
    (d / "documents.json").write_text(json.dumps({"version": 1, "built": "2026-10-01T02:00:00+00:00", "documents": docs}), encoding="utf-8")
    if status is not None:
        (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if decisions is not None:
        (d / "decisions.json").write_text(json.dumps(decisions), encoding="utf-8")
    return d


def make(root: Path) -> dict[str, Path]:
    root.mkdir(parents=True, exist_ok=True)
    ana = write_case(root, "case-ana", {
        "applicant.given_name": fact("applicant.given_name", source("pass.pdf", "passport", "ANA CLARA"), source("questionnaire", "intake_questionnaire", "Ana Clara")),
        "applicant.family_name": fact("applicant.family_name", source("pass.pdf", "passport", "EXEMPLO SOUZA"), source("questionnaire", "intake_questionnaire", "Exemplo Sousa")),
        "applicant.date_of_birth": fact("applicant.date_of_birth", source("pass.pdf", "passport", "14 MAR 2006", "2006-03-14")),
        "applicant.country_of_birth": fact("applicant.country_of_birth", source("pass.pdf", "passport", "BRASIL")),
        "applicant.birth_certificate_name": fact("applicant.birth_certificate_name", source("cert.pdf", "birth_certificate", "ANA CLARA EXEMPLO SOUZA")),
        "applicant.father_given_name": fact("applicant.father_given_name", source("cert.pdf", "birth_certificate", "JOSE")),
        "applicant.father_family_name": fact("applicant.father_family_name", source("cert.pdf", "birth_certificate", "EXEMPLO SOUZA")),
        "applicant.mother_given_name": fact("applicant.mother_given_name", source("cert.pdf", "birth_certificate", "MARIA")),
        "applicant.mother_family_name": fact("applicant.mother_family_name", source("cert.pdf", "birth_certificate", "EXEMPLO LIMA")),
        "applicant.mother_dob": fact("applicant.mother_dob", source("questionnaire", "intake_questionnaire", "05/02/1984", "1984-05-02")),
        "sij.parent_name": fact("sij.parent_name", source("order.pdf", "sij_order", "JOSE EXEMPLO SOUZA")),
        "folder.passport.FZ1234567": fact("folder.passport.FZ1234567", source("pass.pdf", "passport", "FZ1234567")),
    }, [doc("a1", "passport", "applicant", passport="FZ1234567"), doc("a2", "birth_certificate", "applicant")],
        status={"journey": {"people": [{"id": "person.1", "given_name": "Paulo", "family_name": "Exemplo Tio", "name": "Paulo Exemplo Tio", "relationship": "Other relative",
                                        "phone": "", "email": "", "person": "petitioner", "by": "Jane Paralegal", "at": "2026-09-01T10:00:00-04:00"}]}})
    rosa = write_case(root, "case-rosa", {
        "applicant.given_name": fact("applicant.given_name", source("q", "intake_questionnaire", "ROSA")),
        "applicant.family_name": fact("applicant.family_name", source("q", "intake_questionnaire", "EXEMPLO VAWA")),
        "applicant.date_of_birth": fact("applicant.date_of_birth", source("q", "intake_questionnaire", "1988-07-09")),
        "applicant.a_number": fact("applicant.a_number", source("q", "intake_questionnaire", "A055500111")),
        "applicant.spouse_given_name": fact("applicant.spouse_given_name", source("mar.pdf", "marriage_certificate", "CARLOS")),
        "applicant.spouse_family_name": fact("applicant.spouse_family_name", source("mar.pdf", "marriage_certificate", "ABUSADOR EXEMPLO")),
    }, [doc("r1", "passport", "applicant", a_number="A055500111", passport="RX9988776"), doc("r2", "marriage_certificate", "spouse")],
        status={"filings": [{"filing": "vawa", "title": "I-360 VAWA self-petition", "mailed_on": "2026-09-01", "carrier": "USPS", "by": "Sam Attorney"}]},
        decisions={"vawa-abuser": {"action": "set", "values": {"vawa.abuser_given_name": "Carlos", "vawa.abuser_family_name": "Abusador Exemplo",
                                                                 "vawa.abuser_dob": "1979-11-30"}, "reviewer": "Sam Attorney", "note": "", "at": "2026-09-02T10:00:00-04:00",
                                   "item": {"id": "vawa-abuser", "kind": "answer", "level": "review", "title": "The abuser", "group": "g", "facts": []}}})
    bia = write_case(root, "case-bia", {
        "applicant.given_name": fact("applicant.given_name", source("q", "intake_questionnaire", "BEATRIZ")),
        "applicant.family_name": fact("applicant.family_name", source("q", "intake_questionnaire", "EXEMPLO LIMA")),
        "petitioner.given_name": fact("petitioner.given_name", source("usp.pdf", "us_passport", "PAULO")),
        "petitioner.family_name": fact("petitioner.family_name", source("usp.pdf", "us_passport", "PETICIONARIO EXEMPLO")),
        "petitioner.dob": fact("petitioner.dob", source("usp.pdf", "us_passport", "1970-01-20")),
        "applicant.child1_given_name": fact("applicant.child1_given_name", source("q", "intake_questionnaire", "LUCAS")),
        "applicant.child1_family_name": fact("applicant.child1_family_name", source("q", "intake_questionnaire", "EXEMPLO LIMA")),
    }, [doc("b1", "us_passport", "petitioner"), doc("b2", "birth_certificate", "child_1")])
    return {"ana": ana, "rosa": rosa, "bia": bia}
