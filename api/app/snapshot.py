"""Assemble the dashboard snapshot (CONTRACT section 4) from config, demo and live inputs.

``assemble_snapshot`` is pure: it receives the configuration, the effective use cases (Git
baseline + state overlay), the demo inputs (or None) and the live inputs (possibly empty) and
returns the JSON document. Section provenance is reported in ``meta.sections``.

Language (CONTRACT section 9): every engine receives ``lang``; generated texts come from the message
catalogue (``app/i18n.py``) and the final document goes through :func:`i18n.localize_tree`, so no
``{en, fr}`` mapping ever reaches the browser. Ids, enums, numbers and dates do not depend on ``lang``.
"""

from __future__ import annotations

import copy
import datetime as dt
from typing import Any

from .engines.agentops import evaluate_agentops
from .engines.catalog import evaluate_catalog
from .engines.costs import compute_costs
from .engines.dataloop import evaluate_signals
from .engines.pipeline import evaluate_pipeline
from .engines.placement import evaluate_placement, target_label
from .engines.risk import evaluate_registry, tier_counts
from .engines.skills import evaluate_rnd, evaluate_skills
from .i18n import DEFAULT_LANG, LANGUAGES, localize_tree, msg, normalize_lang
from .settings import API_VERSION

SNAPSHOT_KEYS = ("meta", "factory", "profiles", "registry", "costs", "pipeline", "dataloop", "placement",
                 "agentops", "catalog", "skills", "rnd", "gpu", "grafana", "frameworks")
LIVE_CAPABLE = ("costs", "agentops", "gpu", "placement", "grafana")


def _targets(demo: dict[str, Any] | None, live: dict[str, Any], sources: dict[str, bool],
             demo_allowed: bool, lang: str = DEFAULT_LANG) -> tuple[list[dict[str, Any]], str]:
    if demo_allowed and demo:
        targets = copy.deepcopy(demo.get("targets") or [])
        origin = "demo"
    else:
        gateway = "up" if sources.get("litellm") else "down"
        targets = [
            {"id": "cloud", "label": target_label("cloud", lang), "status": gateway, "util_pct": None,
             "eur_per_1k_tokens": None, "latency_ms_p95": None, "accepts_sensitive": False},
            {"id": "vps", "label": target_label("vps", lang), "status": gateway, "util_pct": None,
             "eur_per_1k_tokens": None, "latency_ms_p95": None, "accepts_sensitive": False},
            {"id": "local", "label": target_label("local", lang), "status": "down", "util_pct": None,
             "eur_per_1k_tokens": None, "latency_ms_p95": None, "accepts_sensitive": True},
        ]
        origin = "live"
    touched = False
    for t in targets:
        dep = (live.get("targets") or {}).get(t["id"])
        if dep:
            t["status"] = dep["status"]
            if dep.get("latency_ms_p95") is not None:
                t["latency_ms_p95"] = dep["latency_ms_p95"]
            t["error_ratio"] = dep.get("error_ratio")
            touched = True
    gpu = live.get("gpu")
    if gpu and gpu.get("devices"):
        utils = [float(d.get("util_pct") or 0) for d in gpu["devices"]]
        for t in targets:
            if t["id"] == "local":
                if not (live.get("targets") or {}).get("local"):
                    t["status"] = "up"
                t["util_pct"] = round(sum(utils) / len(utils))
        touched = True
    if touched:
        origin = "mixed" if origin == "demo" else "live"
    return targets, origin


def assemble_snapshot(cfg: dict[str, Any], usecases: list[dict[str, Any]], demo: dict[str, Any] | None,
                      live: dict[str, Any], *, demo_mode: str, seed: int, today: dt.date, now: dt.datetime,
                      days: int = 30, cost_mode: str | None = None,
                      sources: dict[str, bool] | None = None, lang: str = DEFAULT_LANG,
                      languages: tuple[str, ...] | list[str] = LANGUAGES) -> dict[str, Any]:
    lang = normalize_lang(lang)
    # Configuration text may still hold {en, fr} mappings when called directly: resolve it first.
    cfg = localize_tree(cfg, lang)
    usecases = localize_tree(usecases, lang)
    demo = localize_tree(demo, lang) if demo is not None else None
    sources = dict(sources or {})
    fcfg = cfg.get("factory") or {}
    factory_meta = fcfg.get("factory") or {}
    demo_allowed = demo_mode in ("auto", "always") and demo is not None
    live_allowed = demo_mode in ("auto", "never")
    live = live if live_allowed else {}
    sections: dict[str, str] = {"registry": "config", "catalog": "config", "skills": "config", "rnd": "config"}
    people = (cfg.get("profiles") or {}).get("people") or []
    models = (cfg.get("catalog") or {}).get("models") or []
    policy = fcfg.get("placement") or {}

    registry = evaluate_registry(usecases, fcfg, cfg.get("controls") or [], people, today, lang)

    # GPU telemetry (DCGM through Prometheus, else simulated).
    if live.get("gpu"):
        gpu_in, gpu_source = live["gpu"], live["gpu"].get("source", "dcgm")
        sections["gpu"] = "live"
    elif demo_allowed:
        gpu_in, gpu_source = demo["gpu"], "sim"
        sections["gpu"] = "demo"
    else:
        gpu_in, gpu_source = {"devices": [], "series": []}, "none"
        sections["gpu"] = "none"

    # Vector indexes: live collections from Qdrant (usage mapped from the registry), else demo.
    if live.get("qdrant_collections") is not None:
        by_index: dict[str, list[str]] = {}
        for uc in usecases:
            idx = (uc.get("rag") or {}).get("index")
            if idx:
                by_index.setdefault(str(idx), []).append(str(uc.get("id")))
        indexes = [{**c, "usecases": by_index.get(c["index"], []), "last_query_at": None,
                    "eur_month": c.get("eur_month", 0.0)} for c in live["qdrant_collections"]]
    elif demo_allowed:
        indexes = demo.get("indexes") or []
    else:
        indexes = []

    # Costs.
    if live.get("usage"):
        usage, sections["costs"] = live["usage"], "live"
        if live.get("usage_origin") == "litellm_spend_logs":
            cfg = {**cfg, "warnings": list(cfg.get("warnings") or []) + [msg(lang, "warning.litellm_spend_logs")]}
    elif demo_allowed:
        usage, sections["costs"] = demo["usage"], "demo"
    else:
        usage, sections["costs"] = [], "none"
    costs = compute_costs(usage, usecases, fcfg, models, days=days, today=today, mode=cost_mode,
                          gpu_series=gpu_in.get("series"), indexes=indexes, lang=lang)

    # Pipeline: runs are evaluated against the *effective* registry (overlay included).
    effective_versions = {str(u.get("id")): (u.get("model") or {}).get("version") for u in usecases}
    runs = []
    if demo_allowed:
        for run in demo.get("pipeline_runs") or []:
            run = copy.deepcopy(run)
            if not run.get("candidate_model_upgrade"):
                run["model_version"] = effective_versions.get(run.get("usecase"), run.get("model_version"))
            runs.append(run)
    pipeline = evaluate_pipeline(runs, registry, fcfg, (demo or {}).get("rollback_history") if demo_allowed else [],
                                 lang=lang)
    sections["pipeline"] = "demo" if demo_allowed else "none"

    dataloop = evaluate_signals((demo or {}).get("signals") if demo_allowed else [], registry, fcfg, lang=lang)
    sections["dataloop"] = "demo" if demo_allowed else "none"

    targets, sections["placement"] = _targets(demo, live, sources, demo_allowed, lang)
    placement = evaluate_placement(registry, targets, models, policy, lang=lang)

    # Agent Ops.
    if live.get("agent_samples"):
        samples, series, sections["agentops"] = live["agent_samples"], [], "live"
    elif demo_allowed:
        samples, series, sections["agentops"] = demo["agent_samples"], demo.get("agentops_series"), "demo"
    else:
        samples, series, sections["agentops"] = {}, [], "none"
    agentops = evaluate_agentops(samples, (cfg.get("catalog") or {}).get("agents") or [],
                                 fcfg.get("agentops_slo") or {}, series)

    catalog = evaluate_catalog(cfg.get("catalog") or {}, registry, people, list(policy.get("sensitive_classes") or []),
                               lang=lang)
    if live.get("litellm_models") is not None:
        catalog["gateway_models"] = live["litellm_models"]
    skills = evaluate_skills(cfg.get("skills") or {}, cfg.get("profiles") or {}, fcfg.get("regulatory"), today,
                             lang=lang)
    rnd = evaluate_rnd(cfg.get("skills") or {}, cfg.get("profiles") or {})

    devices = copy.deepcopy(gpu_in.get("devices") or [])
    if sections["gpu"] == "live":
        local_sensitive = sum(1 for w in placement["workloads"] if w["target"] == "local"
                              and w.get("sensitivity") in (policy.get("sensitive_classes") or []))
        for d in devices:
            d["sensitive_sessions"] = local_sensitive
    gpu = {"source": gpu_source, "available": bool(devices),
           "devices": devices, "series": copy.deepcopy(gpu_in.get("series") or [])}

    dashboards = live.get("grafana_dashboards")
    grafana = {"available": dashboards is not None, "dashboards": dashboards or []}
    sections["grafana"] = "live" if dashboards is not None else "none"

    used_live = any(sections.get(s) in ("live", "mixed") for s in LIVE_CAPABLE)
    used_demo = any(v in ("demo", "mixed") for v in sections.values())
    if demo_mode == "always":
        mode = "demo"
    elif demo_mode == "never":
        mode = "live"
    else:
        mode = "mixed" if used_live and used_demo else ("live" if used_live else "demo")

    regulatory = fcfg.get("regulatory") or {}
    warnings = list(cfg.get("warnings") or []) + list(skills.get("warnings") or [])
    if factory_meta.get("fine_tuning"):
        warnings.append(msg(lang, "warning.fine_tuning_ignored"))
    return localize_tree({
        "meta": {
            "mode": mode,
            "generated_at": now.replace(microsecond=0).isoformat(),
            "seed": seed,
            "anchor_date": today.isoformat(),
            "sources": {k: bool(sources.get(k, False)) for k in ("prometheus", "grafana", "langfuse", "litellm", "qdrant")},
            "sections": sections,
            "fine_tuning": False,
            "version": API_VERSION,
            "demo_mode": demo_mode,
            "warnings": warnings,
            "lang": lang,
            "languages": list(languages),
        },
        "factory": {
            "name": factory_meta.get("name"),
            "tagline": factory_meta.get("tagline"),
            "currency": factory_meta.get("currency", "EUR"),
            "timezone": factory_meta.get("timezone"),
            "demo": bool(factory_meta.get("demo", True)),
            "fine_tuning": False,
            "regulatory": regulatory,
            "cost_centers": fcfg.get("cost_centers") or [],
            "risk_counts": tier_counts(registry),
        },
        "profiles": {"roles": (cfg.get("profiles") or {}).get("roles") or [],
                     "people": (cfg.get("profiles") or {}).get("people") or []},
        "registry": registry,
        "costs": costs,
        "pipeline": pipeline,
        "dataloop": dataloop,
        "placement": placement,
        "agentops": agentops,
        "catalog": catalog,
        "skills": skills,
        "rnd": rnd,
        "gpu": gpu,
        "grafana": grafana,
        # Indicative NIST AI RMF <-> AI Act <-> ISO/IEC 42001 crosswalk (config/frameworks/crosswalk.yaml)
        "frameworks": {
            "crosswalk": [
                {k: c.get(k) for k in ("id", "title", "nist", "ai_act", "iso42001", "tiers", "evidence_hint", "basis") if k in c}
                for c in cfg.get("controls") or []
            ],
            "meta": ((cfg.get("frameworks") or {}).get("crosswalk") or {}).get("meta") or {},
        },
    }, lang)
