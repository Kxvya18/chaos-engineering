"""Experiment planning: deterministic candidate catalog -> AI (or fallback) ranking -> validated plan.

The AI may only SELECT and RANK pre-validated candidates and write reasons/hypotheses; it cannot
change targets or parameters. The plan is hashed (pre-registration) and `run` refuses edited plans.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ai, depmap, safety
from .config import Config
from .util import iso, read_json, sha256_of, write_json

PLAN_SYSTEM = """You are the experiment planner of a chaos-engineering tool for the OpenTelemetry Demo running \
in an isolated sandbox. Choose the most informative and riskiest experiments first, using the dependency map \
(fan-in, datastores, missing healthchecks/depends_on, unverified edges), the measured baseline and results of \
previous runs. Select between 3 and 6 candidates covering at least 3 distinct fault_type values. You may only \
reference candidate_id values that appear in the input; you cannot change parameters. For each selection give \
a one-sentence reason that cites concrete evidence from the input, and a falsifiable hypothesis stating the \
expected customer-journey impact and recovery. Do not invent data that is not in the input."""

PLAN_SCHEMA = {
    "type": "object",
    "properties": {
        "selections": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "candidate_id": {"type": "string"},
                    "rank": {"type": "integer"},
                    "reason": {"type": "string"},
                    "hypothesis": {"type": "string"},
                },
                "required": ["candidate_id", "rank", "reason", "hypothesis"],
                "additionalProperties": False,
            },
        },
        "strategy_note": {"type": "string"},
    },
    "required": ["selections", "strategy_note"],
    "additionalProperties": False,
}


def _svc(dep_map: dict[str, Any], name: str) -> dict[str, Any]:
    return next((s for s in dep_map.get("services", []) if s["name"] == name), {})


def candidates(cfg: Config, dep_map: dict[str, Any]) -> list[dict[str, Any]]:
    out = []
    for target in cfg.allowlist:
        is_ds = target in cfg.datastores
        expected = depmap.journeys_touching(cfg, target)
        specs = [("container-stop", {"mode": "crash"}, 60)]  # real crash: exercises the restart policy
        if is_ds:
            specs += [("container-pause", {}, 45), ("cpu-throttle", {"cpus": 0.1}, 60),
                      ("network-delay", {"delay_ms": 300, "jitter_ms": 50}, 60)]
        else:
            specs += [("network-delay", {"delay_ms": 500, "jitter_ms": 100}, 60),
                      ("cpu-throttle", {"cpus": 0.1}, 60)]
        for ftype, params, dur in specs:
            out.append({"candidate_id": f"{ftype}:{target}", "fault_type": ftype, "target": target,
                        "params": params, "duration_s": dur, "expected_journeys": expected,
                        "kind": "datastore" if is_ds else "app"})
    out.append({"candidate_id": "traffic-spike:load-generator", "fault_type": "traffic-spike",
                "target": "load-generator", "params": {"users": 40, "spawn_rate": 5}, "duration_s": 120,
                "expected_journeys": sorted(cfg.journeys), "kind": "load"})
    for c in out:
        f = {k: c[k] for k in ("fault_type", "target", "params", "duration_s")}
        safety.validate_fault(cfg, f)  # every candidate is safe by construction
    return out


def evidence(cfg: Config, dep_map: dict[str, Any], c: dict[str, Any], history: list[dict[str, Any]]) -> dict[str, Any]:
    t = c["target"]
    svc = _svc(dep_map, t)
    fan_in = len(depmap.dependents(dep_map, t)) if t != "load-generator" else None
    weight = sum(cfg.journeys[j].weight for j in c["expected_journeys"])
    tried = sum(1 for h in history if h.get("fault_type") == c["fault_type"] and h.get("target") == t)
    drift = dep_map.get("drift", {})
    return {
        "fan_in": fan_in,
        "journey_weight": round(weight, 2),
        "journeys": c["expected_journeys"],
        "datastore": c["kind"] == "datastore",
        "no_healthcheck": svc.get("healthcheck") is False,
        "missing_depends_on": any(x.endswith(f"->{t}") for x in drift.get("missing_depends_on", [])),
        "times_run_before": tried,
    }


def score(ev: dict[str, Any]) -> float:
    """Deterministic fallback ranking (documented in docs/SCORING.md)."""
    s = 2.0 * ev["journey_weight"] + 0.5 * (ev["fan_in"] or 0)
    s += 1.5 if ev["datastore"] else 0
    s += 1.0 if ev["no_healthcheck"] else 0
    s += 1.0 if ev["missing_depends_on"] else 0
    s -= 3.0 * ev["times_run_before"]
    return round(s, 3)


def fallback_select(scored: list[dict[str, Any]], n: int = 4) -> list[dict[str, Any]]:
    ranked = sorted(scored, key=lambda c: (-c["score"], c["candidate_id"]))
    chosen: list[dict[str, Any]] = []
    types: set[str] = set()
    # first pass: best candidate per distinct fault type until we have 3 types
    for c in ranked:
        if c["fault_type"] not in types and len(types) < 3:
            chosen.append(c)
            types.add(c["fault_type"])
    for c in ranked:
        if len(chosen) >= n:
            break
        if c not in chosen and c["target"] not in {x["target"] for x in chosen}:
            chosen.append(c)
    chosen.sort(key=lambda c: (-c["score"], c["candidate_id"]))
    return [{"candidate_id": c["candidate_id"], "rank": i + 1,
             "reason": (f"Rule-based: score {c['score']} (journey weight {c['evidence']['journey_weight']}, "
                        f"fan-in {c['evidence']['fan_in']}, datastore={c['evidence']['datastore']}, "
                        f"no_healthcheck={c['evidence']['no_healthcheck']}, "
                        f"missing_depends_on={c['evidence']['missing_depends_on']})."),
             "hypothesis": (f"{c['fault_type']} on {c['target']} degrades {', '.join(c['expected_journeys']) or 'no journey'}"
                            f" only, and all journeys return to baseline band within "
                            f"{60}s of rollback.")}
            for i, c in enumerate(chosen)]


def validate_selection(sel: list[dict[str, Any]], by_id: dict[str, Any]) -> str | None:
    if not 3 <= len(sel) <= 6:
        return f"expected 3-6 selections, got {len(sel)}"
    ids = [s["candidate_id"] for s in sel]
    if len(set(ids)) != len(ids):
        return "duplicate candidate_id"
    unknown = [i for i in ids if i not in by_id]
    if unknown:
        return f"unknown candidate_id(s): {unknown}"
    if len({by_id[i]["fault_type"] for i in ids}) < 3:
        return "fewer than 3 distinct fault types"
    if sorted(s["rank"] for s in sel) != list(range(1, len(sel) + 1)):
        return "ranks must be 1..n"
    if any(not s["reason"].strip() or not s["hypothesis"].strip() for s in sel):
        return "empty reason or hypothesis"
    return None


def experiment_from(cfg: Config, c: dict[str, Any], s: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": f"E{s['rank']:02d}-{c['fault_type']}-{c['target']}",
        "rank": s["rank"],
        "faults": [{k: c[k] for k in ("fault_type", "target", "params", "duration_s")}],
        "expected_journeys": c["expected_journeys"],
        "reason": s["reason"],
        "hypothesis": s["hypothesis"],
        "evidence": c["evidence"],
        "abort_conditions": "docs/SAFETY.md A1-A6 (config/sandbox.toml [watchdog])",
        "rollback": f"{c['fault_type']} adapter rollback (idempotent), watchdog + kill switch backed",
        "success_criteria": "no unexpected journey impacted; recovered within recovery.timeout_s",
    }


def tripwire(cfg: Config) -> dict[str, Any]:
    """Deliberate safety test: inject latency while declaring NO expected impact -> A1 must abort."""
    return {
        "id": "TRIPWIRE-network-delay-product-catalog",
        "rank": 0,
        "faults": [{"fault_type": "network-delay", "target": "product-catalog",
                    "params": {"delay_ms": 1500, "jitter_ms": 0}, "duration_s": 90}],
        "expected_journeys": [],
        "reason": "Safety validation: proves the watchdog aborts and rolls back on a blast-radius breach.",
        "hypothesis": "Watchdog aborts (A1) within ~15 s of injection and the fault is rolled back automatically.",
        "abort_conditions": "A1 expected to fire", "rollback": "network-delay adapter rollback",
        "success_criteria": "ABORT.json written by watchdog; recovered after rollback",
    }


def make_plan(cfg: Config, dep_map_path: Path, baseline_path: Path, history: list[dict[str, Any]],
              use_ai: bool = True) -> dict[str, Any]:
    status, problems = "executable", []
    dep_map = read_json(dep_map_path) if dep_map_path.exists() else {}
    baseline = read_json(baseline_path) if baseline_path.exists() else None
    if not dep_map:
        status, problems = "draft", problems + [f"missing dependency map {dep_map_path}"]
    if not baseline:
        status, problems = "draft", problems + [f"missing baseline {baseline_path}"]
    cands = candidates(cfg, dep_map)
    for c in cands:
        c["evidence"] = evidence(cfg, dep_map, c, history)
        c["score"] = score(c["evidence"])
    by_id = {c["candidate_id"]: c for c in cands}

    planner_meta: dict[str, Any] = {"method": "rule-based"}
    selection = None
    if use_ai:
        payload = {
            "candidates": [{k: c[k] for k in ("candidate_id", "fault_type", "target", "params", "duration_s",
                                              "expected_journeys", "evidence", "score")} for c in cands],
            "dependency_drift": dep_map.get("drift"),
            "baseline_overall": (baseline or {}).get("overall"),
            "baseline_journeys": (baseline or {}).get("journeys"),
            "previous_runs": history,
        }
        try:
            data, meta = ai.structured_call(PLAN_SYSTEM, payload, PLAN_SCHEMA, effort="medium")
            err = validate_selection(data["selections"], by_id)
            if err:
                planner_meta = {"method": "rule-based", "ai_rejected": err, "ai_meta": meta}
            else:
                selection = sorted(data["selections"], key=lambda s: s["rank"])
                planner_meta = {"method": "ai", "ai_meta": meta, "strategy_note": data.get("strategy_note")}
        except ai.AIUnavailable as exc:
            planner_meta = {"method": "rule-based", "ai_unavailable": str(exc)}
    if selection is None:
        selection = fallback_select(cands)
    experiments = [experiment_from(cfg, by_id[s["candidate_id"]], s) for s in selection]
    for e in experiments:
        safety.validate_experiment(cfg, e)
    plan = {"meta": {"status": status, "problems": problems, "created": iso(), "planner": planner_meta,
                     "inputs": {"dependency_map": str(dep_map_path), "baseline": str(baseline_path)},
                     "sha256": sha256_of(experiments)},
            "experiments": experiments,
            "candidates_considered": len(cands)}
    return plan


def verify_plan(plan: dict[str, Any]) -> None:
    if plan["meta"].get("status") != "executable":
        raise safety.SafetyError(f"plan status is {plan['meta'].get('status')!r}: {plan['meta'].get('problems')}")
    if sha256_of(plan["experiments"]) != plan["meta"]["sha256"]:
        raise safety.SafetyError("plan experiments changed after pre-registration (sha256 mismatch)")


def save(plan: dict[str, Any], path: Path) -> None:
    write_json(path, plan)
