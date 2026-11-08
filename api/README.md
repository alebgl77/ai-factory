# Aggregation API — Virtual AI Factory

*English · [Français](README.fr.md)*

FastAPI service (Python 3.12, MIT licence) that aggregates the GitOps configuration (`config/`),
the writable state (`FACTORY_STATE_DIR`) and the live sources (Prometheus, Grafana, Langfuse,
LiteLLM, Qdrant) for the 3D dashboard. Interface contract: [`docs/CONTRACT.md`](../docs/CONTRACT.md),
sections 2.4, 3, 4, 5 and 9.

All demo data is **fictional** and generated deterministically (seed + date). No fine-tuning and
no retraining: the data loop can only reindex, update an evaluation set, change the routing or
request a human review.

## Running locally

```bash
cd api
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
FACTORY_CONFIG_DIR=../config FACTORY_STATE_DIR=../data FACTORY_DEMO_MODE=always \
  uvicorn app.main:app --port 8000
curl -s 'localhost:8000/api/v1/snapshot?lang=en' | head -c 400
```

OpenAPI documentation: `http://localhost:8000/api/v1/docs`.

## Environment variables

| Variable | Purpose | Default |
|---|---|---|
| `FACTORY_CONFIG_DIR` | `config/` directory | `/config` |
| `FACTORY_DEMO_MODE` | `auto` (live sources when reachable, demo otherwise → `mixed`), `always`, `never` | `auto` |
| `FACTORY_DEMO_SEED` | seed of the demo generator | `42` |
| `FACTORY_DEFAULT_LANG` | language of the responses when `?lang=` is absent or unknown: `en` or `fr` | `en` |
| `FACTORY_STATE_DIR` | writable state (model versions, reviews, 3D bookings) | `/data` |
| `PROMETHEUS_URL`, `GRAFANA_URL`, `GRAFANA_TOKEN_FILE`, `GRAFANA_PROM_DS_UID` | observability sources | — |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | Langfuse public API | — |
| `LITELLM_URL`, `LITELLM_MASTER_KEY` | LLM gateway (server side only) | — |
| `QDRANT_URL` | vector database | — |
| `FACTORY_DEMO_DATE` (optional) | anchor date of the demo, `YYYY-MM-DD` | today |
| `GRAFANA_PUBLIC_URL` (optional) | prefix of the Grafana links returned to the browser | `/grafana` |
| `FACTORY_CORS_ORIGINS` (optional) | allowed CORS origins (closed by default) | empty |
| `FACTORY_MAX_BODY_BYTES`, `FACTORY_CONNECTOR_TIMEOUT_S`, `FACTORY_CACHE_TTL_S` (optional) | limits | `65536`, `2`, `10` |
| `FACTORY_AUTH_MODE` | `none` (demo: nobody authenticated, everything allowed as `admin`, flagged) or `proxy` (identity from the SSO proxy); any other value fails closed as `proxy` | `none` |
| `FACTORY_TRUSTED_PROXIES` | CIDRs allowed to assert an identity and an `X-Forwarded-For` (e.g. the edge proxy only); `0.0.0.0/0` and `::/0` are refused | empty |
| `FACTORY_ROLE_MAP` | SSO group to role, e.g. `ai-governance:approver,ai-platform:maintainer,staff:editor` | empty |
| `FACTORY_DEFAULT_ROLE` (optional) | role of an authenticated user without any mapped group (e.g. `viewer`) | none (deny) |
| `FACTORY_RATE_LIMIT_PER_MIN`, `FACTORY_RATE_LIMIT_BURST` | write requests per minute and bucket size, per actor (or client IP); `0` disables | `60`, `20` |

## Endpoints

`/api/v1/health`, `/snapshot`, `/registry[/{id}]`, `POST /registry/{id}/model-version`,
`POST /registry/{id}/assessment`, `/costs?days=30&mode=showback|chargeback`, `/pipeline`,
`/dataloop`, `/placement`, `POST /placement/decide`, `/agentops`, `/catalog`, `/skills`, `/gpu`,
`/grafana/dashboards`, `/grafana/metric/{name}`, `GET|PUT /spaces/state`, `/audit`,
`/audit/verify`, `/whoami`, and `/metrics` (Prometheus exposition of the factory KPIs).

## Languages (English first, French second)

- Every read endpoint accepts `?lang=en|fr` (region subtags such as `fr-FR` are accepted). When
  the parameter is absent or unknown, `FACTORY_DEFAULT_LANG` applies (itself `en`).
- Responses are **fully resolved**: every `{en: "...", fr: "..."}` mapping of the configuration
  becomes the string of the chosen language (fallback: the other language, then the first value),
  and every string the engines generate (waste alert messages and actions, placement reasons,
  data-loop details, pipeline stage details, risk reasons, rollback triggers, Art. 4 notes,
  warnings, error messages) comes from the message catalogue [`app/i18n.py`](app/i18n.py).
  The browser never receives a language mapping.
- Ids, enums, numbers, dates, metric names and Grafana uids are identical in every language.
  The snapshot carries `meta.lang` and `meta.languages: ["en", "fr"]`; snapshots are cached per
  language.
- Writes (`POST`/`PUT`) are language-independent: the state keeps ids, versions, dates and
  decisions. `?lang=` on a write only selects the language of the texts of the answer.
- Adding a message: add a key to `MESSAGES` in `app/i18n.py` with an `en` and an `fr` template
  using the same `str.format` fields, then call `msg(lang, "the.key", field=value)` from the
  engine, which receives `lang` as an argument (engines never read global state). French numbers
  are rendered with a decimal comma by `msg`.

## Security

Every control below is enforced in code and covered by tests (`tests/test_security.py`: identity,
the full role × endpoint matrix, four-eyes, audit chain and tamper detection, rate limiting,
headers, logs, no secret in any answer). The posture is published without any secret in
`GET /api/v1/health` (`security`) and in `meta.security` of the snapshot, so the dashboard can
show it: `status` is `demo` (authentication off), `enforced`, or `degraded` (a configuration
warning, or a broken or unavailable audit chain; `warnings` lists the reason codes).

- No secret in any response: keys stay in the `Settings` object, the LiteLLM and Grafana answers
  are rebuilt field by field, and the Grafana token is redacted if it ever appears in an answer.
- Grafana proxy: `GET` only, only `/api/search`, `/api/dashboards/uid/{uid}` and
  `/api/datasources/proxy/uid/{uid}/api/v1/query`; only named PromQL queries (listed by
  `/api/v1/grafana/dashboards`) can be run. Grafana (AGPLv3) remains a separate, unmodified
  service: this repository ships none of its code.
- Request bodies are size-limited (413), CORS is closed by default, non-root container.

### Identity: only from the trusted SSO proxy

| `FACTORY_AUTH_MODE` | Behaviour |
|---|---|
| `none` (default, demo) | Nobody is authenticated. Every request acts as role `admin`; identity headers are **ignored** whatever their source. Health and `meta.security` say `status: demo`, warning `auth_disabled`. |
| `proxy` | An SSO proxy in front of the API (oauth2-proxy, or any equivalent) authenticates the user and forwards `X-Forwarded-User`, `X-Forwarded-Email` and `X-Forwarded-Groups` (one comma-separated value, as oauth2-proxy sends it). The API reads them **only** when the TCP peer is in `FACTORY_TRUSTED_PROXIES`. Any other request to `/api/*` is refused with 401 before its body is read, unknown paths included (deny by default). Duplicate, over-long or control-character identity headers are refused. |

`/api/v1/health` stays public (liveness probe; it carries no identity and no secret). `/metrics`
is not routed by the edge proxy. A misconfiguration fails closed: an unknown mode is treated as
`proxy`, an invalid or catch-all CIDR is dropped, a group mapped to an unknown role is ignored,
and each case is reported in `security.warnings`.

### Roles (RBAC) and separation of duties

| Role | Permissions |
|---|---|
| `viewer` | `read`: every read endpoint, `/api/v1/docs` and `/api/v1/openapi.json` |
| `editor` | `read`, `spaces.write` (`PUT /spaces/state`), `placement.decide` (`POST /placement/decide`) |
| `maintainer` | `read`, `registry.model_version` (`POST /registry/{id}/model-version`) |
| `approver` | `read`, `registry.assessment` (`POST /registry/{id}/assessment`), `audit.read` (`/audit`, `/audit/verify`) |
| `admin` | everything |

Roles come from SSO groups through `FACTORY_ROLE_MAP`; a user in several groups gets the union.
Without a mapped group, an authenticated user has no role (403) unless `FACTORY_DEFAULT_ROLE` is
set. `GET /api/v1/whoami` shows the identity, roles and permissions the API sees. 401 and 403
answers are `{detail, code}` with `detail` localised by `?lang=en|fr` and `code` one of
`untrusted_source`, `unauthenticated`, `invalid_identity`, `forbidden`, `separation_of_duties`,
`rate_limited`.

**Four-eyes rule** (AI Act record-keeping, ISO/IEC 42001 independent review): when authentication
is on, `POST /registry/{id}/assessment` with an approving decision (`approved`,
`approved_with_conditions`) is refused with 403 `separation_of_duties` if the approver shares an
identity key (user id, or e-mail compared case-insensitively) with the identity that recorded the
current model version through the API. The check runs under the state lock, the refusal is itself
audited (`outcome: denied`), and `pending` / `rejected` stay possible (they keep the deployment
gate closed). Every assessment records `reviewed_by`, `reviewed_by_role` and
`separation_of_duties`: `verified`, `unknown_recorder` (version defined in Git and reviewed
there), `not_required` or `not_enforced` (demo mode).

Registry reads and the snapshot expose `review_revision`, an opaque SHA-256 value. Send it as
`expected_review_revision` in the assessment request together with `decision` and `reviewer`.
A missing or malformed revision returns 422. A stale revision returns 409 with
`code: stale_review_revision`. Read the current proposal and review it again before resubmitting.

### Tamper-evident audit log

Every write (model version, assessment, bookings, placement decision) appends one JSON line to
`FACTORY_STATE_DIR/audit.jsonl`, under the same file lock as the state change. If the record cannot
be written the state change is rolled back and the API answers 503: there is no unaudited write.

```json
{"v":1,"seq":2,"ts":"2026-11-04T10:00:00.000Z","actor":"amir@example.org","role":"approver",
 "auth_mode":"proxy","action":"registry.assessment","target":"usecase:uc-02","outcome":"success",
 "before_hash":"…","after_hash":"…","request_id":"…","client_ip":"198.51.100.7",
 "details":{"decision":"approved","reviewer":"p07","reviewed_model_version":"2026-11",
 "separation_of_duties":"verified"},"prev_hash":"…","hash":"…"}
```

- `hash` is the SHA-256 of the canonical JSON of the record without `hash` (keys sorted, no
  whitespace, UTF-8); `prev_hash` is the previous line's `hash` (64 zeros for the first). Editing,
  deleting, inserting or reordering a line breaks the chain.
- `before_hash` / `after_hash` are digests of the stored target before and after the change; the
  records hold ids, versions, decisions and hashes only, never a request body, a free-text field or
  a secret.
- `GET /api/v1/audit?offset=&limit=&action=` (roles `approver`, `admin`) lists the records, newest
  first. `GET /api/v1/audit/verify` recomputes the whole chain, reports the first broken link
  (`line`, `seq`, `reason`) and reconciles the state with the log: a stored document whose digest
  differs from the last recorded `after_hash` is reported as `drift` (changed outside the API).
- Offline check for auditors (standard library only):
  `docker compose exec api python -m app.audit verify /data/audit.jsonl` (exit code 1 when broken).
- A hash chain cannot stop someone who rewrites the whole file. Each record is therefore also
  emitted as a JSON log line (`factory.audit`, collected into Loki), and the head hash is
  published in `/api/v1/health`, `meta.security` and metrics, so copies kept outside the state
  volume witness the history.
- Metrics: `factory_audit_events_total{action}`, `factory_audit_chain_ok` (0/1),
  `factory_security_denied_total{reason}`.

### Rate limiting, headers, request ids, security logs

- Writes are rate limited per actor (per client IP without authentication) by an in-memory token
  bucket: 429 with `Retry-After`. Reads are not limited. Refused writes leave no audit record.
- Every answer carries `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src
  'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `Referrer-Policy:
  no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`, and
  `Cache-Control: no-store` under `/api/`. The OpenAPI page loads Swagger UI from a pinned version
  with Subresource Integrity and a per-answer CSP nonce.
- `X-Request-ID` is propagated (when it matches `[A-Za-z0-9._:-]{1,128}`) or generated, returned on
  every answer and stored in the audit record.
- The client address is the TCP peer, or the right-most untrusted `X-Forwarded-For` hop when the
  peer is a trusted proxy; a client can never choose its own address.
- Every denied request writes one JSON line on `factory.security` (`event: request.denied`,
  `reason`, `status`, `method`, `path`, `request_id`, `client_ip`, `actor`, `roles`), never a header
  value sent by an untrusted source, a query string or a body.

### Turning authentication on behind an SSO proxy

1. Put an SSO proxy in front of the API, for example oauth2-proxy (MIT). Reverse-proxy mode
   forwards `X-Forwarded-User`, `X-Forwarded-Email` and `X-Forwarded-Groups` and strips
   client-supplied values. With Caddy `forward_auth` (oauth2-proxy `--set-xauthrequest`), copy and
   rename the headers; `copy_headers` deletes any client-supplied value first (Caddy 2.11.7):

   ```caddyfile
   handle /oauth2/* {
       reverse_proxy oauth2-proxy:4180   # sign-in flow (start, callback, sign-out)
   }
   handle /api/* {
       forward_auth oauth2-proxy:4180 {
           uri /oauth2/auth
           copy_headers X-Auth-Request-User>X-Forwarded-User X-Auth-Request-Email>X-Forwarded-Email X-Auth-Request-Groups>X-Forwarded-Groups
       }
       reverse_proxy api:8000
   }
   ```

   Do not add a top-level `request_header -X-Forwarded-User`: Caddy orders `request_header` after
   `forward_auth`, so it would delete the identity that was just set.
2. Set `FACTORY_AUTH_MODE=proxy`, `FACTORY_TRUSTED_PROXIES` to the address of that proxy only, and
   `FACTORY_ROLE_MAP` to your groups. Keep the API reachable only through the proxy.
3. Check `GET /api/v1/health`: `security.status` must read `enforced` with no warning.
4. Alert on `factory_audit_chain_ok == 0`, on a rise of `factory_security_denied_total` and on
   `event="request.denied"` lines in Loki; back up `audit.jsonl` with the `api-state` volume.

## Engines (pure, tested functions)

`app/engines/`: `risk` (AI Act risk tiers, Annex III, Art. 6(3) derogation, reassessment on every
model version), `costs` (showback/chargeback, platform overhead, 8 waste rules with estimated
savings), `pipeline` (7 stages, evaluation thresholds per risk tier, deployment blocked while a
reassessment is required, automatic rollback), `dataloop` (signal → action, any retraining is
refused), `placement` (sensitive ⇒ local only, recommends waiting during a local GPU outage),
`agentops`, `skills` (Art. 4 AI literacy), `catalog`.

## Offline snapshots of the dashboard

`scripts/build_snapshot.py` embeds two snapshots between the `<!-- DEMO_SNAPSHOT_START -->` and
`<!-- DEMO_SNAPSHOT_END -->` markers of `dashboard/index.html`:
`<script type="application/json" id="demo-snapshot-en">` and `id="demo-snapshot-fr"`.

```bash
python scripts/build_snapshot.py                                  # update dashboard/index.html (both languages)
python scripts/build_snapshot.py --check                          # exit 1 if either embedded snapshot is stale
python scripts/build_snapshot.py --out snapshot.json --date 2026-11-03            # English JSON only
python scripts/build_snapshot.py --lang fr --out snapshot-fr.json --date 2026-11-03
```

## Dependencies

See `requirements.txt`: MIT, BSD, Apache-2.0 and PSF licences. `httpx` depends on `certifi`
(MPL-2.0, weak file-level copyleft, used unmodified).
