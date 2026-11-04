import copy
import datetime as dt

import pytest

from app.engines import costs
from tests.conftest import TODAY

MODELS = [
    {"id": "premium", "alias": "chat-default", "tier": "premium", "price_in_per_1k": 0.002, "price_out_per_1k": 0.006,
     "allowed_classes": ["C1", "C2"], "hosting": "cloud"},
    {"id": "small", "alias": "chat-small", "tier": "standard", "price_in_per_1k": 0.0002, "price_out_per_1k": 0.0006,
     "allowed_classes": ["C1", "C2"], "hosting": "cloud"},
]
USECASES = [
    {"id": "a", "cost_center": "cc1", "task_type": "classification", "sensitivity": "C1", "stage": "run",
     "model": {"alias": "chat-default"}},
    {"id": "b", "cost_center": "cc1", "task_type": "rag_qa", "sensitivity": "C2", "stage": "run",
     "model": {"alias": "chat-small"}},
    {"id": "c", "cost_center": "cc2", "task_type": "agent", "sensitivity": "C3", "stage": "mvp",
     "model": {"alias": "chat-sensitive"}},
]


def factory_cfg(**over):
    from app.config_loader import DEFAULT_FACTORY
    cfg = copy.deepcopy(DEFAULT_FACTORY)
    cfg["cost_centers"] = [{"id": "cc1", "name": "CC 1", "budget_month_eur": 1000},
                           {"id": "cc2", "name": "CC 2", "budget_month_eur": 500}]
    for k, v in over.items():
        cfg[k] = v
    return cfg


def rec(uc, day, **kw):
    base = {"date": (TODAY - dt.timedelta(days=day)).isoformat(), "usecase": uc, "requests": 100, "retries": 1,
            "tokens_in": 100_000, "tokens_out": 10_000, "cached_tokens": 30_000, "repeat_ratio": 0.1,
            "avg_prompt_tokens": 1000, "llm_eur": 10.0, "llm_in_eur": 6.0, "eval_eur": 1.0, "gpu_hours": 0.0}
    base.update(kw)
    return base


def records(**per_uc):
    out = []
    for uc in ("a", "b", "c"):
        for d in range(30):
            out.append(rec(uc, d, **per_uc.get(uc, {})))
    return out


def run(recs, cfg=None, mode=None, **kw):
    return costs.compute_costs(recs, USECASES, cfg or factory_cfg(), MODELS, days=30, today=TODAY, mode=mode, **kw)


def rules(result):
    return [a["rule"] for a in result["alerts"]]


# ---------------------------------------------------------------- totals and allocation
@pytest.mark.parametrize("allocation", ["tokens", "equal", "requests"])
def test_chargeback_sums_equal_totals(allocation):
    cfg = factory_cfg(chargeback={"mode": "chargeback", "shared_allocation": allocation})
    r = run(records(c={"llm_eur": 0.0, "llm_in_eur": 0.0, "eval_eur": 0.0, "gpu_hours": 2.0}), cfg)
    t = r["totals"]
    assert r["mode"] == "chargeback"
    uc_total = round(sum(u["total_eur"] for u in r["by_usecase"]), 2)
    assert uc_total == t["total_eur"]
    assert round(sum(u["platform_eur"] for u in r["by_usecase"]), 2) == t["platform_eur"]
    assert round(sum(c["spent_eur"] for c in r["by_cost_center"]), 2) == t["total_eur"]
    assert round(t["llm_eur"] + t["gpu_eur"] + t["platform_eur"], 2) == t["total_eur"]
    assert t["unallocated_platform_eur"] == 0.0
    for cc in r["by_cost_center"]:
        assert round(sum(line["eur"] for line in cc["invoice_lines"]), 2) == cc["spent_eur"]
        assert cc["billable"] is True


def test_showback_keeps_platform_unallocated():
    r = run(records())
    t = r["totals"]
    assert r["mode"] == "showback"
    assert all(u["platform_eur"] == 0 for u in r["by_usecase"])
    assert t["unallocated_platform_eur"] == t["platform_eur"] > 0
    assert round(sum(u["total_eur"] for u in r["by_usecase"]) + t["unallocated_platform_eur"], 2) == t["total_eur"]
    assert all(cc["billable"] is False for cc in r["by_cost_center"])


def test_mode_override_and_platform_pool():
    r = run(records(), mode="chargeback")
    assert r["mode"] == "chargeback"
    pb = r["totals"]["platform_breakdown"]
    direct = r["totals"]["llm_eur"] + r["totals"]["gpu_eur"]
    assert pb["overhead_eur"] == pytest.approx(direct * 0.12, abs=0.02)
    assert pb["vps_eur"] == 40.0
    assert round(pb["overhead_eur"] + pb["vps_eur"] + pb["idle_gpu_eur"], 2) == r["totals"]["platform_eur"]


def test_gpu_cost_uses_local_rates():
    rates = costs.local_gpu_rates(factory_cfg()["rates"])
    assert rates["amort_hour"] == pytest.approx(4500 / 36 / 720)
    assert rates["power_hour"] == pytest.approx(0.24 * 0.21)
    r = run(records(c={"llm_eur": 0.0, "llm_in_eur": 0.0, "eval_eur": 0.0, "gpu_hours": 1.0}))
    c = next(u for u in r["by_usecase"] if u["usecase"] == "c")
    assert c["gpu_hours"] == 30.0
    assert c["gpu_eur"] == pytest.approx(30 * rates["hour"], abs=0.01)


def test_cloud_gpu_hours_priced():
    r = run(records(b={"cloud_gpu_hours": {"h100": 1.0}}))
    b = next(u for u in r["by_usecase"] if u["usecase"] == "b")
    assert b["gpu_eur"] == pytest.approx(30 * 2.9, abs=0.01)


def test_per_usecase_ratios_and_trend():
    recs = [rec("a", d, requests=100 if d < 7 else 50, llm_eur=20.0 if d < 7 else 10.0) for d in range(30)]
    r = run(recs)
    a = next(u for u in r["by_usecase"] if u["usecase"] == "a")
    assert a["cached_ratio"] == 0.3
    assert a["retry_ratio"] == pytest.approx(30 / 1850, abs=1e-4)
    assert a["trend_pct"] == 100.0
    assert len(a["daily"]) == 30 and a["daily"][-1]["date"] == TODAY.isoformat()
    assert a["eur_per_request"] == pytest.approx(a["total_eur"] / a["requests"], abs=1e-4)


def test_window_excludes_old_records():
    recs = records() + [rec("a", 45, llm_eur=1000.0)]
    assert run(recs)["totals"]["llm_eur"] == run(records())["totals"]["llm_eur"]


# ---------------------------------------------------------------- the 8 waste rules
def test_no_alert_on_healthy_usage():
    r = run(records(), cfg=factory_cfg(cost_centers=[]))
    # 'a' is a classification on a premium model -> only oversized_model is expected
    assert rules(r) == ["oversized_model"]


def test_rule_oversized_model_with_saving():
    r = run(records())
    alert = next(a for a in r["alerts"] if a["rule"] == "oversized_model")
    assert alert["usecase"] == "a"
    a = next(u for u in r["by_usecase"] if u["usecase"] == "a")
    # cheaper model is 10x cheaper -> 90 % of the LLM spend saved
    assert alert["saving_eur_month"] == pytest.approx(a["llm_eur"] * 0.9, abs=0.05)
    assert "chat-small" in alert["action"]


def test_rule_low_cache_hit():
    r = run(records(b={"repeat_ratio": 0.5, "cached_tokens": 10_000}))
    alert = next(a for a in r["alerts"] if a["rule"] == "low_cache_hit")
    assert alert["usecase"] == "b" and alert["saving_eur_month"] > 0
    r2 = run(records(b={"repeat_ratio": 0.5, "cached_tokens": 40_000}))
    assert "low_cache_hit" not in rules(r2)


def test_rule_retry_storm_and_severity():
    r = run(records(b={"retries": 8}))
    alert = next(a for a in r["alerts"] if a["rule"] == "retry_storm")
    assert alert["severity"] == "warn" and alert["saving_eur_month"] > 0
    r = run(records(b={"retries": 12}))
    assert next(a for a in r["alerts"] if a["rule"] == "retry_storm")["severity"] == "crit"
    assert "retry_storm" not in rules(run(records(b={"retries": 5})))


def test_rule_prompt_bloat():
    recs = [rec("b", d, avg_prompt_tokens=1000 * (1 + 0.5 * (29 - d) / 29)) for d in range(30)]
    alert = next(a for a in run(recs)["alerts"] if a["rule"] == "prompt_bloat")
    assert alert["value"] > 30 and alert["saving_eur_month"] > 0
    flat = [rec("b", d, avg_prompt_tokens=1000 * (1 + 0.1 * (29 - d) / 29)) for d in range(30)]
    assert "prompt_bloat" not in rules(run(flat))


def gpu_series(idle_hours):
    out = []
    for h in range(24):
        util = 3.0 if h < idle_hours else 60.0
        out.append({"t": f"2026-11-03T{h:02d}:00:00+01:00", "device": "gpu0", "util_pct": util, "power_w": 40.0})
    return out


def test_rule_idle_gpu():
    r = run(records(), gpu_series=gpu_series(6))
    alert = next(a for a in r["alerts"] if a["rule"] == "idle_gpu")
    assert alert["value"] == 6 and alert["device"] == "gpu0"
    assert alert["saving_eur_month"] == pytest.approx(6 * 0.04 * 0.21 * 30, abs=0.01)
    assert "idle_gpu" not in rules(run(records(), gpu_series=gpu_series(3)))


def test_rule_orphan_index():
    idx = [
        {"index": "kb-live", "usecases": ["b"], "last_query_at": "2026-11-03T08:00:00+01:00", "eur_month": 4},
        {"index": "kb-stale", "usecases": ["b"], "last_query_at": "2026-10-01T08:00:00+01:00", "eur_month": 5},
        {"index": "kb-orphan", "usecases": [], "last_query_at": "2026-11-02T08:00:00+01:00", "eur_month": 3},
    ]
    alerts = [a for a in run(records(), indexes=idx)["alerts"] if a["rule"] == "orphan_index"]
    assert sorted(a["index"] for a in alerts) == ["kb-orphan", "kb-stale"]
    assert sum(a["saving_eur_month"] for a in alerts) == 8


def test_rule_eval_overspend():
    r = run(records(b={"eval_eur": 3.0}))
    alert = next(a for a in r["alerts"] if a["rule"] == "eval_overspend")
    assert alert["value"] == 30.0
    assert alert["saving_eur_month"] == pytest.approx((3.0 - 2.5) * 30, abs=0.01)
    assert "eval_overspend" not in rules(run(records(b={"eval_eur": 2.0})))


def test_rule_budget_breach_warn_and_crit():
    cfg = factory_cfg(cost_centers=[{"id": "cc1", "name": "CC 1", "budget_month_eur": 650},
                                    {"id": "cc2", "name": "CC 2", "budget_month_eur": 500}])
    r = run(records(), cfg)  # cc1 spends 600 EUR / 30 days -> 92 %
    alert = next(a for a in r["alerts"] if a["rule"] == "budget_breach")
    assert alert["cost_center"] == "cc1" and alert["severity"] == "warn"
    cfg["cost_centers"][0]["budget_month_eur"] = 500
    alert = next(a for a in run(records(), cfg)["alerts"] if a["rule"] == "budget_breach")
    assert alert["severity"] == "crit" and alert["saving_eur_month"] == pytest.approx(100, abs=1)


def test_all_eight_rules_can_fire_together():
    recs = []
    for d in range(30):
        recs.append(rec("a", d))
        recs.append(rec("b", d, repeat_ratio=0.6, cached_tokens=5_000, retries=9, eval_eur=4.0,
                        avg_prompt_tokens=1000 * (1 + 0.6 * (29 - d) / 29)))
    cfg = factory_cfg(cost_centers=[{"id": "cc1", "name": "CC 1", "budget_month_eur": 500}])
    idx = [{"index": "kb-orphan", "usecases": [], "last_query_at": "2026-09-01T00:00:00+00:00", "eur_month": 2}]
    r = run(recs, cfg, gpu_series=gpu_series(8), indexes=idx)
    assert set(rules(r)) == set(costs.RULE_ORDER)
    assert [a["id"] for a in r["alerts"]] == [f"w-{n:02d}" for n in range(1, len(r["alerts"]) + 1)]
    assert r["savings_eur_month"] == pytest.approx(sum(a["saving_eur_month"] for a in r["alerts"]), abs=0.01)


def test_disabled_rule_is_skipped():
    cfg = factory_cfg()
    cfg["waste_rules"] = {"retry_storm": {"max_retry_ratio": 0.05}}
    assert rules(run(records(b={"retries": 9}), cfg)) == ["retry_storm"]
