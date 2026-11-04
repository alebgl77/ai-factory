# FinOps : coûts tokens et GPU, showback et chargeback, alertes de gaspillage

*[English version](../FINOPS.md)*

Le moteur `api/app/engines/costs.py` calcule les coûts par use case et par centre de coût,
la refacturation interne et les alertes de gaspillage. Paramètres : `config/factory.yaml`
(`cost_centers`, `rates`, `chargeback`, `waste_rules`). Tous les tarifs et budgets fournis
sont **fictifs**. Les montants sont
en euros ; les messages et actions des alertes sont rendus dans la langue demandée
(`?lang=en|fr`), sans changer les identifiants des règles ni les nombres.

## 1. Collecte et attribution

- Chaque appel passe par la passerelle **LiteLLM** avec `metadata.usecase` (et
  `metadata.agent`) : c'est la clé d'attribution de tous les coûts.
- Le callback MIT `compose/litellm/factory_callbacks.py` expose dans Prometheus
  `factory_llm_tokens_total{model,usecase,direction}`, `factory_llm_cost_eur_total{model,usecase}`
  et `factory_llm_requests_total{…,status}` ; Langfuse conserve les traces (prompt, version).
- La télémétrie du GPU local (DCGM ou `gpu-sim`) donne utilisation et puissance.
- L'API republie les agrégats : `factory_usecase_cost_eur{usecase,cost_center,kind}`,
  `factory_usecase_tokens_total`, `factory_waste_alerts{rule,severity}`.

Sans source vivante (`FACTORY_DEMO_MODE=auto`), un générateur déterministe produit 30 jours
d'historique fictif calé sur les budgets configurés.

## 2. Modèle de coût

| Poste | Formule | Paramètres |
|---|---|---|
| Tokens LLM | `tokens_in × prix_entrée + tokens_out × prix_sortie` (prix par 1 000 tokens), entrée en cache remisée de 90 % | `catalog/models.yaml` (`price_in_per_1k`, `price_out_per_1k`), alignés sur la config LiteLLM |
| Évaluations | coût des appels RAGAS et LLM-as-judge, compté dans le coût LLM et suivi à part (`eval_eur`) | — |
| GPU local | `heures × (capex / mois d'amortissement / 720 + puissance kW × prix du kWh)` | `rates.local_gpu` : 4 500 € / 36 mois / 240 W / 0,21 €/kWh ⇒ **0,224 €/h** |
| GPU cloud | `heures × tarif horaire` (batchs de réindexation, évaluations hors ligne) | `rates.cloud_gpu_eur_per_hour` |
| Plateforme partagée | `platform_overhead_pct` des coûts directs + forfait VPS + amortissement du GPU local **non utilisé** | `rates.platform_overhead_pct`, `rates.vps_eur_per_month` |

Le VPS et le GPU local n'ont pas de prix marginal par token : leur coût fixe est réparti via
la plateforme (VPS, GPU inactif) ou imputé à l'heure GPU consommée (GPU local actif). Tous les
calculs se font en centimes entiers : la somme des lignes égale toujours le total.

## 3. Showback ou chargeback

| | Showback (défaut) | Chargeback |
|---|---|---|
| But | rendre visibles les coûts, sans facturer | refacturer chaque centre de coût |
| Coûts directs (tokens, GPU) | affichés par use case et centre | facturés au centre du use case |
| Plateforme partagée | affichée, **non répartie** (`unallocated_platform_eur`) | répartie au prorata `tokens`, `requests` ou `equal` |
| Lignes de facture | informatives | `billable: true` par centre de coût |

Mode par défaut dans `chargeback.mode` ; le dashboard bascule avec
`GET /api/v1/costs?days=30&mode=chargeback`. Prévision mensuelle d'un centre : rythme des
7 derniers jours × 30 (majoré de la quote-part plateforme en chargeback). Recommandation :
démarrer en showback pour faire accepter les chiffres, passer en chargeback quand
l'attribution par `metadata.usecase` est fiable.

## 4. Les 8 règles de gaspillage

Seuils configurables (`waste_rules`) ; une règle absente de la configuration est désactivée.
Chaque alerte donne une économie mensuelle estimée et une action.

| Règle | Déclenchement (valeurs par défaut) | Économie estimée | Action proposée |
|---|---|---|---|
| `oversized_model` | modèle `premium` sur une tâche simple (`classification`, `extraction`, `routing`) | écart avec le modèle non premium le moins cher autorisé pour la classe de données | router vers ce modèle (ex. `chat-small`), valider sur le golden set |
| `low_cache_hit` | requêtes répétées ≥ 40 % et taux de cache ≤ 20 % | part d'entrée répétée non servie par le cache | cache de prompt ou cache sémantique dans la passerelle |
| `retry_storm` | relances > 5 % des requêtes (critique au double) | coût des relances au-delà du seuil | backoff exponentiel, disjoncteur, correction des schémas d'outils |
| `prompt_bloat` | taille moyenne du prompt +30 % sur la période | tokens d'entrée ajoutés | compresser le contexte, réduire le top-k RAG, versionner le prompt |
| `idle_gpu` | GPU local < 10 % d'utilisation pendant ≥ 4 h consécutives | énergie consommée à vide | planifier réindexations et évaluations sur ces créneaux, ou veille |
| `orphan_index` | index vectoriel sans requête depuis > 14 jours ou sans use case actif | coût mensuel de l'index | archiver le snapshot puis supprimer la collection |
| `eval_overspend` | évaluations > 25 % du coût LLM du use case | dépassement au-delà de 25 % | échantillonner le jeu d'évaluation, juge plus petit |
| `budget_breach` | consommé ou prévision ≥ 90 % du budget du centre (critique à 100 %) | dépassement prévu | appliquer les économies proposées ou réviser le budget |

Dans la démo fictive : uc-03 (classification sur le modèle premium) déclenche
`oversized_model` et `eval_overspend`, uc-01 `low_cache_hit`, uc-02 `retry_storm`, uc-06
`prompt_bloat`, le GPU local `idle_gpu` la nuit, d'anciens index `orphan_index`. Le centre
« Relation client et marketing » est piloté près de son budget : `budget_breach` apparaît
quand la quote-part plateforme lui est imputée (chargeback). Les remèdes restent dans le
périmètre de l'usine : routage, cache, contexte, planification. **Jamais de fine-tuning.**

## 5. Budgets et gouvernance des coûts

- Un budget mensuel par centre de coût (`cost_centers[].budget_month_eur`), fictif.
- Les alertes alimentent la console FinOps du dashboard, Prometheus et les tableaux Grafana
  provisionnés (`compose/grafana/dashboards/finops.json`).
- Toute action de routage sur un système à haut risque passe par une revue humaine (voir
  [LLMOPS.md](LLMOPS.md), boucle de données) et, si le modèle change, par une réévaluation du
  risque ([GOVERNANCE.md](GOVERNANCE.md)).
