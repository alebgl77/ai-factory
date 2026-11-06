# AI Usage Observatory

The **AI Usage Observatory** replaces the decorative restrooms on the 3D map. Open it in the map or from the **Factory** tab. GitHub Pages shows a clearly marked fictional example. An authenticated enterprise deployment can show four separate views of AI use.

| View | Source and interpretation |
| --- | --- |
| Discovery signals | The rolling, organization-wide Open Shadow AI evidence window. `events` counts deduplicated collected evidence. `unique_users` counts distinct source identity values on eligible events. Coverage depends on enabled collectors and identity quality. |
| Team map | Company-produced monthly counts of distinct people and evidence events, grouped by team, tool and type of evidence. |
| Adoption | Company-produced monthly counts of distinct active users and eligible people for each measured tool. The UI can compare active and eligible populations when both the active and inactive groups reach the privacy threshold. |
| Governance | Tool status, approved alternatives and decisions entered by designated AI Factory users. |

Inventory evidence establishes presence. Observation can record contact with a service, for example a DNS lookup. Usage is a separately instrumented interaction. Adoption comes from the company's active-user measurement. Open Shadow AI does not provide a verified team dimension in this release. Use the company's team roster and approved telemetry to produce monthly aggregates inside its trusted environment. [Pinned evidence contract](https://github.com/alebgl77/open-shadow-ai/blob/6aa329a512adc1de5be630641861fa5777c3f649/src/shadai/api/dashboard.py#L164-L230) · [Pinned event schema](https://github.com/alebgl77/open-shadow-ai/blob/6aa329a512adc1de5be630641861fa5777c3f649/src/shadai/models/event.py#L52-L128)

## Enable the protected Observatory

Configure the Factory SSO edge and use a dedicated Factory deployment and data volume per organization. The Observatory is off by default. [Production hardening guide](SECURITY.md#14-production-hardening-guide)

```dotenv
FACTORY_AUTH=sso
FACTORY_AUTH_MODE=proxy
FACTORY_SHADOW_AI_ENABLED=on
FACTORY_SHADOW_AI_PRIVACY_THRESHOLD=5
FACTORY_SHADOW_AI_GROUPS=ai-observatory-viewers,ai-observatory-editors,ai-observatory-importers,ai-observatory-approvers
FACTORY_ROLE_MAP=ai-observatory-viewers:viewer,ai-observatory-editors:editor,ai-observatory-importers:admin,ai-observatory-approvers:approver
```

Keep the rest of the existing role mappings when adding these entries. Set the deployment's OIDC issuer, audience and allowed groups at the edge. Expose the dashboard and API behind this edge, bind the Factory API to the private network, and let only the configured `FACTORY_TRUSTED_PROXIES` assert identity and group headers. For each mutation, the edge must supply a stable `X-Forwarded-User` unique to the account. An Observatory capability comes from a group explicitly listed in both `FACTORY_SHADOW_AI_GROUPS` and `FACTORY_ROLE_MAP`. Put an import automation account only in the dedicated importer group. Give the approver group to designated people with separate stable identities. Test real sign-in, unauthorized access and distinct approval identities before importing data.

With the bundled Compose SSO profile configured, apply the environment with `docker compose --profile sso up -d`. A custom ingress must enforce its own SSO policy and strip caller-supplied identity headers before forwarding authenticated ones. The Observatory is for a single organization's authorized group, with team-level views visible across that group.

## Connect Open Shadow AI

Run Open Shadow AI separately, on a private organizational endpoint. The validated source revision is [6aa329a](https://github.com/alebgl77/open-shadow-ai/tree/6aa329a512adc1de5be630641861fa5777c3f649), under Apache-2.0. Its CI passed at the pinned commit. The Factory stack does not start the upstream collectors or write to Open Shadow AI. The operator bridge reads the authenticated dashboard evidence route and transmits only its window and two counts. [Upstream CI](https://github.com/alebgl77/open-shadow-ai/actions/runs/41363193106)

1. Obtain a short-lived Open Shadow AI viewer access token through your upstream authentication flow. Supply it as `SHADOW_AI_SOURCE_TOKEN` through your approved secret manager. Collector keys authenticate ingestion and do not grant dashboard access.
2. Supply a separate short-lived credential accepted by the Factory SSO edge for an authorized admin. Use `SHADOW_AI_FACTORY_TOKEN` for a bearer-capable ingress, or `SHADOW_AI_FACTORY_SESSION` for one managed SSO session cookie in `name=value` form if the ingress uses cookie authentication. Set one credential. The standard Factory OAuth edge authenticates browser sessions. Confirm the supported operator flow with the owner of that edge.
3. Run the bridge from the checkout with Python 3.11 or later. Use HTTPS origins and the operator's trusted CA store. The bridge rejects redirects and sends each credential to its configured origin.

```sh
python scripts/shadow_ai_pull.py --source https://shadow.example.org --factory https://factory.example.org --days 30
```

The window accepts 1–90 days. A successful push exits silently. For a separate authenticated API client, omit `--factory` to emit just the projection on stdout and send it directly to `POST /api/v1/shadow-ai/evidence` as JSON. Treat that projection as private operator data and keep it out of terminal recordings and workflow logs. The Observatory hides the global rollup after seven days without a fresh end timestamp. If the distinct identity count falls below the threshold, the current rollup disappears. Authorized readers can compare rolling reports over time. The threshold suppresses small counts. Anonymity and differential privacy require separate controls.

## Upload monthly team and adoption counts

Create the rollup where the organization already controls staff identities, team membership and usage instrumentation. Assign each person to one team using a stable monthly roster, then count each eligible or active person once per cohort and month. Establish one consistent eligibility rule, such as access to the internal assistant, and an active-use rule based on a meaningful interaction with the specific tool. Use approved team and tool codes. Apply small-cohort suppression at the source before transfer.

The [fictional sample](../examples/shadow-ai/month.json) shows the exact shape. Replace the month with a closed UTC month from the previous 12 months and use actual pre-aggregated rows in your private file.

```sh
python scripts/shadow_ai_pull.py --period-file ./examples/shadow-ai/month.json --factory https://factory.example.org
```

The same authenticated endpoint is `POST /api/v1/shadow-ai/periods`. Each file contains `period` (`YYYY-MM`), `team_usage` and `adoption`. Team and tool keys are lowercase ASCII letters or digits with optional dashes, up to 64 characters. Team usage includes `kind` (`inventory`, `observation` or `usage`), `people` and `events`. Adoption includes `active` and `eligible`. Omit rows without a verified distinct-person measure. Use organizational group codes and avoid names, addresses, identifiers for individual accounts, URLs, prompts and raw evidence. The bridge accepts up to 200 rows per list and 64 KiB per file.

The Factory publishes and retains one exact measure per team, tool and month. If both `active` and `eligible - active` reach the threshold, it publishes adoption. Otherwise it keeps the strongest signal that meets the threshold for distinct people, in this order `usage`, `observation`, `inventory`. The threshold defaults to 5 and supports values from 5 to 100. It discards the remaining measures, including all suppressed counts. An absent row can mean the source lacked data or the measure was suppressed. The month is immutable once imported. Retrying the same visible projection is safe, while a conflicting projection returns HTTP 409. Have the data owner verify the month before the first submission.

## Review tools and adoption

Open **Governance** in the Observatory. An editor or admin proposes a status of `reviewing`, `pilot`, `approved` or `restricted`. To replace a tool, select an already approved alternative. That may be a tool developed internally or an approved third-party solution. A different person with the explicit `approver` role reviews and approves or rejects the proposal. Approval is bound to the policy revision that the proposer saw. If the policy changed in the meantime, reload and submit a fresh proposal.

Use the same canonical tool ID as the configured Factory catalogue to project the effective decision into the authenticated catalogue. The projection shows status, revision and an alternative when that approved tool is visible to the reader. A non-approved status disables catalogue self-service for the tool and agents that depend on it. An approved status preserves the existing entitlement. Enforcement at the network or source remains with the relevant system.

With a BU policy enabled, readers see only the teams explicitly assigned to their Observatory group and BU. The organisation-wide evidence and governance operations require an explicitly mapped Observatory group with global BU scope. A role from a separate group cannot supply this grant.

Use the adoption view to follow whether eligible teams are using the alternative after a pilot or rollout. Adoption is measured for that alternative's own tool key. Changing a governance status alone does not create an adoption count.

| API operation | Access within the Observatory group |
| --- | --- |
| `GET /api/v1/shadow-ai` | Viewer, editor, approver or admin |
| `POST /api/v1/shadow-ai/evidence` and `/api/v1/shadow-ai/periods` | Admin |
| `POST /api/v1/shadow-ai/policies/proposals` | Editor or admin, with JSON fields `tool`, `status`, `alternative` (key or `null`) and `expected_revision` (0 for a new policy) |
| `POST /api/v1/shadow-ai/policies/proposals/{id}/decision` | Explicit approver with a different identity from the proposer, with JSON field `decision` set to `approve` or `reject` |

## Operate and verify

- Live data is separate from the public snapshot, metrics and Pages. Keep the private SQLite volume and its backups restricted to operators. The live API is served without browser caching.
- The read API keeps a 12-month horizon for team and adoption data. Startup and subsequent successful writes prune expired data. Pending proposals expire after 90 days and have a 500-item cap. Decided proposals are retained for up to 365 days and capped at 5,000. Policies have a 500-item cap. Manage backup and local journal retention separately.
- The local journal is committed with Observatory changes. Verify its SHA-256 chain with `docker compose exec api python -m app.shadow_ai verify /data/shadow-ai/observatory.sqlite3`. New decision entries bind a digest of the proposal ID, tool, status, alternative, expected revision and decision. Earlier entries retain their original chain format. Recovering the exact policy proposal after expiry requires separate retention. Seal and export with `python -m app.cyber` using `--kind shadow --path /data/shadow-ai/observatory.sqlite3`, a dedicated stream and a checkpoint kept independently of the application. [Cyber operator guide](fr/CYBER_SECURITY.md#sceller-et-vérifier-un-journal)
- The repository validates the upstream contract with a mock of its pinned response shape. Use a private pilot instance to verify your SSO group mapping, current source data, roster and collector coverage before relying on the figures.

## Démarrage en français

Dans la carte 3D, ouvrez **Observatoire des usages IA** à la place des sanitaires, ou passez par l’onglet **Usine**. La démo publique utilise des données fictives. Le déploiement d’entreprise exige un SSO et un groupe explicitement autorisé. Ajoutez les variables de la section [Activation](#enable-the-protected-observatory), configurez votre fournisseur d’identité, puis démarrez le profil `sso`.

1. Déployez Open Shadow AI séparément sur un réseau privé. Le script `scripts/shadow_ai_pull.py` récupère son bilan global en lecture seule. Les événements d’inventaire et d’observation restent des signaux. L’adoption se mesure à partir des usages instrumentés. [Commande de synchronisation](#connect-open-shadow-ai)
2. Produisez le fichier mensuel dans votre environnement de confiance, avec une seule équipe d’appartenance par personne pour le mois. Gardez les identités et les preuves brutes côté source. L’Observatoire conserve une seule mesure par équipe, outil et mois. Il privilégie l’adoption si les populations active et inactive atteignent le seuil, puis le signal le plus probant qui l’atteint. [Format et commande d’import](#upload-monthly-team-and-adoption-counts)
3. Un éditeur propose une décision et peut désigner une alternative déjà approuvée. Une autre personne, membre du groupe doté du rôle explicite `approver`, valide ou rejette la proposition. Le compte automatisé d’import appartient uniquement au groupe d’import. Suivez ensuite l’adoption de l’alternative dans les équipes éligibles. [Circuit de décision](#review-tools-and-adoption)
4. Utilisez l’identifiant canonique du catalogue Factory pour y retrouver la décision et l’alternative visible. Les statuts en revue, en pilote et restreints bloquent le libre-service du catalogue. La politique BU limite la lecture aux équipes associées au groupe SSO de l’Observatoire. Les décisions et les imports exigent une habilitation globale explicite dans ce même groupe. [Scellement et export Cyber](fr/CYBER_SECURITY.md#sceller-et-vérifier-un-journal)
