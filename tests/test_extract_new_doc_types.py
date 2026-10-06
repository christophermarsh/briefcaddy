"""Independent fictional document text; no client-derived scenario corpus."""
from classify import classify_text
from extract import extract_fields
from extract.work_permit import check_digit

NTA = """NOTICE TO APPEAR
DOB: 04/08/1990

X You are an alien present in the United States who has not been admitted or paroled.

The Department alleges that you:
1. You are a native of BRAZIL and a citizen of BRAZIL.

On the basis of the foregoing, it is charged that this fictional example requires review.
"""
DOCKET = """CRIMINAL DOCKET
9999CR000002
Defendant: Taylor Fictional

1 123/EXAMPLE FICTIONAL TRAINING COUNT c123 § 1 04/08/2017

This fictional example mentions dismissal; it is not evidence of guilt.
"""

def _facts(fields):
    return {field.fact_key: field.normalized_value for field in fields}

def test_check_digit_matches_icao_7_3_1():
    assert check_digit("123456789") == 7

def test_notice_to_appear():
    assert classify_text(NTA).doc_type == "notice_to_appear"
    values = _facts(extract_fields("notice_to_appear", NTA))
    assert values["applicant.nta_present"] == "Yes"
    assert values["applicant.nta_admission_status"] == "not_admitted_or_paroled"
    assert values["applicant.country_of_birth"] == "BRAZIL"
    assert values["applicant.dob"] == "1990-04-08"

def test_criminal_docket_records_charges_not_guilt():
    assert classify_text(DOCKET).doc_type == "criminal_record"
    values = _facts(extract_fields("criminal_record", DOCKET))
    assert values["applicant.criminal_record_present"] == "Yes"
    assert values["applicant.criminal_docket_number"] == "9999CR000002"
    assert "FICTIONAL TRAINING COUNT (2017-04-08)" == values["applicant.criminal_offenses"]
    assert "applicant.criminal_conviction" not in values
