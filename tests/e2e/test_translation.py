"""A foreign-language document through the screens (src/translation.py), on a made-up client cloned from the demo client
(tests/e2e/world.py): the attorney adds a translator on the Settings page; the filing packet's Translations panel offers
the Portuguese birth certificate's machine translation as a DRAFT; the translator is chosen and recorded as having signed;
the packet then holds the signed translation and certificate beside the original, and the checklist stops asking. A Haitian
Creole copy has no machine translation: it says a human translator is needed, takes the translator's typed text, and signs.
The Documents page and the review cards carry the summary built from the extractors' fields. Everything here is invented.
"""

from __future__ import annotations

import json
import re

TRANSLATOR = "Marta Tradutora Exemplo"


def _state(world, client: str) -> dict:
    return json.loads((world["clients"] / client / "translations.json").read_text(encoding="utf-8"))["documents"]


def _panel(screen):
    return screen.page.locator("#translations")


def _wait_for_toast(screen, seconds: int = 180) -> str:
    """The offline translator loads its model the first time (tens of seconds): wait for the app's answer, then check it."""
    screen.page.locator("#toast").wait_for(state="visible", timeout=seconds * 1000)
    return screen.toast()


def test_the_attorney_adds_a_translator_and_the_birth_certificate_is_translated_and_signed(world, attorney, paralegal):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-translate")
    # the birth certificate gives a birth city the client's own answer disagrees with: a review card citing the Portuguese document
    certificate = next(r for r in json.loads((d / "documents.json").read_text(encoding="utf-8"))["documents"] if r["type"] == "birth_certificate")
    w.add_fact(d, "applicant.birth_city", "CAMPINAS", certificate["doc_ids"][0], "birth_certificate")

    # Settings: a translator, with who and when
    attorney.page.goto("about:blank")
    attorney.page.goto(world["review"] + "#settings:translators")
    attorney.settle()
    section = attorney.page.locator("#set-translators")
    assert "No translators yet" in section.inner_text()
    attorney.page.get_by_label("Translator's full name").fill(TRANSLATOR)
    attorney.page.get_by_label("Translator works for").select_option(label="Outside translator")
    attorney.page.get_by_label("Translator's firm or business (optional)").fill("Exemplo Translations")
    section.get_by_label("Portuguese").check()
    section.get_by_label("Haitian Creole").check()
    attorney.page.get_by_label("Translator's statement of competence").fill("Native Portuguese speaker; ten years translating civil records.")
    attorney.page.get_by_role("button", name="Add the translator").click()
    assert attorney.toast() == "Translator added."
    attorney.settle()
    body = attorney.check("translation-settings")
    row = attorney.page.locator("#set-translators tr", has_text=TRANSLATOR).inner_text()
    assert "Outside translator" in row and "Portuguese, Haitian Creole" in row and "Ana Attorney" in row, row
    assert "settings.json" not in body and "_translators" not in body

    # the Documents page: what the document is, from the extractors' fields, and where its translation stands
    attorney.open("case-translate", "documents")
    body = attorney.check("translation-documents")
    row = attorney.page.locator("#documents tr", has_text="Birth certificate").inner_text()
    assert "Birth certificate in Portuguese" in row and "Needs a translation" in row, row
    assert "Read from the document by the system, not by a person" in row and "applicant." not in row

    # the packet: the machine's DRAFT
    attorney.open("case-translate", "packet", "i485")
    body = attorney.check("translation-packet-needed")
    panel = _panel(attorney).inner_text()
    assert "Translations of foreign-language documents" in panel and "Birth certificate (Portuguese)" in panel and "Needs a translation" in panel, panel
    assert re.search(r"No English translation of the birth certificate of \S.* \(Portuguese\) yet", body), body
    attorney.page.get_by_role("button", name="Make the translation").click()
    assert _wait_for_toast(attorney) == "Translation made."
    attorney.settle()
    text = attorney.page.get_by_role("textbox", name="English translation of the birth certificate")
    assert re.search(r"(?i)birth certificate", text.input_value()), text.input_value()
    assert "Translation: DRAFT, not signed" in _panel(attorney).inner_text() and "Machine translation (Argos Translate" in _panel(attorney).inner_text()
    assert "is a DRAFT" in attorney.text()
    pdf = attorney.page.request.get(world["review"].rstrip("/") + "/api/translation.pdf?client=case-translate&id=" + next(iter(_state(world, "case-translate"))))
    assert pdf.status == 200 and pdf.body()[:5] == b"%PDF-"

    # the attorney chooses the translator; a paralegal cannot
    paralegal.open("case-translate", "packet", "i485")
    assert paralegal.page.get_by_label("Translator for the birth certificate").is_disabled()
    attorney.page.get_by_label("Translator for the birth certificate").select_option(label=f"{TRANSLATOR} (Outside translator)")
    assert attorney.toast() == "Translator chosen."
    attorney.settle()
    attorney.page.get_by_role("button", name="The translator has signed").click()
    assert attorney.toast() == "Recorded as signed."
    attorney.settle()
    body = attorney.check("translation-packet-signed")
    panel = _panel(attorney).inner_text()
    assert "Translated and signed" in panel and re.search(rf"Signed \d\d/\d\d/\d{{4}} by {TRANSLATOR}\. Recorded by Ana Attorney", panel), panel
    card = attorney.page.locator("#translations .tr-card").first.inner_text()
    assert "Open the signed translation and certificate" in card and "DRAFT" not in card
    order = attorney.page.locator("section", has_text="In the packet, in order").first.inner_text()
    assert "English translation and translator's certificate: Birth certificate" in order, order
    todo = attorney.page.locator("section", has_text="Before you mail it").first.inner_text()
    assert "No English translation" not in todo and "is a DRAFT" not in todo
    assert "translator signs the certificate of translation" in todo
    saved = next(iter(_state(world, "case-translate").values()))
    assert saved["signed"]["by"] == "Ana Attorney" and saved["signed"]["translator"] == TRANSLATOR and saved["translator_set_by"]["who"] == "Ana Attorney"
    assert re.fullmatch(r"\d\d/\d\d/\d{4}", saved["signed"]["date"])

    # built into the packet, after the original, without a DRAFT mark
    attorney.page.get_by_role("button", name=re.compile("Build packet|Rebuild packet")).click()
    assert re.match(r"Packet built", _wait_for_toast(attorney, 120))
    from pypdf import PdfReader

    packet = PdfReader(str(world["clients"] / "case-translate" / "packet.pdf"))
    text = "\n".join(page.extract_text() or "" for page in packet.pages)
    assert "CERTIFICATE OF TRANSLATION" in text and re.search(r"Signed \d\d/\d\d/\d{4} by Marta Tradutora Exemplo", re.sub(r"\s+", " ", text))
    assert "DRAFT machine translation" not in text

    # the review cards: a source in Portuguese carries its summary
    items = attorney.page.request.get(world["review"].rstrip("/") + "/api/items?client=case-translate").json()
    notes = [s["foreign"] for c in items["cards"] for f in (c.get("facts") or []) + (c.get("context") or []) for s in f.get("sources") or [] if s.get("foreign")]
    assert notes and notes[0]["language_name"] == "Portuguese" and notes[0]["summary"].startswith("Birth certificate in Portuguese"), notes[:1]


def test_haitian_creole_goes_to_a_human_translator_who_types_the_translation(world, attorney):
    w = world["world"]
    d = w.clone(world["root"], "demo-ana", "case-creole")
    data = json.loads((d / "documents.json").read_text(encoding="utf-8"))
    for r in data["documents"]:
        if r["type"] == "birth_certificate":
            r["language"] = "ht"
    (d / "documents.json").write_text(json.dumps(data), encoding="utf-8")

    attorney.open("case-creole", "packet", "i485")
    body = attorney.check("translation-creole-needed")
    panel = _panel(attorney).inner_text()
    assert "Haitian Creole" in panel and "Needs a human translator" in panel and "no Haitian Creole model" in panel, panel
    assert attorney.page.get_by_role("button", name="Make the translation").count() == 0  # nothing to ask of a model that doesn't exist
    assert "needs a human translator" in body
    attorney.page.get_by_role("textbox", name="English translation of the birth certificate").fill("BIRTH CERTIFICATE\nName: Ana Clara Exemplo Souza")
    attorney.page.get_by_role("button", name="Save the translation").click()
    assert attorney.toast() == "Translation saved."
    attorney.settle()
    assert "Translation: DRAFT, not signed" in _panel(attorney).inner_text() and "Typed by Ana Attorney" in _panel(attorney).inner_text()
    attorney.page.get_by_label("Translator for the birth certificate").select_option(label=f"{TRANSLATOR} (Outside translator)")
    assert attorney.toast() == "Translator chosen."
    attorney.settle()
    attorney.page.get_by_role("button", name="The translator has signed").click()
    assert attorney.toast() == "Recorded as signed."
    attorney.settle()
    attorney.check("translation-creole-signed")
    assert "Translated and signed" in _panel(attorney).inner_text()
    state = next(iter(_state(world, "case-creole").values()))
    assert state["status"] == "typed" and state["typed_by"]["who"] == "Ana Attorney" and state["signed"]["translator"] == TRANSLATOR
