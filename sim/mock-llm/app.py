# SPDX-License-Identifier: MIT
"""OpenAI-compatible mock LLM server for the key-less demo of the Virtual AI Factory.

One process stands in for the three placement targets of the factory. Each target is
exposed under its own path prefix so that LiteLLM can route to it like to a real
deployment:

    /cloud/v1/...   cloud provider (fast, remote)
    /vps/v1/...     small CPU model on the VPS (slow time to first token, low throughput)
    /local/v1/...   local GPU (fast, used for sensitive data classes)

Endpoints per prefix: GET /models, POST /chat/completions (JSON or SSE streaming),
POST /embeddings. Un-prefixed /v1/... maps to the cloud profile.

The failure rate of each profile is set by environment variables and can be changed at
runtime through POST /admin/failure (internal network only) to exercise the gateway
fallbacks. GET /stats exposes per-profile counters that the GPU simulator follows, plus two
privacy probes used by scripts/smoke_stack.sh: redaction placeholders of the gateway seen in
prompts, and e-mail addresses received in clear text (counts only, prompts are never stored).

No real model is involved: the generated text is synthetic French filler.
"""

from __future__ import annotations

import asyncio
import hashlib
import json
import math
import os
import random
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, StreamingResponse

PROFILES = ("cloud", "vps", "local")


def _env_float(name: str, default: float) -> float:
    try:
        return float(os.environ.get(name, default))
    except ValueError:
        return default


@dataclass
class Profile:
    name: str
    ttft_ms: float          # mean time to first token
    ttft_jitter_ms: float   # standard deviation
    tokens_per_s: float     # decode throughput
    embed_ms: float         # mean latency of an embeddings call
    failure_rate: float     # share of requests answered with a retryable error
    models: tuple[str, ...]
    max_tokens_cap: int


@dataclass
class Stats:
    requests: int = 0
    failures: int = 0
    in_flight: int = 0
    tokens_out: int = 0
    tokens_in: int = 0
    embeddings: int = 0
    by_model: dict[str, int] = field(default_factory=dict)
    # Privacy probes for the smoke test (counts only, the prompt itself is never kept): gateway
    # redaction placeholders seen, and e-mail addresses that reached this target in clear text.
    redaction_markers: int = 0
    raw_emails: int = 0


def _load_profiles() -> dict[str, Profile]:
    return {
        "cloud": Profile(
            name="cloud",
            ttft_ms=_env_float("MOCK_TTFT_MS_CLOUD", 420),
            ttft_jitter_ms=_env_float("MOCK_TTFT_JITTER_MS_CLOUD", 160),
            tokens_per_s=_env_float("MOCK_TPS_CLOUD", 90),
            embed_ms=_env_float("MOCK_EMBED_MS_CLOUD", 45),
            failure_rate=_env_float("MOCK_FAILURE_RATE_CLOUD", 0.03),
            models=("mock-cloud-large", "mock-cloud-small", "mock-embed"),
            max_tokens_cap=400,
        ),
        "vps": Profile(
            name="vps",
            ttft_ms=_env_float("MOCK_TTFT_MS_VPS", 750),
            ttft_jitter_ms=_env_float("MOCK_TTFT_JITTER_MS_VPS", 240),
            tokens_per_s=_env_float("MOCK_TPS_VPS", 30),
            embed_ms=_env_float("MOCK_EMBED_MS_VPS", 140),
            failure_rate=_env_float("MOCK_FAILURE_RATE_VPS", 0.01),
            models=("mock-vps-8b", "mock-embed"),
            max_tokens_cap=160,
        ),
        "local": Profile(
            name="local",
            ttft_ms=_env_float("MOCK_TTFT_MS_LOCAL", 260),
            ttft_jitter_ms=_env_float("MOCK_TTFT_JITTER_MS_LOCAL", 90),
            tokens_per_s=_env_float("MOCK_TPS_LOCAL", 55),
            embed_ms=_env_float("MOCK_EMBED_MS_LOCAL", 25),
            failure_rate=_env_float("MOCK_FAILURE_RATE_LOCAL", 0.005),
            models=("mock-local-14b", "mock-embed"),
            max_tokens_cap=300,
        ),
    }


PROFILE_MAP = _load_profiles()
STATS: dict[str, Stats] = {p: Stats() for p in PROFILES}
STATS_LOCK = threading.Lock()
EMBED_DIM = int(os.environ.get("MOCK_EMBED_DIM", "384"))
# Upper bound on simulated latency so that a misconfiguration never hangs a client.
MAX_DELAY_S = _env_float("MOCK_MAX_DELAY_S", 20.0)
STARTED_AT = time.time()

WORDS = (
    "the factory processes the request in line with the data placement policy "
    "the use case registry shows a limited risk tier and a recent review "
    "the answer relies on indexed excerpts of the fictional document corpus "
    "costs are allocated to the cost center of the use case according to the chargeback mode "
    "the evaluation gate checks faithfulness answer relevancy and context precision "
    "no real data is used in this generic demonstration"
).split()

app = FastAPI(title="mock-llm", version="1.0.0", docs_url=None, redoc_url=None, openapi_url=None)


def _count_tokens(text: str) -> int:
    # Rough heuristic (about 4 characters per token), good enough for cost simulation.
    return max(1, math.ceil(len(text) / 4))


def _messages_text(messages: Any) -> str:
    parts: list[str] = []
    if isinstance(messages, list):
        for m in messages:
            if not isinstance(m, dict):
                continue
            content = m.get("content")
            if isinstance(content, str):
                parts.append(content)
            elif isinstance(content, list):
                for c in content:
                    if isinstance(c, dict) and isinstance(c.get("text"), str):
                        parts.append(c["text"])
    return "\n".join(parts)


_REDACTION_MARKER_RE = re.compile(r"\[REDACTED_[A-Z]+\]")
_EMAIL_RE = re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+(?:\.[A-Za-z0-9-]+)*\.[A-Za-z]{2,}")


def _profile_or_404(name: str) -> Profile:
    profile = PROFILE_MAP.get(name)
    if profile is None:
        raise HTTPException(status_code=404, detail=f"unknown profile {name!r}")
    return profile


def _ttft_seconds(profile: Profile) -> float:
    value = random.gauss(profile.ttft_ms, profile.ttft_jitter_ms)
    # Occasional slow outlier (queueing, cold cache) to make the p95 meaningful.
    if random.random() < 0.03:
        value *= random.uniform(1.8, 3.0)
    return min(MAX_DELAY_S, max(0.03, value / 1000.0))


def _completion_length(profile: Profile, body: dict[str, Any]) -> int:
    requested = body.get("max_tokens") or body.get("max_completion_tokens") or 120
    try:
        requested = int(requested)
    except (TypeError, ValueError):
        requested = 120
    cap = max(1, min(requested, profile.max_tokens_cap))
    return max(1, int(random.triangular(cap * 0.25, cap, cap * 0.6)))


def _synthetic_words(n_tokens: int, seed_text: str) -> list[str]:
    rnd = random.Random(hashlib.sha256(seed_text.encode()).hexdigest() + str(time.time_ns()))
    # One word is roughly 1.3 tokens in French.
    n_words = max(1, int(n_tokens / 1.3))
    return [rnd.choice(WORDS) for _ in range(n_words)]


def _maybe_fail(profile: Profile) -> JSONResponse | None:
    if random.random() >= profile.failure_rate:
        return None
    status = random.choice((503, 503, 429, 500))
    with STATS_LOCK:
        STATS[profile.name].failures += 1
    return JSONResponse(
        status_code=status,
        content={"error": {"message": f"simulated {profile.name} failure", "type": "server_error",
                           "code": status}},
        headers={"retry-after": "1"} if status == 429 else None,
    )


def _usage(prompt_tokens: int, completion_tokens: int) -> dict[str, int]:
    return {"prompt_tokens": prompt_tokens, "completion_tokens": completion_tokens,
            "total_tokens": prompt_tokens + completion_tokens}


async def _chat(profile: Profile, request: Request) -> Any:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")

    model = str(body.get("model") or profile.models[0])
    prompt_text = _messages_text(body.get("messages"))
    prompt_tokens = _count_tokens(prompt_text)
    stream = bool(body.get("stream"))
    include_usage = bool((body.get("stream_options") or {}).get("include_usage"))

    with STATS_LOCK:
        st = STATS[profile.name]
        st.requests += 1
        st.tokens_in += prompt_tokens
        st.by_model[model] = st.by_model.get(model, 0) + 1
        st.redaction_markers += len(_REDACTION_MARKER_RE.findall(prompt_text))
        st.raw_emails += len(_EMAIL_RE.findall(prompt_text))

    failure = _maybe_fail(profile)
    if failure is not None:
        # Fail after a short delay, like a real overloaded upstream.
        await asyncio.sleep(random.uniform(0.05, 0.3))
        return failure

    n_out = _completion_length(profile, body)
    words = _synthetic_words(n_out, prompt_text)
    ttft = _ttft_seconds(profile)
    per_token = 1.0 / max(1.0, profile.tokens_per_s)
    created = int(time.time())
    completion_id = f"chatcmpl-mock-{uuid.uuid4().hex[:20]}"

    if not stream:
        with STATS_LOCK:
            STATS[profile.name].in_flight += 1
        try:
            await asyncio.sleep(min(MAX_DELAY_S, ttft + n_out * per_token))
        finally:
            with STATS_LOCK:
                STATS[profile.name].in_flight -= 1
                STATS[profile.name].tokens_out += n_out
        return {
            "id": completion_id,
            "object": "chat.completion",
            "created": created,
            "model": model,
            "system_fingerprint": f"mock-{profile.name}",
            "choices": [{"index": 0, "finish_reason": "stop",
                         "message": {"role": "assistant", "content": " ".join(words)}}],
            "usage": _usage(prompt_tokens, n_out),
        }

    async def events():
        with STATS_LOCK:
            STATS[profile.name].in_flight += 1
        sent = 0
        try:
            await asyncio.sleep(ttft)
            head = {"id": completion_id, "object": "chat.completion.chunk", "created": created,
                    "model": model, "system_fingerprint": f"mock-{profile.name}"}
            first = dict(head, choices=[{"index": 0, "delta": {"role": "assistant", "content": ""},
                                         "finish_reason": None}])
            yield f"data: {json.dumps(first)}\n\n"
            # Emit words in small groups to keep the number of SSE frames reasonable.
            group = 3
            budget = MAX_DELAY_S
            for i in range(0, len(words), group):
                chunk_words = words[i:i + group]
                text = (" " if i else "") + " ".join(chunk_words)
                delay = per_token * 1.3 * len(chunk_words)
                budget -= delay
                if budget > 0:
                    await asyncio.sleep(delay)
                sent += len(chunk_words)
                frame = dict(head, choices=[{"index": 0, "delta": {"content": text},
                                             "finish_reason": None}])
                yield f"data: {json.dumps(frame, ensure_ascii=False)}\n\n"
            last = dict(head, choices=[{"index": 0, "delta": {}, "finish_reason": "stop"}])
            yield f"data: {json.dumps(last)}\n\n"
            if include_usage:
                usage = dict(head, choices=[], usage=_usage(prompt_tokens, n_out))
                yield f"data: {json.dumps(usage)}\n\n"
            yield "data: [DONE]\n\n"
        finally:
            with STATS_LOCK:
                STATS[profile.name].in_flight -= 1
                STATS[profile.name].tokens_out += n_out if sent else 0

    return StreamingResponse(events(), media_type="text/event-stream",
                             headers={"cache-control": "no-cache", "x-accel-buffering": "no"})


def _embedding_vector(text: str) -> list[float]:
    # Deterministic pseudo-embedding: same text, same vector; unit norm.
    seed = int.from_bytes(hashlib.sha256(text.encode("utf-8", "replace")).digest()[:8], "big")
    rnd = random.Random(seed)
    vec = [rnd.gauss(0.0, 1.0) for _ in range(EMBED_DIM)]
    norm = math.sqrt(sum(v * v for v in vec)) or 1.0
    return [round(v / norm, 6) for v in vec]


async def _embeddings(profile: Profile, request: Request) -> Any:
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    if not isinstance(body, dict):
        raise HTTPException(status_code=400, detail="JSON object expected")
    raw = body.get("input")
    inputs = [raw] if isinstance(raw, str) else [str(x) for x in (raw or [])]
    if not inputs or len(inputs) > 512:
        raise HTTPException(status_code=400, detail="input must hold 1..512 items")
    model = str(body.get("model") or "mock-embed")
    tokens = sum(_count_tokens(t) for t in inputs)
    with STATS_LOCK:
        st = STATS[profile.name]
        st.requests += 1
        st.embeddings += 1
        st.tokens_in += tokens
        st.by_model[model] = st.by_model.get(model, 0) + 1
    failure = _maybe_fail(profile)
    if failure is not None:
        return failure
    delay = random.gauss(profile.embed_ms, profile.embed_ms * 0.3) / 1000.0 + 0.0004 * len(inputs)
    await asyncio.sleep(min(MAX_DELAY_S, max(0.005, delay)))
    return {
        "object": "list",
        "model": model,
        "data": [{"object": "embedding", "index": i, "embedding": _embedding_vector(t)}
                 for i, t in enumerate(inputs)],
        "usage": {"prompt_tokens": tokens, "total_tokens": tokens},
    }


def _models(profile: Profile) -> dict[str, Any]:
    return {"object": "list", "data": [
        {"id": m, "object": "model", "created": int(STARTED_AT), "owned_by": f"mock-{profile.name}"}
        for m in profile.models]}


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/stats")
async def stats() -> dict[str, Any]:
    with STATS_LOCK:
        data = {name: {"requests": s.requests, "failures": s.failures, "in_flight": s.in_flight,
                       "tokens_in": s.tokens_in, "tokens_out": s.tokens_out,
                       "embeddings": s.embeddings, "by_model": dict(s.by_model),
                       "redaction_markers": s.redaction_markers, "raw_emails": s.raw_emails,
                       "failure_rate": PROFILE_MAP[name].failure_rate}
                for name, s in STATS.items()}
    return {"uptime_s": round(time.time() - STARTED_AT, 1), "profiles": data}


@app.post("/admin/failure")
async def set_failure(request: Request) -> dict[str, Any]:
    """Change the failure rate of a profile at runtime (used by the smoke test)."""
    try:
        body = await request.json()
    except (json.JSONDecodeError, UnicodeDecodeError):
        raise HTTPException(status_code=400, detail="invalid JSON body") from None
    profile = _profile_or_404(str(body.get("profile", "")))
    try:
        rate = float(body.get("rate"))
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="rate must be a number in [0, 1]") from None
    if not 0.0 <= rate <= 1.0:
        raise HTTPException(status_code=400, detail="rate must be a number in [0, 1]")
    profile.failure_rate = rate
    return {"profile": profile.name, "failure_rate": rate}


@app.get("/{profile_name}/v1/models")
async def models(profile_name: str) -> dict[str, Any]:
    return _models(_profile_or_404(profile_name))


@app.post("/{profile_name}/v1/chat/completions")
async def chat(profile_name: str, request: Request) -> Any:
    return await _chat(_profile_or_404(profile_name), request)


@app.post("/{profile_name}/v1/embeddings")
async def embeddings(profile_name: str, request: Request) -> Any:
    return await _embeddings(_profile_or_404(profile_name), request)


@app.get("/v1/models")
async def models_default() -> dict[str, Any]:
    return _models(PROFILE_MAP["cloud"])


@app.post("/v1/chat/completions")
async def chat_default(request: Request) -> Any:
    return await _chat(PROFILE_MAP["cloud"], request)


@app.post("/v1/embeddings")
async def embeddings_default(request: Request) -> Any:
    return await _embeddings(PROFILE_MAP["cloud"], request)
