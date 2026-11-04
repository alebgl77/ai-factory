# LLMOps et RAG : pipeline, portes d'évaluation, retour arrière, boucle de données, placement

*[English version](../LLMOPS.md)*

Moteurs : `api/app/engines/pipeline.py`, `dataloop.py`, `placement.py`, `agentops.py`.
Paramètres : `config/factory.yaml` (`eval_gates`, `rollback`, `dataloop`, `placement`,
`agentops_slo`) et `config/usecases/*.yaml` (`rag`, `eval`). Le moteur **évalue** les
portes et les règles ; il ne déploie rien lui-même. Les détails des étapes, déclencheurs de
retour arrière, raisons de placement et détails de la boucle de données sont rendus dans la
langue demandée (`?lang=en|fr`) ; identifiants d'étapes, raisons `blocked_by` et noms d'actions
ne changent jamais.

> Principe : **aucun fine-tuning, aucun réentraînement.** Les leviers sont le corpus, le
> découpage, l'index, le prompt, l'agent, le routage entre modèles publiés et l'évaluation.

## 1. Les sept étapes du pipeline

| Étape | Contenu | Outils de la stack |
|---|---|---|
| `ingestion` | collecte des sources, contrôle de classe C1–C4, pseudonymisation si nécessaire | outils MCP `tl-ocr-local`, `tl-pii-redact` |
| `indexing` | découpage (`chunk_size`, `chunk_overlap`), embeddings `embed-default` sur GPU local, index hybride | Qdrant |
| `versioning` | versions liées du prompt, de l'agent LangGraph, de l'index et du modèle (`model.version`) | Langfuse (prompts), Git |
| `eval_gate` | golden set, RAGAS, LLM-as-judge, red team ; seuils selon le niveau AI Act | Langfuse (scores) |
| `deploy` | canary puis 100 % ; **bloqué** si réévaluation du risque requise, évaluation rejetée, pratique interdite ou porte échouée | LiteLLM (alias) |
| `feedback` | pouces haut / bas, fidélité en ligne échantillonnée, taux d'erreur | Langfuse, Prometheus |
| `rollback_guard` | retour arrière **automatique** vers la version précédente si un seuil est franchi | API, alertes Prometheus |

`blocked_by` indique la cause d'un blocage : `risk_reassessment`, `eval_gate`,
`assessment_rejected` ou `prohibited_practice`.

## 2. Portes d'évaluation (RAGAS + LLM-as-judge + red team)

| Niveau AI Act | faithfulness | answer_relevancy | context_precision | judge_score (/5) | redteam_pass |
|---|---|---|---|---|---|
| minimal | 0,75 | 0,75 | 0,70 | 3,5 | 0,90 |
| limited | 0,80 | 0,80 | 0,75 | 3,8 | 0,95 |
| high | 0,90 | 0,85 | 0,85 | 4,2 | 0,99 |

- **RAGAS** : `faithfulness` (réponse fondée sur le contexte récupéré), `answer_relevancy`
  (pertinence par rapport à la question), `context_precision` (pertinence des passages
  remontés). Une métrique manquante fait échouer la porte.
- **LLM-as-judge** : grille notée sur 5, juge distinct du modèle évalué (alias `eval.judge_alias`
  du use case), calibré sur un échantillon noté par des humains ; pour les données C3/C4 le
  juge reste sur le GPU local (`chat-sensitive`), ce que vérifie `validate_config.py`.
- **Red team** : taux de réussite d'une campagne (injection de prompt directe et indirecte,
  exfiltration via outils MCP, contournement), construite à partir des risques du profil
  NIST AI 600-1.
- Les seuils sont plus stricts quand le risque augmente (contrôlé par `validate_config.py`).

## 3. Retour arrière automatique

Après déploiement, la garde compare les retours d'usage aux seuils `rollback` :

| Déclencheur | Seuil par défaut |
|---|---|
| `thumbs_down_ratio` (taux de pouces bas / référence avant déploiement) | > 2,0 |
| `online_faithfulness_drop` (baisse de fidélité mesurée en ligne) | > 0,08 |
| `error_rate` | > 5 % |

Le premier seuil franchi provoque le retour à `previous_version`, journalisé
(`pipeline.rollbacks`, `factory_rollbacks_total{usecase}`) avec la valeur et le seuil.

## 4. Boucle de données : agir sans réentraîner

| Signal | Mauvais quand | Action |
|---|---|---|
| `retrieval_hit_rate` | < 0,75 | `reindex` (re-découpage, ré-embedding) |
| `corpus_freshness` | > 10 % de documents hors délai de fraîcheur | `reindex` incrémental |
| `query_drift` | distance du centroïde des requêtes > 0,25 | `update_eval_set` |
| `eval_failure_cluster` | > 5 échecs sur un même thème | `update_eval_set` |
| `cost_drift` | coût par requête > +20 % | `change_routing` |
| `latency_drift` | latence p95 > +20 % | `change_routing` |

Garde-fous : un signal à ≥ 2× son seuil, ou un changement de routage sur un système à haut
risque, donne `human_review`. Les actions autorisées sont exactement `reindex`,
`update_eval_set`, `change_routing`, `human_review` ; `retrain`, `fine_tune`, `lora`,
`rlhf`, `distill`… sont refusées par l'API (`ForbiddenActionError`) quelle que soit la
configuration. Un changement de routage qui modifie le modèle d'un use case modifie sa
version : il déclenche la réévaluation du risque ([GOVERNANCE.md](GOVERNANCE.md)).

## 5. Placement hybride : cloud d'abord, puis VPS, puis matériel local

| Cible | Service | Usage |
|---|---|---|
| `cloud` | API managée via LiteLLM | préférence pour les données C1/C2 |
| `vps` | Ollama sur CPU (profil `vps`), modèles ≤ `vps_max_params_b` (8 B) | repli, tâches simples |
| `local` | vLLM sur le GPU local (profil `local-gpu`), télémétrie DCGM vers Prometheus et le rack 3D | **seule** cible des données C3/C4 |

Règles du moteur (`POST /api/v1/placement/decide` pour un essai) :

1. Classe C3 ou C4 ⇒ GPU local uniquement, alias `chat-sensitive`, aucun repli externe ; GPU
   indisponible ⇒ mise **en file d'attente**, jamais d'envoi au cloud ou au VPS.
2. Sinon, ordre `cloud > vps > local`, en sautant les cibles indisponibles et le VPS si le
   modèle requis dépasse 8 B (taille déduite du catalogue ou du type de tâche) ; une cible
   dégradée n'est choisie qu'en dernier recours.

Alias LiteLLM : `chat-default` (cloud, replis VPS puis local), `chat-small` (VPS d'abord),
`chat-sensitive` (local seulement), `embed-default` (local seulement, les corpus pouvant
contenir du C3/C4).

## 6. Salle Agent Ops

SLO (`agentops_slo`) : délai avant le premier token **TTFT p95 ≤ 1 500 ms**, **tokens par
tâche p95 ≤ 12 000**, **latence vectorielle p95 ≤ 120 ms**. Par agent : TTFT, tokens par tâche,
latence vectorielle, taux de succès, coût par tâche et SLO dépassés
(`/api/v1/agentops`, métriques `factory_agent_ttft_seconds`, `factory_agent_tokens_per_task`,
`factory_vector_latency_seconds`). Les opérateurs des consoles O1 et O2 disposent du
coupe-circuit des agents et des escalades humaines définies dans le catalogue.
