"""Collection overlap, cache lifetime, and snapshot provenance at the service boundary."""

import datetime as dt
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event, Lock
from types import SimpleNamespace

import pytest

from app import live, service
from app.settings import Settings


def test_independent_live_collectors_overlap():
    started = Barrier(2, timeout=2)

    def sample(value):
        started.wait()
        return value

    conns = SimpleNamespace(
        grafana=SimpleNamespace(dashboards=lambda: sample([{"uid": "test"}])),
        qdrant=SimpleNamespace(collections=lambda: sample([])),
    )
    now = dt.datetime(2026, 11, 3, tzinfo=dt.timezone.utc)
    assert live.collect_live(conns, {"grafana": True, "qdrant": True}, today=now.date(), now=now,
                             days=30, tz=dt.timezone.utc) == {
        "grafana_dashboards": [{"uid": "test"}], "qdrant_collections": [],
    }


def test_concurrent_locales_share_collection_by_date_range(make_client, monkeypatch):
    svc = make_client(demo_mode="auto", prometheus_url="http://unused", cache_ttl_s=60).app.state.service
    monkeypatch.setattr(svc, "sources", lambda: {"prometheus": True})
    callers = Barrier(4, timeout=2)
    entered = Event()
    release = Event()
    lock = Lock()
    calls = []

    def collect(*args, **kwargs):
        with lock:
            calls.append(kwargs["days"])
            if 7 in calls and 30 in calls:
                entered.set()
        assert release.wait(2)
        return {"qdrant_collections": []}

    monkeypatch.setattr(service, "collect_live", collect)

    def snapshot(lang, mode=None, days=30):
        callers.wait()
        return svc.snapshot(lang=lang, cost_mode=mode, days=days)

    with ThreadPoolExecutor(max_workers=4) as pool:
        en = pool.submit(snapshot, "en")
        fr = pool.submit(snapshot, "fr", "chargeback")
        en_same = pool.submit(snapshot, "en")
        week = pool.submit(snapshot, "en", None, 7)
        try:
            assert entered.wait(2)
        finally:
            release.set()
        en, fr, en_same, week = (job.result(timeout=3) for job in (en, fr, en_same, week))

    assert sorted(calls) == [7, 30]
    assert en is en_same
    assert week is not en
    assert en["meta"]["lang"] == "en" and fr["meta"]["lang"] == "fr"
    assert en["meta"]["telemetry_collected_at"] == fr["meta"]["telemetry_collected_at"]


def test_raw_expiry_force_refresh_and_local_invalidation(make_client, monkeypatch):
    svc = make_client(demo_mode="auto", qdrant_url="http://unused", cache_ttl_s=10).app.state.service
    monkeypatch.setattr(svc, "sources", lambda: {"qdrant": True})
    seconds = 0.0
    origin = dt.datetime(2026, 11, 3, 9, tzinfo=dt.timezone.utc)
    monkeypatch.setattr(svc, "_clock", lambda: origin + dt.timedelta(seconds=seconds))
    monkeypatch.setattr(service, "time", SimpleNamespace(monotonic=lambda: seconds))
    calls = []

    def collect(*args, **kwargs):
        calls.append(kwargs["days"])
        return {"qdrant_collections": []} if seconds < 10 else {}

    monkeypatch.setattr(service, "collect_live", collect)
    first = svc.snapshot()
    seconds = 5
    forced = svc.snapshot(use_cache=False)
    assert forced["meta"]["telemetry_collected_at"] != first["meta"]["telemetry_collected_at"]
    assert svc.snapshot() is first
    svc.invalidate()
    regenerated = svc.snapshot()
    assert regenerated is not first
    assert regenerated["meta"]["telemetry_collected_at"] == first["meta"]["telemetry_collected_at"]
    assert regenerated["meta"]["generated_at"] != first["meta"]["generated_at"]
    assert calls == [30, 30]
    assert svc.snapshot(days=7)["meta"]["telemetry_collected_at"] == forced["meta"]["telemetry_collected_at"]
    seconds = 11
    assert "telemetry_collected_at" not in svc.snapshot()["meta"]
    assert calls == [30, 30, 7, 30]


def test_invalidation_during_build_cannot_restore_old_snapshot(make_client, monkeypatch):
    svc = make_client(cache_ttl_s=60).app.state.service
    original = svc._build_snapshot
    entered = Event()
    release = Event()
    builds = []

    def build(*args, **kwargs):
        snap = original(*args, **kwargs)
        builds.append(snap)
        if len(builds) == 1:
            entered.set()
            assert release.wait(2)
        return snap

    monkeypatch.setattr(svc, "_build_snapshot", build)
    with ThreadPoolExecutor(max_workers=1) as pool:
        old = pool.submit(svc.snapshot)
        try:
            assert entered.wait(2)
            svc.invalidate()
            fresh = svc.snapshot()
        finally:
            release.set()
        assert old.result(timeout=3) is not fresh
    assert svc.snapshot() is fresh
    assert len(builds) == 2
    assert "telemetry_collected_at" not in fresh["meta"]


def test_failed_snapshot_build_can_be_retried(make_client, monkeypatch):
    svc = make_client(cache_ttl_s=60).app.state.service
    original = svc._build_snapshot

    def fail(*args, **kwargs):
        raise RuntimeError("synthetic failure")

    monkeypatch.setattr(svc, "_build_snapshot", fail)
    with pytest.raises(RuntimeError, match="synthetic failure"):
        svc.snapshot()
    monkeypatch.setattr(svc, "_build_snapshot", original)
    assert svc.snapshot()["meta"]["mode"] == "demo"


@pytest.mark.parametrize("value", ["bad", "NaN", "inf", "0", "-0.5"])
def test_invalid_cost_fx_is_rejected(monkeypatch, value):
    monkeypatch.setenv("FACTORY_COST_FX", value)
    with pytest.raises(ValueError, match="FACTORY_COST_FX"):
        Settings.from_env()


def test_settings_reads_configured_cost_fx(monkeypatch):
    monkeypatch.setenv("FACTORY_COST_FX", "0.8")
    assert Settings.from_env().cost_fx == 0.8
