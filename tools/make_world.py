"""Builds a made-up firm at size, to see the product work with thousands of cases before a firm buys it (docs/deployment.md, "1,800 cases").

    python tools/make_world.py --cases 2000 --out /path/to/folder          the firm of 2,000 cases below
    python tools/make_world.py --cases 300 --out /path/to/folder --seed 7  a smaller one, another firm (same seed, same firm)

Everything in it is made up ("Ana Clara Exemplo Souza"): nobody's name, number or document. It is built by writing the records the product keeps (the ones
docs/data_dictionary.md lists) straight into folders, never by reading a document, so two thousand cases take minutes, not days. Two runs with the same seed
build the same firm. It builds on tests/firm_world.py (the made-up firm of the data tests): that file writes one case's records, this one writes thousands of
them, with a made-up portal, staff, a view log, a ledger and a notice inbox around them.

What it makes under the folder:

    data/clients/<case>/          each case's records (facts, documents, decisions, status, access), and the notice an overnight run would have left
    data/portal/clients/<case>/   the client's portal folder (profile, answers, messages, requests, uploads) for most of them
    data/review_users.json        the staff: 2 signed in for the measurements (an attorney and a paralegal, in world.json) and 40 more
    data/review_views.jsonl       the view log: who opened what (200,000 rows by default)
    data/events-YYYY-MM.jsonl     the event ledger (500,000 rows by default), a month a file
    data/inbox/                   the notice inbox: notices waiting for a person, and the log of what was routed
    clients/<case>/source/        a one-page scan for the first --sources cases, so the overnight run has something to read
    world.json                    where everything is, the staff's sign-ins and the environment a review app needs to run on this folder

Around 300 of the 2,000 cases are restricted (the law's: VAWA, T, U and asylum; and the ones an attorney marked, some with a paralegal named on them).
"""

from __future__ import annotations

import argparse
import json
import random
import sys
import time
from concurrent.futures import ThreadPoolExecutor
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

REPO = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(REPO / "src"))
import schema_path  # noqa: E402
sys.path.insert(0, str(REPO / "tests"))

import firm_world  # noqa: E402  (tests/firm_world.py: one case's records)

SEED = 485
GIVEN = ("Ana Clara", "Rosa", "Marcos", "Luana", "Tiago", "Beatriz", "Caio", "Marisol", "Joel", "Ines", "Davi", "Lia", "Mateo", "Sofia", "Elias", "Camila", "Renan", "Yara",
         "Nilo", "Alma", "Kauan", "Dalila", "Ivo", "Tais", "Orlando", "Jade", "Wesley", "Vera", "Otavio", "Nara", "Gael", "Luz", "Bento", "Aline", "Ruan", "Mara", "Jonas", "Celia",
         "Fabio", "Dina")
FAMILY = ("Exemplo Souza", "Exemplo Lima", "Exemplo Costa", "Exemplo Rocha", "Exemplo Pires", "Exemplo Duarte", "Exemplo Barros", "Exemplo Teles", "Exemplo Neves", "Exemplo Moura",
          "Exemplo Cardoso", "Exemplo Nunes", "Exemplo Faria", "Exemplo Matos", "Exemplo Prado", "Exemplo Viana", "Exemplo Borges", "Exemplo Lopes", "Exemplo Santos", "Exemplo Reis")
STAFF_GIVEN = ("Paulo", "Joana", "Bruno", "Carla", "Diego", "Elisa", "Fernando", "Gisele", "Hugo", "Irene", "Julio", "Karen", "Leandro", "Monica", "Nelson", "Olga", "Pedro", "Quiteria",
               "Rafael", "Sandra", "Tomas", "Ursula", "Victor", "Wanda", "Xavier", "Yolanda", "Zeca", "Alice", "Breno", "Clara")
STAFF_FAMILY = ("Staffexemplo", "Atendente", "Revisor", "Assistente", "Secretaria", "Estagio")
OFFICES = ("Boston", "Miami", "Orlando", "Worcester")
LANGUAGES = ("pt", "pt", "pt", "es", "es", "en", "ht")
# the kinds of case, with how many of 100 each is; the first four are the law's restricted ones (the rest of the 300 are an attorney's marks on any kind)
TRACK_MIX = (("sij", 46), ("family", 18), ("naturalization", 6), ("daca", 4), ("caa", 3), ("vawa", 4), ("asylum", 5), ("t_visa", 1), ("u_visa", 1))
WORDS = ("passport", "birth", "mother", "father", "court", "order", "juvenile", "custody", "school", "record", "entry", "arrival", "address", "notice", "receipt", "approval", "hearing",
         "judge", "county", "state", "guardian", "affidavit", "evidence", "signature", "certificate", "translation", "photograph", "identity", "residence", "employment", "fingerprint",
         "medical", "vaccination", "report", "statement", "petition", "application", "interview", "biometrics", "appointment")
LEDGER_KINDS = (("facts", "read", "Read the facts from a document"), ("documents", "set", "Set whose a document is"), ("decisions", "confirm", "Confirmed an answer"),
                ("decisions", "correct", "Corrected an answer"), ("journey", "mark", "Marked a step done"), ("filings", "mailed", "Recorded a filing as mailed"),
                ("packet", "built", "Built the filing packet"), ("office", "set", "Set the case's office"), ("translations", "made", "Made a translation"),
                ("portal", "answered", "The client answered questions"), ("portal", "uploaded", "The client sent a photo"), ("imports", "added", "Added a document to the case"))
VIEW_KINDS = ("case", "case", "case", "case", "scan", "scan", "document", "documents", "documents", "packet", "filled_form", "answers", "review_bundle", "translation")
VIEW_WEIGHTS_RESTRICTED = 0.12  # share of the view log's rows that are on a restricted case
LEDGER_FIRST = date(2026, 5, 1)  # the ledger and the view log run from here to NOW
NOW = datetime(2026, 10, 3, 8, 30, tzinfo=timezone(timedelta(hours=-4)))
PASSWORDS = {"attorney": ("attorney@world.example", "Ana Attorneyexemplo", "world-attorney-pass-1"), "paralegal": ("paralegal@world.example", "Paulo Paralegalexemplo", "world-paralegal-pass-1")}


def _blank_pdf() -> bytes:
    """One blank page, as a real PDF (the scan the overnight run reads, and a notice waiting in the inbox)."""
    import io

    from pypdf import PdfWriter

    writer, out = PdfWriter(), io.BytesIO()
    writer.add_blank_page(width=612, height=792)
    writer.write(out)
    return out.getvalue()


BLANK_PDF = _blank_pdf()


def slug(text: str) -> str:
    return "-".join(text.lower().split())


def pick_track(rng: random.Random) -> str:
    n = rng.randrange(100)
    for track, share in TRACK_MIX:
        if n < share:
            return track
        n -= share
    return "sij"


def case_plan(cases: int, restricted: int, seed: int) -> list[dict]:
    """Who each case is, by a pattern that does not change with the order the cases are written in: its id, name, kind of case, office, language and
    whether it is restricted (by the law's track, or by an attorney's mark)."""
    rng = random.Random(f"{seed}:plan")
    plan = []
    for i in range(cases):
        r = random.Random(f"{seed}:case:{i}")
        given, family = GIVEN[r.randrange(len(GIVEN))], FAMILY[r.randrange(len(FAMILY))]
        track = pick_track(r)
        plan.append({"n": i, "id": f"{slug(given + ' ' + family)}-{i:04d}", "given": given, "family": family, "track": track, "office": OFFICES[r.randrange(len(OFFICES))],
                     "language": LANGUAGES[r.randrange(len(LANGUAGES))], "restricted": None})
    law = [p for p in plan if p["track"] in ("vawa", "asylum", "t_visa", "u_visa")]
    for p in law:
        p["restricted"] = "law"
    others = [p for p in plan if p["restricted"] is None and p["track"] in ("sij", "family")]
    rng.shuffle(others)
    for p in others[: max(0, restricted - len(law))]:
        p["restricted"] = "marked"
    if len(law) > restricted:  # a small world: the law's cases are never more than the number asked for
        for p in law[restricted:]:
            p["track"], p["restricted"] = "sij", None
    return plan


def value_for(key: str, r: random.Random) -> str:
    """A made-up value of the kind a fact's key asks for (a date where it is a date, so the case's own cross-checks can read it)."""
    last = key.split(".")[-1]
    if last.endswith("dob") or "date" in last:
        return f"{1960 + r.randrange(45)}-{1 + r.randrange(12):02d}-{1 + r.randrange(28):02d}"
    if "state" in last:
        return ("MA", "FL")[r.randrange(2)]
    if "number" in last or last.endswith("_no"):
        return "".join(str(r.randrange(10)) for _ in range(9))
    return "MADE UP " + last.upper()


def stamp(moment: datetime) -> str:
    return moment.isoformat(timespec="seconds")


def staff_plan(staff: int) -> list[tuple[str, str, str]]:
    """(email, name, role) for the staff beyond the two who sign in: one attorney in six, the rest paralegals."""
    out = []
    for i in range(staff):
        name = f"{STAFF_GIVEN[i % len(STAFF_GIVEN)]} {STAFF_FAMILY[i // len(STAFF_GIVEN) % len(STAFF_FAMILY)]}"
        out.append((f"staff{i:02d}@world.example", name, "attorney" if i % 6 == 0 else "paralegal"))
    return out


def write_case(world: dict, p: dict) -> None:
    """One case's records on top of tests/firm_world.make_case (the same shapes), with a fuller fact graph, the case's kind of track and office, and its
    restriction. Everything is chosen by the case's own seed."""
    r = random.Random(f"{world['seed']}:rec:{p['n']}")
    clients = world["clients"]
    law = {"vawa": "1367", "t_visa": "1367", "u_visa": "1367", "asylum": "208.6"}.get(p["track"]) if p["restricted"] == "law" else None
    d = firm_world.make_case(clients, p["id"], docs=3 + r.randrange(9), facts=6, decisions=r.randrange(7), filings=1 if r.randrange(7) == 0 else 0,
                             restricted=law, expires=(date(2026, 11, 1) + timedelta(days=r.randrange(900))).isoformat() if r.randrange(3) == 0 else None)
    # a fuller fact graph: the facts a reading leaves (keys from the I-485's own map), the name of the person, an office's state
    graph = json.loads((d / "fact_graph.json").read_text(encoding="utf-8"))
    for key in r.sample(world["fact_keys"], min(len(world["fact_keys"]), 30 + r.randrange(40))):
        graph["facts"][key] = firm_world.fact(key, value_for(key, r), tier=1 + r.randrange(3), sources=1 + r.randrange(2), status="resolved" if r.randrange(12) else "conflict")
    graph["facts"]["applicant.dob"] = firm_world.fact("applicant.dob", f"{2004 + r.randrange(14)}-{1 + r.randrange(12):02d}-{1 + r.randrange(28):02d}")
    graph["facts"]["applicant.given_name"] = firm_world.fact("applicant.given_name", p["given"].upper())
    graph["facts"]["applicant.family_name"] = firm_world.fact("applicant.family_name", p["family"].upper())
    graph["facts"]["applicant.physical_state"] = firm_world.fact("applicant.physical_state", "FL" if p["office"] in ("Miami", "Orlando") else "MA")
    text = json.dumps(graph)
    (d / "fact_graph.json").write_text(text, encoding="utf-8")
    (d / "fact_graph_raw.json").write_text(text, encoding="utf-8")
    # documents: some words of text each, so Search has something to find
    records = json.loads((d / "documents.json").read_text(encoding="utf-8"))
    for rec in records["documents"]:
        rec["text"] = " ".join(r.choice(WORDS) for _ in range(60 + r.randrange(120)))
        rec["person"] = ("applicant", "applicant", "applicant", "mother", "father", "petitioner")[r.randrange(6)]
    (d / "documents.json").write_text(json.dumps(records), encoding="utf-8")
    # status: the kind of case, a step or two, the filing the I-485 or I-360
    status = json.loads((d / "status.json").read_text(encoding="utf-8")) if (d / "status.json").exists() else {}
    status.setdefault("journey", {})["track"] = {"value": p["track"], "by": "Ana Attorneyexemplo", "at": stamp(NOW - timedelta(days=r.randrange(120))), "note": None}
    if r.randrange(5) == 0:
        status["filed_at"], status["filed_by"] = stamp(NOW - timedelta(days=r.randrange(60))), "Ana Attorneyexemplo"
    (d / "status.json").write_text(json.dumps(status), encoding="utf-8")
    if p["n"] % 6 == 0:  # a sixth of the cases have a card that waits for an attorney (an alert from the reading): the attorney's queue has something to hold (src/approvals.py)
        (d / "reading_flags.json").write_text(json.dumps([{"level": "review", "fact_key": "folder.criminal_history", "kind": "alert",
                                                           "message": "CRIMINAL HISTORY: the folder shows a made-up citation for the attorney to read."}]), encoding="utf-8")
    if p["office"]:
        (d / "office.json").write_text(json.dumps({"office": p["office"], "by": "Ana Attorneyexemplo", "at": stamp(NOW - timedelta(days=30))}), encoding="utf-8")
    if p["restricted"] == "marked":
        chosen = world["named"][:1] if p["n"] % 5 == 0 else world["named"][1: 1 + r.randrange(3)]  # the world's paralegal is named on a fifth of them: she sees those and not the rest
        people = [{"email": e, "name": n, "by": "Ana Attorneyexemplo", "at": stamp(NOW - timedelta(days=3))} for e, n in chosen]
        (d / "access.json").write_text(json.dumps({"marked": {"on": True, "by": "Ana Attorneyexemplo", "at": stamp(NOW - timedelta(days=9)), "reason": "The client is a minor.", "law": None},
                                                   "people": people, "messages": None, "history": [{"at": stamp(NOW - timedelta(days=9)), "by": "Ana Attorneyexemplo",
                                                                                                      "what": "Restricted the case", "reason": "The client is a minor."}]}), encoding="utf-8")
    elif p["restricted"] == "law" and (p["n"] % 5 == 0 or r.randrange(4) == 0):  # a few the law keeps closed also have a paralegal named on them
        e, n = world["named"][0] if p["n"] % 5 == 0 else world["named"][r.randrange(len(world["named"]))]
        (d / "access.json").write_text(json.dumps({"marked": None, "people": [{"email": e, "name": n, "by": "Ana Attorneyexemplo", "at": stamp(NOW - timedelta(days=3))}],
                                                   "messages": None, "history": []}), encoding="utf-8")
    if p["n"] < world["sources"]:  # the scan the overnight run reads, and where the case's records say it is
        src = world["inputs"] / p["id"] / "source"
        src.mkdir(parents=True, exist_ok=True)
        for k in range(2):
            (src / f"scan-{k}.pdf").write_bytes(BLANK_PDF)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        meta["source_folder"] = str(src)
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    elif p["n"] % 3 == 1:  # a third of the rest have the papers the packet asks for in their folder (an I-360 approval and a birth certificate), so no paper is left for the client to send:
        # their packet is held by the office (the review cards) and the attorney (an alert), the others also by the client (Today, src/day_plan.py: each holder is in the world)
        src = world["inputs"] / p["id"] / "source"
        src.mkdir(parents=True, exist_ok=True)
        papers = {"approval.pdf": "i360_approval", "birth.pdf": "birth_certificate"}
        for name in papers:
            (src / name).write_bytes(BLANK_PDF)
        meta = json.loads((d / "meta.json").read_text(encoding="utf-8"))
        meta["source_folder"], meta["classifications"] = str(src), papers
        (d / "meta.json").write_text(json.dumps(meta), encoding="utf-8")
    # the portal's folder: most clients have one
    if p["n"] % 20 != 19:
        write_portal(world, p, r)


def write_portal(world: dict, p: dict, r: random.Random) -> None:
    folder = world["portal"] / "clients" / p["id"]
    folder.mkdir(parents=True, exist_ok=True)
    status = ("invited", "answering", "answering", "submitted", "submitted", "submitted")[r.randrange(6)]
    profile = {"id": p["id"], "name": f"{p['given']} {p['family']}", "phone": "", "email": f"{p['id']}@client.example", "language": p["language"],
               "consent": {"email": True, "sms": False, "whatsapp": False}, "status": status, "created_at": stamp(NOW - timedelta(days=60 + r.randrange(60))),
               "filing": "n400" if p["track"] == "naturalization" else "i485"}
    if status != "invited" or r.randrange(2):
        profile["invited_at"] = stamp(NOW - timedelta(days=50 + r.randrange(40)))
    (folder / "profile.json").write_text(json.dumps(profile), encoding="utf-8")
    if status != "invited":
        (folder / "answers.json").write_text(json.dumps({f"applicant.{k}": "MADE UP" for k in ("given_name", "family_name", "date_of_birth")[: 1 + r.randrange(3)]}), encoding="utf-8")
    (folder / "events.jsonl").write_text(json.dumps({"at": stamp(NOW - timedelta(days=r.randrange(40))), "event": "signed_in"}) + "\n", encoding="utf-8")
    if r.randrange(25) == 0:
        (folder / "messages.json").write_text(json.dumps([{"id": "m1", "from": "client", "at": stamp(NOW - timedelta(days=r.randrange(5))), "text": "Made up question about my case.",
                                                          "status": "new"}]), encoding="utf-8")
    if r.randrange(30) == 0:
        (folder / "tasks.json").write_text(json.dumps([{"id": "t1", "kind": "retake", "doc_id": "passport", "doc_en": "Passport", "why_en": "the photo was blurry",
                                                       "asked_at": stamp(NOW - timedelta(days=2)), "received_at": None}]), encoding="utf-8")
    if r.randrange(40) == 0:
        (folder / "uploads.json").write_text(json.dumps([{"doc_id": "passport", "filename": "passport-0123456789abcdef.pdf", "at": stamp(NOW - timedelta(hours=3)), "retake": False,
                                                          "reading": None}]), encoding="utf-8")


def write_staff(world: dict) -> None:
    """data/review_users.json: the attorney and the paralegal the measurements sign in as (a password each, the attorney's second factor remembered on a device)
    and the rest of the staff, who share one made-up password's hash (nobody signs in as them: they are who the logs name)."""
    from review import totp
    from review.auth import Accounts

    import clock

    users = Accounts(world["users"])
    out = {}
    for role, (email, name, password) in PASSWORDS.items():
        users.add(email, name, role)
        users._set_password(email, password, must_change=False)
        out[role] = {"email": email, "name": name, "password": password}
    token, _ = users.sign_in(PASSWORDS["attorney"][0], PASSWORDS["attorney"][2])
    letters = users.enrol_start(token, "Case Review")["letters"].replace(" ", "")
    secret = __import__("base64").b32decode(letters + "=" * (-len(letters) % 8))
    now = clock.utcnow().timestamp()
    users.enrol_finish(token, totp.code_at(secret, now))
    token, _ = users.sign_in(PASSWORDS["attorney"][0], PASSWORDS["attorney"][2])
    out["attorney"]["device"] = users.verify_code(token, totp.code_at(secret, now + totp.STEP), remember=True)["device"]
    data = json.loads(world["users"].read_text(encoding="utf-8"))
    template = data["users"][PASSWORDS["paralegal"][0]]
    for email, name, role in staff_plan(world["staff"]):
        data["users"][email] = {"name": name, "role": role, "active": True, "created_at": stamp(NOW - timedelta(days=200)), **{k: template[k] for k in ("salt", "hash", "scrypt")},
                                "must_change": False, "failures": 0, "locked_until": None}
    data["sessions"] = {}
    world["users"].write_text(json.dumps(data), encoding="utf-8")
    world["people"] = out


def write_logs(world: dict, plan: list[dict]) -> None:
    """The view log and the ledger: rows spread over the months from LEDGER_FIRST to the day of the world, oldest first (a month's file each for the ledger)."""
    r = random.Random(f"{world['seed']}:logs")
    people = [(e, n, role) for e, n, role in staff_plan(world["staff"])] + [(v["email"], v["name"], k) for k, v in world["people"].items()]
    closed = {p["id"] for p in plan if p["restricted"]}
    ids = [p["id"] for p in plan]
    open_ids = [i for i in ids if i not in closed] or ids
    closed_ids = sorted(closed) or ids
    span = (NOW - datetime.combine(LEDGER_FIRST, datetime.min.time(), tzinfo=NOW.tzinfo)).total_seconds()

    def moments(n: int) -> list[datetime]:
        first = datetime.combine(LEDGER_FIRST, datetime.min.time(), tzinfo=NOW.tzinfo)
        return [first + timedelta(seconds=s) for s in sorted(r.uniform(0, span) for _ in range(n))]

    views = world["data"] / "review_views.jsonl"
    with open(views, "w", encoding="utf-8") as f:
        for at in moments(world["views"]):
            email, name, role = people[r.randrange(len(people))]
            hidden = r.random() < VIEW_WEIGHTS_RESTRICTED
            case = r.choice(closed_ids if hidden else open_ids)
            kind = r.choice(VIEW_KINDS)
            f.write(json.dumps({"at": stamp(at), "email": email, "name": name, "role": role, "client": case, "kind": kind, "file": f"scan-{r.randrange(9)}.pdf" if kind in ("scan", "document") else None,
                                "address": "10.0.0." + str(1 + r.randrange(40))} | ({"restricted": True} if hidden else {}), ensure_ascii=False) + "\n")
    handles: dict[str, object] = {}
    try:
        for at in moments(world["ledger"]):
            email, name, role = people[r.randrange(len(people))]
            kind, action, what = LEDGER_KINDS[r.randrange(len(LEDGER_KINDS))]
            via = "portal" if kind == "portal" else "overnight" if kind in ("facts", "imports") and r.randrange(2) else "staff"
            who = "The client" if kind == "portal" else "The overnight run" if via == "overnight" else name
            row = {"at": stamp(at), "who": who, "role": "client" if kind == "portal" else "system" if via == "overnight" else role, "via": via, "case": r.choice(ids), "kind": kind,
                   "version": 1, "action": action, "what": what}
            month = stamp(at)[:7]
            if month not in handles:
                handles[month] = open(world["data"] / f"events-{month}.jsonl", "w", encoding="utf-8")
            handles[month].write(json.dumps(row, ensure_ascii=False, separators=(",", ":")) + "\n")
    finally:
        for h in handles.values():
            h.close()
    (world["data"] / "events.jsonl").write_text("", encoding="utf-8")  # the ledger's name (its rows are in the month files)


def write_inbox(world: dict, plan: list[dict]) -> None:
    inbox = world["data"] / "inbox"
    for sub in ("waiting", "not_ours", "originals"):
        (inbox / sub).mkdir(parents=True, exist_ok=True)
    r = random.Random(f"{world['seed']}:inbox")
    queue = []
    for i in range(60):
        cands = r.sample(plan, 2)
        name = f"notice-{i:03d}.pdf"
        (inbox / "waiting" / name).write_bytes(BLANK_PDF)
        queue.append({"id": f"{i:012x}", "hash": f"{i:064x}", "file": name, "original": name, "pages": [1], "of": 1, "scanned": "2026-10-01",
                      "read": {"form": "I-797C", "kind": "receipt", "names": [f"{cands[0]['given']} {cands[0]['family']}".upper()], "a_number": None},
                      "what": "A receipt notice", "status": "ambiguous", "why": "Two cases have this name.",
                      "candidates": [{"case": c["id"], "name": f"{c['given']} {c['family']}"} for c in cands], "queued_at": stamp(NOW - timedelta(days=r.randrange(5)))})
    (inbox / "queue.json").write_text(json.dumps(queue), encoding="utf-8")
    with open(inbox / "inbox_log.jsonl", "w", encoding="utf-8") as f:
        for i in range(3000):
            c = plan[r.randrange(len(plan))]
            if c["restricted"]:
                continue
            f.write(json.dumps({"at": stamp(NOW - timedelta(minutes=r.randrange(60 * 24 * 30))), "event": "routed", "case": c["id"], "name": f"{c['given']} {c['family']}", "how": "receipt number",
                                "what": "A receipt notice", "read": {"kind": "receipt"}}) + "\n")


def write_paths(world: dict, plan: list[dict]) -> dict[str, str]:
    """Two cases with a path of their own, approved by the world's attorney (src/path.py): the first SIJ case without the visa wait and with the I-131 left
    out (a filing removed), and the first family case with the state-court step added before the family petition (a court step added). Returns {case: which}."""
    import path as own_path

    stages = json.loads(schema_path.path("register", "journey").read_text(encoding="utf-8"))["stages"]
    out = {}
    plans = (("sij", "filing_removed", lambda t: [x for x in t if x != "visa_wait"], ["i131"], "The client already has a visa date: no wait; no travel."),
             ("family", "court_added", lambda t: [*t[:t.index("family_ready")], "state_court", *t[t.index("family_ready"):]] if "family_ready" in t else t, [],
              "A state-court order is needed before the family petition."))
    for track, which, shape, removed, reason in plans:
        p = next((x for x in plan if x["track"] == track and not x["restricted"]), None)
        if p is None:
            continue
        template = [x for x in stages[track] if x != "consular"]
        steps = [{"id": x, "date": None} for x in shape(template)]
        at = stamp(NOW - timedelta(days=2))
        change = own_path.change_words({"steps": [{"id": x, "date": None} for x in template], "removed": []}, {"steps": steps, "removed": removed})
        approved = {"steps": steps, "removed": removed, "by": "Ana Attorneyexemplo", "by_email": PASSWORDS["attorney"][0], "role": "attorney", "at": at,
                    "reason": reason, "change": change, "approved_by": "Ana Attorneyexemplo", "approved_at": at}
        (world["clients"] / p["id"] / "path.json").write_text(json.dumps({"version": 1, "approved": approved, "proposed": None,
                                                                          "history": [{"at": at, "by": "Ana Attorneyexemplo", "action": "approved", "change": change, "reason": reason}]}),
                                                              encoding="utf-8")
        out[p["id"]] = which
    return out


def write_firm(world: dict) -> None:
    data = world["data"]
    (data / "settings.json").write_text(json.dumps({"firm": {"values": {"firm_name": "Exemplo Law (made up)"}, "updated_by": "Ana Attorneyexemplo", "updated_at": stamp(NOW), "history": []}}), encoding="utf-8")
    for name, body in (("policies_firm.json", {"edits": {}}), ("rules_approved.json", {}), ("maintenance_log.json", {}), ("deployment.json", {})):
        (data / name).write_text(json.dumps(body), encoding="utf-8")
    (data / "getting_started.json").write_text(json.dumps({"seen": {world["people"]["attorney"]["email"]: stamp(NOW)}}), encoding="utf-8")
    (data / "portal").mkdir(exist_ok=True)


def environment(world: dict) -> dict[str, str]:
    """The environment a review app needs to run on this world and nothing else of this computer's (the variables the tests set in tests/conftest.py)."""
    data, root = world["data"], world["root"]
    return {"PORTAL_DATA": str(world["portal"]), "I485_SETTINGS": str(data / "settings.json"), "I485_MAINTENANCE_LOG": str(data / "maintenance_log.json"),
            "I485_DEPLOYMENT": str(data / "deployment.json"), "I485_LIVE_STATUS": str(data / "maintenance_status.json"), "I485_RULES_APPROVED": str(data / "rules_approved.json"),
            "I485_POLICIES_FIRM": str(data / "policies_firm.json"), "I485_EVENTS": str(data / "events.jsonl"), "I485_QUERY_DB": str(data / "query.db"),
            "I485_INDEX": str(data / "index.db"), "I485_INBOX": str(data / "inbox"), "I485_CASES": str(world["clients"]), "I485_ACCURACY": "0", "I485_SHADOW": "0",
            "I485_LIVE_CHECKS": "0", "I485_JOBS": str(data / "jobs"), "I485_ACCURACY_HISTORY": str(data / "accuracy_history.jsonl"), "I485_REFERENCE": str(root / "reference")}


def build(out: str | Path, cases: int = 2000, seed: int = SEED, views: int = 200_000, ledger: int = 500_000, restricted: int = 300, staff: int = 40,
          sources: int = 200, workers: int = 8, log=print) -> dict:
    """The firm, from nothing, under `out` (which must not exist or be empty). Returns what world.json holds."""
    out = Path(out).resolve()
    if out.exists() and any(out.iterdir()):
        raise SystemExit(f"{out} already has something in it: choose a new folder.")
    started = time.time()
    data = out / "data"
    world = {"seed": seed, "root": out, "data": data, "clients": data / "clients", "portal": data / "portal", "inputs": out / "clients", "users": data / "review_users.json",
             "views": views, "ledger": ledger, "staff": staff, "sources": min(sources, cases)}
    for key in ("clients", "portal", "inputs"):
        world[key].mkdir(parents=True, exist_ok=True)
    (world["portal"] / "clients").mkdir(exist_ok=True)
    write_staff(world)
    world["named"] = [(e, n) for e, n, role in staff_plan(staff) if role == "paralegal"][:8] or [(PASSWORDS["paralegal"][0], PASSWORDS["paralegal"][1])]
    world["named"] = [(PASSWORDS["paralegal"][0], PASSWORDS["paralegal"][1])] + world["named"]
    keys = sorted(json.loads((schema_path.path("field_map", "i485")).read_text(encoding="utf-8"))["fact_to_acroform"])
    world["fact_keys"] = keys
    plan = case_plan(cases, restricted, seed)
    log(f"{len(plan)} cases ({sum(1 for p in plan if p['restricted'])} restricted)...")
    with ThreadPoolExecutor(max_workers=workers) as pool:
        list(pool.map(lambda p: write_case(world, p), plan))
    log(f"records written in {time.time() - started:.0f} s; the logs...")
    write_logs(world, plan)
    write_inbox(world, plan)
    write_firm(world)
    paths = write_paths(world, plan)
    manifest = {"seed": seed, "cases": cases, "restricted": sum(1 for p in plan if p["restricted"]), "views": views, "ledger": ledger, "staff": staff + 2, "built_seconds": round(time.time() - started),
                "data": str(data), "clients": str(world["clients"]), "inputs": str(world["inputs"]), "portal": str(world["portal"]), "users": str(world["users"]),
                "people": world["people"], "env": environment(world), "case_ids": [p["id"] for p in plan], "restricted_ids": [p["id"] for p in plan if p["restricted"]], "paths": paths}
    (out / "world.json").write_text(json.dumps(manifest, indent=1), encoding="utf-8")
    log(f"built {out} in {time.time() - started:.0f} s")
    return manifest


def main(argv: list[str] | None = None) -> None:
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("--cases", type=int, default=2000)
    ap.add_argument("--out", type=Path, required=True, help="a new folder")
    ap.add_argument("--seed", type=int, default=SEED)
    ap.add_argument("--views", type=int, default=200_000, help="rows in the view log")
    ap.add_argument("--ledger", type=int, default=500_000, help="rows in the event ledger")
    ap.add_argument("--restricted", type=int, default=300, help="restricted cases")
    ap.add_argument("--staff", type=int, default=40)
    ap.add_argument("--sources", type=int, default=200, help="cases that get a scan for the overnight run to read")
    args = ap.parse_args(argv)
    build(args.out, args.cases, args.seed, args.views, args.ledger, min(args.restricted, args.cases), args.staff, args.sources)


if __name__ == "__main__":
    main()
