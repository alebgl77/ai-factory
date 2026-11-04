# Intégration des dépôts GitHub `alebgl77`

*[English version](../GITHUB_INTEGRATION.md)*

Rapport d'intégration des projets publics du compte GitHub `alebgl77` dans Virtual AI
Factory, au **3 novembre 2026**. Les projets retenus sont intégrés comme **fiches du catalogue**
(`config/catalog/github/<dépôt>.yaml`, schéma : [CONTRACT.md](../CONTRACT.md), section 2.5) et comme **sous-modules git**
épinglés (`integrations/<dépôt>`) : aucun code n'est copié dans ce dépôt.

## En bref

| Indicateur | Valeur |
|---|---|
| Dépôts inventoriés | **28** : 15 dépôts propres publics, 12 forks, 1 dépôt privé |
| Intégrés (fiche + sous-module) | **7** : grafana-llmops-forge, openspanguard, open-shadow-ai, dsh-plugin-otel-genai, tierdecay, harnessmeter, promptor |
| À relicencier obligatoirement | **0** : tous les dépôts retenus ont une licence compatible (6 MIT, 2 Apache-2.0) |
| Relicence MIT conseillée (facultative) | 2 : open-shadow-ai et openspanguard (Apache-2.0, voir plus bas) |
| Exclus | **21** : 8 dépôts propres hors piliers, 12 forks, 1 dépôt privé |
| Dépendances vérifiées (dépôts propres) | 193, dont 108 pour les 7 dépôts intégrés : **aucune dépendance d'exécution GPL, AGPL, LGPL ou SSPL** |
| Secrets (gitleaks, historique complet) | 559 commits analysés, 20 résultats bruts, 19 faux positifs triés, **1 résultat restant** (clé privée de test d'un dépôt exclu) |
| Piliers couverts | LLMOps et observabilité (4), évaluation (2), gouvernance et sécurité (1), multi-agents (1, sans LangGraph), **RAG (0)** |

## Méthode

1. **Inventaire.** `gh` n'était pas authentifié dans la session de travail (jeton invalide) et
   l'API GitHub n'était pas accessible depuis la session : la liste vient de l'inventaire des dépôts de la session
   (15 propres, 12 forks, 1 privé). Elle est figée dans `integrations/inventory.yaml`, avec le
   classement par pilier. Avec `gh auth login`, `scripts/inventory_github.py --from-gh alebgl77`
   reconstruit la liste depuis `gh repo list` ; un nouveau dépôt y apparaît « non classé » jusqu'à
   une revue humaine.
2. **Clonage complet** de chaque dépôt propre (toutes les branches, tout l'historique), lecture
   du README, du fichier de licence et des manifestes de dépendances (`package.json` et
   `package-lock.json`, `pyproject.toml`, `requirements*.txt`, `go.mod`, `Cargo.toml`).
3. **Classement par pilier** (`rag`, `evaluation`, `multi_agents`, `llmops_observability`,
   `governance_security`, sinon `none`) : décision humaine, motivée dans `integrations/inventory.yaml`.
4. **Licence du dépôt** : identification du texte du fichier `LICENSE` et contrôle de cohérence
   avec les déclarations (`package.json`, `pyproject.toml`, `CITATION.cff`).
5. **Licences des dépendances** : `package-lock.json` quand il existe, sinon l'API JSON de PyPI, le
   registre npm, pkg.go.dev ou crates.io. Chaque licence est évaluée par la politique de
   `licenses/third_party.yaml` (expressions SPDX `AND` / `OR` comprises). GPL, AGPL, LGPL, SSPL et
   licences inconnues sont signalées ; une dépendance **d'exécution** copyleft ou inconnue bloque
   l'intégration, une dépendance de développement est seulement signalée. Chemins exclus de
   l'analyse (justifiés dans `integrations/inventory.yaml`) : les recettes de reconstruction
   d'images tierces de open-shadow-ai (`requirements/service-builds/` : Prometheus, node_exporter
   et gosu, sous Apache-2.0) et le banc d'essai de tierdecay. Les fichiers de verrouillage
   `requirements/runtime.txt` et `requirements/agent.txt` de open-shadow-ai sont analysés en plus :
   les dépendances transitives déployées sont donc vérifiées elles aussi.
6. **Secrets** : gitleaks **8.30.1** (image `zricethezav/gitleaks:v8.30.1` épinglée, réseau
   coupé, dépôt monté en lecture seule) sur **tout l'historique git**, règles par défaut ; une
   éventuelle configuration gitleaks du dépôt analysé et les commentaires `gitleaks:allow` sont
   ignorés. Le rapport est masqué (`--redact`) puis supprimé après lecture : seuls la règle, le
   fichier, le commit abrégé et la ligne sont conservés. Chaque résultat est trié à la main dans
   `integrations/secrets-triage.yaml` ; tout résultat non trié bloque l'intégration.
7. **Décision** (calculée par le script) :
   - `integrated` : pilier couvert, licence permissive, aucune dépendance d'exécution copyleft ou
     inconnue, scan propre après tri ;
   - `to_relicense` : pilier couvert mais pas de licence, ou une licence que le titulaire doit changer ;
   - `excluded` : fork, dépôt privé, aucun pilier, secret restant ou dépendance copyleft.
8. **Intégration** : `git submodule add https://github.com/alebgl77/<dépôt>.git integrations/<dépôt>`,
   lien épinglé sur **le commit analysé**, puis génération de la fiche du catalogue. Rien n'est
   committé automatiquement.

## Inventaire complet (28 dépôts)

Tableau généré par `scripts/inventory_github.py --markdown docs/fr/GITHUB_INTEGRATION.md` (entre
les deux marqueurs ; `--markdown docs/GITHUB_INTEGRATION.md` pour la
[version anglaise](../GITHUB_INTEGRATION.md)).

<!-- INVENTORY_TABLE_START -->
| # | Dépôt | Type | Pilier | Licence | Dépendances | Secrets (gitleaks, historique complet) | Décision | Raison |
|---|---|---|---|---|---|---|---|---|
| 1 | [grafana-llmops-forge](https://github.com/alebgl77/grafana-llmops-forge) | propre | LLMOps et observabilité | MIT | aucune dépendance déclarée | 80 commits, 1 brut(s), 1 faux positif(s), 0 restant(s) | **intégré** | pilier LLMOps et observabilité, licence MIT, aucune dépendance d'exécution copyleft, scan propre |
| 2 | [openspanguard](https://github.com/alebgl77/openspanguard) | propre | Évaluation | Apache-2.0 | 10 vérifiées, aucune copyleft | 17 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | **intégré** | pilier Évaluation, licence Apache-2.0, aucune dépendance d'exécution copyleft, scan propre |
| 3 | [open-shadow-ai](https://github.com/alebgl77/open-shadow-ai) | propre | Gouvernance et sécurité | Apache-2.0 | 84 vérifiées ; copyleft faible : certifi (MPL-2.0, runtime) | 133 commits, 6 brut(s), 6 faux positif(s), 0 restant(s) | **intégré** | pilier Gouvernance et sécurité, licence Apache-2.0, aucune dépendance d'exécution copyleft, scan propre |
| 4 | [dsh-plugin-otel-genai](https://github.com/alebgl77/dsh-plugin-otel-genai) | propre | LLMOps et observabilité | MIT | 12 vérifiées, aucune copyleft | 14 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | **intégré** | pilier LLMOps et observabilité, licence MIT, aucune dépendance d'exécution copyleft, scan propre |
| 5 | [tierdecay](https://github.com/alebgl77/tierdecay) | propre | LLMOps et observabilité | MIT | aucune dépendance déclarée | 35 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | **intégré** | pilier LLMOps et observabilité, licence MIT, aucune dépendance d'exécution copyleft, scan propre |
| 6 | [harnessmeter](https://github.com/alebgl77/harnessmeter) | propre | LLMOps et observabilité | MIT | 2 vérifiées, aucune copyleft | 27 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | **intégré** | pilier LLMOps et observabilité, licence MIT, aucune dépendance d'exécution copyleft, scan propre |
| 7 | [promptor](https://github.com/alebgl77/promptor) | propre | Évaluation | MIT | aucune dépendance déclarée | 4 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | **intégré** | pilier Évaluation, licence MIT, aucune dépendance d'exécution copyleft, scan propre |
| 8 | [awesome-ai-architect](https://github.com/alebgl77/awesome-ai-architect) | propre | aucun | MIT | aucune dépendance déclarée | 47 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : liste de veille documentaire, pas un composant de l'usine (utile comme lien de veille du R&D Lab) |
| 9 | [generative-engine-monitor](https://github.com/alebgl77/generative-engine-monitor) | propre | aucun | MIT | 30 vérifiées ; copyleft faible : geist (OFL-1.1, runtime) | 38 commits, 1 brut(s), 1 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : mesure marketing (GEO) des réponses de fournisseurs, hors des piliers de l'usine |
| 10 | [ftp-deploy-mcp](https://github.com/alebgl77/ftp-deploy-mcp) | propre | aucun | MIT | 10 vérifiées, aucune copyleft | 65 commits, 10 brut(s), 9 faux positif(s), 1 restant(s) | exclu | aucun pilier de l'usine : outil de livraison de sites web, hors des piliers de l'usine ; en outre 1 secret(s) potentiel(s) non trié(s) |
| 11 | [design-md-viewer](https://github.com/alebgl77/design-md-viewer) | propre | aucun | MIT | 25 vérifiées, aucune copyleft | 27 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : outil de design, hors des piliers de l'usine |
| 12 | [pilovo](https://github.com/alebgl77/pilovo) | propre | aucun | MIT | 20 vérifiées ; copyleft faible : @axe-core/playwright (MPL-2.0, dev), @fontsource-variable/montserrat (OFL-1.1, dev) | 19 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : productivité individuelle dans le navigateur, hors des piliers de l'usine |
| 13 | [open-fullscreenshot](https://github.com/alebgl77/open-fullscreenshot) | propre | aucun | MIT | aucune dépendance déclarée | 14 commits, 0 brut(s), 0 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : outil de capture d'écran, hors des piliers de l'usine |
| 14 | [alebgl77](https://github.com/alebgl77/alebgl77) | propre | aucun | aucun fichier LICENSE | aucune dépendance déclarée | 34 commits, 1 brut(s), 1 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : page de profil, aucun code |
| 15 | [alebgl77.github.io](https://github.com/alebgl77/alebgl77.github.io) | propre | aucun | aucun fichier LICENSE | aucune dépendance déclarée | 5 commits, 1 brut(s), 1 faux positif(s), 0 restant(s) | exclu | aucun pilier de l'usine : site de présentation, aucun code applicatif |
| 16 | [openwiki](https://github.com/alebgl77/openwiki) | fork | aucun | MIT (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 17 | [semantic-conventions-genai](https://github.com/alebgl77/semantic-conventions-genai) | fork | aucun | Apache-2.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 18 | [awesome-agent-harness](https://github.com/alebgl77/awesome-agent-harness) | fork | aucun | aucun fichier LICENSE | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 19 | [Awesome-LLMOps](https://github.com/alebgl77/Awesome-LLMOps) | fork | aucun | CC0-1.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 20 | [agentshield](https://github.com/alebgl77/agentshield) | fork | aucun | MIT (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 21 | [swarms](https://github.com/alebgl77/swarms) | fork | aucun | Apache-2.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 22 | [plugin-tools](https://github.com/alebgl77/plugin-tools) | fork | aucun | Apache-2.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 23 | [LibreChat](https://github.com/alebgl77/LibreChat) | fork | aucun | MIT (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 24 | [awesome-observability](https://github.com/alebgl77/awesome-observability) | fork | aucun | CC0-1.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 25 | [awesome-ai-model-routing](https://github.com/alebgl77/awesome-ai-model-routing) | fork | aucun | Apache-2.0 (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 26 | [awesome-cli-coding-agents](https://github.com/alebgl77/awesome-cli-coding-agents) | fork | aucun | aucun fichier LICENSE | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 27 | [cal.diy](https://github.com/alebgl77/cal.diy) | fork | aucun | MIT (amont) | — | non scanné (fork) | exclu | fork d'un projet amont : pas un projet propre, relicence impossible (droits des auteurs amont) |
| 28 | ai-quote-engine | privé | aucun | non lue | — | non scanné (privé) | exclu | dépôt privé : hors périmètre d'une publication MIT |
<!-- INVENTORY_TABLE_END -->

## Dépôts intégrés

| Sous-module | Commit épinglé | Pilier | Licence | Rôle dans l'usine |
|---|---|---|---|---|
| `integrations/grafana-llmops-forge` | `491fcac` | LLMOps et observabilité | MIT | Génère et déploie des dashboards Grafana LLM (coûts par fournisseur, agents, qualité, preuves AI Act / ISO 42001 / NIST AI RMF) à partir de la télémétrie existante : complément des dashboards versionnés de `compose/grafana/dashboards/`. |
| `integrations/openspanguard` | `b1bcb3c` | Évaluation | Apache-2.0 | Évaluation en ligne attachée aux spans OTLP et couverture d'évaluation dans Prometheus : prolonge les gates d'évaluation (RAGAS, LLM-as-judge) après le déploiement. |
| `integrations/open-shadow-ai` | `6aa329a` | Gouvernance et sécurité | Apache-2.0 | Découverte des usages d'IA non déclarés (réseau, postes, annuaire) pour compléter le registre des use cases. |
| `integrations/dsh-plugin-otel-genai` | `9610c0c` | LLMOps et observabilité | MIT | Métriques OpenTelemetry GenAI (tokens, durée) d'un harnais d'agents, exportées en OTLP vers Alloy ou Prometheus. |
| `integrations/tierdecay` | `b836dad` | LLMOps et observabilité | MIT | Certification statistique de tiers de modèles moins chers et recertification à chaque changement de modèle : alimente l'action « changement de routage » de la boucle de données et l'alerte « modèle surdimensionné », sans réentraînement. |
| `integrations/harnessmeter` | `ebad8f3` | LLMOps et observabilité | MIT | Empreinte en tokens des instructions et skills des agents, budgets en CI : tokens par tâche et règle « prompt_bloat ». |
| `integrations/promptor` | `75d5a7d` | Évaluation | MIT | Conception de prompts jugée sur les sorties (essai obligatoire, tournoi à l'aveugle de type LLM-as-judge), en amont du versionnage des prompts. |

L'intégration ne lance aucun de ces projets : la stack `docker compose` ne construit ni
n'exécute les sous-modules. Ils apparaissent dans le **catalogue en libre-service**
(`GET /api/v1/catalog`, clé `repos`, console « Catalogue » du dashboard 3D) avec leur pilier,
leur licence, l'état du scan et le commit épinglé. Chaque équipe décide ensuite de les déployer.

Remarques par dépôt intégré (reprises dans le champ `notes` des fiches) :

- **open-shadow-ai** : son propre déploiement construit Redis 7.4.11 (licences RSALv2 / SSPLv1,
  non libres) comme service séparé. L'usine ne déploie pas ce service ; côté projet, Valkey
  (BSD-3-Clause) est recommandé, comme dans la stack de l'usine. `certifi` (MPL-2.0, copyleft
  faible au niveau du fichier) figure dans ses dépendances transitives, utilisé sans modification.
  L'outil de documentation `tools/motion` utilise `imageio-ffmpeg` (BSD-2-Clause), qui embarque un
  exécutable FFmpeg dont la licence (LGPL ou GPL) dépend de sa compilation : outil de
  développement jamais installé dans une image d'exécution, à revérifier s'il devait être
  redistribué.
- **openspanguard** : sa stack de démonstration (`compose.yaml`) lance Grafana et Tempo
  (AGPL-3.0) comme services séparés non modifiés ; l'usine ne la déploie pas.
- **dsh-plugin-otel-genai** : dépendances pairs `@deepseek-ai/cordis` et `@deepseek-ai/schemastery`
  (MIT), fournies par le harnais hôte.

## Résultats du scan de secrets

559 commits analysés sur les 15 dépôts propres ; aucune valeur n'est reproduite ci-dessous.

| Dépôt | Fichier | Règle | Commit | Ligne | Verdict |
|---|---|---|---|---|---|
| grafana-llmops-forge | `tests/audit_harness.py` | generic-api-key | a3e813a | 521 | faux positif : expression régulière de détection de jetons dans le harnais d'audit |
| open-shadow-ai | `tests/fixtures/component-scanner/node-exporter-amd64.json` | generic-api-key | bf9f571, cced76d | 4243 | faux positif : empreinte `go.sum` (h1:) d'un module public |
| open-shadow-ai | `tests/fixtures/component-scanner/node-exporter-arm64.json` | generic-api-key | bf9f571, cced76d | 4243 | faux positif : empreinte `go.sum` (h1:) d'un module public |
| open-shadow-ai | `tests/fixtures/component-scanner/node-exporter-current-amd64.json` | generic-api-key | bf9f571, cced76d | 4242 | faux positif : empreinte `go.sum` (h1:) d'un module public |
| generative-engine-monitor | `src/lib/env.ts` | generic-api-key | 105e5d0 | 21 | faux positif : valeur de développement documentée, refusée en production par le code |
| ftp-deploy-mcp | `test/durable/engine.test.mjs` | generic-api-key | ba55d6b | 216, 505, 509, 523, 529, 615, 624, 685 | faux positifs : clés d'idempotence de test |
| ftp-deploy-mcp | `test/redaction-performance.test.mjs` | private-key | ffd19a0 | 122 | faux positif : en-têtes PEM factices d'un test de masquage |
| ftp-deploy-mcp | `test/fixtures/transport/localhost-key.pem` | private-key | 24b8730 | 1 | **restant** : vraie clé privée RSA de test (certificat auto-signé `localhost`) |
| alebgl77 | `README.md` | generic-api-key | 237d8c3 | 28 | faux positif : prose du README de profil |
| alebgl77.github.io | `index.html` | generic-api-key | 3468db7 | 82 | faux positif : texte HTML de présentation |

Les 7 dépôts intégrés sont propres après tri. Le seul résultat restant concerne **ftp-deploy-mcp**,
exclu par ailleurs (hors piliers). La clé ne protège aucun service réel, mais une clé privée
versionnée reste un risque de réutilisation et de faux positifs en chaîne. Action conseillée :
générer la paire clé/certificat pendant les tests (par exemple `openssl req -x509 -newkey rsa:2048
-nodes -subj /CN=localhost` dans le setup), supprimer le fichier, puis le retirer de l'historique
(`git filter-repo --path test/fixtures/transport/localhost-key.pem --invert-paths`) avant de
forcer la mise à jour du dépôt.

## Dépôts à relicencier en MIT

**Obligatoire : aucun.** Les 7 dépôts qui couvrent un pilier sont sous MIT ou Apache-2.0, deux
licences permissives compatibles avec un dépôt MIT qui les référence comme sous-modules.

**Facultatif, pour une usine 100 % MIT** : `open-shadow-ai` et `openspanguard` (Apache-2.0).
L'Apache-2.0 apporte une licence explicite de brevets et un mécanisme `NOTICE` que la MIT n'a
pas : relicencier n'est utile que si l'uniformité MIT est une exigence. L'historique git ne montre
aucun contributeur humain tiers (seulement vos propres commits et Dependabot) : vous pouvez donc décider seul. Étapes exactes, pour chacun des deux dépôts :

1. Vérifier les auteurs : `git log --format='%an <%ae>' | sort -u`. Si un contributeur tiers
   apparaît, obtenir son accord écrit ou réécrire sa contribution.
2. Remplacer `LICENSE` par le texte MIT : `Copyright (c) 2026 Alexandre Beguel`.
3. Mettre à jour les déclarations : `"license": "MIT"` dans `package.json` (et
   `frontend/package.json` pour open-shadow-ai), `license = "MIT"` dans `pyproject.toml` et
   `agent/pyproject.toml`, `license: MIT` dans `CITATION.cff`, badges du README, en-têtes
   `SPDX-License-Identifier` (`git grep -n "Apache-2.0"` pour les trouver).
4. Conserver les fichiers de licence et `NOTICE` des **tiers** (par exemple
   `requirements/service-builds/*/LICENSE` et `NOTICE` dans open-shadow-ai) : ils concernent le
   code amont, pas le vôtre.
5. Ajouter au `CHANGELOG` : « Relicencié d'Apache-2.0 en MIT à partir de vX.Y.Z ; les versions
   antérieures restent disponibles sous Apache-2.0 » (une relicence n'est pas rétroactive).
6. Publier une version, puis dans ce dépôt :
   `git -C integrations/<dépôt> fetch && git -C integrations/<dépôt> checkout <tag>`,
   `python scripts/inventory_github.py --only <dépôt> --write-cards` (relance le scan) et
   `python scripts/check_licenses.py`.

**Hors piliers, sans licence** : `alebgl77` (README de profil) et `alebgl77.github.io` (site de
profil). Rien n'est requis pour l'usine. Si vous souhaitez autoriser leur réutilisation, ajoutez un
fichier `LICENSE` (GitHub : *Add file → Create new file → LICENSE → Choose a license template*) ;
pour du texte et des pages de présentation, CC-BY-4.0 convient mieux que la MIT.

**Hygiène des mentions (pas une relicence)** :

- titulaire générique dans le texte MIT : « contributors » (grafana-llmops-forge, ftp-deploy-mcp),
  « TierDecay contributors », « Briefoak contributors » (pilovo, ancien nom ?). Préférer
  `Copyright (c) 2026 Alexandre Beguel and contributors` ;
- champ `license` absent : `pyproject.toml`, `agent/pyproject.toml` et `frontend/package.json` de
  open-shadow-ai, `package.json` de generative-engine-monitor ;
- versions `latest` non épinglées dans `package.json` de pilovo (hors piliers).

## Forks et dépôt privé

Les 12 forks sont des copies de projets amont : ils ne sont ni intégrés ni relicenciables (seuls
leurs auteurs amont détiennent les droits). La licence indiquée dans l'inventaire est celle du
fichier présent dans le fork. Deux forks n'ont pas de fichier de licence à la racine
(awesome-agent-harness, awesome-cli-coding-agents) ou en ont une non logicielle (CC0-1.0
pour Awesome-LLMOps et awesome-observability) : aucune réutilisation de leur contenu n'est
prévue. Pour contribuer à un projet amont, le passage par une *pull request* vers ce projet reste
la seule voie. Le dépôt privé `ai-quote-engine` est hors périmètre d'une publication MIT.

## Couverture des piliers et lacunes

| Pilier | Dépôts intégrés | Constat |
|---|---|---|
| RAG | aucun | Aucun projet RAG public. Le pipeline RAG de l'usine repose sur Qdrant et l'API (`engines/pipeline.py`). Lacune à combler : un dépôt MIT de référence (ingestion, découpage, indexation, jeux d'évaluation RAGAS). |
| Évaluation | openspanguard, promptor | Évaluation en ligne et conception de prompts jugée sur les sorties ; pas de banc RAGAS dédié. |
| Multi-agents | aucun | Aucun projet multi-agents intégré ; lacune à combler : un dépôt MIT de référence multi-agents LangGraph. |
| LLMOps et observabilité | grafana-llmops-forge, dsh-plugin-otel-genai, tierdecay, harnessmeter | Pilier le mieux couvert : dashboards, métriques GenAI, routage par coût, budgets de tokens. |
| Gouvernance et sécurité | open-shadow-ai | Découverte de l'IA fantôme ; la gouvernance AI Act / NIST / ISO 42001 de l'usine est dans `config/frameworks/`. |

## Reproduire et mettre à jour

```bash
# Récupérer les sous-modules après un clone
git submodule update --init

# Inventaire complet : clone, licences, dépendances, gitleaks, fiches, tableau de ce document
python scripts/inventory_github.py --report /tmp/inventory.json \
  --markdown docs/fr/GITHUB_INTEGRATION.md --write-cards --add-submodules

# Avec gh authentifié, liste construite depuis GitHub
gh auth login && python scripts/inventory_github.py --from-gh alebgl77 --report /tmp/inventory.json

# Auto-test des règles de décision (sans réseau) puis contrôle des licences de tout le dépôt
python scripts/inventory_github.py --self-test
python scripts/check_licenses.py
```

Monter la version d'un sous-module : `git -C integrations/<dépôt> fetch && git -C
integrations/<dépôt> checkout <commit>`, puis relancer l'inventaire avec `--only <dépôt>` :
un nouveau commit doit être **rescanné** avant d'être committé (la fiche affiche le commit
analysé dans `commit`). Retirer un dépôt : `git submodule deinit -f integrations/<dépôt>`,
`git rm -f integrations/<dépôt>`, `rm -rf .git/modules/integrations/<dépôt>`, puis supprimer sa
fiche. Corriger un classement : modifier `integrations/inventory.yaml` et relancer le script (les
fiches sont générées, ne pas les modifier à la main).
