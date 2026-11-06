# Integrate the adaptive Workbench

[Français](fr/ADAPTIVE_WORKBENCH.md)

The Workbench prepares a dossier from an organization's working method and records a separate decision on an exact revision. The Python SDK embeds the engine. The protected HTTP API connects the Decision Companion and approval relays. The local MCP server gives agents tools to prepare and read dossiers.

## Run the SDK example

Run these commands from the repository root with Python 3.11 or newer in a virtual environment.

```sh
python -m pip install ./api
python examples/adaptive_workbench.py
```

The example uses disposable local SQLite stores and synthetic data. Its JSON report shows a direct human decision, Slack and Teams human relays, an internal service relaying a human decision, and a machine-only governance approval that was blocked. It compares two profile policies on sample cases. A second scenario commits a draft in a separate simulated ERP, drops the response, and verifies one draft after the engine reopens. The example needs no provider account, network access during execution, or external credentials.

## Adapt the working method

| Profile | Evidence it asks for |
| --- | --- |
| `governance` | Artifact, evaluation, risk summary and rollback plan |
| `operations` | Business record, observed state and expected effect |
| `platform` | Change, tests, impact summary and rollback plan |

The host selects the active profile. Embedded applications can load a data-only `WorkProfile` with `WorkProfile.from_dict` and call `profile.describe()` to render its checklist and steps. `profile.prepare(engine, actor, ...)` validates the evidence and binds the profile ID, version and fingerprint to the review proposal. A profile change can produce a new revision. Approval uses the case ID, revision and proposal hash from the reviewer's dossier.

SDK review dossiers and profile-bound proposals require an authenticated reviewer, including in demo mode. New authenticated CRM submissions over HTTP or MCP capture the active profile. A trusted SDK host can bind it with `engine.submit(event, editor, profile=profile.snapshot())`. Drafts created without a profile require human review. Changing the active profile cannot turn an old machine proof into a valid approval.

`compare_policies(baseline, candidate, cases)` compares review readiness and eligible channels on supplied cases. Omit `cases` for the synthetic sample. A comparison returns differences for review. Policy activation is a host configuration decision.

```python
from pathlib import Path
from ai_factory_workbench import Actor, Workbench
from ai_factory_workbench.profiles import get_profile

engine = Workbench(Path("governance.sqlite3"), workspace="platform-team", mode="protected_demo")
editor = Actor("release-editor", frozenset({"editor"}))
case = get_profile("governance").prepare(
    engine, editor, subject="model/v2", title="Review model v2",
    reason="Review the release evidence before promotion.",
    evidence={"artifact_ref": "models/v2", "evaluation_ref": "eval/v2",
              "risk_summary": "Check sensitive data handling.", "rollback_plan": "Restore v1."},
)
```

`Actor` represents identity and roles verified by the embedding host. In a deployment, construct it from trusted identity data. The host owns the database path and uses one SQLite file per workspace. The engine binds the file to its workspace and ERP target on open.

## Connect an MCP client

```sh
python -m pip install './api[mcp]'
ai-factory-mcp --database ./workbench.sqlite3 --workspace platform-team --profile governance
```

Configure your MCP client to start that command over stdio. Use an executable and database path accessible to the client's local process. Replace `--profile governance` with `--profile-file ./work-profile.json` to load an operator-managed JSON profile. Clients that support MCP skill discovery can use `skills/list` and `skills/get`. Clients with resource support can read `skill://ai-factory-workbench/SKILL.md`. The bundled skill describes the same workflow for compatible Agent Skills clients. [Bundled `SKILL.md`](../api/ai_factory_workbench/agent_skills/ai-factory-workbench/SKILL.md)

1. Read `workbench_get_profile` to determine the evidence checklist.
2. Call `workbench_prepare_review` with the completed evidence or use `workbench_propose_opportunity` for the reference CRM event. Both create proposals for separate review.
3. Use `workbench_list_cases` and `workbench_get_case` to check the exact revision, evidence and recorded outcome.

The MCP process runs as an editor in its configured workspace. Give its process access only to the intended database and share that file with the protected host only when both are authorized for the same workspace and target. The command-line server uses the built-in ERP sandbox. A trusted host that injects its own connector can use `ai_factory_workbench.mcp_server.build_server(engine, profile)` to serve the same workspace. Approval and ERP recovery run in the trusted host. [MCP Python SDK](https://py.sdk.modelcontextprotocol.io/v2/) · [Agent Skills specification](https://agentskills.io/specification)

## Protect the HTTP and approval surfaces

The API is off by default. Enable `FACTORY_WORKBENCH_MODE=protected_demo` with SSO for a protected deployment. Use the existing same-origin JSON endpoints from the Companion. Service-to-service clients authenticate through the trusted proxy. [Workbench modes and SSO setup](WORKBENCH.md#open-the-mobile-companion)

| Endpoint under `/api/v1/workbench` | Purpose |
| --- | --- |
| `GET /profiles` | Active method and available profiles |
| `POST /reviews` | Prepare a review with `subject`, `title`, `reason`, `evidence` and optional `kind` |
| `GET /cases/{case_id}` | Read the exact dossier |
| `POST /cases/{case_id}/decisions` | Record an authenticated Companion decision |
| `GET /cases/{case_id}/history` | Read decision and receipt history according to the caller's role |
| `POST /approval-relay` | Submit a verified relay's signed envelope when the host enables it |

After constructing an app with protected Workbench routes, the host can use `configure_workbench_surfaces(app, profile="governance", engine=engine, authorities=authorities)` from `app.routers.workbench`. `profile` accepts a built-in name or a validated `WorkProfile`. The engine is optional and must match the configured HTTP access mode. The relay is disabled until the host configures authorities. SDK-only hosts can instantiate `ApprovalRelay` directly.

| Route | Who verifies identity and approval |
| --- | --- |
| **Decision Companion** | The trusted proxy authenticates the reviewer. The reviewer opens the dossier and records a decision. |
| **Slack or Teams** | An installed integration verifies the provider callback and approved tenant, then maps the provider's user ID to a canonical human reviewer. For Slack, validate the signature over the raw request body and its timestamp. For Teams, validate the bot activity's authentication and tenant with the Microsoft adapter. |
| **Internal service** | A registered service signs evidence of an independently authenticated human decision for governance. The Operations profile can separately permit an explicitly configured service identity to decide a supported ERP draft. |

Registered machine identities use the signed relay for decisions. The Companion route denies them and their registered aliases even when the proxy grants an approval role. Include canonical account and email aliases when registering a machine. Keep the SSO groups that grant approver or admin roles human-only so unregistered machine identities cannot access the human decision route.

For the relay, hold a separate high-entropy key for each registered issuer in host secret storage. Bind the decision to the server-side dossier the reviewer saw. Construct the envelope with `workspace`, `issuer`, `channel`, `identity`, `case_id`, `revision`, `proposal_hash`, `decision`, `rationale`, `issued_at`, `expires_at` and `request_id`. Times are Unix seconds. Use a fresh request ID per decision and a validity window of at most five minutes. Send `{ "envelope": envelope, "signature": sign_approval(key, envelope) }` through the protected route or pass those two arguments to `ApprovalRelay.decide` in a trusted process. Use the registered canonical actor mapping and retain the same request ID for an exact retry.

The relay verifies a shared-secret HMAC and the engine records a proof fingerprint and its origin. HMAC verification requires the shared secret. The SDK example exercises the relay contract. Install and configure your own Slack or Teams integration to process live events. [Slack request verification](https://api.slack.com/docs/verifying-requests-from-slack) · [Microsoft Bot Connector authentication](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication)

## Connect a draft ERP adapter

Inject a trusted `DraftConnector` and a workspace-scoped customer catalogue. The connector declares a stable ERP target and supports draft writes, a unique idempotency key enforced by the ERP, and independent readback. The engine compares the persisted draft to the approved fields. A conflicting result requires investigation.

To run effects in a host worker, construct the engine with `defer_execution=True`. Approval persists an `applying` case. The host can discover pending cases with `pending_execution_ids()` and resume one with `reconcile_pending(case_id)`. If a write times out, resume the same case after restart and let the adapter look up the same operation key. Reserve worker access for trusted infrastructure. The bundled Odoo probe reads partner records. The example uses a local ERP simulator. [Read-only Odoo probe](WORKBENCH.md#read-only-odoo-probe-and-synthetic-replay)

The worker claims the exact revision in SQLite before contacting the ERP. If the Workbench accepts a newer CRM event before the claim, it replaces the pending proposal and requires a new review. Once execution is claimed, or if an older pending case has an unknown execution state, reconcile the existing operation first. The claim is local to the Workbench database. Atomic uniqueness at the ERP remains the connector's responsibility.
