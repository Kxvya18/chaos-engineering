"""Steady-state baseline: probe all journeys under the demo's normal Locust load, capture container
health at start and end, and judge whether the baseline is good enough to experiment against."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import metrics
from .config import Config
from .probes import JourneyFn, ProbeRunner
from .util import JsonlWriter, iso, now, read_jsonl, stamp, write_json

MAX_BASELINE_ERROR_RATE = 0.02   # a journey failing >2% at rest is not a usable steady state
MIN_SAMPLE_FRACTION = 0.8        # each journey must deliver >=80% of the expected probe samples


def quality(cfg: Config, summary: dict[str, Any], health_start: dict[str, Any], health_end: dict[str, Any],
            load: dict[str, Any]) -> dict[str, Any]:
    checks = []

    def add(name: str, ok: bool, detail: str) -> None:
        checks.append({"check": name, "ok": ok, "detail": detail})

    users = load.get("user_count") or 0
    add("steady load present", load.get("state") in ("running", "spawning") and users >= 1,
        f"Locust state={load.get('state')} users={users}")
    expected = summary["duration_s"] / cfg.probes["interval_s"] * MIN_SAMPLE_FRACTION
    low = {j: st["n"] for j, st in summary["journeys"].items() if st["n"] < expected}
    missing = sorted(set(cfg.journeys) - set(summary["journeys"]))
    add("every journey sampled", not low and not missing,
        f"expected >= {expected:.0f} samples each; low: {low or 'none'}; missing: {missing or 'none'}")
    bad = {j: round(st["error_rate"], 3) for j, st in summary["journeys"].items()
           if st["error_rate"] is not None and st["error_rate"] > MAX_BASELINE_ERROR_RATE}
    add("journeys healthy at rest", not bad, f"error rate > {MAX_BASELINE_ERROR_RATE:.0%}: {bad or 'none'}")
    for label, snap in (("start", health_start), ("end", health_end)):
        down = sorted(t for t in cfg.allowlist if snap.get(t, {}).get("state") != "running")
        unhealthy = sorted(n for n, s in snap.items() if "unhealthy" in (s.get("status") or ""))
        add(f"containers healthy at {label}", not down and not unhealthy,
            f"{sum(1 for s in snap.values() if s.get('state') == 'running')} running; not running: {down or 'none'}; "
            f"unhealthy: {unhealthy or 'none'}")
    return {"pass": all(c["ok"] for c in checks), "checks": checks}


def run(cfg: Config, journeys: dict[str, JourneyFn], duration_s: float, context: dict[str, Any],
        health: Callable[[], dict[str, Any]] = dict, load: Callable[[], dict[str, Any]] = dict,
        sleep: Callable[[float], None] = time.sleep, progress: Callable[[str], None] = print) -> Path:
    run_dir = cfg.runs_dir / f"{stamp()}-baseline"
    run_dir.mkdir(parents=True, exist_ok=False)
    health_start = health()
    writer = JsonlWriter(run_dir / "metrics.jsonl")
    pr = ProbeRunner(journeys, writer, cfg.probes["interval_s"])
    t0 = now()
    pr.start()
    try:
        while now() - t0 < duration_s:
            sleep(min(10.0, duration_s - (now() - t0)))
            progress(f"  baseline: {int(now() - t0)}/{int(duration_s)} s, samples={pr.count}")
    finally:
        pr.stop()
        writer.close()
    health_end = health()
    load_state = load()
    samples = read_jsonl(run_dir / "metrics.jsonl")
    summary = {"generated": iso(), "run_dir": str(run_dir), "duration_s": duration_s,
               "context": context, **metrics.summarize(samples),
               "health": {"start": health_start, "end": health_end}, "load": load_state}
    summary["quality"] = quality(cfg, summary, health_start, health_end, load_state)
    write_json(run_dir / "summary.json", summary)
    write_json(cfg.artifacts_dir / "baseline.json", summary)
    return run_dir
