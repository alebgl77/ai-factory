"""Durable reconciliation of Portfolio handoffs and exact Workbench decisions."""

from __future__ import annotations

import json
import logging
import sqlite3

from fastapi import FastAPI

from ai_factory_workbench import Workbench, WorkbenchError
from ai_factory_workbench.cyberlog import JournalError
from ai_factory_workbench.service import _PORTFOLIO_SOURCE

from .portfolio_store import PortfolioError

_BATCH_SIZE = 32
_LOGGER = logging.getLogger("factory.portfolio_workbench")


def active_workbench(app: FastAPI) -> Workbench | None:
    workbench = getattr(app.state, "workbench", None)
    return workbench if getattr(workbench, "mode", None) == "portfolio" else None


def has_workbench_case(workbench: Workbench, task_id: str) -> bool:
    """Check reserved subjects across revisions without creating a Workbench store."""
    try:
        with workbench.store.read_existing() as db:
            return db is not None and db.execute(
                "SELECT 1 FROM cases WHERE source = ? AND subject GLOB ? LIMIT 1",
                (_PORTFOLIO_SOURCE, f"portfolio/{task_id}/*"),
            ).fetchone() is not None
    except WorkbenchError:
        raise PortfolioError("portfolio_unavailable", status=503) from None


def reconcile_portfolio_reviews(app: FastAPI) -> int:
    """Run a bounded pass. Both databases independently commit before the next step."""
    workbench = active_workbench(app)
    portfolio = getattr(app.state, "portfolio", None)
    if workbench is None or portfolio is None:
        return 0

    cursors = getattr(app.state, "portfolio_review_cursors", {})
    app.state.portfolio_review_cursors = cursors
    applied = 0
    retries = 0
    for linked in (False, True):
        rows = portfolio.pending_workbench_reviews(linked=linked, after=cursors.get(linked, 0), limit=_BATCH_SIZE)
        # Round-robin scans keep undecided or unavailable cases from blocking
        # newer work. Cursors are advisory only; the durable row governs replay.
        cursors[linked] = rows[-1].seq if len(rows) == _BATCH_SIZE else 0
        for row in rows:
            try:
                if not linked:
                    proposal = json.loads(row.proposal)
                    proposal["exclusions"] = tuple(proposal["exclusions"])
                    binding = workbench._ensure_portfolio_review(subject=row.subject, **proposal)
                    portfolio.bind_workbench_review(row, binding)
                else:
                    receipt = workbench._portfolio_receipt(
                        row.subject, row.case_id, row.case_revision, row.proposal_hash,
                    )
                    if receipt is not None and portfolio.apply_workbench_receipt(row, *receipt):
                        applied += 1
            except (WorkbenchError, JournalError, sqlite3.Error, OSError):
                retries += 1
    if retries:
        # Neither proposal data nor decision identifiers belong in logs.
        _LOGGER.warning("portfolio_review_reconcile_retry count=%d", retries)
    return applied
