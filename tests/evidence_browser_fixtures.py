"""Retained fictional inputs shared by before/after UI captures.

The local text is a declared reader fixture, not an OCR quality measurement.
Physical PDF bytes pass through the real boundary/subject/proof paths.
"""
from hashlib import sha256
from io import BytesIO

import documents
import subject_attribution as subjects
from batch import process_documents, record_documents
from document_instances import context
from portal.demo import document_pdf
from review.state import save_bundle
from test_nta_full import NTA
from test_review_evidence_routes import TEXT

NOTICE = NTA.replace("Ana Clara Exemplo Souza", "Alpha Sample").replace("03/03/2008", "01/02/2000")
PORTUGUESE = "REPUBLICA FEDERATIVA DO BRASIL\nPASSAPORTE PASSPORT\nSOBRENOME / SURNAME: SAMPLE\nNOME / GIVEN NAMES: ALPHA\nNACIONALIDADE / NATIONALITY: BRASILEIRO(A)\nEXEMPLO: DOCUMENTO FICTICIO"
TRANSLATION = "Fictional stored English translation: Federative Republic of Brazil; passport; surname SAMPLE; given names ALPHA; nationality Brazilian."


def combined_pdf(rotated=False):
    from pypdf import PdfReader, PdfWriter
    writer = PdfWriter()
    if rotated:
        from PIL import Image, ImageDraw
        from reportlab.pdfgen import canvas
        from reportlab.lib.utils import ImageReader
        image = Image.new("RGB", (612, 792), "white")
        draw = ImageDraw.Draw(image)
        for index, line in enumerate(TEXT.splitlines()):
            draw.text((30, 30 + index * 24), line, fill="black")
        out = BytesIO()
        pdf = canvas.Canvas(out, pagesize=(612, 792), invariant=1)
        pdf.drawImage(ImageReader(image.rotate(90, expand=True)), 0, 0, width=612, height=792)
        pdf.showPage(); pdf.save()
        writer.add_page(PdfReader(BytesIO(out.getvalue())).pages[0])
    else:
        writer.add_page(PdfReader(BytesIO(document_pdf(TEXT.splitlines()))).pages[0])
    writer.add_page(PdfReader(BytesIO(document_pdf(NOTICE.splitlines()))).pages[0])
    writer.add_metadata({"/Producer": "Fictional EV5 browser fixture"})
    out = BytesIO(); writer.write(out)
    return out.getvalue()


def retain_case(world, app, tmp_path, *, rotated=False, translated=False, legacy=False):
    client = "case-ana"
    folder = tmp_path / "retained-browser-source"
    folder.mkdir()
    pdf = combined_pdf(rotated)
    (folder / "combined.pdf").write_bytes(pdf)
    texts = [("combined.pdf", TEXT + "\n" + NOTICE)]
    pages = {"combined.pdf": [TEXT, NOTICE]}
    if translated:
        (folder / "portuguese.pdf").write_bytes(document_pdf(PORTUGUESE.splitlines()))
        texts.append(("portuguese.pdf", PORTUGUESE))
        pages["portuguese.pdf"] = [PORTUGUESE]
    supplied = context(folder, None, [name for name, _ in texts])
    result = process_documents(client, texts, pages=pages, boundary_context=supplied)
    record_documents(result, folder, texts)
    case = world / client
    save_bundle(result, case, folder)
    people = documents.read(case)["case_subjects"]["people"]
    person = next(p["id"] for p in people if p["case_role"] == "applicant")
    for row in subjects.views(case):
        # These printed documents identify one person; NTA uses respondent,
        # while passports and I-94 use holder. Do not infer multi-party roles.
        expected = {"respondent"} if row["type"] == "notice_to_appear" else {"holder"}
        assert set(row["slots"]) == expected, row
        subjects.assign(case, row["instance_id"], row["fingerprint"], {slot: person for slot in expected}, "Fictional Setup Reviewer", "paralegal")
    if translated:
        row = next(d for d in documents.read(case)["documents"] if "portuguese.pdf" in d["files"])
        documents.set_translated(case, row["id"], TRANSLATION, "Fictional Setup Reviewer")
    if legacy:
        import json
        data = documents.read(case)
        data["boundary_plans"] = {}
        (case / "documents.json").write_text(json.dumps(data), encoding="utf-8")
    app.roster.touch(client)
    return {"client": client, "case": case, "source": folder, "sha256": sha256(pdf).hexdigest(),
            "rotated": rotated, "reader_text": "declared synthetic local text; no OCR/model execution"}
