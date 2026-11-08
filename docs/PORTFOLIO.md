# Portfolio and work orchestration

**English** · [Français](fr/PORTFOLIO.md) · [Interactive guide](https://alebgl77.github.io/ai-factory/docs/index.en.html#production)

The **Portfolio and tasks** view connects Factory objectives to projects and tasks across business units (BUs). Leadership records indicators and targets. Project leads plan tasks, managers assign them, and architects review technical deliverables. Staff see their assignments. Registered agents see their own eligible assignments, including child tasks they supervise.

GitHub Pages offers an offline walkthrough with fictional data. Use a protected private installation for model execution and recorded decisions.

## Protected deployment

Serve the dashboard and API behind the same-origin SSO proxy. Configure `FACTORY_AUTH=sso` at the edge and `FACTORY_AUTH_MODE=proxy`, `FACTORY_TRUSTED_PROXIES`, `FACTORY_ROLE_MAP`, `FACTORY_BU_POLICY` and `FACTORY_DEMO_MODE=never` at the API. Mount the BU policy read-only and restart the API after changing it. Use a single API process so the manager's start authorization and the assigned agent's use of it reach the same process. Keep both SQLite databases, other state and consistent backups in the private `api-state` volume and its protected backup path. [BU security](BU_SECURITY.md)

In the [example policy](../config/business-units.example.yaml), add SSO groups for roles and service accounts under `groups`. Map each in `FACTORY_ROLE_MAP`. Dedicated Portfolio groups can use the API `viewer` role. Administration of goals and projects requires an `admin` group that itself grants `["*"]`.

```yaml
# Sections to add to a complete policy. Group names are fictional.
portfolio_roles:
  relation-chefs-projet: [project_lead]
  relation-managers: [manager]
  relation-architectes: [architect]
portfolio_agents:
  oidc-service-support: ag-support-supervisor
  oidc-service-recherche: ag-support-research
portfolio_autonomy:
  cc-relation:
    read_only: autonomous
    prepare: human_review
    sensitive: blocked
```

Map these business groups to `[cc-relation]` and put service accounts in a dedicated read group for the same BU. The service identity is the stable subject verified by the proxy. An agent must be in the catalog, associated with a BU use case and have passed evaluation. Staff are associated with their profiles through `identities` and with their BU through `people`. The same SSO group supplies the operational role and scope.

## Execution and review

- **Autonomous.** The assigned agent can start once dependencies are complete.
- **Human review.** The manager records a one-time start authorization. This is the default for each omitted mode. Reassignment, rescheduling or an API restart invalidates a pending authorization. The manager renews it after restart if the task is still ready. The same rule applies when restoring an earlier policy file.
- **Blocked.** The BU prevents assigning or starting an agent in this mode.

A submitted technical task goes to an independent architect. A sensitive task moves to **Workbench handoff** after preparation and any required technical review. The API requires Workbench Portfolio mode to start or submit sensitive work and to approve its technical review. Read and preparation subtasks of a sensitive parent also pause when this mode is unavailable.

When an agent's evaluation is revoked, the agent loses access to its Portfolio assignments. The BU manager can mark an in-progress task blocked and assign it to an eligible agent under the BU policy.

A manager can select a distinct eligible sub-agent or use automatic selection. The server prefers the same use case, then the agent with the fewest child tasks on that parent, then the agent identifier. The assigned agent can request automatic selection when the child mode is autonomous. The parent must be in progress. Children stay in the same BU and project, with one delegation level and at most four children over the parent's lifetime, including cancellations. A child inherits technical review and can read or prepare work. The parent can be submitted after all children have completed or been canceled.

## Built-in runner

The runner processes read and preparation subtasks assigned to configured agents. It waits for dependencies and enforces BU autonomy policy before each attempt. For a human-reviewed start, the manager authorizes it in the Portfolio.

1. Register the agent's C3/C4 use case in the BU with the `chat-sensitive` alias bound to the local model. Check the agent's evaluation.
2. Provision a LiteLLM virtual key dedicated to the use case. Restrict the model and chat routes, register the key fingerprint in strict policy and configure the private GPU network. [Authenticated model routing](GATEWAY_SECURITY.md)
3. Provision a private file from the secret manager readable by API UID `10001`. Set `FACTORY_PORTFOLIO_RUNNER_SOURCE` to its absolute host path. Add [docker-compose.portfolio.yml](../docker-compose.portfolio.yml). It mounts the file read-only and sets `FACTORY_PORTFOLIO_RUNNER_FILE=/run/secrets/factory_portfolio_runner`. The runner stays disabled without this file.

```json
{
  "version": 1,
  "bindings": [
    {
      "agent_id": "ag-support-research",
      "unit": "cc-relation",
      "usecase_id": "uc-41",
      "virtual_key": "<use-case virtual key>"
    }
  ]
}
```

The identifiers are fictional. Keep the file outside the repository. The runner fixes the alias and request format. Each attempt returns bounded text using a call limited to 60 seconds and 512 output tokens, with tools and remote fallback disabled. The Portfolio shows the result within task scope and stores provider token counts when available. After interruption, the reservation can last up to two minutes. The manager checks the history and reassigns the task if recovery is needed. The `portfolio.runner_attempt_finished` and `portfolio.runner_tick_failed` events omit instructions, results and virtual keys.

## Workbench decisions

Set `FACTORY_WORKBENCH_MODE=portfolio` and `FACTORY_WORKBENCH_CYBER_AUDIT=on` on the private deployment. Keep the security journal enabled to open, decide and reconcile cases. In **Decision Companion**, use an SSO group granting both `approver` or `admin` and global scope `["*"]`. The submitter and any task architect cannot decide the case.

The Workbench stores the case and decision with their revision. The API reconciles the two databases in the background, including when the sub-agent runner is disabled. The Portfolio shows the outcome and timestamp. The rationale and decision maker's identity stay in the Workbench.

| Decision | Portfolio state |
|---|---|
| Approve | Completed. Dependent tasks can resume. |
| Reject | Canceled. A dependency that requires success continues to wait. |
| Request changes | Ready. Supply new evidence and renew start authorization if required. The next submission opens a separate case. |

A Portfolio decision approves submitted work. For external effects, use the approved connector path for the target environment. [Workbench and Companion](https://github.com/alebgl77/ai-factory/blob/main/docs/WORKBENCH.md)

If an older root task reached review or handoff while the mode was disabled, a global administrator can request recovery after enabling Portfolio mode. Read the task ID and current revision in `GET /api/v1/portfolio`, using pagination when needed. Recovery is available only when the API can rule out an existing submission author, handoff or decision. The task keeps its ID, assignment and dependencies. Its evidence and start authorization are cleared and the reset is recorded in the task history. Start and submit the work again through the usual review process.

## API integration

An external runner can authenticate through the proxy with a service account per agent. It reads authorized assignments, starts work when permitted, executes in its own environment and returns evidence. The API coordinates by state and revision.

| Request | Body or purpose |
|---|---|
| `GET /api/v1/portfolio` | Authorized goals, projects, tasks, roles and autonomy policies, including the `capabilities.workbench_handoff` mode flag and each task’s `workbench_required` flag |
| `GET /api/v1/portfolio?limit=50` | Tasks ordered newest first by creation. Pass `next_cursor` as `cursor` with the same limit. |
| `POST /api/v1/portfolio/tasks/{id}/commands` | `{revision, action: "authorize_start"}` by the manager when authorization is required |
| `POST /api/v1/portfolio/tasks/{id}/commands` | `{revision, action: "start"}` by the assigned agent |
| `POST /api/v1/portfolio/tasks/{id}/commands` | `{revision, action: "report_usage", tokens: {input, output}}` during execution |
| `POST /api/v1/portfolio/tasks/{id}/commands` | `{revision, action: "submit", evidence?, tokens?}` by the assigned person or agent |
| `POST /api/v1/portfolio/tasks/{id}/recover-workbench` | `{revision}` by a global administrator to reset an eligible legacy task. |
| `POST /api/v1/portfolio/tasks/{id}/subagents` | `{revision, mode, title, description?, agent_id?}`. Use `manual` with `agent_id` or `automatic` for server selection. The response includes `parent` and `task`. |

Human users see all strategic titles and states. A project outside their BUs returns only `title` and `state`. A goal with no project in their BUs returns only `title` and `status` (`met`, `unmet` or `unmeasured`). Participating BUs and global admins can see details. Agents see the goals and projects attached to their authorized assignments independently of task pagination.

Without `limit`, the API returns the 200 most recently updated tasks. With `limit` from 1 to 200, `next_cursor` is `null` on the final page. The cursor is bound to the verified identity, rights and limit. A rights change can invalidate continuation with HTTP 422. Each page reevaluates assignments and excludes tasks created after the first page. Changes to rights or assignments can change what is visible.

Use the GET path without a trailing slash. The API generates task and subtask IDs. Send JSON mutations with the latest revision. On HTTP 409, reload the authorized view before resubmitting a still-valid intention.

Evidence can reference a file, support or work item with `{kind, label, ref}`, where `ref` is an opaque internal ID. Declared `input` and `output` counters are cumulative integers from zero to one billion each on the current assignment. The server checks they are non-decreasing, calculates the total and labels the source `agent_reported`. The built-in runner can produce `provider_reported` counts. Submission preserves the latest count when it supplies no new count. Reassignment to another agent clears the current count. Missing values remain unknown. The work history keeps separate measurements. [Work history and token evidence](https://github.com/alebgl77/ai-factory/blob/main/docs/WORKBENCH.md#work-history-and-token-evidence)

## Operations

- For the current pilot, use the [matching signed deployment bundle](DEPLOYMENT.en.md#verify-and-install-a-release) once published. Before then, [qualify the exact source checkout](DEPLOYMENT.en.md#qualify-from-source) with locally built images.
- Run the [security preflight](SECURITY.md#14-production-hardening-guide) with the same Compose files and profiles used on the target. Pass the resolved JSON directly to the script because it can contain secrets.
- With synthetic data, verify role separation, cross-BU denial, successful sub-agent execution, model outage and restart between a Workbench decision and its appearance in the Portfolio.
- Back up `api-state` in a controlled window with the API stopped or through a snapshot consistent across both databases. Keep the restore point, versions and policies together. Rehearse the restoration on an isolated network.

### Historical image-list installation

Use this procedure only to reproduce an earlier release as a historical reference. Verify its [image signatures and attestations](../SECURITY.md#verify-a-release). Set `FACTORY_RELEASE_API_SHA256`, `FACTORY_RELEASE_DASHBOARD_SHA256` and `FACTORY_RELEASE_GRAFANA_BOOTSTRAP_SHA256` to the verified digests, each as 64 hexadecimal characters without a prefix. Add [docker-compose.release.yml](../docker-compose.release.yml) to select those three images and disable local builds.

```sh
docker compose -f docker-compose.yml -f docker-compose.release.yml \
  -f docker-compose.portfolio.yml \
  --env-file /secure/ai-factory/production.env --profile sso \
  config --format json | python scripts/check_production.py
```

Remove `-f docker-compose.portfolio.yml` when the built-in runner is disabled. After the preflight passes, reuse the same options for `up -d --no-build`. For an external private GPU, qualify TLS and add the allowed DNS names to the preflight command.
