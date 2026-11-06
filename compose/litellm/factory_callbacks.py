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

2. enforces placement before dispatch. In strict mode the verified virtual key selects a
   registered case, BU, minimum classification and model alias. C3/C4 use local-only aliases.
   The demo uses synthetic request metadata and reroutes sensitive calls to ``chat-sensitive``.
   Sensitive prompts and completions are masked in Langfuse and excluded from LiteLLM logging.

3. runs the factory LLM guardrails (pre-call, before the request leaves the gateway):

   - prompt injection / jailbreak detection (English and French patterns, chat-template
     delimiters, hidden Unicode tag and bidi characters). ``FACTORY_GUARDRAIL_MODE``:
     ``flag`` (default: request forwarded, rule ids in ``metadata.factory_guardrail`` and a
     ``guardrail:prompt_injection`` tag), ``block`` (HTTP 400, nothing forwarded) or ``off``.
     An unknown value fails closed to ``block``.
   - PII redaction (e-mail addresses, phone numbers, IBAN with mod-97 check, payment card
     numbers with Luhn check, French NIR with its key) on every request routed to an alias
     that may leave the local GPU (C1/C2 data; C3/C4 never do while sensitive routing is
     enforced). ``FACTORY_PII_REDACTION=true`` (default) | ``false``.
   - neither the detectors nor the metrics ever record prompt text: only rule ids and counts.

   Metric: factory_llm_guardrail_events_total{type,action,usecase} with type in
   prompt_injection | pii_email | pii_phone | pii_iban | pii_card | pii_nir and action in
   flagged | blocked | redacted (one event per request and type).

Cost attribution uses the authorized case in strict mode and synthetic labels in demo mode.
Metric label values are validated and capped to keep Prometheus cardinality bounded.
"""

from __future__ import annotations

import asyncio
import json
import math
import os
import re
import ssl
import sys
import threading
import types
import unicodedata
from collections import Counter as Tally
from collections.abc import Callable
from datetime import datetime
from pathlib import Path
from typing import Any

from litellm.integrations.custom_logger import CustomLogger
from prometheus_client import CollectorRegistry, Counter, Histogram, start_http_server

try:  # the LiteLLM proxy runs on FastAPI: an HTTPException becomes the HTTP status of the reply
    from fastapi import HTTPException
except ImportError:  # unit tests without FastAPI
    class HTTPException(Exception):  # type: ignore[no-redef]
        def __init__(self, status_code: int, detail: Any = None) -> None:
            super().__init__(status_code, detail)
            self.status_code = status_code
            self.detail = detail

_LABEL_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9_.:-]{0,63}$")
_MAX_VALUES_PER_LABEL = int(os.environ.get("FACTORY_METRICS_MAX_LABEL_VALUES", "200"))
_SENSITIVE = {c.strip().upper() for c in
              os.environ.get("FACTORY_SENSITIVE_CLASSES", "C3,C4").split(",") if c.strip()}
_SENSITIVE_ALIAS = os.environ.get("FACTORY_SENSITIVE_ALIAS", "chat-sensitive")
# Aliases allowed for sensitive data: the local-only chat alias and the local embeddings.
_SENSITIVE_OK = {_SENSITIVE_ALIAS, *[a.strip() for a in os.environ.get(
    "FACTORY_SENSITIVE_OK_ALIASES", "embed-default").split(",") if a.strip()]}
_ENFORCE = os.environ.get("FACTORY_ENFORCE_SENSITIVE_ROUTING", "true").lower() in ("1", "true", "yes")
try:
    _COST_FX = float((os.environ.get("FACTORY_COST_FX") or "").strip() or "1.0")
except ValueError:
    raise ValueError("FACTORY_COST_FX must be a finite positive rate") from None
if not math.isfinite(_COST_FX) or _COST_FX <= 0:
    raise ValueError("FACTORY_COST_FX must be a finite positive rate")
_PORT = int(os.environ.get("FACTORY_METRICS_PORT", "9464"))
_DEPLOYMENTS = {"cloud", "vps", "local"}
_GUARDRAIL_MODES = ("off", "flag", "block")


def _guardrail_mode(raw: str | None) -> str:
    value = (raw or "flag").strip().lower()
    return value if value in _GUARDRAIL_MODES else "block"  # unknown value: fail closed


def _env_bool(name: str, default: bool) -> bool:
    raw = os.environ.get(name)
    if raw is None or not raw.strip():
        return default
    return raw.strip().lower() in ("1", "true", "yes", "on")


GUARDRAIL_MODE = _guardrail_mode(os.environ.get("FACTORY_GUARDRAIL_MODE"))
PII_REDACTION = _env_bool("FACTORY_PII_REDACTION", True)
# Aliases served by the local GPU only: prompts sent to them never leave the factory.
LOCAL_ALIASES = frozenset(a.strip() for a in os.environ.get(
    "FACTORY_LOCAL_ALIASES", f"{_SENSITIVE_ALIAS},embed-default").split(",") if a.strip())
MAX_SCAN_CHARS = max(1024, int(os.environ.get("FACTORY_GUARDRAIL_MAX_SCAN_CHARS", "500000") or 500000))
# Above this size the detectors run in a worker thread, off the event loop of the proxy.
_THREAD_THRESHOLD_CHARS = 20_000

# These exact proxy routes and call types are supported by the pinned LiteLLM image.
_CHAT_ROUTES = frozenset({"/chat/completions", "/v1/chat/completions"})
_EMBED_ROUTES = frozenset({"/embeddings", "/v1/embeddings"})
_STRICT_SENSITIVE = frozenset({"C3", "C4"})
_STRICT_CLASSES = frozenset({"C1", "C2"}) | _STRICT_SENSITIVE
_STRICT_CHAT_ALIASES = frozenset({"chat-default", "chat-small", "chat-sensitive"})
_OPENAI_LOCAL_MODEL = re.compile(r"openai/[A-Za-z0-9][A-Za-z0-9_.:-]*(?:/[A-Za-z0-9][A-Za-z0-9_.:-]*)*")
_KEY_DIGEST = re.compile(r"[0-9a-f]{64}")
_AUTH_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_OVERRIDES = frozenset({
    "api_base", "api_key", "api_type", "api_version", "aws_access_key_id", "aws_secret_access_key",
    "azure_ad_token", "base_url", "context_window_fallbacks", "content_policy_fallbacks",
    "custom_llm_provider", "default_fallbacks", "extra_body", "extra_headers", "extra_query",
    "fallbacks", "fastest_response", "headers", "model_info", "model_list", "provider", "region_name",
    "router_settings", "router_settings_override", "specific_deployment", "user_config",
    "vertex_ai_project", "vertex_credentials", "_router_weights",
})
_STRICT_PAYLOAD_FIELDS = frozenset({
    # Standard chat and embedding options, without provider-specific options.
    "messages", "input", "stream", "stream_options", "temperature", "top_p", "n", "stop", "seed",
    "max_tokens", "max_completion_tokens", "frequency_penalty", "presence_penalty", "logprobs", "top_logprobs",
    "logit_bias", "response_format", "tools", "tool_choice", "parallel_tool_calls", "dimensions", "encoding_format",
    # LiteLLM-owned request context. Both metadata channels are rebuilt below.
    "litellm_call_id", "litellm_logging_obj", "proxy_server_request", "metadata", "litellm_metadata",
})


def _strict_mapping(pairs: list[tuple[Any, Any]]) -> dict:
    result = {}
    for key, value in pairs:
        if not isinstance(key, str) or key in result:
            raise ValueError("Invalid gateway policy mapping.")
        result[key] = value
    return result


def _read_policy_text(path: Path) -> str:
    with path.open(encoding="utf-8") as source:
        contents = source.read(262_145)
    if len(contents) > 262_144:
        raise ValueError("Gateway policy input is too large.")
    return contents


def _load_gateway_policy(path: str, registry: str) -> dict:
    # PyYAML ships with the pinned proxy. The strict loader prevents duplicate GitOps
    # keys from changing which sensitivity or model the gateway sees.
    import yaml

    class RegistryLoader(yaml.SafeLoader):
        def construct_mapping(self, node, deep=False):
            return _strict_mapping(self.construct_pairs(node, deep=deep))

    def identifier(value: Any) -> bool:
        return isinstance(value, str) and _AUTH_IDENTIFIER.fullmatch(value) is not None

    try:
        policy = json.loads(_read_policy_text(Path(path)), object_pairs_hook=_strict_mapping)
        if (not isinstance(policy, dict) or set(policy) != {"version", "bindings"}
                or type(policy["version"]) is not int or policy["version"] != 1):
            raise ValueError
        entries = policy["bindings"]
        if not isinstance(entries, list) or not 1 <= len(entries) <= 1000:
            raise ValueError
        cases = {}
        files = sorted(p for p in Path(registry).iterdir() if p.suffix in {".yml", ".yaml"})
        if not 1 <= len(files) <= 1000:
            raise ValueError
        for file in files:
            case = yaml.load(_read_policy_text(file), Loader=RegistryLoader)
            case_id = case["id"]
            center = case["cost_center"]
            sensitivity = case["sensitivity"]
            alias = case["model"]["alias"]
            if (not identifier(case_id) or case_id in cases or not identifier(center)
                    or sensitivity not in _STRICT_CLASSES or alias not in _STRICT_CHAT_ALIASES
                    or (sensitivity in _STRICT_SENSITIVE and alias != "chat-sensitive")):
                raise ValueError
            cases[case_id] = (center, sensitivity, alias)
        bindings = {}
        team_centers = {}
        for entry in entries:
            fields = {"key_sha256", "team_id", "usecase"}
            if not isinstance(entry, dict) or not fields <= set(entry) <= fields | {"allow_embeddings"}:
                raise ValueError
            digest, team, case_id = (entry[field] for field in ("key_sha256", "team_id", "usecase"))
            embeddings = entry.get("allow_embeddings", False)
            if (not isinstance(digest, str) or not _KEY_DIGEST.fullmatch(digest) or digest in bindings
                    or not identifier(team) or not identifier(case_id) or type(embeddings) is not bool):
                raise ValueError
            center, sensitivity, alias = cases[case_id]
            if team_centers.setdefault(team, center) != center:
                raise ValueError
            bindings[digest] = (team, case_id, center, sensitivity, alias, embeddings)
        return bindings
    except (OSError, ValueError, KeyError, TypeError, yaml.YAMLError, RecursionError):
        raise ValueError("Invalid or unavailable Factory gateway policy or registry.") from None


def _gateway_policy() -> dict | None:
    path = os.environ.get("FACTORY_GATEWAY_POLICY", "").strip()
    if path:
        if (not _ENFORCE or not _STRICT_SENSITIVE <= _SENSITIVE or _SENSITIVE_ALIAS != "chat-sensitive"
                or _SENSITIVE_OK != {"chat-sensitive", "embed-default"}
                or LOCAL_ALIASES != {"chat-sensitive", "embed-default"}):
            raise ValueError("Strict gateway routing requires the Factory sensitive aliases.")
        local_models = (os.environ.get(name, "") for name in ("LOCAL_CHAT_MODEL", "LOCAL_EMBED_MODEL"))
        if not all(len(model) <= 256 and _OPENAI_LOCAL_MODEL.fullmatch(model) for model in local_models):
            raise ValueError("Strict gateway routing requires explicit OpenAI-compatible local models.")
        if os.environ.get("FACTORY_DEMO_MODE", "auto").strip().lower() == "never" and (
            GUARDRAIL_MODE != "block" or not PII_REDACTION
        ):
            raise ValueError("Production gateway requires blocking guardrails and PII redaction.")
        return _load_gateway_policy(path, os.environ.get("FACTORY_GATEWAY_REGISTRY", "/factory-usecases"))
    if os.environ.get("FACTORY_DEMO_MODE", "auto").strip().lower() == "never":
        raise ValueError("Factory gateway policy is required with demo mode disabled.")
    return None


def _install_strict_http_client():
    import httpx
    import litellm

    # The pinned OpenAI adapters use a custom aclient_session in preference to
    # their redirect-following default, and do not close this caller-owned client.
    current = getattr(litellm, "aclient_session", None)
    owned = getattr(litellm, "_factory_strict_http_client", None)
    if owned is not None and current is owned and not owned.is_closed:
        return owned
    if current is not None or owned is not None:
        raise ValueError("Strict gateway cannot use a preconfigured HTTP client.")
    client = httpx.AsyncClient(follow_redirects=False, trust_env=False, verify=ssl.create_default_context())
    litellm.aclient_session = client
    litellm._factory_strict_http_client = client
    return client


def _gateway_denied() -> HTTPException:
    return HTTPException(status_code=403, detail={"error": {
        "message": "Request outside the Factory gateway policy.", "code": "factory_gateway_denied"}})


def _text_chat(data: dict) -> bool:
    messages = data.get("messages")
    if not isinstance(messages, list) or not messages:
        return False
    for message in messages:
        if not isinstance(message, dict):
            return False
        content = message.get("content")
        if isinstance(content, list):
            if any(not isinstance(part, dict) or set(part) != {"type", "text"}
                   or part["type"] != "text" or not isinstance(part["text"], str) for part in content):
                return False
        elif content is not None and not isinstance(content, str):
            return False
    tools = data.get("tools", [])
    return isinstance(tools, list) and all(
        isinstance(tool, dict) and set(tool) == {"type", "function"} and tool["type"] == "function"
        for tool in tools
    )


def _bound_gateway_request(policy: dict, auth, data: dict, call_type) -> None:
    if (getattr(auth, "via_virtual_key", False) is not True
            or getattr(auth, "user_role", None) in {"proxy_admin", "proxy_admin_viewer"}
            or getattr(auth, "aliases", None) or getattr(auth, "router_settings", None)):
        raise _gateway_denied()
    # UserAPIKeyAuth normalizes virtual credentials during LiteLLM authentication.
    # This callback consumes only its authenticated fingerprint and refuses raw keys.
    digest = getattr(auth, "api_key", None)
    if not isinstance(digest, str) or not _KEY_DIGEST.fullmatch(digest):
        raise _gateway_denied()
    binding = policy.get(digest)
    if binding is None:
        raise _gateway_denied()
    team, case_id, center, sensitivity, alias, embeddings = binding
    route = getattr(auth, "request_route", None)
    routes = getattr(auth, "allowed_routes", None)
    admitted = _CHAT_ROUTES | _EMBED_ROUTES if embeddings else _CHAT_ROUTES
    models = getattr(auth, "models", None)
    approved_models = {alias, "embed-default"} if embeddings else {alias}
    call = getattr(call_type, "value", call_type)
    if (getattr(auth, "team_id", None) != team or not isinstance(route, str)
            or not isinstance(routes, (list, tuple)) or not routes
            or any(not isinstance(item, str) or item not in admitted for item in routes) or route not in routes
            or not isinstance(models, (list, tuple)) or any(not isinstance(item, str) for item in models)
            or set(models) != approved_models
            or not ((call == "acompletion" and route in _CHAT_ROUTES)
                    or (call == "aembedding" and embeddings and route in _EMBED_ROUTES))
            or any(data.get(field) is not None for field in _OVERRIDES)
            or (call == "acompletion" and not _text_chat(data))):
        raise _gateway_denied()
    if call == "aembedding":
        alias = "embed-default"
    if data.get("model") != alias:
        raise _gateway_denied()
    channels = [(field, _as_dict(data.get(field))) for field in ("metadata", "litellm_metadata")
                if field == "metadata" or field in data]
    for _, current in channels:
        if current.get("usecase") not in (None, "", case_id):
            raise _gateway_denied()
        requested = current.get("sensitivity")
        if requested is not None:
            if not isinstance(requested, str) or requested not in _STRICT_CLASSES:
                raise _gateway_denied()
            sensitivity = max(sensitivity, requested)
    if sensitivity in _STRICT_SENSITIVE and alias not in {"chat-sensitive", "embed-default"}:
        # A request may raise its classification, but that does not authorize a new model.
        raise _gateway_denied()
    for field, current in channels:
        metadata = {}
        # Preserve the proxy's accounting key only in its canonical hashed form.
        for name in ("user_api_key", "user_api_key_hash"):
            if current.get(name) == digest:
                metadata[name] = current[name]
        for attribute in ("key_alias", "user_id", "org_id", "project_id", "request_route"):
            value = getattr(auth, attribute, None)
            if isinstance(value, str):
                field_name = "alias" if attribute == "key_alias" else attribute
                metadata[f"user_api_key_{field_name}"] = value
        metadata.update(usecase=case_id, sensitivity=sensitivity, factory_alias=alias,
                        factory_business_unit=center, user_api_key_team_id=team)
        if sensitivity in _STRICT_SENSITIVE or alias == "chat-sensitive":
            metadata.update(mask_input=True, mask_output=True, turn_off_message_logging=True)
        data[field] = metadata
    for field in data.keys() - _STRICT_PAYLOAD_FIELDS:
        data.pop(field)
    data["model"] = alias
    data["disable_fallbacks"] = True

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
    rt.guardrail = Counter("factory_llm_guardrail_events",
                           "Guardrail events by type (prompt_injection, pii_*), action and use case.",
                           ["type", "action", "usecase"], registry=reg)
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


# ================================================================== guardrails: detectors
# Pure functions (unit tested in compose/litellm/tests/). They return rule ids and counts
# only: no matched text is ever stored, logged or exported.

# Invisible characters removed before matching (soft hyphen, zero-width space / joiners,
# word joiner, BOM): they are used to split keywords ("ig<U+200B>nore").
_INVISIBLE_RE = re.compile("[\u00ad\u200b\u200c\u200d\u2060\ufeff]")
# Unicode "tag" characters (ASCII smuggling) and bidirectional overrides (Trojan Source):
# no legitimate business prompt needs them.
_HIDDEN_RE = re.compile("[\U000e0000-\U000e007f\u202a-\u202e\u2066-\u2069]")
_SPACES_RE = re.compile(r"\s+")

_W = r"(?:\W+\w+)"  # one more word (bounded repetitions below keep matching linear)
_INJECTION_RULES: tuple[tuple[str, re.Pattern[str]], ...] = tuple((rule, re.compile(rx)) for rule, rx in (
    # "ignore all previous instructions", "disregard your system prompt", "bypass the guardrails"
    ("override_instructions", r"\b(?:ignore|disregard|forget|override|bypass)\b" + _W + r"{0,4}?\W+"
     r"(?:previous|prior|above|earlier|preceding|former|initial|original|system|developer|your|these|those|all)\b"
     + _W + r"{0,3}?\W+(?:instructions?|rules|guidelines|prompts?|directives?|directions|constraints|"
     r"polic(?:y|ies)|guardrails?|safeguards?)\b"),
    ("override_instructions", r"\b(?:bypass|disable|circumvent|turn off)\W+(?:\w+\W+){0,2}?(?:guardrails?|"
     r"safeguards?|safety filters?|content filters?|moderation)\b"),
    # "ignore the instructions above", "forget the rules given before"
    ("override_instructions", r"\b(?:ignore|disregard|forget)\b" + _W + r"{0,3}?\W+"
     r"(?:instructions?|rules|guidelines|prompts?)\W+(?:above|before|so far|given (?:to you )?(?:earlier|before))\b"),
    # FR: "ignore toutes les instructions précédentes", "oublie tes consignes"
    ("override_instructions", r"\b(?:ignore[rsz]?|oublie[rsz]?|neglige[rsz]?|contourne[rsz]?|outrepasse[rsz]?)\b"
     + _W + r"{0,4}?\W+(?:precedent(?:e|es|s)?|anterieur(?:e|es|s)?|ci-dessus|initia(?:l|le|les|ux)|systeme|"
     r"toutes?|tes|vos)\b" + _W + r"{0,3}?\W+(?:instructions?|consignes?|regles?|directives?|contraintes?)\b"),
    # FR: "oublie les consignes précédentes"
    ("override_instructions", r"\b(?:ignore[rsz]?|oublie[rsz]?|neglige[rsz]?|ne tiens? pas compte des?)\b"
     + _W + r"{0,3}?\W+(?:instructions?|consignes?|regles?|directives?)\W+"
     r"(?:precedent\w*|anterieur\w*|ci-dessus|initia\w*|(?:du )?systeme)\b"),
    # "reveal your system prompt", "print the hidden instructions", "repeat the text above"
    ("prompt_exfiltration", r"\b(?:reveal|show|print|repeat|output|display|dump|leak|disclose|tell me|give me|"
     r"write out|spell out)\b" + _W + r"{0,4}?\W+(?:system|initial|hidden|secret|original|developer|internal)\W+"
     r"(?:prompt|instructions?|message|rules|configuration)\b"),
    ("prompt_exfiltration", r"\brepeat\b" + _W + r"{0,3}?\W+(?:text|words|everything)\W+(?:above|before)\b"),
    ("prompt_exfiltration", r"\bwhat\W+(?:is|are|were)\W+your\W+(?:system\W+prompt|initial\W+instructions|"
     r"hidden\W+instructions)\b"),
    # FR: "révèle ton prompt système", "affiche les instructions cachées"
    ("prompt_exfiltration", r"\b(?:revele|affiche|montre|repete|imprime|donne|ecris|divulgue|recopie)[rsz]?\b"
     + _W + r"{0,4}?\W+(?:prompt|instructions?|message|consignes?)\W+"
     r"(?:systeme|initia\w*|cache\w*|secret\w*|interne\w*)\b"),
    # "you are now DAN", "act as an unrestricted AI", "pretend you have no restrictions"
    ("role_jailbreak", r"\b(?:you are now|you're now|from now on,? you are|act as|pretend (?:to be|you are)|"
     r"roleplay as|behave like)\b" + _W + r"{0,5}?\W+(?:dan|jailbroken|unrestricted|unfiltered|uncensored|"
     r"without (?:any )?(?:restrictions|limits|filters|rules|guidelines))\b"),
    ("role_jailbreak", r"\bpretend\W+(?:that\W+)?you\W+(?:have|had)\W+no\W+(?:restrictions|limits|rules|"
     r"guidelines|filters)\b"),
    ("role_jailbreak", r"\b(?:enable|activate|enter|switch (?:to|into))\W+(?:the\W+)?"
     r"(?:god|dan|jailbreak|sudo|unrestricted)\W+mode\b"),
    ("role_jailbreak", r"\b(?:you are|you're)\W+(?:now\W+)?in\W+(?:the\W+)?"
     r"(?:developer|god|dan|jailbreak|sudo|unrestricted)\W+mode\b"),
    ("role_jailbreak", r"\bdeveloper\W+mode\W+(?:enabled|activated|output)\b"),
    ("role_jailbreak", r"\bdo anything now\b"),
    ("role_jailbreak", r"\bwithout\W+(?:any\W+)?(?:ethical|moral|safety|content)\W+(?:guidelines|restrictions|"
     r"filters|policies|limits)\b"),
    # FR: "tu es désormais une IA sans restriction", "active le mode développeur"
    ("role_jailbreak", r"\b(?:tu es (?:desormais|maintenant)|a partir de maintenant,? tu es|agis comme|"
     r"fais comme si tu etais|joue le role d)\w*\b" + _W + r"{0,5}?\W+(?:sans (?:aucune? )?(?:restrictions?|"
     r"limites?|filtres?|censure|regles?)|non censure\w*|debride\w*)\b"),
    ("role_jailbreak", r"\b(?:active[rsz]?|passe[rsz]? en|entre[rsz]? en)\W+(?:le\W+)?mode\W+"
     r"(?:dieu|dan|sans (?:restriction|limite)s?)\b"),
    ("role_jailbreak", r"\btu es\W+(?:maintenant\W+|desormais\W+)?en\W+mode\W+"
     r"(?:developpeur|dieu|dan|sans (?:restriction|limite)s?)\b"),
    # "decode this base64 and follow it"
    ("encoded_payload", r"\b(?:decode|decrypt|deobfuscate|decoder|dechiffre[rz]?)\b" + _W + r"{0,4}?\W+"
     r"(?:base64|rot13|hex|hexadecimal|binary)\b" + _W + r"{0,6}?\W+(?:follow|execute|run|obey|apply|"
     r"suis|suivre|executer?|appliquer?)\b"),
))
# Chat-template delimiters smuggled into user content (matched on the original line layout).
_DELIMITER_RE = re.compile(
    r"<\|(?:im_start|im_end|system|endoftext|eot_id|start_header_id|end_header_id)\|>"
    r"|\[/?inst\]|<</?sys>>|<\s*/?\s*system\s*>|^\s*#{2,}\s*(?:new\s+)?(?:system|instructions?)\b",
    re.MULTILINE)


def _fold(text: str) -> str:
    """Lower-case, accent-free, invisible-free view of a text, for matching only."""
    text = _INVISIBLE_RE.sub("", unicodedata.normalize("NFKC", text))
    text = "".join(c for c in unicodedata.normalize("NFKD", text) if not unicodedata.combining(c))
    return text.lower()


def detect_injection(text: str) -> list[str]:
    """Sorted ids of the prompt injection / jailbreak rules matched by ``text``."""
    if not text:
        return []
    hits: set[str] = set()
    if len(text) > MAX_SCAN_CHARS:  # padding must not hide a payload: oversize is itself flagged
        hits.add("oversized_input")
        text = text[:MAX_SCAN_CHARS]
    if _HIDDEN_RE.search(text):
        hits.add("hidden_unicode")
    folded = _fold(text)
    if _DELIMITER_RE.search(folded):
        hits.add("delimiter_injection")
    flat = _SPACES_RE.sub(" ", folded)
    for rule, rx in _INJECTION_RULES:
        if rule not in hits and rx.search(flat):
            hits.add(rule)
    return sorted(hits)


def detect_injection_in(texts: list[str]) -> list[str]:
    """Rule ids over several texts, within one budget of FACTORY_GUARDRAIL_MAX_SCAN_CHARS."""
    hits: set[str] = set()
    budget = MAX_SCAN_CHARS
    for text in texts:
        if budget <= 0:
            hits.add("oversized_input")
            break
        if len(text) > budget:
            hits.add("oversized_input")
            text = text[:budget]
        budget -= len(text)
        hits.update(detect_injection(text))
    return sorted(hits)


def luhn_ok(digits: str) -> bool:
    total = 0
    for i, ch in enumerate(reversed(digits)):
        d = ord(ch) - 48
        if i % 2:
            d = d * 2 - 9 if d > 4 else d * 2
        total += d
    return total % 10 == 0


# Lengths of the IBAN of the SEPA countries and a few others (ISO 13616 registry).
_IBAN_LENGTHS = {
    "AD": 24, "AT": 20, "BE": 16, "BG": 22, "CH": 21, "CY": 28, "CZ": 24, "DE": 22, "DK": 18, "EE": 20,
    "ES": 24, "FI": 18, "FR": 27, "GB": 22, "GI": 23, "GR": 27, "HR": 21, "HU": 28, "IE": 22, "IS": 26,
    "IT": 27, "LI": 21, "LT": 20, "LU": 20, "LV": 21, "MC": 27, "MT": 31, "NL": 18, "NO": 15, "PL": 28,
    "PT": 25, "RO": 24, "SE": 24, "SI": 19, "SK": 24, "SM": 27, "VA": 22, "AE": 23, "BR": 29, "MA": 28,
    "SA": 24, "TN": 24, "TR": 26, "UA": 29,
}


def iban_ok(candidate: str) -> bool:
    iban = re.sub(r"\s", "", candidate).upper()
    if not re.fullmatch(r"[A-Z]{2}\d{2}[A-Z0-9]{11,30}", iban):
        return False
    expected = _IBAN_LENGTHS.get(iban[:2])
    if expected is not None and len(iban) != expected:
        return False
    rearranged = iban[4:] + iban[:4]
    return int("".join(str(int(c, 36)) for c in rearranged)) % 97 == 1


def nir_ok(candidate: str) -> bool:
    """French social security number (NIR): 13 digits + key = 97 - (number mod 97)."""
    nir = re.sub(r"[\s.]", "", candidate).upper()
    if not re.fullmatch(r"[1-478]\d{4}(?:\d{2}|2[AB])\d{6}\d{2}", nir):
        return False
    month = int(nir[3:5])
    if month == 0 or 13 <= month <= 19:
        return False
    body = nir[:5] + {"2A": "19", "2B": "18"}.get(nir[5:7], nir[5:7]) + nir[7:13]
    return 97 - int(body) % 97 == int(nir[13:])


_EMAIL_RE = re.compile(r"(?<![A-Za-z0-9._%+-])[A-Za-z0-9._%+-]{1,64}@"
                       r"(?:[A-Za-z0-9](?:[A-Za-z0-9-]{0,61}[A-Za-z0-9])?\.){1,8}[A-Za-z]{2,24}(?![A-Za-z0-9-])")
_IBAN_GROUPED_RE = re.compile(r"(?<![A-Za-z0-9])[A-Z]{2}\d{2}(?: [A-Z0-9]{4}){2,7}(?: [A-Z0-9]{1,4})?(?![A-Za-z0-9])")
_IBAN_COMPACT_RE = re.compile(r"(?<![A-Za-z0-9])[A-Za-z]{2}\d{2}[A-Za-z0-9]{11,30}(?![A-Za-z0-9])")
_NIR_RE = re.compile(r"(?<![\w])[1-478][ .]?\d{2}[ .]?\d{2}[ .]?(?:\d{2}|2[ABab])"
                     r"[ .]?\d{3}[ .]?\d{3}[ .]?\d{2}(?![\w])")
# Compact (13-19 digits), groups of four (4-4-4-1..7) or the 4-6-5 layout, one separator kind.
_CARD_RE = re.compile(r"(?<![\d-])(?:\d{13,19}|\d{4}([ -])\d{4}\1\d{4}\1\d{1,7}|\d{4}([ -])\d{6}\2\d{5})(?!\d)")
_PHONE_RES = (
    re.compile(r"(?<![\w+])(?:\+|00)[1-9]\d{0,2}(?:[ .\-]?\(?\d{1,4}\)?){2,6}(?![\w])"),     # international
    re.compile(r"(?<![\w+])0[1-9](?:[ .\-]?\d{2}){4}(?![\w])"),                             # French national
    re.compile(r"(?<![\w+])(?:\+?1[ .\-]?)?(?:\(\d{3}\)[ .\-]?|\d{3}[ .\-])\d{3}[ .\-]\d{4}(?![\w])"),  # NANP
)
PLACEHOLDERS = {kind: f"[REDACTED_{kind.upper()}]" for kind in ("email", "iban", "nir", "card", "phone")}


def _iban_sub(match: re.Match[str], counts: Tally) -> str:
    text = match.group(0)
    if " " in text:  # grouped form: the last group may belong to the following text
        groups = text.split(" ")
        for end in range(len(groups), 2, -1):
            candidate = " ".join(groups[:end])
            if iban_ok(candidate):
                counts["iban"] += 1
                return PLACEHOLDERS["iban"] + text[len(candidate):]
        return text
    if iban_ok(text):
        counts["iban"] += 1
        return PLACEHOLDERS["iban"]
    return text


def _card_sub(match: re.Match[str], counts: Tally) -> str:
    digits = re.sub(r"\D", "", match.group(0))
    if 13 <= len(digits) <= 19 and digits[0] in "23456" and len(set(digits)) > 1 and luhn_ok(digits):
        counts["card"] += 1
        return PLACEHOLDERS["card"]
    return match.group(0)


def _phone_sub(match: re.Match[str], counts: Tally) -> str:
    if 9 <= len(re.sub(r"\D", "", match.group(0))) <= 15:
        counts["phone"] += 1
        return PLACEHOLDERS["phone"]
    return match.group(0)


def redact_pii(text: str) -> tuple[str, Tally]:
    """Text with e-mails, IBAN, NIR, card and phone numbers replaced, and the counts by kind."""
    counts: Tally = Tally()
    if not text or len(text) > MAX_SCAN_CHARS * 4:
        return text, counts

    def email(m: re.Match[str]) -> str:
        counts["email"] += 1
        return PLACEHOLDERS["email"]

    def nir(m: re.Match[str]) -> str:
        if nir_ok(m.group(0)):
            counts["nir"] += 1
            return PLACEHOLDERS["nir"]
        return m.group(0)

    text = _EMAIL_RE.sub(email, text)
    text = _IBAN_GROUPED_RE.sub(lambda m: _iban_sub(m, counts), text)
    text = _IBAN_COMPACT_RE.sub(lambda m: _iban_sub(m, counts), text)
    text = _NIR_RE.sub(nir, text)
    text = _CARD_RE.sub(lambda m: _card_sub(m, counts), text)
    for rx in _PHONE_RES:
        text = rx.sub(lambda m: _phone_sub(m, counts), text)
    return text, +counts


# ------------------------------------------------------------- request payload walking
_TRUSTED_ROLES = frozenset({"system", "developer", "assistant"})
_TEXT_FIELDS = ("content", "text", "parts", "input", "output")
_PAYLOAD_FIELDS = ("messages", "prompt", "input", "contents", "system")


def _map_text(node: Any, fn: Callable[[str], str], depth: int = 0) -> Any:
    """Apply ``fn`` to every text of a message structure (in place for dicts and lists)."""
    if depth > 12:
        return node
    if isinstance(node, str):
        return fn(node)
    if isinstance(node, list):
        for i, item in enumerate(node):
            node[i] = _map_text(item, fn, depth + 1)
        return node
    if isinstance(node, dict):
        for key in _TEXT_FIELDS:
            if key in node:
                node[key] = _map_text(node[key], fn, depth + 1)
    return node


def _untrusted_texts(data: dict) -> list[str]:
    """User, tool and retrieved texts of a request (system / developer / assistant turns excluded)."""
    texts: list[str] = []

    def keep(text: str) -> str:
        texts.append(text)
        return text

    for field in ("messages", "input", "contents", "prompt"):
        node = data.get(field)
        if isinstance(node, list):
            for item in node:
                if isinstance(item, dict) and str(item.get("role") or "").lower() in _TRUSTED_ROLES:
                    continue
                _map_text(item, keep)
        elif node is not None:
            _map_text(node, keep)
    return texts


def redact_payload(data: dict) -> Tally:
    """Redact PII in place in every text field of a request; counts by kind."""
    total: Tally = Tally()

    def redact(text: str) -> str:
        new, counts = redact_pii(text)
        total.update(counts)
        return new

    for field in _PAYLOAD_FIELDS:
        if field in data and data[field] is not None:
            data[field] = _map_text(data[field], redact)
    return total


def _payload_chars(data: dict) -> int:
    size = 0

    def count(text: str) -> str:
        nonlocal size
        size += len(text)
        return text

    for field in _PAYLOAD_FIELDS:
        if data.get(field) is not None:
            _map_text(data[field], count)
    return size


def _is_generative(call_type: Any) -> bool:
    """Chat / text completions, Responses API, Messages API (call type "<provider>_messages")
    and generate_content; not embeddings, files, batches or the assistants thread messages."""
    name = str(call_type or "")
    if name.endswith("_messages"):
        return "get_" not in name
    return any(k in name for k in ("completion", "responses", "generate_content"))


def _add_tag(md: dict, tag: str) -> None:
    tags = md.get("tags")
    if not isinstance(tags, list):
        tags = [] if tags is None else [str(tags)]
        md["tags"] = tags
    if tag not in tags:
        tags.append(tag)


class FactoryMetrics(CustomLogger):
    """Prometheus exporter and sensitive-data guard for the LiteLLM proxy."""

    def __init__(self) -> None:
        super().__init__()
        self.gateway_policy = _gateway_policy()
        self._strict_http_client = None
        if self.gateway_policy is not None:
            # LiteLLM constructs a logging object before the authorization callback,
            # including for failed calls. Strict deployments never log message text.
            import litellm
            litellm.turn_off_message_logging = True
            self._strict_http_client = _install_strict_http_client()
        self.rt = _runtime()

    # ---- proxy hook: placement rule, masking and guardrails -----------------------------
    async def async_pre_call_hook(self, user_api_key_dict, cache, data: dict, call_type):
        if self.gateway_policy is not None:
            import litellm
            if (getattr(litellm, "aclient_session", None) is not self._strict_http_client
                    or self._strict_http_client.is_closed):
                raise HTTPException(status_code=503, detail="Factory gateway HTTP transport unavailable.")
            if not isinstance(data, dict):
                raise _gateway_denied()
            _bound_gateway_request(self.gateway_policy, user_api_key_dict, data, call_type)
        if not isinstance(data, dict):
            return data
        key = "metadata" if isinstance(data.get("metadata"), dict) or "litellm_metadata" not in data \
            else "litellm_metadata"
        md = data.get(key)
        if not isinstance(md, dict):
            md = {}
            data[key] = md
        rt = self.rt
        model = str(data.get("model") or "")
        sensitivity = str(md.get("sensitivity") or "").strip().upper()
        usecase = _label(rt, "usecase", md.get("usecase"))
        generative = _is_generative(call_type)

        # 1. Placement rule: C3/C4 generation always runs on the local-only alias
        #    (chat completions, text completions, Responses API and Messages API alike).
        if _ENFORCE and sensitivity in _SENSITIVE and model not in _SENSITIVE_OK and generative:
            rt.reroutes.labels(_label(rt, "model", model), usecase).inc()
            md["factory_rerouted_from"] = model
            data["model"] = _SENSITIVE_ALIAS
            model = _SENSITIVE_ALIAS
        if model == _SENSITIVE_ALIAS or sensitivity in _SENSITIVE:
            # Sensitive prompts and answers are never stored in clear text: masked in Langfuse
            # and redacted for every LiteLLM logging integration; a client header cannot opt out.
            md["mask_input"] = True
            md["mask_output"] = True
            md["turn_off_message_logging"] = True
            headers = md.get("headers")
            if isinstance(headers, dict):
                for name in [h for h in headers if str(h).lower().endswith("disable-message-redaction")]:
                    headers.pop(name, None)
        md.setdefault("factory_alias", model)

        report: dict[str, Any] = {}
        # 2. Prompt injection / jailbreak detection on untrusted content (user, tool, retrieved).
        if GUARDRAIL_MODE != "off" and generative:
            texts = _untrusted_texts(data)
            if sum(len(t) for t in texts) > _THREAD_THRESHOLD_CHARS:
                rules = await asyncio.to_thread(detect_injection_in, texts)
            else:
                rules = detect_injection_in(texts)
            if rules:
                action = "blocked" if GUARDRAIL_MODE == "block" else "flagged"
                rt.guardrail.labels("prompt_injection", action, usecase).inc()
                if action == "blocked":
                    # Generic message: the matched rules are not disclosed to the caller.
                    raise HTTPException(status_code=400, detail={"error": {
                        "message": "Request blocked by the factory guardrail (prompt injection policy).",
                        "type": "guardrail_violation", "code": "factory_prompt_injection"}})
                report["prompt_injection"] = {"rules": rules, "action": action}
                _add_tag(md, "guardrail:prompt_injection")

        # 3. PII redaction before the prompt may leave the local GPU (cloud or VPS alias).
        if PII_REDACTION and model not in LOCAL_ALIASES:
            if _payload_chars(data) > _THREAD_THRESHOLD_CHARS:
                counts = await asyncio.to_thread(redact_payload, data)
            else:
                counts = redact_payload(data)
            for kind in sorted(counts):
                rt.guardrail.labels(f"pii_{kind}", "redacted", usecase).inc()
            if counts:
                report["pii_redacted"] = dict(sorted(counts.items()))
                _add_tag(md, "guardrail:pii_redacted")
        if report:
            report["mode"] = GUARDRAIL_MODE
            md["factory_guardrail"] = report  # rule ids and counts only, never prompt text
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
