"""Platform health as Docker sees it (container state + healthcheck) vs what customers see (probes).

The gap between the two is the *health-check blind spot*: seconds during which customers were failing while
every container still reported running/healthy. Measured from raw data, never inferred.
"""

from __future__ import annotations

from typing import Any

from . import metrics
from .config import Config


def project_health(cfg: Config, docker) -> dict[str, dict[str, str]]:
    res = docker.run(["ps", "-a", "--filter", f"label=com.docker.compose.project={cfg.sandbox['compose_project']}",
                      "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], check=False)
    out = {}
    for row in res.stdout.strip().splitlines():
        parts = row.split("\t")
        if len(parts) >= 3:
            out[parts[0]] = {"state": parts[1], "status": parts[2]}
    return out


def verdict(cfg: Config, snapshot: dict[str, dict[str, str]]) -> dict[str, Any]:
    """'green' = what an infrastructure dashboard would show: every app container running and not unhealthy."""
    problems = []
    for name in cfg.allowlist:
        s = snapshot.get(name)
        if s is None:
            problems.append(f"{name}: missing")
        elif s.get("state") != "running":
            problems.append(f"{name}: {s.get('state')}")
        elif "unhealthy" in s.get("status", "") or "health: starting" in s.get("status", ""):
            problems.append(f"{name}: {s.get('status')}")
    return {"green": not problems, "problems": problems}


def blind_spot(cfg: Config, samples: list[dict[str, Any]], platform: list[dict[str, Any]], inject_ts: float | None,
               end_ts: float, baseline: dict[str, Any], window_s: float = 2.0) -> dict[str, Any] | None:
    """Per 2 s window after injection: were customers impacted, and was the platform still green?"""
    if inject_ts is None:
        return None
    rec = cfg.recovery
    platform = sorted(platform, key=lambda p: p["ts"])
    impacted_s = silent_s = 0.0
    first_customer = first_platform = None
    for t, w in metrics.windows(samples, inject_ts, end_ts, window_s):
        if not w:
            continue
        bad = not metrics.window_ok(w, baseline, {s["journey"] for s in w}, rec["error_rate_pp"], rec["p95_factor"],
                                    rec["p95_slack_ms"])
        snap = [p for p in platform if p["ts"] <= t + window_s]
        green = snap[-1]["green"] if snap else True
        if not green and first_platform is None:
            first_platform = round(t - inject_ts, 1)
        if bad:
            impacted_s += window_s
            first_customer = round(t - inject_ts, 1) if first_customer is None else first_customer
            if green:
                silent_s += window_s
    return {"customer_impact_s": impacted_s, "silent_impact_s": silent_s,
            "covered_pct": round(100 * (1 - silent_s / impacted_s), 1) if impacted_s else None,
            "customers_first_failed_at_s": first_customer, "platform_first_not_green_at_s": first_platform,
            "method": "2 s windows after injection; 'impacted' = journey outside recovery band; 'silent' = impacted "
                      "while every app container was running and not (un)healthy/starting"}
