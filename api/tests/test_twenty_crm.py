from datetime import datetime, timezone
from decimal import Decimal, localcontext

import httpx
import pytest

from ai_factory_workbench.crm import CRMError
from ai_factory_workbench.twenty import TwentyCRM


START = datetime(2026, 9, 1, 12, 0, tzinfo=timezone.utc)
END = datetime(2026, 9, 1, 12, 1, tzinfo=timezone.utc)
IDS = tuple(f"00000000-0000-0000-0000-{index:012d}" for index in range(1, 5))


def _record(record_id=IDS[0], *, stage="VALIDATED", updated="2026-09-01T12:00:30.001Z", **changes):
    return {
        "id": record_id, "updatedAt": updated, "stage": stage, "name": "Offer A", "companyId": IDS[3],
        "amount": {"amountMicros": "2501250000", "currencyCode": "EUR"}, **changes,
    }


def _page(records, *, cursor=None):
    return {"data": {"opportunities": records}, "pageInfo": {"hasNextPage": cursor is not None, "endCursor": cursor}}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_twenty_pages_by_uuid_and_maps_stage_company_and_exact_micros():
    requests = []

    def respond(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.url.host == "api.twenty.com"
        assert request.url.path == "/rest/opportunities"
        assert request.headers["authorization"] == "Bearer test-key"
        assert request.url.params["order_by"] == "id[AscNullsLast]"
        assert request.url.params["depth"] == "0"
        assert "company" in request.url.params["fields"].split(",")
        assert request.url.params["filter"] == (
            "and(updatedAt[gte]:2026-09-01T12:00:00.000Z,updatedAt[lt]:2026-09-01T12:01:00.000Z)"
        )
        if len(requests) == 1:
            assert "starting_after" not in request.url.params
            return httpx.Response(200, json=_page([_record()], cursor="eyJpZCI6IjEifQ=="))
        assert request.url.params["starting_after"] == "eyJpZCI6IjEifQ=="
        return httpx.Response(200, json=_page([_record(IDS[1], stage="NEGOTIATING", updated="2026-09-01T12:00:05Z")]))

    with _client(respond) as client:
        source = TwentyCRM("https://api.twenty.com", "test-key", instance="org-a", won_stages=["VALIDATED"], client=client)
        with localcontext() as context:
            context.prec = 3
            won, reopened = source.opportunities(START, END)
        source.close()
        assert not client.is_closed
    assert source.source == "twenty/org-a"
    assert len(requests) == 2
    assert (won.record_id, won.won, won.account_id, won.amount, won.currency) == (
        IDS[0], True, IDS[3], Decimal("2501.25"), "EUR",
    )
    assert (reopened.record_id, reopened.won, reopened.amount) == (IDS[1], False, None)


def test_twenty_half_open_window_and_explicit_fallback_currency():
    start = START.replace(microsecond=500)
    end = START.replace(microsecond=2001)

    def respond(request):
        assert "updatedAt[lt]:2026-09-01T12:00:00.003Z" in request.url.params["filter"]
        return httpx.Response(200, json=_page([
            _record(updated="2026-09-01T12:00:00.000Z"),
            _record(IDS[1], updated="2026-09-01T12:00:00.001Z", companyId=None,
                    amount={"amountMicros": 9000000, "currencyCode": ""}),
            _record(IDS[2], updated="2026-09-01T12:00:00.002001Z"),
        ]))

    with _client(respond) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"],
                           default_currency="JPY", client=client)
        opportunity, = source.opportunities(start, end)
    assert (opportunity.record_id, opportunity.account_id, opportunity.amount, opportunity.currency) == (
        IDS[1], None, Decimal("9"), "JPY",
    )


def test_twenty_rejects_hostile_and_repeated_paging():
    requests = []

    def respond(request):
        requests.append(request)
        return httpx.Response(200, json=_page([_record()], cursor="https://evil.example/capture"))

    with _client(respond) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"], client=client)
        with pytest.raises(CRMError, match="^invalid_twenty_cursor$"):
            list(source.opportunities(START, END))
    assert len(requests) == 1

    with _client(lambda request: httpx.Response(200, json=_page([_record()], cursor="same-cursor"))) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"], client=client)
        with pytest.raises(CRMError, match="^invalid_twenty_page_order$"):
            list(source.opportunities(START, END))


def test_twenty_passes_incomplete_won_data_without_losing_a_reopened_record():
    first = _record(amount=None, companyId=None)
    second = _record(IDS[1], amount={"amountMicros": "9000000", "currencyCode": None})
    second.pop("companyId")
    third = _record(IDS[2], stage="NEGOTIATING", amount=None)
    with _client(lambda request: httpx.Response(200, json=_page([first, second, third]))) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"], client=client)
        missing_amount, missing_currency, reopened = source.opportunities(START, END)
    assert (missing_amount.won, missing_amount.amount, missing_amount.currency, missing_amount.account_id) == (
        True, None, None, None,
    )
    assert (missing_currency.won, missing_currency.amount, missing_currency.currency, missing_currency.account_id) == (
        True, Decimal("9"), None, None,
    )
    assert (reopened.record_id, reopened.won) == (IDS[2], False)


@pytest.mark.parametrize(("status", "code"), [(401, "crm_authentication_failed"), (429, "crm_rate_limited")])
def test_twenty_errors_do_not_echo_provider_body_or_credential(status, code):
    with _client(lambda request: httpx.Response(status, json={"private": "test-key"})) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"], client=client)
        with pytest.raises(CRMError, match=f"^{code}$") as caught:
            list(source.opportunities(START, END))
        assert caught.value.status_code == status


def test_twenty_rejects_disagreeing_company_relationship():
    record = _record(company={"id": IDS[1], "name": "Unrelated account"})
    with _client(lambda request: httpx.Response(200, json=_page([record]))) as client:
        source = TwentyCRM("https://crm.example", "test-key", instance="self", won_stages=["VALIDATED"], client=client)
        with pytest.raises(CRMError, match="^invalid_twenty_record$"):
            list(source.opportunities(START, END))
