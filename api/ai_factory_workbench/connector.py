"""Contract for a trusted, independently readable ERP draft adapter."""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Protocol

from .adapters import AdapterCapabilities
from ._hashing import digest
from .store import WorkbenchError

_TARGET = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")


@dataclass(frozen=True)
class DraftReadback:
    id: str
    external_ref: str
    fields: dict[str, Any]
    state: str = "draft"


class DraftConnector(Protocol):
    """Bind a logical ERP target to a unique key enforced inside that ERP.

    ensure_draft must be atomic and idempotent for the key, including
    simultaneous calls. A collision with other fields must never overwrite
    the existing record. read_draft must fetch the committed record from the
    ERP and return None only when it can establish absence. Both methods
    signal uncertain outcomes with TimeoutError.
    """

    @property
    def target(self) -> str: ...
    @property
    def capabilities(self) -> AdapterCapabilities: ...
    def ensure_draft(self, external_ref: str, fields: dict[str, Any]) -> None: ...
    def read_draft(self, external_ref: str) -> DraftReadback | None: ...


def validate_connector(connector: DraftConnector) -> str:
    target = getattr(connector, "target", None)
    capabilities = getattr(connector, "capabilities", None)
    if (
        not isinstance(target, str) or not _TARGET.fullmatch(target) or target == "sandbox-erp"
        or not isinstance(capabilities, AdapterCapabilities)
        or capabilities.draft_writes is not True or capabilities.idempotency_keys is not True
        or not callable(getattr(connector, "ensure_draft", None))
        or not callable(getattr(connector, "read_draft", None))
    ):
        raise ValueError("connector requires a unique target, atomic idempotent draft writes, and readback")
    return target


def write_and_verify(
    connector: DraftConnector, target: str, external_ref: str, fields: dict[str, Any],
) -> dict[str, str]:
    if connector.target != target:
        raise WorkbenchError("connector_target_mismatch", 409)
    expected = digest(fields)
    result = connector.read_draft(external_ref)
    if result is None:
        connector.ensure_draft(external_ref, fields.copy())
        result = connector.read_draft(external_ref)
    if result is None:
        raise TimeoutError
    try:
        valid = (
            isinstance(result, DraftReadback)
            and isinstance(result.id, str) and 0 < len(result.id) <= 160 and result.id.isprintable()
            and result.id == result.id.strip()
            and result.external_ref == external_ref and result.state == "draft"
            and isinstance(result.fields, dict) and digest(result.fields) == expected
        )
    except (TypeError, ValueError, RecursionError):
        valid = False
    if not valid:
        raise WorkbenchError("draft_readback_conflict", 409)
    return {"id": result.id, "external_ref": external_ref, "system": target, "state": result.state}
