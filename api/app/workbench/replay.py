"""Seeded, network-free comparison of a simple rules baseline and the real workbench."""

from __future__ import annotations

import tempfile
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

from ..security import Principal
from .algorithms import ModelRoute, RouteObservation, RoutePolicy, select_route
from .service import Workbench
from .store import WorkbenchError


_CATEGORIES = ("normal", "duplicate", "lost_receipt", "stale_proposal", "self_approval", "unknown_customer")
_NOW = datetime(2026, 11, 5, 12, 0, tzinfo=timezone.utc)
_SUBMITTER = Principal("crm-operator", frozenset({"editor", "approver"}), True, "proxy", user="crm-operator")
_REVIEWER = Principal("erp-reviewer", frozenset({"approver"}), True, "proxy", user="erp-reviewer")


@dataclass(frozen=True)
class Scenario:
    category: str
    variant: int

    @property
    def id(self) -> str:
        return f"{self.category}-{self.variant + 1:02}"

    @property
    def expected_drafts(self) -> int:
        return 0 if self.category in {"self_approval", "unknown_customer"} else 1

    def event(self, version: int = 1) -> dict:
        known = self.category != "unknown_customer"
        customer_name, reference = ("Atelier Acme", "CRM-ACME") if self.variant % 2 == 0 else ("Nova Services", "CRM-NOVA")
        return {
            "specversion": "1.0", "source": "crm-sim", "id": f"won-{self.id}-{version}",
            "type": "crm.opportunity.won", "subject": f"opp-{self.id}",
            "data": {"title": f"Replay case {self.id}", "customer_name": customer_name if known else "Unlisted Trading",
                     "customer_ref": reference if known else "CRM-UNMAPPED",
                     "amount_minor": 120_000 + 2500 * self.variant + 1000 * (version - 1),
                     "currency": "EUR", "record_version": version},
        }


def _approve(workbench: Workbench, case: dict, actor: Principal = _REVIEWER) -> dict:
    return workbench.decide(case["id"], actor, decision="approve", revision=case["revision"],
                            proposal_hash=case["proposal_hash"], rationale="Checked synthetic evidence.")


def _blocked(action, status: int, code: str) -> bool:
    try:
        action()
    except WorkbenchError as exc:
        return exc.status == status and str(exc) == code
    return False


def _rules_only(scenario: Scenario) -> dict:
    """Reference rule: exact known customer creates a draft on each approved attempt."""
    proposal = scenario.event()
    attempts = (proposal, proposal) if scenario.category in {"duplicate", "lost_receipt"} else (proposal,)
    drafts = [event["data"] for event in attempts if event["data"]["customer_ref"] in {"CRM-ACME", "CRM-NOVA"}]
    violations: list[str] = []
    if len(drafts) > 1:
        violations.append("duplicate_draft")
    if drafts and scenario.category == "self_approval":
        violations.append("self_approval")
    if drafts and scenario.category == "stale_proposal":
        if drafts[0]["record_version"] != scenario.event(2)["data"]["record_version"]:
            violations.append("stale_proposal")
    return {"drafts": len(drafts), "violations": violations,
            "pass": len(drafts) == scenario.expected_drafts and not violations}


def _governed(scenario: Scenario, path: Path) -> dict:
    workbench = Workbench(path, mode="protected_demo", clock=lambda: _NOW)
    payload = scenario.event()
    case = workbench.submit(payload, _SUBMITTER)
    protections: list[str] = []
    valid = True
    category = scenario.category

    if category == "unknown_customer":
        valid = case["kind"] == "undocumented" and case["action"] is None and case["status"] == "waiting_review"
        case = _approve(workbench, case)
        valid = valid and case["status"] == "decided"
        protections.append("manual_review")
    elif category == "self_approval":
        valid = _blocked(lambda: _approve(workbench, case, _SUBMITTER), 403, "self_approval_forbidden")
        protections.append("separate_reviewer")
    elif category == "stale_proposal":
        updated = workbench.submit(scenario.event(2), _SUBMITTER)
        valid = _blocked(lambda: _approve(workbench, case), 409, "stale_proposal")
        delayed = {**payload, "id": f"late-{scenario.id}"}
        valid = valid and _blocked(lambda: workbench.submit(delayed, _SUBMITTER), 409, "stale_event")
        case = _approve(workbench, updated)
        valid = valid and case["status"] == "executed" and case["action"]["fields"]["amount_minor"] == scenario.event(2)["data"]["amount_minor"]
        protections.extend(("compare_and_set_approval", "monotonic_event_revision"))
    elif category == "lost_receipt":
        create_draft = workbench.store.create_draft

        def lose_receipt(external_ref: str, fields: dict) -> dict:
            create_draft(external_ref, fields)
            raise TimeoutError

        workbench.store.create_draft = lose_receipt
        valid = _blocked(lambda: _approve(workbench, case), 503, "outcome_unknown_retry_same_approval")
        valid = valid and workbench.store.draft_count() == 1
        # Simulate the process restarting before it can persist the receipt.
        workbench = Workbench(path, mode="protected_demo", clock=lambda: _NOW)
        case = _approve(workbench, case)
        valid = valid and case["status"] == "executed" and bool(case.get("receipt"))
        protections.append("reconciled_after_restart")
    else:
        if category == "duplicate":
            duplicate = workbench.submit(payload, _SUBMITTER)
            valid = duplicate["id"] == case["id"] and duplicate["proposal_hash"] == case["proposal_hash"]
            protections.append("deduplicated_event")
        case = _approve(workbench, case)
        valid = valid and case["status"] == "executed" and bool(case.get("receipt"))
        if category == "duplicate":
            retry = _approve(workbench, case)
            valid = valid and retry["receipt"] == case["receipt"]
            protections.append("idempotent_approval")

    drafts = workbench.store.draft_count()
    return {"drafts": drafts, "protections": protections, "pass": valid and drafts == scenario.expected_drafts}


def _routing_cases() -> dict[str, dict]:
    local = ModelRoute("demo-local", "local", "eu", 0.01, 180, 0.92)
    cloud = ModelRoute("demo-cloud", "cloud", "eu", 0.005, 60, 0.95)
    trusted_region = frozenset({"eu"})
    low_risk = RoutePolicy("C1", trusted_region, 0.02, 250, 0.8)
    sensitive = RoutePolicy("C3", trusted_region, 0.02, 250, 0.8)
    slow_cloud = RouteObservation("demo-cloud", 15, 0.005, 400, 0.95)
    decisions = {
        "low_risk_baseline": (select_route(low_risk, (local, cloud)), "demo-cloud"),
        "low_risk_cloud_too_slow": (select_route(low_risk, (local, cloud), (slow_cloud,)), "demo-local"),
        "sensitive_only_cloud_available": (select_route(sensitive, (cloud,)), None),
    }
    return {key: {"model_id": decision.model_id, "reason": decision.reason, "pass": decision.model_id == expected}
            for key, (decision, expected) in decisions.items()}


def run_benchmark() -> dict:
    """Run deterministic synthetic business cases against a temporary workbench per case."""
    cases = []
    with tempfile.TemporaryDirectory(prefix="factory-workbench-replay-") as temporary:
        for category in _CATEGORIES:
            for variant in range(6):
                scenario = Scenario(category, variant)
                cases.append({"id": scenario.id, "scenario": category, "expected_drafts": scenario.expected_drafts,
                              "rules_only": _rules_only(scenario),
                              "governed": _governed(scenario, Path(temporary) / f"{scenario.id}.sqlite3")})
    routing = _routing_cases()
    summary = {"scenarios": len(cases), "governed_passed": sum(row["governed"]["pass"] for row in cases),
               "rules_only_passed": sum(row["rules_only"]["pass"] for row in cases),
               "routing_passed": sum(row["pass"] for row in routing.values()), "routing_scenarios": len(routing)}
    return {"source": "synthetic", "metric_scope": "simulated outcomes, not live cost, latency, or model quality",
            "baseline": "exact customer reference plus one ERP draft per approved delivery attempt",
            "summary": summary, "cases": cases, "routing": routing}
