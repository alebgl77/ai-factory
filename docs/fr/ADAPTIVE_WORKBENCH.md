# Intégrer le Workbench adaptatif

[English](../ADAPTIVE_WORKBENCH.md)

Le Workbench prépare un dossier selon la méthode de travail d’une organisation et consigne une décision séparée sur une révision précise. Le SDK Python intègre le moteur. L’API HTTP protégée relie le Compagnon et les relais d’approbation. Le serveur MCP local permet aux agents de préparer et de consulter les dossiers.

## Exécuter l’exemple SDK

Depuis la racine du dépôt, avec Python 3.11 ou une version ultérieure dans un environnement virtuel.

```sh
python -m pip install ./api
python examples/adaptive_workbench.py
```

L’exemple utilise des bases SQLite temporaires et des données fictives. Le rapport JSON montre une décision humaine directe, deux décisions humaines relayées via Slack et Teams, une décision humaine relayée par un service interne et le blocage d’une approbation automatique de gouvernance. Il compare deux politiques sur des dossiers fictifs. Un second scénario crée un brouillon dans un ERP simulé séparé, perd la réponse et retrouve un seul brouillon après réouverture du moteur. L’exécution se fait hors ligne, sans compte fournisseur ni identifiant externe.

## Connecter Salesforce, HubSpot ou Twenty

Copiez [la configuration d’exemple](../../examples/crm-sources.example.json). Donnez un alias d’instance à chaque compte CRM et accordez à l’identité de synchronisation les droits de lecture sur les opportunités et leurs références de comptes ou sociétés. Conservez le même alias pour le même compte lors d’une rotation de jeton. Injectez les identifiants via les variables d’environnement nommées par `token_env` dans la configuration de l’hôte.

| Fournisseur | Configuration du compte |
| --- | --- |
| Salesforce | Indiquez l’origine HTTPS de l’instance et un jeton OAuth d’une identité qui peut lire les opportunités et les comptes. `IsWon` détermine les opportunités gagnées. Renseignez `default_currency` pour une organisation monodevise. Dans une organisation multidevise, omettez ce paramètre pour lire `CurrencyIsoCode`. [Requêtes REST Salesforce](https://developer.salesforce.com/docs/platform/api-rest/guide/resources-query) |
| HubSpot | Fournissez un jeton qui donne accès en lecture aux transactions et aux entreprises. Renseignez `won_stages` avec les identifiants internes des étapes gagnées du portail. Le connecteur retient l’entreprise principale ou l’unique entreprise associée. [Recherche CRM](https://developers.hubspot.com/docs/api-reference/latest/crm/search-the-crm) · [Associations](https://developers.hubspot.com/docs/api-reference/latest/crm/associations/associate-records/guide) |
| Twenty | Utilisez `https://api.twenty.com` ou l’origine HTTPS de votre instance auto-hébergée. Restreignez la clé API au schéma standard des opportunités et à la référence de société. Renseignez `won_stages` avec les valeurs de votre espace, par exemple `WON` si cette valeur y est configurée. [API Twenty](https://github.com/twentyhq/twenty/blob/main/packages/twenty-docs/developers/extend/api.mdx) |

Pour chaque compte connu, indiquez le champ `id` du client ERP et une `reference` CRM de la forme `provider/instance/account-id`. Plusieurs références peuvent désigner le même client ERP. Une opportunité associée à un compte inconnu ou ambigu part en revue sans action de création de brouillon ERP. L’import convertit les montants en unités mineures exactes de leur devise d’origine. Le compteur `blocked` signale les opportunités dont le montant ou la devise manque ou ne peut pas être utilisé. Corrigez la fiche CRM pour produire une révision plus récente. Les autres dossiers de la fenêtre peuvent être traités.

Lancez le premier import depuis l’hôte de confiance. Adaptez les dates à la période souhaitée.

```sh
python -m ai_factory_workbench.crm_sync --database ./crm-workbench.sqlite3 \
  --workspace sales-ops --config ./crm-sources.json \
  --since 2026-11-01T00:00:00Z --until 2026-11-02T00:00:00Z
```

Chaque source enregistre son point de reprise lorsque sa fenêtre a été traitée. Augmentez ensuite `--until` et omettez `--since` pour les sources déjà initialisées. Limitez la fenêtre de lecture à sept jours, dont cinq minutes de recouvrement pour le rejeu. Une fois à jour, planifiez la commande sur l’hôte en omettant `--until` pour arrêter la lecture cinq minutes avant l’exécution. Utilisez `--provider hubspot` pour sélectionner un fournisseur ou `--provider hubspot/eu-portal` pour un compte précis. La commande affiche des compteurs et des codes d’erreur par source. `conflicts` compte les observations contradictoires pour un même dossier CRM et une même révision. Corrigez le dossier dans le CRM et synchronisez une révision plus récente avant de préparer une nouvelle proposition. Reprenez une fenêtre en échec avec la même configuration et la même base.

Les nouvelles opportunités gagnées suivent le profil configuré, `governance` par défaut. Un changement d’étape détecté par la synchronisation retire une action qui n’a pas encore été réservée pour exécution. Si l’exécution a pu commencer, vérifiez d’abord l’opération existante dans l’ERP. Prévoyez un flux de suppression distinct pour les dossiers que le fournisseur retire de sa liste d’opportunités. La commande utilise l’ERP simulé local. Pour partager la file, configurez un Compagnon protégé avec le même espace et la même base. Pour un ERP réel, construisez `Workbench` avec votre `DraftConnector`, le même catalogue exact et `crm_sources=(source.source, ...)`, puis appelez `sync_opportunities` depuis l’hôte SDK de confiance. Utilisez les canaux de validation décrits plus bas pour autoriser les actions proposées.

Si une page échoue, la synchronisation enregistre tout de même les retraits, les observations bloquées et les conflits déjà confirmés. Une opportunité modifiée suspend aussi l’action existante jusqu’à ce qu’une synchronisation complète confirme la mise à jour. Le point de reprise avance une fois la fenêtre complète traitée avec succès.

## Adapter la méthode de travail

| Profil | Preuves demandées |
| --- | --- |
| `governance` | Artefact, évaluation, synthèse des risques et plan de retour arrière |
| `operations` | Référence métier, état observé et effet attendu |
| `platform` | Changement, tests, synthèse des impacts et plan de retour arrière |

L’application hôte choisit le profil actif. Une intégration Python peut charger un `WorkProfile` déclaratif avec `WorkProfile.from_dict` et afficher ses étapes et sa liste de preuves via `profile.describe()`. `profile.prepare(engine, actor, ...)` vérifie les preuves et lie l’identifiant, la version et l’empreinte du profil à la proposition. Un changement de profil peut créer une nouvelle révision. La validation reprend l’identifiant du dossier, sa révision et l’empreinte de la proposition affichée au décideur.

Les dossiers de revue du SDK et les propositions liées à un profil exigent un décideur authentifié, y compris en mode démo. Les nouvelles soumissions CRM authentifiées via HTTP ou MCP enregistrent le profil actif. L’hôte SDK de confiance peut le lier avec `engine.submit(event, editor, profile=profile.snapshot())`. Les brouillons créés sans profil passent par une revue humaine. Un changement de profil ne donne aucun droit supplémentaire à une ancienne preuve émise par un service.

`compare_policies(baseline, candidate, cases)` compare l’état de préparation et les canaux admissibles sur les dossiers fournis. En omettant `cases`, la fonction utilise l’échantillon fictif. Le rapport expose les différences à examiner. L’activation d’un profil relève de la configuration de l’hôte.

```python
from pathlib import Path
from ai_factory_workbench import Actor, Workbench
from ai_factory_workbench.profiles import get_profile

engine = Workbench(Path("governance.sqlite3"), workspace="equipe-plateforme", mode="protected_demo")
editor = Actor("responsable-version", frozenset({"editor"}))
case = get_profile("governance").prepare(
    engine, editor, subject="modele/v2", title="Examiner le modèle v2",
    reason="Examiner les preuves avant la mise en service.",
    evidence={"artifact_ref": "modeles/v2", "evaluation_ref": "eval/v2",
              "risk_summary": "Vérifier le traitement des données sensibles.", "rollback_plan": "Restaurer la v1."},
)
```

`Actor` représente une identité et des rôles vérifiés par l’application hôte. En déploiement, construisez-le à partir de données d’identité de confiance. L’hôte choisit le chemin de la base et utilise un fichier SQLite par espace de travail. À l’ouverture, le moteur lie ce fichier à l’espace et à sa cible ERP.

## Connecter un client MCP

```sh
python -m pip install './api[mcp]'
ai-factory-mcp --database ./workbench.sqlite3 --workspace equipe-plateforme --profile governance
```

Configurez le client MCP pour lancer cette commande sur stdio. Utilisez un exécutable et un chemin de base accessibles à son processus local. Remplacez `--profile governance` par `--profile-file ./work-profile.json` pour charger un profil JSON administré localement. Les clients compatibles avec la découverte de skills MCP peuvent utiliser `skills/list` et `skills/get`. Les clients qui lisent les ressources peuvent ouvrir `skill://ai-factory-workbench/SKILL.md`. Le skill embarqué décrit le même parcours pour les clients Agent Skills compatibles. [Fichier `SKILL.md`](../../api/ai_factory_workbench/agent_skills/ai-factory-workbench/SKILL.md)

1. Lire `workbench_get_profile` pour connaître la liste des preuves attendues.
2. Appeler `workbench_prepare_review` avec les preuves réunies ou utiliser `workbench_propose_opportunity` pour l’événement CRM de référence. Les deux appels créent une proposition à examiner séparément.
3. Utiliser `workbench_list_cases` et `workbench_get_case` pour vérifier la révision, les preuves et le résultat consigné.

Le processus MCP dispose du rôle éditeur dans l’espace configuré. Limitez son accès au fichier prévu et partagez ce fichier avec l’hôte protégé seulement si les deux sont autorisés pour le même espace et la même cible. La commande utilise le bac à sable ERP embarqué. Un hôte de confiance qui injecte son propre connecteur peut servir le même espace avec `ai_factory_workbench.mcp_server.build_server(engine, profile)`. La validation et la reprise d’écriture ERP s’effectuent dans l’hôte de confiance. [SDK MCP Python](https://py.sdk.modelcontextprotocol.io/v2/) · [Spécification Agent Skills](https://agentskills.io/specification)

## Protéger l’API HTTP et les canaux de validation

L’API est désactivée par défaut. Activez `FACTORY_WORKBENCH_MODE=protected_demo` avec le SSO pour un déploiement protégé. Le Compagnon utilise les routes JSON de même origine. Les clients serveur à serveur s’authentifient auprès du proxy de confiance. [Modes Workbench et configuration SSO](WORKBENCH.md#ouvrir-le-compagnon-sur-un-téléphone)

| Route sous `/api/v1/workbench` | Usage |
| --- | --- |
| `GET /profiles` | Méthode active et profils disponibles |
| `POST /reviews` | Préparer une revue avec `subject`, `title`, `reason`, `evidence` et `kind` facultatif |
| `GET /cases/{case_id}` | Lire le dossier précis |
| `POST /cases/{case_id}/decisions` | Consigner une décision authentifiée du Compagnon |
| `GET /cases/{case_id}/history` | Consulter l’historique et le reçu selon le rôle de l’appelant |
| `POST /approval-relay` | Soumettre l’enveloppe signée d’un relais vérifié, si l’hôte l’a activé |

Après la création de l’application avec ses routes Workbench protégées, l’hôte peut appeler `configure_workbench_surfaces(app, profile="governance", engine=engine, authorities=authorities)` depuis `app.routers.workbench`. `profile` accepte un nom intégré ou un `WorkProfile` validé. Le moteur est facultatif et doit respecter le mode d’accès HTTP configuré. Le relais reste désactivé jusqu’à la configuration des autorités. En Python, l’hôte peut également créer `ApprovalRelay` directement.

| Canal | Vérification de l’identité et de l’approbation |
| --- | --- |
| **Compagnon de décision** | Le proxy de confiance authentifie le décideur. Celui-ci ouvre le dossier et consigne sa décision. |
| **Slack ou Teams** | Une intégration installée vérifie le rappel du fournisseur et l’organisation autorisée, puis associe l’identifiant utilisateur à un décideur humain canonique. Pour Slack, vérifier la signature du corps brut et son horodatage. Pour Teams, vérifier l’authentification de l’activité du bot et son organisation via l’adaptateur Microsoft. |
| **Service interne** | Un service enregistré signe la preuve de la décision d’une personne authentifiée de façon indépendante pour les dossiers de gouvernance. Le profil Opérations peut autoriser une identité de service explicitement configurée à décider sur un brouillon ERP pris en charge. |

Les identités de service enregistrées passent par le relais signé pour décider. La route du Compagnon les refuse, y compris via leurs alias, même si le proxy leur attribue un rôle de validation. À l’enregistrement, indiquez le compte canonique et ses alias de messagerie. Réservez les groupes SSO qui accordent les rôles de validation ou d’administration aux personnes pour exclure les identités de service non enregistrées de la route de décision humaine.

Conservez une clé aléatoire distincte pour chaque émetteur dans le coffre de secrets de l’hôte. Liez la décision au dossier côté serveur consulté par le décideur. L’enveloppe comporte `workspace`, `issuer`, `channel`, `identity`, `case_id`, `revision`, `proposal_hash`, `decision`, `rationale`, `issued_at`, `expires_at` et `request_id`. Les dates sont des secondes Unix. Utilisez un identifiant inédit pour chaque décision et une durée de validité maximale de cinq minutes. Envoyez `{ "envelope": envelope, "signature": sign_approval(key, envelope) }` par la route protégée ou passez les deux valeurs à `ApprovalRelay.decide` dans un processus de confiance. Utilisez le mappage d’identités enregistré et gardez le même identifiant lors d’une relance à l’identique.

Le relais vérifie un HMAC à secret partagé. Le moteur consigne l’empreinte de la preuve et son origine. La vérification du HMAC exige ce secret. L’exemple SDK exerce le contrat du relais. Installez et configurez votre intégration Slack ou Teams pour traiter de vrais événements. [Vérification des requêtes Slack](https://api.slack.com/docs/verifying-requests-from-slack) · [Authentification du Bot Connector Microsoft](https://learn.microsoft.com/en-us/azure/bot-service/rest-api/bot-framework-rest-connector-authentication)

## Brancher un adaptateur de brouillons ERP

Injectez un `DraftConnector` de confiance et un catalogue clients limité à l’espace. Le connecteur déclare une cible ERP stable et prend en charge les écritures de brouillons, une clé d’idempotence unique imposée par l’ERP et une relecture indépendante. Utilisez `crm-sim` avec l’ERP simulé intégré et configurez une source de confiance si vous injectez un adaptateur ERP. Le moteur compare le brouillon enregistré aux champs approuvés. Un écart confirmé place le dossier en rapprochement manuel et le retire de la file d’exécution automatique.

Pour exécuter l’effet dans un processus de traitement, créez le moteur avec `defer_execution=True`. L’approbation enregistre le dossier à l’état `applying`. L’hôte peut trouver les dossiers en attente avec `pending_execution_ids()` et reprendre un dossier avec `reconcile_pending(case_id)`. Après une interruption, reprenez le même dossier et laissez le connecteur rechercher la même clé d’opération. Réservez cet accès à l’infrastructure de confiance. La sonde Odoo embarquée lit les fiches partenaires. L’exemple utilise un ERP simulé local. [Sonde Odoo en lecture seule](WORKBENCH.md#sonde-odoo-en-lecture-seule-et-rejeu-synthétique)

Le processus de traitement réserve la révision exacte dans SQLite avant de contacter l’ERP. Si le Workbench accepte un nouvel événement CRM avant cette réservation, il remplace la proposition en attente et exige une nouvelle revue. Lorsque la relecture confirme l’absence du brouillon, le processus vérifie à nouveau la révision et le blocage de rapprochement avant l’envoi. Une requête ERP déjà envoyée peut aboutir et nécessiter un rapprochement manuel. L’unicité atomique dans l’ERP relève du connecteur.
