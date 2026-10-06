"""Shared extractor helpers (src/extract/base.py) not already covered
indirectly through a specific extractor's own tests."""

from extract.base import cm_to_feet_inches, find_sex, find_value_after_label, kg_to_lbs


def test_cm_to_feet_inches_converts_a_real_example():
    # Real example case: the Italian national ID's "STATURA" field.
    assert cm_to_feet_inches(160) == "5'3\""


def test_cm_to_feet_inches_rounds_to_nearest_inch():
    assert cm_to_feet_inches(180) == "5'11\""


def test_kg_to_lbs_converts_and_rounds():
    assert kg_to_lbs(70) == 154
    assert kg_to_lbs(54.4) == 120


def test_find_value_after_label_finds_a_value_on_the_next_line():
    text = "STATURA/HEIGHT/TAILLE(12)\n160\n"
    assert find_value_after_label(text, r"STATURA[^\n]*", r"\d{2,3}") == "160"


def test_find_value_after_label_label_must_consume_its_own_trailing_digits():
    # A bare "STATURA" label match ends right before "(12)" -- the item
    # number in the real label text -- so the search window's first
    # digit-shaped match is "12", not the real value on the next line.
    # This is exactly why src/extract/passport.py uses "STATURA[^\\n]*",
    # not a bare "STATURA".
    text = "STATURA/HEIGHT/TAILLE(12)\n160\n"
    assert find_value_after_label(text, "STATURA", r"\d{2,3}") == "12"


def test_find_value_after_label_returns_none_when_label_is_absent():
    assert find_value_after_label("no relevant label here", "STATURA", r"\d{2,3}") is None


def test_find_value_after_label_returns_none_when_value_pattern_never_matches():
    assert find_value_after_label("STATURA\nnot a number\n", "STATURA", r"\d{2,3}") is None


def test_find_value_after_label_respects_the_window():
    text = "STATURA" + ("x" * 200) + "160"
    assert find_value_after_label(text, "STATURA", r"\d{2,3}", window=50) is None


def test_find_sex_finds_standalone_marker_on_the_next_line():
    # Real example case: Italian national ID's combined label line.
    text = "Sesso. Sex.Sexe. (5) Luogodinascita, Placeof bith. Lieu denaissance.(6)\nF VITORIA (BRA)\n"
    assert find_sex(text) == "F"


def test_find_sex_does_not_false_match_a_passport_number_starting_with_f():
    # Real example case: the visa's table row "F0516185 F 20FEB2002 BRZL"
    # -- the word-boundary requirement must skip the "F" glued into the
    # passport number and find only the standalone one.
    text = "Passport Number Sex Birth Date Nationality\nF0516185 F 20FEB2002 BRZL\n"
    assert find_sex(text) == "F"


def test_find_sex_returns_none_without_a_sex_label():
    assert find_sex("no relevant label here") is None
