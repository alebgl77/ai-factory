# Coin café et boîte à idées

[Démo](#essayer-la-démo) · [Accès et visibilité](#accès-et-visibilité) · [Traçabilité](#traçabilité-du-dépôt) · [Classement](#classement-automatique) · [API](#intégration-par-lapi) · [Exploitation](#déploiement-et-exploitation)

Le **Coin café** rassemble des propositions courtes sous forme de post-its. En installation
protégée, un badge coloré affiche l’origine du dépôt vérifiée par l’API. Le badge **BU** porte
le nom de l’unité. La catégorie et les tags décrivent le sujet de l’idée. Dès sa création,
le post-it est visible par toute la Factory authentifiée.

## Essayer la démo

1. Ouvrez le **Coin café** sur la carte 3D ou le plan. Le même mur est accessible depuis
   **Pilotage → Coin café · Boîte à idées**.
2. Choisissez **Ajouter une idée**, renseignez un titre et une description, puis utilisez
   **Déposer mon idée**.
3. Cliquez sur un badge **BU** pour filtrer par provenance. Vous pouvez combiner ce filtre avec
   la catégorie et la recherche dans les titres et descriptions.

La **démo locale** utilise des exemples fictifs. Les idées ajoutées dans la démo restent dans le
stockage local du navigateur. Pour le travail d’entreprise, utilisez l’installation protégée.

## Accès et visibilité

| Identité | Actions |
| --- | --- |
| Membre authentifié et habilité | Consulter le mur de la Factory et déposer une idée dans une BU autorisée pour son identité. |
| Agent identifié et évalué, lié à une BU | Consulter le mur par l’API et déposer une idée dans sa BU. |
| Administrateur humain global | Consulter le mur, déposer une idée depuis sa BU vérifiée et ouvrir la traçabilité d’un post-it. Le même groupe SSO porte le rôle `admin` et le périmètre global `["*"]`. |

Un administrateur global sans BU vérifiée dépose sous l’origine transversale
**Administration Factory** (`factory:administration`). Si une personne a plusieurs BU
autorisées, elle choisit celle du dépôt.

Une idée intitulée « Comparer les budgets des projets », déposée par la BU Opérations, affiche
**BU · Opérations** et la catégorie **Finance** à part. L’Observatoire des usages IA est une zone
du plan. Un badge **BU · Observatoire** suppose que l’opérateur a configuré une BU de ce nom et
habilité l’auteur à y déposer. [Observatoire des usages IA](../SHADOW_AI.md)

Le titre et la description doivent être partageables à l’échelle de la Factory. Gardez les
secrets, données personnelles et informations client dans les systèmes dédiés.

## Traçabilité du dépôt

L’identité exacte du déposant reste dans la traçabilité privée. Dans l’installation protégée,
un administrateur humain global utilise **Tracer le dépôt** sur le post-it pour la consulter
dans le panneau **Traçabilité du dépôt**. Le panneau affiche le type d’auteur, son identifiant
et la date du dépôt. La démo illustre les badges BU avec des données fictives conservées dans
le navigateur.

L’API enregistre l’identité vérifiée lors du dépôt dans la base privée `ideas.sqlite3`, pour
la même durée que le post-it. Avant de restituer la provenance, l’API enregistre l’événement
d’audit durable `ideas.author_trace` avec le demandeur, l’identifiant du post-it et l’horodatage.
Le contenu du post-it et le sujet du déposant restent hors de cet événement d’audit.

## Classement automatique

L’API attribue une catégorie et des tags contrôlés à partir de mots-clés français ou anglais.
Le calcul est local et déterministe. Une idée dont les mots-clés ne permettent pas de retenir
une catégorie apparaît dans la catégorie `uncategorized`.
Le titre et le texte sont conservés dans leur langue de saisie. Les libellés du mur suivent la
langue de l’interface. [Classifieur et routes API](../../api/app/ideas.py)

| Catégorie API | Tags associés |
| --- | --- |
| `security` | `access`, `compliance` |
| `automation` | `workflow`, `integration` |
| `data` | `analytics`, `quality` |
| `experience` | `usability`, `feedback` |
| `adoption` | `training`, `documentation` |
| `finance` | `financial_analysis`, `budget` |
| `uncategorized` | Sans tag reconnu |

Quelques titres pour essayer le classement.

| Titre | Catégorie et tags attendus |
| --- | --- |
| Revoir les accès SSO | Sécurité · `access` |
| Automate the approval workflow | Automatisation · `workflow` |
| Préparer une formation et sa documentation | Adoption · `training`, `documentation` |

## Intégration par l’API

Un agent passe par le proxy authentifié avec l’identité de service associée à sa BU. Le serveur
déduit l’identité et les origines autorisées de la session. Le titre accepte de 1 à 120
caractères et le texte de 1 à 1 000 caractères. Omettez `bu` si une seule origine est
autorisée. Si plusieurs origines sont autorisées, fournissez l’identifiant de l’origine
choisie. L’omission dans ce cas renvoie HTTP 422 avec `ideas_bu_required`. Une origine hors
périmètre renvoie HTTP 403.

```http
POST /api/v1/ideas HTTP/1.1
Host: factory.example.org
Content-Type: application/json

{
  "bu": "cc-relation",
  "title": "Automate the approval workflow",
  "text": "Review the current workflow and propose a checklist for the team."
}
```

`cc-relation` est un identifiant d’exemple. Utilisez un identifiant autorisé dans le déploiement.
La création renvoie HTTP 201 avec le post-it, sa catégorie, ses tags, sa date et
`visibility: "factory"`. Le champ `bu` est l’identifiant stable. `bu_label` est localisé selon
`?lang=fr` ou `?lang=en`.

| Requête | Usage |
| --- | --- |
| `GET /api/v1/ideas?lang=fr` | Récupérer le mur partagé, les origines de dépôt autorisées et les droits de création et de traçabilité. |
| `POST /api/v1/ideas` | Déposer une idée avec `title`, `text` et, si nécessaire, `bu`. |
| `POST /api/v1/ideas/{id}/trace?lang=fr` | Consulter la provenance privée avec le corps JSON `{}` et une session d’administrateur humain global. Réponse `Cache-Control: no-store`. |

Le paramètre `q` recherche dans tout l’historique visible, sans tenir compte des accents ni
de la casse. Les paramètres `category` et `bu` filtrent respectivement le thème et l’origine.
`limit` accepte de 1 à 120 résultats. Utilisez `next_cursor` dans `cursor` avec la même
identité, la même langue et les mêmes filtres. `next_cursor` vaut `null` à la dernière page.
Les notes ajoutées depuis la première page apparaissent lors d’une nouvelle recherche.

La réponse de traçabilité contient `id`, `bu`, `kind` (`employee`, `agent` ou `admin`),
`subject` (le sujet SSO exact), `created_at` (la date du dépôt) et `view` (le contexte
d’accès courant). Pour une idée absente, une BU retirée ou une ancienne idée sans provenance,
l’API renvoie HTTP 404 avec `idea_origin_unavailable`. Si l’enregistrement de l’audit échoue,
l’API renvoie HTTP 503 avec `ideas_trace_unavailable`.

## Déploiement et exploitation

Servez le tableau de bord et l’API derrière le proxy SSO. Activez `FACTORY_AUTH_MODE=proxy` et
configurez `FACTORY_ROLE_MAP` et `FACTORY_BU_POLICY` pour appliquer les périmètres d’accès.
L’enregistrement des agents reprend les identités de service et les évaluations du portefeuille.
[Sécurité par BU](../BU_SECURITY.md) · [Déploiement protégé](PORTFOLIO.md#déploiement-protégé)

Lorsque vous retirez la liaison d’un compte de service, retirez-le aussi des groupes SSO qui
lui donnent un rôle de lecture dans la Factory. Tant que le compte reste lié comme agent, la
révocation de son évaluation bloque son accès au mur.

Le mur affiche jusqu’à 120 idées à la fois. **Charger plus d’idées** permet de parcourir les
résultats anciens. Le stockage accepte 2 000 idées par origine de dépôt, BU ou Administration
Factory. Au plafond, le dépôt renvoie HTTP 409 avec `ideas_capacity_reached`.

Pour libérer de la place, planifiez une fenêtre d’archivage avec l’exploitant et le responsable
de l’origine concernée. Arrêtez les écritures en arrêtant l’API et sauvegardez le volume privé,
dont `ideas.sqlite3`. Vérifiez la restauration de cette sauvegarde avant toute suppression.
Sur la base active, activez `PRAGMA foreign_keys = ON` avant d’ouvrir une transaction SQLite.
Sélectionnez les identifiants à archiver pour la seule origine approuvée (`bu`) et vérifiez-les
avant de supprimer ces lignes d’`ideas`. La suppression retire aussi les lignes associées
d’`idea_origins`. Vérifiez le périmètre et le nombre de suppressions avant de valider, puis
redémarrez l’API et repartez de la première page. Conservez la sauvegarde sous les mêmes
contrôles d’accès que la base active, car elle contient la provenance privée.

Pour la recette, déposez une idée synthétique depuis une BU et vérifiez sa présence dans le
mur d’une autre BU. Essayez ensuite de créer une idée dans cette seconde BU avec l’identité
de la première. Vérifiez le refus. Rejouez ces deux contrôles avec un agent évalué. Vérifiez
ensuite le refus de traçabilité pour un membre ordinaire, puis consultez l’origine avec un
administrateur humain global et contrôlez l’événement d’audit correspondant.
