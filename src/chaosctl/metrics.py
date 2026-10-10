"""Deterministic metric computation from RAW probe samples.

Percentiles use the nearest-rank method on raw latencies; they are never averaged.
A sample is {"ts", "journey", "ok", "status", "latency_ms", "error"}.
"""

from __future__ import annotations

import math
from typing import Any, Iterable

Sample = dict[str, Any]


def percentile(values: Iterable[float], q: float) -> float | None:
    vals = sorted(values)
    if not vals:
        return None
    rank = max(1, math.ceil(q / 100.0 * len(vals)))
    return float(vals[rank - 1])


def stats(samples: list[Sample]) -> dict[str, Any]:
    n = len(samples)
    if n == 0:
        return {"n": 0, "errors": 0, "error_rate": None, "p50_ms": None, "p95_ms": None,
                "p99_ms": None, "throughput_rps": None}
    errors = sum(1 for s in samples if not s["ok"])
    lat = [float(s["latency_ms"]) for s in samples]
    span = max(s["ts"] for s in samples) - min(s["ts"] for s in samples)
    return {
        "n": n,
        "errors": errors,
        "error_rate": errors / n,
        "p50_ms": percentile(lat, 50),
        "p95_ms": percentile(lat, 95),
        "p99_ms": percentile(lat, 99),
        "throughput_rps": (n / span) if span > 0 else None,
    }


def by_journey(samples: list[Sample]) -> dict[str, list[Sample]]:
    out: dict[str, list[Sample]] = {}
    for s in samples:
        out.setdefault(s["journey"], []).append(s)
    return out


def summarize(samples: list[Sample]) -> dict[str, Any]:
    """Per-journey and overall stats (overall percentiles from pooled raw samples)."""
    return {
        "overall": stats(samples),
        "journeys": {j: stats(ss) for j, ss in sorted(by_journey(samples).items())},
    }


def between(samples: list[Sample], start: float, end: float) -> list[Sample]:
    return [s for s in samples if start <= s["ts"] < end]


def windows(samples: list[Sample], start: float, end: float, size: float) -> list[tuple[float, list[Sample]]]:
    out = []
    t = start
    while t < end:
        out.append((t, between(samples, t, min(t + size, end))))
        t += size
    return out


def journey_baseline(baseline: dict[str, Any], journey: str) -> dict[str, Any]:
    j = baseline.get("journeys", {}).get(journey)
    return j or baseline.get("overall", {})


def window_ok(window: list[Sample], baseline: dict[str, Any], journeys: Iterable[str],
              error_rate_pp: float, p95_factor: float, p95_slack_ms: float) -> bool:
    """True if every journey in the window is within the recovery band of its baseline."""
    groups = by_journey(window)
    for j in journeys:
        ss = groups.get(j, [])
        if not ss:
            return False  # no evidence is not evidence of recovery
        st = stats(ss)
        base = journey_baseline(baseline, j)
        base_err = base.get("error_rate") or 0.0
        base_p95 = base.get("p95_ms") or 0.0
        if st["error_rate"] > base_err + error_rate_pp / 100.0:
            return False
        if st["p95_ms"] > p95_factor * base_p95 + p95_slack_ms:
            return False
    return True


def recovery_time(samples: list[Sample], rollback_ts: float, baseline: dict[str, Any],
                  journeys: Iterable[str], rec: dict[str, Any], end_ts: float | None = None) -> float | None:
    """Seconds from rollback to the start of the first of N consecutive in-band windows."""
    journeys = list(journeys)
    size = float(rec["window_s"])
    need = int(rec["consecutive"])
    limit = rollback_ts + float(rec["timeout_s"]) + size * need
    end = min(end_ts, limit) if end_ts else limit
    run = 0
    first: float | None = None
    for t, w in windows(samples, rollback_ts, end, size):
        if window_ok(w, baseline, journeys, rec["error_rate_pp"], rec["p95_factor"], rec["p95_slack_ms"]):
            run += 1
            first = t if run == 1 else first
            if run >= need:
                rt = first - rollback_ts
                return rt if rt <= float(rec["timeout_s"]) else None
        else:
            run, first = 0, None
    return None
