# Revue de sécurité avant un déploiement en entreprise

Revue initiale du 6 novembre 2026. État des corrections actualisé le 27 novembre 2026.
La pile est exploitable en démonstration. Avant une exposition en entreprise, les
contrôles de configuration et de publication doivent s'accompagner d'une recette sur le tenant
cible et de preuves de reprise. Cette revue couvre la pile Compose et les releases `linux/amd64`.

## Contrôles ajoutés

- **Exposition de la pile.** `EDGE_BIND` vaut `127.0.0.1` par défaut. Pour servir des utilisateurs
  distants, sélectionner explicitement l'adresse d'écoute voulue, configurer HTTPS et le profil
  SSO, et conserver `EDGE_ALT_BIND` sur une adresse de boucle locale. Le précontrôle vérifie
  notamment le réseau de confiance du proxy, le groupe ou domaine autorisé, les cookies,
  les secrets de remplacement, les ports et le blocage du routage C3/C4 vers le cloud.
  Il exige `FACTORY_DEMO_MODE=never`, l'absence du profil `sim` et un Workbench désactivé ou en
  mode `portfolio` avec ses politiques. Le chat générique reste désactivé en mode `portfolio`.
  Il exige aussi des politiques de BU et de routage des clés montées en lecture seule.
  La politique de BU ferme les accès Grafana et Langfuse à l’edge. [Guide du routage](GATEWAY_SECURITY.md)
  Il rejette aussi les modes réseau non autorisés ainsi que les destinations GPU de simulation
  ou sur des IP publiques. Le contrôle des flux réels reste à faire sur la cible.
- **Approbation du registre.** Les lectures de `/api/v1/registry`, du cas individuel et le
  snapshot publient `review_revision`, un SHA-256 opaque. Toute soumission à
  `POST /api/v1/registry/{id}/assessment` doit reprendre cette valeur dans
  `expected_review_revision`. Une valeur absente ou mal formée renvoie 422. Une version
  périmée renvoie 409 avec `code: stale_review_revision` et l'évaluation reste inchangée.
  Le client doit relire le dossier et demander une nouvelle décision. La vérification se fait
  sous le verrou d'écriture et la règle des quatre yeux reste applicable en mode authentifié.
  La révision porte sur la proposition individuelle, avec les politiques partagées à examiner
  séparément. Les anciennes approbations restent liées à la version du modèle jusqu'à une
  nouvelle revue. Faire revoir les dossiers actifs lors de la migration avant la mise en service.
- **Autorisation de publication.** Avant les builds, le workflow de release vérifie le tag,
  l'ascendance sur la branche par défaut et le changelog. Il exige ensuite que la dernière
  exécution `push` de `ci.yml`, `security.yml` et `codeql.yml` sur cette branche, pour le SHA exact
  du commit pointé par le tag, soit terminée avec succès. Une exécution absente, en cours ou en
  échec, ou une erreur de lecture GitHub, bloque la release. Le précontrôle n'écrit pas sur GitHub.

Exécuter le précontrôle sur le fichier d'environnement rendu depuis le gestionnaire de secrets,
avec les mêmes profils et fichiers Compose que le déploiement.

```sh
docker compose --env-file /path/to/.env --profile sso config --format json | python scripts/check_production.py
```

Renseigner `GRAFANA_PUBLIC_URL` et `LANGFUSE_PUBLIC_URL` avec leurs URL HTTPS de production.
Pour utiliser les services vLLM fournis, ajouter `--profile local-gpu` au rendu et au déploiement.
Le précontrôle accepte par défaut les adresses IP privées explicites et les services vLLM fournis
qui sont actifs. Pour des GPU distants nommés dans votre DNS privé, autoriser chaque nom exact.

```sh
docker compose --env-file /path/to/.env --profile sso config --format json | \
  python scripts/check_production.py --private-gpu-host inference-gpu.corp.internal \
    --private-gpu-host embeddings-gpu.corp.internal
```

Confirmer que ces noms résolvent vers des adresses privées depuis la pile et que les règles
de sortie n'autorisent que les destinations attendues.

Le code de retour 0 valide la configuration rendue. Cette commande ne démarre aucun service.
Le JSON intermédiaire peut contenir des secrets. Le passer directement sur l'entrée standard
du script, sans le stocker ni l'inclure dans les journaux. Les cas de précontrôle sont exécutés
par le job de configuration en CI. [Guide de durcissement](fr/SECURITY.md#14-guide-de-durcissement-pour-la-production)

## Conditions encore à remplir

Au 27 novembre, le code de préparation de la v1.2.0 remplace la clé maître de l'API par
`LITELLM_TELEMETRY_KEY` et la clé facultative `LITELLM_INFERENCE_KEY`, avec variantes `_FILE`.
`LITELLM_ADMIN_KEY_SHA256` est exigé avec ces clés ou les liaisons Portfolio. Le démarrage
refuse la clé administrative et les clés réutilisées. Le workflow construit chaque image une
fois, analyse son identifiant local, vérifie et analyse le digest de quarantaine, puis vérifie
signatures et attestations avant la promotion du même manifeste. Les nouvelles preuves de
clés limitées et d'identité de publication attendent encore leur exécution CI sur Ubuntu.
La validation du tenant réel et la publication de v1.2.0 restent à effectuer.

Les réglages GitHub actifs imposent 21 contrôles et une branche `main` à jour. Les tags `v*`
sont protégés contre la modification et la suppression. Les alertes et correctifs automatiques
Dependabot sont activés. Les releases et leurs pièces jointes restent modifiables, selon le
périmètre de protection retenu. Vérifier les signatures et les digests à chaque téléchargement.

| Priorité | Écart et conséquence | Preuve attendue |
| --- | --- | --- |
| Avant exposition | Le mode SSO et les contrôles TLS sont configurables. Le dépôt ne peut pas vérifier la politique MFA, les groupes et l'émetteur du tenant réel, la validité des certificats ou les règles de sortie du pare-feu. | Tester sur la cible le refus sans identité ou groupe autorisé, l'absence d'accès direct aux services internes, les certificats et le rejet d'une sortie C3/C4 vers le cloud. |
| Avant exposition | Avec Docker Engine antérieur à 28.0.0, un autre hôte sur le même segment de niveau 2 peut atteindre des ports publiés sur la boucle locale. | Utiliser une version maintenue de Docker Engine à partir de 28.0.0. Depuis un autre hôte sur le même segment, vérifier que les ports réservés à la boucle locale restent injoignables. [Ports Docker](https://docs.docker.com/engine/network/port-publishing/) |
| Avant production | Les nouvelles clés limitées doivent être provisionnées sur la passerelle cible. | Migrer les secrets de l'API et les liaisons Portfolio. Vérifier que les clés refusent l'administration et les alias distants et que la télémétrie ne peut pas lancer d'inférence. [Configuration](GATEWAY_SECURITY.md) |
| Avant activation d'une démonstration Workbench | Le mode `protected_demo` peut conserver le message brut d'une demande escaladée dans un dossier partagé de démonstration. | Réserver ce mode aux données synthétiques. Pour le parcours privé `portfolio`, appliquer les politiques de BU, la minimisation et la rétention, puis tester les refus inter-BU et l'absence de données sensibles dans les traces. |
| Avant première release d'entreprise | La construction unique et la promotion du digest vérifié sont implémentées. La preuve complète de cette nouvelle chaîne attend une exécution de publication. | Vérifier le succès CI puis les scans, signatures et attestations du candidat v1.2.0. Conserver les digests et les preuves de refus en cas de divergence. |
| Avant première release d'entreprise | Les tests `sim` de la pile et SSO avec IdP de test sont déclenchés manuellement. Un succès antérieur ne couvre pas le commit ou les images d'une release ultérieure. | Exiger ces tests sur le candidat et conserver leurs résultats avec les digests publiés. Le [run historique](https://github.com/alebgl77/ai-factory/actions/runs/42110756671) valide le scénario de test sur un commit antérieur. |
| Avant déploiement reproductible | Les images tierces de Compose sont référencées par des tags modifiables. La CI Trivy des images couvre les six images du projet et le bilan tiers documenté n'inclut pas vLLM. | Verrouiller les références de production par digest, scanner celles des profils retenus, dont vLLM si activé, et valider une installation sans reconstruction. |
| Gouvernance du dépôt | Les contrôles stricts de `main` et la protection des tags sont actifs. Le dépôt reste maintenu par une seule personne et les releases restent modifiables. Un tag créé sur un ancien commit peut utiliser un workflow antérieur. | Désigner un second mainteneur avant d'exiger sa revue, limiter les créateurs de tags aux responsables des releases et ne publier que depuis le commit qualifié avec les contrôles actuels. Vérifier les artefacts signés lors de chaque installation. |
| Exploitation | La chaîne de hachage de l'audit aide à détecter une altération. La conservation, l'export protégé et la restauration dépendent de l'exploitant. | Définir la rétention des journaux et dossiers, envoyer les événements au SIEM, sauvegarder hors site avec immutabilité pour l'audit, puis restaurer et vérifier la chaîne avec `python -m app.audit verify`. Conserver une preuve de restauration et les délais mesurés. |

Éléments examinés dans le dépôt et sur GitHub.
[Release](../.github/workflows/release.yml) · [Tests CI](../.github/workflows/ci.yml) ·
[Compose](../docker-compose.yml) · [Inventaire Trivy](../compose/README.md#image-scan-trivy) ·
[Règle de `main`](https://github.com/alebgl77/ai-factory/rules/26648663) ·
[Workbench](../api/app/workbench/service.py) · [Clé LiteLLM de l'API](../docker-compose.yml)

Références pour les contrôles et la recette.
[Docker Compose en production](https://docs.docker.com/compose/how-tos/production/) ·
[OWASP LLM06, autonomie excessive](https://genai.owasp.org/llmrisk/llm062025-excessive-agency/) ·
[NIST SP 800-218, SSDF](https://csrc.nist.gov/pubs/sp/800/218/final) ·
[API GitHub des exécutions de workflow](https://docs.github.com/en/rest/actions/workflow-runs#list-workflow-runs-for-a-workflow)
