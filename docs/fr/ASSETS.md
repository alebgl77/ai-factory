# Pack visuel : génération dans ChatGPT et intégration

*[English version](../ASSETS.md) (prompts en anglais)*

Ce document liste les 15 images qui enrichissent le rendu du dashboard 3D, avec un prompt prêt
à coller pour chacune. Les images sont **facultatives** : si l'une manque, le dashboard garde
son rendu procédural actuel. Les prompts ci-dessous sont en français ; la
[version anglaise](../ASSETS.md) donne les mêmes prompts en anglais, pour les mêmes fichiers.

## Mode opératoire (≈ 30 min)

1. Ouvrir **une seule conversation** ChatGPT et y coller d'abord le message de cadrage
   ci-dessous : le style restera homogène d'une image à l'autre.
2. Coller ensuite les prompts un par un, dans l'ordre. Pour chaque image, vérifier le
   format demandé (carré 1024 × 1024, paysage 1536 × 1024 ou portrait 1024 × 1536) ; sinon,
   écrire « même image, au format … ».
3. Télécharger chaque image en PNG et la **renommer exactement** comme indiqué
   (`floor-oak.png`, etc.).
4. Déposer les fichiers dans `dashboard/assets/src/`, puis lancer :
   ```bash
   pip install -r scripts/requirements-assets.txt
   python scripts/build_assets.py            # contrôle, optimise et publie dashboard/assets/assets.js
   python scripts/build_assets.py --check    # liste ce qui manque ou ne respecte pas le format
   ```

Le script :
- rend les textures raccordables (fondu des bords) ;
- détoure les fonds magenta (`#FF00FF`) quand l'image n'a pas de transparence ;
- découpe la planche de portraits en 12 avatars ;
- convertit en WebP ;
- écrit un bundle `dashboard/assets/assets.js` qui fonctionne aussi hors ligne (`file://`) ;
- trace la provenance (empreinte SHA-256, générateur, date) dans `dashboard/assets/PROVENANCE.md`.

## Règles communes

- **Données fictives uniquement** : aucune personne réelle, aucune célébrité, aucune marque,
  aucun logo existant, aucun texte lisible (sauf mention contraire).
- **Fond magenta** `#FF00FF` uni pour les éléments à détourer : le script le rend transparent.
  ChatGPT ne garantit pas la transparence native selon le modèle utilisé.
- **Licence** : par décision de l'auteur, les images du pack visuel sont publiées sous la licence
  MIT du dépôt, avec leur provenance (SHA-256). Selon les conditions d'utilisation d'OpenAI, les
  images générées appartiennent à l'utilisateur, qui peut les publier ; point à vérifier dans les
  conditions en vigueur au moment de la génération. La protection par le droit d'auteur d'une
  image entièrement générée n'est pas garantie selon les pays (voir [LICENSES.md](LICENSES.md)).

## Message de cadrage (à coller en premier)

```text
Tu vas générer une série cohérente d'images pour le jumeau numérique 3D d'un bureau tech
fictif, « l'AI Factory » : open space, laboratoire sécurisé, R&D Lab, Skills Lab (formation),
salle de supervision d'agents IA, coin café, accueil. Style commun à toute la série :
architecture d'intérieur scandinave moderne, lumière du jour douce et diffuse, palette chêne
clair, blanc cassé #F8FAFC, gris ardoise #334155, accents violet #6D28D9 et sarcelle #0EA5A4.
Règles strictes pour chaque image : aucun texte lisible, aucun logo ni marque existante,
aucune personne réelle, pas de filigrane. Respecte exactement le format demandé. Quand je
demande un fond magenta, utilise un fond uni #FF00FF parfaitement plat, sans ombre portée ni
dégradé. Réponds uniquement par l'image.
```

## Les 15 images

### Textures raccordables (carré 1024 × 1024, vue de dessus orthographique)

| Fichier | Usage dans la 3D |
|---|---|
| `floor-oak.png` | sols du coin café et du Lab sécurisé |
| `floor-carpet.png` | Delivery floor et Skills Lab |
| `floor-concrete.png` | accueil, R&D Lab, salle Agent Ops |
| `floor-terrazzo.png` | sanitaires et War Room |
| `wood-desk.png` | plateaux de bureaux, tables, comptoir |
| `felt-acoustic.png` | focus pods, panneaux acoustiques, assises |

**floor-oak.png**
```text
Texture de sol, vue strictement de dessus, orthographique, sans perspective : parquet en chêne
clair posé en point de Hongrie, lames de 10 cm, veinage fin naturel, finition mate. Éclairage
parfaitement uniforme, aucune ombre, aucun reflet, aucune vignette. Texture raccordable sans
couture sur les quatre bords (tileable). Format carré 1024 × 1024.
```

**floor-carpet.png**
```text
Texture de sol, vue strictement de dessus, orthographique : moquette en dalles 50 × 50 cm,
bouclette dense gris anthracite légèrement bleuté, fines variations de fibres, joints de dalles
à peine visibles. Éclairage uniforme, aucune ombre. Texture raccordable sans couture sur les
quatre bords. Format carré 1024 × 1024.
```

**floor-concrete.png**
```text
Texture de sol, vue strictement de dessus, orthographique : béton ciré gris très clair, nuances
nuageuses subtiles, micro-granulats, aspect satiné doux. Éclairage uniforme, aucune ombre ni
reflet de fenêtre. Texture raccordable sans couture sur les quatre bords. Format carré
1024 × 1024.
```

**floor-terrazzo.png**
```text
Texture de sol, vue strictement de dessus, orthographique : terrazzo blanc cassé avec éclats de
marbre gris ardoise, sarcelle et violet discret (petits fragments de 2 à 8 mm), poli mat.
Éclairage uniforme, aucune ombre. Texture raccordable sans couture sur les quatre bords. Format
carré 1024 × 1024.
```

**wood-desk.png**
```text
Texture de surface de bureau, vue strictement de dessus, orthographique : placage de chêne
clair à fil droit, veinage fin et régulier orienté horizontalement, finition huilée mate.
Éclairage uniforme, aucune ombre, aucun objet. Texture raccordable sans couture sur les quatre
bords. Format carré 1024 × 1024.
```

**felt-acoustic.png**
```text
Texture de tissu acoustique, vue de face orthographique : feutre de laine épais gris chiné avec
une très légère teinte violette, fibres visibles en gros plan, sans motif. Éclairage uniforme,
aucune ombre. Texture raccordable sans couture sur les quatre bords. Format carré 1024 × 1024.
```

### Environnement

**ground-plaza.png** (carré 1024 × 1024) : sol paysager autour du bâtiment
```text
Vue aérienne strictement verticale, orthographique, d'un parvis paysager entourant un immeuble
de bureaux : allées en dalles claires, pelouses, bosquets d'arbres vus du dessus, bancs, bandes
de vélos, quelques massifs fleuris discrets. Le centre de l'image (un rectangle d'environ 55 % de
la largeur et 40 % de la hauteur) est une dalle grise uniforme et vide, où sera posé le
bâtiment. Les bords de l'image se fondent vers un gris-vert uni. Lumière douce de fin de
matinée, ombres très légères. Aucun véhicule, aucune personne, aucun texte. Format carré
1024 × 1024.
```

**skyline-panorama.png** (paysage 1536 × 1024) : toile de fond à 360°
```text
Panorama d'horizon pour un décor de fond : silhouettes lointaines et brumeuses d'une ville
moderne et de collines, ciel dégradé doux du bleu pâle au pêche clair, quelques nuages légers.
Contraste faible, couleurs pastel, aucun bâtiment reconnaissable, aucun texte. La ligne
d'horizon se situe au tiers inférieur. Les bords gauche et droit doivent se raccorder sans
couture (l'image sera enroulée à 360°). Format paysage 1536 × 1024.
```

### Décor

**art-1.png** (portrait 1024 × 1536) : tableau mural de la War Room
```text
Œuvre d'art abstraite encadrée, vue de face parfaitement frontale : composition géométrique de
formes organiques douces évoquant un réseau de neurones et des flux de données, palette violet
#6D28D9, sarcelle #0EA5A4, sable et blanc cassé, texture de toile visible. Cadre fin en chêne
clair. L'œuvre remplit toute l'image, sans mur ni ombre autour. Aucun texte. Format portrait
1024 × 1536.
```

**art-2.png** (portrait 1024 × 1536) : tableau mural du coin café
```text
Affiche artistique encadrée, vue de face frontale : paysage minimaliste de collines stylisées
en aplats superposés, soleil bas, palette pêche, terracotta doux, sarcelle et crème, grain
d'impression risographie. Cadre fin noir mat. L'affiche remplit toute l'image, sans mur ni
ombre. Aucun texte. Format portrait 1024 × 1536.
```

**art-3.png** (paysage 1536 × 1024) : grande œuvre de l'accueil
```text
Grande œuvre murale abstraite encadrée, vue de face frontale : vagues de lignes fines
parallèles formant des courbes de niveau, comme une carte topographique de données, dégradé
du violet profond au sarcelle sur fond crème. Cadre fin en chêne clair. L'œuvre remplit toute
l'image, sans mur ni ombre. Aucun texte. Format paysage 1536 × 1024.
```

**logo-factory.png** (carré 1024 × 1024, fond magenta) : emblème de l'usine
```text
Emblème graphique original pour une « AI Factory » fictive : un monogramme géométrique
combinant un hexagone, des engrenages stylisés et un nœud de réseau neuronal, traits épais
arrondis, dégradé violet #6D28D9 vers sarcelle #0EA5A4, style flat moderne. Aucune lettre,
aucun texte. Centré, occupant 70 % de l'image, sur fond uni magenta #FF00FF parfaitement plat.
Format carré 1024 × 1024.
```

### Personnages

**avatars-sheet.png** (paysage 1536 × 1024) : planche des 12 portraits de l'équipe
```text
Planche de 12 portraits-avatars en buste pour une application, disposés en grille exacte de
4 colonnes × 3 rangées, cellules de taille égale séparées par des marges régulières, chaque
buste centré dans sa cellule, de face ou de trois quarts, sur fond uni blanc cassé #F8FAFC
identique dans toutes les cellules. Style illustration vectorielle moderne cohérente (aplats
doux, légers dégradés), personnages entièrement fictifs. Ordre de lecture de gauche à droite
puis de haut en bas :
1 peau claire rosée, cheveux châtains mi-longs, pull violet ;
2 peau mate cuivrée, cheveux noirs courts bouclés, chemise magenta ;
3 peau dorée, longs cheveux noirs lisses, haut rose framboise ;
4 peau très claire, cheveux roux courts, lunettes rondes, veste bleu pétrole ;
5 peau claire, cheveux blonds attachés, chemise vert sapin ;
6 peau brune, barbe courte, cheveux noirs ras, pull brun caramel ;
7 peau claire rosée, carré gris argenté, blazer gris ardoise ;
8 peau claire, cheveux bruns en bataille, sweat vert olive ;
9 peau mate cuivrée, cheveux bouclés volumineux, haut lilas clair ;
10 peau claire, cheveux blonds courts, col roulé bordeaux ;
11 peau foncée, tresses fines relevées, pull gris pierre ;
12 peau dorée, cheveux noirs mi-longs en chignon, chemise lavande.
Aucun texte, aucun nom. Format paysage 1536 × 1024.
```

**mascot-visitor.png** (portrait 1024 × 1536, fond magenta) : personnage du mode visite
```text
Petit personnage mascotte original pour un mode « visite » façon vue immersive : silhouette
ronde et sympathique, corps jaune ambre #F5B301 en volume doux (style jouet 3D mat), bras
légèrement levés comme s'il était attrapé par le haut, petit sac à dos sarcelle, aucun visage
détaillé (deux points pour les yeux). Vue de trois quarts, pieds visibles, centré, occupant
80 % de la hauteur, sur fond uni magenta #FF00FF parfaitement plat. Aucun texte. Format
portrait 1024 × 1536.
```

### Interface

**hero-isometric.png** (paysage 1536 × 1024) : illustration de l'écran d'aide et du README
```text
Illustration isométrique soignée d'un plateau de bureaux tech en coupe (maquette sans toit) :
open space de 6 postes, salle de réunion vitrée, laboratoire sécurisé avec une baie serveur
lumineuse, atelier de formation, salle de supervision avec un grand mur d'écrans de courbes,
coin café, accueil avec casiers, plantes. Petits personnages fictifs stylisés au travail.
Style illustration 3D douce, lumière du jour, palette chêne clair, blanc cassé, ardoise,
accents violet #6D28D9 et sarcelle #0EA5A4, fond uni très clair. Aucun texte, aucun logo.
Format paysage 1536 × 1024.
```

## Correspondance avec le code

| Identifiant | Fichier source | Traitement | Utilisation |
|---|---|---|---|
| `floor-*`, `wood-desk`, `felt-acoustic` | PNG 1024² | raccord + WebP 1024 | `map` des matériaux, UV en coordonnées monde (1 répétition ≈ 2 m) |
| `ground-plaza` | PNG 1024² | WebP 1024, bords fondus | plan au sol de 64 × 64 m sous le bâtiment |
| `skyline-panorama` | PNG 1536 × 1024 | raccord horizontal + WebP | cylindre de fond (3 répétitions), teinte nuit en thème sombre |
| `art-1..3` | PNG | WebP | cadres sur les murs (masqués avec les murs en coupe) |
| `logo-factory` | PNG magenta | détourage + WebP | enseigne de l'accueil, logo de l'en-tête |
| `avatars-sheet` | PNG 1536 × 1024 | découpe 4 × 3, WebP 256² | pastilles des personnes et tuiles de visio de la War Room (`p01` à `p12` dans l'ordre de `config/profiles.yaml`) |
| `mascot-visitor` | PNG magenta | détourage + WebP | bouton « Visite » et personnage glissé |
| `hero-isometric` | PNG | WebP 1280 | aide (`?`) et README |
