"""Pre-registered scenarios (scenarios/*.json). The file hash is recorded in every run."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from .config import Config
from .util import read_json, sha256_file


def directory(cfg: Config) -> Path:
    return cfg.root / "scenarios"


def list_all(cfg: Config) -> list[dict[str, Any]]:
    return [{"name": p.stem, **read_json(p)} for p in sorted(directory(cfg).glob("*.json"))]


def load(cfg: Config, name: str) -> tuple[dict[str, Any], dict[str, Any]]:
    path = directory(cfg) / f"{name}.json"
    if not path.exists():
        raise FileNotFoundError(f"scenario {name!r} not found in {directory(cfg)}")
    exp = read_json(path)
    exp.setdefault("rank", 0)
    return exp, {"scenario": name, "path": str(path), "file_sha256": sha256_file(path)}
