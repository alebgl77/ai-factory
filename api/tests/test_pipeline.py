import copy

import pytest

from app.config_loader import DEFAULT_FACTORY
from app.engines import pipeline

GATES = DEFAULT_FACTORY["eval_gates"]
ROLLBACK = DEFAULT_FACTORY["rollback"]
GOOD = {"faithfulness": 0.93, "answer_relevancy": 0.9, "context_precision": 0.88, "judge_score": 4.4,
        "redteam_pass": 0.995}


def make_run(**over):
    run = {
        "usecase": "uc-x", "version": "rag-1.2.0", "previous_version": "rag-1.1.9", "prompt_version": "p-3",
        "agent_version": "1.0.0", "model_version": "v2",
        "stages": {"ingestion": {"at": "t1"}, "indexing": {"at": "t2"}, "versioning": {"at": "t3"},
                   "deploy": {"at": "t5"}},
        "eval": {"at": "t4", "metrics": dict(GOOD)},
        "online": {"at": "t6", "thumbs_down_ratio": 1.1, "faithfulness_drop": 0.01, "error_rate": 0.01},
    }
    run.update(over)
    return run


def risk(tier="limited", reeval=False, decision="approved", reviewed="v2"):
    return {"tier": tier, "reevaluation_required": reeval, "reason": "model v3 not reviewed (last v2)" if reeval else None,
            "deploy_block": "assessment_rejected" if decision == "rejected" else None,
            "reviewed_model_version": reviewed}


def statuses(item):
    return {s["key"]: s["status"] for s in item["stages"]}


def test_seven_stages_in_order():
    item, rb = pipeline.evaluate_run(make_run(), risk(), GATES, ROLLBACK)
    assert [s["key"] for s in item["stages"]] == pipeline.STAGES
    assert set(statuses(item).values()) == {"passed"}
    assert rb is None and item["blocked_by"] is None and item["gate_passed"]
    assert item["deployed_version"] == "rag-1.2.0"


@pytest.mark.parametrize("tier", ["minimal", "limited", "high"])
def test_gate_thresholds_by_tier(tier):
    assert pipeline.gate_thresholds(tier, GATES) == GATES[tier]
    assert pipeline.gate_thresholds("prohibited", GATES) is None


def test_same_metrics_pass_limited_but_fail_high():
    metrics = {"faithfulness": 0.86, "answer_relevancy": 0.86, "context_precision": 0.8, "judge_score": 4.0,
               "redteam_pass": 0.97}
    run = make_run(eval={"at": "t", "metrics": metrics})
    limited, _ = pipeline.evaluate_run(run, risk("limited"), GATES, ROLLBACK)
    high, _ = pipeline.evaluate_run(run, risk("high"), GATES, ROLLBACK)
    assert statuses(limited)["eval_gate"] == "passed"
    assert statuses(high)["eval_gate"] == "failed"
    gate = next(s for s in high["stages"] if s["key"] == "eval_gate")
    assert gate["thresholds"] == GATES["high"]
    assert "faithfulness" in gate["detail"]
    assert statuses(high)["deploy"] == "blocked" and high["blocked_by"] == "eval_gate"


def test_missing_metric_fails_gate():
    metrics = dict(GOOD)
    metrics.pop("redteam_pass")
    item, _ = pipeline.evaluate_run(make_run(eval={"metrics": metrics}), risk(), GATES, ROLLBACK)
    assert statuses(item)["eval_gate"] == "failed"


def test_deploy_blocked_when_risk_reassessment_required():
    item, rb = pipeline.evaluate_run(make_run(), risk(reeval=True), GATES, ROLLBACK)
    st = statuses(item)
    assert st["eval_gate"] == "passed"
    assert st["deploy"] == "blocked"
    assert item["blocked_by"] == "risk_reassessment"
    assert st["rollback_guard"] == "skipped" and rb is None


def test_deploy_blocked_when_candidate_changes_model_version():
    item, _ = pipeline.evaluate_run(make_run(model_version="v3"), risk(reviewed="v2"), GATES, ROLLBACK)
    assert item["blocked_by"] == "risk_reassessment"
    deploy = next(s for s in item["stages"] if s["key"] == "deploy")
    assert "v3" in deploy["detail"]


def test_prohibited_and_rejected_never_deploy():
    item, _ = pipeline.evaluate_run(make_run(), risk("prohibited"), GATES, ROLLBACK)
    assert item["blocked_by"] == "prohibited_practice" and statuses(item)["deploy"] == "blocked"
    item, _ = pipeline.evaluate_run(make_run(), risk(decision="rejected"), GATES, ROLLBACK)
    assert item["blocked_by"] == "assessment_rejected"


@pytest.mark.parametrize("online,trigger,value,threshold", [
    ({"thumbs_down_ratio": 2.6, "faithfulness_drop": 0.0, "error_rate": 0.0}, "thumbs_down_ratio", 2.6, 2.0),
    ({"thumbs_down_ratio": 1.0, "faithfulness_drop": 0.12, "error_rate": 0.0}, "online_faithfulness_drop", 0.12, 0.08),
    ({"thumbs_down_ratio": 1.0, "faithfulness_drop": 0.0, "error_rate": 0.07}, "error_rate", 0.07, 0.05),
])
def test_auto_rollback_triggers(online, trigger, value, threshold):
    item, rb = pipeline.evaluate_run(make_run(online={"at": "t9", **online}), risk(), GATES, ROLLBACK)
    assert rb == {"at": "t9", "usecase": "uc-x", "from": "rag-1.2.0", "to": "rag-1.1.9", "trigger": trigger,
                  "value": value, "threshold": threshold, "auto": True}
    assert statuses(item)["rollback_guard"] == "failed"
    assert item["deployed_version"] == "rag-1.1.9"


def test_threshold_equal_is_not_a_breach():
    assert pipeline.check_rollback({"thumbs_down_ratio": 2.0}, ROLLBACK) is None


def test_in_progress_and_failed_upstream():
    item, _ = pipeline.evaluate_run(make_run(in_progress_stage="indexing"), risk(), GATES, ROLLBACK)
    st = statuses(item)
    assert st["indexing"] == "running" and st["versioning"] == "pending" and st["eval_gate"] == "pending"
    assert st["deploy"] == "pending"
    item, _ = pipeline.evaluate_run(make_run(failed_stage="ingestion"), risk(), GATES, ROLLBACK)
    assert statuses(item)["ingestion"] == "failed" and statuses(item)["indexing"] == "pending"


def test_deployed_without_feedback_is_monitored():
    item, rb = pipeline.evaluate_run(make_run(online=None), risk(), GATES, ROLLBACK)
    assert statuses(item)["feedback"] == "running" and statuses(item)["rollback_guard"] == "running"
    assert rb is None


def test_evaluate_pipeline_merges_history_and_counts():
    registry = [{"id": "uc-x", "risk": risk(), "assessment": {"reviewed_model_version": "v2"}},
                {"id": "uc-y", "risk": risk(reeval=True), "assessment": {"reviewed_model_version": "v1"}}]
    runs = [make_run(online={"at": "2026-11-02", "thumbs_down_ratio": 3}),
            make_run(usecase="uc-y", model_version="v2"), make_run(usecase="unknown")]
    history = [{"at": "2026-10-01", "usecase": "uc-y", "from": "1", "to": "0", "trigger": "error_rate",
                "value": 0.07, "threshold": 0.05, "auto": True}]
    cfg = copy.deepcopy(DEFAULT_FACTORY)
    out = pipeline.evaluate_pipeline(runs, registry, cfg, history)
    assert out["stages"] == pipeline.STAGES
    assert len(out["items"]) == 2  # unknown use case ignored
    assert [r["at"] for r in out["rollbacks"]] == ["2026-11-02", "2026-10-01"]
    assert out["summary"]["blocked_risk"] == 1 and out["summary"]["rollbacks"] == 2
