"""Simulated sandbox (fake Docker + fake app + fake Locust) for selftest and unit tests.

Nothing here touches a real container. Latencies are scaled so tests finish in seconds.
"""

from __future__ import annotations

import json
import threading
import time
from typing import Any

from .config import Config
from .dockerctl import DockerError, Result
from .http import HttpError

NETEM_SCALE = 0.2  # simulated latency = netem delay x scale


class FakeDocker:
    def __init__(self, cfg: Config, endpoint: str = "unix:///var/run/docker.sock"):
        self.cfg = cfg
        self.endpoint = endpoint
        self.lock = threading.Lock()
        names = set(cfg.allowlist) | set(cfg.protected)
        self.containers: dict[str, dict[str, Any]] = {
            n: {"status": "running", "nano_cpus": 0, "restart_count": 0,
                "project": cfg.sandbox["compose_project"], "networks": [cfg.sandbox["docker_network"]]}
            for n in names}
        self.netem: dict[str, str] = {}
        self.helpers: dict[str, str] = {}  # helper name -> target
        self.fail_on: set[str] = set()
        self.calls: list[list[str]] = []

    # --- DockerCLI interface -------------------------------------------------
    def run(self, args: list[str], check: bool = True, timeout: float = 60) -> Result:
        with self.lock:
            self.calls.append(list(args))
            res = self._dispatch(args)
        if check and res.returncode != 0:
            raise DockerError(f"docker {' '.join(args)} failed: {res.stderr}")
        return res

    def _dispatch(self, a: list[str]) -> Result:
        ok = Result(0, "", "")
        cmd = a[0]
        if cmd in self.fail_on:
            return Result(1, "", f"simulated failure of {cmd}")
        if cmd in ("stop", "kill", "start", "pause", "unpause"):
            name = a[-1]
            if name in self.helpers:
                if cmd == "stop":
                    self.netem.pop(self.helpers.pop(name), None)  # trap cleanup removes qdisc
                return ok
            c = self.containers.get(name)
            if not c:
                return Result(1, "", f"No such container: {name}")
            if cmd in ("stop", "kill"):
                c["status"] = "exited"
                self.netem.pop(name, None)
            elif cmd == "start":
                c["status"] = "running"
            elif cmd == "pause":
                if c["status"] != "running":
                    return Result(1, "", "not running")
                c["status"] = "paused"
            elif cmd == "unpause":
                if c["status"] != "paused":
                    return Result(1, "", "not paused")
                c["status"] = "running"
            return ok
        if cmd == "rm":
            self.helpers.pop(a[-1], None)  # rm -f: qdisc stays (realistic) until tc del
            return ok
        if cmd == "update":
            c = self.containers[a[-1]]
            c["nano_cpus"] = int(float(a[a.index("--cpus") + 1]) * 1e9)
            return ok
        if cmd == "run":
            net = a[a.index("--network") + 1].removeprefix("container:")
            if self.containers.get(net, {}).get("status") != "running":
                return Result(125, "", "cannot join network of a non-running container")
            if "-d" in a:
                name = a[a.index("--name") + 1]
                script = a[-1]
                spec = script.split("netem", 1)[1].split("||", 1)[0].strip()
                self.helpers[name] = net
                self.netem[net] = spec
                return Result(0, "helperid\n", "")
            tc = a[a.index("tc") + 1:]
            if tc[:2] == ["qdisc", "show"]:
                return Result(0, f"qdisc netem 8001: root {self.netem[net]}\n" if net in self.netem
                              else "qdisc noqueue 0: root\n", "")
            if tc[:2] == ["qdisc", "del"]:
                if net in self.netem:
                    self.netem.pop(net)
                    return ok
                return Result(2, "", "Error: Cannot delete qdisc with handle of zero.")
            return ok
        if cmd == "ps":
            return Result(0, "\n".join(self.helpers) + "\n", "")
        if cmd == "context":
            if a[1] == "show":
                return Result(0, "desktop-linux\n", "")
            return Result(0, json.dumps([{"Endpoints": {"docker": {"Host": self.endpoint}}}]), "")
        if cmd == "info":
            return Result(0, "8\n", "")
        return Result(1, "", f"unsupported fake command {a}")

    def inspect(self, name: str) -> dict[str, Any] | None:
        c = self.containers.get(name)
        if not c:
            return None
        return {"State": {"Status": c["status"]}, "RestartCount": c["restart_count"],
                "HostConfig": {"NanoCpus": c["nano_cpus"]},
                "Config": {"Labels": {"com.docker.compose.project": c["project"]}},
                "NetworkSettings": {"Networks": {n: {} for n in c["networks"]}}}

    def state(self, name: str) -> dict[str, Any]:
        c = self.containers.get(name)
        if not c:
            return {"exists": False, "status": None, "health": None, "restart_count": None}
        return {"exists": True, "status": c["status"], "health": None, "restart_count": c["restart_count"]}

    def context_endpoint(self) -> str:
        return self.endpoint

    def ncpu(self) -> int:
        return 8

    def list_names(self, label: str) -> list[str]:
        return list(self.helpers)


class FakeHttp:
    """Fake Locust API."""

    def __init__(self):
        self.users = 5
        self.state = "running"

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if url.endswith("/stats/requests"):
            return {"state": self.state, "user_count": self.users}
        raise HttpError(f"fake: no route {url}")

    def post_form(self, url: str, form: dict[str, Any]) -> Any:
        if url.endswith("/swarm"):
            self.users, self.state = int(form["user_count"]), "running"
        elif url.endswith("/stop"):
            self.state = "stopped"
        return {"success": True}


def fake_journeys(cfg: Config, docker: FakeDocker, http: FakeHttp, base_latency_s: float = 0.005):
    def make(services: list[str]):
        def fn():
            delay = base_latency_s
            for s in services:
                c = docker.containers.get(s)
                if c is None:
                    continue
                if c["status"] == "exited":
                    time.sleep(delay)
                    return False, 503, f"{s} unavailable"
                if c["status"] == "paused":
                    time.sleep(0.3)
                    return False, 504, f"{s} timed out"
                spec = docker.netem.get(s)
                if spec:
                    ms = float(spec.split()[1].removesuffix("ms"))
                    delay += ms / 1000 * NETEM_SCALE
                if c["nano_cpus"] and c["nano_cpus"] < 0.5e9:
                    delay += 0.05
            if http.users > 30:
                delay += 0.02
            time.sleep(delay)
            return True, 200, None
        return fn
    return {name: make(j.services) for name, j in cfg.journeys.items()}
