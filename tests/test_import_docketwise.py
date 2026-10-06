"""tools/import_docketwise.py: a Docketwise export (contacts, matters, documents) into client folders and the portal.

Everything here is made up: the people are "Ana Clara Exemplo Souza" and friends, the column headings are the importer's own
guesses (schemas/firm/import_docketwise_columns.json: Docketwise does not publish its export's columns), and the "PDFs" are a
few bytes. Nothing touches the network or any real client's folder."""

import io
import json
import sys
import zipfile
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "tools"))

import import_docketwise as imp  # noqa: E402

CONTACTS = (
    "id,First Name,Last Name,Email,Mobile Phone Number,Language,SMS OK\n"
    "c1,Ana Clara,Exemplo Souza,ana.exemplo@example.com,+1 555 010 0201,Portuguese,yes\n"
    "c2,Maria,Exemplo,maria.exemplo@example.com,+1 555 010 0202,,\n"
    "c3,Jean,Egzanp,,+1 555 010 0203,Kreyòl,no\n"
)
MATTERS = (
    "ID,Number,Title,Client ID,Type,Status,Archived\n"
    "4101,A-1,Souza SIJ,c1,SIJ,Open,\n"
    "4102,A-2,Maria green card,c2,I-485,Open,\n"
    "4103,A-3,Old file,c3,SIJ,Closed,yes\n"
    "4104,,Orphan,c9,SIJ,Open,\n"
    ",,No id,c1,SIJ,Open,\n"
)
PDF = b"%PDF-1.4 made-up passport"


def png_bytes() -> bytes:
    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(buf, format="PNG")
    return buf.getvalue()


@pytest.fixture
def world(tmp_path):
    (tmp_path / "contacts.csv").write_text(CONTACTS, encoding="utf-8")
    (tmp_path / "matters.csv").write_text(MATTERS, encoding="utf-8")
    docs = tmp_path / "docs"
    (docs / "4101").mkdir(parents=True)
    (docs / "4101" / "passport.pdf").write_bytes(PDF)
    (docs / "4101" / "Birth").mkdir()
    (docs / "4101" / "Birth" / "certificate photo.png").write_bytes(png_bytes())
    (docs / "Maria green card (4102)").mkdir()
    (docs / "Maria green card (4102)" / "i94.pdf").write_bytes(b"%PDF-1.4 i94")
    (docs / "4103").mkdir()
    (docs / "4103" / "old.pdf").write_bytes(b"%PDF-1.4 old")
    (docs / "Nobody").mkdir()
    (docs / "Nobody" / "x.pdf").write_bytes(b"%PDF-1.4 x")
    (docs / "4102" / "notes.docx").parent.mkdir(exist_ok=True)
    (docs / "4102" / "notes.docx").write_bytes(b"not readable by the pipeline")
    (docs / "loose.pdf").write_bytes(b"%PDF-1.4 loose")
    return {"contacts": tmp_path / "contacts.csv", "matters": tmp_path / "matters.csv", "docs": docs,
            "out": tmp_path / "clients", "portal": tmp_path / "portal", "root": tmp_path}


def go(w, **kw):
    return imp.run_import(w["contacts"], w["matters"], kw.pop("docs", w["docs"]), w["out"], w["portal"], **kw)


def profile(w, client_id):
    return json.loads((w["portal"] / "clients" / client_id / "profile.json").read_text(encoding="utf-8"))


ANA = "ana_clara_exemplo_souza-dw4101"
MARIA = "maria_exemplo-dw4102"


def test_one_client_folder_and_one_portal_client_per_matter(world):
    run = go(world)
    assert {o.client_id: o.result for o in run.outcomes if o.client_id} == {ANA: "created", MARIA: "created"}
    p = profile(world, ANA)
    assert (p["name"], p["email"], p["phone"], p["language"]) == ("Ana Clara Exemplo Souza", "ana.exemplo@example.com", "+1 555 010 0201", "pt")
    assert p["docketwise_matter_id"] == "4101" and p["docketwise_contact_id"] == "c1" and p["docketwise_matter_type"] == "SIJ"
    assert p["consent"] == {"email": False, "sms": True, "whatsapp": False}   # only the column the export has; the rest is not a yes
    assert sorted(f.name for f in (world["out"] / ANA / "source").iterdir()) == ["Birth - certificate photo.pdf", "passport.pdf"]
    assert (world["out"] / ANA / "source" / "Birth - certificate photo.pdf").read_bytes().startswith(b"%PDF")  # the photo became a PDF
    assert (world["out"] / MARIA / "source" / "i94.pdf").exists()
    state = json.loads((world["out"] / ANA / imp.STATE).read_text(encoding="utf-8"))
    assert state["matter_id"] == "4101" and len(state["files"]) == 2


def test_what_the_export_does_not_say_is_not_asked_and_not_guessed(world):
    go(world)
    maria = profile(world, MARIA)
    assert maria["consent"] == {"email": False, "sms": False, "whatsapp": False} and maria["consent_asked"] is False
    assert maria["language"] == "pt" and maria["language_from_export"] is False
    assert profile(world, ANA)["consent_asked"] is True


def test_the_column_mapping_reads_headings_by_name_and_says_what_it_did_not_find(world):
    contacts = imp.Table.build(*imp.read_csv(world["contacts"], "Contacts"), imp.load_mapping(imp.MAPPING)["contacts"])
    assert contacts.found["first_name"] == "First Name" and contacts.found["phone"] == "Mobile Phone Number" and contacts.found["sms_ok"] == "SMS OK"
    assert "office" in contacts.missing and "email_ok" in contacts.missing and contacts.unused == []
    mapping = imp.load_mapping(imp.MAPPING)
    assert "TO CONFIRM WITH THE FIRM'S EXPORT" in mapping["_status"]   # nothing in the mapping is presented as fact


def test_the_firm_can_change_the_mapping(world, tmp_path):
    (world["contacts"]).write_text("Pessoa,Telefone,E-mail\nc1,+1 555 010 0201,ana.exemplo@example.com\n", encoding="utf-8")
    mapping = json.loads(imp.MAPPING.read_text(encoding="utf-8"))
    mapping["contacts"]["id"] = ["Pessoa"]
    mapping["contacts"]["full_name"] = ["Pessoa"]
    mapping["contacts"]["phone"] = ["Telefone"]
    mapping["contacts"]["email"] = ["E-mail"]
    path = tmp_path / "mine.json"
    path.write_text(json.dumps(mapping), encoding="utf-8")
    run = go(world, mapping_path=path, docs=None)
    assert run.contacts.found["phone"] == "Telefone" and run.contacts.found["email"] == "E-mail"


def test_a_dry_run_writes_nothing_and_says_so(world):
    run = go(world, dry_run=True)
    assert not world["out"].exists() and not world["portal"].exists()
    text = imp.report(run)
    assert "practice run, nothing was written" in text and "would be created: 2" in text


def test_a_matter_with_no_usable_row_is_skipped_with_the_reason(world):
    run = go(world, dry_run=True)
    why = {o.matter_id: o.why for o in run.outcomes if o.result == "skipped"}
    assert "archived" in why["4103"]
    assert "contact c9" in why["4104"]
    assert "no matter id" in why[""]


def test_an_existing_client_is_never_overwritten(world):
    folder = world["out"] / ANA
    (folder / "source").mkdir(parents=True)
    (folder / "source" / "mine.pdf").write_bytes(b"%PDF-1.4 the firm's own")
    run = go(world)
    [refused] = [o for o in run.outcomes if o.client_id == ANA]
    assert refused.result == "refused" and "did not come from this Docketwise matter" in refused.why
    assert [f.name for f in (folder / "source").iterdir()] == ["mine.pdf"]
    assert not (folder / imp.STATE).exists() and not (world["portal"] / "clients" / ANA).exists()
    assert (world["out"] / MARIA / "source").exists()        # the other matter went ahead


def test_a_portal_client_already_made_for_the_matter_is_not_made_twice(world):
    go(world)
    other = world["portal"] / "clients" / "someone_else"
    other.mkdir()
    (other / "profile.json").write_text(json.dumps({"id": "someone_else", "docketwise_matter_id": "4102"}), encoding="utf-8")
    (world["portal"] / "clients" / MARIA / "profile.json").unlink()
    run = go(world)
    assert next(o for o in run.outcomes if o.matter_id == "4102").result == "refused"


def test_running_twice_adds_nothing_twice(world):
    go(world)
    before = {p.relative_to(world["root"]).as_posix(): p.read_bytes() for p in world["root"].rglob("*") if p.is_file() and "events" not in p.name}
    run = go(world)
    assert {o.result for o in run.outcomes if o.client_id} == {"already"}
    after = {p.relative_to(world["root"]).as_posix(): p.read_bytes() for p in world["root"].rglob("*") if p.is_file() and "events" not in p.name}
    assert before == after
    events = (world["portal"] / "clients" / ANA / "events.jsonl").read_text(encoding="utf-8").splitlines()
    assert sum('"client_added"' in e for e in events) == 1
    run = go(world, merge=True)   # even with merge: the same files are not placed again
    assert {o.result for o in run.outcomes if o.client_id} == {"merged"} and all(not o.documents_placed for o in run.outcomes)
    assert sorted(f.name for f in (world["out"] / ANA / "source").iterdir()) == ["Birth - certificate photo.pdf", "passport.pdf"]


def test_merge_adds_only_new_documents_and_leaves_the_profile_alone(world):
    go(world)
    imp_profile = profile(world, ANA)
    (world["docs"] / "4101" / "ssn card.pdf").write_bytes(b"%PDF-1.4 ssn")
    run = go(world)   # without merge: left alone
    assert next(o for o in run.outcomes if o.client_id == ANA).result == "already"
    assert not (world["out"] / ANA / "source" / "ssn card.pdf").exists()
    run = go(world, merge=True)
    assert next(o for o in run.outcomes if o.client_id == ANA).documents_placed == ["ssn card.pdf"]
    assert profile(world, ANA) == imp_profile


def test_a_stopped_import_is_finished_not_refused(world):
    go(world)
    (world["portal"] / "clients" / ANA / "profile.json").unlink()   # the first run died after the folder, before the profile
    run = go(world)
    assert next(o for o in run.outcomes if o.client_id == ANA).result == "finished"
    assert profile(world, ANA)["docketwise_matter_id"] == "4101"


def test_the_report_is_plain_words_and_says_what_was_left_out(world):
    text = imp.report(go(world))
    for phrase in ("Docketwise import", "Cases were created: 2", "Matters skipped: 3", "archived in Docketwise", "Things to check",
                   "got Portuguese, because the export gave no language", "has no consent answer, so the portal will send nothing", "Nobody", "loose.pdf", "notes.docx",
                   "its matter was skipped", "Looked for and not in the file", "guesses until compared"):
        assert phrase in text, phrase
    assert "—" not in text and " -- " not in text   # no dashes the screens avoid
    assert "TypeError" not in text and "Traceback" not in text


def test_a_client_who_has_not_said_yes_is_not_messaged(world):
    from portal.admin import send
    from portal.notify import Notifier
    from portal.store import PortalStore

    import conflicts

    cases = world["root"] / "cases"  # this test's own case folders, where the conflict checks are recorded
    go(world, cases=cases)
    store = PortalStore(world["portal"])
    notifier = Notifier(world["portal"] / "outbox.jsonl", cases_root=cases)
    # an imported client waits for an attorney's conflict decision (src/conflicts.py): nothing goes until then
    assert send(store, notifier, MARIA, "invite") == [{"channel": "all", "result": "skipped", "why": "conflict check waiting", "text": conflicts.HELD}]
    for cid in (MARIA, ANA):
        conflicts.decide(cases / cid, "none", "", by="Sam Attorney", role="attorney")
    results = send(store, notifier, MARIA, "invite")
    assert results and all(r["result"] == "skipped" and r["why"] == "no consent" for r in results)
    assert not (world["portal"] / "outbox.jsonl").exists() or (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8") == ""
    ana = send(store, notifier, ANA, "invite")   # Ana's export said sms yes, and said nothing about email or WhatsApp
    assert {r["channel"] for r in ana if r["result"] == "skipped"} == {"email", "whatsapp"}
    assert {r["channel"] for r in ana if r["result"] != "skipped"} == {"sms"}


def test_the_documents_say_where_they_came_from(world):
    import documents

    assert "docketwise" in documents.SOURCES
    assert documents.source_of(ANA) == "docketwise" and documents.source_of("ana-fv123") == "filevine" and documents.source_of("ana") == "folder"
    from connectors.base import RemoteClient
    from connectors.sync import local_id

    assert local_id(RemoteClient("4101", "Ana Clara Exemplo Souza"), "docketwise") == ANA   # one naming rule, here and in the connectors
    assert imp.local_id("Ana Clara Exemplo Souza", "4101") == ANA


def test_the_overnight_run_picks_the_new_cases_up(world):
    import overnight

    go(world)
    todo, skipped = overnight.choose(world["out"], world["root"] / "data_clients", {}, False, None)
    assert sorted(name for name, _why in todo) == sorted([ANA, MARIA])


def test_a_zip_with_a_wrapping_folder_works_and_unsafe_paths_stay_out(world):
    z = world["root"] / "export.zip"
    with zipfile.ZipFile(z, "w") as zf:
        zf.writestr("Docketwise export/4101/passport.pdf", PDF)
        zf.writestr("Docketwise export/4102/i94.pdf", b"%PDF-1.4 i94")
        zf.writestr("../escape.pdf", b"%PDF-1.4 no")
    run = go(world, docs=z)
    assert (world["out"] / ANA / "source" / "passport.pdf").exists() and (world["out"] / MARIA / "source" / "i94.pdf").exists()
    assert not (world["root"] / "escape.pdf").exists()
    assert any("unsafe path" in x for x in run.zip_left_out)


def test_a_person_with_two_matters_is_pointed_out(world):
    world["matters"].write_text(MATTERS + "4105,A-5,Second Souza matter,c1,I-485,Open,\n", encoding="utf-8")
    text = imp.report(go(world, dry_run=True))
    assert "more than one matter" in text


def test_the_office_must_be_one_of_the_firms(world):
    from offices import offices

    real = offices()[0]["name"]
    world["contacts"].write_text(
        "id,First Name,Last Name,Email,Mobile Phone Number,Language,SMS OK,Office\n"
        f"c1,Ana Clara,Exemplo Souza,ana.exemplo@example.com,+1 555 010 0201,Portuguese,yes,\"{real}\"\n"
        "c2,Maria,Exemplo,maria.exemplo@example.com,+1 555 010 0202,,,Atlantis\n", encoding="utf-8")
    run = go(world)
    assert profile(world, ANA)["office"] == real
    assert "office" not in profile(world, MARIA)
    assert any("Atlantis" in n for o in run.outcomes for n in o.notes)


def test_a_matters_file_without_an_id_column_is_refused_in_plain_words(world):
    world["matters"].write_text("Foo,Bar\n1,2\n", encoding="utf-8")
    with pytest.raises(imp.ImportProblem, match="matter's id"):
        go(world)
    assert imp.main(["--contacts", str(world["contacts"]), "--matters", str(world["matters"]), "--out", str(world["out"]),
                     "--portal", str(world["portal"])]) == 2
    assert not world["out"].exists()


def test_the_command_line_writes_the_report_and_prints_the_next_step(world, capsys):
    argv = ["--contacts", str(world["contacts"]), "--matters", str(world["matters"]), "--documents", str(world["docs"]),
            "--out", str(world["out"]), "--portal", str(world["portal"])]
    assert imp.main(argv + ["--dry-run"]) == 0
    assert not world["out"].exists() and "Practice run" in capsys.readouterr().out
    assert imp.main(argv) == 0
    shown = capsys.readouterr().out
    assert (world["out"] / "import_report.md").is_file() and "overnight.py" in shown and "--clients-root" in shown
    assert imp.main(argv + ["--merge"]) == 0
    assert (world["out"] / "import_report.md").is_file() and len(list(world["out"].glob("import_report_*.md"))) == 1   # the earlier report is kept


def test_a_missing_file_is_a_sentence_not_a_traceback(world, capsys):
    assert imp.main(["--contacts", str(world["root"] / "nope.csv"), "--matters", str(world["matters"]), "--out", str(world["out"])]) == 2
    assert "is not a file" in capsys.readouterr().err


def test_the_report_names_the_people_to_fix_not_a_count(world):
    world["matters"].write_text(MATTERS + "4105,A-5,Second Souza matter,c1,I-485,Open,\n", encoding="utf-8")
    text = imp.report(go(world))
    # language: Maria's row has none; consent: Maria's has no answer; several matters: Ana has two. Each by name and matter title.
    assert "  Maria Exemplo (matter Maria green card): none given" in text
    assert "  Maria Exemplo (matter Maria green card)\n" in text
    assert "  Ana Clara Exemplo Souza: Souza SIJ; Second Souza matter" in text
    # the cases list reads as people and matters, never the folder's internal id
    cases = text.split("## Cases")[1].split("\n## ")[0]
    assert "- Ana Clara Exemplo Souza (matter Souza SIJ): 2 documents" in cases and "-dw4101" not in cases


def test_the_report_agrees_in_number(world):
    text = imp.report(go(world))
    assert "The documents folder 'Nobody' was left out" in text and "1 file is loose" in text
    assert "The documents folder 'Nobody' would be left out" in imp.report(go(world, dry_run=True))


@pytest.mark.parametrize("name", ["D:/x.pdf", "D:x.pdf", "4101/a:b.pdf", "C:\\Windows\\x.pdf", "/abs.pdf", "../x.pdf", "4101/../../x.pdf"])
def test_the_zip_guard_rejects_paths_that_could_leave_the_folder(name):
    assert imp._safe_member(name) is None
    assert imp._safe_member("4101/passport.pdf") is not None


def test_a_zip_with_drive_paths_extracts_nothing_outside(tmp_path):
    z = tmp_path / "bad.zip"
    with zipfile.ZipFile(z, "w") as zf:
        for name in ("D:/x.pdf", "D:x.pdf", "4101/a:b.pdf", "4101/ok.pdf"):
            zf.writestr(name, PDF)
    into = tmp_path / "into"
    into.mkdir()
    left = imp.unpack(z, into)
    assert len(left) == 3 and all("unsafe path" in x for x in left)
    assert sorted(p.relative_to(into).as_posix() for p in into.rglob("*") if p.is_file()) == ["4101/ok.pdf"]


def test_an_asylum_or_vawa_matter_is_restricted_from_the_start_and_named_in_the_report(world, monkeypatch):
    """docs/research/buyer_walkthrough_4.md, finding 1: an asylum matter came in as an ordinary invited client. Now the case folder holds
    the restriction before the portal client exists, the report names each protected matter and says it was not invited, and no
    invitation can go to it by any path."""
    import restricted
    from portal import admin
    from portal.store import PortalStore

    (world["root"] / "matters.csv").write_text(MATTERS + "4105,A-5,Ana asylum,c1,Asylum,Open,\n4106,A-6,Maria VAWA,c2,VAWA Self-Petition,Open,\n",
                                              encoding="utf-8")
    cases = world["root"] / "cases"
    monkeypatch.setenv("I485_CASES", str(cases))
    practice = imp.report(go(world, dry_run=True, cases=cases))
    assert "## Restricted: protected cases, not invited" in practice and "Each would be restricted from the start" in practice and not cases.exists()
    run = go(world, cases=cases)
    asylum, vawa = "ana_clara_exemplo_souza-dw4105", "maria_exemplo-dw4106"
    assert {o.client_id: o.protected for o in run.outcomes if o.protected} == {asylum: "208.6", vawa: "1367"}
    for cid, law in ((asylum, "208.6"), (vawa, "1367")):
        rec = restricted.record(cases / cid)
        assert rec["marked"]["on"] and rec["marked"]["law"] == law and rec["marked"]["by"] == "the Docketwise import"
        assert rec["marked"]["reason"].startswith("Imported from Docketwise, matter type ")
    assert not (cases / ANA / "access.json").exists()  # an SIJ matter is not restricted by its type (its folder holds its conflict check)
    text = imp.report(run)
    section = text.split("## Restricted: protected cases, not invited", 1)[1].split("\n## ", 1)[0]
    assert "Ana Clara Exemplo Souza (matter Ana asylum): matter type Asylum, an asylum case (8 CFR 208.6)" in section
    assert "Maria Exemplo (matter Maria VAWA): matter type VAWA Self-Petition, a VAWA, T or U visa case (8 U.S.C. 1367)" in section
    assert "No invitation was sent" in section and "in person" in section and "Each is restricted" in section
    types = text.split("## Matter types in the export", 1)[1].split("\n## ", 1)[0]  # every type, and how it was taken
    assert "- Asylum (1 matter): protected, an asylum case (8 CFR 208.6)" in types and "- SIJ (4 matters): not taken as protected" in types
    # the command line's "invite everyone not invited yet": the protected two get nothing, the others are invited as before
    store = PortalStore(world["portal"])
    for cid in (ANA, MARIA, asylum, vawa):  # consent recorded by the office, so a message could go if it were allowed
        store.update_profile(cid, consent={"email": True, "sms": True, "whatsapp": False})
    import conflicts

    for cid in (ANA, MARIA, asylum, vawa):  # every imported case waits for an attorney's conflict decision (src/conflicts.py); the attorney decides
        assert conflicts.held(cases / cid)
        conflicts.decide(cases / cid, "none", "", by="Sam Attorney", role="attorney")
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example")
    admin.main(["--root", str(world["portal"]), "invite"])
    outbox = (world["portal"] / "outbox.jsonl").read_text(encoding="utf-8")
    assert "ana.exemplo@example.com" in outbox  # Ana's SIJ case was invited...
    assert not store.profile(asylum).get("invited_at") and not store.profile(vawa).get("invited_at") and store.profile(ANA).get("invited_at")
    # ...and a second import never marks again a case an attorney has since lifted
    restricted.mark(cases / asylum, False, "", "Sam Attorney", "attorney")
    again = imp.report(go(world, cases=cases))
    assert not restricted.is_restricted(cases / asylum)
    lifted = again.split("## Protected by type, restriction lifted by an attorney", 1)[1].split("\n## ", 1)[0]  # said so, not "restricted"
    assert "Ana Clara Exemplo Souza (matter Ana asylum): matter type Asylum" in lifted
    assert "Ana asylum" not in again.split("## Restricted: protected cases, not invited", 1)[1].split("\n## ", 1)[0]


def test_asilo_and_a_type_the_firm_names_protected_are_restricted_and_never_texted(world, monkeypatch):
    """The verifier's live run: Docketwise matters typed "Asilo" and "Humanitarian" came in unrestricted and `admin invite` texted
    both. "Asilo" is asylum in Portuguese and Spanish (8 CFR 208.6); "Humanitarian" is the firm's own word: the report lists it as
    not taken as protected, and the protected-type option names it."""
    import restricted
    from portal import admin
    from portal.store import PortalStore

    (world["root"] / "matters.csv").write_text("ID,Number,Title,Client ID,Type,Status,Archived\n4201,B-1,Ana asilo,c1,Asilo,Open,\n"
                                              "4202,B-2,Maria humanitarian,c2,Humanitarian,Open,\n", encoding="utf-8")
    cases = world["root"] / "cases"
    monkeypatch.setenv("I485_CASES", str(cases))
    asilo, humanitarian = "ana_clara_exemplo_souza-dw4201", "maria_exemplo-dw4202"
    practice = imp.report(go(world, dry_run=True, cases=cases))
    assert "- Asilo (1 matter): protected, an asylum case (8 CFR 208.6)" in practice and "- Humanitarian (1 matter): not taken as protected" in practice
    run = go(world, cases=cases, protected_types=["humanitarian"])
    assert {o.client_id: o.protected for o in run.outcomes} == {asilo: "208.6", humanitarian: restricted.FIRM_KIND}
    rec = restricted.record(cases / humanitarian)["marked"]
    assert rec["on"] and rec["law"] is None and rec["reason"] == "Imported from Docketwise, matter type Humanitarian: the firm treats this kind of case as confidential from the start."
    text = imp.report(run)
    assert "- Humanitarian (1 matter): protected, named protected by the firm" in text
    store = PortalStore(world["portal"])
    for cid in (asilo, humanitarian):
        store.update_profile(cid, consent={"email": True, "sms": True, "whatsapp": False})
    monkeypatch.setenv("PORTAL_BASE_URL", "https://portal.example")
    admin.main(["--root", str(world["portal"]), "invite"])
    outbox = world["portal"] / "outbox.jsonl"
    assert not outbox.exists() or not outbox.read_text(encoding="utf-8").strip()  # nothing texted or e-mailed to either
    assert not store.profile(asilo).get("invited_at") and not store.profile(humanitarian).get("invited_at")
    assert imp.parse(["--contacts", "c", "--matters", "m", "--out", "o", "--protected-type", "Humanitarian", "--protected-type", "Parole"]).protected_types == ["Humanitarian", "Parole"]


def test_every_mapped_field_says_where_its_name_comes_from():
    mapping = imp.load_mapping(imp.MAPPING)
    for part in ("contacts", "matters"):
        fields = {k for k in mapping[part] if not k.startswith("_")}
        assert fields == set(mapping[part]["_basis"]), part
        for field_, basis in mapping[part]["_basis"].items():
            assert basis.startswith("guess") or ("Docketwise" in basis and "https://" in basis), (part, field_)
    # consent, language and office are guesses: no Docketwise page names them
    assert all(mapping["contacts"]["_basis"][f].startswith("guess") for f in ("language", "email_ok", "sms_ok", "whatsapp_ok", "office"))


# -- buyer visit 4: the report in a paralegal's words, and a language for the clients the export gives none for --------------


def test_the_missing_id_column_is_said_without_file_editing_words(world):
    world["matters"].write_text("Foo,Bar\n1,2\n", encoding="utf-8")
    with pytest.raises(imp.ImportProblem) as e:
        go(world)
    text = str(e.value)
    assert "needs a column with each matter's id" in text and "tell us which heading" in text and "Foo, Bar" in text
    assert "column mapping" not in text and "put the right one first" not in text.lower()


def test_a_language_can_be_chosen_for_clients_whose_row_names_none(world):
    run = go(world, language="Spanish")
    assert profile(world, MARIA)["language"] == "es" and profile(world, ANA)["language"] == "pt"  # Ana's row names Portuguese: the export wins
    text = imp.report(run)
    assert "got Spanish, because the export gave no language the portal speaks and Spanish is the language chosen for those clients" in text
    assert "## Each client's language" in text and "Portuguese, as the export says (Portuguese)" in text and "Spanish, because the export gave no language" in text


def test_without_a_chosen_language_the_report_says_portuguese_is_the_default(world):
    text = imp.report(go(world))
    assert profile(world, MARIA)["language"] == "pt"
    assert "got Portuguese, because the export gave no language the portal speaks and no other language was chosen" in text
    assert "(the default)" in text


def test_a_language_the_portal_does_not_speak_is_refused_and_the_command_line_takes_it(world):
    with pytest.raises(imp.ImportProblem, match="not one the portal speaks"):
        go(world, language="Klingon")
    assert imp.main(["--contacts", str(world["contacts"]), "--matters", str(world["matters"]), "--out", str(world["out"]),
                     "--portal", str(world["portal"]), "--language", "ht", "--dry-run"]) == 0
    assert not world["out"].exists()
    assert imp.main(["--contacts", str(world["contacts"]), "--matters", str(world["matters"]), "--out", str(world["out"]),
                     "--portal", str(world["portal"]), "--language", "ht"]) == 0
    assert profile(world, MARIA)["language"] == "ht"
