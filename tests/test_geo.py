"""Places for every country the firm's clients come from (src/extract/geo.py,
schemas/geo/ from GeoNames): regions, cities, postal codes -- what Brazil had."""

import pytest

from extract import geo


@pytest.mark.parametrize("country, written, region", [
    ("COLOMBIA", "Depto. del Valle", "VALLE DEL CAUCA"), ("CO", "Antiokia", "ANTIOQUIA"),
    ("HAITI", "Grand Anse", "GRAND'ANSE"), ("HAITI", "Lwès", "OUEST"),
    ("DOMINICAN REPUBLIC", "D.N.", "DISTRITO NACIONAL"), ("REPUBLICA DOMINICANA", "Santiago", "SANTIAGO"),
    ("ARGENTINA", "Pcia. de Buenos Aires", "BUENOS AIRES"), ("ARGENTINA", "Capital Federal", "CIUDAD AUTONOMA DE BUENOS AIRES"),
    ("CHILE", "Región Metropolitana", "METROPOLITANA DE SANTIAGO"), ("PERU", "La Libertad", "LA LIBERTAD"),
    ("VENEZUELA", "Edo. Zulia", "ZULIA"), ("BOLIVIA", "La Paz", "LA PAZ"), ("MEXICO", "Edo. de México", "ESTADO DE MEXICO"),
    ("BRAZIL", "SP", "SAO PAULO"), ("SURINAME", "Paramaribo", "PARAMARIBO"), ("GUYANA", "Demerara-Mahaica", "DEMERARA-MAHAICA"),
])
def test_a_region_is_named_the_way_the_form_wants_it(country, written, region):
    assert geo.region(country, written) == region


def test_every_country_of_ours_has_its_regions():
    expected = {"AR": 24, "BO": 9, "BR": 27, "CL": 16, "CO": 33, "EC": 24, "GY": 10, "PY": 18, "PE": 25, "SR": 10, "UY": 19, "VE": 25,
                "HT": 10, "DO": 32, "GT": 22, "HN": 18, "SV": 14, "MX": 32, "NI": 17}
    for iso, n in expected.items():
        assert len(geo._data(iso)["regions"]) == n, iso


@pytest.mark.parametrize("country, city, region", [
    ("COLOMBIA", "Medellín", "ANTIOQUIA"), ("HAITI", "Port-au-Prince", "OUEST"), ("HAITI", "Cap-Haïtien", "NORD"),
    ("HAITI", "Jacmel", "SUD-EST"), ("DOMINICAN REPUBLIC", "Santiago de los Caballeros", "SANTIAGO"),
    ("DOMINICAN REPUBLIC", "San Francisco de Macorís", "DUARTE"), ("ECUADOR", "Cuenca", "AZUAY"), ("PERU", "Arequipa", "AREQUIPA"),
    ("ARGENTINA", "Rosario", "SANTA FE"), ("VENEZUELA", "Maracaibo", "ZULIA"), ("BOLIVIA", "Cochabamba", "COCHABAMBA"),
    ("PARAGUAY", "Ciudad del Este", "ALTO PARANA"), ("GUYANA", "Georgetown", "DEMERARA-MAHAICA"), ("HONDURAS", "San Pedro Sula", "CORTES"),
    ("BRAZIL", "Vila Velha", "ESPIRITO SANTO"),
])
def test_a_city_names_its_region_when_only_one_has_it_or_it_is_by_far_the_largest(country, city, region):
    found = geo.likely_region(country, city)
    assert found and found[0] == region and "GeoNames" in found[1] or "IBGE" in found[1]


def test_a_name_many_regions_share_decides_nothing():
    assert geo.likely_region("GUATEMALA", "San José") is None and len(geo.regions_for_place("GUATEMALA", "San José")) > 3
    assert geo.likely_region("BRAZIL", "Bom Jesus") is None


@pytest.mark.parametrize("country, code, region", [
    ("COLOMBIA", "050021", "ANTIOQUIA"), ("COLOMBIA", "760001", "VALLE DEL CAUCA"), ("ECUADOR", "170150", "PICHINCHA"),
    ("PERU", "15074", "LIMA"), ("HAITI", "HT6110", "OUEST"), ("HAITI", "6110", "OUEST"), ("ARGENTINA", "C1425ABC", "CIUDAD AUTONOMA DE BUENOS AIRES"),
    ("ARGENTINA", "X5000", "CORDOBA"), ("ARGENTINA", "2000", "SANTA FE"), ("CHILE", "8320000", "METROPOLITANA DE SANTIAGO"),
    ("MEXICO", "06700", "CIUDAD DE MEXICO"), ("GUATEMALA", "13001", "HUEHUETENANGO"), ("URUGUAY", "11200", "MONTEVIDEO"),
    ("DOMINICAN REPUBLIC", "10101", "DISTRITO NACIONAL"), ("BRAZIL", "29104-585", "ESPIRITO SANTO"),
])
def test_a_postal_code_names_its_region(country, code, region):
    assert geo.region_from_postal(country, code) == region


def test_postal_formats_and_countries_without_postal_codes():
    assert geo.postal_ok("COLOMBIA", "05 0021") and geo.postal_ok("COLOMBIA", "50021") is False
    assert geo.postal_ok("HAITI", "ht 6110") and geo.normalize_postal("HAITI", "6110") == "HT6110"
    assert geo.postal_format("ARGENTINA").startswith("a letter") and geo.postal_format("PERU") == "5 digits"
    assert geo.postal("BOLIVIA") is None and geo.postal_ok("GUYANA", "1234") is None


def test_city_and_region_are_split():
    assert geo.split_place("COLOMBIA", "Medellín, Antioquia") == ("MEDELLIN", "ANTIOQUIA")
    assert geo.split_place("ECUADOR", "Quito - Pichincha") == ("QUITO", "PICHINCHA")
    assert geo.split_place("HAITI", "Jacmel, Sud-Est, Haïti") == ("JACMEL", "SUD-EST")
    assert geo.split_place("COLOMBIA", "Medellín") is None


def test_official_country_names_are_recognized():
    from extract.places import country_name

    for written, name in (("República Bolivariana de Venezuela", "VENEZUELA"), ("République d'Haïti", "HAITI"),
                          ("Rep. Dominicana", "DOMINICAN REPUBLIC"), ("Republiek Suriname", "SURINAME"), ("Estado Plurinacional de Bolivia", "BOLIVIA")):
        assert country_name(written) == name


# --- wired in: the client's foreign address, the saved-address checks, the review app -----

def test_a_foreign_address_outside_brazil_goes_in_the_right_boxes():
    from questionnaire.handwriting import tidy_foreign_parts

    v = tidy_foreign_parts("foreign_address", {"street": "CALLE 10 # 43-12", "city": "Medellín, Antioquia 050021", "country": "Colombia"})
    assert v == {"street": "CALLE 10 # 43-12", "city": "MEDELLIN", "province": "ANTIOQUIA", "postal_code": "050021", "country": "COLOMBIA"}
    v = tidy_foreign_parts("foreign_address", {"street": "RUE 5, DELMAS 33", "city": "Port-au-Prince", "province": "Haiti"})
    assert (v["country"], v["province"], v["city"]) == ("HAITI", "OUEST", "Port-au-Prince")
    v = tidy_foreign_parts("employer", {"employer": "COLMADO X", "city": "Santiago de los Caballeros", "country": "Republica Dominicana"})
    assert v["state"] == "SANTIAGO" and v["country"] == "DOMINICAN REPUBLIC"
    v = tidy_foreign_parts("foreign_address", {"city": "Quito", "province": "Pichincha", "postal_code": "170150", "country": "ECUADOR"})
    assert v["province"] == "PICHINCHA" and v["postal_code"] == "170150"
    v = tidy_foreign_parts("foreign_address", {"city": "San José", "country": "GUATEMALA"})
    assert "province" not in v  # several San Josés: the reviewer asks
    # the postal code leaves the city box (the pattern's \b had been written as a backspace character, so it never matched)
    v = tidy_foreign_parts("foreign_address", {"street": "RUE 5", "city": "Port-au-Prince HT6110", "country": "Haiti"})
    assert (v["city"], v["postal_code"]) == ("Port-au-Prince", "HT6110")


def _saved(values):
    from assemble import consistency_findings
    from factgraph import FactGraph

    g = FactGraph("t")
    for key, value in values.items():
        g.add_source(f"applicant.last_foreign_{key}", "paralegal_review", "review", value, value, 1.0, tier=3)
    return dict(consistency_findings(g))


def test_saved_foreign_addresses_outside_brazil_are_checked_with_that_countrys_rules():
    found = _saved({"city": "MEDELLIN", "province": "CUNDINAMARCA", "postal_code": "050021", "country": "COLOMBIA"})
    assert "ANTIOQUIA" in found["applicant.last_foreign_postal_code"] and "ANTIOQUIA" in found["applicant.last_foreign_province"]
    found = _saved({"city": "QUITO", "province": "PICHINCHA", "postal_code": "17015", "country": "ECUADOR"})
    assert "6 digits" in found["applicant.last_foreign_postal_code"]
    assert not any(k.startswith("applicant.last_foreign") for k in _saved({"city": "JACMEL", "province": "SUD-EST", "postal_code": "HT9110",
                                                                           "country": "HAITI"}))
    assert not any(k.startswith("applicant.last_foreign") for k in _saved({"city": "COCHABAMBA", "province": "COCHABAMBA", "postal_code": "0000",
                                                                           "country": "BOLIVIA"}))  # no postal codes in use: nothing to check


def test_the_review_app_explains_a_region_and_points_to_the_post_office():
    from review.state import _foreign_help_elsewhere

    region, postal, place = {"suggest": "ANTIOQUIA"}, {"key": "p"}, {"suggest": "MEDELLIN"}
    _foreign_help_elsewhere("COLOMBIA", region, postal, place)
    assert "MEDELLIN is in ANTIOQUIA" in region["help"]["text"] and postal["help"]["url"].startswith("https://")
    region, place = {"key": "r"}, {"suggest": "SAN JOSE"}
    _foreign_help_elsewhere("GUATEMALA", region, None, place)
    assert "Ask the client which" in region["help"]["text"]
    postal = {"key": "p"}
    _foreign_help_elsewhere("BOLIVIA", None, postal, {"suggest": "LA PAZ"})
    assert "doesn't use postal codes" in postal["help"]["text"]
