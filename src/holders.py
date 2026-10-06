"""Who holds each line that keeps a packet from being ready: the client, the office or the attorney.

A packet's plan (src/packet.py) lists the lines that hold it ("Missing: the birth certificate.", "The G-28's choices were not confirmed for this case.").
The packet's index sheet prints those lines, so their words never change. This module adds, beside each line, the one who can clear it:

  - the client: a paper only the client can send, a question only the client can answer;
  - the office: a review card the paralegal works, a Settings line, a choice on the packet, a rebuild;
  - the attorney: a sign-off, an approval, an attestation, a decision about the case, a restriction question.

The holder is given where the line is made, by the function that makes it, and never by reading the line's words:

  - a producer (a function that returns lines) is declared with @producer(holder): every line it returns that no one labelled itself is that holder's;
  - a producer whose lines have different holders labels those lines with held(holder, text), and the rest take the producer's own;
  - a line nothing labelled is the office's (holder_of), so a new producer that forgets is the paralegal's to read, never lost.

A labelled line is a str: it compares, prints, hashes and is written to a file exactly as the plain string does, so the packet's index sheet and the sample packet are
the same bytes as before. tests/test_holders.py lists every producer in src/ and fails when one declares no holder.

A line may also say `via`: the kind of attorney item (src/approvals.py) that already lists it, so the day's list counts it once, from the approvals registry and not a second time.
And `say`: the words for a screen when the line itself names a file ("Not found in the client's folder: scan-1.pdf."). And `n`: how many things the line stands for ("3 review cards still open"
is the cards the paralegal works, the attorney's own being counted from the approvals registry).
"""

from __future__ import annotations

import functools
from typing import Any, Callable, Iterable

CLIENT, OFFICE, ATTORNEY = "client", "office", "attorney"
HOLDERS = (CLIENT, OFFICE, ATTORNEY)

# every function declared a producer: "module.function" -> its holder (filled as the modules are imported)
PRODUCERS: dict[str, str] = {}

# producers whose function is not named `problems` (tests/test_holders.py finds the ones that are by their name)
OTHER_PRODUCERS = ("journey.i485_court_problem", "fill.cover_letter.priority", "fill.cover_letter.mail_to", "fill.cover_letter.gaps",
                   "part14_explain.problems_of")


class Line(str):
    """A line that keeps a packet from being ready, and who holds it."""

    holder: str
    via: str | None
    say: str | None
    n: int

    def __new__(cls, text: str, holder: str = OFFICE, via: str | None = None, say: str | None = None, n: int = 1):
        if holder not in HOLDERS:
            raise ValueError(f"A line is held by the client, the office or the attorney, not {holder!r}.")
        line = super().__new__(cls, text)
        line.holder, line.via, line.say, line.n = holder, via, say, n
        return line


def held(holder: str, text: str, via: str | None = None, say: str | None = None, n: int = 1) -> Line:
    return Line(text, holder, via, say, n)


def holder_of(line: Any) -> str:
    """The line's holder; a line no producer labelled is the office's."""
    return getattr(line, "holder", OFFICE)


def label(holder: str, lines: Iterable[Any] | None) -> list[Line]:
    """The lines, each held by `holder` unless the producer already labelled it."""
    return [x if isinstance(x, Line) else Line(x, holder) for x in lines or []]


def producer(holder: str, part: int | None = None) -> Callable:
    """Declares a function that returns lines (or, with part, a tuple whose element `part` is the list of lines) and who holds the lines it leaves unlabelled."""
    if holder not in HOLDERS:
        raise ValueError(f"A producer's lines are held by the client, the office or the attorney, not {holder!r}.")

    def wrap(fn: Callable) -> Callable:
        @functools.wraps(fn)
        def made(*args: Any, **kwargs: Any) -> Any:
            got = fn(*args, **kwargs)
            if part is None:
                return label(holder, got) if isinstance(got, (list, tuple)) else got
            out = list(got)
            out[part] = label(holder, out[part])
            return tuple(out)

        PRODUCERS[f"{fn.__module__}.{fn.__name__}"] = holder
        return made
    return wrap


def of_who(who: Any) -> str:
    """The holder a question's own `who` names (the client, the petitioner, the attorney; the paralegal, a document and the rest are the office's): the product's data about
    who answers a question, not the words of a line."""
    text = str(who or "").lower()
    return CLIENT if ("client" in text or "petitioner" in text) else ATTORNEY if "attorney" in text else OFFICE


def of_first(questions: Iterable[dict[str, Any]]) -> str:
    """The holder of a line that stands for the questions not answered yet: whoever the first of them is for (its own `who`)."""
    first = next((q for q in questions if q.get("required") and q.get("value") is None), None)
    return of_who(first.get("who")) if first else OFFICE


def counts(lines: Iterable[Any]) -> dict[str, int]:
    out = {h: 0 for h in HOLDERS}
    for line in lines:
        out[holder_of(line)] += 1
    return out
