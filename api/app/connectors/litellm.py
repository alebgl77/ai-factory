"""LiteLLM gateway connector using a dedicated read-only virtual key.

Only selected, non-secret fields are kept from the gateway answers: never ``api_key``,
``api_base`` or any ``litellm_params`` credential.
"""

from __future__ import annotations

import datetime as dt
import math
from collections import defaultdict
from typing import Any

import httpx

from ..util import UNATTRIBUTED, to_datetime
from .base import HttpSource, SourceError


class LiteLLMClient(HttpSource):
    name = "litellm"

    def __init__(self, base_url: str | None, telemetry_key: str | None, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._key = telemetry_key

    def _headers(self) -> dict[str, str]:
        if not self._key:
            raise SourceError("litellm: read-only telemetry credential is not configured")
        return {"Authorization": f"Bearer {self._key}"}

    def health(self) -> bool:
        return self._request_ok("/health/liveliness")

    def models(self) -> list[dict[str, Any]]:
        payload = self._request_json("/model/info", headers=self._headers())
        data = payload.get("data") if isinstance(payload, dict) else None
        if not isinstance(data, list):
            raise SourceError("litellm: unexpected answer")
        out = []
        for item in data:
            if not isinstance(item, dict):
                continue
            params = item.get("litellm_params") or {}
            info = item.get("model_info") or {}
            backend = str(params.get("model") or "")
            out.append({
                "alias": item.get("model_name"),
                "provider": backend.split("/", 1)[0] if "/" in backend else None,
                "mode": info.get("mode"),
                "deployment": info.get("factory_deployment"),
                "max_tokens": info.get("max_tokens"),
            })
        return out

    def spend_daily(self, start: dt.date, end: dt.date) -> list[dict[str, Any]]:
        """Aggregate the DB-backed spend logs per day and use case (metadata.usecase).

        LiteLLM reports ``spend`` in its own price unit (USD by default). The live collector
        converts these amounts to EUR with the same configured rate as the gateway callback.
        """
        # The pinned endpoint defaults to daily summaries and caps raw results at 10,000.
        # An incomplete window must not be reported as complete attributed spend.
        try:
            end_exclusive = end + dt.timedelta(days=1)
            with self._client() as client:
                response = client.get("/spend/logs", headers=self._headers(), params={
                    "start_date": start.isoformat(), "end_date": end_exclusive.isoformat(), "summarize": "false",
                })
            if response.status_code >= 400:
                raise SourceError(f"litellm: HTTP {response.status_code}")
            if response.headers.get("x-litellm-spend-logs-truncated", "false").lower() != "false":
                raise SourceError("litellm: spend window is truncated")
            payload = response.json()
        except httpx.HTTPError as exc:
            raise SourceError(f"litellm: {exc.__class__.__name__}") from None
        except (ValueError, OverflowError):
            raise SourceError("litellm: invalid spend response or date window") from None
        if not isinstance(payload, list) or len(payload) >= 10000:
            raise SourceError("litellm: unexpected answer")
        agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"spend": 0.0, "requests": 0,
                                                                           "tokens_in": 0, "tokens_out": 0})
        for row in payload:
            if not isinstance(row, dict):
                raise SourceError("litellm: invalid spend log")
            when = to_datetime(row.get("startTime"))
            meta = row.get("metadata") or {}
            if (when is None or not isinstance(meta, dict)
                    or any(type(row.get(name)) is not int or row[name] < 0
                           for name in ("prompt_tokens", "completion_tokens"))):
                raise SourceError("litellm: spend response requires individual request logs")
            requester = meta.get("requester_metadata") or {}
            if not isinstance(requester, dict):
                raise SourceError("litellm: invalid spend attribution")
            try:
                spend = float(row["spend"])
            except (KeyError, TypeError, ValueError):
                raise SourceError("litellm: invalid spend amount") from None
            if not math.isfinite(spend) or spend < 0:
                raise SourceError("litellm: invalid spend amount")
            day = when.astimezone(dt.timezone.utc).date()
            if day < start or day > end:
                continue
            usecase = str(meta.get("usecase") or requester.get("usecase") or UNATTRIBUTED)
            a = agg[(day.isoformat(), usecase)]
            a["spend"] += spend
            a["requests"] += 1
            a["tokens_in"] += row["prompt_tokens"]
            a["tokens_out"] += row["completion_tokens"]
        return [{"date": d, "usecase": u, **v} for (d, u), v in sorted(agg.items())]
