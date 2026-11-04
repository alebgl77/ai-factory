# API d'agrégation — Virtual AI Factory

*[English](README.md) · Français*

Service FastAPI (Python 3.12, licence MIT) qui agrège la configuration GitOps (`config/`), l'état
modifiable (`FACTORY_STATE_DIR`) et les sources en direct (Prometheus, Grafana, Langfuse,
LiteLLM, Qdrant) pour le tableau de bord 3D. Contrat d'interface : [`docs/CONTRACT.md`](../docs/CONTRACT.md),
sections 2.4, 3, 4, 5 et 9.

Toutes les données de démonstration sont **fictives** et générées de façon déterministe
(graine + date). Aucun fine-tuning, aucun réentraînement : la boucle de données ne peut que
réindexer, mettre à jour un jeu d'évaluation, changer le routage ou demander une revue humaine.

## Démarrage local

```bash
cd api
python3.12 -m venv .venv && . .venv/bin/activate
pip install -r requirements-dev.txt
pytest -q
FACTORY_CONFIG_DIR=../config FACTORY_STATE_DIR=../data FACTORY_DEMO_MODE=always \
  uvicorn app.main:app --port 8000
curl -s 'localhost:8000/api/v1/snapshot?lang=fr' | head -c 400
```

Documentation OpenAPI : `http://localhost:8000/api/v1/docs`.

## Variables d'environnement

| Variable | Rôle | Défaut |
|---|---|---|
| `FACTORY_CONFIG_DIR` | répertoire `config/` | `/config` |
| `FACTORY_DEMO_MODE` | `auto` (sources en direct si joignables, démo sinon → `mixed`), `always`, `never` | `auto` |
| `FACTORY_DEMO_SEED` | graine du générateur de démo | `42` |
| `FACTORY_DEFAULT_LANG` | langue des réponses quand `?lang=` est absent ou inconnu : `en` ou `fr` | `en` |
| `FACTORY_STATE_DIR` | état modifiable (versions de modèle, revues, réservations 3D) | `/data` |
| `PROMETHEUS_URL`, `GRAFANA_URL`, `GRAFANA_TOKEN_FILE`, `GRAFANA_PROM_DS_UID` | sources d'observabilité | — |
| `LANGFUSE_HOST`, `LANGFUSE_PUBLIC_KEY`, `LANGFUSE_SECRET_KEY` | API publique Langfuse | — |
| `LITELLM_URL`, `LITELLM_MASTER_KEY` | passerelle LLM (côté serveur uniquement) | — |
| `QDRANT_URL` | base vectorielle | — |
| `FACTORY_DEMO_DATE` (optionnel) | date d'ancrage de la démo `AAAA-MM-JJ` | aujourd'hui |
| `GRAFANA_PUBLIC_URL` (optionnel) | préfixe des liens Grafana renvoyés au navigateur | `/grafana` |
| `FACTORY_CORS_ORIGINS` (optionnel) | origines CORS autorisées (fermé par défaut) | vide |
| `FACTORY_MAX_BODY_BYTES`, `FACTORY_CONNECTOR_TIMEOUT_S`, `FACTORY_CACHE_TTL_S` (optionnels) | limites | `65536`, `2`, `10` |

## Points d'accès

`/api/v1/health`, `/snapshot`, `/registry[/{id}]`, `POST /registry/{id}/model-version`,
`POST /registry/{id}/assessment`, `/costs?days=30&mode=showback|chargeback`, `/pipeline`,
`/dataloop`, `/placement`, `POST /placement/decide`, `/agentops`, `/catalog`, `/skills`, `/gpu`,
`/grafana/dashboards`, `/grafana/metric/{nom}`, `GET|PUT /spaces/state`, et `/metrics`
(exposition Prometheus des KPI de l'usine).

## Langues (anglais d'abord, français ensuite)

- Chaque point d'accès en lecture accepte `?lang=en|fr` (les sous-étiquettes régionales comme
  `fr-FR` sont acceptées). Si le paramètre est absent ou inconnu, `FACTORY_DEFAULT_LANG`
  s'applique (lui-même `en` par défaut).
- Les réponses sont **entièrement résolues** : chaque correspondance `{en: "...", fr: "..."}` de
  la configuration devient la chaîne de la langue choisie (repli : l'autre langue, puis la
  première valeur), et chaque texte produit par les moteurs (messages et actions des alertes de
  gaspillage, raisons de placement, détails de la boucle de données et des étapes du pipeline,
  motifs de réévaluation du risque, déclencheurs de retour arrière, notes art. 4, avertissements,
  messages d'erreur) provient du catalogue de messages [`app/i18n.py`](app/i18n.py). Le
  navigateur ne reçoit jamais de correspondance de langue.
- Les identifiants, énumérations, nombres, dates, noms de métriques et uid Grafana sont
  identiques dans toutes les langues. L'instantané porte `meta.lang` et
  `meta.languages: ["en", "fr"]` ; le cache est tenu par langue.
- Les écritures (`POST`/`PUT`) ne dépendent pas de la langue : l'état ne garde que des
  identifiants, versions, dates et décisions. `?lang=` sur une écriture ne choisit que la langue
  des textes de la réponse.
- Ajouter un message : ajouter une clé à `MESSAGES` dans `app/i18n.py` avec un gabarit `en` et un
  gabarit `fr` utilisant les mêmes champs `str.format`, puis appeler `msg(lang, "la.cle", champ=valeur)`
  depuis le moteur, qui reçoit `lang` en argument (les moteurs ne lisent aucun état global).
  `msg` écrit les nombres français avec une virgule décimale.

## Sécurité

- Aucun secret dans les réponses : les clés restent dans l'objet `Settings`, les réponses de
  LiteLLM et de Grafana sont reconstruites champ par champ, le jeton Grafana est masqué s'il
  apparaît dans une réponse.
- Proxy Grafana : uniquement `GET`, uniquement `/api/search`, `/api/dashboards/uid/{uid}` et
  `/api/datasources/proxy/uid/{uid}/api/v1/query` ; seules les requêtes PromQL nommées
  (`/api/v1/grafana/dashboards` en donne la liste) sont exécutables. Grafana (AGPLv3) reste un
  service séparé et non modifié : ce dépôt n'en embarque aucun code.
- Corps de requête limités (413), CORS fermé par défaut, en-têtes `nosniff` / `no-store`,
  conteneur non-root.

## Moteurs (fonctions pures, testées)

`app/engines/` : `risk` (niveaux de risque de l'AI Act, annexe III, dérogation de l'art. 6,
paragraphe 3, réévaluation à chaque version de modèle), `costs` (showback/chargeback, frais de
plateforme, 8 règles de gaspillage avec économies estimées), `pipeline` (7 étapes, seuils
d'évaluation par niveau de risque, déploiement bloqué tant qu'une réévaluation est requise,
retour arrière automatique), `dataloop` (signal → action, tout réentraînement est refusé),
`placement` (sensible ⇒ local uniquement, mise en file d'attente si le GPU local est
indisponible), `agentops`, `skills` (art. 4, maîtrise de l'IA), `catalog`.

## Instantanés hors ligne du tableau de bord

`scripts/build_snapshot.py` embarque deux instantanés entre les marqueurs
`<!-- DEMO_SNAPSHOT_START -->` et `<!-- DEMO_SNAPSHOT_END -->` de `dashboard/index.html` :
`<script type="application/json" id="demo-snapshot-en">` et `id="demo-snapshot-fr"`.

```bash
python scripts/build_snapshot.py                                  # met à jour dashboard/index.html (deux langues)
python scripts/build_snapshot.py --check                          # code 1 si l'un des instantanés est obsolète
python scripts/build_snapshot.py --out snapshot.json --date 2026-11-03            # JSON anglais seul
python scripts/build_snapshot.py --lang fr --out snapshot-fr.json --date 2026-11-03
```

## Dépendances

Voir `requirements.txt` : licences MIT, BSD, Apache-2.0 et PSF. `httpx` dépend de `certifi`
(MPL-2.0, copyleft faible au niveau du fichier, utilisé sans modification).
