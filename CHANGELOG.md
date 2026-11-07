# Changelog

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
