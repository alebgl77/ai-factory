"""Deterministic demo generator (fictional data only).

``generate_demo(config, seed, anchor)`` returns the raw inputs of the engines for ``days`` days
of history ending on ``anchor``: daily usage and costs per use case, pipeline release runs
(with at least one automatic rollback and one deploy blocked by a model-version change),
data-loop signals (covering the four allowed actions), Agent Ops task samples and series,
local GPU telemetry, vector index statistics and placement target status.

Same configuration + seed + anchor date => identical output (string-seeded ``random.Random``
streams, no wall clock, no hash randomisation). ``lang`` only changes the human-readable details
(message catalogue ``app/i18n.py``): every id, number and date is the same in every language.
"""

from __future__ import annotations

import datetime as dt
import math
import random
from typing import Any
from zoneinfo import ZoneInfo

from .engines.costs import local_gpu_rates, model_for_alias
from .engines.placement import target_label
from .engines.risk import compute_tier, reevaluation
from .i18n import DEFAULT_LANG, msg

WEEK_AVG = (5 * 1.0 + 0.35 + 0.25) / 7  # mean of the weekday traffic factors used below
STAGE_WEIGHT = {"run": 1.0, "indus": 0.85, "mvp": 0.55, "poc": 0.2, "cadrage": 0.0, "retired": 0.0}
TASK_TOKENS = {  # (prompt tokens, completion tokens) per request
    "rag_qa": (2400, 380), "agent": (5200, 900), "classification": (650, 25), "extraction": (1600, 260),
    "routing": (320, 12), "generation": (1100, 850), "summarization": (3200, 420),
}
TASK_TOKENS_PER_TASK = {"agent": 7200, "rag_qa": 3600, "generation": 2600, "summarization": 3900,
                        "extraction": 2000, "classification": 700, "routing": 350}
DEPLOYABLE_STAGES = ("mvp", "indus", "run")
PIPELINE_STAGES = ("poc", "mvp", "indus", "run")
LOCAL_TOKENS_PER_GPU_HOUR = 400_000
REFERENCE_HOUR = 10
DEFAULT_PRICE = (0.001, 0.003)


class _Rng:
    def __init__(self, seed: int, anchor: dt.date) -> None:
        self.prefix = f"{seed}|{anchor.isoformat()}"

    def __call__(self, key: str) -> random.Random:
        return random.Random(f"{self.prefix}|{key}")


def _tz(cfg: dict[str, Any]) -> dt.tzinfo:
    name = ((cfg.get("factory") or {}).get("factory") or {}).get("timezone") or "UTC"
    try:
        return ZoneInfo(str(name))
    except Exception:  # unknown zone name: fall back to UTC
        return dt.timezone.utc


def reference_time(cfg: dict[str, Any], anchor: dt.date) -> dt.datetime:
    return dt.datetime(anchor.year, anchor.month, anchor.day, REFERENCE_HOUR, 0, 0, tzinfo=_tz(cfg))


def _iso(t: dt.datetime) -> str:
    return t.replace(microsecond=0).isoformat()


def _pick(candidates: list[str], used: set[str]) -> str | None:
    for c in candidates:
        if c not in used:
            used.add(c)
            return c
    return candidates[0] if candidates else None


def _bump_version(version: str, anchor: dt.date) -> str:
    candidate = anchor.strftime("%Y-%m")
    return candidate if candidate != version else f"{version}.1"


def _semver(rng: random.Random, prefix: str) -> tuple[str, str]:
    major, minor, patch = rng.randint(1, 2), rng.randint(0, 9), rng.randint(1, 6)
    return f"{prefix}-{major}.{minor}.{patch}", f"{prefix}-{major}.{minor}.{patch - 1}"


def generate_demo(cfg: dict[str, Any], seed: int, anchor: dt.date, days: int = 30,
                  lang: str = DEFAULT_LANG) -> dict[str, Any]:
    days = max(1, int(days))
    rng = _Rng(seed, anchor)
    fcfg = cfg.get("factory") or {}
    usecases = list(cfg.get("usecases") or [])
    models = list((cfg.get("catalog") or {}).get("models") or [])
    agents_cfg = list((cfg.get("catalog") or {}).get("agents") or [])
    sensitive_classes = set((fcfg.get("placement") or {}).get("sensitive_classes") or ["C3", "C4"])
    eval_gates = fcfg.get("eval_gates") or {}
    rates = fcfg.get("rates") or {}
    gpu_rates = local_gpu_rates(rates)
    ref = reference_time(cfg, anchor)

    def is_sensitive(uc: dict[str, Any]) -> bool:
        return uc.get("sensitivity") in sensitive_classes

    active = [u for u in usecases if STAGE_WEIGHT.get(str(u.get("stage")), 0.3) > 0]
    cloud_active = [u for u in active if not is_sensitive(u)]
    by_type = lambda types, pool: [str(u["id"]) for u in pool if u.get("task_type") in types]  # noqa: E731

    # ---------------------------------------------------------------- special picks (waste rules)
    used: set[str] = set()
    pool = [str(u["id"]) for u in cloud_active] or [str(u["id"]) for u in active]
    cache_pick = _pick(by_type(("rag_qa", "summarization"), cloud_active) + pool, used)
    retry_pick = _pick(by_type(("agent", "extraction"), cloud_active) + pool, used)
    bloat_pick = _pick(by_type(("agent", "rag_qa", "generation"), cloud_active) + pool, used)
    eval_pick = _pick(pool, used)
    reindex_gpu_pick = (by_type(("rag_qa",), cloud_active) or [None])[0]

    # Cost centre targets: spend is derived from the configured budgets so that the demo stays
    # consistent with any configuration; one cost centre is driven close to its budget.
    centers = {str(c.get("id")): c for c in fcfg.get("cost_centers") or [] if isinstance(c, dict) and c.get("id")}
    cc_weight: dict[str, float] = {}
    for u in cloud_active:
        cc = str(u.get("cost_center"))
        cc_weight[cc] = cc_weight.get(cc, 0.0) + STAGE_WEIGHT.get(str(u.get("stage")), 0.3)
    breach_cc = max(sorted(cc_weight), key=lambda c: cc_weight[c]) if cc_weight else None
    cc_factor = {}
    for cc in sorted(cc_weight):
        cc_factor[cc] = 0.97 if cc == breach_cc else rng(f"ccf:{cc}").uniform(0.42, 0.72)

    # ---------------------------------------------------------------- usage records
    usage: list[dict[str, Any]] = []
    profile: dict[str, dict[str, Any]] = {}
    for u in active:
        uid = str(u["id"])
        r = rng(f"usage:{uid}")
        weight = STAGE_WEIGHT.get(str(u.get("stage")), 0.3)
        alias = (u.get("model") or {}).get("alias")
        model = model_for_alias(models, alias) or {}
        pin = float(model.get("price_in_per_1k") if model.get("price_in_per_1k") is not None else DEFAULT_PRICE[0])
        pout = float(model.get("price_out_per_1k") if model.get("price_out_per_1k") is not None else DEFAULT_PRICE[1])
        tin, tout = TASK_TOKENS.get(str(u.get("task_type")), (1500, 300))
        sensitive = is_sensitive(u)
        local = sensitive or model.get("hosting") == "local"
        if local:
            pin = pout = 0.0
        eur_req = (tin * pin + tout * pout) / 1000.0
        cc = str(u.get("cost_center"))
        if not local and cc in cc_weight and eur_req > 0:
            budget = float((centers.get(cc) or {}).get("budget_month_eur") or 250.0)
            target_month = budget * cc_factor[cc] * weight / cc_weight[cc]
            # Leave room for the GPU, eval and retry shares of the cost centre; weekend traffic is
            # lower (factor 0.35 / 0.25 below), so scale the weekday volume by the weekly average.
            per_day = target_month / 30.0 / (eur_req * 1.12) / WEEK_AVG
        else:
            per_day = 420.0 * weight
        per_day = max(5.0, min(per_day, 40000.0 * weight))
        retry = 0.085 if uid == retry_pick else r.uniform(0.004, 0.028)
        repeat = 0.56 if uid == cache_pick else r.uniform(0.12, 0.34)
        cache_hit = 0.07 if uid == cache_pick else r.uniform(0.22, 0.42)
        eval_share = 0.33 if uid == eval_pick else r.uniform(0.05, 0.17)
        growth = 0.46 if uid == bloat_pick else r.uniform(0.0, 0.1)
        trend = 0.12 if cc == breach_cc else r.uniform(-0.04, 0.16)
        profile[uid] = {"tin": tin, "tout": tout, "pin": pin, "pout": pout, "local": local, "per_day": per_day,
                        "alias": alias}
        for k in range(days):
            d = anchor - dt.timedelta(days=days - 1 - k)
            progress = k / (days - 1) if days > 1 else 1.0
            weekday = 1.0 if d.weekday() < 5 else (0.35 if d.weekday() == 5 else 0.25)
            noise = r.uniform(0.92, 1.08)
            requests = int(round(per_day * weekday * noise * (1.0 + trend * (progress - 0.5))))
            retries = int(round(requests * retry * r.uniform(0.85, 1.15)))
            prompt = tin * (1.0 + growth * progress)
            calls = requests + retries
            tokens_in = int(round(calls * prompt))
            tokens_out = int(round(calls * tout * r.uniform(0.9, 1.1)))
            cached = int(round(tokens_in * cache_hit * r.uniform(0.9, 1.1)))
            llm_in = max(0.0, (tokens_in - cached * 0.9)) / 1000.0 * pin
            llm_out = tokens_out / 1000.0 * pout
            serving = llm_in + llm_out
            eval_eur = serving * eval_share / (1.0 - eval_share) if serving else 0.0
            gpu_hours = (tokens_in + tokens_out) / LOCAL_TOKENS_PER_GPU_HOUR if local else 0.0
            if local:
                gpu_hours *= 1.0 + eval_share  # local judge for sensitive data
            cloud_gpu = {"l4": 1.2} if (uid == reindex_gpu_pick and k % 7 == 3) else {}
            usage.append({
                "date": d.isoformat(), "usecase": uid, "model_alias": alias,
                "requests": requests, "retries": retries, "tokens_in": tokens_in, "tokens_out": tokens_out,
                "cached_tokens": cached, "repeat_ratio": round(repeat * r.uniform(0.95, 1.05), 4),
                "avg_prompt_tokens": round(prompt, 1),
                "llm_in_eur": round(llm_in, 6), "llm_eur": round(serving + eval_eur, 6),
                "eval_eur": round(eval_eur, 6), "gpu_hours": round(gpu_hours, 4), "cloud_gpu_hours": cloud_gpu,
            })

    # ---------------------------------------------------------------- pipeline runs
    base_risk = {}
    for u in usecases:
        flag, _reason = reevaluation(u)
        base_risk[str(u["id"])] = {"tier": compute_tier(u.get("ai_act")), "reeval": flag,
                                   "decision": (u.get("assessment") or {}).get("decision")}
    pipe_ucs = [u for u in usecases if u.get("stage") in PIPELINE_STAGES]
    deployable = [str(u["id"]) for u in pipe_ucs if u.get("stage") in DEPLOYABLE_STAGES
                  and base_risk[str(u["id"])]["tier"] != "prohibited"
                  and base_risk[str(u["id"])]["decision"] != "rejected"]
    naturally_blocked = [i for i in deployable if base_risk[i]["reeval"]]
    free = [i for i in deployable if i not in naturally_blocked]
    pused: set[str] = set()
    forced_block = None
    if not naturally_blocked and len(free) > 1:
        forced_block = free[-1]
        pused.add(forced_block)
    rollback_pick = next((i for i in free if i not in pused), None)
    if rollback_pick:
        pused.add(rollback_pick)
    eval_fail_pick = next((i for i in free if i not in pused), None)
    if eval_fail_pick:
        pused.add(eval_fail_pick)
    history_pick = next((i for i in free if i not in (rollback_pick, eval_fail_pick)), None) or rollback_pick
    in_progress_pick = next((str(u["id"]) for u in pipe_ucs if u.get("stage") == "poc"), None)
    agent_version_by_uc = {}
    for a in agents_cfg:
        if isinstance(a, dict) and a.get("usecase") and a.get("usecase") not in agent_version_by_uc:
            agent_version_by_uc[a["usecase"]] = a.get("version")

    runs = []
    for u in pipe_ucs:
        uid = str(u["id"])
        r = rng(f"pipeline:{uid}")
        prefix = {"rag_qa": "rag", "agent": "agent"}.get(str(u.get("task_type")), "svc")
        version, previous = _semver(r, prefix)
        tier = base_risk[uid]["tier"]
        th = eval_gates.get(tier) or eval_gates.get("high") or {}
        metrics = {}
        for key, value in th.items():
            if key == "judge_score":
                metrics[key] = round(min(5.0, float(value) + r.uniform(0.12, 0.5)), 2)
            else:
                metrics[key] = round(min(0.995, float(value) + r.uniform(0.012, 0.06)), 3)
        if uid == eval_fail_pick and "context_precision" in metrics:
            metrics["context_precision"] = round(float(th["context_precision"]) - 0.07, 3)
        elif uid == eval_fail_pick and metrics:
            first = sorted(metrics)[0]
            metrics[first] = round(float(th[first]) * 0.9, 3)
        deploy_at = ref - dt.timedelta(days=r.randint(1, 5), hours=r.randint(0, 6))
        t_ing = deploy_at - dt.timedelta(hours=r.randint(20, 40))
        model_version = (u.get("model") or {}).get("version")
        candidate = False
        if uid == forced_block and model_version:
            model_version = _bump_version(str(model_version), anchor)
            candidate = True
        corpus = (u.get("rag") or {}).get("corpus_docs")
        run = {
            "usecase": uid,
            "version": version,
            "previous_version": previous,
            "prompt_version": f"p-{r.randint(3, 28)}",
            "agent_version": agent_version_by_uc.get(uid),
            "model_version": model_version,
            "candidate_model_upgrade": candidate,
            "stages": {
                "ingestion": {"at": _iso(t_ing), "detail": msg(lang, "demo.ingestion.documents", count=corpus) if corpus
                              else msg(lang, "demo.ingestion.sources")},
                "indexing": {"at": _iso(t_ing + dt.timedelta(hours=3)),
                             "detail": msg(lang, "demo.indexing", size=(u.get("rag") or {}).get("chunk_size", "-"),
                                           overlap=(u.get("rag") or {}).get("chunk_overlap", "-"))},
                "versioning": {"at": _iso(t_ing + dt.timedelta(hours=5)), "detail": msg(lang, "demo.versioning")},
                "deploy": {"at": _iso(deploy_at)},
            },
            "eval": {"at": _iso(t_ing + dt.timedelta(hours=8)), "metrics": metrics,
                     "suite": "RAGAS + LLM-as-judge + red team"},
        }
        if uid == in_progress_pick:
            run["in_progress_stage"] = "indexing"
        online_at = _iso(deploy_at + dt.timedelta(hours=18))
        if uid == rollback_pick:
            run["online"] = {"at": online_at, "thumbs_down_ratio": 2.6, "faithfulness_drop": 0.03, "error_rate": 0.012}
        else:
            run["online"] = {"at": online_at, "thumbs_down_ratio": round(r.uniform(0.7, 1.4), 2),
                             "faithfulness_drop": round(r.uniform(0.0, 0.04), 3),
                             "error_rate": round(r.uniform(0.003, 0.02), 3)}
        runs.append(run)

    rollback_history = []
    if history_pick:
        r = rng("rollback-history")
        major, minor = r.randint(1, 2), r.randint(0, 8)
        hist_uc = next((u for u in pipe_ucs if str(u["id"]) == history_pick), {})
        prefix = {"rag_qa": "rag", "agent": "agent"}.get(str(hist_uc.get("task_type")), "svc")
        rollback_history.append({
            "at": _iso(ref - dt.timedelta(days=12, hours=3)), "usecase": history_pick,
            "from": f"{prefix}-{major}.{minor}.3", "to": f"{prefix}-{major}.{minor}.2", "trigger": "error_rate",
            "value": 0.071, "threshold": float((fcfg.get("rollback") or {}).get("error_rate_max", 0.05)), "auto": True,
        })

    # ---------------------------------------------------------------- data loop signals
    signals = []
    active_ids = [str(u["id"]) for u in active] or [str(u["id"]) for u in usecases]
    rag_ids = by_type(("rag_qa",), active) or active_ids
    non_high = [i for i in active_ids if base_risk.get(i, {}).get("tier") not in ("high", "prohibited")] or active_ids
    high_ids = [i for i in active_ids if base_risk.get(i, {}).get("tier") == "high"]
    if active_ids:
        def at(days_ago: int, hour: int) -> str:
            return _iso(ref - dt.timedelta(days=days_ago, hours=hour))

        sused: set[str] = set()

        def spread(candidates: list[str]) -> str:
            """Prefer a use case not used yet by another signal (more varied demo)."""
            return _pick(candidates + active_ids, sused) or active_ids[0]

        eval_uc = eval_fail_pick or active_ids[-1]
        sused.add(eval_uc)
        signals += [
            {"at": at(9, 2), "usecase": spread(rag_ids), "signal": "retrieval_hit_rate", "value": 0.64,
             "threshold": 0.75, "status": "done",
             "detail": msg(lang, "demo.signal.retrieval_hit_rate")},
            {"at": at(1, 4), "usecase": spread(rag_ids), "signal": "corpus_freshness", "value": 0.16,
             "threshold": 0.10, "status": "running",
             "detail": msg(lang, "demo.signal.corpus_freshness", pct=16)},
            {"at": at(14, 1), "usecase": spread(active_ids), "signal": "query_drift", "value": 0.33,
             "threshold": 0.25, "status": "done",
             "detail": msg(lang, "demo.signal.query_drift", count=40)},
            {"at": at(2, 3), "usecase": eval_uc, "signal": "eval_failure_cluster", "value": 8, "threshold": 5,
             "status": "running", "detail": msg(lang, "demo.signal.eval_failure_cluster")},
            {"at": at(6, 5), "usecase": spread(non_high), "signal": "cost_drift", "value": 31.0, "threshold": 20.0,
             "status": "done", "detail": msg(lang, "demo.signal.cost_drift", pct=31, alias="chat-small")},
            {"at": at(0, 1), "usecase": spread(non_high), "signal": "latency_drift", "value": 47.0,
             "threshold": 20.0, "status": "proposed",
             "detail": msg(lang, "demo.signal.latency_drift", pct=47)},
        ]
        if high_ids:
            signals.append({"at": at(4, 2), "usecase": high_ids[0], "signal": "cost_drift", "value": 26.0,
                            "threshold": 20.0, "status": "proposed",
                            "detail": msg(lang, "demo.signal.cost_drift_high_risk")})

    # ---------------------------------------------------------------- agent ops
    agents = [a for a in agents_cfg if isinstance(a, dict) and a.get("id")]
    if not agents:
        agents = [{"id": f"ag-{u['id']}", "usecase": u["id"]} for u in active
                  if u.get("task_type") in ("agent", "rag_qa")]
    uc_by_id = {str(u["id"]): u for u in usecases}
    slow_agent = next((str(a["id"]) for a in agents if is_sensitive(uc_by_id.get(str(a.get("usecase")), {}))),
                      str(agents[0]["id"]) if agents else None)
    heavy_agent = next((str(a["id"]) for a in agents if str(a.get("usecase")) == bloat_pick and str(a["id"]) != slow_agent),
                       None)
    samples: dict[str, list[dict[str, Any]]] = {}
    for a in agents:
        aid = str(a["id"])
        uc = uc_by_id.get(str(a.get("usecase")), {})
        if uc and STAGE_WEIGHT.get(str(uc.get("stage")), 0.3) == 0:
            continue
        r = rng(f"agent:{aid}")
        sensitive = is_sensitive(uc)
        n = r.randint(30, 140)
        ttft_base = (900.0 if sensitive else 520.0) * (1.6 if aid == slow_agent else 1.0)
        tok_base = TASK_TOKENS_PER_TASK.get(str(uc.get("task_type")), 3000) * (1.35 if aid == heavy_agent else 1.0)
        vec_base = r.uniform(38.0, 55.0)
        success_p = r.uniform(0.92, 0.99)
        p = profile.get(str(a.get("usecase")), {"pin": DEFAULT_PRICE[0], "pout": DEFAULT_PRICE[1], "local": sensitive})
        blended = (0.85 * p["pin"] + 0.15 * p["pout"])
        rows = []
        for _ in range(n):
            tokens = max(200, int(r.gauss(tok_base, tok_base * 0.3)))
            rows.append({
                "ttft_ms": round(ttft_base * math.exp(r.gauss(0.0, 0.33)), 1),
                "tokens": tokens,
                "vector_latency_ms": round(vec_base * math.exp(r.gauss(0.0, 0.35)), 1),
                "success": r.random() < success_p,
                "eur": round(tokens / 1000.0 * blended, 6) if not p["local"] else round(tokens / LOCAL_TOKENS_PER_GPU_HOUR * gpu_rates["hour"], 6),
            })
        samples[aid] = rows
    series = []
    r = rng("agentops-series")
    for h in range(23, -1, -1):
        t = ref - dt.timedelta(hours=h)
        load = 1.0 + 0.35 * math.sin((t.hour - 8) / 24.0 * 2 * math.pi)
        series.append({"t": _iso(t), "ttft_ms_p95": round(1050 * load * r.uniform(0.9, 1.1)),
                       "vector_latency_ms_p95": round(78 * load * r.uniform(0.9, 1.1)),
                       "tokens_per_task_avg": round(4100 * r.uniform(0.9, 1.1))})

    # ---------------------------------------------------------------- local GPU telemetry
    r = rng("gpu")
    lg = rates.get("local_gpu") or {}
    max_power = float(lg.get("power_w") or 240)
    gpu_series = []
    for h in range(23, -1, -1):
        t = ref - dt.timedelta(hours=h)
        hour = t.hour
        if hour < 7:
            util = r.uniform(2.0, 7.0)
        elif hour < 9:
            util = r.uniform(18.0, 35.0)
        elif hour < 19:
            util = r.uniform(45.0, 85.0)
        elif hour in (21, 22):
            util = r.uniform(55.0, 70.0)  # nightly evaluation batch
        else:
            util = r.uniform(12.0, 25.0)
        gpu_series.append({"t": _iso(t), "device": "gpu0", "util_pct": round(util, 1),
                           "power_w": round(min(max_power, 30.0 + util / 100.0 * (max_power - 30.0)), 1)})
    cur = gpu_series[-1]
    sensitive_active = [u for u in active if is_sensitive(u)]
    gpu = {
        "devices": [{
            "id": "gpu0", "name": msg(lang, "demo.gpu.name"), "util_pct": cur["util_pct"],
            "temp_c": round(33.0 + cur["util_pct"] * 0.42, 1), "power_w": cur["power_w"],
            "mem_used_gb": round(min(23.5, 5.5 + cur["util_pct"] * 0.15), 1), "mem_total_gb": 24.0,
            "sensitive_sessions": sum(r.randint(1, 4) for _ in sensitive_active),
        }],
        "series": gpu_series,
    }

    # ---------------------------------------------------------------- vector indexes
    r = rng("indexes")
    idx: dict[str, dict[str, Any]] = {}
    for u in usecases:
        rag = u.get("rag") or {}
        if not rag.get("index"):
            continue
        entry = idx.setdefault(str(rag["index"]), {"index": str(rag["index"]), "usecases": [], "vectors": 0,
                                                    "active": False})
        entry["usecases"].append(str(u["id"]))
        entry["vectors"] += int(rag.get("corpus_docs") or 500) * 6
        entry["active"] = entry["active"] or STAGE_WEIGHT.get(str(u.get("stage")), 0.3) > 0
    indexes = []
    for name in sorted(idx):
        e = idx[name]
        ago = dt.timedelta(minutes=r.randint(2, 600)) if e["active"] else dt.timedelta(days=r.randint(30, 60))
        ram_gb = e["vectors"] * 1024 * 4 / 1e9 * 1.5
        indexes.append({"index": name, "usecases": e["usecases"], "vectors": e["vectors"],
                        "last_query_at": _iso(ref - ago), "ram_gb": round(ram_gb, 3),
                        "eur_month": round(max(1.5, ram_gb * 6.0), 2)})
    base_name = sorted(idx)[0] if idx else "kb-archive"
    orphan_vectors = idx[base_name]["vectors"] if idx else 9000
    orphan_ram = orphan_vectors * 1024 * 4 / 1e9 * 1.5
    indexes.append({"index": f"{base_name}-v1", "usecases": [], "vectors": orphan_vectors,
                    "last_query_at": _iso(ref - dt.timedelta(days=37)), "ram_gb": round(orphan_ram, 3),
                    "eur_month": round(max(3.0, orphan_ram * 6.0), 2),
                    "detail": msg(lang, "demo.index.orphan_detail")})

    # ---------------------------------------------------------------- placement targets
    cloud_models = [m for m in models if m.get("hosting") == "cloud" and m.get("price_in_per_1k") is not None]
    cloud_price = (sum((float(m["price_in_per_1k"]) + float(m.get("price_out_per_1k") or 0)) / 2 for m in cloud_models)
                   / len(cloud_models)) if cloud_models else 0.002
    vps_month = float(rates.get("vps_eur_per_month") or 40)
    r = rng("targets")
    targets = [
        {"id": "cloud", "label": target_label("cloud", lang, demo=True), "status": "up",
         "util_pct": round(r.uniform(25, 40)),
         "eur_per_1k_tokens": round(cloud_price, 5), "latency_ms_p95": round(r.uniform(850, 1100)),
         "accepts_sensitive": False},
        {"id": "vps", "label": target_label("vps", lang), "status": "up", "util_pct": round(r.uniform(35, 55)),
         "eur_per_1k_tokens": round(vps_month / 60_000.0, 5), "latency_ms_p95": round(r.uniform(2300, 2900)),
         "accepts_sensitive": False,
         "max_params_b": (fcfg.get("placement") or {}).get("vps_max_params_b")},
        {"id": "local", "label": target_label("local", lang), "status": "up", "util_pct": round(cur["util_pct"]),
         "eur_per_1k_tokens": round(gpu_rates["hour"] / (LOCAL_TOKENS_PER_GPU_HOUR / 1000.0), 5),
         "latency_ms_p95": round(r.uniform(700, 900)), "accepts_sensitive": True},
    ]

    return {
        "seed": seed,
        "anchor_date": anchor.isoformat(),
        "reference_time": _iso(ref),
        "days": days,
        "usage": usage,
        "pipeline_runs": runs,
        "rollback_history": rollback_history,
        "signals": signals,
        "agent_samples": samples,
        "agentops_series": series,
        "gpu": gpu,
        "indexes": indexes,
        "targets": targets,
        "picks": {"cache": cache_pick, "retry": retry_pick, "bloat": bloat_pick, "eval": eval_pick,
                  "budget_cost_center": breach_cc, "rollback": rollback_pick, "eval_fail": eval_fail_pick,
                  "forced_block": forced_block, "naturally_blocked": naturally_blocked},
    }
