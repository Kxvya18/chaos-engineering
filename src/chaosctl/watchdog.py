"""Independent watchdog process: enforces abort rules, TTLs and the dead-man's switch.

Runs as `chaosctl watchdog --run-dir runs/<id>` in its own OS process. It never calls the AI.
Exits when the run is DONE and none of the run's faults remain registered.
"""

from __future__ import annotations

import collections
import time
from pathlib import Path
from typing import Any, Callable

from . import faults, safety
from .config import Config
from .registry import Registry
from .util import EventLog, JsonlTail, now, read_json, write_json


class Watchdog:
    def __init__(self, cfg: Config, run_dir: Path, ctx: faults.Ctx, clock: Callable[[], float] = now,
                 sleep: Callable[[float], None] = time.sleep):
        self.cfg = cfg
        self.run_dir = run_dir
        self.ctx = ctx
        self.clock = clock
        self.sleep = sleep
        self.wd = cfg.watchdog
        exp = read_json(run_dir / "experiment.json")
        self.run_id = exp["run_id"]
        self.expected = exp["experiment"].get("expected_journeys", [])
        self.baseline = exp["baseline"]
        self.registry = Registry(cfg.state_dir)
        self.events = EventLog(run_dir / "events.jsonl", "watchdog")
        self.tail = JsonlTail(run_dir / "metrics.jsonl")
        self.window: collections.deque = collections.deque()
        self.started = clock()
        self.last_sample_ts: float | None = None
        self.aborted = False

    # -- signals --------------------------------------------------------------
    def _done(self) -> bool:
        return (self.run_dir / "DONE").exists()

    def _heartbeat_age(self) -> float | None:
        hb = self.run_dir / "heartbeat"
        if not hb.exists():
            return None
        try:
            return self.clock() - float(hb.read_text().strip())
        except ValueError:
            return None

    def _faults_active(self) -> bool:
        return bool(self.registry.list(self.run_id))

    # -- actions --------------------------------------------------------------
    def abort(self, reason: str, details: Any) -> None:
        if not self.aborted:
            self.aborted = True
            write_json(self.run_dir / "ABORT.json", {"ts": self.clock(), "reason": reason, "details": details,
                                                     "by": "watchdog"})
            self.events.emit("abort", reason=reason, details=details)
        self.rollback_all(reason)

    def rollback_all(self, reason: str) -> None:
        for entry in self.registry.list(self.run_id):
            res = faults.rollback(self.ctx, entry)
            self.events.emit("rollback", fault_id=entry["fault_id"], reason=reason, result=res)
            if res["ok"]:
                self.registry.remove(entry["fault_id"])

    # -- one evaluation step (unit-testable) ------------------------------------
    def step(self) -> bool:
        """Return False when the watchdog should exit."""
        t = self.clock()
        for s in self.tail.read_new():
            self.window.append(s)
            self.last_sample_ts = max(self.last_sample_ts or 0, s["ts"])
        while self.window and self.window[0]["ts"] < t - self.wd["window_s"]:
            self.window.popleft()

        if safety.kill_engaged(self.cfg):
            self.abort("A6_kill_switch", {"file": str(safety.kill_file(self.cfg))})
        for entry in self.registry.list(self.run_id):
            if t > entry["expires_at"]:
                self.abort("A5_duration_exceeded", {"fault_id": entry["fault_id"], "expires_at": entry["expires_at"]})
        ref = self.last_sample_ts if self.last_sample_ts is not None else self.started
        if t - ref > self.wd["stale_after_s"] and self._faults_active():
            self.abort("A3_stale_telemetry", {"last_sample_age_s": round(t - ref, 2)})
        hb = self._heartbeat_age()
        if hb is not None and hb > self.wd["heartbeat_stale_s"]:
            self.abort("A4_orchestrator_dead", {"heartbeat_age_s": round(hb, 2)})
        if self._faults_active():
            breaches = safety.evaluate_window(list(self.window), self.baseline, self.expected, self.wd)
            if breaches:
                self.abort(breaches[0]["rule"], breaches)

        if hb is not None and hb > self.wd["heartbeat_stale_s"] and not self._faults_active():
            return False  # orchestrator gone and nothing left to protect
        return not (self._done() and not self._faults_active())

    def run(self) -> None:
        self.events.emit("watchdog_start", expected_journeys=self.expected)
        try:
            while self.step():
                self.sleep(self.wd["poll_s"])
        finally:
            if self._faults_active():
                self.rollback_all("watchdog_exit")
            self.events.emit("watchdog_exit", aborted=self.aborted)
            self.events.close()
