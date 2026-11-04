"""Smoke test against the repository configuration (config/), skipped when it is absent."""

import json
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from app.config_loader import load_config
from app.engines.dataloop import ALLOWED_ACTIONS
from app.main import create_app
from app.settings import Settings
from tests.conftest import NOW, TODAY

REAL_CONFIG = Path(__file__).resolve().parents[2] / "config"
pytestmark = pytest.mark.skipif(not (REAL_CONFIG / "factory.yaml").is_file(),
                                reason="config/factory.yaml absent (written by another workstream)")


@pytest.fixture
def real_client(tmp_path):
    settings = Settings(config_dir=str(REAL_CONFIG), demo_mode="always", demo_seed=42, demo_date=TODAY.isoformat(),
                        state_dir=str(tmp_path / "state"), cache_ttl_s=0)
    return TestClient(create_app(settings, clock=lambda: NOW))


def test_real_config_loads_without_warnings():
    cfg = load_config(REAL_CONFIG)
    assert cfg["usecases"], "no use case in config/usecases"
    assert cfg["factory"]["factory"]["fine_tuning"] is False
    assert not cfg["warnings"], cfg["warnings"]


def test_real_config_snapshot(real_client):
    r = real_client.get("/api/v1/snapshot")
    assert r.status_code == 200
    s = r.json()
    assert s["meta"]["mode"] == "demo" and not s["meta"]["warnings"], s["meta"]["warnings"]
    ids = {u["id"] for u in s["registry"]}
    people = {p["id"] for p in s["profiles"]["people"]}
    for item in s["registry"]:
        assert item["owner"] in people, item["id"]
        assert set(item["squad"]) <= people, item["id"]
    for row in s["costs"]["by_usecase"]:
        assert row["usecase"] in ids
    tiers = {i["risk"]["tier"] for i in s["registry"]}
    assert {"high", "limited", "minimal"} <= tiers
    assert any(i["blocked_by"] == "risk_reassessment" for i in s["pipeline"]["items"])
    assert any(rb["auto"] for rb in s["pipeline"]["rollbacks"])
    assert {e["action"] for e in s["dataloop"]["events"]} == set(ALLOWED_ACTIONS)
    for w in s["placement"]["workloads"]:
        if w["sensitivity"] in ("C3", "C4"):
            assert w["target"] == "local"
    print("\nrules:", sorted({a["rule"] for a in s["costs"]["alerts"]}))
    print("size:", len(json.dumps(s)), "bytes")


def test_real_config_deterministic(real_client):
    a = real_client.get("/api/v1/snapshot").json()
    b = real_client.get("/api/v1/snapshot").json()
    assert json.dumps(a, sort_keys=True) == json.dumps(b, sort_keys=True)


def test_real_config_both_languages(real_client):
    """CONTRACT section 9 on the repository configuration: fully resolved, no French in English."""
    from tests.test_i18n import french_leaks, mappings

    en = real_client.get("/api/v1/snapshot", params={"lang": "en"}).json()
    fr = real_client.get("/api/v1/snapshot", params={"lang": "fr"}).json()
    assert en["meta"]["lang"] == "en" and fr["meta"]["lang"] == "fr"
    assert not list(mappings(en)) and not list(mappings(fr))
    leaks = french_leaks(en)
    assert not leaks, leaks[:10]
    assert french_leaks(fr), "the French snapshot holds no French text"
    assert [u["id"] for u in en["registry"]] == [u["id"] for u in fr["registry"]]
    assert en["costs"]["totals"] == fr["costs"]["totals"]
