"""Reads hand-marked "( X ) Sim  ( ) Não"-style checkboxes off a scanned
questionnaire page, deterministically and locally -- no vision model.

Why this works where plain OCR doesn't (docs/decisions.md, "Tesseract
cannot reliably OCR handwritten questionnaires"): the *printed* option
labels ("Sim", "Não", "Feminino", "Solteiro", ...) OCR fine, only the
handwritten mark doesn't. So the label is used as an anchor, and the mark
is judged by pixel darkness in the "( )" box printed just left of it --
confirmed against the real example questionnaire, where every box sits
immediately before its label, e.g. "(><) Não", "( -<) Solteiro".

Tesseract usually glues the box to the label ("( )Sim", "(><)Néao"), so
a label's x position is interpolated from its character offset inside the
OCR token rather than taken from the token's left edge.

A group is judged *relatively*: every option's box has the same printed
parentheses, so the marked one is the one with clearly more ink than the
rest. When no option clearly stands out (blank, two marked, a scan speck
the size of an X), the reading is "ambiguous" and no answer is produced --
never a guess.
"""

from __future__ import annotations

from dataclasses import dataclass
from statistics import median
from typing import Any

from classify.ocr import OcrWord

# Box geometry, in multiples of the page's typical word height. Measured
# Fictional fixture or generic implementation note.
# "( X )" spans ~70px ending ~5px before its label.
BOX_WIDTH = 3.3
BOX_GAP = 0.15
BOX_HALF_HEIGHT = 0.7
DARK_THRESHOLD = 128

# A marked box must beat the runner-up by both an absolute and a relative
# margin (fraction of dark pixels). Parentheses alone come to ~0.05-0.08
# on the real scan; a hand-drawn X adds well over 0.05.
MIN_MARGIN = 0.03
MIN_RATIO = 1.4


@dataclass(frozen=True)
class LabelHit:
    """One option label located on a page: which OCR token it's inside and
    where the label starts within that token's text."""

    page: int
    word: OcrWord
    char_index: int


@dataclass(frozen=True)
class OptionInk:
    value: str
    hit: LabelHit
    ink: float
    box: tuple[int, int, int, int]


@dataclass(frozen=True)
class ChoiceReading:
    chosen: str | None  # None when ambiguous
    options: list[OptionInk]
    reason: str

    @property
    def confidence(self) -> float:
        """Margin between the marked box and the runner-up, squashed into
        0.5..0.99. Only meaningful when chosen is not None."""
        if self.chosen is None or len(self.options) < 2:
            return 0.0
        if self.reason == "typed X in this box":
            return 0.99  # an exact typed mark, not an ink estimate
        inks = sorted((o.ink for o in self.options), reverse=True)
        return min(0.99, 0.5 + (inks[0] - inks[1]) * 5)


def typical_word_height(words: list[OcrWord]) -> float:
    """Median height of plain alphabetic words -- the page's text size,
    which scales the box geometry so a different scan DPI still works."""
    heights = [w.height for w in words if w.text.isalpha() and len(w.text) >= 3]
    return float(median(heights)) if heights else 23.0


def box_region(hit: LabelHit, word_height: float) -> tuple[int, int, int, int]:
    w = hit.word
    frac = hit.char_index / max(1, len(w.text))
    label_x = w.left + w.width * frac
    cy = w.top + w.height / 2
    return (
        max(0, round(label_x - BOX_WIDTH * word_height)),
        max(0, round(cy - BOX_HALF_HEIGHT * word_height)),
        round(label_x - BOX_GAP * word_height),
        round(cy + BOX_HALF_HEIGHT * word_height),
    )


def ink_fraction(gray: Any, box: tuple[int, int, int, int]) -> float:
    region = gray.crop(box)
    total = region.width * region.height
    if total == 0:
        return 0.0
    return sum(region.histogram()[:DARK_THRESHOLD]) / total


def judge_single_choice(options: list[OptionInk]) -> ChoiceReading:
    if len(options) < 2:
        return ChoiceReading(None, options, "fewer than two option labels found")
    ranked = sorted(options, key=lambda o: o.ink, reverse=True)
    top, runner_up = ranked[0], ranked[1]
    if top.ink - runner_up.ink < MIN_MARGIN or top.ink < runner_up.ink * MIN_RATIO:
        return ChoiceReading(None, options, "no box clearly darker than the others")
    return ChoiceReading(top.value, options, "marked box clearly darker")


def judge_multi_choice(options: list[OptionInk]) -> list[ChoiceReading]:
    """For "select all that apply" groups (race): each option is compared
    against the lightest box in the group, which stands in for an empty
    one. Requires at least one box to be clearly unmarked, otherwise
    there's no baseline and nothing is decided."""
    if len(options) < 2:
        return [ChoiceReading(None, options, "fewer than two option labels found")]
    baseline = min(o.ink for o in options)
    marked = [o for o in options if o.ink - baseline >= MIN_MARGIN and o.ink >= baseline * MIN_RATIO]
    if not marked:
        return [ChoiceReading(None, options, "no box clearly darker than the others")]
    if len(marked) == len(options):
        return [ChoiceReading(None, options, "every box looks marked")]
    return [ChoiceReading(o.value, options, "marked box clearly darker than the empty ones") for o in marked]
