from __future__ import annotations

import sqlite3
from contextlib import closing
from dataclasses import replace
from datetime import datetime, timedelta, timezone
from decimal import Decimal, Inexact, localcontext

import httpx
import pytest

from ai_factory_workbench import Actor, Workbench, WorkbenchError
from ai_factory_workbench.crm import CRMError, CRMHTTP, CRMOpportunity, source_id, sync_opportunities
from ai_factory_workbench.profiles import get_profile

START = datetime(2026, 1, 2, 10, tzinfo=timezone.utc)
EDITOR = Actor("sync-agent", frozenset({"editor"}))
REVIEWER = Actor("reviewer", frozenset({"approver"}))
SALESFORCE = source_id("salesforce", "eu")
HUBSPOT = source_id("hubspot", "eu")
TWENTY = source_id("twenty", "eu")


def minute(value: int) -> datetime:
    return START + timedelta(minutes=value)


def won(record_id: str, offset: int, *, account_id="client", amount="123.45", currency="EUR") -> CRMOpportunity:
    return CRMOpportunity(record_id, minute(offset), True, f"{record_id} won", account_id, "Acme", amount, currency)


def customer(source: str, *, erp="erp-1", account_id="client", tenant="orders") -> dict[str, str]:
    return {"id": erp, "tenant": tenant, "name": "Acme", "reference": f"{source}/{account_id}"}


class Feed:
    def __init__(self, source: str, *rows: CRMOpportunity) -> None:
        self.source = source
        self.rows = rows
        self.windows = []

    def opportunities(self, start: datetime, end: datetime):
        self.windows.append((start, end))
        yield from self.rows


def engine(tmp_path, *, name="workbench.sqlite3", sources=(SALESFORCE,), customers=None) -> Workbench:
    return Workbench(tmp_path / name, mode="protected_demo", workspace="orders", crm_sources=sources,
                     customers=customers if customers is not None else [customer(SALESFORCE)], defer_execution=True)


def approve(wb: Workbench, case: dict) -> dict:
    return wb.decide(case["id"], REVIEWER, decision="approve", revision=case["revision"],
                     proposal_hash=case["proposal_hash"], rationale="Reviewed source and account")


def test_three_sources_same_ids_scoped_exact_aliases_and_profile(tmp_path):
    sources = (SALESFORCE, HUBSPOT, TWENTY)
    wb = engine(tmp_path, sources=sources, customers=[
        *(customer(source) for source in sources),
        customer(SALESFORCE, erp="erp-wrong", tenant="other"),
        customer(HUBSPOT, account_id="ambiguous"),
        customer(HUBSPOT, erp="erp-2", account_id="ambiguous"),
    ])
    profile = get_profile("governance").snapshot()
    feeds = [Feed(source, won("shared", 1)) for source in sources]
    feeds[1].rows += (won("ambiguous", 1, account_id="ambiguous"), won("absent", 1, account_id=None),
                      won("wrong-portal", 1, account_id="elsewhere"))
    for feed in feeds:
        sync_opportunities(wb, feed, EDITOR, since=START, until=minute(2), profile=profile)

    shared = [row for row in wb.cases(REVIEWER) if row["title"] == "shared won"]
    assert len({row["id"] for row in shared}) == 3
    assert {row["action"]["fields"]["customer_id"] for row in shared} == {"erp-1"}
    assert all(row["profile"] == profile for row in shared)
    assert all(row["action"] is None for row in wb.cases(REVIEWER) if row["title"] != "shared won")
    fake = {"specversion": "1.0", "type": "crm.opportunity.won", "source": SALESFORCE, "id": "forged",
            "subject": "shared", "data": {"title": "Forged", "customer_name": "Acme",
                                          "amount_minor": 5000, "currency": "EUR", "record_version": 1}}
    with pytest.raises(WorkbenchError, match="invalid_event"):
        wb.submit(fake, EDITOR)
    with pytest.raises(WorkbenchError, match="crm_source_not_authorized"):
        sync_opportunities(wb, Feed(source_id("hubspot", "other")), EDITOR, since=START, until=minute(2))
    with pytest.raises(WorkbenchError, match="authentication_required"):
        sync_opportunities(wb, feeds[0], Actor("anonymous", frozenset({"editor"}), False), until=minute(3))


def test_restart_checkpoint_lookback_and_stale_window(tmp_path):
    wb = engine(tmp_path)
    first = Feed(SALESFORCE, won("old", 2))
    assert sync_opportunities(wb, first, EDITOR, since=START, until=minute(4)).applied == 1
    reopened = engine(tmp_path)
    second = Feed(SALESFORCE, won("new", 5), won("old", 2))
    result = sync_opportunities(reopened, second, EDITOR, until=minute(6))
    assert (result.applied, result.replayed, result.seen) == (1, 1, 2)
    assert second.windows == [(START, minute(6))]
    with pytest.raises(CRMError, match="crm_checkpoint_not_forward"):
        sync_opportunities(reopened, second, EDITOR, until=minute(5))
    with pytest.raises(CRMError, match="crm_since_already_checkpointed"):
        sync_opportunities(reopened, second, EDITOR, since=minute(1), until=minute(7))
    assert sync_opportunities(reopened, second, EDITOR, since=START, until=minute(6)).seen == 0
    assert second.windows == [(START, minute(6))]


def test_resume_mixed_source_run_with_same_arguments(tmp_path):
    wb = engine(tmp_path, sources=(SALESFORCE, HUBSPOT))
    first = Feed(SALESFORCE, won("first", 1))
    second = Feed(HUBSPOT, won("second", 2))
    assert sync_opportunities(wb, first, EDITOR, since=START, until=minute(2)).applied == 1
    with pytest.raises(CRMError, match="crm_out_of_window"):
        sync_opportunities(wb, second, EDITOR, since=START, until=minute(2))
    second.rows = (won("second", 1),)
    assert sync_opportunities(wb, first, EDITOR, since=START, until=minute(2)).seen == 0
    assert sync_opportunities(wb, second, EDITOR, since=START, until=minute(2)).applied == 1
    assert len(first.windows) == 1


def test_sink_failure_retries_completed_observations_and_does_not_checkpoint(tmp_path, monkeypatch):
    wb = engine(tmp_path)
    feed = Feed(SALESFORCE, won("b", 2), won("a", 1))
    apply = wb._apply_crm_observation

    def intermittent(source, subject, version, event, actor, **kwargs):
        if subject == "b":
            raise WorkbenchError("temporary_sink_failure", 503)
        return apply(source, subject, version, event, actor, **kwargs)

    monkeypatch.setattr(wb, "_apply_crm_observation", intermittent)
    with pytest.raises(WorkbenchError, match="temporary_sink_failure"):
        sync_opportunities(wb, feed, EDITOR, since=START, until=minute(3))
    assert [row["title"] for row in wb.cases(REVIEWER)] == ["a won"]
    reopened = engine(tmp_path)
    with pytest.raises(CRMError, match="crm_initial_since_required"):
        sync_opportunities(reopened, feed, EDITOR, until=minute(3))
    result = sync_opportunities(reopened, feed, EDITOR, since=START, until=minute(3))
    assert (result.applied, result.replayed) == (1, 1)
    assert len(reopened.cases(REVIEWER)) == 2


def test_compare_and_set_does_not_move_concurrent_checkpoint_backwards(tmp_path):
    wb = engine(tmp_path)

    class RacingFeed(Feed):
        def opportunities(self, start, end):
            other = engine(tmp_path)
            sync_opportunities(other, Feed(SALESFORCE, won("deal", 3)), EDITOR,
                               since=START, until=minute(4))
            yield won("deal", 1)

    with pytest.raises(CRMError, match="crm_checkpoint_changed"):
        sync_opportunities(wb, RacingFeed(SALESFORCE), EDITOR, since=START, until=minute(2))
    with pytest.raises(CRMError, match="crm_checkpoint_not_forward"):
        sync_opportunities(wb, Feed(SALESFORCE), EDITOR, until=minute(3))
    assert wb.cases(REVIEWER)[0]["revision"] == 1


def test_reopen_withdraws_unclaimed_approval_and_late_won_cannot_restore(tmp_path):
    wb = engine(tmp_path)
    sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, since=START, until=minute(2))
    case = wb.cases(REVIEWER)[0]
    assert approve(wb, case)["status"] == "applying"

    result = sync_opportunities(wb, Feed(SALESFORCE, CRMOpportunity("deal", minute(3), False)),
                                EDITOR, until=minute(4))
    assert result.withdrawn == 1
    assert wb.get_case(case["id"], REVIEWER)["status"] == "withdrawn"
    assert wb.pending_execution_ids() == []
    with pytest.raises(WorkbenchError, match="no_pending_execution"):
        wb.reconcile_pending(case["id"])
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        approve(wb, case)
    replay = sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, until=minute(5))
    assert replay.stale == 1
    assert wb.store.draft_count() == 0


@pytest.mark.parametrize("change", ["reopen", "blocked", "conflict"])
def test_partial_poll_preserves_only_latest_verified_holds(tmp_path, change):
    wb = engine(tmp_path)
    original = won("deal", 1)
    sync_opportunities(wb, Feed(SALESFORCE, original), EDITOR, since=START, until=minute(2))
    case = wb.cases(REVIEWER)[0]
    approve(wb, case)

    class PartialFeed(Feed):
        def opportunities(self, start, end):
            if change == "reopen":
                yield CRMOpportunity("deal", minute(3), False)
                yield original  # A later arrival from an older revision cannot undo the withdrawal.
            elif change == "blocked":
                yield won("deal", 3, amount=None)
            else:
                yield original
                yield replace(original, amount="500")
            yield won("new", 3)
            raise CRMError("crm_rate_limited", 429)

    with pytest.raises(CRMError, match="^crm_rate_limited$") as failure:
        sync_opportunities(wb, PartialFeed(SALESFORCE), EDITOR, until=minute(4))
    assert failure.value.status_code == 429
    restarted = engine(tmp_path)
    records = restarted.cases(REVIEWER)
    assert [record["id"] for record in records] == [case["id"]]
    assert records[0]["status"] == ("withdrawn" if change == "reopen" else "waiting_review")
    assert records[0]["action"] is None
    with pytest.raises(WorkbenchError, match="no_pending_execution"):
        restarted.reconcile_pending(case["id"])
    assert restarted.store.draft_count() == 0
    result = sync_opportunities(restarted, Feed(SALESFORCE, won("new", 3)), EDITOR, until=minute(4))
    assert (result.seen, result.applied) == (1, 1)


@pytest.mark.parametrize("evidence", ["reopen", "winner", "older-conflict"])
def test_partial_poll_blocks_old_approval_until_complete_repeat(tmp_path, evidence):
    wb = engine(tmp_path)
    original = won("deal", 1)
    updated = won("deal", 4, amount="500")
    sync_opportunities(wb, Feed(SALESFORCE, original), EDITOR, since=START, until=minute(2))
    old = wb.cases(REVIEWER)[0]
    approve(wb, old)
    if evidence == "reopen":
        changes = [CRMOpportunity("deal", minute(3), False), updated]
    elif evidence == "older-conflict":
        changes = [original, updated, replace(original, amount="300")]
    else:
        changes = [updated]

    class PartialFeed(Feed):
        def opportunities(self, start, end):
            yield from changes
            raise CRMError("crm_rate_limited", 429)

    with pytest.raises(CRMError, match="^crm_rate_limited$"):
        sync_opportunities(wb, PartialFeed(SALESFORCE), EDITOR, until=minute(5))
    current = wb.get_case(old["id"], REVIEWER)
    assert current["status"] == ("withdrawn" if evidence == "reopen" else "waiting_review")
    assert current["action"] is None
    if evidence == "winner":
        assert current["can_decide"] is False
        with pytest.raises(WorkbenchError, match="^crm_sync_incomplete$") as failure:
            approve(wb, current)
        assert failure.value.status == 409
    assert wb.pending_execution_ids() == []
    with pytest.raises(WorkbenchError, match="no_pending_execution"):
        wb.reconcile_pending(old["id"])
    assert wb.store.draft_count() == 0

    result = sync_opportunities(wb, Feed(SALESFORCE, *changes), EDITOR, until=minute(5))
    assert (result.seen, result.applied) == (len(changes), 1)
    current = wb.get_case(old["id"], REVIEWER)
    assert current["revision"] > old["revision"]
    assert current["action"]["fields"]["amount_minor"] == 50000
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        approve(wb, old)


def test_complete_poll_cannot_restore_a_different_winner_at_an_incomplete_revision(tmp_path):
    wb = engine(tmp_path)
    sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, since=START, until=minute(2))
    old = wb.cases(REVIEWER)[0]
    approve(wb, old)

    class PartialFeed(Feed):
        def opportunities(self, start, end):
            yield won("deal", 3, amount="500")
            raise CRMError("crm_rate_limited", 429)

    with pytest.raises(CRMError, match="^crm_rate_limited$"):
        sync_opportunities(wb, PartialFeed(SALESFORCE), EDITOR, until=minute(4))
    result = sync_opportunities(wb, Feed(SALESFORCE, won("deal", 3, amount="700")), EDITOR, until=minute(4))
    assert (result.conflicts, result.applied) == (1, 0)
    assert wb.get_case(old["id"], REVIEWER)["action"] is None
    assert wb.pending_execution_ids() == []
    assert wb.store.draft_count() == 0


def test_unwon_without_existing_case_is_a_tombstone(tmp_path):
    wb = engine(tmp_path)
    # Two changes for one record can arrive out of timestamp order in one scan.
    result = sync_opportunities(wb, Feed(SALESFORCE, CRMOpportunity("deal", minute(2), False), won("deal", 1)),
                                EDITOR, since=START, until=minute(3))
    assert (result.seen, result.applied, result.ignored) == (2, 0, 1)
    result = sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, until=minute(4))
    assert result.stale == 1
    assert wb.cases(REVIEWER) == []


def test_crm_metadata_prevents_legacy_workspace_adoption(tmp_path):
    path = tmp_path / "state.sqlite3"
    wb = Workbench(path, workspace="orders", crm_sources=(SALESFORCE,))
    sync_opportunities(wb, Feed(SALESFORCE, CRMOpportunity("deal", minute(1), False)),
                       EDITOR, since=START, until=minute(2))
    with closing(sqlite3.connect(path)) as connection, connection:
        connection.execute("DROP TABLE approval_provenance")
        connection.execute("DROP TABLE workspace_settings")
    with pytest.raises(WorkbenchError, match="workspace_binding_required"):
        Workbench(path).cases(REVIEWER)


def test_reopen_after_claim_holds_future_retries_and_does_not_block_checkpoint(tmp_path, monkeypatch):
    wb = engine(tmp_path)
    sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, since=START, until=minute(2))
    case = wb.cases(REVIEWER)[0]
    approve(wb, case)

    def lost_ack(*_args, **_kwargs):
        raise TimeoutError

    monkeypatch.setattr(wb.store, "create_draft", lost_ack)
    with pytest.raises(WorkbenchError, match="outcome_unknown_retry_same_approval"):
        wb.reconcile_pending(case["id"])
    reopened_deal = CRMOpportunity("deal", minute(3), False)
    result = sync_opportunities(wb, Feed(SALESFORCE, reopened_deal, won("other", 3)), EDITOR, until=minute(4))
    assert (result.reconciliation_required, result.applied) == (1, 1)
    restarted = engine(tmp_path)
    assert restarted.get_case(case["id"], REVIEWER)["reconciliation_required"] is True
    assert restarted.history(case["id"], REVIEWER)["reconciliation_required"] is True
    assert restarted.pending_execution_ids() == []
    with pytest.raises(WorkbenchError, match="reconciliation_required"):
        restarted.reconcile_pending(case["id"])
    assert restarted.store.draft_count() == 0
    assert sync_opportunities(restarted, Feed(SALESFORCE, reopened_deal), EDITOR,
                              until=minute(5)).reconciliation_required == 1


@pytest.mark.parametrize(("currency", "amount", "minor"), [("EUR", "1.23", 123), ("JPY", 123, 123),
                                                          ("KWD", Decimal("1.023"), 1023)])
def test_exact_currency_minor_units(tmp_path, currency, amount, minor):
    wb = engine(tmp_path)
    sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1, amount=amount, currency=currency)),
                       EDITOR, since=START, until=minute(2))
    assert wb.cases(REVIEWER)[0]["action"]["fields"]["amount_minor"] == minor


def test_currency_conversion_does_not_inherit_decimal_context(tmp_path):
    wb = engine(tmp_path)
    with localcontext() as context:
        context.prec = context.Emax = 2
        context.Emin = -2
        context.traps[Inexact] = True
        sync_opportunities(wb, Feed(SALESFORCE, won("good", 1, amount="1000.01"),
                                    won("invalid", 1, amount="1.0001")),
                           EDITOR, since=START, until=minute(2))

    cases = {case["title"]: case for case in wb.cases(REVIEWER)}
    assert cases["good won"]["action"]["fields"]["amount_minor"] == 100001
    assert cases["CRM opportunity requires review"]["action"] is None


@pytest.mark.parametrize(("row", "code"), [(won("deal", 2), "crm_out_of_window"),
                                          (won("../deal", 1), "crm_invalid_opportunity")])
def test_unverified_identity_or_window_fails_before_applying_any_row(tmp_path, row, code):
    wb = engine(tmp_path)
    with pytest.raises(CRMError, match=code):
        sync_opportunities(wb, Feed(SALESFORCE, won("ok", 1), row), EDITOR, since=START, until=minute(2))
    assert wb.cases(REVIEWER) == []


@pytest.mark.parametrize("unusable", [won("z-blocked", 3, amount=None), won("z-blocked", 3, amount="0"),
                                      won("z-blocked", 3, amount="1.0001"), won("z-blocked", 3, amount="NaN"),
                                      won("z-blocked", 3, currency="ZZZ"),
                                      replace(won("z-blocked", 3), title="invalid\x01private-data")])
def test_blocked_won_does_not_prevent_an_independent_withdrawal(tmp_path, unusable):
    wb = engine(tmp_path)
    sync_opportunities(wb, Feed(SALESFORCE, won("deal", 1)), EDITOR, since=START, until=minute(2))
    old = wb.cases(REVIEWER)[0]
    approve(wb, old)
    result = sync_opportunities(wb, Feed(SALESFORCE, unusable, CRMOpportunity("deal", minute(3), False)),
                                EDITOR, until=minute(4))
    assert (result.withdrawn, result.blocked, result.until) == (1, 1, minute(4))
    assert wb.get_case(old["id"], REVIEWER)["status"] == "withdrawn"
    blocked = next(case for case in wb.cases(REVIEWER) if case["id"] != old["id"])
    assert blocked["action"] is None and blocked["kind"] == "undocumented"
    assert "private-data" not in str(blocked)
    assert wb.pending_execution_ids() == []
    assert wb.store.draft_count() == 0
    assert sync_opportunities(wb, Feed(SALESFORCE), EDITOR, until=minute(4)).seen == 0


@pytest.mark.parametrize("contradiction", [won("deal", 1, amount="99.00"), CRMOpportunity("deal", minute(1), False)])
def test_conflicting_equal_provider_revision_blocks_old_approval_then_accepts_newer(tmp_path, contradiction):
    wb = engine(tmp_path)
    original = won("deal", 1)
    sync_opportunities(wb, Feed(SALESFORCE, original), EDITOR, since=START, until=minute(2))
    old = wb.cases(REVIEWER)[0]
    approve(wb, old)
    result = sync_opportunities(wb, Feed(SALESFORCE, contradiction), EDITOR, until=minute(3))
    assert (result.conflicts, result.reconciliation_required) == (1, 0)
    new = wb.get_case(old["id"], REVIEWER)
    assert new["revision"] == old["revision"] + 1 and new["action"] is None
    with pytest.raises(WorkbenchError, match="no_pending_execution"):
        wb.reconcile_pending(old["id"])
    with pytest.raises(WorkbenchError, match="stale_proposal"):
        approve(wb, old)
    assert wb.store.draft_count() == 0
    reopened = engine(tmp_path)
    assert sync_opportunities(reopened, Feed(SALESFORCE, original), EDITOR, until=minute(4)).conflicts == 1
    assert reopened.get_case(old["id"], REVIEWER)["revision"] == new["revision"]
    assert sync_opportunities(reopened, Feed(SALESFORCE, won("deal", 4, amount="500")), EDITOR,
                              until=minute(5)).applied == 1
    approved = approve(reopened, reopened.get_case(old["id"], REVIEWER))
    assert approved["action"]["fields"]["amount_minor"] == 50000
    assert reopened.reconcile_pending(old["id"])["status"] == "executed"
    assert reopened.store.draft_count() == 1


def test_conflict_inside_window_does_not_block_unrelated_reopen(tmp_path):
    wb = engine(tmp_path)
    original = won("reopened", 1)
    sync_opportunities(wb, Feed(SALESFORCE, original), EDITOR, since=START, until=minute(2))
    approve(wb, wb.cases(REVIEWER)[0])
    result = sync_opportunities(wb, Feed(SALESFORCE, won("conflict", 2),
                                        won("conflict", 2, amount="99.00"),
                                        CRMOpportunity("reopened", minute(3), False), won("conflict", 2)),
                                EDITOR, until=minute(4))
    assert (result.conflicts, result.withdrawn, result.applied) == (1, 1, 0)
    assert wb.pending_execution_ids() == []
    assert all(case["action"] is None for case in wb.cases(REVIEWER))


def test_http_credentials_stay_at_configured_origin_and_errors_redact_bodies():
    seen = []

    def handler(request):
        seen.append(request)
        if request.url.path == "/redirect":
            return httpx.Response(302, headers={"Location": "https://external.example/leak"}, text="private")
        if request.url.path == "/limited":
            return httpx.Response(429, text="private")
        return httpx.Response(200, content=b'{"amount": 1.000000001}')

    with httpx.Client(transport=httpx.MockTransport(handler), follow_redirects=True) as client:
        transport = CRMHTTP("https://crm.example", "example-token", client=client)
        assert transport.request("GET", "/valid") == {"amount": Decimal("1.000000001")}
        with pytest.raises(CRMError, match="^crm_http_error$") as failure:
            transport.request("GET", "/redirect")
        assert failure.value.status_code == 302
        with pytest.raises(CRMError, match="^crm_rate_limited$"):
            transport.request("GET", "/limited")
        for path in ("//external.example/leak", "/../outside", "/%2e%2e/outside", "/safe?token=invalid"):
            with pytest.raises(CRMError, match="crm_invalid_request"):
                transport.request("GET", path)
        assert len(seen) == 3
        assert {request.url.host for request in seen} == {"crm.example"}
        assert {request.headers["Authorization"] for request in seen} == {"Bearer example-token"}


def test_http_bounds_response_and_rejects_non_https_or_unscoped_urls():
    for url in ("http://crm.example", "https://identity@crm.example", "https://crm.example/prefix"):
        with pytest.raises(CRMError, match="crm_invalid_origin"):
            CRMHTTP(url, "example-token")
    with httpx.Client(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b" " * (4 * 1024**2 + 1)))) as client:
        with pytest.raises(CRMError, match="crm_response_too_large"):
            CRMHTTP("https://crm.example", "example-token", client=client).request("GET", "/valid")
