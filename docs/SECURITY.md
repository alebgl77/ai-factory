# Security

*[Version française](fr/SECURITY.md)*

This document describes the security architecture of the Virtual AI Factory, how to harden it
for production and how to respond to an incident. It is written for CTOs, CIOs and CISOs who need
to decide whether the stack can be trusted, and for the engineers who will check.

Every control below names the code, test or command that proves it. Items that are designed but
not yet shipped are marked **Roadmap**. Companion documents:

- [THREAT_MODEL.md](THREAT_MODEL.md): data flows, assets, actors, STRIDE per component and the
  LLM-specific threats, with residual risks;
- [SECURITY_CONTROLS.md](SECURITY_CONTROLS.md): each control mapped to OWASP Top 10 for LLM
  Applications 2025, OWASP ASVS 5.0.0, NIST SP 800-218 (SSDF), CIS Docker Benchmark,
  ISO/IEC 27001:2022 Annex A, with pointers for NIS2 and DORA;
- [../SECURITY.md](../SECURITY.md): how to report a vulnerability and how to verify a release.

## 1. Security posture in one minute

The stack has two postures, and it always tells you which one it runs in:

| | Demo (default) | Enforced (production) |
|---|---|---|
| Purpose | key-less demo on a laptop or in Codespaces | a real deployment |
| Edge authentication | `FACTORY_AUTH=none` | `FACTORY_AUTH=sso` (OpenID Connect through oauth2-proxy, profile `sso`) |
| API identity | `FACTORY_AUTH_MODE=none`: nobody is authenticated, every request acts as `admin` | `FACTORY_AUTH_MODE=proxy`: identity from the edge only, RBAC, four-eyes rule |
| Posture published | `security.status: demo`, warning `auth_disabled` | `security.status: enforced` (or `degraded` with reason codes) |

Check it at any time, without credentials and without exposing any secret:

```sh
curl -s https://<FACTORY_HOST>/api/v1/health | jq .security
```

The same object is in `meta.security` of the snapshot. `degraded` means enforced mode with a
configuration warning (for example no trusted proxy, no role mapping, an invalid value that was
dropped) or a broken or unavailable audit chain. Code: `posture()` in
[`api/app/security.py`](../api/app/security.py); test: `test_posture_in_health_and_snapshot_meta`
in [`api/tests/test_security.py`](../api/tests/test_security.py).

Controls that do not depend on the posture (container hardening, network segmentation, sensitive
data routing, guardrails, no token in the browser, supply chain) are always on.

## 2. Architecture and trust boundaries

```text
 Internet (untrusted)
   Browser (no token)       Identity provider (Entra ID, Okta...)    LLM providers (cloud, VPS)
        │ HTTPS                       ▲ OIDC                             ▲ C1/C2 only, PII redacted
 ═══════╪═════════════════════════════╪═════════════════════════════════╪═══ Docker host ═══
        ▼                             │                                 │
   edge (Caddy): TLS, headers, limits, auth none | basic | sso,         │
   removes identity headers sent by clients                             │
     ├─► dashboard (static page)      │                                 │
     ├─► oauth2-proxy (profile sso) ──┘                                 │
     └─► api   [edge-identity network: identity accepted from the fixed edge address only]
           └─► Prometheus · Grafana (Viewer token) · Langfuse · Qdrant · LiteLLM (server side)
                                                                        │
   LiteLLM gateway + factory callback ──────────────────────────────────┘
     └─ C3/C4 ─► local GPU (vLLM) only, no fallback, fail closed   [WireGuard if remote]
   Alloy ─► docker-socket-proxy (GET allowlist) ─► Docker socket
   internal only: Loki · ClickHouse · Postgres · Valkey · SeaweedFS · Prometheus
```

Networks (`docker-compose.yml`, section `networks`):

| Network | Internet access | Members | Purpose |
|---|---|---|---|
| `factory` | **none** (`internal: true`) | every service | service-to-service traffic |
| `egress` | yes | `edge` (ACME), `litellm` (LLM providers), `oauth2-proxy` (identity provider), Ollama and vLLM (model downloads) | the only way out |
| `edge-identity` | none | `edge` at the fixed address `FACTORY_EDGE_IDENTITY_IP` (default `10.89.255.2`), `api`, `oauth2-proxy` | the only path on which an identity can be asserted; other members get addresses from a separate `ip_range`, so no container can take the edge address |
| `docker-api` | none | `alloy`, `docker-socket-proxy` | read-only Docker API for log collection |

Only `edge` publishes ports (80, 443, and 3001/3002 bound to `127.0.0.1` for local access to
Grafana and Langfuse). LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres, Valkey, SeaweedFS and
Loki are never routed by the edge, and the API's `/metrics` is not routed either. Verify:
`scripts/smoke_stack.sh` checks "API /metrics not exposed through the edge"; list members with
`docker network inspect ai-factory_factory ai-factory_egress`.

The trust boundaries, data flows and threats are detailed in [THREAT_MODEL.md](THREAT_MODEL.md).

## 3. Identity, authentication and roles

### 3.1 At the edge

`FACTORY_AUTH` (in `.env`) selects how the edge authenticates users of the dashboard and API:

- `none`: demo; no authentication.
- `basic`: HTTP basic authentication against a bcrypt hash (`FACTORY_BASIC_AUTH_HASH`, made with
  `caddy hash-password`); the user name is passed to the API.
- `sso`: OpenID Connect through oauth2-proxy (MIT, profile `sso`), configurable for Microsoft
  Entra ID, Okta, Google Workspace, Keycloak or any OIDC provider (examples in `.env.example`).
  Authorisation code flow with PKCE `S256`; session in an encrypted `__Host-` cookie, `Secure`,
  `HttpOnly`, `SameSite=Lax`, 8 hours by default; optional e-mail domain and group gates
  (`SSO_EMAIL_DOMAINS`, `SSO_ALLOWED_GROUPS`). Pages without a session are redirected to the
  provider, API calls get a plain 401. oauth2-proxy refuses to start without a valid
  configuration, and the edge then answers 502 (fail closed).

**Identity header integrity.** In every mode the edge deletes any `X-Forwarded-User`,
`X-Forwarded-Email`, `X-Forwarded-Groups` and `X-Auth-Request-*` header sent by a client; only the
edge sets them (the basic user name, or the oauth2-proxy answer through `forward_auth
copy_headers`, which deletes client copies first). Grafana and Langfuse keep their own login, and
the identity headers are stripped on their host names too.

Verify: [`compose/sso/test_sso_e2e.sh`](../compose/sso/test_sso_e2e.sh) runs a real sign-in
against a test OIDC provider (Dex) with the shipped Caddyfile and checks redirects, 401s, that
forged headers never reach the API, that the API sees the identity from the fixed edge address
only, and that a user outside `SSO_ALLOWED_GROUPS` gets 403 (17 checks). It needs Docker; CI runs
it on manual dispatch (workflow `ci`, input `sso_e2e`). On every change, CI job `compose` runs
[`compose/caddy/tests/test_identity_headers.sh`](../compose/caddy/tests/test_identity_headers.sh):
forged `X-Forwarded-*`, `X-Auth-Request-*` and client-address headers on every route and host, in
`none`, `basic` and `sso` modes, against stand-in upstreams that echo what they receive (68 checks).
Configuration: [compose/README.md](../compose/README.md#enterprise-single-sign-on-profile-sso).

### 3.2 In the API

`FACTORY_AUTH_MODE=proxy` turns on identity and authorisation in the API
([`api/app/security.py`](../api/app/security.py)):

- **Identity from the edge only.** `X-Forwarded-User`, `-Email` and `-Groups` are read only when
  the TCP peer is in `FACTORY_TRUSTED_PROXIES` (default in `docker-compose.yml`: the edge's fixed
  address, `/32`). Any other request to `/api/*` gets 401 `untrusted_source` before its body is
  read, unknown paths included (deny by default). Only `/api/v1/health` stays public (liveness
  probe, no identity, no secret). Duplicate, over-long, control-character or oversized group
  headers are refused (401 `invalid_identity`).
- **Fail closed on misconfiguration.** An unknown auth mode is treated as `proxy`; an invalid
  CIDR or a catch-all (`0.0.0.0/0`, `::/0`) is dropped; a group mapped to an unknown role is
  ignored; without any role mapping everybody is denied. Each case is reported as a warning and
  the posture becomes `degraded`.
- **RBAC.** Five roles, granted from SSO groups by `FACTORY_ROLE_MAP` (`group:role,...`; a user
  in several groups gets the union) and optionally `FACTORY_DEFAULT_ROLE`:

  | Role | Permissions |
  |---|---|
  | `viewer` | `read` (every read endpoint, the OpenAPI page) |
  | `editor` | `read`, `spaces.write`, `placement.decide` |
  | `maintainer` | `read`, `registry.model_version` |
  | `approver` | `read`, `registry.assessment`, `audit.read` |
  | `admin` | all |

  `GET /api/v1/whoami` shows the identity, roles and permissions the API sees. Refusals are
  `{detail, code}` with a localised `detail`.

Verify (all in [`api/tests/test_security.py`](../api/tests/test_security.py), run by the CI job
`api-tests` in [`.github/workflows/ci.yml`](../.github/workflows/ci.yml)):
`test_proxy_mode_authorization_matrix` (every endpoint × every role),
`test_every_api_route_declares_exactly_one_permission` (a new route without a permission fails
CI), `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers`,
`test_proxy_mode_unknown_api_path_is_denied_by_default`,
`test_proxy_mode_rejects_ambiguous_or_malformed_identity`, `test_misconfiguration_fails_closed`.
Full details: [api/README.md](../api/README.md#security).

## 4. Four-eyes rule (separation of duties)

A model version change makes the use case's risk assessment `pending` and blocks deployment
until it is reviewed (governance engine, [GOVERNANCE.md](GOVERNANCE.md)). With authentication
on, an approving review (`approved`, `approved_with_conditions`) is refused with 403
`separation_of_duties` when the approver shares an identity key (user id, or e-mail compared
case-insensitively) with the identity that recorded the current model version through the API.
The check runs under the state lock (no race between check and write), the refusal itself is
audited, and `pending` / `rejected` remain possible. Each assessment records `reviewed_by`,
`reviewed_by_role` and `separation_of_duties` (`verified`, `unknown_recorder`, `not_required`,
`not_enforced`).

Limit: a model version defined in Git (`config/usecases/*.yaml`) has no API recorder
(`unknown_recorder`): its four-eyes control is the review of the Git change (protect the default
branch and require an approving review, see section 14, step 11).

Verify: `test_four_eyes_refuses_self_approval_and_records_the_reviewer`,
`test_four_eyes_is_checked_under_the_state_lock`,
`test_four_eyes_version_defined_in_git_has_no_api_recorder`,
`test_four_eyes_not_enforced_in_demo_mode_but_recorded`.

## 5. Tamper-evident audit trail

Every write (model version, assessment, 3D bookings, placement decision) appends one JSON record
to `FACTORY_STATE_DIR/audit.jsonl` ([`api/app/audit.py`](../api/app/audit.py)): who (actor, role,
auth mode), what (action, target, outcome, digests of the target before and after), when (UTC),
where (client IP, request id). Records hold ids, versions, decisions and hashes only, never a
request body, free text or a secret.

- **Hash chain.** `hash` is the SHA-256 of the canonical JSON of the record; `prev_hash` links to
  the previous record. Editing, deleting, inserting, reordering or truncating a line breaks the
  chain at that point.
- **No unaudited write.** The record is written with `O_APPEND` and `fsync` under the state file
  lock; if it cannot be written, the state change is rolled back and the API answers 503.
- **Verification.** `GET /api/v1/audit/verify` (approver, admin) recomputes the whole chain,
  reports the first broken link and reconciles the stored state with the log (a change made
  outside the API shows up as `drift`). Auditors can check a copy offline with the standard
  library only: `docker compose exec api python -m app.audit verify /data/audit.jsonl` (exit
  code 1 when broken).
- **Witnesses.** Each record is also emitted as a JSON log line (`factory.audit`) collected into
  Loki, and the head hash is published in `/api/v1/health`, `meta.security` and the metrics
  `factory_audit_chain_ok`, `factory_audit_events_total{action}`.

Limits (stated in the threat model): the chain is not keyed, so someone with write access to the
`api-state` volume could rewrite the whole file; the Loki copy and the published head hash make
that detectable, an HMAC key or anchoring to WORM storage is **Roadmap**. `audit.jsonl` has no
rotation yet (**Roadmap**); keep it with your backups.

Verify: `test_every_write_is_audited_with_a_valid_chain`, `test_tampering_is_detected` (edit,
edit and rehash, delete, reorder, garbage, truncation), `test_offline_verifier_cli`,
`test_state_changed_outside_the_api_is_reported_as_drift`,
`test_no_unaudited_write_when_the_audit_log_cannot_be_written`,
`test_concurrent_writers_keep_one_valid_chain`.

## 6. No token in the browser

- The dashboard only talks to the aggregation API, same origin, through the edge. It never calls
  Grafana, LiteLLM or Langfuse and holds no token or key.
- The Grafana token (a *Viewer* service account created by `grafana-bootstrap`) is written to a
  volume mounted read-only into the API and read server side from `GRAFANA_TOKEN_FILE`. The
  LiteLLM master key and the Langfuse keys stay in the API's server-side settings.
- **Restricted Grafana proxy**: `GET` only, to an allowlist of Grafana paths, and only *named*
  PromQL queries defined server side. The browser cannot send arbitrary PromQL.
- No endpoint returns a secret: the LiteLLM and Grafana answers are rebuilt field by field.

Verify: smoke test checks "Grafana token absent from every browser-facing response" and
"API lists the dashboards through its server-side Viewer token";
`test_no_secret_in_any_response_with_auth_on`.

## 7. Sensitive data: local GPU only, fail closed

Use cases carry a data class from C1 (public) to C4 (secret). Classes C3 and C4 are processed
**only** on the local GPU:

- **Placement policy** (`api/app/engines/placement.py`): a C3/C4 workload goes to the local GPU;
  when the GPU is down it is **queued**, never sent to the cloud or the VPS.
- **Gateway enforcement** ([`compose/litellm/factory_callbacks.py`](../compose/litellm/factory_callbacks.py)):
  every request whose `metadata.sensitivity` is C3/C4 is rerouted to `chat-sensitive`, whatever
  model it asked for. This covers chat and text completions and the Responses and Messages APIs
  (`FACTORY_ENFORCE_SENSITIVE_ROUTING=true`, the default).
- **No fallback outside the GPU** ([`compose/litellm/config.yaml`](../compose/litellm/config.yaml)):
  `chat-sensitive` and `embed-default` (corpora may contain C3/C4 documents) have no fallback.
  When the local GPU is unavailable they fail closed (HTTP error) and the Prometheus alert
  `FactorySensitiveRouteDown` fires.
- **Not logged**: C3/C4 prompts and completions are masked in Langfuse and excluded from every
  LiteLLM logging integration (`turn_off_message_logging`).
- **Remote GPU**: when the GPU is on another machine than the VPS, connect it through an
  encrypted tunnel (WireGuard) and never expose vLLM (section 14, step 5).

Verify: smoke test checks "chat-sensitive served by the local GPU", "C4 request on chat-default
rerouted to the local GPU", "local GPU down: chat-sensitive fails closed (no cloud/VPS fallback)",
"sensitive (C3) traces masked in Langfuse"; unit tests
`test_sensitive_request_rerouted_and_never_logged`,
`test_responses_and_messages_api_are_rerouted_too` in
[`compose/litellm/tests/test_guardrails.py`](../compose/litellm/tests/test_guardrails.py).

Residual risk: the data class is a label set by the calling application. A C3 document sent with
a C1 label would go to the cloud, with only the PII redaction below as a safety net. Classify at
the source and restrict who holds gateway credentials (see the threat model).

## 8. LLM guardrails

Run by the gateway callback before any request leaves the gateway
([compose/README.md](../compose/README.md#llm-guardrails-gateway)):

- **Prompt injection and jailbreak detection** on untrusted content (user, tool and retrieved
  text): instruction override, system prompt exfiltration, role-play jailbreaks, chat-template
  delimiters, encoded payloads, hidden Unicode tag and bidirectional characters, in English and
  French, after Unicode normalisation; padding beyond the scan budget is itself flagged.
  `FACTORY_GUARDRAIL_MODE=flag` (default: forwarded and tagged), `block` (HTTP 400 with a generic
  message) or `off`; an unknown value means `block`.
- **PII redaction** before any alias that may leave the local GPU: e-mail addresses, phone
  numbers, IBAN (length and mod-97 check), payment cards (Luhn) and French NIR (with its key)
  become `[REDACTED_<KIND>]` (`FACTORY_PII_REDACTION=true`, the default).
- **No prompt in logs**: `redact_messages_in_exceptions: true` (LiteLLM otherwise copies the
  start of the prompt into error messages), `store_prompts_in_spend_logs: false`; guardrail
  metadata and metrics hold rule ids and counts only.
- Metric `factory_llm_guardrail_events_total{type,action,usecase}`.

Verify: 76 unit tests, `python -m pytest -q compose/litellm/tests` (CI job `guardrails`);
smoke test checks "guardrail: crafted prompt injection flagged", "PII redacted before the cloud
target" (the simulated cloud received 0 raw e-mails), "gateway logs contain no prompt text".

Limits: detection is rule-based and will miss novel attacks; it reduces risk, it does not remove
it. Outputs are not scanned. See [THREAT_MODEL.md](THREAT_MODEL.md#5-llm-specific-threats).

## 9. API and edge hardening

API ([api/README.md](../api/README.md#rate-limiting-headers-request-ids-security-logs)):

- write rate limiting per actor (per client IP without authentication): 429 with `Retry-After`
  (`FACTORY_RATE_LIMIT_PER_MIN`, default 60, burst 20);
- request bodies limited to 64 KiB (`FACTORY_MAX_BODY_BYTES`; 413), and refused with 401 before
  being read when the caller is not authenticated;
- on every answer: `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; ...`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Cross-Origin-Resource-Policy: same-origin`, and `Cache-Control: no-store` under `/api/`;
- `X-Request-ID` propagated or generated, stored in the audit record; the client IP is taken from
  `X-Forwarded-For` only behind a trusted proxy;
- one JSON line on `factory.security` per denied request (never an untrusted header value, a
  query string or a body); metric `factory_security_denied_total{reason}`;
- CORS closed by default; the OpenAPI page requires `read` and loads Swagger UI from a pinned
  version with Subresource Integrity and a per-answer CSP nonce;
- the container runs as a non-root user with a read-only root filesystem.

Edge ([`compose/caddy/Caddyfile`](../compose/caddy/Caddyfile)):

- automatic TLS certificates (ACME) when `FACTORY_SCHEME=https` and `FACTORY_HOST` is a real
  domain; `Strict-Transport-Security` only over HTTPS (a plain-HTTP demo host is never pinned);
- `Content-Security-Policy` for the dashboard (Three.js vendored, no CDN by default),
  `Permissions-Policy` (camera, microphone, geolocation, payment, USB... denied),
  `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy`; `Server`, `Via` and
  `X-Powered-By` removed;
- request body limits (1 MB dashboard and API, 10 MB Grafana, 20 MB Langfuse), header and body
  timeouts, headers capped at 64 KB;
- `/.well-known/security.txt` (RFC 9116) on every host name.

Verify: `test_security_headers_on_every_api_response`, `test_headers_also_on_denials_and_metrics`,
`test_writes_are_rate_limited_per_actor_with_retry_after`,
`test_docs_page_is_pinned_with_sri_and_a_nonce`,
`test_client_ip_comes_from_x_forwarded_for_only_behind_a_trusted_proxy`; smoke test checks
"edge headers (HSTS absent over HTTP, Permissions-Policy, COOP/CORP, nosniff, no Server header)",
"/.well-known/security.txt", "edge request body limit (413 above 1 MB)".

## 10. Container and host hardening

Measured with `docker inspect` on the running stack ([compose/README.md](../compose/README.md#container-hardening-measured-with-docker-inspect-profile-sim)):

- every service: `no-new-privileges`, `cap_drop: [ALL]`, PID limit, memory and CPU limits,
  size-capped logs;
- every service of the default and `sim` profiles: read-only root filesystem (tmpfs for scratch
  paths) and a non-root user, with documented exceptions: `edge` keeps `NET_BIND_SERVICE` (its
  binary carries that file capability), the one-shot `volume-init` runs as root with `CHOWN` and
  `DAC_READ_SEARCH` and no network, `docker-socket-proxy` runs as root without capabilities by
  default (set `DOCKER_SOCKET_PROXY_USER=65534:<docker gid>` to run it as `nobody`);
- **Docker socket**: no service mounts it except `docker-socket-proxy`
  ([wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy), MIT), read-only, which
  answers Alloy only and lets through `GET` on ping, version, networks, container list, inspect
  and logs. Create, exec, archive, export and image calls are refused.

Verify: smoke test checks "no-new-privileges", "capabilities dropped", "read-only root",
"non-root", "PID limit on every container", "Docker socket mounted by docker-socket-proxy only",
"Docker API proxy: Alloy can list containers, cannot create one or list images (200 405 403)".

Residual risks: container inspect returns the environment of the containers, so Alloy can read
the secrets passed as environment variables (moving them to secret files is **Roadmap**). The
`vps` and `local-gpu` profiles (Ollama, vLLM, dcgm-exporter) run as their images' default user
without a read-only root filesystem and were not runtime-tested here (no GPU).

## 11. Secrets handling

- **No secret in the repository.** Values come from `.env`, which is not versioned;
  `.env.example` only holds placeholders (`change-me...`). `docker compose` refuses to start when
  a required secret is missing (`${VAR:?}`). In Codespaces, `.devcontainer/setup-env.sh`
  generates a random value for every placeholder.
- **Scanning.** gitleaks scans the working tree and the full git history on every push and pull
  request (CI job `secrets`, `scripts/scan_secrets.sh`); Trivy's secret scanner runs on the
  repository and on every image built here (`.github/workflows/security.yml`).
- **Least exposure at runtime.** Secrets are server side only (section 6); the Grafana token
  lives in a dedicated volume mounted read-only; the Valkey password is read from a private tmpfs
  file, never from the command line; LiteLLM removes API key information from what it sends to
  its logging integrations (`redact_user_api_key_info`).
- **Production**: render `.env` from a secrets manager at deploy time and rotate keys (section 14,
  steps 3 and 4).
  Native Docker secret files instead of environment variables are **Roadmap**.

## 12. Supply chain

- **Pinned CI.** Every third-party GitHub Action is pinned by full commit SHA (version in a
  comment, updated weekly by Dependabot after a 7-day cooldown); Docker tool images are pinned by
  digest; CI tools install from hash-pinned, wheel-only locks (`.github/tools/`); the Python
  dependencies of the images and of CI install from hash-pinned locks (`requirements*.txt`, compiled
  from `requirements*.in` by pip-compile, `pip install --require-hashes`); Dockerfile base images
  are pinned as `tag@sha256:digest`. Workflows have
  no permission by default (`permissions: {}`), each job asks only for what it uses, and no
  checkout keeps the token (`persist-credentials: false`). actionlint and zizmor check the
  workflows on every change (CI job `workflows`).
- **Scanning.** CodeQL (`security-extended`: Python, JavaScript, GitHub Actions) on every push,
  pull request and weekly; pip-audit on every pinned requirements file; Trivy on the repository
  (dependencies, Dockerfile misconfigurations, secrets) and on every image built here, daily;
  gitleaks on the full history; OpenSSF Scorecard weekly. CI fails on known fixable HIGH or
  CRITICAL vulnerabilities and on HIGH/CRITICAL misconfigurations or secrets. GitHub Code
  Scanning receives all repository findings, image secrets and image vulnerabilities with a
  fix at any severity. Image vulnerabilities without an upstream fix remain open risks in the
  vulnerability-only SARIF artifacts `trivy-image-vulnerabilities-*`, retained for 30 days by
  the `security` workflow. Accepted risks are listed with a reason and an expiry date in
  [`.github/trivy/trivyignore.yaml`](../.github/trivy/trivyignore.yaml).
- **SBOM.** CycloneDX and SPDX for the source tree and every image: `scripts/sbom.sh` locally,
  CI artifacts (`security.yml`), release assets.
- **Signed releases.** Images `ghcr.io/alebgl77/<name>` are signed keyless with Sigstore cosign
  and carry SLSA build provenance and SBOM attestations; release checksums are signed too. How to
  verify: [../SECURITY.md](../SECURITY.md#verify-a-release).
- **Licence policy.** Only permissive licences in the code we ship; AGPL services (Grafana, Loki)
  run as separate, unmodified containers. `scripts/check_licenses.py` (CI job `licenses`) checks
  every dependency, image and submodule against `licenses/third_party.yaml`.
- **Third-party images** are pinned by version and scanned; the latest results and the residual
  upstream findings are in [compose/README.md](../compose/README.md#image-scan-trivy). The six
  images built here pass the gate (no fixable HIGH or CRITICAL); the dashboard runs on
  `nginx-unprivileged` alpine-slim, without the optional nginx modules and their libraries.

The release workflow, Scorecard publication, CodeQL and SARIF uploads need GitHub to run; they are
checked statically (actionlint, zizmor in three modes) and their scripts are unit-tested.

## 13. Data residency and privacy

- **Self-hosted.** The whole stack runs on infrastructure you control: dashboard, API, gateway,
  traces (Langfuse with its ClickHouse, Postgres and S3-compatible SeaweedFS), metrics, logs and
  vector store. There is no SaaS dependency and no licence key.
- **What leaves your infrastructure**, and only through the `egress` network: C1/C2 prompts sent
  to the cloud or VPS LLM endpoints you configure (after PII redaction); ACME certificate
  requests from the edge; the OIDC exchange with your identity provider (profile `sso`); model
  downloads by Ollama or vLLM. C3/C4 data never leaves the local GPU.
- **No telemetry.** Usage reporting and update checks are switched off where the service has a
  setting: Langfuse (`TELEMETRY_ENABLED=false`), Grafana (`GF_ANALYTICS_*=false`), Loki
  (`analytics.reporting_enabled: false`), Alloy (`--disable-reporting`), Qdrant
  (`QDRANT__TELEMETRY_DISABLED`), vLLM (`VLLM_NO_USAGE_STATS`, `DO_NOT_TRACK`). Services on the
  internal `factory` network cannot reach the Internet at all.
- **No training on your data.** `factory.fine_tuning` is `false`; the API refuses any `retrain`
  or `fine_tune` action in the data loop.
- **Fictional data only** in the repository: every person, use case and cost in `config/` is
  invented.
- **Personal data the stack does process**: identities (user id, e-mail, groups) and client IP
  addresses in the audit log and security logs, reviewer e-mail (`reviewed_by`) in the registry.
  Define their retention with your data protection officer (section 14, step 8).
- **Language does not widen the surface**: `?lang=en|fr` only selects strings from the
  configuration and the message catalogue; unknown values fall back to the default language.

## 14. Production hardening guide

Apply in this order. Each step says how to check it.

1. **TLS.** Set `FACTORY_HOST` to a real domain (with DNS records for it, `grafana.` and
   `langfuse.`) and `FACTORY_SCHEME=https`: Caddy obtains and renews the certificates (TLS 1.2
   and 1.3) and sends HSTS. The Grafana session cookie follows (`GRAFANA_COOKIE_SECURE=auto`, the
   default, sets `Secure` with `https`). Check:
   `curl -sI https://<host>/ | grep -i strict-transport`.
2. **Single sign-on.** `FACTORY_AUTH=sso`, the `SSO_*` block of `.env.example`, start with
   `--profile sso`; then `FACTORY_AUTH_MODE=proxy` and `FACTORY_ROLE_MAP` from your groups
   (keep `admin` to a small break-glass group). Enforce multi-factor authentication in the
   identity provider. Configure Grafana and Langfuse with the same provider (they keep their own
   login) and disable their local admin accounts afterwards. Check: `security.status` is
   `enforced` with no warning (section 1), and `GET /api/v1/whoami` shows the expected roles.
3. **Secrets.** Replace every `change-me` value (for example `openssl rand -hex 32`; LiteLLM keys
   start with `sk-`). Keep them in a secrets manager (HashiCorp Vault, a cloud key vault...) and
   render `.env` at deploy time, owned by root, mode `0600`. Never commit it. Check:
   `grep -c change-me .env` returns 0.
4. **Key rotation.** Rotate at least every 90 days, at each departure of a person who had access,
   and immediately on suspicion:

   | Secret | How to rotate | Effect |
   |---|---|---|
   | `LITELLM_MASTER_KEY`, provider API keys (`CLOUD_API_KEY`...) | new value in `.env` (and at the provider), `docker compose up -d litellm api` | none for users |
   | `SSO_CLIENT_SECRET` | new secret in the identity provider, then `.env`, restart `oauth2-proxy` | none |
   | `SSO_COOKIE_SECRET` | new value, restart `oauth2-proxy` | every user signs in again (also the way to revoke all sessions) |
   | database, Valkey, ClickHouse, S3 passwords | change in the service, then `.env`, restart the dependants | short restart |
   | Grafana Viewer token | revoke it in Grafana (Administration, Service accounts), then `docker compose up grafana-bootstrap`: it revokes stale tokens and writes a new one | none |
   | `LITELLM_SALT_KEY`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` | plan a migration: they protect stored credentials and data | changing them makes stored items unreadable |

5. **Network.** Open only 80 and 443 (TCP, and UDP 443 for HTTP/3) to the Internet; keep 3001 and
   3002 bound to `127.0.0.1` (`EDGE_ALT_BIND`). Connect a remote GPU through WireGuard and never
   expose vLLM or Ollama. Restrict outbound traffic at the host firewall to your LLM providers,
   your identity provider and your ACME CA.
6. **Host.** Keep Docker Engine up to date, encrypt the disks that hold the volumes, run
   `docker-socket-proxy` as `nobody` (`DOCKER_SOCKET_PROXY_USER`), apply the host and daemon
   sections of the CIS Docker Benchmark (out of this repository's scope), synchronise the clock
   (NTP: audit timestamps are UTC).
7. **Backups.** Back up the volumes `postgres-data`, `clickhouse-data`, `seaweedfs-data`,
   `qdrant-data`, `grafana-data`, `caddy-data` and `api-state` (state and `audit.jsonl`),
   encrypted, off-site, with immutability (object lock or WORM) for the audit log. Test a restore
   every quarter, then run `python -m app.audit verify` on the restored file.
8. **Logging and retention.** Defaults are demo values: Loki keeps 7 days
   (`compose/loki/loki.yml`, `retention_period`), Prometheus 30 days (`PROMETHEUS_RETENTION`), and
   `audit.jsonl` is never rotated. Set retention from your policy (security logs are often kept
   12 months or more; personal data no longer than needed). Forward the `factory.security` and
   `factory.audit` JSON lines to your SIEM. The shipped rules alert on a broken audit chain
   (`FactoryAuditChainBroken`, `factory_audit_chain_ok == 0` for 1 minute, critical), on a spike of
   refusals (`FactorySecurityDenialsSpike`, more than 50 in 5 minutes) and on
   `FactorySensitiveRouteDown`; they have no Alertmanager: wire Prometheus to yours.
9. **Guardrails.** After tuning on your traffic, set `FACTORY_GUARDRAIL_MODE=block` for
   applications exposed to untrusted users; keep `FACTORY_PII_REDACTION=true` and
   `FACTORY_ENFORCE_SENSITIVE_ROUTING=true`.
10. **Updates.** Verify every release before you deploy it ([../SECURITY.md](../SECURITY.md#verify-a-release)),
    deploy by digest, and rescan third-party images weekly (new CVEs appear without any change).
11. **Repository governance** (for your fork): protect the default branch (required reviews and
    status checks `ci`, `codeql`, `security`), restrict who can create `v*` tags, require actions
    pinned to a full SHA, enable secret scanning push protection, Dependabot security updates
    and private vulnerability reporting.

## 15. Incident response basics

This is a starting point to adapt to your own process, not a substitute for it.

1. **Detect.** Signals produced by the stack: `factory_audit_chain_ok == 0` or `chain_ok: false`
   in `/api/v1/health`; a rise of `factory_security_denied_total{reason}` or of
   `event="request.denied"` lines in Loki; `factory_llm_guardrail_events_total{action="blocked"}`;
   `FactorySensitiveRouteDown`; `FactoryGatewayErrorRateHigh`; unexpected cost growth
   (`factory_llm_cost_eur_total`, FinOps alerts `budget_breach`, `retry_storm`).
2. **Contain.** Cut LLM traffic: `docker compose stop litellm` (nothing reaches the providers any
   more). Revoke every web session: rotate `SSO_COOKIE_SECRET` and restart `oauth2-proxy`. Rotate
   `LITELLM_MASTER_KEY` and the provider keys. Block an address at the host firewall. Remove a
   user from the identity provider group.
3. **Preserve evidence** before any change to the volumes: copy `audit.jsonl` and run the offline
   verifier on the copy; note the `head_hash` from `/api/v1/health`; export the Loki logs and
   `docker compose logs`; snapshot the volumes. Do not recreate the containers' volumes.
4. **Eradicate and recover.** Redeploy from released images verified with
   `.github/scripts/verify_image.sh`; restore data from a backup taken before the incident;
   verify the audit chain; rotate every secret the attacker may have seen (the environment of the
   containers is readable through `docker inspect`).
5. **Notify.** Check your obligations with your legal team; indicatively: NIS2 (Directive (EU)
   2022/2555, Art. 23) for essential and important entities: early warning within 24 hours,
   incident notification within 72 hours, final report within one month; DORA (Regulation (EU)
   2022/2554, Art. 19) for financial entities: initial, intermediate and final reports of major
   ICT-related incidents, with time limits set by Commission Delegated Regulation (EU) 2025/301;
   GDPR (Art. 33): personal data breach notified to the supervisory authority within 72 hours.
6. **Learn.** Write a blameless post-incident review, add a test that would have caught the issue,
   and report any vulnerability of this project as described in [../SECURITY.md](../SECURITY.md).

## Reporting a vulnerability

See [../SECURITY.md](../SECURITY.md): GitHub private vulnerability reporting on
`alebgl77/ai-factory`, acknowledgement within 72 hours, triage within 7 days. Never publish a
proof of concept or a secret in a public issue.
