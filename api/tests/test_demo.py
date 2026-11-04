import datetime as dt
import json

from app.demo import generate_demo
from app.engines.costs import RULE_ORDER
from app.engines.dataloop import ALLOWED_ACTIONS
from app.snapshot import assemble_snapshot
from tests.conftest import TODAY


def snap_for(cfg, demo, today=TODAY):
    now = dt.datetime(today.year, today.month, today.day, 10, tzinfo=dt.timezone.utc)
    return assemble_snapshot(cfg, cfg["usecases"], demo, {}, demo_mode="always", seed=demo["seed"], today=today,
                             now=now)


def test_same_seed_and_date_is_identical(cfg):
    a = generate_demo(cfg, 42, TODAY)
    b = generate_demo(cfg, 42, TODAY)
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)
    assert json.dumps(snap_for(cfg, a), sort_keys=True) == json.dumps(snap_for(cfg, b), sort_keys=True)


def test_seed_or_date_changes_output(cfg):
    base = json.dumps(generate_demo(cfg, 42, TODAY), sort_keys=True)
    assert json.dumps(generate_demo(cfg, 43, TODAY), sort_keys=True) != base
    assert json.dumps(generate_demo(cfg, 42, TODAY - dt.timedelta(days=1)), sort_keys=True) != base


def test_thirty_days_anchored_on_today(cfg):
    demo = generate_demo(cfg, 42, TODAY)
    dates = sorted({r["date"] for r in demo["usage"]})
    assert len(dates) == 30
    assert dates[-1] == TODAY.isoformat()
    assert dates[0] == (TODAY - dt.timedelta(days=29)).isoformat()
    # only fictional configured use cases appear, retired / cadrage ones have no traffic
    used = {r["usecase"] for r in demo["usage"]}
    assert used <= {u["id"] for u in cfg["usecases"]}
    assert "uc-07" not in used and "uc-06" not in used


def test_demo_covers_contract_scenarios(cfg):
    snap = snap_for(cfg, generate_demo(cfg, 42, TODAY))
    pipe = snap["pipeline"]
    assert any(r["auto"] for r in pipe["rollbacks"])
    assert any(i["blocked_by"] == "risk_reassessment" for i in pipe["items"])
    actions = {e["action"] for e in snap["dataloop"]["events"]}
    assert actions == set(ALLOWED_ACTIONS)
    assert snap["dataloop"]["retraining"] == "disabled"
    rules = {a["rule"] for a in snap["costs"]["alerts"]}
    assert rules == set(RULE_ORDER), rules - set(RULE_ORDER) or set(RULE_ORDER) - rules
    assert snap["gpu"]["source"] == "sim" and snap["gpu"]["devices"]
    assert snap["agentops"]["by_agent"] and snap["agentops"]["series"]
    assert len(snap["agentops"]["series"]) == 24


def test_forced_block_when_config_has_no_pending_review(cfg):
    import copy
    cfg2 = copy.deepcopy(cfg)
    for u in cfg2["usecases"]:
        u["assessment"]["reviewed_model_version"] = u["model"]["version"]
    demo = generate_demo(cfg2, 42, TODAY)
    assert demo["picks"]["forced_block"]
    snap = snap_for(cfg2, demo)
    assert not any(r["risk"]["reevaluation_required"] for r in snap["registry"] if r["risk"]["tier"] != "prohibited")
    blocked = [i for i in snap["pipeline"]["items"] if i["blocked_by"] == "risk_reassessment"]
    assert blocked and blocked[0]["usecase"] == demo["picks"]["forced_block"]
    assert any(r["auto"] for r in snap["pipeline"]["rollbacks"])


def test_demo_works_with_empty_config():
    from app.config_loader import load_config
    cfg = load_config("/nonexistent-config-dir")
    snap = snap_for(cfg, generate_demo(cfg, 1, TODAY))
    assert snap["registry"] == [] and snap["costs"]["by_usecase"] == []
    assert snap["meta"]["warnings"]
