# Coin café et boîte à idées

[Démo](#essayer-la-démo) · [Accès et visibilité](#accès-et-visibilité) · [Classement](#classement-automatique) · [API agents](#intégration-par-lapi) · [Exploitation](#déploiement-et-exploitation)

Le **Coin café** rassemble des propositions courtes sous forme de post-its. Chaque idée indique
sa BU d’origine, sa catégorie et ses tags. Dès sa création, elle est visible par toute la
Factory authentifiée.

## Essayer la démo

1. Ouvrez le **Coin café** sur la carte 3D ou le plan. Le même mur est accessible depuis
   **Pilotage → Coin café · Boîte à idées**.
2. Choisissez **Ajouter une idée**, renseignez un titre et une description, puis utilisez
   **Déposer mon idée**.
3. Filtrez les post-its par catégorie ou recherchez des mots dans leurs titres et descriptions.

La **démo locale** utilise des exemples fictifs. Les idées ajoutées dans la démo restent dans le
stockage local du navigateur. Pour le travail d’entreprise, utilisez l’installation protégée.

## Accès et visibilité

| Identité | Actions |
| --- | --- |
| Membre authentifié et habilité | Consulter le mur de la Factory et déposer une idée dans une BU autorisée pour son identité. |
| Agent identifié et évalué, lié à une BU | Consulter le mur par l’API et déposer une idée dans sa BU. |
| Administrateur humain global | Consulter le mur et déposer une idée dans une BU configurée. Le même groupe SSO porte le rôle `admin` et le périmètre global `["*"]`. |

Le titre et la description doivent être partageables à l’échelle de la Factory. Gardez les
secrets, données personnelles et informations client dans les systèmes dédiés.

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
| `uncategorized` | Sans tag reconnu |

Quelques titres pour essayer le classement.

| Titre | Catégorie et tags attendus |
| --- | --- |
| Revoir les accès SSO | Sécurité · `access` |
| Automate the approval workflow | Automatisation · `workflow` |
| Préparer une formation et sa documentation | Adoption · `training`, `documentation` |

## Intégration par l’API

Un agent passe par le proxy authentifié avec l’identité de service associée à sa BU. Le serveur
déduit l’identité et les habilitations de la session. Le client transmet la BU, le titre et le
texte. Le titre accepte de 1 à 120 caractères et le texte de 1 à 1 000 caractères.

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
| `GET /api/v1/ideas?lang=fr` | Récupérer le mur partagé, les BU disponibles pour la session et le droit de création. |
| `POST /api/v1/ideas` | Déposer une idée avec `{bu, title, text}`. |

## Déploiement et exploitation

Servez le tableau de bord et l’API derrière le proxy SSO. Activez `FACTORY_AUTH_MODE=proxy` et
configurez `FACTORY_ROLE_MAP` et `FACTORY_BU_POLICY` pour appliquer les périmètres d’accès.
L’enregistrement des agents reprend les identités de service et les évaluations du portefeuille.
[Sécurité par BU](../BU_SECURITY.md) · [Déploiement protégé](PORTFOLIO.md#déploiement-protégé)

Lorsque vous retirez la liaison d’un compte de service, retirez-le aussi des groupes SSO qui
lui donnent un rôle de lecture dans la Factory. Tant que le compte reste lié comme agent, la
révocation de son évaluation bloque son accès au mur.

Le mur affiche les 120 idées les plus récentes. Les idées plus anciennes restent en base
jusqu’à intervention de l’exploitant. Le stockage accepte 2 000 idées par BU et refuse les
nouveaux dépôts au-delà de ce plafond. Sauvegardez `ideas.sqlite3` avec le volume privé de l’API.

Pour la recette, déposez une idée synthétique depuis une BU et vérifiez sa présence dans le
mur d’une autre BU. Essayez ensuite de créer une idée dans cette seconde BU avec l’identité
de la première. Vérifiez le refus. Rejouez ces deux contrôles avec un agent évalué.
