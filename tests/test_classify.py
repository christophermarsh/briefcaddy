"""classify_text -- grounded in real text extracted from the example case's
actual documents (see docs/ARCHITECTURE.md section 1 and src/classify/patterns.py).
Only classify_text is exercised here: it's the pure function kept separate
from PDF I/O specifically so it's testable without a PDF fixture for every
doc_type (see classifier.py's own docstring)."""

import pytest

from classify import classify_text
from classify.classifier import _try_translate
from classify.translate import installed_language_codes

pytestmark_fr = pytest.mark.skipif(
    "fr" not in installed_language_codes(), reason="Argos Translate fr->en package not installed"
)


def test_empty_text_is_unclassified_and_flags_extraction_failure():
    result = classify_text("")
    assert result.doc_type == "unclassified"
    assert result.text_extracted is False


def test_whitespace_only_text_is_unclassified_and_flags_extraction_failure():
    result = classify_text("   \n\t  ")
    assert result.doc_type == "unclassified"
    assert result.text_extracted is False


def test_i94_classifies_on_cbp_signals():
    # Real example case: the CBP I-94 printout.
    text = "Most Recent I-94\nAdmission I-94 Record Number: 14335150685\nClass of Admission: B2\nU.S. Customs and Border Protection"
    result = classify_text(text)
    assert result.doc_type == "i94"
    assert result.confidence >= 0.5


def test_generic_uscis_notice_without_case_type_stays_generic():
    text = "I-797, NOTICE OF ACTION\nReceipt Number: ABC1234567890\nU.S. CITIZENSHIP AND IMMIGRATION SERVICES"
    result = classify_text(text)
    assert result.doc_type == "uscis_notice"


def test_uscis_notice_subclassifies_by_case_type_i360():
    # Real example case: the I-360 approval notice, sub-classified by its
    # own "Case Type" field per docs/ARCHITECTURE.md section 1.
    text = (
        "I-797, NOTICE OF ACTION\nReceipt Number: WAC1234567890\n"
        "U.S. CITIZENSHIP AND IMMIGRATION SERVICES\n"
        "Case Type: I360 - PETITION FOR AMERASIAN, WIDOWER, OR SPECIAL IMMIGRANT"
    )
    result = classify_text(text)
    assert result.doc_type == "i360_approval"


def test_uscis_notice_subclassifies_by_case_type_i765():
    text = "I-797, NOTICE OF ACTION\nReceipt Number: WAC9876543210\nCase Type: I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION"
    result = classify_text(text)
    assert result.doc_type == "i765_approval"


def test_uscis_notice_subclassifies_by_case_type_i526():
    # UNVERIFIED case-type pattern (src/classify/patterns.py, 2026-09-30):
    # no real I-526 notice sample seen yet, matched by form number alone.
    text = "I-797, NOTICE OF ACTION\nReceipt Number: EAC1234567890\nCase Type: I526 - IMMIGRANT PETITION BY STANDALONE INVESTOR"
    result = classify_text(text)
    assert result.doc_type == "i526_approval"


def test_uscis_notice_subclassifies_by_case_type_i590():
    text = "I-797, NOTICE OF ACTION\nReceipt Number: EAC1234567891\nCase Type: I590 - REGISTRATION FOR CLASSIFICATION AS REFUGEE"
    result = classify_text(text)
    assert result.doc_type == "i590_approval"


def test_uscis_notice_subclassifies_by_case_type_i730():
    text = "I-797, NOTICE OF ACTION\nReceipt Number: EAC1234567892\nCase Type: I730 - REFUGEE/ASYLEE RELATIVE PETITION"
    result = classify_text(text)
    assert result.doc_type == "i730_approval"


def test_birth_certificate_matches_portuguese_and_english_headers():
    text = "CERTIDÃO DE NASCIMENTO\nCIVIL REGISTRY\nFather's Name: Joao Sampaio\nMother's Name: Sabrina Moura Almeida"
    result = classify_text(text)
    assert result.doc_type == "birth_certificate"


def test_passport_matches_real_brazilian_mrz_line():
    # Real example case: Maria Eduarda's Brazilian passport bio page.
    text = "REPÚBLICA FEDERATIVA DO BRASIL\nPASSAPORTE\nP<BRAMOURA<<SAMPAIO<<MARIA<EDUARDA<<<<<<<<<<<<<<<<<<<<<<"
    result = classify_text(text)
    assert result.doc_type == "passport"


def test_visa_matches_real_mrz_visa_line():
    # Real example case: the US visa stamp in an older passport.
    text = "UNITED STATES of AMERICA\nVisa Type/Class: B2\nVNUSAMOURA<SAMPAIO<<MARIA<EDUARDA<<<<<<<<<<<<<<<<<<<<<<"
    result = classify_text(text)
    assert result.doc_type == "visa"


def test_ssn_card_matches_work_authorization_restriction():
    text = "YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
    result = classify_text(text)
    assert result.doc_type == "ssn_card"


def test_drivers_license_matches_state_id_header():
    text = "DRIVER'S LICENSE\nNOT FOR FEDERAL ID\nCLASS D\nDOB 02/20/2002"
    result = classify_text(text)
    assert result.doc_type == "drivers_license"


def test_g28_matches_the_real_forms_omb_number_and_part1_header():
    # Real example case: a bare "G-28" / "Notice of Entry of Appearance"
    # mention isn't enough on its own -- confirmed as a false-positive
    # source when the real I-360 approval notice's own boilerplate
    # mentions Form G-28 in passing (see patterns.py). The real G-28 form
    # itself has these two much more specific markers.
    text = (
        "Form G-28\nNotice of Entry of Appearance as Attorney or Accredited Representative\n"
        "DHS Form G-28 OMB No. 1615-0105\nPart 1. Information About Attorney or Accredited Representative"
    )
    result = classify_text(text)
    assert result.doc_type == "g28"


def test_g28_bare_mention_alone_is_not_enough_to_classify():
    # Exactly the false positive this pattern set used to have: an I-360
    # approval notice's boilerplate mentions Form G-28 without being one.
    text = "...should be accompanied by Form G-28, Notice of Entry of Appearance as Attorney or Accredited Representative."
    result = classify_text(text)
    assert result.doc_type != "g28"


def test_i765_matches_employment_authorization_application():
    text = "Form I-765\nApplication for Employment Authorization"
    result = classify_text(text)
    assert result.doc_type == "i765"


def test_i485_matches_register_permanent_residence():
    text = "Form I-485\nApplication to Register Permanent Residence or Adjust Status"
    result = classify_text(text)
    assert result.doc_type == "i485"


def test_intake_questionnaire_matches_firm_header():
    text = "Questionário I485 - SIJS\nGeorges | Cote Law"
    result = classify_text(text)
    assert result.doc_type == "intake_questionnaire"


def test_intake_questionnaire_matches_despite_real_ocr_garbling():
    # Real example case: a handwritten, scanned intake questionnaire's OCR
    # garbled both "Questionário" (-> "Questiondrio") and "SIJS" (-> "SIS",
    # the J dropped) past the original patterns' recognition. "AJUSTE DE
    # STATUS" (the questionnaire's own subtitle) came through clean.
    text = "Questiondrio para Ajuste de Status\n1485 - SIS\nGEORGES | COTE LAW"
    result = classify_text(text)
    assert result.doc_type == "intake_questionnaire"


def test_below_min_confidence_is_unclassified_but_keeps_best_guess_in_ambiguous_with():
    # A single weak signal (one CLASS/DOB style hit) shouldn't be trusted as
    # a real classification -- MIN_CONFIDENCE exists precisely so a lone
    # 0.1-weight signal doesn't masquerade as a confident match.
    text = "CLASS"
    result = classify_text(text)
    assert result.doc_type == "unclassified"
    assert "drivers_license" in result.ambiguous_with


def test_near_tie_between_two_confident_scores_is_flagged_ambiguous_not_guessed():
    # Constructed edge case (not from the example case): stacks enough
    # signals that both i94 and uscis_notice clear MIN_CONFIDENCE within
    # CONFLICT_MARGIN of each other, to prove a close score is surfaced as
    # ambiguous rather than silently resolved to the higher one.
    text = (
        "I-94\nU.S. Customs and Border Protection\nClass of Admission\n"
        "I-797\nReceipt Number\nU.S. CITIZENSHIP AND IMMIGRATION SERVICES"
    )
    result = classify_text(text)
    assert result.doc_type == "i94"
    assert "uscis_notice" in result.ambiguous_with


def test_unrecognized_text_is_unclassified_with_no_signals():
    result = classify_text("This is a grocery list. Milk, eggs, bread.")
    assert result.doc_type == "unclassified"
    assert result.confidence == 0.0
    assert result.matched_signals == []


@pytestmark_fr
def test_try_translate_recognizes_a_french_birth_certificate():
    # Standard French civil-registry terminology (not a real client
    # document -- none in French exists yet, see docs/decisions.md), used
    # to confirm the translate-then-classify fallback actually works.
    text = "ACTE DE NAISSANCE\nNom du pere: JEAN DUPONT\nNom de la mere: CLAIRE MARTIN\n"
    assert classify_text(text).doc_type == "unclassified"  # unrecognized before translation

    translated = _try_translate(text)
    assert translated is not None
    assert classify_text(translated).doc_type == "birth_certificate"


def test_try_translate_returns_none_for_text_that_still_wont_classify():
    # Gibberish that no language pair will translate into anything
    # matching a known pattern.
    assert _try_translate("asdf qwer zxcv") is None

def test_recognized_upright_card_is_not_replaced_by_rotated_garbage(monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from classify import classifier, ocr
    upright = "YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"
    calls = []
    def read(image):
        calls.append(image)
        return upright if len(calls) == 1 else "DATE PLACE NAME THE AND OF " * 10
    monkeypatch.setattr(ocr, "ocr_image", read)
    monkeypatch.setattr(classifier, "_page_images", lambda page: [SimpleNamespace(image=Image.new("RGB", (40, 60)))])
    assert classifier._ocr_page_images(object()) == upright
    assert len(calls) == 1


def test_rotated_card_recovery_prefers_recognizable_document(monkeypatch):
    from types import SimpleNamespace
    from PIL import Image
    from classify import classifier, ocr
    texts = iter(["garbled unreadable", "YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"])
    monkeypatch.setattr(ocr, "ocr_image", lambda image: next(texts))
    monkeypatch.setattr(classifier, "_page_images", lambda page: [SimpleNamespace(image=Image.new("RGB", (40, 60)))])
    assert classifier.classify_text(classifier._ocr_page_images(object())).doc_type == "ssn_card"
@pytest.mark.parametrize("table", [
    "Date Type Location\n2026-01-01 Arrival BOS\n2026-02-01 Departure JFK",
    "Row Date Type Location\n1 2026-01-01 Arrival BOS",
    "Row Date Type Location\nNo records found",
    "Arrivals / Departures\n2026-01-01 BOS",
])
def test_travel_history_results_remains_distinct_from_latest_i94(table):
    text = "10/5/26, 10:21 AM I-94/I-95 Official Website\nU.S. Customs and Border Protection\nTravel History Results\n" + table
    assert classify_text(text).doc_type == "travel_history"
    assert classify_text("Most Recent I-94\nAdmission I-94 Record Number\nClass of Admission: B2").doc_type == "i94"
    assert classify_text(text + "\nForm I-485 Edition 09/18/26 Page 1 of 24").doc_type == "i485"


def test_history_title_without_table_evidence_is_not_recognized_as_history():
    assert classify_text("Please retrieve Travel History Results for review.").doc_type != "travel_history"
