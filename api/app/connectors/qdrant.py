"""Qdrant connector (Apache-2.0 vector database): health and collections."""

from __future__ import annotations

from typing import Any

from .base import HttpSource, SourceError


class QdrantClient(HttpSource):
    name = "qdrant"

    def health(self) -> bool:
        return self._request_ok("/readyz") or self._request_ok("/healthz")

    def collections(self, details: bool = True) -> list[dict[str, Any]]:
        payload = self._request_json("/collections")
        items = ((payload or {}).get("result") or {}).get("collections") if isinstance(payload, dict) else None
        if not isinstance(items, list):
            raise SourceError("qdrant: unexpected answer")
        out = []
        for c in items[:50]:
            name = str((c or {}).get("name") or "")
            if not name:
                continue
            entry: dict[str, Any] = {"index": name}
            if details:
                try:
                    info = (self._request_json(f"/collections/{name}") or {}).get("result") or {}
                    entry["vectors"] = info.get("points_count") or info.get("vectors_count")
                    entry["status"] = info.get("status")
                except SourceError:
                    pass
            out.append(entry)
        return out
