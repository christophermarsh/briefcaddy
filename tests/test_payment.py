"""Paying a mailed filing (src/payment.py): one G-1450 per payment, a Pub. L.
119-21 fee always its own, nothing for a fee-exempt or EOIR filing, the
applicant's name and the exact amount filled -- never the card. Every client
value is CONSTRUCTED.
"""

from datetime import date

from pypdf import PdfReader

import packet
import payment
from factgraph import FactGraph

TODAY = date(2026, 10, 1)


def _graph(**extra):
    g = FactGraph("c")
    for key, value in ({"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.physical_street": "10 EXAMPLE ST",
                        "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA", "applicant.physical_zip": "02143",
                        "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "MARCOS"} | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _pays(filing, graph, variant=None):
    schema = packet.load_filing(filing) | ({"variant": variant} if variant else {})
    return payment.payments(schema, None, graph, TODAY)


def test_one_payment_per_request_and_the_public_law_fee_alone():
    fam = _pays("family", _graph())
    assert [(p["form"], p["amount"], p["petitioner"]) for p in fam] == [("I-130", 675, True), ("I-485", 1440, False), ("I-765", 260, False)]
    assert [p["amount"] for p in _pays("i360", _graph())] == [250]                          # Pub. L. 119-21, its own payment
    assert _pays("i485", _graph()) == []                                                     # the SIJ I-485 and I-765: no fee
    assert _pays("i589", _graph(asylum__ms_l="Yes")) == [] and _pays("i589", _graph(), "in_court") == []   # Ms. L.; in court: the EOIR portal
    assert [p["amount"] for p in _pays("i589", _graph())] == [100]
    assert _pays("bia", _graph()) == [] and _pays("eoir28", _graph()) == [] and _pays("address", _graph()) == []
    assert [p["amount"] for p in _pays("n400", _graph(n400__fee_reduction="Yes"))] == [380]


def test_the_g1450_names_the_applicant_and_the_amount_never_the_card(tmp_path, monkeypatch):
    pays = payment.render(tmp_path, _graph(), _pays("family", _graph())[:1])
    f = {n.rsplit(".", 1)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / pays[0]["file"])).get_fields().items()}
    assert (f["GivenName[0]"], f["FamilyName[0]"], f["AuthorizedPaymentAmt[0]"]) == ("MARCOS", "EXEMPLO", "675")   # the I-130's petitioner
    assert not f.get("CreditCardNumber_1[0]") and not f.get("ExpirationDate[0]") and not f.get("CCHolderFamilyName[0]")
    monkeypatch.setattr(payment, "settings", lambda: {"card_holder": "client"})
    pays = payment.render(tmp_path, _graph(), _pays("family", _graph())[1:2])
    f = {n.rsplit(".", 1)[-1]: x.get("/V") for n, x in PdfReader(str(tmp_path / pays[0]["file"])).get_fields().items()}
    assert (f["CCHolderFamilyName[0]"], f["CityOrTown[0]"], f["AuthorizedPaymentAmt[0]"]) == ("EXEMPLO", "SOMERVILLE", "1,440")
    assert "card number" in payment.checklist(pays)[0]["text"]
