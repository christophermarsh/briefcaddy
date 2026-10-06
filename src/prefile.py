"""Before a packet goes in the mail: is it correct, current and on time?
And once it goes: a record of exactly what was mailed, when and how.

The attorney signs off on a packet the system assembled; this is the list
an experienced attorney runs through before letting it leave the office,
for any filing (I-485, I-360, family, N-400) -- each check says what it
looked at:

  - the packet was built after the last review decision (nothing changed
    since), and it isn't a DRAFT (the packet's own problems are listed);
  - every form in it is the edition USCIS accepts today, the fee is the
    current fee schedule's, and the mailing address is from USCIS's
    current page (the nightly live checks, src/maintenance.py);
  - the client's name, A-Number and date of birth read back the same from
    every filled form as the case holds them (a form filled before a
    correction would show here);
  - the deadlines this filing has (the 21st birthday before an I-360, the
    N-400's earliest date, anything due within two weeks);
  - who signs where.

record_filing() then keeps the mailing: the filing, the date, carrier and
tracking number, the address and fee the cover letter stated, the
packet's fingerprint (sha256) -- and the attorney's reason when they mail
despite a failed check. The case timeline, the receipt follow-up and the
client's portal read it (src/journey.py).

A filing chosen for the USCIS online account (src/online_filing.py) is
checked the same way, less the mailing address, plus the bundle (built from
this very packet, every file within USCIS's limit); it is recorded with the
carrier "USCIS online account", and its receipt number -- which USCIS's
confirmation doesn't carry -- is added when the case card shows it
(add_receipt).
"""

from __future__ import annotations

import json
import re
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import clock
import events
import schema_path


IDENTITY = ("applicant.family_name", "applicant.given_name", "applicant.a_number", "applicant.dob")
ADDRESS_CHECK = {"i485": "lockbox_chart", "family": "lockbox_chart", "i360": "i360_addresses", "n400": "n400_addresses", "i589": "i589_addresses",
                 "i90": "i90_address", "i131": "i131_addresses", "n600": "n600_addresses", "i751": "i751_addresses",
                 "ead": "i765_addresses", "asylee": "i485_addresses", "i290b": "i290b_addresses", "n336": "n336_addresses", "i601a": "i601a_address",
                 "i914": "i914_addresses"}
ADDRESS_CHECK["u_visa"] = "i918_addresses"  # the U petition (src/u_visa.py)
ADDRESS_CHECK["i730"] = "i730_address"  # uscis.gov/i-730 (src/i730.py)
CARRIERS = ["USPS", "FedEx", "UPS", "DHL", "Hand delivery", "Online", "USCIS online account"]  # Online: the EOIR portal, or NVC's CEAC
ONLINE_USCIS = "USCIS online account"  # filed by PDF upload (src/online_filing.py): the receipt number comes later, from the case card
RECEIPT = r"[A-Z]{3}\d{10}"  # USCIS's receipt number: three letters, ten digits (developer.uscis.gov Case Status API)
FEE_KEYS = {"i485": ["paper.i485", "paper.i765"], "i360": ["pl_119_21.i360_sij"], "family": ["paper.i130", "paper.i485", "paper.i765_with_pending_i485_paid", "paper.i485_supa"],
            "n400": ["paper.n400", "paper.n400_reduced"], "i589": ["pl_119_21.i589_asylum"], "i90": ["paper.i90"],
            "i131": ["paper.i131_reentry", "paper.i131_advance_parole", "paper.i131_rtd_asylee_16plus", "paper.i131_rtd_asylee_under16"],
            "n600": ["paper.n600"], "i751": ["paper.i751", "paper.i751_abuse_waiver"], "ead": ["paper.i765", "pl_119_21.i765_asylum_initial", "pl_119_21.i765_asylum_renewal"],
            "asylee": ["paper.i485", "paper.i485_refugee"], "i290b": ["paper.i290b"], "n336": ["paper.n336"], "bia": ["eoir.eoir26"], "i601a": ["paper.i601a"],
            "cancellation": ["eoir.eoir42a", "eoir.eoir42b", "eoir.dhs_biometrics"]}
ADDRESS_CHECK["caa"] = "i485_addresses_caa"  # the CAA and HRIFA rows of USCIS's I-485 filing addresses (src/cuban_adjustment.py)
FEE_KEYS["caa"] = ["paper.i485", "paper.i485_under_14_with_parent", "paper.i765_with_pending_i485_paid"]
ADDRESS_CHECK["vawa"] = "vawa_address"  # the "Attn: 1367" lockboxes (src/vawa.py)
FEE_KEYS["vawa"] = ["paper.i360_vawa", "paper.i485_vawa"]
FEE_KEYS["i914"] = ["paper.i914", "paper.i914a", "paper.i192_t"]
FEE_KEYS["u_visa"] = ["paper.i918", "paper.i918a", "paper.i192_u"]
ADDRESS_CHECK["daca"] = "daca_addresses"  # USCIS "Direct Filing Addresses for Form I-821D" (src/daca.py)
FEE_KEYS["daca"] = ["paper.i821d", "paper.i765_c33"]
FEE_KEYS["i730"] = ["paper.i730"]
FEE_KEYS["court_motion"] = ["eoir.ij_motion", "eoir.motion_no_fee_relief"]  # a motion to the judge (src/court_motion.py); a bond request has no fee
COURT_FILINGS = {"eoir28", "cancellation", "court_bond", "court_motion"}  # filed with the immigration court, not mailed to USCIS


def _us(iso: str) -> str:
    """2026-10-13 -> 10/13/2026, as every date on screen."""
    m = re.fullmatch(r"(\d{4})-(\d{2})-(\d{2})", str(iso or ""))
    return f"{m[2]}/{m[3]}/{m[1]}" if m else str(iso)
ADDRESS_CHECK.update({"i601": "i601_addresses", "i212": "i212_addresses"})  # USCIS's direct filing addresses for each (src/inadmissibility_waiver.py, src/reapply.py)
FEE_KEYS.update({"i601": ["paper.i601", "paper.i212"], "i212": ["paper.i212"]})  # the I-601's packet carries the I-212 when they go together
ADDRESS_CHECK["n565"] = "n565_address"  # uscis.gov/n-565 (src/n565.py)
FEE_KEYS["n565"] = ["paper.n565", "paper.n565_uscis_error"]
ADDRESS_CHECK["parole"] = "parole_addresses"  # the Dallas lockbox's row on the I-131 addresses page (src/parole.py)
FEE_KEYS["parole"] = ["paper.i131_parole"]
ADDRESS_CHECK["tps"] = "tps_status"  # the countries' own TPS pages, by country and state (src/tps.py)
FEE_KEYS["tps"] = ["paper.i821_initial", "paper.i821_reregistration", "paper.i821_biometrics", "paper.i765_tps_initial", "pl_119_21.tps_ead_initial", "pl_119_21.tps_ead_renewal"]
LIVE_MAX_AGE_DAYS = 7


def _read(path: Path, default: Any) -> Any:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return default


def _check(id_: str, level: str, title: str, text: str) -> dict[str, str]:
    """level: pass | fail (stops the mailing) | warn (look at it) | info."""
    return {"id": id_, "level": level, "title": title, "text": text}


FORM_CHECKS = {"ead": "form_i765"}  # a form filled from another form's template is checked as that form
FORM_CHECKS.update({"i360_vawa": "form_i360", "i485_vawa": "form_i485"})  # the VAWA I-360's own map; its I-485, filled by src/vawa.py
FORM_CHECKS.update({"i918a_1": "form_i918a", "i918a_2": "form_i918a", "i918a_3": "form_i918a"})  # the U petition's Supplements A
FORM_CHECKS.update({"i765_daca": "form_i765"})  # the DACA renewal's I-765, filled from the work permit's map
FORM_CHECKS.update({"i765_tps": "form_i765", "i131_parole": "form_i131"})  # the TPS work permit (the ead map) and the parole variant of the I-131 (src/tps.py, src/parole.py)


def _form_check_id(form_id: str) -> str:
    form_id = re.sub(r"_\d+$", "", form_id)  # one per person: "i864a_2" is an I-864A
    return "form_g28" if form_id.startswith("g28") else FORM_CHECKS.get(form_id, f"form_{form_id}")


def live_results(path: Path | None = None) -> tuple[dict[str, Any], str | None]:
    import maintenance

    data = _read(path or maintenance.LAST_LIVE, {})
    return data.get("results") or {}, data.get("at")


TYPED_ONLINE_ONLY = {"g639"}  # USCIS takes it only through its FOIA portal, typed in: never mailed (src/g639.py)


def check(client_dir: Path, filing: str, today: date | None = None, live_path: Path | None = None) -> dict[str, Any]:
    import packet as packet_mod

    if filing.startswith("rfe:"):  # a response to a USCIS request (src/rfe.py)
        import rfe

        return rfe.check(client_dir, filing[4:], today)
    today = today or clock.today()
    schema = packet_mod.for_case(packet_mod.load_filing(filing), client_dir)
    manifest = _read(client_dir / schema.get("manifest", "packet.json"), None)
    checks: list[dict[str, str]] = []
    import online_filing

    online = online_filing.mode(client_dir, filing, schema) == online_filing.ONLINE  # filed by PDF upload, not mailed
    if not manifest:
        return {"filing": filing, "ready": False, "online": online,
                "checks": [_check("built", "fail", "Packet", f"Not built yet: build the {'online-filing bundle' if online else 'packet'} first.")]}
    if online:
        checks += _online_checks(client_dir, filing, manifest, results=live_results(live_path)[0])

    # 1. built after the last change, and not a draft
    built_at = clock.parse(manifest["built_at"])
    changed = [p for p in ("decisions.json", "fact_graph.json", "meta.json") if (client_dir / p).exists()
               and datetime.fromtimestamp((client_dir / p).stat().st_mtime, timezone.utc) > built_at]
    checks.append(_check("fresh", "fail" if changed else "pass", "Up to date",
                         "Something changed after the packet was built (review decisions or the case was re-read): rebuild it."
                         if changed else f"Built {clock.local(built_at).strftime('%m/%d/%Y %H:%M')} by {manifest.get('built_by')}, after the last change."))
    if manifest.get("draft"):  # the title says the state (the pages say DRAFT): never "Not a draft" above a list of open problems
        n = len(manifest.get("problems") or [])
        checks.append(_check("draft", "fail", f"Draft until {n} problem{'s' if n != 1 else ''} {'are' if n != 1 else 'is'} settled" if n else "Draft",
                             "This packet is marked DRAFT because it was built while these were open (settle them, then build it again): " + " ".join(manifest.get("problems") or [])))
    else:
        ready = "Ready to file online" if online else "Ready to enter online" if filing in TYPED_ONLINE_ONLY else "Ready to mail"
        checks.append(_check("draft", "pass", ready, "No open problems when it was built."))

    # 2. current editions, fee and address (the nightly live checks)
    results, at = live_results(live_path)
    age = (clock.now() - clock.parse(at)).days if at else None
    if age is None or age > LIVE_MAX_AGE_DAYS:
        checks.append(_check("live", "warn", "Checked against USCIS",
                             "The official sources haven't been checked in the last week. They are checked every night; if this stays, tell " + __import__("deployment").support() + "."))
    forms = manifest.get("forms") or [{"id": f} for f in schema.get("forms", [])]
    for form in forms:
        if form["id"] in (schema.get("generated") or {}) and form["id"] not in FORM_CHECKS:  # the firm's own pages (the visa answer sheet): no edition
            continue
        r = results.get(_form_check_id(form["id"]))
        name = form.get("short") or form["id"].upper()
        agency = "the immigration court (EOIR)" if form["id"].startswith("eoir") else "USCIS"
        if r is None:
            checks.append(_check(f"edition_{form['id']}", "warn", f"{name} edition",
                                 f"Not covered by the live check: confirm the edition on {'justice.gov/eoir' if agency != 'USCIS' else 'uscis.gov'}."))
        elif r.get("ok") is False:
            checks.append(_edition_check(f"edition_{form['id']}", name, agency, r, today))
        elif r.get("ok") is None or r.get("recheck_failed_on"):  # tonight's check couldn't run: never a pass without a recent good one
            checks.append(_unchecked_edition(f"edition_{form['id']}", name, agency, r, today))
        else:
            checks.append(_check(f"edition_{form['id']}", "pass", f"{name} edition", f"Edition {r.get('ours')}: the current one."))
    # the fee and the lockbox: only for a packet mailed to USCIS (not the EOIR-28 or the visa's online filing)
    # filed online: no mailing address, and the fee is the online one (the bundle's)
    # the I-601 with the I-212 in one envelope goes to the I-212 page's address (src/inadmissibility_waiver.py where): that page is checked
    address_check = ADDRESS_CHECK.get("i212" if schema.get("pair") else filing, "lockbox_chart")
    mailed = (("fee_schedule", "Fee"),) if online else (("fee_schedule", "Fee"), (address_check, "Mailing address"))
    for key, title in mailed if schema.get("cover_letter") else ():
        r = results.get(key)
        if r is None:
            checks.append(_check(key, "warn", title, "Not checked against USCIS yet."))
        elif r.get("ok") is False:
            checks.append(_check(key, "fail", title, f"USCIS changed its page ({r.get('finding') or 'see Keeping current'}): update before {'filing' if online else 'mailing'}."))
        elif online:
            fees_due = (_read(online_filing.bundle_paths(client_dir, filing)[1], {}) or {}).get("fees") or []
            checks.append(_check(key, "pass", title, "Current per USCIS. Paid in the account: "
                                 + ("; ".join(f"Form {p['form']} {online_filing.money(p['online'])}" for p in fees_due) or "no fee") + "."))
        else:
            extra = f" {manifest['fee']}" if title == "Fee" and manifest.get("fee") else (f" To: {', '.join(manifest['mail_to'])}." if manifest.get("mail_to") else "")
            checks.append(_check(key, "pass", title, f"Current per USCIS.{extra}"))

    # an announced fee change for this filing coming soon (src/fees.py): the date the packet is postmarked decides the amount
    import fees as fee_schedule

    soon = fee_schedule.upcoming(FEE_KEYS.get(filing, []), 30, today)
    if soon:
        checks.append(_check("fee_change", "warn", "Fee change coming",
                             "; ".join(f"{c['fee'].split('.')[-1]} becomes ${c['amount']:,} for anything postmarked on or after {c['effective']}" for c in soon)
                             + ", if this packet goes out on or after that day, rebuild it then."))

    # 3. the same person on every form
    checks.append(_identity(client_dir, forms))

    # 4. deadlines
    checks += _deadlines(client_dir, filing, today)

    # 5. who signs where
    sigs = manifest.get("signatures") or []
    if sigs and not online:  # online: each form's own signature pages are in the bundle's checklist
        by_who: dict[str, list[str]] = {}
        for s in sigs:
            by_who.setdefault(s["who"], []).append(f"page {s['page']} ({s['form']})")
        checks.append(_check("signatures", "info", "Signatures", " ".join(f"{who.capitalize()}: {', '.join(p)}." for who, p in by_who.items())
                             + " A stamped or typed name is not a signature."))
    return {"filing": filing, "ready": not any(c["level"] == "fail" for c in checks), "checks": checks, "online": online,
            "built_at": manifest["built_at"], "mail_to": None if online else manifest.get("mail_to"), "fee": None if online else manifest.get("fee"), "sha256": manifest.get("sha256")}


def _edition_check(id_: str, name: str, agency: str, r: dict[str, Any], today: date) -> dict[str, str]:
    """A form USCIS has a newer edition of (src/editions.py): the packet waits for the update, unless USCIS's own page still
    accepts the old edition on the day it goes out (then it is a warning with the last day). The firm's words: the provider updates it."""
    import deployment
    import editions

    v = editions.verdict(r, today, name)
    if v is None:  # the new edition couldn't be read off USCIS's file: no date, no grace
        return _check(id_, "fail", f"{name} edition", f"{agency} now has edition {r.get('uscis')}; the packet uses {r.get('ours')}. An old edition may be rejected. "
                                                      + editions.updating(deployment.provider(), deployment.DEFAULT["provider"]["name"]))
    words = f"{v['headline']} {v['grace']}"
    who = editions.updating(deployment.provider(), deployment.DEFAULT["provider"]["name"])
    if v["held"]:
        return _check(id_, "fail", f"{name} edition", f"{words} {who}")
    return _check(id_, "warn", f"{name} edition", f"{words} Send the packet before {editions.mdy(v['accepted_until'])}, or wait for the update. {who}")


def _unchecked_edition(id_: str, name: str, agency: str, r: dict[str, Any], today: date) -> dict[str, str]:
    """The edition check itself failed (the source didn't answer): a packet isn't called current on that. A good check within
    LIVE_MAX_AGE_DAYS stands, with a warning; with none, the packet waits until someone confirms the edition by hand."""
    import editions

    source = "justice.gov/eoir" if agency != "USCIS" else "uscis.gov"
    last = r.get("checked_on") if r.get("ok") else None
    if last and (today - date.fromisoformat(last)).days <= LIVE_MAX_AGE_DAYS:
        return _check(id_, "warn", f"{name} edition", f"Edition {r.get('ours')} was the current one on {editions.mdy(last)}; last night's check of {agency} couldn't run. "
                                                      f"Confirm the edition on {source} before mailing.")
    return _check(id_, "fail", f"{name} edition", f"The edition couldn't be checked against {agency} (the check has not run successfully since "
                                                  f"{editions.mdy(last) if last else 'it was set up'}): confirm it on {source}, or give the reason it's mailed anyway.")


def edition_holds(forms: list[dict[str, Any]], today: date | None = None, live_path: Path | None = None) -> list[dict[str, Any]]:
    """The forms of a packet that USCIS has a newer edition of: one verdict each (src/editions.py), the packet tab's banner.
    forms: the packet's forms ({"id", ...}). Empty when every edition is current."""
    import deployment
    import editions

    today = today or clock.today()
    results, _at = live_results(live_path)
    seen, out = set(), []
    for form in forms:
        cid = _form_check_id(form["id"])
        v = editions.verdict(results.get(cid), today, form.get("short") or form["id"].upper())
        if v is None or cid in seen:
            continue
        seen.add(cid)
        out.append(v | {"id": cid, "updating": editions.updating(deployment.provider(), deployment.DEFAULT["provider"]["name"])})
    return out


def _online_checks(client_dir: Path, filing: str, manifest: dict[str, Any], results: dict[str, Any]) -> list[dict[str, str]]:
    """Filed by PDF upload (src/online_filing.py): still allowed, the bundle built from this very packet, every file within USCIS's limit."""
    import online_filing

    out = []
    page = results.get("online_filing")
    e = online_filing.eligibility(filing, client_dir)
    if page is not None and page.get("ok") is False:
        out.append(_check("online_page", "warn", "Online filing allowed", "USCIS changed its page of forms that can be filed online: confirm this "
                                                                          "filing is still on it before uploading."))
    else:
        out.append(_check("online_page", "pass", "Online filing allowed", f"{e['why'].rstrip('.')} (USCIS's page of {e['updated']})."))
    bundle = _read(online_filing.bundle_paths(client_dir, filing)[1], None)
    if not bundle:
        out.append(_check("bundle", "fail", "Online-filing bundle", "Not built yet: build the online-filing bundle."))
    elif bundle.get("packet_sha256") != manifest.get("sha256"):
        out.append(_check("bundle", "fail", "Online-filing bundle", "The packet was rebuilt after the online-filing bundle: build the bundle again."))
    elif bundle.get("too_large"):
        out.append(_check("bundle", "fail", "Online-filing bundle", f"Over USCIS's {bundle['limit_bytes'] // 1_000_000} MB per file: "
                                                                     f"{', '.join(bundle['too_large'])}. Scan it again, lighter."))
    else:
        out.append(_check("bundle", "pass", "Online-filing bundle", f"{sum(1 for f in bundle['files'] if f['kind'] == 'evidence')} evidence file(s), "
                                                                     f"each within USCIS's {bundle['limit_bytes'] // 1_000_000} MB."))
    return out


def _identity(client_dir: Path, forms: list[dict]) -> dict[str, str]:
    """Reads the name, A-Number and date of birth back out of every filled form."""
    from pypdf import PdfReader

    from fill import load_field_map
    from fill.companion import field_map_for, load_profile
    from fill.field_map import _resolve
    from review.state import reviewed_graph

    graph = reviewed_graph(client_dir)
    profile = load_profile()
    wrong, read = [], 0
    for form in forms:
        fid = form["id"]
        if fid == "i485":
            fmap, path = load_field_map(schema_path.path("field_map", "i485")), client_dir / "i485_filled.pdf"
        elif fid in profile["forms"]:
            fmap, path = field_map_for(profile["forms"][fid]), client_dir / profile["forms"][fid]["output"]
        else:
            continue
        if not path.exists():
            wrong.append(f"{form.get('short', fid)}: the filled form is missing")
            continue
        values = {name: (f.get("/V") or "") for name, f in (PdfReader(str(path)).get_fields() or {}).items()}
        for key in IDENTITY:
            fact = graph.get(key)
            if key not in fmap or fact is None or fact.status != "resolved" or fact.value in (None, ""):
                continue
            for field, expected in _resolve(fact.value, fmap[key]).items():
                read += 1
                if str(values.get(field, "")).strip() != str(expected).strip():
                    wrong.append(f"{form.get('short', fid)} shows {values.get(field) or 'nothing'} for {key.split('.')[-1].replace('_', ' ')} "
                                 f"(the case: {expected})")
                    break
    if wrong:
        return _check("identity", "fail", "Same person on every form", "; ".join(wrong[:6]) + ": rebuild the packet.")
    if not read:  # nothing fillable to read back (the visa's answer sheet)
        return _check("identity", "info", "Same person on every form", "No filled USCIS form in this packet to read back.")
    return _check("identity", "pass", "Same person on every form", f"Name, A-Number and date of birth read back identically ({read} boxes checked).")


def _deadlines(client_dir: Path, filing: str, today: date) -> list[dict[str, str]]:
    out = []
    if filing == "i360":
        import i360

        from review.state import reviewed_graph

        dob = reviewed_graph(client_dir).get("applicant.dob")
        age = i360.age_check(dob.value if dob is not None and dob.status == "resolved" else None, today)
        level = {"blocking": "fail", "urgent": "warn", "check": "warn"}.get(age["level"], "pass")
        out.append(_check("age_21", level, "Before the 21st birthday", age["text"] + (" Send by courier with tracking." if level == "warn" else "")))
    if filing == "i589":  # the 1-year deadline, and USCIS (not the court) as the place to file
        import asylum

        from review.state import reviewed_graph

        g = asylum.derive(reviewed_graph(client_dir), today)
        y, where = asylum.one_year(g, today), asylum.where_to_file(g)
        level = {"late": "warn", "urgent": "warn", "check": "warn"}.get(y["level"], "pass")
        out.append(_check("one_year", level, "The 1-year deadline", y["text"] + (" Send by overnight courier with tracking." if y["level"] == "urgent" else "")))
        out.append(_check("where", "pass" if where["with"] == "uscis" else "fail", "Filed with USCIS", where["text"]))
    if filing == "n400":
        import naturalization

        e = naturalization.status(client_dir, today)["eligibility"]
        ready = e.get("file_from") and e["file_from"] <= today.isoformat()
        out.append(_check("n400_date", "pass" if ready else "fail", "Eligible to file today",
                          f"Can file from {e['file_from']}." if e.get("file_from") else "No filing date yet: " + " ".join(e.get("blockers") or [])))
    try:
        import journey

        soon = [d for d in journey.journey(client_dir, today)["deadlines"] if 0 <= d["days_left"] <= 14 and d["owner"] != "client"]
        if soon:
            send = (" File it so the Board receives it in time: ECAS, or a courier with tracking (no mailbox rule)." if filing == "bia" else
                    " File it so the court receives it in time: ECAS, the court's filing window, or a courier with tracking."
                    if filing in COURT_FILINGS else " Mail by courier with tracking if this filing is what's due.")
            out.append(_check("due_soon", "warn", "Due within two weeks",
                              "; ".join(f"{d['what']} ({_us(d['date'])})" for d in soon) + "." + send))
    except Exception:  # noqa: BLE001 -- the timeline is a help here, never a blocker
        pass
    return out


_NOT_A_MAIN_FORM = {"I-912", "I-864", "I-130A", "EOIR-27", "EOIR-28"}  # the fee waiver, the affidavit of support, the spouse's biographic form, notices of appearance


def package_forms(manifest: dict[str, Any]) -> list[str]:
    """The forms the packet is for, as USCIS and the court number them ("I-601", "I-212"): what the client's page says was sent. The
    attorney's appearance, the payment, the receipt e-mail form, the supplements and the worksheets are part of the package, not the filing."""
    out: list[str] = []
    for f in (manifest or {}).get("forms") or []:
        short = str(f.get("short") or "").strip()
        if re.fullmatch(r"(?:I|N)-\d{2,3}[A-Z]?|EOIR-\d+[A-Z]?", short) and short not in _NOT_A_MAIN_FORM and short not in out:
            out.append(short)
    return out


def record_filing(client_dir: Path, filing: str, mailed_on: str, carrier: str, tracking: str, who: str,
                  override: str | None = None, role: str | None = None, today: date | None = None) -> dict[str, Any]:
    """The mailing, recorded: refuses when a check fails unless the attorney gives the reason."""
    import packet as packet_mod

    if filing.startswith("rfe:"):  # a response to a USCIS request (src/rfe.py)
        import rfe

        return rfe.record(client_dir, filing[4:], mailed_on, carrier, tracking, who, override, role, today)
    mailed = validate_mailing(mailed_on, carrier, tracking, who, role, today)
    result = check(client_dir, filing, today)
    failed = [c for c in result["checks"] if c["level"] == "fail"]
    if failed and not str(override or "").strip():
        raise ValueError("Not ready to mail: " + "; ".join(f"{c['title']}: {c['text']}" for c in failed)
                         + ": fix these, or give the reason it's mailed anyway.")
    schema = packet_mod.for_case(packet_mod.load_filing(filing), client_dir)
    manifest = _read(client_dir / schema.get("manifest", "packet.json"), {})
    online = carrier == ONLINE_USCIS
    if online != bool(result.get("online")):  # the way it was filed is the way the packet was built for
        raise ValueError("This filing was prepared for the USCIS online account: choose that as how it was filed." if result.get("online") else
                         "This filing was prepared on paper: choose Online on the packet tab and build the online-filing bundle first.")
    record = {"filing": filing, "variant": schema.get("variant"), "title": schema.get("title"),
              **({"forms": package_forms(manifest)} if package_forms(manifest) else {}), "mailed_on": mailed.isoformat(), "carrier": carrier, "tracking": str(tracking or "").strip(),
              "mail_to": None if online else manifest.get("mail_to"), "fee": manifest.get("fee"), "packet_sha256": manifest.get("sha256"), "built_at": manifest.get("built_at"),
              "override": str(override).strip() if failed else None, "failed_checks": [c["title"] for c in failed]}
    if online:  # src/online_filing.py: what was uploaded, at the online fees; the receipt number when the case card shows it
        import online_filing

        bundle = _read(online_filing.bundle_paths(client_dir, filing)[1], {}) or {}
        record |= {"online": True, "tracking": "", "receipt": re.sub(r"[\s-]", "", str(tracking or "")).upper() or None, "bundle_sha256": bundle.get("sha256"),
                   "fee": "; ".join(f"Form {p['form']} {online_filing.money(p['online'])} (online)" for p in bundle.get("fees") or []) or None}
    return append_record(client_dir, record, who)


def add_receipt(client_dir: Path, receipt: str, who: str, filing: str | None = None) -> dict[str, Any]:
    """The receipt number of a filing made online, once USCIS shows it on the case card (the confirmation has none).
    Anyone may add it: it is read off the account, not a decision. The latest online filing without one (or of `filing`)."""
    if not who:
        raise ValueError("Enter your name first: the record says who added the receipt number.")
    receipt = re.sub(r"[\s-]", "", str(receipt or "")).upper()
    if not re.fullmatch(RECEIPT, receipt):
        raise ValueError("The receipt number: three letters and ten digits, as on the case card (e.g. IOE0123456789).")
    status = _read(client_dir / "status.json", {})
    records = [r for r in status.get("filings") or [] if r.get("online") and (filing is None or r.get("filing") == filing)]
    target = next((r for r in reversed(records) if not r.get("receipt")), records[-1] if records else None)
    if target is None:
        raise LookupError("No filing made in the USCIS online account is recorded on this case.")
    target |= {"receipt": receipt, "receipt_by": who, "receipt_at": clock.stamp()}
    (client_dir / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    events.record("filings", "receipt_added", f"Added the receipt number of an online filing: {_filing_name(target)}", case_dir=client_dir, who=who)
    return target


def validate_mailing(mailed_on: str, carrier: str, tracking: str, who: str, role: str | None, today: date | None = None) -> date:
    """The mailing's own details, the same for a packet and an RFE response."""
    if not who:
        raise ValueError("Enter your name first: the filing record says who mailed it.")
    if role == "paralegal":
        raise PermissionError("Only an attorney can record a filing as mailed.")
    try:
        mailed = date.fromisoformat(mailed_on)
    except (TypeError, ValueError):
        raise ValueError("The mailing date: YYYY-MM-DD.") from None
    if mailed > (today or clock.today()):
        raise ValueError("The mailing date can't be in the future.")
    if carrier not in CARRIERS:
        raise ValueError(f"The carrier: one of {', '.join(CARRIERS)}.")
    if carrier == "Online" and not str(tracking or "").strip():
        raise ValueError("The confirmation number the portal gave: it is the proof of the filing.")
    if carrier == ONLINE_USCIS:  # USCIS's confirmation carries no receipt number: it is added when the case card shows it
        given = re.sub(r"[\s-]", "", str(tracking or "")).upper()
        if given and not re.fullmatch(RECEIPT, given):
            raise ValueError("The receipt number: three letters and ten digits, as on the case card (or leave it empty until USCIS shows it).")
        return mailed
    if carrier not in ("Hand delivery", "Online") and not str(tracking or "").strip():
        raise ValueError("The tracking number: it is the proof of when USCIS received the filing.")
    return mailed


def _filing_name(record: dict[str, Any]) -> str:
    """The filing in words for the event ledger: the record's title, else the packet's (never the filing's id)."""
    if record.get("title"):
        return str(record["title"])
    import packet

    return packet.filing_title(record.get("filing"))


def append_record(client_dir: Path, record: dict[str, Any], who: str, main_filing: bool = True) -> dict[str, Any]:
    """Adds a mailing to status.json. main_filing: a packet (the dashboard's "Filed" stage) rather than a response."""
    status = _read(client_dir / "status.json", {})
    at = clock.stamp()
    record = record | {"by": who, "at": at}
    status.setdefault("filings", []).append(record)
    if main_filing:
        status.update(filed_at=at, filed_by=who)
    (client_dir / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    events.record("filings", "mailed", f"Recorded as filed: {_filing_name(record)}", case_dir=client_dir, who=who)  # not the date, carrier or tracking number
    return record


def undo_filing(client_dir: Path, who: str, role: str | None = None) -> dict[str, Any]:
    if role == "paralegal":
        raise PermissionError("Only an attorney can remove a filing record.")
    status = _read(client_dir / "status.json", {})
    filings = status.get("filings") or []
    if filings:
        removed = filings.pop()
        status.setdefault("removed_filings", []).append(removed | {"removed_by": who, "removed_at": clock.stamp()})
    if filings:
        status.update(filed_at=filings[-1]["at"], filed_by=filings[-1]["by"])
    else:
        status.pop("filed_at", None)
        status.pop("filed_by", None)
    (client_dir / "status.json").write_text(json.dumps(status, indent=1), encoding="utf-8")
    events.record("filings", "removed", "Removed the latest filing record", case_dir=client_dir, who=who, role=role)
    return status


def filings(client_dir: Path) -> list[dict[str, Any]]:
    return _read(client_dir / "status.json", {}).get("filings") or []
