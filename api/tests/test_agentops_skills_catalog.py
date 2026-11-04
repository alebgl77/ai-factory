import datetime as dt

import pytest

from app.engines import agentops, catalog, risk, skills
from app.util import allocate_cents, percentile
from tests.conftest import TODAY

SLO = {"ttft_ms_p95": 1500, "vector_latency_ms_p95": 120, "tokens_per_task_p95": 12000}


def test_percentile_linear():
    assert percentile([], 0.95) == 0.0
    assert percentile([5], 0.5) == 5
    assert percentile([1, 2, 3, 4], 0.5) == 2.5
    assert percentile(list(range(1, 101)), 0.95) == pytest.approx(95.05)


def test_allocate_cents_exact():
    parts = allocate_cents(1000, [1, 1, 1])
    assert sum(parts) == 1000 and sorted(parts) == [333, 333, 334]
    assert allocate_cents(5, [0, 0]) == [3, 2]
    assert allocate_cents(0, [1, 2]) == [0, 0]


def test_agent_stats_and_breaches():
    fast = [{"ttft_ms": 400 + i, "tokens": 3000, "vector_latency_ms": 50, "success": True, "eur": 0.01} for i in range(20)]
    slow = [{"ttft_ms": 2000, "tokens": 15000, "vector_latency_ms": 200, "success": i % 2 == 0, "eur": 0.02}
            for i in range(10)]
    out = agentops.evaluate_agentops({"ag-fast": fast, "ag-slow": slow},
                                     [{"id": "ag-fast", "usecase": "uc-01", "name": "Rapide"}], SLO, [{"t": "x"}])
    by = {a["agent"]: a for a in out["by_agent"]}
    assert by["ag-fast"]["breaches"] == [] and by["ag-fast"]["usecase"] == "uc-01"
    assert set(by["ag-slow"]["breaches"]) == set(agentops.SLO_KEYS)
    assert by["ag-slow"]["success_rate"] == 0.5
    assert by["ag-fast"]["eur_per_task"] == 0.01
    assert out["global"]["tasks"] == 30
    assert out["breaching_agents"] == ["ag-slow"]
    assert out["slo"] == SLO and out["window"] == "1h" and out["series"] == [{"t": "x"}]


def test_skills_coverage_per_role_and_person(cfg):
    out = skills.evaluate_skills(cfg["skills"], cfg["profiles"], cfg["factory"]["regulatory"], TODAY)
    roles = {r["role"]: r for r in out["roles"]}
    assert roles["head"]["coverage"] == 1.0
    assert roles["po"]["coverage"] == 0.5
    assert roles["llm_engineer"]["coverage"] == pytest.approx(3 / 8, abs=0.01)
    people = {p["person"]: p for p in out["people"]}
    assert people["p03"]["required"] == 4 and people["p03"]["done"] == 2
    # m-redteam is planned in an upcoming session; m-no-ai-kata (art. 4) is overdue
    assert people["p03"]["planned"] == ["m-redteam"]
    assert people["p03"]["overdue"] == ["m-no-ai-kata"]
    # p04 has not done the art. 4 literacy module
    assert "m-literacy-101" in people["p04"]["overdue"]
    total_req = sum(p["required"] for p in out["people"])
    total_done = sum(p["done"] for p in out["people"])
    assert out["coverage"] == round(total_done / total_req, 2)
    assert out["art4"]["status"] == "partial"
    assert "2026/1744" in out["art4"]["note"]
    s1 = next(s for s in out["sessions"] if s["id"] == "s-2026-11-05")
    assert s1["room"] == "S1" and s1["seats"] == 8 and s1["free_seats"] == 6 and s1["status"] == "upcoming"


def test_future_completion_does_not_count(cfg):
    sk = dict(cfg["skills"])
    sk["completions"] = [{"person": "p01", "module": "m-literacy-101", "date": "2026-03-04"},
                         {"person": "p01", "module": "m-governance", "date": "2099-01-01"}]
    out = skills.evaluate_skills(sk, cfg["profiles"], cfg["factory"]["regulatory"], TODAY)
    p01 = next(p for p in out["people"] if p["person"] == "p01")
    assert p01["done"] == 1 and p01["overdue"] == ["m-governance"]


def test_art4_measures_in_place_when_everyone_trained(cfg):
    sk = dict(cfg["skills"])
    completions = []
    for p in cfg["profiles"]["people"]:
        for m in sk["requirements"].get(p["role"], []):
            completions.append({"person": p["id"], "module": m, "date": "2026-01-10"})
    sk["completions"] = completions
    out = skills.evaluate_skills(sk, cfg["profiles"], cfg["factory"]["regulatory"], TODAY)
    assert out["art4"]["status"] == "measures_in_place" and out["coverage"] == 1.0
    assert out["art4"]["overdue_people"] == []


def test_overdue_only_after_literacy_milestone(cfg):
    early = skills.evaluate_skills(cfg["skills"], cfg["profiles"], cfg["factory"]["regulatory"], dt.date(2025, 1, 1))
    assert all(p["overdue"] == [] for p in early["people"])


def test_rnd_benches_and_gpu_hours(cfg):
    out = skills.evaluate_rnd(cfg["skills"], cfg["profiles"])
    assert out["gpu_hours_month"] == 50.0  # stopped experiment excluded
    benches = {x["id"]: x["bench"] for x in out["experiments"]}
    assert benches == {"x-01": "X1", "x-02": "X2", "x-03": None}
    assert out["by_stage"]["prototype"] == 1 and out["fine_tuning"] is False
    assert out["experiments"][0]["owner_role"] == "research"


@pytest.mark.parametrize("lic,cls", [("MIT", "permissive"), ("Apache-2.0", "permissive"), ("GPL-3.0-only", "copyleft"),
                                     ("AGPL-3.0", "copyleft"), ("NOASSERTION", "unknown"), (None, "unknown"),
                                     ("proprietary", "proprietary")])
def test_license_class(lic, cls):
    assert catalog.license_class(lic) == cls


def test_catalog_enrichment(cfg):
    reg = risk.evaluate_registry(cfg["usecases"], cfg["factory"], cfg["controls"], cfg["profiles"]["people"], TODAY)
    out = catalog.evaluate_catalog(cfg["catalog"], reg, cfg["profiles"]["people"], ["C3", "C4"])
    models = {m["id"]: m for m in out["models"]}
    assert models["local-secure"]["sensitive_ok"] is True and models["cloud-large"]["sensitive_ok"] is False
    assert "uc-01" in models["cloud-large"]["usecases"]
    agents = {a["id"]: a for a in out["agents"]}
    assert agents["ag-rh"]["registry_tier"] == "high" and agents["ag-rh"]["risk_tier_mismatch"] is True
    assert agents["ag-rh"]["self_service"] == {"allowed": False, "approval": "governance"}
    assert agents["ag-research"]["owner_name"] == "Inès Robert"
    tools = {t["id"]: t for t in out["tools"]}
    assert tools["tl-qdrant"]["used_by"] == ["ag-research", "ag-support", "ag-rh"]
    assert tools["tl-crm"]["self_service"]["approval"] == "owner"
    repos = {r["id"]: r for r in out["repos"]}
    assert repos["gh-demo-rag"]["integrable"] is True
    assert repos["gh-demo-eval"]["needs_relicense"] is True and repos["gh-demo-eval"]["integrable"] is False
    assert out["counts"]["repos_to_relicense"] == 1
