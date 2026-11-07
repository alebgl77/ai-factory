# Changelog

## 1.1.1 (2026-11)

- Updates Debian `zlib1g` in the five Python images to address CVE-2026-76844 and CVE-2026-85091.
- Includes the [1.1.0 functionality](https://github.com/alebgl77/ai-factory/blob/main/CHANGELOG.md#110-2026-11)
  for portfolio tasks, local sub-agent execution, and Workbench decision reconciliation. Follow
  the [interactive operations guide](https://alebgl77.github.io/ai-factory/docs/#production)
  to install by verified image digest and qualify the private environment.

The `v1.1.0` source tag remains unchanged. Its image security gate stopped publication of
the signed release. Use `v1.1.1` for the signed images.

## 1.1.0 (2026-11)

- Portfolio and tasks connect company objectives to projects, team assignments, dependencies,
  work evidence and token usage. Business-unit policies and verified SSO groups govern planning,
  assignment, technical review and agent autonomy.
- Manual and automatic delegation create up to four child tasks per parent. The optional
  integrated runner executes configured reading and preparation tasks through scoped LiteLLM
  virtual keys and the local-only C3/C4 `chat-sensitive` route. It records model output and
  provider-reported usage, and blocks interrupted attempts for operator review.
- In the private `portfolio` Workbench mode, a separate approver reviews the exact handoff
  revision in the Decision Companion. Approval, rejection and requests for changes return to the
  portfolio. The rationale and approver identity remain in the Workbench.
- GitHub Pages serves the fictional demo and the [interactive operations guide](https://alebgl77.github.io/ai-factory/docs/#production).
  The guide covers [private activation](https://alebgl77.github.io/ai-factory/docs/#sous-agents),
  validation and [recovery](https://alebgl77.github.io/ai-factory/docs/#reprise). A Compose overlay
  installs the first-party release images by verified digest.
- The dashboard runtime pins Alpine zlib to `1.4-r0` to address CVE-2026-4738.

For private activation, use proxy SSO, a business-unit policy and one API process. If you
piloted an earlier portfolio build, review existing pending handoffs manually. Automatic
reconciliation applies to handoffs recorded by this release.

## 1.0.0 (2026-11)

First public release.

- 3D digital twin of an AI factory (open space, secure lab with local GPU rack, R&D Lab, Skills
  Lab, Agent Ops room, War Room, focus pods), live and replayable timeline, bookings.
- First-person **visit mode**: drop the character on the floor, walk, interact.
- Twelve articulated fictional 3D avatars with multi-view portraits, fitted necks and shoulders,
  and selectable close-up angles.
- Animated inter-unit exchanges in the demo and read-only tracking of authorized Workbench
  requests and decisions in the protected dashboard.
- Private work history by agent or user, with recorded duration, provider token counts where
  available, action outcomes and references to files and supporting work. Explicitly authorized
  global administrators can select a cross-user view.
- Factory console: AI risk register (EU AI Act incl. the 2026 Digital Omnibus, NIST AI RMF,
  ISO/IEC 42001 crosswalk), re-assessment on model version change, FinOps showback/chargeback with
  8 waste rules, LLMOps/RAG pipeline with evaluation gates and automatic rollback, data loop
  without retraining, hybrid cloud/VPS/local-GPU placement, Agent Ops, self-service catalogue,
  Skills and R&D labs.
- Aggregation API (FastAPI) and full Docker stack: LiteLLM gateway with an MIT cost callback,
  Langfuse (MIT core), Prometheus, Grafana and Loki (separate AGPL services), Alloy, Qdrant,
  SeaweedFS, Valkey; `sim` profile runs everything without any API key.
- English and French.
- Application security (API): identity from an SSO proxy accepted only from the fixed edge
  address (`FACTORY_AUTH_MODE=proxy`, deny by default), role-based access control with
  separation of duties (four-eyes approval of model versions), tamper-evident SHA-256 hash-chained
  audit log with a verify endpoint and an offline verifier, write rate limiting, security headers
  and request ids, security posture in `/api/v1/health` and `meta.security`.
- Stack hardening: every container non-root with a read-only root filesystem, no capability,
  no-new-privileges and PID and memory limits; Docker socket behind a GET-only allowlist proxy;
  optional OpenID Connect single sign-on (`--profile sso`, oauth2-proxy); edge security headers
  and RFC 9116 `security.txt`; identity headers only ever set by the edge (tested on every route
  in every authentication mode); the dashboard CSP is kept off `/api/*`, and the OpenAPI page
  works through the edge (pinned Swagger UI, Subresource Integrity, per-answer nonce); security
  alerts (broken audit chain, spike of refusals); Grafana session cookie `Secure` with HTTPS.
- LLM guardrails at the gateway: prompt-injection and jailbreak detection (English and French,
  flag or block), PII redaction before any target that may leave the local GPU, no prompt in logs.
- Supply chain: SHA-pinned least-privilege workflows, CodeQL, OpenSSF Scorecard, pip-audit, Trivy
  (repository and images), CycloneDX/SPDX SBOMs, signed releases (cosign keyless, SLSA provenance
  and SBOM attestations), Dependabot with cooldown; hash-pinned Python locks installed with
  `--require-hashes`, base images pinned by digest, dashboard image on nginx-unprivileged
  alpine-slim (no HIGH or CRITICAL finding).
- Security documentation: security policy with private reporting and release verification
  (SECURITY.md), threat model (STRIDE + OWASP LLM Top 10 2025), controls matrix (ASVS 5.0.0, NIST
  SSDF, CIS Docker, ISO/IEC 27001:2022, NIS2 and DORA pointers), production hardening and incident
  response guides, in English and French.
