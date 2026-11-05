# `compose/` — configuration des services de la stack

[English](README.md) | **Français**

Chaque sous-dossier configure un service de `docker-compose.yml`. Tout ce qui est ici est
sous licence MIT ; Grafana et Loki (AGPLv3) sont utilisés comme **services séparés et non
modifiés** : le dépôt ne contient que leur configuration, jamais leur code.

| Dossier | Service | Rôle |
|---|---|---|
| `caddy/` | `edge` | Proxy de bord (« proxy du VPS ») : tableau de bord, `/api/*`, `grafana.<hôte>`, `langfuse.<hôte>`, en-têtes de sécurité, `security.txt`, TLS automatique et HSTS en mode VPS, authentification `none` / `basic` / `sso` (test des en-têtes d'identité et de la CSP dans `caddy/tests/`) |
| `sso/` | `oauth2-proxy` (profil `sso`) | Test de bout en bout de l'authentification unique (`test_sso_e2e.sh`, fournisseur d'identité de test dans `e2e/`) |
| `prometheus/` | `prometheus` | Collecte (API, rappel LiteLLM `:9464`, GPU `:9400`, Qdrant, plateforme), règles d'enregistrement (constantes de SLO) et d'alerte (gaspillage, TTFT p95, température GPU, GPU inactif, taux d'erreur de la passerelle, chaîne d'audit rompue, pic de refus de sécurité ; tests unitaires promtool dans `prometheus/tests/`) |
| `prometheus/targets/` | `prometheus` | Hôtes GPU distants (`gpu-*.yml`), par exemple l'atelier local relié au VPS par VPN |
| `grafana/` | `grafana` | Sources de données provisionnées (UID `prometheus` et `loki`) et tableaux de bord versionnés en JSON |
| `grafana-bootstrap/` | `grafana-bootstrap` | Tâche unique : compte de service Viewer et jeton déposé sur un volume lu par l'API seule |
| `loki/` | `loki` | Journaux, binaire unique, stockage fichier, rétention 7 jours |
| `alloy/` | `alloy` | Découverte des conteneurs Docker (via `docker-socket-proxy`, liste blanche en lecture seule) et envoi des journaux vers Loki (Promtail n'est plus maintenu) |
| `litellm/` | `litellm` | Passerelle LLM : alias `chat-default`, `chat-small`, `chat-sensitive`, `embed-default`, replis cloud → VPS → GPU local, rappel MIT `factory_callbacks.py` (métriques, routage sensible, garde-fous ; tests unitaires dans `litellm/tests/`) |
| `postgres/` | `postgres` | Bases `litellm` et `langfuse`, un rôle propriétaire par service |
| `clickhouse/` | `clickhouse` | Réglages « petite machine » pour les traces Langfuse |
| `seaweedfs/` | `seaweedfs` | Stockage objet S3 de Langfuse (à la place de MinIO), identité limitée à son seau |
| `build/` | (construction) | Certificats racine supplémentaires optionnels pour un proxy TLS d'entreprise (`BUILD_CA_BUNDLE`) |

## Profils

| Profil | Services ajoutés | Usage |
|---|---|---|
| (aucun) | cœur de la stack | fournisseurs réels configurés dans `.env` |
| `sim` | `mock-llm`, `traffic`, `gpu-sim` | démonstration complète **sans aucune clé** |
| `vps` | `ollama-vps`, `ollama-vps-pull` | modèle CPU sur le VPS (repli 1) |
| `local-gpu` | `vllm-local`, `vllm-embed`, `dcgm-exporter` | GPU NVIDIA local : données sensibles C3/C4, télémétrie du rack 3D |
| `sso` | `oauth2-proxy` | authentification unique d'entreprise (OpenID Connect) devant le tableau de bord et l'API |

## Placement « cloud d'abord »

```
chat-default   : cloud ──(échec)──▶ VPS ──(échec)──▶ GPU local
chat-small     : VPS   ──(échec)──▶ cloud (petit modèle) ──(échec)──▶ GPU local
chat-sensitive : GPU local uniquement (C3/C4), aucun repli : échec fermé si le GPU est arrêté
embed-default  : GPU local uniquement (les corpus peuvent contenir du C3/C4)
```

Le rappel `factory_callbacks.py` applique aussi la règle à la passerelle : une requête dont
`metadata.sensitivity` vaut C3 ou C4 est redirigée vers `chat-sensitive`, et son contenu est
masqué dans Langfuse.

## Réseaux

- `factory` (interne, sans accès à Internet) : tous les services.
- `egress` : seulement `edge` (ACME), `litellm` (fournisseurs cloud), `oauth2-proxy`
  (fournisseur d'identité), `ollama-vps` et `vllm-*` (téléchargement des modèles).
- `edge-identity` (interne, `10.89.255.0/28` par défaut) : `edge`, `api`, `oauth2-proxy`. Le
  proxy de bord détient l'adresse fixe `FACTORY_EDGE_IDENTITY_IP` (`10.89.255.2`) ; les autres
  membres reçoivent une adresse de `10.89.255.8/29` uniquement. L'API ne lit les en-têtes
  d'identité que depuis cette adresse (`FACTORY_TRUSTED_PROXIES`), oauth2-proxy ne fait
  confiance qu'à elle pour les en-têtes transmis (`--trusted-proxy-ip`).
- `docker-api` (interne) : `alloy` et `docker-socket-proxy` seulement.

Seul `edge` publie des ports. LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres, Loki ne sont
jamais exposés ; le navigateur ne détient aucun jeton (Grafana est interrogé par l'API, côté
serveur, avec le jeton du compte de service Viewer).

## Durcissement de sécurité

Chaque mesure ci-dessous est vérifiée par un test, pas seulement décrite.
`scripts/smoke_stack.sh` inspecte chaque conteneur en marche et échoue à la moindre régression.

### Durcissement des conteneurs (mesuré avec `docker inspect`, profil `sim`)

Appliqué à tous les services : `no-new-privileges`, `cap_drop: [ALL]`, limite de processus
(`deploy.resources.limits.pids`), limites mémoire et CPU, journaux plafonnés.

| Service | Utilisateur | Racine en lecture seule | Capacités ajoutées | Limite de processus | Remarques |
|---|---|---|---|---|---|
| `edge` | 10001 | oui (`/tmp` en tmpfs) | `NET_BIND_SERVICE` | 128 | le binaire `caddy` porte la capacité de fichier `cap_net_bind_service` (ports 80/443) : l'exécution échoue si elle n'est pas dans l'ensemble limite |
| `volume-init` (tâche unique) | root | oui | `CHOWN`, `DAC_READ_SEARCH` | 16 | sans réseau ; donne les volumes du proxy (certificats ACME) à l'uid 10001, sans effet sur des volumes neufs |
| `dashboard` | 101 | oui | aucune | 64 | port 80 via `net.ipv4.ip_unprivileged_port_start=0` |
| `api` | 10001 | oui | aucune | 128 | |
| `oauth2-proxy` (`sso`) | 65532 | oui | aucune | 64 | image distroless, pas de sonde de santé possible (le proxy de bord échoue fermé en 502) |
| `prometheus` | nobody | oui | aucune | 256 | |
| `grafana` | 472 | oui | aucune | 512 | |
| `grafana-bootstrap` (tâche unique) | 10001 | oui | aucune | 32 | |
| `loki` | 10001 | oui | aucune | 512 | distroless, disponibilité vérifiée via Grafana |
| `docker-socket-proxy` | root (option : nobody + groupe docker) | oui | aucune | 32 | seul conteneur avec le socket Docker, voir plus bas |
| `alloy` | 473 | oui | aucune | 256 | pas de socket Docker ; état dans le volume `alloy-state` |
| `litellm` | nobody | oui | aucune | 512 | image officielle non root `litellm/litellm-non_root` |
| `qdrant` | 1000 | oui | aucune | 512 | instantanés conservés dans le volume de stockage |
| `langfuse-web` | 1001 | oui | aucune | 512 | cache Next.js en tmpfs ; la lecture seule protège aussi les fichiers applicatifs, que l'image laisse modifiables par son utilisateur |
| `langfuse-worker` | 1001 | oui | aucune | 512 | |
| `postgres` | 70 | oui | aucune | 512 | démarre directement en `postgres` : aucune étape root, `gosu` jamais exécuté |
| `clickhouse` | 101 | oui | aucune | 4096 | `users.d` en tmpfs (écrit par le point d'entrée) ; les pools de fils grandissent avec les cœurs |
| `valkey` | 999 | oui | aucune | 128 | mot de passe lu dans un fichier tmpfs privé, jamais sur la ligne de commande |
| `seaweedfs` | 1000 | oui | aucune | 512 | identité S3 générée dans un tmpfs privé |
| `mock-llm`, `traffic`, `gpu-sim` (`sim`) | 10001 | oui | aucune | 64 / 64 / 32 | |
| `ollama-vps`, `ollama-vps-pull` (`vps`) | root | non | aucune | 1024 / 64 | non testé à l'exécution ici ; l'image écrit dans `/root/.ollama` |
| `vllm-local`, `vllm-embed` (`local-gpu`) | root | non | aucune | 4096 | non testé à l'exécution ici (pas de GPU) ; `/dev/shm` dimensionné (`VLLM_SHM_SIZE`) au lieu de `ipc: host` |
| `dcgm-exporter` (`local-gpu`) | défaut de l'image | non | ensemble par défaut conservé | 256 | NVIDIA documente `CAP_SYS_ADMIN` pour les métriques de profilage ; non testable sans GPU |

Une installation neuve (volumes vides) a été vérifiée avec ces réglages pour le proxy de
bord, les services de stockage, Prometheus, Loki, Alloy et le proxy du socket.

### Socket Docker

Alloy a besoin de l'API Docker pour découvrir les conteneurs et lire leurs journaux. Il ne
monte jamais le socket : il passe par `docker-socket-proxy`
([wollomatic/socket-proxy](https://github.com/wollomatic/socket-proxy), MIT) sur le réseau
interne `docker-api`. Le proxy ne répond qu'au conteneur `alloy` et ne laisse passer que les
`GET` sur `_ping`, `version`, `networks`, `containers/json`, `containers/<id>/json` et
`containers/<id>/logs` ; toute autre méthode ou chemin (création, exec, archive, export,
images…) est refusé (403 / 405, vérifié par le test de fumée). Risque résiduel : l'inspection
d'un conteneur renvoie son environnement, donc Alloy est aussi sensible que le fichier `.env`.
Pour exécuter le proxy en `nobody` : `DOCKER_SOCKET_PROXY_USER=65534:$(stat -c %g /var/run/docker.sock)`.

### Proxy de bord (Caddy)

- `Content-Security-Policy` : la politique du tableau de bord (sans CDN par défaut) sur le
  tableau de bord seulement ; `/api/*` garde la politique de l'API (`default-src 'none'` sur le
  JSON, un nonce par réponse et Swagger UI épinglé et vérifié par Subresource Integrity sur
  `/api/v1/docs`), avec une valeur par défaut qui interdit tout pour une réponse qui n'en aurait
  pas. `X-Frame-Options`, `X-Content-Type-Options`, `Referrer-Policy` : posées quand l'amont
  n'envoie pas de valeur plus stricte.
- `Permissions-Policy` (caméra, micro, géolocalisation, paiement, USB… tous refusés),
  `Cross-Origin-Opener-Policy` et `Cross-Origin-Resource-Policy: same-origin`,
  `X-Permitted-Cross-Domain-Policies: none` ; `Server`, `Via` et `X-Powered-By` retirés.
- `Strict-Transport-Security` uniquement si `FACTORY_SCHEME=https` : un navigateur n'épingle
  jamais en TLS un hôte de démonstration en HTTP.
- `/.well-known/security.txt` (RFC 9116) sur chaque nom d'hôte : contact via le signalement
  privé de vulnérabilité GitHub du dépôt, `Expires` (`FACTORY_SECURITY_TXT_EXPIRES`, à avancer
  chaque année), `Policy`, `Canonical`, `Preferred-Languages: en, fr`.
- Taille des corps de requête : 1 Mo (tableau de bord et API), 10 Mo (Grafana), 20 Mo
  (ingestion Langfuse) ; délai d'en-tête 10 s, de corps 60 s, inactivité 2 min, en-têtes
  plafonnés à 64 Ko.
- Les en-têtes d'identité (`X-Forwarded-User`, `-Email`, `-Groups`, `-Preferred-Username`,
  `X-Auth-Request-*`) envoyés par les clients sont supprimés sur chaque route et chaque hôte,
  dans tous les modes ; seul le proxy de bord les pose (nom de l'utilisateur basique en mode
  `basic`, réponse d'oauth2-proxy via `forward_auth copy_headers` en mode `sso`). Un
  `X-Real-IP` ou `X-Forwarded-For` fourni par le client n'atteint pas non plus les services.
  `caddy/tests/test_identity_headers.sh` vérifie chaque route en `none`, `basic` et `sso` face à
  des services factices (68 vérifications, job CI `compose`).

### Authentification unique d'entreprise (profil `sso`)

1. Déclarer une application chez le fournisseur d'identité (Microsoft Entra ID, Okta, Google
   Workspace, Keycloak ou tout fournisseur OpenID Connect) avec l'URI de redirection
   `https://<FACTORY_HOST>/oauth2/callback`.
2. Dans `.env` : `FACTORY_AUTH=sso`, `FACTORY_AUTH_MODE=proxy`, le bloc `SSO_*` (fournisseur,
   émetteur, client, secret du cookie, domaines de courriel et groupes autorisés) et
   `FACTORY_ROLE_MAP` (groupes SSO vers rôles de l'API).
3. `docker compose --profile sim --profile sso up -d` (ou sans `sim` avec des fournisseurs réels).

Le parcours : Caddy interroge oauth2-proxy (`forward_auth`) à chaque page et appel d'API ; une
page sans session est redirigée vers le fournisseur (code d'autorisation avec PKCE `S256`), un
appel d'API reçoit un simple 401. En cas de succès, Caddy transmet `X-Forwarded-User`,
`X-Forwarded-Email` et `X-Forwarded-Groups` à l'API (les copies du client sont supprimées
avant). La session est un cookie chiffré `HttpOnly`, `Secure`, `SameSite=Lax`, préfixé
`__Host-`, de 8 heures par défaut. oauth2-proxy refuse de démarrer sans configuration valide
(client, secret, secret du cookie, domaines de courriel) : le proxy de bord répond alors 502
(échec fermé). Grafana et Langfuse gardent leur propre connexion (chacun peut utiliser
nativement le même fournisseur).

`compose/sso/test_sso_e2e.sh` exécute le vrai parcours dans un réseau isolé : le Caddyfile
livré, oauth2-proxy avec l'environnement produit par `docker compose --profile sso config`, un
fournisseur de test (Dex) et un service d'écho à la place du tableau de bord et de l'API. Il
vérifie les redirections, les 401, les en-têtes forgés, l'identité reçue par l'API, l'adresse
fixe du proxy de bord et le refus d'un utilisateur hors de `SSO_ALLOWED_GROUPS`.

### Garde-fous LLM (passerelle)

`litellm/factory_callbacks.py` s'exécute avant qu'une requête ne quitte la passerelle :

- **Détection d'injection de prompt / jailbreak** sur le contenu non fiable (texte de
  l'utilisateur, des outils et des documents récupérés ; les prompts système ne sont pas
  analysés) : contournement des instructions, exfiltration du prompt système, jeux de rôle de
  jailbreak, délimiteurs de gabarit de conversation, charges encodées, caractères Unicode
  invisibles (étiquettes, bidirectionnels), en anglais et en français, après normalisation
  Unicode. Un remplissage au-delà du budget d'analyse est lui-même signalé.
  `FACTORY_GUARDRAIL_MODE=flag` (défaut : transmis, identifiants de règles dans
  `metadata.factory_guardrail`, étiquette `guardrail:prompt_injection` dans Langfuse), `block`
  (HTTP 400 avec un message générique) ou `off` ; une valeur inconnue vaut `block`.
- **Masquage des données personnelles** avant un alias qui peut quitter le GPU local
  (`chat-default`, `chat-small` et leurs replis) : adresses de courriel, numéros de téléphone
  (international, français, nord-américain), IBAN (longueur ISO 13616 et contrôle mod 97),
  cartes de paiement (Luhn) et NIR (avec sa clé, Corse comprise) deviennent
  `[REDACTED_<TYPE>]`. Les requêtes C3/C4 n'atteignent jamais ces alias (elles sont redirigées
  vers `chat-sensitive`) ; `FACTORY_PII_REDACTION=false` désactive le masquage.
- **Aucun prompt dans les journaux** : les requêtes C3/C4 sont masquées dans Langfuse et
  portent `turn_off_message_logging` (toutes les intégrations de journalisation de LiteLLM ;
  l'en-tête client qui désactiverait le masquage est retiré) ;
  `redact_messages_in_exceptions` tient les prompts hors des messages d'erreur et
  `store_prompts_in_spend_logs: false` hors des journaux de dépenses. Les métadonnées et
  métriques des garde-fous ne contiennent que des identifiants de règles et des comptes.
- Métrique `factory_llm_guardrail_events_total{type,action,usecase}` ; les API Responses et
  Messages sont redirigées comme les complétions de chat.

Tests unitaires (sans LiteLLM) :

```bash
pip install --require-hashes -r compose/litellm/tests/requirements.txt   # verrou à empreintes
python -m pytest -q compose/litellm/tests                                # job CI « guardrails »
```

### Analyse des images (Trivy)

Le tableau des vulnérabilités HIGH/CRITICAL par image (Trivy 0.76.1, base du 2026-11-04) est
dans la [version anglaise](README.md#image-scan-trivy). Les six images construites depuis ce
dépôt passent le contrôle de la CI (aucune HIGH ou CRITICAL corrigeable) : celle du tableau de
bord (`nginx/nginx-unprivileged:1.30.6-alpine-slim`, sans les modules optionnels ni leurs
bibliothèques) n'a aucun constat HIGH ou CRITICAL ; les cinq images Python
(`python:3.12.16-slim`, Debian 13.7) portent 45 HIGH encore sans version corrigée. Toutes les images sont à leur dernière
version ; les constats restants attendent une reconstruction en amont. Ceux de `postgres` sont
tous dans `gosu`, jamais exécuté (le conteneur démarre en utilisateur `postgres`).

## Vérifications

```bash
docker compose --profile sim --profile vps --profile local-gpu --profile sso config -q
scripts/smoke_stack.sh            # démarre le profil sim et vérifie toute la chaîne + la sécurité
compose/sso/test_sso_e2e.sh       # authentification unique, réseau isolé
compose/caddy/tests/test_identity_headers.sh   # en-têtes d'identité falsifiés et CSP, chaque mode
python -m pytest -q compose/litellm/tests   # détecteurs des garde-fous et crochet de la passerelle
docker run --rm -v "$PWD/compose/prometheus:/etc/prometheus:ro" --entrypoint promtool \
  prom/prometheus:v3.15.1 test rules /etc/prometheus/tests/security-alerts.test.yml
scripts/scan_secrets.sh .         # gitleaks (MIT) avant tout commit ou intégration
```
