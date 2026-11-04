# `compose/` — configuration des services de la stack

[English](README.md) | **Français**

Chaque sous-dossier configure un service de `docker-compose.yml`. Tout ce qui est ici est
sous licence MIT ; Grafana et Loki (AGPLv3) sont utilisés comme **services séparés et non
modifiés** : le dépôt ne contient que leur configuration, jamais leur code.

| Dossier | Service | Rôle |
|---|---|---|
| `caddy/` | `edge` | Proxy de bord (« proxy du VPS ») : tableau de bord, `/api/*`, `grafana.<hôte>`, `langfuse.<hôte>`, en-têtes de sécurité, TLS automatique en mode VPS, authentification basique optionnelle |
| `prometheus/` | `prometheus` | Collecte (API, rappel LiteLLM `:9464`, GPU `:9400`, Qdrant, plateforme), règles d'enregistrement (constantes de SLO) et d'alerte (gaspillage, TTFT p95, température GPU, GPU inactif, taux d'erreur de la passerelle) |
| `prometheus/targets/` | `prometheus` | Hôtes GPU distants (`gpu-*.yml`), par exemple l'atelier local relié au VPS par VPN |
| `grafana/` | `grafana` | Sources de données provisionnées (UID `prometheus` et `loki`) et tableaux de bord versionnés en JSON |
| `grafana-bootstrap/` | `grafana-bootstrap` | Tâche unique : compte de service Viewer et jeton déposé sur un volume lu par l'API seule |
| `loki/` | `loki` | Journaux, binaire unique, stockage fichier, rétention 7 jours |
| `alloy/` | `alloy` | Découverte des conteneurs Docker et envoi des journaux vers Loki (Promtail n'est plus maintenu) |
| `litellm/` | `litellm` | Passerelle LLM : alias `chat-default`, `chat-small`, `chat-sensitive`, `embed-default`, replis cloud → VPS → GPU local, rappel MIT `factory_callbacks.py` |
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
- `egress` : seulement `edge` (ACME), `litellm` (fournisseurs cloud), `ollama-vps` et `vllm-*`
  (téléchargement des modèles).

Seul `edge` publie des ports. LiteLLM, Prometheus, Qdrant, ClickHouse, Postgres, Loki ne sont
jamais exposés ; le navigateur ne détient aucun jeton (Grafana est interrogé par l'API, côté
serveur, avec le jeton du compte de service Viewer).

## Vérifications

```bash
docker compose --profile sim --profile vps --profile local-gpu config -q
scripts/smoke_stack.sh            # démarre le profil sim et vérifie toute la chaîne
scripts/scan_secrets.sh .         # gitleaks (MIT) avant tout commit ou intégration
```
