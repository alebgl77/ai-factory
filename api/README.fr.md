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
| `FACTORY_AUTH_MODE` | `none` (démo : personne n'est authentifié, tout est permis en `admin`, signalé) ou `proxy` (identité fournie par le proxy SSO) ; toute autre valeur échoue en mode fermé (`proxy`) | `none` |
| `FACTORY_TRUSTED_PROXIES` | CIDR autorisés à transmettre une identité et un `X-Forwarded-For` (par exemple le seul proxy de bordure) ; `0.0.0.0/0` et `::/0` sont refusés | vide |
| `FACTORY_ROLE_MAP` | groupe SSO vers rôle, par exemple `ai-governance:approver,ai-platform:maintainer,staff:editor` | vide |
| `FACTORY_DEFAULT_ROLE` (optionnel) | rôle d'un utilisateur authentifié sans groupe associé (par exemple `viewer`) | aucun (refus) |
| `FACTORY_RATE_LIMIT_PER_MIN`, `FACTORY_RATE_LIMIT_BURST` | écritures par minute et taille du seau, par acteur (ou adresse IP du client) ; `0` désactive | `60`, `20` |

## Points d'accès

`/api/v1/health`, `/snapshot`, `/registry[/{id}]`, `POST /registry/{id}/model-version`,
`POST /registry/{id}/assessment`, `/costs?days=30&mode=showback|chargeback`, `/pipeline`,
`/dataloop`, `/placement`, `POST /placement/decide`, `/agentops`, `/catalog`, `/skills`, `/gpu`,
`/grafana/dashboards`, `/grafana/metric/{nom}`, `GET|PUT /spaces/state`, `/audit`,
`/audit/verify`, `/whoami`, et `/metrics`
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

Chaque contrôle ci-dessous est appliqué dans le code et couvert par des tests
(`tests/test_security.py` : identité, matrice complète rôles × points d'accès, quatre yeux, chaîne
d'audit et détection d'altération, limitation de débit, en-têtes, journaux, aucun secret dans les
réponses). La posture est publiée sans aucun secret dans `GET /api/v1/health` (`security`) et dans
`meta.security` de l'instantané, pour que le tableau de bord puisse l'afficher : `status` vaut
`demo` (authentification désactivée), `enforced`, ou `degraded` (avertissement de configuration,
chaîne d'audit rompue ou indisponible ; `warnings` liste les codes en cause).

- Aucun secret dans les réponses : les clés restent dans l'objet `Settings`, les réponses de
  LiteLLM et de Grafana sont reconstruites champ par champ, le jeton Grafana est masqué s'il
  apparaît dans une réponse.
- Proxy Grafana : uniquement `GET`, uniquement `/api/search`, `/api/dashboards/uid/{uid}` et
  `/api/datasources/proxy/uid/{uid}/api/v1/query` ; seules les requêtes PromQL nommées
  (`/api/v1/grafana/dashboards` en donne la liste) sont exécutables. Grafana (AGPLv3) reste un
  service séparé et non modifié : ce dépôt n'en embarque aucun code.
- Corps de requête limités (413), CORS fermé par défaut, conteneur non-root.

### Identité : uniquement depuis le proxy SSO de confiance

| `FACTORY_AUTH_MODE` | Comportement |
|---|---|
| `none` (défaut, démo) | Personne n'est authentifié. Chaque requête agit avec le rôle `admin` ; les en-têtes d'identité sont **ignorés** quelle que soit leur origine. La santé et `meta.security` indiquent `status: demo`, avertissement `auth_disabled`. |
| `proxy` | Un proxy SSO placé devant l'API (oauth2-proxy ou équivalent) authentifie l'utilisateur et transmet `X-Forwarded-User`, `X-Forwarded-Email` et `X-Forwarded-Groups` (une seule valeur séparée par des virgules, comme l'envoie oauth2-proxy). L'API ne les lit **que** si le pair TCP figure dans `FACTORY_TRUSTED_PROXIES`. Toute autre requête vers `/api/*` est refusée en 401 avant la lecture de son corps, chemins inconnus compris (refus par défaut). Les en-têtes d'identité dupliqués, trop longs ou porteurs de caractères de contrôle sont refusés. |

`/api/v1/health` reste public (sonde de vivacité ; il ne porte ni identité ni secret). `/metrics`
n'est pas routé par le proxy de bordure. Une erreur de configuration échoue en mode fermé : un mode
inconnu est traité comme `proxy`, un CIDR invalide ou universel est écarté, un groupe associé à un
rôle inconnu est ignoré, et chaque cas est signalé dans `security.warnings`.

### Rôles (RBAC) et séparation des tâches

| Rôle | Permissions |
|---|---|
| `viewer` | `read` : tous les points d'accès en lecture, `/api/v1/docs` et `/api/v1/openapi.json` |
| `editor` | `read`, `spaces.write` (`PUT /spaces/state`), `placement.decide` (`POST /placement/decide`) |
| `maintainer` | `read`, `registry.model_version` (`POST /registry/{id}/model-version`) |
| `approver` | `read`, `registry.assessment` (`POST /registry/{id}/assessment`), `audit.read` (`/audit`, `/audit/verify`) |
| `admin` | tout |

Les rôles viennent des groupes SSO via `FACTORY_ROLE_MAP` ; un utilisateur présent dans plusieurs
groupes cumule les rôles. Sans groupe associé, un utilisateur authentifié n'a aucun rôle (403),
sauf si `FACTORY_DEFAULT_ROLE` est défini. `GET /api/v1/whoami` montre l'identité, les rôles et les
permissions vus par l'API. Les réponses 401 et 403 sont de la forme `{detail, code}`, `detail` étant
traduit selon `?lang=en|fr` et `code` valant `untrusted_source`, `unauthenticated`,
`invalid_identity`, `forbidden`, `separation_of_duties` ou `rate_limited`.

**Règle des quatre yeux** (traçabilité de l'AI Act, revue indépendante ISO/IEC 42001) : quand
l'authentification est active, `POST /registry/{id}/assessment` avec une décision d'approbation
(`approved`, `approved_with_conditions`) est refusé en 403 `separation_of_duties` si l'approbateur
partage une clé d'identité (identifiant, ou e-mail comparé sans tenir compte de la casse) avec
l'identité qui a enregistré la version de modèle courante via l'API. Le contrôle s'exécute sous le
verrou d'état, le refus est lui-même audité (`outcome: denied`), et `pending` / `rejected` restent
possibles (ils gardent la porte de déploiement fermée). Chaque évaluation enregistre
`reviewed_by`, `reviewed_by_role` et `separation_of_duties` : `verified`, `unknown_recorder`
(version définie dans Git et revue là-bas), `not_required` ou `not_enforced` (mode démo).

Le registre et le snapshot exposent `review_revision`, une valeur SHA-256 opaque. Envoyez-la dans
`expected_review_revision` avec `decision` et `reviewer` lors de la soumission. Une valeur absente
ou mal formée renvoie 422. Une version périmée renvoie 409 avec `code: stale_review_revision`.
Relisez le dossier en cours et reprenez la revue avant une nouvelle soumission.

### Journal d'audit infalsifiable

Chaque écriture (version de modèle, évaluation, réservations, décision de placement) ajoute une
ligne JSON à `FACTORY_STATE_DIR/audit.jsonl`, sous le même verrou de fichier que la modification
d'état. Si l'enregistrement ne peut pas être écrit, la modification est annulée et l'API répond
503 : aucune écriture n'échappe à l'audit.

```json
{"v":1,"seq":2,"ts":"2026-11-04T10:00:00.000Z","actor":"amir@example.org","role":"approver",
 "auth_mode":"proxy","action":"registry.assessment","target":"usecase:uc-02","outcome":"success",
 "before_hash":"…","after_hash":"…","request_id":"…","client_ip":"198.51.100.7",
 "details":{"decision":"approved","reviewer":"p07","reviewed_model_version":"2026-11",
 "separation_of_duties":"verified"},"prev_hash":"…","hash":"…"}
```

- `hash` est le SHA-256 du JSON canonique de l'enregistrement sans `hash` (clés triées, sans
  espaces, UTF-8) ; `prev_hash` est le `hash` de la ligne précédente (64 zéros pour la première).
  Modifier, supprimer, insérer ou réordonner une ligne rompt la chaîne.
- `before_hash` / `after_hash` sont les empreintes de la cible stockée avant et après la
  modification ; les enregistrements ne contiennent que des identifiants, versions, décisions et
  empreintes, jamais un corps de requête, un texte libre ou un secret.
- `GET /api/v1/audit?offset=&limit=&action=` (rôles `approver`, `admin`) liste les enregistrements,
  du plus récent au plus ancien. `GET /api/v1/audit/verify` recalcule toute la chaîne, indique le
  premier maillon rompu (`line`, `seq`, `reason`) et rapproche l'état du journal : un document
  stocké dont l'empreinte diffère du dernier `after_hash` enregistré est signalé en `drift`
  (modifié hors de l'API).
- Vérification hors ligne pour les auditeurs (bibliothèque standard uniquement) :
  `docker compose exec api python -m app.audit verify /data/audit.jsonl` (code de sortie 1 si rompu).
- Une chaîne d'empreintes n'empêche pas de réécrire tout le fichier. Chaque enregistrement est donc
  aussi émis en ligne de journal JSON (`factory.audit`, collectée dans Loki), et l'empreinte de tête
  est publiée dans `/api/v1/health`, `meta.security` et les métriques : des copies conservées hors du
  volume d'état attestent l'historique.
- Métriques : `factory_audit_events_total{action}`, `factory_audit_chain_ok` (0/1),
  `factory_security_denied_total{reason}`.

### Limitation de débit, en-têtes, identifiants de requête, journaux de sécurité

- Les écritures sont limitées par acteur (par adresse IP du client sans authentification) par un
  seau à jetons en mémoire : 429 avec `Retry-After`. Les lectures ne sont pas limitées. Une écriture
  refusée ne laisse aucun enregistrement d'audit.
- Chaque réponse porte `X-Content-Type-Options: nosniff`, `Content-Security-Policy: default-src
  'none'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'`, `Referrer-Policy:
  no-referrer`, `X-Frame-Options: DENY`, `Cross-Origin-Resource-Policy: same-origin`, et
  `Cache-Control: no-store` sous `/api/`. La page OpenAPI charge Swagger UI dans une version
  épinglée avec Subresource Integrity et un nonce CSP propre à chaque réponse.
- `X-Request-ID` est propagé (s'il respecte `[A-Za-z0-9._:-]{1,128}`) ou généré, renvoyé dans chaque
  réponse et conservé dans l'enregistrement d'audit.
- L'adresse du client est celle du pair TCP, ou le saut `X-Forwarded-For` non fiable le plus à
  droite quand le pair est un proxy de confiance : un client ne peut jamais choisir sa propre adresse.
- Chaque requête refusée écrit une ligne JSON sur `factory.security` (`event: request.denied`,
  `reason`, `status`, `method`, `path`, `request_id`, `client_ip`, `actor`, `roles`), jamais une valeur
  d'en-tête envoyée par une source non fiable, une chaîne de requête ou un corps.

### Activer l'authentification derrière un proxy SSO

1. Placer un proxy SSO devant l'API, par exemple oauth2-proxy (MIT). En mode proxy inverse, il
   transmet `X-Forwarded-User`, `X-Forwarded-Email` et `X-Forwarded-Groups` et supprime les valeurs
   envoyées par le client. Avec `forward_auth` de Caddy (oauth2-proxy `--set-xauthrequest`), copier
   et renommer les en-têtes ; `copy_headers` supprime d'abord toute valeur fournie par le client
   (Caddy 2.11.7) :

   ```caddyfile
   handle /oauth2/* {
       reverse_proxy oauth2-proxy:4180   # parcours de connexion (start, callback, sign-out)
   }
   handle /api/* {
       forward_auth oauth2-proxy:4180 {
           uri /oauth2/auth
           copy_headers X-Auth-Request-User>X-Forwarded-User X-Auth-Request-Email>X-Forwarded-Email X-Auth-Request-Groups>X-Forwarded-Groups
       }
       reverse_proxy api:8000
   }
   ```

   Ne pas ajouter de `request_header -X-Forwarded-User` au niveau du site : Caddy exécute
   `request_header` après `forward_auth`, ce qui effacerait l'identité qui vient d'être posée.
2. Définir `FACTORY_AUTH_MODE=proxy`, `FACTORY_TRUSTED_PROXIES` avec la seule adresse de ce proxy,
   et `FACTORY_ROLE_MAP` avec vos groupes. L'API ne doit être joignable qu'à travers ce proxy.
3. Vérifier `GET /api/v1/health` : `security.status` doit valoir `enforced`, sans avertissement.
4. Alerter sur `factory_audit_chain_ok == 0`, sur une hausse de `factory_security_denied_total` et
   sur les lignes `event="request.denied"` dans Loki ; sauvegarder `audit.jsonl` avec le volume
   `api-state`.

## Moteurs (fonctions pures, testées)

`app/engines/` : `risk` (niveaux de risque de l'AI Act, annexe III, dérogation de l'art. 6,
paragraphe 3, réévaluation à chaque version de modèle), `costs` (showback/chargeback, frais de
plateforme, 8 règles de gaspillage avec économies estimées), `pipeline` (7 étapes, seuils
d'évaluation par niveau de risque, déploiement bloqué tant qu'une réévaluation est requise,
retour arrière automatique), `dataloop` (signal → action, tout réentraînement est refusé),
`placement` (données sensibles en local, recommandation d’attendre si le GPU local est
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
