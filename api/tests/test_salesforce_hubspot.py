from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from decimal import Decimal

import httpx
import pytest

from ai_factory_workbench import hubspot
from ai_factory_workbench.crm import CRMError
from ai_factory_workbench.hubspot import HubSpotCRM
from ai_factory_workbench.salesforce import SalesforceCRM

_START = datetime(2026, 11, 6, 9, tzinfo=timezone.utc)
_END = _START + timedelta(seconds=10)
_SF_ORIGIN = "https://example.my.salesforce.com"
_SF_QUERY = "/services/data/v61.0/query"
_HS_SEARCH = "/crm/objects/2026-09/deals/search"
_HS_COMPANIES = "/crm/associations/2026-09/deals/companies/batch/read"
_TOKEN = "example-integration-token"
_ACCOUNT = "001000000000001AAA"


@pytest.fixture(autouse=True)
def mock_hubspot_search_interval(monkeypatch):
    monkeypatch.setattr(hubspot, "_SEARCH_INTERVAL", 0)


def _sf_row(index, stamp, *, won=True, amount=12.34, currency="EUR"):
    return {
        "Id": f"00600000000000{index}AAA",
        "SystemModstamp": stamp,
        "IsWon": won,
        "Amount": amount,
        "CurrencyIsoCode": currency,
        "Name": "New contract",
        "AccountId": _ACCOUNT,
        "Account": {"Name": "Acme"},
    }


def _hs_row(index, updated_at, *, stage="custom-won", currency="USD", amount="122.59"):
    return {
        "id": str(index),
        "updatedAt": _END.isoformat(),
        "archived": False,
        "properties": {
            "dealstage": stage, "dealname": "Support renewal", "amount": amount,
            "deal_currency_code": currency, "hs_lastmodifieddate": updated_at,
        },
    }


def _association(company, *, category="HUBSPOT_DEFINED", kind=341):
    return {"toObjectId": company, "associationTypes": [{"category": category, "typeId": kind}]}


def test_salesforce_polls_all_stages_over_multiple_pages_with_exact_boundaries():
    requests = []
    start = _START + timedelta(microseconds=250_000)
    end = _START + timedelta(microseconds=750_000)
    cursor = _SF_QUERY + "/01g000000000000-200"

    def handler(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "example.my.salesforce.com"
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        assert request.headers["Sforce-Query-Options"] == "batchSize=200"
        if request.url.path == _SF_QUERY:
            query = request.url.params["q"]
            assert "SELECT Id, Name, SystemModstamp, IsWon, Amount, AccountId, Account.Name, CurrencyIsoCode" in query
            assert "SystemModstamp >= 2026-11-06T09:00:00+00:00" in query
            assert "SystemModstamp < 2026-11-06T09:00:01+00:00" in query
            assert "IsWon =" not in query
            return httpx.Response(200, json={
                "totalSize": 4, "done": False, "nextRecordsUrl": cursor,
                "records": [_sf_row(1, "2026-11-06T09:00:00.249+0000"),
                            _sf_row(2, "2026-11-06T09:00:00.250+0000")],
            })
        assert request.url.path == cursor and not request.url.params
        return httpx.Response(200, json={
            "totalSize": 4, "done": True,
            "records": [_sf_row(3, "2026-11-06T09:00:00.250+0000", won=False, amount=None),
                        _sf_row(4, "2026-11-06T09:00:00.750+0000")],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="emea", client=client)
        records = list(connector.opportunities(start, end))
        connector.close()
        assert not client.is_closed

    assert connector.source == "salesforce/emea"
    assert len(requests) == 2
    assert [record.won for record in records] == [True, False]
    assert records[0].updated_at == start == records[1].updated_at
    assert records[0].account_id == _ACCOUNT
    assert records[0].account_name == "Acme"
    assert records[0].currency == "EUR"
    assert type(records[0].amount) is Decimal
    assert records[0].amount == Decimal("12.34")
    assert records[1].account_id is None and records[1].amount is None


def test_salesforce_single_currency_without_account_and_strict_version_configuration():
    def handler(request):
        assert request.url.path == "/services/data/v67.0/query"
        assert "CurrencyIsoCode" not in request.url.params["q"]
        row = _sf_row(1, "2026-11-06T09:00:00Z") | {"AccountId": None, "Account": None}
        row.pop("CurrencyIsoCode")
        return httpx.Response(200, json={"totalSize": 1, "done": True, "records": [row]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="jp", api_version="67.0",
                                  default_currency="JPY", client=client)
        record, = connector.opportunities(_START, _END)
        assert record.currency == "JPY" and record.account_id is None
        with pytest.raises(CRMError, match="salesforce_invalid_api_version"):
            SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="jp", api_version="61.0/../../query", client=client)


@pytest.mark.parametrize("cursor", [
    "https://attacker.example/services/data/v61.0/query/01g",
    "//attacker.example/services/data/v61.0/query/01g",
    _SF_QUERY + "/01g%2F..",
    _SF_QUERY + "/01g?access_token=provider-secret",
    "/services/data/v62.0/query/01g",
])
def test_salesforce_rejects_hostile_pagination_without_second_request(cursor):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"done": False, "totalSize": 1, "records": [], "nextRecordsUrl": cursor})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        connector = SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="emea", client=client)
        with pytest.raises(CRMError, match="^salesforce_invalid_cursor$"):
            list(connector.opportunities(_START, _END))
    assert len(requests) == 1


def test_salesforce_rejects_missing_status_and_incomplete_page():
    def handler(request):
        row = _sf_row(1, "2026-11-06T09:00:00Z", won="false")
        return httpx.Response(200, json={"done": True, "totalSize": 2, "records": [row]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="emea", client=client)
        with pytest.raises(CRMError, match="^salesforce_invalid_response$"):
            list(connector.opportunities(_START, _END))


def test_hubspot_paging_exact_won_stages_and_company_batch():
    requests = []
    stamp = "2026-11-06T09:00:00.001Z"
    stamp_ms = str(int(_START.timestamp() * 1000) + 1)

    def handler(request):
        requests.append(request)
        assert request.method == "POST"
        assert request.url.host == "api.hubapi.com"
        assert request.headers["Authorization"] == f"Bearer {_TOKEN}"
        body = json.loads(request.content)
        if request.url.path == _HS_SEARCH:
            assert body["filterGroups"] == [{"filters": [
                {"propertyName": "hs_lastmodifieddate", "operator": "GTE", "value": stamp_ms},
                {"propertyName": "hs_lastmodifieddate", "operator": "LT",
                 "value": str(int(_END.timestamp() * 1000))},
            ]}]
            assert body["sorts"] == [{"propertyName": "hs_object_id", "direction": "ASCENDING"}]
            if "after" not in body:
                return httpx.Response(200, json={
                    "total": 5, "results": [_hs_row(1, stamp), _hs_row(2, stamp, stage="reopened", amount=None)],
                    "paging": {"next": {"after": "2", "link": "https://attacker.example/never-follow"}},
                })
            assert body["after"] == "2"
            return httpx.Response(200, json={
                "total": 5, "results": [_hs_row(3, stamp_ms, currency=None), _hs_row(4, stamp), _hs_row(5, stamp)],
            })
        assert request.url.path == _HS_COMPANIES
        assert body["inputs"] == [{"id": value} for value in ("1", "3", "4", "5")]
        return httpx.Response(200, json={"status": "COMPLETE", "results": [
            {"from": {"id": "1"}, "to": [_association(91, category="USER_DEFINED", kind=5),
                                        _association("92", kind=5)]},
            {"from": {"id": "3"}, "to": [_association("93")]},
            {"from": {"id": "4"}, "to": [_association("94"), _association("95")]},
        ]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = HubSpotCRM(_TOKEN, instance="main-portal", won_stages=["custom-won"],
                               default_currency="EUR", client=client)
        records = list(connector.opportunities(_START + timedelta(microseconds=250), _END))
        connector.close()
        assert not client.is_closed

    assert connector.source == "hubspot/main-portal"
    assert [record.record_id for record in records] == ["1", "2", "3", "4", "5"]
    assert [record.account_id for record in records] == ["92", None, "93", None, None]
    assert [record.won for record in records] == [True, False, True, True, True]
    assert [record.currency for record in records] == ["USD", None, "EUR", "USD", "USD"]
    assert all(record.updated_at == _START + timedelta(milliseconds=1) for record in records)
    assert records[0].amount == "122.59"
    assert len(requests) == 3


def test_hubspot_search_splits_saturated_windows_and_fails_closed_at_one_millisecond(monkeypatch):
    monkeypatch.setattr(hubspot, "_RESULT_LIMIT", 3)
    base = int(_START.timestamp() * 1000)
    windows = []

    def handler(request):
        assert request.url.path == _HS_SEARCH
        filters = json.loads(request.content)["filterGroups"][0]["filters"]
        lower, upper = (int(item["value"]) for item in filters)
        windows.append((lower, upper))
        if upper - lower > 2 or lower == base + 4:
            return httpx.Response(200, json={"total": 3, "results": []})
        index = lower - base + 1
        return httpx.Response(200, json={
            "total": 1, "results": [_hs_row(index, str(lower), stage="reopened")],
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = HubSpotCRM(_TOKEN, instance="portal", won_stages=["custom-won"], client=client)
        records = list(connector.opportunities(_START, _START + timedelta(milliseconds=4)))
        assert [record.record_id for record in records] == ["1", "3"]
        assert windows == [(base, base + 4), (base, base + 2), (base + 2, base + 4)]
        with pytest.raises(CRMError, match="^hubspot_search_window_limit$"):
            list(connector.opportunities(_START + timedelta(milliseconds=4), _START + timedelta(milliseconds=5)))


@pytest.mark.parametrize(("paging", "code"), [
    ({"next": {"after": "invalid"}}, "hubspot_invalid_cursor"),
    ({"next": {"after": 9999}}, "hubspot_invalid_cursor"),
    ({}, "hubspot_search_changed"),
])
def test_hubspot_rejects_unusable_search_pagination(paging, code):
    def handler(request):
        assert request.url.path == _HS_SEARCH
        return httpx.Response(200, json={
            "total": 2, "results": [_hs_row(1, "2026-11-06T09:00:00Z")], "paging": paging,
        })

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = HubSpotCRM(_TOKEN, instance="portal", won_stages=["custom-won"], client=client)
        with pytest.raises(CRMError, match=f"^{code}$"):
            list(connector.opportunities(_START, _END))


def test_hubspot_partial_association_response_cannot_produce_a_writable_company():
    def handler(request):
        if request.url.path == _HS_SEARCH:
            return httpx.Response(200, json={"total": 1, "results": [_hs_row(1, "2026-11-06T09:00:00Z")]})
        assert request.url.path == _HS_COMPANIES
        return httpx.Response(207, json={"status": "COMPLETE", "results": [], "numErrors": 1,
                                         "errors": [{"message": "provider-details"}]})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = HubSpotCRM(_TOKEN, instance="portal", won_stages=["custom-won"], client=client)
        with pytest.raises(CRMError, match="^hubspot_invalid_associations$"):
            list(connector.opportunities(_START, _END))


@pytest.mark.parametrize("provider", ["salesforce", "hubspot"])
def test_incomplete_won_opportunity_preserves_other_reopened_changes(provider):
    stamp = "2026-11-06T09:00:00Z"

    def handler(request):
        if provider == "salesforce":
            assert request.url.path == _SF_QUERY
            incomplete = _sf_row(1, stamp, amount=None, currency=None) | {
                "Name": None, "AccountId": None, "Account": None,
            }
            return httpx.Response(200, json={
                "totalSize": 2, "done": True, "records": [incomplete, _sf_row(2, stamp, won=False)],
            })
        if request.url.path == _HS_SEARCH:
            incomplete = _hs_row(1, stamp, amount=None, currency=None)
            incomplete["properties"]["dealname"] = None
            return httpx.Response(200, json={
                "total": 2, "results": [incomplete, _hs_row(2, stamp, stage="reopened")],
            })
        assert request.url.path == _HS_COMPANIES
        assert json.loads(request.content)["inputs"] == [{"id": "1"}]
        return httpx.Response(200, json={"status": "COMPLETE", "results": []})

    with httpx.Client(transport=httpx.MockTransport(handler)) as client:
        connector = (SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="org", client=client) if provider == "salesforce"
                     else HubSpotCRM(_TOKEN, instance="portal", won_stages=["custom-won"], client=client))
        incomplete, reopened = connector.opportunities(_START, _END)

    assert incomplete.won and not reopened.won
    assert incomplete.updated_at == reopened.updated_at == _START
    assert incomplete.record_id != reopened.record_id
    assert incomplete.amount is incomplete.currency is incomplete.title is incomplete.account_id is None


@pytest.mark.parametrize(("status", "code"), [
    (302, "crm_http_error"),
    (401, "crm_authentication_failed"),
    (429, "crm_rate_limited"),
])
@pytest.mark.parametrize("provider", ["salesforce", "hubspot"])
def test_crm_provider_errors_redact_credentials_and_never_follow_redirect(provider, status, code):
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(status, headers={"Location": "https://attacker.example"},
                              json={"error": f"server echoed {_TOKEN}"})

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        connector = (SalesforceCRM(_SF_ORIGIN, _TOKEN, instance="org", client=client) if provider == "salesforce"
                     else HubSpotCRM(_TOKEN, instance="portal", won_stages=["custom-won"], client=client))
        with pytest.raises(CRMError, match=f"^{code}$") as error:
            list(connector.opportunities(_START, _END))
    assert error.value.status_code == status
    assert _TOKEN not in repr(error.value)
    assert len(requests) == 1
