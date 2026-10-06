"""Protected promotion adapter composition over fictional real loopback HTTP."""
# ruff: noqa: F811 -- imported canonical pytest fixtures are injected by name
import json

import prospects
import restricted
from portal import promotion
from test_prospects import firm, server, ok, call, new, change, NAME, CALL  # noqa: F401 -- fixtures


def add_body(srv, pid):
    found = ok(srv, "sam", "/api/conflict-search", {"purpose": "add", "name": NAME})
    return {"name": NAME, "phone": CALL["phone"], "email": CALL["email"], "language": "es", "prospect": pid,
            "filing": "i485", "conflict": {"search": found["id"], "decision": "none"}, "reviewer": "Spoofed reviewer", "role": "attorney"}


def test_new_target_adapter_passes_current_actor_and_promotes_once(server, firm, monkeypatch):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia"})
    def forbidden(*args, **kwargs):
        raise AssertionError("New-target wrapper must not perform a second promotion")
    monkeypatch.setattr(prospects, "became_client", forbidden)
    result = ok(server, "sam", "/api/client-add", add_body(server, pid))
    assert result["from_call"]["answers"] == 1 and result["promotion"]["state"] == "completed"
    store = prospects.store(firm["portal"])
    record = promotion._record(store.communication_scope(), store, pid)
    assert record["history"][0]["actor"] == "sam@firm.example"
    assert firm["store"].profile(result["id"])["consent"] == {"email": False, "sms": False, "whatsapp": False}
    assert not list((firm["portal"] / "outbox").glob("*"))
    view = ok(server, "sam", "/api/promotion-recovery?client=" + pid + "&store_kind=prospect")
    assert view["state"] == "completed" and not view["can_recover"]


def test_pending_new_target_exact_recovery_rechecks_source_and_target(server, firm, monkeypatch):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia"})
    body = add_body(server, pid)
    real_save = promotion._save
    with monkeypatch.context() as fault:
        def save(scope, store, client, record):
            real_save(scope, store, client, record)
            if record["role"] == "source" and record["state"] == "pending":
                raise OSError("Fictional source reservation interruption")
        fault.setattr(promotion, "_save", save)
        status, raw = call(server, "sam", "/api/client-add", body)
        assert status == 409 and "promotion recovery" in json.loads(raw)["error"]
    query = "/api/promotion-recovery?client=" + pid + "&store_kind=prospect"
    view = ok(server, "sam", query)
    assert view["state"] == "pending" and view["can_recover"]
    target = firm["clients"] / view["client"]
    target.mkdir(exist_ok=True)
    restricted.mark(target, True, "Fictional protected reserved target", "Sam Attorney", "attorney")
    assert call(server, "jane", query)[0] == 404
    request = {"client": pid, "store_kind": "prospect", "action": "recover", "operation": view["operation"], "reviewer": "Spoofed reviewer", "role": "attorney"}
    assert call(server, "jane", "/api/promotion-recovery", request)[0] == 404
    assert call(server, "sam", "/api/promotion-recovery", request | {"operation": "f" * 32})[0] == 400
    # Reconciliation uses the immutable proposed confidentiality, so a newly restricted
    # target remains held rather than silently expanding either audience.
    status, raw = call(server, "sam", "/api/promotion-recovery", request)
    assert status in (400, 409) and "error" in json.loads(raw)
    assert ok(server, "sam", query)["state"] == "pending"


def test_pending_new_target_recovers_same_reserved_client_without_new_consent_or_send(server, firm, monkeypatch):
    pid = new(server)
    change(server, "jane", pid, "answers", answers={"given_name": "Lia"})
    body = add_body(server, pid)
    real_save = promotion._save
    with monkeypatch.context() as fault:
        def save(scope, store, client, record):
            real_save(scope, store, client, record)
            if record["role"] == "source" and record["state"] == "pending":
                raise OSError("Fictional source reservation interruption")
        fault.setattr(promotion, "_save", save)
        status, raw = call(server, "sam", "/api/client-add", body)
        assert status == 409 and "not confirmed" in json.loads(raw)["error"]
    query = "/api/promotion-recovery?client=" + pid + "&store_kind=prospect"
    view = ok(server, "sam", query)
    result = ok(server, "sam", "/api/promotion-recovery", {"client": pid, "store_kind": "prospect", "action": "recover", "operation": view["operation"]})
    assert result["state"] == "completed" and result["client"] == view["client"] and not result["can_recover"]
    assert firm["store"].answers(view["client"])["given_name"] == "Lia"
    assert firm["store"].profile(view["client"])["consent"] == {"email": False, "sms": False, "whatsapp": False}
    assert not (firm["clients"] / (view["client"] + "-2")).exists()
    assert not list((firm["portal"] / "outbox").glob("*"))
