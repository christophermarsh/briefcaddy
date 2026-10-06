"""Actual fictional ACLs; promotion must not expose data or restore permissions."""
import pytest
import json
import restricted
import case_notes
from portal import promotion
from test_contact_promotion import pair, run, pending  # noqa: F401 -- pytest fixture registration and helper reexports
from test_contact_transitions import firm  # noqa: F401 -- pytest fixture registration and helper reexports
from test_communication_consent import STAFF

PEER = "fictional-peer@example.test"


def protect(folder, peer=False):
    restricted.mark(folder, True, "Fictional confidentiality", "Fictional Attorney", "attorney")
    restricted.name_person(folder, STAFF, True, "Fictional Attorney", "attorney", "Fictional Staff")
    if peer:
        users = folder.parent.parent / "review_users.json"
        value = json.loads(users.read_text())
        value["users"][PEER] = {"name": "Fictional Peer", "role": "paralegal", "active": True}
        users.write_text(json.dumps(value))
        restricted.name_person(folder, PEER, True, "Fictional Attorney", "attorney", "Fictional Peer")


@pytest.mark.parametrize("target_policy", ["ordinary", "extra_peer", "messages"])
def test_existing_pair_broader_target_refuses_before_coordinator_or_data_copy(pair, target_policy):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    origin, destination = source[0].cases / source[2], target[0].cases / target[2]
    protect(origin)
    case_notes.add_note(origin, "Fictional confidential original note", "Fictional Staff", "paralegal", prospect=True)
    if target_policy != "ordinary": protect(destination, peer=target_policy == "extra_peer")
    if target_policy == "messages":
        restricted.set_messages(destination, True, "Fictional target-only policy", "Fictional Attorney", "attorney")
    before = (origin / "access.json").read_bytes()
    with pytest.raises(ValueError, match="Promotion is held"):
        run(pair)
    assert not (source[1].client_dir(source[2]) / promotion.FILE).exists()
    assert not (destination / "notes.json").exists()
    assert (origin / "access.json").read_bytes() == before


@pytest.mark.parametrize("changed", ["source_peer_removed", "target_peer_added", "target_messages_on", "target_restriction_lifted"])
def test_post_carry_current_confidentiality_change_is_held_without_regrant_or_recopied_data(pair, monkeypatch, changed):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    origin, destination = source[0].cases / source[2], target[0].cases / target[2]
    protect(origin, peer=True)
    protect(destination, peer=True)
    case_notes.add_note(origin, "Fictional confidential note", "Fictional Staff", "paralegal", prospect=True)
    with monkeypatch.context() as fault:
        operation = pending(pair, fault, "carry")
    if changed == "source_peer_removed":
        restricted.name_person(origin, PEER, False, "Fictional Attorney", "attorney")
    elif changed == "target_peer_added":
        restricted.name_person(destination, "new-peer@fictional.example", True, "Fictional Attorney", "attorney", "Fictional New Peer")
    elif changed == "target_messages_on":
        restricted.set_messages(destination, True, "Fictional newer target policy", "Fictional Attorney", "attorney")
    else:
        restricted.mark(destination, False, "", "Fictional Attorney", "attorney")
    snapshots = {(folder, name): (folder / name).read_bytes() for folder in (origin, destination) for name in ("access.json",) if (folder / name).exists()}
    copied = (destination / "notes.json").read_bytes()
    with pytest.raises(ValueError, match="Promotion is held"):
        promotion.recover(*source, operation, actor_email=STAFF)
    assert all((folder / name).read_bytes() == value for (folder, name), value in snapshots.items())
    assert (destination / "notes.json").read_bytes() == copied
    if changed == "source_peer_removed":
        assert not restricted.visible_to({"email": PEER, "role": "paralegal"}, origin)


def test_safe_narrower_existing_target_preserves_both_acl_records_without_adding_names(pair):  # noqa: F811 -- pytest fixture injection
    target, source = pair
    origin, destination = source[0].cases / source[2], target[0].cases / target[2]
    protect(origin, peer=True)
    protect(destination)
    before = [(folder / "access.json").read_bytes() for folder in (origin, destination)]
    assert run(pair)["state"] == "completed"
    assert [(folder / "access.json").read_bytes() for folder in (origin, destination)] == before
