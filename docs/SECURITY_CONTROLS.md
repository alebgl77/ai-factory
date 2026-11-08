# Security controls matrix

*[Version française](fr/SECURITY_CONTROLS.md)*

This matrix maps each **implemented** control of the Virtual AI Factory to common security
frameworks, and says how to verify it yourself: a test, a CI job or a command. It is written to
help a security team assess the stack and reuse the evidence in its own compliance work.

> **Indicative, not a certification.** The mapping shows where a control contributes to a
> requirement; it does not claim that the stack, or your deployment of it, complies with any
> framework. Many requirements also need organisational measures that only the operator can
> provide. Gaps are listed in [section 3](#3-known-gaps-and-roadmap).

Architecture and context: [SECURITY.md](SECURITY.md). Threats and residual risks:
[THREAT_MODEL.md](THREAT_MODEL.md).

## Frameworks and identifiers

| Column | Framework | Identifiers used |
|---|---|---|
| LLM | [OWASP Top 10 for LLM Applications 2025](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications) | `LLM01` ... `LLM10` (all `:2025`) |
| ASVS | [OWASP ASVS 5.0.0](https://github.com/OWASP/ASVS) (May 2025) | requirement ids `<chapter>.<section>.<requirement>`, to be read as `v5.0.0-<id>`, e.g. `8.2.1` = `v5.0.0-8.2.1` |
| SSDF | NIST SP 800-218, Secure Software Development Framework (SSDF) v1.1 (February 2022) | practices and tasks, e.g. `PS.3.2`. SSDF v1.2 (SP 800-218 Rev. 1) was published as a draft in December 2025; SP 800-218A adds a profile for generative AI |
| CIS | CIS Docker Benchmark | recommendation numbers as in v1.6.0, the edition automated by [docker-bench-security](https://github.com/docker/docker-bench-security); v1.8.0 was published in 2025 and numbering can shift between editions, so match by title (given in [section 2](#2-cis-docker-benchmark-titles)) |
| ISO | ISO/IEC 27001:2022, Annex A | control numbers, e.g. `8.15` (Logging) |

Pointers for NIS2 and DORA are in [section 4](#4-pointers-for-nis2-and-dora).

How to read "How to verify": test names are in [`api/tests/test_security.py`](../api/tests/test_security.py)
unless another file is named, and run in the CI job `api-tests`
([`.github/workflows/ci.yml`](../.github/workflows/ci.yml)); "smoke" is a check of
`scripts/smoke_stack.sh --no-up` on the running stack (run in CI only on a manual
`workflow_dispatch` with `smoke: true`); "guardrail tests" is
`python -m pytest -q compose/litellm/tests` (local, not yet in CI).

## 1. Controls

### 1.1 Identity and access

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C01 | **Single sign-on** at the edge: OIDC through oauth2-proxy, PKCE `S256`, `__Host-` cookie `Secure` `HttpOnly` `SameSite=Lax`, 8 h lifetime, domain and group gates (`docker-compose.yml` service `oauth2-proxy`, `compose/caddy/Caddyfile` `auth-sso`) | - | 6.8.4, 10.2.1, 3.3.1, 3.3.2, 3.3.3, 3.3.4, 7.3.2 | - | - | 5.16, 5.17, 8.5 | `compose/sso/test_sso_e2e.sh` (17 checks, needs Docker); `docker compose --profile sso config -q` |
| C02 | **Identity header integrity**: edge deletes client `X-Forwarded-User/-Email/-Groups` in every mode; API reads them only from `FACTORY_TRUSTED_PROXIES` (default: the edge's fixed address on network `edge-identity`) | - | 4.1.3, 15.3.4 | - | - | 8.5, 8.22 | `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers`, `test_client_ip_comes_from_x_forwarded_for_only_behind_a_trusted_proxy`; SSO e2e "forged headers never reach the API" |
| C03 | **Deny by default, fail closed**: every `/api/*` path needs an identity in proxy mode; unknown mode, catch-all CIDR, unknown role are refused and reported (`api/app/security.py`) | - | 8.2.1, 16.5.3 | PW.9.1, PW.9.2 | - | 8.3, 8.9 | `test_proxy_mode_unknown_api_path_is_denied_by_default`, `test_misconfiguration_fails_closed`, `test_proxy_mode_without_role_mapping_denies_everyone` |
| C04 | **RBAC**: five roles from SSO groups; one permission per route (`ROLE_PERMISSIONS`, `require()`) | LLM06 (for factory actions) | 8.1.1, 8.2.1, 8.3.1 | PW.8.2 | - | 5.15, 5.18, 8.2, 8.3 | `test_proxy_mode_authorization_matrix`, `test_every_api_route_declares_exactly_one_permission`, `test_role_permission_table_keeps_duties_separate` |
| C05 | **Four-eyes rule**: an identity cannot approve a model version it recorded; checked under the state lock; refusal audited (`api/app/state.py`) | - | 2.3.5, 15.4.2 | - | - | 5.3 | `test_four_eyes_refuses_self_approval_and_records_the_reviewer`, `test_four_eyes_is_checked_under_the_state_lock` |

### 1.2 Audit, logging and monitoring

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C06 | **Tamper-evident audit trail**: SHA-256 hash chain of every write, UTC timestamps, actor, role, request id, client IP; rollback if the record cannot be written; verify endpoint and offline CLI; drift detection (`api/app/audit.py`) | - | 16.2.1, 16.2.2, 16.3.3, 16.4.2 (tamper-evident), 11.4.1 | - | - | 8.15, 5.28, 5.33 | `test_every_write_is_audited_with_a_valid_chain`, `test_tampering_is_detected`, `test_no_unaudited_write_when_the_audit_log_cannot_be_written`, `test_offline_verifier_cli`; `GET /api/v1/audit/verify`; `docker compose exec api python -m app.audit verify /data/audit.jsonl` |
| C07 | **Security event logs**: one JSON line per denied request (no untrusted header value, query or body); audit records mirrored to Loki; metrics `factory_security_denied_total`, `factory_audit_chain_ok` (`api/app/seclog.py`, `api/app/metrics.py`) | - | 16.3.2, 16.4.1, 16.4.3 (Loki copy), 16.2.4 | - | - | 8.15, 8.16 | `test_denials_are_logged_as_json_without_spoofed_values`, `test_audit_appends_are_witnessed_in_the_logs`, `test_security_metrics` |
| C08 | **Published security posture**: `security` in `/api/v1/health` and `meta.security` (`demo`, `enforced`, `degraded` + reason codes), no secret | - | - | - | - | 8.16, 5.36 | `test_posture_in_health_and_snapshot_meta`; `curl -s <host>/api/v1/health \| jq .security` |
| C09 | **Operational alerts**: sensitive route down, gateway error rate, waste and budget alerts, target down (`compose/prometheus/rules/factory-alerts.yml`) | LLM10 | 16.3.4 | - | - | 8.16 | CI job `compose` runs `promtool check config` |

### 1.3 Application and edge hardening

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C10 | **Write rate limiting** per actor or client IP, 429 with `Retry-After` | LLM10 (API side) | 2.4.1, 15.2.2 | - | - | 8.6 | `test_writes_are_rate_limited_per_actor_with_retry_after`, `test_rate_limit_keys_on_client_ip_without_authentication` |
| C11 | **Request limits**: API body 64 KiB (401 before reading when unauthenticated); edge body 1/10/20 MB, header and body timeouts, 64 KB headers | LLM10 | 15.2.2 | - | - | 8.6 | `test_unauthenticated_large_body_is_refused_before_it_is_read`; smoke "edge request body limit (413 above 1 MB)" |
| C12 | **Security headers**: API CSP `default-src 'none'`, nosniff, `Referrer-Policy`, `X-Frame-Options`, CORP, `Cache-Control: no-store`; edge CSP, Permissions-Policy, COOP, CORP, HSTS over HTTPS only | - | 3.4.1, 3.4.3, 3.4.4, 3.4.5, 3.4.6, 3.4.8, 14.3.2 | - | - | 8.26 | `test_security_headers_on_every_api_response`, `test_headers_also_on_denials_and_metrics`; smoke "security headers", "edge headers" |
| C13 | **External resources with integrity**: Swagger UI pinned with SRI and a per-answer CSP nonce, behind `read`; Three.js vendored in the dashboard image (no CDN) | - | 3.6.1 | PW.4.1 | - | 8.26 | `test_docs_page_is_pinned_with_sri_and_a_nonce`; smoke "dashboard served with vendored Three.js (no CDN)" |
| C14 | **No information leakage**: `Server`, `Via`, `X-Powered-By` removed; API `/metrics` not routed by the edge; generic error and guardrail messages | - | 13.4.5, 13.4.6, 16.5.1 | - | - | 8.9 | smoke "API /metrics not exposed through the edge", "edge headers (... no Server header)" |
| C15 | **No token in the browser**; Grafana proxy limited to GET on allowlisted paths and named PromQL queries; answers rebuilt field by field | - | 14.2.6, 14.3.3, 13.2.4, 15.3.1 | - | - | 8.3, 8.12 | smoke "Grafana token absent from every browser-facing response"; `test_no_secret_in_any_response_with_auth_on`; `api/tests/test_grafana_and_live.py` (`test_non_allow_listed_path_refused_without_http_call`, `test_grafana_client_only_issues_get`, `test_dashboards_through_api_token_never_returned`) |
| C16 | **TLS**: automatic certificates (ACME, TLS 1.2 and 1.3) when `FACTORY_SCHEME=https` | - | 12.1.1, 12.2.1, 12.2.2 | - | - | 8.24, 8.20 | `curl -sI https://<host>/`; CI job `compose` runs `caddy validate` |
| C17 | **Vulnerability disclosure**: `SECURITY.md` (private reporting, 72 h / 7 days targets, safe harbour), RFC 9116 `security.txt` on every host | - | 15.1.1 | RV.1.3 | - | 8.8, 6.8 | smoke "/.well-known/security.txt (RFC 9116 ...)"; [`../SECURITY.md`](../SECURITY.md) |

### 1.4 LLM and data protection

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C18 | **Sensitive data on the local GPU only, fail closed**: the planner recommends waiting during an outage and does not enqueue work. The gateway reroutes C3/C4 to `chat-sensitive` for every generative API. Direct requests fail if the local GPU is unavailable. No external fallback for `chat-sensitive` and `embed-default` (`api/app/engines/placement.py`, `compose/litellm/factory_callbacks.py`, `compose/litellm/config.yaml`) | LLM02, LLM08 | 14.2.3, 16.5.3 | - | - | 5.12, 5.14, 8.12, 5.23 | smoke "chat-sensitive served by the local GPU", "C4 request on chat-default rerouted to the local GPU", "local GPU down: chat-sensitive fails closed"; `api/tests/test_placement.py` (`test_sensitive_is_queued_when_local_down_never_cloud_or_vps`); guardrail tests `test_responses_and_messages_api_are_rerouted_too` |
| C19 | **PII redaction** before aliases that may leave the GPU: e-mail, phone, IBAN (mod-97), card (Luhn), French NIR | LLM02 | 14.2.3, 14.2.6 | - | - | 8.11, 8.12, 5.34 | guardrail tests `test_pii_redacted`, `test_pii_false_positives`, `test_pii_redacted_for_cloud_alias`; smoke "PII redacted before the cloud target" |
| C20 | **Prompt injection and jailbreak detection** (EN and FR rules, hidden Unicode, encoded payloads, padding), `flag` / `block` / `off`, unknown value = `block`; metric `factory_llm_guardrail_events_total` | LLM01, LLM07 | 16.3.3 | - | - | 8.16, 8.26 | guardrail tests `test_injection_detected` (25 cases), `test_benign_not_flagged` (12), `test_block_mode_refuses`, `test_unknown_mode_fails_closed`; smoke "guardrail: crafted prompt injection flagged" |
| C21 | **No prompt in logs**: C3/C4 masked in Langfuse and `turn_off_message_logging`; `redact_messages_in_exceptions`; `store_prompts_in_spend_logs: false`; guardrail metadata holds rule ids only | LLM02 | 16.2.5, 14.2.4, 16.5.1 | - | - | 8.11, 8.15, 5.34 | smoke "gateway logs contain no prompt text", "sensitive (C3) traces masked in Langfuse"; guardrail tests `test_sensitive_request_rerouted_and_never_logged`, `test_detection_returns_rule_ids_only` |
| C22 | **No training on customer data**: `fine_tuning: false`; the data loop refuses `retrain`, `fine_tune` and similar actions | LLM04 | - | - | - | 5.34 | `api/tests/test_dataloop.py` (`test_retrain_and_fine_tune_are_refused`, `test_requested_retrain_in_signal_is_refused`) |
| C23 | **Model change control**: every model version change sets the assessment to `pending` and blocks deployment (`reevaluation_required`); evaluation gates per risk tier | LLM03, LLM09 | - | - | - | 8.32 | `api/tests/test_risk.py` (`test_reevaluation_on_model_version_change`), `api/tests/test_pipeline.py` (`test_deploy_blocked_when_risk_reassessment_required`, `test_gate_thresholds_by_tier`) |
| C24 | **Cost visibility** (denial of wallet detection): cost per use case and model, budgets per cost centre, waste rules `budget_breach` and `retry_storm`, gateway timeouts and a single retry | LLM10 | 15.1.3 | - | - | 8.6, 8.16 | `api/tests/test_costs.py`; smoke "callback metrics in Prometheus" |
| C25 | **Data residency, no telemetry**: self-hosted; telemetry off in Langfuse, Grafana, Loki, Alloy, Qdrant, vLLM; internal services without Internet access | LLM02 | 14.2.3 | - | - | 5.23, 5.34 | `docker compose config` (settings `TELEMETRY_ENABLED`, `GF_ANALYTICS_*`, `--disable-reporting`, `QDRANT__TELEMETRY_DISABLED`, `VLLM_NO_USAGE_STATS`); `compose/loki/loki.yml` |

### 1.5 Infrastructure and containers

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C26 | **Network segmentation**: `factory` internal (no Internet); `egress` for edge, gateway, oauth2-proxy and model servers only; only the edge publishes ports, 3001/3002 on `127.0.0.1` | LLM02 | 13.2.4, 13.2.5 | - | 5.9, 5.14 | 8.20, 8.22 | `docker compose config` (networks); `docker network inspect ai-factory_factory`; smoke "API /metrics not exposed through the edge" |
| C27 | **Container hardening**: `no-new-privileges`, `cap_drop: ALL` (documented exceptions), non-root users, read-only root filesystems, PID, memory and CPU limits, capped logs (`docker-compose.yml` anchor `x-hardening`) | - | 13.2.2 | - | 4.1, 5.4, 5.5, 5.11, 5.12, 5.13, 5.26, 5.29 | 8.9, 8.27 | smoke "no-new-privileges", "capabilities dropped", "read-only root", "non-root", "PID limit on every container" (`docker inspect` of every running container, exceptions allowed by name) |
| C28 | **Docker socket isolation**: only `docker-socket-proxy` mounts it (read-only), GET allowlist, client `alloy` only, internal network `docker-api` | - | 13.2.2 | - | 5.32 (one documented, filtered exception) | 8.2, 8.3 | smoke "Docker socket mounted by docker-socket-proxy only", "Docker API proxy: Alloy can list containers, cannot create one or list images (200 405 403)" |
| C29 | **Health checks** on long-running services; restart policies | - | - | - | 4.6, 5.27 | 8.6 | smoke "services healthy (profile sim)" |
| C30 | **Secrets handling**: none in the repository; `.env` not versioned; compose refuses to start without required secrets; gitleaks on tree and full history; Trivy secret scanner; Valkey password in a private tmpfs file; Grafana token in a read-only volume | - | 13.3.2, 13.2.3 (placeholders must be replaced) | PS.1.1 | 4.10 | 5.17, 8.12 | CI job `secrets` (`scripts/scan_secrets.sh . --git`); `security.yml` job `trivy-repo` |

### 1.6 Secure development and supply chain

| Id | Control (where) | LLM | ASVS | SSDF | CIS | ISO | How to verify |
|---|---|---|---|---|---|---|---|
| C31 | **Hardened CI**: actions pinned by full SHA, `permissions: {}` and per-job permissions, `persist-credentials: false`, timeouts, tool images pinned by digest, CI tools from hash-pinned locks | LLM03 | 15.2.4 | PO.3.2, PO.5.1, PS.1.1 | - | 8.25, 5.21 | CI job `workflows` (actionlint 1.7.12, zizmor in [`ci.yml`](../.github/workflows/ci.yml)) |
| C32 | **SAST**: CodeQL `security-extended` for Python, JavaScript/TypeScript and GitHub Actions, on push, pull request and weekly | - | - | PW.7.2, PO.4.1 | - | 8.28, 8.29 | [`codeql.yml`](../.github/workflows/codeql.yml); results in the Security tab |
| C33 | **Dependency and image scanning**: pip-audit on every pinned requirements file (pins checked); Trivy on the repository and on every image built here, daily; gate on fixable HIGH/CRITICAL; accepted risks with reason and expiry | LLM03 | 15.1.1, 15.2.1 | PW.4.4, RV.1.1, RV.1.2, RV.2.2 | 4.4 | 8.8, 5.21 | [`security.yml`](../.github/workflows/security.yml) jobs `pip-audit`, `trivy-repo`, `images`; [`.github/trivy/trivyignore.yaml`](../.github/trivy/trivyignore.yaml) |
| C34 | **SBOM**: CycloneDX and SPDX for the source tree and every image (syft pinned by digest) | LLM03 | 15.1.2 | PS.3.2 | - | 5.9, 5.21 | `scripts/sbom.sh --images`; `security.yml` jobs `sbom-source`, `images` (artifacts) |
| C35 | **Signed, attested releases**: Trivy gate before push, cosign keyless signature, SLSA build provenance, SPDX and CycloneDX attestations, signed `SHA256SUMS`, self-verification | LLM03 | 15.2.4 | PS.2.1, PS.3.1, PS.3.2 | 4.5, 4.12 (for operators who verify) | 5.21, 8.32 | [`release.yml`](../.github/workflows/release.yml); `.github/scripts/verify_image.sh IMAGE@DIGEST`; commands in [`../SECURITY.md`](../SECURITY.md#verify-a-release) |
| C36 | **Dependency updates**: Dependabot weekly with a 7-day cooldown (30 days for Python majors), security updates grouped; exact `==` pins | LLM03 | 15.2.1 | PW.4.1, RV.2.2 | 4.4 | 8.8 | [`dependabot.yml`](../.github/dependabot.yml); `security.yml` step "Every requirement is pinned with ==" |
| C37 | **Licence policy**: only permissive licences in shipped code; AGPL services separate and unmodified; inventory with version drift checks | LLM03 | 15.1.4 | PW.4.4 | - | 5.32 | CI job `licenses` (`python scripts/check_licenses.py`, `licenses/third_party.yaml`) |
| C38 | **Security testing in CI**: 482 API tests including 238 security tests; configuration validation of every service; workflow linting | - | - | PW.8.2, PO.4.1 | - | 8.29 | CI jobs `api-tests`, `compose`, `workflows`; `scripts/smoke_stack.sh` (manual dispatch) |
| C39 | **Threat model and documented security decisions** | - | 15.1.3, 15.1.5 | PW.1.1, PO.1.1 | - | 5.8, 8.27 | [THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY.md](SECURITY.md) |

## 2. CIS Docker Benchmark titles

Numbers as in v1.6.0; match by title in the edition you use.

| Number | Recommendation | Status |
|---|---|---|
| 4.1 | Ensure that a user for the container has been created | default and `sim` profiles: yes (exceptions in C27); `vps` and `local-gpu`: image default user |
| 4.2 | Ensure that containers use only trusted base images | official images, pinned by version, licence-checked |
| 4.4 | Ensure images are scanned and rebuilt to include security patches | Trivy daily on images built here; third-party scan table in [compose/README.md](../compose/README.md#image-scan-trivy) |
| 4.5 | Ensure Content trust for Docker is Enabled | not Docker Content Trust: images of this repository are signed with cosign instead (C35) |
| 4.6 | Ensure that HEALTHCHECK instructions have been added to container images | health checks defined in `docker-compose.yml`; one accepted exception (`grafana-bootstrap`, one-shot) |
| 4.10 | Ensure secrets are not stored in Dockerfiles | yes (gitleaks and Trivy) |
| 4.12 | Ensure all signed artifacts are validated | `verify_image.sh` for released images (operator step) |
| 5.4 | Ensure that Linux kernel capabilities are restricted within containers | `cap_drop: ALL` everywhere; `edge` adds `NET_BIND_SERVICE`, `volume-init` adds `CHOWN`, `DAC_READ_SEARCH`; `dcgm-exporter` keeps its default set |
| 5.5 | Ensure that privileged containers are not used | none |
| 5.9 | Ensure that only needed ports are open on the container | only `edge` publishes ports |
| 5.11 | Ensure that the memory usage for containers is limited | yes |
| 5.12 | Ensure that CPU priority is set appropriately on containers | CPU limits set |
| 5.13 | Ensure that the container's root filesystem is mounted as read only | default and `sim` profiles: yes; `vps` and `local-gpu`: no |
| 5.14 | Ensure that incoming container traffic is bound to a specific host interface | alternate ports 3001/3002 on `127.0.0.1`; 80/443 public by design |
| 5.26 | Ensure that the container is restricted from acquiring additional privileges | `no-new-privileges` everywhere |
| 5.27 | Ensure that container health is checked at runtime | yes (smoke "services healthy") |
| 5.29 | Ensure that the PIDs cgroup limit is used | yes |
| 5.32 | Ensure that the Docker socket is not mounted inside any containers | one exception: `docker-socket-proxy`, read-only, filtered (C28) |

The host and daemon sections (1 to 3) and Docker Swarm (7) are the operator's responsibility.

## 3. Known gaps and roadmap

What a reviewer will not find today, stated so it can be planned:

| Area | Gap | Framework reference | Status |
|---|---|---|---|
| Default posture | the default configuration is the key-less demo (no authentication, every request acts as `admin`), flagged as `security.status: demo` | SSDF PW.9.2 | by design for the demo; production settings in [SECURITY.md](SECURITY.md#14-production-hardening-guide) |
| Cost control | spend is measured and alerted, not capped per application at the gateway | LLM10 | **Roadmap**: per-application gateway keys with budgets and rate limits |
| Secrets | secrets passed as environment variables (readable through `docker inspect`); no built-in secrets manager | ASVS 13.3.1, 13.3.4 | **Roadmap**: Docker secret files; operator: secrets manager and rotation ([SECURITY.md](SECURITY.md#14-production-hardening-guide)) |
| Internal transport | plain HTTP between containers on internal networks | ASVS 12.3.1, 12.3.3, 12.3.5 | **Roadmap** for multi-host deployments (mutual TLS) |
| Store authentication | Prometheus, Loki and Qdrant accept unauthenticated requests inside the internal network | ASVS 13.2.1; ISO 8.3 | open: single-tenant host assumed |
| Audit chain | not keyed; no rotation | ASVS 16.4.2 | **Roadmap**: HMAC or anchoring, rotation; operator: WORM backups |
| Dashboard CSP | `'unsafe-inline'` scripts (single-file page) | ASVS 3.4.3 | **Roadmap**: nonce- or hash-based CSP |
| Pinning | runtime Python requirements not hash-locked; base and third-party images pinned by tag, not digest | SSDF PW.4.4; ASVS 15.2.4 | **Roadmap** |
| Model artefacts | model weights not pinned by revision or hash | LLM03 | **Roadmap** |
| MFA | delegated to the identity provider | ASVS 6.3.3; NIS2 Art. 21(2)(j) | operator: enforce MFA in the IdP |
| CI coverage | guardrail unit tests, SSO end-to-end test and the full smoke test are not run on every change | SSDF PW.8.2 | **Roadmap**: add them to CI |
| GPU profiles | `vps` and `local-gpu` services run as root without read-only root filesystems; not runtime-tested here | CIS 4.1, 5.13 | operator: test on the target host |

## 4. Pointers for NIS2 and DORA

Indicative only: these regulations apply to organisations, not to software. The table shows which
controls and documents can support an entity's own measures.

### NIS2: Directive (EU) 2022/2555

| Article | Measure | Supporting controls and documents |
|---|---|---|
| 21(2)(a) | risk analysis and information system security policies | [THREAT_MODEL.md](THREAT_MODEL.md), C39 |
| 21(2)(b) | incident handling | C06, C07, C09; [SECURITY.md, section 15](SECURITY.md#15-incident-response-basics) |
| 21(2)(c) | business continuity, backup management, disaster recovery | fail-closed sensitive route (C18), fallbacks for C1/C2; backups: [SECURITY.md, section 14, step 7](SECURITY.md#14-production-hardening-guide) (operator) |
| 21(2)(d) | supply chain security | C31 to C37 |
| 21(2)(e) | security in acquisition, development and maintenance, including vulnerability handling and disclosure | C17, C32, C33, C36, C38 |
| 21(2)(f) | assessing the effectiveness of measures | C38, OpenSSF Scorecard ([`scorecard.yml`](../.github/workflows/scorecard.yml)), C08 |
| 21(2)(g) | basic cyber hygiene and training | C27, C30; AI literacy modules (`config/skills.yaml`, [SKILLS_RND.md](SKILLS_RND.md)) |
| 21(2)(h) | cryptography and encryption | C16, C06 (SHA-256 chain), C35 (Sigstore signatures) |
| 21(2)(i) | human resources security, access control, asset management | C01 to C05, C34 (inventory) |
| 21(2)(j) | multi-factor or continuous authentication | C01 (MFA enforced by the identity provider) |
| 23 | reporting obligations (24 h early warning, 72 h notification, final report within one month) | [SECURITY.md, section 15](SECURITY.md#15-incident-response-basics), C06 evidence |

For digital infrastructure and digital service providers, see also Commission Implementing
Regulation (EU) 2024/2690 (technical and methodological requirements).

### DORA: Regulation (EU) 2022/2554 (financial entities, applicable since 17 January 2025)

| Article | Topic | Supporting controls and documents |
|---|---|---|
| 6 | ICT risk management framework | [SECURITY.md](SECURITY.md), [THREAT_MODEL.md](THREAT_MODEL.md), this matrix |
| 8 | identification (assets, dependencies) | THREAT_MODEL assets (section 3), C34 (SBOM), `licenses/third_party.yaml` |
| 9 | protection and prevention | C01 to C05, C12, C16, C18, C26 to C30 |
| 10 | detection | C07, C08, C09, C20 |
| 11, 12 | response and recovery; backup and restoration | [SECURITY.md, sections 14 and 15](SECURITY.md#15-incident-response-basics) (operator procedures) |
| 17, 19 | ICT-related incident management and reporting of major incidents | C06, C07; time limits in Commission Delegated Regulation (EU) 2025/301 |
| 24, 25 | digital operational resilience testing | C38, C32, C33 |
| 28 | ICT third-party risk | C31 to C37; LLM providers receive C1/C2 only (C18) |

Detailed ICT risk management requirements: Commission Delegated Regulation (EU) 2024/1774.

For US buyers: the SSDF column above is the basis of the CISA Secure Software Development
Attestation Form used by US federal agencies.
