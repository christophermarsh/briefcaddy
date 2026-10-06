"""The waiver of grounds of inadmissibility (Form I-601, src/inadmissibility_waiver.py) and permission to reapply after removal
(Form I-212, src/reapply.py): where each is filed by where the client stands (USCIS's direct filing address pages, read 10/02/2026),
the fees and their $0 categories (G-1055 10/01/26), the pair in one envelope (one packet, one cover letter, two payments), what the
instructions rule out, the filled boxes (the I-601's eye colors by where they print), the portal's questions in the client's language,
and the case page's offers. Every client value is CONSTRUCTED.
"""

import json
import re
from datetime import date

import pytest
from pypdf import PdfReader

import enotice
import filing_questions
import inadmissibility_waiver as i601
import journey
import packet
import reapply
from factgraph import FactGraph
from fill.companion import fill_companions, load_profile
import schema_path

TODAY = date(2026, 10, 2)
BASE = {"applicant.family_name": "EXEMPLO", "applicant.given_name": "ANA", "applicant.dob": "1995-03-14", "applicant.country_of_birth": "BRAZIL",
        "applicant.physical_street": "10 EXAMPLE ST", "applicant.physical_city": "SOMERVILLE", "applicant.physical_state": "MA",
        "applicant.physical_zip": "02143", "applicant.eye_color": "Gray", "applicant.sex": "F",
        "family.relationship": "Spouse", "petitioner.status": "USC", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "JOAO",
        "petitioner.dob": "1990-01-02", "applicant.filing_category": "Spouse of U.S. citizen"}


def _graph(**extra):
    g = FactGraph("c")
    for key, value in (BASE | {k.replace("__", "."): v for k, v in extra.items()}).items():
        g.add_source(key, "test", "test", value, value, 1.0)
    return g


def _notice(g, receipt, form, kind, when):
    g.add_source(f"folder.uscis_case.{receipt}.{kind}_{when.replace('-', '')}", f"{kind}.pdf", "uscis_notice", receipt, f"{form} {kind.upper()}, {when}", 0.9)


def _short(pdf):
    return {re.split(r"(?<!\\)\.", n)[-1]: f.get("/V") for n, f in PdfReader(str(pdf)).get_fields().items()}


def _fill(g, tmp_path, *forms):
    profile = load_profile()
    profile["forms"] = {f: profile["forms"][f] for f in forms}
    fill_companions(g, tmp_path, profile)


def _case(tmp_path, g, name="case"):
    d = tmp_path / name
    d.mkdir(exist_ok=True)
    (d / "meta.json").write_text(json.dumps({"classifications": {}}), encoding="utf-8")
    g.save(d / "fact_graph.json")
    return d


def test_a_consular_refusal_goes_to_phoenix_with_the_spouse_as_the_qualifying_relative(tmp_path):
    g = _graph(visa__result="Refused", visa__nvc_case_number="RIO2026000123", visa__consulate="RIO DE JANEIRO", i601__g15_unlawful_presence="Yes",
               petitioner__physical_street="10 EXAMPLE ST", petitioner__physical_unit_type="STE", petitioner__physical_apt="4",
               i601__g12_fraud="Yes", i601__inadmissibility_statement="SEE ATTACHED LETTER", i601__hardship_statement="SEE ATTACHED LETTER",
               i601__discretion_statement="SEE ATTACHED LETTER")
    i601.derive(g, TODAY)
    v = lambda k: g.get(k).value  # noqa: E731
    assert v("i601.situation") == i601.CONSULAR and v("i601.i485_filed") == "No"            # the refusal recorded on the visa page
    assert v("i601.consular_case_number") == "RIO2026000123" and v("i601.consulate_city") == "RIO DE JANEIRO"
    assert v("i601.relative_relationship") == "SPOUSE" and v("i601.relative_status") == "U.S. citizen" and v("i601.relative_given_name") == "JOAO"
    assert i601.fee(g, TODAY) == (1050, "the I-601 fee (G-1055)")
    assert i601.where(g)["mail_to"] == ["USCIS", "ATTN: I-601 FOREIGN FILERS", "P.O. BOX 21600", "PHOENIX, AZ 85036-1600"]
    assert i601.problems(tmp_path, g, TODAY) == []
    letter = i601.letter(g, TODAY)
    assert "Department of State Case Number RIO2026000123" in letter["re_lines"] and "$1,050" in letter["fees"] and not letter["no_payment"]
    _fill(g, tmp_path, "i601", "g28_i601")
    f = _short(tmp_path / "i601_filled.pdf")
    assert f["p4Line15CB[0]"] == "/Y" and f["p4Line12CB[0]"] == "/Y" and f["p4Line4CB[0]"] in (None, "/Off")
    assert f["p1Line16aYesNo[0]"] == "/N" and f["p1Line15aCaseNumber[0]"] == "RIO2026000123"
    assert f["p3Line5EyeColor[1]"] == "/GRN"            # the box printed beside "Gray" (its on-value says GRN)
    assert f["p5Line5Relationship[0]"] == "SPOUSE" and f["p5Line8DateofBirth[0]"] == "01/02/1990"
    assert f["p5Line2Unit[0]"] == "/ STE " and f["p5Line2AptSteFlrNumber[0]"] == "4"                   # Part 5, 2.B: the box and its number
    assert _short(tmp_path / "g28_i601_filled.pdf")["Line1b_ListFormNumber[0]"] == "I-601"


@pytest.mark.parametrize("receipt, box", [("IOE0999000555", "P.O. BOX 805887"), ("MSC0999000777", "P.O. BOX 805887"),
                                          ("LIN2690000001", "P.O. BOX 660867"), ("WAC2690000001", "P.O. BOX 660867"), ("NBC2690000001", None)])
def test_with_the_i485_pending_the_receipt_number_picks_the_lockbox(tmp_path, receipt, box):
    g = _graph(i601__situation=i601.PENDING)
    _notice(g, receipt, "I-485", "receipt", "2026-05-01")
    i601.derive(g, TODAY)
    assert g.get("i601.i485_receipt").value == receipt and g.get("i601.i485_filed").value == "Yes"
    place = i601.where(g)
    if box:
        assert box in place["mail_to"] and place["lockbox"]
    else:  # a prefix USCIS's page doesn't name: the attorney sets it
        assert place["mail_to"] is None and any("MSC, IOE" in p for p in i601.problems(tmp_path, g, TODAY))


def test_the_fee_and_its_zero_categories(tmp_path):
    assert i601.fee(_graph(applicant__filing_category="Special immigrant juvenile"), TODAY)[0] == 0          # SIJ: G-1055 page 20
    vawa = _graph(vawa__classification="Spouse of a U.S. citizen", i601__situation=i601.VAWA)
    assert i601.fee(vawa, TODAY)[0] == 0 and i601.where(vawa)["mail_to"] == ["USCIS", "ATTN: 1367", "P.O. BOX 8075", "CHICAGO, IL 60680-8075"]
    assert "VAWA self-petitioner" in i601.letter(vawa, TODAY)["fees"] and i601.letter(vawa, TODAY)["no_payment"]
    u = _graph(i601__fee_exempt="Seeking or granted U nonimmigrant status")
    assert i601.fee(u, TODAY)[0] == 0 and "U nonimmigrant status" in i601.letter(u, TODAY)["fees"]
    # the I-212's own list: no $0 for an SIJ, T or U (G-1055 page 12)
    assert reapply.fee(_graph(applicant__filing_category="Special immigrant juvenile"), TODAY)[0] == 1175
    assert reapply.fee(_graph(vawa__classification="Spouse of a U.S. citizen"), TODAY)[0] == 0
    court = _graph(i212__situation=reapply.COURT, i601__situation=i601.COURT)
    assert reapply.payments(court, TODAY, ["i212"]) == [] and i601.payments(court, TODAY, ["i601"]) == []   # paid as the court instructs


def test_a_t_nonimmigrant_with_an_i485_goes_to_the_1367_lockbox_and_tps_is_the_attorneys(tmp_path):
    # USCIS's I-601 page: "A T nonimmigrant filing Form I-601 together with or based on a pending Form I-485" -> the VAWA, T and U page, by state
    t = _graph(tvisa__victim="Yes", applicant__physical_state="FL", i601__situation=i601.PENDING)
    _notice(t, "EAC2690000001", "I-485", "receipt", "2026-05-01")
    assert any("VAWA, T and U lockbox" in p for p in i601.problems(tmp_path, t, TODAY))         # a T case on the general row: told which row
    t = _graph(tvisa__victim="Yes", applicant__physical_state="FL", i601__situation=i601.T_I485)
    _notice(t, "EAC2690000001", "I-485", "receipt", "2026-05-01")
    assert i601.where(t)["mail_to"] == ["USCIS", "ATTN: 1367", "P.O. BOX 4205", "CAROL STREAM, IL 60197-4205"]
    assert not any("VAWA, T and U lockbox" in p for p in i601.problems(tmp_path, t, TODAY))
    tps = _graph(i601__situation=i601.TPS)
    assert i601.where(tps)["mail_to"] is None and any("Federal Register" in p for p in i601.problems(tmp_path, tps, TODAY))


@pytest.mark.parametrize("track, i601_fee, i212_fee", [({"tvisa__victim": "Yes"}, 0, 1175), ({"uvisa__crime": "FELONIOUS ASSAULT"}, 0, 1175),
                                                       ({"applicant__filing_category": "Special immigrant juvenile"}, 0, 1175),
                                                       ({"vawa__classification": "Spouse of a U.S. citizen"}, 0, 0), ({}, 1050, 1175)])
def test_each_tracks_fee_for_both_forms(track, i601_fee, i212_fee):
    g = _graph(**track)  # G-1055 10/01/26: the I-601's $0 list names SIJ, T, U and VAWA; the I-212's only VAWA (of these)
    assert (i601.fee(g, TODAY)[0], reapply.fee(g, TODAY)[0]) == (i601_fee, i212_fee)


def test_a_vawa_waiver_by_where_the_client_stands_e_mails_the_office(tmp_path, monkeypatch):
    import settings

    real = settings.overlay
    monkeypatch.setattr(settings, "overlay", lambda name, default: {"recipient": "client"} if name == "enotice" else real(name, default))
    mine = {"applicant__email": "ana@example.com", "applicant__mobile_phone": "6175550100"}
    assert enotice._contact(_graph(**mine), tmp_path, "i601")["email"] == "ana@example.com"            # the firm's setting: the client
    for filing, situation in (("i601", {"i601__situation": i601.VAWA}), ("i212", {"i212__situation": reapply.VAWA}),
                              ("i601", {"i601__situation": i601.T_I485})):
        contact = enotice._contact(_graph(**mine, **situation), tmp_path, filing)
        assert contact["email"] != "ana@example.com" and contact["mobile"] is None, (filing, situation)   # 8 U.S.C. 1367: the office


def test_the_pair_is_one_packet_one_letter_two_payments(tmp_path):
    g = _graph(visa__result="Refused", visa__nvc_case_number="RIO2026000123", i601__with_i212="Yes", i601__g15_unlawful_presence="Yes",
               i212__deportable="Yes", i212__deportable_which=reapply.DEPORTABLE[0], i212__deportable_date="2020-06-01")
    i601.derive(g, TODAY)
    assert g.get("i212.situation").value == reapply.IV_WITH_601 and g.get("companion.g28_forms_i601").value == "I-601, I-212"
    assert g.get("i212.status_sought").value == "Permanent resident" and g.get("i212.relative_is").value == "A U.S. citizen"
    schema = i601.case_schema(packet.load_filing("i601"), g, TODAY)
    assert schema["forms"] == ["g28_i601", "i601", "i212"] and any(x["id"] == "absence" for x in schema["exhibits"]) and schema["lockbox"]
    assert i601.case_schema(schema, g, TODAY) == schema                                      # laid over twice: the same
    import prefile

    assert prefile.ADDRESS_CHECK["i212"] == "i212_addresses" and schema["pair"]               # the pair's address is checked on the I-212 page
    assert [(p[1], p[2]) for p in i601.payments(g, TODAY, schema["forms"])] == [("I-601", 1050), ("I-212", 1175)]
    letter = i601.letter(g, TODAY)
    assert letter["mail_to"] == reapply.PHOENIX_WITH_601 and letter["re_lines"][1].startswith("and I-212")
    assert "$1,050" in letter["fees"] and "$1,175" in letter["fees"] and "its own Form G-1450" in letter["fees"]
    assert not filing_questions.hidden_sections(i601, g)                                  # the I-212's questions show on the I-601's panel
    assert any("The pair's address" == n["title"] for n in i601.notes(g, TODAY))
    # the I-212 alone says where the pair is built
    assert any("build the I-601's packet" in p for p in reapply.problems(tmp_path, g, TODAY))
    _fill(g, tmp_path, "i212", "g28_i601")
    f = _short(tmp_path / "i212_filled.pdf")
    assert f["p1Line22YesNo[0]"] == "/Y" and f["p2Line5YesNo[0]"] == "/Y" and f["p2Line5CheckBox[0]"] == "/5b" and f["p2Line6Date[0]"] == "06/01/2020"
    assert f["p3Line1CheckBox[0]"] == "/P" and f["p3Line4CheckBox[1]"] == "/CIT" and f["p1Line19aDOSNumber[0]"] == "RIO2026000123"
    assert _short(tmp_path / "g28_i601_filled.pdf")["Line1b_ListFormNumber[0]"] == "I-601, I-212"


def test_the_pair_packet_built_through_the_review_app_holds_one_i212(tmp_path, monkeypatch):
    """packet.build lays the case over the schema, then plan() does again: the I-212 goes in once, with one G-1450 of its own."""
    from pypdf import PdfWriter

    from review.server import ReviewApp

    monkeypatch.setattr(packet, "_today", lambda: TODAY)
    g = _graph(applicant__a_number="A099000777", visa__result="Refused", visa__nvc_case_number="RIO2026000123", i601__with_i212="Yes",
               i601__g15_unlawful_presence="Yes", i601__inadmissibility_statement="SEE ATTACHED LETTER",
               i601__hardship_statement="SEE ATTACHED LETTER", i601__discretion_statement="SEE ATTACHED LETTER", i212__arriving="No",
               i212__deportable="Yes", i212__deportable_which=reapply.DEPORTABLE[0], i212__deportable_date="2020-06-01",
               i212__reentry_after_presence="No", i212__reentry_after_removal="No", i212__why="FAMILY")
    root = tmp_path / "clients"
    root.mkdir()
    d = _case(root, g, "case-pair")
    source = tmp_path / "source"
    source.mkdir()
    for name in ("marriage.pdf", "passport.pdf"):
        w = PdfWriter()
        w.add_blank_page(width=612, height=792)
        with open(source / name, "wb") as fh:
            w.write(fh)
    (d / "meta.json").write_text(json.dumps({"client_id": "case-pair", "source_folder": str(source),
                                             "classifications": {"marriage.pdf": "marriage_certificate", "passport.pdf": "passport"}}), encoding="utf-8")
    review = ReviewApp(root, schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None, None)
    m = review.build_packet("case-pair", {"filing": "i601", "reviewer": "Jane"})
    tabs = [s["tab"] for s in m["sections"]]
    assert [f["short"] for f in m["forms"]] == ["G-28 (I-601)", "I-601", "I-212"]
    assert tabs.count("I-212") == 1 and [p["form"] for p in m["payments"]] == ["I-601", "I-212"]
    assert sum(t.startswith("G-1450 (I-212") for t in tabs) == 1 and sum(t.startswith("G-1450 (I-601") for t in tabs) == 1
    pages = {s["tab"]: (s["first_page"], s["last_page"]) for s in m["sections"]}
    total = len(PdfReader(str(d / "packet_i601.pdf")).pages)
    spans = sorted(pages.values())
    assert spans[0][0] == 1 and spans[-1][1] == total and all(a[1] + 1 == b[0] for a, b in zip(spans, spans[1:]))  # no gap, no overlap
    assert pages["I-212"][1] - pages["I-212"][0] + 1 == len(PdfReader(str(schema_path.path("template", "i212"))).pages)
    assert sum("Form I-212: copies of every document" in c["text"] for c in m["checklist"]) == 1


def test_what_the_i212_instructions_rule_out(tmp_path):
    inside = _graph(i212__situation=reapply.AOS, i212__arriving="No", i212__deportable="No", i212__reentry_after_presence="Yes",
                    i212__reentry_after_removal="No", i212__presence_departed="2019-05-01")
    out = " ".join(reapply.problems(tmp_path, inside, TODAY))
    assert "from inside the U.S." in out and "05/01/2029 at the earliest" in out and "the filing location for the client's I-485" in out
    none = _graph(i212__arriving="No", i212__deportable="No", i212__reentry_after_presence="No", i212__reentry_after_removal="No")
    assert any("no reason chosen" in p for p in reapply.problems(tmp_path, none, TODAY))
    over = _graph(i212__arriving="Yes", i212__arriving_which=reapply.ARRIVING[0], i212__arriving_date="2019-01-15")
    assert any(n["title"] == "The period may be over" and "01/15/2024" in n["text"] for n in reapply.notes(over, TODAY))
    kv = _graph(i212__situation=reapply.KV)
    assert reapply.where(kv)["mail_to"] == ["USCIS", "ATTN: I-212", "P.O. BOX 21600", "PHOENIX, AZ 85036-1600"]
    field = _graph(i212__situation=reapply.IV_NO_601)
    schema = reapply.case_schema(packet.load_filing("i212"), field, TODAY)
    assert not schema["lockbox"] and not enotice.wanted(schema) and reapply.letter(field, TODAY)["mail_to"][0].startswith("[")   # a field office: no G-1145
    assert enotice.wanted(reapply.case_schema(packet.load_filing("i212"), kv, TODAY))


def test_who_is_a_qualifying_relative_for_fraud_and_unlawful_presence(tmp_path):
    son = _graph(family__relationship="Parent", i601__g12_fraud="Yes", i601__hardship_statement="X")   # the petitioner is the client's son
    i601.derive(son, TODAY)
    assert son.get("i601.relative_relationship").value == "SON OR DAUGHTER"
    assert any("isn't a qualifying relative" in p for p in i601.problems(tmp_path, son, TODAY))
    cimt = _graph(family__relationship="Parent", i601__g04_cimt="Yes", i601__g12_fraud="Yes", i601__hardship_statement="X")
    i601.derive(cimt, TODAY)
    assert not any("isn't a qualifying relative" in p for p in i601.problems(tmp_path, cimt, TODAY))   # 212(h): a son or daughter counts
    sibling = _graph(family__relationship="Sibling", i601__g15_unlawful_presence="Yes")
    i601.derive(sibling, TODAY)
    out = " ".join(i601.problems(tmp_path, sibling, TODAY))
    assert "no qualifying relative" in out and "Part 5, 9" in out
    assert any("no ground" in p for p in i601.problems(tmp_path, _graph(), TODAY))


def test_the_long_questions_go_to_the_portal_in_the_clients_language(tmp_path):
    asked = filing_questions.ask_in_portal("i601", None, "pt")
    assert [k for k, _en, _t in asked] == ["i601.client_what_happened", "i601.client_hardship", "i601.client_life"]
    _key, en, typed = asked[1]
    assert en.startswith("If you cannot live in the U.S.") and typed["text_client"].startswith("Se você não puder morar nos EUA")
    assert not typed["machine_translated"] and not typed["needs_translator"]
    creole = filing_questions.ask_in_portal("i212", None, "ht")[0][2]
    assert creole["machine_translated"] and creole["text_client"]                          # a machine draft, marked as one
    assert filing_questions.ask_in_portal("i212", None, "fr")[0][2]["needs_translator"]     # no wording: English, for a translator
    g = _graph(i601__with_i212="Yes", i601__client_hardship="MY HUSBAND IS SICK")
    d = _case(tmp_path, g)
    keys = [k for k, _en, _t in filing_questions.ask_in_portal("i601", d, "es")]
    assert "i601.client_hardship" not in keys and "i212.client_removal" in keys          # answered: not asked again; the pair asks the I-212's


def test_the_office_queues_them_and_the_client_reads_them_in_portuguese(tmp_path):
    from fastapi.testclient import TestClient

    from portal.app import create_app
    from portal.notify import Notifier
    from review.server import ReviewApp

    app = create_app(root=tmp_path / "portal", base_url="https://portal.example", secure_cookies=False,
                     notifier=Notifier(tmp_path / "portal" / "outbox.jsonl", env={}))
    store = app.state.store
    store.add_client("pilot-ana", "Ana Clara Exemplo Souza", email="ana@example.com", language="pt")
    (tmp_path / "clients").mkdir()
    review = ReviewApp(tmp_path / "clients", schema_path.path("field_map", "i485"), schema_path.path("template", "i485"), None,
                       tmp_path / "portal")
    with pytest.raises(ValueError, match="Enter your name"):
        review.filing_ask("pilot-ana", {"filing": "i601"})
    out = review.filing_ask("pilot-ana", {"filing": "i601", "reviewer": "Jane"})
    assert out == {"added": 3, "waiting": 3}
    assert review.filing_ask("pilot-ana", {"filing": "i601", "reviewer": "Jane"})["added"] == 0          # never twice
    review.ask_send("pilot-ana", {"reviewer": "Jane"})
    first = next(r for r in store.requests("pilot-ana") if r.get("facts") == ["i601.client_what_happened"])
    store.answer_request("pilot-ana", first["id"], reply="I USED A FALSE PASSPORT IN 2015")
    assert review.filing_ask("pilot-ana", {"filing": "i601", "reviewer": "Jane"})["added"] == 0          # answered, not yet in the case: not again
    client = TestClient(app)
    assert client.get(f"/l/{store.new_link_token('pilot-ana')}", follow_redirects=False).status_code == 303
    tasks = [t for t in client.get("/api/me").json()["tasks"] if t["kind"] == "request"]
    assert any(t["text"].startswith("Se você não puder morar nos EUA") and not t["in_english"] for t in tasks)


def test_the_case_page_offers_them_after_a_finding(tmp_path):
    refused = _graph(visa__result="Refused")
    d = _case(tmp_path, refused, "refused")
    offers = {f["filing"]: f for f in journey.journey(d, TODAY, graph=refused)["next_filings"]}
    assert not offers["i601"]["now"] and "the consulate refused the visa" in offers["i601"]["label"] and "i212" in offers
    chosen = _graph(visa__result="Refused", i601__situation=i601.CONSULAR)
    d = _case(tmp_path, chosen, "chosen")
    offer = next(f for f in journey.journey(d, TODAY, graph=chosen)["next_filings"] if f["filing"] == "i601")
    assert offer["now"] and offer["label"].endswith("the attorney chose it")
    quiet = _graph()
    d = _case(tmp_path, quiet, "quiet")
    assert not {"i601", "i212"} & {f["filing"] for f in journey.journey(d, TODAY, graph=quiet)["next_filings"]}


def test_registered_with_their_sources():
    assert filing_questions.module("i601") is i601 and filing_questions.module("i212") is reapply
    assert {"i601", "i212"} <= set(packet.FILINGS) and {"i601", "i212"} <= enotice.LOCKBOX_FILINGS
    fees = json.loads((schema_path.path("law", "fees")).read_text(encoding="utf-8"))
    assert (fees["paper"]["i601"], fees["paper"]["i212"]) == (1050, 1175) and "page 20" in fees["notes"]["i601"]
    items = {x["id"]: x for x in json.loads((schema_path.path("register", "maintenance")).read_text(encoding="utf-8"))["items"]}
    for iid in ("form_i601", "form_i212", "i601_addresses", "i212_addresses", "waivers_601_212_rules"):
        assert items[iid]["party"] == "provider" and items[iid]["source"], iid
    assert items["form_i601"]["check"]["type"] == "uscis_form_edition" and items["i601_addresses"]["check"]["known"] == "10/23/2025"
    for template in ("i601", "i212"):
        text = PdfReader(str(schema_path.path("template", template))).pages[0].extract_text()
        assert "01/20/25" in text, template
