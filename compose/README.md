# `compose/` — configuration of the stack services

**English** | [Français](README.fr.md)

Each subfolder configures one service of `docker-compose.yml`. Everything here is
MIT-licensed; Grafana and Loki (AGPLv3) run as **separate, unmodified services**: the
repository only holds their configuration, never their code.

| Folder | Service | Role |
|---|---|---|
| `caddy/` | `edge` | Edge proxy (the "VPS proxy"): dashboard, `/api/*`, `grafana.<host>`, `langfuse.<host>`, security headers, `security.txt`, automatic TLS and HSTS in VPS mode, authentication `none` / `basic` / `sso` (identity-header and CSP test in `caddy/tests/`) |
| `sso/` | `oauth2-proxy` (profile `sso`) | End-to-end test of the single sign-on (`test_sso_e2e.sh`, test identity provider in `e2e/`) |
| `prometheus/` | `prometheus` | Scraping (API, LiteLLM callback `:9464`, GPU `:9400`, Qdrant, platform), recording rules (SLO constants) and alerting rules (waste, TTFT p95, GPU temperature, idle GPU, gateway error rate, broken audit chain, spike of security denials; promtool unit tests in `prometheus/tests/`) |
| `prometheus/targets/` | `prometheus` | Remote GPU hosts (`gpu-*.yml`), for example the local workshop linked to the VPS over a VPN |
| `grafana/` | `grafana` | Provisioned data sources (UIDs `prometheus` and `loki`) and dashboards versioned as JSON |
| `grafana-bootstrap/` | `grafana-bootstrap` | One-shot job: Viewer service account and token written to a volume that only the API reads |
| `loki/` | `loki` | Logs, single binary, filesystem storage, 7-day retention |
| `alloy/` | `alloy` | Docker container discovery (through `docker-socket-proxy`, read-only allowlist) and log shipping to Loki (Promtail is no longer maintained) |
| `litellm/` | `litellm` | LLM gateway: aliases `chat-default`, `chat-small`, `chat-sensitive`, `embed-default`, cloud → VPS → local GPU fallbacks, MIT callback `factory_callbacks.py` (metrics, sensitive routing, guardrails; unit tests in `litellm/tests/`) |
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
| `sso` | `oauth2-proxy` | enterprise single sign-on (OpenID Connect) in front of the dashboard and the API |

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
- `egress`: only `edge` (ACME), `litellm` (cloud providers), `oauth2-proxy` (identity
  provider), `ollama-vps` and `vllm-*` (model downloads).
- `edge-identity` (internal, `10.89.255.0/28` by default): `edge`, `api`, `oauth2-proxy`. The
  edge holds the fixed address `FACTORY_EDGE_IDENTITY_IP` (`10.89.255.2`); the other members
  get addresses from `10.89.255.8/29` only. The API reads identity headers from that address
  only (`FACTORY_TRUSTED_PROXIES`), oauth2-proxy trusts forwarded headers from it only
  (`--trusted-proxy-ip`).
- `docker-api` (internal): `alloy` and `docker-socket-proxy` only.

Only `edge` publishes ports. LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres and Loki are
never exposed; the browser holds no token (the API queries Grafana server-side with the
Viewer service account token).

## Security hardening

Every control below is checked by a test, not only described. `scripts/smoke_stack.sh`
inspects every running container and fails on any regression.

### Container hardening (measured with `docker inspect`, profile `sim`)

Applied to every service: `no-new-privileges`, `cap_drop: [ALL]`, PID limit
(`deploy.resources.limits.pids`), memory and CPU limits, size-capped logs.

| Service | Runs as | Read-only root | Capabilities added | PID limit | Notes |
|---|---|---|---|---|---|
| `edge` | 10001 | yes (`/tmp` tmpfs) | `NET_BIND_SERVICE` | 128 | the `caddy` binary carries the file capability `cap_net_bind_service` (ports 80/443): exec fails if it is not in the bounding set |
| `volume-init` (one-shot) | root | yes | `CHOWN`, `DAC_READ_SEARCH` | 16 | no network; gives the edge volumes (ACME certificates) to uid 10001, a no-op on fresh volumes |
| `dashboard` | 101 | yes | none | 64 | port 80 through `net.ipv4.ip_unprivileged_port_start=0` |
| `api` | 10001 | yes | none | 128 | |
| `oauth2-proxy` (`sso`) | 65532 | yes | none | 64 | distroless image, no healthcheck possible (the edge fails closed with 502) |
| `prometheus` | nobody | yes | none | 256 | |
| `grafana` | 472 | yes | none | 512 | |
| `grafana-bootstrap` (one-shot) | 10001 | yes | none | 32 | |
| `loki` | 10001 | yes | none | 512 | distroless, readiness checked through Grafana |
| `docker-socket-proxy` | root (option: nobody + docker group) | yes | none | 32 | the only container with the Docker socket, see below |
| `alloy` | 473 | yes | none | 256 | no Docker socket; state in the `alloy-state` volume |
| `litellm` | nobody | yes | none | 512 | official non-root image `litellm/litellm-non_root` |
| `qdrant` | 1000 | yes | none | 512 | snapshots kept in the storage volume |
| `langfuse-web` | 1001 | yes | none | 512 | Next.js cache in tmpfs; read-only also protects the application files, which the image leaves writable by its user |
| `langfuse-worker` | 1001 | yes | none | 512 | |
| `postgres` | 70 | yes | none | 512 | runs as `postgres` directly: no root step, `gosu` never executed |
| `clickhouse` | 101 | yes | none | 4096 | `users.d` in tmpfs (written by the entrypoint); thread pools grow with the cores |
| `valkey` | 999 | yes | none | 128 | password read from a private tmpfs file, never on the command line |
| `seaweedfs` | 1000 | yes | none | 512 | S3 identity rendered in a private tmpfs |
| `mock-llm`, `traffic`, `gpu-sim` (`sim`) | 10001 | yes | none | 64 / 64 / 32 | |
| `ollama-vps`, `ollama-vps-pull` (`vps`) | root | no | none | 1024 / 64 | not runtime-tested here; the image writes `/root/.ollama` |
| `vllm-local`, `vllm-embed` (`local-gpu`) | root | no | none | 4096 | not runtime-tested here (no GPU); sized `/dev/shm` (`VLLM_SHM_SIZE`) instead of `ipc: host` |
| `dcgm-exporter` (`local-gpu`) | image default | no | default set kept | 256 | NVIDIA documents `CAP_SYS_ADMIN` for profiling metrics; not testable without a GPU |

A fresh install (empty volumes) was verified for the edge, the storage services, Prometheus,
Loki, Alloy and the socket proxy with these settings.

### Docker socket

Alloy needs the Docker API to discover containers and read their logs. It never mounts the
socket: it talks to `docker-socket-proxy` ([wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy),
MIT) over the internal `docker-api` network. The proxy answers the `alloy` container only and
lets through `GET` on `_ping`, `version`, `networks`, `containers/json`,
`containers/<id>/json` and `containers/<id>/logs`; every other method or path (create, exec,
archive, export, images...) is refused (403 / 405, checked by the smoke test). Residual risk:
container inspect returns the environment of the containers, so Alloy is as sensitive as the
`.env` file. To run the proxy as `nobody`, set
`DOCKER_SOCKET_PROXY_USER=65534:$(stat -c %g /var/run/docker.sock)`.

### Edge (Caddy)

- `Content-Security-Policy`: the dashboard policy (no CDN by default) on the dashboard only;
  `/api/*` keeps the API's own policy (`default-src 'none'` on JSON, a per-answer nonce and the
  pinned, Subresource-Integrity-checked Swagger UI on `/api/v1/docs`), with a deny-all default
  for an answer that would come without one. `X-Frame-Options`, `X-Content-Type-Options`,
  `Referrer-Policy`: set when the upstream does not send a stricter value.
- `Permissions-Policy` (camera, microphone, geolocation, payment, USB... all denied),
  `Cross-Origin-Opener-Policy` and `Cross-Origin-Resource-Policy: same-origin`,
  `X-Permitted-Cross-Domain-Policies: none`; `Server`, `Via` and `X-Powered-By` removed.
- `Strict-Transport-Security` only when `FACTORY_SCHEME=https`: a browser never pins a plain
  HTTP demo host.
- `/.well-known/security.txt` (RFC 9116) on every host name: contact through the GitHub
  private vulnerability reporting of the repository, `Expires` (`FACTORY_SECURITY_TXT_EXPIRES`,
  to move forward every year), `Policy`, `Canonical`, `Preferred-Languages: en, fr`.
- Request body limits: 1 MB (dashboard and API), 10 MB (Grafana), 20 MB (Langfuse ingestion);
  header timeout 10 s, body timeout 60 s, idle 2 min, headers capped at 64 KB.
- Identity headers (`X-Forwarded-User`, `-Email`, `-Groups`, `-Preferred-Username`,
  `X-Auth-Request-*`) sent by clients are removed on every route and host in every mode; only the
  edge sets them (basic user name in `basic` mode, oauth2-proxy answer through `forward_auth
  copy_headers` in `sso` mode). A client-supplied `X-Real-IP` or `X-Forwarded-For` never reaches
  an upstream either. `caddy/tests/test_identity_headers.sh` checks every route for `none`,
  `basic` and `sso` against stand-in upstreams (68 checks, CI job `compose`).

### Enterprise single sign-on (profile `sso`)

1. Register an application at the identity provider (Microsoft Entra ID, Okta, Google
   Workspace, Keycloak or any OpenID Connect provider) with the redirect URI
   `https://<FACTORY_HOST>/oauth2/callback`.
2. In `.env`: `FACTORY_AUTH=sso`, `FACTORY_AUTH_MODE=proxy`, the `SSO_*` block (provider,
   issuer, client, cookie secret, allowed e-mail domains and groups) and
   `FACTORY_ROLE_MAP` (SSO groups to API roles).
3. `docker compose --profile sim --profile sso up -d` (or without `sim` for real providers).

The flow: Caddy asks oauth2-proxy (`forward_auth`) on every page and API call; pages without
a session are redirected to the provider (authorization code with PKCE `S256`), API calls get
a plain 401. On success Caddy passes `X-Forwarded-User`, `X-Forwarded-Email` and
`X-Forwarded-Groups` to the API (client copies are deleted first). The session is an
encrypted, `HttpOnly`, `Secure`, `SameSite=Lax` cookie with the `__Host-` prefix, 8 hours by
default. oauth2-proxy refuses to start without a valid configuration (client, secret, cookie
secret, e-mail domains): the edge then answers 502 (fail closed). Grafana and Langfuse keep
their own login (each can use the same provider natively).

`compose/sso/test_sso_e2e.sh` runs the real flow in an isolated network: the shipped
Caddyfile, oauth2-proxy with the environment rendered by `docker compose --profile sso
config`, a test provider (Dex) and an echo service in place of the dashboard and the API. It
checks redirections, 401s, forged headers, the identity received by the API, the fixed edge
address and the refusal of a user outside `SSO_ALLOWED_GROUPS`.

### LLM guardrails (gateway)

`litellm/factory_callbacks.py` runs before any request leaves the gateway:

- **Prompt injection / jailbreak** detection on untrusted content (user, tool and retrieved
  text; system prompts are not scanned): instruction override, system prompt exfiltration,
  role-play jailbreaks, chat-template delimiters, encoded payloads, hidden Unicode tag and
  bidirectional characters, in English and French, after Unicode normalisation. Padding
  beyond the scan budget is itself flagged. `FACTORY_GUARDRAIL_MODE=flag` (default: forwarded,
  rule ids in `metadata.factory_guardrail`, tag `guardrail:prompt_injection` in Langfuse),
  `block` (HTTP 400 with a generic message) or `off`; an unknown value means `block`.
- **PII redaction** before an alias that may leave the local GPU (`chat-default`,
  `chat-small` and their fallbacks): e-mail addresses, phone numbers (international, French,
  North American), IBAN (ISO 13616 length and mod-97 check), payment cards (Luhn) and French
  NIR (with its key, Corsica included) become `[REDACTED_<KIND>]`. C3/C4 requests never reach
  those aliases (they are rerouted to `chat-sensitive`); `FACTORY_PII_REDACTION=false` turns
  redaction off.
- **No prompt in logs**: C3/C4 requests are masked in Langfuse and carry
  `turn_off_message_logging` (every LiteLLM logging integration; the client header that
  would disable redaction is dropped); `redact_messages_in_exceptions` keeps prompts out of
  error messages and `store_prompts_in_spend_logs: false` out of the spend logs. Guardrail
  metadata and metrics hold rule ids and counts only.
- Metric `factory_llm_guardrail_events_total{type,action,usecase}`; the Responses and
  Messages APIs are rerouted like chat completions.

Unit tests (no LiteLLM needed):

```bash
pip install --require-hashes -r compose/litellm/tests/requirements.txt   # hash-pinned lock
python -m pytest -q compose/litellm/tests                                # CI job "guardrails"
```

### Image scan (Trivy)

Trivy 0.76.1 (`aquasec/trivy`, Apache-2.0), vulnerability database of 2026-11-04,
HIGH/CRITICAL only (fixable = a fixed version exists upstream):

| Image | CRITICAL (fixable) | HIGH (fixable) |
|---|---|---|
| `caddy:2.11.7-alpine` | 1 (1) | 9 (8) |
| `prom/prometheus:v3.15.1` | 0 | 0 |
| `grafana/grafana:13.0.12` (was `grafana/grafana-oss:13.0.2`: 2 CRITICAL, 103 HIGH) | 0 | 4 (4) |
| `grafana/loki:3.7.9` | 0 | 0 |
| `grafana/alloy:v1.21.3` | 0 | 0 |
| `wollomatic/socket-proxy:1.13.2` | 0 | 0 |
| `litellm/litellm-non_root:v1.106.7` (was `litellm/litellm:v1.106.6`: 18 HIGH) | 0 | 1 (1) |
| `qdrant/qdrant:v1.20.0-unprivileged` | 0 | 50 (6) |
| `langfuse/langfuse:3.227.0` | 6 (6) | 43 (43) |
| `langfuse/langfuse-worker:3.227.0` | 6 (6) | 39 (39) |
| `postgres:17.11-alpine` | 1 (1) | 25 (25) |
| `clickhouse/clickhouse-server:26.8.36.3` | 0 | 0 |
| `valkey/valkey:8.1.11-alpine` | 0 | 0 |
| `chrislusf/seaweedfs:4.52` | 0 | 0 |
| `quay.io/oauth2-proxy/oauth2-proxy:v7.15.5` (release binary) | 0 | 7 (6) |
| `nvidia/dcgm-exporter:4.6.1-4.8.4` | 0 | 15 (15) |
| `ollama/ollama:0.40.14` | 0 | 0 |
| `vllm/vllm-openai:v0.33.0` | not scanned (10.9 GB image) | |

In the 2026-11-04 baseline, the six images built here passed the CI gate (no fixable HIGH or
CRITICAL). The dashboard image then used `nginx/nginx-unprivileged:1.30.6-alpine-slim` and had
no HIGH or CRITICAL findings. The five Python images (`python:3.12.16-slim`, Debian 13.7) had
45 HIGH findings with no fixed version in that scan.

For the current results on these six images, download `trivy-image-vulnerabilities-<image>` from
the `security` workflow's artifacts. These reports include all vulnerability severities and
findings without a fix and are retained for 30 days. GitHub Code Scanning lists image secrets
and vulnerabilities with a fix. Findings available only in the artifacts can still affect the
images.

In that baseline, the `postgres` findings were all in `gosu`, which is never executed because
the container starts as the `postgres` user. The `langfuse` findings were in its Next.js
dependencies. Reproduce the scan with the current database:

```bash
docker run --rm -v /var/run/docker.sock:/var/run/docker.sock:ro aquasec/trivy:0.76.1 \
  image --scanners vuln --severity HIGH,CRITICAL <image>
```

## Checks

```bash
docker compose --profile sim --profile vps --profile local-gpu --profile sso config -q
scripts/smoke_stack.sh            # starts the sim profile and checks the whole chain + security
compose/sso/test_sso_e2e.sh       # single sign-on, isolated network
compose/caddy/tests/test_identity_headers.sh   # forged identity headers and CSP, every auth mode
python -m pytest -q compose/litellm/tests   # guardrail detectors and gateway hook
docker run --rm -v "$PWD/compose/prometheus:/etc/prometheus:ro" --entrypoint promtool \
  prom/prometheus:v3.15.1 test rules /etc/prometheus/tests/security-alerts.test.yml
scripts/scan_secrets.sh .         # gitleaks (MIT) before any commit or integration
```
