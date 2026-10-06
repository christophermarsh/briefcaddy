"""A made-up client for demonstrations: the whole flow -- portal answers,
document photos, cross-checks, the paralegal's review, the filled I-485 --
with nothing from any real client.

    python src/portal/demo.py            # a demo client with answers and documents, processed, ready to review
    python src/portal/demo.py --fresh    # a demo client who has only been invited (to fill the portal live)
    python src/portal/demo.py --reset    # remove the demo client everywhere
    python tools/demo_portal.py          # the showcase client (below), in a temp folder, with its sign-in link: the two-minute tour

Everything is invented and labelled so: the name "EXEMPLO", the ICAO-style
passport number, an SSN in the range used in examples, a receipt number
starting IOE0999. A few things are planted to show what the system does:
the client typed one digit of the SSN wrong (the portal asks which is
right), answered "I'm not sure" to one eligibility question (the attorney
gets a list), belongs to a church youth group (Part 9 items 1-9), and has
two earlier jobs (Part 14).

The showcase client (showcase(), tools/demo_portal.py) is a second made-up person for the two-minute tour of the client's side:
a Portuguese-speaking SIJ applicant with the questionnaire half done, one blurry passport photo to retake, a thread with the office
(an answer waiting to be read, a new question waiting for the office) and a biometrics appointment next month, with its
what-to-bring page. Everything about her is invented and labelled so.
"""

from __future__ import annotations

import argparse
import io
import json
import os
import shutil
import sys
from datetime import date, timedelta
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from fill.continuation import _Page  # noqa: E402
from portal.bank import all_questions, clean, load_bank, missing_required  # noqa: E402
from portal.store import PortalStore  # noqa: E402
import clock

REPO = Path(__file__).resolve().parents[2]
DEMO_ID = "demo-ana"
PROFILE = {"name": "Ana Clara Exemplo Souza", "phone": "+1 555 010 0142", "email": "demo.client@example.com", "language": "pt",
           "consent": {"email": True, "sms": False, "whatsapp": False}}

ANSWERS = {
    "given_name": "Ana Clara", "family_name": "Exemplo Souza", "used_other_names": "No", "name_changed": "No", "dob": "2006-03-14", "other_dob": "No",
    "sex": "F", "birth_city": "Sorocaba", "birth_country": "Brasil", "citizenship": "Brasil", "dual_citizenship": "No",
    "a_number": "A099000123", "other_a_number": "No", "has_ssn": "Yes", "ssn": "123-45-6780",  # one digit off the card: planted
    "phone": "+1 555 010 0142", "email": "demo.client@example.com",
    "home_address": {"street": "10 Example Street", "apt": "2", "city": "Springfield", "state": "MA", "zip": "01103"},
    "home_since": "2022-08-01", "mail_here": "Yes",
    "address_history": [{"street": "45 Sample Avenue", "city": "Springfield", "state": "MA", "zip": "01109", "country": "USA",
                         "date_from": "2019-07-20", "date_to": "2022-07-31"}],
    "last_foreign_address": {"street": "Rua das Flores 100", "city": "Sorocaba", "province": "SP", "postal_code": "18000-000",
                             "country": "Brasil", "date_from": "2006-03-14", "date_to": "2019-07-14"},
    "current_job": {"employer": "Springfield Example High School", "occupation": "Student", "street": "1 School Road",
                    "city": "Springfield", "state": "MA", "zip": "01103", "date_from": "2022-09-01"},
    "job_history": [{"employer": "Example Bakery", "occupation": "Cashier", "city": "Springfield", "state": "MA", "country": "USA",
                     "date_from": "2021-06-01", "date_to": "2021-08-31"},
                    {"employer": "Sample Middle School", "occupation": "Student", "city": "Springfield", "state": "MA", "country": "USA",
                     "date_from": "2019-09-01", "date_to": "2022-06-15"}],
    "last_foreign_job": {"employer": "Escola Estadual Exemplo", "occupation": "Estudante", "city": "Sorocaba", "country": "Brasil",
                         "date_from": "2012-02-01", "date_to": "2019-06-30"},
    "last_entry_date": "2019-07-15", "last_entry_place": {"city": "Boston", "state": "MA"}, "entry_how": "inspected", "visa_type": "B-2",
    "first_time_in_us": "Yes", "has_i94": "Yes", "applied_visa_abroad": "No", "someone_petitioned": "No", "other_processes": "No",
    "mother_name": "Maria Exemplo Lima", "mother_dob": "1984-05-02", "mother_birth_country": "Brasil", "mother_deceased": "No",
    "father_name": "Jose Exemplo Souza", "father_birth_country": "Brasil", "father_deceased": "No",
    "marital_status": "Single", "total_children": 0,
    "ethnicity": "Not Hispanic or Latino", "race": "White", "height": {"cm": "160"}, "weight": {"kg": "55"},
    "eye_color": "Brown", "hair_color": "Brown",
    "applied_relief": "Unsure",  # planted: the attorney gets it on the "not sure" list
    "org_member": "Yes",
    "organizations": [{"name": "Example Church Youth Group", "city": "Springfield", "state": "MA", "country": "USA",
                       "nature": "Church youth group", "involvement": "Member", "date_from": "2020-01-01"}],
}


def _check_digit(field: str) -> int:
    values = {**{str(d): d for d in range(10)}, **{chr(65 + i): 10 + i for i in range(26)}, "<": 0}
    return sum(values[c] * (7, 3, 1)[i % 3] for i, c in enumerate(field)) % 10


def _mrz() -> tuple[str, str]:
    number, nationality, dob, sex, expiry = "XX0001234", "BRA", "060314", "F", "320110"
    line1 = ("P<BRAEXEMPLO<SOUZA<<ANA<CLARA").ljust(44, "<")
    personal = "<" * 14
    core = f"{number}{_check_digit(number)}{nationality}{dob}{_check_digit(dob)}{sex}{expiry}{_check_digit(expiry)}{personal}{_check_digit(personal)}"
    final = _check_digit(core[0:10] + core[13:20] + core[21:43])
    return line1, core + str(final)


DOCUMENTS = {
    "passport": ("passport.pdf", ["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE PASSPORT", "EXEMPLO: DEMONSTRATION DOCUMENT",
                                  "SOBRENOME / SURNAME: EXEMPLO SOUZA", "NOME / GIVEN NAMES: ANA CLARA",
                                  "NACIONALIDADE / NATIONALITY: BRASILEIRO(A)", "DATA DE NASCIMENTO / DATE OF BIRTH: 14 MAR 2006",
                                  "SEXO / SEX: F", "NATURALIDADE / PLACE OF BIRTH: SOROCABA/SP",
                                  "DATA DE EXPEDICAO / DATE OF ISSUE: 11 JAN/JAN 2022", "VALIDADE / DATE OF EXPIRY: 10 JAN/JAN 2032", "", *_mrz()]),
    "birth_certificate": ("certidao.pdf", ["REPUBLICA FEDERATIVA DO BRASIL", "CERTIDAO DE NASCIMENTO", "EXEMPLO: DEMONSTRATION DOCUMENT",
                                           "NOME: /", "ANA CLARA EXEMPLO SOUZA /",
                                           "MUNICIPIO DE REGISTRO E UF LOCAL, MUNICIPIO DE NASCIMENTO E UF SEXO",
                                           "SOROCABA - SP HOSPITAL REGIONAL, SOROCABA-SP. F",
                                           "FILIAGAO",
                                           "JOSE EXEMPLO SOUZA, NACIONALIDADE: BRASILEIRO(A), NATURALIDADE: SOROCABA-SP, e MARIA",
                                           "EXEMPLO LIMA, NACIONALIDADE: BRASILEIRO(A), NATURALIDADE: SOROCABA-SP.",
                                           "AVOS", "PEDRO EXEMPLO e LUCIA EXEMPLO",
                                           "DATA DO REGISTRO: 20/03/2006", "DATA DA EMISSAO: 15/05/2023"]),
    "i360_approval": ("i360-approval.pdf", ["NOTICE OF ACTION I-797", "THE UNITED STATES OF AMERICA", "EXEMPLO: DEMONSTRATION DOCUMENT",
                                            "Receipt Number Case Type",
                                            "IOE0999000123 I360 - PETITION FOR AMERASIAN, WIDOW(ER), OR SPECIAL IMMIGRANT",
                                            "Received Date Priority Date Petitioner A099 000 123",
                                            "02/10/2025 02/10/2025 EXEMPLO SOUZA, ANA CLARA",
                                            "Notice Date Page Beneficiary A099 000 123",
                                            "08/20/2025 1 of 1 EXEMPLO SOUZA, ANA CLARA",
                                            "Notice Type: Approval Notice",
                                            "Section: Special Immigrant Juvenile"]),
    "i94": ("i94.pdf", ["I-94/I-95 Official Website - Get Most Recent Response", "Most Recent I-94", "EXEMPLO: DEMONSTRATION DOCUMENT",
                        "Admission (I-94) Record Number: 99900012300", "Arrival/Issued Date: 2019 July 15", "Class of Admission: B2",
                        "Admit Until Date: 01/14/2020", "Last/Surname: EXEMPLO SOUZA", "First (Given) Name: ANA CLARA",
                        "Birth Date: 03/14/2006", "Document Number: XX0001234", "Country of Citizenship: Brazil"]),
    "ssn_card": ("ssn-card.pdf", ["SOCIAL SECURITY", "EXEMPLO: DEMONSTRATION DOCUMENT", "THIS NUMBER HAS BEEN ESTABLISHED FOR",
                                  "123-45-6789", "ANA CLARA EXEMPLO SOUZA", "VALID FOR WORK ONLY WITH DHS AUTHORIZATION"]),
}


def document_pdf(lines: list[str]) -> bytes:
    """A one-page PDF with a text layer -- what a clean scan becomes after OCR."""
    from pypdf import PdfWriter

    writer, page = PdfWriter(), _Page()
    y = 740
    for line in lines:
        mono = line.startswith("P<") or (len(line) >= 40 and "<" in line)
        page.text(54, y, line, "F1" if mono else "F3", 10 if mono else 11)
        y -= 20
    writer.add_page(page.to_page(writer))
    out = io.BytesIO()
    writer.write(out)
    return out.getvalue()


def answers() -> dict:
    """The demo answers, cleaned as the portal cleans them, with every other
    required question answered the way most clients answer it."""
    bank = load_bank()
    questions = all_questions(bank)
    out = {}
    for qid, value in ANSWERS.items():
        cleaned, error = clean(questions[qid], value)
        assert not error, (qid, error)
        out[qid] = cleaned
    while missing := missing_required(bank, out):
        for qid in missing:
            kind = questions[qid]["type"]
            out[qid] = {"none": True} if kind == "checklist" else "No" if kind == "yes_no" else None
        if all(out.get(q) is None for q in missing):
            raise RuntimeError(f"demo answers missing: {missing}")
    return out


def reset(store: PortalStore, bundles: Path, client_id: str = DEMO_ID) -> None:
    shutil.rmtree(store.root / "clients" / client_id, ignore_errors=True)
    (store.root / "queue" / client_id).unlink(missing_ok=True)
    shutil.rmtree(bundles / client_id, ignore_errors=True)


def seed(store: PortalStore, bundles: Path, fresh: bool = False) -> None:
    reset(store, bundles)
    store.add_client(DEMO_ID, PROFILE["name"], phone=PROFILE["phone"], email=PROFILE["email"], language=PROFILE["language"],
                     consent=PROFILE["consent"])
    if fresh:
        return
    store.save_answers(DEMO_ID, answers())
    store.update_profile(DEMO_ID, status="started")
    for doc_id, (filename, lines) in DOCUMENTS.items():
        store.add_upload(DEMO_ID, doc_id, filename, document_pdf(lines), "application/pdf")
    from portal.engine import process_client

    print(process_client(store, DEMO_ID, out_root=bundles))


# -- the showcase client: the client's side in two minutes ------------------------------------------------------

SHOWCASE_ID = "demo-bia"
SHOWCASE_PROFILE = {"name": "Beatriz Exemplo Lima", "phone": "+1 555 010 0177", "email": "demo.bia@example.com", "language": "pt",
                    "consent": {"email": True, "sms": False, "whatsapp": False}}
# half the questionnaire: who she is and where she lives (the Social Security number, the jobs, the family and the rest are still to do)
SHOWCASE_ANSWERS = ["given_name", "family_name", "used_other_names", "name_changed", "dob", "other_dob", "sex", "birth_city", "birth_country", "citizenship",
                    "dual_citizenship", "a_number", "other_a_number", "has_ssn", "phone", "email", "home_address", "home_since", "mail_here"]
SHOWCASE_CHANGES = {"given_name": "Beatriz", "family_name": "Exemplo Lima", "a_number": "A099000456", "email": SHOWCASE_PROFILE["email"],
                    "phone": SHOWCASE_PROFILE["phone"], "birth_city": "Campinas"}
# her documents are Ana's with other names and numbers (one fake document, one fake person: nothing real)
SHOWCASE_SWAPS = [("ANA CLARA EXEMPLO SOUZA", "BEATRIZ EXEMPLO LIMA"), ("EXEMPLO SOUZA, ANA CLARA", "EXEMPLO LIMA, BEATRIZ"), ("EXEMPLO SOUZA", "EXEMPLO LIMA"),
                  ("A099 000 123", "A099 000 456"), ("IOE0999000123", "IOE0999000456"), ("SOROCABA", "CAMPINAS")]
SHOWCASE_MESSAGES = [  # (who, text): the client asks, the office answers, the client thanks the office and asks again
    ("client", "Bom dia! Posso mandar a foto do passaporte amanhã? Hoje estou sem o documento."),
    ("office", "Yes, that is fine. When you have the passport, send a new photo with the camera button on your page. Lay the whole passport flat in good light."),
    ("client", "Obrigada! Mando amanhã. Uma pergunta: preciso levar alguém comigo na coleta de digitais?"),
]
# what the machine draft of the office's answer would say (the installation's offline translator writes it for real; the demo needs none installed)
SHOWCASE_DRAFTS = {"pt": "Sim, tudo bem. Quando você tiver o passaporte, envie uma foto nova com o botão da câmera na sua página. Coloque o passaporte inteiro sobre uma mesa, com boa luz.",
                   "es": "Sí, está bien. Cuando tenga el pasaporte, envíe una foto nueva con el botón de la cámara en su página. Ponga el pasaporte completo sobre una mesa, con buena luz."}


def blurry_photo() -> bytes:
    """A made-up passport page photographed out of focus: what a client's shaky phone makes."""
    from PIL import Image, ImageDraw, ImageFilter

    image = Image.new("RGB", (900, 620), (236, 232, 222))
    draw = ImageDraw.Draw(image)
    draw.rectangle((30, 30, 870, 590), outline=(80, 90, 120), width=4)
    for n, line in enumerate(["REPUBLICA FEDERATIVA DO BRASIL", "PASSAPORTE", "EXEMPLO: DEMONSTRATION DOCUMENT", "BEATRIZ EXEMPLO LIMA", "P<BRAEXEMPLO<LIMA<<BEATRIZ<<<<<<<<<<<<<<<<<<<<"]):
        draw.text((60, 80 + 70 * n), line, fill=(30, 30, 40))
    out = io.BytesIO()
    image.filter(ImageFilter.GaussianBlur(7)).save(out, format="PNG")
    return out.getvalue()


def _demo_draft(text: str, lang: str) -> dict:
    return {"language": lang, "text": SHOWCASE_DRAFTS.get(lang), "machine": lang in SHOWCASE_DRAFTS, "needs_translator": lang not in SHOWCASE_DRAFTS}


def next_month_weekday(today: date, day: int = 12) -> date:
    """The 12th of next month, or the Monday after when that is a weekend: the made-up appointment."""
    year, month = (today.year + 1, 1) if today.month == 12 else (today.year, today.month + 1)
    when = date(year, month, day)
    return when + timedelta(days={5: 2, 6: 1}.get(when.weekday(), 0))


def showcase(store: PortalStore, bundles: Path, process: bool = True, today: date | None = None) -> dict:
    """Builds the showcase client from nothing. process=False skips the pipeline (no case page in the review app, which tests don't need)."""
    import journey
    from factgraph import FactGraph
    from portal import messages
    from portal.bank import bank_for, languages
    from portal.engine import client_tasks, document_records, stamp_retakes

    today = today or clock.today()
    reset(store, bundles, SHOWCASE_ID)
    store.add_client(SHOWCASE_ID, SHOWCASE_PROFILE["name"], phone=SHOWCASE_PROFILE["phone"], email=SHOWCASE_PROFILE["email"],
                     language=SHOWCASE_PROFILE["language"], consent=SHOWCASE_PROFILE["consent"])
    bank = bank_for(store.profile(SHOWCASE_ID))
    questions = all_questions(bank)
    mine = {}
    for qid in SHOWCASE_ANSWERS:
        cleaned, error = clean(questions[qid], (ANSWERS | SHOWCASE_CHANGES)[qid])
        assert not error, (qid, error)
        mine[qid] = cleaned
    mine["has_ssn"] = "Yes"  # asked next: the number itself is still to do
    store.save_answers(SHOWCASE_ID, mine)
    store.update_profile(SHOWCASE_ID, status="started")
    if process:  # her case in the review app: the birth certificate and the I-360 approval, read like any client's
        for doc_id in ("birth_certificate", "i360_approval"):
            filename, lines = DOCUMENTS[doc_id]
            for old, new in SHOWCASE_SWAPS:
                lines = [line.replace(old, new) for line in lines]
            store.add_upload(SHOWCASE_ID, doc_id, filename, document_pdf(lines), "application/pdf")
        from portal.engine import process_client

        process_client(store, SHOWCASE_ID, out_root=bundles)
    # the photo of her passport the reader found blurry (a record in the shape of documents.json, src/documents.py)
    photo = store.add_upload(SHOWCASE_ID, "passport", "passport-photo.jpg", blurry_photo(), "image/png")
    folder = bundles / SHOWCASE_ID
    folder.mkdir(parents=True, exist_ok=True)
    record = {"id": photo["sha256"][:16], "files": [photo["stored"]], "pages": [1], "type": "passport", "person": "applicant", "quality": "blurry", "confidence": 0.9}
    # beside the records her processed documents already have (the birth certificate, the I-360 approval): never in place of them
    path = folder / "documents.json"
    processed = [r for r in (json.loads(path.read_text(encoding="utf-8"))["documents"] if path.exists() else []) if r.get("id") != record["id"]]
    path.write_text(json.dumps({"version": 1, "built": clock.stamp(), "documents": processed + [record]}, indent=1), encoding="utf-8")
    uploads = store.uploads(SHOWCASE_ID)
    kept = [t for t in store.tasks(SHOWCASE_ID) if t["kind"] != "retake"]
    retakes = client_tasks(store.answers(SHOWCASE_ID), uploads, FactGraph(SHOWCASE_ID), "pt", None, bank, None, None, document_records(folder))
    tasks = kept + [t for t in retakes if t["kind"] == "retake"]
    stamp_retakes(store, SHOWCASE_ID, tasks)
    store.update_uploads(SHOWCASE_ID, uploads)
    store.save_tasks(SHOWCASE_ID, tasks)
    # the thread: the office's answer carries a draft in her language (a machine draft: the office's own words stay next to it)
    for who, text in SHOWCASE_MESSAGES:
        if who == "client":
            store.add_message(SHOWCASE_ID, "client", text, language="pt")
        else:
            messages.reply(store, SHOWCASE_ID, text, "Demo Paralegal", translate=_demo_draft)
    # "your case" with a biometrics appointment next month, and its what-to-bring page
    when = next_month_weekday(today)
    stage = "i485_ready"
    ids = journey.settings()["stages"]["sij"]
    j = {"stage": stage, "stage_index": ids.index(stage), "today": today.isoformat(), "stages": [{"id": x} for x in ids], "filings": [],
         "notices": [{"kind": "biometrics", "appointment": f"{when.isoformat()} 09:30 AM", "date": today.isoformat(), "form": "I-765",
                      "where": "USCIS Application Support Center (a made-up place), 1 Example Street, Springfield, MA 01103"}]}
    store.save_journey(SHOWCASE_ID, {lang: journey.client_view(j, lang) for lang in languages()})
    return {"client": SHOWCASE_ID, "appointment": when.isoformat(), "tasks": len(tasks), "messages": len(store.messages(SHOWCASE_ID))}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    parser.add_argument("--fresh", action="store_true", help="only invite the demo client (to fill the portal live)")
    parser.add_argument("--reset", action="store_true", help="remove the demo client")
    args = parser.parse_args(argv)
    store = PortalStore(os.environ.get("PORTAL_DATA", REPO / "data" / "portal"))
    bundles = REPO / "data" / "clients"
    if args.reset:
        reset(store, bundles)
        print("demo client removed")
        return 0
    seed(store, bundles, fresh=args.fresh)
    base = os.environ.get("PORTAL_BASE_URL", "http://localhost:8600").rstrip("/")
    print(f"Client portal sign-in link (works once): {base}/l/{store.new_link_token(DEMO_ID)}")
    if not args.fresh:
        print(f"Paralegal review: http://127.0.0.1:8485/#{DEMO_ID}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
