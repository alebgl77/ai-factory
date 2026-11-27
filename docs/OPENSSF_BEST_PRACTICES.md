# OpenSSF Best Practices application evidence

**English** · [Français](fr/OPENSSF_BEST_PRACTICES.md)

Prepared on 2026-11-27 against `main` commit `49ae650` and the 1.2.0 changelog; this change prepares 1.2.1. This is an evidence inventory, not a badge or a declaration that every criterion is met. No public application was found in the [project lookup](https://www.bestpractices.dev/en/projects.json?url=https%3A%2F%2Fgithub.com%2Falebgl77%2Fai-factory) on that date. Instrumented fuzzing remains pending until its workflow passes on the merged commit.

Use the [official passing criteria](https://www.bestpractices.dev/en/criteria/0) and their [applicability details](https://www.bestpractices.dev/en/criteria/0?details=true), checked on 2026-11-27. **E** means repository evidence exists; **O** needs owner verification or attestation; **G** identifies an unresolved gap. These labels are not application answers. N/A requires the specific criterion to allow it and a factual justification.

## Mandatory criteria: 43 IDs

| Criterion ID | Evidence state | Evidence or remaining action |
|---|---|---|
| `description_good` | E | [README](../README.md) describes the digital twin, demo and private-pilot scope. |
| `interact` | E | [README](../README.md), [contribution guide](../CONTRIBUTING.md), [issues](https://github.com/alebgl77/ai-factory/issues). |
| `contribution` | E | [CONTRIBUTING](../CONTRIBUTING.md) describes focused pull requests and design discussions. |
| `floss_license` | E | [MIT license](../LICENSE); third-party boundaries in [LICENSES](LICENSES.md). |
| `license_location` | E | Root [LICENSE](../LICENSE). |
| `documentation_basics` | E | [README](../README.md), [documentation index](README.md), [deployment guide](DEPLOYMENT.en.md). |
| `documentation_interface` | E | [API contract](CONTRACT.md), [API README](../api/README.md), generated `/api/v1/docs`. |
| `sites_https` | E | [Repository](https://github.com/alebgl77/ai-factory), [demo](https://alebgl77.github.io/ai-factory/), [releases](https://github.com/alebgl77/ai-factory/releases) use HTTPS. |
| `discussion` | E | Public searchable [issues](https://github.com/alebgl77/ai-factory/issues) and [discussions](https://github.com/alebgl77/ai-factory/discussions); browser access. |
| `maintained` | E | `git log` includes `49ae650` dated 2026-11-27; reassess activity when applying. |
| `repo_public` | E | [Public source](https://github.com/alebgl77/ai-factory). |
| `repo_track` | E | Git commit history records authors, timestamps and diffs. |
| `repo_interim` | E | [Commit history](https://github.com/alebgl77/ai-factory/commits/main/) and pull requests retain changes between releases. |
| `version_unique` | E | [CHANGELOG](../CHANGELOG.md) and [release workflow](../.github/workflows/release.yml) use versioned tags. |
| `release_notes` | E | [CHANGELOG](../CHANGELOG.md) describes changes and upgrade requirements; release workflow uses it. |
| `release_notes_vulns` | O | Changelog lists dependency CVEs. Check project-code advisories separately; justify N/A only if no qualifying project vulnerability existed. |
| `report_process` | E | [Contribution guide](../CONTRIBUTING.md) and [bug form](../.github/ISSUE_TEMPLATE/bug_report.yml). |
| `report_responses` | O | Review the preceding 2–12 months of bug reports and owner acknowledgements; retain counts and links. |
| `report_archive` | E | [Issue archive](https://github.com/alebgl77/ai-factory/issues?q=is%3Aissue). |
| `vulnerability_report_process` | E | [Security policy](../SECURITY.md#reporting-a-vulnerability). |
| `vulnerability_report_private` | E | Policy links the [private reporting form](https://github.com/alebgl77/ai-factory/security/advisories/new); the owner enabled the repository setting and verified `enabled: true` through the API on 2026-11-27. |
| `vulnerability_report_response` | O | Check every initial response over six months against 14 days; policy target is 72 hours. N/A only if no reports arrived. |
| `build` | E | [API Dockerfile](../api/Dockerfile), [dashboard Dockerfile](../dashboard/Dockerfile), [Compose](../docker-compose.yml), [CI](../.github/workflows/ci.yml). |
| `test` | E | Public pytest and browser suites; commands in [CONTRIBUTING](../CONTRIBUTING.md) and CI. |
| `test_policy` | E | Contribution guide explicitly requires automated tests for major new functionality. |
| `tests_are_added` | E | `49ae650` adds [release-image tests](../tests/deployment/test_release_image.py), [gateway-credential tests](../api/tests/test_gateway_credentials.py) and Kubernetes tests alongside 1.2.0 changes. |
| `warnings` | O | CI runs actionlint/ShellCheck and Helm lint. Confirm sufficient warning/lint coverage of application languages before answering Met. |
| `warnings_fixed` | O | Inspect current lint results and any documented false positives; configuration alone proves no clean result. |
| `know_secure_design` | O | A primary developer must personally assess the design knowledge listed in the official details; code and scanners cannot attest to it. |
| `know_common_errors` | O | A primary developer must attest to relevant vulnerability classes and mitigations from their own knowledge. |
| `crypto_published` | O | [Security architecture](SECURITY.md), SHA-256/HMAC in [audit](../api/app/audit.py) and [approvals](../api/ai_factory_workbench/approvals.py); review all cryptographic dependencies and protocols. |
| `crypto_floss` | O | [License inventory](../licenses/third_party.yaml) and [license check](../scripts/check_licenses.py) support review; verify every cryptographic function has a FLOSS implementation. |
| `crypto_keylength` | O | Check TLS, signing keys, HMAC secrets and supported configuration against the official minimums; algorithm names alone are insufficient. |
| `crypto_working` | O | Review defaults and every dependency for broken algorithms or unsuitable modes; justify any protocol exception. |
| `crypto_password_storage` | O | [Edge basic auth](../compose/caddy/Caddyfile) uses bcrypt; assess all inbound user-password stores, including configured services. Outbound API credentials are a different case. |
| `crypto_random` | O | [Environment setup](../.devcontainer/setup-env.sh) uses `openssl rand`; verify all generated keys/nonces, including library and service paths. |
| `delivery_mitm` | E | HTTPS source/releases and [signature verification](../SECURITY.md#verify-a-release). |
| `delivery_unsigned` | O | Signed release checksums and [verification script](../.github/scripts/verify_image.sh) provide evidence; audit every download path for insecure checksum retrieval. |
| `vulnerabilities_fixed_60_days` | O | Security policy now targets 60 days from public disclosure for medium+. Verify actual patch history and all open findings; HIGH/CRITICAL fixable-only gates do not establish this. |
| `no_leaked_credentials` | O | [Secret scanner](../scripts/scan_secrets.sh) runs on files and Git history in CI; inspect results and confirm any exposed real credentials were revoked. |
| `static_analysis` | O | [CodeQL](../.github/workflows/codeql.yml), zizmor and [release preflight](../.github/workflows/release.yml) exist; attach successful exact-commit runs before claiming release compliance. |
| `static_analysis_fixed` | O | Inspect confirmed medium+ exploitable findings and dated patches; scanner suppression or an upstream exception is not proof of a fix. |
| `dynamic_analysis_fixed` | O | Review test/fuzz findings and patch dates; document applicability using the official details. New fuzzing CI evidence is pending. |

## SHOULD and SUGGESTED answers

SHOULD answers need a justification if unmet; SUGGESTED items are optional for passing. Do not silently treat either as mandatory or N/A.

| Strength and exact IDs | Evidence or remaining action |
|---|---|
| SHOULD: `contribution_requirements`, `english`, `report_tracker`, `test_invocation` | Contribution rules, English docs, issues and `python -m pytest` are documented. |
| SHOULD: `enhancement_responses` | Owner checks responses to more than half of enhancement requests in the preceding 2–12 months. |
| SHOULD: `build_floss_tools` | Review the default build and any optional GPU tooling; distinguish tools from external services. |
| SHOULD: `crypto_call`, `crypto_weaknesses`, `crypto_pfs` | Standard crypto libraries are used; owner reviews defaults and key agreement across services. No blanket N/A. |
| SHOULD: `vulnerabilities_critical_fixed` | Owner checks critical-report and patch timestamps. |
| SUGGESTED: `floss_license_osi`, `repo_distributed`, `version_semver`, `version_tags` | MIT, Git and versioned tags provide evidence. |
| SUGGESTED: `build_common_tools`, `test_continuous_integration`, `tests_documented_added` | Docker/setuptools, CI and the contribution test policy provide evidence. |
| SUGGESTED: `test_most`, `warnings_strict` | No whole-project branch-coverage figure or maximum-warning claim is established here. |
| SUGGESTED: `static_analysis_common_vulnerabilities`, `static_analysis_often` | CodeQL uses `security-extended` on pushes/PRs, plus a weekly schedule. |
| SUGGESTED: `dynamic_analysis` | [Atheris targets and commands](../tests/fuzz/README.md), [fuzz workflow](../.github/workflows/fuzzing.yml): successful merged-commit CI evidence is pending. Ordinary tests alone need at least 80% branch coverage under the official details. |
| SUGGESTED: `dynamic_analysis_unsafe` | N/A candidate for project-owned Python/JavaScript/shell code; confirm no project-produced memory-unsafe code is in scope. |
| SUGGESTED: `dynamic_analysis_enable_assertions` | Pytest and browser tests contain assertions; retain successful runs and review the fuzz invariants. |

## Refresh the evidence

Run from the repository root; the API command changes its working directory. See CI for browser, image and integration prerequisites. These commands are reproducible instructions, not a claim that every check ran for this dossier.

```sh
git log -5 --format='%h %ad %s' --date=short
python -m pip install --require-hashes -r api/requirements-dev.txt -r api/requirements-mcp.txt
(cd api && python -m pytest -q)
python -m pytest -q tests/deployment/test_release_image.py tests/deployment/test_release_registry.py
python scripts/check_licenses.py --offline
scripts/scan_secrets.sh . --git
```

For instrumented Linux/Python 3.12 fuzzing, use the exact install and invocation commands in [the fuzzing guide](../tests/fuzz/README.md): both targets run at most 25,000 inputs or 90 seconds, with assertions, a five-second input timeout and a 1 GiB process limit. Retain the `Fuzzing (security boundaries)` run URL for the reviewed commit.

## Owner steps before a public application

1. Publish the reviewed change, then record its exact commit and successful CI, security, CodeQL and fuzzing run URLs. Link the resulting signed release separately; a changelog entry is not publication evidence.
2. Resolve or honestly mark every O/G entry. Review bug/enhancement counts, private response dates, all medium+ vulnerability history, crypto scope and personal security knowledge. Keep private report contents private.
3. Open [New project](https://www.bestpractices.dev/en/projects/new), sign in and explicitly authorize account creation if needed. Register `https://github.com/alebgl77/ai-factory`; enter only verified answers and public evidence URLs. The final public application URL will be assigned there; none is assigned in this dossier.
4. Review the public application, complete every required answer and obtain an actual passing badge before adding its ID/link to this repository or claiming the Scorecard alert is resolved.

The independent Scorecard `Maintained` repository-age gate remains time-bound (2027-02-02); `Branch-Protection` and `Code-Review` need real independent human participation for the remaining review requirements. Keeping one maintainer does not satisfy those review requirements. This dossier changes none of those facts.
