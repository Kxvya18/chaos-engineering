"""Thin, mockable wrapper around the Docker CLI (no Docker SDK dependency)."""

from __future__ import annotations

import json
import subprocess
from dataclasses import dataclass
from typing import Any


class DockerError(Exception):
    pass


@dataclass
class Result:
    returncode: int
    stdout: str
    stderr: str


class DockerCLI:
    def __init__(self, binary: str = "docker"):
        self.binary = binary

    def run(self, args: list[str], check: bool = True, timeout: float = 60) -> Result:
        try:
            p = subprocess.run([self.binary, *args], capture_output=True, text=True, timeout=timeout)
        except FileNotFoundError as exc:
            raise DockerError("docker CLI not found on PATH") from exc
        except subprocess.TimeoutExpired as exc:
            raise DockerError(f"docker {' '.join(args)} timed out after {timeout}s") from exc
        res = Result(p.returncode, p.stdout, p.stderr)
        if check and p.returncode != 0:
            raise DockerError(f"docker {' '.join(args)} failed: {p.stderr.strip() or p.stdout.strip()}")
        return res

    # --- queries -----------------------------------------------------------
    def inspect(self, name: str) -> dict[str, Any] | None:
        res = self.run(["inspect", name], check=False)
        if res.returncode != 0:
            return None
        data = json.loads(res.stdout or "[]")
        return data[0] if data else None

    def state(self, name: str) -> dict[str, Any]:
        info = self.inspect(name)
        if not info:
            return {"exists": False, "status": None, "health": None, "restart_count": None}
        st = info.get("State", {})
        return {
            "exists": True,
            "status": st.get("Status"),
            "health": (st.get("Health") or {}).get("Status"),
            "restart_count": info.get("RestartCount"),
        }

    def context_endpoint(self) -> str:
        name = self.run(["context", "show"]).stdout.strip()
        data = json.loads(self.run(["context", "inspect", name]).stdout or "[]")
        try:
            return data[0]["Endpoints"]["docker"]["Host"]
        except (IndexError, KeyError) as exc:
            raise DockerError(f"cannot read endpoint of docker context {name!r}") from exc

    def ncpu(self) -> int:
        return int(self.run(["info", "--format", "{{.NCPU}}"]).stdout.strip() or "1")

    def list_names(self, label: str) -> list[str]:
        res = self.run(["ps", "-a", "--filter", f"label={label}", "--format", "{{.Names}}"], check=False)
        return [n for n in res.stdout.split() if n]
