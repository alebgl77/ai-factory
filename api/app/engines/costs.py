"""FinOps engine: token and GPU costs per use case, showback/chargeback, waste alerts.

Pure functions. Input records are daily aggregates per use case:

    {date, usecase, requests, retries, tokens_in, tokens_out, cached_tokens, repeat_ratio,
     avg_prompt_tokens, llm_eur, llm_in_eur?, eval_eur, gpu_hours, cloud_gpu_hours: {type: h}}

Money is computed in integer cents so that allocations always add up to the totals.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from ..i18n import DEFAULT_LANG, msg
from ..util import UNASSIGNED_COST_CENTER, UNATTRIBUTED, allocate_cents, cents, eur, mean, r2, r4, safe_div, \
    to_date, to_datetime

RULE_ORDER = (
    "oversized_model", "low_cache_hit", "retry_storm", "prompt_bloat",
    "idle_gpu", "orphan_index", "eval_overspend", "budget_breach",
)
HOURS_PER_MONTH = 720.0
DEFAULT_CHEAP_RATIO = 0.25  # when the catalog has no priced alternative model
CACHED_INPUT_DISCOUNT = 0.9  # share of the input price saved on a cache hit (indicative)


# --------------------------------------------------------------------------------------------
# Rates
# --------------------------------------------------------------------------------------------
def local_gpu_rates(rates: dict[str, Any]) -> dict[str, float]:
    lg = (rates or {}).get("local_gpu") or {}
    capex = float(lg.get("capex_eur") or 0)
    months = float(lg.get("amortization_months") or 0) or 1.0
    amort_month = capex / months
    amort_hour = amort_month / HOURS_PER_MONTH
    power_hour = float(lg.get("power_w") or 0) / 1000.0 * float(lg.get("kwh_eur") or 0)
    return {"amort_month": amort_month, "amort_hour": amort_hour, "power_hour": power_hour,
            "hour": amort_hour + power_hour}


def _cloud_gpu_eur(hours: dict[str, Any] | None, rates: dict[str, Any]) -> float:
    table = (rates or {}).get("cloud_gpu_eur_per_hour") or {}
    total = 0.0
    for kind, h in (hours or {}).items():
        total += float(h or 0) * float(table.get(kind, 0) or 0)
    return total


def model_for_alias(models: list[dict[str, Any]], alias: str | None) -> dict[str, Any] | None:
    for m in models or []:
        if alias and m.get("alias") == alias:
            return m
    return None


# --------------------------------------------------------------------------------------------
# Aggregation
# --------------------------------------------------------------------------------------------
def _window(records: list[dict[str, Any]], days: int, today: dt.date) -> list[dict[str, Any]]:
    start = today - dt.timedelta(days=days - 1)
    out = []
    for r in records or []:
        d = to_date(r.get("date"))
        if d and start <= d <= today:
            out.append({**r, "_date": d})
    return out


def _aggregate(window: list[dict[str, Any]], rates: dict[str, Any], gpu_rates: dict[str, float],
               days: int, today: dt.date) -> dict[str, dict[str, Any]]:
    agg: dict[str, dict[str, Any]] = {}
    split = days // 2 if days < 14 else 7
    recent_start = today - dt.timedelta(days=split - 1)
    prior_start = recent_start - dt.timedelta(days=split)
    for r in sorted(window, key=lambda x: (x["_date"], str(x.get("usecase")))):
        uc = str(r.get("usecase"))
        a = agg.setdefault(uc, {
            "tokens_in": 0, "tokens_out": 0, "cached_tokens": 0, "requests": 0, "retries": 0,
            "llm": 0.0, "llm_in": 0.0, "eval": 0.0, "gpu_hours": 0.0, "gpu": 0.0,
            "repeat_w": 0.0, "daily": defaultdict(float), "prompt_first": [], "prompt_last": [],
            "recent": 0.0, "prior": 0.0, "llm_recent": 0.0, "llm_in_recent": 0.0,
        })
        tin = int(r.get("tokens_in") or 0)
        tout = int(r.get("tokens_out") or 0)
        llm = float(r.get("llm_eur") or 0)
        llm_in = r.get("llm_in_eur")
        if llm_in is None:
            llm_in = llm * safe_div(tin, tin + 4 * tout, 0.5)  # output tokens ~4x pricier
        local_h = float(r.get("gpu_hours") or 0)
        cloud_h = sum(float(v or 0) for v in (r.get("cloud_gpu_hours") or {}).values())
        gpu = local_h * gpu_rates["hour"] + _cloud_gpu_eur(r.get("cloud_gpu_hours"), rates)
        a["tokens_in"] += tin
        a["tokens_out"] += tout
        a["cached_tokens"] += int(r.get("cached_tokens") or 0)
        req = int(r.get("requests") or 0)
        a["requests"] += req
        a["retries"] += int(r.get("retries") or 0)
        a["llm"] += llm
        a["llm_in"] += float(llm_in)
        a["eval"] += float(r.get("eval_eur") or 0)
        a["gpu_hours"] += local_h + cloud_h
        a["local_gpu_hours"] = a.get("local_gpu_hours", 0.0) + local_h
        a["gpu"] += gpu
        a["repeat_w"] += float(r.get("repeat_ratio") or 0) * max(req, 0)
        a["daily"][r["_date"]] += llm + gpu
        if r.get("model_alias"):
            a["model_alias"] = r.get("model_alias")
        d = r["_date"]
        if d >= recent_start:
            a["recent"] += llm + gpu
            a["llm_recent"] += llm
            a["llm_in_recent"] += float(llm_in)
            if r.get("avg_prompt_tokens"):
                a["prompt_last"].append(float(r["avg_prompt_tokens"]))
        elif d >= prior_start:
            a["prior"] += llm + gpu
        if d < today - dt.timedelta(days=days - split - 1) and r.get("avg_prompt_tokens"):
            a["prompt_first"].append(float(r["avg_prompt_tokens"]))
    return agg


def compute_costs(records: list[dict[str, Any]], usecases: list[dict[str, Any]], factory_cfg: dict[str, Any],
                  models: list[dict[str, Any]], *, days: int, today: dt.date, mode: str | None = None,
                  gpu_series: list[dict[str, Any]] | None = None,
                  indexes: list[dict[str, Any]] | None = None, lang: str = DEFAULT_LANG) -> dict[str, Any]:
    """FinOps view of the window. Text fields of the inputs must already be localised."""
    days = max(1, int(days))
    rates = factory_cfg.get("rates") or {}
    cb = factory_cfg.get("chargeback") or {}
    mode = mode or cb.get("mode") or "showback"
    if mode not in ("showback", "chargeback"):
        mode = "showback"
    allocation = cb.get("shared_allocation") or "tokens"
    if allocation not in ("tokens", "equal", "requests"):
        allocation = "tokens"
    gpu_rates = local_gpu_rates(rates)
    window = _window(records, days, today)
    agg = _aggregate(window, rates, gpu_rates, days, today)
    uc_by_id = {str(u.get("id")): u for u in usecases}

    # Use cases known in the registry come first (stable order), then unknown ids from the data.
    ids = [str(u.get("id")) for u in usecases] + sorted(k for k in agg if k not in uc_by_id)
    empty = {"tokens_in": 0, "tokens_out": 0, "cached_tokens": 0, "requests": 0, "retries": 0, "llm": 0.0,
             "llm_in": 0.0, "eval": 0.0, "gpu_hours": 0.0, "gpu": 0.0, "repeat_w": 0.0, "daily": {},
             "prompt_first": [], "prompt_last": [], "recent": 0.0, "prior": 0.0, "llm_recent": 0.0,
             "llm_in_recent": 0.0, "local_gpu_hours": 0.0}
    rows = {i: agg.get(i, empty) for i in ids}

    llm_c = {i: cents(rows[i]["llm"]) for i in ids}
    gpu_c = {i: cents(rows[i]["gpu"]) for i in ids}
    direct_c = sum(llm_c.values()) + sum(gpu_c.values())

    overhead_pct = float(rates.get("platform_overhead_pct") or 0)
    overhead_c = int(round(direct_c * overhead_pct / 100.0))
    vps_c = cents(float(rates.get("vps_eur_per_month") or 0) * days / 30.0)
    used_local_h = sum(float(rows[i].get("local_gpu_hours", 0.0)) for i in ids)
    amort_window = gpu_rates["amort_month"] * days / 30.0
    idle_c = cents(max(0.0, amort_window - used_local_h * gpu_rates["amort_hour"]))
    platform_c = overhead_c + vps_c + idle_c

    if mode == "chargeback":
        if allocation == "tokens":
            weights = [rows[i]["tokens_in"] + rows[i]["tokens_out"] for i in ids]
        elif allocation == "requests":
            weights = [rows[i]["requests"] for i in ids]
        else:
            weights = [1.0 if (rows[i]["requests"] or llm_c[i] or gpu_c[i]) else 0.0 for i in ids]
        if not any(w > 0 for w in weights):
            weights = [1.0] * len(ids)
        shares = dict(zip(ids, allocate_cents(platform_c, weights), strict=True)) if ids else {}
        unallocated_c = 0 if ids else platform_c
    else:
        shares = {i: 0 for i in ids}
        unallocated_c = platform_c

    by_usecase = []
    for i in ids:
        a = rows[i]
        uc = uc_by_id.get(i, {})
        total_c = llm_c[i] + gpu_c[i] + shares.get(i, 0)
        daily = []
        for k in range(days):
            d = today - dt.timedelta(days=days - 1 - k)
            daily.append({"date": d.isoformat(), "eur": r2(a["daily"].get(d, 0.0))})
        name = uc.get("name")
        if name is None and i == UNATTRIBUTED:
            name = msg(lang, "costs.unattributed_usecase")
        by_usecase.append({
            "usecase": i,
            "name": name,
            "cost_center": uc.get("cost_center"),
            "model_alias": a.get("model_alias") or (uc.get("model") or {}).get("alias"),
            "tokens_in": a["tokens_in"],
            "tokens_out": a["tokens_out"],
            "cached_ratio": r4(safe_div(a["cached_tokens"], a["tokens_in"])),
            "repeat_ratio": r4(safe_div(a["repeat_w"], a["requests"])),
            "requests": a["requests"],
            "retry_ratio": r4(safe_div(a["retries"], a["requests"])),
            "llm_eur": eur(llm_c[i]),
            "eval_eur": r2(a["eval"]),
            "gpu_hours": r2(a["gpu_hours"]),
            "gpu_eur": eur(gpu_c[i]),
            "platform_eur": eur(shares.get(i, 0)),
            "total_eur": eur(total_c),
            "eur_per_request": r4(safe_div(total_c / 100.0, a["requests"])),
            "trend_pct": round(safe_div(a["recent"] - a["prior"], a["prior"]) * 100.0, 1) if a["prior"] else 0.0,
            "daily": daily,
            "_cents": total_c,
        })

    # Cost centres: invoice lines per use case (billable only in chargeback mode).
    centers_cfg = [c for c in factory_cfg.get("cost_centers") or [] if isinstance(c, dict) and c.get("id")]
    centers = {str(c["id"]): c for c in centers_cfg}
    order = [str(c["id"]) for c in centers_cfg]
    for row in by_usecase:
        cc = row["cost_center"] or UNASSIGNED_COST_CENTER
        if cc not in centers:
            centers[cc] = {"id": cc, "name": msg(lang, "costs.unknown_cost_center"), "budget_month_eur": 0}
            order.append(cc)
    by_cost_center = []
    for cc in order:
        lines = [r for r in by_usecase if (r["cost_center"] or UNASSIGNED_COST_CENTER) == cc]
        spent_c = sum(r["_cents"] for r in lines)
        recent = sum(rows[r["usecase"]]["recent"] for r in lines)
        split = days // 2 if days < 14 else 7
        run_rate = recent / split if split else 0.0
        if mode == "chargeback" and direct_c:
            share_factor = 1.0 + platform_c / direct_c
            run_rate *= share_factor
        budget_month = float(centers[cc].get("budget_month_eur") or 0)
        by_cost_center.append({
            "cost_center": cc,
            "name": centers[cc].get("name"),
            "budget_eur": r2(budget_month * days / 30.0),
            "budget_month_eur": r2(budget_month),
            "spent_eur": eur(spent_c),
            "forecast_eur": r2(run_rate * 30.0),
            "consumed_pct": round(safe_div(spent_c / 100.0, budget_month * days / 30.0) * 100.0, 1),
            "billable": mode == "chargeback",
            "invoice_lines": [{"usecase": r["usecase"], "eur": r["total_eur"],
                               "llm_eur": r["llm_eur"], "gpu_eur": r["gpu_eur"], "platform_eur": r["platform_eur"]}
                              for r in lines if r["_cents"]],
            "_cents": spent_c,
        })

    tokens_in = sum(r["tokens_in"] for r in by_usecase)
    tokens_out = sum(r["tokens_out"] for r in by_usecase)
    total_c = direct_c + platform_c
    totals = {
        "tokens_in": tokens_in,
        "tokens_out": tokens_out,
        "llm_eur": eur(sum(llm_c.values())),
        "gpu_eur": eur(sum(gpu_c.values())),
        "platform_eur": eur(platform_c),
        "total_eur": eur(total_c),
        "requests": sum(r["requests"] for r in by_usecase),
        "unallocated_platform_eur": eur(unallocated_c),
        "platform_breakdown": {"overhead_eur": eur(overhead_c), "vps_eur": eur(vps_c), "idle_gpu_eur": eur(idle_c)},
    }

    alerts = waste_alerts(rows, by_usecase, by_cost_center, usecases, factory_cfg, models, days=days, today=today,
                          gpu_series=gpu_series, indexes=indexes, lang=lang)
    for row in by_usecase:
        row.pop("_cents", None)
    for row in by_cost_center:
        row.pop("_cents", None)
    return {
        "days": days,
        "mode": mode,
        "currency": (factory_cfg.get("factory") or {}).get("currency", "EUR"),
        "allocation": allocation,
        "method": {
            "gpu_eur_per_hour_local": r4(gpu_rates["hour"]),
            "platform": msg(lang, "costs.method.platform", pct=r2(overhead_pct)),
            "showback": msg(lang, "costs.method.showback") if mode == "showback" else None,
            "chargeback": msg(lang, "costs.method.chargeback", allocation=allocation) if mode == "chargeback" else None,
        },
        "totals": totals,
        "by_usecase": by_usecase,
        "by_cost_center": by_cost_center,
        "alerts": alerts,
        "savings_eur_month": r2(sum(a["saving_eur_month"] for a in alerts)),
    }


# --------------------------------------------------------------------------------------------
# Waste rules
# --------------------------------------------------------------------------------------------
def _sev(value: float, threshold: float, crit_factor: float) -> str:
    return "crit" if threshold and value >= threshold * crit_factor else "warn"


def _cheaper_ratio(current: dict[str, Any] | None, models: list[dict[str, Any]], sensitivity: str | None,
                   premium_tier: str, tin: int, tout: int) -> tuple[float, dict[str, Any] | None]:
    if not current:
        return DEFAULT_CHEAP_RATIO, None

    def cost(m: dict[str, Any]) -> float:
        return float(m.get("price_in_per_1k") or 0) * tin / 1000.0 + float(m.get("price_out_per_1k") or 0) * tout / 1000.0

    base = cost(current)
    best: tuple[float, dict[str, Any]] | None = None
    for m in models or []:
        if m is current or m.get("tier") == premium_tier:
            continue
        allowed = m.get("allowed_classes")
        if allowed and sensitivity and sensitivity not in allowed:
            continue
        c = cost(m)
        if c <= 0:
            continue
        if best is None or c < best[0]:
            best = (c, m)
    if best is None or base <= 0:
        return DEFAULT_CHEAP_RATIO, None
    return min(1.0, best[0] / base), best[1]


def waste_alerts(rows: dict[str, dict[str, Any]], by_usecase: list[dict[str, Any]],
                 by_cost_center: list[dict[str, Any]], usecases: list[dict[str, Any]], factory_cfg: dict[str, Any],
                 models: list[dict[str, Any]], *, days: int, today: dt.date,
                 gpu_series: list[dict[str, Any]] | None = None,
                 indexes: list[dict[str, Any]] | None = None, lang: str = DEFAULT_LANG) -> list[dict[str, Any]]:
    """The 8 waste rules; ``message`` and ``action`` come from the catalogue in ``lang``."""
    rules = factory_cfg.get("waste_rules") or {}
    rates = factory_cfg.get("rates") or {}
    monthly = 30.0 / days
    uc_by_id = {str(u.get("id")): u for u in usecases}
    out: list[dict[str, Any]] = []

    def add(rule: str, usecase: str | None, severity: str, message: str, saving: float, action: str,
            value: Any = None, threshold: Any = None, **extra: Any) -> None:
        out.append({"rule": rule, "usecase": usecase, "severity": severity, "message": message,
                    "saving_eur_month": r2(max(0.0, saving)), "action": action, "value": value,
                    "threshold": threshold, **extra})

    usage_rows = [r for r in by_usecase if r["requests"] > 0]

    # 1. oversized_model: premium model on a simple task type.
    cfg = rules.get("oversized_model")
    if isinstance(cfg, dict):
        simple = set(cfg.get("simple_task_types") or [])
        premium = cfg.get("premium_tier", "premium")
        for r in usage_rows:
            uc = uc_by_id.get(r["usecase"], {})
            model = model_for_alias(models, r.get("model_alias"))
            if uc.get("task_type") in simple and model and model.get("tier") == premium:
                ratio, alt = _cheaper_ratio(model, models, uc.get("sensitivity"), premium, r["tokens_in"], r["tokens_out"])
                saving = r["llm_eur"] * (1.0 - ratio) * monthly
                alt_label = (alt.get("alias") or alt.get("id")) if alt else msg(
                    lang, "costs.alert.oversized_model.smaller_model")
                add("oversized_model", r["usecase"], "crit" if saving >= 100 else "warn",
                    msg(lang, "costs.alert.oversized_model.message", model=model.get("id"),
                        task_type=uc.get("task_type")),
                    saving, msg(lang, "costs.alert.oversized_model.action", alternative=alt_label),
                    value=model.get("tier"), threshold=premium)

    # 2. low_cache_hit: repeated prompts but low cache hit ratio.
    cfg = rules.get("low_cache_hit")
    if isinstance(cfg, dict):
        min_rep = float(cfg.get("min_repeat_ratio", 0.4))
        max_hit = float(cfg.get("max_cache_hit", 0.2))
        for r in usage_rows:
            if r["repeat_ratio"] >= min_rep and r["cached_ratio"] <= max_hit:
                a = rows[r["usecase"]]
                saving = a["llm_in"] * max(0.0, r["repeat_ratio"] - r["cached_ratio"]) * CACHED_INPUT_DISCOUNT * monthly
                add("low_cache_hit", r["usecase"], "warn",
                    msg(lang, "costs.alert.low_cache_hit.message", repeat=round(r["repeat_ratio"] * 100),
                        hit=round(r["cached_ratio"] * 100)),
                    saving, msg(lang, "costs.alert.low_cache_hit.action"),
                    value=r["cached_ratio"], threshold=max_hit, repeat_ratio=r["repeat_ratio"])

    # 3. retry_storm.
    cfg = rules.get("retry_storm")
    if isinstance(cfg, dict):
        max_ratio = float(cfg.get("max_retry_ratio", 0.05))
        for r in usage_rows:
            if r["retry_ratio"] > max_ratio:
                a = rows[r["usecase"]]
                calls = a["requests"] + a["retries"]
                excess = max(0.0, a["retries"] - max_ratio * a["requests"])
                saving = a["llm"] * safe_div(excess, calls) * monthly
                add("retry_storm", r["usecase"], _sev(r["retry_ratio"], max_ratio, 2.0),
                    msg(lang, "costs.alert.retry_storm.message", ratio=round(r["retry_ratio"] * 100, 1),
                        max=round(max_ratio * 100, 1)),
                    saving, msg(lang, "costs.alert.retry_storm.action"),
                    value=r["retry_ratio"], threshold=max_ratio)

    # 4. prompt_bloat: average prompt size growth over the window.
    cfg = rules.get("prompt_bloat")
    if isinstance(cfg, dict):
        max_growth = float(cfg.get("max_growth_pct", 30))
        for r in usage_rows:
            a = rows[r["usecase"]]
            first, last = mean(a["prompt_first"]), mean(a["prompt_last"])
            if first > 0 and last > 0:
                growth = (last / first - 1.0) * 100.0
                if growth > max_growth:
                    split = 7 if days >= 14 else max(1, days // 2)
                    saving = a["llm_in_recent"] * (1.0 - first / last) * (30.0 / split)
                    add("prompt_bloat", r["usecase"], _sev(growth, max_growth, 2.0),
                        msg(lang, "costs.alert.prompt_bloat.message", growth=round(growth)),
                        saving, msg(lang, "costs.alert.prompt_bloat.action"),
                        value=round(growth, 1), threshold=max_growth)

    # 5. idle_gpu: longest run of consecutive hours under the utilisation threshold.
    cfg = rules.get("idle_gpu")
    if isinstance(cfg, dict) and gpu_series:
        max_util = float(cfg.get("max_util_pct", 10))
        min_hours = float(cfg.get("min_hours", 4))
        kwh = float(((rates.get("local_gpu") or {}).get("kwh_eur")) or 0)
        by_dev: dict[str, list[dict[str, Any]]] = defaultdict(list)
        for p in gpu_series:
            by_dev[str(p.get("device", "gpu0"))].append(p)
        for dev, points in sorted(by_dev.items()):
            points = sorted(points, key=lambda p: str(p.get("t")))
            best_run: list[dict[str, Any]] = []
            run: list[dict[str, Any]] = []
            for p in points:
                if float(p.get("util_pct") or 0) < max_util:
                    run.append(p)
                    if len(run) > len(best_run):
                        best_run = list(run)
                else:
                    run = []
            hours = float(len(best_run))  # series step is one hour
            if hours >= min_hours:
                kwh_used = sum(float(p.get("power_w") or 0) for p in best_run) / 1000.0
                saving = kwh_used * kwh * 30.0
                add("idle_gpu", None, _sev(hours, min_hours, 2.0),
                    msg(lang, "costs.alert.idle_gpu.message", device=dev, hours=int(hours), max=int(max_util)),
                    saving, msg(lang, "costs.alert.idle_gpu.action"),
                    value=hours, threshold=min_hours, device=dev, target="local")

    # 6. orphan_index: vector index not queried recently or not used by an active use case.
    cfg = rules.get("orphan_index")
    if isinstance(cfg, dict) and indexes:
        max_idle = int(cfg.get("max_idle_days", 14))
        active_ids = {str(u.get("id")) for u in usecases if u.get("stage") != "retired"}
        for idx in sorted(indexes, key=lambda x: str(x.get("index"))):
            users = [u for u in idx.get("usecases") or [] if u in active_ids]
            last = to_datetime(idx.get("last_query_at"))
            idle_days = (today - last.date()).days if last else None
            stale = idle_days is not None and idle_days > max_idle
            if stale or not users:
                why = (msg(lang, "costs.alert.orphan_index.why_stale", days=idle_days) if stale
                       else msg(lang, "costs.alert.orphan_index.why_unused"))
                add("orphan_index", (idx.get("usecases") or [None])[0], _sev(idle_days or 0, max_idle, 3.0) if stale else "warn",
                    msg(lang, "costs.alert.orphan_index.message", index=idx.get("index"), why=why),
                    float(idx.get("eur_month") or 0), msg(lang, "costs.alert.orphan_index.action"),
                    value=idle_days, threshold=max_idle, index=idx.get("index"))

    # 7. eval_overspend: evaluation (RAGAS, LLM-as-judge) share of the LLM spend.
    cfg = rules.get("eval_overspend")
    if isinstance(cfg, dict):
        max_share = float(cfg.get("max_share_pct", 25))
        for r in usage_rows:
            a = rows[r["usecase"]]
            share = safe_div(a["eval"], a["llm"]) * 100.0
            if a["llm"] > 0 and share > max_share:
                saving = (a["eval"] - a["llm"] * max_share / 100.0) * monthly
                add("eval_overspend", r["usecase"], _sev(share, max_share, 1.6),
                    msg(lang, "costs.alert.eval_overspend.message", share=round(share), max=round(max_share)),
                    saving, msg(lang, "costs.alert.eval_overspend.action"),
                    value=round(share, 1), threshold=max_share)

    # 8. budget_breach: cost centre consumption or forecast versus budget.
    cfg = rules.get("budget_breach")
    if isinstance(cfg, dict):
        warn_pct = float(cfg.get("warn_pct", 90))
        for cc in by_cost_center:
            budget = float(cc.get("budget_eur") or 0)
            budget_month = float(cc.get("budget_month_eur") or 0)
            if budget <= 0:
                continue
            spent_pct = safe_div(cc["spent_eur"], budget) * 100.0
            forecast_pct = safe_div(cc["forecast_eur"], budget_month) * 100.0 if budget_month else 0.0
            pct = max(spent_pct, forecast_pct)
            if pct >= warn_pct:
                over = max(0.0, cc["forecast_eur"] - budget_month)
                add("budget_breach", None, "crit" if pct >= 100 else "warn",
                    msg(lang, "costs.alert.budget_breach.message", name=cc.get("name") or cc["cost_center"],
                        spent=round(spent_pct), forecast=round(forecast_pct)),
                    over, msg(lang, "costs.alert.budget_breach.action"),
                    value=round(pct, 1), threshold=warn_pct, cost_center=cc["cost_center"])

    rank = {rule: n for n, rule in enumerate(RULE_ORDER)}
    out.sort(key=lambda a: (rank.get(a["rule"], 99), str(a.get("usecase") or ""), str(a.get("index") or a.get("device") or a.get("cost_center") or "")))
    for n, a in enumerate(out, start=1):
        a["id"] = f"w-{n:02d}"
    return [{"id": a.pop("id"), **a} for a in out]


def alert_counts(alerts: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    counts: dict[tuple[str, str], int] = defaultdict(int)
    for a in alerts:
        counts[(a["rule"], a["severity"])] += 1
    return dict(counts)
