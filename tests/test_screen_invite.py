# ruff: noqa: F811  (the fixtures imported from test_operator are used as arguments)
"""A send that reached nobody is not an invitation (buyer visit 4; the verifier's third finding): the client stays in "Not invited yet".
Made-up people only."""

from __future__ import annotations

from test_operator import ANA, _quiet, add, seeded, world  # noqa: F401 -- the made-up front desk the operator tests build (fixtures)


def _stage(app, client_id):
    return next(r for r in app.overview("attorney", None)["clients"] if r["id"] == client_id)["stage"]


def test_an_invitation_that_reached_no_channel_leaves_the_client_not_invited(world):
    root, store, app = world
    add(app, ANA | {"consent": {"email": False, "sms": False, "whatsapp": False}})  # nobody agreed to any way of being reached
    done = app.client_invite("maria-exemplo-teste", {"reviewer": "Paulo Paralegal"})
    assert done["delivery"]["status"] == "none" and done["delivery"]["text"].startswith("Not sent")
    profile = store.profile("maria-exemplo-teste")
    assert not profile.get("invited_at") and profile["last_invite"]["status"] == "none"
    assert _stage(app, "maria-exemplo-teste") == "not_invited"  # not "Invited": nothing went out


def test_an_invitation_that_was_queued_or_sent_counts(world):
    root, store, app = world
    add(app, ANA)
    assert _stage(app, "maria-exemplo-teste") == "not_invited"
    app.client_invite("maria-exemplo-teste", {"reviewer": "Paulo Paralegal"})
    assert store.profile("maria-exemplo-teste")["invited_at"] and _stage(app, "maria-exemplo-teste") == "invited"
