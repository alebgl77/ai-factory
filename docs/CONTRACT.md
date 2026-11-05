# Interface contract (single source of truth)

This file fixes the names, schemas and rules shared by the dashboard, the aggregation
API, the configuration and the infrastructure. Any change must be reflected here first.

Principles:

- Generic Virtual AI Factory, MIT. **No real data**: every person, client, cost centre and
  use case in `config/` is fictional and configurable.
- **No fine-tuning, no retraining.** `factory.fine_tuning` is `false` and the data loop may only
  reindex, update evaluation sets, change routing or open a human review.
- Cloud first, then VPS, then local hardware. Sensitive data classes are processed **only** on
  the local GPU.
- AGPL services (Grafana, Loki) run as separate, unmodified containers. The repository never
  vendors or links their code. The browser never holds a Grafana token.

## 1. Repository layout and ownership

| Path | Content |
|---|---|
| `api/` | FastAPI aggregation API (Python 3.12), engines, connectors, demo generator, security controls, pytest suite; `requirements*.in` and their hash-pinned locks `requirements*.txt` |
| `config/` | GitOps definition of the factory: `factory.yaml`, `profiles.yaml`, `skills.yaml`, `usecases/*.yaml`, `catalog/*.yaml`, `catalog/github/*.yaml`, `frameworks/*.yaml` |
| `dashboard/` | Static 3D dashboard (`index.html`), nginx Dockerfile that vendors Three.js 0.186.1 |
| `compose/` | Configuration of every service in `docker-compose.yml` (including `compose/litellm/tests/`, the SSO end-to-end test `compose/sso/`, the edge identity-header and CSP test `compose/caddy/tests/` and the alert unit tests `compose/prometheus/tests/`) |
| `sim/` | Simulators for a key-less demo: `mock-llm` (OpenAI-compatible), `traffic` (load generator), `gpu-sim` (DCGM-compatible exporter) |
| `integrations/` | Git submodules of external projects (no copied code) |
| `scripts/` | `build_snapshot.py`, `check_licenses.py`, `validate_config.py`, `build_assets.py`, `scan_secrets.sh`, `smoke_stack.sh`, `inventory_github.py`, `sbom.sh` |
| `.github/` | CI/CD and supply chain: workflows `ci`, `codeql`, `scorecard`, `security`, `release`, `pages`; `dependabot.yml`; hash-pinned CI tools (`tools/`), Trivy policy and accepted risks (`trivy/`), release scripts (`scripts/`) |
| `docs/` | Architecture, governance crosswalk, FinOps, LLMOps, licences, security (`SECURITY.md`, `THREAT_MODEL.md`, `SECURITY_CONTROLS.md`), GitHub integration report |
| `SECURITY.md` (root) | Security policy: supported versions, private vulnerability reporting, response targets (72 h / 7 days), scope, safe harbour, release verification (English then French) |
| `tests/e2e/` | Playwright tests of the dashboard |

## 2. Configuration schemas (`config/`)

All files are YAML. Unknown keys are ignored. Dates are ISO `YYYY-MM-DD`. Money is EUR.

### 2.1 `config/factory.yaml`

```yaml
factory:
  name: "Virtual AI Factory"
  tagline: "Usine IA générique — démo fictive"
  currency: EUR
  demo: true
  fine_tuning: false          # hard guard, the API refuses any retrain/fine-tune action
  timezone: Europe/Paris
regulatory:                   # dates are configuration, not code (check the Official Journal)
  ai_act:
    regulation: "Regulation (EU) 2024/1689"
    amended_by: "Regulation (EU) 2026/1744 (Digital Omnibus on AI)"
    milestones:
      - { id: prohibitions, date: 2025-02-02, label: "Art. 5 pratiques interdites" }
      - { id: literacy,     date: 2025-02-02, label: "Art. 4 maîtrise de l'IA (réécrit par 2026/1744)" }
      - { id: gpai,         date: 2025-08-02, label: "Chapitre V modèles GPAI" }
      - { id: transparency, date: 2026-08-02, label: "Art. 50 transparence" }
      - { id: annex_iii,    date: 2027-12-02, label: "Haut risque annexe III" }
      - { id: annex_i,      date: 2028-08-02, label: "Haut risque annexe I" }
cost_centers:
  - { id: cc-ops, name: "Opérations (fictif)", budget_month_eur: 1800 }
rates:
  local_gpu: { capex_eur: 4500, amortization_months: 36, power_w: 240, kwh_eur: 0.21 }
  cloud_gpu_eur_per_hour: { h100: 2.9, l4: 0.7 }
  vps_eur_per_month: 40
  platform_overhead_pct: 12   # shared platform cost spread over use cases in chargeback mode
chargeback:
  mode: showback              # showback | chargeback
  shared_allocation: tokens   # tokens | equal | requests
waste_rules:
  oversized_model:  { simple_task_types: [classification, extraction, routing], premium_tier: premium }
  low_cache_hit:    { min_repeat_ratio: 0.4, max_cache_hit: 0.2 }
  retry_storm:      { max_retry_ratio: 0.05 }
  prompt_bloat:     { max_growth_pct: 30 }
  idle_gpu:         { max_util_pct: 10, min_hours: 4 }
  orphan_index:     { max_idle_days: 14 }
  eval_overspend:   { max_share_pct: 25 }
  budget_breach:    { warn_pct: 90 }
placement:
  order: [cloud, vps, local]  # preference for non-sensitive workloads
  sensitive_classes: [C3, C4] # local only, never cloud or VPS
  vps_max_params_b: 8         # biggest model the VPS can serve (CPU)
agentops_slo: { ttft_ms_p95: 1500, vector_latency_ms_p95: 120, tokens_per_task_p95: 12000 }
eval_gates:                   # thresholds by AI Act tier (stricter when risk is higher)
  minimal: { faithfulness: 0.75, answer_relevancy: 0.75, context_precision: 0.70, judge_score: 3.5, redteam_pass: 0.90 }
  limited: { faithfulness: 0.80, answer_relevancy: 0.80, context_precision: 0.75, judge_score: 3.8, redteam_pass: 0.95 }
  high:    { faithfulness: 0.90, answer_relevancy: 0.85, context_precision: 0.85, judge_score: 4.2, redteam_pass: 0.99 }
rollback:
  thumbs_down_ratio_max: 2.0  # online thumbs-down rate vs pre-deploy baseline
  online_faithfulness_drop_max: 0.08
  error_rate_max: 0.05
```

### 2.2 `config/profiles.yaml` (fictional, configurable count)

```yaml
roles:
  - { id: head, label: "Head of AI Factory", literacy_level: expert }
people:
  - id: p01
    name: "Camille Laurent"      # fictional
    role: head
    color: "#7c3aed"             # avatar colour
    skin: "#f1c9a5"
    desk: D1                      # home desk (see 3D resources)
    locker: 1
    skills: [Cadrage, Gouvernance]
    squads: [uc-05, uc-06]        # use case ids
    cost_center: cc-ops
    pattern: [1, 1, 0, 1, 1]      # demo presence Mon..Fri
```

Role ids used by the reference profile: `head`, `po`, `architect`, `llm_engineer`,
`data_engineer`, `eval_scientist`, `llmops`, `governance`, `agent_ops`, `research`, `enablement`.

### 2.3 `config/usecases/<id>.yaml` (registry)

```yaml
id: uc-01
code: UC-01
name: "Assistant RAG documentaire (fictif)"
description: "..."
owner: p02                        # person id
cost_center: cc-ops
stage: mvp                        # cadrage | poc | mvp | indus | run | retired
task_type: rag_qa                 # rag_qa | agent | classification | extraction | routing | generation | summarization
sensitivity: C3                   # data class C1 (public) .. C4 (secret)
color: "#0072b2"                  # Okabe-Ito palette, used by the 3D 'use case' lens
ai_act:
  role: deployer                  # provider | deployer | both
  prohibited_practices: []        # Art. 5 flags; non-empty => tier 'prohibited'
  annex_iii_area: null            # biometrics | critical_infrastructure | education | employment |
                                  # essential_services | law_enforcement | migration_border | justice_democracy
  annex_iii_use: null             # free text: the Annex III point concerned
  art6_3_exemption: false         # documented Art. 6(3) derogation (narrow procedural task, etc.)
  annex_i_safety_component: false
  transparency: [chatbot]         # Art. 50 duties: chatbot | synthetic_content | emotion_recognition | deepfake
  gpai_used: true
model:
  alias: chat-default             # LiteLLM model alias (chat-default | chat-sensitive | chat-small | embed-default)
  name: "cloud/mistral-large"     # display name of the current model
  version: "2026-09"
  history:
    - { version: "2026-06", from: 2026-06-10 }
    - { version: "2026-09", from: 2026-09-15 }
assessment:                       # latest risk assessment
  reviewed_model_version: "2026-06"   # differs from model.version => re-evaluation required
  last_reviewed: 2026-06-12
  reviewer: p07
  decision: approved              # approved | approved_with_conditions | rejected | pending
nist:                             # NIST AI RMF 1.0 functions, status done | partial | todo
  govern:  { status: done,    evidence: ["Politique IA v2", "RACI"] }
  map:     { status: done,    evidence: ["Fiche contexte", "Analyse d'impact"] }
  measure: { status: partial, evidence: ["Golden set 120 Q/R"] }
  manage:  { status: todo,    evidence: [] }
iso42001: ["A.5.2", "A.6.2.4", "A.7.4"]   # Annex A controls implemented
rag: { index: kb-docs, corpus_docs: 1840, chunk_size: 800, chunk_overlap: 120, embedding: embed-default }
kpis: { adoption_target: 0.6, value_metric: "Temps de recherche documentaire", value_eur_month: 4200 }
```

### 2.4 Risk engine rules (`api/app/engines/risk.py`)

- `tier`: `prohibited` if `prohibited_practices` non-empty; else `high` if (`annex_iii_area` set and
  not `art6_3_exemption`) or `annex_i_safety_component`; else `limited` if `transparency` non-empty;
  else `minimal`. `gpai` is an orthogonal flag.
- `applicable_from`: the milestone date that governs the tier (`annex_iii`, `annex_i`, `transparency`,
  `prohibitions`; `minimal` -> `literacy`), plus `days_left` from today.
- `reevaluation_required`: `model.version != assessment.reviewed_model_version`, or decision `pending`.
  When true the deploy gate of the pipeline is **blocked** with reason `risk_reassessment`.
- `required_controls`: list of crosswalk entries (section 2.6) whose `tiers` contain the tier.
- `nist_progress`: share of the 4 functions with `done` (partial counts 0.5).
- `POST /api/v1/registry/{id}/model-version {version, name?}` appends to history, sets the new
  version, and therefore flips `reevaluation_required` to true (the response says so).
- `POST /api/v1/registry/{id}/assessment {decision, reviewer}` records a review of the current
  version (clears the flag when `decision` is `approved*`).
- With authentication on, an approving `POST /assessment` by the identity that recorded the
  current model version through the API is refused with 403 `separation_of_duties` (four-eyes).
  The assessment records `reviewed_by`, `reviewed_by_role` and `separation_of_duties`
  (`verified | unknown_recorder | not_required | not_enforced`).

### 2.5 `config/catalog/{models,agents,tools}.yaml`

```yaml
models:
  - { id: cloud-large, alias: chat-default, provider: "cloud (fictif)", hosting: cloud, license: proprietary,
      params_b: null, context_k: 128, price_in_per_1k: 0.002, price_out_per_1k: 0.006, tier: premium,
      allowed_classes: [C1, C2], eval: { faithfulness: 0.91, judge_score: 4.3 } }
agents:
  - { id: ag-research, name: "Agent de recherche documentaire", framework: LangGraph, version: "1.4.0",
      owner: p03, usecase: uc-01, tools: [tl-search, tl-qdrant], risk_tier: limited, eval_status: passed }
tools:
  - { id: tl-qdrant, name: "Recherche vectorielle", kind: mcp, permissions: [read], risk: low, owner: p08 }
```

`config/catalog/github/<repo>.yaml` (external projects, integrated as submodules):

```yaml
id: gh-grafana-llmops-forge
name: grafana-llmops-forge
url: https://github.com/alebgl77/grafana-llmops-forge
pillar: llmops_observability      # rag | evaluation | multi_agents | llmops_observability | governance_security
license: MIT
status: integrated                # integrated | to_relicense | excluded
submodule: integrations/grafana-llmops-forge
reason: "..."
secrets_scan: clean               # clean | findings | not_scanned
copyleft_deps: []
```

### 2.6 `config/frameworks/crosswalk.yaml`

One entry per control family, mapping the three frameworks. The mapping is **indicative**
(the ISO text is not public); each entry cites its basis.

```yaml
controls:
  - id: risk-management
    title: "Gestion des risques sur le cycle de vie"
    nist: ["GOVERN 1.3", "MAP 1.1", "MEASURE 1.1", "MANAGE 1.1"]
    ai_act: ["Art. 9"]
    iso42001: ["6.1", "A.5.2"]
    tiers: [high]
    evidence_hint: "Registre des risques, revue à chaque version de modèle"
```

`config/frameworks/nist_ai_rmf.yaml`, `ai_act.yaml` (articles + Annex III areas), `iso42001.yaml`
(clauses 4-10 and Annex A objectives) hold labels for display.

### 2.7 `config/skills.yaml`

```yaml
modules:
  - { id: m-literacy-101, title: "Socle maîtrise de l'IA", hours: 3, format: lab,   art4: true }
  - { id: m-no-ai-kata,   title: "Kata sans IA (anti-déqualification)", hours: 2, format: kata, art4: true }
requirements:                     # role -> modules required (Art. 4: measures adapted to role, risk and context)
  head: [m-literacy-101, m-governance]
completions:                      # fictional
  - { person: p01, module: m-literacy-101, date: 2026-03-04 }
sessions:                         # Skills Lab room bookings (resource S1)
  - { id: s-2026-11-05, module: m-redteam, date: 2026-11-05, start: "14:00", end: "16:00", trainer: p11, attendees: [p03, p04] }
rnd:
  experiments:
    - { id: x-01, title: "Routage adaptatif petits modèles", stage: prototype, owner: p10,
        hypothesis: "...", gpu_hours: 38, linked_usecase: uc-02, transfer_criteria: "..." }
```

R&D stages: `explore | prototype | evaluate | transfer | stopped`.

## 3. Aggregation API (`api/`, port 8000, prefix `/api/v1`)

Python 3.12, FastAPI, pydantic v2, httpx, PyYAML, prometheus-client. Only permissive licences.

Environment (all optional, see `.env.example`):

| Variable | Meaning |
|---|---|
| `FACTORY_CONFIG_DIR` | path to `config/` (default `/config`) |
| `FACTORY_DEMO_MODE` | `auto` (default: live sources when reachable, demo otherwise), `always`, `never` |
| `FACTORY_DEFAULT_LANG` | default language of the answers without `?lang=` (`en` or `fr`, default `en`, section 9) |
| `FACTORY_DEMO_SEED` | integer seed of the deterministic demo generator (default 42) |
| `FACTORY_STATE_DIR` | where writable state is kept (registry changes, spaces bookings); default `/data` |
| `PROMETHEUS_URL` | e.g. `http://prometheus:9090` |
| `GRAFANA_URL` | e.g. `http://grafana:3000` |
| `GRAFANA_TOKEN_FILE` | file holding a Viewer service-account token (written by `grafana-bootstrap`) |
| `GRAFANA_PROM_DS_UID` | UID of the provisioned Prometheus datasource (`prometheus`) |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Langfuse public API |
| `LITELLM_URL`, `LITELLM_MASTER_KEY` | gateway (server side only) |
| `QDRANT_URL` | vector DB |
| `GRAFANA_PUBLIC_URL` | prefix of the dashboard links returned to the browser (path or absolute URL, e.g. `http://grafana.localhost`) |
| `FACTORY_DEMO_DATE` | fixed anchor date of the demo (`YYYY-MM-DD`, default: today Europe/Paris) |
| `FACTORY_CORS_ORIGINS` | comma-separated allowed origins (default: none, same origin only) |
| `FACTORY_MAX_BODY_BYTES` | request body limit (default 65536) |
| `FACTORY_AUTH_MODE` | `none` (default, demo: no authentication, every request acts as role `admin`, flagged in `/health` and `meta.security`) or `proxy` (identity from `X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups`, set only by the edge and read only from `FACTORY_TRUSTED_PROXIES`); any other value fails closed as `proxy` |
| `FACTORY_TRUSTED_PROXIES` | comma-separated CIDRs allowed to assert an identity and `X-Forwarded-For` (`0.0.0.0/0` and `::/0` refused); docker-compose.yml default: the fixed edge address `${FACTORY_EDGE_IDENTITY_IP:-10.89.255.2}/32` on the `edge-identity` network |
| `FACTORY_ROLE_MAP` | comma-separated `group:role` items; roles `viewer`, `editor`, `maintainer`, `approver`, `admin` |
| `FACTORY_DEFAULT_ROLE` | role of an authenticated user without mapped group (default: none, so 403) |
| `FACTORY_RATE_LIMIT_PER_MIN`, `FACTORY_RATE_LIMIT_BURST` | write rate limit per actor, or per client IP without authentication (default 60 and 20; 0 disables) |
| `FACTORY_CONNECTOR_TIMEOUT_S`, `FACTORY_CACHE_TTL_S` | connector timeout and TTL of raw telemetry and assembled snapshots |
| `FACTORY_COST_FX` | positive finite rate from LiteLLM price unit to EUR, shared by gateway metrics and API spend fallback |

Endpoints (JSON, read endpoints are GET):

| Path | Content |
|---|---|
| `/api/v1/health` | `{status, mode, sources:{prometheus,grafana,langfuse,litellm,qdrant}: bool, security:{...} (same object as meta.security)}`; public (probes) |
| `/api/v1/snapshot` | everything the dashboard needs, schema in section 4 |
| `/api/v1/registry` and `/api/v1/registry/{id}` | evaluated use cases (risk tier, flags, controls) |
| `POST /api/v1/registry/{id}/model-version` | see 2.4 |
| `POST /api/v1/registry/{id}/assessment` | see 2.4 |
| `/api/v1/costs?days=30` | FinOps: per use case, per cost centre, alerts, mode |
| `/api/v1/pipeline` | LLMOps/RAG pipeline state per use case version, gates, rollbacks |
| `/api/v1/dataloop` | drift signals and the action taken |
| `/api/v1/placement` and `POST /api/v1/placement/decide` | hybrid placement decisions |
| `/api/v1/agentops` | TTFT, tokens per task, vector latency, per agent |
| `/api/v1/catalog` | models, agents, tools, repos |
| `/api/v1/skills` | Art. 4 coverage, modules, sessions, R&D experiments |
| `/api/v1/gpu` | local GPU telemetry (DCGM via Prometheus/Grafana, else simulated) |
| `/api/v1/grafana/dashboards` | provisioned dashboards (title, uid, url) through the server-side token |
| `/api/v1/grafana/metric/{name}` | named, allow-listed PromQL queries executed through Grafana's datasource proxy |
| `GET/PUT /api/v1/spaces/state` | optional server persistence of the 3D bookings: GET `{version, updated_at, state}`, PUT `{version, state}`; a stale `version` returns 409 `{detail, current}` |
| `/api/v1/audit?offset=0&limit=100&action=` | hash-chained audit records, newest first: `{total, offset, limit, order, records}` (permission `audit.read`: approver, admin) |
| `/api/v1/audit/verify` | `{ok, chain_ok, records, head_hash, first_broken: {line, seq, reason}\|null, counts, state: {consistent, drift, unaudited}, algorithm: "sha256-chain", verified_at}` (`audit.read`) |
| `/api/v1/whoami` | `{actor, authenticated, auth_mode, roles, permissions, groups, request_id}` (any authenticated identity) |
| `/api/v1/docs`, `/api/v1/openapi.json` | OpenAPI page (Swagger UI 5.34.0 loaded from jsDelivr with Subresource Integrity and a per-answer CSP nonce) and schema (permission `read`) |
| `/metrics` | Prometheus exposition of factory KPIs (section 5) |

Security rules: no endpoint returns a secret; the Grafana proxy only issues GET to an
allow-list of Grafana paths and only runs named queries; request bodies are size-limited;
CORS is closed by default (same origin through the edge proxy).
Permissions: `read` (viewer and above, every GET except `/health`, which stays public),
`spaces.write` and `placement.decide` (editor), `registry.model_version` (maintainer),
`registry.assessment` and `audit.read` (approver); `admin` has all. Refusals are `{detail, code}`
with `code` in `untrusted_source | unauthenticated | invalid_identity | forbidden |
separation_of_duties | rate_limited` (401/403/429, 429 with `Retry-After`). Every write appends a
SHA-256-chained record to `FACTORY_STATE_DIR/audit.jsonl` (fields v, seq, ts, actor, role,
auth_mode, action, target, outcome, before_hash, after_hash, request_id, client_ip, details,
prev_hash, hash); a write whose record cannot be stored is rolled back (503). Answers carry
`X-Request-ID`, `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src 'none';
frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `Referrer-Policy: no-referrer`,
`Cross-Origin-Resource-Policy: same-origin` and, under `/api/`, `Cache-Control: no-store`.
The edge never applies the dashboard CSP to `/api/*` (the API's own policy is the only one) and
removes client-supplied identity headers on every route (section 6).

## 4. Snapshot schema (`GET /api/v1/snapshot`)

When collection yields live data, `meta.telemetry_collected_at` gives the ISO timestamp at the
start of that collection, shared across languages and cost modes for the same date window.
It measures the age of the retrieval, while source timestamps describe the age of individual
measurements. `meta.generated_at` dates snapshot assembly and `meta.sections` gives the origin
of each section. A snapshot with only demo or unavailable telemetry omits the collection time.

```jsonc
{
  "meta": { "mode": "demo|live|mixed", "generated_at": "2026-11-03T10:12:00+01:00", "seed": 42,
            "sources": { "prometheus": false, "grafana": false, "langfuse": false, "litellm": false, "qdrant": false },
            "fine_tuning": false, "version": "1.0.0",
            "security": { "status": "demo|enforced|degraded", "auth_mode": "none|proxy",
                          "authentication": "disabled|sso_proxy", "rbac": "not_enforced|enforced",
                          "roles": ["viewer","editor","maintainer","approver","admin"], "trusted_proxies": 0,
                          "separation_of_duties": "not_enforced|enforced",
                          "audit": { "enabled": true, "algorithm": "sha256-chain", "available": true, "chain_ok": true,
                                     "records": 0, "head_hash": "<64 hex>", "first_broken": null },
                          "rate_limit": { "enabled": true, "writes_per_minute": 60, "burst": 20 },
                          "body_limit_bytes": 65536, "cors": "closed|allow_list", "security_headers": true,
                          "warnings": ["auth_disabled"] } },
  "factory": { "name": "...", "tagline": "...", "currency": "EUR", "regulatory": { /* 2.1 */ } },
  "profiles": { "roles": [ ... ], "people": [ ... ] },
  "registry": [ { "id": "uc-01", "code": "UC-01", "name": "...", "stage": "mvp", "sensitivity": "C3",
                  "sensitive": true, "color": "#0072b2", "owner": "p02", "cost_center": "cc-ops",
                  "task_type": "rag_qa",
                  "risk": { "tier": "limited", "gpai": true, "annex_iii_area": null,
                            "applicable_from": "2026-08-02", "days_left": -93,
                            "reevaluation_required": true, "reason": "model 2026-09 not reviewed (last 2026-06)",
                            "required_controls": ["transparency", "..."], "nist_progress": 0.62,
                            "nist": { "govern": "done", "map": "done", "measure": "partial", "manage": "todo" },
                            "iso42001": ["A.5.2"] },
                  "model": { "alias": "chat-default", "name": "...", "version": "2026-09", "history": [ ... ] },
                  "squad": ["p02", "p03"], "kpis": { ... } } ],
  "costs": { "days": 30, "mode": "showback",
             "totals": { "tokens_in": 0, "tokens_out": 0, "llm_eur": 0, "gpu_eur": 0, "platform_eur": 0, "total_eur": 0 },
             "by_usecase": [ { "usecase": "uc-01", "tokens_in": 0, "tokens_out": 0, "cached_ratio": 0.31,
                               "requests": 0, "retry_ratio": 0.01, "llm_eur": 0, "gpu_hours": 0, "gpu_eur": 0,
                               "platform_eur": 0, "total_eur": 0, "eur_per_request": 0, "trend_pct": 0,
                               "daily": [ { "date": "2026-10-05", "eur": 0 } ] } ],
             "by_cost_center": [ { "cost_center": "cc-ops", "name": "...", "budget_eur": 0, "spent_eur": 0,
                                   "forecast_eur": 0, "invoice_lines": [ { "usecase": "uc-01", "eur": 0 } ] } ],
             "alerts": [ { "id": "w-01", "rule": "oversized_model", "usecase": "uc-03", "severity": "warn|crit",
                           "message": "...", "saving_eur_month": 0, "action": "..." } ] },
  "pipeline": { "stages": ["ingestion","indexing","versioning","eval_gate","deploy","feedback","rollback_guard"],
                "items": [ { "usecase": "uc-01", "version": "rag-1.8.0", "prompt_version": "p-12", "agent_version": "1.4.0",
                             "stages": [ { "key": "eval_gate", "status": "passed|failed|blocked|running|pending|skipped",
                                           "at": "...", "detail": "...",
                                           "metrics": { "faithfulness": 0.91, "answer_relevancy": 0.88,
                                                        "context_precision": 0.84, "judge_score": 4.1, "redteam_pass": 0.97 },
                                           "thresholds": { ... } } ],
                             "blocked_by": null } ],
                "rollbacks": [ { "at": "...", "usecase": "uc-02", "from": "1.3.0", "to": "1.2.4",
                                 "trigger": "thumbs_down_ratio", "value": 2.6, "threshold": 2.0, "auto": true } ] },
  "dataloop": { "events": [ { "at": "...", "usecase": "uc-01", "signal": "retrieval_hit_rate|query_drift|corpus_freshness|eval_failure_cluster|cost_drift|latency_drift",
                              "value": 0.0, "threshold": 0.0,
                              "action": "reindex|update_eval_set|change_routing|human_review",
                              "status": "done|running|proposed", "detail": "..." } ],
                "retraining": "disabled" },
  "placement": { "targets": [ { "id": "cloud|vps|local", "label": "...", "status": "up|degraded|down",
                                "util_pct": 0, "eur_per_1k_tokens": 0, "latency_ms_p95": 0 } ],
                 "workloads": [ { "id": "wl-01", "usecase": "uc-01", "sensitivity": "C3", "target": "local",
                                  "fallback": [], "reasons": ["C3 => local uniquement"], "model_alias": "chat-sensitive" } ] },
                 // when the local GPU is down a C3/C4 workload gets target "queued" (queued: true, fallback: []): never cloud or VPS
  "agentops": { "window": "1h",
                "slo": { "ttft_ms_p95": 1500, "vector_latency_ms_p95": 120, "tokens_per_task_p95": 12000 },
                "global": { "ttft_ms_p50": 0, "ttft_ms_p95": 0, "tokens_per_task_avg": 0, "tokens_per_task_p95": 0,
                            "vector_latency_ms_p50": 0, "vector_latency_ms_p95": 0, "tasks": 0, "success_rate": 0 },
                "by_agent": [ { "agent": "ag-research", "usecase": "uc-01", "ttft_ms_p95": 0, "tokens_per_task_p95": 0,
                                "vector_latency_ms_p95": 0, "success_rate": 0, "tasks": 0, "eur_per_task": 0, "breaches": [] } ],
                "series": [ { "t": "...", "ttft_ms_p95": 0, "vector_latency_ms_p95": 0, "tokens_per_task_avg": 0 } ] },
  "catalog": { "models": [ ... ], "agents": [ ... ], "tools": [ ... ], "repos": [ ... ] },
  "skills": { "modules": [ ... ], "roles": [ { "role": "head", "required": ["m-..."], "coverage": 0.8 } ],
              "people": [ { "person": "p01", "required": 4, "done": 3, "overdue": [] } ],
              "coverage": 0.74, "sessions": [ ... ],
              "art4": { "status": "measures_in_place|partial", "note": "Art. 4 réécrit par le règlement 2026/1744 : prendre des mesures adaptées au rôle, au risque et au contexte" } },
  "rnd": { "experiments": [ ... ], "gpu_hours_month": 0 },
  "gpu": { "source": "dcgm|sim|none", "available": true, "devices": [ { "id": "gpu0", "name": "...", "util_pct": 0, "temp_c": 0, "power_w": 0,
                                               "mem_used_gb": 0, "mem_total_gb": 0, "sensitive_sessions": 0 } ] },
  "grafana": { "available": false, "dashboards": [ { "uid": "factory-overview", "title": "...", "url": "/grafana/d/..." } ] },
  "frameworks": { "crosswalk": [ { "id": "...", "title": "...", "nist": ["GOVERN 1.1"], "ai_act": ["Art. 5"],
                                   "iso42001": ["A.9.4"], "tiers": ["high"], "evidence_hint": "...", "basis": "..." } ],
                  "meta": { /* crosswalk.yaml meta: indicative, sources */ } }
}
```

The demo generator is deterministic for a given `seed` and date, produces 30 days of history
anchored on "today", and is the only source when `mode` is `demo`.

## 5. Metrics (Prometheus names)

Exported by the API on `/metrics`:

- `factory_usecase_cost_eur{usecase,cost_center,kind="llm|gpu|platform"}` (gauge, current period)
- `factory_usecase_tokens_total{usecase,direction="in|out"}`
- `factory_waste_alerts{rule,severity}`
- `factory_risk_usecases{tier}`, `factory_reevaluation_required{usecase}` (0/1)
- `factory_pipeline_gate_pass{usecase}` (0/1), `factory_rollbacks_total{usecase}`
- `factory_skills_coverage_ratio{role}`
- `factory_agent_ttft_seconds{agent,quantile}`, `factory_agent_tokens_per_task{agent,quantile}`,
  `factory_vector_latency_seconds{quantile}` (demo mode: from the generator)
- `factory_audit_events_total{action}`, `factory_audit_chain_ok` (0/1),
  `factory_security_denied_total{reason}` (labels carry no identity)

Exported by the LiteLLM custom callback (`compose/litellm/factory_callbacks.py`, MIT, port 9464):

- `factory_llm_requests_total{model,deployment,usecase,status}`
- `factory_llm_tokens_total{model,usecase,direction}`
- `factory_llm_cost_eur_total{model,usecase}`
- `factory_llm_ttft_seconds_bucket{model,usecase}` (histogram), `factory_llm_latency_seconds_bucket{...}`

GPU: DCGM exporter names (`DCGM_FI_DEV_GPU_UTIL`, `DCGM_FI_DEV_GPU_TEMP`, `DCGM_FI_DEV_POWER_USAGE`,
`DCGM_FI_DEV_FB_USED`, `DCGM_FI_DEV_FB_FREE`), emitted by the real exporter or by `sim/gpu-sim`.

## 6. Services and ports (`docker-compose.yml`)

| Service | Image (licence) | Port (internal) | Profile |
|---|---|---|---|
| `volume-init` | caddy:2 (one-shot: gives the edge volumes to uid 10001) | - | default |
| `edge` | caddy:2 (Apache-2.0) | 80/443 -> host | default |
| `dashboard` | built from `dashboard/` (nginx, BSD-2) | 80 | default |
| `api` | built from `api/` | 8000 | default |
| `prometheus` | prom/prometheus (Apache-2.0) | 9090 | default |
| `grafana` | grafana/grafana (AGPLv3 OSS edition, separate service; grafana/grafana-oss is no longer published after 13.0.2) | 3000 | default |
| `grafana-bootstrap` | built from `compose/grafana-bootstrap` (python:3.12-slim, standard library only; one-shot, writes the Viewer SA token) | - | default |
| `loki` | grafana/loki (AGPLv3, separate service) | 3100 | default |
| `alloy` | grafana/alloy (Apache-2.0) | 12345 | default |
| `docker-socket-proxy` | wollomatic/socket-proxy (MIT): GET allowlist of the Docker API for Alloy, only container mounting the Docker socket | 2375 | default |
| `oauth2-proxy` | quay.io/oauth2-proxy/oauth2-proxy (MIT), OIDC single sign-on behind Caddy forward_auth | 4180 | `sso` |
| `qdrant` | qdrant/qdrant (Apache-2.0) | 6333 | default |
| `litellm` | litellm/litellm-non_root on Docker Hub (official non-root variant of the same release as ghcr.io/berriai/litellm-non_root, switch with `LITELLM_IMAGE`; MIT outside `enterprise/`, no licence key) | 4000, 9464 | default |
| `postgres` | postgres:17 (PostgreSQL) | 5432 | default |
| `langfuse-web`, `langfuse-worker` | langfuse/langfuse:3, langfuse/langfuse-worker:3 (MIT core, no EE key) | 3000, 3030 | default |
| `clickhouse` | clickhouse/clickhouse-server (Apache-2.0) | 8123, 9000 | default |
| `valkey` | valkey/valkey (BSD-3), instead of Redis | 6379 | default |
| `seaweedfs` | chrislusf/seaweedfs (Apache-2.0), S3 API instead of MinIO | 8333 | default |
| `mock-llm`, `traffic`, `gpu-sim` | built from `sim/` | 8080, -, 9400 | `sim` |
| `ollama-vps` | ollama/ollama (MIT) | 11434 | `vps` |
| `vllm-local`, `vllm-embed`, `dcgm-exporter` | vllm/vllm-openai (Apache-2.0), nvidia/dcgm-exporter (Apache-2.0) | 8000, 9400 | `local-gpu` |

Edge routes: `{$FACTORY_HOST}` -> dashboard, `/api/*` -> api; `grafana.{$FACTORY_HOST}` -> grafana;
`langfuse.{$FACTORY_HOST}` -> langfuse-web. LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres are not exposed.
With `FACTORY_AUTH=sso`, `/oauth2/*` -> oauth2-proxy and every other path except `/healthz` and
`/.well-known/security.txt` goes through forward_auth (pages redirect to the identity provider,
`/api/*` gets 401). `/.well-known/security.txt` (RFC 9116) is served on every host. Networks:
`edge-identity` (internal) = edge at the fixed address `FACTORY_EDGE_IDENTITY_IP`, api (alias
`api-identity`), oauth2-proxy; `docker-api` (internal) = alloy and docker-socket-proxy.
Client-supplied `X-Forwarded-User`, `-Email`, `-Groups`, `-Preferred-Username` and
`X-Auth-Request-*` never pass the edge, on any route and in any mode: `none` removes them,
`basic` sets `X-Forwarded-User` to the authenticated user and removes the others, `sso` sets them
only from the oauth2-proxy answer (`forward_auth copy_headers`) and removes them on `/oauth2/*`
(test: `compose/caddy/tests/test_identity_headers.sh`). The dashboard CSP applies to the
dashboard only; `/api/*` keeps the API's own headers.

LiteLLM model aliases: `chat-default` (cloud, fallbacks VPS then local), `chat-small` (VPS first),
`chat-sensitive` (local only, no fallback outside the local GPU), `embed-default` (local only:
corpora may contain C3/C4 documents). Fallback-only groups: `chat-default-vps`,
`chat-default-local`, `chat-small-cloud`. The gateway callback also redirects any request whose
`metadata.sensitivity` is C3/C4 to `chat-sensitive` and masks it in Langfuse.
Requests carry `metadata.usecase` (and `metadata.agent`) for cost attribution.

## 7. Dashboard (front) integration

- On load the dashboard calls `./api/v1/snapshot?lang=<en|fr>` (same origin). On failure (file://,
  API down, static hosting flagged by `<html data-static-demo="1">`) it uses the inline demo
  snapshot of the current language, embedded between `<!-- DEMO_SNAPSHOT_START -->` and
  `<!-- DEMO_SNAPSHOT_END -->` in `<script type="application/json" id="demo-snapshot-en">` and
  `id="demo-snapshot-fr"` (written by `scripts/build_snapshot.py`, section 9). A badge shows the
  source (API or offline demo).
- Polls the snapshot every 30 s in live mode; GPU telemetry drives the 3D rack.
- New 3D resources: R&D Lab benches `X1..X3` (desks), Skills Lab room `S1` (room, 8 seats),
  Agent Ops consoles `O1..O2` (desks), 12 lockers. Profiles come from the snapshot.
- The browser never calls Grafana, LiteLLM or Langfuse directly and holds no token.

## 8. Additions made during the build (all backward compatible)

- `config/factory.yaml`: optional `dataloop.thresholds` (signal thresholds; engine defaults apply
  when absent) and C1–C4 labels.
- `config/skills.yaml`: `sessions[].title` (label shown by the dashboard; `{en, fr}` mapping, section 9).
- Catalogue: optional keys on agents (`model_alias`), tools (`data_classes`, `human_approval`,
  `local_only`), use cases (`eval.judge_alias`) and GitHub cards (`description`, `commit`,
  `inventoried`, `secrets_detail`, `dependencies_checked`, `relicense_to_mit`, `notes`). Repositories
  matching no pillar get no card; they are documented in `docs/GITHUB_INTEGRATION.md`.
- Metrics from the gateway callback: `factory_llm_agent_requests_total{agent,usecase,status}`,
  `factory_llm_agent_tokens_total{agent,usecase,direction}`,
  `factory_llm_sensitive_reroutes_total{from_model,usecase}`; `factory_llm_latency_seconds` carries
  `deployment`. GPU series carry `gpu_source` = `sim` | `dcgm` | `dcgm-remote`; gpu-sim exports
  `factory_gpu_sim_source_up`.
- Dashboard: visit mode (first-person walk, `window.__twin.pov` test hook) and the optional visual
  pack `dashboard/assets/assets.js` (`window.__AF_ASSETS`, see `docs/ASSETS.md`).
- Gateway guardrails (compose/litellm/factory_callbacks.py): `factory_llm_guardrail_events_total{type,action,usecase}`
  with type `prompt_injection|pii_email|pii_phone|pii_iban|pii_card|pii_nir` and action
  `flagged|blocked|redacted` (one event per request and type); env `FACTORY_GUARDRAIL_MODE=flag|block|off`
  (unknown = block) and `FACTORY_PII_REDACTION=true|false`; request metadata `factory_guardrail`
  holds rule ids and counts only, and Langfuse tags `guardrail:prompt_injection` /
  `guardrail:pii_redacted` are added. C3/C4 requests also carry `turn_off_message_logging`, and
  the reroute to `chat-sensitive` covers the Responses and Messages APIs.
- Security alerts (`compose/prometheus/rules/factory-alerts.yml`, group `factory-security`, unit
  tests in `compose/prometheus/tests/`): `FactoryAuditChainBroken` (`factory_audit_chain_ok == 0`
  for 1 min, critical) and `FactorySecurityDenialsSpike` (more than 50 refusals in 5 min, warning).
- Grafana session cookie: `GRAFANA_COOKIE_SECURE=auto` (default) sets the `Secure` flag when
  `FACTORY_SCHEME=https`; `true` or `false` force it.
- Supply chain: every Python dependency installs from a hash-pinned lock (`requirements*.txt`
  compiled from `requirements*.in` by pip-compile, `pip install --require-hashes`); Dockerfile
  base images and the Dockerfile frontend are pinned as `tag@sha256:digest`.
- Releases (tag `v*`): images built from this repository are published as
  `ghcr.io/<owner>/<name>` (api, dashboard, mock-llm, traffic, gpu-sim, grafana-bootstrap;
  linux/amd64), signed keyless with cosign, with SLSA build provenance and SBOM attestations
  (SPDX 2.3, CycloneDX); verify with `.github/scripts/verify_image.sh`.

## 9. Internationalisation (English first, French second)

- **Languages**: `en` (default) and `fr`. Code, comments and identifiers stay in English.
- **Config text**: any human-readable value in `config/` may be a plain string (same text for
  every language) or a mapping `{en: "...", fr: "..."}`. The API never changes ids, enums, numbers
  or dates.
- **API**: every read endpoint accepts `?lang=en|fr` (default: env `FACTORY_DEFAULT_LANG`, itself
  defaulting to `en`; unknown values fall back to the default). The response is fully resolved:
  every `{en, fr}` mapping becomes the string of the chosen language (fallback to the other
  language, then to the first value), and every human-readable string the engines generate
  (alert messages and actions, placement reasons, data-loop details, pipeline stage details,
  risk reasons, rollback triggers, skills notes, warnings…) comes from the message catalogue
  `api/app/i18n.py`. The snapshot carries `meta.lang` and `meta.languages: ["en", "fr"]`.
  Writes (`POST`/`PUT`) are language-independent.
- **Embedded offline snapshots**: `scripts/build_snapshot.py` writes two blocks between the
  existing markers: `<script type="application/json" id="demo-snapshot-en">` and
  `id="demo-snapshot-fr"`; `--check` verifies both; `--lang en|fr` with `--out` writes one.
- **Dashboard**: language = saved preference, else `fr` when `navigator.language` starts with `fr`,
  else `en`; an EN/FR toggle in the header; `<html lang>` follows; numbers and dates use
  `en-US` / `fr-FR`; the snapshot is requested with `?lang=` and refetched on toggle (offline: the
  matching embedded block).
- **Docs**: `README.md` in English and `README.fr.md` in French; English docs in `docs/`, French
  versions in `docs/fr/` (same file names).

Note: the value examples in section 2 are shown as plain strings for brevity; in `config/` every
human-readable value is an `{en, fr}` mapping (section 9). French documentation lives in
`docs/fr/` with the same file names as the English documents in `docs/`.
