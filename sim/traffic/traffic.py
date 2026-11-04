# SPDX-License-Identifier: MIT
"""Synthetic traffic generator for the key-less demo of the Virtual AI Factory.

It plays the fictional use cases (uc-01 .. uc-08) against the LiteLLM gateway with the
cost attribution metadata of the contract ({usecase, agent}) and queries Qdrant so that
the dashboards, the Langfuse traces and the FinOps/Agent Ops metrics have live data:

* rag_qa         embed the question (embed-default), vector search, streamed answer
* agent          plan, tool call (vector search), act, streamed final answer
* classification / extraction / routing   one short call on chat-small
* summarization / generation              one long streamed call

Placement rule of the factory: use cases whose data class is C3 or C4 always go to
chat-sensitive (local GPU only), whatever alias their configuration names.

Use cases and agents are read from the mounted GitOps configuration (FACTORY_CONFIG_DIR,
default /config) when present; a built-in fictional list is used otherwise. A share of
the tasks posts a fictional user feedback score to Langfuse ("retours d'usage").

No real data is ever sent: prompts are synthetic French sentences.
"""

from __future__ import annotations

import asyncio
import glob
import json
import os
import random
import re
import statistics
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import httpx
import yaml

LITELLM_URL = os.environ.get("LITELLM_URL", "http://litellm:4000").rstrip("/")
LITELLM_KEY = os.environ.get("LITELLM_MASTER_KEY", "")
QDRANT_URL = os.environ.get("QDRANT_URL", "http://qdrant:6333").rstrip("/")
LANGFUSE_HOST = os.environ.get("LANGFUSE_HOST", "").rstrip("/")
LANGFUSE_PUBLIC_KEY = os.environ.get("LANGFUSE_PUBLIC_KEY", "")
LANGFUSE_SECRET_KEY = os.environ.get("LANGFUSE_SECRET_KEY", "")
CONFIG_DIR = Path(os.environ.get("FACTORY_CONFIG_DIR", "/config"))
TASKS_PER_MIN = max(0.1, float(os.environ.get("TRAFFIC_TASKS_PER_MIN", "30")))
CONCURRENCY = max(1, int(os.environ.get("TRAFFIC_CONCURRENCY", "4")))
QDRANT_POINTS = max(10, int(os.environ.get("TRAFFIC_QDRANT_POINTS", "400")))
FEEDBACK_SHARE = min(1.0, max(0.0, float(os.environ.get("TRAFFIC_FEEDBACK_SHARE", "0.3"))))
SEED = os.environ.get("TRAFFIC_SEED")
HEARTBEAT = Path(os.environ.get("TRAFFIC_HEARTBEAT_FILE", "/tmp/traffic.heartbeat"))
SUMMARY_EVERY_S = 60.0
SENSITIVE_CLASSES = {"C3", "C4"}
ID_RE = re.compile(r"^[a-z0-9][a-z0-9-]{0,40}$")

rng = random.Random(int(SEED)) if SEED else random.Random()


@dataclass
class UseCase:
    id: str
    task_type: str
    sensitivity: str
    alias: str
    agents: list[str]
    index: str | None
    weight: float = 1.0
    thumbs_down: float = 0.06


# Built-in fictional catalogue, used only when the configuration is not mounted.
BUILTIN = [
    UseCase("uc-01", "rag_qa", "C3", "chat-default", ["ag-research"], "kb-docs", 1.6),
    UseCase("uc-02", "agent", "C2", "chat-default", ["ag-support"], "kb-support", 1.4, 0.12),
    UseCase("uc-03", "classification", "C1", "chat-small", ["ag-triage"], None, 2.0),
    UseCase("uc-04", "extraction", "C2", "chat-small", ["ag-extract"], None, 1.2),
    UseCase("uc-05", "summarization", "C2", "chat-default", ["ag-summary"], None, 0.9),
    UseCase("uc-06", "rag_qa", "C4", "chat-sensitive", ["ag-legal"], "kb-legal", 0.6),
    UseCase("uc-07", "generation", "C1", "chat-default", ["ag-writer"], None, 0.8),
    UseCase("uc-08", "routing", "C1", "chat-small", ["ag-router"], None, 1.5),
]

QUESTIONS = [
    "What is the approval procedure for a new fictional supplier?",
    "Summarize the internal memo on the retention policy for demo documents.",
    "Classify this fictional ticket: the third-floor printer no longer responds.",
    "Extract the amount, the date and the cost center from this demo invoice.",
    "What are the onboarding steps for a fictional employee?",
    "Draft a polite reply to an information request about opening hours.",
    "Which department should handle a question about a fictional expense refund?",
    "Compare the two versions of the fictional organization's AI acceptable use policy.",
]


def _read_yaml(path: Path) -> Any:
    try:
        with path.open(encoding="utf-8") as fh:
            return yaml.safe_load(fh)
    except (OSError, yaml.YAMLError):
        return None


def load_usecases() -> list[UseCase]:
    files = sorted(glob.glob(str(CONFIG_DIR / "usecases" / "*.yaml")))
    if not files:
        return BUILTIN
    agents_by_uc: dict[str, list[str]] = {}
    catalog = _read_yaml(CONFIG_DIR / "catalog" / "agents.yaml") or {}
    for ag in (catalog.get("agents") or []) if isinstance(catalog, dict) else []:
        if isinstance(ag, dict) and ag.get("id") and ag.get("usecase"):
            agents_by_uc.setdefault(str(ag["usecase"]), []).append(str(ag["id"]))
    builtin = {u.id: u for u in BUILTIN}
    result: list[UseCase] = []
    for f in files:
        doc = _read_yaml(Path(f))
        if not isinstance(doc, dict) or not doc.get("id"):
            continue
        uc_id = str(doc["id"])
        if not ID_RE.match(uc_id) or doc.get("stage") == "retired":
            continue
        model = doc.get("model") or {}
        rag = doc.get("rag") or {}
        default = builtin.get(uc_id)
        result.append(UseCase(
            id=uc_id,
            task_type=str(doc.get("task_type") or (default.task_type if default else "generation")),
            sensitivity=str(doc.get("sensitivity") or "C2"),
            alias=str(model.get("alias") or "chat-default"),
            agents=agents_by_uc.get(uc_id) or (default.agents if default else [f"ag-{uc_id}"]),
            index=str(rag["index"]) if isinstance(rag, dict) and rag.get("index") else None,
            weight=default.weight if default else 1.0,
            thumbs_down=default.thumbs_down if default else 0.06,
        ))
    return result or BUILTIN


def route_alias(uc: UseCase, wanted: str) -> str:
    """Factory placement rule: sensitive data classes never leave the local GPU."""
    if wanted == "embed-default":
        return wanted
    if uc.sensitivity in SENSITIVE_CLASSES:
        return "chat-sensitive"
    return wanted


@dataclass
class Counters:
    tasks: int = 0
    task_errors: int = 0
    calls: int = 0
    call_errors: int = 0
    by_alias: dict[str, int] = field(default_factory=dict)
    ttft_ms: list[float] = field(default_factory=list)
    vector_ms: list[float] = field(default_factory=list)
    feedback: int = 0

    def reset(self) -> None:
        self.__init__()


COUNTERS = Counters()
BACKGROUND_TASKS: list[asyncio.Task] = []


def _p95(values: list[float]) -> float | None:
    if len(values) < 2:
        return round(values[0], 1) if values else None
    return round(statistics.quantiles(values, n=20)[18], 1)


def log(level: str, msg: str, **fields: Any) -> None:
    print(json.dumps({"level": level, "msg": msg, **fields}, ensure_ascii=False), flush=True)


def _iso(ts: float) -> str:
    return time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime(ts)) + f".{int((ts % 1) * 1000):03d}Z"


class Factory:
    def __init__(self, client: httpx.AsyncClient):
        self.client = client
        self.collections: dict[str, int] = {}
        # Retrieval spans sent to Langfuse in batches (Agent Ops vector latency).
        self.spans: list[dict[str, Any]] = []

    @property
    def langfuse_enabled(self) -> bool:
        return bool(LANGFUSE_HOST and LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)

    def add_retrieval_span(self, uc: UseCase, agent: str, task_id: str, collection: str,
                           started: float, ended: float, hits: int) -> None:
        if not self.langfuse_enabled or task_id.startswith("index-"):
            return
        span_id = uuid.uuid4().hex
        self.spans.append({
            "id": uuid.uuid4().hex, "type": "span-create", "timestamp": _iso(ended),
            "body": {"id": span_id, "traceId": task_id, "name": "qdrant-vector-search",
                     "startTime": _iso(started), "endTime": _iso(ended),
                     # No query text: only the shape of the call (sensitive use cases too).
                     "input": {"collection": collection, "limit": 5}, "output": {"hits": hits},
                     "metadata": {"usecase": uc.id, "agent": agent, "sensitivity": uc.sensitivity}},
        })

    async def flush_spans(self) -> None:
        if not self.spans:
            return
        batch, self.spans = self.spans[:200], self.spans[200:]
        try:
            resp = await self.client.post(f"{LANGFUSE_HOST}/api/public/ingestion", timeout=10,
                                          auth=(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY),
                                          json={"batch": batch})
            if resp.status_code >= 300:
                log("warn", "langfuse ingestion rejected", status=resp.status_code)
        except httpx.HTTPError as exc:
            log("warn", "langfuse ingestion failed", error=str(exc)[:200])

    @property
    def headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {LITELLM_KEY}"} if LITELLM_KEY else {}

    def _metadata(self, uc: UseCase, agent: str, task_id: str, step: str) -> dict[str, Any]:
        return {
            "usecase": uc.id,
            "agent": agent,
            "sensitivity": uc.sensitivity,
            "task_id": task_id,
            # Langfuse fields understood by the LiteLLM integration: one trace per task.
            "trace_id": task_id,
            "trace_name": agent,
            "generation_name": step,
            "trace_user_id": "sim-traffic",
            "tags": [uc.id, uc.sensitivity, uc.task_type, "simulation"],
        }

    async def embed(self, uc: UseCase, agent: str, task_id: str, texts: list[str]) -> list[list[float]]:
        COUNTERS.calls += 1
        COUNTERS.by_alias["embed-default"] = COUNTERS.by_alias.get("embed-default", 0) + 1
        resp = await self.client.post(
            f"{LITELLM_URL}/v1/embeddings", headers=self.headers, timeout=30,
            json={"model": "embed-default", "input": texts,
                  "metadata": self._metadata(uc, agent, task_id, "embed")})
        if resp.status_code != 200:
            COUNTERS.call_errors += 1
            raise RuntimeError(f"embeddings {resp.status_code}")
        return [d["embedding"] for d in resp.json()["data"]]

    async def chat(self, uc: UseCase, agent: str, task_id: str, step: str, wanted_alias: str,
                   prompt: str, max_tokens: int, stream: bool) -> str:
        alias = route_alias(uc, wanted_alias)
        COUNTERS.calls += 1
        COUNTERS.by_alias[alias] = COUNTERS.by_alias.get(alias, 0) + 1
        body = {
            "model": alias,
            "messages": [
                {"role": "system", "content": "You are a demo assistant of a fictional AI factory."},
                {"role": "user", "content": prompt},
            ],
            "max_tokens": max_tokens,
            "temperature": 0.2,
            "stream": stream,
            "metadata": self._metadata(uc, agent, task_id, step),
        }
        if stream:
            body["stream_options"] = {"include_usage": True}
        started = time.perf_counter()
        if not stream:
            resp = await self.client.post(f"{LITELLM_URL}/v1/chat/completions", headers=self.headers,
                                          json=body, timeout=90)
            if resp.status_code != 200:
                COUNTERS.call_errors += 1
                raise RuntimeError(f"chat {alias} {resp.status_code}")
            COUNTERS.ttft_ms.append((time.perf_counter() - started) * 1000)
            return resp.json()["choices"][0]["message"].get("content") or ""
        text: list[str] = []
        first = None
        async with self.client.stream("POST", f"{LITELLM_URL}/v1/chat/completions",
                                      headers=self.headers, json=body, timeout=90) as resp:
            if resp.status_code != 200:
                await resp.aread()
                COUNTERS.call_errors += 1
                raise RuntimeError(f"chat {alias} {resp.status_code}")
            async for line in resp.aiter_lines():
                if not line.startswith("data:"):
                    continue
                payload = line[5:].strip()
                if payload == "[DONE]":
                    break
                try:
                    chunk = json.loads(payload)
                except json.JSONDecodeError:
                    continue
                for choice in chunk.get("choices") or []:
                    delta = (choice.get("delta") or {}).get("content")
                    if delta:
                        if first is None:
                            first = time.perf_counter()
                        text.append(delta)
        if first is not None:
            COUNTERS.ttft_ms.append((first - started) * 1000)
        return "".join(text)

    async def ensure_collection(self, uc: UseCase, agent: str, name: str) -> None:
        if name in self.collections:
            return
        resp = await self.client.get(f"{QDRANT_URL}/collections/{name}", timeout=10)
        if resp.status_code == 200:
            info = resp.json().get("result", {})
            self.collections[name] = int(info.get("points_count") or 0)
            if self.collections[name] >= QDRANT_POINTS // 2:
                return
        # Build a fictional corpus: embed titles through the gateway when possible.
        titles = [f"Fictional document {i:04d} of index {name}" for i in range(QDRANT_POINTS)]
        vectors: list[list[float]] = []
        try:
            for i in range(0, len(titles), 64):
                vectors.extend(await self.embed(uc, agent, f"index-{name}", titles[i:i + 64]))
        except (httpx.HTTPError, RuntimeError, KeyError) as exc:
            log("warn", "embedding through the gateway failed, random vectors used",
                collection=name, error=str(exc))
            vectors = [[rng.gauss(0, 1) for _ in range(384)] for _ in titles]
        dim = len(vectors[0])
        if resp.status_code != 200:
            create = await self.client.put(f"{QDRANT_URL}/collections/{name}", timeout=30,
                                           json={"vectors": {"size": dim, "distance": "Cosine"}})
            if create.status_code not in (200, 409):
                raise RuntimeError(f"qdrant create {name}: {create.status_code} {create.text[:200]}")
        points = [{"id": i, "vector": v, "payload": {"title": t, "index": name, "fictional": True}}
                  for i, (t, v) in enumerate(zip(titles, vectors, strict=False))]
        for i in range(0, len(points), 100):
            up = await self.client.put(f"{QDRANT_URL}/collections/{name}/points?wait=true",
                                       json={"points": points[i:i + 100]}, timeout=60)
            if up.status_code != 200:
                raise RuntimeError(f"qdrant upsert {name}: {up.status_code} {up.text[:200]}")
        self.collections[name] = len(points)
        log("info", "qdrant collection ready", collection=name, points=len(points), dim=dim)

    async def vector_search(self, uc: UseCase, agent: str, task_id: str, query: str) -> list[str]:
        name = uc.index or f"kb-{uc.id}"
        await self.ensure_collection(uc, agent, name)
        try:
            vector = (await self.embed(uc, agent, task_id, [query]))[0]
        except (httpx.HTTPError, RuntimeError, KeyError):
            vector = [rng.gauss(0, 1) for _ in range(384)]
        wall_start = time.time()
        started = time.perf_counter()
        resp = await self.client.post(f"{QDRANT_URL}/collections/{name}/points/query", timeout=10,
                                      json={"query": vector, "limit": 5, "with_payload": True})
        elapsed = time.perf_counter() - started
        COUNTERS.vector_ms.append(elapsed * 1000)
        if resp.status_code != 200:
            raise RuntimeError(f"qdrant query {name}: {resp.status_code}")
        hits = resp.json().get("result", {}).get("points", [])
        self.add_retrieval_span(uc, agent, task_id, name, wall_start, wall_start + elapsed, len(hits))
        return [str((h.get("payload") or {}).get("title", "")) for h in hits]

    async def feedback(self, uc: UseCase, task_id: str) -> None:
        if not self.langfuse_enabled:
            return
        if rng.random() >= FEEDBACK_SHARE:
            return
        value = 0 if rng.random() < uc.thumbs_down else 1
        try:
            resp = await self.client.post(
                f"{LANGFUSE_HOST}/api/public/scores", timeout=10,
                auth=(LANGFUSE_PUBLIC_KEY, LANGFUSE_SECRET_KEY),
                json={"traceId": task_id, "name": "user-feedback", "value": value,
                      "dataType": "BOOLEAN", "comment": "simulated user feedback"})
            if resp.status_code < 300:
                COUNTERS.feedback += 1
        except httpx.HTTPError:
            pass

    async def run_task(self, uc: UseCase) -> None:
        agent = rng.choice(uc.agents)
        task_id = uuid.uuid4().hex
        question = rng.choice(QUESTIONS)
        COUNTERS.tasks += 1
        try:
            if uc.task_type == "rag_qa":
                passages = await self.vector_search(uc, agent, task_id, question)
                context = "\n".join(passages[:3])
                await self.chat(uc, agent, task_id, "answer", uc.alias,
                                f"Context:\n{context}\n\nQuestion: {question}", 220, True)
            elif uc.task_type == "agent":
                plan = await self.chat(uc, agent, task_id, "plan", uc.alias,
                                       f"Plan in three steps: {question}", 60, True)
                for step in range(rng.randint(1, 2)):
                    passages = await self.vector_search(uc, agent, task_id, plan[:200] or question)
                    await self.chat(uc, agent, task_id, f"act-{step + 1}", uc.alias,
                                    f"Tool vector_search -> {passages[:2]}. Next step?",
                                    90, True)
                await self.chat(uc, agent, task_id, "final", uc.alias,
                                f"Final answer to: {question}", 200, True)
            elif uc.task_type in ("classification", "extraction", "routing"):
                # The configured alias is kept on purpose: a premium model on a simple task
                # is exactly what the FinOps "oversized_model" waste rule must detect.
                await self.chat(uc, agent, task_id, uc.task_type, uc.alias, question,
                                rng.randint(12, 60), True)
            else:  # summarization, generation and unknown types
                await self.chat(uc, agent, task_id, uc.task_type, uc.alias,
                                f"{question} Answer in one paragraph.", rng.randint(180, 320), True)
            await self.feedback(uc, task_id)
        except (httpx.HTTPError, RuntimeError, KeyError, ValueError) as exc:
            COUNTERS.task_errors += 1
            log("warn", "task failed", usecase=uc.id, agent=agent, task_type=uc.task_type,
                error=str(exc)[:200])


async def wait_ready(client: httpx.AsyncClient) -> None:
    while True:
        try:
            r1 = await client.get(f"{LITELLM_URL}/health/readiness", timeout=5)
            r2 = await client.get(f"{QDRANT_URL}/readyz", timeout=5)
            if r1.status_code == 200 and r2.status_code == 200:
                return
        except httpx.HTTPError:
            pass
        HEARTBEAT.touch()
        log("info", "waiting for litellm and qdrant")
        await asyncio.sleep(5)


async def span_loop(factory: Factory) -> None:
    while True:
        await asyncio.sleep(5)
        await factory.flush_spans()


async def summary_loop(usecases: list[UseCase]) -> None:
    while True:
        await asyncio.sleep(SUMMARY_EVERY_S)
        log("info", "traffic summary", window_s=SUMMARY_EVERY_S, tasks=COUNTERS.tasks,
            task_errors=COUNTERS.task_errors, calls=COUNTERS.calls,
            call_errors=COUNTERS.call_errors, by_alias=COUNTERS.by_alias,
            ttft_ms_p95_client=_p95(COUNTERS.ttft_ms), vector_ms_p95_client=_p95(COUNTERS.vector_ms),
            feedback_scores=COUNTERS.feedback, usecases=len(usecases))
        COUNTERS.reset()


async def main() -> None:
    usecases = load_usecases()
    weights = [u.weight for u in usecases]
    log("info", "traffic generator starting", usecases=[u.id for u in usecases],
        tasks_per_min=TASKS_PER_MIN, concurrency=CONCURRENCY)
    limits = httpx.Limits(max_connections=CONCURRENCY * 2 + 4, max_keepalive_connections=CONCURRENCY * 2)
    async with httpx.AsyncClient(limits=limits) as client:
        await wait_ready(client)
        factory = Factory(client)
        sem = asyncio.Semaphore(CONCURRENCY)
        # Strong references: asyncio keeps only weak references to running tasks.
        BACKGROUND_TASKS.extend([asyncio.create_task(summary_loop(usecases)),
                                 asyncio.create_task(span_loop(factory))])
        pending: set[asyncio.Task] = set()

        async def guarded(uc: UseCase) -> None:
            async with sem:
                await factory.run_task(uc)

        while True:
            HEARTBEAT.touch()
            uc = rng.choices(usecases, weights=weights, k=1)[0]
            if len(pending) < CONCURRENCY * 4:  # back-pressure when the gateway is slow
                task = asyncio.create_task(guarded(uc))
                pending.add(task)
                task.add_done_callback(pending.discard)
            # Poisson arrivals.
            await asyncio.sleep(rng.expovariate(TASKS_PER_MIN / 60.0))


if __name__ == "__main__":
    asyncio.run(main())
