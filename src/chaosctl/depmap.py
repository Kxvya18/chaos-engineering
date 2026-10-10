"""Build an evidence-graded dependency map.

static : `docker compose config --format json` (env vars naming other services, depends_on)
runtime: Jaeger dependency graph (/api/dependencies) produced by real traffic
Each edge: verified-static+runtime | verified-static | verified-runtime | assumed (journey config only).
"""

from __future__ import annotations

import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any

from .config import Config
from .http import Http, HttpError
from .util import iso, write_json

INFRA = {"otel-collector", "jaeger", "prometheus", "grafana", "opensearch", "flagd", "flagd-ui",
         "telemetry-docs", "opamp-server"}


def compose_config(cfg: Config) -> dict[str, Any]:
    demo = cfg.demo_dir
    if not demo.is_dir():
        raise FileNotFoundError(f"demo dir not found: {demo} (set sandbox.demo_dir or CHAOS_DEMO_DIR)")
    args = ["docker", "compose", "-p", cfg.sandbox["compose_project"]]
    for env_file in (".env", ".env.override"):  # same as the demo Makefile
        if (demo / env_file).exists():
            args += ["--env-file", env_file]
    for f in cfg.sandbox["compose_files"]:
        args += ["-f", f]
    args += ["config", "--format", "json"]
    p = subprocess.run(args, cwd=demo, capture_output=True, text=True, timeout=120)
    if p.returncode != 0:
        raise RuntimeError(f"docker compose config failed: {p.stderr.strip()}")
    return json.loads(p.stdout)


def _env_items(env: Any) -> list[tuple[str, str]]:
    if isinstance(env, dict):
        return [(k, "" if v is None else str(v)) for k, v in env.items()]
    if isinstance(env, list):
        return [(e.split("=", 1)[0], e.split("=", 1)[1] if "=" in e else "") for e in env]
    return []


def static_edges(compose: dict[str, Any]) -> tuple[dict[str, dict[str, Any]], list[dict[str, Any]]]:
    services = compose.get("services", {})
    names = sorted(services, key=len, reverse=True)
    nodes, edges = {}, {}
    for name, svc in services.items():
        image = svc.get("image", "")
        kind = ("infra" if name in INFRA else
                "datastore" if re.search(r"postgres|valkey|redis|kafka|mongo|mysql", image + name) else "app")
        nodes[name] = {"name": name, "image": image, "kind": kind,
                       "healthcheck": bool(svc.get("healthcheck")) and not (svc.get("healthcheck") or {}).get("disable"),
                       "restart": svc.get("restart"),
                       "memory_limit": ((svc.get("deploy") or {}).get("resources") or {}).get("limits", {}).get("memory")}
        deps = svc.get("depends_on") or {}
        deps = deps if isinstance(deps, dict) else {d: {} for d in deps}
        for key, val in _env_items(svc.get("environment")):
            for other in names:
                if other == name:
                    continue
                if re.search(rf"(?<![\w.-]){re.escape(other)}(?=[:/;,\s]|$)", val):
                    e = edges.setdefault((name, other), {"from": name, "to": other, "evidence": []})
                    e["evidence"].append({"type": "compose-env", "ref": f"services.{name}.environment.{key}"})
                    break
        for dep, spec in deps.items():
            e = edges.setdefault((name, dep), {"from": name, "to": dep, "evidence": []})
            cond = (spec or {}).get("condition", "service_started") if isinstance(spec, dict) else "service_started"
            e["evidence"].append({"type": "compose-depends_on", "ref": f"services.{name}.depends_on.{dep}",
                                  "condition": cond})
    return nodes, list(edges.values())


def runtime_edges(cfg: Config, http: Http, lookback_s: int = 3600) -> list[dict[str, Any]]:
    end_ms = int(time.time() * 1000)
    data = http.get_json(f"{cfg.sandbox['jaeger_api'].rstrip('/')}/dependencies",
                         {"endTs": end_ms, "lookback": lookback_s * 1000})
    rows = data.get("data") if isinstance(data, dict) else data
    return [{"from": r["parent"], "to": r["child"], "call_count": r.get("callCount")} for r in rows or []]


def build_map(cfg: Config, compose: dict[str, Any] | None, runtime: list[dict[str, Any]] | None,
              runtime_error: str | None = None) -> dict[str, Any]:
    nodes, s_edges = static_edges(compose) if compose else ({}, [])
    by_key = {(e["from"], e["to"]): {**e, "static": True, "runtime": False} for e in s_edges}
    for r in runtime or []:
        k = (r["from"], r["to"])
        if r["from"] == r["to"]:
            continue
        e = by_key.setdefault(k, {"from": r["from"], "to": r["to"], "evidence": [], "static": False, "runtime": False})
        e["runtime"] = True
        e["evidence"].append({"type": "jaeger-dependencies", "call_count": r["call_count"]})
        for n in k:
            nodes.setdefault(n, {"name": n, "image": None, "kind": "unknown(runtime-only)", "healthcheck": None})
    for e in by_key.values():
        e["status"] = ("verified-static+runtime" if e["static"] and e["runtime"] else
                       "verified-static" if e["static"] else "verified-runtime")
        e["telemetry"] = e["to"] in INFRA
    runtime_set = {(e["from"], e["to"]) for e in by_key.values() if e["runtime"]}
    journeys = {}
    for name, j in cfg.journeys.items():
        hops = list(zip(j.services, j.services[1:], strict=False))
        journeys[name] = {"weight": j.weight, "services": j.services,
                          "service_status": {s: ("seen-in-runtime" if any(s in k for k in runtime_set)
                                                 else "assumed") for s in j.services},
                          "hops_unverified": [list(h) for h in hops if h not in runtime_set and
                                              (h[1], h[0]) not in runtime_set]}
    drift = {
        "static_only": sorted(f"{e['from']}->{e['to']}" for e in by_key.values()
                              if e["status"] == "verified-static" and not e["telemetry"]),
        "runtime_only": sorted(f"{e['from']}->{e['to']}" for e in by_key.values()
                               if e["status"] == "verified-runtime"),
        "missing_depends_on": sorted(f"{e['from']}->{e['to']}" for e in by_key.values()
                                     if not e["telemetry"] and nodes.get(e["to"], {}).get("kind") == "datastore"
                                     and not any(ev["type"] == "compose-depends_on" for ev in e["evidence"])),
        "no_healthcheck": sorted(n for n, v in nodes.items() if v.get("healthcheck") is False and v["kind"] != "infra"),
    }
    return {"generated": iso(), "app": "opentelemetry-demo", "version": "3.1.0",
            "sources": {"static": bool(compose), "runtime": runtime is not None, "runtime_error": runtime_error},
            "services": sorted(nodes.values(), key=lambda n: n["name"]),
            "edges": sorted(by_key.values(), key=lambda e: (e["from"], e["to"])),
            "journeys": journeys, "drift": drift}


def dependents(dep_map: dict[str, Any], target: str) -> set[str]:
    """Services that (transitively) call `target`, ignoring telemetry edges."""
    rev: dict[str, set[str]] = {}
    for e in dep_map.get("edges", []):
        if not e.get("telemetry"):
            rev.setdefault(e["to"], set()).add(e["from"])
    seen, stack = set(), [target]
    while stack:
        for p in rev.get(stack.pop(), ()):
            if p not in seen:
                seen.add(p)
                stack.append(p)
    return seen


def journeys_touching(cfg: Config, target: str) -> list[str]:
    return sorted(n for n, j in cfg.journeys.items() if target in j.services)


def render_markdown(m: dict[str, Any]) -> str:
    out = [f"# Dependency map — {m['app']} {m['version']}", "",
           f"Generated {m['generated']} by `chaosctl map`. Sources: static={m['sources']['static']}, "
           f"runtime(Jaeger)={m['sources']['runtime']}"
           + (f" (runtime error: {m['sources']['runtime_error']})" if m["sources"].get("runtime_error") else ""),
           "", "Edge status: **verified-static+runtime** (config and live traces agree), **verified-static** "
           "(config/env only), **verified-runtime** (traces only = drift). Telemetry edges (collector, flagd) "
           "are listed but excluded from blast-radius math.", "", "```mermaid", "graph LR"]
    for e in m["edges"]:
        if e["telemetry"]:
            continue
        style = "==>" if e["status"] == "verified-static+runtime" else "-->" if e["static"] else "-.->"
        out.append(f"  {e['from'].replace('-', '_')}[{e['from']}] {style} {e['to'].replace('-', '_')}[{e['to']}]")
    out += ["```", "", "| From | To | Status | Evidence |", "|---|---|---|---|"]
    for e in m["edges"]:
        if e["telemetry"]:
            continue
        ev = "; ".join(x.get("ref") or f"jaeger calls={x.get('call_count')}" for x in e["evidence"])
        out.append(f"| {e['from']} | {e['to']} | {e['status']} | {ev} |")
    d = m["drift"]
    out += ["", "## Drift and gaps", "",
            f"- Static-only edges (not seen in traces): {', '.join(d['static_only']) or 'none'}",
            f"- Runtime-only edges (not declared in config): {', '.join(d['runtime_only']) or 'none'}",
            f"- Datastore calls without `depends_on`: {', '.join(d['missing_depends_on']) or 'none'}",
            f"- Services without healthcheck: {', '.join(d['no_healthcheck']) or 'none'}",
            "", "## Journeys (service paths are ASSUMED from frontend source unless seen in runtime)", "",
            "| Journey | Weight | Services | Unverified hops |", "|---|---|---|---|"]
    for n, j in m["journeys"].items():
        out.append(f"| {n} | {j['weight']} | {' → '.join(j['services'])} | "
                   f"{', '.join('→'.join(h) for h in j['hops_unverified']) or 'none'} |")
    out += ["", "## Manual corrections", "", "_None yet. Record any correction here with the evidence used._", ""]
    return "\n".join(out)


def run(cfg: Config, http: Http, out_json: Path, out_md: Path) -> dict[str, Any]:
    compose = compose_config(cfg)
    runtime, err = None, None
    try:
        runtime = runtime_edges(cfg, http)
    except (HttpError, ValueError, KeyError) as exc:
        err = str(exc)
    m = build_map(cfg, compose, runtime, err)
    write_json(out_json, m)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(m))
    return m
