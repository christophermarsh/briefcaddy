"""Document-processing helper."""

from __future__ import annotations

from . import (
    birth_certificate,
    criminal_record,
    dhs_record,
    drivers_license,
    i94,
    i360_approval,
    marriage_certificate,
    name_change_order,
    notice_to_appear,
    passport,
    petitioner_docs,
    sij_order,
    ssn_card,
    uscis_notice,
    visa,
    work_permit,
)
from .base import ExtractedField

EXTRACTORS = {
    "i94": i94.extract,
    "i360_approval": lambda text: i360_approval.extract(text) + uscis_notice.extract(text),
    # Supporting implementation.
    "uscis_notice": uscis_notice.extract, "i765_approval": uscis_notice.extract, "i130_approval": uscis_notice.extract,
    "i485_receipt": uscis_notice.extract, "i526_approval": uscis_notice.extract, "i590_approval": uscis_notice.extract,
    "i730_approval": uscis_notice.extract,
    "ssn_card": ssn_card.extract,
    "drivers_license": drivers_license.extract,
    "birth_certificate": birth_certificate.extract,
    "passport": passport.extract,
    "visa": visa.extract,
    "work_permit": work_permit.extract,
    "notice_to_appear": notice_to_appear.extract,
    "i213": dhs_record.extract,
    "criminal_record": criminal_record.extract,
    "sij_order": sij_order.extract,
    # Supporting implementation.
    "us_passport": petitioner_docs.us_passport,
    "citizenship_certificate": petitioner_docs.citizenship_certificate,
    "green_card": petitioner_docs.green_card,
    "us_birth_certificate": petitioner_docs.us_birth_certificate,
    "tax_return": petitioner_docs.tax_return,
    "marriage_certificate": marriage_certificate.extract,
    "name_change_order": name_change_order.extract,  # Supporting implementation.
}


def extract_fields(doc_type: str, text: str) -> list[ExtractedField]:
    """Document-processing helper."""
    extractor = EXTRACTORS.get(doc_type)
    if extractor is None:
        return []
    return extractor(text)


__all__ = ["EXTRACTORS", "ExtractedField", "extract_fields"]
