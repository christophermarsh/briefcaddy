"""The attorney's queue: every open item only an attorney may approve, across every case, on one screen (My approvals).

Each kind of item registers itself here (register); the screen is drawn from what the registry says (its kinds, their words) and from the
items it finds, so a kind the product adds later appears on the screen with no change to it. A kind is one of two shapes:

  - a case kind: a function of one case (CaseContext) that returns that case's open items. It runs when the roster reads the case
    (src/review/roster.py keeps the result in the case's entry, so a list of 2,000 cases reads no case folder), and again whenever the
    case changes;
  - a firm kind: a function of the reader (FirmContext) that returns the firm's own open items (a practice waiting for approval, a
    wording from a past filing): read when the page is asked for.

An item says what it is in plain English, why it needs an attorney (the item's own words), who raised it and when, and the actions it
already has. An action is the item's own route and the body to send it, exactly as the case page sends it: this module and the screen add no
way to write anything. Nothing here writes.

    item = {"ref": the item's name inside its case (or the firm), "what", "why", "detail" (optional, the long words), "by", "by_email", "at",
            "actions": [action], "focus": {"tab", "card"} (where "open the case" lands)}
    action = {"id", "label", "route", "body", "inputs": [{"name", "label", "type" ("text", "voice", "reason"), "required"}], "ask": false}

An input's value goes into the body under its name. The queue adds two actions to every case item: ask the paralegal (a task on the case,
src/case_notes.py, with a note) and open the case at the item.

A restricted case's items are shown to the attorneys named on that case and to no one else, and counted nowhere else (queue's `visible`).
"""

from __future__ import annotations

import sys
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Callable

import clock

PAGE = 50
ASK_DAYS = 2  # the task "ask the paralegal" makes is due this many days out
PRODUCT = "The product's own checks"  # raised by no person: the reading, a rule, a firm setting
MAX_TEXT = 600  # characters of an item's "what" and "why" the queue holds (the long words are in "detail")

KINDS: dict[str, dict[str, Any]] = {}


def register(kind: str, label: str, plural: str, scope: str, gather: Callable, order: int | None = None) -> None:
    """Add a kind of item. scope "case": gather(CaseContext) -> items; scope "firm": gather(FirmContext) -> items. order: where the kind sits among the others
    (the screen lists kinds in this order; later registrations go last)."""
    if scope not in ("case", "firm"):
        raise ValueError("A kind's scope is case or firm.")
    KINDS[kind] = {"id": kind, "label": label, "plural": plural, "scope": scope, "gather": gather,
                   "order": order if order is not None else 100 + len(KINDS)}


def kinds() -> list[dict[str, str]]:
    """The registry as the screen needs it: id, the singular and the plural words, in order."""
    return [{"id": k["id"], "label": k["label"], "plural": k["plural"], "scope": k["scope"]} for k in sorted(KINDS.values(), key=lambda k: k["order"])]


def action(id: str, label: str, route: str, body: dict[str, Any], inputs: list[dict[str, Any]] | None = None) -> dict[str, Any]:
    return {"id": id, "label": label, "route": route, "body": body, "inputs": inputs or []}


def _sentence(text: Any) -> str:
    text = str(text or "").strip()
    return text[:1].upper() + text[1:]


def _short(text: Any, limit: int = MAX_TEXT) -> str:
    text = " ".join(str(text or "").split())
    return text if len(text) <= limit else text[:limit - 1].rstrip() + "…"


# -- a case's items --------------------------------------------------------------------------------------------------------------------------


class CaseContext:
    """One case, read once for every kind that asks (the case's cards and reviewed graph are the costly reads: each is made when a kind first needs it)."""

    def __init__(self, client_dir: Path, row: dict[str, Any], field_map: dict, template: str | Path, catalog):
        self.dir, self.case, self.row = Path(client_dir), Path(client_dir).name, row
        self.field_map, self.template, self.catalog = field_map, template, catalog
        self._items: dict[str, Any] | None = None
        self._graph = None
        self._decisions: dict[str, Any] | None = None
        self.since = row.get("processed_at") or row.get("last_decision")  # when the product last read the case: what an item no person raised dates from

    def items(self) -> dict[str, Any]:
        if self._items is None:
            from review.state import build_items

            self._items = build_items(self.dir, self.field_map, self.template, self.catalog, pending=False)
        return self._items

    def graph(self):
        if self._graph is None:
            from review.state import reviewed_graph

            self._graph = reviewed_graph(self.dir)
        return self._graph

    def decisions(self) -> dict[str, Any]:
        if self._decisions is None:
            from review.state import load_decisions

            self._decisions = load_decisions(self.dir)
        return self._decisions

    def may_answer_yes(self) -> bool:
        """A cheap look before the costly one: a Yes to a Part 9 question is in the case's facts or in a decision a person made. A case with none has no explanation to wait on."""
        for name in ("fact_graph.json", "decisions.json"):
            try:
                text = (self.dir / name).read_text(encoding="utf-8")
            except OSError:
                continue
            if "applicant.part9." in text and "Yes" in text:
                return True
        return False

    def staff_hand(self) -> tuple[str, str | None]:
        """The person who last worked the case who is not an attorney, and when: who the cards waiting for an attorney were prepared by."""
        mine = [d for d in self.decisions().values() if d.get("role") == "paralegal" and d.get("reviewer")]
        last = max(mine, key=lambda d: clock.key(d.get("at")), default=None)
        return (last["reviewer"], last.get("at")) if last else (PRODUCT, None)


def case_items(client_dir: Path, row: dict[str, Any], field_map: dict, template: str | Path, catalog) -> list[dict[str, Any]]:
    """Every open attorney item of one case, from every case kind: stored in the case's roster entry. A kind that cannot read the case is left out (stderr says
    which): one broken record must not hide the others."""
    ctx = CaseContext(client_dir, row, field_map, template, catalog)
    out = []
    for kind in sorted(KINDS.values(), key=lambda k: k["order"]):
        if kind["scope"] != "case":
            continue
        try:
            found = kind["gather"](ctx) or []
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"approvals: {kind['id']} not read for a case ({type(exc).__name__})\n")
            continue
        for item in found:
            out.append({"kind": kind["id"], "ref": item["ref"], "what": _short(item["what"]), "why": _short(_sentence(item.get("why"))), "detail": item.get("detail") or "",
                        "by": item.get("by") or PRODUCT, "by_email": item.get("by_email") or "", "at": item.get("at") or ctx.since,
                        "actions": item.get("actions") or [], "focus": item.get("focus") or {"tab": "attorney", "card": 0}})
    return out


# -- the firm's items ------------------------------------------------------------------------------------------------------------------------


class FirmContext:
    def __init__(self, data_root: Path, visible: Callable[[str], bool]):
        self.data_root, self.visible = Path(data_root), visible  # visible(case id): whether the reader may be told of the case (the stricter rule: a restricted case only to the attorneys named on it)


def firm_items(ctx: FirmContext) -> list[dict[str, Any]]:
    out = []
    for kind in sorted(KINDS.values(), key=lambda k: k["order"]):
        if kind["scope"] != "firm":
            continue
        try:
            found = kind["gather"](ctx) or []
        except Exception as exc:  # noqa: BLE001
            sys.stderr.write(f"approvals: {kind['id']} not read ({type(exc).__name__})\n")
            continue
        for item in found:
            out.append({"kind": kind["id"], "ref": item["ref"], "what": _short(item["what"]), "why": _short(_sentence(item.get("why"))), "detail": item.get("detail") or "",
                        "by": item.get("by") or PRODUCT, "by_email": item.get("by_email") or "", "at": item.get("at"),
                        "actions": item.get("actions") or [], "focus": item.get("focus"), "case": None, "name": None, "open_cases": item.get("open_cases") or []})
    return out


# -- the page --------------------------------------------------------------------------------------------------------------------------------


def _ask(case: str, item: dict[str, Any]) -> dict[str, Any]:
    """Ask the paralegal: a task on the case with the attorney's note (the case page's own route, src/case_notes.py), due in a day or two."""
    due = (clock.today() + timedelta(days=ASK_DAYS)).isoformat()
    title = _short("The attorney asks: " + item["what"], 120)
    return action("ask", "Ask the paralegal", "/api/case-notes", {"client": case, "action": "task", "title": title, "date": due, "who": item.get("by_email") or ""},
                  [{"name": "who", "label": "Who", "type": "staff", "required": False}, {"name": "note", "label": "What do you need?", "type": "text", "required": True}])


def _open(item: dict[str, Any]) -> dict[str, Any]:
    return {"id": "open", "label": "Open the case here", "route": None, "body": {}, "inputs": [], "open": item["focus"]}


def row_of(case: str | None, name: str | None, item: dict[str, Any], now) -> dict[str, Any]:
    at = clock.parse(item.get("at")) if item.get("at") else None
    acts = [dict(a) for a in item["actions"]]
    if case:
        acts += [_ask(case, item), _open(item | {"focus": item.get("focus") or {"tab": "attorney", "card": 0}})]
    elif item.get("open_cases"):
        acts += [{"id": "open", "label": "Open a case it was seen on", "route": None, "body": {}, "inputs": [], "open": {"client": item["open_cases"][0], "tab": "packet", "card": 0}}]
    out = {"id": f"{item['kind']}|{case or ''}|{item['ref']}", "kind": item["kind"], "case": case, "name": name, "what": item["what"], "why": item["why"], "detail": item.get("detail") or "",
           "by": item["by"], "at": item.get("at"), "date": clock.us_date(item["at"]) if item.get("at") else None,
           "waited_days": max(0, (now - at).days) if at else None, "actions": acts}
    for a in out["actions"]:  # the case an action is about travels in its own body: nothing the screen adds
        if a.get("open") is not None:
            a["open"] = {**a["open"], "client": a["open"].get("client") or case}
    return out


def queue(cases: list[tuple[str, str | None, list[dict[str, Any]]]], firm: list[dict[str, Any]], q: dict[str, Any] | None = None) -> dict[str, Any]:
    """The queue as one reader sees it. cases: (case id, the client's name, the case's stored items) for the cases this reader may be told of, and only those;
    firm: the firm's items. Counts are over everything the reader may see, before the filters; the rows are one page of what the filters keep: grouped by kind,
    the oldest first in each (an item with no date last)."""
    q = q or {}
    now = clock.now()
    rows = [row_of(case, name, item, now) for case, name, items in cases for item in items] + [row_of(None, None, item, now) for item in firm]
    order = {k["id"]: i for i, k in enumerate(kinds())}
    by_kind: dict[str, int] = {}
    by_person: dict[str, int] = {}
    for r in rows:
        by_kind[r["kind"]] = by_kind.get(r["kind"], 0) + 1
        by_person[r["by"]] = by_person.get(r["by"], 0) + 1
    dated = sorted((r["at"] for r in rows if r.get("at")), key=clock.key)
    kind, by = str(q.get("kind") or ""), str(q.get("by") or "")
    kept = [r for r in rows if (not kind or r["kind"] == kind) and (not by or r["by"] == by)]
    kept.sort(key=lambda r: (order.get(r["kind"], 999), r["at"] is None, clock.key(r["at"]) if r.get("at") else now, r["name"] or "", r["id"]))
    try:
        size = max(1, min(int(q.get("size") or PAGE), 200))
        page = max(1, int(q.get("page") or 1))
    except (TypeError, ValueError):
        raise ValueError("Page and size must be numbers.") from None
    pages = max(1, -(-len(kept) // size))
    page = min(page, pages)
    return {"total": len(rows), "kinds": [k | {"count": by_kind.get(k["id"], 0)} for k in kinds()],
            "people": [{"name": n, "count": c} for n, c in sorted(by_person.items(), key=lambda kv: (-kv[1], kv[0]))],
            "oldest": {"at": dated[0], "date": clock.us_date(dated[0])} if dated else None,
            "rows": kept[(page - 1) * size: page * size], "matching": len(kept), "page": page, "pages": pages, "size": size}


def summary(cases: list[tuple[str, str | None, list[dict[str, Any]]]], firm: list[dict[str, Any]]) -> dict[str, Any]:
    """One line's worth: how many, and the oldest date (My work, the morning report)."""
    dates = [i["at"] for _, _, items in cases for i in items if i.get("at")] + [i["at"] for i in firm if i.get("at")]
    n = sum(len(items) for _, _, items in cases) + len(firm)
    oldest = min(dates, key=clock.key) if dates else None
    return {"total": n, "oldest": oldest, "oldest_date": clock.us_date(oldest) if oldest else None}


def line(s: dict[str, Any]) -> str:
    """"3 approvals waiting, the oldest from 10/01/2026": the same words on My work and in the morning report. Empty when nothing waits."""
    n = s["total"]
    if not n:
        return ""
    return f"{n} approval{'s' if n != 1 else ''} waiting" + (f", the oldest from {s['oldest_date']}" if s.get("oldest_date") else "")


# -- the kinds the product has today -------------------------------------------------------------------------------------------------------


def _cards(ctx: CaseContext) -> list[dict[str, Any]]:
    """The review cards on the attorney's tab (src/review/state.py): sign-offs, rules' notes, the questions on Part 9 only an attorney settles."""
    if not ctx.row.get("attorney"):
        return []
    by, _ = ctx.staff_hand()
    cards = [c for c in ctx.items()["cards"] if c["tab"] == "attorney"]
    out = []
    for n, card in enumerate(cards):
        acts = []
        body = {"client": ctx.case, "item_ids": card["item_ids"]}
        if "confirm" in card["actions"]:
            acts.append(action("confirm", "Confirm", "/api/decide", body | {"action": "confirm"}))
        elif "acknowledge" in card["actions"]:
            blocking = card["level"] == "blocking"
            acts.append(action("acknowledge", "Acknowledge", "/api/decide", body | {"action": "acknowledge"},
                               [{"name": "note", "label": "Why it is all right to go on", "type": "reason", "required": True}] if blocking else []))
        out.append({"ref": card["id"], "what": card.get("headline") or card["title"], "why": " ".join(card["messages"][:2]) or "An attorney signs this off.",
                    "by": by, "at": None, "actions": acts, "focus": {"tab": "attorney", "card": n}})
    return out


def _part14(ctx: CaseContext) -> list[dict[str, Any]]:
    """An explanation for Part 14 written and waiting for the attorney's approval, or approved and then changed (src/part14_explain.py)."""
    import part14_explain as px

    if not (ctx.dir / "fact_graph.json").exists() or not ctx.may_answer_yes():
        return []
    out = []
    for e in px.entries(ctx.dir, ctx.graph()):
        if e["state"] not in ("draft", "changed"):
            continue
        who = (e["edit"] or {}).get("who") or PRODUCT
        at = (e["edit"] or {}).get("at")
        ready = bool(e["text"].strip()) and not e["waits"] and not e["blanks"]
        why = ((e["approval"] or {}).get("why_not") or "") if e["state"] == "changed" else "The explanation is written and waits for an attorney's approval before the packet can be ready."
        out.append({"ref": e["key"], "what": f"The Part 14 explanation for Part 9, item {e['item']}: {e['question']}", "why": why, "detail": e["text"], "by": who, "at": at,
                    "actions": [action("approve", "Approve", "/api/explain", {"client": ctx.case, "action": "approve", "key": e["key"]})] if ready else [],
                    "focus": {"tab": "explain", "card": 0}})
    return out


def _eoir26a(ctx: CaseContext) -> list[dict[str, Any]]:
    """The fee waiver request (EOIR-26A): item 4 waiting for the attorney's approval, and the attestation after the client signed (src/eoir26a.py)."""
    import eoir26a

    if not (ctx.dir / eoir26a.FILE).exists():
        return []
    request = eoir26a.read(ctx.dir).get("request")
    if not request:
        return []
    graph = eoir26a.case_graph(ctx.dir)
    out = []
    sentence = request.get("sentence")
    if not sentence or eoir26a.sentence_stale(request, graph):
        suggested = eoir26a.suggestion(graph)
        draft = request.get("sentence_draft") or {}
        text = draft.get("text") or (suggested["text"]["en"] if suggested else "")
        out.append({"ref": "item4", "what": "The fee waiver request (EOIR-26A): item 4, why the client cannot pay", "detail": text,
                    "by": draft.get("by") or request.get("made_by") or PRODUCT, "at": draft.get("at") or request.get("made_at"),
                    "why": eoir26a.STALE_ITEM4 if sentence else "The words for item 4 wait for an attorney's approval before the form is signed.",
                    "actions": [action("approve", "Approve", "/api/eoir26a", {"client": ctx.case, "action": "approve_sentence", "text": text})] if text else [],
                    "focus": {"tab": "feewaiver", "card": 0}})
    signature, attestation = request.get("signature"), request.get("attestation")
    if signature and eoir26a.covers_now(signature, graph) and not (attestation and eoir26a.covers_now(attestation, graph)):
        out.append({"ref": "attest", "what": "The fee waiver request (EOIR-26A): the attorney's attestation", "by": request.get("made_by") or PRODUCT, "at": signature.get("at") or signature.get("on"),
                    "why": "The client has signed. The form is not ready until the attorney attests, by typing their own name.",
                    "actions": [action("attest", "Attest", "/api/eoir26a", {"client": ctx.case, "action": "attest"},
                                       [{"name": "typed_name", "label": "Your full name", "type": "text", "required": True}])],
                    "focus": {"tab": "feewaiver", "card": 0}})
    return out


def _restricted(ctx: CaseContext) -> list[dict[str, Any]]:
    """A case the law does not close but the attorney may: a minor's SIJ case, until the attorney chooses (src/restricted.py)."""
    import restricted

    if not restricted.state(ctx.dir)["suggest"]:
        return []
    return [{"ref": "sij", "what": "Should this minor's SIJ case be restricted?", "by": PRODUCT, "at": ctx.since,
             "why": "A case for a minor asking for Special Immigrant Juvenile status is the attorney's choice: restricted, it is open only to the attorneys and the staff named on it.",
             "actions": [action("mark", "Restrict this case", "/api/access", {"client": ctx.case, "action": "mark"},
                                [{"name": "reason", "label": "Why it is restricted", "type": "reason", "required": True}])],
             "focus": {"tab": "done", "card": 0}}]


def _practices(want: Callable[[str], bool]) -> Callable:
    def gather(ctx: FirmContext) -> list[dict[str, Any]]:
        import settings
        from review.state import rule_info
        from rules import approval

        states = approval.statuses()
        since = settings.mtime()
        out = []
        for entry in approval.catalog():
            if entry["kind"] != "practice" or not want(entry["id"]) or states.get(entry["id"], {}).get("state") == "approved":
                continue
            info = rule_info(entry["id"])
            out.append({"ref": entry["id"], "what": f"The firm's practice: {entry.get('name') or entry['code']}", "why": info["approval_text"] + ".",
                        "detail": entry["plain_text"], "by": PRODUCT, "at": datetime.fromtimestamp(since, clock.zone()).isoformat() if since else None,
                        "actions": [action("approve", "Approve", "/api/rules/approve", {"rule": entry["id"]})]})
        return out
    return gather


def _wordings(ctx: FirmContext) -> list[dict[str, Any]]:
    """A wording imported from a past filing, a candidate until an attorney approves it (src/wordings.py): offered on cases only afterwards."""
    import wordings

    library = wordings.library(wordings.root(ctx.data_root), ctx.visible)
    out = []
    for r in library["candidates"] + library["to_place"]:
        if r["status"] != "candidate":
            continue
        ready = bool(r["key"]) and r["edition"] == library["edition"]
        acts = []
        if ready:
            acts = [action("approve", "Approve", "/api/wordings", {"action": "approve", "ids": [r["id"]]},
                           [{"name": "voice", "label": "Voice", "type": "voice", "required": True}] if r["needs_voice"] else []),
                    action("discard", "Set aside", "/api/wordings", {"action": "discard", "ids": [r["id"]]})]
        out.append({"ref": r["id"], "what": f"A firm wording from a past filing: {r['item']}", "detail": r["spoken"], "by": PRODUCT, "at": (r.get("history") or [{}])[0].get("at"),
                    "why": r["status_words"] + (". It is offered on cases only once an attorney approves it." if ready else ". Place it on its item first: Settings, Firm wordings."),
                    "actions": acts})
    return out


def _audit(ctx: FirmContext) -> list[dict[str, Any]]:
    """A box the office changes the same way on several cases: a rule may be missing (src/audit_fill.py). Nothing here can be approved: the attorney reads it."""
    import audit_fill

    rows = [r for r in (audit_fill.read(ctx.data_root) or {}).get("rows") or [] if ctx.visible(r["case"])]
    out = []
    for g in audit_fill.groups(rows):
        if len(g["cases"]) < audit_fill.THRESHOLD:
            continue
        out.append({"ref": g["id"], "what": g["line"], "by": PRODUCT, "at": None, "open_cases": g["cases"],
                    "why": "The same box was changed the same way on several cases, which usually means a rule is missing. Reports, Boxes the office changes, lists the cases."})
    return out


def _path(ctx: CaseContext) -> list[dict[str, Any]]:
    """A change to the case's path, proposed by a paralegal and waiting for an attorney (src/path.py)."""
    import path

    if not (ctx.dir / path.FILE).exists():
        return []
    p = path.read(ctx.dir)["proposed"]
    if not p:
        return []
    return [{"ref": "path", "what": f"A change to the case's path: {p['change']}", "detail": f"Why: {p['reason']}", "by": p.get("by") or PRODUCT, "by_email": p.get("by_email"),
             "at": p.get("at"), "why": "A changed path is a decision the attorney approves: until then the case keeps the path as approved, and nothing is derived from the change.",
             "actions": [action("approve", "Approve the path", "/api/path", {"client": ctx.case, "action": "approve"}),
                         action("refuse", "Refuse", "/api/path", {"client": ctx.case, "action": "refuse"},
                                [{"name": "reason", "label": "Why it is refused", "type": "reason", "required": True}])],
             "focus": {"tab": "path", "card": 0}}]


register("card", "Review card", "Review cards that need an attorney", "case", _cards, 10)
register("part14", "Part 14 explanation", "Part 14 explanations", "case", _part14, 20)
register("practice", "Firm practice", "Practices waiting for approval", "firm", _practices(lambda i: not i.startswith("PRACTICE:G28")), 30)
register("g28", "G-28 office switches", "G-28 office switches", "firm", _practices(lambda i: i.startswith("PRACTICE:G28")), 40)
register("eoir26a", "Fee waiver (EOIR-26A)", "Fee waiver requests (EOIR-26A)", "case", _eoir26a, 50)
register("restricted", "Restricted-case question", "Restricted-case questions", "case", _restricted, 60)
register("wording", "Firm wording", "Wordings from past filings", "firm", _wordings, 70)
register("audit", "Box the office changes", "Boxes the office changes", "firm", _audit, 80)
register("path", "Path change", "Changes to a case's path", "case", _path, 55)
