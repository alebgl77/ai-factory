"""Read changed Salesforce opportunities through the REST Query API."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import datetime, timedelta
from decimal import Decimal
from typing import Any

import httpx

from .crm import CRMError, CRMHTTP, CRMOpportunity, crm_time, source_id, validate_window

_API_VERSION = re.compile(r"[1-9][0-9]{0,2}\.[0-9]")
_RECORD_ID = re.compile(r"[A-Za-z0-9]{15}(?:[A-Za-z0-9]{3})?")
_CURRENCY = re.compile(r"[A-Z]{3}")
_MAX_PAGES = 128
_QUERY_OPTIONS = {"Sforce-Query-Options": "batchSize=200"}


def _record_id(value: object) -> str:
    if not isinstance(value, str) or not _RECORD_ID.fullmatch(value):
        raise CRMError("salesforce_invalid_response")
    return value


class SalesforceCRM:
    """Poll one Salesforce org with an access token issued for its instance URL.

    Set default_currency for a single-currency org, where CurrencyIsoCode
    is unavailable. Otherwise the native opportunity currency is read.
    Missing proposal fields are passed through for Workbench review.
    """

    def __init__(
        self,
        instance_url: str,
        token: str,
        *,
        instance: str,
        api_version: str = "61.0",
        default_currency: str | None = None,
        client: httpx.Client | None = None,
    ) -> None:
        if not isinstance(api_version, str) or not _API_VERSION.fullmatch(api_version):
            raise CRMError("salesforce_invalid_api_version")
        if default_currency is not None and (
            not isinstance(default_currency, str) or not _CURRENCY.fullmatch(default_currency)
        ):
            raise CRMError("salesforce_invalid_currency")
        self.source = source_id("salesforce", instance)
        self._query_path = f"/services/data/v{api_version}/query"
        self._cursor = re.compile(re.escape(self._query_path) + r"/[A-Za-z0-9_-]{1,256}")
        self._currency = default_currency
        self._http = CRMHTTP(instance_url, token, client=client)

    def close(self) -> None:
        self._http.close()

    def opportunities(self, start: datetime, end: datetime) -> Iterable[CRMOpportunity]:
        start, end = validate_window(start, end)
        # SOQL's documented dateTime literals have second precision. Fetch
        # the surrounding seconds and apply exact bounds to returned records.
        query_start = start.replace(microsecond=0)
        query_end = end.replace(microsecond=0)
        if end.microsecond:
            query_end += timedelta(seconds=1)
        fields = "Id, Name, SystemModstamp, IsWon, Amount, AccountId, Account.Name"
        if self._currency is None:
            fields += ", CurrencyIsoCode"
        query = (
            f"SELECT {fields} FROM Opportunity "
            f"WHERE SystemModstamp >= {query_start.isoformat()} "
            f"AND SystemModstamp < {query_end.isoformat()} ORDER BY SystemModstamp ASC, Id ASC"
        )
        path = self._query_path
        params = {"q": query}
        cursors: set[str] = set()
        ids: set[str] = set()
        total = None
        previous = query_start
        for _ in range(_MAX_PAGES):
            page = self._http.request("GET", path, params=params, headers=_QUERY_OPTIONS)
            records = page.get("records")
            done = page.get("done")
            size = page.get("totalSize")
            if (not isinstance(records, list) or len(records) > 2000 or type(done) is not bool
                    or type(size) is not int or size < 0 or (total is not None and size != total)):
                raise CRMError("salesforce_invalid_response")
            total = size
            for row in records:
                record = self._opportunity(row)
                if (not previous <= record.updated_at < query_end
                        or record.record_id in ids or len(ids) >= total):
                    raise CRMError("salesforce_invalid_response")
                previous = record.updated_at
                ids.add(record.record_id)
                if start <= record.updated_at < end:
                    yield record
            if done:
                if len(ids) != total or page.get("nextRecordsUrl") is not None:
                    raise CRMError("salesforce_invalid_response")
                return
            path = page.get("nextRecordsUrl")
            if not isinstance(path, str) or not self._cursor.fullmatch(path) or path in cursors:
                raise CRMError("salesforce_invalid_cursor")
            cursors.add(path)
            params = None
        raise CRMError("salesforce_page_limit")

    def _opportunity(self, row: Any) -> CRMOpportunity:
        if not isinstance(row, dict) or type(row.get("IsWon")) is not bool:
            raise CRMError("salesforce_invalid_response")
        record_id = _record_id(row.get("Id"))
        try:
            updated_at = crm_time(row.get("SystemModstamp"))
        except (ValueError, TypeError, OverflowError, CRMError):
            raise CRMError("salesforce_invalid_response") from None
        if not row["IsWon"]:
            return CRMOpportunity(record_id, updated_at, False)
        account_id = row.get("AccountId")
        account = row.get("Account")
        if account_id is not None:
            account_id = _record_id(account_id)
        if account is not None and not isinstance(account, dict):
            raise CRMError("salesforce_invalid_response")
        account_name = account.get("Name") if account and account_id else None
        title = row.get("Name")
        amount = row.get("Amount")
        currency = self._currency if self._currency is not None else row.get("CurrencyIsoCode")
        if ((title is not None and (not isinstance(title, str) or len(title) > 120))
                or (account_name is not None and (not isinstance(account_name, str) or len(account_name) > 255))
                or (amount is not None and type(amount) not in {int, str, Decimal})
                or (currency is not None and (not isinstance(currency, str) or not _CURRENCY.fullmatch(currency)))):
            raise CRMError("salesforce_invalid_response")
        return CRMOpportunity(record_id, updated_at, True, title, account_id, account_name, amount, currency)
