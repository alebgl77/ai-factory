# Revue de sécurité avant un déploiement en entreprise

Revue du 6 novembre 2026, fondée sur le dépôt et les règles GitHub en vigueur au début de
l'audit. La pile est exploitable en démonstration. Avant une exposition en entreprise, les
contrôles de configuration et de publication doivent s'accompagner d'une recette sur le tenant
cible et de preuves de reprise. Cette revue couvre la pile Compose et les releases `linux/amd64`.

## Contrôles ajoutés

- **Exposition de la pile.** `EDGE_BIND` vaut `127.0.0.1` par défaut. Pour servir des utilisateurs
  distants, sélectionner explicitement l'adresse d'écoute voulue, configurer HTTPS et le profil
  SSO, et conserver `EDGE_ALT_BIND` sur une adresse de boucle locale. Le précontrôle vérifie
  notamment le réseau de confiance du proxy, le groupe ou domaine autorisé, les cookies,
  les secrets de remplacement, les ports et le blocage du routage C3/C4 vers le cloud.
  Il exige `FACTORY_DEMO_MODE=never`, `FACTORY_WORKBENCH_MODE=off` et l'absence du profil `sim`.
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

| Priorité | Écart et conséquence | Preuve attendue |
| --- | --- | --- |
| Avant exposition | Le mode SSO et les contrôles TLS sont configurables. Le dépôt ne peut pas vérifier la politique MFA, les groupes et l'émetteur du tenant réel, la validité des certificats ou les règles de sortie du pare-feu. | Tester sur la cible le refus sans identité ou groupe autorisé, l'absence d'accès direct aux services internes, les certificats et le rejet d'une sortie C3/C4 vers le cloud. |
| Avant exposition | Avec Docker Engine antérieur à 28.0.0, un autre hôte sur le même segment de niveau 2 peut atteindre des ports publiés sur la boucle locale. | Utiliser une version maintenue de Docker Engine à partir de 28.0.0. Depuis un autre hôte sur le même segment, vérifier que les ports réservés à la boucle locale restent injoignables. [Ports Docker](https://docs.docker.com/engine/network/port-publishing/) |
| Avant production | L'API reçoit la clé maître LiteLLM même quand le Workbench est désactivé. Une compromission de l'API pourrait donner accès aux opérations d'administration ou à des modèles distants. | Remplacer la clé maître de l'API par des clés distinctes et à privilèges limités pour la télémétrie et l'inférence. Vérifier que les deux refusent l'administration et les alias distants, et que la télémétrie ne peut pas lancer d'inférence. |
| Avant activation du Workbench | Une demande escaladée peut conserver le message brut dans le dossier partagé de démonstration. | Maintenir le Workbench hors production. Avant une activation, isoler l'accès aux dossiers, appliquer la minimisation et la rétention, puis tester les refus d'accès et l'absence de données sensibles dans les traces. |
| Avant première release d'entreprise | Trivy contrôle un build local, puis le workflow reconstruit et pousse l'image. Les SBOM et attestations portent sur le digest poussé. La preuve Trivy ne lie pas actuellement ce digest au résultat du scan. | Construire une fois, scanner et vérifier le digest candidat, puis promouvoir ce même digest. Tester qu'un échec Trivy ou une divergence empêche la publication. |
| Avant première release d'entreprise | Les tests `sim` de la pile et SSO avec IdP de test sont déclenchés manuellement. Un succès antérieur ne couvre pas le commit ou les images d'une release ultérieure. | Exiger ces tests sur le candidat et conserver leurs résultats avec les digests publiés. Le [run historique](https://github.com/alebgl77/ai-factory/actions/runs/42110756671) valide le scénario de test sur un commit antérieur. |
| Avant déploiement reproductible | Les images tierces de Compose sont référencées par des tags modifiables. La CI Trivy des images couvre les six images du projet et le bilan tiers documenté n'inclut pas vLLM. | Verrouiller les références de production par digest, scanner celles des profils retenus, dont vLLM si activé, et valider une installation sans reconstruction. |
| Gouvernance du dépôt | Le dépôt est maintenu par une seule personne. Le règlement de `main` impose une PR et des contrôles, avec zéro approbation exigée. Aucun règlement de tags de release n'était actif lors de l'audit. Un tag pointant vers un ancien commit accessible depuis `main` peut déclencher une version du workflow qui précède le nouveau contrôle. | Désigner un second mainteneur avant d'exiger sa revue, limiter les créateurs de tags aux responsables des releases et refuser les commits qui précèdent le contrôle. Empêcher la modification ou la suppression des tags hors procédure. |
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
