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

TELEMETRY = {"otel-collector", "jaeger", "prometheus", "grafana", "opensearch", "telemetry-docs", "opamp-server"}
FEATURE_FLAGS = {"flagd", "flagd-ui"}
INFRA = TELEMETRY | FEATURE_FLAGS  # excluded from blast-radius math (and protected from faults)
LOAD_CLIENTS = {"load-generator"}  # synthetic customers: edges INTO it are the operator's web UI, not data flows
# client-span attributes that identify a datastore/broker call inside the CALLER's trace spans
DB_SYSTEMS = {"redis": "valkey-cart", "valkey": "valkey-cart", "postgresql": "astronomy-db", "postgres": "astronomy-db"}
MESSAGING_SYSTEMS = {"kafka": "kafka"}


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
    if (demo / "compose.chaos-fix.yaml").exists():  # an applied fix (scripts/demo.sh fix <name>)
        args += ["-f", "compose.chaos-fix.yaml"]
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
                       "memory_limit": ((svc.get("deploy") or {}).get("resources") or {}).get("limits", {}).get("memory"),
                       "volumes": [v.get("target") if isinstance(v, dict) else str(v) for v in svc.get("volumes") or []],
                       "command": svc.get("command")}
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


def flow_type(e: dict[str, Any], nodes: dict[str, Any]) -> str:
    """Classify the data flow of an edge from its evidence (env var names) and target kind."""
    keys = " ".join(ev.get("ref", "") for ev in e.get("evidence", [])).upper()
    if e["from"] in TELEMETRY:
        return "telemetry (monitoring scrape)"
    if e["to"] in LOAD_CLIENTS:
        return "operator UI route"
    if e["to"] in FEATURE_FLAGS:
        return "feature-flag config"
    if e["to"] in TELEMETRY:
        return "telemetry"
    if "KAFKA" in keys or e["to"] == "kafka":
        return "async event (Kafka)"
    if "DB_CONNECTION" in keys or "POSTGRES" in keys or e["to"] == "astronomy-db":
        return "database (SQL)"
    if "VALKEY" in keys or "REDIS" in keys or e["to"] == "valkey-cart":
        return "key-value store"
    if e.get("evidence") and all(ev["type"] == "compose-depends_on" for ev in e["evidence"]):
        return "startup ordering only"
    return "sync request (gRPC/HTTP)"


def runtime_edges(cfg: Config, http: Http, lookback_s: int = 3600) -> list[dict[str, Any]]:
    end_ms = int(time.time() * 1000)
    data = http.get_json(f"{cfg.sandbox['jaeger_api'].rstrip('/')}/dependencies",
                         {"endTs": end_ms, "lookback": lookback_s * 1000})
    rows = data.get("data") if isinstance(data, dict) else data
    return [{"from": r["parent"], "to": r["child"], "call_count": r.get("callCount")} for r in rows or []]


def client_span_edges(cfg: Config, http: Http, callers: list[str], lookback_s: int = 3600) -> list[dict[str, Any]]:
    """Datastores/brokers are not traced as services, so Jaeger's dependency graph never shows them.
    Verify those edges from the CALLER's client spans (db.system / messaging.system attributes)."""
    api = cfg.sandbox["jaeger_api"].rstrip("/")
    end = int(time.time() * 1e6)
    found: dict[tuple[str, str], dict[str, Any]] = {}
    for svc in callers:
        try:
            data = http.get_json(f"{api}/traces", {"service": svc, "start": end - lookback_s * 1_000_000,
                                                   "end": end, "limit": 30})
        except (HttpError, ValueError):
            continue
        for tr in (data or {}).get("data") or []:
            procs = {k: v.get("serviceName") for k, v in (tr.get("processes") or {}).items()}
            for sp in tr.get("spans") or []:
                if procs.get(sp.get("processID")) != svc:
                    continue
                tags = {x.get("key"): str(x.get("value")).lower() for x in sp.get("tags") or []}
                target = DB_SYSTEMS.get(tags.get("db.system", "")) or MESSAGING_SYSTEMS.get(tags.get("messaging.system", ""))
                if target:
                    e = found.setdefault((svc, target), {"from": svc, "to": target, "spans": 0,
                                                         "attribute": tags.get("db.system") or tags.get("messaging.system"),
                                                         "example_operation": sp.get("operationName")})
                    e["spans"] += 1
    return list(found.values())


def load_corrections(cfg: Config) -> dict[str, Any]:
    path = cfg.root / "config" / "map-corrections.toml"
    if not path.exists():
        return {"add": [], "remove": []}
    import tomllib
    with path.open("rb") as fh:
        data = tomllib.load(fh)
    return {"add": data.get("add", []), "remove": data.get("remove", [])}


def build_map(cfg: Config, compose: dict[str, Any] | None, runtime: list[dict[str, Any]] | None,
              runtime_error: str | None = None, client_spans: list[dict[str, Any]] | None = None,
              corrections: dict[str, Any] | None = None) -> dict[str, Any]:
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
    for cs in client_spans or []:
        k = (cs["from"], cs["to"])
        e = by_key.setdefault(k, {"from": cs["from"], "to": cs["to"], "evidence": [], "static": False, "runtime": False})
        e["runtime"] = True
        e["evidence"].append({"type": "jaeger-client-span", "spans": cs["spans"], "attribute": cs["attribute"],
                              "operation": cs["example_operation"]})
    corrections = corrections or {"add": [], "remove": []}
    removed = []
    for c in corrections.get("remove", []):
        if by_key.pop((c["from"], c["to"]), None) is not None:
            removed.append(c)
    for c in corrections.get("add", []):
        e = by_key.setdefault((c["from"], c["to"]), {"from": c["from"], "to": c["to"], "evidence": [],
                                                     "static": False, "runtime": False})
        e["manual"] = True
        e["evidence"].append({"type": "manual-correction", "ref": c.get("reason", "")})
        for n in (c["from"], c["to"]):
            nodes.setdefault(n, {"name": n, "image": None, "kind": "unknown(manual)", "healthcheck": None})
    for e in by_key.values():
        e["flow"] = flow_type(e, nodes)
        e["status"] = ("verified-static+runtime" if e["static"] and e["runtime"] else
                       "verified-static" if e["static"] else "verified-runtime" if e["runtime"] else "manual")
        # monitoring, feature flags and the operator UI route are not on the customer request path
        e["telemetry"] = e["to"] in INFRA or e["from"] in INFRA or e["to"] in LOAD_CLIENTS
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
            "sources": {"static": bool(compose), "runtime": runtime is not None, "runtime_error": runtime_error,
                        "client_span_edges": len(client_spans or [])},
            "manual_corrections": {"added": corrections.get("add", []), "removed": removed},
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
    out += ["```", "", "| From | To | Data flow | Status | Evidence |", "|---|---|---|---|---|"]
    for e in m["edges"]:
        if e["telemetry"]:
            continue
        ev = "; ".join(x.get("ref") or (f"jaeger calls={x.get('call_count')}" if "call_count" in x else
                                        f"client spans={x.get('spans')} ({x.get('attribute')})") for x in e["evidence"])
        out.append(f"| {e['from']} | {e['to']} | {e.get('flow', '')} | {e['status']} | {ev} |")
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
    out += ["", "## Data flows per journey", "",
            "Edges among each journey's services, in path order, with the kind of data flow. Async side effects "
            "(events consumed later, off the request path) are listed separately.", ""]
    edges = [e for e in m["edges"] if not e["telemetry"]]
    for n, j in m["journeys"].items():
        svcs = j["services"]
        hops = [e for e in edges if e["from"] in svcs and e["to"] in svcs]
        hops.sort(key=lambda e: (svcs.index(e["from"]), svcs.index(e["to"])))
        # consumers of a broker the journey publishes to run later, off the request path
        brokers = {e["to"] for e in hops if e["flow"].startswith("async")}
        side = [e for e in edges if e["to"] in brokers and e["from"] not in svcs]
        out.append(f"- **{n}**: " + ("; ".join(f"{e['from']} → {e['to']} ({e['flow']})" for e in hops) or "frontend only"))
        if side:
            out.append("  - async consumers (off the request path): " +
                       "; ".join(f"{e['from']} ← {e['to']}" for e in side))
    mc = m.get("manual_corrections", {})
    out += ["", "## Manual corrections (`config/map-corrections.toml`)", ""]
    out += [f"- added {c['from']} → {c['to']}: {c.get('reason', '')}" for c in mc.get("added", [])]
    out += [f"- removed {c['from']} → {c['to']}: {c.get('reason', '')}" for c in mc.get("removed", [])]
    if not mc.get("added") and not mc.get("removed"):
        out.append("_None. Add `[[add]]`/`[[remove]]` entries with a reason and re-run `chaosctl map`._")
    out.append("")
    return "\n".join(out)


def run(cfg: Config, http: Http, out_json: Path, out_md: Path) -> dict[str, Any]:
    compose = compose_config(cfg)
    runtime, err = None, None
    try:
        runtime = runtime_edges(cfg, http)
    except (HttpError, ValueError, KeyError) as exc:
        err = str(exc)
    nodes, s_edges = static_edges(compose)
    callers = sorted({e["from"] for e in s_edges if nodes.get(e["to"], {}).get("kind") == "datastore"})
    spans = client_span_edges(cfg, http, callers) if runtime is not None else []
    m = build_map(cfg, compose, runtime, err, spans, load_corrections(cfg))
    write_json(out_json, m)
    out_md.parent.mkdir(parents=True, exist_ok=True)
    out_md.write_text(render_markdown(m))
    return m
