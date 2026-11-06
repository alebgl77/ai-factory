"""HTTP integration and operational log redaction for cyber audit."""

from __future__ import annotations

import json
import logging
from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from ai_factory_workbench._hashing import digest
from ai_factory_workbench.cyberlog import iter_verified_events, set_journal_enabled
from app.main import create_app
from app.seclog import reference
from app.settings import Settings

ROOT = "/api/v1/workbench"
PROXY = "10.89.255.2"
REQUEST_ID = "synthetic-id-canary"
PATH_CANARY = "synthetic-path-canary"
QUERY_CANARY = "synthetic-query-canary"
ACTOR_CANARY = "synthetic-actor-canary"
EXCEPTION_CANARY = "synthetic-exception-canary"


def _protected(tmp_path: Path, cyber_audit: str = "off") -> TestClient:
    app = create_app(Settings(state_dir=str(tmp_path), workbench_mode="protected_demo", auth_mode="proxy",
                              trusted_proxies=(f"{PROXY}/32",), role_map=("submit:editor", "view:viewer"),
                              workbench_cyber_audit=cyber_audit))
    return TestClient(app, client=(PROXY, 12345))


def _submit(client: TestClient, event_id: str) -> None:
    event = {"specversion": "1.0", "source": "crm-sim", "id": event_id, "type": "crm.opportunity.won",
             "subject": f"opp-{event_id}", "data": {"title": "Synthetic renewal", "customer_name": "Atelier Acme",
                                                   "customer_ref": "CRM-ACME", "amount_minor": 20000,
                                                   "currency": "EUR", "record_version": 1}}
    response = client.post(f"{ROOT}/events", json=event, headers={"X-Forwarded-User": ACTOR_CANARY,
                                                                "X-Forwarded-Groups": "submit",
                                                                "X-Request-ID": REQUEST_ID})
    assert response.status_code == 200
    assert response.headers["X-Request-ID"] == REQUEST_ID


@pytest.mark.parametrize("invalid", ["", "true", "ON", "typo"])
def test_strict_cyber_audit_opt_in(monkeypatch, invalid):
    name = "FACTORY_WORKBENCH_CYBER_AUDIT"
    monkeypatch.delenv(name, raising=False)
    assert Settings.from_env().workbench_cyber_audit == "off"
    monkeypatch.setenv(name, "on")
    assert Settings.from_env().workbench_cyber_audit == "on"
    monkeypatch.setenv(name, invalid)
    with pytest.raises(ValueError, match=name):
        Settings.from_env()


def test_protected_journal_opt_in_correlation_and_persisted_policy(tmp_path):
    disabled = _protected(tmp_path / "disabled")
    _submit(disabled, "without-journal")
    assert list(iter_verified_events(disabled.app.state.workbench.store.path)) == []

    selected = _protected(tmp_path / "selected", "on")
    assert selected.get(f"{ROOT}/cases").status_code == 401
    _submit(selected, "with-journal")
    engine = selected.app.state.workbench
    path = engine.store.path
    events = list(iter_verified_events(path))
    assert [row["event"] for row in events] == ["journal.enabled", "case.proposed"]
    assert events[-1]["request_id"] == digest(["workbench-cyber-ref-v1", engine.workspace, "request", REQUEST_ID])
    assert REQUEST_ID not in json.dumps(events) and ACTOR_CANARY not in json.dumps(events)

    _submit(_protected(tmp_path / "selected"), "after-restart")
    assert len(list(iter_verified_events(path))) == 3
    assert set_journal_enabled(path, False)
    _submit(_protected(tmp_path / "selected", "on"), "after-disable")
    assert [row["event"] for row in iter_verified_events(path)] == [
        "journal.enabled", "case.proposed", "case.proposed", "journal.disabled",
    ]


def test_denial_log_uses_route_templates_and_pseudonymous_ids(tmp_path, capsys):
    client = _protected(tmp_path)
    capsys.readouterr()
    response = client.request("SYNTHETIC-METHOD-CANARY", f"/api/v1/{PATH_CANARY}?private={QUERY_CANARY}",
                              headers={"X-Request-ID": REQUEST_ID})
    assert response.status_code == 401
    response = client.get(f"/api/v1/audit?private={QUERY_CANARY}", headers={"X-Forwarded-User": ACTOR_CANARY,
                                                                          "X-Forwarded-Groups": "view",
                                                                          "X-Request-ID": REQUEST_ID})
    assert response.status_code == 403
    logs = capsys.readouterr().err
    for value in (PATH_CANARY, QUERY_CANARY, REQUEST_ID, ACTOR_CANARY, "SYNTHETIC-METHOD-CANARY"):
        assert value not in logs
    first, second = (json.loads(line) for line in logs.splitlines() if line.startswith("{"))
    assert first["event"] == second["event"] == "request.denied"
    assert first["route"] == "unresolved" and first["method"] == "UNKNOWN"
    assert second["route"] == "/api/v1/audit" and second["actor_ref"] == reference("actor", ACTOR_CANARY)
    assert first["request_ref"] == second["request_ref"] == reference("request", REQUEST_ID)


def test_unhandled_error_and_duplicate_uvicorn_traceback_are_redacted(make_client, capsys, caplog):
    app = make_client().app

    @app.get("/api/v1/test-cyber-failure")
    def fail():
        raise RuntimeError(EXCEPTION_CANARY)

    capsys.readouterr()
    response = TestClient(app, raise_server_exceptions=False).get(
        f"/api/v1/test-cyber-failure?private={QUERY_CANARY}", headers={"X-Request-ID": REQUEST_ID}
    )
    assert response.status_code == 500 and EXCEPTION_CANARY not in response.text
    log = capsys.readouterr().err
    assert all(value not in log for value in (EXCEPTION_CANARY, QUERY_CANARY, REQUEST_ID, "Traceback"))
    failed = json.loads(log)
    assert failed["event"] == "request.failed" and failed["route"] == "/api/v1/test-cyber-failure"
    assert failed["request_ref"] == reference("request", REQUEST_ID)

    logger = logging.getLogger("uvicorn.error")
    with caplog.at_level(logging.ERROR, logger="uvicorn.error"):
        try:
            raise RuntimeError(EXCEPTION_CANARY)
        except RuntimeError:
            logger.exception("Exception in ASGI application\n")
        logger.error("startup diagnostic")
    assert EXCEPTION_CANARY not in caplog.text and "Traceback" not in caplog.text
    assert caplog.messages[-2:] == ["Exception in ASGI application", "startup diagnostic"]


def test_container_disables_raw_uvicorn_access_logs():
    dockerfile = Path(__file__).resolve().parents[1] / "Dockerfile"
    command = next(line for line in dockerfile.read_text(encoding="utf-8").splitlines() if line.startswith("CMD ["))
    assert "--no-access-log" in json.loads(command.removeprefix("CMD "))
