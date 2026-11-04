# Gouvernance : registre, niveaux de risque, réévaluation, correspondances

*[English version](../GOVERNANCE.md)*

Ce document décrit le pilier « gouvernance » de Virtual AI Factory : le registre des use
cases, la qualification AI Act, le cycle NIST AI RMF, la réévaluation à chaque version de
modèle et la table de correspondance NIST AI RMF ↔ AI Act ↔ ISO/IEC 42001.

> Tout est **indicatif** : ce dépôt n'est pas un avis juridique. Les données de démonstration
> sont **fictives**. Les dates réglementaires sont de la configuration (`config/factory.yaml`)
> et se vérifient au Journal officiel de l'UE avant tout usage.

## 1. Registre des use cases (GitOps)

Un fichier par use case dans `config/usecases/<id>.yaml` (schéma : [CONTRACT.md](../CONTRACT.md), section 2.3, en anglais).
Le registre est versionné dans Git : une revue de risque, un changement de modèle ou une
dérogation laissent une trace de commit. L'API recharge la configuration à chaud et superpose
les décisions saisies dans le dashboard (`FACTORY_STATE_DIR`). Les champs de texte sont une
chaîne simple ou un dictionnaire `{en, fr}` ; l'API les résout selon `?lang=en|fr`.

| Bloc | Rôle |
|---|---|
| `owner`, `cost_center`, `stage`, `task_type` | responsabilité, imputation FinOps, phase `cadrage → poc → mvp → indus → run → retired` |
| `sensitivity` | classe de données C1 (public) à C4 (secret) ; C3/C4 imposent l'alias `chat-sensitive` (GPU local) |
| `ai_act` | rôle (fournisseur, déployeur ou les deux), pratiques interdites, domaine et point de l'annexe III, dérogation art. 6(3), composant de sécurité annexe I, devoirs de transparence art. 50, usage d'un modèle GPAI |
| `model` | alias LiteLLM, version courante, historique des versions |
| `assessment` | dernière revue : version de modèle revue, date, relecteur, décision, conditions |
| `nist` | statut `done / partial / todo` des fonctions Govern, Map, Measure, Manage, avec preuves et écarts |
| `iso42001` | mesures de l'annexe A mises en œuvre (preuves de la table de correspondance) |
| `rag`, `eval`, `kpis` | paramètres RAG, golden set et juge, valeur attendue |

`python scripts/validate_config.py` contrôle la cohérence (personnes, centres de coût, alias,
classes sensibles, identifiants NIST / AI Act / ISO, sessions, bancs R&D…).

## 2. Qualification AI Act

Le moteur `api/app/engines/risk.py` applique, dans l'ordre :

1. **Interdit** si `prohibited_practices` est non vide (art. 5) : aucun déploiement.
2. **Haut risque** si un domaine de l'annexe III est renseigné **sans** dérogation art. 6(3),
   ou si le système est un composant de sécurité d'un produit de l'annexe I.
3. **Transparence (limité)** si un devoir de l'art. 50 s'applique : `chatbot` (50(1)),
   `synthetic_content` (50(2)), `emotion_recognition` (50(3)), `deepfake` (50(4)).
4. **Minimal** sinon (l'art. 4 sur la maîtrise de l'IA s'applique à tous les niveaux).

`gpai` est un indicateur orthogonal : les obligations du chapitre V pèsent sur le fournisseur
du modèle ; l'usine, en aval, exige sa documentation (art. 53(1)(b)) et gère la chaîne de
valeur. La **dérogation art. 6(3)** n'est retenue que si elle est documentée avant la mise en
service (art. 6(4)), ne repose sur aucun profilage et s'accompagne de l'enregistrement de
l'art. 49(2), que le règlement 2026/1744 maintient avec moins d'informations.

`applicable_from` donne le jalon qui gouverne le niveau (annexe III, annexe I, art. 50, art. 5
ou, pour le niveau minimal, art. 4) et `days_left` le délai restant.

### Use cases de la démo (fictifs)

| Id | Use case | Données | Niveau | Point clé de gouvernance |
|---|---|---|---|---|
| uc-01 | Assistant RAG documentaire | C2 | limité (chatbot) | revue à jour sur 2026-09 |
| uc-02 | Agent support multi-étapes (GPAI, LangGraph) | C2 | limité (chatbot) | modèle 2026-10 non revu : **déploiement bloqué** |
| uc-03 | Tri des demandes entrantes | C2 | minimal | classification sur modèle premium (alerte FinOps) |
| uc-04 | Présélection de candidatures | C3 | **haut risque**, annexe III 4 a) | modèle local 2026-10 non revu : **bloqué**, tests de biais à rejouer |
| uc-05 | Contrôle de complétude des dossiers de prêt | C3 | minimal, **dérogation 6(3) a)** documentée | domaine annexe III 5 b) sans évaluation de solvabilité |
| uc-06 | Contenus marketing | C1 | limité (contenu synthétique) | marquage art. 50(2), déployeur d'un outil tiers |
| uc-07 | Synthèse de comités internes | C3 | minimal | revu après la mise à niveau du modèle local |
| uc-08 | Analyse de clauses confidentielles | C4 | minimal | cadrage, revue en attente |

## 3. Cycle NIST AI RMF dans l'usine

| Fonction | Ce qui l'alimente dans l'usine | Exemples de preuves |
|---|---|---|
| **Govern** | politique IA, RACI, rôles de contrôle humain, parcours art. 4 du Skills Lab | `profiles.yaml`, `skills.yaml`, politique versionnée |
| **Map** | fiche de cadrage, classe de données, qualification AI Act, cartographie des outils MCP | blocs `ai_act`, `sensitivity`, `catalog/tools.yaml` |
| **Measure** | golden sets, RAGAS, LLM-as-judge, red team, tests de biais, Agent Ops | porte d'évaluation, `eval_gates`, traces Langfuse |
| **Manage** | décision go / no-go, retour arrière automatique, boucle de données, coupe-circuit, incidents | `rollback`, `dataloop`, salle Agent Ops |

`nist_progress` vaut la part des quatre fonctions au statut `done` (`partial` compte pour 0,5).
Les libellés des 19 catégories et 72 sous-catégories sont dans
`config/frameworks/nist_ai_rmf.yaml` (libellés courts ; la version française est une traduction libre) ; le profil IA générative NIST AI 600-1
y est référencé avec ses 12 risques, utilisés pour construire les campagnes red team.

## 4. Réévaluation à chaque changement de version de modèle

Règle ([CONTRACT.md](../CONTRACT.md), section 2.4) : `reevaluation_required` est vrai si `model.version` diffère de
`assessment.reviewed_model_version`, ou si la décision est `pending`. La porte `deploy` du
pipeline LLMOps est alors **bloquée** avec la raison `risk_reassessment`, quel que soit le
résultat des évaluations.

- `POST /api/v1/registry/{id}/model-version {version, name?}` ajoute la version à l'historique,
  la rend courante et lève le drapeau (la réponse l'indique).
- `POST /api/v1/registry/{id}/assessment {decision, reviewer}` enregistre la revue de la
  version courante ; une décision `approved` ou `approved_with_conditions` lève le blocage.

Pourquoi chaque version ? Un nouveau modèle, même sous le même alias, peut changer la
qualité, les biais ou la robustesse ; chez un fournisseur, une modification substantielle
impose une nouvelle évaluation de conformité (art. 43(4)) et un déployeur qui modifie
substantiellement un système à haut risque en devient fournisseur (art. 25(1)). Dans la démo,
la mise à niveau du modèle local 2026-10 a été revue pour uc-05 et uc-07, pas encore pour uc-04.

## 5. Table de correspondance (crosswalk)

`config/frameworks/crosswalk.yaml` regroupe 24 familles de contrôles. Chaque famille relie des
sous-catégories NIST, des articles de l'AI Act et des clauses ou mesures ISO/IEC 42001, avec
les niveaux pour lesquels elle est **exigée** par la politique de l'usine, un indice de preuve
et la base de la correspondance.

- **Contrôles requis** : pour un use case, les familles dont `tiers` contient son niveau
  (ou `gpai` s'il utilise un modèle GPAI).
- **Contrôle évidencé** : une des références ISO de la famille figure dans la liste `iso42001`
  du use case. `controls_coverage` est la part des familles requises évidencées.
- Familles exigées au-delà de la lettre du règlement (évaluation, réévaluation par version,
  placement souverain, sobriété) : choix de politique de l'usine, signalé dans `basis`.

| Famille (extrait) | NIST AI RMF | AI Act | ISO/IEC 42001 |
|---|---|---|---|
| Gestion des risques | GOVERN 1.3, MAP 1.1, MEASURE 1.1, MANAGE 1.1-1.3 | art. 9 | 6.1, 8.2, 8.3, A.5.2 |
| Gouvernance des données | MAP 2.3, MEASURE 2.1 | art. 10 | A.4.3, A.7.2-A.7.6 |
| Journalisation | MEASURE 2.4, GOVERN 4.3 | art. 12, 19, 26(6) | A.6.2.8 |
| Contrôle humain | GOVERN 3.2, MAP 3.5, MANAGE 2.4 | art. 14, 26(2) | A.9.2, A.9.3 |
| Réévaluation par version | GOVERN 1.5, MANAGE 3.2, MEASURE 3.1 | art. 43(4), 25, 3(23) | 6.3, A.6.2.5 |
| Incidents et retour arrière | MANAGE 2.3, 2.4, 4.3, GOVERN 4.3 | art. 73, 26(5) | 10.2, A.8.4, A.3.3 |
| Maîtrise de l'IA | GOVERN 2.2, MAP 3.4 | art. 4 | 7.2, 7.3, A.4.6 |

Limites : la correspondance est **indicative** ; le texte ISO est payant, les références ISO
reposent sur les intitulés publiés par des sources secondaires ; une certification
ISO/IEC 42001 ne vaut pas présomption de conformité à l'AI Act (elle n'est pas, à notre
connaissance au 2026-11-03, une norme harmonisée citée au JO au titre du règlement, point
**à vérifier**).

## 6. Calendrier réglementaire (AI Act)

Règlement (UE) 2024/1689 modifié par le règlement (UE) 2026/1744 « Omnibus numérique sur
l'IA », publié au JO le **24 juillet 2026**, en vigueur le **27 juillet 2026**. Statut :
« confirmé » = au moins deux sources concordantes ; « à vérifier » = source unique ou
divergente. Le texte du JO n'a pas pu être consulté directement depuis l'environnement de
build (accès réseau restreint) : les dates sont recoupées par des sources secondaires.

| Date | Jalon | Statut |
|---|---|---|
| 2025-02-02 | Art. 5 pratiques interdites ; art. 4 maîtrise de l'IA | confirmé (texte initial) |
| 2025-08-02 | Chapitre V, modèles GPAI | confirmé (texte initial) |
| 2026-07-27 | Entrée en vigueur de 2026/1744 : art. 4 réécrit | confirmé |
| 2026-08-02 | Art. 50 transparence ; amendes GPAI de la Commission (art. 101) | confirmé |
| 2026-12-02 | Art. 50(2) marquage : fin du délai pour les systèmes mis sur le marché avant le 2026-08-02 | confirmé |
| 2026-12-02 | Nouvelles interdictions art. 5 (images intimes non consenties, contenus pédocriminels) | à vérifier |
| 2027-08-02 | Modèles GPAI mis sur le marché avant le 2025-08-02 (art. 111(3)) ; bacs à sable nationaux | confirmé / à vérifier |
| 2027-12-02 | Haut risque annexe III (au lieu du 2026-08-02) | confirmé |
| 2028-08-02 | Haut risque annexe I (au lieu du 2027-08-02) | confirmé |

**Art. 4 réécrit** : l'obligation d'« assurer un niveau suffisant » de maîtrise de l'IA devient
une obligation de **prendre des mesures pour soutenir** la maîtrise de l'IA, sans garantir un
niveau donné ; la Commission et les États membres soutiennent les fournisseurs et
déployeurs. Formulation exacte **à vérifier au JO**. Mise en œuvre : [SKILLS_RND.md](SKILLS_RND.md).

Les dates fixes de l'annexe III et de l'annexe I remplacent le mécanisme conditionnel
(disponibilité des normes) de la proposition de la Commission (COM(2025) 836).

## 7. Adapter à une organisation

1. Remplacer les use cases fictifs par les siens (un fichier chacun), qualifier `ai_act`.
2. Ajuster `eval_gates` et la politique de `tiers` du crosswalk à l'appétence au risque.
3. Faire valider la qualification et la table de correspondance par un juriste.
4. `python scripts/validate_config.py`, puis `python scripts/build_snapshot.py`.

## Sources

Consultées le **2026-11-03** par moteur de recherche. L'environnement de build n'a pas pu
ouvrir directement ces pages (accès réseau restreint) : leur contenu est connu par les extraits
indexés, recoupés entre sources, et, pour l'AI Act initial et le NIST AI RMF, par les textes
officiels publiés avant 2026. Seules les dates marquées « confirmé » au § 6 ont été recoupées
par au moins deux sources.

Textes officiels :

- Règlement (UE) 2024/1689 (AI Act), JO L du 12.7.2024 : <https://eur-lex.europa.eu/eli/reg/2024/1689/oj>
- Règlement (UE) 2026/1744 (Omnibus numérique sur l'IA), JO L du 24.7.2026 : <https://eur-lex.europa.eu/legal-content/EN/TXT/?uri=OJ:L_202601744>
- NIST AI 100-1, *AI Risk Management Framework 1.0*, 26 janvier 2023 : <https://doi.org/10.6028/NIST.AI.100-1>
- NIST AI 600-1, *Generative AI Profile*, 26 juillet 2024 : <https://doi.org/10.6028/NIST.AI.600-1>
- NIST AI Resource Center (Playbook) : <https://airc.nist.gov/>
- ISO/IEC 42001:2023, fiche ISO (norme payante) : <https://www.iso.org/standard/81230.html>

Sources secondaires (Omnibus 2026/1744) :

- Hunton, « EU Digital Omnibus on AI Enters Into Force » : <https://www.hunton.com/privacy-and-cybersecurity-law-blog/eu-digital-omnibus-on-ai-enters-into-force>
- K&L Gates, « EU Digital Omnibus on AI Enters Into Force » (31.7.2026) : <https://www.klgates.com/EU-Digital-Omnibus-on-AI-Enters-Into-Force-7-31-2026>
- White & Case, « EU AI Omnibus enters into force, amending the AI Act » : <https://www.whitecase.com/insight-alert/eu-ai-omnibus-enters-force-amending-ai-act>
- Cooley, « Digital AI Omnibus delays key deadlines, introduces new rules » : <https://cdp.cooley.com/digital-ai-omnibus-delays-key-deadlines-introduces-new-rules/>
- SCL, « Council of the EU approves AI Omnibus reforms » (approbation du Conseil du 29.6.2026) : <https://www.scl.org/council-of-the-eu-approves-ai-omnibus-reforms/>
- Gibson Dunn, « EU AI Act Omnibus Agreement » : <https://www.gibsondunn.com/eu-ai-act-omnibus-agreement-postponed-high-risk-deadlines-and-other-key-changes/>
- Cloud Security Alliance, note sur le délai de marquage art. 50(2) : <https://labs.cloudsecurityalliance.org/research/csa-research-note-eu-ai-act-article50-watermarking-deadline/>
- Center for Democracy and Technology, enregistrement art. 49(2) maintenu : <https://cdt.org/insights/final-ai-omnibus-text-dilutes-fundamental-rights-protections/>
- Digital Policy Alert, nouvelles interdictions art. 5 au 2.12.2026 : <https://digitalpolicyalert.org/event/42386-digital-omnibus-on-ai-regulation-prohibiting-nudification-apps-and-ai-systems-generating-or-manipulating-child-sexual-abuse-material-becomes-applicable>
- Law & Technology, « AI literacy: the Digital Omnibus rewrites Article 4 » : <https://lawandtechnology.eu/en/ai-literacy-digital-omnibus-article-4-ai-act/>
- Matheson, avis conjoint EDPB-EDPS sur l'Omnibus IA (nouvel art. 4a remplaçant l'art. 10(5) dans la proposition) : <https://www.matheson.com/insights/edpb-edps-publishes-opinion-on-ai-digital-omnibus-proposal/>
- Proposition de la Commission COM(2025) 836 du 19.11.2025 (copie du Parlement autrichien) : <https://www.parlament.gv.at/dokument/XXVIII/EU/45688/imfname_11543574.pdf>

Sources secondaires (identifiants NIST et ISO) :

- ISMS.online, mesures de l'annexe A d'ISO/IEC 42001 : <https://www.isms.online/iso-42001/annex-a-controls/>
- Modulos, annexes A à D d'ISO/IEC 42001 : <https://docs.modulos.ai/frameworks/iso-42001/annexes-a-d>
- STIG Viewer, catalogue AI RMF 1.0 : <https://www.stigviewer.com/controls/ai-rmf/catalog>

Points non confirmés, à vérifier au JO : formulation exacte du nouvel art. 4 ; date et
périmètre des nouvelles interdictions de l'art. 5 ; échéance des bacs à sable nationaux ;
numérotation finale de la base juridique « données sensibles pour la détection des biais »
(art. 10(5) ou nouvel art. 4a) ; statut d'ISO/IEC 42001 au regard des normes harmonisées.
