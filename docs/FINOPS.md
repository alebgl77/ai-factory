# FinOps: token and GPU costs, showback and chargeback, waste alerts

*[Version française](fr/FINOPS.md)*

The engine `api/app/engines/costs.py` computes costs per use case and per cost center, internal
chargeback and waste alerts. Parameters: `config/factory.yaml` (`cost_centers`, `rates`,
`chargeback`, `waste_rules`). All the rates and budgets provided are **fictional**. Amounts are
in euros (EUR); alert messages and actions are returned in the requested language
(`?lang=en|fr`), with unchanged rule ids and numbers.

## 1. Collection and attribution

- Every call goes through the **LiteLLM** gateway with `metadata.usecase` (and
  `metadata.agent`): this is the attribution key for all costs.
- The MIT callback `compose/litellm/factory_callbacks.py` exposes in Prometheus
  `factory_llm_tokens_total{model,usecase,direction}`, `factory_llm_cost_eur_total{model,usecase}`
  and `factory_llm_requests_total{…,status}`; Langfuse keeps the traces (prompt, version).
- When Prometheus provides no usable cost and usage data, the API can read LiteLLM spend logs.
  Set `FACTORY_COST_FX` to the EUR multiplier for the gateway's price unit. Both paths apply
  the same configured rate once. The default `1.0` matches the supplied fictional EUR prices.
- Local GPU telemetry (DCGM or `gpu-sim`) provides utilization and power.
- The API republishes the aggregates: `factory_usecase_cost_eur{usecase,cost_center,kind}`,
  `factory_usecase_tokens_total`, `factory_waste_alerts{rule,severity}`.

Without a live source (`FACTORY_DEMO_MODE=auto`), a deterministic generator produces 30 days of
fictional history calibrated on the configured budgets.

## 2. Cost model

| Item | Formula | Parameters |
|---|---|---|
| LLM tokens | `tokens_in × input_price + tokens_out × output_price` (price per 1,000 tokens), cached input discounted by 90% | `catalog/models.yaml` (`price_in_per_1k`, `price_out_per_1k`), aligned with the LiteLLM configuration |
| Evaluations | cost of RAGAS and LLM-as-a-judge calls, counted in the LLM cost and tracked separately (`eval_eur`) | — |
| Local GPU | `hours × (capex / amortization months / 720 + power kW × kWh price)` | `rates.local_gpu`: €4,500 / 36 months / 240 W / €0.21/kWh ⇒ **€0.224/h** |
| Cloud GPU | `hours × hourly rate` (reindexing batches, offline evaluations) | `rates.cloud_gpu_eur_per_hour` |
| Shared platform | `platform_overhead_pct` of direct costs + VPS flat fee + amortization of the **unused** local GPU | `rates.platform_overhead_pct`, `rates.vps_eur_per_month` |

The VPS and the local GPU have no marginal price per token: their fixed cost is spread through
the platform (VPS, idle GPU) or charged per GPU hour consumed (active local GPU). All
calculations use integer cents: the sum of the lines always equals the total.

## 3. Showback or chargeback

| | Showback (default) | Chargeback |
|---|---|---|
| Goal | make costs visible, without billing | recharge each cost center |
| Direct costs (tokens, GPU) | shown per use case and cost center | billed to the use case's cost center |
| Shared platform | shown, **not allocated** (`unallocated_platform_eur`) | allocated pro rata by `tokens`, `requests` or `equal` |
| Invoice lines | informative | `billable: true` per cost center |

The default mode is set in `chargeback.mode`; the dashboard switches with
`GET /api/v1/costs?days=30&mode=chargeback`. Monthly forecast of a cost center: the run rate of
the last 7 days × 30 (plus the platform share in chargeback). Recommendation: start with
showback to get the figures accepted, and move to chargeback once attribution through
`metadata.usecase` is reliable.

## 4. The 8 waste rules

Configurable thresholds (`waste_rules`); a rule missing from the configuration is disabled. Each
alert gives an estimated monthly saving and an action.

| Rule | Trigger (default values) | Estimated saving | Proposed action |
|---|---|---|---|
| `oversized_model` | `premium` model on a simple task (`classification`, `extraction`, `routing`) | gap with the cheapest non-premium model allowed for the data class | route to that model (e.g. `chat-small`), validate on the golden set |
| `low_cache_hit` | repeated requests ≥ 40% and cache hit rate ≤ 20% | share of repeated input not served by the cache | prompt cache or semantic cache in the gateway |
| `retry_storm` | retries > 5% of requests (critical at twice that) | cost of retries beyond the threshold | exponential backoff, circuit breaker, fix the tool schemas |
| `prompt_bloat` | average prompt size +30% over the period | added input tokens | compress the context, reduce the RAG top-k, version the prompt |
| `idle_gpu` | local GPU < 10% utilization for ≥ 4 consecutive hours | energy consumed while idle | schedule reindexing and evaluations in these slots, or standby |
| `orphan_index` | vector index with no query for > 14 days or no active use case | monthly cost of the index | archive the snapshot, then delete the collection |
| `eval_overspend` | evaluations > 25% of the use case's LLM cost | overspend beyond 25% | sample the evaluation set, use a smaller judge |
| `budget_breach` | spend or forecast ≥ 90% of the cost center's budget (critical at 100%) | forecast overrun | apply the proposed savings or revise the budget |

In the fictional demo: uc-03 (classification on the premium model) triggers `oversized_model`
and `eval_overspend`, uc-01 `low_cache_hit`, uc-02 `retry_storm`, uc-06 `prompt_bloat`, the local
GPU `idle_gpu` at night, old indexes `orphan_index`. The "Customer relations and marketing" cost
center is run close to its budget: `budget_breach` appears when the platform share is charged to
it (chargeback). The remedies stay within the scope of the factory: routing, cache, context,
scheduling. **Never fine-tuning.**

## 5. Budgets and cost governance

- One monthly budget per cost center (`cost_centers[].budget_month_eur`), fictional.
- Alerts feed the dashboard's FinOps console, Prometheus and the provisioned Grafana dashboards
  (`compose/grafana/dashboards/finops.json`).
- Any routing action on a high-risk AI system goes through a human review (see
  [LLMOPS.md](LLMOPS.md), data loop) and, if the model changes, through a risk reassessment
  ([GOVERNANCE.md](GOVERNANCE.md)).
