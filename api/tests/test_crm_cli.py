import json
from datetime import datetime, timezone
from pathlib import Path

import pytest

from ai_factory_workbench import crm_sync
from ai_factory_workbench.crm import CRMError, CRMSyncResult


ROOT = Path(__file__).resolve().parents[2]


def _args(path, tmp_path, *extra):
    return ["--database", str(tmp_path / "workbench.sqlite3"), "--workspace", "test-workspace",
            "--config", str(path), "--since", "2026-09-01T00:00:00Z", "--until", "2026-09-02T00:00:00Z", *extra]


def _example_config(tmp_path):
    path = tmp_path / "connectors.json"
    path.write_bytes((ROOT / "examples" / "crm-sources.example.json").read_bytes())
    return path


def test_crm_cli_routes_all_providers_and_reports_each_result(tmp_path, monkeypatch, capsys):
    path = _example_config(tmp_path)
    closed = []
    requested = []
    for name in ("SALESFORCE", "HUBSPOT", "TWENTY"):
        monkeypatch.setenv(f"AI_FACTORY_{name}_TOKEN", "fixture-credential")

    class Provider:
        def __init__(self, source):
            self.source = source

        def close(self):
            closed.append(self.source)

    def factory(kind):
        def open_provider(*args, instance, **kwargs):
            assert args[-1] == "fixture-credential"
            return Provider(f"{kind}/{instance}")
        return open_provider

    monkeypatch.setattr(crm_sync, "SalesforceCRM", factory("salesforce"))
    monkeypatch.setattr(crm_sync, "HubSpotCRM", factory("hubspot"))
    monkeypatch.setattr(crm_sync, "TwentyCRM", factory("twenty"))

    def sync(engine, provider, actor, *, since, until, profile):
        assert engine.mode == "protected_demo"
        assert engine.workspace == "test-workspace"
        assert actor.roles == frozenset({"editor"})
        assert since == datetime(2026, 9, 1, tzinfo=timezone.utc)
        assert profile["id"] == "governance"
        requested.append(provider.source)
        if provider.source.startswith("hubspot/"):
            raise CRMError("crm_rate_limited", 429)
        return CRMSyncResult(source=provider.source, until=until, seen=1, applied=1, replayed=0, ignored=0,
                             stale=0, withdrawn=0, blocked=0, conflicts=0, reconciliation_required=0)

    monkeypatch.setattr(crm_sync, "sync_opportunities", sync)
    assert crm_sync.main(_args(path, tmp_path)) == 1
    output = capsys.readouterr()
    result = json.loads(output.out)
    assert requested == ["salesforce/sales-sandbox", "hubspot/eu-portal", "twenty/operations"]
    assert closed == requested
    assert result[0]["applied"] == 1
    assert result[0]["blocked"] == result[0]["conflicts"] == 0
    assert result[1] == {"source": "hubspot/eu-portal", "error": "crm_rate_limited"}
    assert result[2]["until"] == "2026-09-02T00:00:00+00:00"
    assert "fixture-credential" not in output.out + output.err


def test_crm_cli_requires_credentials_only_for_selected_sources(tmp_path, monkeypatch, capsys):
    path = _example_config(tmp_path)
    monkeypatch.delenv("AI_FACTORY_TWENTY_TOKEN", raising=False)
    assert crm_sync.main(_args(path, tmp_path, "--provider", "twenty/operations")) == 1
    output = capsys.readouterr()
    assert json.loads(output.out) == [{"source": "twenty/operations", "error": "missing_crm_credentials"}]
    assert output.err == ""


@pytest.mark.parametrize("configured", [False, True])
def test_crm_cli_continues_after_provider_setup_failure(tmp_path, monkeypatch, capsys, configured):
    path = _example_config(tmp_path)
    monkeypatch.setenv("AI_FACTORY_SALESFORCE_TOKEN", "fixture-credential")
    monkeypatch.setenv("AI_FACTORY_TWENTY_TOKEN", "fixture-credential")
    monkeypatch.delenv("AI_FACTORY_HUBSPOT_TOKEN", raising=False)
    if configured:
        monkeypatch.setenv("AI_FACTORY_HUBSPOT_TOKEN", "fixture-credential")
    closed = []

    class Provider:
        def __init__(self, source):
            self.source = source

        def close(self):
            closed.append(self.source)

    def broken_provider(token, **_kwargs):
        raise CRMError(f"Invalid credential: {token}")

    monkeypatch.setattr(crm_sync, "SalesforceCRM", lambda *_args, instance, **_kwargs: Provider(f"salesforce/{instance}"))
    monkeypatch.setattr(crm_sync, "TwentyCRM", lambda *_args, instance, **_kwargs: Provider(f"twenty/{instance}"))
    monkeypatch.setattr(crm_sync, "HubSpotCRM", broken_provider)
    monkeypatch.setattr(crm_sync, "sync_opportunities", lambda _engine, provider, _actor, **kwargs:
                        CRMSyncResult(provider.source, kwargs["until"], seen=1, applied=1))

    assert crm_sync.main(_args(path, tmp_path)) == 1
    output = capsys.readouterr()
    first, error, last = json.loads(output.out)
    assert closed == ["salesforce/sales-sandbox", "twenty/operations"]
    assert [first["applied"], last["applied"]] == [1, 1]
    assert error == {"source": "hubspot/eu-portal", "error": "invalid_crm_sync_configuration" if configured
                     else "missing_crm_credentials"}
    assert "fixture-credential" not in output.out + output.err
    assert output.err == ""


@pytest.mark.parametrize("change", ["token", "duplicate", "foreign_mapping"])
def test_crm_cli_invalid_configuration_keeps_input_out_of_errors(tmp_path, capsys, change):
    path = _example_config(tmp_path)
    config = json.loads(path.read_text(encoding="utf-8"))
    if change == "token":
        config["sources"][0]["token"] = "private-value"
    elif change == "duplicate":
        config["sources"].append(dict(config["sources"][0]))
    else:
        config["customers"][0]["reference"] = "twenty/foreign/123"
    path.write_text(json.dumps(config), encoding="utf-8")
    assert crm_sync.main(_args(path, tmp_path)) == 1
    output = capsys.readouterr()
    assert json.loads(output.err) == {"error": "invalid_crm_sync_configuration"}
    assert output.out == ""
