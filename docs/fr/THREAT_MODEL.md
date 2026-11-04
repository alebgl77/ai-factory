# Modèle de menaces

*[English version](../THREAT_MODEL.md)*

Ce modèle de menaces couvre la stack Virtual AI Factory telle que définie dans ce dépôt
(`docker-compose.yml`, `compose/`, `api/`, `dashboard/`, `.github/`). Il applique STRIDE par
composant et l'OWASP Top 10 for LLM Applications 2025 pour les menaces propres aux LLM. Chaque
menace cite l'atténuation **en place** et comment la vérifier, puis le **risque résiduel**. Les
éléments conçus mais pas encore livrés sont marqués **Feuille de route**.

Le risque résiduel est évalué pour la posture de production du
[guide de durcissement](SECURITY.md#14-guide-de-durcissement-pour-la-production)
(`FACTORY_AUTH=sso`, `FACTORY_AUTH_MODE=proxy`, vrais secrets, TLS). Dans la posture de démo sans
clé, les contrôles d'identité, de RBAC et des quatre yeux sont désactivés à dessein, et l'API le
dit (`security.status: demo`).

Revu le : 2026-11-04. À revoir pour chaque nouveau service, flux de données, fournisseur externe
ou release qui touche à la sécurité, et après tout incident.

Sommaire : [1. Périmètre et hypothèses](#1-périmètre-et-hypothèses) ·
[2. Diagramme de flux de données](#2-diagramme-de-flux-de-données) ·
[3. Actifs et acteurs](#3-actifs-et-acteurs) · [4. STRIDE par composant](#4-stride-par-composant) ·
[5. Menaces propres aux LLM](#5-menaces-propres-aux-llm) ·
[6. Registre des risques résiduels](#6-registre-des-risques-résiduels)

## 1. Périmètre et hypothèses

Dans le périmètre : le navigateur et le dashboard, le proxy de bord (Caddy, et oauth2-proxy dans le
profil `sso`), l'API d'agrégation, la passerelle LLM (LiteLLM et le callback de l'usine), les
fournisseurs LLM externes vus depuis la passerelle, les bases d'observabilité et de données, l'hôte
GPU, l'environnement d'exécution des conteneurs et la chaîne d'approvisionnement qui produit les
images.

Hypothèses (si l'une ne tient pas, réévaluez) :

- **A1** L'hôte Docker et ses administrateurs sont de confiance. Root sur l'hôte peut lire chaque
  volume et chaque secret ; le durcissement de l'hôte incombe à l'exploitant.
- **A2** Le fournisseur d'identité est de confiance et impose l'authentification multifacteur.
- **A3** Les fournisseurs LLM sont liés par contrat mais traités comme non fiables techniquement :
  ils ne reçoivent que des données C1/C2, après masquage des données personnelles.
- **A4** Les applications qui appellent la passerelle sont internes, détiennent la clé de la
  passerelle et étiquettent correctement la classe de données de leurs requêtes
  (`metadata.sensitivity`).
- **A5** L'exploitant applique le guide de durcissement pour la production.

Hors périmètre : la sécurité du système de l'hôte, du fournisseur d'identité, de l'infrastructure
des fournisseurs, des applications métier qui appellent la passerelle, et la sécurité physique.

## 2. Diagramme de flux de données

```text
                           TB1 Internet │ Hôte Docker
  [Navigateur] ──F1 HTTPS────────────────┼──► (edge : Caddy) ──F4──► (dashboard : statique)
                                         │       │  │  └──F5──► (Grafana) (Langfuse web)   connexion propre
  [Fournisseur d'identité] ◄─F2 OIDC─────┼── (oauth2-proxy) ◄─┘ forward_auth (profil sso)
                                         │       │
                                         │       └──F3 en-têtes d'identité, depuis l'adresse fixe
                                         │          du bord uniquement (réseau edge-identity) ── TB2
                                         │                   ▼
                                         │             (API d'agrégation) ──F7──► [api-state :
                                         │               │    │                    registre,
                                         │               │    │                    audit.jsonl]
                                         │   F6 côté     │    └──F6──► (Grafana, jeton Viewer :
                                         │   serveur     ▼             GET en liste blanche, requêtes nommées)
                                         │        (Prometheus) (Langfuse) (Qdrant) (LiteLLM)
                                         │
  [Applications, agents internes] ──F8 clé┼──► (passerelle LiteLLM + callback de l'usine)
                                         │      │ redirection C3/C4, garde-fous, masquage PII
  [Fournisseurs LLM : cloud, VPS] ◄──F9──┼──────┤ C1/C2 seulement (egress) ──────────────── TB3
                                         │      └──F10 C3/C4 ──► [hôte GPU : vLLM] ──────── TB4
                                         │      └──F11──► (traces Langfuse, masquées pour C3/C4)
                                         │                (métriques Prometheus :9464)
                                         │  (Alloy) ──F12──► (docker-socket-proxy) ──► [socket
                                         │     └──► (Loki)        liste blanche GET        Docker] TB5
  [GitHub Actions] ──F13 images signées, SBOM, provenance──► [GHCR] ──► l'exploitant vérifie ── TB6
```

Flux :

| Flux | De → vers | Contenu | Protection |
|---|---|---|---|
| F1 | navigateur → bord | pages, `/api/*`, cookie de session (sso) | TLS (`FACTORY_SCHEME=https`), HSTS, CSP, limites de corps et d'en-têtes |
| F2 | oauth2-proxy ↔ fournisseur d'identité | code d'autorisation OIDC | TLS, PKCE `S256`, réseau `egress` |
| F3 | bord → API | requêtes avec `X-Forwarded-User/-Email/-Groups` | réseau interne `edge-identity`, adresse fixe du bord, l'API ne fait confiance qu'à ce `/32`, copies du client supprimées |
| F4, F5 | bord → dashboard, Grafana, Langfuse | page statique ; interfaces avec leur propre connexion | réseau interne ; en-têtes d'identité supprimés |
| F6 | API → sources de données | requêtes en lecture | secrets côté serveur ; liste blanche GET et requêtes nommées pour Grafana |
| F7 | API → volume d'état | registre, réservations, placement, audit | verrou de fichier, audit chaîné, annulation si l'audit échoue |
| F8 | applications → passerelle | prompts avec `metadata.usecase` et `sensitivity` | clé de la passerelle, réseau interne uniquement, non routée par le bord |
| F9 | passerelle → cloud ou VPS | prompts C1/C2 | TLS vers le fournisseur, masquage PII, garde-fous, réseau `egress` |
| F10 | passerelle → hôte GPU | prompts C3/C4, replis | GPU local uniquement, sans repli ; clé d'API vLLM ; WireGuard si distant |
| F11 | passerelle → Langfuse, Prometheus | traces, métriques | C3/C4 masqués et exclus de la journalisation ; les métriques ne contiennent que des comptes |
| F12 | Alloy → proxy du socket → Docker ; Alloy → Loki | liste et journaux des conteneurs | réseau interne `docker-api`, liste blanche GET, client `alloy` uniquement |
| F13 | CI → registre → exploitant | images, SBOM, attestations | actions épinglées par SHA, barrière Trivy avant publication, cosign sans clé, provenance SLSA |

Frontières de confiance : **TB1** Internet vers le bord ; **TB2** bord vers les services internes
(là où l'identité est affirmée) ; **TB3** passerelle vers les fournisseurs LLM externes ; **TB4**
passerelle vers l'hôte GPU (éventuellement une autre machine) ; **TB5** conteneurs vers l'hôte
Docker ; **TB6** CI/CD et registre vers le déploiement.

## 3. Actifs et acteurs

### 3.1 Actifs

| Id | Actif | Où | Propriété en jeu |
|---|---|---|---|
| AS1 | Prompts et réponses C3/C4 | passerelle (en transit), hôte GPU | confidentialité |
| AS2 | Prompts et réponses C1/C2 (peuvent contenir des données personnelles) | passerelle, fournisseurs, Langfuse | confidentialité |
| AS3 | Registres de gouvernance : versions de modèles, évaluations des risques, décisions de placement, réservations | volume `api-state`, `config/` (Git) | intégrité |
| AS4 | Piste d'audit | `audit.jsonl`, copies Loki, empreinte de tête publiée | intégrité, disponibilité |
| AS5 | Secrets : mots de passe des bases, clé maître de la passerelle, clés des fournisseurs, secrets client et cookie du SSO, jeton Grafana Viewer | `.env`, environnement des conteneurs, volumes | confidentialité |
| AS6 | Traces et journaux | Langfuse (Postgres, ClickHouse, SeaweedFS), Loki | confidentialité, intégrité |
| AS7 | Corpus et embeddings | Qdrant | confidentialité, intégrité |
| AS8 | Métriques et données de coût | Prometheus | intégrité |
| AS9 | Images publiées, SBOM, attestations | GHCR, releases GitHub | intégrité |
| AS10 | Budget LLM | comptes chez les fournisseurs | financière (déni de portefeuille) |
| AS11 | Disponibilité de la route sensible | GPU local | disponibilité |

### 3.2 Acteurs (sources de menace)

| Id | Acteur | Objectif typique |
|---|---|---|
| T1 | Attaquant anonyme sur Internet | atteindre les services internes, usurper un utilisateur, épuiser les ressources |
| T2 | Salarié authentifié au-delà de son rôle | lire ou modifier ce que son rôle n'autorise pas |
| T3 | Initié qui contourne la gouvernance | approuver sa propre version de modèle, altérer les registres, effacer ses traces |
| T4 | Application interne compromise ou défaillante | exfiltrer des données via la passerelle, mal étiqueter les données, faire exploser les coûts |
| T5 | Auteur de contenu ingéré par le RAG ou des outils | injection de prompt indirecte |
| T6 | Fournisseur ou modèle LLM compromis ou malveillant | collecter les prompts, renvoyer des réponses nuisibles |
| T7 | Dépendance, image ou GitHub Action compromise en amont | exécution de code en CI ou à l'exécution |
| T8 | Attaquant qui exécute du code dans un conteneur | mouvement latéral, secrets, évasion de conteneur |
| T9 | Administrateur de l'hôte | de confiance (A1), hors périmètre |

## 4. STRIDE par composant

Colonnes : **Menace**, **Atténuation en place** (avec la vérification), **Risque résiduel**
(Faible / Moyen / Élevé). « Smoke » désigne un contrôle de `scripts/smoke_stack.sh` sur la stack
en marche ; les noms de tests renvoient à [`api/tests/test_security.py`](../../api/tests/test_security.py)
sauf mention contraire.

### 4.1 Navigateur et dashboard

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Vol de session ou page d'hameçonnage qui encadre le dashboard | cookie de session `__Host-`, `Secure`, `HttpOnly`, `SameSite=Lax` (sso) ; HSTS en HTTPS ; `frame-ancestors 'none'`, `X-Frame-Options: DENY`. Smoke : « security headers », « edge headers » | Faible ; l'hameçonnage d'identifiants chez le fournisseur d'identité est couvert par son MFA (A2) |
| T | Injection de script via des données affichées par le dashboard | les valeurs sont échappées en HTML avant insertion ; CSP sans origine externe (Three.js embarqué) ; CodeQL `javascript-typescript` en `security-extended` à chaque changement | **Moyen** : la page monofichier exige des scripts `'unsafe-inline'`, donc un échappement oublié s'exécuterait ; le contenu vient de `config/` (GitOps) et de l'API |
| R | Un utilisateur nie un changement fait depuis le dashboard | chaque écriture est dans la piste d'audit avec l'acteur et l'identifiant de requête | Faible |
| I | Jetons ou données exposés dans le navigateur | aucun jeton dans le navigateur ; `Cache-Control: no-store` sur `/api/` ; `Referrer-Policy`. Smoke : « Grafana token absent from every browser-facing response » | Faible |
| D | Sans objet (rendu côté client uniquement) | - | Faible |
| E | Le navigateur atteint directement les services internes | API en même origine uniquement, CORS fermé, services internes non routés | Faible |

### 4.2 Proxy de bord (Caddy, oauth2-proxy)

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Un client forge `X-Forwarded-User/-Email/-Groups` pour devenir `admin` | les en-têtes d'identité envoyés par les clients sont supprimés dans tous les modes ; `forward_auth copy_headers` supprime d'abord les copies du client ; l'API ne fait confiance qu'à l'adresse fixe du bord. `compose/sso/test_sso_e2e.sh` (« forged headers never reach the API »), `test_proxy_mode_untrusted_source_is_rejected_even_with_identity_headers` | Faible. **Élevé** si un exploitant élargit `FACTORY_TRUSTED_PROXIES` au-delà de l'adresse du bord |
| S | Un autre conteneur du réseau interne affirme une identité | réseau `edge-identity` : le bord a une adresse fixe hors de l'`ip_range` des autres membres ; toutes les capacités sont retirées, aucun conteneur ne peut changer son adresse | Faible |
| T | Rétrogradation TLS ou interception | certificats automatiques (ACME), HSTS avec `FACTORY_SCHEME=https` | Faible ; première visite avant HSTS (pas de liste de préchargement) |
| R | Requêtes impossibles à tracer | journaux d'accès JSON sur stdout, collectés dans Loki ; identifiant de requête transmis à l'API | Faible ; conservation Loki de 7 jours par défaut |
| I | Les bannières de version aident un attaquant | `Server`, `Via`, `X-Powered-By` supprimés | Faible |
| D | Clients lents, corps ou en-têtes surdimensionnés | délais d'en-tête, de corps et d'inactivité, limites de corps (1 Mo / 10 Mo / 20 Mo), en-têtes plafonnés à 64 Ko. Smoke : « edge request body limit (413 above 1 MB) » | **Moyen** : pas de limitation de débit par IP au bord ; les attaques volumétriques demandent un WAF ou un CDN en amont |
| E | Pages protégées accessibles sans session (erreur de configuration SSO) | tous les chemins sauf `/healthz`, `/.well-known/security.txt` et `/oauth2/*` passent par `forward_auth` ; oauth2-proxy refuse de démarrer avec une configuration invalide et le bord répond 502 (échec fermé) ; `caddy validate` en CI (job `compose`) | Faible |

### 4.3 API d'agrégation

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Appel direct à l'API qui contourne le bord | 401 `untrusted_source` pour tout pair hors de `FACTORY_TRUSTED_PROXIES`, avant lecture du corps ; chemins inconnus refusés par défaut. `test_proxy_mode_unknown_api_path_is_denied_by_default` | Faible |
| T | Registres de gouvernance modifiés hors de l'API (édition du volume) | le rapprochement de l'audit signale un `drift` ; `factory_audit_chain_ok`. `test_state_changed_outside_the_api_is_reported_as_drift` | **Moyen** : détecté, pas empêché (écrire dans le volume suppose l'accès à l'hôte, A1) |
| T | Des écritures concurrentes corrompent l'état | verrou de fichier ; contrôle de version optimiste (409) pour les réservations. `test_concurrent_writers_keep_one_valid_chain` | Faible |
| R | Un approbateur nie une approbation | enregistrement chaîné avec acteur, rôle, identifiant de requête, IP du client ; copies dans Loki ; empreinte de tête publiée. `test_tampering_is_detected` | **Moyen** : la chaîne n'est pas clé-dépendante ; HMAC ou ancrage WORM en **Feuille de route** |
| I | Secrets dans les réponses de l'API | réponses de LiteLLM et Grafana reconstruites champ par champ ; la posture ne contient que des codes et des nombres. `test_no_secret_in_any_response_with_auth_on` | Faible |
| I | Requêtes arbitraires via le proxy Grafana | GET uniquement, liste blanche de chemins, requêtes PromQL nommées uniquement | Faible |
| I | E-mail de l'évaluateur (`reviewed_by`) visible par chaque viewer | aucune (choix de minimisation des données à l'étude) | Faible à Moyen selon votre politique de confidentialité |
| D | Inondation d'écritures, corps volumineux | seau à jetons par acteur (429, `Retry-After`), limite de corps à 64 Kio, 401 avant lecture des corps non authentifiés. `test_writes_are_rate_limited_per_actor_with_retry_after` | **Moyen** : en mémoire, par processus (plusieurs réplicas demandent une limitation au bord) ; les lectures ne sont pas limitées |
| E | Route sans autorisation, élévation de rôle | refus par défaut ; chaque route déclare une permission (`test_every_api_route_declares_exactly_one_permission`) ; configuration qui échoue fermée (`test_misconfiguration_fails_closed`) ; matrice rôle × point d'accès | Faible |
| E | Un initié approuve sa propre version de modèle (T3) | règle des quatre yeux sous le verrou d'état ; refus audité. `test_four_eyes_refuses_self_approval_and_records_the_reviewer` | Faible pour les versions enregistrées via l'API ; **Moyen** pour les versions définies dans Git (repose sur la revue Git et la protection de branche) |
| E | Production laissée en mode démo (`FACTORY_AUTH_MODE=none`) | posture `status: demo`, avertissement `auth_disabled` dans `/api/v1/health` et `meta.security` | **Moyen** : erreur d'exploitation ; alertez quand `security.status` n'est pas `enforced` |

### 4.4 Passerelle LLM (LiteLLM et le callback de l'usine)

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Un appelant non autorisé utilise la passerelle | clé maître exigée ; passerelle sur le réseau interne uniquement, non routée par le bord | **Moyen** : une seule clé partagée par toutes les applications internes ; des clés par application avec budgets relèvent de la **Feuille de route** |
| T | Requête façonnée pour contourner le routage sensible (autre modèle, API Responses ou Messages) | le callback redirige vers `chat-sensitive` tout type d'appel génératif en C3/C4. `test_responses_and_messages_api_are_rerouted_too` ; smoke « C4 request on chat-default rerouted to the local GPU » | Faible |
| T | Classe de données mal étiquetée (C3 envoyé en C1) | le masquage PII s'applique quand même avant les alias cloud | **Moyen** : dépend de A4 ; classez à la source |
| R | Qui a envoyé quelle requête | métriques par use case et agent, traces Langfuse, journaux de dépense sans prompt | **Moyen** : attribution par use case, pas par utilisateur final |
| I | Prompts qui fuient dans les journaux ou messages d'erreur | `redact_messages_in_exceptions`, `store_prompts_in_spend_logs: false`, `turn_off_message_logging` pour C3/C4. Smoke « gateway logs contain no prompt text » | Faible |
| I | Données personnelles envoyées à un fournisseur cloud | masquage des e-mails, téléphones, IBAN, cartes, NIR avant les alias cloud et VPS. Smoke « PII redacted before the cloud target » | **Moyen** : fondé sur des motifs ; noms, adresses et identifiants en texte libre ne sont pas détectés |
| D | Panne d'un fournisseur | replis cloud → VPS → GPU local pour C1/C2 ; délais, une seule nouvelle tentative, mises en pause | Faible ; C3/C4 s'arrêtent à dessein quand le GPU est indisponible (voir 4.7) |
| E | Routage sensible désactivé par configuration | `FACTORY_ENFORCE_SENSITIVE_ROUTING=true` par défaut ; le mode des garde-fous échoue fermé en `block` sur une valeur inconnue (`test_unknown_mode_fails_closed`) | Faible |

### 4.5 Fournisseurs LLM (cloud, VPS)

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Trafic envoyé à un point d'accès usurpé | URL des fournisseurs réglées par l'exploitant dans `.env` ; validation des certificats TLS par la passerelle | Faible |
| T | Réponse d'un modèle compromis ou manipulé | les réponses ne sont pas de confiance (section 5.5) ; barrières d'évaluation et seuils de retour arrière du pipeline LLMOps ([LLMOPS.md](LLMOPS.md)) | **Moyen** |
| R | Traces côté fournisseur | hors de la stack | contractuel |
| I | Le fournisseur conserve les prompts ou s'en sert pour entraîner | uniquement C1/C2 après masquage PII ; jamais C3/C4 | **Moyen** : choisissez des fournisseurs avec des clauses de non-conservation et de non-entraînement (contractuel, hors de la stack) |
| D | Panne ou quota du fournisseur | replis, mises en pause (`allowed_fails`, `cooldown_time`), délai de 120 s | Faible |
| E | Le fournisseur pénètre dans l'usine | trafic sortant uniquement, depuis le réseau `egress` ; aucune route entrante | Faible |

### 4.6 Bases d'observabilité et de données

Prometheus, Loki, Grafana, Langfuse (avec Postgres, ClickHouse, SeaweedFS, Valkey) et Qdrant.

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | Accès depuis Internet | non exposées ; seuls Grafana et Langfuse sont routés, avec leur propre connexion ; accès anonyme Grafana désactivé, inscription désactivée ; inscription Langfuse désactivée. Smoke « anonymous access refused (401) » | Faible |
| S | Mouvement latéral depuis un conteneur compromis (T8) | réseau interne ; mots de passe pour Postgres, ClickHouse, Valkey et S3 | **Moyen** : Prometheus, Loki et Qdrant acceptent des requêtes non authentifiées de tout conteneur du réseau `factory` |
| T | Journaux falsifiés ou supprimés | Loki sur son propre volume ; enregistrements d'audit aussi dans `audit.jsonl` et empreinte de tête publiée | **Moyen** : Loki n'a pas d'authentification à l'intérieur du réseau |
| I | Données sensibles dans les traces | C3/C4 masqués dans Langfuse ; Langfuse auto-hébergé. Smoke « sensitive (C3) traces masked in Langfuse » | **Moyen** : les prompts C1/C2 sont conservés dans Langfuse pour l'observabilité ; restreignez l'accès et réglez la conservation |
| I | Trafic interne en clair et données au repos | réseaux Docker internes sur un seul hôte | **Moyen** pour les déploiements multi-hôtes (TLS mutuel en **Feuille de route**) ; chiffrez les disques de l'hôte |
| D | Saturation du disque | journaux des conteneurs plafonnés (10 Mo × 3), conservation Loki 7 jours et Prometheus 30 jours, limites de mémoire et de PID | Faible |
| E | Un administrateur Grafana installe un plugin malveillant | aucun plugin provisionné, préinstallation désactivée ; mot de passe administrateur dans `.env` | Faible avec le SSO et le compte administrateur local désactivé |

### 4.7 Hôte GPU (vLLM, dcgm-exporter ; local ou distant)

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| S | C3/C4 envoyés à un faux point d'accès GPU | `LOCAL_API_BASE` réglé par l'exploitant ; vLLM exige une clé d'API (`--api-key`) ; WireGuard recommandé pour un GPU distant | **Moyen** : la clé vLLM passe sur la ligne de commande (visible par `docker inspect`) ; le tunnel est à la charge de l'exploitant |
| T | Poids de modèle altérés | téléchargements depuis le hub de modèles vers un volume de cache | **Moyen** : épinglage des révisions de modèles et vérification des empreintes en **Feuille de route** |
| I | Données C3/C4 au repos sur l'hôte GPU | statistiques d'usage vLLM désactivées ; les données restent sur l'hôte | **Moyen** : chiffrez le disque ; restreignez l'accès à l'hôte |
| D | GPU indisponible | charges C3/C4 mises en file ou refusées (échec fermé) ; alerte `FactorySensitiveRouteDown`. Smoke « local GPU down: chat-sensitive fails closed » | accepté : la disponibilité est sacrifiée à la confidentialité |
| E | Évasion depuis les conteneurs GPU | `no-new-privileges`, toutes les capacités retirées (vLLM, Ollama), limites de PID, `/dev/shm` dimensionné au lieu de `ipc: host` | **Moyen** : exécution en root sans système de fichiers racine en lecture seule ; dcgm-exporter garde ses capacités par défaut ; non testé en exécution ici (pas de GPU) |

### 4.8 Environnement d'exécution des conteneurs et hôte Docker

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| E | Élévation de privilèges ou évasion d'un conteneur | `no-new-privileges`, `cap_drop: ALL`, utilisateurs non root, systèmes de fichiers racine en lecture seule, limites de PID, mémoire et CPU, aucun conteneur privilégié. Smoke : cinq contrôles `docker inspect` | Faible à Moyen (vulnérabilités du noyau ; tenez l'hôte à jour) |
| E | Abus du socket Docker | seul `docker-socket-proxy` le monte, en lecture seule ; liste blanche GET ; client `alloy` uniquement. Smoke « Docker API proxy: ... (200 405 403) » | Faible |
| I | Secrets lus dans l'environnement des conteneurs | aucune au-delà de la liste blanche du proxy | **Moyen** : l'inspection renvoie les variables d'environnement, donc Alloy (et quiconque a le socket) peut les lire ; fichiers secrets en **Feuille de route** |
| D | Bombes de processus, épuisement de la mémoire | limites de PID, mémoire et CPU sur chaque service | Faible |

### 4.9 Chaîne d'approvisionnement (CI/CD, images, dépendances)

| | Menace | Atténuation en place | Risque résiduel |
|---|---|---|---|
| T | GitHub Action compromise (tag redirigé) | chaque action épinglée par SHA complet ; Dependabot avec un délai de 7 jours ; zizmor détecte les épinglages manquants et les commits usurpés | Faible |
| T | Paquet Python malveillant ou vulnérable | épinglage exact `==` vérifié en CI ; pip-audit sur chaque fichier de dépendances ; outils de CI épinglés par empreinte | **Moyen** : les dépendances d'exécution et les images de base ne sont pas encore épinglées par empreinte ou digest (OpenSSF Scorecard *Pinned-Dependencies*) |
| T | Image publiée altérée | barrière Trivy avant publication ; signature cosign sans clé ; attestations de provenance SLSA et de SBOM ; `.github/scripts/verify_image.sh` | Faible pour les exploitants qui vérifient par digest |
| T | Image tierce re-taguée en amont | images épinglées par version et listées dans `licenses/third_party.yaml` | **Moyen** : tags et non digests ; épinglage par digest en **Feuille de route** |
| I | Secret versionné dans le dépôt | gitleaks sur tout l'historique, analyseur de secrets Trivy | Faible |
| E | Injection dans un workflow ou abus de jeton | `permissions: {}` par défaut, permissions par job, `persist-credentials: false`, actionlint et zizmor | Faible |

## 5. Menaces propres aux LLM

Rapprochées de l'[OWASP Top 10 for LLM Applications 2025](https://github.com/OWASP/www-project-top-10-for-large-language-model-applications).

### 5.1 Injection de prompt (LLM01:2025)

- **Menace** : injection directe par un utilisateur, ou indirecte via des documents récupérés ou
  des sorties d'outils (T5), pour contourner les instructions, faire fuiter le prompt système ou
  déclencher des actions non voulues.
- **En place** : détection par règles en anglais et en français sur le contenu utilisateur, les
  sorties d'outils et les documents récupérés (contournement d'instructions, exfiltration du
  prompt système, jailbreaks par jeu de rôle, délimiteurs de gabarits de chat, charges encodées,
  caractères Unicode cachés, remplissage au-delà du budget d'analyse) ; `flag` (par défaut) ou
  `block` (HTTP 400, message générique) ; métrique
  `factory_llm_guardrail_events_total{type="prompt_injection"}`. Vérifier :
  `python -m pytest -q compose/litellm/tests` (25 cas d'attaque et 12 cas légitimes parmi 76
  tests), smoke « guardrail: crafted prompt injection flagged ».
- **Résiduel : Moyen à Élevé**. La détection par motifs réduit l'injection de prompt sans
  l'éliminer ; le mode `flag` transmet la requête. Traitez les réponses du modèle comme non
  fiables (5.5), donnez le moindre privilège aux agents (le catalogue consigne les permissions des
  outils et `human_approval`, mais l'usine n'exécute pas elle-même les agents), et passez en
  `block` pour les applications exposées après réglage.

### 5.2 Exfiltration de données via le modèle (LLM02:2025 Sensitive Information Disclosure)

- **Menace** : données sensibles envoyées à un fournisseur externe, fuite dans les journaux ou
  traces, ou extraction depuis le contexte du modèle par un attaquant.
- **En place** : C3/C4 redirigés vers le GPU local sans repli (échec fermé) ; masquage PII avant
  les alias cloud et VPS ; aucun prompt dans les journaux, exceptions ou journaux de dépense de la
  passerelle ; C3/C4 masqués dans Langfuse et exclus de la journalisation ; sortie réseau limitée
  à la passerelle, au bord, à oauth2-proxy et aux serveurs de modèles. Vérifier : smoke « C4
  request ... rerouted to the local GPU », « PII redacted before the cloud target », « gateway
  logs contain no prompt text », « sensitive (C3) traces masked in Langfuse ».
- **Résiduel : Moyen**. Repose sur un étiquetage correct (A4) ; la détection PII ne couvre que
  cinq types d'identifiants ; les prompts C1/C2 sont conservés dans le Langfuse auto-hébergé. La
  passerelle n'analyse pas les réponses à la recherche de fuites.

### 5.3 Compromission d'un modèle ou d'un fournisseur (LLM03:2025 Supply Chain, LLM04:2025 Data and Model Poisoning)

- **Menace** : un fournisseur ou un modèle téléchargé est compromis, renvoie des réponses
  manipulées ou collecte des données ; un modèle ou un corpus empoisonné change le comportement.
- **En place** : les fournisseurs ne reçoivent que des données C1/C2 ; les alias découplent les use
  cases des modèles ; tout changement de version de modèle impose une nouvelle évaluation des
  risques (barrière de déploiement bloquée jusqu'à la revue) ; barrières d'évaluation par niveau de
  risque (fidélité, pertinence, taux de réussite au red teaming) et seuils de retour arrière,
  évalués par le moteur de pipeline (`api/app/engines/pipeline.py`) ; la boucle de données ne
  réentraîne jamais (`fine_tuning: false` ; `api/app/engines/dataloop.py` refuse `retrain`,
  `fine_tune` et les actions similaires) ; images de ce dépôt signées avec provenance ; images
  tierces épinglées et analysées.
- **Résiduel : Moyen**. Les poids des modèles ne sont pas épinglés par révision ni empreinte
  (**Feuille de route**) ; la qualité des barrières d'évaluation dépend de vos jeux
  d'évaluation ; la confiance envers les fournisseurs est contractuelle.

### 5.4 Abus de coûts et déni de portefeuille (LLM10:2025 Unbounded Consumption)

- **Menace** : une application défaillante ou compromise (T4), ou un prompt qui déclenche des
  boucles, fait exploser la facture des fournisseurs ou sature le GPU.
- **En place** : coût par use case et par modèle (`factory_llm_cost_eur_total`), budgets FinOps
  par centre de coût avec les règles de gaspillage `budget_breach` et `retry_storm`, alertes
  Prometheus `FactoryWasteAlertCritical` et `FactoryGatewayErrorRateHigh` ; délais de la
  passerelle (120 s), une seule nouvelle tentative et mises en pause ; limite de corps au bord
  (1 Mo) ; limitation du débit des écritures de l'API.
- **Résiduel : Moyen à Élevé**. La dépense est **détectée**, pas **plafonnée** : aucun budget ni
  débit par application n'est encore appliqué à la passerelle (clés virtuelles LiteLLM avec
  `max_budget` et limites de débit en **Feuille de route**). Fixez des plafonds de dépense chez vos
  fournisseurs.

### 5.5 Traitement non sûr des réponses (LLM05:2025 Improper Output Handling)

- **Menace** : une réponse du modèle utilisée comme code, HTML, SQL, commande ou appel d'outil sans
  validation.
- **En place** : l'usine elle-même n'affiche ni n'exécute les réponses des modèles : le dashboard
  montre des métriques agrégées et de la configuration, échappées en HTML, sous une CSP ; l'API
  renvoie du JSON structuré avec `default-src 'none'`. Les messages de blocage des garde-fous sont
  génériques.
- **Résiduel : dépend de l'application appelante** (hors périmètre, A4). Les applications doivent
  traiter les complétions comme des entrées non fiables : encoder avant affichage, valider avant
  exécution, exiger une approbation humaine pour les appels d'outils à fort impact.

### 5.6 Autres entrées de l'OWASP LLM 2025

| Entrée | État dans cette stack |
|---|---|
| LLM06:2025 Excessive Agency | le catalogue consigne les permissions des outils, les classes de données et `human_approval` ; l'application relève de l'environnement d'exécution des agents (hors périmètre) |
| LLM07:2025 System Prompt Leakage | détection des tentatives d'exfiltration du prompt système (5.1) ; ne mettez pas de secrets dans les prompts |
| LLM08:2025 Vector and Embedding Weaknesses | embeddings calculés sur le GPU local uniquement (`embed-default`) ; Qdrant interne uniquement mais sans authentification à l'intérieur du réseau (4.6) |
| LLM09:2025 Misinformation | barrières d'évaluation (fidélité, LLM-as-a-judge) et seuils de retour arrière sur les retours en ligne, modélisés par le moteur de pipeline ([LLMOPS.md](LLMOPS.md)) |

## 6. Registre des risques résiduels

Les principaux risques résiduels, par priorité, avec l'action qui les réduit :

| # | Risque résiduel | Niveau | Action |
|---|---|---|---|
| R1 | Injection de prompt non détectée par les règles | Moyen-Élevé | mode `block` pour les applications exposées ; agents à moindre privilège ; traitement des réponses dans les applications |
| R2 | Dépense détectée mais non plafonnée | Moyen-Élevé | plafonds chez les fournisseurs dès maintenant ; clés par application avec budgets (**Feuille de route**) |
| R3 | Classe de données mal étiquetée par un appelant | Moyen | classer à la source ; restreindre la clé de la passerelle ; revoir les étiquettes dans le registre |
| R4 | Secrets lisibles dans l'environnement des conteneurs | Moyen | protéger le socket Docker ; fichiers secrets (**Feuille de route**) |
| R5 | Bases non authentifiées dans le réseau interne (Prometheus, Loki, Qdrant) | Moyen | garder le réseau mono-locataire ; ajouter l'authentification des bases ou des politiques réseau sur les hôtes partagés |
| R6 | Chaîne d'audit non clé-dépendante | Moyen | envoyer les copies Loki vers un SIEM ; sauvegardes WORM ; HMAC ou ancrage (**Feuille de route**) |
| R7 | La CSP du dashboard autorise les scripts en ligne | Moyen | CodeQL à chaque changement ; CSP à nonce ou empreinte (**Feuille de route**) |
| R8 | Dépendances d'exécution et images tierces non épinglées par empreinte ou digest | Moyen | verrous à empreintes et épinglage par digest (**Feuille de route**) |
| R9 | Profils GPU non testés en exécution ; conteneurs GPU en root | Moyen | tester sur l'hôte GPU cible avant la production |
| R10 | Pas de limitation de débit par IP au bord | Moyen | WAF ou CDN en amont pour une exposition publique |
