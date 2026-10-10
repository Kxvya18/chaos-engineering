"""Before/after comparison of two runs of the same pre-registered scenario (e.g. before and after a fix)."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .util import iso, read_json, write_json


def _row(a: dict[str, Any]) -> dict[str, Any]:
    worst_err = max((j["fault"]["error_rate"] or 0) for j in a["journeys"].values())
    worst_p95 = max((j["fault"]["p95_ms"] or 0) for j in a["journeys"].values())
    dur = next((c for c in a.get("checks", []) if c.get("check") == "cart_durability"), None)
    return {"run_id": a["run_id"], "outcome": a["outcome"], "impacted_journeys": a["impacted_journeys"],
            "worst_journey_error_rate": round(worst_err, 3), "worst_journey_p95_ms": worst_p95,
            "time_to_impact_s": a["time_to_impact_s"], "time_to_restore_s": a.get("time_to_restore_s"),
            "self_healed": any(a.get("self_healed", {}).values()),
            "carts_lost": None if not dur else f"{dur['lost']}/{dur['seeded']}",
            "severity": a["severity"]["label"], "severity_score": a["severity"]["score"]}


def compare(before_dir: Path, after_dir: Path) -> dict[str, Any]:
    a, b = read_json(before_dir / "analysis.json"), read_json(after_dir / "analysis.json")
    if a["experiment_id"] != b["experiment_id"]:
        raise ValueError(f"different experiments: {a['experiment_id']} vs {b['experiment_id']}")
    ra, rb = _row(a), _row(b)
    delta = None
    if ra["severity_score"] is not None and rb["severity_score"] is not None:
        delta = round(rb["severity_score"] - ra["severity_score"], 1)
    verdict = "improved" if delta is not None and delta < -5 else "regressed" if delta and delta > 5 else "no material change"
    return {"generated": iso(), "experiment_id": a["experiment_id"], "before": ra, "after": rb,
            "severity_delta": delta, "verdict": verdict}


def render_md(c: dict[str, Any]) -> str:
    keys = [k for k in c["before"] if k != "run_id"]
    lines = [f"# Before / after — {c['experiment_id']}", "", f"Verdict: **{c['verdict']}** "
             f"(severity score change {c['severity_delta']})", "", "| Metric | Before | After |", "|---|---|---|",
             f"| run | `{c['before']['run_id']}` | `{c['after']['run_id']}` |"]
    lines += [f"| {k} | {c['before'][k]} | {c['after'][k]} |" for k in keys]
    return "\n".join(lines) + "\n"


def run(before_dir: Path, after_dir: Path, out_dir: Path) -> tuple[dict[str, Any], Path]:
    c = compare(before_dir, after_dir)
    path = out_dir / f"comparison-{c['experiment_id']}.md"
    write_json(path.with_suffix(".json"), c)
    path.write_text(render_md(c))
    return c, path
