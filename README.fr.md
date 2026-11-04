# Virtual AI Factory

[English](README.md) · **Français**

**Virtual AI Factory — le jumeau numérique open source d'une AI factory d'entreprise**

[![CI](https://github.com/alebgl77/ai-factory/actions/workflows/ci.yml/badge.svg)](https://github.com/alebgl77/ai-factory/actions/workflows/ci.yml)
[![Licence : MIT](https://img.shields.io/badge/licence-MIT-blue.svg)](LICENSE)
[![Démo en ligne](https://img.shields.io/badge/d%C3%A9mo-GitHub%20Pages-6D28D9.svg)](https://alebgl77.github.io/ai-factory/)
[![Ouvrir dans GitHub Codespaces](https://github.com/codespaces/badge.svg)](https://codespaces.new/alebgl77/ai-factory)

Virtual AI Factory est un modèle **générique** et **déployable** de la façon dont une entreprise
exploite l'IA en production, rendu sous la forme d'un jumeau 3D des bureaux où tout se passe.
Derrière la scène 3D tourne une vraie stack : **gouvernance** (AI Act, y compris l'Omnibus
numérique de 2026, NIST AI RMF et ISO/IEC 42001, avec réévaluation du risque à chaque version de
modèle), **FinOps** (coûts tokens et GPU, showback et chargeback, 8 règles de gaspillage), un
**pipeline LLMOps et RAG** avec portes d'évaluation et retour arrière automatique, une **boucle de
données qui ne réentraîne jamais**, un **placement hybride** (cloud, puis VPS, puis GPU local pour
les données sensibles), une salle **Agent Ops**, un **catalogue en libre-service**, des **Skills
Lab et R&D Lab**, et un **mode visite en vue subjective** pour parcourir le jumeau. Tout se
configure en YAML, tourne sans aucune clé d'API en mode démo, et est sous licence MIT.

> Toutes les données sont **fictives** : personnes, use cases, centres de coût, coûts et
> métriques de démonstration. Tout se configure dans `config/`. Aucun fine-tuning ni
> réentraînement : la boucle de données agit uniquement par réindexation, mise à jour des jeux
> d'évaluation ou changement de routage.

## Essayer

| En 10 secondes | En 2 minutes | Sur votre machine |
|---|---|---|
| **[Démo en ligne](https://alebgl77.github.io/ai-factory/)** : le dashboard 3D avec le snapshot de démo embarqué (statique, sans serveur) | **[Ouvrir dans GitHub Codespaces](https://codespaces.new/alebgl77/ai-factory)** : toute la stack démarre avec le profil `sim`, les secrets sont générés pour vous | `docker compose --profile sim up -d --build` (voir ci-dessous) |

Ensuite, dans le dashboard : touche `M` (ou bouton **Visite**) pour parcourir l'usine en vue
subjective, `U` pour ouvrir la console de l'usine (registre, FinOps, pipeline, boucle de données,
placement, catalogue), `?` pour l'aide. Changez de langue avec le bouton **EN/FR** de l'en-tête.

### Démarrage local (sans aucune clé)

Prérequis : Docker 24+ avec Compose v2, 4 vCPU, 8 Go de RAM libres, ports 80 et 443 disponibles.

```bash
git clone --recurse-submodules https://github.com/alebgl77/ai-factory.git
cd virtual-ai-factory
cp .env.example .env
# Remplacer chaque valeur « change-me » par un secret propre à chaque variable, par exemple :
#   for v in $(grep -o '^[A-Z_]*=.*change-me[^ ]*' .env | cut -d= -f1); do
#     sed -i "s|^$v=.*|$v=sk-$(openssl rand -hex 24)|" .env; done
# (LITELLM_MASTER_KEY doit commencer par « sk- » ; garder LITELLM_SALT_KEY stable ensuite.)
docker compose --profile sim up -d --build
./scripts/smoke_stack.sh --no-up  # vérifie santé, routage LLM, métriques, dashboards, traces, logs
```

Puis ouvrir :

| URL (local) | Service |
|---|---|
| `http://localhost/` | dashboard 3D (source « API ») |
| `http://localhost/api/v1/docs` | documentation OpenAPI de l'API d'agrégation |
| `http://grafana.localhost/` ou `http://127.0.0.1:3001/` | Grafana (compte admin du `.env`) |
| `http://langfuse.localhost/` ou `http://127.0.0.1:3002/` | Langfuse (compte d'initialisation `LANGFUSE_INIT_USER_*` du `.env`) |

Le profil `sim` remplace les fournisseurs LLM par `mock-llm` (compatible OpenAI, streaming et
latences réalistes), génère du trafic avec `traffic` et simule la télémétrie DCGM du GPU avec
`gpu-sim`. Toute la chaîne fonctionne donc sans clé ni GPU : passerelle → callback de coûts →
Prometheus → Grafana → API → dashboard 3D.

**Version hors ligne** : ouvrir directement `dashboard/index.html` dans un navigateur. Le
dashboard utilise alors les snapshots de démonstration embarqués (un en anglais, un en français)
et affiche le badge « Démo hors ligne ».

## Ce que fait l'usine

| Priorité | Capacité | Où |
|---|---|---|
| P0 | Registre des use cases, niveaux de risque AI Act (interdit, haut risque annexe III / annexe I, limité art. 50, minimal), cycle NIST AI RMF (GOVERN, MAP, MEASURE, MANAGE) croisé avec l'AI Act et l'ISO/IEC 42001, **réévaluation du risque à chaque changement de version de modèle** | `config/usecases/`, `config/frameworks/`, `api/app/engines/risk.py`, console « Registre » |
| P0 | Coûts tokens et GPU par use case, showback / chargeback, 8 règles d'alerte de gaspillage | `config/factory.yaml`, `engines/costs.py`, console « FinOps » |
| P1 | Pipeline LLMOps / RAG : ingestion → découpage et indexation → versionnage des prompts et agents → portes d'évaluation (RAGAS, LLM-as-a-judge, red team) → déploiement → retours d'usage → retour arrière automatique | `engines/pipeline.py`, console « Pipeline » |
| P1 | Boucle de données sur dérive : réindexation, mise à jour des jeux d'évaluation, changement de routage, revue humaine. Pas de réentraînement | `engines/dataloop.py`, console « Boucle de données » |
| P1 | Placement hybride cloud → VPS → GPU local. Les classes C3/C4 vont uniquement sur le GPU local ; s'il est indisponible, la charge est mise en file d'attente, jamais envoyée ailleurs | `engines/placement.py`, `compose/litellm/config.yaml`, console « Placement » |
| P2 | Salle Agent Ops : TTFT, tokens par tâche, latence vectorielle, SLO | `engines/agentops.py`, consoles O1/O2, dashboard Grafana `agent-ops` |
| P2 | Catalogue en libre-service des modèles, agents, outils MCP et dépôts GitHub intégrés | `config/catalog/`, console « Catalogue » |
| P2 | Skills Lab et R&D Lab en 3D, rattachés aux rôles et à l'art. 4 de l'AI Act (maîtrise de l'IA, tel que réécrit en 2026) | `config/skills.yaml`, salles S1, X1–X3 |
| + | **Mode visite** façon Street View : glisser le personnage sur le sol, marcher en vue subjective (clavier, clic au sol, pavé tactile), interagir avec les postes, salles, personnes, écrans et le rack ; mini-plan avec statut des espaces | bouton **Visite** ou touche `M` |
| + | **Anglais et français** partout : API (`?lang=en\|fr`), configuration (dictionnaires `{en, fr}`), dashboard, documentation | [docs/CONTRACT.md](docs/CONTRACT.md), section 9 (en anglais) |
| + | Pack visuel facultatif généré dans ChatGPT (textures, décor, portraits, mascotte) avec repli procédural | [docs/fr/ASSETS.md](docs/fr/ASSETS.md) |

## Architecture

```text
Navigateur (dashboard 3D, aucun jeton) ──HTTPS──► edge (Caddy : TLS, auth optionnelle)
   ├─ /       dashboard (nginx + Three.js vendorisé)
   └─ /api/*  API d'agrégation (FastAPI, MIT) : moteurs risque, FinOps, pipeline,
              boucle de données, placement, Agent Ops, catalogue et skills
                 │ côté serveur uniquement
                 ├─ Prometheus ◄── callback de coûts LiteLLM, DCGM / gpu-sim, Qdrant, KPI de l'API
                 ├─ Grafana (service AGPL séparé, jeton Viewer) ── Loki ◄── Alloy
                 ├─ Langfuse (traces, prompts, scores)
                 └─ passerelle LiteLLM ──► cloud │ VPS (Ollama) │ GPU local (vLLM)
```

L'API agrège `config/`, son état modifiable et les sources live ; une source injoignable bascule
sur un générateur de démo déterministe (`meta.mode` : `demo`, `mixed`, `live`). Détails, flux et
alternatives écartées : [docs/fr/ARCHITECTURE.md](docs/fr/ARCHITECTURE.md). Contrat d'interface
(schémas, API, métriques, services, en anglais) : [docs/CONTRACT.md](docs/CONTRACT.md).

## Déploiement réel : cloud d'abord, puis VPS, puis matériel local

1. **Cloud** : dans `.env`, remplacer `CLOUD_API_BASE`, `CLOUD_API_KEY`, `CLOUD_CHAT_MODEL` et
   `CLOUD_SMALL_MODEL` (par défaut : LLM simulé) par ceux de votre fournisseur compatible OpenAI,
   puis démarrer **sans** le profil `sim`. L'alias `chat-default` part vers le cloud ; ses replis
   sont le VPS puis le GPU local (`compose/litellm/config.yaml`).
2. **VPS** (proxy et repli) : sur le VPS, définir `FACTORY_HOST=usine.example.org`,
   `FACTORY_SCHEME=https`, `GRAFANA_PUBLIC_URL` et `LANGFUSE_PUBLIC_URL` (enregistrements DNS
   pour le domaine, `grafana.` et `langfuse.`). Caddy obtient automatiquement les certificats TLS.
   Activer l'authentification du proxy (`FACTORY_AUTH=basic`, `FACTORY_BASIC_AUTH_USER`,
   `FACTORY_BASIC_AUTH_HASH`, voir [docs/fr/SECURITY.md](docs/fr/SECURITY.md)). Pour un modèle
   CPU de repli, ajouter le profil `vps` et pointer `VPS_API_BASE=http://ollama-vps:11434/v1` et
   `VPS_CHAT_MODEL=openai/<modèle Ollama>`.
   ```bash
   docker compose --profile vps up -d --build
   ```
3. **GPU local** (données sensibles C3/C4) : sur la machine équipée d'un GPU NVIDIA (pilote et
   NVIDIA Container Toolkit installés), démarrer `vllm-local`, `vllm-embed` et `dcgm-exporter`
   avec le profil `local-gpu`, puis pointer `LOCAL_API_BASE`, `LOCAL_CHAT_MODEL`,
   `LOCAL_EMBED_API_BASE` et `LOCAL_EMBED_MODEL` vers vLLM. Si le GPU est sur une autre machine
   que le VPS, la relier par un tunnel chiffré (WireGuard), mettre son adresse dans
   `LOCAL_*_API_BASE` et déclarer son exporteur DCGM dans `compose/prometheus/targets/gpu-remote.yml`.
   La télémétrie réelle du GPU alimente alors le rack 3D (température, utilisation, puissance,
   mémoire). `chat-sensitive` et `embed-default` restent sur le GPU local et échouent en mode
   fermé (HTTP 503) s'il est indisponible : jamais de repli vers le cloud ou le VPS.
   ```bash
   docker compose --profile local-gpu up -d
   ```

Grafana n'est jamais appelé par le navigateur : l'API l'interroge côté serveur, via le proxy du
VPS, avec le jeton d'un compte de service *Viewer* créé par `grafana-bootstrap` et stocké dans un
volume. Les dashboards Grafana sont versionnés en JSON (`compose/grafana/dashboards/`) et
provisionnés au démarrage.

## Adapter l'usine à une organisation

| Fichier | Contenu |
|---|---|
| `config/factory.yaml` | identité, calendrier réglementaire, centres de coût et budgets, tarifs tokens et GPU, mode showback ou chargeback, règles de gaspillage, politique de placement, SLO Agent Ops, seuils des portes et du retour arrière |
| `config/profiles.yaml` | rôles et personnes (nombre libre), postes, casiers, compétences, rythme de présence |
| `config/usecases/*.yaml` | un fichier par use case : qualification AI Act, NIST, ISO 42001, modèle et version revue, RAG, KPI |
| `config/catalog/` | modèles, agents, outils, fiches des dépôts GitHub |
| `config/skills.yaml` | parcours par rôle (art. 4), modules, sessions du Skills Lab, expériences du R&D Lab |
| `config/frameworks/` | référentiels NIST AI RMF, AI Act, ISO/IEC 42001 et table de correspondance |

Toute valeur lisible peut être une chaîne simple ou un dictionnaire `{en: "...", fr: "..."}`.
Chaque point de lecture de l'API accepte `?lang=en|fr` ; la langue par défaut vient de
`FACTORY_DEFAULT_LANG` (`en` si absent). Après modification : `python scripts/validate_config.py`,
puis `python scripts/build_snapshot.py` pour régénérer les snapshots hors ligne du dashboard.

## Sécurité

- Aucun secret dans le dépôt (gitleaks dans le CI) et **aucun jeton dans le navigateur** : le
  dashboard ne parle qu'à l'API d'agrégation, en même origine.
- Proxy Grafana restreint (liste blanche de GET, requêtes PromQL nommées uniquement).
- Seul `edge` publie des ports ; les services internes restent sur un réseau interne sans accès
  Internet.
- Les données C3/C4 ne vont que sur le GPU local, règle appliquée à la passerelle, et sont
  masquées dans Langfuse.

Détails et durcissement d'un VPS : [docs/fr/SECURITY.md](docs/fr/SECURITY.md). Pour signaler une
vulnérabilité, ouvrez un *Security advisory* privé sur le dépôt.

## Licences

Le code de ce dépôt est sous licence **MIT** ([LICENSE](LICENSE)). Grafana et Loki (AGPL-3.0)
tournent comme **services séparés et non modifiés**, à partir de leurs images officielles ; le
dépôt n'embarque ni ne lie leur code. LiteLLM est utilisé hors de son répertoire `enterprise/` et
Langfuse sans ses modules `ee` ni clé de licence. Les images du pack visuel sont publiées sous la
licence MIT du dépôt (décision de l'auteur), avec leur provenance SHA-256. Détails, matrice de
compatibilité et cas où l'AGPL s'appliquerait : [docs/fr/LICENSES.md](docs/fr/LICENSES.md).

## Intégrations GitHub

Sept dépôts publics de l'auteur, alignés sur les piliers de l'usine, sont référencés en
**sous-modules** (aucun code copié) et en fiches du catalogue, après contrôle des licences, des
dépendances et des secrets sur tout l'historique : voir
[docs/fr/GITHUB_INTEGRATION.md](docs/fr/GITHUB_INTEGRATION.md). Après un clone simple :
`git submodule update --init`.

## Pack visuel

La scène 3D fonctionne entièrement avec des matériaux procéduraux. Un pack facultatif de
15 images (textures raccordables, toile de fond, œuvres, portraits de l'équipe, mascotte du mode
visite, illustration isométrique) se génère dans ChatGPT à partir de prompts prêts à coller, puis
s'optimise et s'assemble avec `scripts/build_assets.py` : [docs/fr/ASSETS.md](docs/fr/ASSETS.md).

## Tests et vérifications

```bash
python -m venv .venv && . .venv/bin/activate && pip install -r api/requirements-dev.txt
(cd api && python -m pytest -q)           # moteurs (risque, coûts, pipeline, placement…), i18n et API
python scripts/validate_config.py         # cohérence de config/
python scripts/build_snapshot.py --check  # snapshots embarqués EN et FR à jour
python scripts/check_licenses.py          # compatibilité des licences (dépendances, images, sous-modules)
pip install -r scripts/requirements-assets.txt && python -m pytest -q tests/assets   # pipeline du pack visuel
./scripts/scan_secrets.sh .               # gitleaks
npm i --no-save playwright three@0.186.1 && THREE_VENDOR_DIR=node_modules/three node tests/e2e/run.mjs   # e2e du dashboard hors ligne
E2E_BASE_URL=http://localhost node tests/e2e/run.mjs                 # e2e sur la stack déployée
```

Le CI (`.github/workflows/ci.yml`) exécute ces contrôles à chaque push, ainsi qu'un test de fumée
complet de la stack `sim`.

## Feuille de route

Des pistes, pas des engagements ; vos retours sont les bienvenus dans les issues.

- Un dépôt MIT de référence pour l'ingestion RAG et les jeux d'évaluation RAGAS, et un exemple
  multi-agents LangGraph (les deux lacunes relevées dans
  [docs/fr/GITHUB_INTEGRATION.md](docs/fr/GITHUB_INTEGRATION.md)).
- Pack visuel généré, publié avec sa provenance.
- Recette d'authentification OIDC devant `edge` (oauth2-proxy ou Authelia).
- Confirmation au Journal officiel des points réglementaires marqués « à vérifier » dans
  [docs/fr/GOVERNANCE.md](docs/fr/GOVERNANCE.md).
- D'autres langues : le catalogue de messages et les dictionnaires `{en, fr}` sont prévus pour.

## Contribuer

Les contributions sont bienvenues : issues, exemples de `config/` anonymisés, corrections des
correspondances réglementaires (avec une source primaire), code. Lire
[CONTRIBUTING.md](CONTRIBUTING.md) (en anglais) et le [code de conduite](CODE_OF_CONDUCT.md). Pour
citer le projet : [CITATION.cff](CITATION.cff).

## Documentation

- [docs/fr/ARCHITECTURE.md](docs/fr/ARCHITECTURE.md) : schéma, flux, choix techniques
- [docs/CONTRACT.md](docs/CONTRACT.md) : contrat d'interface (schémas, API, métriques, services, i18n ; en anglais)
- [docs/fr/GOVERNANCE.md](docs/fr/GOVERNANCE.md) : registre, niveaux de risque, réévaluation, correspondances, calendrier réglementaire et sources
- [docs/fr/FINOPS.md](docs/fr/FINOPS.md), [docs/fr/LLMOPS.md](docs/fr/LLMOPS.md), [docs/fr/SKILLS_RND.md](docs/fr/SKILLS_RND.md)
- [docs/fr/SECURITY.md](docs/fr/SECURITY.md), [docs/fr/LICENSES.md](docs/fr/LICENSES.md), [docs/fr/GITHUB_INTEGRATION.md](docs/fr/GITHUB_INTEGRATION.md)
- [docs/fr/ASSETS.md](docs/fr/ASSETS.md) : pack visuel (prompts ChatGPT, pipeline, provenance)
- Documentation en anglais : [README.md](README.md) et [docs/](docs/ARCHITECTURE.md)

## Auteur

Créé par **Alexandre Beguel** (Digitalizor), GitHub : [alebgl77](https://github.com/alebgl77).
Copyright (c) 2026 Alexandre Beguel (Digitalizor) and contributors, licence MIT.

## Avertissement

Toutes les données de ce dépôt sont **fictives**. Les qualifications AI Act, les correspondances
NIST / AI Act / ISO 42001 et le calendrier réglementaire sont **indicatifs** : ils servent de
modèle de travail et **ne constituent pas un avis juridique**. Les dates d'application sont
configurables dans `config/factory.yaml` ; vérifiez-les au Journal officiel de l'UE avant tout
usage.

Projet indépendant, sans lien avec NVIDIA, Dell Technologies, l'entreprise commune EuroHPC ou la
Commission européenne, ni approuvé par ceux-ci ; « AI factory » est employé comme terme générique.
