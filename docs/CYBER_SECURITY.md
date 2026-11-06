# Cyber operations and audit evidence

[Français](fr/CYBER_SECURITY.md)

The optional Cyber module gives the SOC local detections and JSONL exports for its SIEM. It gives customer auditors a reproducible way to verify a sealed journal. The vulnerability report is a separate view of deployed components and the business processes that depend on them.

## Enable collection for a workspace

The platform audit records supported API governance operations in `audit.jsonl` under the state directory. Workbench adds a journal inside its SQLite database. It records case proposals and revisions, withdrawals and blocks, recorded decisions and their channels, ERP execution claims, completions, holds and uncertain outcomes, and CRM checkpoints. An `execution.dispatch_authorized` event records the final authorization before a new ERP write. Use the completion event to confirm the outcome. Events carry references and revision metadata. The journal schema excludes prompts, rationale text, tokens, and business payloads. Workbench state changes and their journal entries share a SQLite transaction.

Workbench journaling is off by default. Set `FACTORY_WORKBENCH_CYBER_AUDIT=on` to initialize logging on an unconfigured database when Workbench is enabled, or pass `cyber_audit=True` when initializing an embedded Workbench. The policy persists per database. An operator can change it explicitly after Workbench has initialized the database. Both transitions are recorded. Events are collected from activation onward.

From the repository's `api/` directory, run the following with an operator account that can write to the Workbench database.

```bash
python -m app.cyber journal --path /data/workbench-protected_demo.sqlite3 --enabled on
# To pause collection for this database, use --enabled off.
```

Keep `FACTORY_WORKBENCH_MODE=off` when using the enterprise production configuration that requires it. The platform audit remains available as a source for Cyber evidence in that configuration. Workbench journal activation applies to a separately configured Workbench. See the [production security guide](SECURITY.md).

## Seal and verify a journal

Use a separate collector or verification host. Give it read-only access to the audit inputs. Keep its HMAC key and the last trusted checkpoint outside the writer's trust boundary. Supply a binary key of at least 32 random bytes through a restricted file. Use owner-only permissions on POSIX and an ACL restricted to the collector identity on Windows. Keep distinct key and checkpoint locations and retain the keys needed to verify historical checkpoints.

Use a distinct, stable stream name for each audit source and deployment. The example assumes the collector already has the source, key file, and a protected checkpoint directory. Use `--kind platform --path /data/audit.jsonl` with its own stream name for the platform audit.

```bash
cd api
python -m app.cyber seal --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key --key-id cyber-01 \
  --output /evidence/checkpoints/0001.json

python -m app.cyber verify --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json
```

Use the first seal to establish a trusted baseline. For every subsequent seal, pass `--previous` with the last independently trusted checkpoint and select a new output file. During rotation, use the new `--key-file` and `--key-id` and pass the old key with `--previous-key-file`. A checkpoint authenticates the journal prefix through its recorded sequence. Verification checks that prefix and the local chain. `unsigned_tail` counts later events outside the authenticated prefix.

Store checkpoints in separately administered storage with immutability and retention controls appropriate to your evidence policy. Retain the source journal and the appropriate verification key under restricted access. An independently held checkpoint makes rewriting or truncating its authenticated prefix detectable. Changes to the unsealed tail are outside that guarantee. The HMAC is symmetric, so anyone holding the key can also create a seal. Protect it from readers who only need an audit report. [OWASP logging guidance](https://cheatsheetseries.owasp.org/cheatsheets/Logging_Cheat_Sheet.html) · [NIST log management guide](https://csrc.nist.gov/pubs/sp/800/92/final)

## Route evidence to the SOC and customer audit

Use the same source, stream, key and checkpoint as in verification. Send the exported JSONL to the SIEM through your approved collector and transport. Verify and retain a copy of the source and checkpoint for the authorized audit verifier. The minimized export supports investigation and sharing under your access policy. Its `hash` references the source record and is distinct from a signature over the exported JSONL.

```bash
python -m app.cyber export --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json --after 0 --limit 1000

python -m app.cyber detect --kind workbench --path /data/workbench-protected_demo.sqlite3 \
  --stream customer-a-workbench --key-file /secure/keys/cyber-01.key \
  --checkpoint /evidence/checkpoints/0001.json --after 0 --limit 1000 --fail-on high
```

Use `event_id` (stream and sequence) to deduplicate exports and correlate findings with their evidence. Store the last sequence durably after downstream ingestion succeeds. For detections, use `scanned_through` as the next `--after` value and continue while `more` is true, even when a page contains zero findings. Actor and case references are pseudonyms scoped to the stream and key. They can change when you rotate the key.

`detect` applies deterministic rules. It flags ERP holds, uncertain execution and journal deactivation at high severity, CRM conflicts and platform decision denials at medium severity, and recorded service approvals at informational severity. The local scheduler or collector can route the JSON findings and monitor exit codes. An integrity failure produces a critical error on stderr and withholds export rows.

| `app.cyber` exit | Meaning |
| --- | --- |
| `0` | Command completed |
| `1` | Integrity or checkpoint verification failed |
| `2` | Configuration, input or I/O error |
| `3` | A finding reached the configured `detect --fail-on` threshold |

Customer audit reports can cite a stream, a sealed sequence range, and the outcome of verification. SOC findings can cite an `evidence_id` and its recorded event time. Evidence covers captured operations and enabled periods. Use platform authentication and monitoring records separately for request activity outside these journal events. Incident response and remediation require an operator decision.

## Track CVEs across the deployed inventory

Copy the [example inventory](../examples/cyber-inventory.example.json) to an operator-managed file, replace the example versions and aliases with observed deployments, then set `"enabled": true`. Leave it `false` to skip this analysis during setup. Use `packages` for exact versioned package URLs (PURLs) and `depends_on` for explicit relationships between an AI runtime, a connected tool, and its dependent processes.

Generate Trivy JSON for the deployed artifact with package enumeration enabled. Feed the resulting report to the local correlator. Supply `--kev` only when you have an approved local copy of the CISA catalog.

```bash
# Run Trivy against the actual image, ideally pinned by digest.
trivy image --scanners vuln --list-all-pkgs --format json --output /evidence/trivy.json IMAGE
# From api/
python -m app.cyber_cve --inventory /config/cyber-inventory.json \
  --trivy /evidence/trivy.json --kev /evidence/known-exploited-vulnerabilities.json \
  --format jsonl --fail-on high
```

The correlator uses exact versioned PURLs and follows declared dependencies to show affected processes. It retains unmapped CVEs. Treat `observed` package coverage as evidence of presence. Assets with missing or unknown versions require separate verification. A CISA Known Exploited Vulnerability raises priority independently of the scanner's severity. Results depend on inventory accuracy, Trivy coverage and the freshness of the supplied reports and catalog. The correlator works offline. [Trivy reporting documentation](https://www.trivy.dev/docs/latest/guide/configuration/reporting/) · [CISA KEV catalog](https://github.com/cisagov/kev-data)

`app.cyber_cve` returns `0` for success or disabled analysis, `1` when a finding meets the configured priority threshold, and `2` for an input error.

## Triage with an authorized analyst agent

Install the [Cyber triage skill](../api/ai_factory_workbench/agent_skills/ai-factory-cyber-triage/SKILL.md) manually in an authorized analyst environment. Give the agent the verified normalized evidence exports and the approved inventory correlation report. The skill separates facts from inferences, cites evidence IDs, and produces a read-only brief for human review.

### Reviewed analyst skills from GitHub favorites

The [favorites snapshot](https://github.com/alebgl77/awesome-ai-architect/blob/ec2c06989ac22cb7993b632fc5b833f9c65f7585/data/repos.json) supplied the shortlist. GitHub verified the signatures on the three pinned upstream commits. A separate review of security, licensing, and operational usefulness approved these read-only adaptations. Each SDK directory contains the skill text and its original MIT notice. The frontmatter records the upstream repository, pinned revision, path, and SHA-256 of the original skill file. Review those details again before adopting a newer upstream revision.

| SDK skill | Analyst task | Reviewed upstream |
| --- | --- | --- |
| [`ai-factory-skill-vetting`](../api/ai_factory_workbench/agent_skills/ai-factory-skill-vetting/SKILL.md) | Assess the provenance, license, and security of a quarantined skill snapshot. | [claude-inc](https://github.com/alebgl77/claude-inc/blob/3199e383aefa5f8364f3f7389d45e8c5b90b93ab/skills/skill-vetting/SKILL.md) · [MIT](https://github.com/alebgl77/claude-inc/blob/3199e383aefa5f8364f3f7389d45e8c5b90b93ab/LICENSE) |
| [`ai-factory-agent-security-review`](../api/ai_factory_workbench/agent_skills/ai-factory-agent-security-review/SKILL.md) | Review permissions, trust boundaries, and external actions from sanitized agent configuration. | [ECC](https://github.com/affaan-m/ECC/blob/b38be6a7f6350964d59328b52f0624e1d1d552d1/skills/agent-security-hardening/SKILL.md) · [MIT](https://github.com/affaan-m/ECC/blob/b38be6a7f6350964d59328b52f0624e1d1d552d1/LICENSE) |
| [`ai-factory-observability-review`](../api/ai_factory_workbench/agent_skills/ai-factory-observability-review/SKILL.md) | Assess signal coverage, data-source scope, and privacy from sanitized telemetry and dashboard evidence. | [Grafana LLMOps Forge](https://github.com/alebgl77/grafana-llmops-forge/blob/491fcac4f43318e6eda16d8496912ea441bf888b/SKILL.md) · [MIT](https://github.com/alebgl77/grafana-llmops-forge/blob/491fcac4f43318e6eda16d8496912ea441bf888b/LICENSE) |

Install the selected directory manually in the authorized analyst environment. The adaptations ship as text and license notices without upstream scripts or bundles. The host governs tool permissions and transport. Workbench MCP discovery exposes its proposal skill, and the analyst runs these Cyber reviews separately using local, operator-supplied evidence.
