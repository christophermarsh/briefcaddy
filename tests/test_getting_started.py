"""The Getting started page (src/getting_started.py): every Done state is read from the firm's data, never ticked."""

from __future__ import annotations

import json
import re
import threading
import urllib.error
import urllib.request
from datetime import date, datetime, timezone

import pytest

import backups
import clock
import deployment
import getting_started
import maintenance
import settings
import second_factor
from review.auth import Accounts
from review.server import ReviewApp, make_handler, serve
from test_review import _REPO, TEMPLATE, client  # noqa: F401 -- the review app's test client
import schema_path

PASSWORD = "a long enough passphrase"  # secret-scan: allow (a made-up test password)


@pytest.fixture
def firm(tmp_path, monkeypatch):
    """The firm's files in a scratch folder, and the clock on 10/05/2026."""
    monkeypatch.setattr(settings, "PATH", tmp_path / "settings.json")
    monkeypatch.setattr(maintenance, "FIRM_LOG", tmp_path / "maintenance_log.json")
    monkeypatch.setattr(maintenance, "LAST_LIVE", tmp_path / "live.json")
    monkeypatch.setattr(backups, "LOG", tmp_path / "backup_log.json")
    monkeypatch.setattr(deployment, "PATH", tmp_path / "deployment.json")
    monkeypatch.setattr(clock, "_now_override", datetime(2026, 10, 5, 15, 0, tzinfo=timezone.utc))
    return tmp_path


def _app(tmp_path, accounts=True):
    data = tmp_path / "data" / "clients"
    data.mkdir(parents=True, exist_ok=True)
    users = Accounts(tmp_path / "data" / "users.json") if accounts else None
    return ReviewApp(data, schema_path.path("field_map", "i485"), TEMPLATE, None, accounts=users, portal_root=tmp_path / "data" / "portal")


def _item(g, id_):
    return next(i for i in g["items"] if i["id"] == id_)


def _attorney(app, email="sam@firm.example", name="Sam Exemplo"):
    app.accounts.create_first(email, name, PASSWORD, app.accounts.new_setup_code())
    return next(u for u in app.accounts.users() if u["email"] == email)


def test_a_new_installation_has_nothing_done_and_says_what_to_do(firm):
    app = _app(firm)
    sam = _attorney(app)
    g = getting_started.build(app, sam, date(2026, 10, 5))
    assert [i["id"] for i in g["items"]] == ["firm", "visa_bulletin", "fees", "translators", "staff", "second_factor", "first_client", "backups", "clio"]
    assert g["done"] == 0 and g["total"] == 8 and g["first_visit"]
    assert {i["id"]: i["optional"] for i in g["items"]} == {"firm": False, "visa_bulletin": False, "fees": False, "translators": False, "staff": False,
                                                          "second_factor": False, "first_client": False, "backups": False, "clio": True}
    assert _item(g, "second_factor")["detail"] == "0 of 1 attorney has set it up. It is asked for the first time each person signs in."
    assert "October 2026" in _item(g, "visa_bulletin")["detail"] and "Only one person can sign in" in _item(g, "staff")["detail"]
    assert "Nothing is backing up the data yet" in _item(g, "backups")["detail"]
    assert all(i["open"]["to"] in ("settings", "staff", "add_client", "maintenance") for i in g["items"])


def test_the_installers_settings_do_not_count_as_the_attorney_confirming_them(firm):
    app = _app(firm)
    sam = _attorney(app)
    (firm / "settings.json").write_text(json.dumps({"firm": {"values": {"firm.business_name": "Exemplo"}, "updated_by": "Installer", "updated_at": "2026-10-01T09:00:00-04:00"}}), encoding="utf-8")
    assert not _item(getting_started.build(app, sam, date(2026, 10, 5)), "firm")["done"]
    settings.save("firm", {"firm.business_name": "Exemplo & Associates"}, "Sam Exemplo")  # a name alone is not the office's details
    item = _item(getting_started.build(app, sam, date(2026, 10, 5)), "firm")
    assert not item["done"] and item["detail"].startswith("The office's details are not saved yet: Settings, Main office.")
    settings.save("firm", {"firm.business_name": "Exemplo & Associates", "firm.preparer_family_name": "EXEMPLO", "firm.attorney_bar_number": "123456",
                           "firm.street": "1 EXAMPLE ST", "firm.city": "SOMERVILLE", "firm.zip": "02143", "firm.phone": "6175550100"}, "Sam Exemplo")
    item = _item(getting_started.build(app, sam, date(2026, 10, 5)), "firm")
    assert item["done"] and item["detail"] == "Saved by Sam Exemplo on 10/05/2026."


def test_each_item_turns_done_when_the_data_says_so(firm):
    app = _app(firm)
    sam = _attorney(app)
    when = date(2026, 10, 5)
    settings.save("visa_bulletin_eb4", {"month": "October 2026"}, "Sam Exemplo")
    assert _item(getting_started.build(app, sam, when), "visa_bulletin")["done"] and _item(getting_started.build(app, sam, when), "visa_bulletin")["detail"] == "Set for October 2026."
    settings.save("visa_bulletin_eb4", {"month": "September 2026"}, "Sam Exemplo")  # last month's: the finding says what it should be
    stale = _item(getting_started.build(app, sam, when), "visa_bulletin")
    assert not stale["done"] and "September 2026" in stale["detail"] and "October 2026" in stale["detail"]

    assert not _item(getting_started.build(app, sam, when), "fees")["done"]
    settings.save("fees", {}, "Sam Exemplo")  # opened and saved as is: confirmed
    assert _item(getting_started.build(app, sam, when), "fees")["detail"] == "Confirmed by Sam Exemplo on 10/05/2026."

    assert not _item(getting_started.build(app, sam, when), "translators")["done"]
    settings.add_translator("Sam Exemplo", "Maria Exemplo", ["pt"], "outside")
    assert _item(getting_started.build(app, sam, when), "translators")["detail"] == "1 on the list."

    app.accounts.add("jane@firm.example", "Jane Exemplo", "paralegal")
    assert _item(getting_started.build(app, sam, when), "staff")["done"]
    app.accounts.update("jane@firm.example", active=False)  # a turned-off account is not a person who can sign in
    assert not _item(getting_started.build(app, sam, when), "staff")["done"]

    assert not _item(getting_started.build(app, sam, when), "first_client")["done"]
    (app.data_root / "case-ana").mkdir()
    assert _item(getting_started.build(app, sam, when), "first_client")["detail"] == "1 client in the system."
    (firm / "data" / "portal" / "clients" / "invited-only").mkdir(parents=True)  # invited to the portal, not processed yet: counts
    assert _item(getting_started.build(app, sam, when), "first_client")["detail"] == "2 clients in the system."


def test_the_fee_check_in_keeping_current_also_counts(firm):
    app = _app(firm)
    sam = _attorney(app)
    reg = firm / "register.json"
    reg.write_bytes(maintenance.REGISTRY.read_bytes())
    maintenance.mark("fee_schedule", "Paulo Exemplo", path=reg, today=date(2026, 10, 4), log_path=maintenance.FIRM_LOG)
    assert _item(getting_started.build(app, sam, date(2026, 10, 5)), "fees")["detail"] == "Marked checked on 10/04/2026."


def test_the_backup_item_follows_the_backup_log_and_is_not_there_when_the_provider_runs_the_backups(firm):
    app = _app(firm)
    sam = _attorney(app)
    when = date(2026, 10, 5)
    (firm / "backup_log.json").write_text(json.dumps({"last_backup": {"at": "2026-10-05T02:00:00-04:00"}, "last_test_restore": {"at": "2026-09-28T09:00:00-04:00"}}), encoding="utf-8")
    item = _item(getting_started.build(app, sam, when), "backups")
    assert item["done"] and item["detail"] == "Last backup 10/05/2026; last test restore 09/28/2026, 7 days ago."
    (firm / "backup_log.json").write_text(json.dumps({"last_backup": {"at": "2026-09-20T02:00:00-04:00"}}), encoding="utf-8")
    item = _item(getting_started.build(app, sam, when), "backups")
    assert not item["done"] and "15 days ago: overdue" in item["detail"] and item["open"]["to"] == "maintenance"
    (firm / "deployment.json").write_text(json.dumps({"mode": "hosted", "provider": {"name": "Acme", "email": "s@acme.example"}}), encoding="utf-8")
    assert "backups" not in [i["id"] for i in getting_started.build(app, sam, when)["items"]]


def test_clio_is_optional_and_staff_and_the_second_factor_show_only_when_there_are_accounts(firm):
    app = _app(firm, accounts=False)
    g = getting_started.build(app, None, date(2026, 10, 5))
    ids = [i["id"] for i in g["items"]]
    assert "staff" not in ids and "second_factor" not in ids and _item(g, "clio")["optional"] and g["total"] == 6 and not g["first_visit"]


def test_the_second_factor_line_counts_the_attorneys_who_set_the_app_up_or_everyone_when_the_firm_says_so(firm):
    app = _app(firm)
    sam = _attorney(app)
    when = date(2026, 10, 5)
    assert app.accounts.second_factor_summary() == (0, 1)
    assert not _item(getting_started.build(app, sam, when), "second_factor")["done"]
    app.accounts.change_password("sam@firm.example", app.accounts.reset("sam@firm.example"), PASSWORD)
    second_factor.set_up(app.accounts, "sam@firm.example", PASSWORD)
    app.accounts.add("jane@firm.example", "Jane Exemplo", "paralegal")  # a paralegal is not asked unless the firm says everyone
    assert app.accounts.second_factor_summary() == (1, 1)
    item = _item(getting_started.build(app, sam, when), "second_factor")
    assert item["done"] and item["detail"] == "The attorney has set it up."
    settings.save("sign_in", {"code_everyone": "yes"}, "Sam Exemplo")  # require a code for everyone: Jane has not set hers up
    assert app.accounts.second_factor_summary() == (1, 2)
    item = _item(getting_started.build(app, sam, when), "second_factor")
    assert not item["done"] and item["detail"].startswith("1 of 2 people have set it up")
    app.accounts.update("jane@firm.example", active=False)  # a turned-off account is not owed a code
    assert app.accounts.second_factor_summary() == (1, 1)


def test_the_firms_due_items_are_listed_under_also_due_without_repeating_the_ones_above(firm):
    app = _app(firm)
    sam = _attorney(app)
    g = getting_started.build(app, sam, date(2026, 10, 5))
    ids = {x["id"] for x in g["also_due"]}
    assert "visa_bulletin_family" in ids and "journey_rules" in ids  # never checked by this firm
    assert not ids & getting_started.COVERED and all(x["what"] for x in g["also_due"])
    assert next(x for x in g["also_due"] if x["id"] == "visa_bulletin_family")["settings"] == "visa_bulletin_family"
    assert "{provider}" not in json.dumps(g)


def test_first_visit_is_once_per_attorney_and_never_for_a_paralegal(firm):
    app = _app(firm)
    sam = _attorney(app)
    app.accounts.add("jane@firm.example", "Jane Exemplo", "paralegal")
    jane = next(u for u in app.accounts.users() if u["email"] == "jane@firm.example")
    assert getting_started.first_visit(sam) and not getting_started.first_visit(jane) and not getting_started.first_visit(None)
    assert not getting_started.first_visit(sam | {"must_change": True})
    getting_started.mark_seen(sam)
    assert not getting_started.first_visit(sam)
    app.accounts.add("pat@firm.example", "Pat Exemplo", "attorney")
    pat = next(u for u in app.accounts.users() if u["email"] == "pat@firm.example")
    assert getting_started.first_visit(pat | {"must_change": False})  # a second attorney has a first sign-in of their own


def test_what_the_page_says_follows_the_on_screen_rules(firm):
    app = _app(firm)
    sam = _attorney(app)
    text = json.dumps(getting_started.build(app, sam, date(2026, 10, 5)), ensure_ascii=False)
    assert "—" not in text and " -- " not in text
    assert not re.search(r"\.(json|py|db|pdf)\b|/api/|src/|schemas/|tools/", text), re.findall(r"[^ ]*\.(?:json|py|db|pdf)\b", text)


# -- over HTTP ---------------------------------------------------------------------------------------------------------

@pytest.fixture
def server(firm):
    app = _app(firm)
    for email, name, role in (("sam@firm.example", "Sam Exemplo", "attorney"), ("jane@firm.example", "Jane Exemplo", "paralegal")):
        app.accounts.change_password(email, app.accounts.add(email, name, role), PASSWORD)
    second_factor.set_up(app.accounts, "sam@firm.example", PASSWORD)  # the attorney's authenticator app
    httpd = serve(app, 0)
    port = httpd.server_address[1]
    httpd.RequestHandlerClass = make_handler(app, port)
    threading.Thread(target=httpd.serve_forever, daemon=True).start()
    yield f"http://127.0.0.1:{port}"
    httpd.shutdown()


def _call(url, body=None, cookie=None):
    headers = {"Content-Type": "application/json", "X-Review-App": "1"} | ({"Cookie": cookie} if cookie else {})
    req = urllib.request.Request(url, data=json.dumps(body).encode() if body is not None else None, headers=headers, method="POST" if body is not None else "GET")
    try:
        with urllib.request.urlopen(req) as r:
            return r.status, json.loads(r.read() or b"{}"), r.headers.get("Set-Cookie")
    except urllib.error.HTTPError as e:
        return e.code, json.loads(e.read() or b"{}"), None


def _in(base, email):
    status, body, cookie = _call(base + "/api/login", {"email": email, "password": PASSWORD})
    cookie = cookie.split(";")[0]
    return second_factor.finish(base, email, cookie) if body["user"].get("second_factor") == "code" else cookie  # an attorney gives a code too


def test_the_attorney_sees_it_once_on_first_sign_in_and_the_paralegal_never(server):
    sam, jane = _in(server, "sam@firm.example"), _in(server, "jane@firm.example")
    assert _call(server + "/api/me", cookie=sam)[1]["first_sign_in"] is True
    status, g, _ = _call(server + "/api/getting-started", cookie=sam)
    assert status == 200 and g["total"] == 8 and g["first_visit"]
    assert _call(server + "/api/getting-started", {}, cookie=sam)[0] == 200  # the page records that it was shown
    assert "first_sign_in" not in _call(server + "/api/me", cookie=sam)[1] and _call(server + "/api/getting-started", cookie=sam)[1]["first_visit"] is False
    assert "first_sign_in" not in _call(server + "/api/me", cookie=jane)[1]
    status, err, _ = _call(server + "/api/getting-started", cookie=jane)
    assert status == 403 and "attorney's" in err["error"]
    assert _call(server + "/api/getting-started", {}, cookie=jane)[0] == 403
    assert _call(server + "/api/getting-started")[0] == 401  # nobody signed in
