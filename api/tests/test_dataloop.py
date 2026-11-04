import pytest

from app.config_loader import DEFAULT_FACTORY
from app.engines import dataloop

REGISTRY = [{"id": "uc-lim", "risk": {"tier": "limited"}}, {"id": "uc-high", "risk": {"tier": "high"}}]


@pytest.mark.parametrize("signal,value,threshold,action", [
    ("retrieval_hit_rate", 0.62, 0.75, "reindex"),
    ("corpus_freshness", 0.15, 0.10, "reindex"),
    ("query_drift", 0.33, 0.25, "update_eval_set"),
    ("eval_failure_cluster", 7, 5, "update_eval_set"),
    ("cost_drift", 30, 20, "change_routing"),
    ("latency_drift", 25, 20, "change_routing"),
])
def test_signal_to_action(signal, value, threshold, action):
    assert dataloop.decide_action(signal, value, threshold, "limited")[0] == action


def test_change_routing_on_high_risk_goes_to_human_review():
    action, why = dataloop.decide_action("cost_drift", 30, 20, "high")
    assert action == "human_review" and "high-risk" in why
    action_fr, why_fr = dataloop.decide_action("cost_drift", 30, 20, "high", "fr")
    assert action_fr == action and "haut risque" in why_fr


def test_severe_drift_goes_to_human_review():
    assert dataloop.decide_action("latency_drift", 45, 20, "minimal")[0] == "human_review"
    assert dataloop.decide_action("retrieval_hit_rate", 0.3, 0.75, "minimal")[0] == "human_review"


def test_unknown_signal_goes_to_human_review():
    assert dataloop.decide_action("mystery", 1, 0, "minimal")[0] == "human_review"


@pytest.mark.parametrize("action", ["retrain", "fine_tune", "fine-tune", "finetune", "Retrain", "train_lora",
                                    "continued_pretraining", "fine_tuning"])
def test_retrain_and_fine_tune_are_refused(action):
    with pytest.raises(dataloop.ForbiddenActionError):
        dataloop.validate_action(action, fine_tuning=False)
    # even a (misconfigured) fine_tuning=true cannot enable a training path
    with pytest.raises(dataloop.ForbiddenActionError):
        dataloop.validate_action(action, fine_tuning=True)


def test_unknown_action_refused_and_allowed_actions_pass():
    with pytest.raises(dataloop.ForbiddenActionError):
        dataloop.validate_action("delete_index")
    for a in dataloop.ALLOWED_ACTIONS:
        assert dataloop.validate_action(a) == a


def test_requested_retrain_in_signal_is_refused():
    signals = [{"usecase": "uc-lim", "signal": "query_drift", "value": 0.4, "threshold": 0.25,
                "requested_action": "retrain"}]
    with pytest.raises(dataloop.ForbiddenActionError):
        dataloop.evaluate_signals(signals, REGISTRY, DEFAULT_FACTORY)


def test_evaluate_signals_covers_four_actions_and_disables_retraining():
    signals = [
        {"at": "2026-11-01", "usecase": "uc-lim", "signal": "retrieval_hit_rate", "value": 0.6, "threshold": 0.75, "status": "done"},
        {"at": "2026-11-02", "usecase": "uc-lim", "signal": "query_drift", "value": 0.3, "status": "running"},
        {"at": "2026-11-03", "usecase": "uc-lim", "signal": "cost_drift", "value": 30, "threshold": 20},
        {"at": "2026-10-30", "usecase": "uc-high", "signal": "cost_drift", "value": 30, "threshold": 20, "status": "done"},
        {"at": "2026-10-29", "usecase": "uc-lim", "signal": "latency_drift", "value": 10, "threshold": 20},  # no breach
    ]
    out = dataloop.evaluate_signals(signals, REGISTRY, DEFAULT_FACTORY)
    assert out["retraining"] == "disabled" and out["fine_tuning"] is False
    assert [e["at"] for e in out["events"]] == ["2026-11-03", "2026-11-02", "2026-11-01", "2026-10-30"]
    actions = {e["action"] for e in out["events"]}
    assert actions == set(dataloop.ALLOWED_ACTIONS)
    by_at = {e["at"]: e for e in out["events"]}
    assert by_at["2026-11-02"]["threshold"] == 0.25  # default threshold
    assert by_at["2026-11-03"]["status"] == "proposed"  # missing status
    assert by_at["2026-10-30"]["status"] == "proposed"  # human review cannot be 'done' without a reviewer
    assert all(e["action"] in dataloop.ALLOWED_ACTIONS for e in out["events"])
