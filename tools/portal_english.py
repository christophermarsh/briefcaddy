"""English words on a screen that should be in the client's language (Portuguese, Spanish or Haitian Creole).

The portal's rule is that a client reads their own language on every screen: "Work permit" in a Portuguese list is a defect, a
form's name ("I-485"), a person's name or a number is not. This flags words that exist only in English. It is a list of words,
not a dictionary of English (the languages share too many look-alikes), so it finds what a person would circle; a new English
word a screen starts to use is added to ENGLISH. Used three ways:

    tests/test_portal.py                    every text the portal page and the question bank hold, in pt, es and ht
    tests/e2e/test_portal_safety.py         every screen of the real page, walked in each language
    python tools/portal_english.py "text"   what a line would be flagged for

Words that are the firm's own names for things its clients know in English ("green card", "ZIP") are allowed: ALLOWED.
"""

from __future__ import annotations

import re
import sys

# words that exist in English and in none of Portuguese, Spanish or Haitian Creole (look-alikes such as "status" or "case" are left out)
ENGLISH = frozenset("""
the and your you yours please with from this that these those have has are was were will would can could not any all new send
read work permit card number name date address phone mobile photo document documents passport birth certificate security yes thank thanks
office answer answers question questions help click tap start back next required choose select upload file take picture later
about when where what which who how here there now today week month year hello welcome leave screen button open close
save saved delete remove add edit show hide less words message messages need needs needed
information application attorney lawyer court judge check cannot don't doesn't isn't wasn't won't
if or but because while after before again also only just very much many some other another each every both either neither
""".split())
ALLOWED = frozenset({"zip", "uscis", "sms", "pdf", "jpg", "png"})  # file kinds and the like a phone names in English
# names clients know in English, which stay in English on every screen: the card, the agency's number, the field on the card, a
# placeholder, and a name the text quotes on purpose to say what it is called on the door or the screen (“clerk's office”)
NAMES = re.compile(r"green card|social security|a-number|resident since|\{[a-z_]+\}|“[^”]*”", re.I)
TOKEN = re.compile(r"[A-Za-zÀ-ÿ']+")


def english_words(text: str) -> list[str]:
    """The English words in a line of text, lowercased, in order, once each. All-capitals words (a form's name, an acronym) are not words."""
    out: list[str] = []
    for token in TOKEN.findall(NAMES.sub(" ", text or "")):
        if token.isupper() and len(token) > 1:
            continue
        word = token.lower().strip("'")
        if word in ENGLISH and word not in ALLOWED and word not in out:
            out.append(word)
    return out


if __name__ == "__main__":
    print(english_words(" ".join(sys.argv[1:])))
