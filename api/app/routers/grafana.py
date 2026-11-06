"""Grafana proxy (server-side token, GET allow-list, named queries only)."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Depends, HTTPException, Path

from ..connectors import NAMED_QUERIES, SourceError
from ..connectors.grafana import describe_queries
from ..i18n import msg
from ..service import FactoryService
from .deps import Reader, require_global, response_lang, service

router = APIRouter(prefix="/api/v1/grafana", tags=["grafana"])


@router.get("/dashboards", summary="Provisioned Grafana dashboards and named queries")
def dashboards(access: Reader, svc: FactoryService = Depends(service), lang: str = Depends(response_lang)) -> dict[str, Any]:
    require_global(svc, access)
    client = svc.connectors.grafana
    queries = describe_queries(lang)
    if not client.configured or svc.settings.demo_mode == "always":
        return {"available": False, "dashboards": [], "queries": queries}
    try:
        dashboards = client.dashboards()
        if svc.bu_policy is not None:
            dashboards = [{key: dashboard.get(key) for key in ("uid", "title")} for dashboard in dashboards]
        return {"available": True, "dashboards": dashboards, "queries": queries}
    except SourceError:
        return {"available": False, "dashboards": [], "queries": queries}


@router.get("/metric/{name}", summary="Named PromQL query run through Grafana's datasource proxy")
def metric(access: Reader, name: str = Path(pattern=r"^[a-z0-9_]{1,64}$"), svc: FactoryService = Depends(service),
           lang: str = Depends(response_lang)) -> dict[str, Any]:
    require_global(svc, access)
    if name not in NAMED_QUERIES:
        raise HTTPException(status_code=404, detail=msg(lang, "error.unknown_query"))
    client = svc.connectors.grafana
    if not client.configured or svc.settings.demo_mode == "always":
        raise HTTPException(status_code=503, detail=msg(lang, "error.grafana_unavailable"))
    try:
        return client.metric(name, lang)
    except SourceError:
        raise HTTPException(status_code=502, detail=msg(lang, "error.grafana_no_answer")) from None
