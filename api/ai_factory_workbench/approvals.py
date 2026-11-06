"""Verify host-configured approval attestations from trusted workflow relays."""

from __future__ import annotations

import datetime as dt
import hashlib
import hmac
import json
import re
from collections.abc import Callable, Iterable, Mapping
from dataclasses import dataclass, field
from types import MappingProxyType
from typing import TYPE_CHECKING, Any

from .identity import ASSESSMENT, WorkActor
from .profiles import WorkProfile
from .store import WorkbenchError

if TYPE_CHECKING:
    from .service import Workbench

_RELAY_CHANNELS = frozenset({"slack", "teams", "service"})
_DECISIONS = frozenset({"approve", "reject", "request_changes"})
_TOKEN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:@-]{0,127}")
_DIGEST = re.compile(r"[a-f0-9]{64}")
_FIELDS = frozenset({
    "workspace", "issuer", "channel", "identity", "case_id", "revision", "proposal_hash",
    "decision", "rationale", "issued_at", "expires_at", "request_id",
})
_SIGNING_CONTEXT = b"ai-factory-workbench:approval:v1\0"
_MAX_AGE_SECONDS = 300
_FUTURE_SKEW_SECONDS = 30


def _token(value: object) -> bool:
    return isinstance(value, str) and _TOKEN.fullmatch(value) is not None


def _key_ok(key: object) -> bool:
    return isinstance(key, bytes) and 32 <= len(key) <= 128


def _canonical(envelope: Mapping[str, Any]) -> tuple[dict[str, Any], bytes]:
    if not isinstance(envelope, Mapping) or set(envelope) != _FIELDS:
        raise WorkbenchError("invalid_approval_envelope", 422)
    data = dict(envelope)
    rationale = data["rationale"]
    if (not all(_token(data[key]) for key in ("workspace", "issuer", "identity", "case_id", "request_id"))
            or not isinstance(data["channel"], str) or data["channel"] not in _RELAY_CHANNELS
            or not isinstance(data["decision"], str) or data["decision"] not in _DECISIONS
            or not isinstance(data["proposal_hash"], str) or _DIGEST.fullmatch(data["proposal_hash"]) is None
            or type(data["revision"]) is not int or not 1 <= data["revision"] <= (1 << 63) - 1
            or any(type(data[key]) is not int or not 0 < data[key] <= 253402300799
                   for key in ("issued_at", "expires_at"))
            or not 0 < data["expires_at"] - data["issued_at"] <= _MAX_AGE_SECONDS
            or not isinstance(rationale, str) or not rationale.strip() or len(rationale) > 1000
            or any(0xD800 <= ord(char) <= 0xDFFF for char in rationale)):
        raise WorkbenchError("invalid_approval_envelope", 422)
    canonical = json.dumps(data, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return data, _SIGNING_CONTEXT + canonical.encode("ascii")


def sign_approval(key: bytes, envelope: Mapping[str, Any]) -> str:
    """Sign a validated v1 envelope in an already authenticated, trusted relay."""
    if not _key_ok(key):
        raise ValueError("approval signing key must contain 32 to 128 bytes")
    _, canonical = _canonical(envelope)
    return hmac.new(key, canonical, hashlib.sha256).hexdigest()


@dataclass(frozen=True, slots=True)
class RelayAuthority:
    """Trust one issuer to attest identities from one preconfigured workflow channel."""

    issuer: str
    key: bytes = field(repr=False)
    channel: str
    actors: Mapping[str, WorkActor] = field(repr=False)
    service_identities: frozenset[str] = frozenset()

    def __post_init__(self) -> None:
        if (not _token(self.issuer) or not _key_ok(self.key)
                or not isinstance(self.channel, str) or self.channel not in _RELAY_CHANNELS
                or not isinstance(self.actors, Mapping) or not self.actors
                or not all(_token(identity) for identity in self.actors)
                or not isinstance(self.service_identities, (set, frozenset))
                or not self.service_identities <= self.actors.keys()
                or (self.service_identities and self.channel != "service")):
            raise ValueError("invalid approval relay authority")
        object.__setattr__(self, "actors", MappingProxyType(dict(self.actors)))
        object.__setattr__(self, "service_identities", frozenset(self.service_identities))


def _actor_aliases(actor: WorkActor) -> frozenset[str]:
    aliases = actor.ids | {actor.actor}
    return aliases | frozenset(alias.lower() for alias in aliases if "@" in alias)


class ApprovalRelay:
    """Apply verified decisions using the same revision and role checks as the Companion."""

    def __init__(self, engine: Workbench, profile: WorkProfile, authorities: Iterable[RelayAuthority], *,
                 clock: Callable[[], dt.datetime] | None = None) -> None:
        trusted = tuple(authorities)
        if (not trusted or not all(isinstance(authority, RelayAuthority) for authority in trusted)
                or len({authority.issuer for authority in trusted}) != len(trusted)
                or len({authority.key for authority in trusted}) != len(trusted)):
            raise ValueError("approval relay authorities require unique issuers and keys")
        machine_ids = frozenset(
            alias for authority in trusted for identity in authority.service_identities
            for alias in _actor_aliases(authority.actors[identity])
        )
        if any(
            machine_ids & _actor_aliases(actor) for authority in trusted
            for identity, actor in authority.actors.items() if identity not in authority.service_identities
        ):
            raise ValueError("human and service approval identities must be separate")
        self._machine_actor_ids = machine_ids
        self._authorities = {authority.issuer: authority for authority in trusted}
        self.engine = engine
        self.profile = profile
        self.clock = clock or engine.clock

    @property
    def machine_actor_ids(self) -> frozenset[str]:
        """Canonical aliases that are restricted to the service approval path."""
        return self._machine_actor_ids

    def decide(self, envelope: Mapping[str, Any], signature: str) -> dict[str, Any]:
        data, canonical = _canonical(envelope)
        authority = self._authorities.get(data["issuer"])
        if (authority is None or not isinstance(signature, str) or _DIGEST.fullmatch(signature) is None
                or not hmac.compare_digest(signature, hmac.new(authority.key, canonical, hashlib.sha256).hexdigest())):
            raise WorkbenchError("approval_signature_invalid", 403)
        if data["workspace"] != self.engine.workspace or data["channel"] != authority.channel:
            raise WorkbenchError("approval_scope_mismatch", 403)

        now = self.clock().timestamp()
        if data["expires_at"] <= now or data["issued_at"] > now + _FUTURE_SKEW_SECONDS:
            raise WorkbenchError("approval_attestation_expired", 409)

        actor = authority.actors.get(data["identity"])
        if actor is None or not actor.authenticated or not actor.role_for(ASSESSMENT):
            raise WorkbenchError("approval_identity_forbidden", 403)
        if data["channel"] not in self.profile.approval_channels:
            raise WorkbenchError("approval_channel_unavailable", 403)

        case = self.engine.get_case(data["case_id"], actor)
        if case["revision"] != data["revision"] or case["proposal_hash"] != data["proposal_hash"]:
            raise WorkbenchError("stale_proposal", 409)
        bound_profile = case.get("profile")
        if bound_profile is not None and bound_profile != self.profile.snapshot():
            raise WorkbenchError("approval_profile_mismatch", 409)
        action = case.get("action") or {}
        service_identity = data["identity"] in authority.service_identities
        if (service_identity and bound_profile is None) or self.profile.channel_disposition(
            channel=data["channel"], kind=case["kind"], service_identity=service_identity, action_type=action.get("type"),
        ) != "review_ready":
            raise WorkbenchError("human_approval_required", 403)

        provenance = {
            "issuer": authority.issuer, "channel": authority.channel, "identity": data["identity"],
            "request_id": data["request_id"],
            "proof_sha256": hashlib.sha256(canonical + bytes.fromhex(signature)).hexdigest(),
            "expires_at": dt.datetime.fromtimestamp(data["expires_at"], dt.timezone.utc).isoformat(),
        }
        return self.engine.decide(
            data["case_id"], actor, decision=data["decision"], revision=data["revision"],
            proposal_hash=data["proposal_hash"], rationale=data["rationale"], provenance=provenance,
        )
