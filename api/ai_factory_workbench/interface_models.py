"""Validated request bodies shared by HTTP and MCP adapters."""

from __future__ import annotations

from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

CASE_ID_PATTERN = r"^[0-9a-f]{32}$"
TEXT = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=1000)]
EvidenceKey = Annotated[str, StringConstraints(pattern=r"^[a-z][a-z0-9_]{0,47}$")]
EvidenceText = Annotated[str, StringConstraints(min_length=1, max_length=240)]
EventKey = Annotated[str, StringConstraints(pattern=r"^[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}$")]


class SurfaceInput(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, hide_input_in_errors=True)


class ReviewInput(SurfaceInput):
    """A proposed review and its evidence, keyed by the active work profile."""

    subject: str = Field(min_length=1, max_length=160, description="Stable dossier reference.")
    title: str = Field(min_length=1, max_length=160)
    reason: TEXT
    evidence: dict[EvidenceKey, EvidenceText] = Field(min_length=1, max_length=12)
    kind: Literal["strategic", "undocumented"] | None = None


class OpportunityData(SurfaceInput):
    title: str = Field(min_length=1, max_length=160)
    customer_name: str = Field(min_length=1, max_length=160)
    customer_ref: EventKey | None = None
    amount_minor: int = Field(ge=1, le=100_000_000_000)
    currency: str = Field(pattern=r"^[A-Z]{3}$")
    record_version: int = Field(ge=1, le=2**53 - 1)


class OpportunityInput(SurfaceInput):
    """A bounded CloudEvents opportunity from the reference CRM adapter."""

    specversion: Literal["1.0"] = "1.0"
    source: Literal["crm-sim"] = "crm-sim"
    type: Literal["crm.opportunity.won"] = "crm.opportunity.won"
    id: EventKey
    subject: EventKey
    data: OpportunityData
    time: str | None = Field(default=None, max_length=40)
    datacontenttype: Literal["application/json"] = "application/json"
