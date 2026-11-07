# AI Factory Workbench and Decision Companion

[Français](fr/WORKBENCH.md)

Workbench follows a fictional won CRM opportunity from event intake to a draft ERP order.
It matches the customer, records a proposal for human review and reconciles the simulated
receipt after an interruption. Strategic and undocumented questions enter the same decision
inbox. The mobile companion also lets people ask agents questions and give feedback on a case.

## Open the mobile companion

Select **Decision Companion** or **QR code** in the 3D dashboard or UC-04 workshop. The QR is
generated locally and contains only the companion URL and its `lang` parameter. Sign in
separately when the workspace requires it. To use another device, open the dashboard from a
workspace address that the phone can reach over HTTPS. Local files and `localhost` point to the
device opening them.

| Mode | How to start | Data and access |
|---|---|---|
| Offline browser | Open [the companion HTML file](../dashboard/companion/index.html) directly | Fictional cases, decisions and replies live in the current tab. Reloading starts over. |
| API off | Keep `FACTORY_WORKBENCH_MODE=off`, the default | The workbench API routes are absent. |
| Connected simulation | Set `FACTORY_WORKBENCH_MODE=demo` | Shared fictional cases and ERP drafts in SQLite. Use a controlled environment if authentication is off. |
| Protected simulation | Set `FACTORY_WORKBENCH_MODE=protected_demo` with proxy SSO | The same simulated ERP in an authenticated workspace with role checks. |

The companion supports English and French. The static Pages workflow packages it at the
relative path `companion/` and explicitly selects the isolated browser simulation. The QR
carries no session, credential or case ID. API or authentication failures in an online
workspace are shown in the companion without falling back to a local demo.

Follow the [Compose quick start](../README.md#local-quick-start-no-key-at-all), set the desired
mode in `.env` and run `docker compose --profile sim up -d --build`. The companion is at
`/companion/`. For the protected simulation, follow the [SSO and hardening guide](SECURITY.md),
configure `FACTORY_AUTH=sso`, `FACTORY_AUTH_MODE=proxy` and `FACTORY_ROLE_MAP`, and start the
`sso` profile. Serve the companion and API through the same origin.

```mermaid
flowchart LR
    CRM["Fictional CRM event"] --> API["Workbench API and role checks"]
    Dashboard["3D dashboard or workshop"] -->|QR opens URL| Phone["Decision Companion"]
    Phone -->|Same-origin review and feedback| API
    API --> Store["SQLite cases, decisions and receipts"]
    Store --> ERP["Simulated ERP draft"]
    API -. Optional advice .-> Model["Internal LiteLLM · chat-sensitive"]
    CLI["Read-only CLI probe"] -. HTTPS .-> Odoo["Odoo 19 partners"]
```

## Follow a review from the 3D map

On the protected dashboard, select **Follow the Workbench**, then **Enable follow**. The
map uses the existing Workbench proxy sign-in on the same origin. If business unit scopes
are configured, the caller needs global Workbench read access. Symbolic agent and reviewer
avatars show a request in progress and the outcome recorded for that case revision. The map
receives only the case ID, revision and status. It uses no proposal content or staff identity.
Open the **Decision Companion** to read the case and submit decisions according to your role.

The public dashboard offers **3D exchanges** with fictional scenarios and replies confined to
the browser. A protected dashboard uses the read-only Workbench follow mode. It needs no
additional setting beyond the existing protected Workbench and proxy configuration.

## Work history and token evidence

Open **Operations → Work history** to filter recorded work by agent or user. The static
dashboard uses fictional records. The protected history uses the existing same-origin identity
proxy. A user sees their own events for their current BU grants. An administrator whose role
comes from a trusted group can explicitly select all users. With a BU policy, that same group
must also grant global BU access. Global reads produce a security log entry with pseudonymous
references.

Use `GET /api/v1/activity?days=7&view=own` for the newest 300 events in the UTC window. Supported
periods are 7, 30 and 90 days. Check `truncated` before interpreting aggregate values.
`view=all` requires the global administrator grant. An agent event identifies the agent and the
authenticated user who initiated or reported the work, so both filters refer to the same record.

An authenticated editor or administrator can send `POST /api/v1/activity` as JSON through the
identity proxy to report an instrumented run:

```json
{
  "id": "763fcb2113ee49e481602a88a7ec326bc",
  "actor_type": "agent",
  "actor_id": "document-assistant",
  "unit": "cc-ops",
  "action": "Summarize reference material",
  "started_at": "2026-11-06T10:00:00Z",
  "finished_at": "2026-11-06T10:00:08Z",
  "status": "completed",
  "tokens": {"input": 1200, "output": 400},
  "supports": [{"kind": "file", "label": "Reference.pdf", "ref": "doc-42"}]
}
```

Reuse the same lower-case 32-hex ID and BU on retries. An exact replay for the same user and BU
succeeds. A conflicting replay in that scope returns 409. Omit `actor_id` for user actions.
The server supplies the user identity. Omit
`unit` without a BU policy or when the writer has exactly one permitted unit. Support references
are opaque IDs and display labels. Action and support labels come from the integration. The
integration must supply display metadata free of prompts, document content, and secrets. The API
rejects unknown fields, paths, and URLs.

The protected Workbench records new decisions. It also records advice attempts when the local
adviser is enabled. With a BU policy, external integrations declare runs through the activity
API. Advice duration is elapsed execution time and token counts come from the provider when supplied. For a decision,
duration and tokens are unknown. Reported runs keep their own provenance. Missing values stay
unknown and an explicit zero stays zero. The journal measures execution intervals rather than
staff presence or working hours. It starts with new events and uses the local state volume.

## Decisions and agent conversations

- Readers can consult cases and history, ask questions and give feedback. Editors,
  maintainers and admins can submit events. Approvers and admins can decide. Authenticated
  proposers cannot approve their own cases.
- Approval requires a rationale and the exact `revision` and `proposal_hash`. A newer
  proposal invalidates the earlier revision. Initial approval of a proposed action expires
  after seven days. To reconcile a previously recorded approval after a lost response, the
  same approver resubmits the same revision, hash, decision and rationale, even after the
  deadline. A stable action reference lets the sandbox return the existing draft.
- Ambiguous matches and strategic, undocumented or action requests in chat go to a human.
  A recorded decision on a case without a proposed action creates no ERP draft. Pending cases
  take precedence and the priority score incorporates age before the 200-case limit.
- The companion renders case data and replies as text. The API stores cases, decisions,
  receipts and feedback in `workbench-<mode>.sqlite3` under `FACTORY_STATE_DIR`. Escalated
  question text is stored as a shared case. Send only details appropriate for that workspace.
  The workbench does not persist chat transcripts. History exposes feedback totals and your
  own comment. Decision rationales are visible to approvers, admins and their author.

`FACTORY_WORKBENCH_CHAT_MODE=simulation` is the default and uses deterministic replies.
Optional `FACTORY_WORKBENCH_CHAT_MODE=local` requires `protected_demo`, proxy authentication
and the internal LiteLLM gateway. The server selects the fixed `chat-sensitive` alias, applies
C3 metadata, masks inputs and outputs and disables prompt logging for the request. Configure
the local target as shown in the [deployment guide](../README.md#real-deployment-cloud-first-then-vps-then-local-hardware).
The model advises without tools. An unavailable local gateway returns an error. The local
target can itself be simulated under the `sim` profile.

## API and integration contract

Routes below are relative to `/api/v1/workbench`. Browser writes use same-origin JSON.

| Method and path | Behavior |
|---|---|
| `GET /cases` | Read the mode, caller capabilities and prioritized cases with their proposal versions. |
| `GET /encounters` | Read the case IDs, revisions and statuses of up to 100 prioritized cases with protected Workbench read access. Add `?case_id=<id>` to follow one case even when it leaves the prioritized queue. |
| `POST /events` | Submit the supported CloudEvents 1.0 subset for `crm.opportunity.won` from `crm-sim`. |
| `POST /cases/{id}/decisions` | Submit `approve`, `reject` or `request_changes` with revision, hash and rationale. |
| `GET /cases/{id}/history` | Read the receipt, role-scoped decisions and feedback summary. |
| `POST /cases/{id}/feedback` | Record or replace the caller's `helpful` or `needs_work` rating and optional comment. |
| `POST /chat` | Send a message to `operations`, `finance` or `governance` with an optional `case_id`. |

In the connected local simulation, submit a fictional event and open the companion to review it.

```bash
base=http://localhost/api/v1/workbench
curl -sS "$base/cases"
curl -sS "$base/events" -H 'Content-Type: application/cloudevents+json' -d '{
  "specversion":"1.0","source":"crm-sim","id":"guide-001",
  "type":"crm.opportunity.won","subject":"opp-guide-001",
  "data":{"title":"Guide opportunity","customer_name":"Atelier Acme","customer_ref":"CRM-ACME",
          "amount_minor":124000,"currency":"EUR","record_version":1}
}'
```

The delivery key combines `source` and `id`. An identical retry reuses the case. A changed
payload for the same delivery, stale record version or stale approval returns `409`.
Amounts use integer minor units. Customer matching filters by workspace and allowed record
IDs before comparing exact references or normalized names. It abstains on ambiguous results.
Model routing filters by sensitivity, residency, availability, cost, latency and minimum
quality before scoring. C3 and C4 only admit a local target. The benchmark exercises routing
with synthetic observations. Optional companion advice uses the fixed local alias.

## Read-only Odoo probe and synthetic replay

Install the [API dependencies](../README.md#tests-and-checks). Set `FACTORY_ODOO_URL` to an
HTTPS origin and provide `FACTORY_ODOO_DATABASE` and `FACTORY_ODOO_API_KEY` in the operator
process environment. Use an account restricted to reading from the Odoo 19 JSON-2 API.

```bash
python scripts/workbench_benchmark.py --odoo-read --limit 25
python scripts/workbench_benchmark.py --odoo-read --limit 25 \
  --after-write-date '2026-11-05 12:00:00' --after-id 42 --overlap-seconds 5
```

Replace the sample UTC cursor with the previous output. The command reads one partner page
and prints counts and the next `(write_date, id)` cursor. The limit is 200. The probe performs
no imports or Odoo writes. The synchronization helper commits the checkpoint after applying a
full page. Overlap and interrupted pages can replay records, so the consumer must be
idempotent. Deletions need separate reconciliation.

```bash
python scripts/workbench_benchmark.py
(cd api && python -m pytest -q tests/test_workbench.py tests/test_workbench_algorithms.py)
node tests/e2e/companion.mjs
```

The benchmark replays 36 synthetic cases in SQLite, including duplicate deliveries, lost
receipts, stale approvals and self-approval. It reports 36/36 correct governed outcomes
against 12/36 for a deliberately simple rule, plus 3/3 synthetic routing checks. These outcomes do not measure production
model quality, latency or cost. The browser test uses the locked Playwright setup from
[CI](../.github/workflows/ci.yml).

This implementation covers one shared fictional workspace. The SQLite receipt verifies the
simulated ERP behavior. An external ERP requires its own write and reconciliation contract.
The opt-in Odoo read connector is exercised with a mocked transport in tests.
