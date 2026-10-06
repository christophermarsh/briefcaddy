"""Visa extractor (src/extract/visa.py). Text mirrors the real example
case's visa stamp layout: "Passport Number Sex Birth Date Nationality"
as one table header row, values on the row below."""

from extract.visa import extract


def _by_key(fields, key):
    return next(f for f in fields if f.fact_key == key)


def test_visa_extracts_sex_and_dob():
    text = (
        "Passport Number Sex Birth Date Nationality\n"
        "F0516185 F 20FEB2002 BRZL\n"
        "Entries Issue Date Expiration Date\n"
        "1 10Nov2015 08Nov2025\n"
    )
    fields = extract(text)
    assert _by_key(fields, "applicant.sex").normalized_value == "F"
    assert _by_key(fields, "applicant.dob").normalized_value == "2002-02-20"


def test_visa_sex_does_not_false_match_the_leading_f_in_a_passport_number():
    # Real bug risk: "F0516185" starts with the letter "F" -- the word-
    # boundary requirement in find_sex (base.py) must skip that and find
    # only the standalone "F" token.
    text = "Passport Number Sex Birth Date Nationality\nF0516185 M 20FEB2002 BRZL\n"
    fields = extract(text)
    assert _by_key(fields, "applicant.sex").normalized_value == "M"


def test_visa_returns_nothing_without_recognizable_fields():
    assert extract("This is not a visa at all.") == []
