"""src/portal: the firm's own client questionnaire -- question bank logic,
storage and sign-in, notifications, the firm-side task list, and the web
app's guards. Synthetic clients only."""

import io
import json

import pytest
from communication_fixture import (
    accepted_link,
    approve_client,
    installation,
    review_request,
)
from fastapi.testclient import TestClient
from PIL import Image

import schema_path
from batch import process_documents
from portal.app import create_app
from portal.bank import (
    PORTAL_DOC_ID,
    all_questions,
    answers_to_facts,
    clean,
    holds,
    languages,
    load_bank,
    localized,
    missing_required,
    required_documents,
)
from portal.engine import client_tasks
from portal.notify import TEMPLATES, Notifier, render
from portal.store import PortalStore

BANK = load_bank()
Q = all_questions(BANK)
LANGS = languages()  # pt, es, en, ht
SSN_CARD = "YOUR SOCIAL SECURITY CARD\n123-45-6789\nVALID FOR WORK ONLY WITH DHS AUTHORIZATION"


def _png() -> bytes:
    out = io.BytesIO()
    Image.new("RGB", (40, 30), "white").save(out, format="PNG")
    return out.getvalue()


# --- the question bank ---------------------------------------------------------


def test_every_question_has_every_language_and_a_target():
    for qid, q in Q.items():
        for lang in LANGS:
            assert q["label"].get(lang), (qid, lang)
        for o in q.get("options", []):
            assert all(o["label"].get(lang) for lang in LANGS), (qid, o)
        targets = q.get("fact") or q.get("facts") or q.get("fact_pattern") or [f for o in q.get("options", []) for f in o.get("facts", [])]
        assert targets, qid
    for d in BANK["documents"]:
        assert all(d["label"].get(lang) and d["why"].get(lang) for lang in LANGS), d["id"]


def test_conditions():
    assert holds("always", {}) and not holds("optional", {})
    assert holds({"q": "arrested", "eq": "Yes"}, {"arrested": "Yes"})
    assert not holds({"q": "arrested", "eq": "Yes"}, {})
    assert holds({"q": "times_married", "gt": 1}, {"times_married": 2})
    assert not holds({"q": "times_married", "gt": 1}, {"times_married": "x"})
    assert holds({"any": [{"q": "a", "eq": 1}, {"q": "b", "eq": 2}]}, {"b": 2})
    assert not holds({"all": [{"q": "a", "eq": 1}, {"q": "b", "eq": 2}]}, {"b": 2})
    assert holds({"q": "a_number", "filled": True}, {"a_number": "012345678"})


def test_follow_up_questions_appear_only_when_called_for():
    def ids(answers):
        return {q["id"] for s in localized(BANK, "en", answers) for q in s["questions"]}

    assert "criminal_cases" not in ids({})
    assert "criminal_cases" in ids({"arrested": "Yes"})
    assert "spouse_name" not in ids({"marital_status": "Single"})
    assert "spouse_name" in ids({"marital_status": "Married"})
    assert "ssn" in ids({"has_ssn": "Yes"}) and "ssn" not in ids({"has_ssn": "No"})


def test_answers_are_checked_as_typed():
    assert clean(Q["dob"], "2008-02-30") == (None, "invalid_date")
    assert clean(Q["dob"], "2008-02-03") == ("2008-02-03", "")
    assert clean(Q["ssn"], "123 45 6789") == ("123-45-6789", "")
    assert clean(Q["ssn"], "12345") == (None, "invalid_ssn")
    assert clean(Q["a_number"], "A12345678") == ("012345678", "")
    assert clean(Q["phone"], "(555) 010-0199") == ("+15550100199", "")
    assert clean(Q["sex"], "X") == (None, "invalid_choice")
    assert clean(Q["height"], {"feet": "5", "inches": "4"}) == ({"feet": 5, "inches": 4, "form": "5'4\""}, "")


def test_clients_never_convert_units_themselves():
    assert clean(Q["height"], {"cm": "165"}) == ({"cm": 165, "form": "5'5\""}, "")
    assert clean(Q["height"], {"cm": "1,65"})[0]["cm"] == 165  # metres typed in the cm box
    assert clean(Q["height"], {"cm": "16"})[1] == "invalid_height"
    assert clean(Q["weight"], {"kg": "60"}) == ({"kg": 60, "form": "132"}, "")
    assert clean(Q["weight"], {"kg": "58,5"})[0] == {"kg": 58.5, "form": "129"}
    assert clean(Q["weight"], {"lb": "130"}) == ({"lb": 130, "form": "130"}, "")
    assert clean(Q["weight"], {"kg": "600"})[1] == "invalid_weight"
    facts = {f.fact_key: f for f in answers_to_facts({"height": {"cm": 165, "form": "5'5\""}, "weight": {"kg": 60, "form": "132"}})}
    assert facts["applicant.height"].normalized_value == "5'5\"" and facts["applicant.height"].raw_value == "165 cm"
    assert facts["applicant.weight_lbs"].normalized_value == "132" and facts["applicant.weight_lbs"].raw_value == "60 kg"


def test_not_sure_leaves_the_box_blank_and_tells_the_attorney():
    from batch import eligibility_alerts

    assert all(o["value"] in ("Yes", "No", "Unsure") for s in BANK["sections"] if s["id"] == "eligibility"
               for q in s["questions"] for o in q.get("options", []))
    other = {f.fact_key: f.normalized_value for f in answers_to_facts({"used_other_names": "Unsure"})}
    assert "questionnaire.blank.other_names" not in other  # "not sure" never becomes the firm's N/A
    assert "questionnaire.unsure.questionnaire.used_other_names" in other
    answers = {"final_order": "Unsure", "denied_visa": "No", "arrested": "Unsure"}
    facts = {f.fact_key: f.normalized_value for f in answers_to_facts(answers)}
    assert "applicant.part9.final_order_of_removal" not in facts and facts["applicant.part9.denied_visa"] == "No"
    assert facts["questionnaire.unsure.applicant.part9.final_order_of_removal"].startswith("Has an immigration judge or officer ever ordered you deported")
    court = next(d for d in required_documents(answers) if d["id"] == "court_disposition")
    assert court["required"] is False  # only a Yes requires court papers; "not sure" offers the upload
    result = process_documents("pilot", [], answers=answers_to_facts(answers))
    alert = next(f for f in eligibility_alerts(result.graph) if "CLIENT NOT SURE" in f.message)
    assert "ordered you deported" in alert.message and "arrested" in alert.message
    assert clean(Q["home_address"], {"street": "1 Test St", "zip": "123"})[1] == "invalid_zip"
    history = Q["address_history"]
    assert clean(history, [{"street": "2 Elm", "date_from": "2020-05-01", "date_to": "2019-01-01"}])[1] == "dates_backwards"
    rows, err = clean(history, [{"street": "2 Elm", "date_from": "2019-01-01", "date_to": "2020-05-01"}, {}])
    assert err == "" and rows == [{"street": "2 Elm", "date_from": "2019-01-01", "date_to": "2020-05-01"}]


def test_missing_required_follows_visibility():
    assert "criminal_cases" not in missing_required(BANK, {"arrested": "No"})
    assert "criminal_cases" in missing_required(BANK, {"arrested": "Yes"})
    assert "a_number" not in missing_required(BANK, {})  # optional


def test_answers_become_the_pipelines_own_fact_keys():
    answers = {"given_name": "Ana Teste", "family_name": "da Silva", "dob": "2007-04-09", "sex": "F",
               "birth_country": "Brasil", "used_other_names": "No", "entry_how": "border",
               "home_address": {"street": "10 Example Rd", "apt": "2", "city": "Testville", "state": "Massachusetts", "zip": "02100"},
               "address_history": [{"street": "5 Old Rd", "city": "Testville", "state": "MA", "date_from": "2019-01-01", "date_to": "2021-01-01"}],
               "arrested": "No", "criminal_cases": [{"what": "hidden"}]}
    facts = {f.fact_key: f.normalized_value for f in answers_to_facts(answers)}
    assert facts["applicant.given_name"] == "ANA TESTE" and facts["applicant.family_name"] == "DA SILVA"
    assert facts["applicant.dob"] == "2007-04-09" and facts["applicant.sex"] == "F"
    assert facts["applicant.country_of_birth"] == "BRAZIL"
    assert facts["applicant.physical_state"] == "MA" and facts["applicant.physical_unit_type"] == "APT"
    assert facts["questionnaire.prior_address1_street"] == "5 OLD RD"
    assert facts["questionnaire.prior_address1_date_from"] == "2019-01-01"
    assert facts["questionnaire.blank.other_names"] == "Yes"
    assert facts["questionnaire.entered_via_border"] == "Yes"
    assert not any(k.startswith("questionnaire.criminal_case") for k in facts)  # hidden questions say nothing


def test_documents_the_answers_call_for():
    def docs(answers):
        return {d["id"]: d for d in required_documents(answers)}

    base = docs({})
    assert [d for d in base if base[d]["required"]] == ["birth_certificate"]  # the one document every client has
    # offered, never blocking: some clients have no passport; the office usually has the I-360 approval
    assert all(base[d]["required"] is False for d in ("passport", "i360_approval", "work_permit", "drivers_license"))
    assert "immigration_court" not in base  # only after a Yes (required) or "not sure" (offered)
    assert docs({"final_order": "Unsure"})["immigration_court"]["required"] is False
    assert "court_disposition" not in base and "visa" not in base and "i94" not in base
    border = docs({"entry_how": "border", "has_i94": "No"})
    assert "visa" not in border and "i94" not in border
    visa = docs({"entry_how": "inspected", "has_i94": "No"})
    assert visa["visa"]["required"] is False and visa["i94"]["required"] is False
    assert docs({"has_i94": "Yes"})["i94"]["required"] is True
    assert docs({"in_proceedings": "Yes"})["immigration_court"]["required"] is True
    assert list(docs({"has_ssn": "Yes"}))[:2] == ["birth_certificate", "ssn_card"]  # required first
    arrested = docs({"arrested": "Yes", "criminal_cases": [{"what": "a"}, {"what": "b"}]})
    assert arrested["court_disposition"]["count"] == 2  # one disposition per case
    assert "ssn_card" in docs({"has_ssn": "Yes"}) and "ssn_card" not in docs({"has_ssn": "No"})
    assert "divorce_decree" in docs({"marital_status": "Married", "times_married": 2})


# --- storage, sign-in -----------------------------------------------------------------


def test_links_work_once_and_only_hashes_are_stored(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    (data / "clients/pilot-1").mkdir()
    store = PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Teste", phone="+1 555 010 0199", email="ana@example.com")
    approve_client(store, "pilot-1")
    with pytest.raises(PermissionError):
        store.new_link_token("pilot-1")
    token = accepted_link(store, "pilot-1")
    assert token not in (store.root / "auth.json").read_text()
    session = store.redeem_link(token)
    assert session and store.session_client(session) == "pilot-1"
    assert session not in (store.root / "auth.json").read_text()
    assert store.redeem_link(token) is None  # once
    store.end_session(session)
    assert store.session_client(session) is None


def test_contacts_and_client_ids(tmp_path):
    store = PortalStore(tmp_path)
    store.add_client("pilot-1", "Ana Teste", phone="+1 (555) 010-0199", email="Ana@Example.com")
    assert store.find_by_contact("+1 (555) 010-0199") == "pilot-1"
    assert store.find_by_contact("5550100199") is None  # country is never inferred from a suffix
    assert store.find_by_contact(" ana@example.com ") == "pilot-1"
    assert store.find_by_contact("0199") is None
    with pytest.raises(LookupError):
        store.client_dir("../etc")


def test_photos_are_stored_as_pdfs_under_random_names(tmp_path):
    store = PortalStore(tmp_path)
    store.add_client("pilot-1", "Ana Teste")
    record = store.add_upload("pilot-1", "passport", "../../IMG_0001.png", _png(), "image/png")
    assert record["stored"].startswith("passport-") and record["stored"].endswith(".pdf")
    assert (tmp_path / "clients" / "pilot-1" / "uploads" / record["stored"]).read_bytes()[:4] == b"%PDF"
    with pytest.raises(ValueError):
        store.add_upload("pilot-1", "passport", "x.exe", b"MZ", "application/octet-stream")


# --- notifications -----------------------------------------------------------------


def test_messages_carry_no_case_details_and_respect_consent(tmp_path, monkeypatch):
    for langs in TEMPLATES.values():
        assert set(langs) == set(LANGS)
    data = installation(tmp_path, monkeypatch)
    (data / "clients/pilot-1").mkdir()
    store = PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Teste", language="es", email="ana@example.com", phone="+15550100199",
                     consent={"email": True, "sms": True, "whatsapp": False})
    approve_client(store, "pilot-1")
    notifier = Notifier(store.root / "outbox.jsonl", env={}, store=store)
    profile = store.profile("pilot-1")
    results = notifier.send(profile, "invite", "https://portal.example/l/abc")
    assert [r["channel"] for r in results] == ["email", "sms", "whatsapp"]
    assert all(row["result"] == "skipped" and row["why"] == "actual_client_signoff_required" for row in results[1:])
    sent = [json.loads(line) for line in notifier.outbox.read_text(encoding="utf-8").splitlines()]
    assert len(sent) == 1 and all("Ana" not in m["body"] for m in sent)
    assert "/l/…" in sent[0]["body"] and "abc" not in sent[0]["body"]
    assert render("invite", "pt", "L")["body"].endswith("L")


def test_the_dry_run_outbox_never_holds_a_working_sign_in_link(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    (data / "clients" / "pilot-1").mkdir(parents=True)  # Real canonical enrollment scaffold.
    store = PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Teste", language="en", email="ana@example.com")
    approve_client(store, "pilot-1")
    profile = store.profile("pilot-1")
    token = "Zq3_secret-token-value-Xy12ab"
    obj = Notifier(store.root / "outbox.jsonl", env={"PORTAL_BASE_URL": "https://portal.example"}, store=store)
    obj.send(profile, "invite", f"https://portal.example/l/{token}")
    outbox = obj.outbox.read_text(encoding="utf-8")
    assert token not in outbox and "https://portal.example/l/…" in outbox
    # tests and local demos only: the whole link, to open it
    obj = Notifier(store.root / "demo.jsonl", env={"PORTAL_OUTBOX_FULL_LINKS": "1", "PORTAL_BASE_URL": "https://portal.example"}, store=store)
    obj.send(profile, "invite", f"https://portal.example/l/{token}")
    import re
    actual = re.search(r"/l/([A-Za-z0-9_-]+)", json.loads(obj.outbox.read_text())["body"])[1]
    assert actual != token and store.redeem_link(actual) is None  # dry-run has no accepted provider proof


def test_mail_checks_the_servers_certificate(tmp_path, monkeypatch):
    import ssl

    from portal import notify

    seen = {}

    class FakeSMTP:
        def __init__(self, host, port, timeout):
            seen["host"] = host

        def __enter__(self):
            return self

        def __exit__(self, *a):
            return False

        def starttls(self, context=None):
            seen["context"] = context

        def send_message(self, msg):
            seen["sent"] = True

    monkeypatch.setattr(notify.smtplib, "SMTP", FakeSMTP)
    data = installation(tmp_path, monkeypatch)
    (data / "clients/pilot-1").mkdir()
    store = PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Teste", language="en", email="ana@example.com")
    approve_client(store, "pilot-1")
    env = {"SMTP_HOST": "smtp.example", "MAIL_FROM": "office@firm.example"}
    assert Notifier(store.root / "unused.jsonl", env=env, store=store).send(store.profile("pilot-1"), "invite", "x")[0]["result"] == "sent"
    context = seen["context"]
    assert isinstance(context, ssl.SSLContext) and context.verify_mode == ssl.CERT_REQUIRED and context.check_hostname


# --- the firm-side task list ------------------------------------------------------------


def test_a_document_that_disagrees_asks_the_client_to_confirm():
    answers = {"has_ssn": "Yes", "ssn": "123-45-6780"}
    result = process_documents("pilot", [("ssn_card-1.pdf", SSN_CARD)], answers=answers_to_facts(answers))
    tasks = client_tasks(answers, [], result.graph, "en")
    confirm = [t for t in tasks if t["kind"] == "confirm"]
    assert len(confirm) == 1 and confirm[0]["question"] == "ssn" and confirm[0]["options"] == ["123-45-6789", "123-45-6780"]
    assert "part9" not in confirm[0]["text"]
    # the client stood by their answer: not asked again (the paralegal sees the conflict)
    assert not [t for t in client_tasks(answers, [], result.graph, "en", {"ssn": "123-45-6780"}) if t["kind"] == "confirm"]
    uploads = [t for t in tasks if t["kind"] == "upload"]
    assert {t["doc_id"] for t in uploads} == {"birth_certificate", "ssn_card"}  # only documents they said they have


def test_confirmations_show_dates_the_way_the_client_writes_them():
    from portal.engine import _shown

    assert _shown("2007-09-04", "pt") == "04/09/2007" and _shown("2007-09-04", "en") == "09/04/2007"
    assert _shown("123-45-6789", "es") == "123-45-6789"


def test_every_question_has_help_and_every_yes_no_allows_not_sure():
    """Answer the client's question before they pick up the phone: no
    question without help, no yes/no without an honest way out."""
    from portal.bank import load_help

    tips = load_help()["questions"]
    for qid, q in Q.items():
        assert q.get("help") or qid in tips, f"{qid}: no help for the client"
        if q["type"] == "yes_no":
            assert [o["value"] for o in q["options"]] == ["Yes", "No", "Unsure"], qid
    answers = {"has_i94": "Unsure", "first_time_in_us": "Unsure"}
    assert {d["id"]: d["required"] for d in required_documents(answers)}["i94"] is False  # offered, not demanded
    assert "other_entries" not in {q["id"] for s in localized(BANK, "en", answers) for q in s["questions"]}


def test_every_section_has_help_and_tips_point_at_real_questions():
    from portal.bank import load_help, localized_faq

    help = load_help()
    assert set(help["sections"]) == {s["id"] for s in BANK["sections"]}
    assert set(help["questions"]) <= set(Q)
    for tip in [*help["questions"].values(), *(s["why"] for s in help["sections"].values())]:
        assert all(tip.get(lang) for lang in LANGS)
    assert len(localized_faq("es")) == len(help["faq"]) > 0
    about = next(s for s in localized(BANK, "pt", {}) if s["id"] == "about")
    assert about["why"] and about["icon"] == "person"
    assert next(q for q in about["questions"] if q["id"] == "a_number")["tip"].startswith("Não sabe?")


def test_legal_questions_never_become_client_tasks():
    answers = {"arrested": "Yes", "criminal_cases": [{"what": "a"}]}
    result = process_documents("pilot", [], answers=answers_to_facts(answers))
    tasks = client_tasks(answers, [], result.graph, "pt")
    assert all(t["kind"] in ("upload", "retake", "info", "confirm") for t in tasks)
    assert not any("part9" in json.dumps(t) for t in tasks)
    assert any(t.get("doc_id") == "court_disposition" for t in tasks)  # the document is asked for, no judgment shown


def test_misfiled_uploads_are_refiled_and_unreadable_ones_retaken():
    uploads = [{"id": "passport-a", "doc_id": "passport", "detected": "ssn_card", "status": "checked"},
               {"id": "birth_certificate-b", "doc_id": "birth_certificate", "detected": "unclassified", "status": "retake"}]
    answers = {"has_ssn": "Yes"}
    result = process_documents("pilot", [], answers=answers_to_facts(answers))
    tasks = client_tasks(answers, uploads, result.graph, "en")
    assert uploads[0]["doc_id"] == "ssn_card"
    kinds = {t["id"]: t for t in tasks}
    assert "moved:passport-a" in kinds and "retake:birth_certificate-b" in kinds
    assert "birth certificate" in kinds["retake:birth_certificate-b"]["text"]
    assert "upload:ssn_card" not in kinds and "upload:passport" not in kinds  # passport: "if you have it"
    assert "still need" not in kinds["moved:passport-a"]["text"]


# --- the web app ------------------------------------------------------------------------


@pytest.fixture
def portal(tmp_path, monkeypatch):
    data = installation(tmp_path, monkeypatch)
    root = data / "portal"
    app = create_app(root=root, base_url="https://portal.example", secure_cookies=False,
                     notifier=Notifier(root / "outbox.jsonl", env={}))
    store = app.state.store
    # Real installed enrollment requires its own case folder before profile
    # publication; approval below must not manufacture that prerequisite later.
    for client_id in ("pilot-1", "pilot-2"):
        (data / "clients" / client_id).mkdir(parents=True)
    store.add_client("pilot-1", "Ana Teste", phone="+15550100199", email="ana@example.com", language="en")
    store.add_client("pilot-2", "Bia Teste", email="bia@example.com")
    approve_client(store, "pilot-1")
    approve_client(store, "pilot-2")
    return TestClient(app), store, root


def _sign_in(client, store, cid="pilot-1"):
    r = client.get(f"/l/{accepted_link(store, cid)}", follow_redirects=False)
    assert r.status_code == 303 and r.headers["location"] == "/"
    return r


H = {"X-Portal": "1"}


def test_page_and_security_headers(portal):
    client, _, _ = portal
    r = client.get("/")
    assert r.status_code == 200 and "<html" in r.text
    assert "frame-ancestors 'none'" in r.headers["content-security-policy"]
    assert r.headers["x-frame-options"] == "DENY" and r.headers["cache-control"] == "no-store"


def test_sign_in_flow(portal):
    client, store, tmp = portal
    assert client.get("/api/me").status_code == 401
    assert client.post("/api/link", json={"contact": "ana@example.com"}, headers=H).json() == {"ok": True}
    assert client.post("/api/link", json={"contact": "nobody@example.com"}, headers=H).json() == {"ok": True}
    outbox = (tmp / "outbox.jsonl").read_text(encoding="utf-8")
    assert outbox.count("https://portal.example/l/") == 1
    r = client.get("/l/not-a-token", follow_redirects=False)
    assert r.headers["location"] == "/?expired=1"
    _sign_in(client, store)
    me = client.get("/api/me").json()
    assert me["first_name"] == "Ana" and me["language"] == "en" and me["progress"] == 0


def test_writes_need_the_portal_header(portal):
    client, store, _ = portal
    _sign_in(client, store)
    assert client.put("/api/answers", json={"dob": "2007-04-09"}).status_code == 403
    assert client.put("/api/answers", json={"dob": "2007-04-09"}, headers=H).status_code == 200


def test_answers_save_validate_and_queue(portal):
    client, store, _tmp = portal
    _sign_in(client, store)
    r = client.put("/api/answers", json={"dob": "2007-04-09", "ssn": "12", "not_a_question": "x"}, headers=H).json()
    assert r["errors"] == {"ssn": "invalid_ssn"} and r["answers"] == {"dob": "2007-04-09"}
    assert store.queued() == ["pilot-1"] and store.profile("pilot-1")["status"] == "started"
    r = client.put("/api/answers", json={"arrested": "Yes"}, headers=H).json()
    assert "criminal_cases" in r["missing"]
    assert any(d["id"] == "court_disposition" for d in r["documents"])
    assert store.answers("pilot-2") == {}  # isolation


def test_every_document_has_an_example_picture(portal):
    client, store, _ = portal
    _sign_in(client, store)
    for d in BANK["documents"]:
        r = client.get(f"/examples/{d['id']}.svg")
        assert r.status_code == 200 and r.headers["content-type"].startswith("image/svg+xml") and b"EXAMPLE" in r.content, d["id"]
        assert b"<script" not in r.content
    docs = client.get("/api/me").json()["documents"]
    assert all(d["example"] == f"/examples/{d['id']}.svg" for d in docs)
    assert client.get("/examples/..%2Fportal.html").status_code == 404
    assert client.get("/examples/passport.exe").status_code == 404


def test_uploads(portal):
    client, store, _ = portal
    _sign_in(client, store)
    files = {"file": ("photo.png", _png(), "image/png")}
    r = client.post("/api/upload", data={"doc_id": "passport"}, files=files, headers=H)
    assert r.status_code == 200
    passport = next(d for d in r.json()["documents"] if d["id"] == "passport")
    assert passport["uploads"] and passport["uploads"][0]["name"] == "photo.png"
    bad = client.post("/api/upload", data={"doc_id": "passport"}, files={"file": ("x.png", b"MZ\x90\x00", "image/png")}, headers=H)
    assert bad.status_code == 415
    for name, kind, head in (("x.jpg", "image/jpeg", b"\xff\xd8\xff\xe0"), ("x.png", "image/png", b"\x89PNG\r\n\x1a\n")):  # a photo's first bytes, then junk
        fake = client.post("/api/upload", data={"doc_id": "passport"}, files={"file": (name, head + b"junk" * 50, kind)}, headers=H)
        assert fake.status_code == 415 and fake.json()["detail"] == "That image can't be opened. Please add a clear photo or a PDF."
    assert client.post("/api/upload", data={"doc_id": "nope"}, files=files, headers=H).status_code == 400


def test_confirm_task_becomes_the_answer(portal):
    client, store, _ = portal
    _sign_in(client, store)
    store.save_tasks("pilot-1", [{"id": "confirm:sex", "kind": "confirm", "question": "sex", "text": "?", "options": ["F", "M"]}])
    assert client.post("/api/task", json={"task": "confirm:sex", "choice": "X"}, headers=H).status_code == 400
    r = client.post("/api/task", json={"task": "confirm:sex", "choice": "F"}, headers=H).json()
    assert r["answers"]["sex"] == "F" and r["tasks"] == []
    assert store.profile("pilot-1")["confirmed"] == {"sex": "F"}


def test_submit_needs_every_answer_and_an_attestation(portal):
    client, store, tmp = portal
    _sign_in(client, store)
    assert client.post("/api/submit", json={"agree": True, "signature": "Ana Teste"}, headers=H).status_code == 400
    everything = {qid: "x" for qid in missing_required(BANK, {})}
    store.save_answers("pilot-1", everything)
    while missing_required(BANK, store.answers("pilot-1")):  # follow-ups opened by the placeholder answers
        store.save_answers("pilot-1", {qid: "x" for qid in missing_required(BANK, store.answers("pilot-1"))})
    assert client.post("/api/submit", json={"agree": False, "signature": "Ana Teste"}, headers=H).status_code == 400
    r = client.post("/api/submit", json={"agree": True, "signature": "Ana Teste"}, headers=H)
    assert r.status_code == 200 and r.json()["status"] == "submitted"
    assert store.profile("pilot-1")["attestation"]["typed_name"] == "Ana Teste"
    assert client.put("/api/answers", json={"dob": "2007-04-09"}, headers=H).status_code == 409
    assert "We received your questionnaire" in (tmp / "outbox.jsonl").read_text(encoding="utf-8")


def test_sign_in_requests_are_rate_limited_without_telling(portal):
    client, _, tmp = portal
    for _ in range(8):
        assert client.post("/api/link", json={"contact": "ana@example.com"}, headers=H).json() == {"ok": True}
    assert (tmp / "outbox.jsonl").read_text(encoding="utf-8").count("/l/") == 5


@pytest.mark.parametrize("trusted, separate", [(None, False), ("testclient", True), ("1", True), ("10.0.0.9", False)])
def test_the_link_limit_is_per_client_behind_a_trusted_proxy_only(tmp_path, monkeypatch, trusted, separate):
    """Behind a reverse proxy every request comes from the proxy: PORTAL_TRUSTED_PROXY says whose
    X-Forwarded-For to believe. Without it the header is ignored (anyone can write one)."""
    if trusted:
        monkeypatch.setenv("PORTAL_TRUSTED_PROXY", trusted)
    else:
        monkeypatch.delenv("PORTAL_TRUSTED_PROXY", raising=False)
    data = installation(tmp_path, monkeypatch)
    root = data / "portal"
    client = TestClient(create_app(root=root, base_url="https://portal.example", secure_cookies=False,
                                   notifier=Notifier(root / "outbox.jsonl", env={})))
    for _ in range(30):
        assert client.get("/l/nope", headers={"X-Forwarded-For": "203.0.113.5"}, follow_redirects=False).status_code == 303
    assert client.get("/l/nope", headers={"X-Forwarded-For": "203.0.113.5"}, follow_redirects=False).status_code == 429
    other = client.get("/l/nope", headers={"X-Forwarded-For": "198.51.100.20, 203.0.113.5"}, follow_redirects=False).status_code
    assert other == (303 if separate else 429)  # another client (the first address) has its own limit only when the proxy is trusted


# --- every I-485 box a client can answer is asked, and lands on the form ----------------


def _filled(answers, tmp_path, documents=()):
    from pathlib import Path

    from pypdf import PdfReader

    from batch import ClientResult, finalize_client
    from fill import load_field_map
    from rules import ALL_RULES
    from rules.policy import load_policy_profile

    repo = Path(__file__).resolve().parent.parent
    result = process_documents("pilot", list(documents), rules=ALL_RULES, answers=answers_to_facts(answers),
                               policies=load_policy_profile(schema_path.path("law", "policy_sijs", schema_path.schemas_in(repo))))
    filing = finalize_client(ClientResult(graph=result.graph, review_flags=result.review_flags), schema_path.path("template", "i485", schema_path.schemas_in(repo)),
                             load_field_map(schema_path.path("field_map", "i485", schema_path.schemas_in(repo))), [], tmp_path)
    fields = PdfReader(str(filing.filled_pdf_path)).get_fields()
    value = lambda leaf: next((str(f.get("/V")) for name, f in fields.items() if name.endswith(leaf)), None)
    return result.graph, filing, value


def test_follow_up_answers_reach_their_boxes(tmp_path):
    answers = {"other_dob": "Yes", "other_dobs": [{"date": "2006-04-09"}], "other_a_number": "Yes", "other_a_numbers": "A 012-345-678",
               "uscis_account": "000000000000", "phone": "+15550100199", "email": "ana@example.com",
               "entry_how": "border", "applied_visa_abroad": "Yes",
               "visa_abroad": {"city": "Testville", "country": "Brasil", "decision": "REFUSED", "date": "2015-03-02"},
               "marital_status": "Divorced", "times_married": 1,
               "prior_marriages": [{"name": "Pedro Example Sample", "dob": "2000-01-02", "birth_country": "Brasil", "citizenship": "Brasil",
                                    "date_married": "2019-05-06", "date_ended": "2021-07-08", "ended_city": "Testville", "ended_state": "MA",
                                    "ended_country": "USA", "how_ended": "Divorced"}],
               "org_member": "Yes", "organizations": [{"name": "Youth Group Example", "city": "Testville", "country": "Brasil", "nature": "Religious group",
                                                       "involvement": "Member", "date_from": "2018-01-01"}]}
    graph, _filing, value = _filled(answers, tmp_path)
    assert value("Pt1Line3A_OtherDOB[0]") == "04/09/2006"
    assert value("Pt1Line5A_ANumber[0]") == "012345678"
    assert value("Pt1Line9_USCISAccountNumber[0]") == "000000000000"
    assert value("Pt2Line11_CB[2]") == "/11C"  # Part 1 item 11: came in without admission or parole
    assert graph.get("applicant.part9.pt9line75").value == "Yes"  # item 73: never the firm's default "No" for a border crossing
    assert (value("Pt4Line2_CityTown[0]"), value("Pt4Line3_Decision[0]"), value("Pt4Line4_Date[0]")) == ("TESTVILLE", "REFUSED", "03/02/2015")
    assert (value("Pt6Line12_GivenName[0]"), value("Pt6Line19_MaritalStatus[3]")) == ("PEDRO", "/2")  # divorced
    assert value("Pt6Line16_DateofBirth[1]") == "07/08/2021"
    assert (value("Pt9Line2_Organization1[0]"), value("Pt9Line4_Involvement[0]")) == ("YOUTH GROUP EXAMPLE", "MEMBER")
    assert (value("Pt3Line3_DaytimePhoneNumber1[0]"), value("Pt3Line5_Email[0]")) == ("5550100199", "ana@example.com")


def test_part9_checklists_replace_the_firms_default_no(tmp_path):
    from portal.bank import YES_PREFIX

    screening = {q["id"]: {"none": True} for q in Q.values() if q["type"] == "checklist"}
    screening["s_armed"] = {"selected": ["military"]}  # required military service, say
    screening["s_vice"] = "Unsure"
    graph, filing, _value = _filled({**screening, "arrested": "No", "committed_crime": "No", "org_member": "No"}, tmp_path)
    drugs = graph.get("applicant.part9.pt8line26")
    assert drugs.value == "No" and all(s.doc_id == PORTAL_DOC_ID for s in drugs.sources)  # the client's own No, not the firm default
    assert graph.get("applicant.part9.pt8line50") is None or graph.get("applicant.part9.pt8line50").status != "resolved"
    assert graph.get(YES_PREFIX + "applicant.part9.pt8line50")
    assert graph.get("applicant.part9.pt8line43a").value == "No"  # same checklist, not ticked
    assert graph.get("applicant.part9.pt8line30") is None or graph.get("applicant.part9.pt8line30").status != "resolved"
    messages = " | ".join(f.message for f in filing.flags)
    assert "CLIENT TICKED" in messages and "military or police unit" in messages and "Item 50" in messages
    assert "CLIENT NOT SURE" in messages and "prostitution" in messages


def test_client_wording_is_plain_language():
    """Clients aren't lawyers: no immigration/legal jargon in what they read
    unless it's explained right there (the official term in parentheses or
    quotes, after everyday words)."""
    import re

    JARGON = r"\b(alien|nonimmigrant|rescind\w*|adjudicat\w*|inadmissib\w*|authoriz\w*|admission|admitted|inspected|petition\w*|biographic|deceased|" \
             r"reinstat\w*|removal|proceedings|eligib\w*|derivative|beneficiary|disposition|docket|conveyance|procur\w*|abet\w*|totalitarian|immunity)\b"
    texts = []
    for s in BANK["sections"]:
        texts.append(("section " + s["id"], s["title"]["en"]))
        for q in s["questions"]:
            texts.append((q["id"], q["label"]["en"]))
            texts += [(f"{q['id']}/{o['value']}", o["label"]["en"]) for o in q.get("options", [])]
            texts += [(f"{q['id']}.{f['id']}", f["label"]["en"]) for f in q.get("fields", [])]
    texts += [("doc " + d["id"], d["label"]["en"]) for d in BANK["documents"]]
    unexplained = []
    for where, text in texts:
        bare = re.sub(r"\([^)]*\)|“[^”]*”|\"[^\"]*\"", "", text)
        hits = re.findall(JARGON, bare, flags=re.IGNORECASE)
        if hits:
            unexplained.append((where, hits, text))
    assert not unexplained, "\n".join(f"{w}: {h}: {t}" for w, h, t in unexplained)


def test_every_checklist_option_stands_on_its_own():
    """A client reads each line by itself: no "those things" pointing at the line above."""
    import re

    for q in Q.values():
        for o in q.get("options", []):
            for lang in LANGS:
                assert not re.search(r"those things|any of those|dessas coisas|essas coisas|esas cosas|de esas", o["label"][lang], re.IGNORECASE), (q["id"], o["value"], lang)


def test_the_demo_client_is_complete_and_its_documents_read():
    """The demonstration client (src/portal/demo.py): invented, complete, and
    its fake documents are recognized like real ones."""
    from classify import classify_text
    from extract.passport import read_mrz
    from portal import demo

    answers = demo.answers()
    assert not missing_required(BANK, answers)
    assert answers["ssn"] == "123-45-6780"  # the planted one-digit mismatch with the SSN card
    for doc_id, (_, lines) in demo.DOCUMENTS.items():
        assert classify_text("\n".join(lines)).doc_type == {"passport": "passport", "birth_certificate": "birth_certificate",
                                                           "i360_approval": "i360_approval", "i94": "i94", "ssn_card": "ssn_card"}[doc_id], doc_id
        assert any("EXEMPLO" in line for line in lines)  # labelled as a demonstration document
    mrz = read_mrz("\n".join(demo.DOCUMENTS["passport"][1]))
    assert mrz and mrz["number"] == "XX0001234" and mrz["issuer"] == "BRAZIL"  # not "SSA" from "PASSAPORTE"


def test_the_office_asks_and_the_client_answers(portal, monkeypatch):
    """'Ask the client' from the review app -> a task in the portal + a
    message with only a sign-in link -> the client's reply or upload
    answers it, and the worker's task rebuild can't erase it."""
    client, store, _tmp = portal
    text = "Please tell us your father's date of birth."
    request = store.add_request("pilot-1", text, None, "Jane", typed={"language": "en", "text_client": text, "type": "text"})
    text = "Please send your I-94."
    document_request = store.add_request("pilot-1", text, "i94", "Jane", typed={"language": "en", "text_client": text, "type": "text"})
    review_request(store, "pilot-1", request)
    review_request(store, "pilot-1", document_request)
    _sign_in(client, store)
    me = client.get("/api/me").json()
    asked = [t for t in me["tasks"] if t["kind"] == "request"]
    assert [t["text"] for t in asked] == ["Please tell us your father's date of birth.", "Please send your I-94."]
    assert any(d["id"] == "i94" and d["required"] for d in me["documents"])  # asked for, so listed even if not otherwise needed
    store.save_tasks("pilot-1", [])  # the worker rebuilds tasks.json; requests live apart
    assert client.post("/api/request-reply", json={"request": request["id"], "reply": ""}, headers=H).status_code == 400
    r = client.post("/api/request-reply", json={"request": request["id"], "reply": "03/04/1970"}, headers=H).json()
    assert [t["text"] for t in r["tasks"] if t["kind"] == "request"] == ["Please send your I-94."]
    client.post("/api/upload", data={"doc_id": "i94"}, files={"file": ("i94.png", _png(), "image/png")}, headers=H)
    statuses = {x["text"]: (x["status"], x.get("reply"), bool(x.get("upload"))) for x in store.requests("pilot-1")}
    assert statuses == {"Please tell us your father's date of birth.": ("answered", "03/04/1970", False),
                        "Please send your I-94.": ("answered", None, True)}


def test_the_review_app_preserves_unreviewed_request_without_notifying_client(tmp_path, monkeypatch):
    from pathlib import Path

    from portal.store import PortalStore
    from review.server import ReviewApp

    repo = Path(__file__).resolve().parent.parent
    data = installation(tmp_path, monkeypatch)
    (data / "clients" / "pilot-1").mkdir()
    store = PortalStore(data / "portal")
    store.add_client("pilot-1", "Ana Teste", email="ana@example.com", language="pt")
    auth_before = (store.root / "auth.json").read_bytes()
    app = ReviewApp(data / "clients", schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None, store.root)
    with pytest.raises(ValueError):
        app.ask_client("pilot-1", {"text": "Please send your passport", "reviewer": ""})  # who asked is recorded
    out = app.ask_client("pilot-1", {"text": "Please send your passport", "doc_id": "passport", "reviewer": "Jane"})
    assert out["request"]["doc_id"] == "passport" and app.client_requests("pilot-1")[0]["by"] == "Jane"
    assert out["delivery"]["status"] == "none"
    assert store.requests("pilot-1")[0]["language_hold"]
    assert not (store.root / "outbox.jsonl").exists()
    assert (store.root / "auth.json").read_bytes() == auth_before
    with pytest.raises(ValueError):
        app.ask_client("someone-else", {"text": "x", "reviewer": "Jane"})  # not in the portal: can't be reached


def test_a_task_follows_the_language_the_client_switches_to():
    """Tasks are written once, at processing, but the client may switch
    language afterwards: the task's words switch with the page."""
    from portal.app import _in_language
    from portal.engine import _texts

    words = {"pt": "Qual está certo?", "es": "¿Cuál es correcto?", "en": "Which is right?", "ht": "Kilès ki kòrèk?"}
    task = {"id": "confirm:ssn", "kind": "confirm", **_texts(lambda lg: words[lg], "pt"),
            "labels": ["01/02/2006"], "labels_by_lang": {"pt": ["02/01/2006"], "es": ["02/01/2006"], "en": ["01/02/2006"]}}
    assert task["text"] == "Qual está certo?"
    assert _in_language(task, "en")["text"] == "Which is right?" and _in_language(task, "en")["labels"] == ["01/02/2006"]
    assert _in_language(task, "es")["labels"] == ["02/01/2006"]
    assert _in_language({"id": "old", "text": "made before"}, "en")["text"] == "made before"  # tasks saved before the change


def test_the_review_app_phone_reminder_remains_held_until_ev8(tmp_path, monkeypatch):
    from pathlib import Path

    from portal.store import PortalStore
    from review.server import ReviewApp

    repo = Path(__file__).resolve().parent.parent
    data = installation(tmp_path, monkeypatch)
    (data / "clients/pilot-2").mkdir()
    store = PortalStore(data / "portal")
    store.add_client("pilot-2", "Bia Teste", phone="+15550100199", email="bia@example.com", language="pt",
                     consent={"email": False, "sms": True, "whatsapp": False})
    approve_client(store, "pilot-2", channel="sms")
    auth_before = (store.root / "auth.json").read_bytes()
    app = ReviewApp(data / "clients", schema_path.path("field_map", "i485", schema_path.schemas_in(repo)), schema_path.path("template", "i485", schema_path.schemas_in(repo)), None, store.root)
    with pytest.raises(ValueError):
        app.remind("pilot-2", {"reviewer": ""})  # who sent it is recorded
    out = app.remind("pilot-2", {"reviewer": "Jane"})
    assert all(s["result"] == "skipped" for s in out["sent"])
    assert next(s for s in out["sent"] if s["channel"] == "sms")["why"] == "contact_control_required"
    assert out["delivery"]["status"] == "none"
    assert not (store.root / "outbox.jsonl").exists()
    assert (store.root / "auth.json").read_bytes() == auth_before
    # Current staff adapter records an attempt timestamp even when withheld;
    # it is not accepted delivery and awaits the protected EV7 server contract.
    store.update_profile("pilot-2", status="submitted")
    with pytest.raises(ValueError):
        app.remind("pilot-2", {"reviewer": "Jane"})  # nothing left to remind her of
