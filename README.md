# Virtual AI Factory

**English** · [Français](README.fr.md) · [MIT license](LICENSE) · [CI status](https://github.com/alebgl77/ai-factory/actions/workflows/ci.yml)

**An open-source digital twin for operating enterprise AI.**

Follow work by people and agents, review decisions with their evidence, and inspect model
routing and token costs from a 3D workspace. The repository includes a browser demo and a
self-hosted stack for private pilots.

**[Open the 3D demo](https://alebgl77.github.io/ai-factory/?lang=en)** · [Documentation](docs/README.md) · [Install](#local-quick-start-no-key-at-all) · [Architecture](docs/ARCHITECTURE.md) · [Technologies](#technology-stack)

<img src="dashboard/docs/media/ai-factory-control-floor.webp" width="1200" alt="Isometric AI operations floor with server racks, workstations and a lime path connecting the rooms.">

| Environment | What you can verify |
|---|---|
| **Public demo** | Explore a fictional portfolio, approval flow and routing simulation in the browser, without provider keys. [Interactive guide](https://alebgl77.github.io/ai-factory/docs/index.en.html) |
| **Private pilot** | Configure SSO and business-unit policies, connect approved model targets and collect operating evidence from authorized producers. [Compose](docs/DEPLOYMENT.en.md) · [Kubernetes](docs/KUBERNETES.md) |

<a id="technologies"></a>

## Technology stack

### Reference stack

| Layer | Technologies and roles |
|---|---|
| **Interface and API** | <img src="dashboard/docs/media/tech/threejs.svg" width="24" height="24" alt=""> **Three.js** for the 3D twin · <img src="dashboard/docs/media/tech/fastapi.svg" width="24" height="24" alt=""> **FastAPI** for the aggregation API |
| **Deployment and routing** | **Docker Compose** for the reference stack · <img src="dashboard/docs/media/tech/kubernetes.svg" width="24" height="24" alt=""> **Kubernetes** and <img src="dashboard/docs/media/tech/helm.svg" width="24" height="24" alt=""> **Helm** for an alternative deployment · <img src="dashboard/docs/media/tech/caddy.svg" width="24" height="24" alt=""> **Caddy** for the edge · **LiteLLM** for model routing |
| **Observability** | <img src="dashboard/docs/media/tech/prometheus.svg" width="24" height="24" alt=""> **Prometheus** for metrics · **Grafana** for dashboards · **Loki** for logs · <img src="dashboard/docs/media/tech/langfuse.svg" width="24" height="24" alt=""> **Langfuse** for model traces |
| **Retrieval** | <img src="dashboard/docs/media/tech/qdrant.svg" width="24" height="24" alt=""> **Qdrant** for vector storage and collection telemetry in the Compose reference stack |

The Helm deployment uses operator-managed model and telemetry services. See the [environment comparison](docs/README.md#choose-an-environment) for what each deployment includes.

### Optional integrations

| Integration | Scope |
|---|---|
| **Model serving** | <img src="dashboard/docs/media/tech/vllm.svg" width="24" height="24" alt=""> **vLLM** for local GPU inference · <img src="dashboard/docs/media/tech/ollama.svg" width="24" height="24" alt=""> **Ollama** for the VPS CPU profile. [Model placement](#compose-model-placement) |
| **Agent tools** | <img src="dashboard/docs/media/tech/mcp.svg" width="24" height="24" alt=""> **MCP** over local stdio for the adaptive Workbench. [MCP setup](docs/ADAPTIVE_WORKBENCH.md#connect-an-mcp-client) |
| **CRM** | **Salesforce**, **HubSpot** and <img src="dashboard/docs/media/tech/twenty.svg" width="24" height="24" alt=""> **Twenty** for read-only opportunity and company data. [CRM setup](docs/ADAPTIVE_WORKBENCH.md#connect-salesforce-hubspot-or-twenty) |
| **ERP** | **Odoo** via a read-only probe. Live draft creation requires an operator-supplied adapter and its own approval flow. [ERP adapter](docs/ADAPTIVE_WORKBENCH.md#connect-a-draft-erp-adapter) |
| **Usage governance** | **Open Shadow AI** runs separately. The operator bridge imports aggregate evidence into the Observatory. [Observatory setup](docs/SHADOW_AI.md#connect-open-shadow-ai) |

[Logo credits and usage terms](dashboard/docs/media/tech/README.md)

## See the Factory in action

**01 · Follow delegated work.** Open the portfolio from the 3D twin and unfold a task to see
its sub-agents, progress and declared token usage.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/media/ai-factory-pilotage.png">
  <img src="docs/media/ai-factory-pilotage.gif" width="760" alt="Fictional AI Factory dashboard with the team list and 3D workspace.">
</picture>

[GIF](docs/media/ai-factory-pilotage.gif) · [Still image](docs/media/ai-factory-pilotage.png) · [Open the live demo](https://alebgl77.github.io/ai-factory/)

<details>
<summary><strong>02 · Review a decision in the Decision Companion</strong></summary>

Review a proposed ERP draft from a fictional CRM opportunity, enter a rationale and see the
simulated decision and receipt in the history.

<picture>
  <source media="(prefers-reduced-motion: reduce)" srcset="docs/media/ai-factory-workbench.png">
  <img src="docs/media/ai-factory-workbench.gif" width="760" alt="The Decision Companion’s fictional CRM and ERP walkthrough shows a simulated receipt and the reason recorded in the decision history.">
</picture>

[GIF](docs/media/ai-factory-workbench.gif) · [Still image](docs/media/ai-factory-workbench.png) · [Workbench guide](docs/WORKBENCH.md)

</details>

[Technical diagram](docs/ARCHITECTURE.md#deployment-and-approval-paths) · [Annotated floor plan](docs/ARCHITECTURE.md#2d-floor-plan)

## Try it

| In your browser | In GitHub Codespaces | On your machine |
|---|---|---|
| **[Open the live demo](https://alebgl77.github.io/ai-factory/?lang=en)** with the embedded fictional dataset. | **[Launch Codespaces](https://codespaces.new/alebgl77/ai-factory)**. The dev container generates local secrets and starts the `sim` profile. | **[Start the Compose demo](#local-quick-start-no-key-at-all)** with simulated model providers and GPU telemetry. |

Then, in the dashboard: press `M` (or the **Visit** button) to walk through the factory in first
person, `U` to open the factory console (register, FinOps, pipeline, data loop, placement,
catalog), `?` for help. Switch language with the **EN/FR** toggle in the header.

Open **UC-04 Workshop** from the 3D dashboard or launch the [standalone 2D workshop](https://alebgl77.github.io/ai-factory/exercise/).
Business, risk and platform review a fictional release, simulate a GPU outage and export a decision record. The exercise runs in the browser with simulated data and decisions.

Use the **Decision Companion** from the dashboard or workshop to open the Workbench on a phone. See the [Workbench guide](docs/WORKBENCH.md) for the offline demo, approval flow and integration contract.

Embed the **adaptive Workbench** in an agent or application with `python -m pip install ./api`, then run `python examples/adaptive_workbench.py`. The [integration guide](docs/ADAPTIVE_WORKBENCH.md) covers working methods, the MCP skill, approval channels and ERP recovery.

Open the **AI Usage Observatory** on the map or in the Factory tab. The protected mode connects the Open Shadow AI evidence rollup, maps monthly team usage, tracks adoption of alternatives and records governance decisions with separate approval. [Observatory guide](docs/SHADOW_AI.md)

### Local quick start (no key at all)

Requirements: Docker 24+ with Compose v2, 4 vCPUs, 8 GB of free RAM, ports 80 and 443 available.

```bash
git clone --recurse-submodules https://github.com/alebgl77/ai-factory.git
cd ai-factory
cp .env.example .env
# Replace every "change-me" value with a secret of its own, for example:
#   for v in $(grep -o '^[A-Z_]*=.*change-me[^ ]*' .env | cut -d= -f1); do
#     sed -i "s|^$v=.*|$v=sk-$(openssl rand -hex 24)|" .env; done
# (LITELLM_MASTER_KEY must start with "sk-"; keep LITELLM_SALT_KEY stable afterwards.)
docker compose --profile sim up -d --build
./scripts/smoke_stack.sh --no-up  # checks health, LLM routing, metrics, dashboards, traces, logs
```

Then open:

| URL (local) | Service |
|---|---|
| `http://localhost/` | 3D dashboard (source "API") |
| `http://localhost/api/v1/docs` | OpenAPI documentation of the aggregation API |
| `http://grafana.localhost/` or `http://127.0.0.1:3001/` | Grafana (admin account from `.env`) |
| `http://langfuse.localhost/` or `http://127.0.0.1:3002/` | Langfuse (bootstrap account `LANGFUSE_INIT_USER_*` from `.env`) |

The `sim` profile replaces the LLM providers with `mock-llm` (OpenAI-compatible, streaming and
realistic latencies), generates traffic with `traffic` and simulates the GPU's DCGM telemetry
with `gpu-sim`. The whole chain therefore runs without any key or GPU: gateway → cost callback →
Prometheus → Grafana → API → 3D dashboard.

**Offline version**: open `dashboard/index.html` directly in a browser. The dashboard then uses
the embedded demo snapshots (one in English, one in French) and shows an *Offline demo* badge.

The Helm chart supports a local Kubernetes demo and an authenticated private pilot with
operator-managed services. [Install Kubernetes](docs/KUBERNETES.md) · [Operate Kubernetes](docs/KUBERNETES_OPERATIONS.md) · [Deploy with Compose](docs/DEPLOYMENT.en.md)

## What the factory does

| Priority | Capability | Where |
|---|---|---|
| P0 | Use-case register, AI Act risk tiers (prohibited, high risk under Annex III / Annex I, limited under Art. 50, minimal), NIST AI RMF cycle (GOVERN, MAP, MEASURE, MANAGE) crosswalked with the AI Act and ISO/IEC 42001, **risk reassessment on every model version change** | `config/usecases/`, `config/frameworks/`, `api/app/engines/risk.py`, "Register" console |
| P0 | Token and GPU costs per use case, showback / chargeback, 8 waste alert rules | `config/factory.yaml`, `engines/costs.py`, "FinOps" console |
| P1 | LLMOps / RAG pipeline: ingestion → indexing → versioning → evaluation gates → deployment and rollback receipts from authorized producers. The demo simulates the sequence. | [Operating evidence](docs/OPERATING_EVIDENCE.md), "Pipeline" console |
| P1 | Data loop on drift: reindexing, evaluation-set updates, routing changes, human review. No fine-tuning or retraining | `engines/dataloop.py`, "Data loop" console |
| P1 | Hybrid placement cloud → VPS → local GPU. Direct C3/C4 requests require a local GPU and fail if it is unavailable. The placement simulator recommends waiting in that case and does not enqueue work | `engines/placement.py`, `compose/litellm/config.yaml`, "Placement" console |
| P2 | Agent Ops room: TTFT, tokens per task, vector latency, SLOs | `engines/agentops.py`, consoles O1/O2, Grafana dashboard `agent-ops` |
| P2 | Self-service catalog of models, agents, MCP tools and integrated GitHub repositories | `config/catalog/`, "Catalog" console |
| P2 | Skills Lab and R&D Lab in 3D, tied to roles and to AI Act Art. 4 (AI literacy, as rewritten in 2026) | `config/skills.yaml`, rooms S1, X1–X3 |
| + | **Visit mode**, Street View style: drag the character onto the floor, walk in first person (keyboard, click on the floor, touchpad), interact with desks, rooms, people, screens and the rack; minimap with the status of each space | **Visit** button or `M` key |
| + | **English and French** dashboard, localizable API data (`?lang=en\|fr`), configuration (`{en, fr}` mappings) and operator guides | [Interface contract](docs/CONTRACT.md), section 9 |
| + | Optional visual pack generated in ChatGPT (textures, decor, portraits, mascot) with a procedural fallback | [docs/ASSETS.md](docs/ASSETS.md) |

## Architecture

```text
Browser (3D dashboard, no token) ──HTTPS──► edge (Caddy: TLS, optional auth)
   ├─ /       dashboard (nginx + vendored Three.js)
   └─ /api/*  aggregation API (FastAPI, MIT): risk, FinOps, pipeline, data loop,
              placement, Agent Ops, catalog and skills engines
                 │ server side only
                 ├─ Prometheus ◄── LiteLLM cost callback, DCGM / gpu-sim, Qdrant, API KPIs
                 ├─ Grafana (separate AGPL service, Viewer token) ── Loki ◄── Alloy
                 ├─ Langfuse (traces, prompts, scores)
                 └─ LiteLLM gateway ──► cloud │ VPS (Ollama) │ local GPU (vLLM)
```

The API aggregates `config/`, its mutable state and live sources. In `auto` mode, unavailable
sources can fall back to simulated data for an unscoped view. Business-unit views always omit
unavailable data and keep telemetry within their scope. Private pilots use
`FACTORY_DEMO_MODE=never`. [Architecture](docs/ARCHITECTURE.md) · [Interface contract](docs/CONTRACT.md)

<a id="real-deployment-cloud-first-then-vps-then-local-hardware"></a>

## Compose model placement

Use the [private deployment guide](docs/DEPLOYMENT.en.md) for installation, SSO, release
verification, migration rehearsal and recovery. Before a matching signed bundle ships,
[qualify from the exact source checkout](docs/DEPLOYMENT.en.md#qualify-from-source).
The steps below configure the model routes.

1. **Cloud**: in `.env`, replace `CLOUD_API_BASE`, `CLOUD_API_KEY`, `CLOUD_CHAT_MODEL` and
   `CLOUD_SMALL_MODEL` (default: simulated LLM) with those of your OpenAI-compatible provider,
   then start **without** the `sim` profile. The `chat-default` alias goes to the cloud; its
   fallbacks are the VPS, then the local GPU (`compose/litellm/config.yaml`).
2. **VPS** (proxy and fallback): on the VPS, set `FACTORY_HOST=factory.example.org`,
   `FACTORY_SCHEME=https`, `GRAFANA_PUBLIC_URL` and `LANGFUSE_PUBLIC_URL` (DNS records for the
   domain, `grafana.` and `langfuse.`). Caddy obtains the TLS certificates automatically. Enable
   proxy authentication (`FACTORY_AUTH=basic`, `FACTORY_BASIC_AUTH_USER`,
   `FACTORY_BASIC_AUTH_HASH`, see [docs/SECURITY.md](docs/SECURITY.md)). For a CPU fallback
   model, add the `vps` profile and set `VPS_API_BASE=http://ollama-vps:11434/v1` and
   `VPS_CHAT_MODEL=openai/<Ollama model>`.
   ```bash
   docker compose --profile vps up -d --build
   ```
3. **Local GPU** (sensitive C3/C4 data): on the machine with an NVIDIA GPU (driver and NVIDIA
   Container Toolkit installed), start `vllm-local`, `vllm-embed` and `dcgm-exporter` with the
   `local-gpu` profile, then point `LOCAL_API_BASE`, `LOCAL_CHAT_MODEL`, `LOCAL_EMBED_API_BASE`
   and `LOCAL_EMBED_MODEL` to vLLM. If the GPU is on a different machine than the VPS, connect it
   through an encrypted tunnel (WireGuard), put its address in `LOCAL_*_API_BASE` and declare its
   DCGM exporter in `compose/prometheus/targets/gpu-remote.yml`. The real GPU telemetry then
   drives the 3D rack (temperature, utilization, power, memory). `chat-sensitive` and
   `embed-default` stay on the local GPU and fail closed (HTTP 503) if it is unavailable: never a
   fallback to the cloud or the VPS.
   ```bash
   docker compose --profile local-gpu up -d
   ```

Grafana is never called by the browser: the API queries it server side, through the VPS proxy,
with the token of a *Viewer* service account created by `grafana-bootstrap` and stored in a
volume. The Grafana dashboards are versioned as JSON (`compose/grafana/dashboards/`) and
provisioned at startup.

## Adapt the factory to your organization

| File | Content |
|---|---|
| `config/factory.yaml` | identity, regulatory calendar, cost centers and budgets, token and GPU rates, showback or chargeback mode, waste rules, placement policy, Agent Ops SLOs, gate and rollback thresholds |
| `config/profiles.yaml` | roles and people (any number), desks, lockers, skills, attendance pattern |
| `config/usecases/*.yaml` | one file per use case: AI Act qualification, NIST, ISO 42001, reviewed model and version, RAG, KPIs |
| `config/catalog/` | models, agents, tools, GitHub repository cards |
| `config/skills.yaml` | learning paths per role (Art. 4), modules, Skills Lab sessions, R&D Lab experiments |
| `config/frameworks/` | NIST AI RMF, AI Act and ISO/IEC 42001 frameworks and the crosswalk |

Any human-readable value may be a plain string or an `{en: "...", fr: "..."}` mapping. Every
read endpoint accepts `?lang=en|fr`; the default comes from `FACTORY_DEFAULT_LANG` (`en` if
unset). After a change: `python scripts/validate_config.py`, then
`python scripts/build_snapshot.py` to regenerate the dashboard's offline snapshots.

## Security

### Security & trust at a glance

[CodeQL](https://github.com/alebgl77/ai-factory/actions/workflows/codeql.yml) · [OpenSSF Scorecard](https://scorecard.dev/viewer/?uri=github.com/alebgl77/ai-factory) · [Supply-chain checks](https://github.com/alebgl77/ai-factory/actions/workflows/security.yml) · [Verify a signed release](SECURITY.md#verify-a-release)

Every line below is backed by a test, a CI job or a command you can run yourself.

| Area | What is in place | How to verify |
|---|---|---|
| Identity and access | Enterprise SSO (OpenID Connect via oauth2-proxy: Entra ID, Okta, Google, Keycloak), identity accepted from the edge only, 5-role RBAC, deny by default, fail-closed configuration | `api/tests/test_security.py`, `compose/sso/test_sso_e2e.sh` |
| Governance integrity | Four-eyes rule: nobody approves a model version they recorded; SHA-256 hash-chained audit trail for supported governance writes, offline verifier | `GET /api/v1/audit/verify`, `python -m app.audit verify` |
| Sensitive data | C3/C4 data processed on the local GPU only, fail closed (no cloud fallback); PII redacted before any cloud model; no prompt in logs | `scripts/smoke_stack.sh` |
| LLM guardrails | Prompt-injection and jailbreak detection (EN/FR), flag or block | `compose/litellm/tests` |
| No token in the browser | The dashboard only talks to the API; Grafana token, gateway and provider keys stay server side | `scripts/smoke_stack.sh` |
| Hardened runtime | Non-root, read-only, all capabilities dropped, no-new-privileges, PID limits; Docker socket behind a GET-only proxy; internal networks without Internet access | `scripts/smoke_stack.sh` (`docker inspect` of every container) |
| Supply chain | SHA-pinned least-privilege workflows, CodeQL, OpenSSF Scorecard, pip-audit, Trivy, gitleaks, CycloneDX and SPDX SBOMs, Dependabot with cooldown | [`.github/workflows/`](.github/workflows/) |
| Signed releases | Keyless cosign signatures, SLSA build provenance and SBOM attestations | `.github/scripts/verify_image.sh` |
| Data residency | Self-hosted, no SaaS dependency, telemetry off, no training on your data | [docs/SECURITY.md](docs/SECURITY.md#13-data-residency-and-privacy) |

The default configuration is a key-less demo with authentication off, and it says so:
`GET /api/v1/health` reports `security.status: demo`. Production settings (TLS, SSO, secrets,
rotation, backups, retention) are in the
[hardening guide](docs/SECURITY.md#14-production-hardening-guide).

- [docs/SECURITY.md](docs/SECURITY.md): security architecture, hardening guide, incident response
- [Cyber operations guide](docs/CYBER_SECURITY.md) for optional Workbench journaling, authenticated checkpoints, SIEM exports and CVE triage
- [Business-unit security](docs/BU_SECURITY.md) for scoped access and bookings
- [Gateway security](docs/GATEWAY_SECURITY.md) for key-bound routing and local handling of sensitive workloads
- [Operating evidence](docs/OPERATING_EVIDENCE.md) for live deployment receipts and drift observations
- [docs/THREAT_MODEL.md](docs/THREAT_MODEL.md): data flows, STRIDE per component, LLM threats, residual risks
- [docs/SECURITY_CONTROLS.md](docs/SECURITY_CONTROLS.md): controls mapped to OWASP LLM Top 10 2025, OWASP ASVS 5.0, NIST SSDF, CIS Docker, ISO/IEC 27001:2022, with NIS2 and DORA pointers
- [SECURITY.md](SECURITY.md): report a vulnerability (private reporting, acknowledgement within 72 h) and verify a release

## Licenses

The code of this repository is under the **MIT** license ([LICENSE](LICENSE)). Grafana and Loki
(AGPL-3.0) run as **separate, unmodified services**, from their official images; the repository
neither vendors nor links their code. LiteLLM is used without its `enterprise/` directory and
Langfuse without its `ee` modules or any license key. The images of the visual pack are
published under the repository's MIT license (author's decision), with SHA-256 provenance.
Details, compatibility matrix and the cases where the AGPL would apply:
[docs/LICENSES.md](docs/LICENSES.md).

## GitHub integrations

Seven public repositories of the author, aligned with the pillars of the factory, are
referenced as **submodules** (no copied code) and as catalog cards, after checking licenses,
dependencies and secrets across their full history: see
[docs/GITHUB_INTEGRATION.md](docs/GITHUB_INTEGRATION.md). After a plain clone:
`git submodule update --init`.

## Visual pack

The 3D scene works fully with procedural materials. An optional pack of 15 images (tileable
textures, backdrop, artwork, team portraits, visit-mode mascot, isometric hero) can be generated
in ChatGPT from ready-to-paste prompts, then optimized and bundled by
`scripts/build_assets.py`: [docs/ASSETS.md](docs/ASSETS.md).

## Tests and checks

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r api/requirements-dev.txt
(cd api && python -m pytest -q)           # engines (risk, costs, pipeline, placement…), i18n and API
python scripts/validate_config.py         # consistency of config/
python scripts/build_snapshot.py --check  # embedded EN and FR snapshots are up to date
python scripts/check_licenses.py          # license compatibility (dependencies, images, submodules)
pip install -r scripts/requirements-assets.txt && python -m pytest -q tests/assets   # visual pack pipeline
./scripts/scan_secrets.sh .               # gitleaks
npm i --no-save playwright three@0.186.1 && THREE_VENDOR_DIR=node_modules/three node tests/e2e/run.mjs   # offline dashboard e2e
E2E_BASE_URL=http://localhost node tests/e2e/run.mjs                 # e2e against the deployed stack
```

CI runs the core checks on pushes and pull requests. The Compose smoke and Kubernetes jobs run
on relevant changes or by manual dispatch. [Workflow](.github/workflows/ci.yml) · [Verified scope](docs/README.md#choose-an-environment)

## Roadmap

Follow the [enterprise roadmap](https://alebgl77.github.io/ai-factory/docs/roadmap.html?lang=en)
for delivery stages, prerequisites and validation criteria.

## Contributing

Contributions are welcome: issues, anonymized `config/` examples, corrections to the regulatory
mappings (with a primary source), code. Read [CONTRIBUTING.md](CONTRIBUTING.md) and the
[code of conduct](CODE_OF_CONDUCT.md). If you use the project in your work, see
[CITATION.cff](CITATION.cff).

## Documentation

The [technical documentation](docs/README.md) organizes the guides by role, environment and
data flow. The [interactive guide](https://alebgl77.github.io/ai-factory/docs/index.en.html)
covers the dashboard and the path to a private pilot. [Documentation en français](docs/fr/README.md)

## Author

Created by **Alexandre Beguel** (Digitalizor), GitHub: [alebgl77](https://github.com/alebgl77).
Copyright (c) 2026 Alexandre Beguel (Digitalizor) and contributors, MIT license.

## Disclaimer

All data in this repository is **fictional**. The AI Act qualifications, the NIST / AI Act /
ISO 42001 mappings and the regulatory calendar are **indicative**: they are a working model and
**not legal advice**. Application dates are configurable in `config/factory.yaml`; check them
against the Official Journal of the European Union before any use.

Independent project, not affiliated with or endorsed by NVIDIA, Dell Technologies, the EuroHPC
Joint Undertaking or the European Commission; "AI factory" is used as a generic term.
