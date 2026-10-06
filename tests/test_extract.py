"""Per-doc_type extractors (docs/ARCHITECTURE.md section 2). Values here
match the ones already used in tests/test_factgraph.py so the two test
suites describe the same example case consistently (same I-94 number,
same admit-until date, same eye-color conflict)."""

from extract import extract_fields
from extract.birth_certificate import extract as extract_birth_certificate
from extract.drivers_license import extract as extract_drivers_license
from extract.i94 import extract as extract_i94
from extract.i360_approval import extract as extract_i360_approval
from extract.passport import extract as extract_passport
from extract.ssn_card import extract as extract_ssn_card


def test_birth_parent_rows_separated_from_heading_remain_unattributed():
    from extract.birth_certificate import extract
    text = """REPUBLICA FEDERATIVA DO BRASIL
CERTIDAO DE NASCIMENTO
ANA FICCAOO DOS FICCAOK, natural de Contagem - MG
[FICCAOL FICCAOG DA FICCAOB, natural de Belo Horizonte - MG
DATA DE NASCIMENTO
FILIAGAO
REGISTRO CIVIL DO SUBDISTRITO
"""
    fields = {f.fact_key: f.normalized_value for f in extract(text)}
    assert not any("birth_cert.parent_" in key for key in fields)
    assert not any(key in fields for key in ("applicant.birth_cert.mother_name", "applicant.birth_cert.father_name"))



def _by_key(fields, key):
    return next(f for f in fields if f.fact_key == key)


def test_i94_extracts_all_five_fields():
    text = (
        "Admission (I-94) Record Number: 14335150685\n"
        "Most Recent Date of Entry: 02/21/2015\n"
        "Class of Admission: B2\n"
        "Admit Until Date: 02/21/2017\n"
        "Citizenship: Brazil\n"
    )
    fields = extract_i94(text)
    assert _by_key(fields, "applicant.i94_number").normalized_value == "14335150685"
    assert _by_key(fields, "applicant.i94_admit_until_date").normalized_value == "2017-02-21"
    assert _by_key(fields, "applicant.i94_arrival_date").normalized_value == "2015-02-21"
    assert _by_key(fields, "applicant.i94_class_of_admission").normalized_value == "B2"
    assert _by_key(fields, "applicant.citizenship").normalized_value == "BRAZIL"


def test_i94_skips_missing_fields_instead_of_guessing():
    fields = extract_i94("Admission (I-94) Record Number: 14335150685\n")
    keys = {f.fact_key for f in fields}
    assert keys == {"applicant.i94_number"}


def test_i94_skips_unparseable_dates_rather_than_emitting_a_wrong_one():
    fields = extract_i94("Admit Until Date: sometime next year\n")
    assert len(fields) == 1 and fields[0].normalized_value is None
    assert fields[0].raw_value == "sometime next year" and fields[0].reading_issues


def test_i94_extracts_dob_and_travel_document_number():
    # Real example case values: the document number used at last arrival
    # is her Italian passport, not necessarily whichever passport is
    # otherwise on file (docs/decisions.md, 2026-09-29 citizenship entry).
    text = "Birth Date: 2002 February 20\nDocument Number: YA9487599\n"
    fields = extract_i94(text)
    assert _by_key(fields, "applicant.dob").normalized_value == "2002-02-20"
    assert _by_key(fields, "applicant.travel_document_number").normalized_value == "YA9487599"


def test_i94_extracts_family_and_given_name():
    text = "Last/Surname: MOURA SAMPAIO\nFirst (Given) Name: MARIA EDUARDA\n"
    fields = extract_i94(text)
    assert _by_key(fields, "applicant.family_name").normalized_value == "MOURA SAMPAIO"
    assert _by_key(fields, "applicant.given_name").normalized_value == "MARIA EDUARDA"


def test_i360_approval_extracts_receipt_priority_and_status():
    # Real example case values, per tests/test_factgraph.py.
    text = "Receipt Number: WAC1234567890\nPriority Date: 10/14/2022\nCurrent Status: Case Approved.\n"
    fields = extract_i360_approval(text)
    assert _by_key(fields, "applicant.i360_receipt_number").normalized_value == "WAC1234567890"
    assert _by_key(fields, "applicant.i360_priority_date").normalized_value == "2022-10-14"
    assert _by_key(fields, "applicant.i360_current_status").normalized_value == "Case Approved"


def test_i360_approval_extracts_the_a_number_despite_jumbled_layout():
    # Real example case: "A201 821 016" appears right after the
    # "Petitioner"/"Beneficiary" column headers in the notice's own OCR
    # text, not adjacent to any "A-Number" label.
    text = "Receipt Number Case Type\nMSC2390000001 I360\nPetitioner A201 821 016\n10/14/2022 MOURA SAMPAIO"
    fields = extract_i360_approval(text)
    assert _by_key(fields, "applicant.a_number").normalized_value == "A201821016"


def test_ssn_card_extracts_the_printed_number():
    fields = extract_ssn_card("YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION")
    assert len(fields) == 1
    assert fields[0].fact_key == "applicant.ssn"
    assert fields[0].normalized_value == "123-45-6789"


def test_ssn_card_returns_nothing_without_an_ssn_shaped_number():
    assert extract_ssn_card("YOUR SOCIAL SECURITY CARD\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION") == []


def test_drivers_license_prefers_aamva_barcode_data_over_ocr_patterns():
    # Constructed against the published AAMVA DL/ID standard (a real
    # federal spec, not a guessed layout) -- exact structured data should
    # win over label/pattern matching when both are present, since the
    # barcode is exact and the OCR path is a fallback for when it isn't
    # (confirmed necessary for the real example case's own license scan,
    # whose barcode didn't have enough resolution to decode at all).
    text = (
        "some OCR noise that would otherwise confuse label matching\n"
        "DCSMOURA SAMPAIO\nDACMARIA EDUARDA\nDAG1500 WORCESTER RD\n"
        "DAIFRAMINGHAM\nDAJMA\nDAK01702\nDAYBRO\nDBB02202002\nDAU069 in\n"
    )
    fields = extract_drivers_license(text)
    assert _by_key(fields, "applicant.family_name").normalized_value == "MOURA SAMPAIO"
    assert _by_key(fields, "applicant.given_name").normalized_value == "MARIA EDUARDA"
    assert _by_key(fields, "applicant.address_street").normalized_value == "1500 WORCESTER RD"
    assert _by_key(fields, "applicant.address_city").normalized_value == "FRAMINGHAM"
    assert _by_key(fields, "applicant.address_state").normalized_value == "MA"
    assert _by_key(fields, "applicant.address_zip").normalized_value == "01702"
    assert _by_key(fields, "applicant.eye_color").normalized_value == "Brown"
    assert _by_key(fields, "applicant.dob").normalized_value == "2002-02-20"
    assert _by_key(fields, "applicant.height").normalized_value == "5'9\""


def test_drivers_license_aamva_height_converts_centimeters():
    fields = extract_drivers_license("DCSSMITH\nDAU173 cm\n")
    assert _by_key(fields, "applicant.height").normalized_value == "5'8\""


def test_drivers_license_falls_back_to_ocr_patterns_without_barcode_data():
    # No AAMVA field codes at all -- must fall through to the existing
    # label/pattern extraction, not return nothing.
    fields = extract_drivers_license("Eyes: BRO\nHeight: 5'-05\"\n")
    assert _by_key(fields, "applicant.eye_color").normalized_value == "Brown"


def test_drivers_license_normalizes_eye_color_abbreviation():
    # Real example case: the license read "BRO", the old manual process
    # mistyped "Black" -- this is the conflict the fact graph is built to
    # catch, so the extractor must normalize the code, not pass "BRO" raw.
    fields = extract_drivers_license("DRIVER LICENSE\nEyes: BRO\nHeight: 5'-05\"\nDOB: 02/20/2002\n")
    assert _by_key(fields, "applicant.eye_color").raw_value == "BRO"
    assert _by_key(fields, "applicant.eye_color").normalized_value == "Brown"
    assert _by_key(fields, "applicant.height").normalized_value == "5'5\""
    assert _by_key(fields, "applicant.dob").normalized_value == "2002-02-20"


def test_drivers_license_finds_eye_color_unlabeled_when_the_label_is_garbled():
    # Real example case: OCR (classify/ocr.py) reads "Eyes" as "iseves" --
    # no usable label at all -- but the code itself comes through clean.
    fields = extract_drivers_license("iseves BRO\nMA 01702\n")
    assert _by_key(fields, "applicant.eye_color").normalized_value == "Brown"
    assert _by_key(fields, "applicant.eye_color").confidence == 0.8


def test_drivers_license_prefers_the_quote_anchored_height_over_a_garbled_label_match():
    # Real bug found by actually running OCR: once "HGT" appears (from the
    # OCR pass) amid badly garbled surrounding text, a same-line label
    # capture grabbed neighboring noise ("5-0! 02/20/02" -> wrongly
    # "5'0\"") instead of the correct, quote-anchored "5-05\"" elsewhere in
    # the same text. The quote-anchored match must win regardless of where
    # the label ends up pointing.
    text = "seek sgHGT 5-0! 02/20/02\nsomething else\nos 5-05\" more junk"
    fields = extract_drivers_license(text)
    assert _by_key(fields, "applicant.height").normalized_value == "5'5\""


def test_drivers_license_accepts_compact_height_encoding():
    fields = extract_drivers_license("Height: 505\n")
    assert _by_key(fields, "applicant.height").normalized_value == "5'5\""


def test_drivers_license_skips_unrecognized_eye_color_code():
    fields = extract_drivers_license("Eyes: XYZ\n")
    assert fields == []


def test_extract_fields_dispatches_by_doc_type():
    fields = extract_fields("ssn_card", "SOCIAL SECURITY\n987-65-4321")
    assert len(fields) == 1
    assert fields[0].normalized_value == "987-65-4321"


def test_extract_fields_returns_empty_for_unwired_doc_type():
    # translation_certification is a real doc_type in
    # src/classify/patterns.py but has no extractor -- should not raise.
    assert extract_fields("translation_certification", "TRANSLATION CERTIFICATION") == []


def test_birth_certificate_extracts_parents_place_and_country_of_birth():
    # Real example case: the certified English translation.
    text = (
        "FEDERATIVE REPUBLIC OF BRAZIL\nBIRTH CERTIFICATE\n"
        "Date and Time of Birth FEBRUARY 20, 2002 ~ (02/20/2002) \x97 09:20 PM\n"
        "Father's Name MARCONDES DE LAUSANNE SAMPAIO\n"
        "Mother's Name SABRINA MOURA ALMEIDA SAMPAIO\n"
        "Gender FEMALE\n"
        "City and State of Birth VITORIA - ESPIRITO SANTO\n"
    )
    fields = extract_birth_certificate(text)
    assert _by_key(fields, "applicant.dob").normalized_value == "2002-02-20"
    assert _by_key(fields, "applicant.sex").normalized_value == "F"
    assert _by_key(fields, "applicant.birth_cert.father_name").normalized_value == "MARCONDES DE LAUSANNE SAMPAIO"
    assert _by_key(fields, "applicant.birth_cert.mother_name").normalized_value == "SABRINA MOURA ALMEIDA SAMPAIO"
    assert _by_key(fields, "applicant.birth_city").normalized_value == "VITORIA"
    assert _by_key(fields, "applicant.birth_state").normalized_value == "ESPIRITO SANTO"
    assert _by_key(fields, "applicant.country_of_birth").normalized_value == "BRAZIL"


def test_birth_certificate_skips_country_of_birth_without_a_known_header():
    fields = extract_birth_certificate("Father's Name JOHN DOE\n")
    keys = {f.fact_key for f in fields}
    assert "applicant.country_of_birth" not in keys


def test_passport_extracts_brazilian_nationality():
    text = "NACIONALIDADE / NATIONALITY\nBRASILEIRO(A)\n"
    fields = extract_passport(text)
    assert len(fields) == 1
    assert fields[0].fact_key == "applicant.citizenship"
    assert fields[0].normalized_value == "BRAZIL"


def test_passport_extracts_italian_nationality():
    text = "Cittadinanza. Nationality. Nationalité. (3)\nITALIANA\n"
    fields = extract_passport(text)
    assert fields[0].normalized_value == "ITALY"


def test_passport_returns_nothing_for_an_unrecognized_nationality():
    assert extract_passport("Nationality\nCANADIAN\n") == []


def test_passport_extracts_height_and_eye_color_from_italian_national_id():
    # Real example case: an Italian national ID card (classified as
    # "passport" here since it carries the same MRZ format) reports height
    # in centimeters and eye color as an Italian word.
    text = (
        "Cittadinanza. Nationality. Nationalité. (3)\nITALIANA\n"
        "STATURA/HEIGHT/TAILLE(12)\n160\n"
        "COLOREDEGLIOCCHI/COLOUROFEVES/COULEURDESYEUX(13)\nMARRONI\n"
        "Sesso. Sex.Sexe. (5) Luogodinascita, Placeof bith. Lieu denaissance.(6)\nF VITORIA (BRA)\n"
    )
    fields = extract_passport(text)
    height = next(f for f in fields if f.fact_key == "applicant.height")
    eye_color = next(f for f in fields if f.fact_key == "applicant.eye_color")
    sex = next(f for f in fields if f.fact_key == "applicant.sex")
    assert height.normalized_value == "5'3\""
    assert eye_color.normalized_value == "Brown"
    assert sex.normalized_value == "F"


def test_passport_skips_height_and_eye_color_when_absent():
    fields = extract_passport("ITALIANA\n")
    keys = {f.fact_key for f in fields}
    assert keys == {"applicant.citizenship"}
