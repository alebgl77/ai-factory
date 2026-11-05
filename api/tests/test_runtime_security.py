"""Regression tests for simulator inputs and service-local file permissions."""

import importlib.util
import os
import stat
import sys
from pathlib import Path

import pytest

from app.audit import AuditContext, AuditLog

ROOT = Path(__file__).resolve().parents[2]


def load_script(monkeypatch, name, path):
    spec = importlib.util.spec_from_file_location(name, ROOT / path)
    module = importlib.util.module_from_spec(spec)
    monkeypatch.setitem(sys.modules, name, module)
    spec.loader.exec_module(module)
    return module


def test_mock_llm_counts_raw_emails_with_long_nonmatches(monkeypatch):
    mock_llm = load_script(monkeypatch, "factory_mock_llm", "sim/mock-llm/app.py")
    noise = "a" * 250_000 + "@example" + ".x" * 2_000
    assert mock_llm._count_raw_emails(noise) == 0
    assert mock_llm._count_raw_emails(
        f"{noise} [REDACTED_EMAIL] alice+tag@example.org, b@dev.example.net."
    ) == 2


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_audit_append_creates_private_file_and_repairs_legacy_mode(tmp_path):
    audit = AuditLog(tmp_path)
    context = AuditContext("actor", "admin", "none", "request", "127.0.0.1")
    first = audit.append(context, action="spaces.put", target="workspace")
    assert stat.S_IMODE(audit.path.stat().st_mode) == 0o600
    audit.path.chmod(0o640)
    second = audit.append(context, action="spaces.put", target="workspace")
    assert stat.S_IMODE(audit.path.stat().st_mode) == 0o600
    assert second["prev_hash"] == first["hash"]
    assert audit.verify().ok


def test_grafana_token_write_is_atomic_and_cleans_up_on_failure(monkeypatch, tmp_path):
    bootstrap = load_script(monkeypatch, "factory_grafana_bootstrap", "compose/grafana-bootstrap/bootstrap.py")
    path = tmp_path / "tokens" / "viewer"
    monkeypatch.setattr(bootstrap, "TOKEN_FILE", path)
    bootstrap.write_token("viewer-placeholder")
    assert path.read_text(encoding="utf-8") == "viewer-placeholder"
    if os.name == "posix":
        assert stat.S_IMODE(path.stat().st_mode) == 0o600

    def fail_replace(*args):
        raise OSError("simulated rename failure")

    monkeypatch.setattr(bootstrap.os, "replace", fail_replace)
    with pytest.raises(OSError, match="simulated rename failure"):
        bootstrap.write_token("replacement-placeholder")
    assert path.read_text(encoding="utf-8") == "viewer-placeholder"
    assert list(path.parent.iterdir()) == [path]


@pytest.mark.skipif(os.name != "posix", reason="POSIX file permissions")
def test_grafana_reused_token_becomes_owner_only(monkeypatch, tmp_path):
    bootstrap = load_script(monkeypatch, "factory_grafana_bootstrap", "compose/grafana-bootstrap/bootstrap.py")
    path = tmp_path / "viewer"
    path.write_text("viewer-placeholder\n", encoding="utf-8")
    path.chmod(0o644)
    monkeypatch.setattr(bootstrap, "TOKEN_FILE", path)

    def request(method, endpoint, *, auth, token):
        assert stat.S_IMODE(path.stat().st_mode) == 0o600
        assert token == "viewer-placeholder"
        return 200, []

    monkeypatch.setattr(bootstrap, "request", request)
    assert bootstrap.existing_token_valid()
