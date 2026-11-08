# Workbench AI Factory et Compagnon de décision

[English](../WORKBENCH.md)

Le Workbench suit une opportunité CRM fictive depuis sa réception jusqu’à une commande ERP en
brouillon. Il rapproche le client, soumet la proposition à une personne et retrouve le reçu
simulé en cas d’interruption. Les questions stratégiques ou non documentées rejoignent la même
boîte de décision. Le Compagnon permet aussi de poser des questions aux agents et de donner un
avis sur un dossier.

## Ouvrir le Compagnon sur un téléphone

Sélectionnez **Compagnon de décision** ou **Code QR** dans la scène 3D ou l’atelier UC-04. Le
QR généré localement contient uniquement l’URL du Compagnon et le paramètre `lang`.
Connectez-vous séparément si l’espace l’exige. Pour changer d’appareil, ouvrez la scène depuis
une adresse HTTPS accessible au téléphone. Les fichiers locaux et `localhost` désignent
l’appareil qui les ouvre.

| Mode | Activation | Données et accès |
|---|---|---|
| Navigateur hors ligne | Ouvrir `dashboard/companion/index.html` depuis une copie des sources | Dossiers, décisions et réponses fictifs dans l’onglet courant. Un rechargement réinitialise la session. |
| API désactivée | Conserver `FACTORY_WORKBENCH_MODE=off`, valeur par défaut | Les routes du Workbench sont absentes. |
| Simulation connectée | Définir `FACTORY_WORKBENCH_MODE=demo` | Dossiers fictifs et brouillons ERP partagés dans SQLite. Utiliser en environnement contrôlé si l’authentification est désactivée. |
| Simulation protégée | Définir `FACTORY_WORKBENCH_MODE=protected_demo` avec le proxy SSO | Espace authentifié avec le même ERP simulé et contrôle des rôles. |

Le Compagnon est disponible en français et en anglais. Le workflow Pages le prépare au chemin
relatif `companion/` et sélectionne explicitement la simulation isolée dans le navigateur. Le
QR ne transporte ni session, ni identifiant de dossier, ni secret. En mode connecté, le
Compagnon affiche les erreurs d’API ou d’authentification sans basculer vers la démo locale.

Copiez `.env.example` vers `.env`, choisissez le mode et lancez
`docker compose --profile sim up -d --build` depuis une copie des sources. Le Compagnon
est à l’adresse `/companion/`. Pour la simulation protégée, suivez le
[guide SSO et durcissement](SECURITY.md). Configurez `FACTORY_AUTH=sso`,
`FACTORY_AUTH_MODE=proxy` et `FACTORY_ROLE_MAP`, puis activez le profil `sso`.
Servez le Compagnon et l’API sur la même origine.

```mermaid
flowchart LR
    CRM["Événement CRM fictif"] --> API["API Workbench et contrôle des rôles"]
    Dashboard["Scène 3D ou atelier"] -->|QR ouvre le lien| Phone["Compagnon de décision"]
    Phone -->|Revue et avis sur la même origine| API
    API --> Store["SQLite · dossiers, décisions et reçus"]
    Store --> ERP["Brouillon ERP simulé"]
    API -. Conseil facultatif .-> Model["LiteLLM interne · chat-sensitive"]
    CLI["Sonde CLI en lecture seule"] -. HTTPS .-> Odoo["Partenaires Odoo 19"]
```

## Suivre une revue depuis la carte 3D

Dans le tableau de bord protégé, sélectionnez **Suivre le Workbench**, puis **Activer le suivi**.
La carte utilise l’authentification existante du Workbench par proxy sur la même origine. Si des
périmètres par BU sont configurés, l’utilisateur doit disposer d’un accès global en lecture au
Workbench. Des avatars symboliques représentent l’agent et la personne chargée de la revue.
Ils montrent la demande en cours et l’issue enregistrée pour la révision du dossier. La carte
reçoit uniquement l’identifiant, la révision et le statut du dossier. Pour consulter la proposition
et rendre une décision selon votre rôle, ouvrez le **Compagnon de décision**.

Sur le tableau de bord public, **Échanges 3D** propose des scénarios fictifs dont les réponses
restent dans le navigateur. Le tableau de bord protégé suit le Workbench en lecture seule. La
configuration existante du Workbench protégé et du proxy suffit.

## Historique du travail et consommation de tokens

Ouvrez **Pilotage → Historique du travail** pour filtrer les exécutions enregistrées par agent ou
utilisateur. Le tableau de bord statique présente un historique fictif. L’historique protégé
utilise le proxy d’identité sur la même origine. Chaque utilisateur consulte ses événements
dans son périmètre BU actuel. Un administrateur dont le rôle vient d’un groupe de confiance
peut choisir explicitement tous les utilisateurs. Avec une politique BU, ce même groupe doit
accorder l’accès global. Une consultation globale produit un journal de sécurité avec des
références pseudonymisées.

`GET /api/v1/activity?days=7&view=own` renvoie jusqu’à 300 événements, du plus récent au plus
ancien dans la période UTC. Les périodes disponibles sont 7, 30 et 90 jours. Vérifiez
`truncated` avant d’interpréter les cumuls. `view=all` exige le droit administrateur global.
Une exécution d’agent porte aussi l’identité vérifiée de l’utilisateur qui l’a initiée ou
déclarée, de façon à retrouver le même événement avec les deux filtres.

Un éditeur ou administrateur authentifié peut déclarer une exécution instrumentée avec
`POST /api/v1/activity` au format JSON via le proxy d’identité. Exemple de déclaration :

```json
{
  "id": "763fcb2113ee49e481602a88a7ec326bc",
  "actor_type": "agent",
  "actor_id": "assistant-documentaire",
  "unit": "cc-ops",
  "action": "Synthèse des documents de référence",
  "started_at": "2026-11-06T10:00:00Z",
  "finished_at": "2026-11-06T10:00:08Z",
  "status": "completed",
  "tokens": {"input": 1200, "output": 400},
  "supports": [{"kind": "file", "label": "Référence.pdf", "ref": "doc-42"}]
}
```

Réutilisez le même identifiant hexadécimal minuscule de 32 caractères et la même BU en cas de
nouvel essai. Pour le même utilisateur et la même BU, un rejeu identique réussit et un rejeu
différent renvoie 409. Pour une action d’utilisateur,
omettez `actor_id`. Le serveur fournit l’identité de l’utilisateur. Omettez `unit` quand aucune
politique BU ne s’applique ou qu’une seule BU est autorisée en écriture. Les supports sont
identifiés par un libellé d’affichage et une référence opaque. L’intégration fournit les
libellés de l’action et des supports. Elle doit fournir des métadonnées d’affichage exemptes
de prompts, de contenu de document et de secrets. L’API rejette les champs inconnus, les
chemins et les URL.

Le Workbench protégé journalise les nouvelles décisions. Il journalise aussi les tentatives de
conseil quand le conseiller local est activé. Avec une politique BU, les intégrations externes
déclarent leurs exécutions via l’API d’activité. La durée du conseil correspond au temps
écoulé de l’exécution. Les tokens viennent du
fournisseur lorsqu’il les communique. La durée et les tokens d’une décision restent inconnus.
La provenance distingue les exécutions déclarées. Une mesure absente reste inconnue et un
zéro explicite reste zéro. Le journal mesure des intervalles d’exécution, indépendamment de
la présence ou du temps de travail humain. Il collecte les nouveaux événements dans le volume
d’état local.

## Décisions et conversations

- Les rôles de lecture peuvent consulter les dossiers et l’historique, poser des questions
  et donner un avis. Les rôles `editor`, `maintainer` et `admin` soumettent les événements.
  Les rôles `approver` et `admin` prennent les décisions. Un utilisateur authentifié ne
  peut pas approuver sa propre proposition.
- Une approbation exige un motif, la `revision` et le `proposal_hash` exacts du dossier.
  Une proposition plus récente invalide la révision précédente. L’approbation initiale
  d’une action expire après sept jours. Pour retrouver le résultat d’une approbation déjà
  enregistrée, le même approbateur renvoie la même révision, la même empreinte, la même
  décision et le même motif, même après l’échéance. Une référence métier stable permet de
  restituer le brouillon existant.
- Un rapprochement ambigu, une question stratégique, une demande non documentée ou une
  demande d’action dans le chat est orientée vers une personne. Consigner une décision sur
  un dossier sans action proposée ne crée aucun brouillon ERP. Les dossiers en attente
  passent en tête et leur ancienneté contribue à leur priorité avant la limite de 200 dossiers.
- Le Compagnon affiche les données et les réponses en texte. L’API conserve les dossiers,
  décisions, reçus et avis dans `workbench-<mode>.sqlite3` sous `FACTORY_STATE_DIR`.
  Une question orientée vers la revue est conservée dans un dossier partagé. Ne saisissez
  que des éléments adaptés à cet espace. Le Workbench ne conserve pas de transcription du
  chat. L’historique affiche les totaux des avis et votre propre commentaire. Les motifs des
  décisions sont visibles par les approbateurs, les administrateurs et leur auteur.

Le mode par défaut `FACTORY_WORKBENCH_CHAT_MODE=simulation` donne des réponses déterministes.
Le mode facultatif `FACTORY_WORKBENCH_CHAT_MODE=local` exige `protected_demo`,
l’authentification par proxy et la passerelle LiteLLM interne. Le serveur choisit l’alias
fixe `chat-sensitive`, marque la requête C3, masque les entrées et sorties et désactive la
journalisation du prompt pour cette requête. Configurez la cible locale en suivant
[le guide de placement Compose](../../compose/README.md#cloud-first-placement). Le modèle donne un avis sans outil.
L’indisponibilité de la passerelle locale produit une erreur. Le profil `sim` peut aussi
simuler la cible locale.

## API et contrat d’intégration

Les routes sont relatives à `/api/v1/workbench`. Le navigateur écrit en JSON sur la même origine.

| Méthode et route | Comportement |
|---|---|
| `GET /cases` | Retourne le mode, les capacités de l’utilisateur et les dossiers priorisés avec leur révision et leur empreinte. |
| `GET /encounters` | Retourne les identifiants, révisions et statuts de 100 dossiers prioritaires au maximum, avec un accès en lecture au Workbench protégé. Ajoutez `?case_id=<id>` pour suivre un dossier même s’il sort de la file prioritaire. |
| `POST /events` | Reçoit le sous-ensemble CloudEvents 1.0 pris en charge pour `crm.opportunity.won` depuis `crm-sim`. |
| `POST /cases/{id}/decisions` | Accepte `approve`, `reject` ou `request_changes` avec révision, empreinte et motif. |
| `GET /cases/{id}/history` | Retourne le reçu, les décisions visibles selon le rôle et le résumé des avis. |
| `POST /cases/{id}/feedback` | Enregistre ou remplace l’avis `helpful` ou `needs_work` de l’utilisateur et son commentaire facultatif. |
| `POST /chat` | Accepte un agent `operations`, `finance` ou `governance`, un message et un `case_id` facultatif. |

Dans la simulation connectée en local, envoyez un événement fictif puis ouvrez le Compagnon
pour examiner le dossier.

```bash
base=http://localhost/api/v1/workbench
curl -sS "$base/cases"
curl -sS "$base/events" -H 'Content-Type: application/cloudevents+json' -d '{
  "specversion":"1.0","source":"crm-sim","id":"guide-fr-001",
  "type":"crm.opportunity.won","subject":"opp-guide-fr-001",
  "data":{"title":"Opportunité de démonstration","customer_name":"Atelier Acme","customer_ref":"CRM-ACME",
          "amount_minor":124000,"currency":"EUR","record_version":1}
}'
```

La clé de livraison combine `source` et `id`. Un renvoi identique réutilise le dossier.
Un contenu différent pour la même livraison, une version obsolète ou une approbation
périmée reçoit `409`. Les montants utilisent des unités monétaires mineures entières. Le
rapprochement filtre selon l’espace et les identifiants autorisés, puis compare les références
exactes ou les noms normalisés. Il s’abstient si le résultat est ambigu. Le routage filtre
selon la sensibilité, la résidence, la disponibilité, le coût, la latence et la qualité
minimale avant de calculer un score. Les classes C3 et C4 exigent une cible locale. Le
benchmark exerce ce routage avec des observations fictives. Le Compagnon utilise son alias
local fixe pour le conseil facultatif.

## Sonde Odoo en lecture seule et rejeu synthétique

Installez les [dépendances de l’API](../../api/README.fr.md#démarrage-local), puis configurez `FACTORY_ODOO_URL`
avec une origine HTTPS et fournissez `FACTORY_ODOO_DATABASE` et `FACTORY_ODOO_API_KEY` dans
l’environnement du processus opérateur. Utilisez un compte limité à la lecture pour l’API
JSON-2 d’Odoo 19.

```bash
python scripts/workbench_benchmark.py --odoo-read --limit 25
python scripts/workbench_benchmark.py --odoo-read --limit 25 \
  --after-write-date '2026-11-05 12:00:00' --after-id 42 --overlap-seconds 5
```

Remplacez le curseur UTC d’exemple par la valeur de la sortie précédente. La commande lit
une page de partenaires et affiche le compteur et le prochain curseur `(write_date, id)`.
La limite est de 200. La sonde ne réalise aucun import ni écriture dans Odoo. La fonction de
synchronisation enregistre le curseur après traitement de la page entière. Le recouvrement
et une interruption de traitement peuvent rejouer des lignes. Le consommateur doit donc être
idempotent. Les suppressions exigent une réconciliation séparée.

```bash
python scripts/workbench_benchmark.py
(cd api && python -m pytest -q tests/test_workbench.py tests/test_workbench_algorithms.py)
node tests/e2e/companion.mjs
```

Le benchmark rejoue 36 cas synthétiques dans SQLite, dont les livraisons dupliquées, la perte
de reçu, les approbations obsolètes et l’autoapprobation. Il comprend aussi trois vérifications
de routage synthétiques, toutes réussies. Le scénario gouverné obtient 36/36 résultats
attendus, contre 12/36 pour une règle volontairement simple.
Ces résultats ne mesurent ni la qualité des modèles en production, ni la latence, ni le coût.
Le test navigateur utilise la même installation Playwright verrouillée que
[la CI](../../.github/workflows/ci.yml).

L’implémentation couvre un seul espace fictif partagé. Le reçu SQLite vérifie le comportement
de l’ERP simulé. Un ERP externe demande son propre contrat d’écriture et de réconciliation.
La lecture Odoo est facultative et le connecteur est testé avec un transport simulé.
