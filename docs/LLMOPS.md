# LLMOps and RAG: pipeline, evaluation gates, rollback, data loop, placement

*[Version française](fr/LLMOPS.md)*

Engines: `api/app/engines/pipeline.py`, `dataloop.py`, `placement.py`, `agentops.py`.
Parameters: `config/factory.yaml` (`eval_gates`, `rollback`, `dataloop`, `placement`,
`agentops_slo`) and `config/usecases/*.yaml` (`rag`, `eval`). The engine **evaluates** the gates
and rules; it does not deploy anything itself. Stage details, rollback triggers, placement
reasons and data-loop details are returned in the requested language (`?lang=en|fr`); stage ids,
`blocked_by` reasons and action names never change.

> Principle: **no fine-tuning, no retraining.** The levers are the corpus, chunking, the index,
> the prompt, the agent, routing between published models, and evaluation.

## 1. The seven pipeline stages

| Stage | Content | Stack tools |
|---|---|---|
| `ingestion` | source collection, C1–C4 class check, pseudonymization when needed | MCP tools `tl-ocr-local`, `tl-pii-redact` |
| `indexing` | chunking (`chunk_size`, `chunk_overlap`), `embed-default` embeddings on the local GPU, hybrid index | Qdrant |
| `versioning` | linked versions of the prompt, the LangGraph agent, the index and the model (`model.version`) | Langfuse (prompts), Git |
| `eval_gate` | golden set, RAGAS, LLM-as-a-judge, red teaming; thresholds by AI Act tier | Langfuse (scores) |
| `deploy` | canary, then 100%; **blocked** if a risk reassessment is required, the assessment is rejected, a prohibited practice is declared or a gate failed | LiteLLM (aliases) |
| `feedback` | thumbs up / down, sampled online faithfulness, error rate | Langfuse, Prometheus |
| `rollback_guard` | **automatic** rollback to the previous version when a threshold is crossed | API, Prometheus alerts |

`blocked_by` gives the cause of a block: `risk_reassessment`, `eval_gate`,
`assessment_rejected` or `prohibited_practice`.

## 2. Evaluation gates (RAGAS + LLM-as-a-judge + red teaming)

| AI Act tier | faithfulness | answer_relevancy | context_precision | judge_score (/5) | redteam_pass |
|---|---|---|---|---|---|
| minimal | 0.75 | 0.75 | 0.70 | 3.5 | 0.90 |
| limited | 0.80 | 0.80 | 0.75 | 3.8 | 0.95 |
| high | 0.90 | 0.85 | 0.85 | 4.2 | 0.99 |

- **RAGAS**: `faithfulness` (answer grounded in the retrieved context), `answer_relevancy`
  (relevance to the question), `context_precision` (relevance of the retrieved passages). A
  missing metric fails the gate.
- **LLM-as-a-judge**: rubric scored out of 5, with a judge distinct from the evaluated model (the
  use case's `eval.judge_alias`), calibrated on a human-scored sample; for C3/C4 data the judge
  stays on the local GPU (`chat-sensitive`), which `validate_config.py` checks.
- **Red teaming**: pass rate of a campaign (direct and indirect prompt injection, exfiltration
  through MCP tools, jailbreaks), built from the risks of the NIST AI 600-1 profile.
- Thresholds get stricter as risk increases (checked by `validate_config.py`).

## 3. Automatic rollback

After deployment, the guard compares usage feedback with the `rollback` thresholds:

| Trigger | Default threshold |
|---|---|
| `thumbs_down_ratio` (thumbs-down rate / pre-deployment baseline) | > 2.0 |
| `online_faithfulness_drop` (drop in faithfulness measured online) | > 0.08 |
| `error_rate` | > 5% |

The first threshold crossed triggers a rollback to `previous_version`, logged
(`pipeline.rollbacks`, `factory_rollbacks_total{usecase}`) with the value and the threshold.

## 4. Data loop: act without retraining

| Signal | Bad when | Action |
|---|---|---|
| `retrieval_hit_rate` | < 0.75 | `reindex` (re-chunking, re-embedding) |
| `corpus_freshness` | > 10% of documents past their freshness window | incremental `reindex` |
| `query_drift` | distance of the query centroid > 0.25 | `update_eval_set` |
| `eval_failure_cluster` | > 5 failures on the same topic | `update_eval_set` |
| `cost_drift` | cost per request > +20% | `change_routing` |
| `latency_drift` | p95 latency > +20% | `change_routing` |

Guardrails: a signal at ≥ 2× its threshold, or a routing change on a high-risk AI system, yields
`human_review`. The allowed actions are exactly `reindex`, `update_eval_set`, `change_routing`,
`human_review`; `retrain`, `fine_tune`, `lora`, `rlhf`, `distill`… are rejected by the API
(`ForbiddenActionError`) whatever the configuration. A routing change that changes a use case's
model changes its version: it triggers the risk reassessment ([GOVERNANCE.md](GOVERNANCE.md)).

## 5. Hybrid placement: cloud first, then VPS, then local hardware

| Target | Service | Use |
|---|---|---|
| `cloud` | managed API through LiteLLM | preferred for C1/C2 data |
| `vps` | Ollama on CPU (`vps` profile), models ≤ `vps_max_params_b` (8 B) | fallback, simple tasks |
| `local` | vLLM on the local GPU (`local-gpu` profile), DCGM telemetry to Prometheus and the 3D rack | **only** target for C3/C4 data |

Engine rules (`POST /api/v1/placement/decide` for a dry run):

1. Class C3 or C4 ⇒ local GPU only, alias `chat-sensitive`. If the GPU is unavailable,
   the dry run returns `queued` as a recommendation to wait. Direct requests fail
   without an external fallback or automatic retry.
2. Otherwise, order `cloud > vps > local`, skipping unavailable targets, and skipping the VPS if
   the required model exceeds 8 B (size inferred from the catalog or the task type); a degraded
   target is only chosen as a last resort.

LiteLLM aliases: `chat-default` (cloud, fallbacks VPS then local), `chat-small` (VPS first),
`chat-sensitive` (local only), `embed-default` (local only, since corpora may contain C3/C4).

## 6. Agent Ops room

SLOs (`agentops_slo`): time to first token **TTFT p95 ≤ 1,500 ms**, **tokens per task
p95 ≤ 12,000**, **vector latency p95 ≤ 120 ms**. Per agent: TTFT, tokens per task, vector
latency, success rate, cost per task and breached SLOs (`/api/v1/agentops`, metrics
`factory_agent_ttft_seconds`, `factory_agent_tokens_per_task`, `factory_vector_latency_seconds`).
The operators at consoles O1 and O2 have the agents' kill switch and the human escalations
defined in the catalog.
