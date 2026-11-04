import copy

import pytest

from app.engines import risk
from tests.conftest import TODAY


def uc(**ai_act):
    base = {"prohibited_practices": [], "annex_iii_area": None, "art6_3_exemption": False,
            "annex_i_safety_component": False, "transparency": [], "gpai_used": False}
    base.update(ai_act)
    return {"id": "uc-x", "ai_act": base, "model": {"version": "v2"},
            "assessment": {"reviewed_model_version": "v2", "decision": "approved"},
            "nist": {"govern": {"status": "done"}, "map": {"status": "done"},
                     "measure": {"status": "partial"}, "manage": {"status": "todo"}}}


@pytest.mark.parametrize("ai_act,tier", [
    ({"prohibited_practices": ["art5_1_a"]}, "prohibited"),
    ({"prohibited_practices": ["art5_1_a"], "annex_iii_area": "employment"}, "prohibited"),
    ({"annex_iii_area": "employment"}, "high"),
    ({"annex_i_safety_component": True}, "high"),
    ({"annex_iii_area": "education", "art6_3_exemption": True, "transparency": ["chatbot"]}, "limited"),
    ({"annex_iii_area": "education", "art6_3_exemption": True}, "minimal"),
    ({"annex_iii_area": "education", "art6_3_exemption": True, "annex_i_safety_component": True}, "high"),
    ({"transparency": ["chatbot"]}, "limited"),
    ({}, "minimal"),
])
def test_tiers(ai_act, tier):
    assert risk.compute_tier(uc(**ai_act)["ai_act"]) == tier


def test_all_four_tiers_in_fixture(cfg):
    reg = risk.evaluate_registry(cfg["usecases"], cfg["factory"], cfg["controls"], cfg["profiles"]["people"], TODAY)
    tiers = {r["id"]: r["risk"]["tier"] for r in reg}
    assert tiers["uc-06"] == "prohibited"
    assert tiers["uc-04"] == "high"
    assert tiers["uc-01"] == "limited"
    assert tiers["uc-03"] == "minimal"
    assert tiers["uc-05"] == "minimal"  # Annex III area but documented Art. 6(3) exemption


def test_art6_3_exemption_changes_milestone(cfg):
    reg = cfg["factory"]["regulatory"]
    high = risk.evaluate_risk(uc(annex_iii_area="education"), reg, cfg["controls"], TODAY)
    exempt = risk.evaluate_risk(uc(annex_iii_area="education", art6_3_exemption=True), reg, cfg["controls"], TODAY)
    assert high["tier"] == "high" and high["applicable_from"] == "2027-12-02"
    assert exempt["tier"] == "minimal" and exempt["applicable_from"] == "2025-02-02"
    assert exempt["art6_3_exemption"] is True
    assert "risk-management" in high["required_controls"]
    assert "risk-management" not in exempt["required_controls"]


@pytest.mark.parametrize("ai_act,date,milestone", [
    ({"prohibited_practices": ["x"]}, "2025-02-02", "prohibitions"),
    ({"annex_iii_area": "employment"}, "2027-12-02", "annex_iii"),
    ({"annex_i_safety_component": True}, "2028-08-02", "annex_i"),
    ({"annex_i_safety_component": True, "annex_iii_area": "employment"}, "2027-12-02", "annex_iii"),
    ({"transparency": ["chatbot"]}, "2026-08-02", "transparency"),
    ({}, "2025-02-02", "literacy"),
])
def test_applicable_from_and_days_left(cfg, ai_act, date, milestone):
    r = risk.evaluate_risk(uc(**ai_act), cfg["factory"]["regulatory"], cfg["controls"], TODAY)
    assert r["applicable_from"] == date
    assert r["applicable_milestone"] == milestone
    import datetime as dt
    assert r["days_left"] == (dt.date.fromisoformat(date) - TODAY).days
    assert r["in_force"] == (r["days_left"] <= 0)


def test_limited_example_days_left_matches_contract(cfg):
    r = risk.evaluate_risk(uc(transparency=["chatbot"]), cfg["factory"]["regulatory"], cfg["controls"], TODAY)
    assert r["days_left"] == -93  # contract example for 2026-11-03


def test_reevaluation_on_model_version_change():
    u = uc()
    assert risk.reevaluation(u) == (False, None)
    changed = copy.deepcopy(u)
    changed["model"]["version"] = "v3"
    flag, reason = risk.reevaluation(changed)
    assert flag is True
    assert reason == "model v3 not reviewed (last v2)"


def test_reevaluation_on_pending_decision():
    u = uc()
    u["assessment"]["decision"] = "pending"
    flag, reason = risk.reevaluation(u)
    assert flag and "pending" in reason


def test_deploy_block_reasons(cfg):
    reg = cfg["factory"]["regulatory"]
    u = uc()
    u["model"]["version"] = "v9"
    assert risk.evaluate_risk(u, reg, [], TODAY)["deploy_block"] == "risk_reassessment"
    u = uc()
    u["assessment"]["decision"] = "rejected"
    assert risk.evaluate_risk(u, reg, [], TODAY)["deploy_block"] == "assessment_rejected"
    assert risk.evaluate_risk(uc(prohibited_practices=["x"]), reg, [], TODAY)["deploy_block"] == "prohibited_practice"
    assert risk.evaluate_risk(uc(), reg, [], TODAY)["deploy_block"] is None


def test_required_controls_by_tier_and_gpai(cfg):
    controls = cfg["controls"]
    assert risk.required_controls("minimal", False, controls) == ["literacy"]
    assert risk.required_controls("minimal", True, controls) == ["literacy", "gpai-docs"]
    assert set(risk.required_controls("high", False, controls)) == {
        "risk-management", "data-governance", "human-oversight", "transparency", "literacy"}
    assert risk.required_controls("prohibited", False, controls) == []


def test_controls_status_uses_iso_refs(cfg):
    status = risk.controls_status(["risk-management", "transparency"], cfg["controls"], ["A.5.2"])
    assert status[0]["implemented"] is True and status[1]["implemented"] is False


def test_nist_progress():
    assert risk.nist_progress(uc()) == 0.62  # done, done, partial, todo = 2.5 / 4
    u = uc()
    u["nist"] = {}
    assert risk.nist_progress(u) == 0.0
    assert risk.nist_status(u) == {"govern": "todo", "map": "todo", "measure": "todo", "manage": "todo"}


def test_squad_includes_owner(cfg):
    people = cfg["profiles"]["people"]
    u = next(x for x in cfg["usecases"] if x["id"] == "uc-01")
    assert risk.squad_of(u, people) == ["p02", "p03"]
