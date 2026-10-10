"""Experiment building blocks: the safe candidate catalog, per-candidate evidence, experiment records, the
tripwire safety test and plan verification. The FMEA (fmea.py) rates and selects candidates; the AI may only
choose among these pre-validated candidates and cannot change targets or parameters. Plans are hashed
(pre-registration) and refused if edited."""

from __future__ import annotations

from typing import Any

from . import depmap, safety
from .config import Config
from .util import sha256_of


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


def verify_plan(plan: dict[str, Any]) -> None:
    if plan["meta"].get("status") != "executable":
        raise safety.SafetyError(f"plan status is {plan['meta'].get('status')!r}: {plan['meta'].get('problems')}")
    if sha256_of(plan["experiments"]) != plan["meta"]["sha256"]:
        raise safety.SafetyError("plan experiments changed after pre-registration (sha256 mismatch)")
