# SPDX-License-Identifier: MIT
"""Load compose/litellm/factory_callbacks.py without LiteLLM installed.

The callback only needs ``litellm.integrations.custom_logger.CustomLogger`` as a base class;
a minimal stand-in is registered when LiteLLM is absent. The metrics HTTP server binds an
ephemeral port so that tests never collide with a running gateway.
"""

from __future__ import annotations

import importlib.util
import os
import sys
import types
from pathlib import Path

import pytest

CALLBACK = Path(__file__).resolve().parent.parent / "factory_callbacks.py"


def _stub_litellm() -> None:
    try:
        import litellm.integrations.custom_logger  # noqa: F401
        return
    except ImportError:
        pass

    class CustomLogger:  # minimal stand-in
        def __init__(self, *args, **kwargs) -> None:
            pass

    pkg = types.ModuleType("litellm")
    integrations = types.ModuleType("litellm.integrations")
    custom_logger = types.ModuleType("litellm.integrations.custom_logger")
    custom_logger.CustomLogger = CustomLogger
    pkg.integrations = integrations
    integrations.custom_logger = custom_logger
    sys.modules.update({"litellm": pkg, "litellm.integrations": integrations,
                        "litellm.integrations.custom_logger": custom_logger})


@pytest.fixture(scope="session")
def fc():
    """The callback module, loaded the way LiteLLM loads it (spec_from_file_location)."""
    os.environ["FACTORY_METRICS_PORT"] = "0"
    os.environ.pop("FACTORY_GUARDRAIL_MODE", None)
    _stub_litellm()
    spec = importlib.util.spec_from_file_location("factory_callbacks", CALLBACK)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module


@pytest.fixture()
def guard(fc, monkeypatch):
    """The callback instance with the default guardrail settings (mode flag, redaction on)."""
    monkeypatch.setattr(fc, "GUARDRAIL_MODE", "flag")
    monkeypatch.setattr(fc, "PII_REDACTION", True)
    return fc.factory_metrics


def metric(fc, type_: str, action: str, usecase: str) -> float:
    value = fc.factory_metrics.rt.registry.get_sample_value(
        "factory_llm_guardrail_events_total", {"type": type_, "action": action, "usecase": usecase})
    return value or 0.0
