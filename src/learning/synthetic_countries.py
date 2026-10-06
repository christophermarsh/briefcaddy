"""Invented documents for every country of ours (learning/synthetic.py's
writers call these): each country's own birth and marriage records,
passports and national identity cards, in its language, with its offices'
names and real places (schemas/geo, extract/geo.py) -- and the certified
English translations clients bring with them.

Wording follows each country's published formats where we have them
(Peru: RENIEC's official blank actas, schemas/registers/training_sources.json) and the
standard titles and offices of each registry otherwise; values are invented.
"""

from __future__ import annotations

import json
import random
from functools import lru_cache
import schema_path


# iso2: (iso3, header as printed, language, nationality word, passport authority, names, passport union)
PROFILES = {
    "BR": ("BRA", "REPUBLICA FEDERATIVA DO BRASIL", "pt", "BRASILEIRO(A)", "DPF", "br", "MERCOSUL"),
    "AR": ("ARG", "REPUBLICA ARGENTINA", "es", "ARGENTINA", "RENAPER", "es", "MERCOSUR"),
    "BO": ("BOL", "ESTADO PLURINACIONAL DE BOLIVIA", "es", "BOLIVIANA", "DIGEMIG", "es", "COMUNIDAD ANDINA"),
    "CL": ("CHL", "REPUBLICA DE CHILE", "es", "CHILENA", "SERVICIO DE REGISTRO CIVIL E IDENTIFICACION", "es", ""),
    "CO": ("COL", "REPUBLICA DE COLOMBIA", "es", "COLOMBIANA", "MINISTERIO DE RELACIONES EXTERIORES", "es", "COMUNIDAD ANDINA"),
    "EC": ("ECU", "REPUBLICA DEL ECUADOR", "es", "ECUATORIANA", "DIRECCION GENERAL DE REGISTRO CIVIL", "es", "COMUNIDAD ANDINA"),
    "GY": ("GUY", "CO-OPERATIVE REPUBLIC OF GUYANA", "en", "GUYANESE", "CENTRAL IMMIGRATION AND PASSPORT OFFICE", "gy", "CARIBBEAN COMMUNITY"),
    "PY": ("PRY", "REPUBLICA DEL PARAGUAY", "es", "PARAGUAYA", "POLICIA NACIONAL", "es", "MERCOSUR"),
    "PE": ("PER", "REPUBLICA DEL PERU", "es", "PERUANA", "MIGRACIONES", "es", "COMUNIDAD ANDINA"),
    "SR": ("SUR", "REPUBLIEK SURINAME", "nl", "SURINAAMSE", "CENTRAAL BUREAU VOOR BURGERZAKEN", "sr", "CARIBISCHE GEMEENSCHAP"),
    "UY": ("URY", "REPUBLICA ORIENTAL DEL URUGUAY", "es", "URUGUAYA", "DIRECCION NACIONAL DE IDENTIFICACION CIVIL", "es", "MERCOSUR"),
    "VE": ("VEN", "REPUBLICA BOLIVARIANA DE VENEZUELA", "es", "VENEZOLANA", "SAIME", "es", ""),
    "HT": ("HTI", "REPUBLIQUE D'HAITI", "fr", "HAITIENNE", "DIRECTION DE L'IMMIGRATION ET DE L'EMIGRATION", "ht", "COMMUNAUTE CARIBEENNE"),
    "DO": ("DOM", "REPUBLICA DOMINICANA", "es", "DOMINICANA", "DIRECCION GENERAL DE PASAPORTES", "es", ""),
    "GT": ("GTM", "REPUBLICA DE GUATEMALA", "es", "GUATEMALTECA", "INSTITUTO GUATEMALTECO DE MIGRACION", "es", "CENTROAMERICA"),
    "HN": ("HND", "REPUBLICA DE HONDURAS", "es", "HONDURENA", "INSTITUTO NACIONAL DE MIGRACION", "es", "CENTROAMERICA"),
    "SV": ("SLV", "REPUBLICA DE EL SALVADOR", "es", "SALVADORENA", "DIRECCION GENERAL DE MIGRACION Y EXTRANJERIA", "es", "CENTROAMERICA"),
    "MX": ("MEX", "ESTADOS UNIDOS MEXICANOS", "es", "MEXICANA", "SECRETARIA DE RELACIONES EXTERIORES", "es", ""),
    "NI": ("NIC", "REPUBLICA DE NICARAGUA", "es", "NICARAGUENSE", "DIRECCION GENERAL DE MIGRACION Y EXTRANJERIA", "es", "CENTROAMERICA"),
}
# Brazil's own clients come first in this firm; every other country is covered evenly
WEIGHTS = {"BR": 4, "HT": 2, "DO": 2, "CO": 2, "EC": 2, "VE": 2, "GT": 2, "HN": 2, "SV": 2}

NAMES = {
    "es": (["MARIA JOSE", "JUAN CARLOS", "ANDRES", "SANTIAGO", "VALENTINA", "CAMILA", "SOFIA", "DIEGO", "ALEJANDRO", "DANIELA", "LUIS FERNANDO",
            "JHON", "YEFERSON", "WILMER", "YULIANA", "KEVIN", "BRAYAN", "JOSUE", "ESTEFANIA", "NICOLAS", "MARIANA", "GABRIELA", "ANGEL", "CARLOS",
            "LUCIA", "FERNANDO", "ROSA", "ANA", "JOSE", "LUIS", "MIGUEL", "YESENIA", "DARWIN", "JEFFERSON", "ROSMERY", "MILAGROS"],
           ["GARCIA", "RODRIGUEZ", "MARTINEZ", "HERNANDEZ", "LOPEZ", "GONZALEZ", "PEREZ", "SANCHEZ", "RAMIREZ", "TORRES", "FLORES", "RIVERA",
            "GOMEZ", "DIAZ", "REYES", "MORALES", "CRUZ", "ORTIZ", "GUTIERREZ", "CHAVEZ", "RAMOS", "VARGAS", "CASTILLO", "JIMENEZ", "MENDOZA",
            "ROJAS", "QUISPE", "MAMANI", "CONDORI", "HUAMAN", "ALVAREZ", "ROMERO", "SUAREZ", "MEDINA", "AGUILAR", "VASQUEZ", "CASTRO", "PAREDES"]),
    "br": (["ANA", "JULIA", "BEATRIZ", "LARISSA", "GABRIELA", "FERNANDA", "JOAO", "PEDRO", "LUCAS", "GABRIEL", "MATHEUS", "RAFAEL", "KAUAN",
            "ISABELA", "THIAGO", "VINICIUS", "ALISSON", "LETICIA", "BRUNA"],
           ["SILVA", "SOUZA", "OLIVEIRA", "PEREIRA", "LIMA", "COSTA", "RODRIGUES", "ALMEIDA", "NASCIMENTO", "ARAUJO", "RIBEIRO", "CARVALHO",
            "GOMES", "MARTINS", "ROCHA", "FERREIRA", "TEIXEIRA", "CAMPOS", "MOREIRA", "CARDOSO", "VIEIRA", "MONTEIRO"]),
    "ht": (["JEAN", "PIERRE", "MARIE", "JOSEPH", "LOUIS", "CHARLES", "ROSE", "NADEGE", "WIDLINE", "JUNIOR", "WOODLEY", "STEVENSON", "MIRLANDE",
            "KETTELY", "FRITZNER", "GUERLINE", "JEAN ROBERT", "MARIE CLAIRE", "WILNER", "DAPHNEE", "JUDE", "ESTHER", "SAMUEL", "LOVELY"],
           ["JEAN-BAPTISTE", "PIERRE", "JOSEPH", "LOUIS", "CHARLES", "ETIENNE", "AUGUSTIN", "DESIR", "ALEXIS", "CELESTIN", "DORVIL", "FLEURANT",
            "SAINT-FLEUR", "JEUNE", "PAUL", "VALCOURT", "BAZILE", "NOEL", "FRANCOIS", "BAPTISTE", "PETIT-FRERE", "DORCELY", "EXANTUS"]),
    "gy": (["DEVON", "SHONETTE", "RAVI", "PRIYA", "ANIL", "KEISHA", "ANDRE", "NADIRA", "SHAQUILLE", "VANESSA", "DEOKIE", "TROY", "ROXANNE"],
           ["PERSAUD", "SINGH", "RAMDASS", "WILLIAMS", "JOHNSON", "BOODHOO", "KHAN", "FRASER", "ALI", "HENRY", "MOHAMED", "THOMAS", "SAMAROO"]),
    "sr": (["RAJESH", "SHANTI", "DJOENED", "SITI", "ROY", "NATASHA", "MITCHEL", "IVANNA", "SANDEEP", "MELVIN", "SHARMILA", "KENNETH"],
           ["SOEKHAI", "RAMDIN", "KROMOSOETO", "PINAS", "ABRAHAMS", "WONG", "SAMSOEDIN", "BRUNINGS", "DEKKER", "MOHAN", "JAHANGIER", "KARIJO"]),
}
MESES = ["ENERO", "FEBRERO", "MARZO", "ABRIL", "MAYO", "JUNIO", "JULIO", "AGOSTO", "SEPTIEMBRE", "OCTUBRE", "NOVIEMBRE", "DICIEMBRE"]
MOIS = ["JANVIER", "FEVRIER", "MARS", "AVRIL", "MAI", "JUIN", "JUILLET", "AOUT", "SEPTEMBRE", "OCTOBRE", "NOVEMBRE", "DECEMBRE"]
MAANDEN = ["JANUARI", "FEBRUARI", "MAART", "APRIL", "MEI", "JUNI", "JULI", "AUGUSTUS", "SEPTEMBER", "OKTOBER", "NOVEMBER", "DECEMBER"]
MONTHS = ["JANUARY", "FEBRUARY", "MARCH", "APRIL", "MAY", "JUNE", "JULY", "AUGUST", "SEPTEMBER", "OCTOBER", "NOVEMBER", "DECEMBER"]


@lru_cache(maxsize=None)
def _places(iso: str) -> tuple[list[tuple[str, str]], list[float], list[str]]:
    """(place, region) pairs weighted toward where people live, and the regions."""
    if iso == "BR":
        data = json.loads((schema_path.path("law", "br_municipalities")).read_text(encoding="utf-8"))["municipios"]
        from extract.places import BR_UF

        pairs = [(m, BR_UF[ufs[0]]) for m, ufs in data.items()]
        return pairs, [1.0] * len(pairs), sorted(BR_UF.values())
    d = json.loads(schema_path.path("geo", iso).read_text(encoding="utf-8"))
    pairs, weights = [], []
    admin = ("ARRONDISSEMENT", "PROVINCIA", "MUNICIPIO", "DISTRITO DE", "CANTON", "PARROQUIA", "COMMUNE", "DEPARTAMENTO", "PARTIDO", "DISTRICT")
    for place, regs in d["places"].items():
        if place.startswith(admin):
            continue  # an administrative unit's label is not where someone was born
        for region, pop in regs[:1]:
            if pop > 0:
                pairs.append((place, region))
                weights.append(min(pop, 2_000_000) ** 0.5)
    return pairs, weights, list(d["regions"])


class Person:
    """One invented person from one country: name, birth, parents, places."""

    def __init__(self, r: random.Random, iso: str | None = None):
        self.r = r
        self.iso = iso or r.choices(list(PROFILES), [WEIGHTS.get(c, 1) for c in PROFILES])[0]
        self.iso3, self.header, self.lang, self.nationality, self.authority, names, self.union = PROFILES[self.iso]
        first, last = NAMES[names]
        self.first, self.last = first, last
        two = names in ("es", "br")  # two surnames where the culture uses them
        self.surname = " ".join(r.sample(last, 2 if two and r.random() < 0.8 else 1))
        self.given = r.choice(first) if r.random() < 0.6 else " ".join(r.sample(first, 2))
        self.father = f"{r.choice(first)} {self.surname.split()[0]} {r.choice(last) if two else ''}".strip()
        self.mother = f"{r.choice(first)} {(self.surname.split() + [r.choice(last)])[1] if two else r.choice(last)} {r.choice(last) if two else ''}".strip()
        pairs, weights, self.regions = _places(self.iso)
        self.city, self.region = r.choices(pairs, weights)[0]
        self.reg_city, self.reg_region = r.choices(pairs, weights)[0] if r.random() < 0.3 else (self.city, self.region)
        self.birth = (r.randint(1999, 2012), r.randint(1, 12), r.randint(1, 28))
        self.registered = (self.birth[0] + r.choice([0, 0, 0, 1, 2]), r.randint(1, 12), r.randint(1, 28))

    def digits(self, n: int) -> str:
        return "".join(self.r.choice("0123456789") for _ in range(n))

    def letters(self, n: int) -> str:
        return "".join(self.r.choice("ABCDEFGHIJKLMNOPQRSTUVWXYZ") for _ in range(n))

    def date(self, ymd: tuple[int, int, int] | None = None, style: str | None = None) -> str:
        y, m, d = ymd or (self.r.randint(2014, 2026), self.r.randint(1, 12), self.r.randint(1, 28))
        style = style or self.r.choice(["dmy", "dmy", "long", "mon"])
        if style == "dmy":
            return f"{d:02d}/{m:02d}/{y}"
        if style == "mon":
            return f"{d:02d} {['ENE', 'FEB', 'MAR', 'ABR', 'MAY', 'JUN', 'JUL', 'AGO', 'SEP', 'OCT', 'NOV', 'DIC'][m - 1]}/{['JAN', 'FEB', 'MAR', 'APR', 'MAY', 'JUN', 'JUL', 'AUG', 'SEP', 'OCT', 'NOV', 'DEC'][m - 1]} {y}"
        words = {"es": f"{d} DE {MESES[m - 1]} DE {y}", "fr": f"{d} {MOIS[m - 1]} {y}", "nl": f"{d} {MAANDEN[m - 1]} {y}",
                 "en": f"{d} {MONTHS[m - 1]} {y}", "pt": f"{d} DE {MESES[m - 1]} DE {y}"}
        return words[self.lang]

    def dob(self, style: str | None = None) -> str:
        return self.date(self.birth, style)

    def english(self, ymd: tuple[int, int, int]) -> str:
        return f"{MONTHS[ymd[1] - 1].title()} {ymd[2]}, {ymd[0]}"

    def some(self, lines: list[str], keep: float = 0.8) -> list[str]:
        return [ln for ln in lines if ln and self.r.random() < keep]

    def mrz(self, code: str = "P<") -> list[str]:
        a, b, c = self.birth
        name = self.surname.replace(" ", "<").replace("-", "<") + "<<" + self.given.replace(" ", "<")
        return [f"{code}{self.iso3}{name}".ljust(44, "<")[:44],
                f"{self.letters(2)}{self.digits(7)}{self.digits(1)}{self.iso3}{a % 100:02d}{b:02d}{c:02d}{self.digits(1)}{self.r.choice('MF')}"
                f"{self.digits(7)}".ljust(44, "<")]


# --- birth records ---------------------------------------------------------------------

def birth_record(p: Person) -> list[str]:
    r, iso = p.r, p.iso
    if r.random() < 0.3:
        return birth_translation(p)
    if iso == "CO":
        return p.some(["REPUBLICA DE COLOMBIA", "REGISTRADURIA NACIONAL DEL ESTADO CIVIL", "REGISTRO CIVIL DE NACIMIENTO", "Indicativo Serial",
                       p.digits(8), "NUIP", p.digits(10), "Datos de la oficina de registro", f"Clase: {r.choice(['REGISTRADURIA', 'NOTARIA', 'CONSULADO'])}",
                       f"Codigo {p.digits(5)}", f"Pais - Departamento - Municipio - Corregimiento: COLOMBIA - {p.reg_region} - {p.reg_city}",
                       "Datos del inscrito", "Primer Apellido", p.surname.split()[0], "Segundo Apellido", (p.surname.split() + [""])[1], "Nombre(s)",
                       p.given, "Fecha de nacimiento Ano Mes Dia", f"{p.birth[0]} {p.birth[1]:02d} {p.birth[2]:02d}", "Sexo", r.choice(["FEMENINO", "MASCULINO"]),
                       "Grupo sanguineo", r.choice(["O", "A", "B", "AB"]), "Factor RH", r.choice(["+", "-"]),
                       f"Lugar de nacimiento (Pais - Departamento - Municipio): COLOMBIA - {p.region} - {p.city}",
                       "Tipo de documento antecedente o Declaracion de testigos", "CERTIFICADO DE NACIDO VIVO", f"Numero certificado de nacido vivo {p.digits(9)}",
                       "Datos de la madre", f"Apellidos y nombres completos: {p.mother}", f"Documento de identificacion: CC {p.digits(10)}", "Nacionalidad: COLOMBIANA",
                       "Datos del padre", f"Apellidos y nombres completos: {p.father}", f"Documento de identificacion: CC {p.digits(10)}",
                       "Datos del declarante", "Fecha de inscripcion", p.date(p.registered, "dmy"), "Nombre y firma del funcionario que autoriza",
                       "ESPACIO PARA NOTAS", "ORIGINAL PARA LA OFICINA DE REGISTRO"])
    if iso == "PE":
        return p.some(["REGISTRO NACIONAL DE IDENTIFICACION Y ESTADO CIVIL", "RENIEC", "ACTA DE NACIMIENTO", f"N° {p.digits(10)}",
                       f"MUNICIPALIDAD DISTRITAL DE {p.reg_city}" if r.random() < 0.5 else "OFICINA REGISTRAL", "FECHA DE NACIMIENTO", p.dob("long"),
                       "HORA", f"{r.randint(0, 23):02d}:{r.randint(0, 59):02d}", "LUGAR", "LOCALIDAD", p.city, f"DEPARTAMENTO {p.region}",
                       f"PROVINCIA {p.city}", f"DISTRITO {p.city}", "SEXO", r.choice(["FEMENINO", "MASCULINO"]), "Primer Apellido", p.surname.split()[0],
                       "Segundo Apellido", (p.surname.split() + [""])[1], "Prenombres", p.given, "DATOS DE LOS PADRES", "PADRE", p.father, "MADRE", p.mother,
                       "Documento de Identidad", f"DNI {p.digits(8)}", "Domicilio de la madre", "FECHA DE REGISTRO", p.date(p.registered, "dmy"),
                       "OFICINA REGISTRAL", "DECLARANTE / VINCULO", "REGISTRADOR CIVIL", "OBSERVACIONES", "Firma del Declarante", "Impresion dactilar"])
    if iso == "DO":
        return p.some(["REPUBLICA DOMINICANA", "JUNTA CENTRAL ELECTORAL", f"OFICIALIA DEL ESTADO CIVIL DE LA {r.randint(1, 9)}RA. CIRCUNSCRIPCION DE {p.reg_city}",
                       r.choice(["ACTA DE NACIMIENTO", "EXTRACTO DE ACTA DE NACIMIENTO", "ACTA INEXTENSA DE NACIMIENTO"]), f"Libro No. {p.digits(5)}",
                       f"Folio No. {p.digits(4)}", f"Acta No. {p.digits(5)}", f"Ano {p.registered[0]}", f"Nombre(s): {p.given}", f"Apellido(s): {p.surname}",
                       f"Sexo: {r.choice(['FEMENINO', 'MASCULINO'])}", f"Fecha de Nacimiento: {p.dob('long')}", f"Lugar de Nacimiento: {p.city}, {p.region}",
                       f"Padre: {p.father}", f"Cedula: {p.digits(3)}-{p.digits(7)}-{p.digits(1)}", f"Madre: {p.mother}", "Nacionalidad: DOMINICANA",
                       "Declarante", "Oficial del Estado Civil", "CERTIFICO: Que la presente es fiel y conforme al original que reposa en los archivos a mi cargo",
                       "Registro Nacional del Estado Civil", "www.jce.gob.do", f"Codigo de verificacion {p.letters(4)}-{p.digits(6)}"])
    if iso == "HT":
        return p.some(["REPUBLIQUE D'HAITI", "MINISTERE DE LA JUSTICE ET DE LA SECURITE PUBLIQUE", "ETAT CIVIL", "EXTRAIT DES REGISTRES DE L'ETAT CIVIL",
                       "ACTE DE NAISSANCE", f"Registre: {r.choice(['A', 'B', 'C'])} {p.digits(2)}  Folio: {p.digits(3)}  No: {p.digits(4)}",
                       f"Commune de {p.reg_city}", f"Departement de l'{p.reg_region}" if p.reg_region == "OUEST" else f"Departement du {p.reg_region}",
                       f"L'an {p.registered[0]}, et le {p.registered[2]} {MOIS[p.registered[1] - 1]}, par devant nous {r.choice(p.first)} {r.choice(p.last)},",
                       f"Officier de l'Etat Civil de la commune de {p.reg_city}, a comparu le citoyen {p.father},",
                       f"lequel nous a declare que le {p.dob('long')} est ne(e) a {p.city} un enfant de sexe {r.choice(['feminin', 'masculin'])}",
                       f"qui a recu les prenoms de {p.given}", f"fils/fille de {p.father} et de {p.mother}",
                       "Et ont signe avec nous le present acte apres lecture faite.", "Pour extrait conforme", "Delivre conforme par nous Officier de l'Etat Civil",
                       "ARCHIVES NATIONALES D'HAITI", f"Port-au-Prince, le {p.date(style='long')}", "Timbre", "Le Directeur General"])
    if iso == "GY":
        return p.some(["CO-OPERATIVE REPUBLIC OF GUYANA", "GENERAL REGISTER OFFICE", "BIRTH CERTIFICATE", "CERTIFIED COPY OF AN ENTRY OF BIRTH",
                       f"Registration District: {p.reg_region}", f"Entry No. {p.digits(4)}", f"When and where born: {p.dob('long')}, {p.city}",
                       f"Name, if any: {p.given}", f"Sex: {r.choice(['FEMALE', 'MALE'])}", f"Name and surname of father: {p.father}",
                       f"Name and maiden surname of mother: {p.mother}", "Rank or profession of father", "Signature, description and residence of informant",
                       f"When registered: {p.date(p.registered, 'long')}", "Signature of Registrar", "Name entered after registration",
                       "I hereby certify that this is a true copy of the entry", "Registrar General", "GENERAL REGISTER OFFICE, GEORGETOWN"])
    if iso == "SR":
        return p.some(["REPUBLIEK SURINAME", "CENTRAAL BUREAU VOOR BURGERZAKEN", "UITTREKSEL UIT HET REGISTER VAN GEBOORTEN", "GEBOORTEAKTE",
                       f"District: {p.reg_region}", f"Aktenummer: {p.digits(4)}", f"In het jaar {p.registered[0]}", f"Geslachtsnaam: {p.surname}",
                       f"Voornamen: {p.given}", f"Geboren op: {p.dob('long')}", f"Geboorteplaats: {p.city}", f"Geslacht: {r.choice(['VROUWELIJK', 'MANNELIJK'])}",
                       f"Zoon/dochter van: {p.father} en {p.mother}", "De Ambtenaar van de Burgerlijke Stand", "Voor eensluidend uittreksel",
                       f"Paramaribo, {p.date(style='long')}", "Het Hoofd van het Centraal Bureau voor Burgerzaken"])
    if iso == "BR":
        return []  # Brazil: synthetic.birth_certificate's own (CNJ model) writer
    # the other Spanish-speaking registries: same shape, each with its own office
    office = {"AR": ("REGISTRO DEL ESTADO CIVIL Y CAPACIDAD DE LAS PERSONAS", "ACTA DE NACIMIENTO", f"Tomo {p.digits(2)} Acta N° {p.digits(4)} Ano {p.registered[0]}"),
              "BO": ("SERVICIO DE REGISTRO CIVIL - SERECI", "CERTIFICADO DE NACIMIENTO", f"Oficialia N° {p.digits(4)} Libro N° {p.digits(2)} Partida N° {p.digits(3)} Folio N° {p.digits(3)}"),
              "CL": ("SERVICIO DE REGISTRO CIVIL E IDENTIFICACION", "CERTIFICADO DE NACIMIENTO", f"Circunscripcion: {p.reg_city}  Nro. inscripcion: {p.digits(4)}  Registro: {r.choice(['S', 'E'])}  Ano: {p.registered[0]}"),
              "EC": ("DIRECCION GENERAL DE REGISTRO CIVIL, IDENTIFICACION Y CEDULACION", r.choice(["CERTIFICADO DE NACIMIENTO", "PARTIDA DE NACIMIENTO", "INSCRIPCION DE NACIMIENTO"]), f"Tomo {p.digits(1)} Pagina {p.digits(3)} Acta {p.digits(4)}"),
              "PY": ("DIRECCION GENERAL DEL REGISTRO DEL ESTADO CIVIL DE LAS PERSONAS", "CERTIFICADO DE NACIMIENTO", f"Oficina N° {p.digits(3)} Libro {p.digits(2)} Folio {p.digits(3)} Acta N° {p.digits(4)}"),
              "UY": ("DIRECCION GENERAL DEL REGISTRO DE ESTADO CIVIL", r.choice(["PARTIDA DE NACIMIENTO", "TESTIMONIO DE PARTIDA DE NACIMIENTO"]), f"Seccion {p.digits(2)} Acta N° {p.digits(4)} Ano {p.registered[0]}"),
              "VE": ("CONSEJO NACIONAL ELECTORAL - COMISION DE REGISTRO CIVIL Y ELECTORAL", r.choice(["ACTA DE NACIMIENTO", "PARTIDA DE NACIMIENTO"]), f"Acta N° {p.digits(4)} Unidad de Registro Civil Parroquia {p.reg_city}"),
              "GT": ("REGISTRO NACIONAL DE LAS PERSONAS - RENAP", "CERTIFICADO DE NACIMIENTO", f"CUI {p.digits(4)} {p.digits(5)} {p.digits(4)}"),
              "HN": ("REGISTRO NACIONAL DE LAS PERSONAS", r.choice(["CERTIFICACION DE ACTA DE NACIMIENTO", "ACTA DE NACIMIENTO"]), f"Tomo {p.digits(4)} Folio {p.digits(3)} Asiento {p.digits(4)}"),
              "SV": ("REGISTRO DEL ESTADO FAMILIAR", r.choice(["PARTIDA DE NACIMIENTO", "CERTIFICACION DE PARTIDA DE NACIMIENTO"]), f"Partida numero {p.digits(3)} Libro {p.digits(3)} Folio {p.digits(3)}"),
              "MX": ("REGISTRO CIVIL", "ACTA DE NACIMIENTO", f"CURP {p.letters(4)}{p.digits(6)}{p.letters(6)}{p.digits(2)}  Identificador electronico {p.digits(20)}"),
              "NI": ("REGISTRO DEL ESTADO CIVIL DE LAS PERSONAS", "CERTIFICADO DE NACIMIENTO", f"Partida N° {p.digits(4)} Tomo {p.digits(3)} Folio {p.digits(3)}")}[iso]
    return p.some([p.header, office[0], office[1], office[2], f"Nombre(s): {p.given}", f"Apellidos: {p.surname}", f"Fecha de nacimiento: {p.dob()}",
                   f"Lugar de nacimiento: {p.city}, {p.region}", f"Departamento/Provincia: {p.region}", f"Sexo: {r.choice(['FEMENINO', 'MASCULINO'])}",
                   f"Nombre del padre: {p.father}", f"Nombre de la madre: {p.mother}", f"Nacionalidad: {p.nationality}",
                   f"Fecha de inscripcion: {p.date(p.registered)}", "Oficial del Registro Civil", "Se expide el presente certificado a solicitud del interesado",
                   "Doy fe", f"{p.reg_city}, {p.date()}", r.choice(["Firma y sello", "Codigo de verificacion " + p.digits(10), ""])])


def birth_translation(p: Person) -> list[str]:
    """The certified English translation that comes with a foreign birth record."""
    r = p.r
    source = {"es": "Spanish", "pt": "Portuguese", "fr": "French", "nl": "Dutch", "en": "English"}[p.lang]
    country = {"AR": "ARGENTINE REPUBLIC", "BO": "PLURINATIONAL STATE OF BOLIVIA", "CL": "REPUBLIC OF CHILE", "CO": "REPUBLIC OF COLOMBIA",
               "EC": "REPUBLIC OF ECUADOR", "PY": "REPUBLIC OF PARAGUAY", "PE": "REPUBLIC OF PERU", "UY": "ORIENTAL REPUBLIC OF URUGUAY",
               "VE": "BOLIVARIAN REPUBLIC OF VENEZUELA", "HT": "REPUBLIC OF HAITI", "DO": "DOMINICAN REPUBLIC", "SR": "REPUBLIC OF SURINAME",
               "GT": "REPUBLIC OF GUATEMALA", "HN": "REPUBLIC OF HONDURAS", "SV": "REPUBLIC OF EL SALVADOR", "MX": "UNITED MEXICAN STATES",
               "NI": "REPUBLIC OF NICARAGUA", "GY": "CO-OPERATIVE REPUBLIC OF GUYANA", "BR": "FEDERATIVE REPUBLIC OF BRAZIL"}[p.iso]
    office = {"CO": "NATIONAL REGISTRY OF CIVIL STATUS", "PE": "NATIONAL REGISTRY OF IDENTIFICATION AND CIVIL STATUS", "DO": "CENTRAL ELECTORAL BOARD",
              "HT": "CIVIL REGISTRY -- EXTRACT FROM THE CIVIL STATUS RECORDS", "EC": "GENERAL DIRECTORATE OF CIVIL REGISTRY, IDENTIFICATION AND ID CARDS",
              "VE": "NATIONAL ELECTORAL COUNCIL -- CIVIL REGISTRY", "SR": "CENTRAL BUREAU OF CIVIL AFFAIRS"}.get(p.iso, "CIVIL REGISTRY")
    return p.some([f"[Translation from {source}]", country, office, r.choice(["BIRTH CERTIFICATE", "BIRTH RECORD", "CERTIFIED COPY OF BIRTH RECORD"]),
                   f"Book {p.digits(3)} Page {p.digits(3)} Entry No. {p.digits(4)}", f"Name: {p.given} {p.surname}", f"Date of birth: {p.english(p.birth)}",
                   f"Place of birth: {p.city}, {p.region}, {country.split(' OF ')[-1]}", f"Sex: {r.choice(['FEMALE', 'MALE'])}", f"Father's name: {p.father}",
                   f"Mother's name: {p.mother}", f"Date of registration: {p.english(p.registered)}", "Civil Registrar", "[seal]", "[illegible signature]",
                   "Certified to be a true copy of the original"], 0.85)


# --- marriage records ------------------------------------------------------------------

def marriage_record(p: Person) -> list[str]:
    r = p.r
    spouse = f"{r.choice(p.first)} {r.choice(p.last)} {r.choice(p.last)}"
    when = (r.randint(2018, 2025), r.randint(1, 12), r.randint(1, 28))
    if p.lang == "fr":
        return p.some([p.header, "EXTRAIT DES REGISTRES DE L'ETAT CIVIL", "ACTE DE MARIAGE", f"Commune de {p.reg_city}", f"Registre {p.digits(2)} Folio {p.digits(3)}",
                       f"L'an {when[0]}, le {when[2]} {MOIS[when[1] - 1]}", f"Ont comparu {p.given} {p.surname} et {spouse}",
                       "lesquels ont declare vouloir se prendre pour epoux", "Officier de l'Etat Civil", "Pour extrait conforme", "ARCHIVES NATIONALES D'HAITI"])
    if p.lang == "nl":
        return p.some([p.header, "CENTRAAL BUREAU VOOR BURGERZAKEN", "UITTREKSEL UIT HET REGISTER VAN HUWELIJKEN", "HUWELIJKSAKTE", f"Aktenummer {p.digits(4)}",
                       f"Op {p.date(when, 'long')} zijn in het huwelijk verbonden:", f"{p.given} {p.surname}", f"en {spouse}", "De Ambtenaar van de Burgerlijke Stand"])
    if p.lang == "en":
        return p.some([p.header, "GENERAL REGISTER OFFICE", "MARRIAGE CERTIFICATE", "CERTIFIED COPY OF AN ENTRY OF MARRIAGE", f"When married: {p.date(when, 'long')}",
                       f"Name and surname: {p.given} {p.surname}", f"Name and surname: {spouse}", "Condition", "Rank or profession", "Residence at the time of marriage",
                       "Married in the presence of us", "Registrar General"])
    return p.some([p.header, {"CO": "REGISTRADURIA NACIONAL DEL ESTADO CIVIL", "PE": "RENIEC", "DO": "JUNTA CENTRAL ELECTORAL"}.get(p.iso, "REGISTRO CIVIL"),
                   r.choice(["ACTA DE MATRIMONIO", "REGISTRO CIVIL DE MATRIMONIO", "CERTIFICADO DE MATRIMONIO", "PARTIDA DE MATRIMONIO"]),
                   f"Acta N° {p.digits(5)}", f"Fecha de celebracion: {p.date(when)}", f"Lugar: {p.reg_city}, {p.reg_region}", "DATOS DE LOS CONTRAYENTES",
                   f"Contrayente: {p.given} {p.surname}", f"Contrayente: {spouse}", f"Nacionalidad: {p.nationality}", "Regimen patrimonial", "Testigos",
                   "Oficial del Registro Civil", "Firma de los contrayentes"])


# --- passports and national identity cards ---------------------------------------------

_LABELS = {
    "es": ["PASAPORTE / PASSPORT", "Tipo / Type", "Codigo del pais / Country code", "Pasaporte N° / Passport No.", "Apellidos / Surnames", "Nombres / Given names",
           "Nacionalidad / Nationality", "Fecha de nacimiento / Date of birth", "Sexo / Sex", "Lugar de nacimiento / Place of birth",
           "Fecha de expedicion / Date of issue", "Fecha de vencimiento / Date of expiry", "Autoridad / Authority", "Firma del titular / Holder's signature"],
    "fr": ["PASSEPORT / PASSPORT", "Type", "Code du pays / Country code", "No. du passeport / Passport No.", "Nom / Surname", "Prenoms / Given names",
           "Nationalite / Nationality", "Date de naissance / Date of birth", "Sexe / Sex", "Lieu de naissance / Place of birth",
           "Date de delivrance / Date of issue", "Date d'expiration / Date of expiry", "Autorite / Authority", "Signature du titulaire"],
    "nl": ["PASPOORT / PASSPORT", "Type", "Code van het land / Country code", "Paspoortnummer / Passport No.", "Naam / Surname", "Voornamen / Given names",
           "Nationaliteit / Nationality", "Geboortedatum / Date of birth", "Geslacht / Sex", "Geboorteplaats / Place of birth",
           "Datum van afgifte / Date of issue", "Geldig tot / Date of expiry", "Autoriteit / Authority", "Handtekening houder"],
    "en": ["PASSPORT", "Type", "Country code", "Passport No.", "Surname", "Given names", "Nationality", "Date of birth", "Sex", "Place of birth", "Date of issue",
           "Date of expiry", "Authority", "Holder's signature"],
}


def passport_page(p: Person) -> list[str]:
    r = p.r
    L = _LABELS.get(p.lang, _LABELS["es"])
    values = ["", "P", p.iso3, f"{p.letters(r.choice([1, 2]))}{p.digits(7)}", p.surname, p.given, p.nationality, p.dob("mon"), r.choice("MF"),
              f"{p.city}" if r.random() < 0.6 else p.iso3, p.date(style="mon"), p.date((r.randint(2026, 2034), r.randint(1, 12), r.randint(1, 28)), "mon"),
              p.authority, ""]
    lines = [p.header, p.union] + [x for pair in zip(L, values) for x in pair]
    if r.random() < 0.3:
        lines.append({"es": "N° personal / Personal No.", "fr": "NIF / NIU", "nl": "ID-nummer", "en": "National ID No."}.get(p.lang, "N° personal") + f" {p.digits(9)}")
    return p.some(lines, 0.8) + p.mrz()


def national_id(p: Person) -> list[str]:
    """A national identity card: not a passport, a licence or a work permit -- "other"."""
    r = p.r
    title, office = {
        "CO": ("CEDULA DE CIUDADANIA", "REGISTRADURIA NACIONAL DEL ESTADO CIVIL"), "PE": ("DOCUMENTO NACIONAL DE IDENTIDAD", "RENIEC"),
        "EC": ("CEDULA DE IDENTIDAD", "REGISTRO CIVIL"), "AR": ("DOCUMENTO NACIONAL DE IDENTIDAD", "REGISTRO NACIONAL DE LAS PERSONAS"),
        "CL": ("CEDULA DE IDENTIDAD", "SERVICIO DE REGISTRO CIVIL E IDENTIFICACION"), "VE": ("CEDULA DE IDENTIDAD", "SAIME"),
        "DO": ("CEDULA DE IDENTIDAD Y ELECTORAL", "JUNTA CENTRAL ELECTORAL"), "HT": ("CARTE D'IDENTIFICATION NATIONALE", "OFFICE NATIONAL D'IDENTIFICATION"),
        "BO": ("CEDULA DE IDENTIDAD", "SEGIP"), "PY": ("CEDULA DE IDENTIDAD CIVIL", "POLICIA NACIONAL - DEPARTAMENTO DE IDENTIFICACIONES"),
        "UY": ("CEDULA DE IDENTIDAD", "DIRECCION NACIONAL DE IDENTIFICACION CIVIL"), "GY": ("NATIONAL IDENTIFICATION CARD", "GUYANA ELECTIONS COMMISSION"),
        "SR": ("IDENTITEITSKAART", "CENTRAAL BUREAU VOOR BURGERZAKEN"), "BR": ("CARTEIRA DE IDENTIDADE", "SECRETARIA DE SEGURANCA PUBLICA"),
        "MX": ("CREDENCIAL PARA VOTAR", "INSTITUTO NACIONAL ELECTORAL"), "GT": ("DOCUMENTO PERSONAL DE IDENTIFICACION", "RENAP"),
        "HN": ("DOCUMENTO NACIONAL DE IDENTIFICACION", "REGISTRO NACIONAL DE LAS PERSONAS"), "SV": ("DOCUMENTO UNICO DE IDENTIDAD", "RNPN"),
        "NI": ("CEDULA DE IDENTIDAD", "CONSEJO SUPREMO ELECTORAL"),
    }[p.iso]
    labels = {"fr": ["Nom", "Prenom", "Date de naissance", "Lieu de naissance", "Sexe", "NIU"], "nl": ["Naam", "Voornamen", "Geboortedatum", "Geboorteplaats", "Geslacht", "ID-nummer"],
              "en": ["Surname", "Given names", "Date of birth", "Place of birth", "Sex", "ID No."], "pt": ["NOME", "FILIACAO", "DATA DE NASCIMENTO", "NATURALIDADE", "SEXO", "REGISTRO GERAL"]}
    L = labels.get(p.lang, ["APELLIDOS", "NOMBRES", "FECHA DE NACIMIENTO", "LUGAR DE NACIMIENTO", "SEXO", "NUMERO"])
    number = {"CO": f"{p.digits(10)}", "CL": f"{p.digits(2)}.{p.digits(3)}.{p.digits(3)}-{p.digits(1)}", "VE": f"V-{p.digits(8)}", "DO": f"{p.digits(3)}-{p.digits(7)}-{p.digits(1)}",
              "MX": f"CURP {p.letters(4)}{p.digits(6)}{p.letters(6)}{p.digits(2)}"}.get(p.iso, p.digits(9))
    return p.some([p.header, title, office, L[0], p.surname, L[1], p.given, L[2], p.dob("dmy"), L[3], f"{p.city} {p.region}", L[4], r.choice("MF"), L[5], number,
                   r.choice(["FECHA DE EXPEDICION", "FECHA DE EMISION", "Date d'emission", "Datum van afgifte", "DATA DE EXPEDICAO"]), p.date(style="dmy"),
                   r.choice(["FECHA DE VENCIMIENTO", "Date d'expiration", "Geldig tot", "VALIDADE"]), p.date((r.randint(2026, 2035), 1, 1), "dmy"),
                   r.choice(["ESTATURA", "ESTADO CIVIL", "DOMICILIO", "Adresse", "Adres"]), r.choice(["SOLTERO(A)", "1.65", p.city]), "FIRMA", "Huella"])
