"""Experiment lifecycle: validate -> reconcile -> identity -> pre/steady-state -> inject ->
observe -> rollback -> recovery -> analysis. Deterministic; the AI is never on this path."""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import threading
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

from . import analysis, checks, evidence, faults, fmea, health, incident, metrics, safety
from .config import Config
from .probes import JourneyFn, ProbeRunner
from .registry import Registry
from .util import EventLog, JsonlWriter, now, read_jsonl, sha256_file, stamp, write_json


class RunAborted(Exception):
    pass


@dataclass
class RunSession:
    """Everything one experiment run holds between its phases."""
    run_id: str
    run_dir: Path
    experiment: dict[str, Any]
    events: Any = None
    writer: Any = None
    probes: Any = None
    hb: Any = None
    hb_stop: Any = None
    watchdog: Any = None
    old_handlers: dict[Any, Any] = field(default_factory=dict)
    injected: list[dict[str, Any]] = field(default_factory=list)
    rollbacks: list[dict[str, Any]] = field(default_factory=list)
    outcome: dict[str, Any] = field(default_factory=lambda: {"status": "started"})
    closed: bool = False

    @property
    def targets(self) -> list[str]:
        return [f["target"] for f in self.experiment["faults"] if f["fault_type"] != "traffic-spike"]


def spawn_watchdog_process(cfg_path: Path, run_dir: Path) -> subprocess.Popen:
    log = (run_dir / "watchdog.log").open("a")
    env = {**os.environ, "CHAOS_CONFIG": str(cfg_path)}
    # own session: a Ctrl-C/SIGHUP in the operator's terminal must not take the safety guard down with it
    return subprocess.Popen([sys.executable, "-m", "chaosctl", "watchdog", "--run-dir", str(run_dir)],
                            stdout=log, stderr=subprocess.STDOUT, env=env, start_new_session=True)


class Orchestrator:
    def __init__(self, cfg: Config, ctx: faults.Ctx, journeys: dict[str, JourneyFn], baseline: dict[str, Any],
                 watchdog_starter: Callable[[Path], Any] | None = None,
                 clock: Callable[[], float] = now, sleep: Callable[[float], None] = time.sleep,
                 cfg_path: Path | None = None, progress: Callable[[str], None] | None = None,
                 use_ai: bool = False, explain: bool = False):
        self.cfg = cfg
        self.ctx = ctx
        self.journeys = journeys
        self.baseline = baseline
        self.clock = clock
        self.sleep = sleep
        self.registry = Registry(cfg.state_dir)
        self.cfg_path = cfg_path or Path(os.environ.get("CHAOS_CONFIG", "config/sandbox.toml")).resolve()
        self.watchdog_starter = watchdog_starter or (lambda rd: spawn_watchdog_process(self.cfg_path, rd))
        self._interrupted = threading.Event()
        self.progress = progress or (lambda msg: None)
        self.use_ai = use_ai
        self.explain = explain  # Day-2 feature: incident analysis after the run (opt-in)
        self._last_progress = 0.0

    # -- helpers --------------------------------------------------------------
    def _stop_reason(self, run_dir: Path) -> str | None:
        if self._interrupted.is_set():
            return "interrupted"
        if safety.kill_engaged(self.cfg):
            return "kill_switch"
        if (run_dir / "ABORT.json").exists():
            return "watchdog_abort"
        return None

    def _wait(self, run_dir: Path, seconds: float, on_tick: Callable[[], None] | None = None) -> str | None:
        end = self.clock() + seconds
        while self.clock() < end:
            reason = self._stop_reason(run_dir)
            if reason:
                return reason
            if on_tick:
                on_tick()
            self.sleep(min(1.0, max(0.0, end - self.clock())))
        return self._stop_reason(run_dir)

    def _status_line(self, run_dir: Path, phase: str, t0: float, targets: list[str], events: EventLog | None) -> None:
        """Live view: per-journey success over the last 6 s + target container state (every ~2 s)."""
        t = self.clock()
        if t - self._last_progress < 2:
            return
        self._last_progress = t
        recent = metrics.between(read_jsonl(run_dir / "metrics.jsonl"), t - 6, t + 1)
        parts = []
        for j, ss in sorted(metrics.by_journey(recent).items()):
            ok = sum(1 for s in ss if s["ok"])
            parts.append(f"{j}:{'OK ' if ok == len(ss) else 'ERR' if ok == 0 else 'DEG'}{ok}/{len(ss)}")
        states = {tg: self.ctx.docker.state(tg) for tg in targets}
        if events is not None and states:
            events.emit("target_state", states=states)
        if events is not None:  # what an infrastructure dashboard would show right now
            events.emit("platform_health", **health.verdict(self.cfg, health.project_health(self.cfg, self.ctx.docker)))
        st = " ".join(f"{tg}={s['status']}{'/' + s['health'] if s.get('health') else ''}(restarts={s['restart_count']})"
                      for tg, s in states.items())
        self.progress(f"[{phase:9} +{t - t0:5.1f}s] {' '.join(parts)} | {st}")

    def _watchdog_ready(self, run_dir: Path, timeout_s: float = 15.0) -> bool:
        end = self.clock() + timeout_s
        while self.clock() < end:
            if any(e["action"] == "watchdog_start" and e["source"] == "watchdog"
                   for e in read_jsonl(run_dir / "events.jsonl")):
                return True
            self.sleep(0.2)
        return False

    def _heartbeat(self, run_dir: Path, stop: threading.Event) -> None:
        while not stop.is_set():
            (run_dir / "heartbeat").write_text(f"{self.clock()}")
            stop.wait(1.0)

    # -- main ---------------------------------------------------------------------
    # The lifecycle is split into phases so the multi-agent mission (agents.py) can hand each phase to a different
    # agent: open + steady (Safety Officer) -> inject_and_hold (Chaos Injector) -> close (Recovery) -> post (Analyst).
    # run() composes the same phases with try/finally, so a fault is ALWAYS rolled back by close().
    def run(self, experiment: dict[str, Any], plan_ref: dict[str, Any] | None = None) -> Path:
        s = self.open(experiment, plan_ref)
        try:
            if self.steady(s):
                self.inject_and_hold(s)
        except RunAborted as exc:
            s.outcome = {"status": str(exc)}
        except safety.SafetyError as exc:
            s.outcome = {"status": "safety_error", "error": str(exc)}
        finally:
            self.close(s)
            self.post(s)
        return s.run_dir

    def open(self, experiment: dict[str, Any], plan_ref: dict[str, Any] | None = None) -> RunSession:
        """Pre-flight: validate, reconcile leftovers, verify target identity, create the run record."""
        cfg = self.cfg
        safety.validate_experiment(cfg, experiment)
        if safety.kill_engaged(cfg):
            raise safety.SafetyError("kill switch engaged (state/KILL); run `chaosctl kill --reset` first")
        leftovers = faults.reconcile(self.ctx, self.registry)
        if self.registry.list():
            raise safety.SafetyError(f"could not clear leftover faults: {leftovers}")
        identities = {f["target"]: safety.check_identity(cfg, self.ctx.docker, f["target"])
                      for f in experiment["faults"]}

        run_id = f"{stamp(self.clock())}-{experiment['id']}"
        run_dir = cfg.runs_dir / run_id
        run_dir.mkdir(parents=True, exist_ok=False)
        cfg_file = self.cfg_path if self.cfg_path.exists() else None
        write_json(run_dir / "experiment.json", {
            "run_id": run_id, "experiment": experiment, "plan": plan_ref, "baseline": self.baseline,
            "identity": identities, "reconcile_before": leftovers,
            "config_sha256": sha256_file(cfg_file) if cfg_file else None,
            "safety": {"watchdog": cfg.watchdog, "recovery": cfg.recovery, "steady_state": cfg.steady_state},
        })
        s = RunSession(run_id=run_id, run_dir=run_dir, experiment=experiment)
        s.events = EventLog(run_dir / "events.jsonl", "orchestrator")
        s.writer = JsonlWriter(run_dir / "metrics.jsonl")
        s.probes = ProbeRunner(self.journeys, s.writer, cfg.probes["interval_s"], clock=self.clock)
        s.hb_stop = threading.Event()
        s.hb = threading.Thread(target=self._heartbeat, args=(run_dir, s.hb_stop), daemon=True)
        if threading.current_thread() is threading.main_thread():
            for sig in (signal.SIGINT, signal.SIGTERM):
                s.old_handlers[sig] = signal.signal(sig, lambda *_: self._interrupted.set())
        return s

    def steady(self, s: RunSession) -> bool:
        """Start probes and the independent watchdog, then require the steady state before any fault."""
        cfg, run_dir, events = self.cfg, s.run_dir, s.events
        s.hb.start()
        s.probes.start()
        s.watchdog = self.watchdog_starter(run_dir)
        if not self._watchdog_ready(run_dir):
            raise RunAborted("watchdog_not_running")  # never inject without the independent safety guard
        events.emit("watchdog_ready")
        events.emit("pre_start", seconds=cfg.steady_state["pre_s"])
        t_pre = self.clock()
        reason = self._wait(run_dir, cfg.steady_state["pre_s"],
                            lambda: self._status_line(run_dir, "steady", t_pre, s.targets, None))
        if reason:
            raise RunAborted(reason)
        pre = metrics.between(read_jsonl(run_dir / "metrics.jsonl"), t_pre, self.clock())
        ok, detail = safety.steady_state_ok(pre, self.baseline, cfg)
        events.emit("steady_state_check", ok=ok, **detail)
        if not ok:
            s.outcome = {"status": "steady_state_not_met", **detail}
        return ok

    def inject_and_hold(self, s: RunSession) -> None:
        """Inject every fault (registered BEFORE injection) and hold it for its duration with live status."""
        run_dir, events, experiment = s.run_dir, s.events, s.experiment
        if "cart_durability" in experiment.get("checks", []):
            seeds = checks.cart_seed(self.cfg, self.ctx.http)
            events.emit("check_seed", check="cart_durability", seeds=seeds)
            self.progress(f"seeded {sum(x['seeded'] for x in seeds)}/{len(seeds)} sentinel carts before the fault")
        duration = max(f["duration_s"] for f in experiment["faults"])
        for i, f in enumerate(experiment["faults"]):
            t = self.clock()
            entry = {"fault_id": f"{s.run_id}-f{i}", "run_id": s.run_id, "fault_type": f["fault_type"],
                     "target": f["target"], "params": f.get("params") or {}, "duration_s": f["duration_s"],
                     "injected_at": t, "expires_at": t + f["duration_s"], "state": {}}
            entry["desired"] = faults.snapshot(self.ctx, entry)  # the healthy state rollback must restore
            self.registry.add(entry)  # registered BEFORE inject: a crash mid-inject is still rolled back
            events.emit("inject_start", fault=entry, target_state=self.ctx.docker.state(f["target"])
                        if f["fault_type"] != "traffic-spike" else None)
            try:
                entry["state"] = faults.inject(self.ctx, entry)
                self.registry.update(entry["fault_id"], state=entry["state"])
                s.injected.append(entry)
                events.emit("inject_ok", fault_id=entry["fault_id"], state=entry["state"])
                self.progress(f">>> INJECTED {f['fault_type']} on {f['target']} {f.get('params') or ''}")
            except Exception as exc:
                events.emit("inject_failed", fault_id=entry["fault_id"], error=str(exc))
                s.injected.append(entry)
                raise RunAborted(f"inject_failed: {exc}") from exc
        t_inj = self.clock()
        reason = self._wait(run_dir, duration, lambda: self._status_line(run_dir, "FAULT", t_inj, s.targets, events))
        s.outcome = {"status": "completed" if reason is None else reason}

    def close(self, s: RunSession) -> list[dict[str, Any]]:
        """Verified rollback of every injected fault, leftovers, recovery watch, durability check, run teardown.
        Idempotent: safe to call twice (the mission's safety net calls it again on any error)."""
        if s.closed:
            return s.rollbacks
        s.closed = True
        run_dir, events = s.run_dir, s.events
        for entry in reversed(s.injected):
            res = faults.rollback_verified(self.ctx, entry)
            s.rollbacks.append({"fault_id": entry["fault_id"], **res})
            events.emit("rollback", fault_id=entry["fault_id"], result=res)
            self.progress(f"rollback {'VERIFIED' if res.get('verified') else 'NOT VERIFIED'} after "
                          f"{res.get('attempts')} attempt(s): " + "; ".join(
                              f"{c['check']}={'ok' if c['ok'] else 'FAIL'}" for c in res["history"][-1]["checks"]))
            if res["ok"]:
                self.registry.remove(entry["fault_id"])
        for entry in self.registry.list(s.run_id):  # anything the watchdog or a crash left behind for this run
            res = faults.rollback(self.ctx, entry)
            events.emit("rollback", fault_id=entry["fault_id"], result=res, late=True)
            if res["ok"]:
                self.registry.remove(entry["fault_id"])
        rollback_ts = self.clock()
        events.emit("rollback_complete", remaining=len(self.registry.list(s.run_id)))
        if s.injected:
            self.progress(f"<<< ROLLED BACK ({s.outcome.get('status')}); watching recovery")
            self._observe_recovery(run_dir, rollback_ts, events, s.targets)
            seed_ev = next((e for e in read_jsonl(run_dir / "events.jsonl") if e["action"] == "check_seed"), None)
            if seed_ev:
                res = checks.cart_verify(self.cfg, self.ctx.http, seed_ev["seeds"])
                events.emit("check_result", **res)
                self.progress(f"cart durability: {res['intact']}/{res['seeded']} sentinel carts intact, "
                              f"{res['lost']} lost")
        (run_dir / "DONE").write_text(f"{self.clock()}")
        s.probes.stop()
        s.writer.close()
        s.hb_stop.set()
        if s.watchdog is not None and hasattr(s.watchdog, "wait"):
            try:
                s.watchdog.wait(timeout=30)
            except subprocess.TimeoutExpired:
                s.watchdog.terminate()
        elif s.watchdog is not None and hasattr(s.watchdog, "join"):
            s.watchdog.join(timeout=30)
        for sig, h in s.old_handlers.items():
            signal.signal(sig, h)
        events.emit("run_end", outcome=s.outcome)
        events.close()
        write_json(run_dir / "outcome.json", s.outcome)
        return s.rollbacks

    def post(self, s: RunSession, explain: bool | None = None) -> dict[str, Any] | None:
        """Analysis, evidence, incident explanation (AI if enabled) and the measured FMEA update."""
        cfg, run_dir = self.cfg, s.run_dir
        try:
            analysis.analyze_run(cfg, run_dir)
            if not s.injected:
                return None
            self.progress("collecting evidence (Jaeger traces, container logs, compose facts) ...")
            evidence.collect(cfg, self.ctx, run_dir)
            if self.explain if explain is None else explain:
                self.progress("incident analysis (AI if enabled, else rule-based) ...")
                incident.explain(cfg, run_dir, use_ai=self.use_ai)
            row = fmea.update_from_run(cfg, run_dir, cfg.artifacts_dir / "fmea.json")
            if row:
                me = row["measured"]
                self.progress(f"FMEA {row['component']} / {row['failure_mode']}: predicted S{row['severity']} "
                              f"D{row['detection']} RPN {row['rpn']}  ->  measured S{me['severity']} "
                              f"D{me['detection']} RPN {me['rpn']}")
            return row
        except Exception as exc:  # never mask the run outcome; re-run `chaosctl analyze/explain` later
            (run_dir / "analysis_error.txt").write_text(f"{type(exc).__name__}: {exc}\n")
            return None

    def _observe_recovery(self, run_dir: Path, rollback_ts: float, events: EventLog, targets: list[str]) -> None:
        rec = self.cfg.recovery
        deadline = rollback_ts + rec["timeout_s"] + rec["window_s"] * rec["consecutive"]
        events.emit("recovery_watch_start", timeout_s=rec["timeout_s"])
        while self.clock() < deadline and not self._interrupted.is_set():
            self._status_line(run_dir, "recovery", rollback_ts, targets, events)
            samples = read_jsonl(run_dir / "metrics.jsonl")
            rt = metrics.recovery_time(samples, rollback_ts, self.baseline, self.cfg.journeys, rec, self.clock())
            if rt is not None:
                events.emit("recovered", recovery_time_s=round(rt, 2))
                return
            self.sleep(1.0)
        events.emit("not_recovered", waited_s=round(self.clock() - rollback_ts, 1))
