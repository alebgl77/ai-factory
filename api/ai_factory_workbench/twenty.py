"""Read opportunities from the standard Twenty REST workspace schema."""

from __future__ import annotations

import re
from collections.abc import Iterator, Sequence
from datetime import datetime, timedelta
from decimal import Decimal

import httpx

from .crm import CRMError, CRMHTTP, CRMOpportunity, crm_time, source_id, validate_window

_UUID = re.compile(r"[0-9a-f]{8}(?:-[0-9a-f]{4}){3}-[0-9a-f]{12}", re.ASCII)
_STAGE = re.compile(r"[A-Za-z0-9_-]{1,128}", re.ASCII)
_CURRENCY = re.compile(r"[A-Z]{3}", re.ASCII)
_MICROS = re.compile(r"-?[0-9]{1,20}", re.ASCII)
_CURSOR = re.compile(r"[A-Za-z0-9_+/-]{1,512}={0,2}", re.ASCII)
_PAGE_SIZE = 100
_MAX_PAGES = 100


def _uuid(value: object) -> str:
    if not isinstance(value, str) or not _UUID.fullmatch(value):
        raise CRMError("invalid_twenty_record")
    return value


def _milliseconds(value: datetime) -> datetime:
    return value.replace(microsecond=value.microsecond // 1000 * 1000)


def _format(value: datetime) -> str:
    return value.isoformat(timespec="milliseconds").replace("+00:00", "Z")


class TwentyCRM:
    def __init__(self, base_url: str, token: str, *, instance: str, won_stages: Sequence[str],
                 default_currency: str | None = None, client: httpx.Client | None = None) -> None:
        self.source = source_id("twenty", instance)
        if (not isinstance(won_stages, Sequence) or isinstance(won_stages, str)
                or not 1 <= len(won_stages) <= 50
                or any(not isinstance(stage, str) or not _STAGE.fullmatch(stage) for stage in won_stages)
                or len(set(won_stages)) != len(won_stages)):
            raise CRMError("invalid_twenty_won_stages")
        if default_currency is not None and (
            not isinstance(default_currency, str) or not _CURRENCY.fullmatch(default_currency)
        ):
            raise CRMError("invalid_twenty_currency")
        self._won_stages = frozenset(won_stages)
        self._default_currency = default_currency
        self._http = CRMHTTP(base_url, token, client=client)

    def close(self) -> None:
        self._http.close()

    def opportunities(self, start: datetime, end: datetime) -> Iterator[CRMOpportunity]:
        start, end = validate_window(start, end)
        # Twenty's date filters use millisecond buckets. Widen to full buckets,
        # then enforce the caller's exact half-open window on returned timestamps.
        lower = _milliseconds(start)
        upper = _milliseconds(end)
        if upper < end:
            upper += timedelta(milliseconds=1)
        params = {
            "filter": f"and(updatedAt[gte]:{_format(lower)},updatedAt[lt]:{_format(upper)})",
            "fields": "id,updatedAt,stage,name,company,amount",
            # A UUID cursor avoids skips on Twenty versions whose DATE_TIME
            # cursor truncates sub-millisecond database sort keys.
            "order_by": "id[AscNullsLast]",
            "depth": "0",
            "limit": str(_PAGE_SIZE),
        }
        seen_cursors: set[str] = set()
        previous_id = ""
        for _ in range(_MAX_PAGES):
            body = self._http.request("GET", "/rest/opportunities", params=params)
            data, info = body.get("data"), body.get("pageInfo")
            records = data.get("opportunities") if isinstance(data, dict) else None
            if (not isinstance(records, list) or len(records) > _PAGE_SIZE
                    or not isinstance(info, dict) or type(info.get("hasNextPage")) is not bool):
                raise CRMError("invalid_twenty_page")
            for record in records:
                if not isinstance(record, dict):
                    raise CRMError("invalid_twenty_record")
                record_id = _uuid(record.get("id"))
                if record_id <= previous_id:
                    raise CRMError("invalid_twenty_page_order")
                previous_id = record_id
                updated_at = crm_time(record.get("updatedAt"))
                if not lower <= updated_at < upper:
                    raise CRMError("invalid_twenty_page_window")
                if start <= updated_at < end:
                    yield self._opportunity(record_id, updated_at, record)
            if not info["hasNextPage"]:
                return
            cursor = info.get("endCursor")
            if (not records or not isinstance(cursor, str) or not _CURSOR.fullmatch(cursor)
                    or cursor in seen_cursors):
                raise CRMError("invalid_twenty_cursor")
            seen_cursors.add(cursor)
            params = {**params, "starting_after": cursor}
        raise CRMError("twenty_page_limit_exceeded")

    def _opportunity(self, record_id: str, updated_at: datetime, record: dict) -> CRMOpportunity:
        stage = record.get("stage")
        if ("stage" not in record or (stage is not None and (
            not isinstance(stage, str) or not _STAGE.fullmatch(stage)
        ))):
            raise CRMError("invalid_twenty_record")
        if stage not in self._won_stages:
            return CRMOpportunity(record_id, updated_at, False)

        company = record.get("company")
        if company is not None and not isinstance(company, dict):
            raise CRMError("invalid_twenty_record")
        account_id = record.get("companyId")
        if company and company.get("id") is not None:
            company_id = _uuid(company["id"])
            if account_id is not None and account_id != company_id:
                raise CRMError("invalid_twenty_record")
            account_id = company_id
        if account_id is not None:
            account_id = _uuid(account_id)

        value = record.get("amount")
        if value is not None and not isinstance(value, dict):
            raise CRMError("invalid_twenty_amount")
        micros = value.get("amountMicros") if value else None
        amount = None
        if micros is not None:
            if type(micros) is int:
                if abs(micros) >= 10**20:
                    raise CRMError("invalid_twenty_amount")
                micros = str(micros)
            if not isinstance(micros, str) or not _MICROS.fullmatch(micros):
                raise CRMError("invalid_twenty_amount")
            amount = Decimal(f"{micros}e-6")
        currency = value.get("currencyCode") if value else None
        if currency is None or currency == "":
            currency = self._default_currency
        if currency is not None and (not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)):
            raise CRMError("invalid_twenty_currency")
        return CRMOpportunity(record_id, updated_at, True, title=record.get("name"), account_id=account_id,
                              account_name=company.get("name") if company else None,
                              amount=amount, currency=currency)
