"""Read endpoints derived from the snapshot (CONTRACT section 3).

Every read endpoint accepts ``?lang=en|fr`` (CONTRACT section 9) and returns fully resolved strings.
"""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, Query

from ..i18n import LANGUAGES
from ..service import FactoryService
from ..settings import API_VERSION
from .deps import response_lang, service

router = APIRouter(prefix="/api/v1", tags=["factory"])


@router.get("/health", summary="API and data source health")
def health(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    sources = svc.sources()
    if svc.settings.demo_mode == "always":
        mode = "demo"
    elif svc.settings.demo_mode == "never":
        mode = "live"
    else:
        mode = "mixed" if any(sources.values()) else "demo"
    cfg = svc.config.get()
    return {"status": "ok", "mode": mode, "demo_mode": svc.settings.demo_mode, "version": API_VERSION,
            "sources": sources, "config_warnings": len(cfg.get("warnings") or []),
            "usecases": len(cfg.get("usecases") or []), "lang": lang, "languages": list(LANGUAGES),
            "default_lang": svc.settings.default_lang}


@router.get("/snapshot", summary="Full snapshot for the 3D dashboard")
def snapshot(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)


@router.get("/costs", summary="FinOps: costs per use case and cost center, waste alerts")
def costs(days: int = Query(30, ge=1, le=90),
          mode: Literal["showback", "chargeback"] | None = Query(None),
          svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(days=days, cost_mode=mode, lang=lang)["costs"]


@router.get("/pipeline", summary="LLMOps/RAG pipeline, gates and rollbacks")
def pipeline(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["pipeline"]


@router.get("/dataloop", summary="Data loop: drift signals and actions (no retraining)")
def dataloop(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["dataloop"]


@router.get("/agentops", summary="Agent Ops room: TTFT, tokens per task, vector latency")
def agentops(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["agentops"]


@router.get("/catalog", summary="Self-service catalog: models, agents, tools, repositories")
def catalog(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["catalog"]


@router.get("/skills", summary="Skills Lab (AI Act Art. 4 AI literacy) and R&D Lab")
def skills(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    snap = svc.snapshot(lang=lang)
    return {**snap["skills"], "rnd": snap["rnd"]}


@router.get("/gpu", summary="Local GPU telemetry (DCGM or simulated)")
def gpu(svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    return svc.snapshot(lang=lang)["gpu"]
