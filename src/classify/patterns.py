"""Document-processing helper."""

import json
import re
import schema_path

MIN_CONFIDENCE = 0.5
CONFLICT_MARGIN = 0.15

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_MRZ_PASSPORT_LINE = re.compile(r"P<[A-Z]{3}[A-Z<]+")
_MRZ_VISA_LINE = re.compile(r"V[A-Z]USA[A-Z<]+")
# Supporting implementation.
# Supporting implementation.
_MRZ_EAD_LINE = re.compile(r"[IL1|]AUSA\d{9}[A-Z0-9<]+")

# Supporting implementation.
# Supporting implementation.
_CIVIL_REGISTRY = (re.compile(r"REGISTRO\s+(?:DEL\s+ESTADO\s+)?CIVIL|REGISTRADOR\s+CIVIL|[EÉ]TAT\s+CIVIL|BURGERLIJKE\s+STAND|"
                              r"GENERAL\s+REGISTER\s+OFFICE", re.I), 0.15)

PATTERNS: dict[str, list[tuple[re.Pattern, float]]] = {
    "i94": [
        (re.compile(r"I-94", re.I), 0.3),
        (re.compile(r"Admission I-94 Record Number", re.I), 0.5),
        (re.compile(r"U\.?S\.?\s*Customs and Border Protection", re.I), 0.3),
        (re.compile(r"Class\s*of\s*Admission", re.I), 0.3),
        (re.compile(r"Most Recent I-94", re.I), 0.4),
    ],
    "uscis_notice": [
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"[I1]-?797", re.I), 0.4),
        (re.compile(r"NOTICE OF ACTION", re.I), 0.4),
        (re.compile(r"Receipt Number", re.I), 0.2),
        (re.compile(r"U\.?S\.?,?\s*CITIZENSHIP.{0,5}IMMIGRATION", re.I), 0.2),
    ],
    "birth_certificate": [
        (re.compile(r"BIRTH CERTIFICATE", re.I), 0.5),
        (re.compile(r"CERTID[ÃA]O DE NASCIMENTO", re.I), 0.5),
        (re.compile(r"CIVIL REGISTRY", re.I), 0.3),
        (re.compile(r"Father'?s Name", re.I), 0.2),
        (re.compile(r"Mother'?s Name", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"BIRTH ACT", re.I), 0.5),
        (re.compile(r"Name of (the )?father", re.I), 0.2),
        (re.compile(r"Name of (the )?mother", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"FILIA[CÇ][AÃ]O", re.I), 0.4),
        (re.compile(r"MATRICULA", re.I), 0.3),
        (re.compile(r"DATA DE NASCIMENTO", re.I), 0.3),
        (re.compile(r"CARTORIO", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"ACTA\s+DE\s+NACIMIENTO", re.I), 0.5),
        (re.compile(r"DATOS\s+DE\s+LOS\s+PADRES", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"CARTEIRA\s+DE\s+IDENTIDADE|REGISTRO\s+GERAL\b", re.I), -0.6),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"REGISTRO\s+CIVIL\s+DE\s+NACIMIENTO|(?:PARTIDA|CERTIFICADO|CERTIFICACI[OÓ]N\s+DE\s+(?:ACTA|PARTIDA))\s+DE\s+NACIMIENTO|"
                    r"ACTE\s+DE\s+NAISSANCE|GEBOORTEAKTE|REGISTER\s+VAN\s+GEBOORTEN|ENTRY\s+OF\s+BIRTH", re.I), 0.45),
        _CIVIL_REGISTRY,
    ],
    "drivers_license": [
        (re.compile(r"DRIVER'?S? LICENSE", re.I), 0.5),
        (re.compile(r"NOT FOR FEDERAL ID", re.I), 0.4),
        (re.compile(r"\bCLASS\b", re.I), 0.1),
        (re.compile(r"\bDOB\b", re.I), 0.1),
        # The real Massachusetts license's "DRIVER'S LICENSE" banner and
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # license: the back-of-card "ENDORSEMENTS"/"RESTRICTIONS" section
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"ENDORSEMENTS", re.I), 0.2),
        (re.compile(r"RESTRICTIONS", re.I), 0.2),
        (re.compile(r"Corrective Lenses", re.I), 0.3),
        (re.compile(r"PERMANENT INK", re.I), 0.2),
    ],
    "ssn_card": [
        (re.compile(r"SOCIAL SECURITY", re.I), 0.3),
        (re.compile(r"YOUR SOCIAL SECURITY CARD", re.I), 0.5),
        (re.compile(r"VALID FOR WORK ONLY WITH DHS AUTHORIZATION", re.I), 0.5),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"Form SSA-\d+", re.I), 0.4),
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"Fo\w{1,2}\s*SS\s*A-?\d{4}", re.I), 0.4),
        (re.compile(r"WITH DHS AUTHORIZATION", re.I), 0.3),
        (re.compile(r"DO NOT CARRY THIS CARD", re.I), 0.3),
        (re.compile(r"Do not laminate", re.I), 0.2),
    ],
    "passport": [
        (_MRZ_PASSPORT_LINE, 0.6),
        (re.compile(r"P<USA"), -0.4),  # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"PASSAPORTE|PASSPORT|PASSAPORTO|PASAPORTE|PASSEPORT|PASPOORT", re.I), 0.2),
        (re.compile(r"REP[ÚU]BLICA FEDERATIVA DO BRASIL", re.I), 0.2),
    ],
    "visa": [
        (_MRZ_VISA_LINE, 0.5),
        (re.compile(r"\bVISA\b", re.I), 0.2),
        (re.compile(r"UNITED STATES of AMERICA", re.I), 0.2),
        (re.compile(r"Visa Type\s*/?\s*Class", re.I), 0.4),
    ],
    "intake_questionnaire": [
        (re.compile(r"Question[áa]rio", re.I), 0.5),
        (re.compile(r"I485\s*-\s*SIJS", re.I), 0.4),
        (re.compile(r"Georges\s*\|?\s*Cote Law", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"Question\wrio", re.I), 0.3),
        (re.compile(r"AJUSTE DE STATUS", re.I), 0.4),
        (re.compile(r"I?485\s*-?\s*SI\w?S", re.I), 0.3),
    ],
    "g28": [
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"G-28", re.I), 0.15),
        (re.compile(r"NOTICE OF ENTRY OF APPEARANCE", re.I), 0.15),
        (re.compile(r"OMB No\.?\s*1615-0105", re.I), 0.5),
        (re.compile(r"Information About Attorney or Accredited Representative", re.I), 0.4),
    ],
    "i765": [
        (re.compile(r"I-765", re.I), 0.4),
        (re.compile(r"Application for Employment Authorization", re.I), 0.5),
    ],
    "i485": [
        (re.compile(r"Form I-485", re.I), 0.4),
        (re.compile(r"Application to Register Permanent Residence", re.I), 0.5),
    ],
    "translation_certification": [
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"TRANSLATION CERTIFICATION", re.I), 0.5),
        (re.compile(r"true and complete.{0,25}translation", re.I), 0.4),
        (re.compile(r"Translator'?s Name", re.I), 0.3),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "marriage_certificate": [
        (re.compile(r"MARRIAGE CERTIFICATE", re.I), 0.4),
        (re.compile(r"CERTID[ÃA]O DE CASAMENTO", re.I), 0.4),
        (re.compile(r"CERTIFICATE OF MARRIAGE", re.I), 0.4),
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"Date of Marriage", re.I), 0.3),
        (re.compile(r"Place of Marriage", re.I), 0.2),
        (re.compile(r"Party\s+[AB]\b", re.I), 0.1),
        # Supporting implementation.
        (re.compile(r"ACTA\s+DE\s+MATRIMONIO", re.I), 0.5),
        (re.compile(r"FECHA\s+DE\s+CELEBRACI[OÓ]N", re.I), 0.2),
        # Supporting implementation.
        (re.compile(r"(?:PARTIDA|CERTIFICADO|REGISTRO\s+CIVIL|CERTIFICACI[OÓ]N\s+DE\s+ACTA)\s+DE\s+MATRIMONIO|ACTE\s+DE\s+MARIAGE|"
                    r"HUWELIJKSAKTE|REGISTER\s+VAN\s+HUWELIJKEN|ENTRY\s+OF\s+MARRIAGE", re.I), 0.45),
        _CIVIL_REGISTRY,
    ],
    # Supporting implementation.
    "notice_to_appear": [
        (re.compile(r"NOTICE TO APPEAR", re.I), 0.6),
        (re.compile(r"removal proceedings under section 240", re.I), 0.4),
        (re.compile(r"alleges that you", re.I), 0.2),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "i213": [
        (re.compile(r"Record\s+of\s+Deportable\s*/?\s*Inadmissible\s+Alien", re.I), 0.6),
        (re.compile(r"Form\s+I-?213\b", re.I), 0.3),
        (re.compile(r"Date,?\s+Place,?\s+Time,?\s+and\s+Manner\s+of\s+Last\s+Entry", re.I), 0.3),
        (re.compile(r"Method\s+of\s+Location\s*/?\s*Apprehension", re.I), 0.2),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "sij_order": [
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (re.compile(r"JUDGMENT\s+AND\s+FINDINGS\s+ON\s+COMPLAINT\s+FOR\s+DEPENDENCY", re.I), 0.7),
        (re.compile(r"COMPLAINT\s+FOR\s+DEPENDENCY", re.I), -0.5),
        (re.compile(r"WHEREFORE,?\s+Plaintiff", re.I), -0.3),
        (re.compile(r"SPECIAL\s+IMMIGRANT\s+JUVENILE\s+(?:STATUS\s+)?(?:FINDINGS|ORDER)|ORDER[^\n]{0,40}SPECIAL\s+IMMIGRANT\s+JUVENILE|SPECIAL\s+FINDINGS", re.I), 0.4),
        (re.compile(r"reunification[^.]{0,200}?not\s+(?:a\s+)?viable", re.I | re.S), 0.35),
        (re.compile(r"best\s+interests?[^.]{0,80}?(?:returned|to\s+return)", re.I | re.S), 0.25),
        (re.compile(r"1101\s*\(a\)\s*\(27\)\s*\(J\)|204\.11|c\.\s*119,?\s*(?:§|s\.)\s*39M", re.I), 0.2),
        (re.compile(r"PROBATE\s+AND\s+FAMILY\s+COURT|JUVENILE\s+COURT|FAMILY\s+COURT|FAMILY\s+PART", re.I), 0.1),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "us_passport": [
        (re.compile(r"P<USA[A-Z<]+"), 1.0),
        (re.compile(r"UNITED\s+STATES\s+OF\s+AMERICA[\s\S]{0,80}PASSPORT|PASSPORT[\s\S]{0,80}UNITED\s+STATES\s+OF\s+AMERICA", re.I), 0.3),
        (re.compile(r"Nationality\s*[:/]?\s*UNITED\s+STATES", re.I), 0.3),
    ],
    "citizenship_certificate": [
        (re.compile(r"CERTIFICATE\s+OF\s+(?:NATURALIZATION|CITIZENSHIP)", re.I), 0.7),
        (re.compile(r"Naturalization", re.I), 0.2),
        (re.compile(r"Certificate\s+(?:Number|No\.?)", re.I), 0.3),
        (re.compile(r"\bN-5(?:50|60|61|70)\b", re.I), 0.3),
        (re.compile(r"CONSULAR\s+REPORT\s+OF\s+BIRTH\s+ABROAD|\bFS-240\b", re.I), 0.6),
    ],
    "green_card": [
        (re.compile(r"PERMANENT\s+RESIDENT(?:\s+CARD)?", re.I), 0.4),
        (re.compile(r"Resident\s+Since", re.I), 0.4),
        (re.compile(r"\bC[1<]USA\d"), 0.6),
        (re.compile(r"USCIS\s*#", re.I), 0.1),
    ],
    "us_birth_certificate": [
        (re.compile(r"CERTIFICATION\s+OF\s+VITAL\s+RECORD|CERTIFICATE\s+OF\s+LIVE\s+BIRTH", re.I), 0.5),
        (re.compile(r"CERTIFICATE\s+OF\s+BIRTH", re.I), 0.3),
        (re.compile(r"(?:REGISTRY|DIVISION|OFFICE)\s+OF\s+VITAL\s+(?:RECORDS|STATISTICS)|DEPARTMENT\s+OF\s+(?:PUBLIC\s+)?HEALTH", re.I), 0.3),
        (re.compile(r"\b(?:STATE|COMMONWEALTH)\s+OF\s+(?:MASSACHUSETTS|RHODE\s+ISLAND|NEW\s+YORK|NEW\s+JERSEY|CONNECTICUT|FLORIDA|TEXAS|CALIFORNIA)\b", re.I), 0.1),
    ],
    # Supporting implementation.
    "tax_return": [
        (re.compile(r"U\.?S\.?\s+Individual\s+Income\s+Tax\s+Return|Tax\s+Return\s+Transcript", re.I), 0.6),
        (re.compile(r"Form\s*1040\b", re.I), 0.3),
        (re.compile(r"Adjusted\s+gross\s+income", re.I), 0.2),
        (re.compile(r"Internal\s+Revenue\s+Service", re.I), 0.1),
    ],
    "w2": [(re.compile(r"Wage\s+and\s+Tax\s+Statement", re.I), 0.6), (re.compile(r"Form\s*W-2\b", re.I), 0.3)],
    "pay_stub": [
        (re.compile(r"EARNINGS\s+STATEMENT|PAY\s+STATEMENT|PAY\s*STUB|ADVICE\s+OF\s+DEPOSIT", re.I), 0.4),
        (re.compile(r"Net\s+pay", re.I), 0.2), (re.compile(r"Gross\s+pay", re.I), 0.2), (re.compile(r"Pay\s+period", re.I), 0.2),
    ],
    "bank_statement": [
        (re.compile(r"(?:ACCOUNT|CHECKING|SAVINGS)\s+STATEMENT|STATEMENT\s+OF\s+ACCOUNT|Statement\s+period", re.I), 0.3),
        (re.compile(r"(?:Beginning|Opening|Ending|Closing)\s+balance", re.I), 0.3),
        (re.compile(r"Deposits\s+and\s+(?:other\s+)?(?:additions|credits)|Withdrawals", re.I), 0.1),
    ],
    "lease": [
        (re.compile(r"(?:RESIDENTIAL\s+)?LEASE\s+AGREEMENT|RENTAL\s+AGREEMENT|TENANCY\s+AT\s+WILL", re.I), 0.6),
        (re.compile(r"\bLandlord\b", re.I), 0.1), (re.compile(r"\bTenants?\b", re.I), 0.1),
    ],
    "utility_bill": [
        (re.compile(r"Service\s+address", re.I), 0.3), (re.compile(r"Amount\s+due|Total\s+due", re.I), 0.2),
        (re.compile(r"\bkWh\b|\btherms?\b|electricity|natural\s+gas|water\s+(?:and|&)\s+sewer", re.I), 0.2),
    ],
    "divorce_decree": [
        (re.compile(r"JUDGMENT\s+OF\s+DIVORCE|DIVORCE\s+DECREE|DECREE\s+OF\s+DIVORCE|JUDGMENT\s+OF\s+DISSOLUTION|SENTEN[ÇC]A\s+DE\s+DIV[ÓO]RCIO|"
                    r"SENTENCIA\s+DE\s+DIVORCIO|ACTE\s+DE\s+DIVORCE|DIVORCE\s+NISI|DIVORCE\s+ABSOLUTE", re.I), 0.6),
        (re.compile(r"dissolution\s+of\s+(?:the\s+)?marriage|disoluci[óo]n\s+del\s+v[íi]nculo", re.I), 0.3),
    ],
    # Supporting implementation.
    # Supporting implementation.
    "name_change_order": [
        (re.compile(r"(?:DECREE|ORDER|JUDGMENT)\s+(?:OF|FOR|ON|GRANTING)\s+(?:A\s+)?(?:LEGAL\s+)?CHANGE\s+OF\s+NAME|NAME\s+CHANGE\s+(?:DECREE|ORDER)", re.I), 0.4),
        (re.compile(r"\bis\s+(?:hereby\s+)?changed\s+to\b|^\s*(?:New\s+Name|Name\s+changed\s+to)\s*:", re.I | re.M), 0.3),
        (re.compile(r"PETITION\s+(?:TO|FOR)\s+(?:A\s+)?CHANGE\s+(?:OF\s+)?NAME", re.I), -0.3),
    ],
    "criminal_record": [
        (re.compile(r"CRIMINAL DOCKET", re.I), 0.6),
        (re.compile(r"DOCKET NUMBER", re.I), 0.2),
        (re.compile(r"OFFENSE (DESCRIPTION|COUNTS|DATE)", re.I), 0.2),
        (re.compile(r"Trial Court of", re.I), 0.2),
    ],
    "work_permit": [
        (re.compile(r"EMPLOYMENT AUTHORIZATION", re.I), 0.4),
        (re.compile(r"USCIS.{0,10}CARD", re.I), 0.2),
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        # Supporting implementation.
        (_MRZ_EAD_LINE, 0.6),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "i693_envelope": [
        (re.compile(r"DO\s+NOT\s+OPEN\.?\s*,?\s*FOR\s+USCIS\s+USE\s+ONLY", re.I), 0.8),
        (re.compile(r"[I1]-?693", re.I), 0.1),
        (re.compile(r"CIVIL\s+SURGEON", re.I), 0.1),
    ],
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    "eoir_hearing_notice": [
        (re.compile(r"NOTICE\s+OF\s+(?:(?:IN-?\s?PERSON|INTERNET-?\s?BASED|VIDEO|TELEPHONIC)\s+)?HEARING", re.I), 0.45),
        (re.compile(r"Executive\s+Office\s+for\s+Immigration\s+Review|\bIMMIGRATION\s+COURT\b", re.I), 0.25),
        (re.compile(r"scheduled\s+for\s+an?\s+(?:MASTER|INDIVIDUAL|MERITS|BOND)\s+hearing", re.I), 0.3),
    ],
}

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
TAXONOMY_PATH = schema_path.path("register", "document_types")


def _load_taxonomy() -> dict[str, dict]:
    return {t["id"]: t for t in json.loads(TAXONOMY_PATH.read_text(encoding="utf-8"))["types"]}


TYPES: dict[str, dict] = _load_taxonomy()
NAMES: dict[str, str] = {type_id: t["name"] for type_id, t in TYPES.items()}
# Supporting implementation.
# Supporting implementation.
SHORT_NAMES: dict[str, str] = {type_id: t["short_name"] for type_id, t in TYPES.items() if t.get("short_name")}


def name(doc_type: str) -> str:
    """Document-processing helper."""
    return NAMES.get(doc_type) or doc_type.replace("_", " ").capitalize()


def short_name(doc_type: str) -> str:
    """Document-processing helper."""
    return SHORT_NAMES.get(doc_type) or name(doc_type)

# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
NOTICE_CASE_TYPE_MAP: dict[re.Pattern, str] = {
    re.compile(r"[I1]-?360", re.I): "i360_approval",
    re.compile(r"[I1]-?765", re.I): "i765_approval",
    re.compile(r"[I1]-?130", re.I): "i130_approval",
    re.compile(r"[I1]-?485", re.I): "i485_receipt",
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    # Supporting implementation.
    re.compile(r"[I1]-?526", re.I): "i526_approval",
    re.compile(r"[I1]-?590", re.I): "i590_approval",
    re.compile(r"[I1]-?730", re.I): "i730_approval",
}
