"""Steady-state baseline: probe all journeys under the demo's normal Locust load."""

from __future__ import annotations

import time
from pathlib import Path
from typing import Any, Callable

from . import metrics
from .config import Config
from .probes import JourneyFn, ProbeRunner
from .util import JsonlWriter, iso, now, read_jsonl, stamp, write_json


def run(cfg: Config, journeys: dict[str, JourneyFn], duration_s: float, context: dict[str, Any],
        sleep: Callable[[float], None] = time.sleep, progress: Callable[[str], None] = print) -> Path:
    run_dir = cfg.runs_dir / f"{stamp()}-baseline"
    run_dir.mkdir(parents=True, exist_ok=False)
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
    samples = read_jsonl(run_dir / "metrics.jsonl")
    summary = {"generated": iso(), "run_dir": str(run_dir), "duration_s": duration_s,
               "context": context, **metrics.summarize(samples)}
    write_json(run_dir / "summary.json", summary)
    write_json(cfg.artifacts_dir / "baseline.json", summary)
    return run_dir
