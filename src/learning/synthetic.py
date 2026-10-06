"""Training documents for the document-type classifier (learning/textcat.py),
made up -- not collected.

There is no public, labelled set of SIJS case documents (I-797 notices,
Brazilian birth certificates, Massachusetts dockets ...), and real people's
documents are not something to gather from the internet. So the training set
is written here: for each document type, the wording that type really carries
(taken from public specimens -- USCIS forms and the M-274 handbook, state
RMV/DMV samples, civil-registry layouts -- and from the kinds of OCR damage
seen on the firm's own scans), filled with invented names and numbers, then
damaged the way scans and phone photos damage text:

  - letters read as other letters (I/1, O/0, S/5, rn/m), lost accents,
    words run together, stray characters;
  - "garbage" lines from photos, stamps and backgrounds;
  - lines lost, blocks read out of order, the top of the page cut off
    (where the rules fail: docs/learning.md).

The firm's real documents are never used to train -- only to test
(tools/train_textcat.py). Every value is invented: names come from common
first names and surnames, SSNs from the 9xx-0x range that is never issued,
A-numbers and receipt numbers at random.
"""

from __future__ import annotations

import random
import string
import unicodedata
from typing import Callable

VERSION = "s6"  # s3: every client country (learning/synthetic_countries.py)  # bump when the generator changes: models record which data trained them

FIRST = ["ANA", "CLARA", "JULIA", "BEATRIZ", "LARISSA", "GABRIELA", "FERNANDA", "CAMILA", "LETICIA", "BRUNA", "JOAO", "PEDRO", "LUCAS",
         "GABRIEL", "MATHEUS", "RAFAEL", "GUILHERME", "FELIPE", "THIAGO", "VINICIUS", "JOSE", "CARLOS", "LUIS", "JUAN", "MARCOS", "KEVIN",
         "BRAYAN", "JOSUE", "DANIELA", "KATHERINE", "YESENIA", "MARLON", "EDGAR", "WILLIAM", "ALISSON", "KAUAN", "ISABELA", "SOFIA"]
LAST = ["SILVA", "SOUZA", "OLIVEIRA", "PEREIRA", "LIMA", "COSTA", "RODRIGUES", "ALMEIDA", "NASCIMENTO", "ARAUJO", "RIBEIRO", "CARVALHO",
        "GOMES", "MARTINS", "ROCHA", "BARBOSA", "LOPEZ", "HERNANDEZ", "GARCIA", "MARTINEZ", "RAMIREZ", "CRUZ", "REYES", "MORALES", "ORTIZ",
        "CASTILLO", "MENDOZA", "AGUILAR", "FERREIRA", "TEIXEIRA", "CAMPOS", "MOREIRA", "CARDOSO", "VIEIRA", "MONTEIRO", "PAIVA"]
COUNTRIES = [("BRA", "BRAZIL", "BRASIL", "pt"), ("GTM", "GUATEMALA", "GUATEMALA", "es"), ("HND", "HONDURAS", "HONDURAS", "es"),
             ("SLV", "EL SALVADOR", "EL SALVADOR", "es"), ("MEX", "MEXICO", "MEXICO", "es"), ("ECU", "ECUADOR", "ECUADOR", "es"),
             ("COL", "COLOMBIA", "COLOMBIA", "es")]
TOWNS_BR = ["GOVERNADOR VALADARES", "BELO HORIZONTE", "IPATINGA", "VITORIA", "SAO PAULO", "GOIANIA", "CRICIUMA", "TEOFILO OTONI"]
TOWNS_ES = ["HUEHUETENANGO", "SAN PEDRO SULA", "TEGUCIGALPA", "SAN MIGUEL", "QUETZALTENANGO", "GUAYAQUIL", "CUENCA", "SANTA ANA"]
TOWNS_US = [("FRAMINGHAM", "MA", "01702"), ("MARLBOROUGH", "MA", "01752"), ("EVERETT", "MA", "02149"), ("SOMERVILLE", "MA", "02143"),
            ("LOWELL", "MA", "01852"), ("REVERE", "MA", "02151"), ("NEWARK", "NJ", "07105"), ("DANBURY", "CT", "06810"),
            ("POMPANO BEACH", "FL", "33060"), ("WORCESTER", "MA", "01604")]
STREETS = ["MAIN ST", "SUMMER ST", "WASHINGTON ST", "PLEASANT ST", "HIGHLAND AVE", "BROADWAY", "CENTRAL ST", "ELM ST", "WATER ST"]
MONTHS = ["JAN", "FEB", "MAR", "APR", "MAY", "JUN", "JUL", "AUG", "SEP", "OCT", "NOV", "DEC"]
MONTHS_LONG = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
MESES = ["janeiro", "fevereiro", "marco", "abril", "maio", "junho", "julho", "agosto", "setembro", "outubro", "novembro", "dezembro"]
POSTS = ["SAO PAULO", "RIO DE JANEIRO", "BRASILIA", "GUATEMALA CITY", "TEGUCIGALPA", "SAN SALVADOR", "MEXICO CITY", "QUITO", "BOGOTA",
         "PORT-AU-PRINCE", "SANTO DOMINGO", "LIMA", "GUAYAQUIL", "CARACAS", "GEORGETOWN", "PARAMARIBO", "BUENOS AIRES", "SANTIAGO", "LA PAZ",
         "ASUNCION", "MONTEVIDEO", "MANAGUA"]
PORTS = ["BOS", "JFK", "MIA", "EWR", "IAH", "LAX", "ATL", "ORL", "HID", "SYS"]


class Faker:
    def __init__(self, rnd: random.Random):
        self.r = rnd
        self.country = rnd.choice(COUNTRIES)
        self.surname = " ".join(rnd.sample(LAST, rnd.choice([1, 2, 2, 3])))
        self.given = " ".join(rnd.sample(FIRST, rnd.choice([1, 2])))
        self.birth = (rnd.randint(1999, 2012), rnd.randint(1, 12), rnd.randint(1, 28))
        self.mother = f"{rnd.choice(FIRST)} {' '.join(rnd.sample(LAST, 2))}"
        self.father = f"{rnd.choice(FIRST)} {' '.join(rnd.sample(LAST, 2))}"
        self.a_number = f"{rnd.randint(200, 249)}{rnd.randint(0, 999999):06d}"
        self.town = rnd.choice(TOWNS_US)
        self.street = f"{rnd.randint(1, 299)} {rnd.choice(STREETS)}"

    def digits(self, n: int) -> str:
        return "".join(self.r.choice(string.digits) for _ in range(n))

    def letters(self, n: int) -> str:
        return "".join(self.r.choice(string.ascii_uppercase) for _ in range(n))

    def date(self, lo: int = 2015, hi: int = 2026, style: str | None = None, ymd: tuple[int, int, int] | None = None) -> str:
        y, m, d = ymd or (self.r.randint(lo, hi), self.r.randint(1, 12), self.r.randint(1, 28))
        style = style or self.r.choice(["us", "us", "br", "mon", "long", "iso"])
        return {"us": f"{m:02d}/{d:02d}/{y}", "br": f"{d:02d}/{m:02d}/{y}", "mon": f"{d:02d} {MONTHS[m - 1]} {y}",
                "long": f"{MONTHS_LONG[m - 1]} {d}, {y}", "iso": f"{y}-{m:02d}-{d:02d}",
                "monus": f"{MONTHS[m - 1]} {d:02d} {y}", "ext": f"{d} de {MESES[m - 1]} de {y}"}[style]

    def dob(self, style: str | None = None) -> str:
        return self.date(style=style, ymd=self.birth)

    def receipt(self) -> str:
        return self.r.choice(["IOE", "IOE", "EAC", "WAC", "SRC", "LIN", "MSC", "NBC"]) + self.digits(10)

    def ssn(self) -> str:  # 9xx-0x-xxxx: never issued as an SSN or an ITIN
        return f"9{self.digits(2)}-0{self.digits(1)}-{self.digits(4)}"

    def mrz_name(self) -> str:
        return self.surname.replace(" ", "<") + "<<" + self.given.replace(" ", "<")

    def town_home(self) -> str:
        return self.r.choice(TOWNS_BR if self.country[3] == "pt" else TOWNS_ES)


def _pick(r: random.Random, *options):
    return r.choice(options)


def _some(r: random.Random, lines: list[str], keep: float = 0.75) -> list[str]:
    return [ln for ln in lines if r.random() < keep]


def _mrz(f: Faker, code: str) -> list[str]:
    a, b, c = f.birth
    line1 = f"{code}{f.country[0]}{f.mrz_name()}".ljust(44, "<")[:44]
    line2 = f"{f.letters(2)}{f.digits(6)}<{f.digits(1)}{f.country[0]}{a % 100:02d}{b:02d}{c:02d}{f.digits(1)}{f.r.choice('MF')}{f.digits(7)}".ljust(44, "<")
    return [line1, line2]


# --- one writer per document type ----------------------------------------------------

def _person(f: Faker):
    from learning.synthetic_countries import Person

    return Person(f.r)


def passport(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.3:  # a visa/stamps page: little text, mostly stamps
        lines = _some(r, [_pick(r, "VISTOS / VISAS", "VISAS", "VISTOS", "OBSERVACOES"), f"ADMITTED {f.date(style='monus')}",
                          f"{r.choice(PORTS)} {r.randint(1000, 9999)}", "DHS", "U.S. CUSTOMS AND BORDER PROTECTION", "CBP",
                          f"CLASS {r.choice(['B2', 'B1/B2', 'WT'])}", f"UNTIL {f.date(style='mon')}", "U.S. IMMIGRATION", "ENTRADA", "SAIDA",
                          "AEROPORTO INTERNACIONAL DE GUARULHOS", "POLICIA FEDERAL", "DPF", f"{f.date(style='br')}", "DEPARTED",
                          f"{r.randint(2, 32)}", "IMMIGRATION OFFICER"], 0.45)
        return lines or ["ADMITTED"]
    if r.random() < 0.6:  # any country of ours: its header, labels in its language, its MRZ code
        from learning.synthetic_countries import passport_page

        return passport_page(_person(f))
    iso, eng, nat, lang = f.country
    if lang == "pt":
        head = ["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE", "PASSPORT", "TIPO / TYPE", "P", "PAIS EMISSOR / ISSUING COUNTRY", iso,
                "NUMERO DO PASSAPORTE / PASSPORT No.", f"{f.letters(2)}{f.digits(6)}", "SOBRENOME / SURNAME", f.surname, "NOME / GIVEN NAMES",
                f.given, "NACIONALIDADE / NATIONALITY", "BRASILEIRO(A)", "DATA DE NASCIMENTO / DATE OF BIRTH", f.dob("mon"),
                "SEXO / SEX", r.choice("MF"), "NATURALIDADE / PLACE OF BIRTH", f.town_home(), "FILIACAO / FILIATION", f.father, f.mother,
                "DATA DE EXPEDICAO / DATE OF ISSUE", f.date(2014, 2022, "mon"), "VALIDO ATE / DATE OF EXPIRY", f.date(2024, 2032, "mon"),
                "AUTORIDADE / AUTHORITY", _pick(r, "DPF", "POLICIA FEDERAL", "SR/PF/MG")]
    elif r.random() < 0.15:
        head = ["REPUBBLICA ITALIANA", "PASSAPORTO", "PASSPORT", "PASSEPORT", "Tipo. Type. Type", "P", "Codice Paese", "ITA",
                "Cognome. Surname. Nom", f.surname, "Nome. Given Names. Prenoms", f.given, "Cittadinanza. Nationality", "ITALIANA",
                "Data di nascita. Date of birth", f.dob("mon"), "Sesso. Sex", r.choice("MF"), "Luogo di nascita. Place of birth", f.town_home(),
                "Data di rilascio. Date of issue", f.date(2016, 2023, "mon"), "Autorita. Authority", "MINISTRO AFFARI ESTERI",
                "Data di scadenza. Date of expiry", f.date(2025, 2033, "mon")]
    else:
        head = [f"REPUBLICA DE {nat}", "PASAPORTE", "PASSPORT", "Tipo / Type", "P", "Codigo del pais / Country code", iso,
                "Pasaporte No. / Passport No.", f"{f.letters(1)}{f.digits(8)}", "Apellidos / Surname", f.surname, "Nombres / Given names",
                f.given, "Nacionalidad / Nationality", f"{eng.title()}{_pick(r, '', 'A', 'O')}", "Fecha de nacimiento / Date of birth",
                f.dob("mon"), "Sexo / Sex", r.choice("MF"), "Lugar de nacimiento / Place of birth", f.town_home(),
                "Fecha de expedicion / Date of issue", f.date(2016, 2023, "mon"), "Fecha de vencimiento / Date of expiry",
                f.date(2025, 2033, "mon"), "Autoridad / Authority", _pick(r, "DIRECCION GENERAL DE MIGRACION", "INSTITUTO GUATEMALTECO DE MIGRACION",
                                                                          "SECRETARIA DE RELACIONES EXTERIORES")]
    return _some(r, head, 0.8) + _mrz(f, "P<")


def visa(f: Faker) -> list[str]:
    r = f.r
    lines = ["UNITED STATES OF AMERICA", "VISA", "Issuing Post Name", r.choice(POSTS), "Control Number", f"{r.randint(2015, 2023)}{f.digits(9)}",
             "Surname", f.surname, "Given Name", f.given, "Visa Type/Class", _pick(r, "R B1/B2", "R B2", "F1", "R B1/B2"), "Entries", "M",
             "Passport Number", f"{f.letters(2)}{f.digits(6)}", "Sex", r.choice("MF"), "Birth Date", f.dob("mon"), "Nationality",
             f.country[0], "Issue Date", f.date(2015, 2023, "mon"), "Expiration Date", f.date(2020, 2033, "mon"), "Annotation",
             _pick(r, "", "BCC", f"PET RCPT {f.receipt()}", "FATHER: " + f.father)]
    return _some(r, lines, 0.8) + [f"V{_pick(r, '<', 'N')}USA{f.mrz_name()}".ljust(44, "<"),
                                   f"{f.digits(9)}{f.digits(1)}{f.country[0]}{f.digits(7)}{r.choice('MF')}{f.digits(7)}".ljust(44, "<")]


def i94(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.3:
        lines = ["I-94/I-95 Official Website - Get Travel History", "Travel History", "U.S. Customs and Border Protection",
                 "Based on the information you submitted, below is the travel history for", f"{f.given} {f.surname}",
                 "Date Type Location", *[f"{f.date(style='iso')} {r.choice(['Arrival', 'Departure'])} {r.choice(PORTS)}" for _ in range(r.randint(1, 4))],
                 "Disclaimer: This travel history is provided for your information only"]
        return _some(r, lines, 0.85)
    lines = ["I-94/I-95 Official Website - Get Most Recent Response", "U.S. Customs and Border Protection", "Most Recent I-94",
             "Admission I-94 Record Number", f"{f.digits(11)}{_pick(r, '', ' A3')}", "Most Recent Date of Entry:", f.date(style="long"),
             "Arrival/Issued Date", f.date(style="iso"), "Class of Admission:", _pick(r, "B2", "B1", "WT", "F1"), "Admit Until Date:",
             f.date(style="us"), "Details provided on the I-94 Information form:", "Last/Surname:", f.surname, "First (Given) Name:",
             f.given, "Birth Date:", f.dob("us"), "Document Number:", f"{f.letters(2)}{f.digits(6)}", "Country of Citizenship:",
             f.country[1].title(), "Get This Traveler's Travel History", "Effective April 26, 2013, DHS began automating the admission process.",
             "An alien lawfully admitted or paroled into the U.S. is no longer required to be in possession of a preprinted Form I-94.",
             "A record of admission printed from the CBP website constitutes a lawful record of admission. See 8 CFR 1.4(d).",
             "If an employer, local, state or federal agency requests admission information, present your admission (I-94) number"]
    return _some(r, lines, 0.8)


def _i797_frame(f: Faker, case_type: str, notice_type: str, body: list[str]) -> list[str]:
    r = f.r
    top = [_pick(r, "Department of Homeland Security", "Department of Homeland Security U.S. Citizenship and Immigration Services"),
           "U.S. Citizenship and Immigration Services", _pick(r, "Form I-797, Notice of Action", "I-797C, Notice of Action", "I-797, Notice of Action"),
           "THE UNITED STATES OF AMERICA", "NOTICE OF ACTION", "THIS NOTICE DOES NOT GRANT ANY IMMIGRATION STATUS OR BENEFIT.",
           "Receipt Number", f.receipt(), "Case Type", case_type, "Received Date", f.date(style="us"), "Priority Date", f.date(style="us"),
           "Notice Date", f.date(style="us"), "Page", "1 of 1", _pick(r, "Petitioner", "Applicant"), f"{f.given} {f.surname}",
           _pick(r, "Beneficiary", ""), f"A{f.a_number}", f"{f.given} {f.surname}", "c/o Example Immigration Office" if r.random() < 0.3 else "",
           f.street, f"{f.town[0]} {f.town[1]} {f.town[2]}", "Notice Type:", notice_type]
    tail = ["Please see the additional information on the back. You will be notified separately about any other cases you filed.",
            _pick(r, "National Benefits Center", "Nebraska Service Center", "Texas Service Center", "Vermont Service Center",
                  "Potomac Service Center"), "U.S. CITIZENSHIP AND IMMIGRATION SVC", "P.O. Box 82521", "Lincoln NE 68501-2521",
            "USCIS Contact Center: www.uscis.gov/contactcenter", "If you have questions, contact the USCIS Contact Center at 800-375-5283",
            "Form I-797 (Rev. 09/07/93) N", "Form I-797C 07/11/14",
            "If you are represented by an attorney or accredited representative, Form G-28, Notice of Entry of Appearance, must be on file.",
            "NOTICE: Although this application or petition has been approved, USCIS and the U.S. Department of Homeland Security reserve the right to verify the information submitted",
            "Additional Information", "Freedom of Information Act and Privacy Act", "Signature verified, card produced"]
    return _some(r, top, 0.8) + body + _some(r, tail, 0.55)


# The standard wording on the back half of every USCIS petition approval (I-360, I-130 ...):
# the same for all of them, so it says "approval notice", never which petition.
_APPROVED = ["The above petition has been approved.",
             "The petition indicates that the person the petition is for is in the United States and will apply for adjustment of status.",
             "He or she should access the USCIS website at USCIS.gov/i-485 to obtain Form I-485, Application for Permanent Residence.",
             "A copy of this notice should be submitted with the application.",
             "If the person for whom you are petitioning decides to apply for a visa outside the United States based on this petition,",
             "the petitioner should file Form I-824, Application for Action on an Approved Application or Petition,",
             "to request that we send the petition to the Department of State National Visa Center (NVC).",
             "The NVC processes all approved immigrant visa petitions that require consular action.",
             "The approval of this visa petition does not in itself grant any immigration status and does not guarantee that the alien beneficiary",
             "will subsequently be found to be eligible for a visa, for admission to the United States, or for an extension, change, or adjustment of status.",
             "Please read the back of this form carefully for more information.", "THIS NOTICE IS NOT A VISA AND MAY NOT BE USED IN PLACE OF A VISA.",
             "NOTICE: Although this application/petition has been conditionally approved, USCIS and the U.S. Department of Homeland Security reserve the right",
             "to verify the information submitted in the application, petition, and/or supporting documentation",
             "USCIS encourages you to sign up for a USCIS online account."]


def i360_approval(f: Faker) -> list[str]:
    r = f.r
    case = _pick(r, "I360 - PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT", "I-360, Petition for Amerasian, Widow(er), or Special Immigrant",
                 "I360 PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT")
    body = _some(r, ["Approval Notice", _pick(r, "Section: Special Immigrant Juvenile", "Section: Special Immigrant-Juvenile"),
                     _pick(r, "Class: SL6", "Class: SL1", "Classification: 203(b)(4) INA SPECIAL IMMIGRANT JUVENILE"),
                     "Consulate: NATIONAL VISA CENTER" if r.random() < 0.3 else "Consulate: NONE",
                     "The above petition has been approved.", "The petition indicates that the person for whom you are petitioning is in the United States",
                     "and will apply for adjustment of status. He or she should contact the local USCIS office to obtain Form I-485",
                     "You have been granted deferred action as a Special Immigrant Juvenile (SIJ).",
                     "Deferred action is an act of prosecutorial discretion to defer removal action against an alien for a certain period of time.",
                     "SIJ deferred action period of four years", "You may request employment authorization by filing Form I-765 under category (c)(14).",
                     "Beneficiary's date of birth " + f.dob("us")], 0.75)
    return _i797_frame(f, case, "Approval Notice", body + (_some(r, _APPROVED, 0.6) if r.random() < 0.6 else []))


def uscis_notice(f: Faker) -> list[str]:
    r = f.r
    kind = r.choice(["i485_receipt", "i765_receipt", "biometrics", "rfe", "transfer", "i130_approval", "i765_approval", "interview"])
    if kind == "i485_receipt":
        return _i797_frame(f, "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS", "Receipt", _some(r, [
            "Receipt Notice", "Amount received: $1,440.00 U.S.", "Section: Adjustment as Special Immigrant Juvenile",
            "This notice confirms that USCIS received your application or petition (\"this case\") as shown above.",
            "If any of the above information is incorrect, please call the USCIS Contact Center immediately.",
            "We will notify you separately about any other applications or petitions you filed.",
            "Biometrics: we will mail you a biometrics appointment notice"], 0.8))
    if kind in ("i765_receipt", "i765_approval"):
        body = ["Receipt Notice", "Amount received: $520.00", "Eligibility Category: c14", "Your application was received"] if kind == "i765_receipt" else \
            ["Approval Notice", "Class: (c)(14)", "Valid from " + f.date(style="us") + " to " + f.date(style="us"),
             "The above application has been approved. Your new card will be mailed separately.", "card will be mailed to the address above"]
        return _i797_frame(f, "I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION", body[0], _some(r, body, 0.85))
    if kind == "i130_approval":
        return _i797_frame(f, "I130 - PETITION FOR ALIEN RELATIVE", "Approval Notice", _some(r, [
            "Approval Notice", "Section: Unmarried son or daughter of U.S. Citizen, 203(a)(1) INA", "The above petition has been approved.",
            "We have sent the original visa petition to the Department of State National Visa Center (NVC)", "Class: " + _pick(r, "F11", "F21", "IR2")],
            0.85) + (_some(r, _APPROVED, 0.6) if r.random() < 0.6 else []))
    if kind == "biometrics":
        return _i797_frame(f, _pick(r, "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS", "I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION"),
                           "ASC Appointment Notice", _some(r, [
                               "ASC APPOINTMENT NOTICE", "Biometrics Appointment", "To process your case, USCIS must collect your biometrics",
                               "PLEASE APPEAR AT THE BELOW APPLICATION SUPPORT CENTER AT THE DATE AND TIME SPECIFIED.",
                               f"USCIS {f.town[0]} ASC", f"{f.street}", f"{f.town[0]} {f.town[1]} {f.town[2]}",
                               f"Date: {f.date(style='us')}", f"Time: {r.randint(8, 3 + 12) % 12 + 1}:{r.choice(['00', '30'])} {r.choice(['AM', 'PM'])}",
                               "WHEN YOU GO TO THE APPLICATION SUPPORT CENTER TO HAVE YOUR BIOMETRICS TAKEN, YOU MUST BRING:",
                               "1. THIS APPOINTMENT NOTICE and 2. VALID PHOTO IDENTIFICATION", "fingerprints, photograph and signature",
                               "If you do not have any of the required items, you will not be serviced."], 0.8))
    if kind == "rfe":
        return _i797_frame(f, _pick(r, "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS", "I360 - PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT"),
                           "Request for Evidence", _some(r, [
                               "REQUEST FOR EVIDENCE", "Request for Initial Evidence", "Additional evidence is required to process this case.",
                               "Please submit the evidence listed below by " + f.date(style="long"),
                               "Your response must be received in this office by the date shown above.",
                               "Submit Form I-693, Report of Immigration Medical Examination and Vaccination Record",
                               "Place this cover sheet on top of your response", "Failure to respond may result in denial."], 0.8))
    if kind == "interview":
        return _i797_frame(f, "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS", "Interview Notice", _some(r, [
            "REQUEST FOR APPLICANT TO APPEAR FOR INITIAL INTERVIEW", "You are hereby notified to appear for the interview appointment",
            f"USCIS {f.town[0]} Field Office", f"On {f.date(style='long')} at {r.randint(7, 11)}:{r.choice(['00', '15', '30'])} AM",
            "Failure to appear for this interview may result in the denial of your application", "Bring this letter with you"], 0.85))
    return _i797_frame(f, _pick(r, "I485 - APPLICATION TO REGISTER PERMANENT RESIDENCE OR ADJUST STATUS", "I765 - APPLICATION FOR EMPLOYMENT AUTHORIZATION"),
                       "Transfer Notice", _some(r, [
                           "TRANSFER NOTICE", "The above case has been transferred to another USCIS office for processing.",
                           "The office now handling your case is the " + _pick(r, "National Benefits Center", "Boston Field Office", "Potomac Service Center"),
                           "Please send any correspondence about this case to the office shown below."], 0.85))


def birth_certificate(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.65:  # each country's own registry and format (Brazil keeps the writer below)
        from learning.synthetic_countries import birth_record

        lines = birth_record(_person(f))
        if lines:
            return lines
    style = r.choice(["br", "br", "br_en", "es", "es_en"]) if f.country[3] == "pt" or r.random() < 0.5 else r.choice(["es", "es_en"])
    if style.startswith("br") and f.country[3] != "pt":
        style = "es" + style[2:]
    name = f"{f.given} {f.surname}"
    if style == "br":
        lines = ["REPUBLICA FEDERATIVA DO BRASIL", "REGISTRO CIVIL DAS PESSOAS NATURAIS", "CERTIDAO DE NASCIMENTO", "NOME:", name, "CPF",
                 f"{f.digits(3)}.{f.digits(3)}.{f.digits(3)}-{f.digits(2)}", "MATRICULA:",
                 f"{f.digits(6)} {f.digits(2)} 55 {f.birth[0]} 1 {f.digits(5)} {f.digits(3)} {f.digits(7)} {f.digits(2)}",
                 "DATA DE NASCIMENTO POR EXTENSO", f.dob("ext"), "DIA MES ANO", f.dob("br"), "HORA", f"{r.randint(0, 23):02d}:{r.randint(0, 59):02d}",
                 "MUNICIPIO DE REGISTRO E UNIDADE DA FEDERACAO", f"{f.town_home()} - MG", "LOCAL DE NASCIMENTO", "HOSPITAL MUNICIPAL",
                 "MUNICIPIO DE NASCIMENTO E UNIDADE DA FEDERACAO", f.town_home(), "SEXO", _pick(r, "FEMININO", "MASCULINO"), "FILIACAO",
                 f.father, f.mother, "AVOS", f"{r.choice(FIRST)} {r.choice(LAST)}, {r.choice(FIRST)} {r.choice(LAST)}", "GEMEO", "NAO",
                 "NUMERO DA DECLARACAO DE NASCIDO VIVO", f.digits(11), "DATA DO REGISTRO", f.date(2000, 2013, "ext"),
                 "AVERBACOES/ANOTACOES A ACRESCER", "NADA CONSTA", "ANOTACOES DE CADASTRO",
                 "O conteudo da certidao e verdadeiro. Dou fe.", f"CARTORIO DO REGISTRO CIVIL DE {f.town_home()}", "Oficial de Registro Civil",
                 "Selo digital", f"{f.letters(3)}{f.digits(5)}", "Valor total do ato: R$", "Consulte a validade do selo em www.tjmg.jus.br"]
    elif style == "br_en":
        lines = ["FEDERATIVE REPUBLIC OF BRAZIL", "CIVIL REGISTRY OF NATURAL PERSONS", "BIRTH CERTIFICATE", "NAME:", name,
                 "REGISTRATION NUMBER:", f"{f.digits(6)} {f.digits(2)} 55 {f.birth[0]} 1 {f.digits(5)} {f.digits(3)} {f.digits(7)} {f.digits(2)}",
                 "DATE OF BIRTH IN WORDS", f"{MONTHS_LONG[f.birth[1] - 1]} {f.birth[2]}, {f.birth[0]}", "DAY MONTH YEAR", f.dob("br"),
                 "TIME", f"{r.randint(0, 23):02d}:{r.randint(0, 59):02d}", "MUNICIPALITY OF REGISTRATION AND STATE", f.town_home(),
                 "PLACE OF BIRTH", "MUNICIPAL HOSPITAL", "SEX", _pick(r, "FEMALE", "MALE"), "PARENTAGE", f.father, f.mother,
                 "Father's Name", f.father, "Mother's Name", f.mother, "GRANDPARENTS", f"{r.choice(FIRST)} {r.choice(LAST)}", "TWIN", "NO",
                 "DATE OF REGISTRATION", f.date(2000, 2013, "long"), "ANNOTATIONS", "NOTHING TO DECLARE",
                 "The content of this certificate is true. I attest.", "Civil Registry Officer", "[seal]", "[signature]"]
    elif style == "es":
        lines = [f"REPUBLICA DE {f.country[1]}", _pick(r, "REGISTRO NACIONAL DE LAS PERSONAS", "REGISTRO DEL ESTADO FAMILIAR", "REGISTRO CIVIL"),
                 _pick(r, "CERTIFICADO DE NACIMIENTO", "CERTIFICACION DE ACTA DE NACIMIENTO", "PARTIDA DE NACIMIENTO", "ACTA DE NACIMIENTO"),
                 "RENAP" if f.country[0] == "GTM" else "", "Nombre del inscrito:", name, "Fecha de nacimiento:", f.dob("br"),
                 "Lugar de nacimiento:", f.town_home(), "Sexo:", _pick(r, "FEMENINO", "MASCULINO"), "Nombre de la madre:", f.mother,
                 "Nombre del padre:", f.father, "Libro", str(r.randint(1, 400)), "Folio", str(r.randint(1, 500)), "Partida", str(r.randint(1, 900)),
                 "Codigo Unico de Identificacion", f.digits(13), "Registrador Civil", "Se extiende la presente certificacion", "Doy fe"]
    else:
        lines = [f"REPUBLIC OF {f.country[1]}", _pick(r, "NATIONAL REGISTRY OF PERSONS", "CIVIL REGISTRY"), "BIRTH CERTIFICATE",
                 "Name of registered person:", name, "Date of birth:", f.dob("long"), "Place of birth:", f.town_home(), "Sex:",
                 _pick(r, "FEMALE", "MALE"), "Mother's Name:", f.mother, "Father's Name:", f.father, "Book", str(r.randint(1, 400)),
                 "Page", str(r.randint(1, 500)), "Entry", str(r.randint(1, 900)), "Civil Registrar", "[seal]", "[illegible signature]"]
    return _some(r, lines, 0.8)


def translation_certification(f: Faker) -> list[str]:
    r = f.r
    lang = _pick(r, "Portuguese", "Spanish", "Spanish", "French", "Haitian Creole", "Dutch")
    who = f"{r.choice(FIRST).title()} {r.choice(LAST).title()}"
    lines = [_pick(r, "TRANSLATION CERTIFICATION", "CERTIFICATE OF TRANSLATION", "CERTIFICATION OF TRANSLATION ACCURACY", "Translator's Certification"),
             _pick(r, f"I, {who}, certify that I am fluent (conversant) in the English and {lang} languages,",
                   f"I, {who}, hereby certify that I am competent to translate from {lang} into English,"),
             _pick(r, "and that the above/attached document is an accurate translation of the document attached entitled",
                   "and that the foregoing is a true and complete English translation of the original document"),
             _pick(r, "Birth Certificate.", "Certidao de Nascimento.", "Marriage Certificate.", "the attached document."),
             "I certify under penalty of perjury that the foregoing is true and correct.", "Translator's Name:", who, "Signature:",
             "[signature]", "Date:", f.date(style="us"), "Address:", f"{f.street}, {f.town[0]}, {f.town[1]} {f.town[2]}",
             "Telephone:", f"({f.digits(3)}) {f.digits(3)}-{f.digits(4)}", _pick(r, "", "Notary Public", "Subscribed and sworn before me")]
    return _some(r, lines, 0.85)


def marriage_certificate(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.5:
        from learning.synthetic_countries import marriage_record

        return marriage_record(_person(f))
    spouse = f"{r.choice(FIRST)} {' '.join(r.sample(LAST, 2))}"
    if r.random() < 0.5:
        lines = ["REPUBLICA FEDERATIVA DO BRASIL", "REGISTRO CIVIL DAS PESSOAS NATURAIS", "CERTIDAO DE CASAMENTO", "NOMES DOS CONJUGES",
                 f"{f.given} {f.surname}", spouse, "MATRICULA", f"{f.digits(6)} 01 55 {r.randint(2018, 2025)} 2 {f.digits(5)} {f.digits(3)} {f.digits(7)} {f.digits(2)}",
                 "DATA DE CELEBRACAO DO CASAMENTO", f.date(2018, 2025, "br"), "REGIME DE BENS", "COMUNHAO PARCIAL DE BENS",
                 "NOME QUE CADA UM DOS CONJUGES PASSOU A UTILIZAR", "AVERBACOES/ANOTACOES", "O conteudo da certidao e verdadeiro. Dou fe."]
    else:
        lines = [_pick(r, "THE COMMONWEALTH OF MASSACHUSETTS", "STATE OF NEW JERSEY", "STATE OF CONNECTICUT"),
                 _pick(r, "CERTIFICATE OF MARRIAGE", "MARRIAGE CERTIFICATE", "CERTIFIED COPY OF MARRIAGE RECORD"), "PARTY A", f"{f.given} {f.surname}",
                 "PARTY B", spouse, "Date of Birth", f.dob("us"), "Place of Birth", f.country[1], "Date of Marriage", f.date(2018, 2025, "us"),
                 "Place of Marriage", f.town[0], "Intention filed", f.date(2018, 2025, "us"), "Officiant", f"{r.choice(FIRST)} {r.choice(LAST)}",
                 "Justice of the Peace", "City Clerk", "I certify that this is a true copy of the record"]
    return _some(r, lines, 0.8)


def ssn_card(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.25:  # the letter the card comes on
        return _some(r, ["Social Security Administration", "Your Social Security Card", "Here is your Social Security card.",
                         "Keep your card in a safe place. Do not carry this card with you.", "Do not laminate your card.",
                         f"{f.given} {f.surname}", f.ssn(), "VALID FOR WORK ONLY WITH DHS AUTHORIZATION",
                         "If you need to change your name on your card", "www.socialsecurity.gov", "Form SSA-3000-U3"], 0.75)
    front = ["SOCIAL SECURITY", "UNITED STATES OF AMERICA", f.ssn(), _pick(r, "THIS NUMBER HAS BEEN ESTABLISHED FOR", ""), f"{f.given} {f.surname}",
             _pick(r, "VALID FOR WORK ONLY WITH DHS AUTHORIZATION", "VALID FOR WORK ONLY WITH DHS AUTHORIZATION", ""), "SIGNATURE"]
    back = ["This card belongs to the Social Security Administration and should be returned upon request.",
            "Improper use of this card or number by the card holder or any other person is punishable by fine, imprisonment or both.",
            "If you find a card that isn't yours, please return it to:", "Social Security Administration", "P.O. Box 33015, Baltimore, MD 21290",
            "For any other Social Security matters, contact your local Social Security office.", "Form SSA-3000"]
    return _some(r, front, 0.8) + (_some(r, back, 0.6) if r.random() < 0.6 else [])


def drivers_license(f: Faker) -> list[str]:
    r = f.r
    city, state, zipc = f.town
    state_name = {"MA": "MASSACHUSETTS", "NJ": "NEW JERSEY", "CT": "CONNECTICUT", "FL": "FLORIDA"}[state]
    kind = _pick(r, "DRIVER'S LICENSE", "DRIVER LICENSE", "LEARNER'S PERMIT", "IDENTIFICATION CARD", "LIMITED TERM LICENSE")
    lines = [state_name, kind, _pick(r, "NOT FOR FEDERAL ID", "", "LIMITED TERM", "USA"), f"4d LIC# {f.letters(1)}{f.digits(8)}",
             f"3 DOB {f.dob('us')}", f"4b EXP {f.date(2025, 2031, 'us')}", f"4a ISS {f.date(2021, 2025, 'us')}", f"1 {f.surname}", f"2 {f.given}",
             f"8 {f.street}", f"{city}, {state} {zipc}-{f.digits(4)}", f"15 SEX {r.choice('MF')}", f"16 HGT {r.randint(4, 6)}-{r.randint(0, 11):02d}",
             f"18 EYES {r.choice(['BRN', 'BLK', 'HAZ', 'GRN'])}", f"9 CLASS {r.choice(['D', 'D', 'M', 'C'])}", "9a END NONE", "12 REST NONE",
             f"5 DD {f.digits(10)}", _pick(r, "www.mass.gov/rmv", "Registry of Motor Vehicles", "Department of Motor Vehicles", "NJ Motor Vehicle Commission"),
             "CLASS D: Small vehicle less than 26,001 lbs, except school bus.", "ENDORSEMENTS - RESTRICTIONS -", "NONE NONE",
             _pick(r, "Corrective Lenses", "ORGAN DONOR", ""), "CHANGE OF ADDRESS. PRINT BELOW. PERMANENT INK.", f"Rev {f.date(2018, 2022, 'us')}"]
    return _some(r, lines, 0.65)


def work_permit(f: Faker) -> list[str]:
    r = f.r
    front = ["UNITED STATES OF AMERICA", "EMPLOYMENT AUTHORIZATION CARD", "Surname", f.surname, "Given Name", f.given,
             "USCIS#", f"{f.a_number[:3]}-{f.a_number[3:6]}-{f.a_number[6:]}", "Category", _pick(r, "C14", "C14", "C09", "C08", "C11"),
             "Card#", f"{f.letters(3)}{f.digits(10)}", "Country of Birth", f.country[1].title(), "Terms and Conditions", "None",
             "Date of Birth", f.dob("us"), "Sex", r.choice("MF"), "Valid From", f.date(2020, 2025, "us"), "Card Expires",
             f.date(2023, 2030, "us"), "NOT VALID FOR REENTRY TO U.S."]
    back = ["This card is not evidence of U.S. citizenship or permanent residence.",
            "This document is valid only for the period shown and may be revoked by the U.S. Government.",
            "The person identified by this card is authorized to work in the U.S. for the validity period of this card.",
            "If found, drop in any U.S. Mailbox. USPS: Mail to: DHS/USCIS, 1 Product Way, Lee's Summit, MO 64002",
            f"{r.choice('IL1')}AUSA{f.a_number}{f.digits(1)}{f.letters(3)}{f.digits(7)}<<".ljust(30, "<"),
            f"{f.birth[0] % 100:02d}{f.birth[1]:02d}{f.birth[2]:02d}{f.digits(1)}{r.choice('MF')}{f.digits(7)}{f.country[0]}<<<<<<<<<<<{f.digits(1)}",
            f.mrz_name().ljust(30, "<")[:30]]
    return _some(r, front, 0.7) + (_some(r, back, 0.7) if r.random() < 0.7 else [])


def notice_to_appear(f: Faker) -> list[str]:
    r = f.r
    kind = r.choice(["nta", "nta", "hearing", "order"])
    name = f"{f.given} {f.surname}"
    if kind == "nta":
        lines = ["U.S. DEPARTMENT OF HOMELAND SECURITY", "NOTICE TO APPEAR", "In removal proceedings under section 240 of the Immigration and Nationality Act:",
                 f"Subject ID: {f.digits(9)}", f"FINS: {f.digits(10)}", f"File No: {f.a_number}", f"DOB: {f.dob('us')}", f"Event No: {f.letters(3)}{f.digits(10)}",
                 "In the Matter of:", f"Respondent: {name}", "currently residing at:", f"{f.street} {f.town[0]} {f.town[1]} {f.town[2]}",
                 _pick(r, "You are an arriving alien.", "You are an alien present in the United States who has not been admitted or paroled.",
                       "You have been admitted to the United States, but are removable for the reasons stated below."),
                 "The Department of Homeland Security alleges that you:", "1. You are not a citizen or national of the United States;",
                 f"2. You are a native of {f.country[1]} and a citizen of {f.country[1]};",
                 f"3. You arrived in the United States at or near {r.choice(['Hidalgo, TX', 'El Paso, TX', 'San Ysidro, CA', 'Boston, MA'])} on or about {f.date(style='long')};",
                 "4. You were not then admitted or paroled after inspection by an Immigration Officer.",
                 "On the basis of the foregoing, it is charged that you are subject to removal from the United States pursuant to the following provision(s) of law:",
                 "212(a)(6)(A)(i) of the Immigration and Nationality Act, as amended",
                 "YOU ARE ORDERED to appear before an immigration judge of the United States Department of Justice at:",
                 f"{r.choice(['15 New Sudbury Street, Room 320, Boston MA', '26 Federal Plaza, New York NY', '1100 Commerce St, Dallas TX'])}",
                 "on a date to be set at a time to be set to show why you should not be removed from the United States",
                 "Form I-862 (6/22)", "Notice to Respondent", "Warning: Any statement you make may be used against you in removal proceedings.",
                 "Executive Office for Immigration Review"]
    elif kind == "hearing":
        lines = ["UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT", f"{f.town[0]}, {f.town[1]}",
                 "NOTICE OF HEARING IN REMOVAL PROCEEDINGS", f"RE: {name}", f"FILE: A{f.a_number}", f"DATE: {f.date(style='us')}",
                 "Please take notice that the above captioned case has been scheduled for a MASTER hearing before the Immigration Court on",
                 f"{f.date(style='long')} at {r.randint(8, 11)}:{r.choice(['00', '30'])} AM", "You may be represented in these proceedings, at no expense to the Government",
                 "Failure to appear at your hearing except for exceptional circumstances may result in one or more of the following actions:",
                 "You may be taken into custody by the Department of Homeland Security", "EOIR Automated Case Information 1-800-898-7180", "Form EOIR-28"]
    else:
        lines = ["UNITED STATES DEPARTMENT OF JUSTICE", "EXECUTIVE OFFICE FOR IMMIGRATION REVIEW", "IMMIGRATION COURT",
                 _pick(r, "ORDER OF THE IMMIGRATION JUDGE", "ORDER OF THE IMMIGRATION JUDGE ON MOTION"), f"Respondent: {name}", f"Case No.: A{f.a_number}",
                 "IN REMOVAL PROCEEDINGS", _pick(r, "It is HEREBY ORDERED that the proceedings be DISMISSED without prejudice.",
                                                 "Respondent's motion to dismiss/terminate proceedings is GRANTED.",
                                                 "These proceedings are administratively closed."),
                 "The respondent has an approved Form I-360 Special Immigrant Juvenile petition.", "Immigration Judge", f"Date: {f.date(style='us')}",
                 "CERTIFICATE OF SERVICE", "THIS DOCUMENT WAS SERVED BY: MAIL (M) PERSONAL SERVICE (P) ELECTRONIC SERVICE (E)", "TO: [ ] ALIEN [X] ALIEN'S ATT/REP [X] DHS"]
    return _some(r, lines, 0.8)


def criminal_record(f: Faker) -> list[str]:
    r = f.r
    name = f"{f.surname}, {f.given}"
    offence = _pick(r, "MOTOR VEH, UNLICENSED OPERATION OF c90 S10", "SHOPLIFTING BY ASPORTATION c266 S30A", "TRESPASS c266 S120",
                    "ASSAULT AND BATTERY c265 S13A(a)", "DISORDERLY CONDUCT c272 S53", "LARCENY UNDER $1200 c266 S30(1)")
    if r.random() < 0.65:
        lines = ["CRIMINAL DOCKET", f"DOCKET NUMBER {r.randint(18, 25)}{r.randint(10, 99)}CR{f.digits(6)}", "Trial Court of Massachusetts",
                 _pick(r, "District Court Department", "Boston Municipal Court Department", "Juvenile Court Department"),
                 "DEFENDANT NAME AND ADDRESS", name, f"{f.street} {f.town[0]}, {f.town[1]} {f.town[2]}", "DEFENDANT DOB", f.dob("us"),
                 "OFFENSE COUNTS", "OFFENSE DESCRIPTION", f"1 {offence}", "OFFENSE DATE", f.date(2019, 2025, "us"), "ARREST DATE", f.date(2019, 2025, "us"),
                 "POLICE DEPARTMENT", f"{f.town[0]} PD", "COMPLAINT ISSUED", "ARRAIGNMENT", "DISPOSITION",
                 _pick(r, "Dismissed at request of Commonwealth", "Nolle Prosequi", "Continued without a finding", "Dismissed - Not Guilty",
                       "Dismissed upon payment of court costs"), "DISPOSITION DATE", f.date(2019, 2025, "us"), "ATTORNEY",
                 f"{r.choice(FIRST)} {r.choice(LAST)}", "BAIL", "Personal recognizance", "PROBATION", "NONE", "Clerk-Magistrate",
                 "A TRUE COPY ATTEST:", "Docket entries"]
    else:
        lines = [_pick(r, f"{f.town[0]} POLICE DEPARTMENT", "INCIDENT REPORT", "ARREST REPORT"), _pick(r, "INCIDENT REPORT", "Booking Sheet", "CASE DISPOSITION"),
                 f"Incident #: {r.randint(18, 25)}-{f.digits(5)}", f"Arrestee: {name}", f"DOB: {f.dob('us')}", f"Charge: {offence}",
                 "Narrative:", "On the above date and time I was dispatched to", "Officer", "Badge #" + f.digits(4),
                 _pick(r, "Letter of dismissal: the charges against the above-named defendant were dismissed on",
                       "This letter confirms the disposition of the case", "Certified record of court disposition"),
                 f.date(style="long"), "Clerk's Office"]
    return _some(r, lines, 0.8)


def intake_questionnaire(f: Faker) -> list[str]:
    r = f.r
    if r.random() < 0.7:
        lines = ["Georges | Cote Law", "Questionario para Ajuste de Status", "I485 - SIJS", "AJUSTE DE STATUS", "Informacoes pessoais",
                 "Nome completo:", f"{f.given} {f.surname}", "Data de nascimento:", f.dob("br"), "Pais de nascimento:", f.country[2],
                 "Endereco atual:", f.street, "Telefone:", f"({f.digits(3)}) {f.digits(3)}-{f.digits(4)}", "Ja foi preso ou detido?", _pick(r, "Sim", "Nao"),
                 "Ja usou outros nomes?", "Nao", "Nome da mae:", f.mother, "Nome do pai:", f.father, "Altura:", "Peso:", "Cor dos olhos:",
                 "Cor do cabelo:", "Raca/Etnia:", "Data da ultima entrada nos EUA:", f.date(style="br"), "Escola atual:", "Empregos nos ultimos 5 anos:",
                 "Assinatura do cliente"]
    else:
        lines = ["Georges | Cote Law", "Cuestionario para Ajuste de Estatus", "I485 - SIJS", "Informacion personal", "Nombre completo:",
                 f"{f.given} {f.surname}", "Fecha de nacimiento:", f.dob("br"), "Pais de nacimiento:", f.country[2], "Direccion actual:", f.street,
                 "Ha sido arrestado alguna vez?", _pick(r, "Si", "No"), "Nombre de la madre:", f.mother, "Nombre del padre:", f.father,
                 "Estatura:", "Peso:", "Color de ojos:", "Fecha de la ultima entrada a EE.UU.:", f.date(style="br"), "Firma del cliente"]
    return _some(r, lines, 0.8)


def other(f: Faker) -> list[str]:
    """What else lands in a client's folder -- and the confusable cases a
    classifier must learn are *not* the types above (blank USCIS forms, letters)."""
    r = f.r
    name = f"{f.given} {f.surname}"
    kind = r.choice(["school", "medical", "letter", "i485_form", "g28_form", "i765_form", "i693",
                     "court_guardianship", "ma_complaint", "affidavit", "national_id", "national_id", "national_id"])
    if kind == "ma_complaint":
        return _some(r, _cjp35(f, name), 0.85)
    if kind == "national_id":  # a cedula, DNI or CIN: an identity card, but none of our types
        from learning.synthetic_countries import national_id

        return national_id(_person(f))
    pools = {
        "bank": ["Bank of America", "Account Statement", f"Account number: **** {f.digits(4)}", f"Statement period {f.date(style='long')}",
                 "Beginning balance", "Deposits and other additions", "Withdrawals and other subtractions", "Ending balance", "ATM WITHDRAWAL",
                 "ZELLE PAYMENT", "DEBIT CARD PURCHASE"],
        "school": [f"{f.town[0]} High School", "Official Transcript", "Student:", name, "Grade 10", "Course Title Credits Grade",
                   "ENGLISH LANGUAGE LEARNERS II", "ALGEBRA I", "BIOLOGY", "GPA", "Attendance record", "Principal", "Enrollment verification letter"],
        "bill": ["Eversource", "Your electricity bill", f"Account Number {f.digits(11)}", "Service address", f.street, "Amount due",
                 f"${r.randint(40, 300)}.{f.digits(2)}", "Due date", f.date(style="us"), "kWh used", "Pay online at"],
        "medical": ["Cambridge Health Alliance", "Immunization Record", "Patient:", name, "DOB:", f.dob("us"), "MMR", "Tdap", "Varicella",
                    "Hepatitis B", "COVID-19", "Visit summary", "Allergies: None known", "Provider signature"],
        "lease": ["RESIDENTIAL LEASE AGREEMENT", "Landlord:", f"{r.choice(FIRST)} {r.choice(LAST)}", "Tenant:", name, "Premises:", f.street,
                  "Monthly rent", f"${r.randint(900, 2500)}", "Term of lease", "Security deposit", "Signed this day of"],
        "paystub": ["EARNINGS STATEMENT", "Employee:", name, "Pay period", f.date(style="us"), "Gross pay", "Federal income tax",
                    "Social Security tax", "Medicare tax", "Net pay", "YTD"],
        "tax": ["Form 1040", "U.S. Individual Income Tax Return", "Department of the Treasury - Internal Revenue Service", "Filing Status",
                "Your first name and middle initial", "Wages, salaries, tips, etc. Attach Form(s) W-2", "Adjusted gross income",
                "Standard deduction", "Taxable income", "Refund"],
        "letter": [f"{f.date(style='long')}", "To Whom It May Concern:", f"This letter is to confirm that {name} is a member of our congregation",
                   "has attended our church regularly", "Igreja Batista", "Please do not hesitate to contact me", "Sincerely,", "Pastor"],
        "i485_form": ["Form I-485", "Application to Register Permanent Residence or Adjust Status", "Department of Homeland Security",
                      "OMB No. 1615-0023", "Part 1. Information About You", "Family Name (Last Name)", "Given Name (First Name)",
                      "Alien Registration Number (A-Number)", "Part 2. Application Type or Filing Category", "Special Immigrant Juvenile",
                      "Part 8. General Eligibility and Inadmissibility Grounds", "Applicant's Signature"],
        "g28_form": ["Form G-28", "Notice of Entry of Appearance as Attorney or Accredited Representative", "OMB No. 1615-0105",
                     "Part 1. Information About Attorney or Accredited Representative", "Attorney State Bar Number", "Part 2. Eligibility Information for Attorney",
                     "Part 3. Notice of Appearance", "Client's Contact Information", "Signature of Attorney"],
        "i765_form": ["Form I-765", "Application for Employment Authorization", "Department of Homeland Security", "OMB No. 1615-0040",
                      "Part 1. Reason for Applying", "Initial permission to accept employment", "Part 2. Information About You",
                      "Eligibility Category", "( c ) ( 14 )", "Applicant's Statement", "Applicant's Signature", "Form I-765 Edition"],
        "i693": ["Form I-693", "Report of Immigration Medical Examination and Vaccination Record", "Civil Surgeon's Certification",
                 "Part 4. Civil Surgeon's Information", "Tuberculosis", "Syphilis", "Gonorrhea", "Vaccination record", "sealed envelope"],
        # a guardianship decree with no SIJ findings: a court paper, not the SIJ order
        "court_guardianship": ["COMMONWEALTH OF MASSACHUSETTS", "THE TRIAL COURT", "PROBATE AND FAMILY COURT", "Docket No.", f"{f.digits(2)}P{f.digits(4)}GD",
                               "DECREE AND ORDER OF APPOINTMENT OF GUARDIAN FOR A MINOR", "Guardianship of minor", name,
                               "Letters of Appointment shall issue", "Justice of the Probate and Family Court"],
        "affidavit": ["AFFIDAVIT", "I, " + name + ", being duly sworn, depose and state:", "1. I was born in", f.country[1],
                      "2. I came to the United States", "I declare under penalty of perjury", "Signed under the pains and penalties of perjury"],
    }
    return _some(r, pools[kind], 0.8)


def sij_order(f: Faker) -> list[str]:
    """A state court's Special Immigrant Juvenile order: whatever the state
    calls it, the findings 8 C.F.R. 204.11 requires."""
    r = f.r
    name = f"{f.given} {f.surname}"
    r.choice(["the child", "the minor", "the juvenile", "she", "he"])  # drawn and not used: the generator's sequence stays as it was
    parents = r.choice(["both parents", "both of the child's parents", "the father", "the mother", "either parent"])
    grounds = ", ".join(r.sample(["abuse", "neglect", "abandonment"], r.randint(1, 3)))
    if r.random() < 0.2:
        grounds += ", or a similar basis under state law"
    reunify = r.choice([f"Reunification of the child with {parents} is not viable due to {grounds}.",
                        f"The Court finds that reunification with {parents} is not viable due to {grounds} as defined under state law.",
                        f"{name}'s reunification with {parents} is not viable because of {grounds}."])
    best = r.choice([f"It is not in the child's best interest to be returned to {f.country[1].title()}, the child's country of nationality.",
                     f"It would not be in {name}'s best interests to be returned to {f.country[1].title()} or to the country of last habitual residence.",
                     f"Return to {f.country[1].title()} is not in the minor's best interest."])
    custody = r.choice([f"{name} is dependent upon the juvenile court.",
                        f"The child has been placed under the custody of {r.choice(FIRST)} {r.choice(LAST)}, appointed as guardian.",
                        f"The Court has appointed {r.choice(FIRST)} {r.choice(LAST)} as the child's guardian pursuant to G. L. c. 190B, s. 5-206.",
                        "The child is legally committed to the Department of Children and Families."])
    style = r.choice(["ma_cjp37", "ma_cjp37", "ma_probate", "ma_juvenile", "ny", "nj", "ct", "fl"])
    if style == "ma_cjp37":  # the Trial Court's own judgment form (CJP 37, 11/8/24), as a filled, scanned copy
        return _some(r, _cjp37(f, name), 0.9)
    head = {
        "ma_probate": ["COMMONWEALTH OF MASSACHUSETTS", "THE TRIAL COURT", "PROBATE AND FAMILY COURT", f"{r.choice(['Suffolk', 'Middlesex', 'Essex', 'Worcester'])} Division",
                       f"Docket No. {r.choice(['SU', 'MI', 'ES', 'WO'])}{f.digits(2)}P{f.digits(4)}{r.choice(['GD', 'EA', 'PP'])}", "SPECIAL IMMIGRANT JUVENILE FINDINGS",
                       r.choice(["CJP 35", "", "Findings of Fact and Rulings of Law"])],
        "ma_juvenile": ["COMMONWEALTH OF MASSACHUSETTS", "THE TRIAL COURT", r.choice(["PROBATE AND FAMILY COURT", "JUVENILE COURT DEPARTMENT"]),
                        f"Docket No. {f.digits(2)}{r.choice(['DP', 'CP'])}{f.digits(4)}",
                        "JUDGMENT AND FINDINGS ON COMPLAINT FOR DEPENDENCY PURSUANT TO G. L. c. 119, s. 39M", "SPECIAL IMMIGRANT JUVENILE FINDINGS"],
        "ny": ["FAMILY COURT OF THE STATE OF NEW YORK", f"COUNTY OF {r.choice(['QUEENS', 'KINGS', 'SUFFOLK', 'WESTCHESTER'])}", f"Docket No. G-{f.digits(5)}-{f.digits(2)}",
               "ORDER - SPECIAL IMMIGRANT JUVENILE STATUS", "Form GF-42"],
        "nj": ["SUPERIOR COURT OF NEW JERSEY", "CHANCERY DIVISION - FAMILY PART", f"Docket No. FD-{f.digits(2)}-{f.digits(6)}-{f.digits(2)}",
               "ORDER OF SPECIAL FINDINGS FOR SPECIAL IMMIGRANT JUVENILE STATUS"],
        "ct": ["STATE OF CONNECTICUT", r.choice(["SUPERIOR COURT FOR JUVENILE MATTERS", "PROBATE COURT"]), f"Docket No. {f.digits(2)}-{f.digits(4)}",
               "FINDINGS REGARDING SPECIAL IMMIGRANT JUVENILE STATUS"],
        "fl": ["IN THE CIRCUIT COURT OF THE ELEVENTH JUDICIAL CIRCUIT", "IN AND FOR MIAMI-DADE COUNTY, FLORIDA", "JUVENILE DIVISION", f"Case No. {f.digits(4)}-DP-{f.digits(6)}",
               "ORDER ON MOTION FOR SPECIAL IMMIGRANT JUVENILE STATUS FINDINGS"],
    }[style]
    body = [f"In the matter of: {name}", f"Date of birth: {f.dob('us')}", f"Country of birth: {f.country[1].title()}",
            "After hearing, the Court makes the following findings pursuant to 8 U.S.C. s. 1101(a)(27)(J) and 8 C.F.R. s. 204.11:",
            custody, reunify, best, r.choice(["SO ORDERED.", "It is so ordered.", ""]),
            r.choice(["Justice of the Probate and Family Court", "Judge of the Family Court", "Judge", "Associate Justice"]),
            f"Date: {f.date(2019, 2026, 'us')}"]
    return _some(r, head, 0.85) + _some(r, body, 0.9)


def _cjp37(f: Faker, name: str) -> list[str]:
    r = f.r
    parent = f"{r.choice(FIRST)} {f.surname.split()[0]}"
    box = lambda: r.choice(["[X]", "X", "☒", "|x|", ""])  # noqa: E731 -- how a ticked box scans, if at all
    return ["CJP 37 (11/8/24) Disposition Code: JCD", "PURSUANT TO G. L. c. 119, § 39M", "JUDGMENT AND FINDINGS ON", "COMPLAINT FOR DEPENDENCY",
            "Massachusetts Trial Court", "Probate and Family Court", f"Docket No. {r.choice(['SU', 'MI', 'ES', 'WO'])}{f.digits(2)}E{f.digits(4)}QC",
            f"{r.choice(['Suffolk', 'Middlesex', 'Essex', 'Worcester'])} Division",
            f"Upon the Complaint for Dependency Pursuant to G. L. c. 119, § 39M filed on {f.date(2023, 2025, 'us')}, the Court FINDS:",
            "Plaintiff", name, 'Defendant "Parent One"', parent, "v.", f"1. {name} (\"Child\") whose date of birth is {f.dob('us')}",
            "is a child pursuant to G. L. c. 119, § 39M. Child is unmarried and under 21 years of age.", f"2. Parent One {parent} is Child's mother father.",
            "3. The Probate and Family Court has jurisdiction in this matter in accordance with G. L. c. 119, § 39M and is sitting as a juvenile court in this matter.",
            "4. Venue is proper.", "5. Child is dependent on this Court for his/her protection, well-being, care and custody, findings, rulings, and orders or "
            "referrals to support the health, safety, welfare of Child or to remedy the effects on Child of abuse, neglect, abandonment, or similar circumstances.",
            f"6. {box()} abuse {box()} neglect {box()} abandonment or a similar basis under Massachusetts law namely:",
            "Reunification of Child with Parent One is not a viable option due to",
            "7. abuse neglect abandonment or a similar basis under Massachusetts law namely:",
            "If applicable, reunification of Child with Parent Two is not a viable option due to",
            "8. It is not in Child's best interest to return to his/her and/or his/her parents' country of nationality or last habitual residence of "
            f"{f.country[1].title()}.", f"9. If applicable: The Court finds that it is in the best interest of Child to remain in the care of {r.choice(FIRST)} {r.choice(LAST)}.",
            "IT IS THEREFORE ORDERED AND ADJUDGED THAT:", "This Judgment is issued for the protection from abuse, abandonment, and neglect, and for the health, "
            "safety, and well-being of Child, and, if applicable, shall remain in effect until the final adjudication of Child's Special Immigrant Juvenile petition.",
            f"Date {f.date(2023, 2026, 'us')}", "Justice of the Probate and Family Court"]


def _cjp35(f: Faker, name: str) -> list[str]:
    """The complaint asking for the findings (CJP 35): a court filing, not the order."""
    return ["CJP 35 (11/8/24)", "COMPLAINT FOR DEPENDENCY Massachusetts Trial Court", "Probate and Family Court", f"Docket No. {f.digits(2)}E{f.digits(4)}QC",
            "PURSUANT TO G. L. c. 119, § 39M", "Plaintiff", name, 'Defendant "Parent One"', f"1. Plaintiff, who resides at {f.street}, {f.town[0]} {f.town[1]} {f.town[2]}",
            "is a child seeking court orders pursuant to G. L. c. 119, § 39M.", f"2. The child who is the subject of the Complaint (\"Child\") is: {name}",
            f"Child's date of birth is: {f.dob('us')}", "Reunification with Parent One is not a viable option for Child due to:",
            "abuse neglect abandonment a similar basis under state law, namely: or", "5. Child is unmarried and under 21 years of age.",
            "6. Child is dependent on the Court for his/her protection, well-being, health, and safety.",
            f"7. It is not in Child's best interest to return to {f.country[1].title()}, the country of his/her and/or his/her parents' nationality or last habitual residence.",
            "WHEREFORE, Plaintiff/Child requests that the Court:", "find that Child is dependent on the Court", "find that Child is under age 21",
            "find that Child is unmarried", "enter a Judgment pursuant to G. L. c. 119, § 39M", "Signature of Attorney or Plaintiff, if pro se", "B.B.O. #",
            f"Date: {f.date(2023, 2025, 'us')}"]


# --- a family-based case: the petitioner's documents, the sponsor's, the marriage's ------------

US_FIRST = ["MICHAEL", "JENNIFER", "DAVID", "JESSICA", "CHRISTOPHER", "ASHLEY", "MATTHEW", "EMILY", "JOSHUA", "SARAH", "DANIEL", "AMANDA", "BRIAN", "NICOLE"]
US_LAST = ["SMITH", "JOHNSON", "WILLIAMS", "BROWN", "JONES", "MILLER", "DAVIS", "WILSON", "ANDERSON", "TAYLOR", "THOMAS", "MOORE", "MARTIN", "SULLIVAN"]
US_STATES = [("MASSACHUSETTS", "MA"), ("NEW YORK", "NY"), ("NEW JERSEY", "NJ"), ("CONNECTICUT", "CT"), ("FLORIDA", "FL"), ("RHODE ISLAND", "RI")]


def _us_person(f: Faker) -> tuple[str, str]:
    r = f.r
    if r.random() < 0.5:  # a naturalized citizen often has a Latin American or Brazilian name
        return f.given, f.surname
    return r.choice(US_FIRST), r.choice(US_LAST)


def us_passport(f: Faker) -> list[str]:
    r = f.r
    given, surname = _us_person(f)
    a, b, c = f.birth
    state = r.choice(US_STATES)[0]
    lines = ["PASSPORT", "UNITED STATES OF AMERICA", "Type / Type / Tipo", "P", "Code / Code / Codigo", "USA", "Passport No. / No. du Passeport / No. de Pasaporte",
             f.digits(9), "Surname / Nom / Apellidos", surname, "Given Names / Prenoms / Nombres", given, "Nationality / Nationalite / Nacionalidad",
             "UNITED STATES OF AMERICA", "Date of birth / Date de naissance / Fecha de nacimiento", f.date(style="mon", ymd=(a - 15, b, c)),
             "Place of birth / Lieu de naissance / Lugar de nacimiento", r.choice([state, "BRAZIL", "GUATEMALA", "COLOMBIA"]), "Sex / Sexe / Sexo", r.choice("MF"),
             "Date of issue / Date de delivrance / Fecha de expedicion", f.date(2016, 2025, "mon"), "Authority / Autorite / Autoridad", "United States Department of State",
             "Date of expiration / Date d'expiration / Fecha de caducidad", f.date(2026, 2035, "mon"), "Endorsements / Mentions Speciales / Anotaciones", "SEE PAGE 27"]
    mrz = [f"P<USA{surname.replace(' ', '<')}<<{given.replace(' ', '<')}".ljust(44, "<")[:44], f"{f.digits(9)}{f.digits(1)}USA{f.digits(7)}{r.choice('MF')}{f.digits(7)}".ljust(44, "<")]
    return _some(r, lines, 0.75) + mrz


def citizenship_certificate(f: Faker) -> list[str]:
    r = f.r
    given, surname = f.given, f.surname
    kind = r.choice(["naturalization", "naturalization", "citizenship", "crba"])
    if kind == "crba":
        return _some(r, ["CONSULAR REPORT OF BIRTH ABROAD OF A CITIZEN OF THE UNITED STATES OF AMERICA", "FS-240", f"Name {given} {surname}",
                         f"Date of Birth {f.dob('long')}", f"Place of Birth {f.town_home()}, {f.country[1]}", "Department of State", "Report Number " + f.digits(10)], 0.85)
    return _some(r, ["THE UNITED STATES OF AMERICA", "CERTIFICATE OF NATURALIZATION" if kind == "naturalization" else "CERTIFICATE OF CITIZENSHIP",
                     f"Form {'N-550' if kind == 'naturalization' else 'N-560'}", f"Certificate Number {r.choice(['', 'A'])}{f.digits(8)}",
                     f"USCIS Registration No. A{f.a_number}", f"Personal description of holder as of date of naturalization: Date of birth {f.dob('long')}",
                     f"Sex {r.choice(['Male', 'Female'])}", f"Height {r.randint(4, 6)} feet {r.randint(0, 11)} inches", f"Marital status {r.choice(['Married', 'Single'])}",
                     f"Country of former nationality {f.country[1]}", "I certify that the description above given is true, and that the photograph affixed hereto is a likeness of me.",
                     f"Be it known that, pursuant to an application filed with the Secretary of Homeland Security at {f.town[0]}, {f.town[1]}",
                     f"the Secretary having found that {given} {surname}", "having complied in all respects with all of the applicable provisions of the naturalization laws of the United States",
                     f"became a citizen of the United States of America on {f.date(2008, 2025, 'long')}", "Director, U.S. Citizenship and Immigration Services",
                     "IT IS PUNISHABLE BY U.S. LAW TO COPY, PRINT OR PHOTOGRAPH THIS CERTIFICATE WITHOUT LAWFUL AUTHORITY."], 0.8)


def green_card(f: Faker) -> list[str]:
    r = f.r
    return _some(r, ["UNITED STATES OF AMERICA", "PERMANENT RESIDENT", "Surname", f.surname, "Given Name", f.given, "USCIS#", f"{f.a_number[:3]}-{f.a_number[3:6]}-{f.a_number[6:]}",
                     "Category", r.choice(["IR1", "CR1", "IR5", "F21", "F41", "SB6", "RE6", "AS6"]), "Country of Birth", f.country[1].title(), "Date of Birth", f.dob("mon"),
                     "Sex", r.choice("MF"), "Card Expires:", f.date(2026, 2036, "us"), "Resident Since:", f.date(2008, 2025, "us"),
                     "THIS CARD IS EVIDENCE OF YOUR PERMANENT RESIDENT STATUS", "If found, drop in any U.S. Mailbox",
                     f"C1USA{f.a_number}{f.digits(1)}{f.letters(3)}{f.digits(7)}<<".ljust(30, "<")], 0.75)


def us_birth_certificate(f: Faker) -> list[str]:
    r = f.r
    state, code = r.choice(US_STATES)
    given, surname = r.choice(US_FIRST), r.choice(US_LAST)
    return _some(r, [f"{'COMMONWEALTH' if code == 'MA' else 'STATE'} OF {state}", r.choice(["CERTIFICATION OF VITAL RECORD", "CERTIFICATE OF LIVE BIRTH", "COPY OF CERTIFICATE OF BIRTH"]),
                     r.choice(["DEPARTMENT OF PUBLIC HEALTH", "REGISTRY OF VITAL RECORDS AND STATISTICS", "OFFICE OF VITAL RECORDS"]), f"Child's name {given} {surname}",
                     f"Date of birth {f.date(1970, 2004, 'long')}", f"Sex {r.choice(['Male', 'Female'])}", f"Place of birth {r.choice(['BOSTON', 'PROVIDENCE', 'NEWARK', 'MIAMI'])}",
                     f"Mother's maiden name {r.choice(US_FIRST)} {r.choice(US_LAST)}", f"Father's name {r.choice(US_FIRST)} {surname}", f"State file number {f.digits(4)}-{f.digits(6)}",
                     "This is a true copy of the record on file", "Registrar of Vital Records", "Not valid without the raised seal"], 0.8)


def tax_return(f: Faker) -> list[str]:
    r = f.r
    year, income = r.randint(2021, 2025), r.randint(18000, 95000)
    if r.random() < 0.35:
        return _some(r, ["This Product Contains Sensitive Taxpayer Data", "Tax Return Transcript", f"Request Date: {f.date(2025, 2026, 'us')}",
                         f"Tax Period Ending: Dec. 31, {year}", "SSN Provided: XXX-XX-" + f.digits(4), "Filing status: Married Filing Joint", "Form number: 1040",
                         "WAGES, SALARIES, TIPS, ETC: $" + f"{income:,}.00", "ADJUSTED GROSS INCOME: $" + f"{income + r.randint(0, 900):,}.00",
                         "TAXABLE INCOME:", "Internal Revenue Service"], 0.85)
    return _some(r, [f"Form 1040 U.S. Individual Income Tax Return {year}", "Department of the Treasury - Internal Revenue Service",
                     f"For the year Jan. 1-Dec. 31, {year}", "Filing Status: Married filing jointly", "Your first name and middle initial", "Last name",
                     "1a Total amount from Form(s) W-2, box 1 " + f"{income:,}", f"9 This is your total income {income:,}", f"11 This is your adjusted gross income {income:,}",
                     "12 Standard deduction or itemized deductions", "15 This is your taxable income", "Sign Here"], 0.85)


def w2(f: Faker) -> list[str]:
    r = f.r
    return _some(r, [f"Form W-2 Wage and Tax Statement {r.randint(2021, 2025)}", "Department of the Treasury-Internal Revenue Service",
                     "a Employee's social security number", "b Employer identification number (EIN)", "c Employer's name, address, and ZIP code",
                     f"{r.choice(['ACME CLEANING LLC', 'SUNRISE CONSTRUCTION INC', 'MARKET BASKET', 'HOME DEPOT USA INC'])}",
                     f"1 Wages, tips, other compensation {r.randint(18000, 90000):,}.00", "2 Federal income tax withheld", "3 Social security wages", "5 Medicare wages and tips",
                     "Copy B-To Be Filed With Employee's FEDERAL Tax Return"], 0.85)


def pay_stub(f: Faker) -> list[str]:
    r = f.r
    return _some(r, [r.choice(["EARNINGS STATEMENT", "PAY STATEMENT", "ADVICE OF DEPOSIT"]), r.choice(["ACME CLEANING LLC", "SUNRISE CONSTRUCTION INC", "STAR MARKET"]),
                     f"Employee: {f.given} {f.surname}", f"Pay period: {f.date(2025, 2026, 'us')} - {f.date(2025, 2026, 'us')}", f"Pay date: {f.date(2025, 2026, 'us')}",
                     "Hours Rate Current YTD", f"Regular {r.randint(30, 45)}.00 {r.randint(15, 32)}.00", f"Gross pay {r.randint(600, 1800)}.00",
                     "Federal income tax", "Social Security tax", "Medicare tax", "MA state income tax", f"Net pay {r.randint(500, 1500)}.00"], 0.85)


def bank_statement(f: Faker) -> list[str]:
    r = f.r
    return _some(r, [r.choice(["Bank of America", "Santander Bank", "Citizens Bank", "TD Bank", "Chase"]), r.choice(["Your checking account statement", "Account Statement", "STATEMENT OF ACCOUNT"]),
                     f"{f.given} {f.surname}", f"{r.choice(US_FIRST)} {f.surname}" if r.random() < 0.6 else "", f.street, f"{f.town[0]} {f.town[1]} {f.town[2]}",
                     f"Account number: **** {f.digits(4)}", f"Statement period {f.date(2024, 2026, 'long')} to {f.date(2024, 2026, 'long')}",
                     f"Beginning balance ${r.randint(100, 9000):,}.{f.digits(2)}", "Deposits and other additions", "Withdrawals and other subtractions",
                     f"Ending balance ${r.randint(100, 9000):,}.{f.digits(2)}", "DEBIT CARD PURCHASE", "ZELLE PAYMENT TO", "ATM WITHDRAWAL"], 0.8)


def lease(f: Faker) -> list[str]:
    r = f.r
    return _some(r, [r.choice(["RESIDENTIAL LEASE AGREEMENT", "LEASE AGREEMENT", "TENANCY AT WILL AGREEMENT", "RENTAL AGREEMENT"]),
                     f"Landlord: {r.choice(US_FIRST)} {r.choice(US_LAST)}", f"Tenant(s): {f.given} {f.surname} and {r.choice(US_FIRST)} {f.surname}",
                     f"Premises: {f.street}, {f.town[0]}, {f.town[1]} {f.town[2]}", f"Term: from {f.date(2023, 2026, 'long')} to {f.date(2024, 2027, 'long')}",
                     f"Monthly rent: ${r.randint(1100, 3200):,}", "Security deposit", "Utilities", "The Tenant shall not sublet", "Signed under seal this day of"], 0.85)


def utility_bill(f: Faker) -> list[str]:
    r = f.r
    return _some(r, [r.choice(["Eversource", "National Grid", "Comcast Xfinity", "Boston Water and Sewer Commission"]),
                     r.choice(["Your electricity bill", "Your natural gas bill", "Your monthly statement", "Water and sewer bill"]), f"{f.given} {f.surname}",
                     "Service address", f.street, f"{f.town[0]} {f.town[1]} {f.town[2]}", f"Account Number {f.digits(11)}", f"Amount due ${r.randint(40, 320)}.{f.digits(2)}",
                     f"Due date {f.date(2025, 2026, 'us')}", f"{r.randint(200, 900)} kWh" if r.random() < 0.5 else f"{r.randint(20, 120)} therms"], 0.85)


def divorce_decree(f: Faker) -> list[str]:
    r = f.r
    spouse = f"{r.choice(FIRST)} {r.choice(LAST)}"
    if f.country[3] == "pt" and r.random() < 0.5:
        return _some(r, ["PODER JUDICIARIO", "TRIBUNAL DE JUSTICA DO ESTADO DE MINAS GERAIS", "SENTENCA DE DIVORCIO", f"Requerente: {f.given} {f.surname}",
                         f"Requerido(a): {spouse}", "Ante o exposto, DECRETO O DIVORCIO do casal", "dissolvendo o vinculo matrimonial", f"Data: {f.date(2010, 2024, 'br')}",
                         "Juiz de Direito"], 0.85)
    if f.country[3] == "es" and r.random() < 0.5:
        return _some(r, ["PODER JUDICIAL", "JUZGADO DE FAMILIA", "SENTENCIA DE DIVORCIO", f"Actor: {f.given} {f.surname}", f"Demandado(a): {spouse}",
                         "Se declara la disolucion del vinculo matrimonial", f"Fecha: {f.date(2010, 2024, 'br')}", "Juez"], 0.85)
    return _some(r, ["COMMONWEALTH OF MASSACHUSETTS", "THE TRIAL COURT", "PROBATE AND FAMILY COURT", f"Docket No. {f.digits(2)}D{f.digits(4)}DR",
                     r.choice(["JUDGMENT OF DIVORCE NISI", "DIVORCE DECREE", "JUDGMENT OF DIVORCE"]), f"{f.given} {f.surname}, Plaintiff", f"v. {spouse}, Defendant",
                     "It is adjudged that a divorce from the bond of matrimony be granted", "irretrievable breakdown of the marriage under G.L. c. 208",
                     f"Date of judgment: {f.date(2010, 2024, 'us')}", "Justice of the Probate and Family Court"], 0.85)


WRITERS: dict[str, Callable[[Faker], list[str]]] = {
    "passport": passport, "visa": visa, "i94": i94, "i360_approval": i360_approval, "uscis_notice": uscis_notice,
    "birth_certificate": birth_certificate, "translation_certification": translation_certification, "marriage_certificate": marriage_certificate,
    "ssn_card": ssn_card, "drivers_license": drivers_license, "work_permit": work_permit, "notice_to_appear": notice_to_appear,
    "criminal_record": criminal_record, "intake_questionnaire": intake_questionnaire, "sij_order": sij_order,
    "us_passport": us_passport, "citizenship_certificate": citizenship_certificate, "green_card": green_card, "us_birth_certificate": us_birth_certificate,
    "tax_return": tax_return, "w2": w2, "pay_stub": pay_stub, "bank_statement": bank_statement, "lease": lease, "utility_bill": utility_bill,
    "divorce_decree": divorce_decree, "other": other,
}

# --- damage: how scans and phone photos read --------------------------------------------

_SWAPS = {"I": ["1", "l", "|"], "l": ["1", "I", "|"], "1": ["I", "l"], "O": ["0", "Q"], "0": ["O", "D"], "S": ["5", "$"], "5": ["S"],
          "B": ["8"], "8": ["B"], "e": ["c"], "c": ["e"], "a": ["e", "o"], "m": ["rn"], "n": ["ri"], "u": ["v"], "G": ["6"], "Z": ["2"],
          ".": [","], ",": ["."], "-": ["=", "~"], "'": ["‘", "’"]}
_JUNK = "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ.,:;'\"|/\\-_~=*<>()[]{}!?°«»‘’“”—"


def _junk_line(r: random.Random) -> str:
    """The noise a photo's background, a stamp or a hologram reads as."""
    words = []
    for _ in range(r.randint(1, 9)):
        n = r.randint(1, 7)
        if r.random() < 0.5:
            w = "".join(r.choice(string.ascii_letters) for _ in range(n))
            w = w.capitalize() if r.random() < 0.3 else (w.upper() if r.random() < 0.4 else w)
        else:
            w = "".join(r.choice(_JUNK) for _ in range(n))
        words.append(w)
    return " ".join(words)


def _damage_line(line: str, r: random.Random, level: float) -> str:
    out = []
    for ch in line:
        x = r.random()
        if x < level * 0.08 and ch in _SWAPS:
            out.append(r.choice(_SWAPS[ch]))
        elif x < level * 0.10:
            continue  # lost
        elif x < level * 0.11:
            out.append(r.choice(_JUNK))
        elif ch == " " and r.random() < level * 0.25:
            continue  # words run together
        else:
            out.append(ch)
    s = "".join(out)
    if r.random() < level * 0.15:
        s = s.lower()
    return s


def strip_accents(text: str) -> str:
    return "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))


def damage(lines: list[str], r: random.Random) -> str:
    level = r.choice([0.0, 0.2, 0.5, 0.8, 1.2, 1.8])  # clean PDF ... a bad phone photo
    lines = [ln for ln in lines if ln]
    if level and r.random() < 0.3 and len(lines) > 6:  # blocks read out of order
        i, j = sorted(r.sample(range(len(lines)), 2))
        lines = lines[j:] + lines[i:j] + lines[:i]
    lines = [_damage_line(ln, r, level) for ln in lines if r.random() > level * 0.12]
    junk = int(len(lines) * r.choice([0, 0, 0.2, 0.5, 1.0, 2.0]) * (0.3 + level))
    for _ in range(junk):
        lines.insert(r.randint(0, len(lines)), _junk_line(r))
    cut = r.random()
    if cut < 0.25 and len(lines) > 4:  # the top of the page lost: a crop, a fold, a photo of half a page
        lines = lines[max(3, len(lines) // 4):]
    elif cut < 0.35 and len(lines) > 8:  # only a strip of it
        a = r.randint(0, len(lines) // 2)
        lines = lines[a:a + r.randint(4, 12)]
    return "\n".join(lines)


_VOCAB: set[str] = set()


def vocabulary() -> set[str]:
    """Words of four letters or more that the document types use (not names)."""
    if not _VOCAB:
        import re

        for write in WRITERS.values():
            for i in range(30):
                for line in write(Faker(random.Random(i))):
                    _VOCAB.update(re.findall(r"[a-z]{4,}", strip_accents(line).lower()))
        _VOCAB.difference_update(w.lower() for w in FIRST + LAST)
    return _VOCAB


def readable(text: str, min_words: int = 3) -> bool:
    """Enough of a page was read to learn from: a few real document words, or
    a machine-readable line. Not the label's words -- every type's."""
    import re

    if re.search(r"P<[A-Z]{3}|V[<N]USA|[IL1]AUSA\d", text):
        return True
    return sum(w in vocabulary() for w in re.findall(r"[a-z]{4,}", strip_accents(text).lower())) >= min_words


def generate(n_per_type: int, seed: int = 0, types: list[str] | None = None) -> list[tuple[str, str]]:
    """[(text, doc_type)] -- n_per_type invented, damaged documents of each type."""
    r = random.Random(seed)
    out = []
    for doc_type in types or list(WRITERS):
        for _ in range(n_per_type):
            f = Faker(random.Random(r.random()))
            out.append((damage(WRITERS[doc_type](f), f.r), doc_type))
    r.shuffle(out)
    return out


if __name__ == "__main__":
    import sys

    kind = sys.argv[1] if len(sys.argv) > 1 else "passport"
    for text, _ in generate(3, seed=int(sys.argv[2]) if len(sys.argv) > 2 else 1, types=[kind]):
        print(text, "\n" + "=" * 60)
