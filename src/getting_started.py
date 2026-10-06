"""The "Getting started" page: what an attorney does on the first day, and where each thing stands.

Every item's Done state is read from the firm's own data (the Settings file, the staff accounts, the case folders, the
backup log), never ticked by hand: it turns Done when the thing is true. The page opens by itself on an attorney's first
sign-in (the screen asks /api/me; "seen" below remembers who has been shown it) and stays under Settings.

The installer fills in the firm's name and time zone so the first screens read right; it records them as made by
"Installer", and that does not count as the attorney confirming the firm's details (INSTALLER below).

An item that is not there in this installation is left out, not shown as undone: staff accounts when the app runs without
accounts, the second factor when this version has none, the backup item when the provider runs the backups.
"""

from __future__ import annotations

import json
from datetime import date
from pathlib import Path
from typing import Any

import backups
import clock
import deployment
import maintenance
import settings

INSTALLER = "Installer"
# register items that already have their own line above; every other due item of the firm's is listed under "Also due"
COVERED = {"visa_bulletin", "firm_details", "backups", "clio_app_credentials", "fee_schedule"}
# where a firm's register item is changed (the same places Keeping current links to)
WHERE = {"visa_bulletin_family": "visa_bulletin_family", "eoir_fees": "fees", "poverty_guidelines": "poverty", "payment_rules": "payment"}


def _path() -> Path:
    return settings.PATH.parent / "getting_started.json"


def _seen() -> dict[str, str]:
    try:
        return json.loads(_path().read_text(encoding="utf-8")).get("seen") or {}
    except (OSError, ValueError):
        return {}


def first_visit(user: dict[str, Any] | None) -> bool:
    """True for an attorney who has not been shown the page yet (the screen then opens it once)."""
    return bool(user) and user.get("role") == "attorney" and not user.get("must_change") and user["email"] not in _seen()


def mark_seen(user: dict[str, Any] | None) -> None:
    email = (user or {}).get("email")
    if not email:
        return
    seen = _seen()
    seen[email] = clock.stamp("seconds")
    path = _path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"seen": seen}, indent=1) + "\n", encoding="utf-8")


def _mdy(stamp: Any) -> str:
    d = clock.local_date(stamp)
    return f"{d.month:02d}/{d.day:02d}/{d.year}" if d else "an earlier date"


def _saved(section: str) -> dict[str, Any] | None:
    """The Settings section as the firm last saved it, unless the installer was the one."""
    mine = settings.load().get(section) or {}
    return mine if mine.get("updated_by") and mine["updated_by"] != INSTALLER else None


def _case_count(app: Any) -> int:
    names = {p.name for p in app.data_root.iterdir() if p.is_dir()} if app.data_root.is_dir() else set()
    if app.portal_root and (Path(app.portal_root) / "clients").is_dir():
        names |= {p.name for p in (Path(app.portal_root) / "clients").iterdir() if p.is_dir()}
    return len(names)


def build(app: Any, user: dict[str, Any] | None, today: date | None = None) -> dict[str, Any]:
    """{"items": [...], "done", "total", "also_due": [...], "first_visit"}. Each item: id, title, detail (one plain sentence), done,
    optional, open ({"to": "settings"|"staff"|"add_client"|"maintenance", "focus": ...})."""
    today = today or clock.today()
    items: list[dict[str, Any]] = []

    def add(id_: str, title: str, done: bool, detail: str, to: str, focus: str | None = None, optional: bool = False) -> None:
        items.append({"id": id_, "title": title, "done": done, "optional": optional, "detail": detail, "open": {"to": to, "focus": focus}})

    firm = _saved("firm")
    offices = len(settings.offices_saved())
    complete = bool(firm) and settings.details_saved()  # the attorney's name, bar number, address and phone are all there
    add("firm", "The firm's details and offices", complete,
        (f"Saved by {firm['updated_by']} on {_mdy(firm.get('updated_at'))}" + (f", with {offices} more office{'s' if offices != 1 else ''}." if offices else "."))
        if complete else "The office's details are not saved yet: Settings, Main office. The attorney's name, bar number, address, phone and who signs the letters: "
                         "until they are there, every form and cover letter leaves them blank. Add each other office too.",
        "settings", "firm")

    status = {i["id"]: i for i in maintenance.status(today)}
    vb = status.get("visa_bulletin")
    month = f"{settings.MONTHS[today.month - 1]} {today.year}"
    vb_done = vb is not None and not vb["findings"]
    add("visa_bulletin", "The Visa Bulletin for this month", vb_done,
        f"Set for {month}." if vb_done else (vb["findings"][0] if vb else f"Enter the {month} cut-offs."), "settings", "visa_bulletin_eb4")

    fees_saved = _saved("fees")
    fee_checked = (maintenance._firm_log().get("fee_schedule") or {}).get("last_checked")
    fees_by = fees_saved["updated_by"] if fees_saved else None
    add("fees", "Filing fees confirmed", bool(fees_saved or fee_checked),
        (f"Confirmed by {fees_by} on {_mdy(fees_saved.get('updated_at'))}." if fees_saved else f"Marked checked on {_mdy(fee_checked)}.") if fees_saved or fee_checked
        else "The amounts come from the official fee schedule. Compare them with the schedule USCIS publishes, then save the page.", "settings", "fees")

    translators = settings.translators()
    add("translators", "Translators", bool(translators),
        f"{len(translators)} on the list." if translators else "Whoever signs a certificate of translation. Every document in another language needs one.", "settings", "translators")

    if app.accounts is not None:
        people = [u for u in app.accounts.users() if u["active"]]
        add("staff", "Staff accounts", len(people) > 1,
            f"{len(people)} people can sign in." if len(people) > 1 else "Only one person can sign in. Add the paralegals and the other attorneys.", "staff")
        hook = getattr(app.accounts, "second_factor_summary", None)  # the built-in second factor, when this version has it
        if hook is not None:
            enrolled, total = hook()
            from review.auth import second_factor

            everyone = second_factor()["everyone"]
            who = ("person" if everyone else "attorney") if total == 1 else ("people" if everyone else "attorneys")  # one is "attorney", not "attorneys"
            add("second_factor", "A second code at sign-in", total > 0 and enrolled >= total,
                (f"The {who} has set it up." if total == 1 else f"All {total} {who} have set it up.") if total and enrolled >= total
                else f"{enrolled} of {total} {who} {'has' if total == 1 else 'have'} set it up. It is asked for the first time each person signs in.",
                "staff")

    cases = _case_count(app)
    add("first_client", "The first client", cases > 0,
        f"{cases} client{'s' if cases != 1 else ''} in the system." if cases else "Add a client, then invite them to the portal or put their documents in.", "add_client")

    if not deployment.hosted():
        b = backups.status(today)
        ok = bool(b["backup"]) and not b["overdue"]
        add("backups", "Nightly backup", ok, b["line"] if ok or b["backup"] else
            "Nothing is backing up the data yet. Your IT sets it up (the installer does); then do a test restore.", "maintenance")

    from connectors import clio

    state = clio.state(app.firm_data)
    connected = bool(state.get("connected")) and not state.get("refresh_failed")
    add("clio", "Clio", connected, "Connected." if connected else "Optional. If the firm uses Clio, connect it: matters and documents come in every night.",
        "settings", "connections", optional=True)

    also = [{"id": i["id"], "what": (i["firm_what"] or i["what"]).replace("{provider}", deployment.provider()), "settings": WHERE.get(i["id"]),
             "findings": i["findings"]}
            for i in status.values() if i["responsible"] == "firm" and i["due"] and i["id"] not in COVERED]
    counted = [i for i in items if not i["optional"]]
    return {"items": items, "done": sum(1 for i in counted if i["done"]), "total": len(counted), "also_due": also, "first_visit": first_visit(user)}
