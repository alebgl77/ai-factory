# `compose/` — configuration of the stack services

**English** | [Français](README.fr.md)

Each subfolder configures one service of `docker-compose.yml`. Everything here is
MIT-licensed; Grafana and Loki (AGPLv3) run as **separate, unmodified services**: the
repository only holds their configuration, never their code.

| Folder | Service | Role |
|---|---|---|
| `caddy/` | `edge` | Edge proxy (the "VPS proxy"): dashboard, `/api/*`, `grafana.<host>`, `langfuse.<host>`, security headers, automatic TLS in VPS mode, optional basic authentication |
| `prometheus/` | `prometheus` | Scraping (API, LiteLLM callback `:9464`, GPU `:9400`, Qdrant, platform), recording rules (SLO constants) and alerting rules (waste, TTFT p95, GPU temperature, idle GPU, gateway error rate) |
| `prometheus/targets/` | `prometheus` | Remote GPU hosts (`gpu-*.yml`), for example the local workshop linked to the VPS over a VPN |
| `grafana/` | `grafana` | Provisioned data sources (UIDs `prometheus` and `loki`) and dashboards versioned as JSON |
| `grafana-bootstrap/` | `grafana-bootstrap` | One-shot job: Viewer service account and token written to a volume that only the API reads |
| `loki/` | `loki` | Logs, single binary, filesystem storage, 7-day retention |
| `alloy/` | `alloy` | Docker container discovery and log shipping to Loki (Promtail is no longer maintained) |
| `litellm/` | `litellm` | LLM gateway: aliases `chat-default`, `chat-small`, `chat-sensitive`, `embed-default`, cloud → VPS → local GPU fallbacks, MIT callback `factory_callbacks.py` |
| `postgres/` | `postgres` | `litellm` and `langfuse` databases, one owner role per service |
| `clickhouse/` | `clickhouse` | "Small machine" settings for the Langfuse traces |
| `seaweedfs/` | `seaweedfs` | Langfuse S3 object storage (instead of MinIO), identity restricted to its bucket |
| `build/` | (build time) | Optional extra root certificates for a corporate TLS proxy (`BUILD_CA_BUNDLE`) |

## Profiles

| Profile | Added services | Use |
|---|---|---|
| (none) | core of the stack | real providers configured in `.env` |
| `sim` | `mock-llm`, `traffic`, `gpu-sim` | full demo **without any key** |
| `vps` | `ollama-vps`, `ollama-vps-pull` | CPU model on the VPS (fallback 1) |
| `local-gpu` | `vllm-local`, `vllm-embed`, `dcgm-exporter` | local NVIDIA GPU: C3/C4 sensitive data, 3D rack telemetry |

## "Cloud first" placement

```
chat-default   : cloud ──(failure)──▶ VPS ──(failure)──▶ local GPU
chat-small     : VPS   ──(failure)──▶ cloud (small model) ──(failure)──▶ local GPU
chat-sensitive : local GPU only (C3/C4), no fallback: fails closed when the GPU is down
embed-default  : local GPU only (corpora may contain C3/C4 documents)
```

The `factory_callbacks.py` callback also enforces the rule at the gateway: a request whose
`metadata.sensitivity` is C3 or C4 is redirected to `chat-sensitive`, and its content is
masked in Langfuse.

## Networks

- `factory` (internal, no Internet access): every service.
- `egress`: only `edge` (ACME), `litellm` (cloud providers), `ollama-vps` and `vllm-*`
  (model downloads).

Only `edge` publishes ports. LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres and Loki are
never exposed; the browser holds no token (the API queries Grafana server-side with the
Viewer service account token).

## Checks

```bash
docker compose --profile sim --profile vps --profile local-gpu config -q
scripts/smoke_stack.sh            # starts the sim profile and checks the whole chain
scripts/scan_secrets.sh .         # gitleaks (MIT) before any commit or integration
```
