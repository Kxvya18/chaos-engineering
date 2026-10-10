"""AI-generated, chaos-validated FMEA (Failure Mode and Effects Analysis).

1. The AI writes an FMEA for the running system: for each injectable failure mode (from the safe candidate
   catalog) it predicts Severity, Occurrence and Detection (1-10) with a rationale; code computes RPN = S x O x D
   and the AI selects which failure modes to inject first (highest risk / most informative).
2. The deterministic engine injects them under the safety controls.
3. Each run MEASURES severity (customer-journey impact) and detection (health-check blind spot), and the FMEA
   row is updated: predicted vs measured. Occurrence cannot be measured by one experiment and stays predicted.

Rating scales follow the usual FMEA convention (1 = best, 10 = worst). RPN is the classic IEC 60812 prioritisation;
the AIAG-VDA (2019) Action Priority table is not implemented.
"""

from __future__ import annotations

import math
from pathlib import Path
from typing import Any

from . import ai, planner, safety
from .config import Config
from .util import iso, read_json, sha256_of, write_json

SCALES = {
    "severity": "1 = no customer-visible effect ... 5 = a low-value journey degraded ... 8 = cart or checkout fails "
                "... 10 = checkout fails or customer data is lost",
    "occurrence": "1 = practically impossible in production ... 5 = plausible a few times per year ... "
                  "10 = expected often (e.g. tight memory limit, single instance, no restart)",
    "detection": "1 = existing health checks/alerts certainly detect it before customers notice ... "
                 "10 = current monitoring cannot detect it (customers notice first)",
}

SYSTEM = """You are a reliability engineer at an automotive/industrial company performing an FMEA (Failure Mode and \
Effects Analysis) for a cloud-native microservice application (the OpenTelemetry Demo shop) that runs in an isolated \
sandbox. You receive the dependency map facts, the measured baseline, the customer journeys with business weights, \
and a catalog of failure modes that the chaos engine can inject safely (each has a candidate_id). For the 12 most \
relevant catalog entries, write one FMEA row: the effect on customer journeys, Severity, Occurrence and Detection \
ratings (integers 1-10, using the provided scales) and a short rationale that cites concrete facts from the input \
(fan-in, memory limits, missing health checks, datastore, single instance, async paths). Detection must reflect what \
Docker health checks and container status can see: they do not observe latency or data loss. Then select 3 rows to \
inject first, covering at least 2 different fault types, preferring the highest risk and the most informative \
experiments, each with a one-sentence reason and a falsifiable hypothesis. Use only candidate_id values from the \
catalog. Do not invent facts."""

SCHEMA = {
    "type": "object",
    "properties": {
        "rows": {"type": "array", "items": {
            "type": "object",
            "properties": {
                "candidate_id": {"type": "string"},
                "effect": {"type": "string"},
                "affected_journeys": {"type": "array", "items": {"type": "string"}},
                "severity": {"type": "integer"}, "occurrence": {"type": "integer"}, "detection": {"type": "integer"},
                "rationale": {"type": "string"},
            },
            "required": ["candidate_id", "effect", "affected_journeys", "severity", "occurrence", "detection",
                         "rationale"],
            "additionalProperties": False}},
        "selected": {"type": "array", "items": {
            "type": "object",
            "properties": {"candidate_id": {"type": "string"}, "rank": {"type": "integer"},
                           "reason": {"type": "string"}, "hypothesis": {"type": "string"}},
            "required": ["candidate_id", "rank", "reason", "hypothesis"], "additionalProperties": False}},
        "summary": {"type": "string"},
    },
    "required": ["rows", "selected", "summary"],
    "additionalProperties": False,
}

FAULT_NAMES = {"container-stop": "crash / unavailable", "container-pause": "hang (process frozen)",
               "network-delay": "slow network", "cpu-throttle": "CPU starvation", "traffic-spike": "traffic surge"}


def mode_name(c: dict[str, Any]) -> str:
    """Failure-mode label: the database error it reproduces (short form) or the generic fault name."""
    return c["db_error"].split(":")[0] if c.get("db_error") else FAULT_NAMES[c["fault_type"]]


def _mem_mib(limit: Any) -> float | None:
    if limit is None:
        return None
    s = str(limit).strip().upper()
    try:
        if s.endswith("G"):
            return float(s[:-1]) * 1024
        if s.endswith("M"):
            return float(s[:-1])
        return float(s) / 2**20
    except ValueError:
        return None


def rule_based_row(cfg: Config, c: dict[str, Any], node: dict[str, Any]) -> dict[str, Any]:
    """Deterministic FMEA ratings used when the AI is unavailable (and as the documented baseline method)."""
    w = max((cfg.journeys[j].weight for j in c["expected_journeys"]), default=0.2)
    sev = max(1, round(10 * w)) - (2 if c["fault_type"] in ("network-delay", "cpu-throttle") else 0)
    stateless_store = c["kind"] == "datastore" and "volumes" in node and not node["volumes"]  # known, not assumed
    data_loss = c["fault_type"] == "container-stop" and stateless_store
    if data_loss:
        sev = 10  # datastore without a volume: a crash can wipe customer data
    occ = 3 + (2 if (_mem_mib(node.get("memory_limit")) or 999) <= 32 else 0) + (1 if node.get("healthcheck") is False else 0) \
        + (1 if c["kind"] == "datastore" else 0)
    det = {"container-stop": 3 if node.get("healthcheck") else 5, "container-pause": 5 if node.get("healthcheck") else 8,
           "network-delay": 9, "cpu-throttle": 8, "traffic-spike": 7}[c["fault_type"]]
    if data_loss:
        det = 8  # the restart policy turns health green again; lost data is invisible to health checks
    return {"candidate_id": c["candidate_id"], "effect": f"{mode_name(c)} of {c['target']} degrades "
            f"{', '.join(c['expected_journeys']) or 'no journey'}", "affected_journeys": c["expected_journeys"],
            "severity": max(1, min(10, sev)), "occurrence": max(1, min(10, occ)), "detection": det,
            "rationale": f"rule-based: journey weight {w}, memory limit {node.get('memory_limit')}, "
                         f"healthcheck={node.get('healthcheck')}, kind={c['kind']}"
                         + (", datastore without a volume (data-loss risk)" if data_loss else "")}


def validate(data: dict[str, Any], by_id: dict[str, Any]) -> str | None:
    if not data["rows"]:
        return "no FMEA rows"
    for r in data["rows"]:
        if r["candidate_id"] not in by_id:
            return f"unknown candidate_id {r['candidate_id']}"
        for k in ("severity", "occurrence", "detection"):
            if not 1 <= r[k] <= 10:
                return f"{k} out of 1..10 for {r['candidate_id']}"
    sel = data["selected"]
    row_ids = {r["candidate_id"] for r in data["rows"]}
    if not 1 <= len(sel) <= 5 or any(s["candidate_id"] not in row_ids for s in sel):
        return "selection must be 1-5 rows taken from the FMEA"
    if len({s["candidate_id"] for s in sel}) != len(sel):
        return "duplicate selection"
    if len(sel) >= 2 and len({by_id[s["candidate_id"]]["fault_type"] for s in sel}) < 2:
        return "selection must cover at least 2 fault types"
    return None


def build(cfg: Config, dep_map_path: Path, baseline_path: Path, use_ai: bool = True,
          coverage: bool = False, fault_seconds: int | None = None, suite: str = "priority") -> dict[str, Any]:
    """coverage=True (mission mode): after the planner's own picks, add the highest-RPN failure of every fault type
    not yet chosen, so each failure type is tested in priority order. Additions are labelled 'coverage'."""
    dep_map = read_json(dep_map_path) if dep_map_path.exists() else {}
    baseline = read_json(baseline_path) if baseline_path.exists() else None
    problems = []
    if not dep_map:
        problems.append(f"missing dependency map {dep_map_path}")
    if not baseline:
        problems.append(f"missing baseline {baseline_path}")
    elif baseline.get("quality") and not baseline["quality"]["pass"]:
        problems.append("baseline failed its quality gate (system not in steady state)")
    nodes = {s["name"]: s for s in dep_map.get("services", [])}
    cands = planner.candidates(cfg, dep_map)
    for c in cands:
        c["evidence"] = planner.evidence(cfg, dep_map, c, [])
    by_id = {c["candidate_id"]: c for c in cands}

    meta: dict[str, Any] = {"method": "rule-based", "ai_unavailable": "AI not requested (--no-ai)"}
    data = None
    if use_ai:
        payload = {
            "rating_scales": SCALES,
            "journeys": {j.name: {"weight": j.weight, "services": j.services} for j in cfg.journeys.values()},
            "services": [{k: s.get(k) for k in ("name", "kind", "healthcheck", "restart", "memory_limit", "volumes")}
                         for s in dep_map.get("services", [])],
            "dependency_gaps": dep_map.get("drift"),
            "baseline": {"overall": (baseline or {}).get("overall"), "journeys": (baseline or {}).get("journeys")},
            "catalog": [{k: c[k] for k in ("candidate_id", "fault_type", "target", "params", "duration_s",
                                           "expected_journeys", "evidence")} for c in cands],
        }
        try:
            got, ai_meta = ai.validated_call("fmea", SYSTEM, payload, SCHEMA, lambda d: validate(d, by_id),
                                             cfg.artifacts_dir / "ai-cache", effort="high")
            data = got
            meta = {"method": "ai-cached" if ai_meta.get("cached") else "ai", "ai_meta": ai_meta,
                    "summary": got["summary"]}
        except ai.AIUnavailable as exc:
            meta = {"method": "rule-based", "ai_unavailable": str(exc)}
    if data is None:
        rows = [rule_based_row(cfg, c, nodes.get(c["target"], {})) for c in cands]
        rows.sort(key=lambda r: -(r["severity"] * r["occurrence"] * r["detection"]))
        chosen, types, targets = [], set(), set()
        for r in rows:  # highest RPN, each a different fault type AND a different component (more informative)
            c = by_id[r["candidate_id"]]
            if c["fault_type"] not in types and c["target"] not in targets and c["fault_type"] != "traffic-spike" \
                    and len(chosen) < 3:
                chosen.append(r)
                types.add(c["fault_type"])
                targets.add(c["target"])
        data = {"rows": rows[:12] + [r for r in chosen if r not in rows[:12]], "summary": "rule-based FMEA",
                "selected": [{"candidate_id": r["candidate_id"], "rank": i + 1,
                              "reason": f"highest risk score among {by_id[r['candidate_id']]['fault_type']} failures "
                                        f"(rule-based fallback)",
                              "hypothesis": f"{r['effect']}; other journeys stay within baseline; service recovers "
                                            f"within {cfg.recovery['timeout_s']} s of rollback"}
                             for i, r in enumerate(chosen)]}
    for r in data["rows"]:
        r["rpn"] = r["severity"] * r["occurrence"] * r["detection"]
        c = by_id[r["candidate_id"]]
        r.update(component=c["target"], fault_type=c["fault_type"], failure_mode=mode_name(c), measured=None)
    data["rows"].sort(key=lambda r: -r["rpn"])
    source = "ai" if meta["method"].startswith("ai") else "rule-based"
    for sel in data["selected"]:
        sel.setdefault("source", source)
    if coverage:
        row_by = {r["candidate_id"]: r for r in data["rows"]}
        covered = {by_id[x["candidate_id"]]["fault_type"] for x in data["selected"]}
        extra = []
        for ftype in FAULT_NAMES:
            pool = [c for c in cands if c["fault_type"] == ftype]
            if ftype in covered or not pool:
                continue
            for c in pool:  # rate anything the planner left unrated, with the documented rule-based scale
                if c["candidate_id"] not in row_by:
                    r = rule_based_row(cfg, c, nodes.get(c["target"], {}))
                    r.update(rpn=r["severity"] * r["occurrence"] * r["detection"], component=c["target"],
                             fault_type=ftype, failure_mode=mode_name(c), measured=None)
                    data["rows"].append(r)
                    row_by[c["candidate_id"]] = r
            extra.append(max((row_by[c["candidate_id"]] for c in pool), key=lambda r: r["rpn"]))
        n = len(data["selected"])
        for i, r in enumerate(sorted(extra, key=lambda r: -r["rpn"])):
            data["selected"].append({"candidate_id": r["candidate_id"], "rank": n + i + 1, "source": "coverage",
                                     "reason": f"coverage: highest-risk {r['failure_mode']} failure (RPN {r['rpn']}), "
                                               "so every failure type is tested",
                                     "hypothesis": f"{r['effect']}; other journeys stay within baseline; service "
                                                   f"recovers within {cfg.recovery['timeout_s']} s of rollback"})
        data["rows"].sort(key=lambda r: -r["rpn"])
    if suite == "database":  # every database error on every datastore, highest risk first
        row_by = {r["candidate_id"]: r for r in data["rows"]}
        for c in cands:
            if c["kind"] == "datastore" and c["candidate_id"] not in row_by:
                r = rule_based_row(cfg, c, nodes.get(c["target"], {}))
                r.update(rpn=r["severity"] * r["occurrence"] * r["detection"], component=c["target"],
                         fault_type=c["fault_type"], failure_mode=mode_name(c), measured=None)
                data["rows"].append(r)
                row_by[c["candidate_id"]] = r
        db = sorted((row_by[c["candidate_id"]] for c in cands if c["kind"] == "datastore"), key=lambda r: -r["rpn"])
        data["selected"] = [{"candidate_id": r["candidate_id"], "rank": i + 1, "source": "database-suite",
                             "reason": f"database error catalog: {by_id[r['candidate_id']]['db_error']} (RPN {r['rpn']})",
                             "hypothesis": f"{r['effect']}; other journeys stay within baseline; service recovers "
                                           f"within {cfg.recovery['timeout_s']} s of rollback"} for i, r in enumerate(db)]
        data["rows"].sort(key=lambda r: -r["rpn"])
    experiments = []
    for s in sorted(data["selected"], key=lambda s: s["rank"]):
        c = by_id[s["candidate_id"]]
        exp = planner.experiment_from(cfg, c, s)
        exp["candidate_id"] = c["candidate_id"]
        exp["title"] = f"{mode_name(c).capitalize()} of {c['target']} (FMEA rank {s['rank']})"
        exp["why"] = s["reason"]
        exp["source"] = s.get("source")
        if fault_seconds:  # shorter fault windows for a demo; still bounded by the safety limits below
            for f in exp["faults"]:
                f["duration_s"] = int(fault_seconds)
        if c["target"] in ("valkey-cart", "cart"):
            exp["checks"] = ["cart_durability"]  # does customer state survive this failure?
        safety.validate_experiment(cfg, exp)
        experiments.append(exp)
    status = "draft" if problems else "executable"
    fmea = {"generated": iso(), "meta": meta, "status": status, "problems": problems, "scales": SCALES,
            "rows": data["rows"], "selected": data["selected"]}
    plan = {"meta": {"status": status, "problems": problems, "created": iso(), "planner": {**meta, "via": "fmea"},
                     "inputs": {"dependency_map": str(dep_map_path), "baseline": str(baseline_path)},
                     "sha256": sha256_of(experiments)},
            "experiments": experiments, "candidates_considered": len(cands)}
    return {"fmea": fmea, "plan": plan}


def measured_ratings(a: dict[str, Any]) -> dict[str, Any]:
    """Map one run's measurements onto the FMEA scales (documented in docs/SCORING.md)."""
    score = a["severity"]["score"] or 0
    sev = max(1, min(10, math.ceil(score / 10)))
    if any(c.get("lost") for c in a.get("checks", [])):
        sev = 10  # customer data lost
    bs = a.get("health_blind_spot") or {}
    if not bs.get("customer_impact_s"):
        det = 1  # no customer impact: nothing to detect
    else:
        det = max(1, min(10, round(10 - 8 * (bs.get("covered_pct") or 0) / 100)))
    return {"severity": sev, "detection": det, "run_id": a["run_id"], "severity_score": a["severity"]["score"],
            "blind_spot": bs, "impacted_journeys": a["impacted_journeys"], "time_to_restore_s": a.get("time_to_restore_s")}


def update_from_run(cfg: Config, run_dir: Path, fmea_path: Path) -> dict[str, Any] | None:
    if not fmea_path.exists() or not (run_dir / "analysis.json").exists():
        return None
    exp = read_json(run_dir / "experiment.json")["experiment"]
    cid = exp.get("candidate_id")
    if not cid:
        return None
    fmea = read_json(fmea_path)
    row = next((r for r in fmea["rows"] if r["candidate_id"] == cid), None)
    if row is None:
        return None
    m = measured_ratings(read_json(run_dir / "analysis.json"))
    m["rpn"] = m["severity"] * row["occurrence"] * m["detection"]
    row["measured"] = m
    write_json(fmea_path, fmea)
    return row


def render_md(fmea: dict[str, Any]) -> str:
    m = fmea["meta"]
    lines = ["# FMEA — AI-generated, chaos-validated", "",
             f"Generated {fmea['generated']} · method: {m['method']}"
             + (f" ({m['ai_meta'].get('model_served')})" if m.get("ai_meta") else "")
             + (f" · AI not used: {m.get('ai_unavailable') or m.get('ai_rejected')}" if m["method"] != "ai" else ""), "",
             "Scales (1 best … 10 worst): " + "; ".join(f"**{k}** {v}" for k, v in fmea["scales"].items()), "",
             "| Component | Failure mode | Effect | S | O | D | RPN | Measured S | Measured D | Measured RPN | Run |",
             "|---|---|---|---|---|---|---|---|---|---|---|"]
    for r in fmea["rows"]:
        me = r.get("measured") or {}
        lines.append(f"| {r['component']} | {r['failure_mode']} | {r['effect']} | {r['severity']} | {r['occurrence']} | "
                     f"{r['detection']} | **{r['rpn']}** | {me.get('severity', '—')} | {me.get('detection', '—')} | "
                     f"{me.get('rpn', '—')} | {me.get('run_id', '')} |")
    lines += ["", "## Selected for injection (by the planner)", ""]
    lines += [f"{s['rank']}. `{s['candidate_id']}` — {s['reason']}  \n   Hypothesis: {s['hypothesis']}" for s in fmea["selected"]]
    return "\n".join(lines) + "\n"


def run(cfg: Config, use_ai: bool = True, coverage: bool = False, fault_seconds: int | None = None,
        suite: str = "priority") -> dict[str, Any]:
    out = build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json", use_ai, coverage,
                fault_seconds, suite)
    write_json(cfg.artifacts_dir / "fmea.json", out["fmea"])
    write_json(cfg.artifacts_dir / "experiment-plan.json", out["plan"])
    (cfg.root / "docs").mkdir(exist_ok=True)
    (cfg.root / "docs" / "FMEA.md").write_text(render_md(out["fmea"]))
    return out

