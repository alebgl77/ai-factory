# Preuves opérationnelles LLMOps et boucle de données

Les producteurs approuvés peuvent publier des observations de déploiement et de dérive. Le tableau de bord évalue ces observations avec les règles du registre, les seuils de la BU et les preuves reçues. Les adaptateurs CI et de télémétrie restent responsables de l’exécution et de la vérification de leurs systèmes externes.

## Activer un producteur

1. Activez `FACTORY_AUTH_MODE=proxy`, `FACTORY_DEMO_MODE=never` et la politique `FACTORY_BU_POLICY`. Configurez le réseau de confiance du proxy et utilisez une identité SSO de service distincte de l’approbateur du registre.
2. Attribuez à cette identité un groupe dédié, mappé uniquement à `viewer` dans `FACTORY_ROLE_MAP` et limité aux BU dont ce producteur atteste les résultats. La même appartenance doit fournir le droit de lecture, l’autorisation explicite de publication et le périmètre du dossier. Toute identité qui porte également un rôle d’écriture ou d’approbation est refusée à l’ingestion.
3. Définissez `FACTORY_OPERATING_EVIDENCE_SOURCES` comme un objet JSON liant chaque identifiant de producteur à ses groupes, par exemple `{"release-ci":["ops-release-publishers"],"rag-monitor":["ops-monitor-publishers"]}`. Une valeur vide laisse l’API d’ingestion désactivée. Faites transiter les requêtes par le proxy authentifiant, avec le type `application/json`.

Les routes sont `POST /api/v1/operating-evidence/releases` et `POST /api/v1/operating-evidence/signals`. La lecture utilise les routes habituelles `/api/v1/pipeline`, `/api/v1/dataloop` et `/api/v1/snapshot` avec les droits BU de la personne connectée.

## Attester une version

Récupérez la `review_revision` et la version du modèle depuis `/api/v1/registry/{usecase}`. Le registre doit porter une approbation `approved` pour cette révision exacte, enregistrée par un approbateur distinct du mainteneur du modèle. Une approbation conditionnelle attend un traitement humain des conditions. Le proxy doit transmettre le sujet stable du fournisseur d’identité. AI Factory utilise son empreinte interne pour distinguer le producteur du reçu et l’approbateur, même après un changement d’adresse e-mail. Une décision historique dépourvue de sujet stable ou d’horodatage précis doit être revue avant de servir de preuve de déploiement.

Un dossier de preuve comporte `observation_id`, `source`, `usecase`, `review_revision`, `model_version` et `observed_at`. Pour une version, ajoutez `run_id`, `version`, `artifact_digest` et, si disponible, `previous_version`. Renseignez dans l’ordre les dates UTC `ingested_at`, `indexed_at`, `versioned_at`, `evaluated_at`. La dernière étape accepte `metrics` avec les clés `faithfulness`, `answer_relevancy`, `context_precision`, `judge_score` et `redteam_pass`. Le score du juge est sur 5. Les autres métriques d’évaluation sont des proportions ou, pour le résultat red team, un booléen.

Après la vérification indépendante du déploiement, ajoutez `deployment_receipt` sous la forme `{"digest":"<sha256 du reçu>","at":"<horodatage UTC>"}`. Cet horodatage doit être strictement postérieur à l’approbation UTC enregistrée par le serveur. Sans reçu, un gate réussi laisse le déploiement en attente. Le condensat référence une preuve détenue et vérifiée par le producteur autorisé. Il ne constitue pas à lui seul une signature du fournisseur ni une autorisation de déploiement.

Vous pouvez joindre `online` avec `thumbs_down_ratio` exprimé comme multiplicateur du taux de référence, `faithfulness_drop` et `error_rate` exprimés en proportions. Un seuil dépassé produit une recommandation. Le retour arrière devient observé seulement lorsque `rollback_receipt` atteste le retour à `previous_version`.

Pour enrichir un même run, envoyez un nouvel `observation_id` avec un `observed_at` plus récent et recopiez ses preuves existantes. Les données des étapes précédentes, les condensats et les reçus sont immuables. L’identité d’une observation est bornée à sa source, son dossier et sa révision. Une reprise identique est idempotente et une modification du même identifiant retourne `409`. Le système accepte les observations des 30 derniers jours avec cinq minutes de tolérance d’horloge et conserve les preuves jusqu’à 90 jours. Les révisions dépassées quittent les vues courantes.

## Publier un signal de dérive

Ajoutez `signal` et `value` aux six champs communs. Les signaux reconnus sont `retrieval_hit_rate`, `query_drift`, `corpus_freshness`, `eval_failure_cluster`, `cost_drift` et `latency_drift`. L’évaluateur détermine le seuil, la recommandation et le recours à un avis humain. Le producteur ne fournit ni action, ni approbation, ni statut d’exécution.

Conservez les preuves et les données détaillées dans leurs systèmes d’origine. L’API n’accepte que les champs structurés décrits ici. Le stockage isole les données par dossier et conserve une référence pseudonymisée du compte producteur. GitHub Pages continue d’utiliser le scénario de démonstration incorporé. Pour qualifier un environnement d’entreprise, vérifiez le SSO, les identités de publication, les reçus et les cibles auprès des systèmes réels du pilote.
