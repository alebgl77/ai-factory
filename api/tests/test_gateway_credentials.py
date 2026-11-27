"""The application receives scoped gateway credentials and an administrative fingerprint."""

import hashlib
import datetime as dt
import json

import httpx
import pytest

from app.connectors import Connectors
from app.connectors.base import SourceError
from app.settings import Settings


ADMIN = "sk-synthetic-administrative-key"
READ = "sk-synthetic-read-only-key"
INFERENCE = "sk-synthetic-local-inference-key"
FINGERPRINT = hashlib.sha256(ADMIN.encode()).hexdigest()


def test_api_environment_rejects_administrative_key(monkeypatch):
    monkeypatch.setenv("LITELLM_MASTER_KEY", ADMIN)
    with pytest.raises(ValueError) as error:
        Settings.from_env()
    assert ADMIN not in str(error.value)


@pytest.mark.parametrize("settings", [
    {"litellm_telemetry_key": READ},
    {"litellm_inference_key": INFERENCE},
    {"portfolio_runner_file": "/run/secrets/bindings"},
    {"litellm_telemetry_key": ADMIN, "litellm_admin_key_sha256": FINGERPRINT},
    {"litellm_inference_key": ADMIN, "litellm_admin_key_sha256": FINGERPRINT},
    {"litellm_telemetry_key": READ, "litellm_inference_key": READ,
     "litellm_admin_key_sha256": FINGERPRINT},
])
def test_invalid_or_shared_gateway_credentials_fail_closed(settings):
    with pytest.raises(ValueError) as error:
        Settings(**settings)
    assert all(value not in str(error.value) for value in (ADMIN, READ, INFERENCE))


def test_telemetry_uses_only_its_dedicated_credential():
    observed = []

    def gateway(request):
        observed.append((request.method, request.url.path, request.headers.get("authorization")))
        return httpx.Response(200, json={"data": []})

    settings = Settings(litellm_url="http://litellm:4000", litellm_telemetry_key=READ,
                        litellm_inference_key=INFERENCE, litellm_admin_key_sha256=FINGERPRINT)
    client = Connectors(settings, transport=httpx.MockTransport(gateway)).litellm
    assert client.models() == []
    assert observed == [("GET", "/model/info", f"Bearer {READ}")]
    assert READ not in repr(settings) and INFERENCE not in repr(settings)


def test_missing_telemetry_key_never_falls_back_to_inference():
    settings = Settings(litellm_url="http://litellm:4000", litellm_inference_key=INFERENCE,
                        litellm_admin_key_sha256=FINGERPRINT)
    transport = httpx.MockTransport(lambda _: pytest.fail("Missing credentials must not dispatch"))
    with pytest.raises(SourceError, match="not configured"):
        Connectors(settings, transport=transport).litellm.models()


def test_secret_files_are_explicit_bounded_and_redacted(tmp_path, monkeypatch):
    source = tmp_path / "telemetry-key"
    source.write_text(READ + "\n", encoding="utf-8")
    monkeypatch.setenv("LITELLM_TELEMETRY_KEY_FILE", str(source))
    monkeypatch.setenv("LITELLM_ADMIN_KEY_SHA256", FINGERPRINT)
    assert Settings.from_env().litellm_telemetry_key == READ
    monkeypatch.setenv("LITELLM_TELEMETRY_KEY", READ)
    with pytest.raises(ValueError) as error:
        Settings.from_env()
    assert READ not in str(error.value)
    monkeypatch.delenv("LITELLM_TELEMETRY_KEY")
    source.write_bytes(b"x" * 4097)
    with pytest.raises(ValueError, match="Invalid or unavailable"):
        Settings.from_env()


def _spend_client(payload, headers=None):
    def gateway(request):
        assert request.url.path == "/spend/logs"
        assert request.url.params["summarize"] == "false"
        assert request.url.params["start_date"] == "2026-11-01"
        assert request.url.params["end_date"] == "2026-11-04"
        return httpx.Response(200, json=payload, headers=headers)

    settings = Settings(litellm_url="http://litellm:4000", litellm_telemetry_key=READ,
                        litellm_admin_key_sha256=FINGERPRINT)
    return Connectors(settings, transport=httpx.MockTransport(gateway)).litellm


def _spend_row(**changes):
    return {"startTime": "2026-11-03T23:59:59Z", "spend": 0.25, "prompt_tokens": 13,
            "completion_tokens": 4, "metadata": {"usecase": "uc-02"}, **changes}


def test_raw_spend_preserves_attribution_tokens_and_the_full_last_day():
    client = _spend_client([_spend_row(), _spend_row(startTime="2026-11-04T00:00:00Z")])
    assert client.spend_daily(dt.date(2026, 11, 1), dt.date(2026, 11, 3)) == [{
        "date": "2026-11-03", "usecase": "uc-02", "spend": 0.25,
        "requests": 1, "tokens_in": 13, "tokens_out": 4,
    }]


@pytest.mark.parametrize("encoded", [False, True])
def test_pinned_gateway_persisted_attribution_supports_json_string_or_object(encoded):
    metadata = {"spend_logs_metadata": {"usecase": "uc-02"}, "user_api_key_alias": "synthetic-key"}
    client = _spend_client([_spend_row(metadata=json.dumps(metadata) if encoded else metadata)])
    result = client.spend_daily(dt.date(2026, 11, 1), dt.date(2026, 11, 3))
    assert result == [{"date": "2026-11-03", "usecase": "uc-02", "spend": 0.25,
                      "requests": 1, "tokens_in": 13, "tokens_out": 4}]


@pytest.mark.parametrize(("payload", "headers"), [
    ([_spend_row()], {"x-litellm-spend-logs-truncated": "true"}),
    ([_spend_row()] * 10000, {}),
    ([{"startTime": "2026-11-03", "spend": 1, "users": {}, "models": {}}], {}),
    ([_spend_row(prompt_tokens=True)], {}),
    ([_spend_row(metadata={"requester_metadata": "invalid"})], {}),
    ([_spend_row(spend="NaN")], {}),
    ([_spend_row(metadata='{invalid')], {}),
    ([_spend_row(metadata='["uc-02"]')], {}),
    ([_spend_row(metadata='{"spend_logs_metadata":{"usecase":"uc-01","usecase":"uc-02"}}')], {}),
    ([_spend_row(metadata={"usecase": "uc-01", "spend_logs_metadata": {"usecase": "uc-02"}})], {}),
    ([_spend_row(metadata={"spend_logs_metadata": {"usecase": {"nested": "uc-02"}}})], {}),
])
def test_incomplete_or_unattributable_spend_never_returns_partial_totals(payload, headers):
    with pytest.raises(SourceError):
        _spend_client(payload, headers).spend_daily(dt.date(2026, 11, 1), dt.date(2026, 11, 3))
