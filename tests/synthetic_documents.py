"""Retained fictional PDFs for tests exercising persisted case bundles.

Pure reader tests may use strings. A saved case must also retain the source
bytes and boundary records its graph points to; this helper uses the real
production evidence path without replacing guards or manufacturing approvals.
"""
from io import BytesIO

from pypdf import PdfReader, PdfWriter

from batch import process_documents, record_documents
from classify import classify_text
from document_instances import context, prepare
from portal.demo import document_pdf


def retain_documents(folder, docs, pages=None):
    folder.mkdir(parents=True, exist_ok=True)
    for name, text in docs:
        writer = PdfWriter()
        for page in (pages or {}).get(name, [text]):
            writer.add_page(PdfReader(BytesIO(document_pdf(page.splitlines()))).pages[0])
        with (folder / name).open("wb") as stream:
            writer.write(stream)
    return context(folder, None, [name for name, _ in docs])


def process_retained_documents(client, folder, docs, *, pages=None, manual_evidence=None, **kwargs):
    """Read normal fixtures; retain separately supplied manual graph evidence.

    manual_evidence represents facts deliberately added by the test itself.
    It establishes the physical source and boundary identity only, preserving
    those tests' existing values, source aliases, tiers and legal assertions.
    """
    ctx = retain_documents(folder, docs, pages)
    result = process_documents(client, docs, pages=pages, boundary_context=ctx, **kwargs)
    if manual_evidence:
        extra_ctx = retain_documents(folder, manual_evidence)
        normalized, _, plans, _ = prepare(manual_evidence, None, extra_ctx)
        result.boundary_plans.update(plans)
        result.document_texts.extend(normalized)
        result.classifications.update({name: classify_text(text) for name, text in normalized})
    record_documents(result, folder, docs)
    return result
