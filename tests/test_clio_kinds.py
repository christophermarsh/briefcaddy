"""The kinds the Clio sync sends since brief J3: the deadlines a person sets, the case's tasks and the case's end state (src/connectors/clio.py).

Against test_clio.py's simulated Clio, with the calls Clio's API reference documents for tasks added (POST and PATCH /tasks.json, users with their e-mail).
The cases are real folders read by the real journey (src/journey.py), so a deadline a person sets goes through src/deadlines_set.py as it does in the app;
a task is the same row marked "task" (src/case_notes.py), and a case's end is the file src/engagement.py writes. Everyone is made up."""

import json
from pathlib import Path

import httpx
import pytest

import clock
import deadlines_set
import documents
from connectors import clio
from test_clio import ENV, SETUP, FakeClio, Quiet, _pdf  # noqa: F401 -- the simulated Clio and its made-up firm

ANA = "ana_clara_exemplo_souza-cl101"
PEOPLE = [{"email": "paulo@firm.example", "name": "Paulo Paralegal", "role": "paralegal"},
          {"email": "gone@firm.example", "name": "Gina Gone", "role": "paralegal"}]


class FakeTasks(FakeClio):
    """test_clio's Clio with Clio's users (each with an e-mail) and tasks."""

    def __init__(self):
        super().__init__()
        self.tasks: dict[str, dict] = {}
        self.users = [{"id": 31, "name": "Andrea Exemplo", "email": "Andrea@Firm.Example", "enabled": True, "subscription_type": "Attorney"},
                      {"id": 32, "name": "Paulo from Clio", "email": "paulo@firm.example", "enabled": True, "subscription_type": "Staff"},
                      {"id": 33, "name": "Gina from Clio", "email": "gone@firm.example", "enabled": False, "subscription_type": "Staff"}]
        self.refuse_tasks = False

    def api(self, method, path, params, request):
        body = json.loads(request.content) if request.content else {}
        if (method, path) == ("GET", "/users.json") and "email" in params.get("fields", ""):
            return self._json(200, {"data": self.users})
        if (method, path) == ("POST", "/tasks.json"):
            if self.refuse_tasks:
                return self._json(403, {"error": {"type": "ForbiddenError", "message": "no"}})
            data = body["data"]
            assert data["name"] and data["description"] and ("assignee" in data) != ("assignees" in data)  # Clio: name, description, and exactly one assignee
            assert data["assignee"]["type"] == "User" and data["matter"]["id"] and data["due_at"]
            tid = str(self._id())
            self.tasks[tid] = dict(data) | {"status": data.get("status", "pending")}
            return self._json(201, {"data": {"id": tid}})
        if method == "PATCH" and path.startswith("/tasks/"):
            self.tasks[path.split("/")[2].split(".")[0]] |= body["data"]
            return self._json(200, {"data": {"id": 1}})
        return super().api(method, path, params, request)


@pytest.fixture
def fake():
    return FakeTasks()


@pytest.fixture
def firm(tmp_path, fake):
    data = tmp_path / "data"
    (data / "clients").mkdir(parents=True)
    clio.save_settings(data, SETUP, "Andrea Attorney", env=ENV)
    clio.connect(data, "good-code", "Andrea Attorney", env=ENV, transport=httpx.MockTransport(fake.handle))
    quiet = Quiet()
    return {"data": data, "clients": tmp_path / "clients", "out": data / "clients", "portal": data / "portal", "fake": fake,
            "run": lambda direction="both": clio.run(data, tmp_path / "clients", data / "clients", data / "portal", direction,
                                                     transport=httpx.MockTransport(fake.handle), env=ENV,
                                                     pace=clio.Pace(monotonic=quiet.mono, sleep=quiet.sleep, wall=quiet.wall))}


def processed(firm, case: str = ANA, filings=None) -> Path:
    """A case the pipeline processed (a real, empty fact graph: the real journey reads it)."""
    out = firm["out"] / case
    out.mkdir(parents=True, exist_ok=True)
    (out / "fact_graph.json").write_text(json.dumps({"client_id": case, "facts": {}}), encoding="utf-8")
    (out / "status.json").write_text(json.dumps({"filings": filings or []}), encoding="utf-8")
    return out


def add_deadline(case: Path, title: str, when: str, email=None, note=""):
    return deadlines_set.add(case, title, when, email, note, "Paulo Paralegal", PEOPLE)


def add_task(case: Path, title: str, when: str, email=None, note="", typed=None):
    """A task is one row of the deadlines file, marked "task" (src/case_notes.py add_task)."""
    row = deadlines_set.add(case, title, when, email, note, "Paulo Paralegal", [] if typed else PEOPLE, typed)  # a typed name: an office with no staff accounts
    data = json.loads((case / deadlines_set.FILE).read_text(encoding="utf-8"))
    next(d for d in data["deadlines"] if d["id"] == row["id"])["task"] = True
    (case / deadlines_set.FILE).write_text(json.dumps(data), encoding="utf-8")
    return row


@pytest.fixture(autouse=True)
def tasks_are_marked(monkeypatch):
    """journey.journey() marks a task's row "task" (src/case_notes.py, brief I4). Where this tree does not have that yet, the same marking is put on the
    two readers here, so these tests describe the rows the sync receives either way."""
    import inspect

    if "task" in inspect.getsource(deadlines_set.open_deadlines):
        return
    open_, finished = deadlines_set.open_deadlines, deadlines_set.finished

    def tasks(case):
        return {d["id"] for d in json.loads((Path(case) / deadlines_set.FILE).read_text(encoding="utf-8")).get("deadlines", []) if d.get("task")}
    monkeypatch.setattr(deadlines_set, "open_deadlines", lambda case, today, cfg: [r | ({"task": True} if r["id"] in tasks(case) else {}) for r in open_(case, today, cfg)])
    monkeypatch.setattr(deadlines_set, "finished", lambda case: [r | ({"task": True} if r["id"] in tasks(case) else {}) for r in finished(case)])


def entries(fake):
    return {e["external_properties"][0]["value"].split(":", 1)[1]: (eid, e) for eid, e in fake.entries.items()}


# --- the deadlines a person sets -------------------------------------------------------------------------------------------


def test_a_deadline_a_person_set_goes_to_the_clio_calendar_the_way_the_products_own_do_and_is_marked_done_there(firm, fake):
    firm["run"]("in")
    case = processed(firm)
    add_deadline(case, "Send the client the interview checklist", "2026-12-01", "paulo@firm.example", "bring the passport")
    add_deadline(case, "Call the court", "2026-12-05")
    line = firm["run"]("out")
    assert "deadlines 2 added, 0 moved, 0 closed" in line, line
    by = entries(fake)
    eid, sent = by["set.1"]
    assert sent["summary"] == "Send the client the interview checklist" and sent["all_day"] is True and sent["matter"] == {"id": 101}  # all-day, on the matter
    assert sent["start_at"].startswith("2026-12-01T00:00:00-05:00") and sent["end_at"].startswith("2026-12-01T23:59:00-05:00") and sent["calendar_owner"] == {"id": 900}
    assert "Who: Paulo Paralegal" in sent["description"] and "Note: bring the passport" in sent["description"] and sent["description"].endswith("From the firm's review app.")
    assert sent["external_properties"] == [{"name": clio.EXTERNAL, "value": f"{ANA}:set.1"}]
    assert "deadlines 0 added, 0 moved, 0 closed" in firm["run"]("out")  # nothing changed: nothing sent
    # marked done in the app: the entry is renamed "Done: ...", with who and when; it is not deleted, and it is not a "no longer due"
    deadlines_set.done(case, "set.1", "Paulo Paralegal")
    assert "0 moved, 1 closed" in firm["run"]("out")
    assert fake.entries[eid]["summary"] == "Done: Send the client the interview checklist" and "Marked done by Paulo Paralegal on" in fake.entries[eid]["description"]
    assert "No longer due" not in fake.entries[eid]["summary"] and len(fake.entries) == 2
    # a deadline marked done before it was ever sent is never sent
    deadlines_set.done(case, "set.2", "Paulo Paralegal")
    firm["run"]("out")
    assert len(fake.entries) == 2 and not any(m == "DELETE" for m, _ in fake.requests)
    assert "tasks" not in firm["run"]("out")  # no task, no mention


# --- tasks -----------------------------------------------------------------------------------------------------------------


def test_a_task_goes_to_the_clio_user_with_the_same_email_else_to_the_connected_user_with_the_name_in_the_description(firm, fake):
    firm["run"]("in")
    case = processed(firm)
    add_task(case, "Gather the client's tax returns", "2026-12-01", "paulo@firm.example", "last three years")
    add_task(case, "Ask for the school records", "2026-12-02", "gone@firm.example")  # this Clio user is turned off: no match
    add_task(case, "Check the address", "2026-12-03", None, typed="Nobody Yet")
    add_task(case, "File the change of address", "2026-12-04")
    add_deadline(case, "The real deadline", "2026-12-10")
    line = firm["run"]("out")
    assert "tasks 4 added, 0 completed" in line and "deadlines 1 added" in line, line
    assert len(fake.tasks) == 4 and set(entries(fake)) == {"set.5"}  # the tasks are tasks, never calendar entries; the deadline is the calendar's
    by_name = {t["name"]: t for t in fake.tasks.values()}
    mine = by_name["Gather the client's tax returns"]
    assert mine["assignee"] == {"id": 32, "type": "User"} and mine["matter"] == {"id": 101} and mine["due_at"] == "2026-12-01" and mine["notify_assignee"] is False
    assert "last three years" in mine["description"] and "Responsible: Paulo Paralegal" in mine["description"] and "Given to" not in mine["description"]
    off = by_name["Ask for the school records"]
    assert off["assignee"] == {"id": 31, "type": "User"}  # the connected user: Clio makes no task without an assignee
    assert "Responsible: Gina Gone" in off["description"] and "Given to Andrea Exemplo here because no Clio user has Gina Gone's e-mail address." in off["description"]
    assert by_name["File the change of address"]["assignee"] == {"id": 31, "type": "User"} and "Responsible" not in by_name["File the change of address"]["description"]
    assert "Responsible: Nobody Yet" in by_name["Check the address"]["description"]
    assert "—" not in json.dumps(fake.tasks) and " -- " not in json.dumps(fake.tasks)
    # asked for the users once for the whole run, not once per task
    assert len([r for r in fake.requests if r[1].startswith("https://app.clio.com/api/v4/users.json") and "email" in r[1]]) == 1
    # nothing changed: nothing sent again
    assert "tasks" not in firm["run"]("out") and len(fake.tasks) == 4
    # done in the app: complete in Clio (never deleted); done before it was sent: never sent
    first = next(t for t in json.loads((case / deadlines_set.FILE).read_text(encoding="utf-8"))["deadlines"] if t["title"] == "Gather the client's tax returns")
    deadlines_set.done(case, first["id"], "Paulo Paralegal")
    add_task(case, "A task done at once", "2026-12-06")
    deadlines_set.done(case, "set.6", "Paulo Paralegal")
    line = firm["run"]("out")
    assert "tasks 0 added, 1 completed" in line
    assert mine["status"] == "complete" and len(fake.tasks) == 4 and sum(1 for t in fake.tasks.values() if t["status"] == "complete") == 1
    assert not any(m == "DELETE" for m, _ in fake.requests)


def test_a_task_made_in_the_case_notes_module_goes_to_clio_and_is_completed_there(firm, fake):
    """The real thing (src/case_notes.py, brief I4): the same row the tests above build by hand."""
    import case_notes

    firm["run"]("in")
    case = processed(firm)
    row = case_notes.add_task(case, "Collect the client's school records", "2026-12-01", "paulo@firm.example", "from the registrar", "Paulo Paralegal", PEOPLE, role="paralegal")
    assert "tasks 1 added" in firm["run"]("out")
    [task] = fake.tasks.values()
    assert task["name"] == "Collect the client's school records" and task["assignee"] == {"id": 32, "type": "User"} and not fake.entries
    case_notes.finish_task(case, row["id"], "Paulo Paralegal", "paralegal")
    assert "tasks 0 added, 1 completed" in firm["run"]("out") and task["status"] == "complete" and len(fake.tasks) == 1


# --- the case's end state --------------------------------------------------------------------------------------------------


def end(case: Path, state: str, on: str):
    rec = {"version": 1, "letters": [], "end": {"state": state, "on": on, "reason": "The matter is concluded.", "by": "Andrea Attorney", "role": "attorney",
                                                "at": f"{on}T10:00:00-04:00", "letter": None}, "history": []}
    (case / "engagement.json").write_text(json.dumps(rec), encoding="utf-8")


def test_a_cases_end_is_a_matter_note_and_the_matters_status_in_clio_is_never_touched(firm, fake):
    firm["run"]("in")
    case = processed(firm)
    firm["run"]("out")
    stage_notes = len(fake.notes)
    end(case, "closed", "2026-10-01")
    line = firm["run"]("out")
    assert "1 note(s) sent" in line, line
    note = fake.notes[stage_notes]
    assert note["matter"] == {"id": 101} and note["subject"] == "Closed on 10/01/2026"
    assert note["detail"].startswith("Closed on 10/01/2026 in the firm's review app.") and "status in Clio is not changed" in note["detail"]
    # no request changed the matter itself: the firm closes its own matters in Clio
    assert not [r for r in fake.requests if r[0] in ("PATCH", "PUT", "POST", "DELETE") and "/matters" in r[1]]
    assert "0 note(s) sent" in firm["run"]("out") and len(fake.notes) == stage_notes + 1  # once
    # reopened: one more note that says so; ended again: one more
    (case / "engagement.json").unlink()
    firm["run"]("out")
    assert fake.notes[-1]["subject"].startswith("Reopened on ") and len(fake.notes) == stage_notes + 2
    firm["run"]("out")
    assert len(fake.notes) == stage_notes + 2
    end(case, "withdrawn", "2026-10-02")
    firm["run"]("out")
    assert fake.notes[-1]["subject"] == "Withdrawn on 10/02/2026" and len(fake.notes) == stage_notes + 3
    assert not any(m == "DELETE" for m, _ in fake.requests)


# --- the rule for a protected case holds for every new kind -----------------------------------------------------------------


def test_nothing_of_a_protected_case_goes_to_clio_for_any_new_kind_until_send_to_clio(firm, fake):
    firm["run"]("in")
    mailed = [{"filing": "vawa", "title": "VAWA self-petition", "mailed_on": "2026-09-30", "carrier": "USPS", "by": "Paulo Paralegal", "at": "2026-09-30T12:00:00-04:00"}]
    case = processed(firm, filings=mailed)
    assert documents.case_confidentiality(case) == "1367"
    add_deadline(case, "A set deadline", "2026-12-01")
    add_task(case, "A task", "2026-12-02", "paulo@firm.example")
    end(case, "closed", "2026-10-01")
    line = firm["run"]("out")
    assert "1 protected case(s) held back" in line
    assert not fake.entries and not fake.tasks and not fake.notes and not fake.created  # not one of the kinds went
    assert not (case / "clio_sent.json").exists()
    clio.allow(firm["data"], ANA, "Andrea Attorney")
    line = firm["run"]("out")
    assert "held back" not in line and fake.entries and fake.tasks and any(n["subject"] == "Closed on 10/01/2026" for n in fake.notes)
    clio.allow(firm["data"], ANA, "Andrea Attorney", allowed=False)  # taken back: new things wait again
    add_task(case, "Another task", "2026-12-03")
    n = len(fake.tasks)
    firm["run"]("out")
    assert len(fake.tasks) == n
    assert not any(m == "DELETE" for m, _ in fake.requests)


def test_a_restricted_case_waits_the_same_way(firm, fake):
    import restricted

    firm["run"]("in")
    case = processed(firm)
    add_task(case, "A task", "2026-12-02")
    restricted.mark(case, True, "The client asked that only the attorney see this case.", "Andrea Attorney", "attorney")
    assert "1 protected case(s) held back" in firm["run"]("out") and not fake.tasks
    clio.allow(firm["data"], ANA, "Andrea Attorney")
    firm["run"]("out")
    assert len(fake.tasks) == 1


def test_the_case_view_splits_tasks_from_deadlines_and_brings_back_what_was_marked_done(tmp_path):
    case = tmp_path / "data" / "clients" / ANA
    case.mkdir(parents=True)
    (case / "fact_graph.json").write_text(json.dumps({"client_id": ANA, "facts": {}}), encoding="utf-8")
    add_deadline(case, "A deadline", "2026-12-01")
    add_deadline(case, "A finished deadline", "2026-12-02")
    add_task(case, "A task", "2026-12-03", "paulo@firm.example")
    add_task(case, "A finished task", "2026-12-04")
    deadlines_set.done(case, "set.2", "Paulo Paralegal")
    deadlines_set.done(case, "set.4", "Paulo Paralegal")
    view = clio.case_view(case)
    assert {d["id"] for d in view["deadlines"]} == {"set.1", "set.2"} and view["done"].keys() == {"set.2"} and view["done"]["set.2"]["by"] == "Paulo Paralegal"
    assert {t["id"]: bool(t["done"]) for t in view["tasks"]} == {"set.3": False, "set.4": True} and view["end"] is None
    assert next(t for t in view["tasks"] if t["id"] == "set.3")["who"] == "paulo@firm.example"
    assert clock.today()  # the view is read in the firm's day


def test_a_task_whose_date_or_person_changed_is_changed_in_clio_like_a_moved_deadline(firm, fake):
    firm["run"]("in")
    case = processed(firm)
    row = add_task(case, "Gather the tax returns", "2026-12-01", "paulo@firm.example", "last three years")
    firm["run"]("out")
    [(tid, task)] = fake.tasks.items()
    assert task["assignee"] == {"id": 32, "type": "User"} and task["due_at"] == "2026-12-01"
    # re-dated (the file's row, as an office edit would leave it)
    data = json.loads((case / deadlines_set.FILE).read_text(encoding="utf-8"))
    next(d for d in data["deadlines"] if d["id"] == row["id"])["date"] = "2026-12-15"
    (case / deadlines_set.FILE).write_text(json.dumps(data), encoding="utf-8")
    line = firm["run"]("out")
    assert "1 changed" in line and task["due_at"] == "2026-12-15" and task["assignee"] == {"id": 32, "type": "User"} and len(fake.tasks) == 1
    assert "tasks" not in firm["run"]("out")  # nothing changed: nothing sent
    # reassigned to a person with no Clio user: the connected user, with the name in the description
    deadlines_set.assign(case, row["id"], "gone@firm.example", "Paulo Paralegal", PEOPLE)
    assert "1 changed" in firm["run"]("out")
    assert task["assignee"] == {"id": 31, "type": "User"} and "Responsible: Gina Gone" in task["description"] and "Given to Andrea Exemplo" in task["description"]
    # a completed task is not changed again
    deadlines_set.done(case, row["id"], "Paulo Paralegal")
    firm["run"]("out")
    assert task["status"] == "complete" and len(fake.tasks) == 1 and not any(m == "DELETE" for m, _ in fake.requests)
