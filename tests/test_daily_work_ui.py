"""Actual fixture contracts before browser acceptance of staff-only UI changes."""

from assignment_route_fixtures import world, app, server, call, sign_in  # noqa: F401 -- pytest fixture registration and helper reexports
from test_review_evidence_routes import controls  # noqa: F401 -- pytest fixture registration and helper reexports
from daily_work_fixtures import typed_part14, SHORT
from evidence_browser_fixtures import retain_case


def test_typed_blank_and_short_part14_are_real_review_items(world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    case = typed_part14(world, app, tmp_path)
    items = app.items("case-ana")
    facts = {f["key"]: f for c in items["cards"] for f in c["facts"]}
    assert facts["applicant.p14_block1_text"]["value"] == SHORT
    assert facts["applicant.p14_block2_text"]["value"] == ""
    for key in ("applicant.p14_block1_text", "applicant.p14_block2_text"):
        card = next(c for c in items["cards"] if any(f["key"] == key for f in c["facts"]))
        assert "set" in card["actions"] or "confirm" in card["actions"]
        assert not card.get("source_prerequisites")
        assert all(s["doc"] == "portal questionnaire" for s in facts[key]["sources"])
    assert not (case / "part14_explanations.json").exists()


def test_packet_order_fixture_has_real_excluded_original_and_preserves_holds(world, app, tmp_path):  # noqa: F811 -- pytest fixture injection
    fixture = retain_case(world, app, tmp_path, translated=True)
    plan = app.packet_plan("case-ana", "i485")
    assert plan["unsorted"] or plan["left_out"]
    assert (fixture["source"] / "portuguese.pdf").is_file()
    assert any(c.get("evidence_fingerprints") for c in app.items("case-ana")["cards"])
