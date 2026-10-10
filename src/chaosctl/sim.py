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
            n: {"status": "running", "nano_cpus": 0, "restart_count": 0, "pid": 100 + i,
                "project": cfg.sandbox["compose_project"], "networks": [cfg.sandbox["docker_network"]]}
            for i, n in enumerate(sorted(names))}
        self.netem: dict[str, str] = {}
        self.persistent: dict[str, bool] = {}  # datastore -> survives a crash (AOF enabled)
        self.carts: dict[str, dict[str, int]] = {}  # the valkey-cart dataset
        self.restart_delay_s = 1.0
        self.crash_times: dict[str, float] = {}
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
        if cmd == "run" and "--pid=host" in a:
            pid = int(a[-1])
            name = next((n for n, c in self.containers.items() if c.get("pid") == pid), None)
            if name is None:
                return Result(1, "", f"kill: ({pid}) - No such process")
            self._crash(name)
            return ok
        if cmd == "logs":
            name = a[-1]
            if name in self.crash_times:
                return Result(0, "", "Error: It was not possible to connect to the redis server(s). UnableToConnect\n"
                                     "RedisConnectionException: No connection is active/available to service this operation\n")
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
            if any("com.docker.compose.project" in x for x in a):
                rows = []
                for n, c in self.containers.items():
                    if c["status"] == "running":
                        since = time.time() - self.crash_times.get(n, 0)
                        st = "Up 2 seconds (health: starting)" if since < 2 else "Up 9 minutes (healthy)"
                    elif c["status"] == "paused":
                        st = "Up 9 minutes (Paused)"
                    else:
                        st = f"Exited ({c.get('exit_code', 0)}) 1 second ago"
                    rows.append(f"{n}\t{c['status']}\t{st}")
                return Result(0, "\n".join(rows) + "\n", "")
            return Result(0, "\n".join(self.helpers) + "\n", "")
        if cmd == "context":
            if a[1] == "show":
                return Result(0, "desktop-linux\n", "")
            return Result(0, json.dumps([{"Endpoints": {"docker": {"Host": self.endpoint}}}]), "")
        if cmd == "info":
            return Result(0, "8\n", "")
        return Result(1, "", f"unsupported fake command {a}")

    def _crash(self, name: str) -> None:
        """Unexpected exit (SIGKILL): Docker's unless-stopped policy restarts it after a short delay."""
        c = self.containers[name]
        c.update(status="exited", exit_code=137)
        self.crash_times[name] = time.time()
        if name == "valkey-cart" and not self.persistent.get(name):
            self.carts.clear()  # in-memory only: the dataset is gone

        def restart():
            time.sleep(self.restart_delay_s)
            with self.lock:
                if c["status"] == "exited":
                    c.update(status="running", exit_code=0, restart_count=c["restart_count"] + 1,
                             pid=c["pid"] + 1000)
        threading.Thread(target=restart, daemon=True).start()

    def inspect(self, name: str) -> dict[str, Any] | None:
        c = self.containers.get(name)
        if not c:
            return None
        return {"State": {"Status": c["status"], "Pid": c["pid"] if c["status"] == "running" else 0,
                          "ExitCode": c.get("exit_code", 0)}, "RestartCount": c["restart_count"],
                "HostConfig": {"NanoCpus": c["nano_cpus"]},
                "Config": {"Labels": {"com.docker.compose.project": c["project"]}},
                "NetworkSettings": {"Networks": {n: {} for n in c["networks"]}}}

    def state(self, name: str) -> dict[str, Any]:
        c = self.containers.get(name)
        if not c:
            return {"exists": False, "status": None, "health": None, "restart_count": None}
        return {"exists": True, "status": c["status"], "health": None, "restart_count": c["restart_count"],
                "exit_code": c.get("exit_code", 0), "oom_killed": False, "started_at": None,
                "pid": c["pid"] if c["status"] == "running" else 0}

    def context_endpoint(self) -> str:
        return self.endpoint

    def ncpu(self) -> int:
        return 8

    def list_names(self, label: str) -> list[str]:
        return list(self.helpers)


class FakeHttp:
    """Fake Locust API, cart API (backed by FakeDocker.carts) and Jaeger traces API."""

    def __init__(self, docker: FakeDocker | None = None):
        self.users = 5
        self.state = "running"
        self.docker = docker

    def _store_up(self) -> bool:
        return bool(self.docker) and self.docker.containers["valkey-cart"]["status"] == "running"

    def request(self, method: str, url: str, body: bytes | None = None, headers: dict[str, str] | None = None,
                timeout: float | None = None) -> tuple[int, bytes, float]:
        if "/api/cart" not in url or not self.docker:
            return 404, b"", 1.0
        if not self._store_up():
            return 500, b'{"error":"cart store unavailable"}', 5.0
        if method == "POST":
            d = json.loads(body or b"{}")
            cart = self.docker.carts.setdefault(d["userId"], {})
            cart[d["item"]["productId"]] = cart.get(d["item"]["productId"], 0) + d["item"]["quantity"]
            return 200, b"{}", 2.0
        user = url.split("sessionId=")[1].split("&")[0]
        items = [{"productId": k, "quantity": v} for k, v in self.docker.carts.get(user, {}).items()]
        return 200, json.dumps({"userId": user, "items": items}).encode(), 2.0

    def get_json(self, url: str, params: dict[str, Any] | None = None) -> Any:
        if url.endswith("/stats/requests"):
            return {"state": self.state, "user_count": self.users}
        if url.endswith("/traces"):
            if not self.docker or "valkey-cart" not in self.docker.crash_times or params.get("service") != "cart":
                return {"data": []}
            tr = {"traceID": "t1", "processes": {"p1": {"serviceName": "frontend"}, "p2": {"serviceName": "cart"}},
                  "spans": [{"spanID": "a", "processID": "p1", "operationName": "POST /api/cart",
                             "tags": [{"key": "error", "value": True}], "references": []},
                            {"spanID": "b", "processID": "p2", "operationName": "oteldemo.CartService/AddItem",
                             "tags": [{"key": "error", "value": True},
                                      {"key": "otel.status_description", "value": "Can't access cart storage."}],
                             "references": [{"refType": "CHILD_OF", "spanID": "a"}]}]}
            return {"data": [tr]}
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
