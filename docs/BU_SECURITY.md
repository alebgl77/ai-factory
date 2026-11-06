# Business-unit boundaries

Deploy the aggregation API behind the trusted identity proxy and set `FACTORY_BU_POLICY=/config/business-units.yaml` to a read-only operator policy based on [the example](../config/business-units.example.yaml). The example groups and subjects are fictional. Configure matching functional grants in `FACTORY_ROLE_MAP` and use the stable forwarded user subject as the identity key. Live proxy mode requires the policy at startup. Restart the API to apply a policy change.

## Scope of each grant

- Each verified group must grant both the requested role permission and the relevant business unit. The authority for a case is its GitOps `cost_center`. A group with `['*']` can grant organization-wide access according to its assigned functional role. The default role cannot supply scope or combine with another group's assignment to grant an action.
- BU readers receive authorized case records, directory entries and case-linked catalogue entries. The service filters usage before computing FinOps totals and excludes unidentified events, global infrastructure telemetry and shared fixed fees from partial showback. Full shared chargeback, raw Grafana queries and the global audit trail require an explicit cross-BU grant and the corresponding role. The internal Prometheus scrape omits business dimensions in this mode. Provision directly exposed telemetry tools as independent operator services with their own authorization.
- The shared HTTP Workbench remains restricted to explicit cross-BU grants in this deployment. An enterprise that exposes Workbench to individual BUs uses an isolated workspace/store per BU with separately authenticated deployment entry points. Workspace choice is an operator binding. See [adaptive Workbench](ADAPTIVE_WORKBENCH.md).
- `shadow_teams` binds each previously aggregated team key to one BU. The Observatory applies that binding in addition to its dedicated SSO groups. Its organization-wide evidence and policy decision queue require cross-BU authorization.

## Reservations and recovery

An authorized editor writes reservations for the profile mapped to their verified subject. Each reservation must reference a case, a resource assigned to its BU and participants assigned to that BU. C3/C4 cases require a resource in `secure_resources`. Readers see only records proven to belong to allowed case/resource/profile combinations. A blank display therefore represents the authorized slice of the planning. The API checks collisions against the full retained schedule and returns a generic conflict for unavailable slots.

The existing versioned GET/PUT merges editable rows under the state and audit lock. Scoped clients return both `version` and the opaque `view_token` from their most recent GET. The token binds the actor, permissions, policy and current version, so an old browser request cannot delete rows after an SSO account switch. A partial write preserves other owners' records and ownerless legacy data. The 409 current state uses the caller's projection. The browser holds protected bookings only in memory and starts with an empty planning when the server has no reservations. Restoring older state is an explicit global-admin action at `/api/v1/spaces/recovery`. Ambiguous legacy rows that might occupy a resource require reconciliation before that resource can be reserved. Preserve the original state, audit and private `.booking-view.key` together when preparing a recovery.

## Frontière BU en français

Un même groupe SSO doit apporter le droit fonctionnel et le centre métier. Le registre fournit la BU et la sensibilité du dossier. Le serveur filtre les données avant de calculer les coûts et conserve les réservations des autres périmètres. Les frais partagés et la télémétrie globale restent réservés aux personnes explicitement habilitées. La reprise d’un ancien planning passe par une action d’administration transversale, journalisée séparément des opérations habituelles.
