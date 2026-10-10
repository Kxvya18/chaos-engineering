"""Deterministic safety enforcement: identity, allowlist, bounds, kill switch, abort rules."""

from __future__ import annotations

from pathlib import Path
from typing import Any, Iterable

from . import metrics
from .config import Config
from .dockerctl import DockerCLI, DockerError

FAULT_TYPES = ("container-stop", "container-pause", "network-delay", "cpu-throttle", "traffic-spike")


class SafetyError(Exception):
    """Raised when an action would violate the safety policy. Never caught to 'continue anyway'."""


# --- kill switch -------------------------------------------------------------
def kill_file(cfg: Config) -> Path:
    return cfg.state_dir / "KILL"


def kill_engaged(cfg: Config) -> bool:
    return kill_file(cfg).exists()


def engage_kill(cfg: Config, reason: str) -> None:
    kill_file(cfg).parent.mkdir(parents=True, exist_ok=True)
    kill_file(cfg).write_text(reason + "\n")


def reset_kill(cfg: Config) -> None:
    kill_file(cfg).unlink(missing_ok=True)


# --- static validation ------------------------------------------------------
def validate_fault(cfg: Config, fault: dict[str, Any]) -> None:
    ftype = fault.get("fault_type")
    target = fault.get("target")
    params = fault.get("params") or {}
    duration = fault.get("duration_s")
    if ftype not in FAULT_TYPES:
        raise SafetyError(f"unknown fault_type {ftype!r}")
    lim = cfg.fault_limits(ftype)
    if not isinstance(duration, (int, float)) or duration <= 0:
        raise SafetyError("duration_s must be a positive number")
    if duration > lim["max_duration_s"]:
        raise SafetyError(f"duration {duration}s exceeds max {lim['max_duration_s']}s for {ftype}")
    if ftype == "traffic-spike":
        if target != "load-generator":
            raise SafetyError("traffic-spike target must be 'load-generator'")
        users = params.get("users")
        rate = params.get("spawn_rate", 5)
        if not isinstance(users, int) or not 1 <= users <= lim["max_users"]:
            raise SafetyError(f"users must be an int in 1..{lim['max_users']}")
        if not isinstance(rate, (int, float)) or not 0 < rate <= lim["max_spawn_rate"]:
            raise SafetyError(f"spawn_rate must be in (0, {lim['max_spawn_rate']}]")
        return
    if target in cfg.protected:
        raise SafetyError(f"target {target!r} is protected")
    if target not in cfg.allowlist:
        raise SafetyError(f"target {target!r} is not in the allowlist")
    if ftype == "container-stop" and params.get("mode", "stop") not in ("stop", "kill", "crash"):
        raise SafetyError("container-stop mode must be 'stop', 'kill' or 'crash'")
    if ftype == "network-delay":
        delay = params.get("delay_ms", 0)
        jitter = params.get("jitter_ms", 0)
        loss = params.get("loss_pct", 0)
        for name, val, mx in (("delay_ms", delay, lim["max_delay_ms"]), ("jitter_ms", jitter, lim["max_jitter_ms"]),
                              ("loss_pct", loss, lim["max_loss_pct"])):
            if not isinstance(val, (int, float)) or not 0 <= val <= mx:
                raise SafetyError(f"{name}={val!r} outside 0..{mx}")
        if delay == 0 and loss == 0:
            raise SafetyError("network-delay needs delay_ms > 0 or loss_pct > 0")
    if ftype == "cpu-throttle":
        cpus = params.get("cpus")
        if not isinstance(cpus, (int, float)) or cpus < lim["min_cpus"]:
            raise SafetyError(f"cpus must be >= {lim['min_cpus']}")


def validate_experiment(cfg: Config, exp: dict[str, Any]) -> None:
    faults = exp.get("faults") or []
    if not faults:
        raise SafetyError("experiment has no faults")
    cap = cfg.limits["max_active_faults_combined"] if exp.get("combined") else cfg.limits["max_active_faults"]
    if len(faults) > cap:
        raise SafetyError(f"{len(faults)} simultaneous faults exceeds limit {cap}")
    targets = [f.get("target") for f in faults]
    if len(set(targets)) != len(targets):
        raise SafetyError("two faults on the same target are not allowed")
    for f in faults:
        validate_fault(cfg, f)
    unknown = set(exp.get("expected_journeys", [])) - set(cfg.journeys)
    if unknown:
        raise SafetyError(f"unknown journeys in expected_journeys: {sorted(unknown)}")


# --- live identity check -----------------------------------------------------
def check_identity(cfg: Config, docker: DockerCLI, target: str) -> dict[str, Any]:
    """Verify we are on the local sandbox and the target belongs to our compose project."""
    try:
        endpoint = docker.context_endpoint()
    except DockerError as exc:
        raise SafetyError(f"cannot verify docker context: {exc}") from exc
    if not endpoint.startswith("unix://"):
        raise SafetyError(f"docker context endpoint {endpoint!r} is not a local unix socket")
    if target == "load-generator":
        target_name = "load-generator"
    else:
        target_name = target
    info = docker.inspect(target_name)
    if not info:
        raise SafetyError(f"target container {target_name!r} not found")
    labels = (info.get("Config") or {}).get("Labels") or {}
    project = labels.get("com.docker.compose.project")
    if project != cfg.sandbox["compose_project"]:
        raise SafetyError(f"target {target_name!r} belongs to project {project!r}, expected "
                          f"{cfg.sandbox['compose_project']!r}")
    nets = ((info.get("NetworkSettings") or {}).get("Networks") or {}).keys()
    if cfg.sandbox["docker_network"] not in nets:
        raise SafetyError(f"target {target_name!r} is not on network {cfg.sandbox['docker_network']!r}")
    return {"endpoint": endpoint, "project": project, "networks": sorted(nets)}


# --- watchdog abort rules (pure) ---------------------------------------------
def evaluate_window(window: list[metrics.Sample], baseline: dict[str, Any], expected: Iterable[str],
                    wd: dict[str, Any]) -> list[dict[str, Any]]:
    """Return breaches (empty list = safe). Pure function: easy to test, no AI involved."""
    breaches: list[dict[str, Any]] = []
    if len(window) < wd["min_samples"]:
        return breaches
    expected = set(expected)
    overall = metrics.stats(window)
    if overall["error_rate"] > wd["global_error_rate_max"]:
        breaches.append({"rule": "A2_global_outage", "error_rate": overall["error_rate"],
                         "threshold": wd["global_error_rate_max"]})
    for journey, ss in metrics.by_journey(window).items():
        if journey in expected or len(ss) < 2:
            continue
        st = metrics.stats(ss)
        base = metrics.journey_baseline(baseline, journey)
        base_err = base.get("error_rate") or 0.0
        base_p95 = base.get("p95_ms") or 0.0
        err_limit = base_err + wd["unexpected_error_rate_pp"] / 100.0
        p95_limit = max(wd["unexpected_p95_factor"] * base_p95, base_p95 + wd["unexpected_p95_floor_ms"])
        slow = sum(1 for s in ss if s["latency_ms"] > p95_limit)
        # at least `min_offending` bad samples: one stray slow/failed probe must not abort a run
        if st["error_rate"] > err_limit and st["errors"] >= wd.get("min_offending", 2):
            breaches.append({"rule": "A1_blast_radius_error", "journey": journey,
                             "error_rate": st["error_rate"], "threshold": err_limit, "errors": st["errors"]})
        if st["p95_ms"] > p95_limit and slow >= wd.get("min_offending", 2):
            breaches.append({"rule": "A1_blast_radius_latency", "journey": journey,
                             "p95_ms": st["p95_ms"], "threshold": p95_limit, "slow_samples": slow})
    return breaches


def steady_state_ok(pre: list[metrics.Sample], baseline: dict[str, Any], cfg: Config) -> tuple[bool, dict[str, Any]]:
    st = metrics.stats(pre)
    base_err = (baseline.get("overall") or {}).get("error_rate") or 0.0
    limit = base_err + cfg.steady_state["error_rate_pp"] / 100.0
    ok = st["n"] > 0 and st["error_rate"] <= limit
    return ok, {"pre": st, "limit_error_rate": limit}
