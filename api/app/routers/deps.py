"""Shared router helpers: service access, response language (CONTRACT section 9), errors, and
the permission each route requires (``app/security.py``)."""

from __future__ import annotations

from fastapi import Depends, HTTPException, Query, Request

from ..i18n import msg, normalize_lang
from ..security import READ, require
from ..service import FactoryService

ID_PATTERN = r"^[A-Za-z0-9_-]{1,64}$"

# Route-level dependency of every read endpoint: role ``viewer`` (or above) when auth is on.
READ_ONLY = [Depends(require(READ))]


def service(request: Request) -> FactoryService:
    return request.app.state.service


def response_lang(request: Request,
                  lang: str | None = Query(None, description="Response language: en or fr (default: "
                                                               "FACTORY_DEFAULT_LANG, itself en). Unknown "
                                                               "values fall back to the default.")) -> str:
    """Language of the human-readable strings of a response; never fails (unknown -> default)."""
    return normalize_lang(lang, request.app.state.settings.default_lang)


def not_found(lang: str) -> HTTPException:
    return HTTPException(status_code=404, detail=msg(lang, "error.usecase_not_found"))


def state_unavailable(lang: str, audit: bool = False) -> HTTPException:
    return HTTPException(status_code=503, detail=msg(lang, "error.audit_unavailable" if audit
                                                     else "error.state_unavailable"))
