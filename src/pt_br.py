"""Brazilian Portuguese, not European: the firm's Portuguese-speaking clients are Brazilian, and the offline translator (Argos, en -> pt) writes
European Portuguese ("telemóvel", "autocarro", "equipa", "carta de condução", and "apelido", which means surname in Portugal and nickname in
Brazil). Every machine draft a client could read goes through `brazilian` first, which rewrites the known European words and phrases to their
Brazilian forms and nothing else; the paralegal still reads and may correct the draft, and every client-facing text stays a DRAFT for the firm's
certified translator (docs/attorney_review.md, "Brazilian Portuguese"). The product's own written Portuguese is Brazilian already; a test holds
it there (tests/test_pt_br.py, the EUROPEAN_ONLY list).

A capitalised word in the middle of a sentence is left alone: it may be a name or a place ("Morada Nova"). Only lower-case words and the first
word of a sentence are rewritten.
"""

from __future__ import annotations

import re

# European form -> Brazilian form. Phrases before single words, so "carta de condução" is rewritten before "carta" could be. Lower case; the
# rewrite keeps a capital first letter where the source had one.
TERMS: list[tuple[str, str]] = [
    # phrases first; where the Brazilian word has the other gender, the article or possessive goes with it
    ("carta de condução", "carteira de motorista"),
    ("bilhete de identidade", "carteira de identidade"),
    ("segurança social", "Seguro Social"),
    ("palavra-passe", "senha"),
    ("pequeno-almoço", "café da manhã"),
    ("a casa de banho", "o banheiro"),
    ("na casa de banho", "no banheiro"),
    ("casa de banho", "banheiro"),
    ("entrar em contacto", "entrar em contato"),
    ("contactá-lo", "entrar em contato com você"),
    ("contactá-la", "entrar em contato com você"),
    ("contactar", "entrar em contato com"),
    ("contacto", "contato"),
    ("a sua morada", "o seu endereço"),
    ("sua morada", "seu endereço"),
    ("a morada", "o endereço"),
    ("da morada", "do endereço"),
    ("na morada", "no endereço"),
    ("uma morada", "um endereço"),
    ("morada", "endereço"),
    ("o ecrã", "a tela"),
    ("no ecrã", "na tela"),
    ("do ecrã", "da tela"),
    ("ecrã", "tela"),
    ("o frigorífico", "a geladeira"),
    ("no frigorífico", "na geladeira"),
    ("do frigorífico", "da geladeira"),
    ("frigorífico", "geladeira"),
    ("telemóvel", "celular"),
    ("autocarro", "ônibus"),
    ("comboio", "trem"),
    ("camião", "caminhão"),
    ("equipa", "equipe"),
    ("ficheiro", "arquivo"),
    ("ficheiros", "arquivos"),
    ("utilizador", "usuário"),
    ("utilizadores", "usuários"),
    ("apelido", "sobrenome"),  # in the translator's European output a surname; in Brazil the word means a nickname, so it is not in EUROPEAN_ONLY
    ("apelidos", "sobrenomes"),
    ("rapariga", "menina"),
    ("registo", "registro"),
    ("registos", "registros"),
    ("facto", "fato"),
    ("factos", "fatos"),
    ("bebé", "bebê"),
    ("género", "gênero"),
    ("económico", "econômico"),
    ("económica", "econômica"),
    ("académico", "acadêmico"),
    ("académica", "acadêmica"),
    ("prémio", "prêmio"),
    ("bónus", "bônus"),
    ("tem de", "precisa"),  # "Tem de levar" -> "Precisa levar"
    ("têm de", "precisam"),
    # the translator's own mistakes seen on 10/04/2026: "appointment" as a nomination, "sign in" as a signature, "upload" as a load
    ("à nomeação", "ao agendamento"),
    ("a nomeação", "o agendamento"),
    ("assine com a senha", "entre com a senha"),
    ("carregue uma foto", "envie uma foto"),
    ("carregue o documento", "envie o documento"),
]
# words that are European Portuguese only, never Brazilian and never Spanish: the product's own strings must hold none (tests/test_pt_br.py).
# "apelido" is not here: it is a Brazilian word too (a nickname). Nor "código postal": Brazil says CEP for its own codes and "código postal" for
# another country's, which is what the questionnaire asks about.
EUROPEAN_ONLY = ["telemóvel", "autocarro", "equipa", "ficheiro", "ecrã", "utilizador", "palavra-passe", "comboio", "rapariga", "carta de condução",
                 "bilhete de identidade", "pequeno-almoço", "casa de banho", "frigorífico"]

_RULES = [(re.compile(r"(?<![\w-])" + re.escape(eu) + r"(?![\w-])", re.I), br) for eu, br in TERMS]


def _case_like(source: str, target: str) -> str:
    return target[:1].upper() + target[1:] if source[:1].isupper() and not target[:1].isupper() else target


def _sentence_start(text: str, at: int) -> bool:
    before = text[:at].rstrip(" \"'(“‘")
    return not before or before[-1] in ".!?:\n"


def brazilian(text: str) -> str:
    """The text with the known European Portuguese words and phrases in their Brazilian form; everything else untouched."""
    out = text or ""
    for (eu, br), (rx, _) in zip(TERMS, _RULES):
        phrase = " " in eu or "-" in eu  # a phrase ("Segurança Social") is a term wherever it stands; a single capitalised word mid-sentence may be a name

        def put(m: re.Match, br=br, phrase=phrase) -> str:
            word = m.group(0)
            if not phrase and word[:1].isupper() and not _sentence_start(out, m.start()):
                return word  # a name or a place, not a word ("Morada Nova")
            return _case_like(word, br)

        out = rx.sub(put, out)
    return out


def european_words_in(text: str) -> list[str]:
    """The European-only words a text holds (whole words, any case), for the guard test and the paralegal's draft note."""
    low = (text or "").lower()
    return [w for w in EUROPEAN_ONLY if re.search(r"(?<![\w-])" + re.escape(w) + r"(?![\w-])", low)]
