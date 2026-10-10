"""Collect root-cause evidence after a run: Jaeger error traces, container logs, compose facts.

Everything is best-effort and recorded with its source; collection errors are kept as evidence
of what could NOT be observed (never silently dropped).
"""

from __future__ import annotations

import json
import re
from collections import Counter
from pathlib import Path
from typing import Any

from .config import Config
from .dockerctl import DockerError
from .faults import Ctx
from .http import HttpError
from .util import iso, read_json, write_json

ERR_LINE = re.compile(r"error|exception|fail|refused|timeout|unavailable|reset|denied|panic", re.IGNORECASE)
MSG_TAGS = ("otel.status_description", "error.message", "exception.message", "exception.type",
            "rpc.grpc.status_code", "http.response.status_code", "http.status_code")


def _tags(span: dict[str, Any]) -> dict[str, Any]:
    tags = {t.get("key"): t.get("value") for t in span.get("tags") or []}
    for log in span.get("logs") or []:
        for f in log.get("fields") or []:
            if f.get("key") in ("exception.message", "exception.type", "message"):
                tags.setdefault(f["key"], f.get("value"))
    return tags


def _is_error(tags: dict[str, Any]) -> bool:
    return tags.get("error") in (True, "true") or str(tags.get("otel.status_code", "")).upper() == "ERROR"


def summarize_traces(traces: list[dict[str, Any]]) -> dict[str, Any]:
    by_op: Counter = Counter()
    root_cause: Counter = Counter()
    messages: dict[str, Counter] = {}
    for tr in traces:
        procs = {k: v.get("serviceName") for k, v in (tr.get("processes") or {}).items()}
        spans = tr.get("spans") or []
        err_ids = set()
        info = {}
        for sp in spans:
            tags = _tags(sp)
            if _is_error(tags):
                err_ids.add(sp["spanID"])
                svc = procs.get(sp.get("processID"), "?")
                info[sp["spanID"]] = (svc, sp.get("operationName"))
                by_op[f"{svc} :: {sp.get('operationName')}"] += 1
                msg = next((str(tags[k]) for k in MSG_TAGS if tags.get(k) not in (None, "")), None)
                if msg:
                    messages.setdefault(svc, Counter())[msg[:160]] += 1
        parents = {r.get("spanID") for sp in spans for r in sp.get("references") or []
                   if r.get("refType") == "CHILD_OF" and sp["spanID"] in err_ids}
        # deepest failing spans: errored spans with no errored child = where the failure originated
        for sid in err_ids - parents:
            root_cause[info[sid][0]] += 1
    return {"error_traces": sum(1 for _ in traces), "error_spans_by_operation": by_op.most_common(10),
            "origin_service_votes": root_cause.most_common(5),
            "top_messages": {svc: c.most_common(3) for svc, c in messages.items()}}


def jaeger_errors(cfg: Config, ctx: Ctx, services: list[str], start: float, end: float) -> dict[str, Any]:
    api = cfg.sandbox["jaeger_api"].rstrip("/")
    traces: dict[str, dict[str, Any]] = {}
    errors = {}
    for svc in services:
        try:
            data = ctx.http.get_json(f"{api}/traces", {"service": svc, "start": int(start * 1e6),
                                                       "end": int(end * 1e6), "limit": 50,
                                                       "tags": json.dumps({"error": "true"})})
            for tr in (data or {}).get("data") or []:
                traces[tr.get("traceID")] = tr
        except (HttpError, ValueError) as exc:
            errors[svc] = str(exc)
    return {"queried_services": services, "query_errors": errors, **summarize_traces(list(traces.values()))}


def container_logs(ctx: Ctx, name: str, start: float, end: float, limit: int = 12) -> dict[str, Any]:
    try:
        res = ctx.docker.run(["logs", "--since", iso(start), "--until", iso(end), name], check=False, timeout=30)
    except DockerError as exc:
        return {"container": name, "error": str(exc)}
    lines = (res.stdout + "\n" + res.stderr).splitlines()
    hits = [ln.strip()[:240] for ln in lines if ERR_LINE.search(ln)]
    return {"container": name, "lines_total": len(lines), "error_lines": len(hits),
            "first": hits[:limit // 2], "last": hits[-(limit // 2):] if len(hits) > limit // 2 else []}


def compose_facts(dep_map: dict[str, Any], names: list[str]) -> dict[str, Any]:
    nodes = {n["name"]: n for n in dep_map.get("services", [])}
    out = {}
    for n in names:
        node = nodes.get(n)
        if node:
            out[n] = {k: node.get(k) for k in ("kind", "image", "restart", "healthcheck", "memory_limit",
                                                "volumes", "command")}
    return out


def collect(cfg: Config, ctx: Ctx, run_dir: Path) -> dict[str, Any]:
    exp = read_json(run_dir / "experiment.json")
    a = read_json(run_dir / "analysis.json")
    t = a["timeline"]
    start = (t.get("inject") or t.get("pre_start") or 0) - 5
    end = t.get("end") or start + 300
    dep_path = cfg.artifacts_dir / "dependency-map.json"
    dep_map = read_json(dep_path) if dep_path.exists() else {}
    targets = [f["target"] for f in exp["experiment"]["faults"] if f["fault_type"] != "traffic-spike"]
    callers = sorted({e["from"] for e in dep_map.get("edges", []) for tg in targets
                      if e["to"] == tg and not e.get("telemetry")})
    # upstream services on every journey path that passes through a target (works without a dependency map)
    upstream = {s for j in cfg.journeys.values() for tg in targets if tg in j.services
                for s in j.services[:j.services.index(tg)]}
    callers = sorted(set(callers) | {s for s in upstream if s != "frontend"} & set(cfg.allowlist))
    trace_services = sorted({"frontend", *callers, *targets})
    ev = {
        "collected": iso(),
        "window": {"start": iso(start), "end": iso(end)},
        "targets": targets,
        "callers": callers,
        "compose_facts": compose_facts(dep_map, sorted({*targets, *callers})),
        "dependency_map_available": bool(dep_map),
        "traces": jaeger_errors(cfg, ctx, trace_services, start, end),
        "logs": [container_logs(ctx, n, start, end) for n in sorted({*targets, *callers})],
    }
    write_json(run_dir / "evidence.json", ev)
    return ev
