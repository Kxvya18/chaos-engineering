"""Fault adapters. Each has validate (in safety.py), inject and an idempotent rollback.

Rollback is reconstructible from the registry entry alone, so the independent watchdog
(or `chaosctl kill` from another terminal) can undo a fault the orchestrator started.
"""

from __future__ import annotations

import shlex
import time
from dataclasses import dataclass
from typing import Any, Callable

from .config import Config
from .dockerctl import DockerCLI, DockerError
from .http import Http, HttpError

NETEM_LABEL = "chaosctl=netem"


@dataclass
class Ctx:
    cfg: Config
    docker: DockerCLI
    http: Http
    sleep: Callable[[float], None] = time.sleep


class FaultError(Exception):
    pass


def _wait(ctx: Ctx, cond: Callable[[], bool], timeout: float, step: float = 0.5) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if cond():
            return True
        ctx.sleep(step)
    return cond()


# --- container-stop ----------------------------------------------------------
def _main_pid(ctx: Ctx, target: str) -> int:
    info = ctx.docker.inspect(target) or {}
    st = info.get("State") or {}
    pid = int(st.get("Pid") or 0)
    if st.get("Status") != "running" or pid <= 1:
        raise FaultError(f"{target} is not running (status={st.get('Status')}, pid={pid})")
    return pid


def stop_inject(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    """mode=stop: graceful `docker stop` (stays down until rollback; Docker treats it as manual).
    mode=kill: `docker kill` (SIGKILL; Docker also treats this as manual -> restart policy NOT applied).
    mode=crash: SIGKILL the container's main process from the VM pid namespace, like an OOM-kill or
                segfault. Docker sees an unexpected exit, so the service's restart policy IS exercised."""
    before = ctx.docker.state(e["target"])
    mode = e["params"].get("mode", "stop")
    if mode == "crash":
        pid = _main_pid(ctx, e["target"])
        ctx.docker.run(["run", "--rm", "--pid=host", "--label", "chaosctl=crash",
                        ctx.cfg.sandbox["netem_image"], "kill", "-9", str(pid)])
        return {"before": before, "killed_pid": pid}
    if mode == "kill":
        ctx.docker.run(["kill", e["target"]])
    else:
        ctx.docker.run(["stop", "-t", "5", e["target"]])
    return {"before": before}


def stop_rollback(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    st = ctx.docker.state(e["target"])
    if not st["exists"]:
        return {"action": "none", "note": "container missing"}
    started = st["status"] not in ("running", "restarting")  # restart policy may already be bringing it back
    if started:
        ctx.docker.run(["start", e["target"]])
    ok = _wait(ctx, lambda: ctx.docker.state(e["target"])["status"] == "running", 30)
    if not ok:
        raise FaultError(f"{e['target']} not running after rollback (status {ctx.docker.state(e['target'])['status']})")
    return {"action": "start" if started else "none", "status_before": st["status"], "running": ok}


# --- container-pause ---------------------------------------------------------
def pause_inject(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    ctx.docker.run(["pause", e["target"]])
    return {}


def pause_rollback(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    st = ctx.docker.state(e["target"])
    if st["status"] == "paused":
        ctx.docker.run(["unpause", e["target"]])
        return {"action": "unpause", "status": ctx.docker.state(e["target"])["status"]}
    return {"action": "none", "status": st["status"]}


# --- network-delay (tc netem via helper container) ---------------------------
def _helper_name(target: str) -> str:
    return f"chaosctl-netem-{target}"


def _tc(ctx: Ctx, target: str, *args: str, check: bool = False):
    return ctx.docker.run(["run", "--rm", "--network", f"container:{target}", "--cap-add", "NET_ADMIN",
                           ctx.cfg.sandbox["netem_image"], "tc", *args], check=check, timeout=30)


def netem_active(ctx: Ctx, target: str) -> bool:
    res = _tc(ctx, target, "qdisc", "show", "dev", ctx.cfg.sandbox["interface"])
    return res.returncode == 0 and "netem" in res.stdout


def netem_inject(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    p, target, iface = e["params"], e["target"], ctx.cfg.sandbox["interface"]
    spec = ["delay", f"{int(p.get('delay_ms', 0))}ms"]
    if p.get("jitter_ms"):
        spec.append(f"{int(p['jitter_ms'])}ms")
    if p.get("loss_pct"):
        spec += ["loss", f"{float(p['loss_pct'])}%"]
    ttl = int(e["duration_s"]) + 2  # adapter-level hard TTL: the helper removes its own qdisc
    script = (
        f"cleanup() {{ tc qdisc del dev {iface} root 2>/dev/null; exit 0; }}; trap cleanup TERM INT; "
        f"tc qdisc replace dev {iface} root netem {' '.join(spec)} || exit 1; "
        f"sleep {ttl} & wait $!; cleanup"
    )
    ctx.docker.run(["rm", "-f", _helper_name(target)], check=False)
    ctx.docker.run(["run", "-d", "--rm", "--name", _helper_name(target),
                    "--label", NETEM_LABEL, "--label", f"chaosctl.target={target}",
                    "--label", f"chaosctl.run={e['run_id']}",
                    "--network", f"container:{target}", "--cap-add", "NET_ADMIN",
                    ctx.cfg.sandbox["netem_image"], "sh", "-c", script])
    if not _wait(ctx, lambda: netem_active(ctx, target), 10, 1.0):
        netem_rollback(ctx, e)
        raise FaultError(f"netem did not become active on {target} (cmd: {shlex.join(spec)})")
    return {"helper": _helper_name(target), "netem": " ".join(spec), "adapter_ttl_s": ttl}


def netem_rollback(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    target = e["target"]
    ctx.docker.run(["stop", "-t", "2", _helper_name(target)], check=False)
    ctx.docker.run(["rm", "-f", _helper_name(target)], check=False)
    if ctx.docker.state(target)["status"] != "running":
        return {"action": "helper-removed", "note": "target not running; qdisc dies with its netns"}
    _tc(ctx, target, "qdisc", "del", "dev", ctx.cfg.sandbox["interface"], "root")
    still = netem_active(ctx, target)
    return {"action": "qdisc-deleted", "netem_still_active": still}


# --- cpu-throttle ------------------------------------------------------------
def _nano_cpus(ctx: Ctx, target: str) -> int:
    info = ctx.docker.inspect(target) or {}
    return int((info.get("HostConfig") or {}).get("NanoCpus") or 0)


def cpu_inject(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    orig = _nano_cpus(ctx, e["target"])
    ncpu = ctx.docker.ncpu()
    ctx.docker.run(["update", "--cpus", str(e["params"]["cpus"]), e["target"]])
    return {"orig_nano_cpus": orig, "ncpu": ncpu}


def cpu_rollback(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    st = e.get("state") or {}
    orig = int(st.get("orig_nano_cpus") or 0)
    restore = orig / 1e9 if orig > 0 else float(st.get("ncpu") or ctx.docker.ncpu())
    if not ctx.docker.state(e["target"])["exists"]:
        return {"action": "none", "note": "container missing"}
    ctx.docker.run(["update", "--cpus", f"{restore:g}", e["target"]])
    return {"action": "restored", "cpus": restore, "nano_cpus_now": _nano_cpus(ctx, e["target"])}


# --- traffic-spike (Locust API) -----------------------------------------------
def _locust(ctx: Ctx) -> str:
    return ctx.cfg.sandbox["locust_url"].rstrip("/")


def locust_status(ctx: Ctx) -> dict[str, Any]:
    data = ctx.http.get_json(f"{_locust(ctx)}/stats/requests")
    return {"state": data.get("state"), "user_count": data.get("user_count")}


def spike_inject(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    try:
        before = locust_status(ctx)
    except HttpError as exc:
        raise FaultError(f"cannot read Locust state: {exc}") from exc
    ctx.http.post_form(f"{_locust(ctx)}/swarm",
                       {"user_count": e["params"]["users"], "spawn_rate": e["params"].get("spawn_rate", 5)})
    return {"before": before}


def spike_rollback(ctx: Ctx, e: dict[str, Any]) -> dict[str, Any]:
    before = (e.get("state") or {}).get("before") or {}
    users = before.get("user_count")
    if before.get("state") in (None, "stopped") or not users:
        ctx.http.post_form(f"{_locust(ctx)}/stop", {})
        return {"action": "stopped"}
    ctx.http.post_form(f"{_locust(ctx)}/swarm", {"user_count": users, "spawn_rate": 10})
    return {"action": "restored", "user_count": users}


ADAPTERS: dict[str, tuple[Callable, Callable]] = {
    "container-stop": (stop_inject, stop_rollback),
    "container-pause": (pause_inject, pause_rollback),
    "network-delay": (netem_inject, netem_rollback),
    "cpu-throttle": (cpu_inject, cpu_rollback),
    "traffic-spike": (spike_inject, spike_rollback),
}


# --- verified rollback: desired-state snapshot -> undo -> compare -> escalate ----------------
def snapshot(ctx: Ctx, entry: dict[str, Any]) -> dict[str, Any]:
    """The healthy state we must return to, captured BEFORE injection."""
    if entry["fault_type"] == "traffic-spike":
        try:
            return {"locust_users": locust_status(ctx).get("user_count")}
        except HttpError:
            return {}
    return {"status": ctx.docker.state(entry["target"])["status"], "nano_cpus": _nano_cpus(ctx, entry["target"])}


def verify(ctx: Ctx, entry: dict[str, Any], desired: dict[str, Any]) -> list[dict[str, Any]]:
    """Compare actual state with the desired snapshot; every check is recorded as evidence."""
    checks = []
    if entry["fault_type"] == "traffic-spike":
        if desired.get("locust_users") is not None:
            try:
                now_users = locust_status(ctx).get("user_count")
            except HttpError as exc:
                now_users = f"unreadable: {exc}"
            checks.append({"check": "load back to baseline users", "expected": desired["locust_users"],
                           "actual": now_users, "ok": now_users == desired["locust_users"]})
        return checks
    st = ctx.docker.state(entry["target"])
    checks.append({"check": "container running", "expected": desired.get("status", "running"), "actual": st["status"],
                   "ok": st["status"] == desired.get("status", "running")})
    if entry["fault_type"] == "cpu-throttle":
        want = desired.get("nano_cpus", 0)
        got = _nano_cpus(ctx, entry["target"])
        ok = got == want or (want == 0 and got >= int(ctx.docker.ncpu() * 1e9))  # 0 = unlimited == all host CPUs
        checks.append({"check": "CPU limit restored", "expected": want, "actual": got, "ok": ok})
    if entry["fault_type"] == "network-delay" and st["status"] == "running":
        active = netem_active(ctx, entry["target"])
        checks.append({"check": "no network fault (tc netem) left", "expected": False, "actual": active, "ok": not active})
    return checks


def rollback_verified(ctx: Ctx, entry: dict[str, Any], attempts: int = 3) -> dict[str, Any]:
    """Undo, then prove it: re-check actual state against the pre-injection snapshot and escalate if needed
    (attempt 2 repeats the adapter rollback; attempt 3 restarts the container). Deterministic by design:
    a safety mechanism must behave identically every time."""
    desired = entry.get("desired") or {}
    history = []
    for n in range(1, attempts + 1):
        if n == attempts and entry["fault_type"] != "traffic-spike" and history:
            ctx.docker.run(["restart", "-t", "5", entry["target"]], check=False)
            res = {"ok": True, "action": "escalated: docker restart"}
        else:
            res = rollback(ctx, entry)
        _wait(ctx, lambda: all(c["ok"] for c in verify(ctx, entry, desired)), 10, 1.0)
        checks = verify(ctx, entry, desired)
        history.append({"attempt": n, "result": res, "checks": checks})
        if res.get("ok") and all(c["ok"] for c in checks):
            return {"ok": True, "verified": True, "attempts": n, "history": history}
    return {"ok": False, "verified": False, "attempts": attempts, "history": history}


def inject(ctx: Ctx, entry: dict[str, Any]) -> dict[str, Any]:
    return ADAPTERS[entry["fault_type"]][0](ctx, entry)


def rollback(ctx: Ctx, entry: dict[str, Any]) -> dict[str, Any]:
    """Idempotent; never raises for 'already rolled back'. Returns a result record."""
    try:
        res = ADAPTERS[entry["fault_type"]][1](ctx, entry)
        return {"ok": True, **res}
    except (DockerError, HttpError, FaultError) as exc:
        return {"ok": False, "error": str(exc)}


def reconcile(ctx: Ctx, registry) -> list[dict[str, Any]]:
    """Roll back every registered fault and remove orphan netem helpers."""
    report = []
    for entry in registry.list():
        res = rollback(ctx, entry)
        report.append({"fault_id": entry["fault_id"], "fault_type": entry["fault_type"],
                       "target": entry["target"], **res})
        if res["ok"]:
            registry.remove(entry["fault_id"])
    try:
        for name in ctx.docker.list_names(NETEM_LABEL):
            target = name.removeprefix("chaosctl-netem-")
            res = rollback(ctx, {"fault_type": "network-delay", "target": target})
            report.append({"orphan_helper": name, **res})
    except DockerError as exc:
        report.append({"ok": False, "error": f"orphan scan failed: {exc}"})
    return report
