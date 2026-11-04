# SPDX-License-Identifier: MIT
"""LiteLLM custom callback of the Virtual AI Factory (MIT, no enterprise feature).

Loaded by the LiteLLM proxy through ``litellm_settings.callbacks`` in config.yaml. It:

1. exports the factory gateway metrics of the interface contract (section 5) on its own
   Prometheus endpoint (``FACTORY_METRICS_PORT``, default 9464), so the factory never
   depends on LiteLLM's enterprise Prometheus integration:

   - factory_llm_requests_total{model,deployment,usecase,status}
   - factory_llm_tokens_total{model,usecase,direction}
   - factory_llm_cost_eur_total{model,usecase}
   - factory_llm_ttft_seconds_bucket{model,usecase}           (histogram)
   - factory_llm_latency_seconds_bucket{model,usecase,deployment} (histogram)

   plus two additive per-agent counters used by the Agent Ops room:
   factory_llm_agent_requests_total{agent,usecase,status} and
   factory_llm_agent_tokens_total{agent,usecase,direction}.

   ``model`` is the alias the client asked for (chat-default, chat-small, chat-sensitive,
   embed-default) even when a fallback served the request; ``deployment`` is where it ran
   (cloud, vps, local), read from ``model_info.factory_deployment``.

2. enforces the placement rule of the factory at the gateway (pre-call hook): a request
   whose ``metadata.sensitivity`` is a sensitive data class (C3, C4 by default) is
   rerouted to ``chat-sensitive`` (local GPU only), and the prompt and completion of every
   sensitive request are masked in Langfuse (``mask_input`` / ``mask_output``).

Cost attribution reads ``metadata.usecase`` and ``metadata.agent`` from the request. Label
values are validated and capped to keep the Prometheus cardinality bounded.
"""

from __future__ import annotations

import os
import re
import sys
import threading
import types
from datetime import datetime
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from prometheus_client import CollectorRegistry, Counter, Histogram, start_http_server

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_MAX_VALUES_PER_LABEL = int(os.environ.get("FACTORY_METRICS_MAX_LABEL_VALUES", "200"))
_SENSITIVE = {c.strip().upper() for c in
              os.environ.get("FACTORY_SENSITIVE_CLASSES", "C3,C4").split(",") if c.strip()}
_SENSITIVE_ALIAS = os.environ.get("FACTORY_SENSITIVE_ALIAS", "chat-sensitive")
# Aliases allowed for sensitive data: the local-only chat alias and the local embeddings.
_SENSITIVE_OK = {_SENSITIVE_ALIAS, *[a.strip() for a in os.environ.get(
    "FACTORY_SENSITIVE_OK_ALIASES", "embed-default").split(",") if a.strip()]}
_ENFORCE = os.environ.get("FACTORY_ENFORCE_SENSITIVE_ROUTING", "true").lower() in ("1", "true", "yes")
_COST_FX = float(os.environ.get("FACTORY_COST_FX", "1.0") or 1.0)
_PORT = int(os.environ.get("FACTORY_METRICS_PORT", "9464"))
_DEPLOYMENTS = {"cloud", "vps", "local"}

TTFT_BUCKETS = (0.05, 0.1, 0.2, 0.3, 0.5, 0.75, 1.0, 1.5, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0)
LATENCY_BUCKETS = (0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 20.0, 30.0, 60.0, 120.0)


def _runtime() -> types.ModuleType:
    """Process-wide metrics state.

    LiteLLM loads this file with ``spec_from_file_location`` and may do so more than once;
    the registry, the metrics and the HTTP server must exist only once per process.
    """
    name = "_factory_callbacks_runtime"
    rt = sys.modules.get(name)
    if rt is not None:
        return rt
    rt = types.ModuleType(name)
    reg = CollectorRegistry(auto_describe=True)
    rt.registry = reg
    rt.requests = Counter("factory_llm_requests", "LLM gateway requests by alias, deployment, use case and status.",
                          ["model", "deployment", "usecase", "status"], registry=reg)
    rt.tokens = Counter("factory_llm_tokens", "LLM tokens by alias, use case and direction (in|out).",
                        ["model", "usecase", "direction"], registry=reg)
    rt.cost = Counter("factory_llm_cost_eur", "LLM cost in EUR by alias and use case.",
                      ["model", "usecase"], registry=reg)
    rt.ttft = Histogram("factory_llm_ttft_seconds", "Time to first token in seconds.",
                        ["model", "usecase"], buckets=TTFT_BUCKETS, registry=reg)
    rt.latency = Histogram("factory_llm_latency_seconds", "End-to-end LLM call latency in seconds.",
                           ["model", "usecase", "deployment"], buckets=LATENCY_BUCKETS, registry=reg)
    rt.agent_requests = Counter("factory_llm_agent_requests", "LLM gateway requests by agent.",
                                ["agent", "usecase", "status"], registry=reg)
    rt.agent_tokens = Counter("factory_llm_agent_tokens", "LLM tokens by agent and direction.",
                              ["agent", "usecase", "direction"], registry=reg)
    rt.reroutes = Counter("factory_llm_sensitive_reroutes", "Requests rerouted to the local-only alias.",
                          ["from_model", "usecase"], registry=reg)
    rt.seen = {}
    rt.lock = threading.Lock()
    try:
        start_http_server(_PORT, addr="0.0.0.0", registry=reg)
        rt.server_error = None
    except OSError as exc:  # port taken (second worker): metrics stay in-process only
        rt.server_error = str(exc)
    sys.modules[name] = rt
    return rt


def _label(rt: types.ModuleType, kind: str, value: Any, default: str = "unknown") -> str:
    """Validated, cardinality-capped label value."""
    if value is None:
        return default
    text = str(value).strip()
    if not text or not _LABEL_RE.match(text):
        return default
    with rt.lock:
        seen = rt.seen.setdefault(kind, set())
        if text in seen:
            return text
        if len(seen) >= _MAX_VALUES_PER_LABEL:
            return "other"
        seen.add(text)
    return text


def _as_dict(value: Any) -> dict:
    return value if isinstance(value, dict) else {}


def _request_metadata(kwargs: dict) -> dict:
    """Merged view of the client metadata (usecase, agent, sensitivity, factory_alias)."""
    lp = _as_dict(kwargs.get("litellm_params"))
    merged: dict = {}
    slo = _as_dict(kwargs.get("standard_logging_object"))
    merged.update(_as_dict(_as_dict(slo.get("metadata")).get("requester_metadata")))
    merged.update(_as_dict(lp.get("metadata")))
    merged.update(_as_dict(lp.get("litellm_metadata")))
    return merged


def _deployment(kwargs: dict) -> str:
    lp = _as_dict(kwargs.get("litellm_params"))
    info = _as_dict(lp.get("model_info")) or _as_dict(_as_dict(lp.get("metadata")).get("model_info"))
    dep = info.get("factory_deployment")
    if dep in _DEPLOYMENTS:
        return dep
    model_id = str(info.get("id") or _as_dict(kwargs.get("standard_logging_object")).get("model_id") or "")
    prefix = model_id.split("-", 1)[0]
    return prefix if prefix in _DEPLOYMENTS else "unknown"


def _seconds(start: Any, end: Any) -> float | None:
    try:
        if isinstance(start, datetime) and isinstance(end, datetime):
            value = (end - start).total_seconds()
        else:
            value = float(end) - float(start)
    except (TypeError, ValueError):
        return None
    return value if value >= 0 else None


def _usage(kwargs: dict, response_obj: Any) -> tuple[int, int]:
    slo = _as_dict(kwargs.get("standard_logging_object"))
    pin, pout = slo.get("prompt_tokens"), slo.get("completion_tokens")
    if pin is None or pout is None:
        usage = getattr(response_obj, "usage", None)
        if usage is None and isinstance(response_obj, dict):
            usage = response_obj.get("usage")
        if isinstance(usage, dict):
            pin, pout = usage.get("prompt_tokens"), usage.get("completion_tokens")
        elif usage is not None:
            pin, pout = getattr(usage, "prompt_tokens", 0), getattr(usage, "completion_tokens", 0)
    try:
        return max(0, int(pin or 0)), max(0, int(pout or 0))
    except (TypeError, ValueError):
        return 0, 0


class FactoryMetrics(CustomLogger):
    """Prometheus exporter and sensitive-data guard for the LiteLLM proxy."""

    def __init__(self) -> None:
        super().__init__()
        self.rt = _runtime()

    # ---- proxy hook: placement rule and Langfuse masking -------------------------------
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        if not isinstance(data, dict):
            return data
        key = "metadata" if isinstance(data.get("metadata"), dict) or "litellm_metadata" not in data \
            else "litellm_metadata"
        md = data.get(key)
        if not isinstance(md, dict):
            md = {}
            data[key] = md
        model = str(data.get("model") or "")
        sensitivity = str(md.get("sensitivity") or "").upper()
        is_chat = "completion" in str(call_type) or call_type in ("acompletion", "completion")
        if _ENFORCE and sensitivity in _SENSITIVE and model not in _SENSITIVE_OK and is_chat:
            usecase = _label(self.rt, "usecase", md.get("usecase"))
            self.rt.reroutes.labels(_label(self.rt, "model", model), usecase).inc()
            md["factory_rerouted_from"] = model
            data["model"] = _SENSITIVE_ALIAS
            model = _SENSITIVE_ALIAS
        if model == _SENSITIVE_ALIAS or sensitivity in _SENSITIVE:
            # Sensitive prompts and answers never reach the trace store in clear text.
            md["mask_input"] = True
            md["mask_output"] = True
        md.setdefault("factory_alias", model)
        return data

    # ---- logging events -----------------------------------------------------------------
    def _record(self, kwargs: dict, response_obj: Any, start_time: Any, end_time: Any,
                status: str) -> None:
        rt = self.rt
        md = _request_metadata(kwargs)
        slo = _as_dict(kwargs.get("standard_logging_object"))
        alias = md.get("factory_alias") or slo.get("model_group") or md.get("model_group") \
            or kwargs.get("model")
        model = _label(rt, "model", alias)
        usecase = _label(rt, "usecase", md.get("usecase"))
        agent = _label(rt, "agent", md.get("agent"))
        deployment = _deployment(kwargs)
        call_type = str(kwargs.get("call_type") or slo.get("call_type") or "")

        rt.requests.labels(model, deployment, usecase, status).inc()
        rt.agent_requests.labels(agent, usecase, status).inc()
        latency = _seconds(start_time, end_time)
        if latency is not None:
            rt.latency.labels(model, usecase, deployment).observe(latency)
        if status != "success":
            return

        tokens_in, tokens_out = _usage(kwargs, response_obj)
        if tokens_in:
            rt.tokens.labels(model, usecase, "in").inc(tokens_in)
            rt.agent_tokens.labels(agent, usecase, "in").inc(tokens_in)
        if tokens_out:
            rt.tokens.labels(model, usecase, "out").inc(tokens_out)
            rt.agent_tokens.labels(agent, usecase, "out").inc(tokens_out)

        cost = kwargs.get("response_cost")
        if cost is None:
            cost = slo.get("response_cost")
        try:
            cost_eur = float(cost or 0.0) * _COST_FX
        except (TypeError, ValueError):
            cost_eur = 0.0
        # Always touch the series so that a zero-cost (VPS/local) alias is visible.
        rt.cost.labels(model, usecase).inc(max(0.0, cost_eur))

        if "embedding" not in call_type:
            ttft = _seconds(start_time, kwargs.get("completion_start_time"))
            if ttft is None and slo.get("completionStartTime") and slo.get("startTime"):
                ttft = _seconds(slo.get("startTime"), slo.get("completionStartTime"))
            if ttft is not None:
                rt.ttft.labels(model, usecase).observe(ttft)

    async def async_log_success_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._record(kwargs, response_obj, start_time, end_time, "success")
        except Exception:  # metrics must never break a request
            pass

    async def async_log_failure_event(self, kwargs, response_obj, start_time, end_time):
        try:
            self._record(kwargs, response_obj, start_time, end_time, "failure")
        except Exception:
            pass


factory_metrics = FactoryMetrics()
