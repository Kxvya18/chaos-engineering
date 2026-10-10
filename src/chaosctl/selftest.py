"""Simulated safety verification (no Docker): writes artifacts/safety-checks.md with evidence."""

from __future__ import annotations

import copy
import os
import tempfile
import threading
import time
import traceback
from pathlib import Path
from typing import Any, Callable

from . import faults, planner, safety
from .config import Config
from .orchestrator import Orchestrator
from .registry import Registry
from .sim import FakeDocker, FakeHttp, fake_journeys
from .util import iso, read_json, read_jsonl, sha256_of, write_json
from .watchdog import Watchdog

FAST = {
    "watchdog": {"window_s": 4, "min_samples": 5, "poll_s": 0.2, "stale_after_s": 2, "heartbeat_stale_s": 3,
                 "unexpected_p95_floor_ms": 200},
    "steady_state": {"pre_s": 2},
    "recovery": {"window_s": 1, "consecutive": 2, "timeout_s": 10},
    "probes": {"interval_s": 0.2},
}


def fast_config(cfg: Config, workdir: Path) -> Config:
    raw = copy.deepcopy(cfg.raw)
    for section, vals in FAST.items():
        raw[section].update(vals)
    raw["sandbox"].update({"state_dir": str(workdir / "state"), "runs_dir": str(workdir / "runs"),
                           "artifacts_dir": str(workdir / "artifacts")})
    return Config(raw=raw, root=cfg.root)


class Env:
    def __init__(self, cfg: Config):
        self.cfg = cfg
        self.docker = FakeDocker(cfg)
        self.http = FakeHttp()
        self.ctx = faults.Ctx(cfg, self.docker, self.http)
        self.journeys = fake_journeys(cfg, self.docker, self.http)
        self.baseline = {"overall": {"error_rate": 0.0, "p95_ms": 8.0},
                         "journeys": {j: {"error_rate": 0.0, "p95_ms": 8.0} for j in cfg.journeys}}

    def orchestrator(self) -> Orchestrator:
        def start_wd(run_dir: Path):
            wd = Watchdog(self.cfg, run_dir, self.ctx)
            t = threading.Thread(target=wd.run, daemon=True)
            t.start()
            return t
        return Orchestrator(self.cfg, self.ctx, self.journeys, self.baseline, watchdog_starter=start_wd,
                            cfg_path=Path("/nonexistent"))


def _expect_error(fn: Callable[[], Any], exc=safety.SafetyError) -> str:
    try:
        fn()
    except exc as e:
        return f"{type(e).__name__}: {e}"
    raise AssertionError("expected an exception, none raised")


def case_allowlist(env: Env) -> str:
    a = _expect_error(lambda: safety.validate_fault(env.cfg, {"fault_type": "container-stop",
                                                              "target": "frontend-proxy", "duration_s": 10}))
    b = _expect_error(lambda: safety.validate_fault(env.cfg, {"fault_type": "container-stop",
                                                              "target": "some-other-team-db", "duration_s": 10}))
    return f"protected target rejected ({a}); unknown target rejected ({b})"


def case_invalid_params(env: Env) -> str:
    a = _expect_error(lambda: safety.validate_fault(env.cfg, {"fault_type": "network-delay", "target": "cart",
                                                              "params": {"delay_ms": 5000}, "duration_s": 30}))
    b = _expect_error(lambda: safety.validate_fault(env.cfg, {"fault_type": "container-stop", "target": "cart",
                                                              "duration_s": 999}))
    c = _expect_error(lambda: safety.validate_fault(env.cfg, {"fault_type": "traffic-spike",
                                                              "target": "load-generator",
                                                              "params": {"users": 500}, "duration_s": 30}))
    return f"{a}; {b}; {c}"


def case_identity(env: Env) -> str:
    env.docker.endpoint = "tcp://10.0.0.5:2376"
    a = _expect_error(lambda: safety.check_identity(env.cfg, env.docker, "cart"))
    env.docker.endpoint = "unix:///var/run/docker.sock"
    env.docker.containers["cart"]["project"] = "someone-else"
    b = _expect_error(lambda: safety.check_identity(env.cfg, env.docker, "cart"))
    env.docker.containers["cart"]["project"] = env.cfg.sandbox["compose_project"]
    return f"remote context refused ({a}); foreign project refused ({b})"


def case_threshold_abort(env: Env) -> str:
    exp = planner.tripwire(env.cfg)
    exp["faults"][0]["duration_s"] = 8
    run_dir = env.orchestrator().run(exp)
    abort = read_json(run_dir / "ABORT.json")
    assert abort["reason"].startswith("A1"), abort
    assert not env.docker.netem, f"netem still active: {env.docker.netem}"
    assert not Registry(env.cfg.state_dir).list(), "registry not empty"
    a = read_json(run_dir / "analysis.json")
    return (f"watchdog abort {abort['reason']} at +{abort['ts'] - a['timeline']['inject']:.1f}s after inject; "
            f"netem removed; registry empty; recovered={a['recovered']} in {a['recovery_time_s']}s")


def case_kill_switch(env: Env) -> str:
    exp = {"id": "SIM-kill", "faults": [{"fault_type": "container-stop", "target": "ad", "params": {},
                                          "duration_s": 20}], "expected_journeys": ["ads"]}
    reg = Registry(env.cfg.state_dir)

    def press():
        for _ in range(200):
            if reg.list():
                time.sleep(0.5)
                safety.engage_kill(env.cfg, "selftest")
                return
            time.sleep(0.05)
    threading.Thread(target=press, daemon=True).start()
    run_dir = env.orchestrator().run(exp)
    ev = {e["action"]: e["ts"] for e in read_jsonl(run_dir / "events.jsonl") if e["source"] == "orchestrator"}
    active = ev["rollback_complete"] - ev["inject_ok"]
    outcome = read_json(run_dir / "outcome.json")
    safety.reset_kill(env.cfg)
    assert outcome["status"] == "kill_switch", outcome
    assert env.docker.containers["ad"]["status"] == "running"
    assert not reg.list()
    return f"fault active {active:.1f}s of a planned 20s, then kill switch rolled it back; `ad` running; registry empty"


def case_rollback_after_exception(env: Env) -> str:
    env.docker.fail_on.add("pause")
    exp = {"id": "SIM-inject-fail", "faults": [{"fault_type": "container-pause", "target": "valkey-cart",
                                                 "params": {}, "duration_s": 10}], "expected_journeys": ["cart"]}
    run_dir = env.orchestrator().run(exp)
    env.docker.fail_on.clear()
    outcome = read_json(run_dir / "outcome.json")
    ev = [e["action"] for e in read_jsonl(run_dir / "events.jsonl") if e["source"] == "orchestrator"]
    assert outcome["status"].startswith("inject_failed"), outcome
    assert "rollback" in ev and not Registry(env.cfg.state_dir).list()
    return f"outcome {outcome['status']!r}; events: {' > '.join(ev)}"


def _wd_fixture(env: Env, expires_in: float) -> tuple[Watchdog, Path, list[float]]:
    run_dir = env.cfg.runs_dir / f"wd-{time.time_ns()}"
    run_dir.mkdir(parents=True)
    write_json(run_dir / "experiment.json", {"run_id": run_dir.name, "baseline": env.baseline,
                                             "experiment": {"id": "wd", "expected_journeys": []}})
    clock = [1000.0]
    reg = Registry(env.cfg.state_dir)
    env.docker.run(["stop", "-t", "5", "email"])
    reg.add({"fault_id": f"{run_dir.name}-f0", "run_id": run_dir.name, "fault_type": "container-stop",
             "target": "email", "params": {}, "duration_s": 5, "injected_at": 1000.0,
             "expires_at": 1000.0 + expires_in, "state": {}})
    wd = Watchdog(env.cfg, run_dir, env.ctx, clock=lambda: clock[0], sleep=lambda s: None)
    return wd, run_dir, clock


def case_ttl(env: Env) -> str:
    wd, run_dir, clock = _wd_fixture(env, expires_in=5)
    (run_dir / "metrics.jsonl").write_text("".join(
        f'{{"ts": {1000 + i * 0.5}, "journey": "home", "ok": true, "status": 200, "latency_ms": 5, "error": null}}\n'
        for i in range(12)))
    clock[0] = 1006.0
    wd.step()
    abort = read_json(run_dir / "ABORT.json")
    assert abort["reason"] == "A5_duration_exceeded" and env.docker.containers["email"]["status"] == "running"
    return "fault past expires_at rolled back by watchdog (A5); `email` running again"


def case_stale(env: Env) -> str:
    wd, run_dir, clock = _wd_fixture(env, expires_in=60)
    clock[0] = 1000.0 + env.cfg.watchdog["stale_after_s"] + 1
    wd.step()
    abort = read_json(run_dir / "ABORT.json")
    assert abort["reason"] == "A3_stale_telemetry", abort
    assert env.docker.containers["email"]["status"] == "running"
    return f"no samples for {env.cfg.watchdog['stale_after_s'] + 1}s -> A3 abort + rollback"


def case_dead_orchestrator(env: Env) -> str:
    wd, run_dir, clock = _wd_fixture(env, expires_in=60)
    (run_dir / "metrics.jsonl").write_text(
        '{"ts": 1004.5, "journey": "home", "ok": true, "status": 200, "latency_ms": 5, "error": null}\n')
    (run_dir / "heartbeat").write_text("1000.0")
    clock[0] = 1000.0 + env.cfg.watchdog["heartbeat_stale_s"] + 1.5
    keep = wd.step()
    abort = read_json(run_dir / "ABORT.json")
    assert abort["reason"] in ("A4_orchestrator_dead", "A3_stale_telemetry"), abort
    assert env.docker.containers["email"]["status"] == "running" and keep is False
    return f"orchestrator heartbeat stale -> {abort['reason']} rollback, watchdog exits"


def case_steady_state_gate(env: Env) -> str:
    env.docker.containers["frontend"]["status"] = "exited"
    exp = {"id": "SIM-gate", "faults": [{"fault_type": "container-stop", "target": "ad", "params": {},
                                          "duration_s": 5}], "expected_journeys": ["ads"]}
    run_dir = env.orchestrator().run(exp)
    env.docker.containers["frontend"]["status"] = "running"
    outcome = read_json(run_dir / "outcome.json")
    injected = [e for e in read_jsonl(run_dir / "events.jsonl") if e["action"].startswith("inject")]
    assert outcome["status"] == "steady_state_not_met" and not injected, outcome
    return f"pre-phase error rate {outcome['pre']['error_rate']:.2f} > limit -> nothing injected"


def case_ai_unavailable(env: Env) -> str:
    old = os.environ.get("CHAOS_AI_DISABLED")
    os.environ["CHAOS_AI_DISABLED"] = "1"
    try:
        d = env.cfg.artifacts_dir
        write_json(d / "dependency-map.json", {"services": [], "edges": [], "drift": {}})
        write_json(d / "baseline.json", env.baseline)
        plan = planner.make_plan(env.cfg, d / "dependency-map.json", d / "baseline.json", [], use_ai=True)
    finally:
        if old is None:
            os.environ.pop("CHAOS_AI_DISABLED")
        else:
            os.environ["CHAOS_AI_DISABLED"] = old
    types = {e["faults"][0]["fault_type"] for e in plan["experiments"]}
    assert plan["meta"]["planner"]["method"] == "rule-based" and len(types) >= 3
    planner.verify_plan(plan)
    tampered = copy.deepcopy(plan)
    tampered["experiments"][0]["faults"][0]["duration_s"] = 119
    msg = _expect_error(lambda: planner.verify_plan(tampered))
    assert sha256_of(plan["experiments"]) == plan["meta"]["sha256"]
    return (f"AI disabled -> rule-based plan with {len(plan['experiments'])} experiments, fault types "
            f"{sorted(types)}; tampered plan refused ({msg})")


CASES = [
    ("Allowlist enforcement", case_allowlist),
    ("Invalid parameters", case_invalid_params),
    ("Sandbox identity check", case_identity),
    ("Threshold abort (tripwire, A1)", case_threshold_abort),
    ("Kill switch", case_kill_switch),
    ("Rollback after exception", case_rollback_after_exception),
    ("Timeout / TTL (A5)", case_ttl),
    ("Stale telemetry (A3)", case_stale),
    ("Dead orchestrator (A4)", case_dead_orchestrator),
    ("Steady-state gate", case_steady_state_gate),
    ("AI unavailable + plan tamper check", case_ai_unavailable),
]


def run_all(cfg: Config, out_path: Path, commit: str | None = None,
            progress: Callable[[str], None] = print) -> list[dict[str, Any]]:
    results = []
    with tempfile.TemporaryDirectory(prefix="chaosctl-selftest-") as tmp:
        fcfg = fast_config(cfg, Path(tmp))
        for name, fn in CASES:
            env = Env(fcfg)
            t0 = time.time()
            try:
                evidence = fn(env)
                status = "PASS"
            except Exception as exc:
                evidence = f"{type(exc).__name__}: {exc}\n{traceback.format_exc(limit=3)}"
                status = "FAIL"
            finally:
                safety.reset_kill(fcfg)
                for e in Registry(fcfg.state_dir).list():
                    Registry(fcfg.state_dir).remove(e["fault_id"])
            results.append({"case": name, "status": status, "seconds": round(time.time() - t0, 1),
                            "evidence": evidence})
            progress(f"  [{status}] {name} ({results[-1]['seconds']}s)")
    lines = ["# Safety checks", "", f"Generated {iso()} by `chaosctl selftest`"
             + (f" at commit `{commit}`" if commit else "") + ".", "",
             "Mode: **SIMULATED** (fake Docker, fake app, fake Locust; real orchestrator, watchdog, registry, "
             "adapters and safety code). Timing is shortened for simulation (config overrides in "
             "`src/chaosctl/selftest.py:FAST`).", "",
             "| Case | Result | Evidence |", "|---|---|---|"]
    for r in results:
        ev = r["evidence"].replace("\n", " ").replace("|", "\\|")
        lines.append(f"| {r['case']} | {r['status']} | {ev} |")
    lines += ["| Live tripwire on sandbox (`chaosctl run --tripwire`) | NOT RUN | requires the Mac sandbox |",
              "| Live kill switch on sandbox (`chaosctl kill` during a run) | NOT RUN | requires the Mac sandbox |", ""]
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(lines))
    return results
