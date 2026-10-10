"""Incident analysis for ONE run: where it failed, how, why, and industry-standard fixes.

AI (structured output) explains; code validates that every evidence id and pattern id exists and
flags any number in the prose that does not appear in the evidence. Rule-based fallback otherwise.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

from . import ai
from .config import Config
from .patterns import PATTERN_IDS, PATTERNS
from .util import iso, read_json, write_json

SYSTEM = """You are a principal site-reliability engineer writing the post-incident analysis of ONE \
controlled chaos experiment run against the OpenTelemetry Demo in an isolated sandbox. Use ONLY the evidence \
items provided; cite them by id (E1, E2, ...). Explain WHERE the failure was (component and propagation path \
to customer journeys), HOW it unfolded (timeline), and WHY (root cause and contributing factors). State \
whether the pre-registered hypothesis was confirmed or refuted. Recommend fixes ONLY from the provided \
pattern catalog, each with a concrete change for this system (config or code), a priority and the \
evidence that motivates it. Do not state any number that is not present in the evidence. Be concise and \
specific; no generic advice."""

SCHEMA = {
    "type": "object",
    "properties": {
        "verdict": {"type": "string", "enum": ["hypothesis_confirmed", "hypothesis_refuted", "inconclusive"]},
        "summary": {"type": "string"},
        "where": {
            "type": "object",
            "properties": {"failed_component": {"type": "string"},
                           "propagation_path": {"type": "array", "items": {"type": "string"}},
                           "impacted_journeys": {"type": "array", "items": {"type": "string"}}},
            "required": ["failed_component", "propagation_path", "impacted_journeys"],
            "additionalProperties": False,
        },
        "how": {"type": "string"},
        "why": {
            "type": "object",
            "properties": {
                "root_cause": {"type": "string"},
                "contributing_factors": {"type": "array", "items": {
                    "type": "object",
                    "properties": {"factor": {"type": "string"},
                                   "evidence_ids": {"type": "array", "items": {"type": "string"}}},
                    "required": ["factor", "evidence_ids"], "additionalProperties": False}},
            },
            "required": ["root_cause", "contributing_factors"],
            "additionalProperties": False,
        },
        "customer_impact": {"type": "string"},
        "detection_gap": {"type": "string"},
        "fixes": {"type": "array", "items": {
            "type": "object",
            "properties": {"pattern_id": {"type": "string", "enum": PATTERN_IDS},
                           "change": {"type": "string"},
                           "priority": {"type": "string", "enum": ["P1", "P2", "P3"]},
                           "expected_effect": {"type": "string"},
                           "evidence_ids": {"type": "array", "items": {"type": "string"}}},
            "required": ["pattern_id", "change", "priority", "expected_effect", "evidence_ids"],
            "additionalProperties": False}},
        "verification_plan": {"type": "string"},
    },
    "required": ["verdict", "summary", "where", "how", "why", "customer_impact", "detection_gap", "fixes",
                 "verification_plan"],
    "additionalProperties": False,
}


def build_items(cfg: Config, run_dir: Path) -> list[dict[str, Any]]:
    exp = read_json(run_dir / "experiment.json")
    a = read_json(run_dir / "analysis.json")
    ev = read_json(run_dir / "evidence.json") if (run_dir / "evidence.json").exists() else {}
    e = exp["experiment"]
    items = [
        {"title": "Pre-registered experiment and hypothesis",
         "data": {k: e.get(k) for k in ("id", "title", "faults", "expected_journeys", "hypothesis", "why")}},
        {"title": "Outcome and timing (computed from raw probe samples)",
         "data": {k: a.get(k) for k in ("outcome", "abort", "time_to_impact_s", "time_to_restore_s", "recovered",
                                        "recovery_time_s", "impacted_journeys", "unexpected_impacted_journeys",
                                        "self_healed")}},
        {"title": "Per-journey impact vs baseline",
         "data": {j: {"baseline_error_rate": v["baseline"]["error_rate"], "fault_error_rate": v["fault"]["error_rate"],
                      "baseline_p95_ms": v["baseline"]["p95_ms"], "fault_p95_ms": v["fault"]["p95_ms"],
                      "impacted": v["impacted"]} for j, v in a["journeys"].items()}},
        {"title": "Target container timeline (docker inspect, sampled)", "data": a.get("target_timeline", [])[:40]},
    ]
    if a.get("checks"):
        items.append({"title": "Business checks (state survives the failure?)", "data": a["checks"]})
    if ev:
        items.append({"title": "Compose configuration facts of target and its callers", "data": ev.get("compose_facts")})
        items.append({"title": "Jaeger error traces in the fault window", "data": ev.get("traces")})
        items.append({"title": "Container log lines matching error patterns", "data": ev.get("logs")})
    items.append({"title": "Severity score (method docs/SCORING.md)", "data": a.get("severity")})
    for i, it in enumerate(items, 1):
        it["id"] = f"E{i}"
    return items


def _numbers(obj: Any) -> set[float]:
    out: set[float] = set()
    if isinstance(obj, bool) or obj is None:
        return out
    if isinstance(obj, (int, float)):
        out |= {round(float(obj), 1), round(float(obj) * 100, 1)}  # rates may be quoted as %
        return out
    if isinstance(obj, str):
        out |= {round(float(x), 1) for x in re.findall(r"-?\d+(?:\.\d+)?", obj)}
        return out
    if isinstance(obj, dict):
        for v in obj.values():
            out |= _numbers(v)
    if isinstance(obj, list):
        for v in obj:
            out |= _numbers(v)
    return out


def unverified_numbers(text: str, items: list[dict[str, Any]]) -> list[str]:
    known = _numbers([it["data"] for it in items])
    bad = []
    for tok in re.findall(r"(?<![\w.])\d+(?:\.\d+)?(?![\w.])", text):
        v = round(float(tok), 1)
        if v in (0.0, 1.0, 2.0, 3.0) or v in known:
            continue  # small counts/ordinals are not measurements
        bad.append(tok)
    return sorted(set(bad))


def validate(result: dict[str, Any], items: list[dict[str, Any]]) -> list[str]:
    ids = {it["id"] for it in items}
    problems = []
    for cf in result["why"]["contributing_factors"]:
        problems += [f"unknown evidence id {x}" for x in cf["evidence_ids"] if x not in ids]
    for fx in result["fixes"]:
        if fx["pattern_id"] not in PATTERNS:
            problems.append(f"unknown pattern {fx['pattern_id']}")
        problems += [f"unknown evidence id {x}" for x in fx["evidence_ids"] if x not in ids]
    if not result["fixes"]:
        problems.append("no fixes")
    return problems


def _eid(items: list[dict[str, Any]], prefix: str) -> str:
    return next((it["id"] for it in items if it["title"].startswith(prefix)), "E2")


def rule_based(cfg: Config, run_dir: Path, items: list[dict[str, Any]]) -> dict[str, Any]:
    e_checks, e_timeline = _eid(items, "Business checks"), _eid(items, "Target container timeline")
    e_journeys, e_outcome = _eid(items, "Per-journey"), _eid(items, "Outcome")
    exp = read_json(run_dir / "experiment.json")["experiment"]
    a = read_json(run_dir / "analysis.json")
    f0 = exp["faults"][0]
    target, ftype = f0["target"], f0["fault_type"]
    dur = next((c for c in a.get("checks", []) if c.get("check") == "cart_durability"), None)
    healed = any(a.get("self_healed", {}).values())
    fixes = []
    if dur and dur.get("lost"):
        fixes.append(("DATA_PERSISTENCE", "P1", f"Enable append-only persistence for {target} on a named volume "
                      "(fixes/cart-store-crash.compose.yaml).", e_checks))
        fixes.append(("REPLICATION_FAILOVER", "P2", f"Run {target} with a replica and automatic failover.", e_checks))
    if ftype == "container-stop" and not healed:
        fixes.append(("SELF_HEALING_RESTART", "P1", f"Ensure {target} has a restart policy / is a supervised workload.",
                      e_timeline))
    if a["impacted_journeys"]:
        fixes.append(("GRACEFUL_DEGRADATION", "P2", "Keep non-dependent parts of impacted journeys working "
                      f"when {target} is unavailable.", e_journeys))
        fixes.append(("CIRCUIT_BREAKER", "P2", f"Fail fast on callers of {target} instead of waiting on timeouts.", e_journeys))
    if ftype in ("network-delay", "container-pause"):
        fixes.insert(0, ("TIMEOUTS_RETRIES_BACKOFF", "P1", f"Set deadlines below the journey SLO on calls to {target}.",
                         e_journeys))
    if ftype == "cpu-throttle":
        fixes.insert(0, ("CAPACITY_RIGHTSIZING", "P1", f"Right-size CPU for {target}.", e_journeys))
    if ftype == "traffic-spike":
        fixes.insert(0, ("LOAD_SHEDDING", "P1", "Shed or rate-limit excess load at frontend-proxy.", e_journeys))
    fixes.append(("SLO_ALERTING", "P3", "Alert on journey success rate, not only container health.", e_journeys))
    verdict = ("hypothesis_refuted" if (a["unexpected_impacted_journeys"] or (dur and dur.get("lost"))
                                        or a["recovered"] is False) else "hypothesis_confirmed")
    ev = read_json(run_dir / "evidence.json") if (run_dir / "evidence.json").exists() else {}
    facts = (ev.get("compose_facts") or {}).get(target, {})
    e_facts, e_traces = _eid(items, "Compose configuration"), _eid(items, "Jaeger")
    tl = [r for r in a.get("target_timeline", []) if r["target"] == target]
    crash = next((r for r in tl if r["status"] != "running"), None)
    back = next((r for r in tl if crash and r["t_s"] is not None and r["t_s"] > crash["t_s"] and r["status"] == "running"),
                None)
    callers = [s for s in (ev.get("callers") or []) if s != target]
    origin = ((ev.get("traces") or {}).get("origin_service_votes") or [[None]])[0][0]
    steps = [f"{ftype} {f0.get('params') or ''} injected on {target}"]
    if crash:
        steps.append(f"{target} went '{crash['status']}' (exit code {crash['exit_code']}) at +{crash['t_s']} s")
    if back:
        steps.append(f"Docker brought it back to 'running' at +{back['t_s']} s (restart count {back['restart_count']})")
    if a["impacted_journeys"]:
        steps.append(f"journeys {', '.join(a['impacted_journeys'])} failed from +{a['time_to_impact_s']} s; service "
                     f"restored after {a.get('time_to_restore_s')} s")
    if origin:
        steps.append(f"traces place the deepest failing span in {origin}")
    if dur:
        steps.append(f"after recovery {dur['lost']} of {dur['seeded']} pre-crash sentinel carts were gone")
    factors = []
    if dur and dur.get("lost"):
        no_vol = not facts.get("volumes")
        factors.append({"factor": f"{target} keeps state only in memory"
                        + (" (no volume, default command without append-only persistence)" if no_vol else ""),
                        "evidence_ids": [e_checks, e_facts]})
    if healed:
        factors.append({"factor": "restart policy restored the process, so container health looks green although data "
                        "was lost", "evidence_ids": [e_timeline]})
    if a["impacted_journeys"]:
        factors.append({"factor": f"{', '.join(callers) or 'callers'} hard-depend on {target}: no fallback while it is "
                        "down", "evidence_ids": [e_journeys, e_traces]})
    root = (f"{target} has no durable storage: a crash wipes its in-memory dataset even though Docker restarts it."
            if dur and dur.get("lost") else f"{target} became unavailable/degraded due to the injected {ftype} and its "
            f"callers have no fallback.")
    return {
        "verdict": verdict,
        "summary": f"{ftype} on {target}: impacted {', '.join(a['impacted_journeys']) or 'no journey'}"
                   + (f"; {dur['lost']}/{dur['seeded']} carts lost" if dur else "") + " (rule-based analysis).",
        "where": {"failed_component": target,
                  "propagation_path": [target, *callers, *[f"journey:{j}" for j in a["impacted_journeys"]]],
                  "impacted_journeys": a["impacted_journeys"]},
        "how": "; then ".join(steps) + ".",
        "why": {"root_cause": root,
                "contributing_factors": factors or [{"factor": "see evidence", "evidence_ids": [e_outcome, e_journeys]}]},
        "customer_impact": f"Impacted journeys: {a['impacted_journeys'] or 'none'}"
                           + (f"; carts lost: {dur['lost']}/{dur['seeded']}" if dur else ""),
        "detection_gap": "Container health checks do not measure journey success or data durability.",
        "fixes": [{"pattern_id": p, "change": c, "priority": pr, "expected_effect": PATTERNS[p]["when"],
                   "evidence_ids": [e]} for p, pr, c, e in fixes],
        "verification_plan": "Apply the P1 fix and re-run the same pre-registered experiment; compare with "
                             "`chaosctl compare`.",
    }


def explain(cfg: Config, run_dir: Path, use_ai: bool = True) -> dict[str, Any]:
    items = build_items(cfg, run_dir)
    meta: dict[str, Any] = {"method": "rule-based"}
    result = None
    if use_ai:
        try:
            data, ai_meta = ai.validated_call(f"incident-{run_dir.name}", SYSTEM,
                                              {"evidence": items,
                                               "pattern_catalog": {k: {"name": v["name"], "when": v["when"]}
                                                                   for k, v in PATTERNS.items()}},
                                              SCHEMA, lambda d: "; ".join(validate(d, items)) or None,
                                              cfg.artifacts_dir / "ai-cache", effort="high")
            prose = " ".join([data["summary"], data["how"], data["why"]["root_cause"], data["customer_impact"]])
            result = data
            meta = {"method": "ai", "ai_meta": ai_meta, "unverified_numbers": unverified_numbers(prose, items)}
        except ai.AIUnavailable as exc:
            meta = {"method": "rule-based", "ai_unavailable": str(exc)}
    if result is None:
        result = rule_based(cfg, run_dir, items)
    for fx in result["fixes"]:
        fx["pattern_name"] = PATTERNS[fx["pattern_id"]]["name"]
        fx["references"] = PATTERNS[fx["pattern_id"]]["refs"]
    out = {"generated": iso(), "run_id": run_dir.name, "meta": meta, "analysis": result, "evidence": items}
    write_json(run_dir / "incident.json", out)
    (run_dir / "incident.md").write_text(render_md(out))
    return out


def render_md(inc: dict[str, Any]) -> str:
    r, m = inc["analysis"], inc["meta"]
    how = (f"AI ({m['ai_meta'].get('model_served')}); evidence ids and pattern ids validated by code"
           + (f"; UNVERIFIED numbers in prose: {m['unverified_numbers']}" if m.get("unverified_numbers") else
              "; all numbers in prose found in evidence")
           if m["method"] == "ai" else f"rule-based ({m.get('ai_unavailable') or m.get('ai_rejected') or 'AI off'})")
    lines = [f"# Incident analysis — {inc['run_id']}", "", f"Method: {how}.", "",
             f"**Verdict:** {r['verdict'].replace('_', ' ')}", "", r["summary"], "",
             "## Where", f"- Failed component: **{r['where']['failed_component']}**",
             f"- Propagation: {' → '.join(r['where']['propagation_path'])}",
             f"- Impacted journeys: {', '.join(r['where']['impacted_journeys']) or 'none'}", "",
             "## How", r["how"], "", "## Why", f"**Root cause:** {r['why']['root_cause']}", ""]
    lines += [f"- {c['factor']} ({', '.join(c['evidence_ids'])})" for c in r["why"]["contributing_factors"]]
    lines += ["", "## Customer impact", r["customer_impact"], "", "## Detection gap", r["detection_gap"], "",
              "## Recommended fixes (industry patterns)", "",
              "| Priority | Pattern | Change | Expected effect | Evidence | References |", "|---|---|---|---|---|---|"]
    for fx in sorted(r["fixes"], key=lambda f: f["priority"]):
        refs = " ".join(f"[{i + 1}]({u})" for i, u in enumerate(fx["references"]))
        lines.append(f"| {fx['priority']} | {fx['pattern_name']} | {fx['change']} | {fx['expected_effect']} | "
                     f"{', '.join(fx['evidence_ids'])} | {refs} |")
    lines += ["", "## Verification plan", r["verification_plan"], "", "## Evidence index", ""]
    lines += [f"- **{it['id']}** {it['title']}" for it in inc["evidence"]]
    return "\n".join(lines) + "\n"
