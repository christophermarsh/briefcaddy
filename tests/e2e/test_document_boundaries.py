"""A paralegal reviews an uncertain split using retained fictional pages."""
import io
import json
from pathlib import Path


def test_staff_upload_boundary_hold_confirm_and_undo(world, paralegal, tmp_path):
    from playwright.sync_api import expect
    from portal.demo import document_pdf
    from pypdf import PdfReader, PdfWriter

    fixture = json.loads((Path(__file__).parents[1] / "fixtures" / "document_instances.json").read_text())
    pages = [fixture["same_type_i94"][0], "Unidentified next page\nBirth Date: 05/06/2001"]
    writer = PdfWriter()
    for page in pages:
        writer.add_page(PdfReader(io.BytesIO(document_pdf(page.splitlines()))).pages[0])
    path = tmp_path / "boundary exercise.pdf"
    with path.open("wb") as output:
        writer.write(output)
    screen = paralegal
    screen.open("demo-ana", "documents")
    screen.page.locator("#dropzone input[type=file]").set_input_files(str(path))
    expect(screen.page.locator("#toast")).to_contain_text("Added 1 document", timeout=120000)
    assert "Added 1 document" in screen.toast()
    section = screen.page.locator("section.group.pad").filter(has=screen.page.locator("h3")).filter(
        has=screen.page.locator("a[href*='boundary_exercise']"))
    expect(section).to_have_count(1, timeout=120000)
    # The durable processing marker can appear before the worker finishes.
    # Wait for the actual unresolved-layout control, not that transient state.
    expect(section.get_by_label("Document start pages", exact=False)).to_be_visible(timeout=120000)
    expect(section).to_contain_text("Uncertain pages are held", timeout=120000)
    screen.check("boundaries-held")
    plan = screen.page.request.get(world["review"] + "api/packet?client=demo-ana").json()
    assert not plan["ready"] and any("document boundaries" in message for message in plan["problems"])
    section.get_by_label("Document start pages", exact=False).fill("1, 2")
    section.get_by_role("button", name="Confirm these boundaries").click()
    assert "Saved." in screen.toast()
    assert "Reviewed by Paulo Paralegal" in screen.when_it_says("Reviewed by Paulo Paralegal")
    screen.check("boundaries-confirmed")
    data = json.loads((world["clients"] / "demo-ana" / "documents.json").read_text())
    decision = next(d for d in data["boundary_decisions"].values() if d["who"] == "Paulo Paralegal")
    assert decision["starts_zero_based"] == [0, 1] and decision["role"] == "paralegal" and decision["at"]
    section.get_by_role("button", name="Undo boundary review").click()
    assert "Saved." in screen.toast()
    assert "Uncertain pages are held" in screen.when_it_says("Uncertain pages are held")
    screen.check("boundaries-undone")
