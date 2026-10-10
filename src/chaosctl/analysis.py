"""Per-run analysis computed by code from raw samples and events (no AI).

Severity / resilience score method: docs/SCORING.md (keep in sync).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import health, metrics
from .config import Config
from .util import read_json, read_jsonl, write_json

RULE_FIXES = {
    ("container-stop", "app"): [
        "Add client-side timeouts + retries with backoff and a circuit breaker on callers of {t}",
        "Run >1 replica of {t} behind the service name; add a healthcheck and restart policy",
        "Degrade gracefully: render the page without {t}'s data instead of failing the request",
    ],
    ("container-stop", "datastore"): [
        "Declare depends_on with condition: service_healthy and add a healthcheck for {t}",
        "Add connection retry/backoff and a cached/read-only fallback when {t} is unavailable",
        "Consider replication/failover for {t}",
    ],
    ("container-pause", "app"): [
        "Set explicit deadlines on calls to {t}; a hung dependency must fail fast, not block",
        "Add bulkheads (bounded concurrency) so a hung {t} cannot exhaust caller threads",
    ],
    ("container-pause", "datastore"): [
        "Set query/connection timeouts towards {t}; fail fast instead of hanging requests",
        "Add a circuit breaker and serve degraded responses while {t} is unresponsive",
    ],
    ("network-delay", "app"): [
        "Tune timeouts on callers of {t} below the user-facing SLO and add hedged/retried requests",
        "Make non-critical calls to {t} asynchronous or optional",
    ],
    ("network-delay", "datastore"): [
        "Tune DB/cache client timeouts and pool sizes for {t}; add caching for hot reads",
    ],
    ("cpu-throttle", "app"): [
        "Right-size CPU requests/limits for {t}; add autoscaling or load shedding",
    ],
    ("cpu-throttle", "datastore"): [
        "Right-size {t}; add read replicas/caching and query timeouts",
    ],
    ("traffic-spike", "app"): [
        "Add rate limiting/load shedding at the edge and capacity headroom for the most-loaded services",
        "Autoscale the bottleneck service identified by the latency increase",
    ],
}


def _phase_bounds(events: list[dict[str, Any]]) -> dict[str, float | None]:
    def first(action: str) -> float | None:
        return next((e["ts"] for e in events if e["action"] == action and e.get("source") == "orchestrator"), None)
    return {"pre_start": first("pre_start"), "inject": first("inject_ok"),
            "rollback_complete": first("rollback_complete"), "end": first("run_end")}


def _impact(fault_st: dict[str, Any], base: dict[str, Any]) -> dict[str, Any]:
    if not fault_st or not fault_st.get("n"):
        return {"error_rate_delta_pp": None, "p95_ratio": None}
    be = base.get("error_rate") or 0.0
    bp = base.get("p95_ms") or 0.0
    return {"error_rate_delta_pp": round((fault_st["error_rate"] - be) * 100, 2),
            "p95_ratio": round(fault_st["p95_ms"] / bp, 2) if bp else None}


def severity(cfg: Config, journeys: dict[str, Any], recovered: bool | None, recovery_s: float | None,
             unexpected: list[str], injected: bool, data_loss: float = 0.0) -> dict[str, Any]:
    """Documented, reproducible score (docs/SCORING.md)."""
    if not injected:
        return {"score": None, "label": "n/a", "components": {}}
    impact = 0.0
    for name, j in journeys.items():
        w = cfg.journeys[name].weight if name in cfg.journeys else 0.5
        de = max(0.0, (j["impact"]["error_rate_delta_pp"] or 0) / 100)
        ratio = j["impact"]["p95_ratio"] or 1.0
        lat = min(1.0, max(0.0, (ratio - 1.0) / 4.0))
        impact = max(impact, w * min(1.0, max(de, lat)))
    timeout = cfg.recovery["timeout_s"]
    rec_pen = 1.0 if not recovered else min(1.0, (recovery_s or 0) / timeout)
    blast = 1.0 if unexpected else 0.0
    loss = min(1.0, max(0.0, data_loss))
    score = round(100 * (0.4 * impact + 0.25 * rec_pen + 0.15 * blast + 0.2 * loss), 1)
    label = "critical" if score >= 70 else "high" if score >= 45 else "medium" if score >= 20 else "low"
    return {"score": score, "label": label, "resilience": round(100 - score, 1),
            "components": {"impact": round(impact, 3), "recovery_penalty": round(rec_pen, 3), "blast_radius": blast,
                           "data_loss": round(loss, 3)}}


def analyze_run(cfg: Config, run_dir: Path) -> dict[str, Any]:
    exp = read_json(run_dir / "experiment.json")
    events = read_jsonl(run_dir / "events.jsonl")
    samples = read_jsonl(run_dir / "metrics.jsonl")
    outcome = read_json(run_dir / "outcome.json") if (run_dir / "outcome.json").exists() else {}
    abort = read_json(run_dir / "ABORT.json") if (run_dir / "ABORT.json").exists() else None
    baseline = exp["baseline"]
    b = _phase_bounds(events)
    end = b["end"] or (max((s["ts"] for s in samples), default=0) + 1)
    injected = b["inject"] is not None
    fault_end = b["rollback_complete"] or end

    phases = {
        "pre": metrics.summarize(metrics.between(samples, b["pre_start"] or 0, b["inject"] or fault_end)),
        "fault": metrics.summarize(metrics.between(samples, b["inject"], fault_end)) if injected else None,
        "post": metrics.summarize(metrics.between(samples, fault_end, end)) if injected else None,
    }
    rec_ev = next((e for e in events if e["action"] in ("recovered", "not_recovered")), None)
    recovered = None if not rec_ev else rec_ev["action"] == "recovered"
    # recompute from raw data (the event is only the online view)
    recovery_s = (metrics.recovery_time(samples, fault_end, baseline, cfg.journeys, cfg.recovery, end)
                  if injected else None)
    if injected and recovery_s is not None:
        recovered = True
    elif injected:
        recovered = False

    rec = cfg.recovery
    journeys: dict[str, Any] = {}
    impacted: list[str] = []
    for name in sorted(cfg.journeys):
        base = metrics.journey_baseline(baseline, name)
        fault_st = (phases["fault"] or {}).get("journeys", {}).get(name, {}) if injected else {}
        imp = _impact(fault_st, base)
        hit = bool(fault_st and fault_st.get("n")) and (
            fault_st["error_rate"] > (base.get("error_rate") or 0) + rec["error_rate_pp"] / 100
            or fault_st["p95_ms"] > rec["p95_factor"] * (base.get("p95_ms") or 0) + rec["p95_slack_ms"])
        if hit:
            impacted.append(name)
        journeys[name] = {"baseline": {k: base.get(k) for k in ("error_rate", "p95_ms")},
                          "fault": {k: fault_st.get(k) for k in ("n", "error_rate", "p95_ms", "p99_ms")},
                          "impact": imp, "impacted": hit}

    tti = None
    if injected:
        for t, w in metrics.windows(samples, b["inject"], fault_end, rec["window_s"]):
            if w and not metrics.window_ok(w, baseline, {s["journey"] for s in w}, rec["error_rate_pp"],
                                           rec["p95_factor"], rec["p95_slack_ms"]):
                tti = round(t - b["inject"], 2)
                break

    expected = exp["experiment"].get("expected_journeys", [])
    unexpected = sorted(set(impacted) - set(expected))
    target_states = [e for e in events if e["action"] == "target_state"]
    restarts: dict[str, list[Any]] = {}
    timeline = []
    for e in target_states:
        for tg, st in (e.get("states") or {}).items():
            restarts.setdefault(tg, []).append(st.get("restart_count"))
            rel = round(e["ts"] - b["inject"], 1) if b["inject"] else None
            row = {"t_s": rel, "target": tg, **{k: st.get(k) for k in ("status", "health", "restart_count", "exit_code",
                                                                       "oom_killed")}}
            if not timeline or {k: v for k, v in timeline[-1].items() if k != "t_s"} != \
                    {k: v for k, v in row.items() if k != "t_s"}:
                timeline.append(row)  # keep state changes only
    self_healed = {tg: (len({r for r in rc if r is not None}) > 1) for tg, rc in restarts.items()}
    checks = [{k: v for k, v in e.items() if k not in ("ts", "time", "source", "action")}
              for e in events if e["action"] == "check_result"]
    data_loss = max((c["lost"] / c["seeded"] for c in checks if c.get("seeded")), default=0.0)

    ttr = None
    if injected and tti is not None:
        rt = metrics.recovery_time(samples, b["inject"] + tti, baseline, cfg.journeys, cfg.recovery, end)
        ttr = round(tti + rt, 2) if rt is not None else None
    elif injected:
        ttr = 0.0

    platform = [{"ts": e["ts"], "green": e["green"]} for e in events if e["action"] == "platform_health"]
    blind = health.blind_spot(cfg, samples, platform, b["inject"], end, baseline) if platform else None

    sev = severity(cfg, journeys, recovered, recovery_s, unexpected, injected, data_loss)
    result = {
        "run_id": exp["run_id"],
        "experiment_id": exp["experiment"]["id"],
        "faults": exp["experiment"]["faults"],
        "hypothesis": exp["experiment"].get("hypothesis"),
        "outcome": outcome.get("status"),
        "abort": abort,
        "timeline": {k: v for k, v in b.items()},
        "time_to_impact_s": tti,
        "recovered": recovered,
        "recovery_time_s": round(recovery_s, 2) if recovery_s is not None else None,
        "expected_journeys": expected,
        "impacted_journeys": impacted,
        "unexpected_impacted_journeys": unexpected,
        "time_to_restore_s": ttr,
        "rollback": [{"fault_id": e["fault_id"], "verified": (e.get("result") or {}).get("verified"),
                      "attempts": (e.get("result") or {}).get("attempts"),
                      "checks": ((e.get("result") or {}).get("history") or [{}])[-1].get("checks")}
                     for e in events if e["action"] == "rollback" and e.get("source") == "orchestrator"],
        "health_blind_spot": blind,
        "self_healed": self_healed,
        "target_timeline": timeline,
        "checks": checks,
        "journeys": journeys,
        "phases": {k: (v["overall"] if v else None) for k, v in phases.items()},
        "severity": sev,
        "sample_count": len(samples),
    }
    write_json(run_dir / "analysis.json", result)
    (run_dir / "findings.md").write_text(render_run_findings(cfg, result))
    return result


def fault_kind(cfg: Config, target: str) -> str:
    return "datastore" if target in cfg.datastores else "app"


def rule_fixes(cfg: Config, fault: dict[str, Any]) -> list[str]:
    key = (fault["fault_type"], fault_kind(cfg, fault["target"]))
    tmpl = RULE_FIXES.get(key) or RULE_FIXES.get((fault["fault_type"], "app"), [])
    return [t.format(t=fault["target"]) for t in tmpl]


def _fmt(v: Any, unit: str = "") -> str:
    return "n/a" if v is None else f"{v}{unit}"


def render_run_findings(cfg: Config, a: dict[str, Any]) -> str:
    f0 = a["faults"][0]
    lines = [f"# Run {a['run_id']}", "",
             f"- Experiment: `{a['experiment_id']}` — " + ", ".join(
                 f"{f['fault_type']} on `{f['target']}` {f.get('params') or ''} for {f['duration_s']}s"
                 for f in a["faults"]),
             f"- Outcome: **{a['outcome']}**" + (f" (abort: {a['abort']['reason']})" if a["abort"] else ""),
             f"- Hypothesis (pre-registered): {a['hypothesis'] or 'n/a'}",
             f"- Time to impact: {_fmt(a['time_to_impact_s'], ' s')} · Recovered: {a['recovered']} · "
             f"Recovery time: {_fmt(a['recovery_time_s'], ' s')}",
             f"- Time to restore service (inject → sustained healthy): {_fmt(a['time_to_restore_s'], ' s')} · "
             f"Self-healed: {a['self_healed'] or 'n/a'}",
             *[f"- Check `{c['check']}`: {c['intact']}/{c['seeded']} intact, **{c['lost']} lost**" for c in a["checks"]],
             f"- Impacted journeys: {', '.join(a['impacted_journeys']) or 'none'} · "
             f"Unexpected: {', '.join(a['unexpected_impacted_journeys']) or 'none'}",
             f"- Severity: **{a['severity']['label']}** (score {_fmt(a['severity']['score'])}) — method: docs/SCORING.md",
             "", "## Observations (computed from raw samples)", "",
             "| Journey | Baseline err | Baseline p95 ms | Fault err | Fault p95 ms | Δ err pp | p95 × | Impacted |",
             "|---|---|---|---|---|---|---|---|"]
    for name, j in a["journeys"].items():
        lines.append(f"| {name} | {_fmt(j['baseline']['error_rate'])} | {_fmt(j['baseline']['p95_ms'])} | "
                     f"{_fmt(j['fault']['error_rate'])} | {_fmt(j['fault']['p95_ms'])} | "
                     f"{_fmt(j['impact']['error_rate_delta_pp'])} | {_fmt(j['impact']['p95_ratio'])} | "
                     f"{'yes' if j['impacted'] else 'no'} |")
    lines += ["", "## Rule-based fix candidates (hypotheses, not observations)", ""]
    lines += [f"- {fx}" for fx in rule_fixes(cfg, f0)]
    return "\n".join(lines) + "\n"
