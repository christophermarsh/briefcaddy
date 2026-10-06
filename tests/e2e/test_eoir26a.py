"""The fee waiver request (Form EOIR-26A) from the client's own answers, in the browser (src/eoir26a.py): the attorney asks for the fee to be waived on a
motion to the judge; the office puts "Your monthly money" on the client's page; the client answers the nine lines on a phone in Portuguese, then signs
(typed name and a drawn signature the attorney allowed); the attorney approves item 4 and attests (refused until the EOIR ID is typed under Settings);
and the signed form is in the motion's packet. Everyone here is made up. E2E_SHOTS keeps a screenshot of each step."""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest
from pypdf import PdfReader

from test_pathways import answer, build

CASE = "case-waiver"
NAME = "Ana Clara Exemplo Souza"  # Implementation note.
ANSWERS = [("Pagamento de trabalho", "1.500,00"), ("Aluguel que você recebe", "0"), ("Juros das suas contas", "0,50"), ("Todo outro dinheiro", "350"),
           ("Aluguel ou prestação", "1.200"), ("Contas da casa", "180,25"), ("Pagamentos de dívidas", "120.50"), ("Custos de vida", "500"), ("Todas as outras despesas", "100")]


@pytest.fixture(scope="module")
def waiver_case(world):
    """A client in removal proceedings (case-court), in the portal in Portuguese."""
    from portal.store import PortalStore

    d = world["world"].clone(world["root"], "case-court", CASE)
    store = PortalStore(world["portal"])
    store.add_client(CASE, NAME, email="rita@example.com", language="pt")
    return d, store


def _shot(page, name: str) -> None:
    shots = os.environ.get("E2E_SHOTS")
    if shots:
        Path(shots).mkdir(parents=True, exist_ok=True)
        page.screenshot(path=str(Path(shots) / f"eoir26a-{name}.png"), full_page=True)


def test_the_client_answers_and_signs_in_portuguese_the_attorney_attests_and_the_form_is_in_the_motions_packet(world, browser, attorney, waiver_case):
    d, store = waiver_case
    page = attorney.page
    # the attorney asks the judge to waive the fee on the motion
    attorney.open(CASE, "packet", "court_motion")
    answer(attorney, "Ask the judge to waive the fee", "Yes")
    attorney.open(CASE, "feewaiver")
    text = attorney.check("eoir26a-start")
    assert "Where this stands" in text and "On the motion to the judge." in text and "has 0 of 9 lines" in text and "not answered" in text
    assert "Put the questions on the client's page" in text
    page.locator("#fw-send").click()
    attorney.toast()
    attorney.settle()
    assert "On the client's page since" in attorney.when_it_says("On the client's page since")

    # the client, on a phone, in Portuguese: the nine lines, with help, and the totals as she answers
    ctx = browser.new_context(viewport={"width": 390, "height": 844}, locale="pt-BR", has_touch=True)
    phone = ctx.new_page()
    errors = []
    phone.on("pageerror", lambda e: errors.append(str(e)))
    phone.goto(f"{world['portal_url']}/l/{store.new_link_token(CASE)}")
    phone.wait_for_load_state("networkidle")
    card = phone.locator("#money")
    assert "Seu dinheiro por mês" in card.inner_text() and "9 linhas faltam responder" in card.inner_text()
    _shot(phone, "1-card")
    phone.locator("#money-open").click()
    layer = phone.locator("#layer .panel")
    layer.wait_for(state="visible")
    sheet = layer.inner_text()
    assert "O tribunal pergunta sobre o seu dinheiro em um mês comum" in sheet and "Como transformar um valor em valor por mês" in sheet
    assert "Dinheiro de trabalhar para alguém" in sheet and "Responda todas as linhas para ver os totais." in sheet
    layer.locator("#q_fw_income_work").fill("muito")
    layer.locator("#q_fw_income_work").blur()
    layer.locator(".err:visible").first.wait_for()
    assert "Escreva um valor em dólares" in layer.inner_text()
    for label, value in ANSWERS:
        box = layer.get_by_label(label, exact=False).first
        box.fill(value)
        with phone.expect_response(lambda r: "/api/answers" in r.url and r.request.method == "PUT"):  # the answer is kept before the next one is typed (a person types slower than this test)
            box.blur()
    layer.locator("#money-totals", has_text="Todo o dinheiro que você recebe por mês").wait_for()
    totals = layer.locator("#money-totals").inner_text()
    assert "$1.850,50" in totals and "$2.100,75" in totals and "-$250,25" in totals
    _shot(phone, "2-answered")
    layer.get_by_role("button", name="Fechar").click()
    assert "O escritório está conferindo as suas respostas." in phone.locator("#money").inner_text()

    # the attorney: every figure with its source and date, the arithmetic, item 4 suggested from them
    attorney.open(CASE, "feewaiver")
    text = attorney.check("eoir26a-figures")
    assert "The client's answer in the portal" in text and "$1,500.00" in text and "Item 3, the difference: $1,850.50 - $2,100.75 = -$250.25" in text
    assert "Item 1.A, total income: 1,500.00 + 0.00 + 0.50 + 350.00 = $1,850.50" in text
    sentence = page.locator("#fw-sentence").input_value()
    assert sentence.startswith("My average monthly expenses ($2,100.75) are more than my average monthly income ($1,850.50).")
    page.locator("#fw-approve").click()
    attorney.toast()
    attorney.settle()
    assert "Approved: “My average monthly expenses" in attorney.when_it_says("Approved: “My average monthly expenses")
    page.locator("#fw-drawn").check()
    page.locator("#fw-open").click()
    attorney.toast()
    attorney.settle()
    text = attorney.when_it_says("Open on the client's page since", "a drawn signature allowed")
    assert "Open on the client's page since" in text and "a drawn signature allowed" in text

    # the client reads what she signs, in Portuguese beside the English, and signs with her name and a drawing
    phone.reload()
    phone.wait_for_load_state("networkidle")
    assert "O seu pedido de isenção da taxa espera a sua assinatura" in phone.locator("#money").inner_text()
    phone.locator("#money-open").click()
    layer = phone.locator("#layer .panel")
    layer.wait_for(state="visible")
    sign = layer.locator("#money-sign").inner_text()
    assert "Declaro, sob pena de perjúrio" in sign and "I declare under penalty of perjury, pursuant to 28 U.S.C. § 1746" in sign
    assert "Minhas despesas médias por mês ($2.100,75)" in sign and "My average monthly expenses ($2,100.75)" in sign
    layer.locator("#money-sign-go").click()
    assert "Marque a caixa e digite o seu nome completo primeiro." in layer.inner_text()           # said in Portuguese: nothing ticked
    layer.locator("#money-agree").check()
    layer.locator("#money-name").fill("Rita Outra Pessoa")  # another person's name: refused in Portuguese, nothing signed
    layer.locator("#money-sign-go").click()
    layer.locator(".err:visible", has_text="Só você pode assinar este pedido").wait_for()
    layer.locator("#money-name").fill(NAME)
    pad = layer.locator("canvas.pad")
    pad.scroll_into_view_if_needed()
    box = pad.bounding_box()
    phone.mouse.move(box["x"] + 20, box["y"] + box["height"] * 0.7)
    phone.mouse.down()
    for i in range(1, 12):
        phone.mouse.move(box["x"] + 20 + i * (box["width"] - 40) / 11, box["y"] + box["height"] * (0.3 if i % 2 else 0.7))
    phone.mouse.up()
    _shot(phone, "3-signing")
    layer.locator("#money-sign-go").click()
    phone.locator("#money", has_text="Pedido de isenção da taxa assinado em").wait_for()
    assert "Obrigado. O escritório recebeu o seu pedido assinado." in phone.locator("#money").inner_text()
    _shot(phone, "4-signed")
    assert not errors
    ctx.close()

    # the attorney: signed (typed name and drawing), the attestation refused until the EOIR ID is typed under Settings
    attorney.open(CASE, "feewaiver")
    text = attorney.check("eoir26a-signed")
    assert "Typed name and drawn signature in the portal" in text and f"typed name “{NAME}”" in text
    assert "The client tried to sign as another name on" in text
    assert "Type the attorney's EOIR ID under Settings." in text
    page.locator("#fw-attest-go").click()
    page.wait_for_function("() => document.querySelector('#main').innerText.includes('Type your full name first.') || document.querySelector('#main').innerText.includes('Type the attorney')")
    typed = page.get_by_label("Your full name, as you sign")
    typed.fill("Ana B. Exemplo")
    page.locator("#fw-attest-go").click()
    page.wait_for_function("() => document.querySelector('#main .err') && document.querySelector('#main .err').innerText.includes(\"Type the attorney's EOIR ID under Settings.\")")
    settings_path = Path(world["env"]["I485_SETTINGS"])
    before = settings_path.read_bytes() if settings_path.exists() else None
    firm = {"firm.preparer_given_name": "Ana", "firm.preparer_family_name": "Exemplo", "firm.attorney_bar_number": "000000", "firm.licensing_authority": "Massachusetts",
            "firm.street": "1 Example Plaza", "firm.city": "Boston", "firm.state": "MA", "firm.zip": "02101", "firm.phone": "6175550100", "firm.business_name": "Example Law LLP",
            "firm.eoir_id": "ZZ999999"}
    try:
        settings_path.write_text(json.dumps({"firm": {"values": firm, "updated_by": "E2E", "updated_at": "2026-10-03T09:00:00-04:00", "history": []}}), encoding="utf-8")
        attorney.open(CASE, "feewaiver")
        typed = page.get_by_label("Your full name, as you sign")
        typed.fill("Ana B. Exemplo")
        page.locator("#fw-attest-go").click()
        attorney.toast()
        attorney.settle()
        text = attorney.check("eoir26a-attested")
        assert "ATTESTED BY ANA EXEMPLO (EOIR ID ZZ999999)" in text.upper() and "Holding the packet" not in text

        # the form is in the motion's packet, signed and attested, with the signing record after it
        attorney.open(CASE, "packet", "court_motion")
        build(attorney, "eoir26a-motion-built")
        manifest = json.loads((d / "packet_court_motion.json").read_text(encoding="utf-8"))
        assert "EOIR-26A" in [s["tab"] for s in manifest["sections"]]
        form = PdfReader(str(d / "eoir26a_filled.pdf"))
        said = " ".join(" ".join(p.extract_text() or "" for p in form.pages).split())
        assert list(form.pages[0].images) and "/s/ Ana B. Exemplo" in said and "Signing record: Form EOIR-26A" in said and f"typed name “{NAME}”" in said
        fields = {k.rsplit(".", 1)[-1]: v.get("/V") for k, v in form.get_fields().items()}
        assert fields["MonthIncome"] == "1,850.50" and fields["TotalTot"] == "-250.25" and fields["EOIR ID Number"] == "ZZ999999" and fields["AlienSigDate"]
    finally:
        if before is None:
            settings_path.unlink(missing_ok=True)
        else:
            settings_path.write_bytes(before)
