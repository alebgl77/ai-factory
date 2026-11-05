"""Deterministic decision support for governed workbench actions."""

from __future__ import annotations

import math
import re
import unicodedata
from collections.abc import Collection, Iterable, Mapping
from dataclasses import dataclass
from difflib import SequenceMatcher


@dataclass(frozen=True)
class MatchResult:
    status: str
    candidate_id: str | None = None
    score: float = 0.0
    candidates: tuple[tuple[str, float], ...] = ()


def _words(value: object) -> tuple[str, ...]:
    if not isinstance(value, str):
        return ()
    text = unicodedata.normalize("NFKD", value[:160].casefold())
    text = "".join(char for char in text if not unicodedata.combining(char))
    return tuple(re.findall(r"[^\W_]+", text))


def match_customer(
    query: Mapping[str, object],
    candidates: Iterable[Mapping[str, object]],
    *,
    tenant: str,
    allowed_ids: Collection[str],
) -> MatchResult:
    """Rank only candidates already authorized by the caller's record policy."""
    allowed = set(allowed_ids)
    visible = [(row["id"], row) for row in candidates
               if row.get("tenant") == tenant and isinstance(row.get("id"), str) and row["id"] in allowed]
    ref = query.get("customer_ref")
    if isinstance(ref, str) and ref:
        exact = tuple(sorted({record_id for record_id, row in visible if row.get("reference") == ref}))
        if len(exact) == 1:
            return MatchResult("matched", exact[0], 1.0, ((exact[0], 1.0),))
        if exact:
            return MatchResult("review", candidates=tuple((record_id, 1.0) for record_id in exact[:3]))
        return MatchResult("missing")

    target = _words(query.get("customer_name"))
    if not target:
        return MatchResult("missing")
    target_set = set(target)
    target_text = " ".join(target)
    scores: dict[str, float] = {}
    for record_id, row in visible:
        words = _words(row.get("name"))
        shared = target_set.intersection(words)
        # Block obviously unrelated names before running the slower sequence comparison.
        if not words or (not shared and words[0][:3] != target[0][:3]):
            continue
        overlap = len(shared) / len(target_set.union(words))
        sequence = SequenceMatcher(None, target_text, " ".join(words), autojunk=False).ratio()
        score = 1.0 if words == target else round(0.4 * overlap + 0.6 * sequence, 4)
        scores[record_id] = max(score, scores.get(record_id, 0.0))

    ranked = tuple(sorted(scores.items(), key=lambda item: (-item[1], item[0]))[:3])
    if not ranked or ranked[0][1] < 0.60:
        return MatchResult("missing")
    best, score = ranked[0]
    runner_up = ranked[1][1] if len(ranked) > 1 else 0.0
    if score >= 0.92 and score - runner_up >= 0.08:
        return MatchResult("matched", best, score, ranked)
    return MatchResult("review", score=score, candidates=ranked)


def review_priority(kind: str, age_hours: float, amount_minor: int) -> int:
    """Age cases to prevent starvation without changing their approval policy."""
    base = {"action": 20, "undocumented": 45, "strategic": 60}.get(kind, 45)
    amount = min(25, max(0, amount_minor) // 100_000)
    age = min(60, max(0, int(age_hours)))
    return base + amount + age


def _finite_nonnegative(value: float) -> bool:
    return math.isfinite(value) and value >= 0


@dataclass(frozen=True)
class RoutePolicy:
    sensitivity: str
    allowed_residencies: frozenset[str]
    max_cost: float
    max_latency_ms: float
    min_quality: float = 0.0

    def __post_init__(self) -> None:
        if (self.sensitivity not in {"C1", "C2", "C3", "C4"}
                or not self.allowed_residencies
                or not _finite_nonnegative(self.max_cost)
                or not _finite_nonnegative(self.max_latency_ms) or not 0 <= self.min_quality <= 1):
            raise ValueError("invalid routing policy")


@dataclass(frozen=True)
class ModelRoute:
    model_id: str
    placement: str
    residency: str
    cost: float
    p95_ms: float
    quality: float
    available: bool = True


@dataclass(frozen=True)
class RouteObservation:
    model_id: str
    samples: int
    cost: float
    p95_ms: float
    success_rate: float


@dataclass(frozen=True)
class RouteDecision:
    model_id: str | None
    reason: str
    score: float | None = None


def select_route(
    policy: RoutePolicy,
    candidates: Iterable[ModelRoute],
    observations: Iterable[RouteObservation] = (),
) -> RouteDecision:
    """Apply hard gates before scoring. Observations only tighten declared estimates."""
    history: dict[str, RouteObservation] = {}
    for item in observations:
        if (item.samples < 5 or not _finite_nonnegative(item.cost)
                or not _finite_nonnegative(item.p95_ms) or not 0 <= item.success_rate <= 1):
            continue
        previous = history.get(item.model_id)
        # Favor the larger sample and the conservative estimate on a tie.
        rank = (item.samples, item.cost, item.p95_ms, -item.success_rate)
        if previous is None or rank > (previous.samples, previous.cost, previous.p95_ms, -previous.success_rate):
            history[item.model_id] = item

    scores: list[tuple[float, str]] = []
    for model in candidates:
        if (not model.available or model.placement not in {"cloud", "vps", "local"}
                or model.residency not in policy.allowed_residencies
                or (policy.sensitivity in {"C3", "C4"} and model.placement != "local")
                or not _finite_nonnegative(model.cost) or not _finite_nonnegative(model.p95_ms)
                or not 0 <= model.quality <= 1):
            continue
        observed = history.get(model.model_id)
        cost = max(model.cost, observed.cost) if observed else model.cost
        latency = max(model.p95_ms, observed.p95_ms) if observed else model.p95_ms
        quality = min(model.quality, observed.success_rate) if observed else model.quality
        if cost > policy.max_cost or latency > policy.max_latency_ms or quality < policy.min_quality:
            continue
        affordability = 1 - cost / policy.max_cost if policy.max_cost else 1.0
        speed = 1 - latency / policy.max_latency_ms if policy.max_latency_ms else 1.0
        scores.append((0.55 * quality + 0.30 * affordability + 0.15 * speed, model.model_id))

    if not scores:
        return RouteDecision(None, "no_eligible_model")
    score, model_id = min(scores, key=lambda candidate: (-candidate[0], candidate[1]))
    return RouteDecision(model_id, "eligible_model_selected", round(score, 6))
