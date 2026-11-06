"""Versioned data profiles for how a team prepares and reviews a decision."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from .identity import WorkActor
from .store import WorkbenchError

if TYPE_CHECKING:
    from .service import Workbench

APPROVAL_CHANNELS = frozenset({"companion", "slack", "teams", "service"})
REVIEW_KINDS = frozenset({"strategic", "undocumented"})
DRAFT_ACTION = "erp.create_draft_order"
_PROFILE_ID = re.compile(r"[a-z][a-z0-9-]{0,47}")
_VERSION = re.compile(r"[A-Za-z0-9][A-Za-z0-9._-]{0,31}")
_FIELD = re.compile(r"[a-z][a-z0-9_]{0,47}")
_PROFILE_FIELDS = frozenset({
    "id", "version", "label", "description", "default_kind", "required_evidence",
    "review_steps", "approval_channels", "allow_service_drafts",
})


def _valid_text(value: object, limit: int) -> bool:
    return (isinstance(value, str) and bool(value.strip()) and len(value) <= limit
            and not any(0xD800 <= ord(char) <= 0xDFFF for char in value))


def _valid_token(value: object, pattern: re.Pattern[str]) -> bool:
    return isinstance(value, str) and pattern.fullmatch(value) is not None


def _valid_list(values: object, limit: int) -> bool:
    return (isinstance(values, (list, tuple)) and 0 < len(values) <= limit
            and all(isinstance(value, str) for value in values)
            and len(set(values)) == len(values))


def _evidence_fields(evidence: Mapping[str, str]) -> dict[str, str]:
    if (not isinstance(evidence, Mapping) or len(evidence) > 12
            or any(not _valid_token(key, _FIELD) or not _valid_text(value, 240)
                   for key, value in evidence.items())):
        raise WorkbenchError("invalid_profile_evidence", 422)
    return dict(evidence)


@dataclass(frozen=True, slots=True)
class WorkProfile:
    """An operator-selected review method. Roles remain enforced by the engine."""

    id: str
    version: str
    label: str
    description: str
    default_kind: str
    required_evidence: tuple[str, ...]
    review_steps: tuple[str, ...]
    approval_channels: tuple[str, ...]
    allow_service_drafts: bool = False

    def __post_init__(self) -> None:
        if (not _valid_token(self.id, _PROFILE_ID) or not _valid_token(self.version, _VERSION)
                or not _valid_text(self.label, 80) or not _valid_text(self.description, 400)
                or not isinstance(self.default_kind, str) or self.default_kind not in REVIEW_KINDS
                or not _valid_list(self.required_evidence, 8)
                or not all(_valid_token(field, _FIELD) for field in self.required_evidence)
                or not _valid_list(self.review_steps, 8)
                or not all(_valid_text(step, 180) for step in self.review_steps)
                or not _valid_list(self.approval_channels, len(APPROVAL_CHANNELS))
                or not set(self.approval_channels) <= APPROVAL_CHANNELS
                or "companion" not in self.approval_channels
                or type(self.allow_service_drafts) is not bool
                or (self.allow_service_drafts
                    and (self.id == "governance" or "service" not in self.approval_channels))):
            raise WorkbenchError("invalid_work_profile", 422)
        for field in ("required_evidence", "review_steps", "approval_channels"):
            object.__setattr__(self, field, tuple(getattr(self, field)))

    @classmethod
    def from_dict(cls, data: Mapping[str, Any]) -> WorkProfile:
        """Load a strict data-only configuration from an operator-controlled source."""
        if not isinstance(data, Mapping) or set(data) != _PROFILE_FIELDS:
            raise WorkbenchError("invalid_work_profile", 422)
        return cls(**data)

    def describe(self) -> dict[str, Any]:
        return {
            "id": self.id, "version": self.version, "label": self.label,
            "description": self.description, "default_kind": self.default_kind,
            "required_evidence": list(self.required_evidence), "review_steps": list(self.review_steps),
            "approval_channels": list(self.approval_channels),
            "allow_service_drafts": self.allow_service_drafts,
        }

    def snapshot(self) -> dict[str, str]:
        canonical = json.dumps(self.describe(), sort_keys=True, separators=(",", ":"), ensure_ascii=True)
        return {"id": self.id, "version": self.version,
                "fingerprint": hashlib.sha256(canonical.encode("ascii")).hexdigest()}

    def missing_evidence(self, evidence: Mapping[str, str]) -> tuple[str, ...]:
        return tuple(field for field in self.required_evidence if field not in evidence)

    def channel_disposition(self, *, channel: str, kind: str,
                            service_identity: bool, action_type: str | None) -> str:
        if channel not in self.approval_channels:
            return "channel_unavailable"
        if service_identity and (channel != "service" or not self.allow_service_drafts
                                 or kind != "action" or action_type != DRAFT_ACTION):
            return "human_required"
        return "review_ready"

    def prepare(self, engine: Workbench, actor: WorkActor, *, subject: str, title: str, reason: str,
                evidence: Mapping[str, str], kind: str | None = None) -> dict[str, Any]:
        """Validate evidence, then propose a review with no executable effect."""
        engine.check_access(actor)
        fields = _evidence_fields(evidence)
        if self.missing_evidence(fields):
            raise WorkbenchError("profile_evidence_required", 422)
        review_kind = self.default_kind if kind is None else kind
        if not isinstance(review_kind, str) or review_kind not in REVIEW_KINDS:
            raise WorkbenchError("invalid_review_kind", 422)
        return engine.propose_review(
            actor, subject=subject, title=title, kind=review_kind, reason=reason,
            evidence=[f"{key}: {fields[key]}" for key in sorted(fields)], profile=self.snapshot(),
        )


_CHANNELS = ("companion", "slack", "teams", "service")
_PROFILES = {
    "governance": WorkProfile(
        "governance", "1", "AI governance",
        "Independent review of AI changes, with an auditable evidence checklist.", "strategic",
        ("artifact_ref", "evaluation_ref", "risk_summary", "rollback_plan"),
        ("Verify the exact artifact and its evaluation.", "Review risks and rollback with an independent reviewer.",
         "Record the reason for the decision."), _CHANNELS,
    ),
    "operations": WorkProfile(
        "operations", "1", "Business operations",
        "Resolve exceptions using verified CRM and ERP context.", "undocumented",
        ("record_ref", "observed_state", "expected_effect"),
        ("Verify the business record.", "Compare the proposed effect with the current ERP state."), _CHANNELS,
        allow_service_drafts=True,
    ),
    "platform": WorkProfile(
        "platform", "1", "Platform engineering",
        "Peer review for operational and platform changes.", "strategic",
        ("change_ref", "test_ref", "impact_summary", "rollback_plan"),
        ("Review the change and tests.", "Confirm that rollback is available.", "Obtain peer review."), _CHANNELS,
    ),
}


def get_profile(profile_id: str = "governance") -> WorkProfile:
    if not isinstance(profile_id, str) or profile_id not in _PROFILES:
        raise WorkbenchError("work_profile_not_found", 404)
    return _PROFILES[profile_id]


def list_profiles() -> tuple[WorkProfile, ...]:
    return tuple(_PROFILES.values())


def _synthetic_policy_cases() -> tuple[dict[str, Any], ...]:
    return (
        {"id": "model-release", "kind": "strategic", "channel": "slack",
         "service_identity": False, "action_type": None,
         "evidence": {"artifact_ref": "model/v2", "evaluation_ref": "eval/v2",
                      "risk_summary": "Regression detected", "rollback_plan": "Restore model/v1"}},
        {"id": "order-draft", "kind": "action", "channel": "service",
         "service_identity": True, "action_type": DRAFT_ACTION,
         "evidence": {"record_ref": "CRM-42", "observed_state": "Opportunity won",
                      "expected_effect": "Create a single draft"}},
    )


def _policy_outcome(profile: WorkProfile, case: Mapping[str, Any], evidence: Mapping[str, str]) -> dict[str, Any]:
    missing = profile.missing_evidence(evidence)
    disposition = profile.channel_disposition(channel=case["channel"], kind=case["kind"],
                                              service_identity=case["service_identity"],
                                              action_type=case["action_type"])
    return {"channel_disposition": disposition, "missing_evidence": list(missing),
            "ready_for_review": not missing and disposition == "review_ready"}


def compare_policies(baseline: WorkProfile, candidate: WorkProfile,
                     cases: Sequence[Mapping[str, Any]] | None = None) -> dict[str, Any]:
    """Compare two profiles on supplied evidence without applying decisions or activating rules."""
    sample = _synthetic_policy_cases() if cases is None else cases
    if not isinstance(sample, (list, tuple)) or len(sample) > 1000:
        raise WorkbenchError("invalid_policy_sample", 422)
    expected = {"id", "kind", "channel", "service_identity", "action_type", "evidence"}
    seen: set[str] = set()
    changes = []
    for case in sample:
        if (not isinstance(case, Mapping) or set(case) != expected or not _valid_text(case["id"], 128)
                or case["id"] in seen or not isinstance(case["kind"], str)
                or case["kind"] not in {"action", *REVIEW_KINDS}
                or not isinstance(case["channel"], str) or case["channel"] not in APPROVAL_CHANNELS
                or type(case["service_identity"]) is not bool
                or (case["action_type"] is not None and not _valid_text(case["action_type"], 128))):
            raise WorkbenchError("invalid_policy_sample", 422)
        seen.add(case["id"])
        evidence = _evidence_fields(case["evidence"])
        before = _policy_outcome(baseline, case, evidence)
        after = _policy_outcome(candidate, case, evidence)
        if before != after:
            changes.append({"id": case["id"], "baseline": before, "candidate": after})
    old, new = baseline.describe(), candidate.describe()
    return {
        "baseline": baseline.snapshot(), "candidate": candidate.snapshot(),
        "sample": "synthetic" if cases is None else "provided",
        "cases_examined": len(sample), "changed_count": len(changes), "changes": changes,
        "rule_changes": {field: {"baseline": old[field], "candidate": new[field]}
                         for field in ("default_kind", "required_evidence", "review_steps",
                                       "approval_channels", "allow_service_drafts") if old[field] != new[field]},
    }
