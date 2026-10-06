"""Current protected family pairs over fictional loopback HTTP and real folders."""
# ruff: noqa: F811 -- canonical imported pytest fixtures are injected by name
from file_policy_fixture import own_case_identity, disposition
import json
import shutil
import time

import pytest

import journey
import restricted
from test_prospects import firm, server, ok, call  # noqa: F401 -- fixtures


def mark(srv, who="sam", *, source="case-ana", target="case-bia", action="link", relationship="Spouse"):
    value = {"client": target} | ({"relationship": relationship} if action == "link" else {})
    return call(srv, who, "/api/journey", {"client": source, "action": action, "value": value, "role": "attorney", "reviewer": "Spoofed staff"})


def links(srv, who="sam", source="case-ana"):
    return ok(srv, who, "/api/journey?client=" + source)["linked"]


def pending(server, firm, monkeypatch):
    save = journey._family_save
    with monkeypatch.context() as fault:
        def crash(folder, status):
            save(folder, status)
            if folder.name == "case-bia" and status.get("journey", {}).get("family_pending"):
                raise OSError("Fictional interruption after reciprocal target write")
        fault.setattr(journey, "_family_save", crash)
        assert mark(server)[0] == 500
    view = ok(server, "sam", "/api/family-link-recovery?client=case-ana")
    assert view["state"] == "pending" and view["can_recover"]
    return view


def test_hidden_and_missing_targets_deny_before_status_or_labels(server, firm, monkeypatch):
    source = firm["clients"] / "case-ana"
    before = (source / "status.json").read_bytes() if (source / "status.json").exists() else None
    assert mark(server, "jane", target="case-rosa") == mark(server, "jane", target="unknown-fictional")
    assert mark(server, "jane", target="case-rosa")[0] == 404
    assert ((source / "status.json").read_bytes() if (source / "status.json").exists() else None) == before
    assert mark(server, "sam", target="case-rosa")[0] == 200
    read = journey._status
    seen = []
    def watch(folder):
        seen.append(folder.name)
        return read(folder)
    monkeypatch.setattr(journey, "_status", watch)
    assert links(server, "jane") == [] and "case-rosa" not in seen
    assert mark(server, "jane", target="case-rosa", action="unlink")[0] == 404
    assert len(links(server)) == 1


def test_path_symlink_explicit_relationship_and_body_are_bounded(server, firm, tmp_path):
    outside = firm["data"] / "outside-family-target"
    outside.mkdir(); (outside / "meta.json").write_text("{}")
    for target in ("../outside-family-target", "/tmp", "case-ana", "", "x" * 201):
        assert mark(server, target=target)[0] in (400, 404)
    assert not (outside / "status.json").exists()
    (firm["clients"] / "family-symlink").symlink_to(outside, target_is_directory=True)
    assert mark(server, target="family-symlink")[0] == 404
    for value in ({"client": "case-bia"}, {"client": "case-bia", "relationship": ""}, {"client": "case-bia", "relationship": []},
                  {"client": "case-bia", "relationship": "Spouse", "grant_access": True}, [], {"client": []}):
        assert call(server, "sam", "/api/journey", {"client": "case-ana", "action": "link", "value": value})[0] == 400
    assert links(server) == []
    with pytest.raises(LookupError):
        journey.mark(firm["clients"] / "case-ana", "link", "Unscoped actor", value={"client": "case-bia", "relationship": "Spouse"})


def test_pair_instance_audit_reciprocal_and_no_permission_transfer(server, firm):
    before = (firm["clients"] / "case-rosa" / "access.json").read_bytes() if (firm["clients"] / "case-rosa" / "access.json").exists() else None
    assert mark(server, target="case-rosa", relationship="Child")[0] == 200
    row = links(server)[0]
    assert row["client"] == "case-rosa" and row["relationship"] == "Child"
    assert links(server, source="case-rosa")[0]["relationship"] == "Parent"
    status = journey._status(firm["clients"] / "case-ana")["journey"]
    plan = status["family_operations"][-1]
    assert plan["by"] == "Sam Attorney" and plan["audit"]["source"] and plan["audit"]["target"]
    assert plan["source_instance"] != plan["target_instance"] and status["linked"][0]["instance"] == plan["target_instance"]
    assert call(server, "jane", "/api/journey?client=case-rosa")[0] == 404
    assert ((firm["clients"] / "case-rosa" / "access.json").read_bytes() if (firm["clients"] / "case-rosa" / "access.json").exists() else None) == before
    assert not (firm["portal"] / "clients" / "case-ana" / "communication_consent.json").exists()
    assert not (firm["portal"] / "outbox.jsonl").exists()
    assert mark(server, target="case-rosa", action="unlink")[0] == 200
    assert links(server) == [] and links(server, source="case-rosa") == []


def test_interrupted_pair_hidden_and_exact_recovery_rechecks_both_acl(server, firm, monkeypatch):
    view = pending(server, firm, monkeypatch)
    assert links(server) == [] and links(server, source="case-bia") == []
    restricted.mark(firm["clients"] / "case-bia", True, "Fictional restricted recovery", "Sam Attorney", "attorney")
    before = [(firm["clients"] / cid / "status.json").read_bytes() for cid in ("case-ana", "case-bia")]
    assert call(server, "jane", "/api/family-link-recovery", {"client": "case-ana", "action": "recover", "operation": view["operation"], "role": "attorney"})[0] == 404
    assert before == [(firm["clients"] / cid / "status.json").read_bytes() for cid in ("case-ana", "case-bia")]
    assert call(server, "sam", "/api/family-link-recovery", {"client": "case-ana", "action": "recover", "operation": "f" * 32})[0] == 400
    result = ok(server, "sam", "/api/family-link-recovery", {"client": "case-ana", "action": "recover", "operation": view["operation"]})
    assert result["recovered"] and result["state"] == "none" and result["access_changed"] is False
    assert len(links(server)) == 1 and len(links(server, source="case-bia")) == 1


def test_actual_purge_and_same_id_recreation_never_inherits_pair(server, firm, monkeypatch):
    import clock
    import engagement
    import purge
    from datetime import datetime, timedelta
    assert mark(server)[0] == 200
    old = journey._status(firm["clients"] / "case-bia")["journey"]["family_instance"]
    # The actual authorized purge helper removes the old service status.
    target = firm["clients"] / "case-bia"
    from rules import approval
    approval.approve(engagement.PRACTICE_ID, "Sam Attorney", "attorney")  # explicit fictional current wording gate
    engagement.end(target, "closed", "Sam Attorney", "attorney", reason="Fictional family purge", portal_root=firm["portal"])
    purge.record_contact(target, "phone", "10/05/2026", "Fictional contact attempt", "Sam Attorney", "attorney")
    purge.review_originals(target, [], True, "Sam Attorney", "attorney")
    own_case_identity(target)
    monkeypatch.setattr(clock, "_now_override", datetime(2032, 10, 6, 10, 30))
    disposition(target, who="Sam Attorney", portal_root=firm["portal"],
                completed_on="2026-10-05", age_status="adult", keep_until="2032-10-05")
    requested = purge.ask(target, "Fictional early purge", "Sam Attorney", "attorney", attorneys=1)
    monkeypatch.setattr(clock, "_now_override", datetime.fromisoformat(requested["purge_on"]) + timedelta(hours=12))
    purge.run(firm["clients"], "case-bia", firm["portal"])
    from review.server import COOKIE
    server["sam"] = f"{COOKIE}={server['accounts'].session_for('sam@firm.example', how='test')[0]}"
    target = firm["clients"] / "case-bia"
    target.mkdir(exist_ok=True)
    (target / "meta.json").write_text('{"classifications":{}}')
    (target / "fact_graph.json").write_text("{}")
    assert links(server) == []
    assert mark(server, action="unlink")[0] == 409
    assert not (target / "status.json").exists()
    assert mark(server)[0] == 409  # Q1's completed destruction record also blocks new writes.
    assert not (target / "status.json").exists()
    assert old != journey._status(target).get("journey", {}).get("family_instance")


def test_pending_recovery_cannot_modify_same_id_replacement(server, firm, monkeypatch):
    view = pending(server, firm, monkeypatch)
    target = firm["clients"] / "case-bia"
    shutil.rmtree(target)
    target.mkdir(); (target / "meta.json").write_text('{"classifications":{}}'); (target / "fact_graph.json").write_text("{}")
    held = ok(server, "sam", "/api/family-link-recovery?client=case-ana")
    assert held["state"] == "held" and not held["can_recover"]
    assert call(server, "sam", "/api/family-link-recovery", {"client": "case-ana", "action": "recover", "operation": view["operation"]})[0] == 400
    assert not (target / "status.json").exists() and links(server) == []


def test_ordinary_folder_replacement_needs_new_explicit_relationship(server, firm):
    assert mark(server)[0] == 200
    target = firm["clients"] / "case-bia"
    old = journey._status(target)["journey"]["family_instance"]
    shutil.rmtree(target)
    target.mkdir(); (target / "meta.json").write_text('{"classifications":{}}'); (target / "fact_graph.json").write_text('{"client_id":"case-bia","facts":{}}')
    assert links(server) == [] and mark(server, action="unlink")[0] == 400
    assert not (target / "status.json").exists()
    assert mark(server, relationship="Sibling")[0] == 200
    assert journey._status(target)["journey"]["family_instance"] != old
    assert links(server)[0]["relationship"] == "Sibling"


def test_candidate_search_empty_hidden_current_acl_counts_and_duplicates(server, firm):
    base = "/api/family-candidates?client=case-ana"
    assert ok(server, "jane", base)["clients"] == []
    hidden = ok(server, "jane", base + "&q=case-rosa")
    assert hidden == ok(server, "jane", base + "&q=unknown-fictional") and hidden["total"] == 0
    assert ok(server, "sam", base + "&q=case-rosa")["total"] == 1
    assert ok(server, "jane", base + "&q=case-ana")["total"] == 0
    assert ok(server, "jane", base + "&q=case&size=9999")["size"] == 50
    assert call(server, "jane", base + "&q=case&size=-1")[0] == 400
    restricted.mark(firm["clients"] / "case-bia", True, "Fictional current ACL change", "Sam Attorney", "attorney")
    assert ok(server, "jane", base + "&q=case-bia")["clients"] == []


def test_current_account_rechecked_after_sorted_locks(server, firm, monkeypatch):
    from contextlib import contextmanager
    import jobs
    held = jobs.case_lock
    @contextmanager
    def deactivate(root, case, timeout=None):
        with held(root, case, timeout):
            if case == "case-bia":
                server["accounts"].update("sam@firm.example", active=False)
            yield
    monkeypatch.setattr(jobs, "case_lock", deactivate)
    assert mark(server)[0] == 403
    assert not journey._status(firm["clients"] / "case-ana").get("journey", {}).get("family_instance")


def test_two_thousand_actual_folders_bounded_search_uses_current_acl(server, firm, monkeypatch, record_property):
    from test_case_assignment_lists import entry
    root, app = firm["clients"], server["app"]
    seeded = {}
    started = time.perf_counter()
    for n in range(1997):
        cid = f"family-fictional-{n:04d}"
        folder = root / cid; folder.mkdir()
        (folder / "meta.json").write_text('{"classifications":{}}')
        (folder / "fact_graph.json").write_text("{}")
        row = entry(cid, None, closed=n % 10 == 0, named=[])
        row["row"]["summary"]["name"] = "Fictional Duplicate Person" if n < 2 else f"Fictional Person {n:04d}"
        seeded[cid] = row
        if row["closed"]:
            (folder / "access.json").write_text(json.dumps({"marked": {"on": True}, "people": []}))
    assert len(list(root.iterdir())) == 2000
    def build(self, cid):
        self.reads += 1
        return seeded.get(cid) or entry(cid, None, closed=cid == "case-rosa", named=[])
    from review.roster import Roster
    monkeypatch.setattr(Roster, "build", build)
    monkeypatch.setenv("I485_WALK_EVERY", "600")
    app.roster.walk(parallel=False)
    before = app.roster.walks, app.roster.reads
    response = ok(server, "jane", "/api/family-candidates?client=case-ana&q=family-fictional&size=9999")
    assert response["size"] == len(response["clients"]) == 50 and response["total"] == 1797
    assert (app.roster.walks, app.roster.reads) == before
    duplicate = ok(server, "sam", "/api/family-candidates?client=case-ana&q=Fictional%20Duplicate%20Person")
    assert len(duplicate["clients"]) == 2 and len({x["id"] for x in duplicate["clients"]}) == 2
    hidden = ok(server, "jane", "/api/family-candidates?client=case-ana&q=family-fictional-0000")
    missing = ok(server, "jane", "/api/family-candidates?client=case-ana&q=unknown-fictional")
    assert hidden == missing
    record_property("actual_folder_count", 2000)
    record_property("fixture_and_queries_seconds", round(time.perf_counter() - started, 4))


def test_q2_internal_family_status_cannot_become_registered_client_document(server, firm):
    import client_file
    assert mark(server)[0] == 200
    folder = firm["clients"] / "case-ana"
    part = ".family-status-" + "a" * 32 + ".part"
    (folder / part).write_text("fictional unfinished family canary")
    catalog = json.loads((folder / "documents.json").read_text())
    catalog.setdefault("documents", []).append({"id": "fictional-internal-malformed", "type": "passport", "files": ["status.json", part]})
    (folder / "documents.json").write_text(json.dumps(catalog))
    selected, excluded, _ = client_file.gather(folder, firm["portal"])
    assert not any(x.source and x.source.name in ("status.json", part) for x in selected)
    assert any(x["path"].endswith("status.json") and "Internal" in x["why"] for x in excluded)


def test_completed_missing_audit_recovery_never_replays_older_relationship(server, firm, monkeypatch):
    import events
    with monkeypatch.context() as fault:
        fault.setattr(events, "record", lambda *args, **kwargs: None)
        assert mark(server)[0] == 200
    operation = journey._status(firm["clients"] / "case-ana")["journey"]["family_operations"][-1]["id"]
    assert mark(server, action="unlink")[0] == 200
    assert links(server) == [] and links(server, source="case-bia") == []
    result = ok(server, "sam", "/api/family-link-recovery", {"client": "case-ana", "action": "recover", "operation": operation})
    assert result["recovered"] and result["state"] == "none"
    assert links(server) == [] and links(server, source="case-bia") == []


def test_concurrent_reciprocal_writes_keep_one_consistent_pair(server, firm):
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=2) as threads:
        first = threads.submit(mark, server, source="case-ana", target="case-bia", relationship="Child")
        second = threads.submit(mark, server, source="case-bia", target="case-ana", relationship="Spouse")
        assert first.result()[0] == second.result()[0] == 200
    source, target = links(server), links(server, source="case-bia")
    assert len(source) == len(target) == 1
    assert journey.RELATIONSHIPS[source[0]["relationship"]] == target[0]["relationship"]
    for cid in ("case-ana", "case-bia"):
        status = journey._status(firm["clients"] / cid)["journey"]
        assert not status.get("family_pending") and all(p["state"] == "completed" for p in status["family_operations"])
