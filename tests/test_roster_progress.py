"""Builder coverage for coherent list progress and unchanged visibility rules."""
from types import SimpleNamespace

import pytest
import approvals
import day_plan
from holders import ATTORNEY, CLIENT, OFFICE
from review.roster import Roster
from review.server import ReviewApp


ACTOR = {"email": "jane@firm.example", "role": "paralegal", "active": True}


def entry(case, *, closed=False, named=(), unwritten=False):
    return {"row": {"id": case, "stage": "invited", "summary": {"name": case}, "journey": {"deadlines": []}},
            "has_case": False, "held": False, "closed": closed, "named": list(named), "unwritten": unwritten,
            "office": None, "ts": None, "filed": {}, "built": {},
            "approvals": [{"kind": "fictional", "ref": "one", "what": "Fictional review", "why": "Needs review", "by": "Named reviewer", "actions": []}],
            "day": {"ready": True, "counts": {ATTORNEY: 0, CLIENT: 0, OFFICE: 0}, "steps": 0, "filing": "i485", "first": [], "client": []}}


def roster(tmp_path, monkeypatch):
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    value = Roster(tmp_path / "data" / "clients", {}, "", None)
    monkeypatch.setattr(value, "sync", lambda: None)
    value.reading = {"total": 1, "done": 0}
    return value


class FinishOnRead(dict):
    def __init__(self, values, finish):
        super().__init__(values)
        self.finish = finish

    def __getitem__(self, key):
        if key == "row":
            self.finish()
        return super().__getitem__(key)

    def get(self, key, default=None):
        if key in {"day", "approvals"}:
            self.finish()
        return super().get(key, default)


@pytest.mark.parametrize("method", ["rows", "approvals", "day_plans"])
def test_progress_and_visibility_belong_to_the_entry_snapshot(tmp_path, monkeypatch, method):
    value = roster(tmp_path, monkeypatch)
    def finish():
        value.ready = True
        value.reading["done"] = 1
    value.entries = {
        "public": FinishOnRead(entry("public"), finish),
        "named": entry("named", closed=True, named=[ACTOR["email"]]),
        "hidden": entry("hidden", closed=True),
        "unwritten": entry("unwritten", unwritten=True),
    }
    rows, pending = getattr(value, method)(ACTOR, with_progress=True)
    ids = {r["id"] if method == "rows" else r[0] for r in rows}
    assert ids == {"public", "named"}
    assert pending == 1 and value.still_reading() == 0
    later, pending = getattr(value, method)(ACTOR, with_progress=True)
    assert pending == 0 and later == rows
    assert isinstance(getattr(value, method)(ACTOR), list)


@pytest.mark.parametrize("method", ["rows", "approvals", "day_plans"])
def test_initial_zero_of_zero_keeps_the_existing_in_progress_signal(tmp_path, monkeypatch, method):
    value = roster(tmp_path, monkeypatch)
    value.reading = {"total": 0, "done": 0}
    value.first_walk = object()
    rows, pending = getattr(value, method)(ACTOR, with_progress=True)
    assert rows == [] and pending == 1


@pytest.mark.parametrize("page", ["today", "approvals"])
def test_response_adapters_do_not_pair_empty_lists_with_later_completion(tmp_path, monkeypatch, page):
    value = roster(tmp_path, monkeypatch)
    application = ReviewApp.__new__(ReviewApp)
    application.data_root = value.data_root
    application.roster = value
    application.accounts = SimpleNamespace()
    monkeypatch.setattr(application, "_people_public", lambda: [])
    actor = ACTOR | {"role": "attorney" if page == "approvals" else "paralegal"}
    def finish():
        value.entries = {"public": entry("public"), "hidden": entry("hidden", closed=True)}
        value.reading["done"] = 1
        value.ready = True
    if page == "today":
        original = day_plan.listing
        def build(*args, **kwargs):
            finish()
            return original(*args, **kwargs)
        monkeypatch.setattr(day_plan, "listing", build)
        response = application.today({}, actor)
        later = application.today({}, actor)
    else:
        def firm_items(_context):
            finish()
            return []
        monkeypatch.setattr(approvals, "firm_items", firm_items)
        response = application.approvals_queue({}, actor)
        later = application.approvals_queue({}, actor)
    assert response["rows"] == [] and response["still_reading"] == 1
    assert later["still_reading"] == 0 and len(later["rows"]) == 1
    assert later["rows"][0]["case"] == "public"
