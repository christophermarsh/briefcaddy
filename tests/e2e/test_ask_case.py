"""Ask about this case, in the browser (src/case_questions.py): off by default (the box says so), the attorney reads the practice in
full in Settings, Drafting and models, approves it and switches questions on; a question gets an answer with its sources, a question
the record cannot answer gets "The case's record does not say.", and the summary for the attorney shows on screen and downloads as a
DRAFT PDF.

The model is faked at the server: a second review app over the world's cases is started with OLLAMA_URL pointed at a small
stand-in for Ollama in this test (it answers every prompt with its first passage, cited), and with its own settings and approvals so
the switch starts off. Nothing here reaches a model. The case is the made-up demo client, cloned.
"""

from __future__ import annotations

import io
import json
import subprocess
import sys
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from pypdf import PdfReader

from conftest import REPO, Screen, _port, _wait


def _fake_ollama():
    """Ollama's /api/generate as the review app calls it: each prompt is answered with its first passage, cited [1]."""
    prompts: list[str] = []

    class Ollama(BaseHTTPRequestHandler):
        def log_message(self, *a):
            pass

        def do_POST(self):
            body = json.loads(self.rfile.read(int(self.headers.get("Content-Length") or 0)) or b"{}")
            prompt = body.get("prompt") or ""
            prompts.append(prompt)
            first = next((line.split("] ", 1)[1] for line in prompt.splitlines() if line.startswith("[1] ")), None)
            data = json.dumps({"model": body.get("model"), "response": f"{first} [1]" if first else "NOT IN THE RECORD", "done": True}).encode()
            self.send_response(200)
            self.send_header("Content-Type", "application/json")
            self.send_header("Content-Length", str(len(data)))
            self.end_headers()
            self.wfile.write(data)

    port = _port()
    httpd = ThreadingHTTPServer(("127.0.0.1", port), Ollama)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    return httpd, f"http://127.0.0.1:{port}", prompts


def test_ask_about_this_case_the_switch_an_answer_the_refusal_and_the_summary(world, browser, tmp_path):
    import world as w

    import case_questions as cq

    w.clone(world["root"], "demo-ana", "case-ask")
    fake, fake_url, prompts = _fake_ollama()
    port = _port()
    base = f"http://127.0.0.1:{port}/"
    env = world["env"] | {"OLLAMA_URL": fake_url, "I485_SETTINGS": str(tmp_path / "settings.json"), "I485_RULES_APPROVED": str(tmp_path / "rules_approved.json")}
    # the attorney has seen Getting started already (it is kept beside the settings: src/getting_started.py)
    seen = Path(world["env"]["I485_SETTINGS"]).parent / "getting_started.json"
    (tmp_path / "getting_started.json").write_bytes(seen.read_bytes())
    log = open(tmp_path / "review.log", "w")
    review = subprocess.Popen([sys.executable, "src/review/server.py", "--data", str(world["clients"]), "--port", str(port),
                               "--users", str(world["users"]), "--portal", str(world["portal"])], cwd=REPO, env=env, stdout=log, stderr=subprocess.STDOUT)
    s = None
    try:
        _wait(base)
        world.setdefault("last_steps", {})
        world.setdefault("devices", {})
        s = Screen(browser, world | {"review": base}, w.ATTORNEY)
        page = s.page

        # off by default: the box says so, and offers no question
        s.open("case-ask", "journey")
        box = page.locator("#ask-case")
        page.wait_for_selector("#ask-off")
        assert "Questions about a case are off. An attorney can switch them on in Settings (Drafting and models)." in box.inner_text()
        assert box.get_by_role("button", name="Ask").count() == 0
        s.check("ask-off")

        # Settings, Drafting and models: the switch (off), the practice in full, not approved yet
        page.goto("about:blank")
        page.goto(base + "#settings:drafting")
        page.wait_for_selector("#set-drafting", state="visible")
        sec = page.locator("#set-drafting")
        text = sec.inner_text()
        assert "Drafting and models" in text and "Questions about a case answered by the local model" in text
        assert "answers are built from the case's own record, and every sentence shows where it came from" in text
        switch = sec.locator("label.setfield", has_text="Questions about a case answered by the local model").locator("select")
        assert switch.input_value() == "off"
        practice = page.locator("#practice-case-questions")
        assert "Not yet approved" in practice.inner_text() and cq.PRACTICE in practice.inner_text()
        s.check("ask-settings")
        practice.get_by_role("button", name="Approve this practice").click()
        assert s.toast() == "Approved."
        page.wait_for_selector("#set-drafting", state="visible")
        assert "Approved by Ana Attorney" in page.locator("#practice-case-questions").inner_text()
        sec = page.locator("#set-drafting")
        sec.locator("label.setfield", has_text="Questions about a case answered by the local model").locator("select").select_option("on")
        sec.get_by_role("button", name="Save").click()
        assert s.toast() == "Saved."

        # a question: an answer with its sources, in words (no file name)
        s.open("case-ask", "journey")
        question = page.get_by_role("textbox", name="Your question about this case")
        question.fill("When did she enter?")
        page.locator("#ask-case").get_by_role("button", name="Ask").click()
        page.wait_for_selector("#ask-answer .ask-sentences li")
        answer = page.locator("#ask-answer").inner_text()
        assert "07/15/2019" in answer and "From: " in answer and ".pdf" not in answer and "applicant." not in answer
        assert prompts and prompts[-1].startswith(cq.PRACTICE) and "Question: When did she enter?" in prompts[-1]
        s.check("ask-answer")

        # nothing in the record answers: the refusal, and nothing else (the model is not asked)
        asked = len(prompts)
        question.fill("Does she have a tattoo?")
        page.locator("#ask-case").get_by_role("button", name="Ask").click()
        page.wait_for_selector("#ask-answer .ask-refusal")
        assert page.locator("#ask-answer").inner_text().strip() == "The case's record does not say." and len(prompts) == asked

        # the summary for the attorney: on screen, DRAFT, each sentence with its source; downloaded as a PDF
        page.locator("#ask-case").get_by_role("button", name="Summary for the attorney").click()
        page.wait_for_selector("#case-summary")
        summary = page.locator("#case-summary").inner_text()
        assert "DRAFT" in summary and "Who the client is" in summary and "Track and stage" in summary and "From: " in summary
        assert "Special Immigrant Juvenile" in summary
        with page.expect_download() as download:
            page.locator("#summary-pdf").click()
        pdf = open(download.value.path(), "rb").read()
        assert pdf.startswith(b"%PDF")
        text = " ".join(p.extract_text() or "" for p in PdfReader(io.BytesIO(pdf)).pages)
        assert "Summary for the attorney (DRAFT)" in text and "From:" in text and "not for filing" in text
        s.check("ask-summary")
        # the earlier questions are listed on the case, with who asked
        s.open("case-ask", "journey")
        page.wait_for_selector("#ask-case .ask-history")
        assert "Earlier questions on this case (3)" in page.locator("#ask-case").inner_text()
        assert not s.errors, s.errors
    finally:
        if s:
            s.close()
        review.terminate()
        log.close()
        fake.shutdown()
    logged = (tmp_path / "review.log").read_text(errors="replace")
    assert "Traceback" not in logged, logged[-3000:]
