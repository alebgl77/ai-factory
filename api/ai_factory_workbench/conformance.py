"""Provider conformance probe for an explicitly disposable, empty test target."""

from __future__ import annotations

import tempfile
from collections.abc import Callable, Mapping, Sequence
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path
from threading import Barrier
from typing import Any

from .connector import DraftConnector, DraftReadback
from .identity import Actor
from .service import Workbench
from .store import WorkbenchError


class _ConnectorProbe:
    def __init__(self, adapter: DraftConnector, *, lose_ack: bool = False, barrier: Barrier | None = None) -> None:
        self._adapter = adapter
        self._lose_ack = lose_ack
        self._barrier = barrier
        self.target = adapter.target
        self.capabilities = adapter.capabilities

    def ensure_draft(self, external_ref: str, fields: dict[str, Any]) -> None:
        self._adapter.ensure_draft(external_ref, fields)
        if self._lose_ack:
            raise TimeoutError

    def read_draft(self, external_ref: str) -> DraftReadback | None:
        result = self._adapter.read_draft(external_ref)
        if result is None and self._barrier is not None:
            self._barrier.wait(timeout=10)
        return result


def probe_draft_connector(
    connector_factory: Callable[[], DraftConnector],
    count_drafts: Callable[[], int],
    *,
    event: dict[str, Any],
    customers: Sequence[Mapping[str, str]],
    workspace: str,
    disposable: bool,
) -> dict[str, int | bool]:
    """Create two test drafts and check them with a provider-level count.

    Factories must return independent clients for the same disposable ERP
    target. Supply a known customer in that workspace and a CloudEvent fixture.
    The counting function must use the provider rather than the Workbench DB.
    """
    if disposable is not True or count_drafts() != 0:
        raise ValueError("an explicitly disposable, empty ERP target is required")

    concurrent_event = {**event, "id": "connector-probe-delivery", "subject": "connector-probe-opportunity"}
    if concurrent_event["id"] == event["id"] or concurrent_event["subject"] == event["subject"]:
        raise ValueError("fixture uses a reserved conformance probe identity")

    submitter = Actor("connector-probe-submitter", frozenset({"editor"}))
    reviewer = Actor("connector-probe-reviewer", frozenset({"approver"}))

    with tempfile.TemporaryDirectory(prefix="ai-factory-connector-") as directory:
        path = Path(directory) / "workbench.sqlite3"

        def engine(adapter: DraftConnector, *, defer: bool = False) -> Workbench:
            return Workbench(path, workspace=workspace, connector=adapter, customers=customers,
                             crm_source=event["source"], defer_execution=defer)

        def approve(workbench: Workbench, case: dict) -> dict:
            if case["action"] is None:
                raise AssertionError("fixture did not resolve an ERP customer")
            return workbench.decide(case["id"], reviewer, decision="approve", revision=case["revision"],
                                    proposal_hash=case["proposal_hash"], rationale="Connector conformance probe.")

        first = engine(_ConnectorProbe(connector_factory(), lose_ack=True))
        lost_ack = first.submit(event, submitter)
        try:
            approve(first, lost_ack)
        except WorkbenchError as exc:
            if str(exc) != "outcome_unknown_retry_same_approval":
                raise
        else:
            raise AssertionError("lost acknowledgement was not surfaced")
        if first.get_case(lost_ack["id"], reviewer)["status"] != "applying" or count_drafts() != 1:
            raise AssertionError("the draft and pending approval must both survive the lost acknowledgement")

        recovered = engine(connector_factory()).reconcile_pending(lost_ack["id"])
        if recovered["status"] != "executed" or not recovered["receipt"] or count_drafts() != 1:
            raise AssertionError("restart did not recover exactly one confirmed draft")

        second = engine(connector_factory(), defer=True)
        case = second.submit(concurrent_event, submitter)
        if approve(second, case)["status"] != "applying" or count_drafts() != 1:
            raise AssertionError("deferred approval must not contact the ERP")

        barrier = Barrier(2)
        left = engine(_ConnectorProbe(connector_factory(), barrier=barrier))
        right = engine(_ConnectorProbe(connector_factory(), barrier=barrier))
        with ThreadPoolExecutor(max_workers=2) as pool:
            jobs = [pool.submit(client.reconcile_pending, case["id"]) for client in (left, right)]
            results = [job.result() for job in jobs]
        drafts = count_drafts()
        if results[0] != results[1] or results[0]["status"] != "executed" or drafts != 2:
            raise AssertionError("concurrent recovery did not converge on one new draft")
    return {"lost_ack_reconciled": True, "concurrent_recovery_verified": True, "provider_drafts": drafts}
