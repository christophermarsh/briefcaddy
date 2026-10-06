"""Language knowledge for reading intake questionnaires: Portuguese (the
firm's current template), Spanish and French.

Deliberately explicit tables, not machine translation, for everything the
I-485 needs verbatim (countries, hair colors, yes/no, months): a table
entry is either right or visibly missing. Machine translation (Argos,
offline) is only a fallback for free-text occupations -- and even there a
curated glossary comes first, because MT of a single word is unreliable
(Spanish "Albañil", bricklayer, came back as "Meatballs").

Adding a language = extending these tables + a question map for that
language's template (schemas/questions/paper/map.<lang>.json).
"""

from __future__ import annotations

import re
import unicodedata

LANGUAGE_NAMES = {"pt": "Portuguese", "es": "Spanish", "fr": "French"}


def fold(text: str) -> str:
    """Upper case, accents removed: "Fevereiro" / "FÉVRIER" -> "FEVEREIRO" / "FEVRIER"."""
    return "".join(c for c in unicodedata.normalize("NFKD", text.upper()) if not unicodedata.combining(c))


# --- language detection ----------------------------------------------------

# Words printed on each language's version of an intake questionnaire.
_MARKERS = {
    "pt": {"VOCE", "NAO", "JA", "ENDERECO", "NASCIMENTO", "SIM", "ESTADOS", "UNIDOS", "QUAL", "SEU", "NOME", "ALGUMA"},
    "es": {"USTED", "NACIMIENTO", "DIRECCION", "SI", "HA", "ALGUNA", "NOMBRE", "SU", "ESTADOS", "UNIDOS", "CUAL", "FECHA"},
    "fr": {"VOUS", "NAISSANCE", "ADRESSE", "OUI", "NON", "AVEZ", "NOM", "VOTRE", "ETATS", "UNIS", "QUEL", "DATE"},
}


def detect_language(text: str) -> str | None:
    """Most likely questionnaire language from its printed text, or None."""
    words = re.findall(r"[A-Z]+", fold(text))
    scores = {lang: sum(1 for w in words if w in markers) for lang, markers in _MARKERS.items()}
    best = max(scores, key=scores.get)
    runner_up = sorted(scores.values())[-2]
    return best if scores[best] >= 10 and scores[best] > 1.5 * runner_up else None


# --- vocabulary (all keys accent-folded) ----------------------------------

MONTHS = {
    # pt
    "JANEIRO": 1, "FEVEREIRO": 2, "MARCO": 3, "ABRIL": 4, "MAIO": 5, "JUNHO": 6, "JULHO": 7, "AGOSTO": 8,
    "SETEMBRO": 9, "OUTUBRO": 10, "NOVEMBRO": 11, "DEZEMBRO": 12,
    # es
    "ENERO": 1, "FEBRERO": 2, "MARZO": 3, "MAYO": 5, "JUNIO": 6, "JULIO": 7,
    "SEPTIEMBRE": 9, "SETIEMBRE": 9, "OCTUBRE": 10, "NOVIEMBRE": 11, "DICIEMBRE": 12,
    # fr
    "JANVIER": 1, "FEVRIER": 2, "MARS": 3, "AVRIL": 4, "MAI": 5, "JUIN": 6, "JUILLET": 7, "AOUT": 8,
    "SEPTEMBRE": 9, "OCTOBRE": 10, "NOVEMBRE": 11, "DECEMBRE": 12,
}

# Connecting words in long-form dates ("12 de março de 1970", "3 del
# marzo", "le 3 mars 2020") -- removed before parsing.
DATE_FILLERS = {"DE", "DEL", "DU", "LE", "EL"}

YES_WORDS = {"SIM", "S", "SI", "YES", "Y", "OUI", "O"}
NO_WORDS = {"NAO", "N", "NO", "NON"}

# Printed around typed blanks on the templates ("De: __ ate agora") -- a
# model value may contain them without the client having typed them.
TEMPLATE_WORDS = {
    "DE", "DO", "DA", "ATE", "AGORA", "ATUAL", "PRESENTE", "APT", "APTO",  # pt
    "DEL", "HASTA", "AHORA", "ACTUAL", "ACTUALIDAD",  # es
    "DU", "AU", "JUSQU", "A", "MAINTENANT", "AUJOURD", "HUI", "LE", "LA", "ACTUEL",  # fr
}
PRESENT_WORDS = {"AGORA", "ATUAL", "PRESENTE", "PRESENT", "HOJE", "AHORA", "ACTUALIDAD", "ACTUAL", "MAINTENANT", "AUJOURD", "ACTUEL"}

HAIR_COLORS = {
    # pt
    "PRETO": "Black", "PRETA": "Black", "NEGRO": "Black", "CASTANHO": "Brown", "CASTANHA": "Brown", "MARROM": "Brown",
    "LOIRO": "Blond", "LOIRA": "Blond", "LOURO": "Blond", "RUIVO": "Red", "RUIVA": "Red", "VERMELHO": "Red",
    "GRISALHO": "Gray", "CINZA": "Gray", "BRANCO": "White", "SEM CABELOS": "Bald", "CARECA": "Bald",
    # es
    "NEGRA": "Black", "CASTANO": "Brown", "CASTANA": "Brown", "CAFE": "Brown", "MARRON": "Brown",
    "RUBIO": "Blond", "RUBIA": "Blond", "PELIRROJO": "Red", "PELIRROJA": "Red", "ROJO": "Red", "CANOSO": "Gray",
    "GRIS": "Gray", "BLANCO": "White", "CALVO": "Bald",
    # fr
    "NOIR": "Black", "NOIRE": "Black", "BRUN": "Brown", "BRUNE": "Brown", "CHATAIN": "Brown", "BLOND": "Blond",
    "BLONDE": "Blond", "ROUX": "Red", "ROUSSE": "Red", "BLANC": "White", "BLANCHE": "White", "CHAUVE": "Bald",
}

# Eye colors as the I-485 lists them (Part 7 item 5). The firm's form
# prints the Portuguese options: Preto, Cinza, Bordo, Azul, Verde, Rosa,
# Marrom, Avela, Desconhecido/Outro. Plural and English forms too.
EYE_COLORS = {
    # pt
    "PRETO": "Black", "PRETOS": "Black", "NEGRO": "Black", "NEGROS": "Black", "CINZA": "Gray", "CINZAS": "Gray",
    "BORDO": "Maroon", "AZUL": "Blue", "AZUIS": "Blue", "VERDE": "Green", "VERDES": "Green", "ROSA": "Pink",
    "MARROM": "Brown", "MARRONS": "Brown", "CASTANHO": "Brown", "CASTANHOS": "Brown", "CASTANHA": "Brown",
    "CASTANHO ESCURO": "Brown", "CASTANHO CLARO": "Brown", "AVELA": "Hazel", "MEL": "Hazel",
    # es
    "NEGRA": "Black", "CAFE": "Brown", "CAFES": "Brown", "MARRON": "Brown", "CASTANO": "Brown", "CASTANOS": "Brown",
    "GRIS": "Gray", "AVELLANA": "Hazel", "MIEL": "Hazel", "GRANATE": "Maroon",
    # fr
    "NOIR": "Black", "NOIRS": "Black", "BRUN": "Brown", "BRUNS": "Brown", "BLEU": "Blue", "BLEUS": "Blue",
    "VERT": "Green", "VERTS": "Green", "NOISETTE": "Hazel",
    # Fictional fixture or generic implementation note.
    "BLACK": "Black", "BROWN": "Brown", "BLUE": "Blue", "GREEN": "Green", "GRAY": "Gray", "GREY": "Gray",
    "HAZEL": "Hazel", "MAROON": "Maroon", "PINK": "Pink",
}

COUNTRIES = {
    # pt
    "BRASIL": "BRAZIL", "ESTADOS UNIDOS": "USA", "EUA": "USA", "ITALIA": "ITALY", "PORTUGAL": "PORTUGAL",
    "HAITI": "HAITI", "REPUBLICA DOMINICANA": "DOMINICAN REPUBLIC", "CUBA": "CUBA", "COLOMBIA": "COLOMBIA",
    "EQUADOR": "ECUADOR", "PERU": "PERU", "VENEZUELA": "VENEZUELA", "GUATEMALA": "GUATEMALA", "HONDURAS": "HONDURAS",
    "EL SALVADOR": "EL SALVADOR", "MEXICO": "MEXICO", "BOLIVIA": "BOLIVIA", "PARAGUAI": "PARAGUAY", "URUGUAI": "URUGUAY",
    "ARGENTINA": "ARGENTINA", "CHILE": "CHILE", "ESPANHA": "SPAIN", "ALEMANHA": "GERMANY", "FRANCA": "FRANCE",
    # es
    "EEUU": "USA", "EE UU": "USA", "ECUADOR": "ECUADOR", "PARAGUAY": "PARAGUAY", "URUGUAY": "URUGUAY", "NICARAGUA": "NICARAGUA",
    "PANAMA": "PANAMA", "COSTA RICA": "COSTA RICA", "ESPANA": "SPAIN",
    # fr
    "ETATS UNIS": "USA", "ETATS-UNIS": "USA", "BRESIL": "BRAZIL", "REPUBLIQUE DOMINICAINE": "DOMINICAN REPUBLIC",
    "MEXIQUE": "MEXICO", "COLOMBIE": "COLOMBIA", "EQUATEUR": "ECUADOR", "PEROU": "PERU", "BOLIVIE": "BOLIVIA",
    "ARGENTINE": "ARGENTINA", "CHILI": "CHILE", "ESPAGNE": "SPAIN", "ALLEMAGNE": "GERMANY", "FRANCE": "FRANCE",
    "CANADA": "CANADA", "ITALIE": "ITALY",
}

# Common occupations among the firm's clients, checked before any machine
# translation. Keys accent-folded; values as the I-485 should read.
OCCUPATIONS = {
    # pt
    "ESTUDANTE": "STUDENT", "DESEMPREGADO": "UNEMPLOYED", "DESEMPREGADA": "UNEMPLOYED", "PEDREIRO": "MASON",
    "SERVENTE": "CONSTRUCTION HELPER", "AJUDANTE DE PEDREIRO": "MASON HELPER", "PINTOR": "PAINTER",
    "FAXINEIRA": "CLEANER", "FAXINEIRO": "CLEANER", "DIARISTA": "HOUSE CLEANER", "COZINHEIRO": "COOK",
    "COZINHEIRA": "COOK", "AJUDANTE DE COZINHA": "KITCHEN HELPER", "GARCOM": "WAITER", "GARCONETE": "WAITRESS",
    "ATENDENTE": "ATTENDANT", "CAIXA": "CASHIER", "MOTORISTA": "DRIVER", "ENTREGADOR": "DELIVERY DRIVER",
    "JARDINEIRO": "LANDSCAPER", "CARPINTEIRO": "CARPENTER", "ELETRICISTA": "ELECTRICIAN", "ENCANADOR": "PLUMBER",
    "BABA": "NANNY", "DONA DE CASA": "HOMEMAKER", "LAVADOR DE PRATOS": "DISHWASHER", "MECANICO": "MECHANIC",
    # es
    "ESTUDIANTE": "STUDENT", "DESEMPLEADO": "UNEMPLOYED", "DESEMPLEADA": "UNEMPLOYED", "ALBANIL": "BRICKLAYER",
    "AYUDANTE DE COCINA": "KITCHEN HELPER", "COCINERO": "COOK", "COCINERA": "COOK", "LIMPIEZA": "CLEANER",
    "MESERO": "WAITER", "MESERA": "WAITRESS", "CAJERO": "CASHIER", "CAJERA": "CASHIER", "CHOFER": "DRIVER",
    "CONDUCTOR": "DRIVER", "JARDINERO": "LANDSCAPER", "CARPINTERO": "CARPENTER", "ELECTRICISTA": "ELECTRICIAN",
    "PLOMERO": "PLUMBER", "NINERA": "NANNY", "AMA DE CASA": "HOMEMAKER", "LAVAPLATOS": "DISHWASHER",
    # fr
    "ETUDIANT": "STUDENT", "ETUDIANTE": "STUDENT", "CHOMEUR": "UNEMPLOYED", "CHOMEUSE": "UNEMPLOYED", "SANS EMPLOI": "UNEMPLOYED",
    "MACON": "MASON", "PEINTRE": "PAINTER", "CUISINIER": "COOK", "CUISINIERE": "COOK", "AIDE-CUISINIER": "KITCHEN HELPER",
    "SERVEUR": "WAITER", "SERVEUSE": "WAITRESS", "CAISSIER": "CASHIER", "CAISSIERE": "CASHIER", "CHAUFFEUR": "DRIVER",
    "JARDINIER": "LANDSCAPER", "MENUISIER": "CARPENTER", "ELECTRICIEN": "ELECTRICIAN", "PLOMBIER": "PLUMBER",
    "FEMME DE MENAGE": "HOUSE CLEANER", "NOUNOU": "NANNY", "MECANICIEN": "MECHANIC", "PLONGEUR": "DISHWASHER",
}


def translate_occupation(value: str, language: str) -> str:
    """Glossary first; else offline machine translation in sentence case (in
    capitals it mistranslates: "PEDREIRO" -> "PETER"); else as written."""
    known = OCCUPATIONS.get(fold(value).strip())
    if known:
        return known
    if language not in LANGUAGE_NAMES:
        return value
    try:
        from classify.translate import translate_text

        translated = translate_text(value.capitalize(), language)
    except Exception:  # noqa: BLE001 -- translation is optional; keep the client's words
        translated = None
    return translated.upper() if translated else value
