"""Explicit provider capabilities, normalized CRM events, and read-only ERP polling."""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Protocol
from urllib.parse import urlsplit

import httpx


@dataclass(frozen=True)
class AdapterCapabilities:
    incremental_reads: bool = False
    draft_writes: bool = False
    idempotency_keys: bool = False
    conditional_writes: bool = False


def _digest(value: object) -> str:
    body = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(body.encode("utf-8")).hexdigest()


_KEY = re.compile(r"[A-Za-z0-9][A-Za-z0-9_.:/-]{0,127}")
_CURRENCY = re.compile(r"[A-Z]{3}")


def _key(value: object, field: str) -> str:
    if not isinstance(value, str) or not _KEY.fullmatch(value):
        raise ValueError(f"invalid {field}")
    return value


def _text(value: object, field: str, limit: int = 160) -> str:
    if not isinstance(value, str) or len(value) > limit or any(ord(char) < 32 for char in value):
        raise ValueError(f"invalid {field}")
    result = value.strip()
    if not result:
        raise ValueError(f"invalid {field}")
    try:
        result.encode("utf-8")
    except UnicodeEncodeError:
        raise ValueError(f"invalid {field}") from None
    return result


def _integer(value: object, field: str, minimum: int, maximum: int) -> int:
    if type(value) is not int or not minimum <= value <= maximum:
        raise ValueError(f"invalid {field}")
    return value


@dataclass(frozen=True)
class OpportunityEvent:
    tenant: str
    source: str
    id: str
    subject: str
    title: str
    customer_name: str
    customer_ref: str | None
    amount_minor: int
    currency: str
    record_version: int
    time: str | None = None

    @property
    def key(self) -> tuple[str, str, str]:
        return self.tenant, self.source, self.id

    @property
    def record_key(self) -> tuple[str, str, str]:
        return self.tenant, self.source, self.subject

    @property
    def fingerprint(self) -> str:
        return _digest(self.to_dict())

    @property
    def record_fingerprint(self) -> str:
        return _digest(self.to_dict()["data"])

    def to_dict(self) -> dict:
        result = {
            "specversion": "1.0",
            "source": self.source,
            "id": self.id,
            "type": "crm.opportunity.won",
            "subject": self.subject,
            "data": {
                "title": self.title,
                "customer_name": self.customer_name,
                "amount_minor": self.amount_minor,
                "currency": self.currency,
                "record_version": self.record_version,
            },
        }
        if self.customer_ref is not None:
            result["data"]["customer_ref"] = self.customer_ref
        if self.time is not None:
            result["time"] = self.time
        return result


def normalize_opportunity_event(payload: Mapping[str, object], *, tenant: str = "demo") -> OpportunityEvent:
    """Normalize the supported CloudEvents subset without trusting tenancy in payloads."""
    allowed = {"specversion", "source", "id", "type", "subject", "data", "time", "datacontenttype"}
    if (not isinstance(payload, Mapping) or set(payload).difference(allowed)
            or payload.get("specversion") != "1.0" or payload.get("type") != "crm.opportunity.won"
            or payload.get("datacontenttype", "application/json") != "application/json"):
        raise ValueError("unsupported opportunity event")

    data = payload.get("data")
    allowed_data = {"title", "customer_name", "customer_ref", "amount_minor", "currency", "record_version"}
    if not isinstance(data, Mapping) or set(data).difference(allowed_data):
        raise ValueError("unsupported opportunity data")
    currency = data.get("currency")
    if not isinstance(currency, str) or not _CURRENCY.fullmatch(currency):
        raise ValueError("invalid currency")
    ref = data.get("customer_ref")
    occurred = payload.get("time")
    if occurred is not None:
        try:
            if (not isinstance(occurred, str) or len(occurred) > 40 or "T" not in occurred):
                raise ValueError
            timestamp = datetime.fromisoformat(occurred.replace("Z", "+00:00"))
            if timestamp.tzinfo is None:
                raise ValueError
            occurred = timestamp.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
        except (ValueError, OverflowError):
            raise ValueError("invalid event time") from None

    return OpportunityEvent(
        tenant=_key(tenant, "tenant"),
        source=_key(payload.get("source"), "event source"),
        id=_key(payload.get("id"), "event id"),
        subject=_key(payload.get("subject"), "event subject"),
        title=_text(data.get("title"), "title"),
        customer_name=_text(data.get("customer_name"), "customer name"),
        customer_ref=_key(ref, "customer reference") if ref is not None else None,
        amount_minor=_integer(data.get("amount_minor"), "amount", 1, 100_000_000_000),
        currency=currency,
        record_version=_integer(data.get("record_version"), "record version", 1, 2**53 - 1),
        time=occurred,
    )


class EventCheckpoint:
    """In-memory event/revision checkpoint for replay and connector conformance tests."""

    def __init__(self) -> None:
        self._seen: dict[tuple[str, str, str], str] = {}
        self._heads: dict[tuple[str, str, str], tuple[int, str]] = {}

    def observe(self, event: OpportunityEvent) -> str:
        """Classify delivery without moving the per-record head backwards."""
        fingerprint = event.fingerprint
        previous = self._seen.get(event.key)
        if previous is not None:
            return "duplicate" if previous == fingerprint else "conflict"
        head = self._heads.get(event.record_key)
        record_fingerprint = event.record_fingerprint
        if head is not None and event.record_version <= head[0]:
            if event.record_version == head[0] and record_fingerprint != head[1]:
                return "conflict"
            self._seen[event.key] = fingerprint
            return "duplicate" if event.record_version == head[0] else "stale"
        self._seen[event.key] = fingerprint
        self._heads[event.record_key] = (event.record_version, record_fingerprint)
        return "accepted"


class AdapterError(RuntimeError):
    """A redacted provider failure safe to report to operators."""


_ODOO_DATE = "%Y-%m-%d %H:%M:%S"
_MAX_PAGE_BYTES = 512 * 1024


@dataclass(frozen=True, order=True)
class PartnerCursor:
    write_date: str
    record_id: int

    def __post_init__(self) -> None:
        try:
            if datetime.strptime(self.write_date, _ODOO_DATE).strftime(_ODOO_DATE) != self.write_date:
                raise ValueError
        except (ValueError, TypeError):
            raise ValueError("invalid Odoo cursor date") from None
        _integer(self.record_id, "Odoo cursor id", 0, 2**53 - 1)

    def overlap(self, seconds: int = 5) -> PartnerCursor:
        """Rewind for overlap polling; sinks must deduplicate rows from the overlap."""
        _integer(seconds, "overlap", 0, 86_400)
        try:
            previous = datetime.strptime(self.write_date, _ODOO_DATE) - timedelta(seconds=seconds)
        except OverflowError:
            raise ValueError("overlap precedes minimum cursor date") from None
        return PartnerCursor(previous.strftime(_ODOO_DATE), 0)


@dataclass(frozen=True)
class OdooPartner:
    record_id: int
    name: str
    reference: str | None
    write_date: str

    @property
    def cursor(self) -> PartnerCursor:
        return PartnerCursor(self.write_date, self.record_id)


@dataclass(frozen=True)
class PartnerPage:
    records: tuple[OdooPartner, ...]
    checkpoint: PartnerCursor | None


class PartnerSource(Protocol):
    capabilities: AdapterCapabilities

    def partners_page(self, after: PartnerCursor | None = None, *, limit: int = 100) -> PartnerPage: ...


class Odoo19Adapter:
    """Use JSON-2 search_read only. The origin and credential are operator supplied."""

    capabilities = AdapterCapabilities(incremental_reads=True)

    def __init__(self, url: str, database: str, api_key: str, *, client: httpx.Client | None = None) -> None:
        if not isinstance(url, str) or not url or any(char.isspace() or char == "\\" for char in url):
            raise ValueError("Odoo URL must be an explicit HTTPS origin")
        origin = urlsplit(url)
        if (origin.scheme != "https" or not origin.hostname or origin.username is not None
                or origin.password is not None or origin.path not in ("", "/") or origin.query or origin.fragment):
            raise ValueError("Odoo URL must be an explicit HTTPS origin")
        # urllib only validates the port when the property is accessed.
        _ = origin.port
        if not isinstance(database, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_.-]{0,127}", database):
            raise ValueError("invalid Odoo database")
        if not isinstance(api_key, str) or not re.fullmatch(r"[!-~]{1,512}", api_key):
            raise ValueError("invalid Odoo API key")
        self._origin = origin.geturl().rstrip("/")
        self._database = database
        self._api_key = api_key
        self._client = client

    def partners_page(self, after: PartnerCursor | None = None, *, limit: int = 100) -> PartnerPage:
        _integer(limit, "page size", 1, 200)
        domain: list = []
        if after is not None:
            domain = ["|", ["write_date", ">", after.write_date], "&",
                      ["write_date", "=", after.write_date], ["id", ">", after.record_id]]
        body = {"domain": domain, "fields": ["id", "name", "ref", "write_date"],
                "order": "write_date asc, id asc", "limit": limit}
        headers = {"Authorization": f"Bearer {self._api_key}", "X-Odoo-Database": self._database}

        def read(client: httpx.Client) -> object:
            payload = bytearray()
            with client.stream("POST", f"{self._origin}/json/2/res.partner/search_read", headers=headers,
                               json=body, timeout=10.0, follow_redirects=False) as response:
                response.raise_for_status()
                for chunk in response.iter_bytes(chunk_size=16_384):
                    if len(payload) + len(chunk) > _MAX_PAGE_BYTES:
                        raise AdapterError("Odoo partner page exceeds size limit")
                    payload.extend(chunk)
            return json.loads(payload)

        try:
            if self._client is None:
                with httpx.Client() as client:
                    rows = read(client)
            else:
                rows = read(self._client)
        except httpx.HTTPStatusError as exc:
            raise AdapterError(f"Odoo read failed (HTTP {exc.response.status_code})") from None
        except (httpx.HTTPError, ValueError):
            raise AdapterError("Odoo read failed") from None

        cursor = after
        records: list[OdooPartner] = []
        try:
            if not isinstance(rows, list) or len(rows) > limit:
                raise ValueError
            for row in rows:
                if not isinstance(row, dict):
                    raise ValueError
                record_id = _integer(row.get("id"), "partner id", 1, 2**53 - 1)
                name = _text(row.get("name"), "partner name", 255)
                raw_ref = row.get("ref")
                reference = None if raw_ref is None or raw_ref is False or raw_ref == "" else _text(raw_ref, "reference")
                next_cursor = PartnerCursor(row.get("write_date"), record_id)
                if cursor is not None and next_cursor <= cursor:
                    raise ValueError
                records.append(OdooPartner(record_id, name, reference, next_cursor.write_date))
                cursor = next_cursor
        except (ValueError, TypeError):
            raise AdapterError("Odoo returned an invalid or unordered partner page") from None
        return PartnerPage(tuple(records), cursor)


def sync_partner_page(
    source: PartnerSource,
    after: PartnerCursor | None,
    apply: Callable[[OdooPartner], None],
    save_checkpoint: Callable[[PartnerCursor], None],
    *,
    limit: int = 100,
) -> PartnerPage:
    """Commit the cursor after all rows apply. A partial failure may replay rows."""
    _integer(limit, "page size", 1, 200)
    page = source.partners_page(after, limit=limit)
    if len(page.records) > limit:
        raise AdapterError("partner source exceeded page size")
    expected = after
    for partner in page.records:
        if expected is not None and partner.cursor <= expected:
            raise AdapterError("partner source returned a non-advancing page")
        expected = partner.cursor
    if page.checkpoint != expected:
        raise AdapterError("partner source returned an inconsistent checkpoint")

    for partner in page.records:
        apply(partner)
    if page.checkpoint is not None and page.checkpoint != after:
        save_checkpoint(page.checkpoint)
    return page
