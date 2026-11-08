# Portefeuille et orchestration du travail

[English](../PORTFOLIO.md) · **Français**

[Guide interactif de mise en production](https://alebgl77.github.io/ai-factory/docs/#production) · [Identités](#déploiement-protégé) · [Moteur intégré](#moteur-intégré) · [Décisions](#décisions-workbench) · [API](#intégration-par-lapi) · [Exploitation](#exploitation)

L’onglet **Portefeuille** relie les objectifs de la Factory aux projets et aux tâches des unités métier (BU). La direction renseigne les indicateurs et leurs cibles. Une cheffe de projet ou un chef de projet planifie les tâches, le manager les attribue, et l’architecte valide les livrables techniques. Les employés retrouvent leurs tâches. Les agents enregistrés reçoivent leurs affectations et, le cas échéant, les sous-tâches qu’ils supervisent.

GitHub Pages présente une démonstration hors ligne avec des données fictives. Les parcours qui exécutent un modèle et enregistrent une décision réelle utilisent une installation privée protégée.

## Déploiement protégé

Servez le tableau de bord et l’API derrière le proxy SSO sur la même origine. Configurez `FACTORY_AUTH=sso` côté edge, `FACTORY_AUTH_MODE=proxy`, `FACTORY_TRUSTED_PROXIES`, `FACTORY_ROLE_MAP`, `FACTORY_BU_POLICY` et `FACTORY_DEMO_MODE=never`. La politique BU est un fichier opérateur en lecture seule. Redémarrez l’API après une modification. Utilisez une instance API à un seul processus pour le portefeuille. Le manager et l’agent passent par ce même processus pour enregistrer puis utiliser une autorisation de démarrage. Conservez les bases SQLite dans le volume privé `api-state`, avec le reste de l’état et des sauvegardes cohérentes. [Sécurité par BU](../BU_SECURITY.md)

Dans la [policy d’exemple](../../config/business-units.example.yaml), ajoutez à `groups` les groupes SSO des rôles et des comptes de service. Chacun doit être présent dans `FACTORY_ROLE_MAP`. Le rôle API `viewer` suffit pour les groupes dédiés au portefeuille. L’administration des objectifs et projets exige un groupe `admin` qui porte lui-même le périmètre `["*"]`.

```yaml
# Sections à ajouter à une policy complète. Les groupes sont fictifs.
portfolio_roles:
  relation-chefs-projet: [project_lead]
  relation-managers: [manager]
  relation-architectes: [architect]
portfolio_agents:
  oidc-service-support: ag-support-supervisor
  oidc-service-recherche: ag-support-research
portfolio_autonomy:
  cc-relation:
    read_only: autonomous
    prepare: human_review
    sensitive: blocked
```

Affectez chacun de ces groupes métier à `[cc-relation]` et donnez aux comptes de service un groupe de lecture dédié à la même BU. L’identité de service est le sujet stable vérifié par le proxy. L’agent doit appartenir au catalogue, être rattaché à un cas d’usage de la BU et avoir passé son évaluation. Les employés sont liés à leur fiche via `identities` et à leur BU via `people`. Le même groupe SSO fournit le rôle opérationnel et le périmètre.

## Exécution et validation

- **Autonome.** L’agent affecté peut démarrer quand les dépendances sont terminées.
- **Validation humaine.** Le manager enregistre une autorisation de démarrage à usage unique. C’est la valeur par défaut de chaque mode absent. Une réaffectation, une replanification ou un redémarrage de l’API invalide l’autorisation en attente. Après un redémarrage, le manager la renouvelle si la tâche est toujours prête. Cette règle s’applique aussi au retour à un fichier de politique antérieur.
- **Bloqué.** La BU interdit l’affectation et le démarrage des agents dans ce mode.

Une tâche technique soumise passe en revue auprès d’un architecte distinct. Une tâche sensible termine sa préparation au statut **Relais Workbench**, après revue technique si elle en requiert une. Activez le mode dédié pour enregistrer et répercuter une décision sur cette tâche.

Si l’évaluation d’un agent est révoquée, l’agent perd l’accès à ses affectations dans le portefeuille. Le manager de sa BU peut mettre la tâche en cours en état bloqué puis l’attribuer à un agent éligible selon la politique de la BU.

Pour lancer une sous-tâche, le manager peut choisir un agent éligible distinct ou utiliser la sélection automatique. Celle-ci privilégie le même cas d’usage, puis l’agent ayant le moins de sous-tâches sur ce parent, puis son identifiant. L’agent affecté au parent peut demander la sélection automatique si le mode de la sous-tâche est autonome. Le parent doit être en cours. Ses sous-tâches restent dans la même BU et le même projet, avec un niveau de délégation et quatre sous-tâches au maximum pendant toute la vie du parent, annulations comprises. Une sous-tâche hérite de la revue technique et peut produire de la lecture ou de la préparation. La soumission du parent attend que chaque sous-tâche soit terminée ou annulée.

## Moteur intégré

Le moteur prend en charge les sous-tâches de lecture et de préparation affectées aux agents configurés. Il attend leurs dépendances et applique la politique d’autonomie de la BU avant chaque tentative. Pour une tâche à validation humaine, le manager délivre l’autorisation de démarrage dans le portefeuille.

1. Enregistrez le cas d’usage C3/C4 de l’agent dans la BU, avec l’alias `chat-sensitive` lié au modèle local. Vérifiez l’évaluation de l’agent.
2. Provisionnez une clé virtuelle LiteLLM dédiée au cas. Restreignez son modèle et ses routes de chat. Enregistrez son empreinte dans la politique stricte et configurez le réseau privé du GPU. [Routage authentifié des modèles](../GATEWAY_SECURITY.md)
3. Depuis le gestionnaire de secrets, préparez un fichier privé lisible par l’UID `10001` de l’API. Renseignez son chemin absolu hôte dans `FACTORY_PORTFOLIO_RUNNER_SOURCE`. Ajoutez [docker-compose.portfolio.yml](../../docker-compose.portfolio.yml) aux fichiers de l’installation. Il monte le fichier en lecture seule et définit `FACTORY_PORTFOLIO_RUNNER_FILE=/run/secrets/factory_portfolio_runner`. Sans fichier configuré, le moteur reste désactivé.

```json
{
  "version": 1,
  "bindings": [
    {
      "agent_id": "ag-support-research",
      "unit": "cc-relation",
      "usecase_id": "uc-41",
      "virtual_key": "<clé virtuelle du cas>"
    }
  ]
}
```

Les identifiants sont fictifs. Gardez le fichier hors du dépôt. Le moteur fixe l’alias et le format des requêtes. Chaque tentative produit une réponse texte bornée, sans outils ni repli distant, avec un appel limité à 60 secondes et 512 tokens de sortie. Le portefeuille affiche le résultat dans le périmètre de la tâche. Les valeurs de tokens du fournisseur sont conservées lorsqu’elles sont disponibles. Après interruption, le délai de réservation peut atteindre deux minutes. Le manager vérifie l’historique et réaffecte la tâche si une reprise est nécessaire. Les événements `portfolio.runner_attempt_finished` et `portfolio.runner_tick_failed` excluent les instructions, le résultat et la clé virtuelle.

## Décisions Workbench

Configurez `FACTORY_WORKBENCH_MODE=portfolio` et `FACTORY_WORKBENCH_CYBER_AUDIT=on` sur l’installation privée. Le journal de sécurité doit rester actif pour ouvrir, décider et rapprocher les dossiers. Pour décider, ouvrez le **Compagnon de décision** avec un compte dont un même groupe SSO porte le rôle `approver` ou `admin` et le périmètre global `["*"]`. Le soumissionnaire et, le cas échéant, l’architecte de la tâche sont exclus de la décision.

Le Workbench conserve le dossier et la décision avec leur révision. L’API rapproche les deux bases en arrière-plan, y compris lorsque le moteur de sous-agents est désactivé. Le portefeuille expose le résultat de la décision et son horodatage. Le motif et l’identité du décideur restent dans le Workbench.

| Décision | État du portefeuille |
| --- | --- |
| Approbation | Terminée. Les tâches dépendantes peuvent reprendre. |
| Refus | Annulée. Une dépendance qui exige la réussite attend toujours. |
| Demande de modifications | Prête. Fournissez de nouvelles preuves et renouvelez l’autorisation de démarrage si elle est requise. La prochaine soumission ouvre un dossier distinct. |

Une décision de portefeuille valide le travail préparé. Pour une action externe, suivez le circuit approuvé du connecteur et de son environnement. [Workbench et Compagnon](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/WORKBENCH.md)

## Intégration par l’API

Un moteur externe peut utiliser un compte de service par agent et s’authentifier auprès du proxy. Il consulte ses affectations, démarre les tâches autorisées, effectue le travail dans son environnement et retourne des preuves. L’API coordonne ces opérations par état et révision.

| Requête sous `/api/v1/portfolio` | Corps ou usage |
|---|---|
| `GET /` | Vue autorisée des objectifs, projets, tâches, rôles et politiques d’autonomie |
| `GET /?limit=50` | Vue paginée des tâches, de la plus récemment créée à la plus ancienne. Reprenez `next_cursor` dans le paramètre `cursor` avec la même limite. |
| `POST /tasks/{id}/commands` | `{revision, action: "authorize_start"}` par le manager si une autorisation est requise |
| `POST /tasks/{id}/commands` | `{revision, action: "start"}` par l’agent affecté |
| `POST /tasks/{id}/commands` | `{revision, action: "report_usage", tokens: {input, output}}` pendant l’exécution |
| `POST /tasks/{id}/commands` | `{revision, action: "submit", evidence?, tokens?}` par la personne ou l’agent affecté |
| `POST /tasks/{id}/subagents` | `{revision, mode, title, description?, agent_id?}`. Les modes sont `manual` avec `agent_id` et `automatic` avec sélection par le serveur. La réponse contient `parent` et `task`. |

Les utilisateurs humains voient tous les titres et états stratégiques. Pour un projet extérieur à leurs BU, la réponse contient uniquement `title` et `state`. Pour un objectif sans projet dans leurs BU, elle contient uniquement `title` et `status` (`met`, `unmet` ou `unmeasured`). Les BU participantes et les administrateurs globaux accèdent aux fiches détaillées. Les agents voient les objectifs et projets liés à leurs affectations autorisées, quelle que soit la page.

Sans `limit`, l’API conserve la vue des 200 tâches les plus récemment mises à jour. Avec `limit` (de 1 à 200), `next_cursor` vaut `null` à la dernière page. Le curseur est lié à l’identité vérifiée, à ses droits et à la limite. Une modification des droits peut invalider la suite avec une réponse HTTP 422. Chaque page réévalue les affectations et exclut les tâches créées après la première page. Un changement de droits ou d’affectation peut modifier l’ensemble visible.

Utilisez le chemin `/api/v1/portfolio` sans barre finale pour le GET. L’API crée les identifiants des tâches et des sous-tâches. Envoyez les mutations en JSON avec la dernière révision reçue. En cas de conflit HTTP 409, rechargez la vue autorisée avant de renvoyer une intention toujours valable.

Les preuves peuvent référencer un fichier, un support ou un travail via `{kind, label, ref}`. Le champ `ref` est un identifiant interne opaque. Les compteurs `input` et `output` déclarés sont des entiers cumulatifs de 0 à un milliard chacun sur l’affectation en cours. Le serveur vérifie qu’ils progressent, calcule le total et indique la provenance `agent_reported`. Le moteur intégré peut produire un relevé `provider_reported`. La soumission conserve le dernier relevé si aucun nouveau relevé ne l’accompagne. Une réaffectation à un autre agent efface le relevé courant. Une valeur absente reste inconnue. Le journal d’activité fournit ses propres mesures. [Historique du travail et consommation de tokens](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/WORKBENCH.md#historique-du-travail-et-consommation-de-tokens)

## Exploitation

- Pour le pilote actuel, utilisez le [bundle signé correspondant](../DEPLOYMENT.md#vérifier-et-installer-une-release) dès sa publication. D’ici là, [qualifiez le clone au commit exact](../DEPLOYMENT.md#qualifier-depuis-les-sources) avec des images construites localement.
- Exécutez le [précontrôle de sécurité](https://github.com/alebgl77/ai-factory/blob/main/docs/fr/SECURITY.md#14-guide-de-durcissement-pour-la-production) avec les mêmes fichiers Compose et profils que la cible. Le JSON peut contenir des secrets. Transmettez-le directement au script.
- Qualifiez la séparation des rôles, un refus inter-BU, un succès de sous-agent, une panne de modèle et un redémarrage entre la décision Workbench et son affichage dans le portefeuille. Utilisez des données synthétiques.
- Sauvegardez le volume `api-state` pendant une fenêtre maîtrisée, avec l’API arrêtée ou un mécanisme d’instantané cohérent pour les deux bases. Conservez le point de restauration, les versions et les politiques ensemble. Testez la restauration sur un réseau isolé avant tout retour en service.

### Installation historique par liste d’images

Utilisez cette procédure uniquement pour reproduire une release antérieure comme référence historique. Vérifiez les [signatures et attestations](../../SECURITY.md#vérifier-une-release). Renseignez `FACTORY_RELEASE_API_SHA256`, `FACTORY_RELEASE_DASHBOARD_SHA256` et `FACTORY_RELEASE_GRAFANA_BOOTSTRAP_SHA256` avec les valeurs vérifiées, chacune sur 64 caractères hexadécimaux, sans préfixe. Ajoutez [docker-compose.release.yml](../../docker-compose.release.yml) pour sélectionner ces trois images et désactiver leur construction locale.

```sh
docker compose -f docker-compose.yml -f docker-compose.release.yml \
  -f docker-compose.portfolio.yml \
  --env-file /secure/ai-factory/production.env --profile sso \
  config --format json | python scripts/check_production.py
```

Si le moteur intégré est désactivé, retirez `-f docker-compose.portfolio.yml`. Après validation du précontrôle, reprenez les mêmes options pour `up -d --no-build`. Sur une cible GPU privée externe, qualifiez TLS et ajoutez les noms DNS autorisés à la commande du précontrôle.
