"""Read endpoints derived from the snapshot (CONTRACT section 3).

Every read endpoint accepts ``?lang=en|fr`` (CONTRACT section 9) and returns fully resolved strings.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from ..i18n import LANGUAGES
from ..service import FactoryService
from ..settings import API_VERSION
from .deps import Reader, require_global, response_lang, service

router = APIRouter(prefix="/api/v1", tags=["factory"])


@router.get("/health", summary="API and data source health, security posture (public: no identity, no secret)")
def health(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    scoped = svc.bu_policy is not None
    sources = svc.sources() if not scoped else {}
    if svc.settings.demo_mode == "always":
        mode = "demo"
    elif svc.settings.demo_mode == "never":
        mode = "live"
    else:
        mode = "mixed" if any(sources.values()) else "demo"
    cfg = svc.config.get()
    if scoped:
        return {"status": "ok", "mode": mode, "version": API_VERSION, "lang": lang,
                "business_scope": True}
    return {"status": "ok", "mode": mode, "demo_mode": svc.settings.demo_mode, "version": API_VERSION,
            "sources": sources, "config_warnings": len(cfg.get("warnings") or []),
            "usecases": len(cfg.get("usecases") or []), "lang": lang, "languages": list(LANGUAGES),
            "default_lang": svc.settings.default_lang, "security": svc.security_posture()}


@router.get("/snapshot", summary="Snapshot for the 3D dashboard")
def snapshot(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))


@router.get("/costs", summary="FinOps: costs per use case and cost center, waste alerts")
def costs(access: Reader, days: int = Query(30, ge=1, le=90),
          mode: Literal["showback", "chargeback"] | None = Query(None),
          svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(days=days, cost_mode=mode, lang=lang, scope=svc.business_scope(access))["costs"]


@router.get("/pipeline", summary="LLMOps/RAG pipeline, gates and rollbacks")
def pipeline(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["pipeline"]


@router.get("/dataloop", summary="Data loop: drift signals and actions (no retraining)")
def dataloop(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["dataloop"]


@router.get("/agentops", summary="Agent Ops room: TTFT, tokens per task, vector latency")
def agentops(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    require_global(svc, access)
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["agentops"]


@router.get("/catalog", summary="Self-service catalog: models, agents, tools, repositories")
def catalog(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["catalog"]


@router.get("/skills", summary="Skills Lab (AI Act Art. 4 AI literacy) and R&D Lab")
def skills(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    snap = svc.snapshot(lang=lang, scope=svc.business_scope(access))
    return {**snap["skills"], "rnd": snap["rnd"]}


@router.get("/gpu", summary="Local GPU telemetry (DCGM or simulated)")
def gpu(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    require_global(svc, access)
    return svc.snapshot(lang=lang, scope=svc.business_scope(access))["gpu"]
