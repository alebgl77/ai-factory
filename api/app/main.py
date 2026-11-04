"""FastAPI application of the Virtual AI Factory aggregation API (MIT).

Run: ``uvicorn app.main:app --host 0.0.0.0 --port 8000``
"""

from __future__ import annotations

import datetime as dt
import json
import logging
from typing import Any, Callable
from urllib.parse import parse_qs

import httpx
from fastapi import FastAPI, Request
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, Response
from starlette.types import ASGIApp, Message, Receive, Scope, Send

from . import metrics as metrics_mod
from .i18n import DEFAULT_LANG, msg, normalize_lang
from .routers import core, grafana, placement, registry, spaces
from .service import FactoryService
from .settings import API_VERSION, Settings

log = logging.getLogger("factory.api")


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


def create_app(settings: Settings | None = None, transport: httpx.BaseTransport | None = None,
               clock: Callable[[], dt.datetime] | None = None) -> FastAPI:
    settings = settings or Settings.from_env()
    app = FastAPI(
        title="Virtual AI Factory — aggregation API",
        description=("Aggregation API (MIT): risk registry, FinOps, LLMOps/RAG, data loop, hybrid placement, "
                     "Agent Ops, catalog, Skills Lab. Fictional demo data. Every read endpoint accepts "
                     "`?lang=en|fr` (default: `FACTORY_DEFAULT_LANG`, itself `en`)."),
        version=API_VERSION,
        docs_url="/api/v1/docs",
        redoc_url=None,
        openapi_url="/api/v1/openapi.json",
        license_info={"name": "MIT", "identifier": "MIT"},
    )
    app.state.service = FactoryService(settings, transport=transport, clock=clock)
    app.state.settings = settings

    if settings.cors_origins:  # closed by default (same origin through the edge proxy)
        app.add_middleware(CORSMiddleware, allow_origins=list(settings.cors_origins), allow_credentials=False,
                           allow_methods=["GET", "POST", "PUT"], allow_headers=["Content-Type"])

    @app.middleware("http")
    async def security_headers(request: Request, call_next: Callable[[Request], Any]) -> Response:
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Referrer-Policy", "no-referrer")
        response.headers.setdefault("X-Frame-Options", "DENY")
        if request.url.path.startswith("/api/v1/") and not request.url.path.startswith("/api/v1/docs"):
            response.headers.setdefault("Cache-Control", "no-store")
        return response

    app.add_middleware(BodySizeLimit, max_bytes=settings.max_body_bytes, default_lang=settings.default_lang)

    @app.exception_handler(Exception)
    async def unhandled(request: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %s", exc.__class__.__name__)
        lang = normalize_lang(request.query_params.get("lang"), settings.default_lang)
        return JSONResponse(status_code=500, content={"detail": msg(lang, "error.internal")})

    for r in (core.router, registry.router, placement.router, grafana.router, spaces.router):
        app.include_router(r)

    @app.get("/metrics", include_in_schema=False)
    def prometheus_metrics() -> Response:
        snap = app.state.service.snapshot()
        return Response(content=metrics_mod.render(snap), media_type="text/plain; version=0.0.4; charset=utf-8")

    return app


app = create_app()
