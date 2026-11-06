# Sécurité

*[English version](../SECURITY.md)*

Ce document décrit l'architecture de sécurité de la Virtual AI Factory, son durcissement pour la
production et la conduite à tenir en cas d'incident. Il s'adresse aux DSI, CTO et RSSI qui doivent
décider si la stack mérite leur confiance, et aux ingénieurs qui le vérifieront.

Chaque contrôle ci-dessous cite le code, le test ou la commande qui le prouve. Les éléments conçus
mais pas encore livrés sont marqués **Feuille de route**. Documents associés :

- [THREAT_MODEL.md](THREAT_MODEL.md) : flux de données, actifs, acteurs, STRIDE par composant et
  menaces propres aux LLM, avec les risques résiduels ;
- [SECURITY_CONTROLS.md](SECURITY_CONTROLS.md) : chaque contrôle rapproché de l'OWASP Top 10 for
  LLM Applications 2025, de l'OWASP ASVS 5.0.0, du NIST SP 800-218 (SSDF), du CIS Docker Benchmark
  et de l'annexe A de l'ISO/IEC 27001:2022, avec des repères NIS2 et DORA ;
- [../../SECURITY.md](../../SECURITY.md#politique-de-sécurité-français) : signaler une
  vulnérabilité et vérifier une release.

## 1. La posture de sécurité en une minute

La stack a deux postures, et elle indique toujours laquelle est active :

| | Démo (par défaut) | Appliquée (production) |
|---|---|---|
| Usage | démo sans clé sur un poste ou dans Codespaces | un déploiement réel |
| Authentification au bord | `FACTORY_AUTH=none` | `FACTORY_AUTH=sso` (OpenID Connect via oauth2-proxy, profil `sso`) |
| Identité dans l'API | `FACTORY_AUTH_MODE=none` : personne n'est authentifié, toute requête agit comme `admin` | `FACTORY_AUTH_MODE=proxy` : identité venant du proxy de bord uniquement, RBAC, règle des quatre yeux |
| Posture publiée | `security.status: demo`, avertissement `auth_disabled` | `security.status: enforced` (ou `degraded` avec des codes de raison) |

Vérifiable à tout moment, sans identifiant et sans exposer de secret :

```sh
curl -s https://<FACTORY_HOST>/api/v1/health | jq .security
```

Le même objet figure dans `meta.security` du snapshot. `degraded` signifie : mode appliqué avec un
avertissement de configuration (par exemple aucun proxy de confiance, aucune correspondance de
rôles, une valeur invalide écartée) ou une chaîne d'audit cassée ou indisponible. Code :
`posture()` dans [`api/app/security.py`](../../api/app/security.py) ; test :
`test_posture_in_health_and_snapshot_meta` dans
[`api/tests/test_security.py`](../../api/tests/test_security.py).

Les contrôles qui ne dépendent pas de la posture (durcissement des conteneurs, segmentation
réseau, routage des données sensibles, garde-fous, aucun jeton dans le navigateur, chaîne
d'approvisionnement) sont toujours actifs.

## 2. Architecture et frontières de confiance

```text
 Internet (non fiable)
   Navigateur (sans jeton)  Fournisseur d'identité (Entra ID, Okta...)  Fournisseurs LLM (cloud, VPS)
        │ HTTPS                       ▲ OIDC                             ▲ C1/C2 seulement, PII masquées
 ═══════╪═════════════════════════════╪═════════════════════════════════╪═══ Hôte Docker ═══
        ▼                             │                                 │
   edge (Caddy) : TLS, en-têtes, limites, auth none | basic | sso,      │
   supprime les en-têtes d'identité envoyés par les clients             │
     ├─► dashboard (page statique)    │                                 │
     ├─► oauth2-proxy (profil sso) ───┘                                 │
     └─► api   [réseau edge-identity : identité acceptée depuis l'adresse fixe du bord seulement]
           └─► Prometheus · Grafana (jeton Viewer) · Langfuse · Qdrant · LiteLLM (côté serveur)
                                                                        │
   Passerelle LiteLLM + callback de l'usine ────────────────────────────┘
     └─ C3/C4 ─► GPU local (vLLM) uniquement, sans repli, échec fermé   [WireGuard si distant]
   Alloy ─► docker-socket-proxy (liste blanche GET) ─► socket Docker
   internes uniquement : Loki · ClickHouse · Postgres · Valkey · SeaweedFS · Prometheus
```

Réseaux (`docker-compose.yml`, section `networks`) :

| Réseau | Accès Internet | Membres | Rôle |
|---|---|---|---|
| `factory` | **aucun** (`internal: true`) | tous les services | trafic entre services |
| `egress` | oui | `edge` (ACME), `litellm` (fournisseurs LLM), `oauth2-proxy` (fournisseur d'identité), Ollama et vLLM (téléchargement des modèles) | la seule sortie |
| `edge-identity` | aucun | `edge` à l'adresse fixe `FACTORY_EDGE_IDENTITY_IP` (par défaut `10.89.255.2`), `api`, `oauth2-proxy` | le seul chemin où une identité peut être affirmée ; les autres membres reçoivent une adresse d'une `ip_range` distincte, aucun conteneur ne peut prendre l'adresse du bord |
| `docker-api` | aucun | `alloy`, `docker-socket-proxy` | API Docker en lecture pour la collecte des journaux |

Seul `edge` publie des ports (80, 443, et 3001/3002 liés à `127.0.0.1` pour l'accès local à
Grafana et Langfuse). LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres, Valkey, SeaweedFS et Loki
ne sont jamais routés par le bord, pas plus que le `/metrics` de l'API. Vérifier :
`scripts/smoke_stack.sh` contrôle « API /metrics not exposed through the edge » ; lister les
membres avec `docker network inspect ai-factory_factory ai-factory_egress`.

Les frontières de confiance, les flux et les menaces sont détaillés dans
[THREAT_MODEL.md](THREAT_MODEL.md).

## 3. Identité, authentification et rôles

### 3.1 Au proxy de bord

`FACTORY_AUTH` (dans `.env`) choisit comment le bord authentifie les utilisateurs du dashboard et
de l'API :

- `none` : démo, sans authentification.
- `basic` : authentification HTTP basique contre une empreinte bcrypt (`FACTORY_BASIC_AUTH_HASH`,
  produite par `caddy hash-password`) ; le nom d'utilisateur est transmis à l'API.
- `sso` : OpenID Connect via oauth2-proxy (MIT, profil `sso`), configurable pour Microsoft Entra
  ID, Okta, Google Workspace, Keycloak ou tout fournisseur OIDC (exemples dans `.env.example`).
  Flux « authorization code » avec PKCE `S256` ; session dans un cookie chiffré `__Host-`,
  `Secure`, `HttpOnly`, `SameSite=Lax`, 8 heures par défaut ; filtres optionnels par domaine
  d'e-mail et par groupe (`SSO_EMAIL_DOMAINS`, `SSO_ALLOWED_GROUPS`). Les pages sans session sont
  redirigées vers le fournisseur, les appels d'API reçoivent un simple 401. oauth2-proxy refuse de
  démarrer sans configuration valide, et le bord répond alors 502 (échec fermé).

**Intégrité des en-têtes d'identité.** Dans tous les modes, le bord supprime tout en-tête
`X-Forwarded-User`, `X-Forwarded-Email`, `X-Forwarded-Groups` et `X-Auth-Request-*` envoyé par un
client ; seul le bord les pose (le nom basique, ou la réponse d'oauth2-proxy via `forward_auth
copy_headers`, qui supprime d'abord les copies du client). Grafana et Langfuse gardent leur propre
connexion, et les en-têtes d'identité sont aussi supprimés sur leurs noms d'hôte.

Vérifier : [`compose/sso/test_sso_e2e.sh`](../../compose/sso/test_sso_e2e.sh) réalise une vraie
connexion auprès d'un fournisseur OIDC de test (Dex) avec le Caddyfile livré, et contrôle les
redirections, les 401, que les en-têtes forgés n'atteignent jamais l'API, que l'API ne voit
l'identité que depuis l'adresse fixe du bord, et qu'un utilisateur hors de `SSO_ALLOWED_GROUPS`
reçoit 403 (17 contrôles). Il demande Docker ; la CI le lance sur déclenchement manuel (workflow
`ci`, entrée `sso_e2e`). À chaque changement, le job de CI `compose` lance
[`compose/caddy/tests/test_identity_headers.sh`](../../compose/caddy/tests/test_identity_headers.sh) :
en-têtes `X-Forwarded-*`, `X-Auth-Request-*` et d'adresse client forgés sur chaque route et chaque
hôte, en modes `none`, `basic` et `sso`, face à des services factices qui renvoient ce qu'ils
reçoivent (68 contrôles).
Configuration : [compose/README.fr.md](../../compose/README.fr.md).

### 3.2 Dans l'API

`FACTORY_AUTH_MODE=proxy` active l'identité et les autorisations dans l'API
([`api/app/security.py`](../../api/app/security.py)) :

- **Identité venant du bord uniquement.** `X-Forwarded-User`, `-Email` et `-Groups` ne sont lus
  que si le pair TCP figure dans `FACTORY_TRUSTED_PROXIES` (par défaut dans `docker-compose.yml` :
  l'adresse fixe du bord, en `/32`). Toute autre requête vers `/api/*` reçoit 401
  `untrusted_source` avant lecture du corps, chemins inconnus compris (refus par défaut). Seul
  `/api/v1/health` reste public (sonde de vivacité, sans identité ni secret). Les en-têtes
  dupliqués, trop longs, avec caractères de contrôle ou trop de groupes sont refusés (401
  `invalid_identity`).
- **Échec fermé sur erreur de configuration.** Un mode inconnu est traité comme `proxy` ; un CIDR
  invalide ou universel (`0.0.0.0/0`, `::/0`) est écarté ; un groupe associé à un rôle inconnu est
  ignoré ; sans aucune correspondance de rôles, tout le monde est refusé. Chaque cas est signalé
  par un avertissement et la posture passe à `degraded`.
- **RBAC.** Cinq rôles, attribués à partir des groupes SSO par `FACTORY_ROLE_MAP`
  (`groupe:rôle,...` ; un utilisateur dans plusieurs groupes reçoit l'union) et éventuellement
  `FACTORY_DEFAULT_ROLE` :

  | Rôle | Permissions |
  |---|---|
  | `viewer` | `read` (tous les points de lecture, la page OpenAPI) |
  | `editor` | `read`, `spaces.write`, `placement.decide` |
  | `maintainer` | `read`, `registry.model_version` |
  | `approver` | `read`, `registry.assessment`, `audit.read` |
  | `admin` | toutes |

  `GET /api/v1/whoami` montre l'identité, les rôles et les permissions vus par l'API. Les refus
  sont `{detail, code}` avec un `detail` localisé.

Vérifier (tout dans [`api/tests/test_security.py`](../../api/tests/test_security.py), lancé par
le job de CI `api-tests` de [`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)) :
`test_proxy_mode_authorization_matrix` (chaque point d'accès × chaque rôle),
`test_every_api_route_declares_exactly_one_permission` (une nouvelle route sans permission fait
échouer la CI), `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers`,
`test_proxy_mode_unknown_api_path_is_denied_by_default`,
`test_proxy_mode_rejects_ambiguous_or_malformed_identity`, `test_misconfiguration_fails_closed`.
Détails : [api/README.fr.md](../../api/README.fr.md).

## 4. Règle des quatre yeux (séparation des tâches)

Un changement de version de modèle remet l'évaluation des risques du use case à `pending` et
bloque le déploiement jusqu'à une revue (moteur de gouvernance, [GOVERNANCE.md](GOVERNANCE.md)).
Avec l'authentification active, une revue favorable (`approved`, `approved_with_conditions`) est
refusée avec 403 `separation_of_duties` si l'approbateur partage une clé d'identité (identifiant,
ou e-mail comparé sans tenir compte de la casse) avec l'identité qui a enregistré la version
courante via l'API. Le contrôle s'exécute sous le verrou d'état (pas de concurrence entre
contrôle et écriture), le refus est lui-même audité, et `pending` / `rejected` restent possibles.
Chaque évaluation enregistre `reviewed_by`, `reviewed_by_role` et `separation_of_duties`
(`verified`, `unknown_recorder`, `not_required`, `not_enforced`).

Avant d'ouvrir une revue, lisez `review_revision` dans le registre ou le snapshot. Envoyez cette
valeur SHA-256 opaque dans `expected_review_revision`. L'API la compare sous le même verrou que
la règle des quatre yeux. Si elle est périmée, l'API renvoie 409 `stale_review_revision`.
Rechargez le dossier et reprenez la revue. Une valeur absente ou mal formée renvoie 422.
La révision porte sur la proposition individuelle. Examinez les politiques partagées séparément.
Les anciennes approbations restent liées à la version du modèle jusqu'à une nouvelle revue.
À la migration, faites revoir les dossiers actifs avant la mise en service.

Limite : une version de modèle définie dans Git (`config/usecases/*.yaml`) n'a pas d'auteur
enregistré par l'API (`unknown_recorder`) : son contrôle des quatre yeux est la revue du
changement Git (protégez la branche par défaut et exigez une revue favorable, voir section 14,
étape 11).

Vérifier : `test_four_eyes_refuses_self_approval_and_records_the_reviewer`,
`test_four_eyes_is_checked_under_the_state_lock`,
`test_four_eyes_version_defined_in_git_has_no_api_recorder`,
`test_four_eyes_not_enforced_in_demo_mode_but_recorded`.

## 5. Piste d'audit infalsifiable

Chaque écriture (version de modèle, évaluation, réservations 3D, décision de placement) ajoute un
enregistrement JSON à `FACTORY_STATE_DIR/audit.jsonl` ([`api/app/audit.py`](../../api/app/audit.py)) :
qui (acteur, rôle, mode d'authentification), quoi (action, cible, résultat, empreintes de la
cible avant et après), quand (UTC), d'où (IP du client, identifiant de requête). Les
enregistrements ne contiennent que des identifiants, versions, décisions et empreintes, jamais de
corps de requête, de texte libre ou de secret.

- **Chaîne d'empreintes.** `hash` est le SHA-256 du JSON canonique de l'enregistrement ;
  `prev_hash` le relie au précédent. Modifier, supprimer, insérer, réordonner ou tronquer une ligne
  casse la chaîne à cet endroit.
- **Aucune écriture non auditée.** L'enregistrement est écrit avec `O_APPEND` et `fsync` sous le
  verrou du fichier d'état ; s'il ne peut pas être écrit, le changement d'état est annulé et l'API
  répond 503.
- **Vérification.** `GET /api/v1/audit/verify` (approver, admin) recalcule toute la chaîne,
  signale le premier maillon cassé et rapproche l'état stocké du journal (un changement fait hors
  de l'API apparaît en `drift`). Les auditeurs peuvent vérifier une copie hors ligne avec la seule
  bibliothèque standard : `docker compose exec api python -m app.audit verify /data/audit.jsonl`
  (code de sortie 1 si la chaîne est cassée).
- **Témoins.** Chaque enregistrement est aussi émis comme ligne de journal JSON (`factory.audit`)
  collectée dans Loki, et l'empreinte de tête est publiée dans `/api/v1/health`, `meta.security` et
  les métriques `factory_audit_chain_ok`, `factory_audit_events_total{action}`.

Limites (reprises dans le modèle de menaces) : la chaîne n'est pas clé-dépendante ; quelqu'un qui
peut écrire dans le volume `api-state` pourrait réécrire tout le fichier ; la copie Loki et
l'empreinte publiée le rendent détectable, une clé HMAC ou un ancrage sur stockage WORM relève de
la **Feuille de route**. `audit.jsonl` n'a pas encore de rotation (**Feuille de route**) :
conservez-le avec vos sauvegardes.

Vérifier : `test_every_write_is_audited_with_a_valid_chain`, `test_tampering_is_detected`
(modification, modification avec nouvelle empreinte, suppression, réordonnancement, données
corrompues, troncature), `test_offline_verifier_cli`,
`test_state_changed_outside_the_api_is_reported_as_drift`,
`test_no_unaudited_write_when_the_audit_log_cannot_be_written`,
`test_concurrent_writers_keep_one_valid_chain`.

## 6. Aucun jeton dans le navigateur

- Le dashboard ne parle qu'à l'API d'agrégation, en même origine, via le bord. Il n'appelle jamais
  Grafana, LiteLLM ou Langfuse et ne détient ni jeton ni clé.
- Le jeton Grafana (compte de service *Viewer* créé par `grafana-bootstrap`) est écrit dans un
  volume monté en lecture seule dans l'API et lu côté serveur depuis `GRAFANA_TOKEN_FILE`. La clé
  maître de LiteLLM et les clés Langfuse restent dans la configuration côté serveur de l'API.
- **Proxy Grafana restreint** : uniquement des `GET` vers une liste blanche de chemins Grafana, et
  uniquement des requêtes PromQL *nommées* définies côté serveur. Le navigateur ne peut pas envoyer
  de PromQL arbitraire.
- Aucun point d'accès ne renvoie de secret : les réponses de LiteLLM et de Grafana sont
  reconstruites champ par champ.

Vérifier : le smoke test contrôle « Grafana token absent from every browser-facing response » et
« API lists the dashboards through its server-side Viewer token » ;
`test_no_secret_in_any_response_with_auth_on`.

## 7. Données sensibles : GPU local uniquement, échec fermé

Les use cases portent une classe de données de C1 (public) à C4 (secret). Les classes C3 et C4 sont
traitées **uniquement** sur le GPU local :

- **Politique de placement** (`api/app/engines/placement.py`) : une charge C3/C4 va sur le GPU
  local ; si le GPU est indisponible, elle est **mise en file d'attente**, jamais envoyée au cloud
  ni au VPS.
- **Application à la passerelle** ([`compose/litellm/factory_callbacks.py`](../../compose/litellm/factory_callbacks.py)) :
  toute requête dont `metadata.sensitivity` vaut C3/C4 est redirigée vers `chat-sensitive`, quel
  que soit le modèle demandé. Cela couvre les complétions chat et texte et les API Responses et
  Messages (`FACTORY_ENFORCE_SENSITIVE_ROUTING=true`, par défaut).
- **Aucun repli hors du GPU** ([`compose/litellm/config.yaml`](../../compose/litellm/config.yaml)) :
  `chat-sensitive` et `embed-default` (les corpus peuvent contenir des documents C3/C4) n'ont pas
  de repli. Si le GPU local est indisponible, ils échouent fermés (erreur HTTP) et l'alerte
  Prometheus `FactorySensitiveRouteDown` se déclenche.
- **Non journalisées** : prompts et réponses C3/C4 sont masqués dans Langfuse et exclus de toutes
  les intégrations de journalisation de LiteLLM (`turn_off_message_logging`).
- **GPU distant** : si le GPU est sur une autre machine que le VPS, reliez-le par un tunnel chiffré
  (WireGuard) et n'exposez jamais vLLM (section 14, étape 5).

Vérifier : le smoke test contrôle « chat-sensitive served by the local GPU », « C4 request on
chat-default rerouted to the local GPU », « local GPU down: chat-sensitive fails closed (no
cloud/VPS fallback) », « sensitive (C3) traces masked in Langfuse » ; tests unitaires
`test_sensitive_request_rerouted_and_never_logged`,
`test_responses_and_messages_api_are_rerouted_too` dans
[`compose/litellm/tests/test_guardrails.py`](../../compose/litellm/tests/test_guardrails.py).

Risque résiduel : la classe de données est une étiquette posée par l'application appelante. Un
document C3 envoyé avec l'étiquette C1 partirait vers le cloud, avec pour seul filet le masquage
des données personnelles ci-dessous. Classez à la source et limitez qui détient les identifiants
de la passerelle (voir le modèle de menaces).

## 8. Garde-fous LLM

Exécutés par le callback de la passerelle avant qu'une requête ne quitte la passerelle
([compose/README.fr.md](../../compose/README.fr.md)) :

- **Détection d'injection de prompt et de jailbreak** sur le contenu non fiable (texte
  utilisateur, sorties d'outils, documents récupérés) : contournement d'instructions,
  exfiltration du prompt système, jailbreaks par jeu de rôle, délimiteurs de gabarits de chat,
  charges encodées, caractères Unicode cachés (tags, bidirectionnels), en anglais et en français,
  après normalisation Unicode ; un remplissage au-delà du budget d'analyse est lui-même signalé.
  `FACTORY_GUARDRAIL_MODE=flag` (par défaut : transmis et étiqueté), `block` (HTTP 400 avec un
  message générique) ou `off` ; une valeur inconnue vaut `block`.
- **Masquage des données personnelles** avant tout alias susceptible de quitter le GPU local :
  adresses e-mail, numéros de téléphone, IBAN (longueur et contrôle mod-97), cartes de paiement
  (Luhn) et NIR (avec sa clé) deviennent `[REDACTED_<KIND>]` (`FACTORY_PII_REDACTION=true`, par
  défaut).
- **Aucun prompt dans les journaux** : `redact_messages_in_exceptions: true` (sinon LiteLLM copie
  le début du prompt dans les messages d'erreur), `store_prompts_in_spend_logs: false` ; les
  métadonnées et métriques des garde-fous ne contiennent que des identifiants de règles et des
  comptes.
- Métrique `factory_llm_guardrail_events_total{type,action,usecase}`.

Vérifier : 76 tests unitaires, `python -m pytest -q compose/litellm/tests` (job de CI
`guardrails`) ; le smoke test contrôle « guardrail: crafted prompt injection
flagged », « PII redacted before the cloud target » (le cloud simulé a reçu 0 e-mail en clair),
« gateway logs contain no prompt text ».

Limites : la détection repose sur des règles et manquera des attaques nouvelles ; elle réduit le
risque, elle ne le supprime pas. Les réponses ne sont pas analysées. Voir
[THREAT_MODEL.md](THREAT_MODEL.md#5-menaces-propres-aux-llm).

## 9. Durcissement de l'API et du proxy de bord

API ([api/README.fr.md](../../api/README.fr.md)) :

- limitation du débit des écritures par acteur (par IP du client sans authentification) : 429 avec
  `Retry-After` (`FACTORY_RATE_LIMIT_PER_MIN`, 60 par défaut, rafale de 20) ;
- corps de requête limités à 64 Kio (`FACTORY_MAX_BODY_BYTES` ; 413), et refusés avec 401 avant
  lecture quand l'appelant n'est pas authentifié ;
- sur chaque réponse : `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'; ...`,
  `X-Content-Type-Options: nosniff`, `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY`,
  `Cross-Origin-Resource-Policy: same-origin`, et `Cache-Control: no-store` sous `/api/` ;
- `X-Request-ID` propagé ou généré, conservé dans l'enregistrement d'audit ; l'IP du client n'est
  prise dans `X-Forwarded-For` que derrière un proxy de confiance ;
- une ligne JSON sur `factory.security` par requête refusée (jamais de valeur d'en-tête non
  fiable, de chaîne de requête ni de corps) ; métrique `factory_security_denied_total{reason}` ;
- CORS fermé par défaut ; la page OpenAPI demande `read` et charge Swagger UI dans une version
  épinglée avec Subresource Integrity et un nonce CSP par réponse ;
- le conteneur s'exécute avec un utilisateur non root et un système de fichiers racine en lecture
  seule.

Proxy de bord ([`compose/caddy/Caddyfile`](../../compose/caddy/Caddyfile)) :

- certificats TLS automatiques (ACME) avec `FACTORY_SCHEME=https` et un vrai domaine dans
  `FACTORY_HOST` ; `Strict-Transport-Security` uniquement en HTTPS (un hôte de démo en HTTP n'est
  jamais épinglé) ;
- `Content-Security-Policy` pour le dashboard (Three.js embarqué, aucun CDN par défaut),
  `Permissions-Policy` (caméra, micro, géolocalisation, paiement, USB... refusés),
  `Cross-Origin-Opener-Policy`, `Cross-Origin-Resource-Policy` ; `Server`, `Via` et
  `X-Powered-By` supprimés ;
- limites de corps de requête (1 Mo dashboard et API, 10 Mo Grafana, 20 Mo Langfuse), délais
  d'en-tête et de corps, en-têtes plafonnés à 64 Ko ;
- `/.well-known/security.txt` (RFC 9116) sur chaque nom d'hôte.

Vérifier : `test_security_headers_on_every_api_response`, `test_headers_also_on_denials_and_metrics`,
`test_writes_are_rate_limited_per_actor_with_retry_after`,
`test_docs_page_is_pinned_with_sri_and_a_nonce`,
`test_client_ip_comes_from_x_forwarded_for_only_behind_a_trusted_proxy` ; le smoke test contrôle
« edge headers (HSTS absent over HTTP, Permissions-Policy, COOP/CORP, nosniff, no Server header) »,
« /.well-known/security.txt », « edge request body limit (413 above 1 MB) ».

## 10. Durcissement des conteneurs et de l'hôte

Mesuré avec `docker inspect` sur la stack en marche ([compose/README.fr.md](../../compose/README.fr.md)) :

- chaque service : `no-new-privileges`, `cap_drop: [ALL]`, limite de PID, limites de mémoire et de
  CPU, journaux plafonnés ;
- chaque service des profils par défaut et `sim` : système de fichiers racine en lecture seule
  (tmpfs pour les zones de travail) et utilisateur non root, avec des exceptions documentées :
  `edge` garde `NET_BIND_SERVICE` (son binaire porte cette capacité de fichier), le service
  ponctuel `volume-init` tourne en root avec `CHOWN` et `DAC_READ_SEARCH` et sans réseau,
  `docker-socket-proxy` tourne en root sans aucune capacité par défaut (réglez
  `DOCKER_SOCKET_PROXY_USER=65534:<gid docker>` pour le passer en `nobody`) ;
- **Socket Docker** : aucun service ne le monte sauf `docker-socket-proxy`
  ([wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy), MIT), en lecture seule,
  qui ne répond qu'à Alloy et ne laisse passer que des `GET` sur ping, version, réseaux, liste des
  conteneurs, inspection et journaux. Création, exec, archive, export et appels sur les images sont
  refusés.

Vérifier : le smoke test contrôle « no-new-privileges », « capabilities dropped », « read-only
root », « non-root », « PID limit on every container », « Docker socket mounted by
docker-socket-proxy only », « Docker API proxy: Alloy can list containers, cannot create one or
list images (200 405 403) ».

Risques résiduels : l'inspection d'un conteneur renvoie son environnement, donc Alloy peut lire les
secrets passés en variables d'environnement (les passer en fichiers secrets relève de la
**Feuille de route**). Les profils `vps` et `local-gpu` (Ollama, vLLM, dcgm-exporter) tournent avec
l'utilisateur par défaut de leurs images, sans système de fichiers racine en lecture seule, et
n'ont pas été testés en exécution ici (pas de GPU).

## 11. Gestion des secrets

- **Aucun secret dans le dépôt.** Les valeurs viennent de `.env`, non versionné ; `.env.example`
  ne contient que des valeurs fictives (`change-me...`). `docker compose` refuse de démarrer si un
  secret requis manque (`${VAR:?}`). Dans Codespaces, `.devcontainer/setup-env.sh` génère une
  valeur aléatoire pour chaque valeur fictive.
- **Analyse.** gitleaks analyse l'arborescence et tout l'historique git à chaque push et pull
  request (job de CI `secrets`, `scripts/scan_secrets.sh`) ; l'analyseur de secrets de Trivy
  passe sur le dépôt et sur chaque image construite ici (`.github/workflows/security.yml`).
- **Exposition minimale à l'exécution.** Les secrets restent côté serveur (section 6) ; le jeton
  Grafana est dans un volume dédié monté en lecture seule ; le mot de passe Valkey est lu dans un
  fichier tmpfs privé, jamais sur la ligne de commande ; LiteLLM retire les informations de clé
  d'API de ce qu'il envoie à ses intégrations de journalisation (`redact_user_api_key_info`).
- **Production** : générez `.env` depuis un gestionnaire de secrets au déploiement et faites
  tourner les clés (section 14, étapes 3 et 4). Les fichiers secrets Docker natifs à la place des
  variables d'environnement relèvent de la **Feuille de route**.

## 12. Chaîne d'approvisionnement

- **CI épinglée.** Chaque GitHub Action tierce est épinglée par SHA de commit complet (version en
  commentaire, mise à jour chaque semaine par Dependabot après un délai de 7 jours) ; les images
  d'outils Docker sont épinglées par digest ; les outils de CI s'installent depuis des verrous à
  empreintes, en wheels uniquement (`.github/tools/`) ; les dépendances Python des images et de
  la CI s'installent depuis des verrous à empreintes (`requirements*.txt`, compilés depuis
  `requirements*.in` par pip-compile, `pip install --require-hashes`) ; les images de base des
  Dockerfiles sont épinglées en `tag@sha256:digest`. Les workflows n'ont aucune permission par
  défaut (`permissions: {}`), chaque job ne demande que ce qu'il utilise, et aucun checkout ne
  conserve le jeton (`persist-credentials: false`). actionlint et zizmor vérifient les workflows à
  chaque changement (job de CI `workflows`).
- **Analyses.** CodeQL (`security-extended` : Python, JavaScript, GitHub Actions) à chaque push,
  pull request et chaque semaine ; pip-audit sur chaque fichier de dépendances épinglé ; Trivy sur
  le dépôt (dépendances, erreurs de configuration des Dockerfiles, secrets) et sur chaque image
  construite ici, chaque jour ; gitleaks sur tout l'historique ; OpenSSF Scorecard chaque semaine.
  La CI échoue sur toute vulnérabilité HIGH ou CRITICAL connue et corrigeable, et sur les erreurs
  de configuration ou secrets HIGH/CRITICAL. Les risques acceptés sont listés avec une
  justification et une date d'expiration dans
  [`.github/trivy/trivyignore.yaml`](../../.github/trivy/trivyignore.yaml).
- **SBOM.** CycloneDX et SPDX du code source et de chaque image : `scripts/sbom.sh` en local,
  artefacts de CI (`security.yml`), pièces jointes des releases.
- **Releases signées.** Les images `ghcr.io/alebgl77/<nom>` sont signées sans clé avec Sigstore
  cosign et portent des attestations de provenance SLSA et de SBOM ; les sommes de contrôle de la
  release sont signées aussi. Avant de construire les images, la release exige la réussite de la
  dernière exécution `push` de CI, sécurité et CodeQL sur la branche par défaut, pour le commit
  exact pointé par le tag. [Vérifier une release](../../SECURITY.md#vérifier-une-release)
- **Politique de licences.** Uniquement des licences permissives dans le code livré ; les services
  AGPL (Grafana, Loki) tournent comme conteneurs séparés et non modifiés.
  `scripts/check_licenses.py` (job de CI `licenses`) vérifie chaque dépendance, image et
  sous-module par rapport à `licenses/third_party.yaml`.
- **Images tierces** épinglées par version et analysées ; les derniers résultats et les
  vulnérabilités résiduelles en amont sont dans [compose/README.fr.md](../../compose/README.fr.md).
  Les six images construites ici passent le contrôle (aucune HIGH ou CRITICAL corrigeable) ; le
  tableau de bord tourne sur `nginx-unprivileged` alpine-slim, sans les modules nginx optionnels ni
  leurs bibliothèques.

Le workflow de release, la publication Scorecard, CodeQL et les envois SARIF ont besoin de GitHub
pour s'exécuter ; ils sont vérifiés statiquement (actionlint, zizmor dans trois modes) et leurs
scripts sont testés unitairement.

## 13. Localisation des données et vie privée

- **Auto-hébergé.** Toute la stack tourne sur une infrastructure que vous maîtrisez : dashboard,
  API, passerelle, traces (Langfuse avec ClickHouse, Postgres et SeaweedFS compatible S3),
  métriques, journaux et base vectorielle. Aucune dépendance SaaS, aucune clé de licence.
- **Ce qui sort de votre infrastructure**, et uniquement via le réseau `egress` : les prompts
  C1/C2 envoyés aux points d'accès LLM cloud ou VPS que vous configurez (après masquage des données
  personnelles) ; les demandes de certificats ACME du bord ; l'échange OIDC avec votre fournisseur
  d'identité (profil `sso`) ; le téléchargement des modèles par Ollama ou vLLM. Les données C3/C4
  ne quittent jamais le GPU local.
- **Aucune télémétrie.** Les rapports d'usage et vérifications de mises à jour sont désactivés là
  où le service propose un réglage : Langfuse (`TELEMETRY_ENABLED=false`), Grafana
  (`GF_ANALYTICS_*=false`), Loki (`analytics.reporting_enabled: false`), Alloy
  (`--disable-reporting`), Qdrant (`QDRANT__TELEMETRY_DISABLED`), vLLM (`VLLM_NO_USAGE_STATS`,
  `DO_NOT_TRACK`). Les services du réseau interne `factory` ne peuvent pas joindre Internet.
- **Aucun entraînement sur vos données.** `factory.fine_tuning` vaut `false` ; l'API refuse toute
  action `retrain` ou `fine_tune` dans la boucle de données.
- **Données fictives uniquement** dans le dépôt : chaque personne, use case et coût de `config/`
  est inventé.
- **Données personnelles que la stack traite** : identités (identifiant, e-mail, groupes) et
  adresses IP des clients dans le journal d'audit et les journaux de sécurité, e-mail de
  l'évaluateur (`reviewed_by`) dans le registre. Définissez leur durée de conservation avec votre
  DPO (section 14, étape 8).
- **La langue n'élargit pas la surface** : `?lang=en|fr` ne fait que choisir des textes de la
  configuration et du catalogue de messages ; une valeur inconnue retombe sur la langue par
  défaut.

## 14. Guide de durcissement pour la production

La démo écoute sur la boucle locale (`EDGE_BIND=127.0.0.1`). Pour autoriser l'accès distant,
choisissez explicitement l'interface d'écoute dans `EDGE_BIND`, configurez HTTPS et le SSO, puis
lancez le précontrôle avec les mêmes fichiers Compose et profils que pour le déploiement.

```sh
docker compose --env-file /path/to/.env --profile sso config --format json | python scripts/check_production.py
```

Renseignez `GRAFANA_PUBLIC_URL` et `LANGFUSE_PUBLIC_URL` avec leurs URL HTTPS de production.
Pour les services vLLM fournis, ajoutez `--profile local-gpu` au rendu et au déploiement. Pour
un GPU accessible par DNS privé, passez `--private-gpu-host HOST` au précontrôle pour chaque nom
exact. Vérifiez la résolution DNS et les règles de sortie sur la cible.

Le JSON rendu peut contenir des secrets. Passez-le directement au script sans le consigner ni
le stocker. Le précontrôle exige de désactiver le mode démo, le Workbench et le profil `sim`.
Il rejette les modes réseau non autorisés et les destinations GPU de simulation ou sur IP
publique. Vérifiez aussi TLS, l'identité, le trafic sortant réel et la reprise sur le tenant cible.
[Revue de sécurité entreprise](../ENTERPRISE_SECURITY_REVIEW.md)

Utilisez une version maintenue de Docker Engine à partir de 28.0.0. Les versions antérieures
peuvent rendre les ports publiés sur la boucle locale accessibles à d'autres hôtes sur le même
segment de niveau 2. Testez ces ports depuis un second hôte sur ce segment avant toute exposition.
[Publication des ports Docker](https://docs.docker.com/engine/network/port-publishing/)

À appliquer dans cet ordre. Chaque étape indique comment la vérifier.

1. **TLS.** Mettez un vrai domaine dans `FACTORY_HOST` (avec des enregistrements DNS pour lui,
   `grafana.` et `langfuse.`) et `FACTORY_SCHEME=https` : Caddy obtient et renouvelle les
   certificats (TLS 1.2 et 1.3) et envoie HSTS. Le cookie de session Grafana suit
   (`GRAFANA_COOKIE_SECURE=auto`, la valeur par défaut, pose `Secure` en `https`).
   Vérifier : `curl -sI https://<hôte>/ | grep -i strict-transport`.
2. **Authentification unique.** `FACTORY_AUTH=sso`, le bloc `SSO_*` de `.env.example`, démarrage
   avec `--profile sso` ; puis `FACTORY_AUTH_MODE=proxy` et `FACTORY_ROLE_MAP` à partir de vos
   groupes (réservez `admin` à un petit groupe d'urgence). Imposez l'authentification
   multifacteur dans le fournisseur d'identité. Configurez Grafana et Langfuse avec le même
   fournisseur (ils gardent leur propre connexion) puis désactivez leurs comptes administrateurs
   locaux. Vérifier : `security.status` vaut `enforced` sans avertissement (section 1), et
   `GET /api/v1/whoami` montre les rôles attendus.
3. **Secrets.** Remplacez chaque valeur `change-me` (par exemple `openssl rand -hex 32` ; les clés
   LiteLLM commencent par `sk-`). Conservez-les dans un gestionnaire de secrets (HashiCorp Vault,
   coffre de clés d'un cloud...) et générez `.env` au déploiement, propriété de root, mode `0600`.
   Ne le versionnez jamais. Vérifier : `grep -c change-me .env` renvoie 0.
4. **Rotation des clés.** Au moins tous les 90 jours, à chaque départ d'une personne qui y avait
   accès, et immédiatement en cas de soupçon :

   | Secret | Comment le faire tourner | Effet |
   |---|---|---|
   | `LITELLM_MASTER_KEY`, clés des fournisseurs (`CLOUD_API_KEY`...) | nouvelle valeur dans `.env` (et chez le fournisseur), `docker compose up -d litellm api` | aucun pour les utilisateurs |
   | `SSO_CLIENT_SECRET` | nouveau secret dans le fournisseur d'identité, puis `.env`, redémarrer `oauth2-proxy` | aucun |
   | `SSO_COOKIE_SECRET` | nouvelle valeur, redémarrer `oauth2-proxy` | chaque utilisateur se reconnecte (c'est aussi la façon de révoquer toutes les sessions) |
   | mots de passe base de données, Valkey, ClickHouse, S3 | changer dans le service, puis `.env`, redémarrer les dépendants | court redémarrage |
   | jeton Grafana Viewer | le révoquer dans Grafana (Administration, Service accounts), puis `docker compose up grafana-bootstrap` : il révoque les jetons périmés et en écrit un nouveau | aucun |
   | `LITELLM_SALT_KEY`, `LANGFUSE_SALT`, `LANGFUSE_ENCRYPTION_KEY` | prévoir une migration : ils protègent des identifiants et données stockés | les changer rend les éléments stockés illisibles |

5. **Réseau.** N'ouvrez que 80 et 443 (TCP, et UDP 443 pour HTTP/3) vers Internet ; laissez 3001 et
   3002 liés à `127.0.0.1` (`EDGE_ALT_BIND`). Reliez un GPU distant par WireGuard et n'exposez
   jamais vLLM ni Ollama. Limitez le trafic sortant au pare-feu de l'hôte à vos fournisseurs LLM,
   votre fournisseur d'identité et votre autorité ACME.
6. **Hôte.** Tenez Docker Engine à jour, chiffrez les disques qui portent les volumes, faites
   tourner `docker-socket-proxy` en `nobody` (`DOCKER_SOCKET_PROXY_USER`), appliquez les sections
   hôte et démon du CIS Docker Benchmark (hors du périmètre de ce dépôt), synchronisez l'horloge
   (NTP : les horodatages d'audit sont en UTC).
7. **Sauvegardes.** Sauvegardez les volumes `postgres-data`, `clickhouse-data`, `seaweedfs-data`,
   `qdrant-data`, `grafana-data`, `caddy-data` et `api-state` (état et `audit.jsonl`), chiffrés,
   hors site, avec immuabilité (verrouillage d'objet ou WORM) pour le journal d'audit. Testez une
   restauration chaque trimestre, puis lancez `python -m app.audit verify` sur le fichier
   restauré.
8. **Journalisation et conservation.** Les valeurs par défaut sont celles d'une démo : Loki garde
   7 jours (`compose/loki/loki.yml`, `retention_period`), Prometheus 30 jours
   (`PROMETHEUS_RETENTION`), et `audit.jsonl` n'est jamais tourné. Réglez la conservation selon
   votre politique (les journaux de sécurité sont souvent gardés 12 mois ou plus ; les données
   personnelles pas plus que nécessaire). Transférez les lignes JSON `factory.security` et
   `factory.audit` à votre SIEM. Les règles livrées alertent sur une chaîne d'audit rompue
   (`FactoryAuditChainBroken`, `factory_audit_chain_ok == 0` pendant 1 minute, critique), sur un
   pic de refus (`FactorySecurityDenialsSpike`, plus de 50 en 5 minutes) et sur
   `FactorySensitiveRouteDown` ; elles n'ont pas d'Alertmanager : reliez Prometheus au vôtre.
9. **Garde-fous.** Après réglage sur votre trafic, passez `FACTORY_GUARDRAIL_MODE=block` pour les
   applications exposées à des utilisateurs non fiables ; gardez `FACTORY_PII_REDACTION=true` et
   `FACTORY_ENFORCE_SENSITIVE_ROUTING=true`.
10. **Mises à jour.** Vérifiez chaque release avant de la déployer
    ([../../SECURITY.md](../../SECURITY.md#vérifier-une-release)), déployez par digest, et
    analysez de nouveau les images tierces chaque semaine (de nouvelles CVE apparaissent sans
    aucun changement).
11. **Gouvernance du dépôt** (pour votre fork) : protégez la branche par défaut (revues et
    contrôles obligatoires `ci`, `codeql`, `security`), limitez qui peut créer des tags `v*`,
    exigez des actions épinglées par SHA complet, activez la protection des secrets au push, les
    mises à jour de sécurité Dependabot et le signalement privé de vulnérabilités.

## 15. Bases de la réponse à incident

Un point de départ à adapter à votre propre processus, pas un substitut.

1. **Détecter.** Signaux produits par la stack : `factory_audit_chain_ok == 0` ou `chain_ok: false`
   dans `/api/v1/health` ; une hausse de `factory_security_denied_total{reason}` ou des lignes
   `event="request.denied"` dans Loki ; `factory_llm_guardrail_events_total{action="blocked"}` ;
   `FactorySensitiveRouteDown` ; `FactoryGatewayErrorRateHigh` ; une croissance inattendue des
   coûts (`factory_llm_cost_eur_total`, alertes FinOps `budget_breach`, `retry_storm`).
2. **Contenir.** Couper le trafic LLM : `docker compose stop litellm` (plus rien n'atteint les
   fournisseurs). Révoquer toutes les sessions web : faire tourner `SSO_COOKIE_SECRET` et
   redémarrer `oauth2-proxy`. Faire tourner `LITELLM_MASTER_KEY` et les clés des fournisseurs.
   Bloquer une adresse au pare-feu de l'hôte. Retirer un utilisateur du groupe dans le fournisseur
   d'identité.
3. **Préserver les preuves** avant toute modification des volumes : copier `audit.jsonl` et lancer
   le vérificateur hors ligne sur la copie ; noter le `head_hash` de `/api/v1/health` ; exporter
   les journaux Loki et `docker compose logs` ; prendre un instantané des volumes. Ne recréez pas
   les volumes des conteneurs.
4. **Éradiquer et rétablir.** Redéployer depuis des images publiées vérifiées avec
   `.github/scripts/verify_image.sh` ; restaurer les données depuis une sauvegarde antérieure à
   l'incident ; vérifier la chaîne d'audit ; faire tourner tout secret que l'attaquant a pu voir
   (l'environnement des conteneurs est lisible via `docker inspect`).
5. **Notifier.** Vérifiez vos obligations avec votre service juridique ; à titre indicatif : NIS2
   (directive (UE) 2022/2555, art. 23) pour les entités essentielles et importantes : alerte
   précoce sous 24 heures, notification d'incident sous 72 heures, rapport final sous un mois ;
   DORA (règlement (UE) 2022/2554, art. 19) pour les entités financières : rapports initial,
   intermédiaire et final des incidents majeurs liés aux TIC, avec des délais fixés par le
   règlement délégué (UE) 2025/301 de la Commission ; RGPD (art. 33) : violation de données
   personnelles notifiée à l'autorité de contrôle sous 72 heures.
6. **Apprendre.** Rédigez une revue post-incident sans recherche de coupable, ajoutez un test qui
   aurait détecté le problème, et signalez toute vulnérabilité de ce projet comme décrit dans
   [../../SECURITY.md](../../SECURITY.md#politique-de-sécurité-français).

## Signaler une vulnérabilité

Voir [../../SECURITY.md](../../SECURITY.md#politique-de-sécurité-français) : signalement privé de
vulnérabilité GitHub sur `alebgl77/ai-factory`, accusé de réception sous 72 heures, tri sous 7
jours. Ne publiez jamais de preuve d'exploitation ni de secret dans une issue publique.
