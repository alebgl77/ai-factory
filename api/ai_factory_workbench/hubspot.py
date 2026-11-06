"""Read HubSpot deal changes and company associations."""

from __future__ import annotations

import re
import time
from collections.abc import Iterable
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from threading import Lock
from typing import Any

import httpx

from .crm import CRMError, CRMHTTP, CRMOpportunity, crm_time, source_id, validate_window

_API_ORIGIN = "https://api.hubapi.com"
_SEARCH = "/crm/objects/2026-09/deals/search"
_COMPANIES = "/crm/associations/2026-09/deals/companies/batch/read"
_FIELDS = ("dealname", "dealstage", "amount", "deal_currency_code", "hs_lastmodifieddate")
_MODIFIED = "hs_lastmodifieddate"
_PAGE_SIZE = 100
_RESULT_LIMIT = 10_000
_MAX_SEARCH_REQUESTS = 256
_SEARCH_INTERVAL = 0.21
_PRIMARY_COMPANY_TYPE = 5
_ID = re.compile(r"[1-9][0-9]{0,19}")
_STAGE = re.compile(r"[A-Za-z0-9_.:-]{1,100}")
_CURRENCY = re.compile(r"[A-Z]{3}")
_EPOCH = datetime(1970, 1, 1, tzinfo=timezone.utc)


def _object_id(value: object) -> str:
    if type(value) is int and 0 < value < 10**20:
        value = str(value)
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise CRMError("hubspot_invalid_response")
    return value


def _ceil_millis(value: datetime) -> int:
    elapsed = value - _EPOCH
    return ((elapsed.days * 86_400 + elapsed.seconds) * 1000
            + (elapsed.microseconds + 999) // 1000)


def _updated_at(value: object) -> datetime:
    try:
        if isinstance(value, str) and re.fullmatch(r"[0-9]{1,15}", value):
            return _EPOCH + timedelta(milliseconds=int(value))
        return crm_time(value)
    except (ValueError, TypeError, OverflowError, CRMError):
        raise CRMError("hubspot_invalid_response") from None


def _next_offset(page: dict[str, Any]) -> int | None:
    paging = page.get("paging", {})
    if not isinstance(paging, dict):
        raise CRMError("hubspot_invalid_cursor")
    if "next" not in paging:
        return None
    cursor = paging["next"]
    value = cursor.get("after") if isinstance(cursor, dict) else None
    if type(value) is int and 0 < value < _RESULT_LIMIT:
        return value
    if isinstance(value, str) and re.fullmatch(r"[1-9][0-9]{0,3}", value):
        return int(value)
    raise CRMError("hubspot_invalid_cursor")


def _company_id(row: dict[str, Any]) -> str | None:
    associated = row.get("to")
    paging = row.get("paging", {})
    if not isinstance(associated, list) or not isinstance(paging, dict):
        raise CRMError("hubspot_invalid_associations")
    if "next" in paging:
        # A truncated list cannot prove a company is unique or the sole primary.
        return None
    companies: set[str] = set()
    primary: set[str] = set()
    for item in associated:
        if not isinstance(item, dict) or not isinstance(item.get("associationTypes"), list):
            raise CRMError("hubspot_invalid_associations")
        company = _object_id(item.get("toObjectId"))
        companies.add(company)
        for relation in item["associationTypes"]:
            if not isinstance(relation, dict):
                raise CRMError("hubspot_invalid_associations")
            if relation.get("category") == "HUBSPOT_DEFINED" and relation.get("typeId") == _PRIMARY_COMPANY_TYPE:
                primary.add(company)
    candidates = primary or companies
    return next(iter(candidates)) if len(candidates) == 1 else None


class HubSpotCRM:
    """Poll one HubSpot portal with a read-scoped OAuth or service token.

    Missing proposal fields or an unresolved company are passed through
    for Workbench review.
    """

    def __init__(
        self,
        token: str,
        *,
        instance: str,
        won_stages: Iterable[str],
        default_currency: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(won_stages, (tuple, list, set, frozenset)) or not 1 <= len(won_stages) <= 100:
            raise CRMError("hubspot_invalid_won_stages")
        if any(not isinstance(stage, str) or not _STAGE.fullmatch(stage) for stage in won_stages):
            raise CRMError("hubspot_invalid_won_stages")
        if default_currency is not None and (
            not isinstance(default_currency, str) or not _CURRENCY.fullmatch(default_currency)
        ):
            raise CRMError("hubspot_invalid_currency")
        self.source = source_id("hubspot", instance)
        self._won_stages = frozenset(won_stages)
        self._currency = default_currency
        self._http = CRMHTTP(_API_ORIGIN, token, client=client)
        self._search_lock = Lock()
        self._search_at = 0.0

    def close(self) -> None:
        self._http.close()

    def opportunities(self, start: datetime, end: datetime) -> Iterable[CRMOpportunity]:
        start, end = validate_window(start, end)
        lower, upper = _ceil_millis(start), _ceil_millis(end)
        if lower == upper:
            return
        windows = [(lower, upper)]
        remaining = _MAX_SEARCH_REQUESTS
        while windows:
            lower, upper = windows.pop()
            offset = 0
            records: list[CRMOpportunity] = []
            record_ids: set[str] = set()
            while True:
                if remaining == 0:
                    raise CRMError("hubspot_search_budget")
                page = self._search(lower, upper, offset)
                remaining -= 1
                total = page.get("total")
                results = page.get("results")
                if type(total) is not int or total < 0 or not isinstance(results, list) or len(results) > _PAGE_SIZE:
                    raise CRMError("hubspot_invalid_response")
                if total >= _RESULT_LIMIT:
                    if upper - lower <= 1:
                        raise CRMError("hubspot_search_window_limit")
                    middle = (lower + upper) // 2
                    windows.extend(((middle, upper), (lower, middle)))
                    break
                for row in results:
                    record = self._opportunity(row)
                    if (record.record_id in record_ids or not start <= record.updated_at < end
                            or not lower <= _ceil_millis(record.updated_at) < upper):
                        raise CRMError("hubspot_search_changed")
                    record_ids.add(record.record_id)
                    records.append(record)
                next_offset = _next_offset(page)
                if next_offset is None:
                    if len(records) != total:
                        raise CRMError("hubspot_search_changed")
                    yield from self._with_companies(records)
                    break
                if not results or next_offset != len(records) or not offset < next_offset < total:
                    raise CRMError("hubspot_invalid_cursor")
                offset = next_offset

    def _search(self, lower: int, upper: int, offset: int) -> dict[str, Any]:
        body = {
            "filterGroups": [{"filters": [
                {"propertyName": _MODIFIED, "operator": "GTE", "value": str(lower)},
                {"propertyName": _MODIFIED, "operator": "LT", "value": str(upper)},
            ]}],
            "sorts": [{"propertyName": "hs_object_id", "direction": "ASCENDING"}],
            "properties": list(_FIELDS),
            "limit": _PAGE_SIZE,
        }
        if offset:
            body["after"] = str(offset)
        # Search has a five-requests-per-second account limit. Coordinate
        # concurrent callers of this instance and keep upstream 429s visible.
        with self._search_lock:
            delay = self._search_at - time.monotonic()
            if delay > 0:
                time.sleep(delay)
            self._search_at = time.monotonic() + _SEARCH_INTERVAL
            return self._http.request("POST", _SEARCH, json=body)

    def _opportunity(self, row: Any) -> CRMOpportunity:
        if not isinstance(row, dict) or row.get("archived") is True or not isinstance(row.get("properties"), dict):
            raise CRMError("hubspot_invalid_response")
        record_id = _object_id(row.get("id"))
        properties = row["properties"]
        stage = properties.get("dealstage")
        if not isinstance(stage, str) or not _STAGE.fullmatch(stage):
            raise CRMError("hubspot_invalid_response")
        updated_at = _updated_at(properties.get(_MODIFIED))
        if stage not in self._won_stages:
            return CRMOpportunity(record_id, updated_at, False)
        title = properties.get("dealname")
        amount = properties.get("amount")
        currency = properties.get("deal_currency_code")
        if ((title is not None and (not isinstance(title, str) or len(title) > 4096))
                or (amount is not None and (not isinstance(amount, str) or len(amount) > 80))
                or (currency not in (None, "") and (not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)))):
            raise CRMError("hubspot_invalid_response")
        return CRMOpportunity(record_id, updated_at, True, title=title, amount=amount, currency=currency or self._currency)

    def _with_companies(self, records: list[CRMOpportunity]) -> Iterable[CRMOpportunity]:
        for index in range(0, len(records), _PAGE_SIZE):
            batch = records[index:index + _PAGE_SIZE]
            ids = {record.record_id for record in batch if record.won}
            companies = self._companies(ids) if ids else {}
            for record in batch:
                yield replace(record, account_id=companies.get(record.record_id)) if record.won else record

    def _companies(self, ids: set[str]) -> dict[str, str | None]:
        page = self._http.request("POST", _COMPANIES, json={"inputs": [{"id": value} for value in sorted(ids)]})
        results = page.get("results")
        if (page.get("status") != "COMPLETE" or page.get("numErrors") not in (None, 0)
                or page.get("errors") or not isinstance(results, list) or len(results) > len(ids)):
            raise CRMError("hubspot_invalid_associations")
        companies = {}
        for row in results:
            if not isinstance(row, dict) or not isinstance(row.get("from"), dict):
                raise CRMError("hubspot_invalid_associations")
            record_id = _object_id(row["from"].get("id"))
            if record_id not in ids or record_id in companies:
                raise CRMError("hubspot_invalid_associations")
            companies[record_id] = _company_id(row)
        return companies
