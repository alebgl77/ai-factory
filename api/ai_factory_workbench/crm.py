"""Read-only CRM synchronization with durable, workspace-bound progress."""

from __future__ import annotations

import json as json_codec
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from decimal import Context, Decimal, InvalidOperation, localcontext
from typing import TYPE_CHECKING, Protocol
from urllib.parse import urlsplit

import httpx

from ._hashing import digest
from .adapters import OpportunityEvent, normalize_opportunity_event
from .cyberlog import append_event

if TYPE_CHECKING:
    from .identity import WorkActor
    from .service import Workbench

_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)
_MAX_WINDOW = timedelta(days=7)
_MAX_ROWS = 10_000
_MAX_BYTES = 4 * 1024 * 1024
_PROVIDER = re.compile(r"[a-z][a-z0-9-]{0,23}")
_INSTANCE = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,47}")
_RECORD = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:-]{0,127}")
_TWO_DECIMALS = frozenset("AUD BRL CAD CHF CNY CZK DKK EUR GBP HKD ILS INR MXN NOK NZD PLN RON SAR SEK SGD TRY USD ZAR".split())
_MINOR_DIGITS = {**dict.fromkeys(_TWO_DECIMALS, 2), **dict.fromkeys(("CLP", "JPY", "KRW", "VND"), 0),
                 **dict.fromkeys(("BHD", "JOD", "KWD", "OMR", "TND"), 3)}


class CRMError(RuntimeError):
    """Redacted CRM failure; safe to report without request or response data."""

    def __init__(self, code: str, status_code: int | None = None) -> None:
        super().__init__(code)
        self.code = code
        self.status_code = status_code


def source_id(provider: str, instance: str) -> str:
    if (not isinstance(provider, str) or not _PROVIDER.fullmatch(provider)
            or not isinstance(instance, str) or not _INSTANCE.fullmatch(instance)):
        raise CRMError("crm_invalid_source")
    return f"{provider}/{instance}"


def validate_source(source: str) -> str:
    if not isinstance(source, str) or source.count("/") != 1:
        raise CRMError("crm_invalid_source")
    return source_id(*source.split("/", 1))


def crm_time(value: str | datetime) -> datetime:
    """Parse a time-zone-qualified provider timestamp and normalize to UTC."""
    try:
        if isinstance(value, str) and len(value) <= 40 and "T" in value:
            value = datetime.fromisoformat(value.removesuffix("Z") + "+00:00" if value.endswith("Z") else value)
        if not isinstance(value, datetime) or value.utcoffset() is None:
            raise ValueError
        return value.astimezone(timezone.utc)
    except (ValueError, OverflowError):
        raise CRMError("crm_invalid_timestamp") from None


def validate_window(start: datetime, end: datetime) -> tuple[datetime, datetime]:
    start, end = crm_time(start), crm_time(end)
    if start < _EPOCH or end <= start or end - start > _MAX_WINDOW:
        raise CRMError("crm_invalid_window")
    return start, end


def _version(timestamp: datetime) -> int:
    difference = timestamp - _EPOCH
    version = (difference.days * 86_400 + difference.seconds) * 1_000_000 + difference.microseconds
    if not 0 < version < 2**53:
        raise CRMError("crm_invalid_timestamp")
    return version


class CRMHTTP:
    """HTTP transport pinned to an operator-selected HTTPS origin."""

    def __init__(self, base_url: str, token: str, *, client: httpx.Client | None = None) -> None:
        try:
            if (not isinstance(base_url, str) or not base_url or len(base_url) > 512
                    or any(not char.isascii() or char.isspace() or ord(char) < 32 for char in base_url)
                    or "\\" in base_url or "?" in base_url or "#" in base_url):
                raise ValueError
            url = urlsplit(base_url)
            if (url.scheme != "https" or not url.hostname or url.username is not None or url.password is not None
                    or url.path not in ("", "/") or url.port == 0 or "%" in url.netloc):
                raise ValueError
            self._origin = httpx.URL(base_url).copy_with(path="")
        except (ValueError, httpx.InvalidURL):
            raise CRMError("crm_invalid_origin") from None
        if (not isinstance(token, str) or not 1 <= len(token) <= 8192
                or any(not 33 <= ord(char) <= 126 for char in token)):
            raise CRMError("crm_invalid_token")
        self._token = token
        self._owns_client = client is None
        self._client = client if client is not None else httpx.Client(
            timeout=httpx.Timeout(20, connect=5), follow_redirects=False, trust_env=False,
        )

    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def request(self, method: str, path: str, *, params: Mapping[str, object] | None = None,
                json: Mapping[str, object] | None = None, headers: Mapping[str, str] | None = None) -> dict:
        # Paths are provider-owned. Query values go through HTTPX parameter encoding.
        if (method not in {"GET", "POST"} or not isinstance(path, str) or not path.startswith("/")
                or path.startswith("//") or any(char in path for char in "\\?#%")
                or any(not 33 <= ord(char) <= 126 for char in path) or len(path) > 2048
                or any(segment in {".", ".."} for segment in path.split("/"))):
            raise CRMError("crm_invalid_request")
        if headers is not None and any(key.lower() != "sforce-query-options" for key in headers):
            raise CRMError("crm_invalid_request")
        request_headers = {"Authorization": f"Bearer {self._token}", "Accept": "application/json", **(headers or {})}
        try:
            with self._client.stream(method, self._origin.copy_with(path=path), params=params, json=json,
                                     headers=request_headers, auth=None, follow_redirects=False) as response:
                status = response.status_code
                if not 200 <= status < 300:
                    code = {401: "crm_authentication_failed", 403: "crm_forbidden", 429: "crm_rate_limited"}.get(
                        status, "crm_http_error",
                    )
                    raise CRMError(code, status)
                content = bytearray()
                for chunk in response.iter_bytes():
                    if len(content) + len(chunk) > _MAX_BYTES:
                        raise CRMError("crm_response_too_large")
                    content.extend(chunk)
            result = json_codec.loads(content, parse_float=Decimal)
        except httpx.HTTPError:
            raise CRMError("crm_transport_error") from None
        except (ValueError, TypeError, InvalidOperation, RecursionError):
            raise CRMError("crm_invalid_response") from None
        if not isinstance(result, dict):
            raise CRMError("crm_invalid_response")
        return result


@dataclass(frozen=True)
class CRMOpportunity:
    record_id: str
    updated_at: datetime
    won: bool
    title: str | None = None
    account_id: str | None = None
    account_name: str | None = None
    amount: str | Decimal | int | None = None
    currency: str | None = None


class CRMSource(Protocol):
    @property
    def source(self) -> str: ...

    def opportunities(self, start: datetime, end: datetime) -> Iterable[CRMOpportunity]: ...


@dataclass(frozen=True)
class CRMBlockedOpportunity:
    """A won record with verified identity but unusable business fields."""

    code: str


CRM_CONFLICT = CRMBlockedOpportunity("crm_revision_conflict")


@dataclass(frozen=True)
class CRMSyncResult:
    source: str
    until: datetime
    seen: int = 0
    applied: int = 0
    replayed: int = 0
    ignored: int = 0
    stale: int = 0
    withdrawn: int = 0
    reconciliation_required: int = 0
    blocked: int = 0
    conflicts: int = 0


def _amount_minor(amount: object, currency: object) -> int:
    if not isinstance(currency, str) or currency not in _MINOR_DIGITS:
        raise CRMError("crm_unsupported_currency")
    try:
        if type(amount) not in (str, int, Decimal) or (isinstance(amount, str) and len(amount) > 48):
            raise ValueError
        value = Decimal(amount)
        if not value.is_finite() or len(value.as_tuple().digits) > 40 or not -18 <= value.as_tuple().exponent <= 16:
            raise ValueError
        with localcontext(Context(prec=50)):
            minor = value.scaleb(_MINOR_DIGITS[currency])
            if minor != minor.to_integral_value() or not 1 <= minor <= 100_000_000_000:
                raise ValueError
        return int(minor)
    except (ValueError, InvalidOperation, OverflowError):
        raise CRMError("crm_invalid_amount") from None


def _normalize(source: str, workspace: str, row: CRMOpportunity,
               start: datetime, end: datetime) -> tuple[int, OpportunityEvent | CRMBlockedOpportunity | None]:
    if (not isinstance(row, CRMOpportunity) or not isinstance(row.record_id, str)
            or not _RECORD.fullmatch(row.record_id) or type(row.won) is not bool):
        raise CRMError("crm_invalid_opportunity")
    timestamp = crm_time(row.updated_at)
    if not start <= timestamp < end:
        raise CRMError("crm_out_of_window")
    version = _version(timestamp)
    if not row.won:
        return version, None

    try:
        minor_amount = _amount_minor(row.amount, row.currency)
    except CRMError as error:
        return version, CRMBlockedOpportunity(error.code)
    if row.account_name is not None and not isinstance(row.account_name, str):
        return version, CRMBlockedOpportunity("crm_invalid_business_fields")
    data = {"title": row.title, "customer_name": row.account_name or row.account_id or "Unmapped CRM company",
            "amount_minor": minor_amount, "currency": row.currency, "record_version": version}
    if row.account_id is not None:
        if not isinstance(row.account_id, str) or not _RECORD.fullmatch(row.account_id):
            return version, CRMBlockedOpportunity("crm_invalid_business_fields")
        data["customer_ref"] = f"{source}/{row.account_id}"
    try:
        event = normalize_opportunity_event({
            "specversion": "1.0", "type": "crm.opportunity.won", "source": source,
            "subject": row.record_id, "id": "crm-" + digest([source, row.record_id, version]),
            "time": timestamp.isoformat().replace("+00:00", "Z"), "data": data,
        }, tenant=workspace)
    except (TypeError, ValueError):
        return version, CRMBlockedOpportunity("crm_invalid_business_fields")
    return version, event


def sync_opportunities(engine: Workbench, provider: CRMSource, actor: WorkActor, *,
                       since: datetime | None = None, until: datetime,
                       lookback: timedelta = timedelta(minutes=5),
                       profile: dict[str, str] | None = None) -> CRMSyncResult:
    """Apply the latest observation per opportunity and checkpoint only on success.

    The CRM and SQLite are not one transaction. Completed sink operations are
    durable and idempotent; a partial failure records confirmed safety holds
    and withdrawals without creating new actionable proposals or advancing the
    cursor.
    """
    source = validate_source(provider.source)
    profile = engine._check_crm_ingestion(source, actor, profile=profile)
    if not isinstance(lookback, timedelta) or not timedelta(0) <= lookback <= timedelta(days=1):
        raise CRMError("crm_invalid_lookback")
    end = crm_time(until)
    end_version = _version(end)
    with engine.store.transaction(write=False) as connection:
        checkpoint = connection.execute(
            "SELECT start_at, until_at FROM crm_checkpoints WHERE source = ?", (source,),
        ).fetchone()
    if checkpoint is None:
        if since is None:
            raise CRMError("crm_initial_since_required")
        start = crm_time(since)
        first_version = _version(start)
        expected = None
    else:
        first_version, expected = checkpoint["start_at"], checkpoint["until_at"]
        if since is not None and _version(crm_time(since)) != first_version:
            raise CRMError("crm_since_already_checkpointed")
        if end_version < expected:
            raise CRMError("crm_checkpoint_not_forward")
        if end_version == expected:
            return CRMSyncResult(source, end)
        start = max(_EPOCH + timedelta(microseconds=first_version),
                    _EPOCH + timedelta(microseconds=expected) - lookback)
    start, end = validate_window(start, end)

    seen = 0
    revisions: dict[tuple[str, int], OpportunityEvent | CRMBlockedOpportunity | None] = {}
    latest: dict[str, int] = {}
    latest_blocking: dict[str, int] = {}
    try:
        for row in provider.opportunities(start, end):
            seen += 1
            if seen > _MAX_ROWS:
                raise CRMError("crm_window_too_many_records")
            version, event = _normalize(source, engine.workspace, row, start, end)
            subject = row.record_id
            key = (subject, version)
            if key in revisions and event != revisions[key]:
                event = CRM_CONFLICT
            revisions[key] = event
            latest[subject] = max(version, latest.get(subject, version))
            if not isinstance(event, OpportunityEvent):
                latest_blocking[subject] = max(version, latest_blocking.get(subject, version))
    except CRMError:
        # A later win cannot hide an observed withdrawal in an incomplete poll.
        for subject, version in sorted(latest_blocking.items()):
            engine._apply_crm_observation(source, subject, version, revisions[subject, version], actor, profile=profile)
        for subject, version in sorted(latest.items()):
            event = revisions[subject, version]
            if isinstance(event, OpportunityEvent):
                engine._apply_crm_observation(source, subject, version, event, actor, profile=profile, incomplete=True)
        raise

    outcomes = dict.fromkeys(("applied", "replayed", "ignored", "stale", "withdrawn", "reconciliation_required",
                             "blocked", "conflicts"), 0)
    for subject, version in sorted(latest.items()):
        outcome = engine._apply_crm_observation(source, subject, version, revisions[subject, version], actor,
                                                profile=profile)
        for state in outcome:
            outcomes[state] += 1

    with engine.store.transaction() as connection:
        if expected is None:
            changed = connection.execute(
                "INSERT OR IGNORE INTO crm_checkpoints(source,start_at,until_at) VALUES (?,?,?)",
                (source, first_version, end_version),
            )
        else:
            changed = connection.execute(
                "UPDATE crm_checkpoints SET until_at = ? WHERE source = ? AND until_at = ?",
                (end_version, source, expected),
            )
        if changed.rowcount != 1:
            raise CRMError("crm_checkpoint_changed")
        append_event(connection, "crm.checkpoint", "success", actor=actor.actor, source=source)
    return CRMSyncResult(source, end, seen, **outcomes)
