"""More kinds of case, end to end through the screens -- each on its own made-up
client (cloned from the demo client, tests/e2e/world.py), so the order the
tests run in doesn't matter:

  a client's move -> the AR-11 first on the case, built;
  a work permit on its own -> its category, built;
  a denied N-400 -> the N-336 first on the case, built;
  a consular case -> the I-601A from More..., built;
  a consular refusal -> the I-601 offered, the I-212 in the same envelope, the client asked in the portal, built;
  a sibling's I-130 waiting for its priority date -> the waiting stage and the monthly setting;
  an asylum case in court -> the I-589 packet for the judge, no USCIS cover letter;
  the client in court (case-court) -> cancellation of removal offered, chosen, the EOIR-42B built.
  a detained client (case-bond) -> the bond request offered, the warning for a client not admitted, built;
  an in absentia order (case-absentia) -> the motion to reopen offered, no fee for lack of notice, built.
  a crime victim -> the U visa certification request (Supplement B), then the I-918 with a family member, built.
  a family packet under INA 245(i) with a household member's income -> Supplement A and an I-864A, built;
  an asylee's spouse abroad -> the I-730 deadline on the case, the I-730 packet built.
  a citizen who lost the certificate -> the N-565 offered, its reason recorded, built; a FOIA request for the client's file (G-639);
  a Sudanese client -> TPS offered with no registration period open, and why; a relative abroad -> parole (I-131 and I-134), built.
  a citizenship filed online -> Online chosen, the bundle built and downloaded, the
  filing recorded, the receipt number added; an SIJ I-485 says why it is paper.
"""

from __future__ import annotations

import io
import json
import re
import zipfile
from datetime import date, timedelta

from test_pathways import answer, build, next_filing, stage_is


def _client(world, name: str, named: bool = False):
    """named: a VAWA, T, U or asylum case is restricted (src/restricted.py): the attorney names the paralegal on it."""
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", name)
    w._not_sij(d)
    if named:
        w.name_paralegal(d)
    return w, d


def test_a_move_puts_the_ar11_first(world, paralegal):
    _client(world, "case-move")
    paralegal.open("case-move", "journey")
    paralegal.page.locator("summary", has_text="The client moved?").click()
    moved = (date.today() - timedelta(days=2)).isoformat()
    paralegal.page.fill("input[aria-label='Date the client moved']", moved)
    paralegal.page.fill("input[aria-label='New address']", "20 NEW STREET, SOMERVILLE MA 02143")
    paralegal.page.get_by_role("button", name="Record the move").click()
    paralegal.toast()
    next_filing(paralegal, "case-move", "address", "Change of address: AR-11")
    body = paralegal.check("case-move-address-panel")
    due = (date.today() - timedelta(days=2) + timedelta(days=10)).strftime("%m/%d/%Y")
    assert f"Due {due}" in body and "HARRISONBURG" in body, body[:1500]
    assert "The new home address isn't entered yet" in body                   # recorded, but the case still has the old address
    for label, value in (("The new home address: street", "20 NEW STREET"), ("The new home address: city", "SOMERVILLE"),
                         ("The new home address: ZIP code", "02143")):
        answer(paralegal, label, value)
    body = paralegal.check("case-move-address-entered")
    assert "The new home address isn't entered yet" not in body
    build(paralegal, "case-move-address-built")


def test_a_work_permit_on_its_own(world, paralegal):
    _client(world, "case-ead")
    paralegal.open("case-ead", "packet", "ead")
    body = paralegal.check("case-ead-panel")
    assert "I-765" in body and "category" in body.lower(), body[:1500]
    build(paralegal, "case-ead-built")


def test_a_denied_n400_gets_the_n336(world, paralegal):
    w, d = _client(world, "case-denied")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2020-03-01", "green-card.pdf", "green_card")
    w.add_notice(d, "IOE0999000901", "N-400", "receipt", "2026-05-01")
    w.add_notice(d, "IOE0999000901", "N-400", "denial", "2026-09-28")
    next_filing(paralegal, "case-denied", "n336", "Hearing on the denied N-400 (N-336): by 10/28/2026")
    body = paralegal.check("case-denied-n336-panel")
    assert "$830" in body and "NATZ" in body, body[:1500]
    build(paralegal, "case-denied-n336-built")


def test_the_provisional_waiver_from_more(world, paralegal):
    w, d = _client(world, "case-waiver")
    for key, value in {"petitioner.status": "USC", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "RAFAEL",
                       "family.relationship": "Spouse", "visa.nvc_case_number": "BOS2026000001"}.items():
        w.add_fact(d, key, value)
    w.add_notice(d, "IOE0999000950", "I-130", "approval", "2026-06-01")
    stage_is(paralegal, "case-waiver", "Immigrant visa", "provisional waiver (I-601A)")
    paralegal.open("case-waiver", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "i601a")
    paralegal.settle()
    body = paralegal.check("case-waiver-i601a-panel")
    assert "$795" in body and "P.O. BOX 4599" in body and "the client's" in body, body[:1500]
    relative = paralegal.page.locator("tr").filter(has_text="Part 4, 1.B: their given name").locator("input").first
    assert relative.input_value() == "RAFAEL"                                                  # the petitioner is the qualifying relative
    build(paralegal, "case-waiver-i601a-built")


def test_a_consular_refusal_gets_the_i601_with_the_i212(world, paralegal):
    """The consulate refused the visa: the case page offers the I-601 and the I-212; the I-601 opens from More..., goes to Phoenix,
    takes the I-212 into the same envelope (its questions, its fee, one cover letter), asks the client in the portal, and builds."""
    from portal.store import PortalStore

    w, d = _client(world, "case-i601")
    PortalStore(world["portal"]).add_client("case-i601", "Ana Clara Exemplo Souza", email="ana.i601@example.com", language="pt")
    for key, value in {"petitioner.status": "USC", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "RAFAEL",
                       "family.relationship": "Spouse", "visa.nvc_case_number": "RIO2026000123", "visa.result": "Refused"}.items():
        w.add_fact(d, key, value)
    paralegal.open("case-i601", "journey")
    body = paralegal.check("case-i601-journey")
    assert "Waiver of inadmissibility (I-601), if the attorney decides the client needs it: the consulate refused the visa" in body, body[:3000]
    paralegal.open("case-i601", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "i601")
    paralegal.settle()
    body = paralegal.check("case-i601-panel")
    assert "$1,050" in body and "ATTN: I-601 FOREIGN FILERS" in body and "P.O. BOX 21600" in body, body[:2000]
    relative = paralegal.page.locator("tr").filter(has_text="1.B: their given name").locator("input").first
    assert relative.input_value() == "RAFAEL"                                                  # the petitioner, from the case
    answer(paralegal, "Part 1, 19: Form I-212 filed with it", "Yes")
    body = paralegal.check("case-i601-pair")
    assert "$1,175" in body and "ATTN: I-212 FOREIGN FILERS" in body and "5.A: removed as a deportable alien" in body, body[:3000]
    paralegal.page.get_by_role("button", name=re.compile(r"Ask the client in the portal \(5\)")).click()
    assert "Added 5 questions to the client's list" in paralegal.toast()
    build(paralegal, "case-i601-built")
    m = (d / "packet_i601.json").read_text(encoding="utf-8")
    assert '"I-212"' in m and "G-1450 (I-601, $1,050)" in m and "G-1450 (I-212, $1,175)" in m and '"G-1145"' in m


def test_a_siblings_petition_waits_for_its_date(world, paralegal):
    w, d = _client(world, "case-sibling")
    for key, value in {"petitioner.status": "USC", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "LUCAS",
                       "family.relationship": "Sibling"}.items():
        w.add_fact(d, key, value)
    w.add_notice(d, "IOE0999000960", "I-130", "receipt", "2015-03-04")
    w.add_fact(d, "folder.notice.IOE0999000960.receipt_20150304.priority_date", "2015-03-01", "notice-i-130-receipt-2015-03-04.pdf", "uscis_notice")
    body = stage_is(paralegal, "case-sibling", "Family petition filed: waiting for the priority date")
    assert "F4: priority date 03/01/2015" in body and "Set this month's family Visa Bulletin (F4" in body and "Open Settings" in body, body[:2000]


def test_an_asylum_case_in_court_files_with_the_judge(world, paralegal):
    w, d = _client(world, "case-defensive", named=True)
    w.add_doc(d, "nta.pdf", "notice_to_appear")
    w.add_fact(d, "applicant.nta_present", "Yes", "nta.pdf", "notice_to_appear")
    w.add_fact(d, "asylum.court", "now")
    w.add_fact(d, "asylum.basis_political_opinion", "Yes")
    paralegal.open("case-defensive", "packet", "i589")
    body = paralegal.check("case-defensive-i589-panel")
    assert "Nothing is sent to USCIS" in body and "EOIR Payment Portal" in body, body[:2000]
    assert "Cover letter" not in paralegal.page.locator("section", has_text="In the packet, in order").first.inner_text()


def test_cancellation_of_removal_for_the_client_in_court(world, attorney):
    w = world["world"]
    w.clone(world["root"], "case-court", "case-cancel")          # the made-up client in removal proceedings (world.py: case-court)
    attorney.open("case-cancel", "journey")
    assert "Cancellation of removal (EOIR-42B), if the attorney chooses it" in attorney.check("case-cancel-journey")
    attorney.open("case-cancel", "packet")
    attorney.page.select_option("select[aria-label='More filings']", "cancellation")
    attorney.settle()
    body = attorney.check("case-cancel-panel")
    assert "$1,690" in body and "$30 a person" in body and "P.O. BOX 660099" in body and "4,000" in body, body[:2000]   # Massachusetts: Dallas
    answer(attorney, "The client applies on", "EOIR-42B (not a permanent resident)")
    answer(attorney, "The date the Notice to Appear was served", "2025-01-10")
    answer(attorney, "the first relative who would suffer", "U.S. citizen child")
    answer(attorney, "Their name (Last, First, Middle)", "EXEMPLO, LUCAS")
    body = attorney.check("case-cancel-answered")
    assert "10 years are reached on 07/15/2029" in body and "10 are required (INA 240A(b)(1)(A), (d)(1))" in body, body[:3000]   # arrived 07/15/2019
    assert "Form G-28" in body and "Form EOIR-42B" in body and "Exhibit" in body
    build(attorney, "case-cancel-built")
    attorney.open("case-cancel", "journey")
    assert "Cancellation of removal (EOIR-42B): the attorney chose it" in attorney.check("case-cancel-journey-chosen")


def _hearing(d, when: date, **extra) -> None:
    """One hearing on the case page, as the office enters it (src/journey.py mark "hearing")."""
    h = {"id": f"hearing.{when.isoformat()}.0", "date": when.isoformat(), "time": "9:00 AM", "kind": "Master calendar", "court": "Boston Immigration Court",
         "judge": None, "detained": False, "by": "E2E", "at": "2026-10-01T09:00:00"} | extra
    (d / "status.json").write_text(json.dumps({"journey": {"hearings": [h]}}), encoding="utf-8")


def test_a_bond_request_for_a_detained_client(world, attorney):
    w = world["world"]
    d = w.clone(world["root"], "case-court", "case-bond")                # in removal proceedings (world.py: case-court), now detained
    _hearing(d, date.today() + timedelta(days=18), detained=True)
    attorney.open("case-bond", "journey")
    assert "Bond request to the immigration judge: the client is detained" in attorney.check("case-bond-journey")
    attorney.open("case-bond", "packet")
    attorney.page.select_option("select[aria-label='More filings']", "court_bond")
    attorney.settle()
    body = attorney.check("case-bond-panel")
    assert "Matter of Guerra, 24 I&N Dec. 37, 40" in body and "8 CFR 1003.19(c)" in body and "15 New Sudbury Street, Room 320" in body, body[:3000]
    assert "None: no fee for a bond redetermination request" in body
    answer(attorney, "Where the client is detained", "EXAMPLE COUNTY HOUSE OF CORRECTION, EXAMPLETOWN, MA")
    answer(attorney, "How the client came to be in the U.S.", "Entered without inspection (never admitted)")
    body = attorney.check("case-bond-answered")
    assert "Matter of Yajure Hurtado, 29 I&N Dec. 216 (BIA 2025)" in body, body[:3000]           # warned, never decided
    order = attorney.page.locator("section", has_text="In the packet, in order").first.inner_text()
    assert order.index("Cover page (DETAINED)") < order.index("Proposed order for the judge's signature") and "Cover letter" not in order
    build(attorney, "case-bond-built")
    m = json.loads((d / "packet_court_bond.json").read_text(encoding="utf-8"))
    assert [s["tab"] for s in m["sections"]][-2:] == ["Proposed order", "Proof of service"] and m["payments"] == []


def test_a_motion_to_reopen_an_in_absentia_order(world, attorney):
    w = world["world"]
    d = w.clone(world["root"], "case-court", "case-absentia")
    ordered = date.today() - timedelta(days=30)
    _hearing(d, ordered, result={"outcome": "Removal ordered in absentia", "decision_date": ordered.isoformat(), "by": "E2E", "at": "2026-10-01T09:00:00"})
    attorney.open("case-absentia", "journey")
    assert "Motion to reopen the in absentia order: by" in attorney.check("case-absentia-journey")
    attorney.open("case-absentia", "packet")
    attorney.page.select_option("select[aria-label='More filings']", "court_motion")
    attorney.settle()
    body = attorney.check("case-absentia-panel")
    assert "What happened, in detail" in body and "$1,095" in body and "in duplicate (8 CFR 1003.23(b)(1)(ii))" in body, body[:3000]
    answer(attorney, "The ground", "No notice of the hearing")
    body = attorney.check("case-absentia-no-notice")
    assert "at any time (8 CFR 1003.23(b)(4)(ii))" in body and "8 CFR 1003.24(b)(2)(iii)" in body, body[:3000]
    build(attorney, "case-absentia-built")
    assert (d / "ijmotion_motion.pdf").exists() and (d / "eoir28_filled.pdf").exists() and not (d / "eoir26a_filled.pdf").exists()


def test_a_paid_filing_carries_its_card_authorization(world, paralegal):
    w, d = _client(world, "case-card")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    paralegal.open("case-card", "packet", "i90")
    body = paralegal.check("case-card-i90-panel")
    assert "G-1450 (I-90, $465)" in body and "never a check" in body, body[:2000]
    build(paralegal, "case-card-i90-built")
    m = (d / "packet_i90.json").read_text(encoding="utf-8")
    assert '"tab": "G-1450 (I-90, $465)"' in m and '"first_page": 1' in m                     # on top of the packet


def test_a_fee_waiver_rides_inside_the_n400(world, paralegal):
    w, d = _client(world, "case-waived")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    paralegal.open("case-waived", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "i912")
    paralegal.settle()
    answer(paralegal, "Which application the I-912 goes with", "N-400 (citizenship)")
    answer(paralegal, "Part 1, 1.A", "Yes")
    body = paralegal.check("case-waived-i912-panel")
    assert "Inside the N-400 packet" in body and "The benefit (e.g. MEDICAID, SNAP)" in body, body[:2000]
    paralegal.open("case-waived", "packet", "n400")
    body = paralegal.check("case-waived-n400-panel")
    assert "Form I-912, Request for Fee Waiver" in body and "G-1450 (N-400" not in body, body[:3000]
    build(paralegal, "case-waived-n400-built")


def test_a_vawa_self_petition_from_more(world, paralegal):
    w, d = _client(world, "case-vawa", named=True)  # Maria Exemplo (made up): her U.S. citizen husband abused her; she has left him
    for key, value in {"vawa.abuser_status": "U.S. citizen born in the United States", "vawa.abuser_family_name": "EXEMPLO ABUSADOR",
                       "vawa.abuser_given_name": "JOSE", "vawa.times_married": "1", "vawa.marriage_date": "2020-06-14", "vawa.marriage_place": "BOSTON, MA",
                       "vawa.marriage_status": "Still married", "vawa.good_faith": "Yes", "vawa.remarried": "No", "vawa.married_in_proceedings": "No",
                       "vawa.lived_from": "2020-06-14", "vawa.lived_to": "2026-03-01", "vawa.lives_with_abuser": "No", "vawa.last_street": "12 SHARED AVE",
                       "vawa.last_city": "REVERE", "vawa.last_from": "2023-01-01", "vawa.abused": "Yes", "vawa.abused_who": "The client",
                       "vawa.gmc_concern": "No", "vawa.in_us": "Yes", "vawa.request_ead": "Yes", "vawa.concurrent_i485": "No"}.items():
        w.add_fact(d, key, value)
    paralegal.open("case-vawa", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "vawa")
    paralegal.settle()
    answer(paralegal, "Part 2: the client is the abuser's", "Spouse")
    body = paralegal.check("case-vawa-panel")
    assert "No safe mailing address chosen" in body and "Part 10, 8.A" in body, body[:2000]          # the spouse's questions, once Spouse is chosen
    answer(paralegal, "Where USCIS mails the client: the safe address", "The office's address")
    body = paralegal.check("case-vawa-safe")
    assert "No safe mailing address" not in body and "ATTN: 1367" in body and "8 U.S.C. 1367" in body and "$0" in body, body[:2000]
    assert "Special Immigrant Juvenile" not in paralegal.page.locator("section", has_text="In the packet, in order").first.inner_text()
    build(paralegal, "case-vawa-built")
    m = (d / "packet_vawa.json").read_text(encoding="utf-8")
    assert '"G-1145"' in m and '"I-360"' in m and "G-1450" not in m                                    # the receipt e-mail on top; nothing to pay
    stage_is(paralegal, "case-vawa", "VAWA self-petition (I-360) to prepare and file", "File the VAWA self-petition (I-360)")


def test_an_expedite_request(world, paralegal):
    w, d = _client(world, "case-expedite")
    w.add_notice(d, "IOE0999000990", "I-131", "receipt", "2026-07-01")
    paralegal.open("case-expedite", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "expedite")
    paralegal.settle()
    receipt = paralegal.page.locator("tr").filter(has_text="The pending case's receipt number").locator("input").first
    assert receipt.input_value() == "IOE0999000990"
    answer(paralegal, "USCIS's criterion it meets", "Emergency or urgent humanitarian situation")
    answer(paralegal, "Why it is urgent", "The client's mother is gravely ill abroad.")
    answer(paralegal, "The evidence, one item per line", "Hospital letter")
    body = paralegal.check("case-expedite-panel")
    assert "800-375-5283" in body and "Ask once" in body, body[:2000]
    build(paralegal, "case-expedite-built")


def test_a_u_visa_from_the_certification_request_to_the_petition(world, paralegal):
    w, d = _client(world, "case-uvisa", named=True)
    w.add_doc(d, "police-report.pdf", "police_report")
    w.add_doc(d, "personal-statement.pdf", "declaration")
    paralegal.open("case-uvisa", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "u_cert")
    paralegal.settle()
    for label, value in (("The qualifying criminal activity", "Felonious assault"), ("When it happened", "2026-01-10"),
                         ("Where it happened", "SOMERVILLE, MA"), ("The agency's case or report number", "2026-000123"),
                         ("The certifying agency (the police", "EXAMPLE CITY POLICE DEPARTMENT"), ("for certification requests: street", "1 EXAMPLE PLAZA"),
                         ("The agency's city", "SOMERVILLE"), ("The agency's state", "MA"), ("The agency's ZIP code", "02143"),
                         ("Where that address comes from", "The agency's web page, read 10/01/2026")):
        answer(paralegal, label, value)
    body = paralegal.check("case-uvisa-cert-panel")
    assert "Parts 2 to 6 are the certifying official's" in body and "never a guessed address" in body, body[:2000]
    body = build(paralegal, "case-uvisa-cert-built")
    assert "Supplement B: The certifying official signs" in body and "G-1145" not in body, body[:3000]
    m = (d / "packet_u_cert.json").read_text(encoding="utf-8")
    assert '"tab": "Request letter"' in m and '"tab": "Supplement B"' in m
    stage_is(paralegal, "case-uvisa", "U visa: asking for the certification", "Ask the certifying agency to sign the certification")
    # the signed Supplement B is back: the petition, with one family member
    paralegal.open("case-uvisa", "packet", "u_visa")
    signed = (date.today() - timedelta(days=10)).isoformat()
    for label, value in (("the date the certifying official signed it", signed), ("The office holds the ORIGINAL", "Yes"),
                         ("Part 2, 5:", "Yes"), ("Part 2, 2:", "Yes"), ("Part 2, 3:", "Yes"), ("The safe mailing address", "In care of the attorney, at the office"),
                         ("Part 2, 7.A", "No"), ("With the Part 3 answers", "No"), ("Qualifying family members petitioned for", "1")):
        answer(paralegal, label, value)
    for label, value in (("Supplement A, Part 1", "Child"), ("Part 3, 1.A: family name", "EXEMPLO"), ("Part 3, 1.B: given name", "LIA"),
                         ("Part 3, 8: date of birth", "2018-05-05"), ("Part 3, 12: sex", "Female"), ("Part 3, 11: marital status", "Single"),
                         ("Part 3, 9: country of birth", "BRAZIL"), ("Part 3, 10: country of citizenship", "BRAZIL"), ("Lives in the United States now?", "Yes"),
                         ("lives at the client's home address?", "Yes"), ("Did this family member commit the crime", "No")):
        answer(paralegal, label, value)
    body = paralegal.check("case-uvisa-i918-panel")
    assert "ATTN: 1367" in body and "$0" in body and "214.14(c)(2)(i)" in body, body[:2500]
    body = build(paralegal, "case-uvisa-i918-built")
    assert "ORIGINAL Supplement B" in body and "Supplement A (1): The family member signs" in body, body[:3000]
    m = (d / "packet_u_visa.json").read_text(encoding="utf-8")
    assert '"tab": "G-1145"' in m and '"tab": "Supplement A (1)"' in m and '"tab": "I-192"' not in m and "ATTN: 1367" in m
    stage_is(paralegal, "case-uvisa", "U visa: ready to file the petition (I-918)", "File the U petition (I-918)")
def test_a_family_packet_with_supplement_a_and_an_i864a(world, paralegal):
    w, d = _client(world, "case-245i")
    for key, value in {"petitioner.status": "USC", "petitioner.family_name": "EXEMPLO", "petitioner.given_name": "MARCOS",
                       "family.relationship": "Child"}.items():
        w.add_fact(d, key, value)
    paralegal.open("case-245i", "packet", "family")
    answer(paralegal, "Is the client adjusting under INA 245(i)", "Yes")
    for label, value in (("Supplement A Part 2, 1: the client is", "Spouse or unmarried child under 21 of one of the above, accompanying or following to join"),
                         ("The qualifying petition or labor certification was filed", "1999-06-01"),
                         ("Part 2, 3: the principal beneficiary of that petition: family name", "EXEMPLO"),
                         ("Part 2, 3: the principal beneficiary: given name", "MARCOS"),
                         ("Supplement A sum: an exemption", "None: the sum is paid"), ("Part 3, 1.c:", "Yes"),
                         ("Household members whose income the sponsor counts", "1"),
                         ("Household member 1: who they are", "The sponsor's spouse"), ("Household member 1: family name", "EXEMPLO"),
                         ("Household member 1: given name", "MARIA"), ("Household member 1: date of birth", "1982-04-04"),
                         ("Household member 1: country of birth", "BRAZIL"), ("Household member 1: home address, street", "10 EXAMPLE ST"),
                         ("Household member 1: city", "SOMERVILLE"), ("Household member 1: state", "MA"), ("Household member 1: ZIP code", "02143"),
                         ("Household member 1: currently", "Employed"), ("Household member 1: current individual annual income", "31000"),
                         ("Household member 1: most recent tax year", "2025"), ("Household member 1: total income that year", "30500")):
        answer(paralegal, label, value)
    body = paralegal.check("case-245i-family-panel")
    assert "Supplement A (INA 245(i)). $1,000" in body and "1 Form I-864A in the packet" in body, body[:3000]
    build(paralegal, "case-245i-family-built")
    m = (d / "packet_family.json").read_text(encoding="utf-8")
    assert '"tab": "G-1450 (I-485 Supplement A, $1,000)"' in m and '"tab": "I-485 Supplement A"' in m and '"tab": "I-864A (1)"' in m
    assert (d / "i485supa_filled.pdf").exists() and (d / "i864a_1_filled.pdf").exists()


def test_an_asylees_spouse_gets_the_i730(world, paralegal):
    w, d = _client(world, "case-asylee-i730", named=True)
    granted = date.today() - timedelta(days=100)
    w.add_notice(d, "ZLA2690000777", "I-589", "approval", granted.isoformat())
    last = (granted.replace(year=granted.year + 2) if (granted.month, granted.day) != (2, 29) else date(granted.year + 2, 3, 1)) - timedelta(days=1)
    body = stage_is(paralegal, "case-asylee-i730", "Granted asylum (asylee)", f"Spouse or children (I-730): USCIS must receive it by {last:%m/%d/%Y}")
    assert "Form I-730 for each" in body, body[:2000]
    paralegal.open("case-asylee-i730", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "i730")
    paralegal.settle()
    for label, value in (("The client was the principal applicant", "Yes"), ("Part 1, 22: where asylum was granted: city", "BOSTON"),
                         ("Part 1, 22: state", "MA"), ("How many relatives", "1"), ("Relative 1: the client's", "Spouse"),
                         ("Relative 1: family name", "EXEMPLO"), ("Relative 1: given name", "PEDRO"), ("Relative 1: date of birth", "1985-05-05"),
                         ("Relative 1: sex", "M"), ("Relative 1: country of birth", "BRAZIL"), ("Relative 1: country of citizenship", "BRAZIL"),
                         ("Relative 1: where they are now", "Outside the United States"), ("Relative 1: where they live: street", "RUA EXEMPLO 10"),
                         ("Relative 1: where they live: city or town", "RECIFE"), ("Relative 1: where they live: country", "BRAZIL"),
                         ("Relative 1: if outside the U.S., the U.S. embassy", "RECIFE, BRAZIL")):
        answer(paralegal, label, value)
    body = paralegal.check("case-asylee-i730-panel")
    assert "P.O. Box 20018" in body and "$0" in body and f"by {last:%m/%d/%Y}" in body, body[:3000]
    build(paralegal, "case-asylee-i730-built")
    m = (d / "packet_i730.json").read_text(encoding="utf-8")
    assert '"tab": "G-1145"' in m and '"tab": "I-730 (1)"' in m and '"tab": "G-28 (I-730, 1)"' in m and '"payments": []' in m
def test_a_lost_naturalization_certificate_gets_the_n565(world, paralegal):
    w, d = _client(world, "case-n565")
    w.add_notice(d, "IOE0999000321", "N-400", "approval", "2020-06-01")
    import journey

    journey.mark(d, "oath", "E2E", value={"date": "2020-07-04"})
    body = stage_is(paralegal, "case-n565", "U.S. citizen")
    assert "Replacement certificate (N-565)" in body, body[:2000]                          # the stage's step says where; it is not a next filing until a person records why
    w.add_doc(d, "passport.pdf", "passport")
    w.add_doc(d, "police-report.pdf", "police_report")
    paralegal.open("case-n565", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "n565")
    paralegal.settle()
    for label, value in (("Why the client needs a new document", "Lost, stolen or destroyed"),
                         ("When, where and how it happened", "Lost in a move in 2024: left in a box."),
                         ("Who issued it: the USCIS office", "USCIS BOSTON"), ("Date it was issued", "2020-07-04"),
                         ("has the client lost or renounced U.S. citizenship", "No")):
        answer(paralegal, label, value)
    body = paralegal.check("case-n565-panel")
    assert "$555" in body and "P.O. Box 20050" in body and "Nebraska Service Center" in body, body[:3000]
    build(paralegal, "case-n565-built")
    m = (d / "packet_n565.json").read_text(encoding="utf-8")
    assert '"tab": "G-1145"' in m and '"tab": "N-565"' in m and "G-1450 (N-565, $555)" in m
    body = stage_is(paralegal, "case-n565", "U.S. citizen")
    assert "Replacement certificate (N-565): lost, stolen or destroyed" in body, body[:2500]   # a person recorded why: it is now the next filing


def test_a_foia_request_for_the_clients_file(world, paralegal):
    w, d = _client(world, "case-foia")
    w.add_notice(d, "IOE0999000111", "I-485", "receipt", "2025-05-01")
    paralegal.open("case-foia", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "g639")
    paralegal.settle()
    for label, value in (("What to ask USCIS for", "The complete A-file"), ("a date scheduled for an immigration court hearing", "No"),
                         ("agrees to pay search and copying charges", "Yes")):
        answer(paralegal, label, value)
    body = paralegal.check("case-foia-panel")
    assert "first.uscis.gov" in body and "01/22/2026" in body and "gives no mailing address" in body, body[:3000]
    receipt = paralegal.page.locator("tr").filter(has_text="A USCIS receipt number the client has filed under").locator("input").first
    assert receipt.input_value() == "IOE0999000111"                                             # the newest notice in the case
    build(paralegal, "case-foia-built")
    m = (d / "packet_g639.json").read_text(encoding="utf-8")
    assert '"tab": "G-639"' in m and '"G-1145"' not in m and '"payments": []' in m


def test_tps_is_offered_but_nothing_is_open(world, paralegal):
    w, d = _client(world, "case-tps")
    w.drop_facts(d, "applicant.citizenship")
    w.add_fact(d, "applicant.citizenship", "SUDAN")
    paralegal.open("case-tps", "journey")
    body = paralegal.check("case-tps-journey")
    assert "TPS for Sudan" not in body, body[:2500]                      # no period is open: not a next filing, only on the More filings list
    paralegal.open("case-tps", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "tps")
    paralegal.settle()
    answer(paralegal, "Initial registration or re-registration", "Initial registration (first time)")
    body = paralegal.check("case-tps-panel")
    assert "No registration period is open for Sudan" in body and "8 CFR 244.17" in body and "P.O. Box 6943" in body, body[:3500]
    assert "$510" in body and "$30 biometric" in body and "$560" in body, body[:3500]
    build(paralegal, "case-tps-built")                       # a draft: the problems are on its pages


def test_humanitarian_parole_for_a_relative_abroad(world, paralegal):
    w, d = _client(world, "case-parole")
    w.add_doc(d, "relative-passport.pdf", "passport")
    w.add_doc(d, "tax-return.pdf", "tax_return")
    paralegal.open("case-parole", "packet")
    paralegal.page.select_option("select[aria-label='More filings']", "parole")
    paralegal.settle()
    for label, value in (("Who the request is for", "The client, for someone else who is outside the U.S."), ("How many people", "1"),
                         ("How the person qualifies for parole", "Needs surgery that is not available at home."), ("How long they expect to stay", "6 months"),
                         ("USCIS should notify: city", "RECIFE"), ("That office: country", "BRAZIL"),
                         ("Why the person can't get a U.S. visa", "The consulate's first appointment is two years away."),
                         ("Person 1: their relationship", "SISTER"), ("Person 1: family name", "EXEMPLO"), ("Person 1: given name", "LUCIA"),
                         ("Person 1: date of birth", "1990-05-05"), ("Person 1: sex", "F"), ("Person 1: marital status", "Single"),
                         ("Person 1: city or town of birth", "RECIFE"), ("Person 1: country of birth", "BRAZIL"), ("Person 1: country of citizenship", "BRAZIL"),
                         ("Person 1: where they live now: street", "RUA EXEMPLO 10"), ("Person 1: where they live now: city", "RECIFE"),
                         ("Person 1: where they live now: country", "BRAZIL"), ("Person 1: ever in exclusion", "No"),
                         ("Who agrees to support them financially", "The client (who lives in the U.S.)"),
                         ("The supporter's immigration status now", "U.S. citizen"), ("The supporter's relationship to the person", "SISTER"),
                         ("Employment status", "Employed"), ("Employed as", "NURSE"), ("Name of the employer", "EXAMPLE HOSPITAL"),
                         ("How many other I-134", "0"), ("How many other dependents", "2"), ("current annual income", "62000"),
                         ("Will the supporter also make specific contributions", "No"), ("daytime telephone", "6175550101")):
        answer(paralegal, label, value)
    body = paralegal.check("case-parole-panel")
    assert "P.O. Box 660865" in body and "$630" in body and "$1,020" in body and "Not built here" in body, body[:3500]
    build(paralegal, "case-parole-built")
    m = (d / "packet_parole.json").read_text(encoding="utf-8")
    assert '"tab": "G-1145"' in m and '"tab": "I-131 (1)"' in m and '"tab": "I-134 (1)"' in m and "G-1450 (I-131, $630)" in m and "P.O. Box 660865" in m
    assert (d / "i131_parole_1_filled.pdf").exists() and (d / "i134_1_filled.pdf").exists()


def test_a_citizenship_filed_online_by_pdf_upload(world, paralegal, attorney):
    w, d = _client(world, "case-upload")
    w.add_doc(d, "green-card.pdf", "green_card")
    w.add_fact(d, "n400.lpr_date", "2018-03-01", "green-card.pdf", "green_card")
    paralegal.open("case-upload", "packet", "i485")                                         # an SIJ-style I-485: paper, and why
    body = paralegal.check("case-upload-i485-paper")
    assert "Filed on paper." in body and "only when it is filed together with its I-130" in body, body[:2000]
    paralegal.open("case-upload", "packet", "n400")
    body = paralegal.check("case-upload-n400-choice")
    assert "How it is filed:" in body and "Form N-400: $710 online, $760 on paper" in body, body[:2500]
    paralegal.page.get_by_role("radio", name="Online (PDF upload)").click()
    paralegal.toast()
    paralegal.settle()
    paralegal.page.get_by_role("button", name=re.compile("uild online-filing bundle")).click()
    assert "Online-filing bundle built" in paralegal.toast()
    paralegal.settle()
    body = paralegal.check("case-upload-n400-bundle")
    assert "uploading it, step by step" in body.lower() and "Upload a Filled-Out PDF Form" in body and "Form N-400 $710" in body, body[:3000]
    assert "before you mail it" not in body.lower() and "G-1450 (N-400" not in body and "Cover letter" not in body
    href = paralegal.page.get_by_role("link", name=re.compile("Download the bundle")).get_attribute("href")
    r = paralegal.page.request.get(world["review"].rstrip("/") + href)
    names = zipfile.ZipFile(io.BytesIO(r.body())).namelist()
    assert r.status == 200 and names[0] == "0 Checklist (read first).pdf" and any("N-400 (print, sign in ink, scan)" in n for n in names)

    attorney.open("case-upload", "packet", "n400")                                          # the attorney uploads it and records it
    attorney.page.locator("input[placeholder^='Reason to']").fill("E2E: made-up client")
    attorney.page.get_by_role("button", name="Record the filing").click()
    assert "recorded" in attorney.toast()
    attorney.settle()
    paralegal.open("case-upload", "packet", "n400")                                         # the receipt number, off the case card
    paralegal.page.fill("input[aria-label='Receipt number from the case card']", "IOE0999000888")
    paralegal.page.get_by_role("button", name="Add the receipt number").click()
    assert "Receipt number added" in paralegal.toast()
    paralegal.settle()
    # the panel is laid out again after the receipt is saved: wait for it, not a fixed pause (slower with several test workers)
    paralegal.page.get_by_text(re.compile("Filed in the USCIS online account")).first.wait_for(timeout=20000)
    body = paralegal.check("case-upload-n400-filed")
    assert "Filed in the USCIS online account, receipt IOE0999000888" in body, body[-2000:]
    paralegal.open("case-upload", "journey")
    body = paralegal.check("case-upload-journey")
    assert "Filed online in the USCIS online account" in body and "IOE0999000888" in body

    attorney.page.goto(world["review"])                                                     # no API keys in the test world: it says so
    attorney.settle()
    attorney.page.get_by_role("button", name=re.compile("Keeping current")).click()
    attorney.settle()
    body = attorney.check("case-upload-keeping-current")
    assert "USCIS case status, checked automatically every night" in body and "Not switched on" in body
