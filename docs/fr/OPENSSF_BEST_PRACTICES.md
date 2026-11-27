# Dossier de candidature OpenSSF Best Practices

[English](../OPENSSF_BEST_PRACTICES.md) · **Français**

Préparé le 2026-11-27 à partir du commit `49ae650` de `main` et du changelog 1.2.0 ; ce changement prépare la 1.2.1. Ce dossier recense des preuves, sans attribuer de badge ni déclarer tous les critères satisfaits. Aucune candidature publique n'a été trouvée dans la [recherche de projets](https://www.bestpractices.dev/en/projects.json?url=https%3A%2F%2Fgithub.com%2Falebgl77%2Fai-factory) à cette date. La preuve de fuzzing instrumenté reste en attente d'une exécution réussie du workflow sur le commit fusionné.

Utiliser les [critères officiels passing](https://www.bestpractices.dev/en/criteria/0) et leurs [détails d'applicabilité](https://www.bestpractices.dev/en/criteria/0?details=true), vérifiés le 2026-11-27. **E** indique une preuve dans le dépôt ; **O**, une vérification ou attestation du responsable ; **G**, une lacune non résolue. Ces étiquettes ne sont pas les réponses du formulaire. N/A exige que le critère l'autorise et une justification factuelle.

## Critères obligatoires : 43 identifiants

| Identifiant du critère | État des preuves | Preuve ou action restante |
|---|---|---|
| `description_good` | E | Le [README](../../README.fr.md) décrit le jumeau numérique, la démo et le périmètre des pilotes privés. |
| `interact` | E | [README](../../README.fr.md), [guide de contribution](../../CONTRIBUTING.md), [issues](https://github.com/alebgl77/ai-factory/issues). |
| `contribution` | E | [CONTRIBUTING](../../CONTRIBUTING.md) décrit les pull requests ciblées et les discussions de conception. |
| `floss_license` | E | [Licence MIT](../../LICENSE) ; périmètre des tiers dans [LICENSES](LICENSES.md). |
| `license_location` | E | [LICENSE](../../LICENSE) à la racine. |
| `documentation_basics` | E | [README](../../README.fr.md), [index documentaire](README.md), [guide de déploiement](../DEPLOYMENT.md). |
| `documentation_interface` | E | [Contrat API](../CONTRACT.md), [README API](../../api/README.fr.md), `/api/v1/docs` généré. |
| `sites_https` | E | [Dépôt](https://github.com/alebgl77/ai-factory), [démo](https://alebgl77.github.io/ai-factory/), [releases](https://github.com/alebgl77/ai-factory/releases) en HTTPS. |
| `discussion` | E | [Issues](https://github.com/alebgl77/ai-factory/issues) et [discussions](https://github.com/alebgl77/ai-factory/discussions) publiques, consultables et recherchables dans le navigateur. |
| `maintained` | E | `git log` inclut `49ae650` du 2026-11-27 ; réévaluer l'activité au moment de la candidature. |
| `repo_public` | E | [Sources publiques](https://github.com/alebgl77/ai-factory). |
| `repo_track` | E | L'historique Git conserve auteurs, dates et différences. |
| `repo_interim` | E | L'[historique](https://github.com/alebgl77/ai-factory/commits/main/) et les pull requests conservent les changements entre releases. |
| `version_unique` | E | [CHANGELOG](../../CHANGELOG.md) et [workflow de release](../../.github/workflows/release.yml) utilisent des tags versionnés. |
| `release_notes` | E | Le [CHANGELOG](../../CHANGELOG.md) décrit les changements et prérequis de mise à jour ; le workflow de release l'utilise. |
| `release_notes_vulns` | O | Le changelog liste des CVE de dépendances. Vérifier séparément les avis du code du projet ; N/A seulement en l'absence de vulnérabilité du projet concernée. |
| `report_process` | E | [Guide de contribution](../../CONTRIBUTING.md) et [formulaire de bug](../../.github/ISSUE_TEMPLATE/bug_report.yml). |
| `report_responses` | O | Examiner les signalements des 2 à 12 derniers mois et leurs accusés de réception ; conserver nombres et liens. |
| `report_archive` | E | [Archive des issues](https://github.com/alebgl77/ai-factory/issues?q=is%3Aissue). |
| `vulnerability_report_process` | E | [Politique de sécurité](../../SECURITY.md#signaler-une-vulnérabilité). |
| `vulnerability_report_private` | E | La politique renvoie au [formulaire privé](https://github.com/alebgl77/ai-factory/security/advisories/new) ; le responsable a activé le réglage du dépôt puis vérifié `enabled: true` par l'API le 2026-11-27. |
| `vulnerability_report_response` | O | Vérifier chaque première réponse sur six mois par rapport au délai de 14 jours ; objectif annoncé de 72 heures. N/A seulement sans signalement reçu. |
| `build` | E | [Dockerfile API](../../api/Dockerfile), [Dockerfile dashboard](../../dashboard/Dockerfile), [Compose](../../docker-compose.yml), [CI](../../.github/workflows/ci.yml). |
| `test` | E | Suites publiques pytest et navigateur ; commandes dans [CONTRIBUTING](../../CONTRIBUTING.md) et la CI. |
| `test_policy` | E | Le guide de contribution exige explicitement des tests automatisés pour toute fonctionnalité majeure. |
| `tests_are_added` | E | `49ae650` ajoute des [tests d'images de release](../../tests/deployment/test_release_image.py), d'[identifiants de passerelle](../../api/tests/test_gateway_credentials.py) et Kubernetes avec les changements 1.2.0. |
| `warnings` | O | La CI lance actionlint/ShellCheck et Helm lint. Confirmer une couverture suffisante des langages applicatifs avant de répondre Met. |
| `warnings_fixed` | O | Examiner les résultats actuels et les faux positifs documentés ; la configuration seule ne prouve aucun résultat sans avertissement. |
| `know_secure_design` | O | Un développeur principal doit évaluer personnellement les connaissances de conception détaillées par le critère ; code et scanners ne les attestent pas. |
| `know_common_errors` | O | Un développeur principal doit attester des classes de vulnérabilités pertinentes et de leurs parades d'après ses propres connaissances. |
| `crypto_published` | O | [Architecture de sécurité](SECURITY.md), SHA-256/HMAC dans l'[audit](../../api/app/audit.py) et les [approbations](../../api/ai_factory_workbench/approvals.py) ; examiner tous les protocoles et dépendances cryptographiques. |
| `crypto_floss` | O | [Inventaire des licences](../../licenses/third_party.yaml) et [contrôle](../../scripts/check_licenses.py) pour l'analyse ; vérifier une implémentation FLOSS pour chaque fonction cryptographique. |
| `crypto_keylength` | O | Vérifier TLS, clés de signature, secrets HMAC et configurations autorisées face aux minimums officiels ; le nom d'un algorithme ne suffit pas. |
| `crypto_working` | O | Examiner les valeurs par défaut et dépendances pour les algorithmes cassés ou modes inadaptés ; justifier toute exception de protocole. |
| `crypto_password_storage` | O | L'[authentification basic au bord](../../compose/caddy/Caddyfile) utilise bcrypt ; examiner tous les stockages de mots de passe utilisateurs, services configurés inclus. Les identifiants d'API sortants sont un autre cas. |
| `crypto_random` | O | La [préparation d'environnement](../../.devcontainer/setup-env.sh) utilise `openssl rand` ; vérifier toute génération de clés/nonces, bibliothèques et services inclus. |
| `delivery_mitm` | E | Sources/releases en HTTPS et [vérification des signatures](../../SECURITY.md#vérifier-une-release). |
| `delivery_unsigned` | O | Sommes signées et [script de vérification](../../.github/scripts/verify_image.sh) à l'appui ; examiner chaque téléchargement pour les sommes récupérées sans sécurité. |
| `vulnerabilities_fixed_60_days` | O | La politique vise désormais 60 jours depuis la divulgation publique pour les sévérités moyennes et supérieures. Vérifier correctifs réels et résultats ouverts ; les barrières HIGH/CRITICAL corrigeables ne le démontrent pas. |
| `no_leaked_credentials` | O | Le [scanner de secrets](../../scripts/scan_secrets.sh) contrôle fichiers et historique Git en CI ; examiner les résultats et confirmer la révocation de tout identifiant réel exposé. |
| `static_analysis` | O | [CodeQL](../../.github/workflows/codeql.yml), zizmor et [précontrôle de release](../../.github/workflows/release.yml) existent ; joindre les exécutions réussies du commit exact avant toute déclaration. |
| `static_analysis_fixed` | O | Examiner les vulnérabilités exploitables confirmées, moyennes ou supérieures, et les correctifs datés ; une exclusion ou exception amont ne prouve pas une correction. |
| `dynamic_analysis_fixed` | O | Examiner les résultats des tests/fuzzers et dates de correction ; documenter l'applicabilité suivant les détails officiels. Preuves CI du nouveau fuzzing en attente. |

## Réponses SHOULD et SUGGESTED

Une réponse SHOULD non satisfaite demande une justification ; les éléments SUGGESTED sont facultatifs pour passing. Ne pas les considérer implicitement comme obligatoires ou N/A.

| Niveau et identifiants exacts | Preuve ou action restante |
|---|---|
| SHOULD : `contribution_requirements`, `english`, `report_tracker`, `test_invocation` | Règles de contribution, documentation anglaise, issues et `python -m pytest` documentés. |
| SHOULD : `enhancement_responses` | Le responsable vérifie les réponses à plus de la moitié des demandes d'amélioration des 2 à 12 derniers mois. |
| SHOULD : `build_floss_tools` | Examiner la construction par défaut et l'outillage GPU optionnel ; distinguer outils et services externes. |
| SHOULD : `crypto_call`, `crypto_weaknesses`, `crypto_pfs` | Bibliothèques cryptographiques standard utilisées ; le responsable examine les valeurs par défaut et échanges de clés entre services. Aucun N/A global. |
| SHOULD : `vulnerabilities_critical_fixed` | Le responsable vérifie les dates de signalement critique et de correctif. |
| SUGGESTED : `floss_license_osi`, `repo_distributed`, `version_semver`, `version_tags` | MIT, Git et tags versionnés constituent des preuves. |
| SUGGESTED : `build_common_tools`, `test_continuous_integration`, `tests_documented_added` | Docker/setuptools, CI et politique de tests des contributions constituent des preuves. |
| SUGGESTED : `test_most`, `warnings_strict` | Aucun taux global de couverture des branches ni niveau maximal d'avertissements n'est établi ici. |
| SUGGESTED : `static_analysis_common_vulnerabilities`, `static_analysis_often` | CodeQL utilise `security-extended` sur push/PR, avec une exécution hebdomadaire. |
| SUGGESTED : `dynamic_analysis` | [Cibles et commandes Atheris](../../tests/fuzz/README.md), [workflow de fuzzing](../../.github/workflows/fuzzing.yml) : preuve CI réussie du commit fusionné en attente. Les tests ordinaires seuls exigent au moins 80 % de couverture des branches selon les détails officiels. |
| SUGGESTED : `dynamic_analysis_unsafe` | N/A possible pour le code Python/JavaScript/shell du projet ; confirmer l'absence de code produit dans un langage sans sûreté mémoire. |
| SUGGESTED : `dynamic_analysis_enable_assertions` | Tests pytest et navigateur avec assertions ; conserver les exécutions réussies et examiner les invariants du fuzzer. |

## Actualiser les preuves

Depuis la racine du dépôt ; la commande API change de répertoire. Voir la CI pour les prérequis navigateur, images et intégrations. Ces commandes sont reproductibles, sans déclarer que chaque contrôle a été exécuté pour ce dossier.

```sh
git log -5 --format='%h %ad %s' --date=short
python -m pip install --require-hashes -r api/requirements-dev.txt -r api/requirements-mcp.txt
(cd api && python -m pytest -q)
python -m pytest -q tests/deployment/test_release_image.py tests/deployment/test_release_registry.py
python scripts/check_licenses.py --offline
scripts/scan_secrets.sh . --git
```

Pour le fuzzing instrumenté sous Linux/Python 3.12, suivre les commandes exactes d'installation et d'exécution du [guide de fuzzing](../../tests/fuzz/README.md) : chaque cible est limitée à 25 000 entrées ou 90 secondes, avec assertions, délai de cinq secondes par entrée et plafond de 1 Gio par processus. Conserver l'URL de l'exécution `Fuzzing (security boundaries)` du commit relu.

## Étapes du responsable avant candidature publique

1. Publier le changement relu, puis conserver le commit exact et les URL des exécutions CI, sécurité, CodeQL et fuzzing réussies. Lier séparément la release signée obtenue ; une entrée de changelog ne prouve pas sa publication.
2. Résoudre chaque entrée O/G ou la déclarer honnêtement. Examiner nombres de bugs/améliorations, délais privés, historique de toutes les vulnérabilités moyennes et supérieures, périmètre cryptographique et connaissances personnelles. Garder confidentiel le contenu des signalements privés.
3. Ouvrir [New project](https://www.bestpractices.dev/en/projects/new), se connecter et autoriser explicitement la création de compte si nécessaire. Enregistrer `https://github.com/alebgl77/ai-factory` ; saisir uniquement des réponses vérifiées et preuves publiques. L'URL de candidature publique sera attribuée sur place ; aucune n'est attribuée dans ce dossier.
4. Relire la candidature publique, compléter chaque réponse requise et obtenir un vrai badge passing avant d'ajouter son identifiant/lien au dépôt ou d'annoncer l'alerte Scorecard résolue.

Le seuil d'âge indépendant de Scorecard `Maintained` reste temporel (2027-02-02) ; `Branch-Protection` et `Code-Review` nécessitent une participation humaine indépendante réelle pour les exigences de revue restantes. Garder un seul mainteneur ne satisfait pas ces exigences de revue. Ce dossier ne change pas ces faits.
