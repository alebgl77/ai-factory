"""LiteLLM gateway connector (MIT, outside enterprise/). Master key stays server side.

Only selected, non-secret fields are kept from the gateway answers: never ``api_key``,
``api_base`` or any ``litellm_params`` credential.
"""

from __future__ import annotations

import datetime as dt
from collections import defaultdict
from typing import Any

from ..util import UNATTRIBUTED, to_datetime
from .base import HttpSource, SourceError


class LiteLLMClient(HttpSource):
    name = "litellm"

    def __init__(self, base_url: str | None, master_key: str | None, **kwargs: Any) -> None:
        super().__init__(base_url, **kwargs)
        self._key = master_key

    def _headers(self) -> dict[str, str]:
        return {"Authorization": f"Bearer {self._key}"} if self._key else {}

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

        LiteLLM reports spend in its own currency setting (USD by default): the values are
        returned as ``spend`` and are not used as EUR unless the gateway is configured so.
        """
        payload = self._request_json("/spend/logs", params={"start_date": start.isoformat(),
                                                             "end_date": end.isoformat()},
                                     headers=self._headers())
        if not isinstance(payload, list):
            raise SourceError("litellm: unexpected answer")
        agg: dict[tuple[str, str], dict[str, Any]] = defaultdict(lambda: {"spend": 0.0, "requests": 0,
                                                                           "tokens_in": 0, "tokens_out": 0})
        for row in payload:
            if not isinstance(row, dict):
                continue
            when = to_datetime(row.get("startTime"))
            meta = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
            usecase = str(meta.get("usecase") or (meta.get("requester_metadata") or {}).get("usecase") or UNATTRIBUTED)
            if when is None:
                continue
            a = agg[(when.date().isoformat(), usecase)]
            a["spend"] += float(row.get("spend") or 0)
            a["requests"] += 1
            a["tokens_in"] += int(row.get("prompt_tokens") or 0)
            a["tokens_out"] += int(row.get("completion_tokens") or 0)
        return [{"date": d, "usecase": u, **v} for (d, u), v in sorted(agg.items())]
