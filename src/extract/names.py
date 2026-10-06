"""Document-processing helper."""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass

PARTICLES = {"DA", "DE", "DO", "DAS", "DOS", "DEL", "LA", "LAS", "LOS", "E", "Y", "DI", "DU", "VAN", "VON", "D"}

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
COMMON_GIVEN = {
    "MARIA", "JOSE", "JOAO", "ANA", "PEDRO", "PAULO", "LUIZ", "LUIS", "CARLOS", "ANTONIO", "FRANCISCO", "JUAN",
    "EDUARDA", "EDUARDO", "GABRIEL", "RAFAEL", "LUCAS", "MATEUS", "VITOR", "VICTOR", "JUNIOR", "JUNIO",
}


def fold_name(text: str) -> str:
    """Document-processing helper."""
    text = "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z' -]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -'")


def looks_like_name(text: str) -> bool:
    """Document-processing helper."""
    tokens = fold_name(text).split()
    real = [t for t in tokens if t not in PARTICLES]
    return len(real) >= 2 and all(len(t) >= 2 for t in real)


def surname_tokens(*names: str) -> set[str]:
    """Document-processing helper."""
    out: set[str] = set()
    for name in names:
        tokens = fold_name(name).split()
        out.update(t for t in tokens[1:] if t not in PARTICLES)
    return out


@dataclass(frozen=True)
class NameSplit:
    given: str
    family: str
    proven: bool
    why: str


def split_name(full: str, surnames: set[str] | frozenset[str] = frozenset(), explicit_family: str = "") -> NameSplit:
    """Document-processing helper."""
    tokens = fold_name(full).split()
    if len(tokens) < 2:
        return NameSplit(" ".join(tokens), "", False, "only one word -- no family name to split off")
    family_tokens = fold_name(explicit_family).split()
    if family_tokens and tokens[-len(family_tokens):] == family_tokens and len(family_tokens) < len(tokens):
        cut = len(tokens) - len(family_tokens)
        return NameSplit(" ".join(tokens[:cut]), " ".join(tokens[cut:]), True, "the document states the surname")
    hints = {s for s in surnames if s not in COMMON_GIVEN}
    start = next((i for i, t in enumerate(tokens) if i >= 1 and t in hints), None)
    if start is not None:
        while start > 1 and tokens[start - 1] in PARTICLES:  # Supporting implementation.
            start -= 1
        given, family = " ".join(tokens[:start]), " ".join(tokens[start:])
        if start == 1:
            return NameSplit(given, family, True, f"{tokens[start] if tokens[start] not in PARTICLES else tokens[start + 1]} is a family surname")
        inherited = tokens[next(i for i in range(start, len(tokens)) if tokens[i] in hints)]
        particle = next((i for i in range(1, start) if tokens[i] in PARTICLES), None)
        if particle is not None:
            # Supporting implementation.
            # Supporting implementation.
            # Supporting implementation.
            return NameSplit(" ".join(tokens[:particle]), " ".join(tokens[particle:]), False,
                             f"family surname {inherited} found; the family name is taken from '{tokens[particle]}' on")
        return NameSplit(given, family, False, f"family surname {inherited} found, but '{given}' may hold a surname too")
    if len(tokens) == 2:
        return NameSplit(tokens[0], tokens[1], True, "two words: first name and surname")
    # Supporting implementation.
    # Supporting implementation.
    return NameSplit(tokens[0], " ".join(tokens[1:]), False, "no relative's surname to confirm where the given name ends")


def same_person_name(a: str, b: str) -> bool:
    """Document-processing helper."""
    ta, tb = fold_name(a).split(), fold_name(b).split()
    if len(ta) != len(tb):
        return False
    return all(x == y or (len(x) == len(y) and sum(p != q for p, q in zip(x, y)) <= 1 and len(x) >= 4) for x, y in zip(ta, tb))


def shares_given_name(a: str, b: str) -> bool:
    ta, tb = fold_name(a).split(), fold_name(b).split()
    return bool(ta and tb and (ta[0] == tb[0] or same_person_name(ta[0], tb[0])))
