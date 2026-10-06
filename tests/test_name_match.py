"""The conflict search's matching rules (src/name_match.py), one by one, with the cases that must NOT match said as plainly as the ones that must.
Every name here is made up ("Ana Clara Exemplo Souza", "Rosa Exemplo", "Jean Pierre Egzanp")."""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path

import pytest

import name_match as nm
from name_match import Name, Person, score
import schema_path

REPO = Path(__file__).resolve().parent.parent


def who(full=None, *, given=None, family=None, dob=None, a=None, passport=None, also=()) -> Person:
    names = [n for n in [Name.parse(given, family, full), *(Name.parse(full=x) for x in also)] if n]
    return Person(names, [nm.parse_date(dob)] if dob else [], {nm.a_number(a)} - {""} if a else set(), {nm.passport(passport)} - {""} if passport else set())


def hit(a: Person, b: Person) -> tuple[int, str, str] | None:
    s = score(a, b)
    return (s.score, s.strength, s.sentence) if s else None


# -- rules 1 and 2: numbers -------------------------------------------------------------------------------------------------------


def test_the_same_a_number_in_any_writing_is_strong_whatever_the_names():
    assert nm.a_number("A-012 345 678") == nm.a_number("a12345678") == nm.a_number("012345678") == "012345678"
    assert hit(who("Rosa Exemplo", a="A-012-345-678"), who("Someone Else Entirely", a="12345678")) == (100, "strong", "The same A-Number.")
    assert nm.a_number("12345") == "" and nm.a_number("A1234567890") == ""  # too short, too long: not an A-Number


def test_the_same_passport_number_is_strong():
    assert hit(who("Ana Exemplo", passport="fz 123 456"), who("Ana Exemplo Souza", passport="FZ123456")) == (100, "strong", "The same passport number.")
    assert nm.passport("12345") == "" and nm.passport("ABCDEF") == ""  # six or more, with a digit


def test_different_numbers_alone_are_no_match():
    assert hit(who("Rosa Exemplo", a="A012345678"), who("Lia Segura", a="A099999999")) is None


# -- rule 3: folding ------------------------------------------------------------------------------------------------------------


def test_accents_capitals_hyphens_and_particles_are_folded_away():
    assert nm.words("Conceição da Silva-Souza") == ["CONCEICAO", "SILVA", "SOUZA"]
    assert nm.words("María del Carmen de los Santos") == ["MARIA", "CARMEN", "SANTOS"]
    assert hit(who("JOÃO DOS SANTOS", dob="2001-02-03"), who("Joao Santos", dob="2001-02-03"))[0] == 95


def test_a_word_written_as_one_or_as_two_is_the_same():
    s = hit(who("Jean-Baptiste Egzanp"), who("Jeanbaptiste Egzanp"))
    assert s and "one word written as two" in s[2]


# -- rule 4: words ---------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("a,b,how", [
    ("SOUZA", "SOUSA", "sound"), ("LUIS", "LUIZ", "sound"), ("HENRIQUE", "ENRIQUE", "sound"), ("GONZALEZ", "GONZALES", "sound"),
    ("TEREZA", "TERESA", "sound"), ("THIAGO", "TIAGO", "sound"), ("HELENA", "ELENA", "sound"), ("RODRIGUEZ", "RODRIGUES", "sound"),
    ("ZE", "JOSE", "nickname"), ("CHICO", "FRANCISCO", "nickname"), ("PACO", "FRANCISCO", "nickname"), ("LUPE", "GUADALUPE", "nickname"),
    ("BETO", "ROBERTO", "nickname"), ("BETO", "ALBERTO", "nickname"), ("NACHO", "IGNACIO", "nickname"), ("BIA", "BEATRIZ", "nickname"),
    ("MANUEL", "MANOEL", "spelling"), ("VITOR", "VICTOR", "spelling"),
    ("JAN", "JEAN", "creole"), ("PYE", "PIERRE", "creole"), ("JAK", "JACQUES", "creole"), ("LWI", "LOUIS", "creole"), ("FRANSWA", "FRANCOIS", "creole"),
    ("MOHAMMED", "MUHAMMAD", "transliteration"), ("YOUSSEF", "YUSUF", "transliteration"), ("SERGEI", "SERGEY", "transliteration"),
    ("CRISTIANO", "CHRISTIANO", "typo"), ("FERNANDEZ", "FERANNDEZ", "typo"), ("ALEXANDRINA", "ALEXANDIRNA", "typo"),
])
def test_two_words_are_one_name(a, b, how):
    assert nm.same_word(a, b) == how and nm.same_word(b, a) == how


@pytest.mark.parametrize("a,b", [
    ("FERNANDA", "FERNANDO"), ("DANIEL", "DANIELA"), ("GABRIEL", "GABRIELE"), ("PAULA", "PAULO"), ("LUIS", "LUISA"),  # a final a, o or e: two people
    ("MARIA", "MARTA"), ("ROSA", "ROSE"), ("ANA", "ANE"),  # short words get no slip
    ("ROBERTO", "ALBERTO"),  # two names that share a nickname are not each other
    ("GUILHERME", "GUILLERMO"),  # the Portuguese and the Spanish name are not merged by a rule
    ("JOSE", "JOSEPH"),  # a name and its translation are two names
    ("SILVA", "SOUZA"),
])
def test_two_words_are_not_one_name(a, b):
    assert nm.same_word(a, b) is None


def test_the_lists_fold_like_the_names_and_every_group_has_two_names():
    data = json.loads((schema_path.path("register", "name_variants")).read_text(encoding="utf-8"))
    assert set(nm.KINDS) <= set(data) and data["_source"]
    for kind in nm.KINDS:
        for group in data[kind]:
            assert len(group) >= 2 and len({nm.fold(n) for n in group}) == len(group), group
    assert "PYE" in nm.variants()["creole"]["PIERRE"]  # Pyè, its accent folded


# -- rule 5: names ------------------------------------------------------------------------------------------------------------------


def test_the_same_name_with_the_same_birth_date_is_strong():
    assert hit(who("Ana Clara Exemplo Souza", dob="2006-03-14"), who(given="Ana Clara", family="Exemplo Souza", dob="03/14/2006")) == (
        95, "strong", "The names match and the birth date agrees.")


def test_two_surnames_in_the_other_order():
    assert hit(who("Ana Clara Souza Exemplo", dob="2006-03-14"), who(given="Ana Clara", family="Exemplo Souza", dob="2006-03-14")) == (
        95, "strong", "The surnames match in the other order and the birth date agrees.")


def test_a_married_surname_added_or_one_of_two_surnames_dropped():
    s = hit(who("Maria Exemplo Lima", dob="1984-05-02"), who("Maria Exemplo Lima Teste", dob="1984-05-02"))
    assert s[0] == 85 and s[1] == "strong" and s[2].startswith("A name is added or dropped on one of them (a married name")
    assert hit(who("Ana Clara Exemplo Souza", dob="2006-03-14"), who("Ana Clara Souza", dob="2006-03-14"))[0] == 85


def test_the_given_name_and_one_surname_with_the_date_is_possible_not_strong():
    s = hit(who("Rosa Exemplo Lima", dob="1990-01-02"), who("Rosa Exemplo Teste", dob="1990-01-02"))
    assert s == (70, "possible", "The given name and one surname match and the birth date agrees.")


def test_a_nickname_creole_and_transliterated_name_say_so():
    assert "allowing a nickname" in hit(who("Ze Exemplo Lima", dob="1980-05-04"), who("Jose Exemplo Lima", dob="1980-05-04"))[2]
    s = hit(who("Jan Pyè Egzanp", dob="1975-07-08"), who("Jean Pierre Egzanp", dob="1975-07-08"))
    assert s[0] == 95 and "the Creole and French spellings of one name" in s[2]
    s = hit(who("Muhammad Exemplo", dob="1990-02-02"), who("Mohammed Exemplo", dob="1990-02-02"))
    assert s[0] == 95 and "another alphabet" in s[2]
    s = hit(who("Ana Clara Exemplo Sousa", dob="2006-03-14"), who("Ana Clara Exemplo Souza", dob="2006-03-14"))
    assert s[0] == 95 and "a spelling variant" in s[2]


def test_a_typing_slip_counts_but_less():
    s = hit(who("Cristiano Exemplo", dob="1999-09-09"), who("Christiano Exemplo", dob="1999-09-09"))
    assert s[0] == 85 and "a one-letter typing slip" in s[2]


def test_the_family_name_first_with_a_comma_is_read_as_such():
    assert hit(who("Exemplo Souza, Ana Clara", dob="2006-03-14"), who("Ana Clara Exemplo Souza", dob="2006-03-14"))[0] == 95


def test_the_same_words_in_another_order_are_low():
    s = hit(who("Souza Ana", dob="2000-01-01"), who("Ana Souza", dob="2000-01-01"))
    assert s[0] == 70 and s[1] == "possible" and "the same words of the name in another order" in s[2].lower()
    assert hit(who("Maria Jose Exemplo"), who("Jose Maria Exemplo"))[1] == "weak"  # two people, likely: never more than weak without a date


def test_any_spelling_the_case_carries_can_match():
    case = who(given="Ana Clara", family="Exemplo Souza", dob="2006-03-14", also=["Ana Clara Exemplo"])
    assert hit(who("Ana Clara Exemplo", dob="2006-03-14"), case)[0] == 95


# -- the explicit non-matches and false positives ---------------------------------------------------------------------------------


def test_a_common_name_and_no_date_of_birth_is_weak_never_strong():
    s = hit(who("Maria Silva"), who("Maria Silva"))
    assert s == (45, "weak", "The names match; there is no date of birth to compare, so this is a weak hit.")
    assert hit(who("Maria da Silva", dob="1990-01-01"), who("Maria Silva"))[1] == "weak"  # a date on one side only


def test_the_same_common_name_born_on_different_days_is_two_people():
    s = hit(who("Maria Silva", dob="1990-01-01"), who("Maria Silva", dob="1991-06-15"))
    assert s == (20, "weak", "The names match, but the birth dates differ.")
    assert hit(who("Maria Silva Lima", dob="1990-01-01"), who("Maria Silva Souza", dob="1991-06-15")) is None


def test_a_shared_surname_alone_or_a_given_name_alone_is_no_match():
    assert hit(who("Pedro Exemplo"), who("Lucia Exemplo")) is None
    assert hit(who("Maria Lima"), who("Maria Souza")) is None
    assert hit(who("Fernanda Exemplo Lima", dob="1990-01-01"), who("Fernando Exemplo Lima", dob="1990-01-01")) is None  # sister and brother


def test_the_same_name_and_date_with_different_a_numbers_is_at_most_possible():
    s = hit(who("Rosa Exemplo", dob="1990-03-04", a="A011111111"), who("Rosa Exemplo", dob="1990-03-04", a="A022222222"))
    assert s[0] == 60 and s[1] == "possible" and s[2].endswith("but the A-Numbers differ.")


def test_the_sentence_never_repeats_a_name_a_date_or_a_number():
    for a, b in ((who("Ana Clara Exemplo Souza", dob="2006-03-14"), who("Ana Clara Souza Exemplo", dob="2006-04-03")),
                 (who("Ze Exemplo", a="A012345678"), who("Jose Exemplo", a="012345678"))):
        sentence = score(a, b).sentence
        assert not any(w in sentence.upper() for w in ("ANA", "CLARA", "SOUZA", "EXEMPLO", "JOSE")) and not any(ch.isdigit() for ch in sentence)


# -- rule 6: dates -----------------------------------------------------------------------------------------------------------------


def test_a_date_agrees_on_the_day_or_with_day_and_month_swapped_in_the_same_year():
    assert nm.dates_agree(date(2006, 3, 4), date(2006, 3, 4)) == "same"
    assert nm.dates_agree(date(2006, 3, 4), date(2006, 4, 3)) == "swapped"
    assert nm.dates_agree(date(2006, 3, 4), date(2007, 4, 3)) is None  # another year: not the same person by the date
    assert nm.dates_agree(date(2006, 3, 4), date(2006, 3, 5)) is None
    s = hit(who("Ana Clara Exemplo Souza", dob="2006-03-04"), who("Ana Clara Exemplo Souza", dob="04/03/2006"))  # 04/03 read as April 3rd
    assert s == (90, "strong", "The names match and the birth date agrees with the day and month swapped.")


def test_dates_the_screen_writes_and_dates_that_are_not():
    assert nm.parse_date("03/14/2006") == date(2006, 3, 14) == nm.parse_date("2006-03-14")
    assert nm.parse_date("14/03/2006") is None and nm.parse_date("tomorrow") is None and nm.parse_date("") is None


# -- strength thresholds -----------------------------------------------------------------------------------------------------------


def test_strong_possible_and_weak():
    assert nm.Score(80, "x").strength == "strong" and nm.Score(79, "x").strength == "possible" and nm.Score(50, "x").strength == "possible"
    assert nm.Score(49, "x").strength == "weak"


# -- the order of the rules (the H2 verification) ------------------------------------------------------------------------------------


def test_the_lists_win_over_the_final_a_o_e_rule():
    assert nm.same_word("MARIE", "MARI") == "creole" and nm.same_word("ISABEL", "ISABELLE") == "spelling"
    s = hit(who("Mari Lourdes Egzanp", dob="1980-01-02"), who("Marie Lourdes Egzanp", dob="1980-01-02"))
    assert s[0] == 95 and "the Creole and French spellings of one name" in s[2]


@pytest.mark.parametrize("a,b", [("JEAN", "JEANNE"), ("MICHEL", "MICHELLE"), ("DANIEL", "DANIELLE"), ("EMMANUEL", "EMMANUELLE"), ("YVON", "YVONNE")])
def test_the_final_a_o_e_rule_wins_over_the_sound_rule(a, b):
    assert nm.same_word(a, b) is None
    assert hit(who(f"{a} Egzanp", dob="1990-05-06"), who(f"{b} Egzanp", dob="1990-05-06")) is None


@pytest.mark.parametrize("a,b", [("Rosa Hernandez", "Rosa Fernandez"), ("Mariana Exemplo", "Marina Exemplo"), ("Rosa Castro", "Rosa Castor"),
                                 ("Hanna Exemplo", "Ana Exemplo")])
def test_false_positives_with_the_same_birth_date_are_gone(a, b):
    """A slip never in the first letter, only in words of seven letters or more; the sound rule needs four letters on each side."""
    assert hit(who(a, dob="1990-05-06"), who(b, dob="1990-05-06")) is None


def test_an_apostrophe_a_slavic_surname_a_romanized_letter_and_an_initial():
    assert nm.words("Ana D'Avila") == nm.words("Ana Davila") == ["ANA", "DAVILA"]
    s = hit(who("Olga Ivanova", dob="1970-03-04"), who("Olga Ivanov", dob="1970-03-04"))
    assert s and s[1] == "strong" and "the man's and the woman's form of one surname" in s[2]
    assert nm.words("Taras Ševčenko") == nm.words("Taras Shevchenko")
    s = hit(who("Ana C. Souza", dob="2006-03-14"), who(given="Ana Clara", family="Exemplo Souza", dob="2006-03-14"))
    assert s[0] == 85 and "an initial for a name" in s[2]
    assert hit(who("Ana C. Souza", dob="2006-03-14"), who("Ana Clara Souza", dob="2006-03-14"))[0] == 95


def test_the_known_limits_stay_misses():
    """Recorded in docs/attorney_review.md as known limits: a name and its translation, and a slip in a short word."""
    for a, b in (("RAQUEL", "RACHEL"), ("JORGE", "GEORGE"), ("ROSA", "RSOA")):
        assert nm.same_word(a, b) is None


def test_the_sentence_for_a_one_word_name_and_a_second_given_name():
    assert hit(who("Ana", dob="2000-01-01"), who("Ana", dob="2000-01-01"))[2] == "The one word each name has is the same and the birth date agrees."
    s = hit(who("Marie-Ange Egzanp", dob="1985-02-03"), who("Marie Egzanp", dob="1985-02-03"))
    assert s[2] == "A second given name is added or dropped on one of them and the birth date agrees."
