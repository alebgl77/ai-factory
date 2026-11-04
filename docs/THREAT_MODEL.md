# Threat model

*[Version française](fr/THREAT_MODEL.md)*

This threat model covers the Virtual AI Factory stack as defined in this repository
(`docker-compose.yml`, `compose/`, `api/`, `dashboard/`, `.github/`). It uses STRIDE per
component and the OWASP Top 10 for LLM Applications 2025 for the LLM-specific threats. Each
threat names the mitigation that is **implemented** and how to verify it, then the **residual
risk** that remains. Items that are designed but not shipped are marked **Roadmap**.

Residual risk is rated for the production posture of [SECURITY.md](SECURITY.md#14-production-hardening-guide)
(`FACTORY_AUTH=sso`, `FACTORY_AUTH_MODE=proxy`, real secrets, TLS). In the key-less demo posture
the identity, RBAC and four-eyes controls are off by design, and the API says so
(`security.status: demo`).

Reviewed: 2026-11-04. Review again for every new service, data flow, external provider or
release with security changes, and after any incident.

Contents: [1. Scope and assumptions](#1-scope-and-assumptions) ·
[2. Data-flow diagram](#2-data-flow-diagram) · [3. Assets and actors](#3-assets-and-actors) ·
[4. STRIDE per component](#4-stride-per-component) ·
[5. LLM-specific threats](#5-llm-specific-threats) ·
[6. Residual risk register](#6-residual-risk-register)

## 1. Scope and assumptions

In scope: the browser and dashboard, the edge (Caddy, and oauth2-proxy in profile `sso`), the
aggregation API, the LLM gateway (LiteLLM and the factory callback), the external LLM providers
as seen from the gateway, the observability and data stores, the GPU host, the container runtime
and the supply chain that produces the images.

Assumptions (if one does not hold, re-assess):

- **A1** The Docker host and its administrators are trusted. Root on the host can read every
  volume and secret; host hardening is the operator's responsibility.
- **A2** The identity provider is trusted and enforces multi-factor authentication.
- **A3** LLM providers are bound by contract but treated as untrusted technically: they only
  ever receive C1/C2 data, after PII redaction.
- **A4** Applications that call the gateway are internal, hold the gateway key, and label the
  data class of their requests (`metadata.sensitivity`) correctly.
- **A5** The operator applies the production hardening guide.

Out of scope: the security of the host OS, the identity provider, the providers' own
infrastructure, the business applications that call the gateway, and physical security.

## 2. Data-flow diagram

```text
                           TB1 Internet │ Docker host
  [Browser] ──F1 HTTPS───────────────────┼──► (edge: Caddy) ──F4──► (dashboard: static)
                                         │       │  │  └──F5──► (Grafana) (Langfuse web)   own login
  [Identity provider] ◄──F2 OIDC (egress)┼── (oauth2-proxy) ◄─┘ forward_auth (profile sso)
                                         │       │
                                         │       └──F3 identity headers, from the fixed edge
                                         │          address only (network edge-identity) ── TB2
                                         │                   ▼
                                         │             (aggregation API) ──F7──► [api-state:
                                         │               │    │                   registry,
                                         │               │    │                   audit.jsonl]
                                         │   F6 server   │    └──F6──► (Grafana, Viewer token:
                                         │   side only   ▼             GET allowlist, named queries)
                                         │        (Prometheus) (Langfuse) (Qdrant) (LiteLLM)
                                         │
  [Internal apps, agents] ──F8 key───────┼──► (LiteLLM gateway + factory callback)
                                         │      │ reroute C3/C4, guardrails, PII redaction
  [LLM providers: cloud, VPS] ◄──F9──────┼──────┤ C1/C2 only (egress) ─────────────────── TB3
                                         │      └──F10 C3/C4 ──► [GPU host: vLLM] ─────── TB4
                                         │      └──F11──► (Langfuse traces, masked for C3/C4)
                                         │                (Prometheus metrics :9464)
                                         │  (Alloy) ──F12──► (docker-socket-proxy) ──► [Docker
                                         │     └──► (Loki)        GET allowlist           socket] TB5
  [GitHub Actions] ──F13 signed images, SBOMs, provenance──► [GHCR] ──► operator verifies ── TB6
```

Flows:

| Flow | From → to | Content | Protection |
|---|---|---|---|
| F1 | browser → edge | pages, `/api/*`, session cookie (sso) | TLS (`FACTORY_SCHEME=https`), HSTS, CSP, body and header limits |
| F2 | oauth2-proxy ↔ identity provider | OIDC authorisation code | TLS, PKCE `S256`, `egress` network |
| F3 | edge → API | requests with `X-Forwarded-User/-Email/-Groups` | `edge-identity` internal network, fixed edge address, API trusts that `/32` only, client copies deleted |
| F4, F5 | edge → dashboard, Grafana, Langfuse | static page; UIs with their own login | internal network; identity headers stripped |
| F6 | API → data sources | read-only queries | server-side secrets; Grafana GET allowlist and named queries |
| F7 | API → state volume | registry, bookings, placement, audit | file lock, hash-chained audit, rollback if audit fails |
| F8 | applications → gateway | prompts with `metadata.usecase` and `sensitivity` | gateway key, internal network only, not routed by the edge |
| F9 | gateway → cloud or VPS | C1/C2 prompts | TLS to the provider, PII redaction, guardrails, `egress` network |
| F10 | gateway → GPU host | C3/C4 prompts, fallbacks | local GPU only, no fallback; vLLM API key; WireGuard when remote |
| F11 | gateway → Langfuse, Prometheus | traces, metrics | C3/C4 masked and excluded from logging; metrics hold counts only |
| F12 | Alloy → socket proxy → Docker; Alloy → Loki | container list and logs | `docker-api` internal network, GET allowlist, client `alloy` only |
| F13 | CI → registry → operator | images, SBOMs, attestations | SHA-pinned actions, Trivy gate before push, cosign keyless, SLSA provenance |

Trust boundaries: **TB1** Internet to edge; **TB2** edge to internal services (where identity is
asserted); **TB3** gateway to external LLM providers; **TB4** gateway to the GPU host (possibly
another machine); **TB5** containers to the Docker host; **TB6** CI/CD and registry to the
deployment.

## 3. Assets and actors

### 3.1 Assets

| Id | Asset | Where | Property that matters |
|---|---|---|---|
| AS1 | C3/C4 prompts and completions | gateway (in transit), GPU host | confidentiality |
| AS2 | C1/C2 prompts and completions (may contain personal data) | gateway, providers, Langfuse | confidentiality |
| AS3 | Governance records: model versions, risk assessments, placement decisions, bookings | `api-state` volume, `config/` (Git) | integrity |
| AS4 | Audit trail | `audit.jsonl`, Loki copies, published head hash | integrity, availability |
| AS5 | Secrets: database and store passwords, gateway master key, provider keys, SSO client and cookie secrets, Grafana Viewer token | `.env`, container environment, volumes | confidentiality |
| AS6 | Traces and logs | Langfuse (Postgres, ClickHouse, SeaweedFS), Loki | confidentiality, integrity |
| AS7 | Corpora and embeddings | Qdrant | confidentiality, integrity |
| AS8 | Metrics and cost data | Prometheus | integrity |
| AS9 | Released images, SBOMs, attestations | GHCR, GitHub releases | integrity |
| AS10 | LLM budget | provider accounts | financial (denial of wallet) |
| AS11 | Availability of the sensitive route | local GPU | availability |

### 3.2 Actors (threat sources)

| Id | Actor | Typical goal |
|---|---|---|
| T1 | Anonymous Internet attacker | reach internal services, impersonate a user, exhaust resources |
| T2 | Authenticated employee beyond their role | read or change what their role does not allow |
| T3 | Insider bypassing governance | approve their own model version, alter records, hide traces |
| T4 | Compromised or faulty internal application | exfiltrate data through the gateway, mislabel data, run up costs |
| T5 | Author of content ingested by RAG or tools | indirect prompt injection |
| T6 | Compromised or malicious LLM provider or model | collect prompts, return harmful output |
| T7 | Compromised upstream dependency, image or GitHub Action | code execution in CI or at runtime |
| T8 | Attacker with code execution in one container | lateral movement, secrets, container escape |
| T9 | Host administrator | trusted (A1), out of scope |

## 4. STRIDE per component

Columns: **Threat**, **Mitigation implemented** (with how to verify), **Residual risk**
(Low / Medium / High). "Smoke" means a check of `scripts/smoke_stack.sh` on the running stack;
test names refer to [`api/tests/test_security.py`](../api/tests/test_security.py) unless stated
otherwise.

### 4.1 Browser and dashboard

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Session theft or phishing page framing the dashboard | `__Host-` session cookie, `Secure`, `HttpOnly`, `SameSite=Lax` (sso); HSTS over HTTPS; `frame-ancestors 'none'`, `X-Frame-Options: DENY`. Smoke: "security headers", "edge headers" | Low; credential phishing at the identity provider is covered by its MFA (A2) |
| T | Script injection through data rendered by the dashboard | values are HTML-escaped before insertion; CSP without external origins (Three.js vendored); CodeQL `javascript-typescript` with `security-extended` on every change | **Medium**: the single-file page needs `'unsafe-inline'` scripts, so a missed escape would execute; content comes from GitOps-controlled `config/` and the API |
| R | A user denies a change made from the dashboard | every write is in the audit trail with actor and request id | Low |
| I | Tokens or data exposed in the browser | no token in the browser; `Cache-Control: no-store` on `/api/`; `Referrer-Policy`. Smoke: "Grafana token absent from every browser-facing response" | Low |
| D | Not applicable (client-side rendering only) | - | Low |
| E | Browser reaches internal services directly | same-origin API only, CORS closed, internal services not routed | Low |

### 4.2 Edge (Caddy, oauth2-proxy)

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Client forges `X-Forwarded-User/-Email/-Groups` to become `admin` | identity headers sent by clients are deleted in every mode; `forward_auth copy_headers` deletes client copies first; the API trusts only the edge's fixed address. `compose/sso/test_sso_e2e.sh` ("forged headers never reach the API"), `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers` | Low. **High** if an operator widens `FACTORY_TRUSTED_PROXIES` beyond the edge address |
| S | Another container on the internal network asserts an identity | `edge-identity` network: the edge holds a fixed address outside the `ip_range` given to the other members; all capabilities are dropped, so no container can change its address | Low |
| T | TLS stripping or interception | automatic certificates (ACME), HSTS when `FACTORY_SCHEME=https` | Low; first visit before HSTS (no preload list) |
| R | Requests cannot be traced | JSON access logs to stdout, collected into Loki; request id forwarded to the API | Low; default Loki retention is 7 days |
| I | Version banners help an attacker | `Server`, `Via`, `X-Powered-By` removed | Low |
| D | Slow clients, oversized bodies or headers | header/body/idle timeouts, body limits (1 MB / 10 MB / 20 MB), headers capped at 64 KB. Smoke: "edge request body limit (413 above 1 MB)" | **Medium**: no per-IP rate limiting at the edge; volumetric attacks need an upstream WAF or CDN |
| E | Protected pages reachable without a session (SSO misconfiguration) | every path except `/healthz`, `/.well-known/security.txt` and `/oauth2/*` goes through `forward_auth`; oauth2-proxy refuses to start with an invalid configuration and the edge answers 502 (fail closed); `caddy validate` in CI (job `compose`) | Low |

### 4.3 Aggregation API

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Direct call to the API that bypasses the edge | 401 `untrusted_source` for any peer outside `FACTORY_TRUSTED_PROXIES`, before the body is read; unknown paths denied by default. `test_proxy_mode_unknown_api_path_is_denied_by_default` | Low |
| T | Governance records changed outside the API (volume edit) | audit reconciliation reports `drift`; `factory_audit_chain_ok`. `test_state_changed_outside_the_api_is_reported_as_drift` | **Medium**: detected, not prevented (write access to the volume means host access, A1) |
| T | Concurrent writes corrupt state | file lock; optimistic version check (409) for bookings. `test_concurrent_writers_keep_one_valid_chain` | Low |
| R | An approver denies an approval | hash-chained record with actor, role, request id, client IP; copies in Loki; head hash published. `test_tampering_is_detected` | **Medium**: the chain is not keyed; HMAC or WORM anchoring is **Roadmap** |
| I | Secrets in API answers | answers of LiteLLM and Grafana rebuilt field by field; posture holds codes and numbers only. `test_no_secret_in_any_response_with_auth_on` | Low |
| I | Arbitrary queries through the Grafana proxy | GET only, path allowlist, named PromQL queries only | Low |
| I | Reviewer e-mail (`reviewed_by`) visible to every viewer | none (data-minimisation choice under review) | Low to Medium, depending on your privacy policy |
| D | Write flooding, large bodies | per-actor token bucket (429, `Retry-After`), 64 KiB body limit, 401 before reading unauthenticated bodies. `test_writes_are_rate_limited_per_actor_with_retry_after` | **Medium**: in-memory, per process (several replicas need edge rate limiting); reads are not limited |
| E | Route without authorisation, role escalation | deny by default; every route declares one permission (`test_every_api_route_declares_exactly_one_permission`); fail-closed configuration (`test_misconfiguration_fails_closed`); role × endpoint matrix | Low |
| E | Insider approves their own model version (T3) | four-eyes rule under the state lock; refusal audited. `test_four_eyes_refuses_self_approval_and_records_the_reviewer` | Low for versions recorded through the API; **Medium** for versions defined in Git (relies on Git review and branch protection) |
| E | Production left in demo mode (`FACTORY_AUTH_MODE=none`) | posture `status: demo`, warning `auth_disabled` in `/api/v1/health` and `meta.security` | **Medium**: operator error; alert when `security.status` is not `enforced` |

### 4.4 LLM gateway (LiteLLM and the factory callback)

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Unauthorised caller uses the gateway | master key required; gateway on the internal network only, not routed by the edge | **Medium**: one shared key for all internal applications; per-application keys with budgets are **Roadmap** |
| T | Request shaped to bypass sensitive routing (other model, Responses or Messages API) | the callback reroutes every generative call type with C3/C4 to `chat-sensitive`. `test_responses_and_messages_api_are_rerouted_too`; smoke "C4 request on chat-default rerouted to the local GPU" | Low |
| T | Mislabelled data class (C3 sent as C1) | PII redaction still applies before cloud aliases | **Medium**: depends on A4; classify at the source |
| R | Who sent which request | per use case and agent metrics, Langfuse traces, spend logs without prompts | **Medium**: attribution is per use case, not per end user |
| I | Prompts leaked into logs or error messages | `redact_messages_in_exceptions`, `store_prompts_in_spend_logs: false`, `turn_off_message_logging` for C3/C4. Smoke "gateway logs contain no prompt text" | Low |
| I | Personal data sent to a cloud provider | redaction of e-mail, phone, IBAN, card, French NIR before cloud and VPS aliases. Smoke "PII redacted before the cloud target" | **Medium**: pattern-based; names, addresses and free-text identifiers are not detected |
| D | Provider outage | fallbacks cloud → VPS → local GPU for C1/C2; timeouts, one retry, cooldowns | Low; C3/C4 stop by design when the GPU is down (see 4.7) |
| E | Sensitive routing switched off by configuration | `FACTORY_ENFORCE_SENSITIVE_ROUTING=true` by default; guardrail mode fails closed to `block` on an unknown value (`test_unknown_mode_fails_closed`) | Low |

### 4.5 LLM providers (cloud, VPS)

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Traffic sent to a spoofed endpoint | provider base URLs set by the operator in `.env`; TLS certificate validation by the gateway | Low |
| T | Compromised or manipulated model output | outputs are not trusted (section 5.5); evaluation gates and rollback thresholds of the LLMOps pipeline ([LLMOPS.md](LLMOPS.md)) | **Medium** |
| R | Provider-side records | outside the stack | contractual |
| I | Provider retains or trains on prompts | only C1/C2 with PII redaction; C3/C4 never sent | **Medium**: choose providers with zero-retention and no-training terms (contractual, outside the stack) |
| D | Provider outage or quota | fallbacks, cooldowns (`allowed_fails`, `cooldown_time`), 120 s timeout | Low |
| E | Provider reaches into the factory | outbound only, from the `egress` network; no inbound route | Low |

### 4.6 Observability and data stores

Prometheus, Loki, Grafana, Langfuse (with Postgres, ClickHouse, SeaweedFS, Valkey) and Qdrant.

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | Access from the Internet | not exposed; only Grafana and Langfuse are routed, with their own login; Grafana anonymous access off, sign-up off; Langfuse sign-up off. Smoke "anonymous access refused (401)" | Low |
| S | Lateral movement from a compromised container (T8) | internal network; passwords for Postgres, ClickHouse, Valkey and S3 | **Medium**: Prometheus, Loki and Qdrant accept unauthenticated requests from any container on the `factory` network |
| T | Fake or deleted logs | Loki on its own volume; audit records also kept in `audit.jsonl` and published head hash | **Medium**: Loki has no authentication inside the network |
| I | Sensitive data in traces | C3/C4 masked in Langfuse; Langfuse self-hosted. Smoke "sensitive (C3) traces masked in Langfuse" | **Medium**: C1/C2 prompts are stored in Langfuse for observability; restrict access and set retention |
| I | Clear-text internal traffic and data at rest | single-host internal Docker networks | **Medium** for multi-host deployments (mutual TLS is **Roadmap**); encrypt host disks |
| D | Disk exhaustion | container logs capped (10 MB × 3), Loki 7-day and Prometheus 30-day retention, memory and PID limits | Low |
| E | Grafana admin installs a malicious plugin | no plugin provisioned, preinstall disabled; admin password from `.env` | Low with SSO and the local admin disabled |

### 4.7 GPU host (vLLM, dcgm-exporter; local or remote)

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| S | C3/C4 sent to a fake GPU endpoint | `LOCAL_API_BASE` set by the operator; vLLM requires an API key (`--api-key`); WireGuard recommended for a remote GPU | **Medium**: the vLLM key is passed on the command line (visible to `docker inspect`); the tunnel is the operator's |
| T | Tampered model weights | downloads from the model hub into a cache volume | **Medium**: pinning model revisions and verifying hashes is **Roadmap** |
| I | C3/C4 data at rest on the GPU host | vLLM usage statistics off; data stays on the host | **Medium**: encrypt the disk; restrict host access |
| D | GPU unavailable | C3/C4 workloads queued or refused (fail closed); alert `FactorySensitiveRouteDown`. Smoke "local GPU down: chat-sensitive fails closed" | accepted: availability is traded for confidentiality |
| E | Container escape from GPU containers | `no-new-privileges`, all capabilities dropped (vLLM, Ollama), PID limits, sized `/dev/shm` instead of `ipc: host` | **Medium**: run as root without a read-only root filesystem; dcgm-exporter keeps its default capabilities; not runtime-tested here (no GPU) |

### 4.8 Container runtime and Docker host

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| E | Privilege escalation or escape from a container | `no-new-privileges`, `cap_drop: ALL`, non-root users, read-only root filesystems, PID, memory and CPU limits, no privileged container. Smoke: five `docker inspect` checks | Low to Medium (kernel vulnerabilities; keep the host patched) |
| E | Docker socket abuse | only `docker-socket-proxy` mounts it, read-only; GET allowlist; client `alloy` only. Smoke "Docker API proxy: ... (200 405 403)" | Low |
| I | Secrets read from container environments | none beyond the proxy allowlist | **Medium**: container inspect returns environment variables, so Alloy (and anyone with the socket) can read them; secret files are **Roadmap** |
| D | Fork bombs, memory exhaustion | PID, memory and CPU limits on every service | Low |

### 4.9 Supply chain (CI/CD, images, dependencies)

| | Threat | Mitigation implemented | Residual risk |
|---|---|---|---|
| T | Compromised GitHub Action (re-pointed tag) | every action pinned by full SHA; Dependabot with a 7-day cooldown; zizmor checks for unpinned and impostor commits | Low |
| T | Malicious or vulnerable Python package | exact `==` pins checked in CI; pip-audit on every requirements file; CI tools hash-pinned | **Medium**: runtime requirements and base images are not yet hash- or digest-pinned (OpenSSF Scorecard *Pinned-Dependencies*) |
| T | Tampered released image | Trivy gate before push; cosign keyless signature; SLSA provenance and SBOM attestations; `.github/scripts/verify_image.sh` | Low for operators who verify by digest |
| T | Third-party image re-tagged upstream | images pinned by version and listed in `licenses/third_party.yaml` | **Medium**: tags, not digests; digest pinning is **Roadmap** |
| I | Secret committed to the repository | gitleaks on the full history, Trivy secret scanner | Low |
| E | Workflow injection or token abuse | `permissions: {}` by default, per-job permissions, `persist-credentials: false`, actionlint and zizmor | Low |

## 5. LLM-specific threats

Mapped to the [OWASP Top 10 for LLM Applications 2025](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications).

### 5.1 Prompt injection (LLM01:2025)

- **Threat**: direct injection by a user, or indirect injection through retrieved documents or
  tool output (T5), to override instructions, leak the system prompt or trigger unwanted actions.
- **Implemented**: rule-based detection in English and French on user, tool and retrieved
  content (instruction override, system prompt exfiltration, role-play jailbreaks, chat-template
  delimiters, encoded payloads, hidden Unicode tag and bidirectional characters, padding beyond
  the scan budget); `flag` (default) or `block` (HTTP 400, generic message); metric
  `factory_llm_guardrail_events_total{type="prompt_injection"}`. Verify:
  `python -m pytest -q compose/litellm/tests` (25 attack and 12 benign cases among 76 tests),
  smoke "guardrail: crafted prompt injection flagged".
- **Residual: Medium to High**. Pattern detection reduces but cannot eliminate prompt injection;
  `flag` mode forwards the request. Treat model output as untrusted (5.5), give agents the least
  privilege (the catalogue records tool permissions and `human_approval`, but the factory does
  not run the agents itself), and switch to `block` for exposed applications after tuning.

### 5.2 Data exfiltration through the model (LLM02:2025 Sensitive Information Disclosure)

- **Threat**: sensitive data sent to an external provider, leaked into logs or traces, or
  extracted from the model's context by an attacker.
- **Implemented**: C3/C4 rerouted to the local GPU with no fallback (fail closed); PII redaction
  before cloud and VPS aliases; no prompt in gateway logs, exceptions or spend logs; C3/C4 masked
  in Langfuse and excluded from logging; egress limited to the gateway, the edge, oauth2-proxy
  and model servers. Verify: smoke checks "C4 request ... rerouted to the local GPU", "PII
  redacted before the cloud target", "gateway logs contain no prompt text", "sensitive (C3)
  traces masked in Langfuse".
- **Residual: Medium**. Relies on correct labelling (A4); PII detection covers five kinds of
  identifier only; C1/C2 prompts are kept in the self-hosted Langfuse. The gateway does not
  scan outputs for leaked data.

### 5.3 Model or provider compromise (LLM03:2025 Supply Chain, LLM04:2025 Data and Model Poisoning)

- **Threat**: a provider or a downloaded model is compromised, returns manipulated output or
  collects data; a poisoned model or corpus changes behaviour.
- **Implemented**: providers only get C1/C2 data; aliases decouple use cases from models; every
  model version change forces a new risk assessment (deployment gate blocked until reviewed);
  evaluation gates per risk tier (faithfulness, relevancy, red-team pass rate) and rollback
  thresholds, evaluated by the pipeline engine (`api/app/engines/pipeline.py`); the data loop
  never retrains (`fine_tuning: false`; `api/app/engines/dataloop.py` refuses `retrain`,
  `fine_tune` and similar actions); images of this repository signed with provenance;
  third-party images pinned and scanned.
- **Residual: Medium**. Model weights are not pinned by revision or hash (**Roadmap**); the
  quality of the evaluation gates depends on your evaluation sets; provider trust is contractual.

### 5.4 Cost abuse and denial of wallet (LLM10:2025 Unbounded Consumption)

- **Threat**: a faulty or compromised application (T4), or a prompt that triggers loops, runs up
  the provider bill or saturates the GPU.
- **Implemented**: cost per use case and model (`factory_llm_cost_eur_total`), FinOps budgets per
  cost centre with the waste rules `budget_breach` and `retry_storm`, Prometheus alerts
  `FactoryWasteAlertCritical` and `FactoryGatewayErrorRateHigh`; gateway timeouts (120 s), a
  single retry and cooldowns; edge body limit (1 MB); API write rate limiting.
- **Residual: Medium to High**. Spending is **detected**, not **capped**: there is no
  per-application budget or rate limit enforced at the gateway yet (LiteLLM virtual keys with
  `max_budget` and rate limits are **Roadmap**). Set spending limits at your providers.

### 5.5 Insecure output handling (LLM05:2025 Improper Output Handling)

- **Threat**: model output used as code, HTML, SQL, a command or a tool call without validation.
- **Implemented**: the factory itself does not render or execute model output: the dashboard
  shows aggregated metrics and configuration, HTML-escaped, under a CSP; the API returns
  structured JSON with `default-src 'none'`. Guardrail block messages are generic.
- **Residual: depends on the calling application** (out of scope, A4). Applications must treat
  completions as untrusted input: encode before rendering, validate before executing, require
  human approval for high-impact tool calls.

### 5.6 Other OWASP LLM 2025 entries

| Entry | Status in this stack |
|---|---|
| LLM06:2025 Excessive Agency | the catalogue records tool permissions, data classes and `human_approval`; enforcement belongs to the agent runtime (out of scope) |
| LLM07:2025 System Prompt Leakage | detection of system prompt exfiltration attempts (5.1); keep secrets out of prompts |
| LLM08:2025 Vector and Embedding Weaknesses | embeddings computed on the local GPU only (`embed-default`); Qdrant internal only but unauthenticated inside the network (4.6) |
| LLM09:2025 Misinformation | evaluation gates (faithfulness, LLM-as-a-judge) and rollback thresholds on online feedback, modelled by the pipeline engine ([LLMOPS.md](LLMOPS.md)) |

## 6. Residual risk register

The main residual risks, in priority order, with the action that reduces them:

| # | Residual risk | Rating | Action |
|---|---|---|---|
| R1 | Prompt injection not caught by rules | Medium-High | `block` mode for exposed apps; least-privilege agents; output handling in apps |
| R2 | Spend detected but not capped | Medium-High | provider spending limits now; per-application keys with budgets (**Roadmap**) |
| R3 | Data class mislabelled by a caller | Medium | classify at the source; restrict the gateway key; review labels in the register |
| R4 | Secrets readable through container environments | Medium | protect the Docker socket; secret files (**Roadmap**) |
| R5 | Unauthenticated stores inside the internal network (Prometheus, Loki, Qdrant) | Medium | keep the network single-tenant; add store authentication or network policies for multi-tenant hosts |
| R6 | Unkeyed audit chain | Medium | ship Loki copies to a SIEM; WORM backups; HMAC or anchoring (**Roadmap**) |
| R7 | Dashboard CSP allows inline scripts | Medium | CodeQL on every change; nonce- or hash-based CSP (**Roadmap**) |
| R8 | Runtime dependencies and third-party images not pinned by hash or digest | Medium | hashed locks and digest pins (**Roadmap**) |
| R9 | GPU profiles not runtime-tested; GPU containers run as root | Medium | test on the target GPU host before production |
| R10 | Edge has no per-IP rate limiting | Medium | upstream WAF or CDN for public exposure |
