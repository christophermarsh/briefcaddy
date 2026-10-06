"""Document-processing helper."""

from __future__ import annotations

import re

from .names import fold_name
import schema_path

BR_UF = {
    "AC": "ACRE", "AL": "ALAGOAS", "AP": "AMAPA", "AM": "AMAZONAS", "BA": "BAHIA", "CE": "CEARA",
    "DF": "DISTRITO FEDERAL", "ES": "ESPIRITO SANTO", "GO": "GOIAS", "MA": "MARANHAO", "MT": "MATO GROSSO",
    "MS": "MATO GROSSO DO SUL", "MG": "MINAS GERAIS", "PA": "PARA", "PB": "PARAIBA", "PR": "PARANA",
    "PE": "PERNAMBUCO", "PI": "PIAUI", "RJ": "RIO DE JANEIRO", "RN": "RIO GRANDE DO NORTE",
    "RS": "RIO GRANDE DO SUL", "RO": "RONDONIA", "RR": "RORAIMA", "SC": "SANTA CATARINA", "SP": "SAO PAULO",
    "SE": "SERGIPE", "TO": "TOCANTINS",
}
BR_STATE_NAMES = set(BR_UF.values())

# Supporting implementation.
# Supporting implementation.
COUNTRY_NAMES = {
    "BRAZIL": "BRAZIL", "BRASIL": "BRAZIL", "COLOMBIA": "COLOMBIA", "ECUADOR": "ECUADOR", "EQUADOR": "ECUADOR",
    "GUATEMALA": "GUATEMALA", "HONDURAS": "HONDURAS", "EL SALVADOR": "EL SALVADOR", "MEXICO": "MEXICO",
    "HAITI": "HAITI", "DOMINICAN REPUBLIC": "DOMINICAN REPUBLIC", "REPUBLICA DOMINICANA": "DOMINICAN REPUBLIC",
    "VENEZUELA": "VENEZUELA", "PERU": "PERU", "BOLIVIA": "BOLIVIA", "CHILE": "CHILE", "ARGENTINA": "ARGENTINA",
    "PARAGUAY": "PARAGUAY", "PARAGUAI": "PARAGUAY", "URUGUAY": "URUGUAY", "URUGUAI": "URUGUAY", "CUBA": "CUBA",
    "NICARAGUA": "NICARAGUA", "COSTA RICA": "COSTA RICA", "PANAMA": "PANAMA", "PORTUGAL": "PORTUGAL",
    "USA": "USA", "UNITED STATES": "USA", "UNITED STATES OF AMERICA": "USA", "EUA": "USA", "ESTADOS UNIDOS": "USA",
    "GUYANA": "GUYANA", "GUIANA": "GUYANA", "CO-OPERATIVE REPUBLIC OF GUYANA": "GUYANA", "SURINAME": "SURINAME", "SURINAM": "SURINAME",
    "REPUBLIEK SURINAME": "SURINAME", "FRENCH GUIANA": "FRENCH GUIANA", "GUYANE": "FRENCH GUIANA", "GUYANE FRANCAISE": "FRENCH GUIANA",
    "AYITI": "HAITI", "REPUBLIQUE D'HAITI": "HAITI", "REPUBLIQUE D HAITI": "HAITI", "REPIBLIK DAYITI": "HAITI",
    "REP DOMINICANA": "DOMINICAN REPUBLIC", "REPUBLICA DOMINICANA RD": "DOMINICAN REPUBLIC", "DOMINICAN REP": "DOMINICAN REPUBLIC",
    "REPUBLICA ARGENTINA": "ARGENTINA", "ESTADO PLURINACIONAL DE BOLIVIA": "BOLIVIA", "REPUBLICA DE BOLIVIA": "BOLIVIA",
    "REPUBLICA DE CHILE": "CHILE", "REPUBLICA DE COLOMBIA": "COLOMBIA", "REPUBLICA DEL ECUADOR": "ECUADOR",
    "REPUBLICA DEL PARAGUAY": "PARAGUAY", "REPUBLICA DEL PERU": "PERU", "REPUBLICA ORIENTAL DEL URUGUAY": "URUGUAY",
    "REPUBLICA BOLIVARIANA DE VENEZUELA": "VENEZUELA", "REPUBLICA DE VENEZUELA": "VENEZUELA", "REPUBLICA FEDERATIVA DO BRASIL": "BRAZIL",
    "REPUBLICA DE GUATEMALA": "GUATEMALA", "REPUBLICA DE HONDURAS": "HONDURAS", "REPUBLICA DE EL SALVADOR": "EL SALVADOR",
    "REPUBLICA DE NICARAGUA": "NICARAGUA", "ESTADOS UNIDOS MEXICANOS": "MEXICO", "BELIZE": "BELIZE", "JAMAICA": "JAMAICA",
}


# Supporting implementation.
# Supporting implementation.
# Supporting implementation.
_CEP_RANGES = [
    (1000, 19999, "SP"), (20000, 28999, "RJ"), (29000, 29999, "ES"), (30000, 39999, "MG"), (40000, 48999, "BA"),
    (49000, 49999, "SE"), (50000, 56999, "PE"), (57000, 57999, "AL"), (58000, 58999, "PB"), (59000, 59999, "RN"),
    (60000, 63999, "CE"), (64000, 64999, "PI"), (65000, 65999, "MA"), (66000, 68899, "PA"), (68900, 68999, "AP"),
    (69000, 69299, "AM"), (69300, 69399, "RR"), (69400, 69899, "AM"), (69900, 69999, "AC"), (70000, 72799, "DF"),
    (72800, 72999, "GO"), (73000, 73699, "DF"), (73700, 76799, "GO"), (76800, 76999, "RO"), (77000, 77999, "TO"),
    (78000, 78899, "MT"), (78900, 78999, "RO"), (79000, 79999, "MS"), (80000, 87999, "PR"), (88000, 89999, "SC"),
    (90000, 99999, "RS"),
]
CEP = re.compile(r"\b(\d{5})-?(\d{3})\b")


def br_state_from_cep(cep: str) -> str | None:
    m = CEP.search(cep or "")
    if not m:
        return None
    first5 = int(m.group(1))
    return next((BR_UF[uf] for lo, hi, uf in _CEP_RANGES if lo <= first5 <= hi), None)


_MUNICIPIOS: dict[str, list[str]] | None = None


def br_states_for_city(city: str) -> list[str]:
    """Document-processing helper."""
    global _MUNICIPIOS
    if _MUNICIPIOS is None:
        import json

        path = schema_path.path("law", "br_municipalities")
        _MUNICIPIOS = json.loads(path.read_text(encoding="utf-8"))["municipios"] if path.exists() else {}
    return [BR_UF[uf] for uf in _MUNICIPIOS.get(fold_name(city), [])]


def country_name(text: str) -> str | None:
    return COUNTRY_NAMES.get(fold_name(text).strip())


def is_us_country(text: str | None) -> bool:
    """Document-processing helper."""
    compact = re.sub(r"[^A-Z]", "", fold_name(str(text or "")))
    return compact in {"US", "USA", "UNITEDSTATES", "UNITEDSTATESOFAMERICA", "EUA", "ESTADOSUNIDOS",
                       "ESTADOSUNIDOSDAAMERICA", "ESTADOSUNIDOSDEAMERICA", "EEUU", "ETATSUNIS", "ETAZINI"}


def br_city_uf(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    matches = re.findall(r"([A-Za-zÀ-ÿ' ]+?)\s*[-–/]\s*([A-Z]{2})\b\.?", text)
    for city, uf in reversed(matches):
        uf = uf.upper()
        city = fold_name(city)
        city = re.sub(r"^(HOSPITAL|MATERNIDADE|SANTA CASA)\b.*", "", city).strip()
        # Supporting implementation.
        city = re.sub(r"^.*\b(NATURAL DE|NATURALIDADE|FROM|AM|PM|HORAS|H)\s+", "", city).strip()
        if uf in BR_UF and city and len(city) >= 3:
            return city, BR_UF[uf]
    return None


def city_country(text: str) -> tuple[str, str] | None:
    """Document-processing helper."""
    if "," not in text:
        return None
    place, _, country = text.rpartition(",")
    name = country_name(country)
    place = fold_name(place)
    return (place, name) if name and place else None


def is_br_state(name: str) -> bool:
    return fold_name(name) in BR_STATE_NAMES
