"""Does this person match someone on another case? The scoring behind the conflict search (src/conflicts.py), read against the people index
(src/people.py: one row per person per case, every spelling of their name the case carries, their dates of birth and numbers).

The rules, in order (docs/decisions.md, the H2 entry, says the same in words; tests/test_name_match.py tests each one, and the cases that must NOT match):

  1. The same A-Number: any spacing or dashes, with or without the A, seven to nine digits read as nine with leading zeros. Strong.
  2. The same passport number: letters and digits, upper case, six characters or more. Strong.
  3. Names are folded before they are compared: accents and capitals gone, a hyphen is a space, an apostrophe joins (D'Avila is DAVILA),
     Š, Č and Ž are written SH, CH and ZH (Ševčenko is Shevchenko), and the particles de, da, do, dos, das, del, di, du, la, las, los, e, y,
     van, von are dropped ("Maria da Silva-Souza" is MARIA SILVA SOUZA). A word written as one in one name and as two in the other
     ("Jeanbaptiste" and "Jean Baptiste") counts as the same. A hyphenated first name ("Marie-Ange") is two given names.
  4. Two words are the same name by the first of these that applies, in this order, so each one wins over the ones after it:
     a. equal after folding;
     b. the lists (schemas/registers/name_variants.json): a known nickname in Portuguese or Spanish (Ze and Jose, Chico and Francisco), two listed
        spellings of one name (Manuel and Manoel), the Creole and French forms (Jan and Jean, Mari and Marie), two spellings from another
        alphabet (Mohammed and Muhammad);
     c. for a given name, never when they differ only by a final a, o or e or an added one, double letters made single first (Fernanda and
        Fernando, Daniel and Daniela, Jean and Jeanne, Michel and Michelle are two people);
     d. for a surname, the same word with an added final a is the man's and the woman's form of one surname (Ivanov and Ivanova);
     e. they sound the same by the spelling rules below, four letters or more on each side (Sousa and Souza, Luis and Luiz, Henrique and
        Enrique; Ana and Hanna are two names);
     f. one typing slip apart (one letter added, dropped, changed or two swapped) in words of seven letters or more, two in ten or more, never
        in the first letter (Maria and Marta, Marina and Mariana, Castro and Castor, Hernandez and Fernandez are two names).
     A one-letter word (a middle initial, "Ana C. Souza") stands for a word that starts with that letter.
  5. The given name: the first given word must be the same name (rule 4). The surnames, and any other given words: every word of one name
     paired with a word of the other. All paired: the names match (in the same order, or "in the other order"). All of one paired and the
     other has more: a name added or dropped (a married name, one of two surnames left off, a second given name). Some paired on each side:
     the given name and one surname match. Nothing but the given name: not a match. When the first words differ but every word of each name
     pairs with one of the other ("Souza Ana" typed for Ana Souza): the same words in another order, counted as low as the given name and one
     surname, since Maria Jose and Jose Maria are two people.
  6. A date of birth agrees when it is the same day; or when the day and the month are swapped and the year is the same (the month-first and
     day-first confusion the firm's own documents show: 03/04 and 04/03).
  7. The score: a number, 100. A name with a date of birth that agrees: 95 (same name), 85 (a name added or dropped), 70 (the given name
     and one surname); 5 less when the day and month were swapped, 10 less when a word matched only as a typing slip. A name with no date of birth on one side: 45, 35, 25 (a weak hit: two
     different people can share a common name). A name whose dates of birth differ: 20 for the same name, nothing otherwise. A name and a
     date that agree but A-Numbers that differ: at most 60. Strong is 80 and up, possible 50 to 79, weak below 50.

Every score comes with one sentence that says why, in words ("The same A-Number." "The surnames match in the other order and the birth
date agrees."). The sentence never repeats a name, a date or a number: the screen shows those only to someone who may open the case.

Only the standard library: the folding is unicodedata's.
"""

from __future__ import annotations

import json
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date
from functools import lru_cache
from pathlib import Path
from typing import Any, Iterable
import schema_path

REPO = Path(__file__).resolve().parent.parent
VARIANTS = schema_path.path("register", "name_variants")
PARTICLES = frozenset({"DE", "DA", "DO", "DOS", "DAS", "DEL", "DI", "DU", "LA", "LAS", "LOS", "E", "Y", "VAN", "VON", "D"})
STRONG, POSSIBLE = 80, 50
KINDS = ("nickname", "spelling", "creole", "transliteration")  # the lists in schemas/registers/name_variants.json

# How two words were found to be the same name, and the words a sentence uses for it (rule 4)
HOW = {"same": "", "sound": "a spelling variant", "nickname": "a nickname", "spelling": "two spellings of one name",
       "creole": "the Creole and French spellings of one name",
       "transliteration": "two spellings of one name from another alphabet", "typo": "a one-letter typing slip", "joined": "one word written as two",
       "family_form": "the man's and the woman's form of one surname", "initial": "an initial for a name"}


# -- folding ----------------------------------------------------------------------------------------------------------


def fold(text: Any) -> str:
    """Capitals, no accents, letters and spaces only: "Conceição da Silva-Souza" -> "CONCEICAO DA SILVA SOUZA"."""
    text = str(text or "")
    for letter, latin in (("Š", "SH"), ("š", "sh"), ("Č", "CH"), ("č", "ch"), ("Ž", "ZH"), ("ž", "zh")):  # Ševčenko is Shevchenko (the usual romanization)
        text = text.replace(letter, latin)
    text = unicodedata.normalize("NFKD", text).upper()
    text = "".join(c for c in text if not unicodedata.combining(c))
    text = text.replace("ß", "SS").replace("Æ", "AE").replace("Ø", "O").replace("Œ", "OE")
    text = re.sub(r"['’‘`´]", "", text)  # an apostrophe joins: D'Avila is DAVILA
    return " ".join(re.sub(r"[^A-Z]+", " ", text).split())


def words(text: Any) -> list[str]:
    """The name's words after folding, particles dropped (rule 3)."""
    return [w for w in fold(text).split() if w not in PARTICLES]


# Spelling rules for Portuguese, Spanish, French and Creole names (rule 4, "sound the same"). Applied in this order to a folded word.
_SOUND = [(r"^H", ""), (r"PH", "F"), (r"TH", "T"), (r"KH", "H"), (r"SCH", "X"), (r"TCH", "X"), (r"SH", "X"), (r"CH", "X"), (r"DJ", "J"),
          (r"LH", "LI"), (r"NH", "NI"), (r"QU", "K"), (r"G(?=[EIY])", "J"), (r"GU(?=[EI])", "G"), (r"C(?=[EIY])", "S"), (r"C", "K"), (r"Q", "K"),
          (r"Z", "S"), (r"Y", "I"), (r"W", "U"), (r"OU", "U"), (r"OO", "U"), (r"EE", "I"), (r"V", "B"), (r"(?<=[AEIOU])H", "")]


@lru_cache(maxsize=65536)
def sound(word: str) -> str:
    """The word as it sounds by the spelling rules above, double letters made single, a final E dropped (JOSE and JOSÉ, MARIE and MARI)."""
    w = word
    for pattern, out in _SOUND:
        w = re.sub(pattern, out, w)
    w = re.sub(r"(.)\1+", r"\1", w)
    return w[:-1] if len(w) > 3 and w.endswith("E") else w


@lru_cache(maxsize=1)
def variants() -> dict[str, dict[str, frozenset[str]]]:
    """{kind: {word: every word of its group}} from schemas/registers/name_variants.json (nicknames, Creole and French spellings, transliterations)."""
    data = json.loads(VARIANTS.read_text(encoding="utf-8"))
    out: dict[str, dict[str, frozenset[str]]] = {}
    for kind in KINDS:
        table: dict[str, set[str]] = {}
        for group in data[kind]:
            names = {fold(n).replace(" ", "") for n in group}
            for n in names:
                table.setdefault(n, set()).update(names)
        out[kind] = {k: frozenset(v) for k, v in table.items()}
    return out


def _distance(a: str, b: str, limit: int) -> int:
    """Optimal string alignment distance (one letter added, dropped, changed, or two side by side swapped), stopping past limit."""
    if abs(len(a) - len(b)) > limit:
        return limit + 1
    prev2: list[int] = []
    prev = list(range(len(b) + 1))
    for i in range(1, len(a) + 1):
        cur = [i] + [0] * len(b)
        for j in range(1, len(b) + 1):
            cost = 0 if a[i - 1] == b[j - 1] else 1
            cur[j] = min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + cost)
            if i > 1 and j > 1 and a[i - 1] == b[j - 2] and a[i - 2] == b[j - 1]:
                cur[j] = min(cur[j], prev2[j - 2] + 1)
        if min(cur) > limit:
            return limit + 1
        prev2, prev = prev, cur
    return prev[-1]


def _gender_pair(a: str, b: str) -> bool:
    """Fernanda / Fernando, Daniel / Daniela, Gabriel / Gabriele: the same stem with a different final a, o or e is two people."""
    if a[:-1] == b[:-1] and a[-1] != b[-1] and {a[-1], b[-1]} <= set("AOE"):
        return True
    short, long_ = sorted((a, b), key=len)
    return len(long_) == len(short) + 1 and long_.startswith(short) and long_[-1] in "AOE"


def _single(word: str) -> str:
    return re.sub(r"(.)\1+", r"\1", word)


@lru_cache(maxsize=262144)
def same_word(a: str, b: str, given: bool = True) -> str | None:
    """How two folded words are the same name (a key of HOW), or None (rule 4). In this order, so each rule wins over the ones after it:
    equal; the lists (Marie and Mari, a nickname); for a given name, the final a/o/e rule (Jean and Jeanne, Michel and Michelle: double letters
    made single first, so the sound rule can never undo it); for a surname, the man's and the woman's form (Ivanov and Ivanova); the sound rule
    (four letters or more on each side: Ana and Hanna are two names); a typing slip (never in the first letter: Hernandez and Fernandez are two
    names; seven letters or more: Marina and Mariana, Castro and Castor are two names). given: the word is a given name (else a surname)."""
    if a == b:
        return "same"
    if not a or not b:
        return None
    table = variants()
    for kind in KINDS:
        if b in table[kind].get(a, ()):
            return kind
    if given and (_gender_pair(a, b) or _gender_pair(_single(a), _single(b))):
        return None
    if not given:
        short, long_ = sorted((a, b), key=len)
        if long_ == short + "A" and len(short) >= 4:
            return "family_form"
    if len(a) >= 4 and len(b) >= 4 and sound(a) == sound(b):
        return "sound"
    shortest = min(len(a), len(b))
    limit = 2 if shortest >= 10 else 1 if shortest >= 7 else 0  # MARIA and MARTA, MARINA and MARIANA are two names, not a slip
    if limit and a[0] == b[0] and _distance(a, b, limit) <= limit:
        return "typo"
    return None


# -- names -----------------------------------------------------------------------------------------------------------


@dataclass(frozen=True)
class Name:
    """A person's name as a list of folded words: the given words first. given: how many of the words are given names (1 when only a full
    name is known and nothing says where the given name ends: the first word)."""

    words: tuple[str, ...]
    given: int = 1

    @classmethod
    def parse(cls, given: Any = None, family: Any = None, full: Any = None) -> "Name | None":
        """From a given name and a family name, or a full name ("Ana Clara Exemplo Souza"; "Souza, Ana Clara" is the family name first)."""
        if given or family:
            g, f = words(given), words(family)
            return cls(tuple(g + f), len(g)) if g else None  # a family name alone names nobody in particular
        text = str(full or "")
        if "," in text:
            family_part, _, given_part = text.partition(",")
            g, f = words(given_part), words(family_part)
            return cls(tuple(g + f), len(g)) if g else None
        w = words(text)
        first = (text.split() or [""])[0]
        given = max(1, len(words(first)))  # "Marie-Ange Exemplo": a hyphenated first name is two given names
        return cls(tuple(w), min(given, len(w))) if w else None


@dataclass
class NameMatch:
    level: int            # 3 the names match, 2 a surname added or dropped, 1 the given name and one surname (or the same words in another order), 0 none
    reordered: bool = False
    how: set[str] = field(default_factory=set)  # the kinds of word match used beyond "same"
    shuffled: bool = False  # every word pairs, but the first words differ: the same words in another order
    single: bool = False  # a one-word name on both sides
    given_only: bool = False  # the words left over are all given names (Marie-Ange and Marie)


def _joined(a: list[str], b: list[str]) -> tuple[list[str], list[str], bool]:
    """Where a word of one side is two neighbouring words of the other run together ("JEANBAPTISTE" / "JEAN BAPTISTE"), the two are joined."""
    joined = False
    for one, other in ((a, b), (b, a)):
        i = 0
        while i < len(other) - 1:
            pair = other[i] + other[i + 1]
            if pair in one and other[i] not in one and other[i + 1] not in one:
                other[i:i + 2] = [pair]
                joined = True
            i += 1
    return a, b, joined


def compare(a: Name, b: Name) -> NameMatch:
    """Rule 5: how two names match."""
    wa, wb, joined = _joined(list(a.words), list(b.words))
    how: set[str] = {"joined"} if joined else set()
    if not wa or not wb:
        return NameMatch(0)
    first = same_word(wa[0], wb[0])
    if first is None:
        return _shuffled(wa, wb, how)
    if first != "same":
        how.add(first)
    rest_a, rest_b = wa[1:], wb[1:]
    if not rest_a and not rest_b:  # a one-word name on both sides: the same word, nothing else to go on
        return NameMatch(1, False, how, single=True)
    shift = len(a.words) - len(wa)  # joined words: positions move left; the given-name count is read on the words as given
    given_a, given_b = max(1, a.given - shift), max(1, b.given - (len(b.words) - len(wb)))
    used: set[int] = set()
    pairs: list[tuple[int, int]] = []
    for i, x in enumerate(rest_a):  # exact pairs first, then the looser kinds, so "SOUZA SOUSA" pairs each with its own
        for j, y in enumerate(rest_b):
            if j not in used and x == y:
                used.add(j)
                pairs.append((i, j))
                break
    paired_a = {i for i, _ in pairs}
    for i, x in enumerate(rest_a):
        if i in paired_a:
            continue
        for j, y in enumerate(rest_b):
            if j in used:
                continue
            if (len(x) == 1) != (len(y) == 1):  # a middle initial ("Ana C. Souza") stands for a word that starts with it
                kind = "initial" if (x[0] == y[0]) else None
            else:
                kind = same_word(x, y, i + 1 < given_a and j + 1 < given_b)
            if kind:
                used.add(j)
                pairs.append((i, j))
                paired_a.add(i)
                if kind != "same":
                    how.add(kind)
                break
    if not pairs:
        return NameMatch(0)
    left_a, left_b = len(rest_a) - len(pairs), len(rest_b) - len(pairs)
    order = [j for _, j in sorted(pairs)]
    reordered = order != sorted(order)
    if not left_a and not left_b:
        return NameMatch(3, reordered, how)
    leftover_a = [i + 1 for i in range(len(rest_a)) if i not in paired_a]
    leftover_b = [j + 1 for j in range(len(rest_b)) if j not in used]
    given_only = all(k < given_a for k in leftover_a) and all(k < given_b for k in leftover_b)
    if not left_a or not left_b:
        return NameMatch(2, reordered, how, given_only=given_only)
    return NameMatch(1, reordered, how)


def _shuffled(wa: list[str], wb: list[str], how: set[str]) -> NameMatch:
    """Every word of each name pairs with a word of the other, two words or more, though the first words differ: the same words in another order."""
    if len(wa) != len(wb) or len(wa) < 2:
        return NameMatch(0)
    left = list(wb)
    for x in wa:
        j = next((j for j, y in enumerate(left) if same_word(x, y)), None)
        if j is None:
            return NameMatch(0)
        kind = same_word(x, left.pop(j))
        if kind and kind != "same":
            how.add(kind)
    return NameMatch(1, True, how, shuffled=True)


def best(names_a: Iterable[Name], names_b: Iterable[Name]) -> NameMatch:
    """The best match between any spelling of one person and any of the other."""
    top = NameMatch(0)
    names_b = list(names_b)
    for x in names_a:
        for y in names_b:
            m = compare(x, y)
            if (m.level, -len(m.how), not m.reordered) > (top.level, -len(top.how), not top.reordered):
                top = m
                if m.level == 3 and not m.how and not m.reordered:
                    return top
    return top


# -- dates and numbers -----------------------------------------------------------------------------------------------


def parse_date(text: Any) -> date | None:
    """YYYY-MM-DD or MM/DD/YYYY (the screen's way of writing a date); anything else is no date."""
    s = str(text or "").strip()
    m = re.fullmatch(r"(\d{4})-(\d{1,2})-(\d{1,2})(?:T.*)?", s) or None
    try:
        if m:
            return date(int(m[1]), int(m[2]), int(m[3]))
        m = re.fullmatch(r"(\d{1,2})/(\d{1,2})/(\d{4})", s)
        if m:
            return date(int(m[3]), int(m[1]), int(m[2]))
    except ValueError:
        return None
    return None


def dates_agree(a: date, b: date) -> str | None:
    """Rule 6: "same", "swapped" (day and month swapped, same year) or None."""
    if a == b:
        return "same"
    if a.year == b.year and a.month == b.day and a.day == b.month and a.month != a.day:
        return "swapped"
    return None


def a_number(value: Any) -> str:
    """Nine digits ("A-012 345 678", "a12345678" and "012345678" are one number); "" when it is not one."""
    digits = re.sub(r"\D", "", str(value or ""))
    return digits.zfill(9) if 7 <= len(digits) <= 9 else ""


def passport(value: Any) -> str:
    """Letters and digits, upper case; "" when shorter than six."""
    code = re.sub(r"[^A-Za-z0-9]", "", str(value or "")).upper()
    return code if len(code) >= 6 and any(ch.isdigit() for ch in code) else ""


# -- the score -------------------------------------------------------------------------------------------------------


@dataclass
class Person:
    """One side of a comparison: every spelling of the name, dates of birth, A-Numbers and passport numbers (already normalized)."""

    names: list[Name] = field(default_factory=list)
    births: list[date] = field(default_factory=list)
    a_numbers: set[str] = field(default_factory=set)
    passports: set[str] = field(default_factory=set)


@dataclass
class Score:
    score: int
    sentence: str
    strength: str = ""

    def __post_init__(self) -> None:
        self.strength = "strong" if self.score >= STRONG else "possible" if self.score >= POSSIBLE else "weak"


_NAME_WORDS = {3: "the names match", 2: "a name is added or dropped on one of them (a married name, one of two surnames, or a second given name)",
               1: "the given name and one surname match"}


def _allowing(how: set[str]) -> str:
    kinds = [HOW[k] for k in (*KINDS, "sound", "family_form", "initial", "typo", "joined") if k in how]
    return f" (allowing {' and '.join(kinds)})" if kinds else ""


def score(a: Person, b: Person) -> Score | None:
    """Rule 7: how strongly person a is person b, with the sentence that says why, or None when nothing matches."""
    if a.a_numbers & b.a_numbers:
        return Score(100, "The same A-Number.")
    if a.passports & b.passports:
        return Score(100, "The same passport number.")
    m = best(a.names, b.names)
    if not m.level:
        return None
    if m.single:
        name = "the one word each name has is the same"
    elif m.shuffled:
        name = "the same words of the name in another order"
    elif m.level == 2 and m.given_only:
        name = "a second given name is added or dropped on one of them" + (" (in another order)" if m.reordered else "")
    elif m.level == 3 and m.reordered:
        name = "the surnames match in the other order"
    else:
        name = _NAME_WORDS[m.level] + (" (in another order)" if m.reordered else "")
    name += _allowing(m.how)
    agree = None
    if a.births and b.births:
        found = [dates_agree(x, y) for x in a.births for y in b.births]
        agree = "same" if "same" in found else "swapped" if "swapped" in found else "differ"
    if agree is None:
        points = {3: 45, 2: 35, 1: 25}[m.level]
        return Score(points, f"{name[0].upper()}{name[1:]}; there is no date of birth to compare, so this is a weak hit.")
    if agree == "differ":
        if m.level < 3:
            return None
        return Score(20, f"{name[0].upper()}{name[1:]}, but the birth dates differ.")
    points = {3: 95, 2: 85, 1: 70}[m.level] - (5 if agree == "swapped" else 0) - (10 if "typo" in m.how else 0)  # Marina and Mariana are one slip apart
    when = "the birth date agrees" if agree == "same" else "the birth date agrees with the day and month swapped"
    sentence = f"{name[0].upper()}{name[1:]} and {when}"
    if a.a_numbers and b.a_numbers:  # each has an A-Number and they are not the same: one person seldom has two
        return Score(min(points, 60), sentence + ", but the A-Numbers differ.")
    return Score(points, sentence + ".")
