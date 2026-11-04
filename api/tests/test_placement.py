import pytest

from app.engines import placement

POLICY = {"order": ["cloud", "vps", "local"], "sensitive_classes": ["C3", "C4"], "vps_max_params_b": 8}


def targets(cloud="up", vps="up", local="up"):
    return [{"id": "cloud", "status": cloud}, {"id": "vps", "status": vps}, {"id": "local", "status": local}]


def wl(sensitivity="C1", **kw):
    return {"id": "wl", "usecase": "uc", "sensitivity": sensitivity, "model_alias": "chat-default", **kw}


@pytest.mark.parametrize("cls", ["C3", "C4"])
def test_sensitive_goes_local_only(cls):
    d = placement.decide(wl(cls), targets(), POLICY)
    assert d["target"] == "local" and d["fallback"] == [] and d["status"] == "placed"
    assert d["reasons"][0] == f"{cls} => local only"
    assert d["model_alias"] == "chat-sensitive"
    fr = placement.decide(wl(cls), targets(), POLICY, "fr")
    assert fr["reasons"][0] == f"{cls} => local uniquement"
    assert {k: v for k, v in fr.items() if k != "reasons"} == {k: v for k, v in d.items() if k != "reasons"}


@pytest.mark.parametrize("cls", ["C3", "C4"])
def test_sensitive_is_queued_when_local_down_never_cloud_or_vps(cls):
    d = placement.decide(wl(cls), targets(local="down"), POLICY)
    assert d["status"] == "queued" and d["queued"] is True
    assert d["target"] not in ("cloud", "vps")
    assert d["fallback"] == []


def test_sensitive_on_degraded_local_stays_local():
    d = placement.decide(wl("C3"), targets(local="degraded"), POLICY)
    assert d["target"] == "local"


def test_non_sensitive_order_cloud_vps_local():
    d = placement.decide(wl("C1", task_type="classification"), targets(), POLICY)
    assert d["target"] == "cloud" and d["fallback"] == ["vps", "local"]
    d = placement.decide(wl("C2", task_type="classification"), targets(cloud="down"), POLICY)
    assert d["target"] == "vps" and d["fallback"] == ["local"]
    assert "cloud unavailable" in d["reasons"]
    assert "cloud indisponible" in placement.decide(wl("C2", task_type="classification"), targets(cloud="down"),
                                                    POLICY, "fr")["reasons"]
    d = placement.decide(wl("C2", task_type="classification"), targets(cloud="down", vps="down"), POLICY)
    assert d["target"] == "local"


def test_vps_respects_max_params():
    d = placement.decide(wl("C1", params_b=70), targets(cloud="down"), POLICY)
    assert d["target"] == "local"
    assert any("VPS max 8 B" in r for r in d["reasons"])
    fr = placement.decide(wl("C1", params_b=70), targets(cloud="down"), POLICY, "fr")
    assert any("8 B max VPS" in r for r in fr["reasons"])
    d = placement.decide(wl("C1", params_b=8), targets(cloud="down"), POLICY)
    assert d["target"] == "vps"
    d = placement.decide(wl("C1", task_type="agent"), targets(cloud="down"), POLICY)  # agent needs >= 30 B
    assert d["target"] == "local"
    d = placement.decide(wl("C1"), targets(cloud="down"), POLICY)  # unknown size: VPS excluded
    assert d["target"] == "local"


def test_degraded_target_is_last_resort():
    d = placement.decide(wl("C1", params_b=7), targets(cloud="degraded"), POLICY)
    assert d["target"] == "vps" and d["fallback"] == ["local", "cloud"]


def test_everything_down_is_queued():
    d = placement.decide(wl("C1"), targets("down", "down", "down"), POLICY)
    assert d["status"] == "queued"


def test_custom_order_is_respected():
    policy = dict(POLICY, order=["local", "cloud", "vps"])
    assert placement.decide(wl("C1"), targets(), policy)["target"] == "local"


def test_evaluate_placement_from_registry():
    registry = [
        {"id": "a", "stage": "run", "sensitivity": "C2", "task_type": "rag_qa", "model": {"alias": "chat-default"}},
        {"id": "b", "stage": "mvp", "sensitivity": "C4", "task_type": "summarization", "model": {"alias": "chat-sensitive"}},
        {"id": "c", "stage": "retired", "sensitivity": "C1", "task_type": "rag_qa", "model": {"alias": "chat-small"}},
        {"id": "d", "stage": "cadrage", "sensitivity": "C1", "task_type": "rag_qa", "model": {"alias": "chat-small"}},
    ]
    models = [{"alias": "chat-sensitive", "params_b": 14}, {"alias": "chat-default", "params_b": None}]
    out = placement.evaluate_placement(registry, targets(local="down"), models, POLICY)
    assert [w["usecase"] for w in out["workloads"]] == ["a", "b"]
    assert [w["id"] for w in out["workloads"]] == ["wl-01", "wl-02"]
    assert out["workloads"][0]["target"] == "cloud"
    assert out["workloads"][1]["status"] == "queued"
    assert out["counts"]["queued"] == 1
