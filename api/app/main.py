"""FastAPI application of the Virtual AI Factory aggregation API (MIT).

Run: ``uvicorn app.main:app --host 0.0.0.0 --port 8000 --no-access-log``
"""

from __future__ import annotations

import asyncio
import datetime as dt
import html
import json
import logging
import secrets
from collections.abc import Awaitable
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Callable
from urllib.parse import parse_qs

import httpx
from fastapi import Depends, FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import HTMLResponse, JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import metrics as metrics_mod
from . import seclog
from .activity import ActivityJournal, router as activity_router
from .i18n import DEFAULT_LANG, msg, normalize_lang
from .portfolio import router as portfolio_router
from .portfolio_runner import POLL_SECONDS, PortfolioEngine
from .portfolio_store import PortfolioStore
from .routers import audit, core, grafana, placement, registry, spaces, workbench
from .routers.shadow_ai import router as shadow_ai_router
from .security import READ, Access, SecurityDenied, SecurityMiddleware, require
from .service import FactoryService
from .settings import API_VERSION, Settings
from .shadow_ai import Observatory
from .workbench.service import Workbench, local_advisor


class BodySizeLimit:
    """Reject request bodies larger than ``max_bytes`` (declared or streamed) with HTTP 413.

    The body is buffered (it is small by construction) before the application sees it, so a
    chunked upload without Content-Length is rejected the same way as a declared one.
    """

    def __init__(self, app: ASGIApp, max_bytes: int, default_lang: str = DEFAULT_LANG) -> None:
        self.app = app
        self.max_bytes = max_bytes
        self.default_lang = default_lang

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return
        for name, value in scope.get("headers") or []:
            if name == b"content-length":
                try:
                    declared = int(value)
                except ValueError:
                    declared = self.max_bytes + 1
                if declared > self.max_bytes:
                    await self._reject(send, self._lang(scope))
                    return
        chunks: list[bytes] = []
        size = 0
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return  # client went away: nothing to answer
            if message["type"] != "http.request":
                continue
            body = message.get("body", b"")
            size += len(body)
            if size > self.max_bytes:
                await self._reject(send, self._lang(scope))
                return
            chunks.append(body)
            if not message.get("more_body", False):
                break
        buffered = b"".join(chunks)
        replayed = False

        async def replay() -> Message:
            nonlocal replayed
            if not replayed:
                replayed = True
                return {"type": "http.request", "body": buffered, "more_body": False}
            return await receive()

        await self.app(scope, replay, send)

    def _lang(self, scope: Scope) -> str:
        query = parse_qs((scope.get("query_string") or b"").decode("latin-1"))
        return normalize_lang((query.get("lang") or [None])[0], self.default_lang)

    @staticmethod
    async def _reject(send: Send, lang: str) -> None:
        body = json.dumps({"detail": msg(lang, "error.body_too_large")}, ensure_ascii=False).encode("utf-8")
        await send({"type": "http.response.start", "status": 413,
                    "headers": [(b"content-type", b"application/json"), (b"content-length", str(len(body)).encode())]})
        await send({"type": "http.response.body", "body": body})


# OpenAPI page: Swagger UI (Apache-2.0) loaded by the browser from a pinned version with Subresource
# Integrity (hashes of the files of the npm tarball swagger-ui-dist@5.34.0, whose sha512 matches the
# registry), and a per-request CSP nonce for the only inline script. Nothing else can load.
SWAGGER_UI_VERSION = "5.34.0"
SWAGGER_UI_BASE = f"https://cdn.jsdelivr.net/npm/swagger-ui-dist@{SWAGGER_UI_VERSION}"
SWAGGER_UI_JS_SRI = "sha384-9Td7MKiIr2S83E1kZyyVi8KdJw/S8CpAvu0llLyMeg0xq/hDGiZJfVn0fV4llCmK"
SWAGGER_UI_CSS_SRI = "sha384-4c3vsE5f+trhFyHrFrLM1fFGUn531NIaC7dcqEWIJdoRQJ5CkJbSuSCwXLpaS1KF"


def docs_page(title: str, openapi_url: str, nonce: str) -> tuple[str, str]:
    """HTML of the OpenAPI page and its Content-Security-Policy."""
    page = f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width, initial-scale=1">
<title>{html.escape(title)}</title>
<link rel="stylesheet" href="{SWAGGER_UI_BASE}/swagger-ui.css" integrity="{SWAGGER_UI_CSS_SRI}" crossorigin="anonymous">
</head><body><div id="swagger-ui"></div>
<script src="{SWAGGER_UI_BASE}/swagger-ui-bundle.js" integrity="{SWAGGER_UI_JS_SRI}"
  crossorigin="anonymous" nonce="{nonce}"></script>
<script nonce="{nonce}">
window.ui = SwaggerUIBundle({{url: {json.dumps(openapi_url)}, dom_id: "#swagger-ui", layout: "BaseLayout",
  deepLinking: true, showExtensions: true, showCommonExtensions: true, validatorUrl: null,
  presets: [SwaggerUIBundle.presets.apis, SwaggerUIBundle.SwaggerUIStandalonePreset]}});
</script></body></html>"""
    csp = (f"default-src 'none'; script-src 'nonce-{nonce}' https://cdn.jsdelivr.net; "
           "style-src https://cdn.jsdelivr.net 'unsafe-inline'; img-src 'self' data:; font-src 'self' data:; "
           "connect-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'none'")
    return page, csp


async def _portfolio_loop(tick: Callable[[], Awaitable[object]], event: str) -> None:
    while True:
        try:
            await tick()
        except Exception:
            # Provider errors, database messages and task bodies can include private content.
            seclog.emit(seclog.API_LOGGER, event, logging.ERROR)
        await asyncio.sleep(POLL_SECONDS)


@asynccontextmanager
async def _portfolio_lifespan(app: FastAPI):
    jobs = []
    engine = getattr(app.state, "portfolio_engine", None)
    if engine is not None:
        jobs.append(asyncio.create_task(_portfolio_loop(engine.run_once, "portfolio.runner_tick_failed")))
    if app.state.settings.workbench_mode == "portfolio":
        from .portfolio_workbench import reconcile_portfolio_reviews

        async def reconcile():
            return await asyncio.to_thread(reconcile_portfolio_reviews, app)

        jobs.append(asyncio.create_task(_portfolio_loop(reconcile, "portfolio.workbench_tick_failed")))
    try:
        yield
    finally:
        for job in jobs:
            job.cancel()
        await asyncio.gather(*jobs, return_exceptions=True)


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
               clock: Callable[[], dt.datetime] | None = None, *,
               portfolio_runner_transport: httpx.AsyncBaseTransport | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    seclog.protect_uvicorn_errors()
    app = FastAPI(
        title="Virtual AI Factory — aggregation API",
        description=("Aggregation API (MIT): risk registry, FinOps, LLMOps/RAG, data loop, hybrid placement, "
                     "Agent Ops, catalog, Skills Lab. Fictional demo data. Every read endpoint accepts "
                     "`?lang=en|fr` (default: `FACTORY_DEFAULT_LANG`, itself `en`)."),
        version=API_VERSION,
        docs_url=None,       # served below, behind the read permission, with SRI and a CSP nonce
        redoc_url=None,
        openapi_url=None,    # served below, behind the read permission
        license_info={"name": "MIT", "identifier": "MIT"},
        lifespan=_portfolio_lifespan,
    )
    service = FactoryService(settings, transport=transport, clock=clock)
    app.state.service = service
    app.state.settings = settings

    if settings.cors_origins:  # closed by default (same origin through the edge proxy)
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type", "X-Request-ID"],
                           expose_headers=["X-Request-ID", "Retry-After"])
    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes, default_lang=settings.default_lang)
    # Outermost: request id, client address, authentication of /api/* before any body is read,
    # security headers on every answer (413 and 401 included).
    app.add_middleware(SecurityMiddleware, guard=service.guard, default_lang=settings.default_lang)

    @app.exception_handler(SecurityDenied)
    async def denied(request: Request, exc: SecurityDenied) -> JSONResponse:
        return exc.response(normalize_lang(request.query_params.get("lang"), settings.default_lang))

    @app.exception_handler(Exception)
    async def unhandled(request: Request, _exc: Exception) -> JSONResponse:
        seclog.emit(seclog.API_LOGGER, "request.failed", logging.ERROR, status=500,
                    route=seclog.route_label(request.scope),
                    request_ref=seclog.reference("request", getattr(request.state, "request_id", None)))
        lang = normalize_lang(request.query_params.get("lang"), settings.default_lang)
        return JSONResponse(status_code=500, content={"detail": msg(lang, "error.internal")})

    for r in (core.router, registry.router, placement.router, grafana.router, spaces.router, audit.router):
        app.include_router(r)
    if settings.auth_mode == "proxy":
        app.state.activity = ActivityJournal(settings.state_dir, clock=clock)
        app.include_router(activity_router)
    if service.bu_policy is not None:
        app.include_router(spaces.recovery_router)
        if settings.auth_mode == "proxy":
            app.state.portfolio = PortfolioStore(settings.state_dir, clock=clock)
            # The BU policy loads at startup. A new API instance must never
            # accept an earlier pending grant, even after a policy rollback.
            app.state.portfolio_grant_generation = f"{service.bu_policy.fingerprint}:{secrets.token_hex(32)}"
            app.include_router(portfolio_router)
    if settings.portfolio_runner_file:
        if not hasattr(app.state, "portfolio"):
            raise ValueError("Portfolio runner requires the authenticated portfolio")
        app.state.portfolio_engine = PortfolioEngine(
            service, app.state.portfolio, app.state.portfolio_grant_generation,
            transport=portfolio_runner_transport,
        )
    if settings.workbench_mode != "off":
        advisor = None
        if settings.workbench_chat_mode == "local":
            assert settings.litellm_url and settings.litellm_master_key  # validated by Settings
            advisor = local_advisor(settings.litellm_url, settings.litellm_master_key, transport=transport)
        app.state.workbench = Workbench(
            Path(settings.state_dir) / f"workbench-{settings.workbench_mode}.sqlite3",
            settings.workbench_mode, clock=clock, advisor=advisor,
            cyber_audit=settings.workbench_cyber_audit == "on",
            workspace="factory-portfolio" if settings.workbench_mode == "portfolio" else "demo",
        )
        app.include_router(workbench.router)

    @app.get("/api/v1/openapi.json", include_in_schema=False)
    def openapi_schema(_: Access = Depends(require(READ))) -> JSONResponse:
        return JSONResponse(app.openapi())

    @app.get("/api/v1/docs", include_in_schema=False)
    def openapi_docs(_: Access = Depends(require(READ))) -> HTMLResponse:
        page, csp = docs_page(f"{app.title} — OpenAPI", "./openapi.json", secrets.token_urlsafe(18))
        return HTMLResponse(page, headers={"Content-Security-Policy": csp})

    if settings.shadow_ai_enabled:
        if not service.guard.config.trusted_networks:
            raise ValueError("Shadow AI requires a trusted identity proxy")
        app.state.shadow_ai = Observatory(settings.state_dir, settings.shadow_ai_privacy_threshold, clock=clock)
        app.include_router(shadow_ai_router)

    if service.bu_policy is not None or settings.shadow_ai_enabled:
        from .operating_evidence import configure_operating_evidence

        configure_operating_evidence(app)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        # The unauthenticated internal scrape carries platform security counters only in BU mode.
        snap = (app.state.service.snapshot() if service.bu_policy is None
                else {"meta": {"mode": "scoped", "version": API_VERSION}})
        extra = metrics_mod.security_families(service.state.audit_status(), service.state.audit_counts(),
                                              service.guard.denials())
        return Response(content=metrics_mod.render(snap, extra),
                        media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()
