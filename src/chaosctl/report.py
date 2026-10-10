"""Findings report: AI explains/ranks weaknesses; code verifies every cited number.

Claims whose cited metric does not match analysis.json are rejected and listed separately.
Without AI, a rule-based report is produced from the same analyses (clearly labelled).
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

from . import ai
from .analysis import rule_fixes
from .config import Config
from .util import iso, read_json, sha256_file, write_json

REPORT_SYSTEM = """You are a senior SRE writing the findings of chaos experiments run against the OpenTelemetry \
Demo in an isolated sandbox. Use ONLY the evidence in the input. Rank weaknesses from most to least severe. \
Every finding must cite evidence as (run_id, metric, value) where metric is a dot path that exists in that run's \
analysis object exactly as given (for example "journeys.checkout.impact.error_rate_delta_pp" or \
"recovery_time_s") and value is copied verbatim. Keep observations (what was measured) separate from \
hypotheses (why it happened). Recommend concrete, specific fixes. Treat runs whose experiment_id starts with \
TRIPWIRE as safety-control validation, not as application weaknesses."""

SEVERITIES = ["low", "medium", "high", "critical"]

REPORT_SCHEMA = {
    "type": "object",
    "properties": {
        "executive_summary": {"type": "string"},
        "findings": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "title": {"type": "string"},
                    "severity": {"type": "string", "enum": SEVERITIES},
                    "run_ids": {"type": "array", "items": {"type": "string"}},
                    "evidence": {
                        "type": "array",
                        "items": {
                            "type": "object",
                            "properties": {"run_id": {"type": "string"}, "metric": {"type": "string"},
                                           "value": {"type": "string"}},
                            "required": ["run_id", "metric", "value"],
                            "additionalProperties": False,
                        },
                    },
                    "observation": {"type": "string"},
                    "hypothesis": {"type": "string"},
                    "recommended_fixes": {"type": "array", "items": {"type": "string"}},
                    "confidence": {"type": "string", "enum": ["low", "medium", "high"]},
                },
                "required": ["title", "severity", "run_ids", "evidence", "observation", "hypothesis",
                             "recommended_fixes", "confidence"],
                "additionalProperties": False,
            },
        },
    },
    "required": ["executive_summary", "findings"],
    "additionalProperties": False,
}


def load_analyses(cfg: Config) -> list[tuple[Path, dict[str, Any]]]:
    out = []
    for p in sorted(cfg.runs_dir.glob("*/analysis.json")):
        out.append((p, read_json(p)))
    return out


def is_tripwire(a: dict[str, Any]) -> bool:
    return a["experiment_id"].startswith("TRIPWIRE")


def scored(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [a for a in analyses if a["severity"]["score"] is not None and not is_tripwire(a)]


def resilience_score(analyses: list[dict[str, Any]]) -> float | None:
    s = scored(analyses)
    if not s:
        return None
    return round(100 - sum(a["severity"]["score"] for a in s) / len(s), 1)


def lookup(obj: Any, path: str) -> tuple[bool, Any]:
    cur = obj
    for part in path.split("."):
        if isinstance(cur, dict) and part in cur:
            cur = cur[part]
        elif isinstance(cur, list) and part.isdigit() and int(part) < len(cur):
            cur = cur[int(part)]
        else:
            return False, None
    return True, cur


def value_matches(actual: Any, claimed: str) -> bool:
    if isinstance(actual, bool) or actual is None:
        return str(actual).lower() == claimed.strip().lower() or (actual is None and claimed.strip() in ("null", "n/a"))
    if isinstance(actual, (int, float)):
        try:
            c = float(claimed.strip().rstrip("%s").replace("ms", "").strip())
        except ValueError:
            return False
        return abs(c - actual) <= max(0.01, 0.01 * abs(actual))
    return str(actual) == claimed.strip()


def check_citations(finding: dict[str, Any], by_run: dict[str, dict[str, Any]]) -> list[str]:
    problems = []
    if not finding["evidence"]:
        problems.append("no evidence cited")
    for ev in finding["evidence"]:
        a = by_run.get(ev["run_id"])
        if a is None:
            problems.append(f"unknown run_id {ev['run_id']}")
            continue
        found, actual = lookup(a, ev["metric"])
        if not found:
            problems.append(f"{ev['run_id']}: metric {ev['metric']} does not exist")
        elif not value_matches(actual, ev["value"]):
            problems.append(f"{ev['run_id']}: {ev['metric']} cited {ev['value']!r} but analysis has {actual!r}")
    return problems


def evidence_pack(analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    keep = ("run_id", "experiment_id", "faults", "hypothesis", "outcome", "abort", "time_to_impact_s", "recovered",
            "recovery_time_s", "expected_journeys", "impacted_journeys", "unexpected_impacted_journeys",
            "self_healed", "journeys", "phases", "severity")
    return [{k: a.get(k) for k in keep} for a in analyses]


def rule_based_findings(cfg: Config, analyses: list[dict[str, Any]]) -> list[dict[str, Any]]:
    out = []
    for a in sorted(scored(analyses), key=lambda a: -a["severity"]["score"]):
        f0 = a["faults"][0]
        worst = max(a["journeys"].items(), key=lambda kv: kv[1]["impact"]["error_rate_delta_pp"] or 0)
        ev = [{"run_id": a["run_id"], "metric": f"journeys.{worst[0]}.impact.error_rate_delta_pp",
               "value": str(worst[1]["impact"]["error_rate_delta_pp"])},
              {"run_id": a["run_id"], "metric": "recovery_time_s", "value": str(a["recovery_time_s"])}]
        out.append({
            "title": f"{f0['fault_type']} on {f0['target']}: impacted {', '.join(a['impacted_journeys']) or 'no journey'}",
            "severity": a["severity"]["label"], "run_ids": [a["run_id"]], "evidence": ev,
            "observation": (f"Impacted journeys: {a['impacted_journeys'] or 'none'}; unexpected: "
                            f"{a['unexpected_impacted_journeys'] or 'none'}; time to impact "
                            f"{a['time_to_impact_s']} s; recovered={a['recovered']} in {a['recovery_time_s']} s."),
            "hypothesis": "Rule-based: callers lack timeouts/fallbacks for this dependency (verify in code/traces).",
            "recommended_fixes": rule_fixes(cfg, f0), "confidence": "medium",
        })
    return out


def generate(cfg: Config, use_ai: bool = True) -> dict[str, Any]:
    pairs = load_analyses(cfg)
    analyses = [a for _, a in pairs]
    by_run = {a["run_id"]: a for a in analyses}
    provenance = [{"run_id": a["run_id"], "analysis": str(p), "sha256": sha256_file(p)} for p, a in pairs]
    meta: dict[str, Any] = {"method": "rule-based"}
    accepted, rejected = [], []
    if use_ai and scored(analyses):
        try:
            data, ai_meta = ai.structured_call(REPORT_SYSTEM, {"runs": evidence_pack(analyses)}, REPORT_SCHEMA,
                                               effort="high")
            for i, f in enumerate(data["findings"]):
                problems = check_citations(f, by_run)
                computed = [by_run[r]["severity"] for r in f["run_ids"] if r in by_run]
                f["computed_severity"] = computed
                f["ai_rank"] = i + 1
                (rejected if problems else accepted).append({**f, "citation_problems": problems})
            meta = {"method": "ai", "ai_meta": ai_meta, "executive_summary": data["executive_summary"]}
        except ai.AIUnavailable as exc:
            meta = {"method": "rule-based", "ai_unavailable": str(exc)}
    if meta["method"] == "rule-based":
        accepted = rule_based_findings(cfg, analyses)
    report = {"generated": iso(), "meta": meta, "resilience_score": resilience_score(analyses),
              "runs": provenance, "findings": accepted, "rejected_ai_claims": rejected,
              "safety_validation": [{"run_id": a["run_id"], "outcome": a["outcome"],
                                     "abort": (a["abort"] or {}).get("reason"), "recovered": a["recovered"]}
                                    for a in analyses if is_tripwire(a)]}
    return report


def render(report: dict[str, Any], analyses: list[dict[str, Any]]) -> str:
    m = report["meta"]
    method = (f"AI-generated ({m['ai_meta']['model_served']}), every cited number verified against analysis.json"
              if m["method"] == "ai" else
              f"Rule-based (AI not used: {m.get('ai_unavailable', 'disabled')})")
    lines = ["# Findings report — OpenTelemetry Demo 3.1.0 (sandbox)", "",
             f"Generated {report['generated']} by `chaosctl report`. Method: {method}.", "",
             f"**Resilience score: {report['resilience_score'] if report['resilience_score'] is not None else 'n/a'} / 100** "
             "(100 − mean severity score of executed experiments; tripwire runs excluded; method: docs/SCORING.md).", ""]
    if m["method"] == "ai":
        lines += ["## Executive summary", "", m["executive_summary"], ""]
    lines += ["## Ranked weaknesses", "", "| # | Severity (AI) | Computed score | Finding | Runs |", "|---|---|---|---|---|"]
    for i, f in enumerate(report["findings"], 1):
        comp = ", ".join(f"{c['label']} {c['score']}" for c in f.get("computed_severity", [])) or f["severity"]
        lines.append(f"| {i} | {f['severity']} | {comp} | {f['title']} | {', '.join(f['run_ids'])} |")
    for i, f in enumerate(report["findings"], 1):
        lines += ["", f"### {i}. {f['title']}", "",
                  f"- **Observation:** {f['observation']}",
                  f"- **Hypothesis:** {f['hypothesis']}",
                  f"- **Confidence:** {f['confidence']}",
                  "- **Evidence:** " + "; ".join(f"`{e['run_id']}` {e['metric']} = {e['value']}" for e in f["evidence"]),
                  "- **Recommended fixes:**"] + [f"  - {x}" for x in f["recommended_fixes"]]
    if report["rejected_ai_claims"]:
        lines += ["", "## Rejected AI claims (citation check failed — not part of the findings)", ""]
        lines += [f"- {f['title']}: {'; '.join(f['citation_problems'])}" for f in report["rejected_ai_claims"]]
    lines += ["", "## Safety-control validation", ""]
    if report["safety_validation"]:
        lines += [f"- `{s['run_id']}`: outcome {s['outcome']}, abort {s['abort']}, recovered {s['recovered']}"
                  for s in report["safety_validation"]]
    else:
        lines.append("- No tripwire run recorded yet (NOT RUN).")
    lines += ["", "## Runs included (evidence bundles)", "", "| Run | Experiment | Outcome | Severity | analysis.json sha256 |",
              "|---|---|---|---|---|"]
    by_run = {a["run_id"]: a for a in analyses}
    for r in report["runs"]:
        a = by_run[r["run_id"]]
        lines.append(f"| `{r['run_id']}` | {a['experiment_id']} | {a['outcome']} | {a['severity']['label']} | "
                     f"`{r['sha256'][:12]}` |")
    lines += ["", "_All numbers come from `runs/<run_id>/analysis.json`, computed by code from raw "
              "`metrics.jsonl` samples. Re-run `chaosctl analyze <run_dir>` to reproduce._", ""]
    return "\n".join(lines)


def run(cfg: Config, use_ai: bool = True) -> dict[str, Any]:
    report = generate(cfg, use_ai)
    analyses = [a for _, a in load_analyses(cfg)]
    out = cfg.artifacts_dir
    write_json(out / "findings-report.json", report)
    (out / "findings-report.md").write_text(render(report, analyses))
    return report
