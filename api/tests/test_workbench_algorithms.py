from __future__ import annotations

import copy
import json

import httpx
import pytest

from app.workbench.adapters import (
    AdapterError, EventCheckpoint, Odoo19Adapter, OdooPartner, PartnerCursor, PartnerPage,
    normalize_opportunity_event, sync_partner_page,
)
from app.workbench.algorithms import ModelRoute, RouteObservation, RoutePolicy, match_customer, review_priority, select_route


def _event(event_id: str = "won-1", revision: int = 1, **changes) -> dict:
    data = {"title": "Renewal", "customer_name": "Atelier Acme", "customer_ref": "CRM-ACME",
            "amount_minor": 100_000, "currency": "EUR", "record_version": revision}
    return {"specversion": "1.0", "source": "crm-sim", "id": event_id, "type": "crm.opportunity.won",
            "subject": "opp-1", "data": {**data, **changes}}


def test_normalization_strict_fields_time_and_scoped_identity() -> None:
    raw = {**_event(), "time": "2026-11-05T16:12:00+01:00", "datacontenttype": "application/json"}
    event = normalize_opportunity_event(raw, tenant="tenant-1")
    assert event.key == ("tenant-1", "crm-sim", "won-1")
    assert event.time == "2026-11-05T15:12:00Z"
    assert event.fingerprint == normalize_opportunity_event(event.to_dict(), tenant="tenant-1").fingerprint
    with pytest.raises(ValueError, match="unsupported opportunity event"):
        normalize_opportunity_event({**raw, "tenant": "other"})
    with pytest.raises(ValueError, match="unsupported opportunity data"):
        normalize_opportunity_event(_event(sensitivity="C1"))
    with pytest.raises(ValueError, match="invalid amount"):
        normalize_opportunity_event(_event(amount_minor=True))
    with pytest.raises(ValueError, match="invalid event time"):
        normalize_opportunity_event({**raw, "time": "2026-11-05T16:12:00"})


def test_checkpoint_duplicate_conflict_out_of_order_and_scope() -> None:
    checkpoint = EventCheckpoint()
    current = normalize_opportunity_event(_event("rev-2", 2))
    assert checkpoint.observe(current) == "accepted"
    assert checkpoint.observe(current) == "duplicate"
    assert checkpoint.observe(normalize_opportunity_event(_event("rev-2", 2, amount_minor=300))) == "conflict"
    assert checkpoint.observe(normalize_opportunity_event(_event("rev-1", 1))) == "stale"
    assert checkpoint.observe(normalize_opportunity_event(_event("replay-rev-2", 2))) == "duplicate"
    assert checkpoint.observe(normalize_opportunity_event(_event("other-rev-2", 2, amount_minor=300))) == "conflict"
    assert checkpoint.observe(normalize_opportunity_event(_event("rev-1", 1), tenant="another")) == "accepted"


def test_matching_restricts_tenant_and_allowed_ids_before_scoring() -> None:
    candidates = [
        {"tenant": "other", "id": "other-tenant", "name": "Atelier Acme", "reference": "CRM-ACME"},
        {"tenant": "t1", "id": "forbidden", "name": "Atelier Acme", "reference": "CRM-ACME"},
        {"tenant": "t1", "id": "visible", "name": "Atelier Acmé", "reference": "CRM-VISIBLE"},
    ]
    options = {"tenant": "t1", "allowed_ids": {"visible", "other-tenant"}}
    by_name = match_customer({"customer_name": "atelier acme"}, candidates, **options)
    assert by_name.status == "matched" and by_name.candidate_id == "visible"
    assert by_name.candidates == (("visible", 1.0),)
    # An inaccessible ref cannot be silently remapped to a similarly named record.
    by_ref = match_customer({"customer_name": "Atelier Acme", "customer_ref": "CRM-ACME"}, candidates, **options)
    assert by_ref.status == "missing" and by_ref.candidates == ()


def test_matching_abstains_on_duplicate_or_uncertain_candidates() -> None:
    rows = [{"tenant": "t", "id": key, "name": "Atelier Acme", "reference": "CRM-ACME"} for key in ("b", "a")]
    options = {"tenant": "t", "allowed_ids": {"a", "b"}}
    assert match_customer({"customer_ref": "CRM-ACME"}, rows, **options).status == "review"
    result = match_customer({"customer_name": "Atelier Acme"}, rows, **options)
    assert result.status == "review" and result.candidate_id is None
    assert result.candidates == (("a", 1.0), ("b", 1.0))
    assert match_customer({"customer_name": "Atelier Acm"}, rows[:1], **options).status == "review"


def test_review_priority_ages_actions_without_changing_case_kind() -> None:
    assert review_priority("strategic", 0, 0) > review_priority("action", 0, 0)
    assert review_priority("action", 60, 0) > review_priority("strategic", 0, 0)
    assert review_priority("action", -1, 10**9) < review_priority("action", 60, 0)


def test_routing_hard_constraints_precede_observed_price_and_quality() -> None:
    cloud = ModelRoute("cloud", "cloud", "eu", 0.001, 10, 1)
    local = ModelRoute("local", "local", "eu", 0.012, 200, 0.93)
    foreign = ModelRoute("foreign", "local", "us", 0.0, 0, 1)
    policy = RoutePolicy("C3", frozenset({"eu"}), 0.02, 300, 0.8)
    assert select_route(policy, (foreign, cloud, local)).model_id == "local"
    assert select_route(policy, (cloud, foreign)).reason == "no_eligible_model"
    assert select_route(policy, (cloud, local), (RouteObservation("local", 5, 0.03, 100, 1),)).model_id is None
    assert select_route(policy, (cloud, local), (RouteObservation("local", 4, 0.03, 100, 1),)).model_id == "local"
    assert select_route(RoutePolicy("C3", frozenset({"eu"}), 0.001, 300), (local,),
                        (RouteObservation("local", 50, 0.0, 1, 1),)).model_id is None


def test_routing_deterministic_tie_and_invalid_input() -> None:
    with pytest.raises(ValueError):
        RoutePolicy("public", frozenset({"eu"}), 1, 1000)
    with pytest.raises(ValueError):
        RoutePolicy("C1", frozenset({"eu"}), float("nan"), 1000)
    policy = RoutePolicy("C1", frozenset({"eu"}), 1, 1000)
    rows = [ModelRoute(key, "cloud", "eu", 0, 10, 1) for key in ("z", "a")]
    rows.append(ModelRoute("unknown", "unknown", "eu", 0, 0, 1))
    assert select_route(policy, rows).model_id == select_route(policy, reversed(rows)).model_id == "a"


def test_odoo_requests_only_search_read_with_order_and_cursor() -> None:
    requests: list[httpx.Request] = []
    earlier = PartnerCursor("2026-11-01 13:00:00", 3)

    def provider(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(200, json=[{"id": 4, "name": "Test partner", "ref": False,
                                           "write_date": earlier.write_date}])

    with httpx.Client(transport=httpx.MockTransport(provider)) as client:
        adapter = Odoo19Adapter("https://erp.example.test", "demo-erp", "fixture-only-token", client=client)
        page = adapter.partners_page(earlier, limit=2)
    assert adapter.capabilities.incremental_reads and not adapter.capabilities.draft_writes
    assert page.records == (OdooPartner(4, "Test partner", None, earlier.write_date),)
    assert page.checkpoint == PartnerCursor(earlier.write_date, 4)
    assert page.checkpoint.overlap(2) == PartnerCursor("2026-11-01 12:59:58", 0)
    request = requests[0]
    assert request.method == "POST" and request.url.path == "/json/2/res.partner/search_read"
    assert request.headers["X-Odoo-Database"] == "demo-erp"
    assert request.headers["Authorization"] == "Bearer fixture-only-token"
    assert json.loads(request.content) == {
        "domain": ["|", ["write_date", ">", earlier.write_date], "&", ["write_date", "=", earlier.write_date], ["id", ">", 3]],
        "fields": ["id", "name", "ref", "write_date"], "order": "write_date asc, id asc", "limit": 2,
    }


@pytest.mark.parametrize("url", ["http://erp.example.test", "https://user:pass@erp.example.test", "https://erp.example.test/path",
                                  "https://erp.example.test?ignored=1", "https://erp.example.test:bad", "https://erp\n.example.test"])
def test_odoo_rejects_non_explicit_https_origin(url: str) -> None:
    with pytest.raises(ValueError):
        Odoo19Adapter(url, "demo-erp", "fixture-only-token")


def test_odoo_does_not_follow_redirects_or_include_provider_message() -> None:
    requests: list[httpx.Request] = []

    def redirect(request: httpx.Request) -> httpx.Response:
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://foreign.example.test/"}, json={"message": "private provider details"})

    with httpx.Client(transport=httpx.MockTransport(redirect), follow_redirects=True) as client:
        adapter = Odoo19Adapter("https://erp.example.test", "demo-erp", "fixture-only-token", client=client)
        with pytest.raises(AdapterError, match=r"^Odoo read failed \(HTTP 307\)$"):
            adapter.partners_page()
    assert len(requests) == 1


def test_odoo_malformed_or_nonadvancing_page_fails_closed() -> None:
    row = {"id": 1, "name": "Fixture", "ref": None, "write_date": "2026-11-01 13:00:00"}
    for rows in ([row, copy.deepcopy(row)], [{**row, "write_date": "invalid"}], [{**row, "id": True}]):
        with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, json=rows))) as client:
            adapter = Odoo19Adapter("https://erp.example.test", "demo-erp", "fixture-only-token", client=client)
            with pytest.raises(AdapterError, match="invalid or unordered"):
                adapter.partners_page()


def test_odoo_page_bytes_are_bounded_before_json_parsing() -> None:
    response = httpx.Response(200, content=b" " * (512 * 1024 + 1))
    with httpx.Client(transport=httpx.MockTransport(lambda _: response)) as client:
        adapter = Odoo19Adapter("https://erp.example.test", "demo-erp", "fixture-only-token", client=client)
        with pytest.raises(AdapterError, match="page exceeds size limit"):
            adapter.partners_page()


def test_checkpoint_advances_only_after_complete_page() -> None:
    rows = tuple(OdooPartner(i, f"Partner {i}", None, "2026-11-01 13:00:00") for i in (1, 2, 3))
    checkpoints: list[PartnerCursor] = []
    delivered: set[int] = set()

    class Source:
        def partners_page(self, after, *, limit):
            assert after is None and limit == 100
            return PartnerPage(rows, rows[-1].cursor)

    def apply_then_fail(record: OdooPartner) -> None:
        if record.record_id == 2:
            raise RuntimeError("consumer interruption")
        delivered.add(record.record_id)

    with pytest.raises(RuntimeError):
        sync_partner_page(Source(), None, apply_then_fail, checkpoints.append)
    assert checkpoints == [] and delivered == {1}
    sync_partner_page(Source(), None, lambda row: delivered.add(row.record_id), checkpoints.append)
    assert checkpoints == [rows[-1].cursor] and delivered == {1, 2, 3}


def test_inconsistent_checkpoint_refuses_delivery() -> None:
    row = OdooPartner(2, "Partner", None, "2026-11-01 13:00:00")
    seen: list[object] = []

    class BrokenSource:
        def partners_page(self, after, *, limit):
            return PartnerPage((row,), None)

    with pytest.raises(AdapterError, match="inconsistent checkpoint"):
        sync_partner_page(BrokenSource(), None, seen.append, seen.append)
    assert seen == []


def test_replay_is_reproducible_and_exercises_real_workbench() -> None:
    from app.workbench.replay import run_benchmark

    report = run_benchmark()
    assert report == run_benchmark()
    assert report["source"] == "synthetic"
    assert report["summary"] == {
        "scenarios": 36, "governed_passed": 36, "rules_only_passed": 12,
        "routing_passed": 3, "routing_scenarios": 3,
    }
    assert len({case["id"] for case in report["cases"]}) == 36
    for case in report["cases"]:
        assert case["governed"]["drafts"] == case["expected_drafts"]
        if case["scenario"] == "lost_receipt":
            assert case["governed"]["protections"] == ["reconciled_after_restart"]
