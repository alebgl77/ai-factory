"""Shared HTTP plumbing for connectors: short timeouts, no proxy env, graceful errors."""

from __future__ import annotations

from typing import Any

import httpx


class SourceError(RuntimeError):
    """A live source failed (unreachable, timeout, bad payload). Never fatal for the snapshot."""


class HttpSource:
    name = "source"

    def __init__(self, base_url: str | None, *, timeout: float = 2.0,
                 transport: httpx.BaseTransport | None = None) -> None:
        self.base_url = (base_url or "").rstrip("/")
        self.timeout = timeout
        self.transport = transport

    @property
    def configured(self) -> bool:
        return bool(self.base_url)

    def _client(self, **kwargs: Any) -> httpx.Client:
        return httpx.Client(
            base_url=self.base_url,
            timeout=httpx.Timeout(self.timeout, connect=min(1.0, self.timeout)),
            transport=self.transport,
            trust_env=False,          # internal services: never route through a proxy from the env
            follow_redirects=False,
            **kwargs,
        )

    def _request_json(self, path: str, *, params: dict[str, Any] | None = None,
                      headers: dict[str, str] | None = None, auth: Any = None) -> Any:
        if not self.configured:
            raise SourceError(f"{self.name}: not configured")
        try:
            with self._client() as client:
                resp = client.get(path, params=params, headers=headers, auth=auth)
        except httpx.HTTPError as exc:
            raise SourceError(f"{self.name}: {exc.__class__.__name__}") from None
        if resp.status_code >= 400:
            raise SourceError(f"{self.name}: HTTP {resp.status_code}")
        try:
            return resp.json()
        except ValueError:
            raise SourceError(f"{self.name}: non-JSON answer") from None

    def _request_ok(self, path: str, *, headers: dict[str, str] | None = None, auth: Any = None) -> bool:
        if not self.configured:
            return False
        try:
            with self._client() as client:
                resp = client.get(path, headers=headers, auth=auth)
        except httpx.HTTPError:
            return False
        return resp.status_code < 400
