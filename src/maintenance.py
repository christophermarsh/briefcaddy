"""Keeping the system current: what goes out of date, and whether it has
(schemas/registers/maintenance.json; docs/maintenance.md for the people doing it).

  status()       -- every item: due or not, from its cadence and last check,
                    plus what can be seen without the internet (this month's
                    Visa Bulletin set? the fees and guidelines recorded?)
  live_checks()  -- the official sources themselves: each USCIS form's
                    current edition against the template we fill, the fee
                    schedule's edition, the I-864P's effective date, the
                    USCIS address pages' "last updated" dates, the post
                    office links, USCIS's developer catalog (is there an API
                    that files a form?). A new edition of a form is read
                    further (src/editions.py: the form page's grace words,
                    the packets that wait). Never changes anything -- it reports.
  mark(id, by)   -- records that a person checked an item today: the firm's
                    checks in its own data (data/maintenance_log.json), ours
                    in the register we ship -- so an update never undoes a
                    firm's record, and a firm never writes into the product.

Each item says who keeps it current ("party", src/deployment.py): the firm,
us (the provider), or whoever runs the server. A firm's screens show its own
items with its own steps, and provider responsibilities with recorded review evidence.

Results of the last live check are kept in data/maintenance_status.json, so
the review app can show them without going online.
"""

from __future__ import annotations

import json
import os
import re
from datetime import date, timezone
from pathlib import Path
from typing import Any

import clock
import events
import editions
import schema_path

REPO = Path(__file__).resolve().parents[1]
REGISTRY = Path(os.environ.get("I485_MAINTENANCE") or schema_path.path("register", "maintenance"))  # a test world keeps its own copy
LAST_LIVE = Path(os.environ.get("I485_LIVE_STATUS") or REPO / "data" / "maintenance_status.json")  # a test world keeps its own
FIRM_LOG = Path(os.environ.get("I485_MAINTENANCE_LOG") or REPO / "data" / "maintenance_log.json")  # the firm's own checks
DAYS = {"monthly": 31, "quarterly": 92, "yearly": 366}
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126 Safari/537.36 i485-pipeline-maintenance"
MONTHS = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
STATUS_LABELS = {"not_checked": "Not checked", "needs_attention": "Needs attention", "due_for_review": "Due for review",
                 "reviewed_within_cadence": "Reviewed within cadence"}
LIVE_TYPES = {"uscis_form_edition", "g1055_edition", "i864p_effective", "page_updated", "api_catalog", "tps_pages", "links"}
MANUAL_RECORD_HOLD = "The saved manual maintenance record cannot be verified. Preserve it and ask your IT to reconcile or restore it before recording another check."


def registry(path: Path = REGISTRY) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _read(p: Path) -> dict[str, Any]:
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _local_finding(item: dict[str, Any], today: date) -> str | None:
    """What can be seen without going online."""
    kind = item["check"]["type"]
    if kind == "visa_bulletin_month":
        from fill.cover_letter import load_config

        month = (load_config().get("visa_bulletin") or {}).get("month")
        want = f"{MONTHS[today.month - 1]} {today.year}"
        return None if month == want else f"The Visa Bulletin setting is {month or 'not set'}; it should be {want}."
    if kind == "visa_bulletin_family_month":
        import preference

        month = preference.settings().get("month")
        want = f"{MONTHS[today.month - 1]} {today.year}"
        return None if month == want else f"The family Visa Bulletin setting is {month or 'not set'}; it should be {want}."
    if kind == "g1055_edition" and not _read(schema_path.path("law", "fees")).get("checked"):
        return "No fees recorded."
    if kind == "case_status_api":  # USCIS's Case Status API: the keys on this server, last night's run (src/case_status.py)
        import case_status

        return case_status.finding()
    if kind == "backup_log":  # the last backup and the last test restore, from the log the backup tools write (src/backups.py)
        import backups

        found = backups.status(today)["findings"]
        return " ".join(found) if found else None
    if kind == "restore_drill":  # the last restore drill (src/backups.py drill): due at once when it failed, when it never ran, and after a month
        import backups

        found = backups.drill_status(today)["findings"]
        return " ".join(found) if found else None
    if kind == "ledger_check":  # last night's check of the event ledger (src/ledger_seal.py): due at once when a row does not match
        import ledger_seal

        kept = ledger_seal.kept(events.base_path(None))
        return None if kept is None or kept["ok"] else str(kept.get("line") or "The record does not match.")
    if kind == "secret_rotation":  # a key or secret is past the cadence the attorney chose (src/firmsecrets.py); no cadence is guessed
        import firmsecrets

        row = next((r for r in firmsecrets.status(today, data_root=FIRM_LOG.parent) if r["kind"] == item["check"]["kind"]), None)
        return f"Due since {clock.us_date(row['due_on'].isoformat())}: last changed {row['last_changed'] or 'never recorded'}." if row and row["overdue"] else None
    if kind == "clio_connection":  # the firm's Clio connection (src/connectors/clio.py): due at once when it stopped
        from connectors import clio

        line, needs = clio.state_text()
        return line if needs else None
    if kind == "clio_webhook":  # Clio's webhook subscription (src/connectors/clio_hooks.py): due at once when Clio stopped it or it could not be renewed
        from connectors import clio, clio_hooks

        return clio_hooks.needs(None) if clio.folder(None).exists() else None
    if kind == "i864p_effective":
        import family

        guides = family.settings().get("poverty_guidelines") or {}
        if not guides.get("effective"):
            return "No poverty guidelines recorded."
        if (today - date.fromisoformat(guides["effective"])).days > 400:
            return f"The guidelines recorded took effect {guides['effective']}: more than a year ago; USCIS has likely published new ones."
    return None


def _review_date(value, today: date) -> tuple[str | None, bool]:
    if value is None:
        return None, False
    try:
        parsed = date.fromisoformat(value) if isinstance(value, str) else None
        if parsed is not None and parsed <= today:
            return parsed.isoformat(), False
    except ValueError:
        pass
    return None, True


def _observation_time(at, today: date) -> bool:
    """Compare timestamp instants, while preserving legacy plain-date semantics."""
    if not isinstance(at, str):
        return False
    try:
        if re.fullmatch(r"\d{4}-\d{2}-\d{2}", at):
            return date.fromisoformat(at) <= today
        parsed = clock.parse(at)
        return bool(parsed and parsed.astimezone(clock.zone()).date() <= today
                    and parsed.astimezone(timezone.utc) <= clock.utcnow())
    except (ValueError, OverflowError):
        return False


def _manual_review(row, today: date) -> tuple[str | None, str | None]:
    """Validate the retained row and all of its history before granting credit."""
    if not isinstance(row, dict):
        raise ValueError(MANUAL_RECORD_HOLD)
    last, bad = _review_date(row.get("last_checked"), today)
    history = row.get("log", [])
    if bad or not isinstance(history, list):
        raise ValueError(MANUAL_RECORD_HOLD)
    previous, generic, by = None, None, None
    for entry in history:
        if not isinstance(entry, dict):
            raise ValueError(MANUAL_RECORD_HOLD)
        at = entry.get("on")
        on = clock.local_date(at).isoformat() if _observation_time(at, today) else None
        actor = entry.get("by")
        if (on is None or not isinstance(actor, str) or not actor.strip()
                or previous is not None and on < previous):
            raise ValueError(MANUAL_RECORD_HOLD)
        previous = on
        # Actual translation attestations share this history but do not mean
        # someone performed the separate generic upkeep check.
        if not entry.get("review_type"):
            generic, by = on, actor
    if generic is not None and last != generic:
        raise ValueError(MANUAL_RECORD_HOLD)
    return last, by


def live_evidence(result, at, today: date) -> dict:
    """Saved source observation, distinct from human review or an update job."""
    if result is None:
        return {"state": "not_checked", "checked_at": None, "ok": None, "finding": None}
    if (not isinstance(result, dict) or not _observation_time(at, today)
            or "ok" not in result or not (result["ok"] is True or result["ok"] is False or result["ok"] is None)
            or result.get("finding") is not None and not isinstance(result["finding"], str)):
        previous = result.get("finding") if isinstance(result, dict) and isinstance(result.get("finding"), str) else None
        return {"state": "unknown", "checked_at": None, "ok": None,
                "finding": ((previous + " ") if previous else "") + "The saved official-source check or its date cannot be verified."}
    ok = result.get("ok")
    valid = ok is True or ok is False or ok is None
    finding = result.get("finding") if isinstance(result.get("finding"), str) else None
    return {"state": "needs_attention" if ok is False or finding else "checked" if ok is True else "unknown",
            "checked_at": at, "ok": ok if valid else None,
            "finding": finding or ("The saved official-source check needs review." if ok is not True else None)}


def public_finding(value: str) -> str:
    """A maintenance finding can explain a difference without provider paths."""
    return re.sub(r"(?:[A-Za-z]:)?(?:[/\\][^\s,;:]+)*[/\\](?:schemas|src|tools|data|clients|install)[/\\][^\s,;]*|\b(?:schemas|src|tools|data|clients|install)[/\\][^\s,;]*",
                  "the recorded settings", value)


def recorded_live() -> dict:
    try:
        data = json.loads(LAST_LIVE.read_text(encoding="utf-8")) if LAST_LIVE.exists() else {}
        if not isinstance(data, dict) or not isinstance(data.get("results", {}), dict):
            raise ValueError("invalid saved live-check shape")
        if data and not _observation_time(data.get("at"), clock.today()):
            return data | {"at": None, "error": "The saved official-source check or its timestamp cannot be verified."}
        if data.get("error") is not None:
            return data | {"at": None, "error": "The saved official-source check is unavailable or malformed."}
        return data
    except (OSError, ValueError):
        return {"results": {}, "error": "The saved official-source check is unavailable or malformed."}


def status(today: date | None = None, path: Path = REGISTRY) -> list[dict[str, Any]]:
    import deployment

    today = today or clock.today()
    live = recorded_live()
    try:
        firm_log, manual_error = _firm_log(), None
    except ValueError:
        firm_log, manual_error = {}, MANUAL_RECORD_HOLD
    out = []
    for item in registry(path)["items"]:
        party = item.get("party", "provider")
        who = deployment.responsible(party)
        review_error, checked_by, mine_last = manual_error if who == "firm" else None, None, None
        if who == "firm" and not review_error:
            try:
                mine_last, checked_by = _manual_review(firm_log.get(item["id"], {}), today)
            except ValueError:
                review_error = MANUAL_RECORD_HOLD
        dates = [_review_date(value, today) for value in (item.get("last_checked"), mine_last)]
        last = max((value for value, _ in dates if value), default=None)
        bad_date = any(invalid for _, invalid in dates)
        age = (today - date.fromisoformat(last)).days if last else None
        due = last is None or (item["cadence"] in DAYS and age is not None and age >= DAYS[item["cadence"]])
        if item["check"]["type"] == "backup_log":  # read from the backup log, never ticked: checked the day of the last test restore, due when the log says so
            import backups

            restore = backups.read_log().get("last_test_restore")
            last, due = (clock.local_date(restore["at"]).isoformat() if restore and clock.local_date(restore["at"]) else None), False
        if item["check"]["type"] == "restore_drill":  # read from the backup log like the backups' own: checked the day of the last drill, due when the log says so
            import backups

            drilled = backups.read_log().get("last_restore_drill")
            last, due = (clock.local_date(drilled["at"]).isoformat() if drilled and clock.local_date(drilled["at"]) else None), False
        if item["check"]["type"] == "secret_rotation":  # read from the record of changes and the attorney's cadence, never ticked: checked the day it was last changed
            import firmsecrets

            row = next((r for r in firmsecrets.status(today, data_root=FIRM_LOG.parent) if r["kind"] == item["check"]["kind"]), None)
            last, due = (row or {}).get("last_changed_iso"), False
            item = {**item, "cadence": (row or {}).get("cadence") or "not set"}
        last, invalid = _review_date(last, today)
        bad_date |= invalid
        if review_error:
            last, checked_by, due = None, None, True
        results = live.get("results") or {}
        saved = results.get(item["id"])
        # A present null is damaged evidence, distinct from a check never saved.
        observation = live_evidence({} if item["id"] in results and saved is None else saved, live.get("at"), today)
        if live.get("error") and item["check"]["type"] in LIVE_TYPES:
            observation = {"state": "unknown", "checked_at": None, "ok": None,
                           "finding": " ".join(f for f in (observation["finding"], live["error"]) if f)}
        findings = [f for f in (_local_finding(item, today), observation["finding"]) if f]
        if review_error:
            findings.append(review_error)
        if bad_date:
            findings.append("The recorded review date is invalid or in the future; check the review record.")
        if last and item["cadence"] not in DAYS:
            findings.append("No review cadence is recorded; the responsible party needs to review it.")
        due = bool(due or findings or last is None)
        state = "needs_attention" if findings else "not_checked" if last is None else "due_for_review" if due else "reviewed_within_cadence"
        out.append({"id": item["id"], "what": item["what"], "owner": item["owner"], "cadence": item["cadence"], "last_checked": last,
                    "due": due, "findings": findings, "state": state, "status": STATUS_LABELS[state], "check_type": item["check"]["type"],
                    "live_check": observation, "where": item["where"], "source": item.get("source"), "steps": item.get("steps", []),
                    "party": party, "responsible": who, "firm_steps": item.get("firm_steps") or [], "firm_what": item.get("firm_what"), "checked_by": checked_by})
    return out


def _firm_log(path: Path | None = None) -> dict[str, Any]:
    p = path or FIRM_LOG
    from portal.communication_consent import _read as consent_read
    try:
        return consent_read(p, {})
    except (OSError, ValueError):
        raise ValueError(MANUAL_RECORD_HOLD) from None


def _get(url: str, binary: bool = False, timeout: float = 60) -> Any:
    import httpx

    r = httpx.get(url, headers={"User-Agent": UA}, follow_redirects=True, timeout=timeout)
    r.raise_for_status()
    return r.content if binary else r.text


def pdf_edition(data: bytes | Path) -> str | None:
    """A USCIS form's edition ("Edition 09/18/26"), from any page's text."""
    import io

    from pypdf import PdfReader

    reader = PdfReader(io.BytesIO(data) if isinstance(data, bytes) else str(data))
    for page in list(reader.pages)[:3] + list(reader.pages)[-1:]:
        text = page.extract_text() or ""
        # "Edition 09/18/26" -- or the G-28's own footer, "Form G-28   09/17/18   Page 1 of 4" (the G-1145's: "Form G-1145  09/26/14  Y  Page 1 of 1")
        m = (re.search(r"Edition\s*(\d{2}/\d{2}/\d{2})", text) or re.search(r"Form\s+[A-Z]{1,2}-\d+[A-Z]?\s+(\d{2}/\d{2}/\d{2})\s+(?:[A-Z]\s+)?Page", text)
             or re.search(r"Form\s+EOIR-\d+\s+Rev\.\s*([A-Z][a-z]{2}\.?\s+\d{4})", text))  # the immigration court's forms: "Form EOIR-28 Rev. Feb. 2025"
        if m:
            return m.group(1)
    return None


def live_checks(path: Path = REGISTRY, get=_get) -> dict[str, Any]:
    """Asks each official source; returns {item id: {"ok", "finding", ...}} and saves it."""
    results: dict[str, Any] = {}
    previous = (json.loads(LAST_LIVE.read_text(encoding="utf-8")).get("results") or {}) if LAST_LIVE.exists() else {}
    for item in registry(path)["items"]:
        check, iid = item["check"], item["id"]
        try:
            if check["type"] == "uscis_form_edition":
                ours, theirs = pdf_edition(REPO / check["template"]), pdf_edition(get(check["pdf"], binary=True))
                if ours is None:
                    ours = item.get("known_edition")
                ok = theirs is not None and theirs == ours
                results[iid] = {"ok": ok, "ours": ours, "uscis": theirs, "checked_on": clock.today().isoformat(),
                                "finding": None if ok else f"USCIS now publishes edition {theirs}; the template we fill is {ours or 'unknown'}."}
                if not ok and theirs is not None:  # a new edition: packets that use the form wait (src/editions.py); the form's page says if there is a grace period
                    page = item.get("source") if str(item.get("source") or "").startswith("https://www.uscis.gov/") else None
                    name = editions.form_name(item["what"])
                    results[iid] |= {"form": name} | editions.read_page(page, ours, theirs, get, previous.get(iid), form=name)
                    results[iid]["finding"] = editions.finding(results[iid])
            elif check["type"] == "g1055_edition":
                ours, theirs = __import__("fees").load().get("edition"), pdf_edition(get(check["url"], binary=True))
                ok = theirs == ours
                results[iid] = {"ok": ok, "ours": ours, "uscis": theirs,
                                "finding": None if ok else f"Form G-1055 is now edition {theirs}; the fees in Settings are from {ours}: update them on the Settings page."}
            elif check["type"] == "i864p_effective":
                text = re.sub(r"<[^>]+>", " ", get(check["url"]))
                m = re.search(r"effective\s+beginning\s+([A-Z][a-z]{2,8})\.?\s+(\d{1,2}),\s+(\d{4})", text)
                theirs = None
                if m:
                    month = next(i for i, name in enumerate(MONTHS, 1) if name.startswith(m.group(1)[:3]))
                    theirs = date(int(m.group(3)), month, int(m.group(2))).isoformat()
                ours = (_read(schema_path.path("law", "family_settings")).get("poverty_guidelines") or {}).get("effective")
                ok = theirs == ours
                results[iid] = {"ok": ok, "ours": ours, "uscis": theirs,
                                "finding": None if ok else f"The I-864P now takes effect {theirs}; the guidelines recorded are from {ours}: update them."}
            elif check["type"] == "page_updated":
                text = re.sub(r"<[^>]+>", " ", get(check["url"]))
                m = re.search(r"Last\s+Reviewed/Updated:\s*(\d{2}/\d{2}/\d{4})", text)
                theirs, ours = (m.group(1) if m else None), check.get("known")
                ok = ours is None or theirs == ours
                results[iid] = {"ok": ok, "ours": ours, "uscis": theirs,
                                "finding": None if ok else f"The page was updated {theirs} (we checked the {ours} version): compare {check.get('compare', 'the addresses')}."}
            elif check["type"] == "api_catalog":  # USCIS's developer portal: is there an API that files a form yet? (none on 10/02/2026)
                titles = sorted(set(re.findall(r'class="card-title">\s*([^<]+?)\s*<', get(check["url"]))))
                known = check.get("known") or []
                new = [t for t in titles if t not in known]
                if not titles:
                    results[iid] = {"ok": None, "finding": f"Couldn't read any API from the catalog page. Check by hand: {item.get('source')}"}
                else:
                    results[iid] = {"ok": not new, "ours": known, "uscis": titles,
                                    "finding": None if not new else f"USCIS's developer catalog now lists {', '.join(new)}. Read what it does: if it files a form, "
                                                                    "online filing by an API replaces the upload by hand (src/online_filing.py)."}
            elif check["type"] == "tps_pages":  # the TPS page and the page of every designated country in schemas/law/tps.json (a date in the file is what we read)
                tps = _read(schema_path.path("law", "tps"))
                pages = [(check["url"], tps.get("page_updated") or check.get("known"), "the TPS page")]
                pages += [(c["page"], c.get("page_updated"), c.get("name") or name) for name, c in (tps.get("countries") or {}).items()
                          if c.get("status") == "designated" and c.get("page")]
                moved = []
                for url, ours, label in pages:
                    m = re.search(r"Last\s+Reviewed/Updated:\s*(\d{2}/\d{2}/\d{4})", re.sub(r"<[^>]+>", " ", get(url)))
                    theirs = m.group(1) if m else None
                    if ours and theirs != ours:
                        moved.append(f"{label} was updated {theirs} (we read the {ours} version)")
                results[iid] = {"ok": not moved, "ours": [p[1] for p in pages], "finding": "; ".join(moved) + ": compare the registration periods and dates in schemas/law/tps.json." if moved else None}
            elif check["type"] == "links":
                bad = []
                for geo in schema_path.glob("geo"):
                    link = (json.loads(geo.read_text(encoding="utf-8")).get("postal") or {}).get("lookup")
                    if link:
                        try:
                            get(link, timeout=25)
                        except Exception:  # noqa: BLE001 -- a dead link is the finding
                            bad.append(f"{geo.stem}: {link}")
                results[iid] = {"ok": not bad, "finding": f"Not responding: {'; '.join(bad)}" if bad else None}
        except Exception as exc:  # noqa: BLE001 -- one source down never stops the others
            results[iid] = {"ok": None, "finding": f"Couldn't check ({type(exc).__name__}). Check by hand: {item.get('source')}"}
            last = previous.get(iid) or {}
            if check["type"] == "uscis_form_edition" and last.get("ok") in (True, False):
                results[iid] = editions.carry(last, clock.today())  # an outage never releases a held packet, nor re-dates what it knew
    out = {"at": clock.stamp("seconds"), "results": results}
    LAST_LIVE.parent.mkdir(parents=True, exist_ok=True)
    LAST_LIVE.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def mark(item_id: str, by: str, path: Path = REGISTRY, today: date | None = None, log_path: Path | None = None) -> dict[str, Any]:
    """Records that `by` checked an item today: the firm's items in the firm's log, ours in the register (the date; the log keeps who)."""
    import deployment

    if not isinstance(by, str) or not by.strip():
        raise ValueError("Enter the name of the person who performed the check.")
    data = registry(path)
    item = next((i for i in data["items"] if i["id"] == item_id), None)
    if item is None:
        raise KeyError(f"no maintenance item {item_id!r}")
    on = (today or clock.today()).isoformat()
    if deployment.responsible(item.get("party", "provider")) == "firm":
        target = log_path or FIRM_LOG
        from portal.communication_consent import data_gate
        from portal.queue_bridge import _atomic
        with data_gate(Path(target).parent):
            log = _firm_log(target)
            for row in log.values():
                _manual_review(row, today or clock.today())
            mine = log.setdefault(item_id, {"log": []})
            mine.setdefault("log", [])
            mine["last_checked"] = on
            mine["log"].append({"on": on, "by": by})
            _atomic(target, log)
            events.record("upkeep", "checked", f"Marked as checked: {item.get('what') or events.words(item_id)}", home=Path(target).parent, who=by)
            return item | {"last_checked": on, "log": mine["log"]}
    _manual_review(item, today or clock.today())
    item["last_checked"] = on
    item.setdefault("log", []).append({"on": item["last_checked"], "by": by})
    path.write_text(json.dumps(data, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    events.record("upkeep", "checked", f"Marked as checked: {item.get('what') or events.words(item_id)}", home=FIRM_LOG.parent, who=by)
    return item
