"""Small pure helpers shared by engines (dates, rounding, percentiles, JSON normalisation)."""

from __future__ import annotations

import datetime as dt
import math
from typing import Any, Iterable, Sequence

# Language-neutral fallback ids (CONTRACT section 9: ids never depend on the language).
UNATTRIBUTED = "unattributed"            # usage or agent task without a use case / agent label
UNASSIGNED_COST_CENTER = "unassigned"    # use case without a cost centre


def to_date(value: Any) -> dt.date | None:
    """Parse an ISO date (or datetime) into a date; return None when impossible."""
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value.date()
    if isinstance(value, dt.date):
        return value
    if isinstance(value, str):
        text = value.strip()
        try:
            return dt.date.fromisoformat(text[:10])
        except ValueError:
            return None
    return None


def to_datetime(value: Any) -> dt.datetime | None:
    if value is None:
        return None
    if isinstance(value, dt.datetime):
        return value
    if isinstance(value, dt.date):
        return dt.datetime(value.year, value.month, value.day, tzinfo=dt.timezone.utc)
    if isinstance(value, str):
        text = value.strip().replace("Z", "+00:00")
        try:
            parsed = dt.datetime.fromisoformat(text)
        except ValueError:
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=dt.timezone.utc)
        return parsed
    return None


def normalize(obj: Any) -> Any:
    """Recursively convert YAML-native values (dates, tuples, sets) into JSON-friendly ones."""
    if isinstance(obj, dict):
        return {str(k): normalize(v) for k, v in obj.items()}
    if isinstance(obj, (list, tuple)):
        return [normalize(v) for v in obj]
    if isinstance(obj, set):
        return sorted(normalize(v) for v in obj)
    if isinstance(obj, dt.datetime):
        return obj.isoformat()
    if isinstance(obj, dt.date):
        return obj.isoformat()
    if isinstance(obj, float) and (math.isnan(obj) or math.isinf(obj)):
        return None
    return obj


def r2(value: float) -> float:
    return round(float(value) + 0.0, 2)


def r4(value: float) -> float:
    return round(float(value) + 0.0, 4)


def safe_div(num: float, den: float, default: float = 0.0) -> float:
    return num / den if den else default


def percentile(values: Sequence[float], q: float) -> float:
    """Linear-interpolated percentile (q in [0, 1]); 0.0 for an empty sequence."""
    data = sorted(float(v) for v in values)
    if not data:
        return 0.0
    if len(data) == 1:
        return data[0]
    pos = max(0.0, min(1.0, q)) * (len(data) - 1)
    lo = math.floor(pos)
    hi = math.ceil(pos)
    if lo == hi:
        return data[lo]
    return data[lo] + (data[hi] - data[lo]) * (pos - lo)


def mean(values: Iterable[float]) -> float:
    data = [float(v) for v in values]
    return sum(data) / len(data) if data else 0.0


def allocate_cents(total_cents: int, weights: Sequence[float]) -> list[int]:
    """Split an integer amount by weights with the largest-remainder method (exact sum)."""
    n = len(weights)
    if n == 0:
        return []
    positive = [max(0.0, float(w)) for w in weights]
    wsum = sum(positive)
    if total_cents == 0:
        return [0] * n
    if wsum <= 0:
        positive = [1.0] * n
        wsum = float(n)
    raw = [total_cents * w / wsum for w in positive]
    base = [math.floor(x) for x in raw]
    remainder = total_cents - sum(base)
    order = sorted(range(n), key=lambda i: (-(raw[i] - base[i]), i))
    for i in order[:remainder]:
        base[i] += 1
    return base


def cents(value_eur: float) -> int:
    return int(round(float(value_eur) * 100))


def eur(value_cents: int) -> float:
    return round(value_cents / 100.0, 2)
