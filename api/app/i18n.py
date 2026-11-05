"""Internationalisation of the aggregation API (CONTRACT section 9): English first, French second.

- ``MESSAGES`` is the message catalogue ``{key: {"en": template, "fr": template}}``. Templates use
  ``str.format`` fields; numbers are rendered with the conventions of the language (``0.86`` in
  English, ``0,86`` in French) by :func:`msg`.
- :func:`msg` returns the text of one key in one language; :func:`both` returns the
  ``{"en", "fr"}`` mapping of a key (used where the language is not known yet, e.g. configuration
  warnings, and resolved later by :func:`localize_tree`).
- :func:`localize_tree` resolves every ``{en, fr}`` mapping of a JSON-like tree (configuration or
  payload) into the string of the chosen language.

Pure module: no I/O, no global state read at call time other than the constant catalogue.
"""

from __future__ import annotations

import string
from typing import Any

LANGUAGES: tuple[str, ...] = ("en", "fr")
DEFAULT_LANG = "en"
_LANG_KEYS = frozenset(LANGUAGES)


def normalize_lang(value: Any, default: str = DEFAULT_LANG) -> str:
    """Return ``en`` or ``fr`` for a requested language; anything else falls back to ``default``.

    Region subtags are accepted (``fr-FR``, ``en_US``); the comparison is case-insensitive.
    """
    fallback = default if default in _LANG_KEYS else DEFAULT_LANG
    if not isinstance(value, str):
        return fallback
    primary = value.strip().lower().replace("_", "-").split("-", 1)[0]
    return primary if primary in _LANG_KEYS else fallback


def other_lang(lang: str) -> str:
    return "fr" if normalize_lang(lang) == "en" else "en"


# --------------------------------------------------------------------------------------------
# Message catalogue
# --------------------------------------------------------------------------------------------
MESSAGES: dict[str, dict[str, str]] = {
    # ---- configuration loader (warnings) and contract defaults
    "config.read_error": {
        "en": "{file}: cannot be read ({error})",
        "fr": "{file} : lecture impossible ({error})",
    },
    "config.dir_missing": {
        "en": "configuration directory not found: {path}",
        "fr": "répertoire de configuration absent : {path}",
    },
    "config.factory_missing": {
        "en": "factory.yaml not found: contract defaults applied",
        "fr": "factory.yaml absent : valeurs par défaut du contrat",
    },
    "config.usecase_no_id": {
        "en": "usecases/{file}: missing id, file ignored",
        "fr": "usecases/{file} : identifiant manquant, fichier ignoré",
    },
    "config.usecase_duplicate": {
        "en": "usecases/{file}: duplicate id {id}, file ignored",
        "fr": "usecases/{file} : identifiant {id} dupliqué, fichier ignoré",
    },
    "default.tagline": {
        "en": "Generic AI factory — fictional demo",
        "fr": "Usine IA générique — démo fictive",
    },
    "default.milestone.prohibitions": {
        "en": "Art. 5 prohibited AI practices",
        "fr": "Art. 5 pratiques interdites",
    },
    "default.milestone.literacy": {
        "en": "Art. 4 AI literacy (rewritten by Regulation (EU) 2026/1744)",
        "fr": "Art. 4 maîtrise de l'IA (réécrit par le règlement (UE) 2026/1744)",
    },
    "default.milestone.gpai": {
        "en": "Chapter V general-purpose AI models",
        "fr": "Chapitre V modèles d'IA à usage général",
    },
    "default.milestone.transparency": {
        "en": "Art. 50 transparency obligations",
        "fr": "Art. 50 obligations de transparence",
    },
    "default.milestone.annex_iii": {
        "en": "High-risk AI systems, Annex III",
        "fr": "Systèmes d'IA à haut risque, annexe III",
    },
    "default.milestone.annex_i": {
        "en": "High-risk AI systems, Annex I",
        "fr": "Systèmes d'IA à haut risque, annexe I",
    },
    # ---- snapshot assembly
    "warning.litellm_spend_logs": {
        "en": "costs taken from the LiteLLM spend logs and converted to EUR at the configured rate",
        "fr": "coûts issus des journaux de dépenses LiteLLM et convertis en EUR au taux configuré",
    },
    "warning.fine_tuning_ignored": {
        "en": "factory.fine_tuning=true ignored: the Virtual AI Factory never trains or fine-tunes a model",
        "fr": "factory.fine_tuning=true ignoré : la Virtual AI Factory n'entraîne et n'affine aucun modèle",
    },
    "placement.target.cloud": {"en": "Cloud (managed API)", "fr": "Cloud (API managée)"},
    "placement.target.cloud_demo": {"en": "Cloud (managed API, fictional)", "fr": "Cloud (API managée, fictif)"},
    "placement.target.vps": {"en": "VPS (CPU, Ollama)", "fr": "VPS (CPU, Ollama)"},
    "placement.target.local": {"en": "Local GPU (vLLM)", "fr": "GPU local (vLLM)"},
    # ---- risk engine
    "risk.reason.model_not_reviewed": {
        "en": "model {version} not reviewed (last {last})",
        "fr": "modèle {version} non revu (dernière revue : {last})",
    },
    "risk.reason.no_review": {"en": "none", "fr": "aucune"},
    "risk.reason.assessment_pending": {"en": "assessment pending", "fr": "évaluation en attente"},
    "risk.reason.separator": {"en": "; ", "fr": " ; "},
    "risk.tier.prohibited": {"en": "prohibited", "fr": "interdit"},
    "risk.tier.high": {"en": "high-risk", "fr": "haut risque"},
    "risk.tier.limited": {"en": "limited-risk", "fr": "risque limité"},
    "risk.tier.minimal": {"en": "minimal-risk", "fr": "risque minimal"},
    # ---- placement engine
    "placement.reason.sensitive_local_only": {
        "en": "{sensitivity} => local only",
        "fr": "{sensitivity} => local uniquement",
    },
    "placement.reason.local_degraded": {
        "en": "local GPU degraded: higher latency accepted, no external fallback",
        "fr": "GPU local dégradé : latence accrue acceptée, aucun repli externe",
    },
    "placement.reason.local_down_queued": {
        "en": "local GPU unavailable: queued, never sent to the cloud or the VPS",
        "fr": "GPU local indisponible : mise en file d'attente, aucun envoi vers le cloud ou le VPS",
    },
    "placement.reason.target_down": {"en": "{target} unavailable", "fr": "{target} indisponible"},
    "placement.reason.vps_unknown_size": {
        "en": "unknown model size: VPS excluded as a precaution",
        "fr": "taille de modèle inconnue : VPS exclu par prudence",
    },
    "placement.reason.vps_too_small": {
        "en": "model of ≥ {need:g} B required > VPS max {max:g} B",
        "fr": "modèle ≥ {need:g} B requis > {max:g} B max VPS",
    },
    "placement.reason.no_target_queued": {
        "en": "no target available: queued",
        "fr": "aucune cible disponible : mise en file d'attente",
    },
    "placement.reason.degraded_chosen": {
        "en": "{target} degraded, chosen for lack of a healthy target",
        "fr": "{target} dégradé, choisi faute de cible saine",
    },
    "placement.reason.order": {
        "en": "{sensitivity}: order {order}, target {target}",
        "fr": "{sensitivity} : ordre {order}, cible {target}",
    },
    "placement.reason.order_preferred": {
        "en": "{sensitivity}: order {order}, target {target} (preferred)",
        "fr": "{sensitivity} : ordre {order}, cible {target} (préférence)",
    },
    # ---- FinOps engine
    "costs.unknown_cost_center": {"en": "Unknown cost center", "fr": "Centre de coût inconnu"},
    "costs.unattributed_usecase": {"en": "Unattributed usage", "fr": "Usage non attribué"},
    "costs.method.platform": {
        "en": "platform cost = {pct:g}% of direct costs + VPS + unused GPU amortization",
        "fr": "frais de plateforme = {pct:g} % des coûts directs + VPS + amortissement GPU non utilisé",
    },
    "costs.method.showback": {
        "en": "costs shown to each team, platform cost not allocated",
        "fr": "coûts affichés, plateforme non répartie",
    },
    "costs.method.chargeback": {
        "en": "platform cost allocated pro rata ({allocation})",
        "fr": "plateforme répartie au prorata ({allocation})",
    },
    "costs.alert.oversized_model.message": {
        "en": "Premium model ({model}) used for a simple task ({task_type})",
        "fr": "Modèle premium ({model}) pour une tâche simple ({task_type})",
    },
    "costs.alert.oversized_model.action": {
        "en": "Route to {alternative} and validate against the golden set",
        "fr": "Router vers {alternative} et valider par le golden set",
    },
    "costs.alert.oversized_model.smaller_model": {"en": "a smaller model", "fr": "un modèle plus petit"},
    "costs.alert.low_cache_hit.message": {
        "en": "{repeat}% of requests are repeated but only {hit}% are served from cache",
        "fr": "{repeat} % de requêtes répétées mais {hit} % de cache",
    },
    "costs.alert.low_cache_hit.action": {
        "en": "Enable prompt caching / semantic caching in the LLM gateway",
        "fr": "Activer le cache de prompt / cache sémantique dans la passerelle LLM",
    },
    "costs.alert.retry_storm.message": {
        "en": "Retry rate {ratio}% > {max}%",
        "fr": "Taux de relance {ratio} % > {max} %",
    },
    "costs.alert.retry_storm.action": {
        "en": "Add exponential backoff and a circuit breaker, fix the tool-schema errors",
        "fr": "Backoff exponentiel, disjoncteur et correction des erreurs de schéma d'outil",
    },
    "costs.alert.prompt_bloat.message": {
        "en": "Average prompt size +{growth}% over the period",
        "fr": "Taille moyenne du prompt +{growth} % sur la période",
    },
    "costs.alert.prompt_bloat.action": {
        "en": "Compress the context, cap the RAG top-k and version the prompt",
        "fr": "Compresser le contexte, limiter le top-k RAG et versionner le prompt",
    },
    "costs.alert.idle_gpu.message": {
        "en": "Local GPU {device} idle for {hours} consecutive hours (< {max}%)",
        "fr": "GPU local {device} inactif {hours} h consécutives (< {max} %)",
    },
    "costs.alert.idle_gpu.action": {
        "en": "Schedule reindexing and evaluations in these time slots, or put the GPU to sleep",
        "fr": "Planifier réindexations et évaluations sur ces créneaux ou mettre en veille",
    },
    "costs.alert.orphan_index.message": {
        "en": "Orphan index {index}: {why}",
        "fr": "Index {index} orphelin : {why}",
    },
    "costs.alert.orphan_index.why_stale": {
        "en": "no query for {days} days",
        "fr": "aucune requête depuis {days} j",
    },
    "costs.alert.orphan_index.why_unused": {"en": "no active use case", "fr": "aucun cas d'usage actif"},
    "costs.alert.orphan_index.action": {
        "en": "Archive a snapshot, then delete the collection",
        "fr": "Archiver le snapshot puis supprimer la collection",
    },
    "costs.alert.eval_overspend.message": {
        "en": "Evaluations = {share}% of the LLM cost (> {max}%)",
        "fr": "Évaluations = {share} % du coût LLM (> {max} %)",
    },
    "costs.alert.eval_overspend.action": {
        "en": "Sample the evaluation set and use a smaller judge model",
        "fr": "Échantillonner le jeu d'évaluation et utiliser un juge plus petit",
    },
    "costs.alert.budget_breach.message": {
        "en": "{name}: {spent}% of the budget consumed, forecast {forecast}%",
        "fr": "{name} : {spent} % du budget consommé, prévision {forecast} %",
    },
    "costs.alert.budget_breach.action": {
        "en": "Apply the proposed savings or revise the budget with the cost center",
        "fr": "Appliquer les économies proposées ou réviser le budget avec le centre de coût",
    },
    # ---- LLMOps / RAG pipeline engine
    "pipeline.gate.missing": {"en": "{metric} missing", "fr": "{metric} manquant"},
    "pipeline.gate.below": {"en": "{metric} {value:g} < {threshold:g}", "fr": "{metric} {value:g} < {threshold:g}"},
    "pipeline.stage.failed": {"en": "stage failed", "fr": "échec de l'étape"},
    "pipeline.stage.running": {"en": "in progress", "fr": "en cours"},
    "pipeline.eval.prohibited": {
        "en": "Prohibited AI practice (Art. 5): no deployment possible",
        "fr": "Pratique interdite (art. 5) : aucun déploiement possible",
    },
    "pipeline.eval.running": {"en": "evaluation in progress", "fr": "évaluation en cours"},
    "pipeline.eval.passed": {
        "en": "Thresholds of the {tier} tier met",
        "fr": "Seuils du niveau {tier} respectés",
    },
    "pipeline.eval.failed": {"en": "Thresholds not met: {failures}", "fr": "Seuils non atteints : {failures}"},
    "pipeline.deploy.prohibited": {"en": "Prohibited AI practice (Art. 5)", "fr": "Pratique interdite (art. 5)"},
    "pipeline.deploy.reassessment": {
        "en": "Risk reassessment required: {reason}",
        "fr": "Réévaluation du risque requise : {reason}",
    },
    "pipeline.deploy.rejected": {"en": "Risk assessment rejected", "fr": "Évaluation des risques rejetée"},
    "pipeline.deploy.eval_gate": {
        "en": "Blocked by the evaluation gate",
        "fr": "Bloqué par le point de contrôle d'évaluation",
    },
    "pipeline.deploy.canary": {"en": "progressive rollout (canary)", "fr": "déploiement progressif (canary)"},
    "pipeline.deploy.done": {"en": "deployed (canary, then 100%)", "fr": "déployé (canary puis 100 %)"},
    "pipeline.feedback.collected": {"en": "usage feedback collected", "fr": "retours d'usage collectés"},
    "pipeline.feedback.collecting": {"en": "collecting feedback", "fr": "collecte en cours"},
    "pipeline.rollback.auto": {
        "en": "Automatic rollback to {to} ({trigger} {value:g} > {threshold:g})",
        "fr": "Retour arrière automatique vers {to} ({trigger} {value:g} > {threshold:g})",
    },
    "pipeline.rollback.ok": {"en": "rollback thresholds respected", "fr": "seuils de retour arrière respectés"},
    "pipeline.rollback.monitoring": {"en": "active monitoring", "fr": "surveillance active"},
    "pipeline.trigger.thumbs_down_ratio": {"en": "thumbs-down ratio", "fr": "taux d'avis négatifs"},
    "pipeline.trigger.online_faithfulness_drop": {
        "en": "online faithfulness drop",
        "fr": "baisse de fidélité en production",
    },
    "pipeline.trigger.error_rate": {"en": "error rate", "fr": "taux d'erreur"},
    # ---- data loop engine
    "dataloop.action.reindex": {
        "en": "Reindex the corpus (re-chunking and re-embedding)",
        "fr": "Réindexation (re-découpage et ré-embedding) du corpus",
    },
    "dataloop.action.update_eval_set": {
        "en": "Update the evaluation set (new reference questions)",
        "fr": "Mise à jour du jeu d'évaluation (nouvelles questions de référence)",
    },
    "dataloop.action.change_routing": {
        "en": "Change the routing in the LLM gateway",
        "fr": "Changement de routage dans la passerelle LLM",
    },
    "dataloop.action.human_review": {
        "en": "Human review required before any change",
        "fr": "Revue humaine requise avant toute modification",
    },
    "dataloop.rationale.unknown_signal": {
        "en": "unknown signal: escalated to a human",
        "fr": "signal inconnu : escalade humaine",
    },
    "dataloop.rationale.severe": {
        "en": "severe drift (≥ {factor:g}x the threshold): escalated to a human",
        "fr": "dérive sévère (≥ {factor:g}x le seuil) : escalade humaine",
    },
    "dataloop.rationale.high_risk_routing": {
        "en": "routing change on a high-risk system: reassessment required",
        "fr": "changement de routage sur un système à haut risque : réévaluation requise",
    },
    "dataloop.error.forbidden": {
        "en": "action '{action}' refused: fine-tuning and retraining are disabled (fine_tuning={fine_tuning})",
        "fr": "action '{action}' refusée : fine-tuning et réentraînement désactivés (fine_tuning={fine_tuning})",
    },
    "dataloop.error.unknown": {
        "en": "unknown action '{action}': allowed actions are {allowed}",
        "fr": "action '{action}' inconnue : actions autorisées {allowed}",
    },
    # ---- Skills Lab and catalogue engines
    "skills.art4_note": {
        "en": ("Art. 4 (AI literacy) as rewritten by Regulation (EU) 2026/1744: take measures adapted to "
               "the role, the risk and the context"),
        "fr": ("Art. 4 (maîtrise de l'IA) réécrit par le règlement (UE) 2026/1744 : prendre des mesures "
               "adaptées au rôle, au risque et au contexte"),
    },
    "skills.warning.missing_module": {
        "en": "module {module} required for {role} is missing from the catalog",
        "fr": "module {module} requis pour {role} absent du catalogue",
    },
    "catalog.allowed_classes": {"en": "allowed classes: {classes}", "fr": "classes autorisées : {classes}"},
    "catalog.unspecified": {"en": "not specified", "fr": "non précisé"},
    # ---- deterministic demo generator
    "demo.ingestion.documents": {"en": "{count} documents ingested", "fr": "{count} documents ingérés"},
    "demo.ingestion.sources": {"en": "sources ingested", "fr": "sources ingérées"},
    "demo.indexing": {
        "en": "chunk size {size} / overlap {overlap}",
        "fr": "découpage {size} / recouvrement {overlap}",
    },
    "demo.versioning": {"en": "prompt, agent and index versioned", "fr": "prompt, agent et index versionnés"},
    "demo.signal.retrieval_hit_rate": {
        "en": "Top-5 hit rate falling: reindexing with a revised chunking",
        "fr": "Hit rate top-5 en baisse : réindexation avec découpage révisé",
    },
    "demo.signal.corpus_freshness": {
        "en": "{pct}% of documents past their freshness deadline: incremental reindexing",
        "fr": "{pct} % des documents au-delà du délai de fraîcheur : réindexation incrémentale",
    },
    "demo.signal.query_drift": {
        "en": "New intents detected: {count} questions added to the golden set",
        "fr": "Nouvelles intentions détectées : ajout de {count} questions au golden set",
    },
    "demo.signal.eval_failure_cluster": {
        "en": "Cluster of failures on a single topic: evaluation set enriched",
        "fr": "Grappe d'échecs sur un même thème : enrichissement du jeu d'évaluation",
    },
    "demo.signal.cost_drift": {
        "en": "Cost per request +{pct}%: simple requests routed to {alias}",
        "fr": "Coût par requête +{pct} % : routage des requêtes simples vers {alias}",
    },
    "demo.signal.latency_drift": {
        "en": "p95 latency +{pct}%: severe drift, human review before any action",
        "fr": "Latence p95 +{pct} % : dérive sévère, revue humaine avant action",
    },
    "demo.signal.cost_drift_high_risk": {
        "en": "Routing change considered on a high-risk system: human review",
        "fr": "Changement de routage envisagé sur un système à haut risque : revue humaine",
    },
    "demo.gpu.name": {"en": "Local GPU 24 GB (simulated)", "fr": "GPU local 24 Go (simulé)"},
    "demo.index.orphan_detail": {
        "en": "previous version kept after reindexing",
        "fr": "ancienne version conservée après réindexation",
    },
    # ---- Grafana named queries (labels and non-symbol units)
    "grafana.query.gpu_util": {"en": "GPU utilization", "fr": "Utilisation GPU"},
    "grafana.query.gpu_temp": {"en": "GPU temperature", "fr": "Température GPU"},
    "grafana.query.gpu_power": {"en": "GPU power draw", "fr": "Puissance GPU"},
    "grafana.query.gpu_mem_used": {"en": "GPU memory used", "fr": "Mémoire GPU utilisée"},
    "grafana.query.llm_requests_rate": {"en": "LLM requests per use case", "fr": "Requêtes LLM par cas d'usage"},
    "grafana.query.llm_error_ratio": {"en": "LLM error rate", "fr": "Taux d'erreur LLM"},
    "grafana.query.llm_cost_24h": {"en": "LLM cost over 24 h per use case", "fr": "Coût LLM 24 h par cas d'usage"},
    "grafana.query.llm_tokens_24h": {"en": "Tokens over 24 h", "fr": "Tokens 24 h"},
    "grafana.query.llm_ttft_p95": {"en": "TTFT p95", "fr": "TTFT p95"},
    "grafana.query.llm_latency_p95": {"en": "p95 latency", "fr": "Latence p95"},
    "grafana.query.waste_alerts": {"en": "Waste alerts", "fr": "Alertes de gaspillage"},
    "grafana.query.reevaluation_required": {"en": "Use cases to reassess", "fr": "Cas d'usage à réévaluer"},
    "grafana.query.skills_coverage": {"en": "Art. 4 AI literacy coverage", "fr": "Couverture art. 4 (maîtrise de l'IA)"},
    "grafana.query.targets_up": {"en": "Prometheus targets", "fr": "Cibles Prometheus"},
    "grafana.unit.alerts": {"en": "alerts", "fr": "alertes"},
    "grafana.unit.usecases": {"en": "use cases", "fr": "cas d'usage"},
    # ---- HTTP answers (errors and write confirmations)
    "error.usecase_not_found": {"en": "use case not found", "fr": "cas d'usage introuvable"},
    "error.version_is_current": {
        "en": "this version is already the current version",
        "fr": "cette version est déjà la version courante",
    },
    "error.unknown_reviewer": {
        "en": "reviewer not found in profiles.yaml",
        "fr": "relecteur inconnu dans profiles.yaml",
    },
    "error.state_unavailable": {
        "en": "state directory unavailable: the change was not saved",
        "fr": "répertoire d'état indisponible : la modification n'a pas été enregistrée",
    },
    "error.sensitivity_required": {
        "en": "sensitivity (C1..C4) or use case required",
        "fr": "sensibilité (C1..C4) ou cas d'usage requis",
    },
    "error.unknown_query": {
        "en": "unknown query (list: /api/v1/grafana/dashboards)",
        "fr": "requête inconnue (liste : /api/v1/grafana/dashboards)",
    },
    "error.grafana_unavailable": {
        "en": "Grafana unavailable (demo mode or not configured)",
        "fr": "Grafana indisponible (mode démo ou non configuré)",
    },
    "error.grafana_no_answer": {"en": "Grafana did not respond", "fr": "Grafana n'a pas répondu"},
    "error.version_conflict": {"en": "version conflict", "fr": "conflit de version"},
    "error.body_too_large": {"en": "request body too large", "fr": "corps de requête trop volumineux"},
    "error.internal": {"en": "internal error", "fr": "erreur interne"},
    "error.audit_unavailable": {
        "en": "audit log unavailable: the change was not saved",
        "fr": "journal d'audit indisponible : la modification n'a pas été enregistrée",
    },
    # ---- security: authentication, authorisation, separation of duties, rate limiting
    "auth.unauthenticated": {
        "en": "authentication required: sign in through the single sign-on proxy",
        "fr": "authentification requise : connectez-vous via le proxy d'authentification unique (SSO)",
    },
    "auth.untrusted_source": {
        "en": "request refused: identities are only accepted from the trusted SSO proxy",
        "fr": "requête refusée : les identités ne sont acceptées que depuis le proxy SSO de confiance",
    },
    "auth.invalid_identity": {
        "en": "request refused: malformed identity headers",
        "fr": "requête refusée : en-têtes d'identité mal formés",
    },
    "auth.forbidden": {
        "en": "access denied: the {permission} permission requires one of these roles: {roles}",
        "fr": "accès refusé : la permission {permission} exige l'un de ces rôles : {roles}",
    },
    "auth.four_eyes": {
        "en": ("separation of duties: the identity that recorded model version {version} cannot approve it; "
               "another approver must review it"),
        "fr": ("séparation des tâches : l'identité qui a enregistré la version de modèle {version} ne peut pas "
               "l'approuver ; un autre approbateur doit la revoir"),
    },
    "auth.rate_limited": {
        "en": "too many write requests: retry in {seconds} s",
        "fr": "trop de requêtes d'écriture : réessayez dans {seconds} s",
    },
    "registry.model_version_recorded": {
        "en": "New model version recorded: risk reassessment required before deployment",
        "fr": "Nouvelle version de modèle enregistrée : réévaluation du risque requise avant déploiement",
    },
}


# --------------------------------------------------------------------------------------------
# Formatting
# --------------------------------------------------------------------------------------------
class _Formatter(string.Formatter):
    """``str.format`` with language-aware numbers (French: decimal comma, thin-space grouping)."""

    def __init__(self, lang: str) -> None:
        super().__init__()
        self.lang = lang

    def format_field(self, value: Any, format_spec: str) -> str:
        text = super().format_field(value, format_spec)
        if self.lang == "fr" and isinstance(value, (int, float)) and not isinstance(value, bool):
            text = text.replace(",", " ").replace(".", ",")
        return text


_FORMATTERS = {lang: _Formatter(lang) for lang in LANGUAGES}


def template(lang: str, key: str) -> str | None:
    """Raw template of ``key`` in ``lang`` (fallback: other language, then any value)."""
    entry = MESSAGES.get(key)
    if not entry:
        return None
    lang = normalize_lang(lang)
    for candidate in (lang, other_lang(lang)):
        text = entry.get(candidate)
        if text:
            return text
    return next(iter(entry.values()), None)


def msg(lang: str, key: str, /, **params: Any) -> str:
    """Localised text of ``key``. Never raises: an unknown key returns the key itself."""
    lang = normalize_lang(lang)
    text = template(lang, key)
    if text is None:
        return key
    try:
        return _FORMATTERS[lang].vformat(text, (), params)
    except (KeyError, IndexError, ValueError, TypeError):
        return text


def both(key: str, /, **params: Any) -> dict[str, str]:
    """``{"en": ..., "fr": ...}`` mapping of a key, resolved later by :func:`localize_tree`."""
    return {lang: msg(lang, key, **params) for lang in LANGUAGES}


def has(key: str) -> bool:
    return key in MESSAGES


# --------------------------------------------------------------------------------------------
# {en, fr} mappings in configuration and payloads
# --------------------------------------------------------------------------------------------
def is_lang_mapping(value: Any) -> bool:
    """True for a dict whose keys are a non-empty subset of {en, fr} (a translatable value)."""
    return isinstance(value, dict) and bool(value) and all(isinstance(k, str) for k in value) \
        and set(value) <= _LANG_KEYS


def _missing(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def pick(mapping: dict[str, Any], lang: str) -> Any:
    """Value of an {en, fr} mapping: requested language, else the other one, else the first value."""
    lang = normalize_lang(lang)
    for candidate in (lang, other_lang(lang)):
        if candidate in mapping and not _missing(mapping[candidate]):
            return mapping[candidate]
    return next(iter(mapping.values()))


def localize_tree(obj: Any, lang: str) -> Any:
    """Return a copy of ``obj`` where every {en, fr} mapping is replaced by the chosen language.

    Plain strings, numbers, ids and dates pass through unchanged; the input is never mutated.
    """
    lang = normalize_lang(lang)
    if isinstance(obj, dict):
        if is_lang_mapping(obj):
            return localize_tree(pick(obj, lang), lang)
        return {k: localize_tree(v, lang) for k, v in obj.items()}
    if isinstance(obj, list):
        return [localize_tree(v, lang) for v in obj]
    if isinstance(obj, tuple):
        return [localize_tree(v, lang) for v in obj]
    return obj
