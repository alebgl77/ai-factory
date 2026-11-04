# Skills Lab et R&D Lab : maîtrise de l'IA (art. 4), rôles, expérimentation

*[English version](../SKILLS_RND.md)*

Moteur : `api/app/engines/skills.py`. Configuration : `config/skills.yaml` et
`config/profiles.yaml` (titres, libellés et notes en anglais et en français). Données **fictives**. Dans la scène 3D, le Skills Lab est la salle
`S1` (8 places), le R&D Lab regroupe les bancs `X1` à `X3` et la salle Agent Ops les consoles
`O1` et `O2`.

## 1. Article 4 tel que réécrit par le règlement (UE) 2026/1744

Depuis le 27 juillet 2026, l'art. 4 ne demande plus aux fournisseurs et déployeurs
d'« assurer un niveau suffisant » de maîtrise de l'IA : ils **prennent des mesures pour
soutenir** la maîtrise de l'IA de leur personnel et des personnes qui utilisent des systèmes
d'IA pour leur compte, sans obligation de garantir un niveau individuel ; la Commission et
les États membres soutiennent ces efforts. Formulation exacte à vérifier au JO (sources :
[GOVERNANCE.md](GOVERNANCE.md)).

L'usine traduit cela en **mesures adaptées au rôle, au risque et au contexte**, et conserve
les preuves :

| Exigence | Mise en œuvre |
|---|---|
| Mesures par rôle | `requirements` : modules exigés pour chacun des 11 rôles, au moins un module `art4: true` par rôle (contrôlé par `validate_config.py`) |
| Adaptées au risque | modules supplémentaires pour qui touche au haut risque : contrôle humain (art. 14 et 26), biais, incidents |
| Adaptées au contexte | usage sûr des classes C1–C4 et du GPU local, supervision d'agents pour la salle Agent Ops |
| Preuves | `completions` datées, `sessions` planifiées, couverture par rôle et par personne dans `/api/v1/skills` |

Le moteur calcule la couverture globale, la couverture des modules art. 4 et les modules
art. 4 **en retard** (manquants et non planifiés en session). Statut `measures_in_place` si
chaque rôle occupé a une mesure art. 4 et si la couverture art. 4 atteint 80 % ; `partial`
sinon. Dans la démo : 73 % de couverture globale, une personne en retard sur un kata, des
sessions planifiées pour les autres manques.

## 2. Parcours et modules

| Module | Format | Art. 4 | Rôles concernés (extrait) |
|---|---|---|---|
| Socle maîtrise de l'IA | lab | oui | tous |
| Usage sûr C1–C4 et GPU local | lab | oui | rôles techniques |
| Kata sans IA (anti-déqualification) | kata | oui | PO, ingénieurs LLM, data, eval, Agent Ops, R&D, enablement |
| Sensibilisation AI Act, ISO/IEC 42001, NIST AI RMF | atelier | oui | pilotage, architecture, gouvernance |
| Gouvernance et réévaluation par version de modèle | atelier | oui | head, PO, gouvernance |
| Contrôle humain (art. 14 et 26) | atelier | oui | gouvernance, Agent Ops, enablement |
| Supervision d'agents (escalades, coupe-circuit, TTFT) | lab | oui | Agent Ops |
| Red teaming LLM | lab | oui | architecte, ingénieurs LLM, eval, R&D |
| Équité et biais | lab | oui | ingénieurs LLM, eval, gouvernance |
| Incidents IA et signalement (art. 73) | exercice | oui | head, LLMOps, gouvernance, Agent Ops |
| RAG, ingénierie de l'évaluation, LLMOps, FinOps | lab / atelier | non (compétences métier) | selon le rôle |

**Kata sans IA** : refaire à la main, à intervalle régulier, une tâche habituellement
déléguée à l'IA (rédiger une réponse, qualifier une demande, relire une clause), puis
comparer avec la sortie du système. Objectif : garder la compétence qui permet de
contrôler l'IA et éviter la sur-confiance (risque « configuration humain-IA » du profil
NIST AI 600-1). Tous les labs utilisent des données fictives.

Sessions : réservations de la salle S1 (8 places), de préférence le jeudi (journée
d'équipe), sans conflit de salle (vérifié par `validate_config.py`).

## 3. Rôles de l'usine

`head`, `po`, `architect`, `llm_engineer`, `data_engineer`, `eval_scientist`, `llmops`,
`governance`, `agent_ops`, `research`, `enablement`. Chaque rôle a un libellé, un niveau de
maîtrise visé et une description de responsabilités dans `profiles.yaml` ; le nombre de
personnes est libre (12 dans la démo, dont deux ingénieurs LLM). Les postes de travail
reflètent les zones : bancs R&D pour la recherche et l'évaluation, Lab sécurisé (B1, B2) pour
les données sensibles, consoles Agent Ops pour la supervision.

## 4. R&D Lab

Une expérience suit `explore → prototype → evaluate → transfer` ou s'arrête (`stopped`).
Chaque fiche porte une hypothèse mesurable, un **critère de transfert** chiffré, les heures
GPU locales par mois, le use case lié et le banc utilisé.

| Étape | Sortie attendue |
|---|---|
| explore | revue de l'état de l'art, protocole, données fictives |
| prototype | prototype sur un banc, premières mesures |
| evaluate | mesure contre le golden set du use case lié et les seuils de son niveau de risque |
| transfer | critères atteints : passage à l'équipe produit, puis pipeline LLMOps et revue de risque |
| stopped | arrêt documenté (résultat négatif ou risque inacceptable) |

Règles : aucune expérience de fine-tuning ou de réentraînement (le validateur rejette ces
termes dans les titres et hypothèses) ; les leviers explorés sont le routage, le
contexte, le cache, le décodage, l'évaluation et la détection de dérive. Un transfert passe
par les mêmes portes que tout changement : évaluation selon le niveau AI Act et, si le
modèle change, réévaluation du risque. Exemple de la démo : `x-07` (gestes commerciaux sans
validation humaine) a été arrêtée sur décision de risque (MANAGE 1.1).
