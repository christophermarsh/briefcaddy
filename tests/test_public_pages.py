"""The public pages (docs/public/, made by tools/public_pages.py from the code and the registers): each is what the tip writes, every number is the function's, every
line of the security page is a line of the product's own papers, nothing is claimed that the code cannot show, and the sample carries only a made-up case.
Everyone here is made up (the Exemplo family)."""

from __future__ import annotations

import inspect
import json
import re
import shutil
import sys
import tempfile
from pathlib import Path

import pytest
from pypdf import PdfReader
import schema_path

REPO = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(REPO / "tools"))

import export_firm  # noqa: E402
import public_pages  # noqa: E402

PUBLIC = REPO / "docs" / "public"
TEXT_PAGES = ["README.md", "what_it_does.md", "security.md", "accuracy.md", "data_statement.md", "price.md", "sample/README.md"]
# the pages that say what the product is: no file name, key or code on them (the data statement is the statement itself, and README.md the owner's index)
PLAIN_PAGES = ["what_it_does.md", "security.md", "accuracy.md", "sample/README.md"]


def page(name: str) -> str:
    return (PUBLIC / name).read_text(encoding="utf-8").replace("\r\n", "\n")


def _json(rel: str | Path):
    return json.loads((REPO / rel).read_text(encoding="utf-8"))


def _number(text: str, pattern: str) -> int:
    m = re.search(pattern, text)
    assert m, pattern
    return int(m.group(1).replace(",", ""))


# -- every page is what the tip writes --------------------------------------------------------------------------------------------------


def test_every_page_regenerates_byte_for_byte_from_the_tip():
    assert public_pages.check() == []
    assert public_pages.main(["--check"]) == 0
    for name, text in public_pages.render_all().items():
        assert page(name) == text, f"docs/public/{name} is out of date: run python tools/public_pages.py"


def test_the_check_names_a_page_that_has_gone_stale(tmp_path):
    shutil.copytree(PUBLIC, tmp_path / "public")
    stale = tmp_path / "public" / "what_it_does.md"
    stale.write_text(stale.read_text(encoding="utf-8") + "A hand edit.\n", encoding="utf-8")
    (tmp_path / "public" / "security.md").unlink()
    problems = public_pages.check(tmp_path / "public")
    assert "docs/public/what_it_does.md is out of date" in problems and "docs/public/security.md is missing" in problems
    assert not any("price" in p or "data_statement" in p for p in problems)


def test_a_page_stamped_for_another_release_is_stale(monkeypatch):
    import version

    monkeypatch.setattr(version, "VERSION", "2099.1.1")
    problems = public_pages.check()
    assert any("what_it_does.md is out of date" in p for p in problems)  # every page carries the version
    assert any("accuracy.md was written for version" in p for p in problems)  # the accuracy tool's page too
    assert not any("built with version" in p for p in problems)  # the sample PDFs say the day they were built, not a version: a release does not make them stale
    assert "Software version 2099.1.1" in public_pages.stamp()
    assert public_pages.accuracy_problem(PUBLIC / "accuracy.md")


def test_every_page_is_stamped_with_the_version_and_the_release_date_except_the_price_page():
    import clock
    import version

    stamp = f"Software version {version.VERSION}, released {clock.us_date(version.RELEASED)}."
    for name in ("README.md", "what_it_does.md", "security.md", "data_statement.md", "sample/README.md"):
        assert stamp in page(name), name
    assert re.search(rf"software version {re.escape(version.VERSION)}\.", page("accuracy.md"))  # the accuracy tool's own stamp, kept to this release


def test_the_accuracy_page_is_the_accuracy_tools_and_this_tool_does_not_rewrite_it(tmp_path):
    assert "accuracy.md" not in public_pages.render_all()
    out = tmp_path / "out"
    assert public_pages.main(["--no-sample", "--out", str(out)]) == 0
    assert not (out / "accuracy.md").exists()
    assert (out / "sample" / public_pages.SAMPLE_PACKET).read_bytes() == (PUBLIC / "sample" / public_pages.SAMPLE_PACKET).read_bytes()


# -- what_it_does: every number is the function's ------------------------------------------------------------------------------------


def test_the_filings_on_the_page_are_the_packet_modules_filings():
    import journey
    import packet

    text = page("what_it_does.md")
    assert _number(text, r"prepares ([\d,]+) kinds? of filing") == len(packet.FILINGS)
    titles = [packet.filing_title(f) for f in packet.FILINGS]
    for fid in packet.FILINGS:
        assert f"| {public_pages.tidy(packet.filing_title(fid))} |" in text, fid  # one row for each filing, in the table of what each packet checks
    assert len(re.findall(r"(?m)^\| .+ \| .+ \| .+ \| .+ \|$", text)) - 1 == len(packet.FILINGS)  # the header row aside
    assert len(set(titles)) == len(titles)
    # the case paths: the filings each leads to are the ones in schemas/registers/journey.json next_filings for its stages
    cfg = _json(schema_path.path("register", "journey"))
    listed = set()
    for track, stages in cfg["stages"].items():
        ids = list(dict.fromkeys(item[0] for stage in stages for item in cfg["next_filings"].get(stage, [])))
        listed |= set(ids)
        if ids:
            block = text.split(f"**{journey._TRACK_WORDS[track]}**")[1].split("\n\n")[1]
            assert block.splitlines() == [f"- {public_pages.tidy(packet.filing_title(i))}" for i in ids], track
    assert _number(text, r"([\d,]+) more filings? (?:is|are) not in any case path's list of next filings") == len(set(packet.FILINGS) - listed)


def test_the_declaration_filings_are_the_drafting_modules():
    import drafting

    text = page("what_it_does.md")
    assert _number(text, r"For ([\d,]+) filings? the product also assembles") == len(drafting.FILINGS)
    assert "Nothing else is added." in text and "Only an attorney marks a declaration as the client's final" in text
    assert "Only an attorney marks a declaration as the client's final" in drafting.PRACTICE and "Nothing else is added." in drafting.PRACTICE


def test_what_a_packet_checks_is_what_the_packet_says_when_it_is_not_ready():
    import packet

    text = page("what_it_does.md")
    with tempfile.TemporaryDirectory() as tmp:
        problems = packet.plan(Path(tmp), {"blocking": 1}, packet.load_filing("i485"))["problems"]
    said = re.findall(r'(?m)^- "(.+)"$', text)
    assert len(said) == 3 and all(s in problems for s in said)
    # the papers a filing is held back for are the exhibits its schema marks required
    for fid in ("i485", "i601a", "tps", "n336"):
        schema = packet.load_filing(fid)
        row = next(r for r in text.splitlines() if r.startswith(f"| {public_pages.tidy(packet.filing_title(fid))} |"))
        for ex in schema["exhibits"]:
            if ex.get("required"):
                assert public_pages.drop_citations(ex["title"]) in row, (fid, ex["title"])
    assert "| EOIR-28 (appearance in immigration court) | EOIR-28 | No paper listed | the attorney |" in text


def test_the_documents_read_are_the_classifiers_kinds():
    types = _json(schema_path.path("register", "document_types"))["types"]
    text = page("what_it_does.md")
    by = {k: [t["name"] for t in types if t["recognized"] == k] for k in ("text", "form_footer", "notice_case_type", "not_yet")}
    read = by["text"] + by["form_footer"] + by["notice_case_type"]
    assert _number(text, r"recognises ([\d,]+) kinds? of document by itself") == len(read)
    assert _number(text, r"knows ([\d,]+) more kinds? by name") == len(by["not_yet"])
    assert len(read) + len(by["not_yet"]) < len(types)  # the print header and the fallback are neither
    for name in read + by["not_yet"]:
        assert name in text, name
    assert "Not recognized yet" not in text  # the fallback is not a kind the product reads
    assert "send nothing out" in text and "Text recognition (OCR)" in text


def test_the_portal_languages_are_the_portals():
    from portal import bank

    text = page("what_it_does.md")
    names = [bank.language_names()[c] for c in bank.languages()]
    assert f"The client portal is in {', '.join(names[:-1])} and {names[-1]}." in text
    assert "The Haitian Creole is a machine draft" in text and _json(schema_path.path("question", "intake"))["translation_status"]["ht"].startswith("MACHINE DRAFT")


def test_the_registers_counts_are_the_registers(tmp_path, monkeypatch):
    import maintenance

    items = _json(schema_path.path("register", "maintenance"))["items"]
    text = page("what_it_does.md")
    assert _number(text, r"It holds ([\d,]+) items") == len(items)
    for cadence in ("monthly", "quarterly", "yearly"):
        assert _number(text, rf"([\d,]+) items? {cadence}") == sum(1 for i in items if i["cadence"] == cadence)
    for party, words in (("provider", "The provider keeps"), ("firm", "the firm keeps"), ("host", "whoever runs the server keeps")):
        assert _number(text, rf"{words} ([\d,]+) items?") == sum(1 for i in items if i.get("party", "provider") == party)

    # the nightly look-ups: the items the overnight run asks about (asked here with a source that always fails: every item it handles is answered)
    def down(url, binary=False, timeout=60):
        raise OSError("no network in a test")

    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    asked = maintenance.live_checks(get=down)["results"]
    assert _number(text, r"looks up ([\d,]+) of them") == len(asked) == sum(int(n) for n in re.findall(r"\*\*[^*]+\*\* \((\d+)\):", text))
    live_names = "; ".join(public_pages.item_name(i) for i in items if i["id"] in asked)
    for name in live_names.split("; "):
        assert name in text, name


def test_what_it_does_not_do_is_the_design_plans_and_the_connectors_status():
    text = page("what_it_does.md")
    plan = (REPO / "docs" / "design_plan.md").read_text(encoding="utf-8")
    body = " ".join(plan.split("## What I'd say no to")[1].split())
    topics = [s.split(": ")[0] for s in re.split(r"(?<=\.)\s+(?=[A-Z])", body) if ": " in s]
    assert len(topics) >= 4 and "Cloud AI for reading client files" in topics
    for topic in topics:
        assert f"- {topic}\n" in text, topic
    cfg = _json(schema_path.path("register", "connectors"))
    assert cfg["active"] == {"documents": "local", "results": "none", "sign_in": "local"} and "NOT switched on" in cfg["_status"]
    assert "none is switched on" in text and "Google Drive" in text and "Microsoft 365" in text


def test_a_connector_that_is_switched_on_stops_the_page(monkeypatch):
    real = public_pages._json

    def switched(rel):
        data = real(rel)
        if rel == schema_path.path("register", "connectors"):
            data["active"]["documents"] = "google_drive"
        return data

    monkeypatch.setattr(public_pages, "_json", switched)
    with pytest.raises(public_pages.PageError, match="switched on"):
        public_pages.not_switched_on()


# -- the security page ------------------------------------------------------------------------------------------------------------------


def _section(text: str, heading: str) -> str:
    return re.search(rf"(?ms)^## {re.escape(heading)}\n(.*?)(?=^## |\Z)", text).group(1)


def _statement() -> str:
    return (REPO / "docs" / "security" / "product_data_statement.md").read_text(encoding="utf-8").replace("\r\n", "\n")


def _bold(text: str) -> list[str]:
    return [re.sub(r"\s+", " ", b).strip() for b in re.findall(r"\*\*(.+?)\*\*", text, re.S)]


def test_every_true_today_line_is_a_line_of_the_data_statement():
    bold = _bold(_statement())
    lines = [ln[2:] for ln in _section(page("security.md"), "What the product does").splitlines() if ln.startswith("- ")]
    assert len(lines) >= 17
    heads = set()
    for line in lines:
        head = next((b for b in bold if line.startswith(b.rstrip(":."))), None)
        assert head, f"no line of the statement behind: {line}"
        heads.add(head)
    for fact in ("Client documents and answers stay in the firm's own installation.", "Nothing is sent to an outside AI service.",
                 "Viewing is logged, not only deciding.", "Every change is recorded in a ledger the firm owns.",
                 "The firm's data is plain files it can copy out at any time."):
        assert fact in heads
    assert any(ln.startswith("A second factor is built in and on by default for attorneys") for ln in lines)
    assert any("Each firm has its own installation" in ln for ln in lines)  # one installation per firm
    # the caveat that comes straight after a bold claim comes with it, and so does the exception the statement makes in another place
    messages = next(ln for ln in lines if ln.startswith("Messages to clients carry no personal data"))
    assert "does show that the firm has a case" in messages
    assert "the day, the time and the place of the appointment" in messages and "the one message the product sends that holds more than the firm's name and a link" in messages
    # the query layer's line says what it holds, and the people index is named with what it holds
    query = next(ln for ln in lines if ln.startswith("There is one more copy of the case records"))
    assert "dates of birth, A-Numbers, passport numbers" in query and "the people index" in query and "no fact's value" in query


def test_every_section_of_the_statement_is_on_the_security_page_or_left_out_for_a_reason():
    statement = _statement()
    headings = re.findall(r"(?m)^## (.+)$", statement)
    on_page = re.findall(r"(?m)^## (.+)$", page("security.md"))
    for heading in headings:
        assert heading in on_page or heading in public_pages.STATEMENT_LEFT_OUT, heading
    assert set(public_pages.STATEMENT_LEFT_OUT) <= set(headings) and all(public_pages.STATEMENT_LEFT_OUT.values())
    assert set(public_pages.STATEMENT_LEFT_OUT) == {"Open decisions", "Sources"}  # nothing about what the product does is left out
    text = page("security.md")
    assert "Prospects: a person who is not a client" in on_page
    assert "The conflict search does not yet look at prospects." in text and "none is built in" in text
    assert "the client's name" in text and "calendar program" in text and "that copy is outside the product" in text  # the calendar feed: what leaves, and the copy outside
    assert "the day, the time and the place of the appointment" in text and "held in two places" in text


def test_the_page_says_which_dates_the_statement_gives_for_which_lines():
    statement = _statement()
    para = statement.split("Statement date:")[1].split("\n\n")[0]
    assert "October 2, 2026" in para and "item 3" in para and "item 15" in para  # what the page's date is made from
    text = page("security.md")
    assert "dated 10/02/2026, with items 3 and 15 checked again 10/03/2026" in text
    lines = [ln for ln in _section(text, "What the product does").splitlines() if ln.startswith("- ")]
    again = [ln for ln in lines if "(checked again 10/03/2026)" in ln]
    assert len(again) == 2 and again[0].startswith("- A second factor") and again[1].startswith("- The agreement, its signatures")
    assert "checked again" not in next(ln for ln in lines if ln.startswith("- Staff sign in with their own account."))
    assert "list of outside services" in text.split("## What the product does")[0]  # the backup line and the services come from that list, not the statement


def test_every_not_yet_line_is_a_sentence_of_the_statement_or_a_row_of_the_owners_table():
    readme = (REPO / "docs" / "security" / "README.md").read_text(encoding="utf-8")
    owner_rows = set(re.findall(r"(?m)^\| \*\*([^*|`]+?)\*\*", readme.split("## What is still the owner's")[1].split("###")[0]))
    not_yet = re.sub(r"\s+", " ", _statement().split("## What is not there yet")[1].split("## What the firm can ask for")[0])
    block = _section(page("security.md"), "What is not there yet")
    flat = re.sub(r"\s+", " ", block)
    # Built operational tools remain distinct from unaccepted contractual terms.
    for needle in ("Export, attorney-gated case purge and installation exit tools are built", "A data agreement, incident commitments and contractual delivery, deletion and backup-expiry terms have not been accepted by both parties",
                   "Purge retains the limited records described in item 15", "older backups and exports, external systems and physical media disposal still require the firm's separate retention and deletion decisions",
                   "paralegals with a password only unless an attorney requires the code for everyone",
                   "run the review app on a network that is not open to the internet", "Hardware security keys and passkeys are not supported",
                   "We do not have a SOC 2 report or an independent penetration test", "The hosted service is not live"):
        assert needle in flat, needle
        assert needle in not_yet or needle in re.sub(r"\s+", " ", readme), needle
    owners = block.split("What is still the owner's:")[1].split("This page gives no date")[0]
    names = [ln[2:] for ln in owners.splitlines() if ln.startswith("- ")]
    assert {"The data processing agreement", "A hosted service", "A SOC 2 report", "A penetration test", "The on-premises license"} <= set(names)
    for name in names:
        assert name.split(":")[0] in owner_rows, name
    cyber = next(n for n in names if n.startswith("Cyber insurance"))
    assert cyber == "Cyber insurance: the security pack says nothing about it."  # the pack's own row says "stated nowhere in this pack": no absence is asserted
    assert "stated nowhere in this pack" in readme
    assert "gives no date for any of them" in block and "The provider will say when" in block
    assert not re.search(r"\d{2}/\d{2}/\d{4}|\b20\d\d\b|next year|next month|by the end of", block)  # the owner writes dates, not the product


def test_what_leaves_the_installation_is_the_list_of_outside_services_with_its_caveats_and_without_a_competitors_name():
    sub = (REPO / "docs" / "security" / "subprocessors.md").read_text(encoding="utf-8")
    text = page("security.md")
    block = _section(text, "What leaves the installation, and to whom")
    rows = [r for r in block.splitlines() if r.startswith("| ") and not r.startswith("| Service") and not r.startswith("|---")]
    used = re.findall(r"(?m)^\| \*\*(.+?)\*\*", sub.split("## The list")[1].split("## Written, not connected")[0])
    assert len(rows) == len(used)
    for row in rows:
        assert row.endswith("|") and len(row.split("|")) == 4
    hosted = [r for r in rows if r.startswith("| Hosting company") or r.startswith("| Backup storage")]
    assert len(hosted) == 2 and all("not used in an installation on the firm's own machine" in r for r in hosted)
    assert "The document reader and the translator run on the firm's own installation and send nothing out." in block
    assert "The backup tool is built: one dated zip of the data, encrypted with a passphrase, with a test restore." in block
    assert "certificate is not checked" not in block  # the product checks the mail server's certificate and name (src/portal/notify.py): no such caveat any more
    for caveat in ("The address is a setting.", "the images would go there",
                   "The provider did not read the source of the third-party libraries", "did not watch the network while the product ran",
                   "including the working sign-in link"):
        assert caveat in block, caveat
    for in_the_list in ("the mail server's certificate and name are checked", "the address is a setting", "the\n  images would go there", "I did **not** read the source of the third-party",
                        "I did not\nwatch the network", "including the working sign-in link"):
        assert in_the_list in sub, in_the_list  # each caveat on the page is a sentence of the list of outside services
    for competitor in ("Clio", "Filevine"):
        assert competitor not in text
    assert "These services can touch client data" not in text and "The firm decides whether each is used" not in text  # typed claims the list does not make


def test_no_page_names_a_competitors_breach():
    for name in TEXT_PAGES:
        low = page(name).lower()
        assert "breach" not in low or name == "data_statement.md", name  # the statement's own papers speak of incidents; no other page names a breach
    assert "docketwise" not in page("security.md").lower()


def test_no_security_line_has_a_code_name_or_a_dangling_sentence():
    text = page("security.md")
    assert "`" not in text and "a file lists" not in text.lower()
    for line in text.splitlines():
        assert not re.search(r"\(\s*\)|\s[,;.]\s|\bto \(", line), line


# -- the data statement, the price ----------------------------------------------------------------------------------------------


def test_the_data_statement_is_the_packs_statement_with_the_providers_line():
    statement = (REPO / "docs" / "security" / "product_data_statement.md").read_text(encoding="utf-8").replace("\r\n", "\n")
    filled = export_firm.fill_statement(statement, public_pages.PROVIDER_PLACEHOLDERS)
    text = page("data_statement.md")
    assert "Provider: [the provider's name]. Security contact: [the provider's security contact]." in text
    head, blank, stamped, rest = text.split("\n", 3)  # the statement's own title, the stamp, then the statement as the pack's export writes it
    assert (head, blank, stamped, rest) == (filled.split("\n", 1)[0], "", public_pages.stamp(), filled.split("\n", 1)[1])
    assert "[decided by the provider" not in text
    named = public_pages.render_data_statement({"name": "Exemplo Software LLC", "email": "security@example.com", "phone": ""})
    assert "Provider: Exemplo Software LLC. Security contact: security@example.com." in named and "[the provider's name]" not in named


def test_a_provider_in_the_installation_is_written_to_a_folder_of_its_own(tmp_path, monkeypatch):
    import deployment

    (tmp_path / "deployment.json").write_text(json.dumps({"mode": "hosted", "provider": {"name": "Exemplo Software LLC", "email": "security@example.com"}}),
                                              encoding="utf-8")
    monkeypatch.setattr(deployment, "PATH", tmp_path / "deployment.json")
    assert public_pages.provider_from_deployment() == {"name": "Exemplo Software LLC", "email": "security@example.com", "phone": ""}
    assert public_pages.main(["--no-sample", "--out", str(tmp_path / "out")]) == 0
    assert "Provider: Exemplo Software LLC." in (tmp_path / "out" / "data_statement.md").read_text(encoding="utf-8")
    assert "[the provider's name]" in page("data_statement.md")  # the repository's copy is untouched, and the check is the repository's
    assert public_pages.check() == []


def test_the_price_page_holds_one_sentence_and_no_digit():
    text = page("price.md")
    assert text == "The owner writes this page.\n"
    assert not re.search(r"\d", text) and not re.search(r"[$€£]", text)
    for name in TEXT_PAGES:
        if name != "accuracy.md":
            assert not re.search(r"\$\d", page(name)), name  # no page states a price, and the accuracy page is the accuracy tool's
    assert "price" not in page("what_it_does.md").lower()


# -- the sample -----------------------------------------------------------------------------------------------------------------------


@pytest.fixture(scope="module")
def sample():
    return public_pages.sample_facts(PUBLIC / "sample")


def test_every_page_of_the_sample_says_sample_and_the_names_are_the_exemplo_familys(sample):
    for name in (public_pages.SAMPLE_PACKET, public_pages.SAMPLE_BUNDLE):
        reader = PdfReader(str(PUBLIC / "sample" / name))
        assert len(reader.pages) > 10
        for number, p in enumerate(reader.pages, 1):
            assert "SAMPLE" in (p.extract_text() or ""), f"{name} page {number}"
        assert "SAMPLE" in str(reader.metadata.get("/Subject")) and "Made-up client" in str(reader.metadata.get("/Subject"))
    text = " ".join(sample["bundle"]["text"] + sample["packet"]["text"])
    low = text.lower()
    assert "EXEMPLO" in text and sample["bundle"]["client"] == "ANA CLARA EXEMPLO SOUZA"
    assert "Marcela Exemplo" in text and "Exemplo Immigration Law LLP" in text
    squeezed = text.replace(" ", "").replace("A-", "A")
    assert set(re.findall(r"A(\d{9})", squeezed)) == {"099000123"}  # one A-Number, a made-up one
    for real in public_pages._real_identity():  # nothing of the shipped firm profile: not the firm, not an attorney, not a number
        assert real not in low, real
    assert len(public_pages._real_identity()) > 10  # the list of what must not appear is not empty


def test_the_samples_readme_says_what_the_two_files_say_about_themselves(sample):
    text = page("sample/README.md")
    p, b = [PdfReader(str(PUBLIC / "sample" / n)) for n in (public_pages.SAMPLE_PACKET, public_pages.SAMPLE_BUNDLE)]
    assert f"{len(p.pages)} pages" in text and f"{len(b.pages)} pages" in text
    assert _number(text, r"lists ([\d,]+) answers") == sample["bundle"]["answers"]
    assert f"{sample['bundle']['approved']} rules are approved by the sample's attorney" in text and sample["bundle"]["unapproved"] == 0
    assert "Every page carries the word SAMPLE" in text and "a client who does not exist" in text
    assert [letter for letter, _ in sample["packet"]["exhibits"]] == ["A", "B", "C", "D", "E"][:len(sample["packet"]["exhibits"])]
    assert sample["packet"]["built"] == sample["bundle"]["built"] and re.fullmatch(r"\d{2}/\d{2}/\d{4}", sample["packet"]["built"])
    assert sample["packet"]["reasons"] and all(r in text for r in sample["packet"]["reasons"])  # what held the packet back is on the page, as the product said it


def test_the_committed_sample_is_what_the_tip_builds(tmp_path, sample):
    """Builds the sample again (about half a minute, in its own process on scratch folders) and compares what the two files say about themselves."""
    public_pages.build_sample(tmp_path)
    again = public_pages.sample_facts(tmp_path)
    for facts in (sample, again):
        assert facts["bundle"]["approval_dates"]
        assert facts["bundle"]["built_on"] == facts["packet"]["built"] == facts["bundle"]["built"]
        assert set(facts["bundle"]["approval_dates"]) == {facts["bundle"]["built_on"]}
    for key in ("packet", "bundle"):  # per-run dates aside; every semantic field and approval count remains compared
        dates = {"text", "built", "built_on", "approval_dates"}
        assert {k: v for k, v in again[key].items() if k not in dates} == {k: v for k, v in sample[key].items() if k not in dates}, key
    readme = public_pages.render_sample_readme(tmp_path)
    for context in ("built on ", "dated "):
        readme = readme.replace(context + again["packet"]["built"], context + sample["packet"]["built"])
    assert readme == page("sample/README.md")  # the release header remains compared verbatim


def test_the_sample_is_built_on_scratch_folders_never_the_machines():
    src = inspect.getsource(public_pages.build_sample)
    for var in ("I485_SETTINGS", "I485_INDEX", "I485_EVENTS", "I485_RULES_APPROVED", "I485_QUERY_DB", "I485_CASES"):
        assert var in public_pages.SAMPLE_ENV
    assert "subprocess.run" in src and "TemporaryDirectory" in src


# -- the words -----------------------------------------------------------------------------------------------------------------------------


@pytest.mark.parametrize("name", TEXT_PAGES)
def test_no_page_has_puffery_a_certificate_a_competitor_or_a_dash(name):
    text = page(name)
    low = text.lower()
    for word in ("cutting edge", "cutting-edge", "best", "ai-powered", "compliant", "soc 2 certified", "guarantee"):
        assert not re.search(rf"(?<![a-z-]){re.escape(word)}(?![a-z-])", low), f"{name}: {word}"
    for word in public_pages.COMPETITORS:
        assert not re.search(rf"(?<![a-z]){re.escape(word)}(?![a-z])", low), f"{name}: {word}"
    for mark in ("—", "–", " -- "):
        assert mark not in text, f"{name}: {mark!r}"
    assert not re.search(r"\b\d{4}-\d{2}-\d{2}\b", text), f"{name}: dates are MM/DD/YYYY"


@pytest.mark.parametrize("name", PLAIN_PAGES)
def test_no_page_that_says_what_the_product_is_has_a_file_name_a_key_or_code(name):
    text = page(name)
    assert "`" not in text, name
    assert not re.search(r"\b[\w-]+\.(?:json|jsonl|py|md|pdf|db|txt|csv)\b", text), name
    assert not re.search(r"\b(?:docs|src|schemas|tools|tests|data)/", text), name
    assert not re.search(r"\b[a-z]+_[a-z]+(?:_[a-z]+)*\b", text), name  # a key is snake_case


def test_a_page_that_names_a_competitor_or_a_dash_is_refused():
    for bad in ("We work beside Clio.", "A page about Docketwise.", "The best way.", "It is cutting edge.", "A — dash.", "A -- dash.", "It meets the standard.",
                "We guarantee it."):
        with pytest.raises(public_pages.PageError):
            public_pages.refuse("a page", bad)
    public_pages.refuse("a page", "A plain page about forms and the I-485. The best-known form is not a claim.")  # a hyphenated word is not the word


def test_the_pages_are_listed_in_the_readme_with_what_makes_each():
    text = page("README.md")
    assert "Nothing serves them" in text and "publishes however they choose" in text
    listed = re.findall(r"(?m)^\| ([\w/]+\.md) \|", text)
    on_disk = sorted(p.relative_to(PUBLIC).as_posix() for p in PUBLIC.rglob("*.md"))
    assert sorted(listed + ["README.md"]) == on_disk
    assert text.count("python tools/accuracy_report.py") == 1  # the accuracy page's maker is the accuracy tool
    for pdf in (public_pages.SAMPLE_PACKET, public_pages.SAMPLE_BUNDLE):
        assert pdf in text and (PUBLIC / "sample" / pdf).exists()


# -- the register ------------------------------------------------------------------------------------------------------------------------


def test_the_register_has_the_regeneration_item_and_the_statement_item_points_at_the_public_copy():
    raw = (schema_path.path("register", "maintenance")).read_text(encoding="utf-8")
    data = json.loads(raw)
    assert json.dumps(data, indent=2, ensure_ascii=False) + "\n" == raw.replace("\r\n", "\n")  # re-dumps byte for byte
    item = next(i for i in data["items"] if i["id"] == "public_pages")
    assert item["party"] == "provider" and item["cadence"] == "monthly" and item["owner"] == "IT" and item["check"]["type"] == "manual"
    steps = " ".join(item["steps"]).lower()
    assert steps.index("run python tools/public_pages.py") < steps.index("read the diff") < steps.index("publish the pages")
    assert "tools/public_pages.py" in item["where"] and "docs/public/ (" in " ".join(item["where"])
    review = next(i for i in data["items"] if i["id"] == "security_program_review")
    assert any(w.startswith("docs/public/data_statement.md") for w in review["where"]) and "docs/security/product_data_statement.md" in review["where"]
    ids = [i["id"] for i in data["items"]]
    assert len(ids) == len(set(ids))
    assert any("public_pages.py" in step and "docs/public/security.md" in step for step in review["steps"])  # the statement changes, the page is made again
    assert "docs/public/README.md is the owner's index and is not for publishing" in " ".join(item["steps"])
    assert "rebuilt" not in " ".join(item["steps"]) and "Build the sample again" in " ".join(item["steps"])  # the sample is built when a filing or the demo case changes


# -- the wording that was wrong, and the names that were garbled ----------------------------------------------------------------------------


def test_every_register_name_on_the_page_is_a_plain_name_with_balanced_brackets_and_no_path_or_dated_source():
    items = _json(schema_path.path("register", "maintenance"))["items"]
    names = [public_pages.item_name(i) for i in items]
    for item, name in zip(items, names):
        assert name and name.count("(") == name.count(")") and "(" not in name.replace("245(i)", ""), (item["id"], name)
        assert not re.search(r"\w/\w|\d{2}/\d{2}/\d{4}|\d{4}-\d{2}-\d{2}|https?:|\.gov|\.com", name), (item["id"], name)  # no web path, no dated source
        assert not name.endswith((",", ";", ":", "-")), (item["id"], name)
    for kind, listed in public_pages.register()["live"].items():
        assert all(len(n) <= 120 for n in listed), kind  # the names printed on the page are short
    # the item whose first parenthesis closes only after a semicolon keeps the words around it
    assert public_pages.item_name({"what": "Where a U petition (I-918, its supplements) is mailed: the Elgin lockbox", "check": {"type": "page_updated"}}) == "Where a U petition is mailed"
    assert public_pages.item_name({"what": "What USCIS accepts (uscis.gov/DACA 'Important Update', 01/24/2025; and more", "check": {"type": "page_updated"}}) == "What USCIS accepts"
    assert public_pages.item_name({"what": "Adjustment under INA 245(i) (Form I-485 Supplement A): who", "check": {"type": "page_updated"}}) == "Adjustment under INA 245(i)"
    text = page("what_it_does.md")
    for name in names:
        assert f"{name}" in text or name in ("", ) or public_pages.item_name({"what": name, "check": {"type": "manual"}}) == name  # a name on the page is its own plain name
    assert "(uscis.gov" not in text and "(read " not in text


def test_what_it_does_not_do_names_nothing_a_function_shows_the_product_does(monkeypatch):
    text = page("what_it_does.md")
    topics = re.findall(r"(?m)^- (.+)$", text.split("## What it does not do")[1].split("Connections to the firm's other systems")[0])
    assert len(topics) >= 5
    done = public_pages.what_the_product_does_now()
    assert done == {"e-signature": True, "calendar": True, "crm": True}  # the functions that show it: the typed-name signature, the calendar feed, prospects and notes
    for topic in topics:
        assert not public_pages.now_done(topic), topic
        for word, shown in done.items():
            if word in topic.lower():
                assert "(" in topic, f"{topic!r} names something the product does, with no narrowing"
    assert not any(t in ("A CRM", "Billing, trust, a general calendar, CRM, e-signature of engagement letters") for t in topics)
    # the plan's old wording is dropped by the function, not by hand
    real = public_pages.section

    def old_plan(text, heading, level=2):
        if heading == "What I'd say no to":
            return ("Employment-based immigration (I-140, PERM, H-1B): not this firm's work. Billing, trust, a general calendar, CRM, e-signature of engagement letters: "
                    "Clio's, and regulated. A chatbot that answers clients' legal questions: no.")
        return real(text, heading, level)

    monkeypatch.setattr(public_pages, "section", old_plan)
    assert public_pages.does_not_do() == ["Employment-based immigration (I-140, PERM, H-1B)", "A chatbot that answers clients' legal questions"]
    plan = (REPO / "docs" / "design_plan.md").read_text(encoding="utf-8")
    no_list = " ".join(plan.split("\n## What I'd say no to\n")[1].split())
    assert "e-signature of engagement letters" not in plan and "typed name" in no_list and "read-only feed" in no_list


def test_the_sample_readme_does_not_overclaim():
    text = page("sample/README.md")
    assert "invented, except USCIS's own addresses and the forms' printed text" in text
    assert "entered by the sample builder, not by a person's review" in text
    assert "40 review cards still open" in text or re.search(r"\d+ review cards? still open", text)  # the open cards are named as well as the Visa Bulletin
    assert "Visa Bulletin" in text and "These checks were open when the product built it:" in text
    assert "software version" not in text.split("\n", 3)[3].lower()  # the PDFs say the day they were built, not a version
    assert re.search(r"They were built on \d{2}/\d{2}/\d{4}\.", text)


def test_the_readme_says_at_its_top_it_is_the_owners_index_and_not_for_publishing():
    text = page("README.md")
    head = text.split("\n\n")[2]
    assert "owner's index" in head and "not for publishing" in head


def test_a_refused_sample_leaves_the_committed_pdfs_as_they_were(tmp_path, monkeypatch):
    out = tmp_path / "sample"
    out.mkdir()
    for name in (public_pages.SAMPLE_PACKET, public_pages.SAMPLE_BUNDLE):
        (out / name).write_bytes(b"the committed file")

    class Refused:
        returncode = 1
        stderr = "the sample carries the shipped firm's own details: a firm"
        stdout = ""

    monkeypatch.setattr(public_pages.subprocess, "run", lambda *a, **k: Refused())
    with pytest.raises(public_pages.PageError, match="shipped firm's own details"):
        public_pages.build_sample(out)
    assert all((out / n).read_bytes() == b"the committed file" for n in (public_pages.SAMPLE_PACKET, public_pages.SAMPLE_BUNDLE))
    src = inspect.getsource(public_pages.sample_worker)
    assert "unlink" not in src  # the worker works in a scratch folder: there is nothing of the repository's to remove


def test_no_sentence_the_generator_types_claims_what_a_function_does_not_show():
    text = page("what_it_does.md")
    for typed in ("Each is a set of forms, a cover letter, a checklist", "still has its review cards and its checklist", "rather than from where the case path stands",
                  "A packet is not ready while any check fails", "on purpose"):
        assert typed not in text, typed
    security = page("security.md")
    assert "The firm decides whether each is used" not in security
