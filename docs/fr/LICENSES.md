# Licences et compatibilité

*[English version](../LICENSES.md)*

Ce dépôt est publié sous **licence MIT** ([LICENSE](../../LICENSE)). Ce document explique pourquoi chaque
composant tiers est compatible avec ce choix, ce qui est vérifié automatiquement et ce qui
changerait si quelqu'un modifiait un service sous AGPL. Il ne constitue pas un avis juridique.

L'inventaire de référence est `licenses/third_party.yaml` (images, code intégré au build,
outils, politique) ; le contrôle est `scripts/check_licenses.py` (job CI « Licences »).

## Principes

1. **Tout le code écrit pour l'usine est MIT** : API d'agrégation, dashboard 3D, simulateurs,
   callback LiteLLM, scripts, configuration et dashboards Grafana JSON.
2. **Dépendances liées au code** (bibliothèques Python de `api/` et `sim/`) : licences
   permissives uniquement. Seule exception admise : MPL-2.0 (copyleft faible au niveau du
   fichier) pour `certifi`, utilisé sans modification.
3. **Services tiers** : chaque service tourne dans **son propre conteneur**, à partir de
   **l'image officielle non modifiée**, et ne communique avec l'usine que par le réseau (HTTP,
   gRPC, protocole SQL). Aucun code de ces services n'est copié, lié ou recompilé dans ce dépôt.
4. **AGPL-3.0 (Grafana OSS, Loki)** : admise **uniquement** pour un service séparé non modifié.
   Le navigateur ne parle jamais à Grafana ; l'API l'interroge côté serveur, par son API HTTP.
5. **Code intégré au build** : Three.js (MIT), récupéré et vérifié au moment de la construction
   de l'image du dashboard, jamais committé.
6. **Projets externes** : sous-modules git sous leur propre licence (MIT ou Apache-2.0), jamais
   copiés (voir [GITHUB_INTEGRATION.md](GITHUB_INTEGRATION.md)).

## Matrice de compatibilité

| Licence | Composants de l'usine | Mode d'utilisation | Compatible avec un dépôt MIT | Obligations pour l'usine |
|---|---|---|---|---|
| MIT, BSD-2/3-Clause, ISC, PSF-2.0, PostgreSQL | FastAPI, Pydantic, PyYAML, httpx, Uvicorn, Starlette, typing-extensions, LiteLLM (cœur), Langfuse (cœur), Ollama, nginx, PostgreSQL, Valkey, Three.js | bibliothèques liées, images, code intégré au build | oui | conserver les mentions de copyright et de licence (Three.js : `LICENSE` copié dans `/vendor/three` et en-tête `@license` des fichiers) |
| Apache-2.0 | prometheus-client, opentelemetry-api, tzdata, Caddy, Prometheus, Alloy, Qdrant, ClickHouse, SeaweedFS, vLLM, dcgm-exporter, sous-modules open-shadow-ai et openspanguard | bibliothèques liées, images, sous-modules | oui | conserver `LICENSE` et `NOTICE` en cas de redistribution ; signaler les fichiers modifiés (aucun ici) |
| MPL-2.0 | `certifi` (dépendance de httpx) | bibliothèque liée, **non modifiée** | oui | si un fichier MPL était modifié, publier ce fichier modifié sous MPL-2.0 |
| AGPL-3.0 | Grafana OSS 13.0.2, Loki 3.7.9 | **service séparé**, image officielle non modifiée, appels réseau | oui, sans effet sur la licence du dépôt | indiquer la licence et la source (fait dans `licenses/third_party.yaml`) ; en cas de modification, voir plus bas |
| GPL-2.0 / GPL-3.0 / LGPL | paquets des images de base (BusyBox, Bash, coreutils, glibc…) | agrégat dans les images, aucun lien avec notre code | oui (simple agrégat) | si vous **publiez** des images construites, fournir l'accès aux sources de ces paquets (archives sources Debian / Alpine) |
| Licence MIT avec répertoires commerciaux | LiteLLM (`enterprise/`), Langfuse (répertoires `ee`) | service séparé, **sans clé de licence** | oui | ne pas définir `LITELLM_LICENSE` ni `LANGFUSE_EE_LICENSE_KEY` : les fonctions commerciales restent inactives |
| Licences propriétaires NVIDIA (CUDA) | bibliothèques des images `vllm/vllm-openai` et `nvidia/dcgm-exporter` (profil `local-gpu`) | images officielles tirées par l'utilisateur, non redistribuées | oui | accepter les conditions NVIDIA sur la machine GPU ; ne pas republier ces images |
| SSPL, RSAL, BUSL, Elastic, Commons Clause | aucun (refusées) | — | non retenues | Redis ≥ 7.4 remplacé par **Valkey** (BSD-3-Clause) ; MinIO (AGPL) remplacé par **SeaweedFS** (Apache-2.0) |

## Pourquoi Grafana et Loki (AGPL-3.0) ne « contaminent » pas le dépôt

- **Ce que fait le dépôt** : `docker-compose.yml` référence les images officielles
  `grafana/grafana-oss:13.0.2` et `grafana/loki:3.7.9`. Il leur fournit de la configuration
  (variables d'environnement, fichiers de provisioning, dashboards JSON versionnés dans
  `compose/grafana/dashboards/`). L'API d'agrégation appelle l'API HTTP de Grafana avec le jeton
  d'un compte de service *Viewer*, côté serveur ; Alloy pousse les journaux vers Loki par HTTP.
  L'installation de plugins est désactivée (`GF_PLUGINS_PREINSTALL_DISABLED`).
- **Ce que le dépôt ne fait pas** : il ne copie, ne modifie, ne recompile ni ne lie aucun code de
  Grafana ou de Loki ; il ne publie pas d'image dérivée.
- **Raisonnement** : le copyleft de l'AGPL s'applique aux œuvres fondées sur le programme. Des
  programmes distincts qui communiquent par le réseau, chacun dans son processus et son
  conteneur, forment un **agrégat**, pas une œuvre combinée. Les dashboards JSON et fichiers de
  provisioning sont des données d'entrée que Grafana lit : ce sont nos œuvres, sous MIT.
- **Obligation qui reste** : la clause réseau de l'AGPL (article 13) ne vise que les versions
  **modifiées**. Avec l'image officielle non modifiée, le code source correspondant est celui
  publié par Grafana Labs (`source` dans `licenses/third_party.yaml`).

## Ce qui change si quelqu'un modifie Grafana (ou Loki)

Est une modification : un patch du code source, une image reconstruite avec un code ou une
interface modifiés, un plugin compilé dans le binaire, un *fork* de l'interface. N'en sont pas :
la configuration par variables d'environnement, les fichiers de provisioning, les dashboards
JSON, un plugin officiel installé tel quel (qui garde sa propre licence, à vérifier avant ajout).

En cas de modification :

1. la version modifiée doit être distribuée sous **AGPL-3.0** ;
2. **article 13** : tous les utilisateurs qui interagissent avec elle par le réseau doivent se
   voir proposer son **code source correspondant** (par exemple un lien visible dans
   l'interface vers le dépôt du *fork*) ;
3. la modification doit vivre dans **un dépôt séparé sous AGPL**, jamais dans ce dépôt MIT : ce
   dépôt peut continuer à orchestrer le service modifié comme conteneur séparé ;
4. dans `licenses/third_party.yaml`, passer l'entrée à `modified: true` et pointer `source` vers
   le *fork* : `check_licenses.py` échoue tant que ces conditions ne sont pas revues (une entrée
   AGPL modifiée est une violation de la politique par défaut) ;
5. conserver les marques Grafana sans les détourner (ne pas présenter le *fork* comme un
   produit Grafana Labs).

Grafana Enterprise est distribué sous une autre licence (propriétaire) : l'usine utilise
`grafana/grafana-oss`.

## Three.js (MIT), intégré au moment de la construction

`dashboard/Dockerfile` télécharge `three@0.186.1` avec `npm pack`, vérifie son intégrité SRI
(sha512 épinglé), copie les seuls fichiers utilisés et le fichier `LICENSE` dans
`/vendor/three`, puis réécrit l'*import map* de `index.html` vers cette copie locale. Rien n'est
committé (`dashboard/vendor/` est ignoré par git). En mode `file://`, le navigateur charge les
mêmes fichiers depuis le CDN jsDelivr. `check_licenses.py` vérifie que la version du Dockerfile
correspond à l'inventaire.

## Dépendances Python des services écrits pour l'usine

Relevé du 2026-11-03 (`python scripts/check_licenses.py` donne l'état courant) :

| Portée | Paquets | Licences |
|---|---|---|
| exécution (`api/`, `sim/`) | fastapi, pydantic, pydantic-core, PyYAML, anyio, h11, annotated-doc, annotated-types, typing-inspection | MIT |
| exécution | uvicorn, httpx, httpcore, starlette, click, idna | BSD-3-Clause |
| exécution | tzdata, opentelemetry-api | Apache-2.0 |
| exécution | prometheus-client | Apache-2.0 AND BSD-2-Clause |
| exécution | typing-extensions | PSF-2.0 |
| exécution | certifi | MPL-2.0 (OK sous condition : non modifié) |
| tests (`api/requirements-dev.txt`) | pytest, pluggy, iniconfig | MIT |
| tests | packaging | Apache-2.0 OR BSD-2-Clause |
| tests | Pygments | BSD-2-Clause |

## Pack visuel et outillage d'images

- **Images générées** (`dashboard/assets/`, facultatives) : produites avec ChatGPT, par
  l'auteur pour ce dépôt (ou par vous si vous régénérez le pack). **Par décision de l'auteur, les
  images du pack visuel sont publiées sous la licence MIT du dépôt**, comme le code. Chaque image
  est tracée dans `dashboard/assets/PROVENANCE.md` avec l'empreinte **SHA-256** de son fichier
  source, le générateur et la date. Selon les conditions d'utilisation d'OpenAI, l'utilisateur
  est propriétaire des images générées et peut les publier (constat tiré de sources secondaires,
  à vérifier dans les conditions en vigueur). Aucune personne, marque ni œuvre existante n'est
  demandée dans les prompts ([ASSETS.md](ASSETS.md)). La protection par le droit
  d'auteur d'une image entièrement générée n'est pas garantie selon les pays : cela ne gêne pas
  la redistribution.
- **Pillow** (`scripts/requirements-assets.txt`, licence MIT-CMU, permissive) : utilisé
  uniquement par `scripts/build_assets.py` au moment de la construction du pack ; il n'est pas
  embarqué dans les images de service.

## Sous-modules (`integrations/`)

| Sous-module | Licence | Commit épinglé |
|---|---|---|
| grafana-llmops-forge, dsh-plugin-otel-genai, tierdecay, harnessmeter, promptor | MIT | voir `git submodule status` |
| open-shadow-ai, openspanguard | Apache-2.0 | voir `git submodule status` |

Un sous-module est une **référence** (un commit d'un autre dépôt) : son code ne fait pas partie
de l'arbre de ce dépôt et se récupère depuis le dépôt d'origine, sous sa propre licence. Si vous
redistribuez une archive qui inclut les sous-modules, gardez leurs fichiers `LICENSE` et `NOTICE`.
Les forks ne sont jamais intégrés.

## Vérification automatisée

`python scripts/check_licenses.py` affiche un tableau et sort en **erreur (code 1)** si :

- la licence du dépôt n'est pas MIT ;
- une dépendance Python de `api/`, `sim/*` ou `compose/*` n'est pas permissive (MPL-2.0 tolérée
  sous condition) ou a une licence inconnue. Sources, dans l'ordre : métadonnées installées
  (`api/.venv` ou l'interpréteur courant), PyPI, licence déclarée en commentaire dans le fichier
  requirements ; `python_overrides` de l'inventaire pour un cas vérifié à la main ;
- une image de `docker-compose.yml` ou un `FROM` d'un Dockerfile du dépôt n'est pas inventorié
  dans `licenses/third_party.yaml`, n'a pas d'URL de source ou d'usage valide ;
- une image AGPL n'est pas un `separate-service` non modifié ; une licence refusée apparaît ;
- la version de Three.js du Dockerfile diffère de l'inventaire ;
- un sous-module n'a pas de fiche, ou sa fiche n'est pas `integrated`, ou son URL, sa licence
  (fichier `LICENSE` lu quand le sous-module est extrait), son scan de secrets ou ses dépendances
  copyleft sont en défaut.

Options : `--offline` (aucune requête PyPI), `--json`, `--strict` (les alertes comptent comme
des violations, par exemple un sous-module non extrait ou un tag d'image différent de
l'inventaire), `--self-test` (tests intégrés du moteur de politique).

**Ajouter un composant** : inscrire l'image (SPDX, usage, `modified`, source) dans
`licenses/third_party.yaml`, ou la dépendance dans le fichier requirements avec sa licence en
commentaire, puis lancer le script. Pour un dépôt GitHub externe, passer par
`scripts/inventory_github.py` (licence, dépendances, secrets, fiche et sous-module).
