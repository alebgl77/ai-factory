"""Shared fixtures: a self-contained fictional configuration and an app factory."""

from __future__ import annotations

import datetime as dt
from pathlib import Path
from typing import Any

import httpx
import pytest
from fastapi.testclient import TestClient

from app.config_loader import load_config
from app.main import create_app
from app.settings import Settings

FIXTURE_DIR = Path(__file__).parent / "fixtures" / "config"
TODAY = dt.date(2026, 11, 3)
NOW = dt.datetime(2026, 11, 3, 9, 0, tzinfo=dt.timezone.utc)


def review_body(client: TestClient, uc_id: str, *, decision: str = "approved", reviewer: str = "p07",
                headers: dict[str, str] | None = None) -> dict[str, str]:
    proposal = client.get(f"/api/v1/registry/{uc_id}", headers=headers)
    assert proposal.status_code == 200, proposal.text
    return {"decision": decision, "reviewer": reviewer, "expected_review_revision": proposal.json()["review_revision"]}


@pytest.fixture(scope="session")
def cfg() -> dict[str, Any]:
    return load_config(FIXTURE_DIR)


@pytest.fixture
def make_client(tmp_path):
    def factory(transport: httpx.BaseTransport | None = None, **overrides: Any) -> TestClient:
        params: dict[str, Any] = dict(config_dir=str(FIXTURE_DIR), demo_mode="always", demo_seed=42,
                                      demo_date=TODAY.isoformat(), state_dir=str(tmp_path / "state"),
                                      cache_ttl_s=0)
        params.update(overrides)
        app = create_app(Settings(**params), transport=transport, clock=lambda: NOW)
        return TestClient(app)
    return factory


@pytest.fixture
def client(make_client) -> TestClient:
    return make_client()
