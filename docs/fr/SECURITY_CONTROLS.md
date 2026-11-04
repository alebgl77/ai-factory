# Matrice des contrôles de sécurité

*[English version](../SECURITY_CONTROLS.md)*

Cette matrice rapproche chaque contrôle **en place** de la Virtual AI Factory des référentiels de
sécurité courants, et indique comment le vérifier vous-même : un test, un job de CI ou une
commande. Elle aide une équipe sécurité à évaluer la stack et à réutiliser les preuves dans ses
propres travaux de conformité.

> **Indicatif, pas une certification.** Le rapprochement montre où un contrôle contribue à une
> exigence ; il n'affirme pas que la stack, ni votre déploiement, est conforme à un référentiel.
> Beaucoup d'exigences demandent aussi des mesures organisationnelles que seul l'exploitant peut
> apporter. Les écarts sont listés en [section 3](#3-écarts-connus-et-feuille-de-route).

Architecture et contexte : [SECURITY.md](SECURITY.md). Menaces et risques résiduels :
[THREAT_MODEL.md](THREAT_MODEL.md).

## Référentiels et identifiants

| Colonne | Référentiel | Identifiants utilisés |
|---|---|---|
| LLM | [OWASP Top 10 for LLM Applications 2025](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications) | `LLM01` ... `LLM10` (tous `:2025`) |
| ASVS | [OWASP ASVS 5.0.0](https://github.com/OWASP/ASVS) (mai 2025) | identifiants `<chapitre>.<section>.<exigence>`, à lire `v5.0.0-<id>`, par exemple `8.2.1` = `v5.0.0-8.2.1` |
| SSDF | NIST SP 800-218, Secure Software Development Framework (SSDF) v1.1 (février 2022) | pratiques et tâches, par exemple `PS.3.2`. La SSDF v1.2 (SP 800-218 Rev. 1) a été publiée en projet en décembre 2025 ; la SP 800-218A ajoute un profil pour l'IA générative |
| CIS | CIS Docker Benchmark | numéros des recommandations de la v1.6.0, l'édition automatisée par [docker-bench-security](https://github.com/docker/docker-bench-security) ; la v1.8.0 a été publiée en 2025 et la numérotation peut changer d'une édition à l'autre : rapprochez par le titre (donné en [section 2](#2-titres-du-cis-docker-benchmark)) |
| ISO | ISO/IEC 27001:2022, annexe A | numéros des mesures, par exemple `8.15` (journalisation) |

Les repères NIS2 et DORA sont en [section 4](#4-repères-nis2-et-dora).

Lecture de la colonne « Vérifier » : les noms de tests sont dans
[`api/tests/test_security.py`](../../api/tests/test_security.py) sauf autre fichier cité, et
tournent dans le job de CI `api-tests` ([`.github/workflows/ci.yml`](../../.github/workflows/ci.yml)) ;
« smoke » désigne un contrôle de `scripts/smoke_stack.sh --no-up` sur la stack en marche (lancé en
CI seulement sur un `workflow_dispatch` manuel avec `smoke: true`) ; « tests des garde-fous »
désigne `python -m pytest -q compose/litellm/tests` (en local, pas encore en CI).

## 1. Contrôles

### 1.1 Identité et accès

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C01 | **Authentification unique** au bord : OIDC via oauth2-proxy, PKCE `S256`, cookie `__Host-` `Secure` `HttpOnly` `SameSite=Lax`, durée 8 h, filtres par domaine et groupe (`docker-compose.yml` service `oauth2-proxy`, `compose/caddy/Caddyfile` `auth-sso`) | - | 6.8.4, 10.2.1, 3.3.1, 3.3.2, 3.3.3, 3.3.4, 7.3.2 | - | - | 5.16, 5.17, 8.5 | `compose/sso/test_sso_e2e.sh` (17 contrôles, demande Docker) ; `docker compose --profile sso config -q` |
| C02 | **Intégrité des en-têtes d'identité** : le bord supprime les `X-Forwarded-User/-Email/-Groups` des clients dans tous les modes ; l'API ne les lit que depuis `FACTORY_TRUSTED_PROXIES` (par défaut : l'adresse fixe du bord sur le réseau `edge-identity`) | - | 4.1.3, 15.3.4 | - | - | 8.5, 8.22 | `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers`, `test_client_ip_comes_from_x_forwarded_for_only_behind_a_trusted_proxy` ; e2e SSO « forged headers never reach the API » |
| C03 | **Refus par défaut, échec fermé** : chaque chemin `/api/*` exige une identité en mode proxy ; mode inconnu, CIDR universel, rôle inconnu sont refusés et signalés (`api/app/security.py`) | - | 8.2.1, 16.5.3 | PW.9.1, PW.9.2 | - | 8.3, 8.9 | `test_proxy_mode_unknown_api_path_is_denied_by_default`, `test_misconfiguration_fails_closed`, `test_proxy_mode_without_role_mapping_denies_everyone` |
| C04 | **RBAC** : cinq rôles issus des groupes SSO ; une permission par route (`ROLE_PERMISSIONS`, `require()`) | LLM06 (pour les actions de l'usine) | 8.1.1, 8.2.1, 8.3.1 | PW.8.2 | - | 5.15, 5.18, 8.2, 8.3 | `test_proxy_mode_authorization_matrix`, `test_every_api_route_declares_exactly_one_permission`, `test_role_permission_table_keeps_duties_separate` |
| C05 | **Règle des quatre yeux** : une identité ne peut pas approuver une version de modèle qu'elle a enregistrée ; contrôle sous le verrou d'état ; refus audité (`api/app/state.py`) | - | 2.3.5, 15.4.2 | - | - | 5.3 | `test_four_eyes_refuses_self_approval_and_records_the_reviewer`, `test_four_eyes_is_checked_under_the_state_lock` |

### 1.2 Audit, journalisation et surveillance

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C06 | **Piste d'audit infalsifiable** : chaîne SHA-256 de chaque écriture, horodatage UTC, acteur, rôle, identifiant de requête, IP du client ; annulation si l'enregistrement ne peut pas être écrit ; point de vérification et outil hors ligne ; détection de dérive (`api/app/audit.py`) | - | 16.2.1, 16.2.2, 16.3.3, 16.4.2 (preuve d'altération), 11.4.1 | - | - | 8.15, 5.28, 5.33 | `test_every_write_is_audited_with_a_valid_chain`, `test_tampering_is_detected`, `test_no_unaudited_write_when_the_audit_log_cannot_be_written`, `test_offline_verifier_cli` ; `GET /api/v1/audit/verify` ; `docker compose exec api python -m app.audit verify /data/audit.jsonl` |
| C07 | **Journaux d'événements de sécurité** : une ligne JSON par requête refusée (aucune valeur d'en-tête non fiable, chaîne de requête ni corps) ; enregistrements d'audit recopiés dans Loki ; métriques `factory_security_denied_total`, `factory_audit_chain_ok` (`api/app/seclog.py`, `api/app/metrics.py`) | - | 16.3.2, 16.4.1, 16.4.3 (copie Loki), 16.2.4 | - | - | 8.15, 8.16 | `test_denials_are_logged_as_json_without_spoofed_values`, `test_audit_appends_are_witnessed_in_the_logs`, `test_security_metrics` |
| C08 | **Posture de sécurité publiée** : `security` dans `/api/v1/health` et `meta.security` (`demo`, `enforced`, `degraded` + codes de raison), sans secret | - | - | - | - | 8.16, 5.36 | `test_posture_in_health_and_snapshot_meta` ; `curl -s <hôte>/api/v1/health \| jq .security` |
| C09 | **Alertes d'exploitation** : route sensible indisponible, taux d'erreur de la passerelle, alertes de gaspillage et de budget, cible indisponible (`compose/prometheus/rules/factory-alerts.yml`) | LLM10 | 16.3.4 | - | - | 8.16 | le job de CI `compose` lance `promtool check config` |

### 1.3 Durcissement de l'application et du bord

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C10 | **Limitation du débit des écritures** par acteur ou IP du client, 429 avec `Retry-After` | LLM10 (côté API) | 2.4.1, 15.2.2 | - | - | 8.6 | `test_writes_are_rate_limited_per_actor_with_retry_after`, `test_rate_limit_keys_on_client_ip_without_authentication` |
| C11 | **Limites de requêtes** : corps API 64 Kio (401 avant lecture sans authentification) ; corps au bord 1/10/20 Mo, délais d'en-tête et de corps, en-têtes de 64 Ko | LLM10 | 15.2.2 | - | - | 8.6 | `test_unauthenticated_large_body_is_refused_before_it_is_read` ; smoke « edge request body limit (413 above 1 MB) » |
| C12 | **En-têtes de sécurité** : CSP de l'API `default-src 'none'`, nosniff, `Referrer-Policy`, `X-Frame-Options`, CORP, `Cache-Control: no-store` ; CSP, Permissions-Policy, COOP, CORP au bord, HSTS uniquement en HTTPS | - | 3.4.1, 3.4.3, 3.4.4, 3.4.5, 3.4.6, 3.4.8, 14.3.2 | - | - | 8.26 | `test_security_headers_on_every_api_response`, `test_headers_also_on_denials_and_metrics` ; smoke « security headers », « edge headers » |
| C13 | **Ressources externes avec intégrité** : Swagger UI épinglé avec SRI et nonce CSP par réponse, derrière `read` ; Three.js embarqué dans l'image du dashboard (aucun CDN) | - | 3.6.1 | PW.4.1 | - | 8.26 | `test_docs_page_is_pinned_with_sri_and_a_nonce` ; smoke « dashboard served with vendored Three.js (no CDN) » |
| C14 | **Aucune fuite d'information** : `Server`, `Via`, `X-Powered-By` supprimés ; `/metrics` de l'API non routé par le bord ; messages d'erreur et de garde-fou génériques | - | 13.4.5, 13.4.6, 16.5.1 | - | - | 8.9 | smoke « API /metrics not exposed through the edge », « edge headers (... no Server header) » |
| C15 | **Aucun jeton dans le navigateur** ; proxy Grafana limité aux GET sur des chemins en liste blanche et aux requêtes PromQL nommées ; réponses reconstruites champ par champ | - | 14.2.6, 14.3.3, 13.2.4, 15.3.1 | - | - | 8.3, 8.12 | smoke « Grafana token absent from every browser-facing response » ; `test_no_secret_in_any_response_with_auth_on` ; `api/tests/test_grafana_and_live.py` (`test_non_allow_listed_path_refused_without_http_call`, `test_grafana_client_only_issues_get`, `test_dashboards_through_api_token_never_returned`) |
| C16 | **TLS** : certificats automatiques (ACME, TLS 1.2 et 1.3) avec `FACTORY_SCHEME=https` | - | 12.1.1, 12.2.1, 12.2.2 | - | - | 8.24, 8.20 | `curl -sI https://<hôte>/` ; le job de CI `compose` lance `caddy validate` |
| C17 | **Divulgation des vulnérabilités** : `SECURITY.md` (signalement privé, objectifs 72 h / 7 jours, sphère de sécurité), `security.txt` RFC 9116 sur chaque hôte | - | 15.1.1 | RV.1.3 | - | 8.8, 6.8 | smoke « /.well-known/security.txt (RFC 9116 ...) » ; [`../../SECURITY.md`](../../SECURITY.md#politique-de-sécurité-français) |

### 1.4 LLM et protection des données

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C18 | **Données sensibles sur le GPU local uniquement, échec fermé** : C3/C4 placés en local ou mis en file ; la passerelle redirige C3/C4 vers `chat-sensitive` pour toute API générative ; aucun repli pour `chat-sensitive` et `embed-default` (`api/app/engines/placement.py`, `compose/litellm/factory_callbacks.py`, `compose/litellm/config.yaml`) | LLM02, LLM08 | 14.2.3, 16.5.3 | - | - | 5.12, 5.14, 8.12, 5.23 | smoke « chat-sensitive served by the local GPU », « C4 request on chat-default rerouted to the local GPU », « local GPU down: chat-sensitive fails closed » ; `api/tests/test_placement.py` (`test_sensitive_is_queued_when_local_down_never_cloud_or_vps`) ; tests des garde-fous `test_responses_and_messages_api_are_rerouted_too` |
| C19 | **Masquage des données personnelles** avant les alias qui peuvent quitter le GPU : e-mail, téléphone, IBAN (mod-97), carte (Luhn), NIR | LLM02 | 14.2.3, 14.2.6 | - | - | 8.11, 8.12, 5.34 | tests des garde-fous `test_pii_redacted`, `test_pii_false_positives`, `test_pii_redacted_for_cloud_alias` ; smoke « PII redacted before the cloud target » |
| C20 | **Détection d'injection de prompt et de jailbreak** (règles EN et FR, Unicode caché, charges encodées, remplissage), `flag` / `block` / `off`, valeur inconnue = `block` ; métrique `factory_llm_guardrail_events_total` | LLM01, LLM07 | 16.3.3 | - | - | 8.16, 8.26 | tests des garde-fous `test_injection_detected` (25 cas), `test_benign_not_flagged` (12), `test_block_mode_refuses`, `test_unknown_mode_fails_closed` ; smoke « guardrail: crafted prompt injection flagged » |
| C21 | **Aucun prompt dans les journaux** : C3/C4 masqués dans Langfuse et `turn_off_message_logging` ; `redact_messages_in_exceptions` ; `store_prompts_in_spend_logs: false` ; les métadonnées des garde-fous ne contiennent que des identifiants de règles | LLM02 | 16.2.5, 14.2.4, 16.5.1 | - | - | 8.11, 8.15, 5.34 | smoke « gateway logs contain no prompt text », « sensitive (C3) traces masked in Langfuse » ; tests des garde-fous `test_sensitive_request_rerouted_and_never_logged`, `test_detection_returns_rule_ids_only` |
| C22 | **Aucun entraînement sur les données des clients** : `fine_tuning: false` ; la boucle de données refuse `retrain`, `fine_tune` et les actions similaires | LLM04 | - | - | - | 5.34 | `api/tests/test_dataloop.py` (`test_retrain_and_fine_tune_are_refused`, `test_requested_retrain_in_signal_is_refused`) |
| C23 | **Maîtrise des changements de modèle** : tout changement de version de modèle remet l'évaluation à `pending` et bloque le déploiement (`reevaluation_required`) ; barrières d'évaluation par niveau de risque | LLM03, LLM09 | - | - | - | 8.32 | `api/tests/test_risk.py` (`test_reevaluation_on_model_version_change`), `api/tests/test_pipeline.py` (`test_deploy_blocked_when_risk_reassessment_required`, `test_gate_thresholds_by_tier`) |
| C24 | **Visibilité des coûts** (détection du déni de portefeuille) : coût par use case et par modèle, budgets par centre de coût, règles de gaspillage `budget_breach` et `retry_storm`, délais de la passerelle et une seule nouvelle tentative | LLM10 | 15.1.3 | - | - | 8.6, 8.16 | `api/tests/test_costs.py` ; smoke « callback metrics in Prometheus » |
| C25 | **Localisation des données, aucune télémétrie** : auto-hébergé ; télémétrie désactivée dans Langfuse, Grafana, Loki, Alloy, Qdrant, vLLM ; services internes sans accès Internet | LLM02 | 14.2.3 | - | - | 5.23, 5.34 | `docker compose config` (réglages `TELEMETRY_ENABLED`, `GF_ANALYTICS_*`, `--disable-reporting`, `QDRANT__TELEMETRY_DISABLED`, `VLLM_NO_USAGE_STATS`) ; `compose/loki/loki.yml` |

### 1.5 Infrastructure et conteneurs

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C26 | **Segmentation réseau** : `factory` interne (sans Internet) ; `egress` seulement pour le bord, la passerelle, oauth2-proxy et les serveurs de modèles ; seul le bord publie des ports, 3001/3002 sur `127.0.0.1` | LLM02 | 13.2.4, 13.2.5 | - | 5.9, 5.14 | 8.20, 8.22 | `docker compose config` (réseaux) ; `docker network inspect ai-factory_factory` ; smoke « API /metrics not exposed through the edge » |
| C27 | **Durcissement des conteneurs** : `no-new-privileges`, `cap_drop: ALL` (exceptions documentées), utilisateurs non root, systèmes de fichiers racine en lecture seule, limites de PID, mémoire et CPU, journaux plafonnés (`docker-compose.yml`, ancre `x-hardening`) | - | 13.2.2 | - | 4.1, 5.4, 5.5, 5.11, 5.12, 5.13, 5.26, 5.29 | 8.9, 8.27 | smoke « no-new-privileges », « capabilities dropped », « read-only root », « non-root », « PID limit on every container » (`docker inspect` de chaque conteneur en marche, exceptions autorisées par nom) |
| C28 | **Isolation du socket Docker** : seul `docker-socket-proxy` le monte (lecture seule), liste blanche GET, client `alloy` uniquement, réseau interne `docker-api` | - | 13.2.2 | - | 5.32 (une exception documentée et filtrée) | 8.2, 8.3 | smoke « Docker socket mounted by docker-socket-proxy only », « Docker API proxy: Alloy can list containers, cannot create one or list images (200 405 403) » |
| C29 | **Contrôles de santé** des services permanents ; politiques de redémarrage | - | - | - | 4.6, 5.27 | 8.6 | smoke « services healthy (profile sim) » |
| C30 | **Gestion des secrets** : aucun dans le dépôt ; `.env` non versionné ; compose refuse de démarrer sans les secrets requis ; gitleaks sur l'arborescence et tout l'historique ; analyseur de secrets Trivy ; mot de passe Valkey dans un fichier tmpfs privé ; jeton Grafana dans un volume en lecture seule | - | 13.3.2, 13.2.3 (les valeurs fictives doivent être remplacées) | PS.1.1 | 4.10 | 5.17, 8.12 | job de CI `secrets` (`scripts/scan_secrets.sh . --git`) ; job `trivy-repo` de `security.yml` |

### 1.6 Développement sécurisé et chaîne d'approvisionnement

| Id | Contrôle (où) | LLM | ASVS | SSDF | CIS | ISO | Vérifier |
|---|---|---|---|---|---|---|---|
| C31 | **CI durcie** : actions épinglées par SHA complet, `permissions: {}` et permissions par job, `persist-credentials: false`, délais, images d'outils épinglées par digest, outils de CI depuis des verrous à empreintes | LLM03 | 15.2.4 | PO.3.2, PO.5.1, PS.1.1 | - | 8.25, 5.21 | job de CI `workflows` (actionlint 1.7.12, zizmor dans [`ci.yml`](../../.github/workflows/ci.yml)) |
| C32 | **SAST** : CodeQL `security-extended` pour Python, JavaScript/TypeScript et GitHub Actions, à chaque push, pull request et chaque semaine | - | - | PW.7.2, PO.4.1 | - | 8.28, 8.29 | [`codeql.yml`](../../.github/workflows/codeql.yml) ; résultats dans l'onglet Security |
| C33 | **Analyse des dépendances et des images** : pip-audit sur chaque fichier de dépendances épinglé (épinglage vérifié) ; Trivy sur le dépôt et sur chaque image construite ici, chaque jour ; barrière sur HIGH/CRITICAL corrigeables ; risques acceptés avec justification et expiration | LLM03 | 15.1.1, 15.2.1 | PW.4.4, RV.1.1, RV.1.2, RV.2.2 | 4.4 | 8.8, 5.21 | jobs `pip-audit`, `trivy-repo`, `images` de [`security.yml`](../../.github/workflows/security.yml) ; [`.github/trivy/trivyignore.yaml`](../../.github/trivy/trivyignore.yaml) |
| C34 | **SBOM** : CycloneDX et SPDX du code source et de chaque image (syft épinglé par digest) | LLM03 | 15.1.2 | PS.3.2 | - | 5.9, 5.21 | `scripts/sbom.sh --images` ; jobs `sbom-source`, `images` de `security.yml` (artefacts) |
| C35 | **Releases signées et attestées** : barrière Trivy avant publication, signature cosign sans clé, provenance de build SLSA, attestations SPDX et CycloneDX, `SHA256SUMS` signé, auto-vérification | LLM03 | 15.2.4 | PS.2.1, PS.3.1, PS.3.2 | 4.5, 4.12 (pour les exploitants qui vérifient) | 5.21, 8.32 | [`release.yml`](../../.github/workflows/release.yml) ; `.github/scripts/verify_image.sh IMAGE@DIGEST` ; commandes dans [`../../SECURITY.md`](../../SECURITY.md#verify-a-release) |
| C36 | **Mises à jour des dépendances** : Dependabot chaque semaine avec un délai de 7 jours (30 jours pour les versions majeures Python), mises à jour de sécurité groupées ; épinglage exact `==` | LLM03 | 15.2.1 | PW.4.1, RV.2.2 | 4.4 | 8.8 | [`dependabot.yml`](../../.github/dependabot.yml) ; étape « Every requirement is pinned with == » de `security.yml` |
| C37 | **Politique de licences** : uniquement des licences permissives dans le code livré ; services AGPL séparés et non modifiés ; inventaire avec contrôle de dérive des versions | LLM03 | 15.1.4 | PW.4.4 | - | 5.32 | job de CI `licenses` (`python scripts/check_licenses.py`, `licenses/third_party.yaml`) |
| C38 | **Tests de sécurité en CI** : 482 tests d'API dont 238 tests de sécurité ; validation de la configuration de chaque service ; contrôle des workflows | - | - | PW.8.2, PO.4.1 | - | 8.29 | jobs de CI `api-tests`, `compose`, `workflows` ; `scripts/smoke_stack.sh` (déclenchement manuel) |
| C39 | **Modèle de menaces et décisions de sécurité documentées** | - | 15.1.3, 15.1.5 | PW.1.1, PO.1.1 | - | 5.8, 8.27 | [THREAT_MODEL.md](THREAT_MODEL.md), [SECURITY.md](SECURITY.md) |

## 2. Titres du CIS Docker Benchmark

Numéros de la v1.6.0 ; rapprochez par le titre (en anglais, tel que publié par le CIS) dans
l'édition que vous utilisez.

| Numéro | Recommandation | État |
|---|---|---|
| 4.1 | Ensure that a user for the container has been created | profils par défaut et `sim` : oui (exceptions en C27) ; `vps` et `local-gpu` : utilisateur par défaut de l'image |
| 4.2 | Ensure that containers use only trusted base images | images officielles, épinglées par version, licences vérifiées |
| 4.4 | Ensure images are scanned and rebuilt to include security patches | Trivy chaque jour sur les images construites ici ; tableau d'analyse des images tierces dans [compose/README.fr.md](../../compose/README.fr.md) |
| 4.5 | Ensure Content trust for Docker is Enabled | pas Docker Content Trust : les images de ce dépôt sont signées avec cosign à la place (C35) |
| 4.6 | Ensure that HEALTHCHECK instructions have been added to container images | contrôles de santé définis dans `docker-compose.yml` ; une exception acceptée (`grafana-bootstrap`, ponctuel) |
| 4.10 | Ensure secrets are not stored in Dockerfiles | oui (gitleaks et Trivy) |
| 4.12 | Ensure all signed artifacts are validated | `verify_image.sh` pour les images publiées (étape de l'exploitant) |
| 5.4 | Ensure that Linux kernel capabilities are restricted within containers | `cap_drop: ALL` partout ; `edge` ajoute `NET_BIND_SERVICE`, `volume-init` ajoute `CHOWN`, `DAC_READ_SEARCH` ; `dcgm-exporter` garde son jeu par défaut |
| 5.5 | Ensure that privileged containers are not used | aucun |
| 5.9 | Ensure that only needed ports are open on the container | seul `edge` publie des ports |
| 5.11 | Ensure that the memory usage for containers is limited | oui |
| 5.12 | Ensure that CPU priority is set appropriately on containers | limites de CPU posées |
| 5.13 | Ensure that the container's root filesystem is mounted as read only | profils par défaut et `sim` : oui ; `vps` et `local-gpu` : non |
| 5.14 | Ensure that incoming container traffic is bound to a specific host interface | ports alternatifs 3001/3002 sur `127.0.0.1` ; 80/443 publics par conception |
| 5.26 | Ensure that the container is restricted from acquiring additional privileges | `no-new-privileges` partout |
| 5.27 | Ensure that container health is checked at runtime | oui (smoke « services healthy ») |
| 5.29 | Ensure that the PIDs cgroup limit is used | oui |
| 5.32 | Ensure that the Docker socket is not mounted inside any containers | une exception : `docker-socket-proxy`, en lecture seule, filtré (C28) |

Les sections hôte et démon (1 à 3) et Docker Swarm (7) relèvent de l'exploitant.

## 3. Écarts connus et feuille de route

Ce qu'un évaluateur ne trouvera pas aujourd'hui, dit clairement pour pouvoir le planifier :

| Domaine | Écart | Référence | État |
|---|---|---|---|
| Posture par défaut | la configuration par défaut est la démo sans clé (aucune authentification, toute requête agit comme `admin`), signalée par `security.status: demo` | SSDF PW.9.2 | voulu pour la démo ; réglages de production dans le [guide de durcissement](SECURITY.md#14-guide-de-durcissement-pour-la-production) |
| Maîtrise des coûts | la dépense est mesurée et alertée, pas plafonnée par application à la passerelle | LLM10 | **Feuille de route** : clés de passerelle par application avec budgets et limites de débit |
| Secrets | secrets passés en variables d'environnement (lisibles via `docker inspect`) ; pas de gestionnaire de secrets intégré | ASVS 13.3.1, 13.3.4 | **Feuille de route** : fichiers secrets Docker ; exploitant : gestionnaire de secrets et rotation ([guide](SECURITY.md#14-guide-de-durcissement-pour-la-production)) |
| Transport interne | HTTP en clair entre conteneurs sur les réseaux internes | ASVS 12.3.1, 12.3.3, 12.3.5 | **Feuille de route** pour les déploiements multi-hôtes (TLS mutuel) |
| Authentification des bases | Prometheus, Loki et Qdrant acceptent des requêtes non authentifiées à l'intérieur du réseau interne | ASVS 13.2.1 ; ISO 8.3 | ouvert : hôte mono-locataire supposé |
| Chaîne d'audit | non clé-dépendante ; pas de rotation | ASVS 16.4.2 | **Feuille de route** : HMAC ou ancrage, rotation ; exploitant : sauvegardes WORM |
| CSP du dashboard | scripts `'unsafe-inline'` (page monofichier) | ASVS 3.4.3 | **Feuille de route** : CSP à nonce ou empreinte |
| Épinglage | dépendances Python d'exécution sans verrou à empreintes ; images de base et tierces épinglées par tag, pas par digest | SSDF PW.4.4 ; ASVS 15.2.4 | **Feuille de route** |
| Artefacts de modèles | poids des modèles non épinglés par révision ni empreinte | LLM03 | **Feuille de route** |
| MFA | délégué au fournisseur d'identité | ASVS 6.3.3 ; NIS2 art. 21(2)(j) | exploitant : imposer le MFA dans le fournisseur d'identité |
| Couverture de la CI | tests unitaires des garde-fous, test e2e SSO et smoke test complet non lancés à chaque changement | SSDF PW.8.2 | **Feuille de route** : les ajouter à la CI |
| Profils GPU | les services `vps` et `local-gpu` tournent en root sans système de fichiers racine en lecture seule ; non testés en exécution ici | CIS 4.1, 5.13 | exploitant : tester sur l'hôte cible |

## 4. Repères NIS2 et DORA

À titre indicatif : ces textes s'appliquent aux organisations, pas aux logiciels. Le tableau
montre quels contrôles et documents peuvent appuyer les mesures propres à une entité.

### NIS2 : directive (UE) 2022/2555

| Article | Mesure | Contrôles et documents à l'appui |
|---|---|---|
| 21(2)(a) | analyse des risques et politiques de sécurité des systèmes d'information | [THREAT_MODEL.md](THREAT_MODEL.md), C39 |
| 21(2)(b) | gestion des incidents | C06, C07, C09 ; [SECURITY.md, section 15](SECURITY.md#15-bases-de-la-réponse-à-incident) |
| 21(2)(c) | continuité d'activité, gestion des sauvegardes, reprise après sinistre | route sensible en échec fermé (C18), replis pour C1/C2 ; sauvegardes : [SECURITY.md, section 14, étape 7](SECURITY.md#14-guide-de-durcissement-pour-la-production) (exploitant) |
| 21(2)(d) | sécurité de la chaîne d'approvisionnement | C31 à C37 |
| 21(2)(e) | sécurité de l'acquisition, du développement et de la maintenance, y compris le traitement et la divulgation des vulnérabilités | C17, C32, C33, C36, C38 |
| 21(2)(f) | évaluation de l'efficacité des mesures | C38, OpenSSF Scorecard ([`scorecard.yml`](../../.github/workflows/scorecard.yml)), C08 |
| 21(2)(g) | cyberhygiène de base et formation | C27, C30 ; modules de maîtrise de l'IA (`config/skills.yaml`, [SKILLS_RND.md](SKILLS_RND.md)) |
| 21(2)(h) | cryptographie et chiffrement | C16, C06 (chaîne SHA-256), C35 (signatures Sigstore) |
| 21(2)(i) | sécurité des ressources humaines, contrôle d'accès, gestion des actifs | C01 à C05, C34 (inventaire) |
| 21(2)(j) | authentification multifacteur ou continue | C01 (MFA imposé par le fournisseur d'identité) |
| 23 | obligations de notification (alerte précoce sous 24 h, notification sous 72 h, rapport final sous un mois) | [SECURITY.md, section 15](SECURITY.md#15-bases-de-la-réponse-à-incident), preuves C06 |

Pour les fournisseurs d'infrastructures et de services numériques, voir aussi le règlement
d'exécution (UE) 2024/2690 de la Commission (exigences techniques et méthodologiques).

### DORA : règlement (UE) 2022/2554 (entités financières, applicable depuis le 17 janvier 2025)

| Article | Thème | Contrôles et documents à l'appui |
|---|---|---|
| 6 | cadre de gestion du risque lié aux TIC | [SECURITY.md](SECURITY.md), [THREAT_MODEL.md](THREAT_MODEL.md), cette matrice |
| 8 | identification (actifs, dépendances) | actifs du modèle de menaces (section 3), C34 (SBOM), `licenses/third_party.yaml` |
| 9 | protection et prévention | C01 à C05, C12, C16, C18, C26 à C30 |
| 10 | détection | C07, C08, C09, C20 |
| 11, 12 | réponse et rétablissement ; sauvegarde et restauration | [SECURITY.md, sections 14 et 15](SECURITY.md#15-bases-de-la-réponse-à-incident) (procédures de l'exploitant) |
| 17, 19 | gestion des incidents liés aux TIC et notification des incidents majeurs | C06, C07 ; délais dans le règlement délégué (UE) 2025/301 de la Commission |
| 24, 25 | tests de résilience opérationnelle numérique | C38, C32, C33 |
| 28 | risque lié aux prestataires tiers de services TIC | C31 à C37 ; les fournisseurs LLM ne reçoivent que du C1/C2 (C18) |

Exigences détaillées de gestion du risque lié aux TIC : règlement délégué (UE) 2024/1774 de la
Commission.

Pour les acheteurs américains : la colonne SSDF ci-dessus fonde le formulaire d'attestation de
développement logiciel sécurisé de la CISA (Secure Software Development Attestation Form) utilisé
par les agences fédérales américaines.
