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

## Endpoints

`/api/v1/health`, `/snapshot`, `/registry[/{id}]`, `POST /registry/{id}/model-version`,
`POST /registry/{id}/assessment`, `/costs?days=30&mode=showback|chargeback`, `/pipeline`,
`/dataloop`, `/placement`, `POST /placement/decide`, `/agentops`, `/catalog`, `/skills`, `/gpu`,
`/grafana/dashboards`, `/grafana/metric/{name}`, `GET|PUT /spaces/state`, and `/metrics`
(Prometheus exposition of the factory KPIs).

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

- No secret in any response: keys stay in the `Settings` object, the LiteLLM and Grafana answers
  are rebuilt field by field, and the Grafana token is redacted if it ever appears in an answer.
- Grafana proxy: `GET` only, only `/api/search`, `/api/dashboards/uid/{uid}` and
  `/api/datasources/proxy/uid/{uid}/api/v1/query`; only named PromQL queries (listed by
  `/api/v1/grafana/dashboards`) can be run. Grafana (AGPLv3) remains a separate, unmodified
  service: this repository ships none of its code.
- Request bodies are size-limited (413), CORS is closed by default, `nosniff` / `no-store`
  headers, non-root container.

## Engines (pure, tested functions)

`app/engines/`: `risk` (AI Act risk tiers, Annex III, Art. 6(3) derogation, reassessment on every
model version), `costs` (showback/chargeback, platform overhead, 8 waste rules with estimated
savings), `pipeline` (7 stages, evaluation thresholds per risk tier, deployment blocked while a
reassessment is required, automatic rollback), `dataloop` (signal → action, any retraining is
refused), `placement` (sensitive ⇒ local only, queued when the local GPU is unavailable),
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
