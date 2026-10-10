"""Load config/sandbox.toml into a plain, typed structure."""

from __future__ import annotations

import os
import tomllib
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

DEFAULT_CONFIG = Path("config/sandbox.toml")


class ConfigError(Exception):
    pass


@dataclass
class Journey:
    name: str
    weight: float
    services: list[str]


@dataclass
class Config:
    raw: dict[str, Any]
    root: Path
    sandbox: dict[str, Any] = field(init=False)
    allowlist: list[str] = field(init=False)
    protected: list[str] = field(init=False)
    datastores: list[str] = field(init=False)
    limits: dict[str, Any] = field(init=False)
    watchdog: dict[str, Any] = field(init=False)
    steady_state: dict[str, Any] = field(init=False)
    recovery: dict[str, Any] = field(init=False)
    probes: dict[str, Any] = field(init=False)
    journeys: dict[str, Journey] = field(init=False)

    def __post_init__(self) -> None:
        r = self.raw
        try:
            self.sandbox = r["sandbox"]
            self.allowlist = list(r["allowlist"]["targets"])
            self.protected = list(r["allowlist"]["protected"])
            self.datastores = list(r["allowlist"].get("datastores", []))
            self.limits = r["limits"]
            self.watchdog = r["watchdog"]
            self.steady_state = r["steady_state"]
            self.recovery = r["recovery"]
            self.probes = r["probes"]
            self.journeys = {
                name: Journey(name, float(j["weight"]), list(j["services"]))
                for name, j in r["journeys"].items()
            }
        except KeyError as exc:
            raise ConfigError(f"missing config key: {exc}") from exc
        overlap = set(self.allowlist) & set(self.protected)
        if overlap:
            raise ConfigError(f"targets both allowed and protected: {sorted(overlap)}")

    def path(self, key: str) -> Path:
        p = Path(self.sandbox[key]).expanduser()
        return p if p.is_absolute() else self.root / p

    @property
    def state_dir(self) -> Path:
        return self.path("state_dir")

    @property
    def runs_dir(self) -> Path:
        return self.path("runs_dir")

    @property
    def artifacts_dir(self) -> Path:
        return self.path("artifacts_dir")

    @property
    def demo_dir(self) -> Path:
        return Path(os.environ.get("CHAOS_DEMO_DIR", self.sandbox["demo_dir"])).expanduser()

    def fault_limits(self, fault_type: str) -> dict[str, Any]:
        lim = self.limits.get(fault_type)
        if not isinstance(lim, dict):
            raise ConfigError(f"no limits configured for fault type {fault_type!r}")
        return lim


def load(path: Path | str | None = None) -> Config:
    p = Path(path or os.environ.get("CHAOS_CONFIG", DEFAULT_CONFIG))
    if not p.is_file():
        raise ConfigError(f"config not found: {p} (run from the repo root or set CHAOS_CONFIG)")
    with p.open("rb") as fh:
        raw = tomllib.load(fh)
    return Config(raw=raw, root=p.resolve().parent.parent)
