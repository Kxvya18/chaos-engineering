"""Remediation tools: vetted fix catalog, apply / revert a fix as a compose override, and the fix verdict.

Deterministic. A fix is applied only if it is in fixes/catalog.toml, targets an allow-listed service of the sandbox
project, and the operator allowed it. The proof is a re-run of the SAME pre-registered experiment (compare.py).
"""

from __future__ import annotations

import shutil
import tomllib
from pathlib import Path
from typing import Any

from . import safety
from .config import Config
from .faults import Ctx

FIX_FILE = "compose.chaos-fix.yaml"  # same convention as `scripts/demo.sh fix|unfix` and depmap.compose_config


def catalog(cfg: Config) -> list[dict[str, Any]]:
    p = cfg.root / "fixes" / "catalog.toml"
    if not p.exists():
        return []
    with p.open("rb") as fh:
        return tomllib.load(fh).get("fix", [])


def find_fix(cfg: Config, experiment: dict[str, Any]) -> dict[str, Any] | None:
    f0 = experiment["faults"][0]
    key = experiment.get("candidate_id") or f"{f0['fault_type']}:{f0['target']}"
    return next((f for f in catalog(cfg) if key in f.get("applies_to", [])), None)


def _compose_args(cfg: Config, extra: list[str]) -> list[str]:
    demo = cfg.demo_dir.resolve()
    args = ["compose", "-p", cfg.sandbox["compose_project"], "--project-directory", str(demo)]
    for env_file in (".env", ".env.override"):
        if (demo / env_file).exists():
            args += ["--env-file", str(demo / env_file)]
    for f in cfg.sandbox["compose_files"]:
        args += ["-f", str(demo / f)]
    return args + extra


def apply_fix(cfg: Config, ctx: Ctx, fix: dict[str, Any]) -> dict[str, Any]:
    if fix["service"] not in cfg.allowlist:
        raise safety.SafetyError(f"fix {fix['id']} targets {fix['service']}, which is not allow-listed")
    safety.check_identity(cfg, ctx.docker, fix["service"])
    src = cfg.root / "fixes" / fix["file"]
    dst = cfg.demo_dir.resolve() / FIX_FILE
    shutil.copyfile(src, dst)
    args = _compose_args(cfg, ["-f", str(dst), "up", "--detach", "--wait", "--no-deps", fix["service"]])
    res = ctx.docker.run(args, check=False, timeout=600)
    return {"ok": res.returncode == 0, "fix_id": fix["id"], "file": str(dst),
            "command": "docker " + " ".join(args), "error": res.stderr.strip()[-500:] if res.returncode else None}


def revert_fix(cfg: Config, ctx: Ctx, fix: dict[str, Any]) -> dict[str, Any]:
    dst: Path = cfg.demo_dir.resolve() / FIX_FILE
    dst.unlink(missing_ok=True)
    args = _compose_args(cfg, ["up", "--detach", "--wait", "--no-deps", fix["service"]])
    res = ctx.docker.run(args, check=False, timeout=600)
    return {"ok": res.returncode == 0, "fix_id": fix["id"], "command": "docker " + " ".join(args),
            "error": res.stderr.strip()[-500:] if res.returncode else None}


def verdict(cmp: dict[str, Any]) -> str:
    """'fixed' only when the re-run is measurably better (compare.py) and loses no data."""
    after = cmp["after"]
    lost = after.get("carts_lost")
    if cmp["verdict"] == "improved" and (lost is None or str(lost).startswith("0/")):
        return "fixed"
    return "not_fixed"
