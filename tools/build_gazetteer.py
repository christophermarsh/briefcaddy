"""Build schemas/geo/<CC>.json -- regions, places and postal-code rules for
the countries the firm's clients come from -- from GeoNames' public data
(CC BY 4.0, https://www.geonames.org; download.geonames.org/export/).

    python tools/build_gazetteer.py            # downloads to data/geo_raw/ (not committed), writes schemas/geo/

Per country:
  - regions: the first-level divisions (states, departments, provinces) by
    the name the country's own documents print (Spanish, Portuguese, French,
    Dutch, English), with the other spellings people write ("VALLE",
    "DEPARTAMENTO DEL VALLE", "DISTRITO NACIONAL"/"NACIONAL") and abbreviations;
  - places: every town, city, municipality and district -> the region(s)
    holding one of that name, with population (to tell Santiago de los
    Caballeros from a hamlet called Santiago);
  - postal: the shortest code prefixes that name a region, learned from
    GeoNames' postal code lists and kept only where 98%+ of the codes under
    a prefix agree (Brazil's CEP rule lives in extract/places.py).
Brazil's places stay IBGE's municipality list (schemas/law/br_municipalities.json).
"""

from __future__ import annotations

import io
import json
import re
import sys
import unicodedata
import zipfile
from collections import Counter, defaultdict
from pathlib import Path
import schema_path

REPO = Path(__file__).resolve().parents[1]
RAW = REPO / "data" / "geo_raw"
UA = "i485-pipeline/1.0 (law-firm forms; gazetteer build)"

# iso2: (name as the I-485 wants it, iso3, document language(s), what a region is called, postal code {label, pattern, lookup})
COUNTRIES = {
    "AR": ("ARGENTINA", "ARG", ["es"], "province", {"label": "código postal (CPA)", "pattern": r"[A-Z]\d{4}[A-Z]{3}|\d{4}",
                                                     "lookup": "https://www.correoargentino.com.ar/formularios/cpa"}),
    "BO": ("BOLIVIA", "BOL", ["es"], "department", None),
    "BR": ("BRAZIL", "BRA", ["pt"], "state", {"label": "CEP", "pattern": r"\d{8}",
                                               "lookup": "https://buscacepinter.correios.com.br/app/endereco/index.php"}),
    "CL": ("CHILE", "CHL", ["es"], "region", {"label": "código postal", "pattern": r"\d{7}", "lookup": "https://www.correos.cl/codigo-postal"}),
    "CO": ("COLOMBIA", "COL", ["es"], "department", {"label": "código postal", "pattern": r"\d{6}", "lookup": "https://visor.codigopostal.gov.co/472/visor/"}),
    "EC": ("ECUADOR", "ECU", ["es"], "province", {"label": "código postal", "pattern": r"\d{6}", "lookup": "https://www.codigopostal.gob.ec/"}),
    "GY": ("GUYANA", "GUY", ["en"], "region", None),
    "PY": ("PARAGUAY", "PRY", ["es"], "department", None),
    "PE": ("PERU", "PER", ["es"], "department", {"label": "código postal", "pattern": r"\d{5}", "lookup": "https://www.serpost.com.pe/"}),
    "SR": ("SURINAME", "SUR", ["nl"], "district", None),
    "UY": ("URUGUAY", "URY", ["es"], "department", {"label": "código postal", "pattern": r"\d{5}", "lookup": None}),
    "VE": ("VENEZUELA", "VEN", ["es"], "state", None),
    "GF": ("FRENCH GUIANA", "GUF", ["fr"], "region", {"label": "code postal", "pattern": r"973\d{2}", "lookup": None}),
    "HT": ("HAITI", "HTI", ["fr", "ht"], "department", {"label": "code postal", "pattern": r"(?:HT)?\d{4}", "lookup": None}),
    "DO": ("DOMINICAN REPUBLIC", "DOM", ["es"], "province", {"label": "código postal", "pattern": r"\d{5}", "lookup": "https://www.inposdom.gob.do/"}),
    "GT": ("GUATEMALA", "GTM", ["es"], "department", {"label": "código postal", "pattern": r"\d{5}", "lookup": None}),
    "HN": ("HONDURAS", "HND", ["es"], "department", {"label": "código postal", "pattern": r"\d{5}", "lookup": None}),
    "SV": ("EL SALVADOR", "SLV", ["es"], "department", None),
    "MX": ("MEXICO", "MEX", ["es"], "state", {"label": "código postal", "pattern": r"\d{5}", "lookup": None}),
    "NI": ("NICARAGUA", "NIC", ["es"], "department", None),
}

# Words around a region's name that aren't the name: "Departamento del Valle del Cauca" is VALLE DEL CAUCA.
_AROUND = re.compile(r"^(?:DEPARTAMENTO|DEPARTAMENT|DEPTO|DPTO|PROVINCIA|PROV|REGION|ESTADO|EDO|DEPARTEMENT|DEPARTMENT|DISTRICT|DISTRITO|"
                     r"DISTRIKT|PROVINCE|STATE|REGIAO|PCIA|PROVA)\.?\s+(?:DE\s+L'|DEL\s+|DE\s+|DU\s+|DES\s+|D'|OF\s+)?|"
                     r"\s+(?:DEPARTMENT|PROVINCE|REGION|STATE|DISTRICT|DEPARTAMENTO|PROVINCIA|SUYU)$")
# Where the common written form differs from GeoNames' (folded): the old name stays an alias.
OVERRIDES = {
    "AR": {"TIERRA DEL FUEGO ANTARTIDA E ISLAS DEL ATLANTICO SUR": "TIERRA DEL FUEGO"},
    "CL": {"METROPOLITANA DE SANTIAGO DE CHILE": "METROPOLITANA DE SANTIAGO"},
    "CO": {"BOGOTA D C": "BOGOTA"},
    "EC": {"ARCHIPIELAGO DE GALAPAGOS": "GALAPAGOS"},
    "PE": {"CONSTITUCIONAL DEL CALLAO": "CALLAO"},
    "MX": {"VERACRUZ-LLAVE": "VERACRUZ", "VERACRUZ DE IGNACIO DE LA LLAVE": "VERACRUZ", "COAHUILA DE ZARAGOZA": "COAHUILA",
           "MICHOACAN DE OCAMPO": "MICHOACAN", "QUERETARO DE ARTEAGA": "QUERETARO", "MEXICO": "ESTADO DE MEXICO"},
    "VE": {"DISTRITO FEDERAL": "DISTRITO CAPITAL", "TERRITORIO DELTA AMACURO": "DELTA AMACURO"},
    "NI": {"AUTONOMA DE LA COSTA CARIBE NORTE": "COSTA CARIBE NORTE", "AUTONOMA DE LA COSTA CARIBE SUR": "COSTA CARIBE SUR"},
}
# Written forms GeoNames lacks (folded).
EXTRA_ALIASES = {
    "DO": {"DISTRITO NACIONAL": ["D N", "DN", "SANTO DOMINGO DE GUZMAN"]},
    "AR": {"CIUDAD AUTONOMA DE BUENOS AIRES": ["C A B A", "CAPITAL"]},
    "CO": {"BOGOTA": ["DISTRITO CAPITAL", "SANTAFE DE BOGOTA", "SANTA FE DE BOGOTA"]},
    "MX": {"CIUDAD DE MEXICO": ["CDMX", "DISTRITO FEDERAL", "D F", "MEXICO D F"]},
    "CL": {"METROPOLITANA DE SANTIAGO": ["METROPOLITANA", "REGION METROPOLITANA", "RM"]},
}
_KEEP_WHOLE = {"DISTRITO NACIONAL", "DISTRITO CAPITAL", "DISTRITO FEDERAL", "CIUDAD DE MEXICO", "CIUDAD AUTONOMA DE BUENOS AIRES"}


def fold(text: str) -> str:
    text = "".join(c for c in unicodedata.normalize("NFKD", text.upper().replace("’", "'")) if not unicodedata.combining(c))
    text = re.sub(r"[^A-Z' -]", " ", text)
    return re.sub(r"\s+", " ", text).strip(" -'")


def bare(name: str) -> str:
    f = fold(name)
    if f in _KEEP_WHOLE:
        return f
    for _ in range(2):
        f = _AROUND.sub("", f).strip()
    return f


def latin(text: str) -> bool:
    return all(ord(c) < 0x250 or unicodedata.combining(c) for c in text)


def download(url: str, dest: Path) -> Path:
    if not dest.exists():
        import httpx

        r = httpx.get(url, headers={"User-Agent": UA}, timeout=300, follow_redirects=True)
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(r.content if r.status_code == 200 else b"")
    return dest


def read_zip_txt(path: Path, name: str) -> list[list[str]]:
    if not path.exists() or path.stat().st_size < 300:
        return []
    with zipfile.ZipFile(path) as z:
        if name not in z.namelist():
            return []
        return [line.split("\t") for line in io.TextIOWrapper(z.open(name), encoding="utf-8").read().splitlines() if line]


def build(cc: str) -> dict:
    name, iso3, langs, word, postal = COUNTRIES[cc]
    G = "https://download.geonames.org/export/"
    dump = read_zip_txt(download(G + f"dump/{cc}.zip", RAW / f"dump_{cc}.zip"), f"{cc}.txt")
    alts = read_zip_txt(download(G + f"dump/alternatenames/{cc}.zip", RAW / f"alt_{cc}.zip"), f"{cc}.txt")
    zips = read_zip_txt(download(G + f"zip/{cc}.zip", RAW / f"zip_{cc}.zip"), f"{cc}.txt")
    alt_by_id: dict[str, list[tuple[str, str, bool]]] = defaultdict(list)
    for a in alts:  # alternateNameId, geonameid, isolanguage, name, isPreferred, isShort, isColloquial, isHistoric, from, to
        if len(a) > 7 and (a[6] == "1" or a[7] == "1"):
            continue  # colloquial or historic names aren't what documents print
        if len(a) > 3 and a[2] not in ("link", "post", "iata", "icao", "faac", "wkdt", "unlc", "tcid", "fr_1793") and latin(a[3]):
            alt_by_id[a[1]].append((a[2], a[3], len(a) > 4 and a[4] == "1"))

    # regions: the ADM1 features
    regions: dict[str, dict] = {}  # admin1 code -> {name, aliases}
    for r in dump:
        if r[6] == "A" and r[7] == "ADM1":
            names = alt_by_id.get(r[0], [])
            # the document language first (Haiti: French before Creole), its preferred name first
            local = [n for lang, n, pref in sorted(names, key=lambda x: (langs.index(x[0]) if x[0] in langs else 9, not x[2])) if lang in langs]
            untagged = [n for lang, n, _ in names if lang in ("", "abbr")]
            canonical = bare(local[0] if local else r[1])
            renamed = OVERRIDES.get(cc, {}).get(canonical)
            aliases = {fold(x) for x in [r[1], r[2], *local, *untagged, *(n for lang, n, _ in names if lang in ("en", "es", "pt", "fr"))]}
            aliases |= {bare(a) for a in aliases}
            aliases |= {fold(n) for lang, n, _ in names if lang == "abbr"}
            if renamed:
                aliases.add(canonical)
                canonical = renamed
            regions[r[10]] = {"name": canonical, "aliases": sorted(a for a in aliases if a and a != canonical and len(a) >= 2)}
    if cc == "DO" and "34" in regions:  # GeoNames calls the capital district "Nacional"
        regions["34"]["name"] = "DISTRITO NACIONAL"
    for reg in regions.values():
        reg["aliases"] = sorted(set(reg["aliases"]) | set(EXTRA_ALIASES.get(cc, {}).get(reg["name"], [])))
    # an alias that two regions share names neither ("SANTIAGO" province vs city aside, "BUENOS AIRES" F.D.)
    seen = Counter(a for reg in regions.values() for a in {reg["name"], *reg["aliases"]})
    for reg in regions.values():
        reg["aliases"] = [a for a in reg["aliases"] if seen[a] == 1 or a == reg["name"]]
    names_index = {a: code for code, reg in regions.items() for a in {reg["name"], *reg["aliases"]} if seen[a] == 1 or a == reg["name"]}

    # places: towns, cities, municipalities, districts
    places: dict[str, dict[str, int]] = defaultdict(dict)
    for r in dump:
        cls, code, admin1, pop = r[6], r[7], r[10], int(r[14] or 0)
        if admin1 not in regions:
            continue
        if not ((cls == "P" and code not in ("PPLH", "PPLQ", "PPLW", "PPLCH")) or (cls == "A" and code in ("ADM2", "ADM3"))):
            continue
        if cc == "MX" and cls == "P" and pop == 0 and not code.startswith(("PPLA", "PPLC")):
            continue  # Mexico lists 150,000 ranchos; municipalities and towns with a population are kept
        # the name, and what the country's own language and English call it -- not the transliterations
        # (Russian "Dzhordzhtaun" for Georgetown, pinyin for Otavalo) that GeoNames lists alongside
        names = {r[1], r[2]} | {n for lang, n, _ in alt_by_id.get(r[0], []) if lang in langs or lang == "en"}
        for n in names:
            for f in {fold(n), bare(n)}:
                if len(f) >= 3:
                    region = regions[admin1]["name"]
                    places[f][region] = max(places[f].get(region, 0), pop)

    # postal: code prefixes that name one region
    prefixes: dict[str, str] = {}
    stats = None
    if postal and zips and cc != "BR":
        codes: dict[str, str] = {}
        for z in zips:
            code = re.sub(r"\D", "", z[1]) if cc != "AR" else z[1].strip()
            region = None
            if cc == "AR" and len(z) > 4 and z[4]:
                codes["L" + z[4]] = None  # the CPA letter itself: learned below from its province name
            for cand in (z[3] if len(z) > 3 else "", z[5] if len(z) > 5 else ""):
                if cand and (bare(cand) in names_index or fold(cand) in names_index):
                    region = regions[names_index.get(bare(cand)) or names_index[fold(cand)]]["name"]
                    break
            if region is None and len(z) > 2:  # no province in the list (DO): the place's own region, if only one has it
                cands = places.get(fold(z[2]), {})
                if len(cands) == 1:
                    region = next(iter(cands))
            if region and code:
                codes[code] = region
            if cc == "AR" and len(z) > 4 and z[4] and region:
                codes["L" + z[4]] = region
        codes = {k: v for k, v in codes.items() if v}
        letters = {k[1:]: v for k, v in codes.items() if k.startswith("L") and len(k) == 2}
        numeric = {k: v for k, v in codes.items() if not k.startswith("L")}
        for k in range(1, max((len(c) for c in numeric), default=0) + 1):
            groups: dict[str, Counter] = defaultdict(Counter)
            for c, region in numeric.items():
                if not any(c.startswith(p) for p in prefixes):
                    groups[c[:k]][region] += 1
            for p, counts in groups.items():
                region, n = counts.most_common(1)[0]
                if n / sum(counts.values()) >= 0.98 and (sum(counts.values()) >= 2 or k == max(len(c) for c in numeric)):
                    prefixes[p] = region
        covered = [c for c in numeric if any(c.startswith(p) for p in prefixes)]
        right = sum(prefixes[next(p for p in prefixes if c.startswith(p))] == numeric[c] for c in covered)
        stats = {"codes": len(numeric), "covered": len(covered), "agree": right}
        if cc == "AR":
            # GeoNames lists no codes for the city of Buenos Aires; the CPA letter is its
            # ISO 3166-2:AR code (C), as the other 23 letters learned above confirm
            postal = postal | {"cpa_letters": dict(sorted((letters | {"C": "CIUDAD AUTONOMA DE BUENOS AIRES"}).items()))}
    out = {
        "_source": "GeoNames (https://www.geonames.org, CC BY 4.0): dump/, alternatenames/ and zip/ files, built by tools/build_gazetteer.py",
        "country": name, "iso2": cc, "iso3": iso3, "region_word": word,
        "regions": {reg["name"]: reg["aliases"] for reg in sorted(regions.values(), key=lambda x: x["name"])},
        "places": {} if cc == "BR" else {p: sorted(([r, pop] for r, pop in regs.items()), key=lambda x: -x[1]) for p, regs in sorted(places.items())},
        "postal": (postal | {"prefixes": dict(sorted(prefixes.items())), "check": stats}) if postal else None,
    }
    return out


def main() -> None:
    only = sys.argv[1:] or list(COUNTRIES)
    schema_path.folder("geo").mkdir(parents=True, exist_ok=True)
    for cc in only:
        data = build(cc)
        path = schema_path.path("geo", cc)
        path.write_text(json.dumps(data, ensure_ascii=False, separators=(",", ":")), encoding="utf-8")
        p = data["postal"] or {}
        print(f"{cc} {data['country']:20} regions={len(data['regions']):3} places={len(data['places']):6} "
              f"postal_prefixes={len(p.get('prefixes', {})):4} check={p.get('check')} {path.stat().st_size // 1024}KB")


if __name__ == "__main__":
    main()
