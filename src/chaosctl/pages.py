"""Day-1 checkpoint pages: milestone checklist, dependency map (SVG), baseline report.

Charts follow the dataviz rules used in htmlreport.py: max 3 categorical hues for a graph (all-pairs
validated slots 1-3), meaning never by colour alone (line style + legend + table), status = icon + label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

from . import ui
from .config import Config
from .htmlreport import CSS, esc, pct, small_multiple
from .util import read_json, read_jsonl

FLOW_GROUP = {  # flow label -> (legend group, css colour var)
    "sync request (gRPC/HTTP)": ("sync request", "var(--series-1)"),
    "async event (Kafka)": ("async event", "var(--series-2)"),
    "database (SQL)": ("datastore (SQL / key-value)", "var(--series-3)"),
    "key-value store": ("datastore (SQL / key-value)", "var(--series-3)"),
}
EXTRA_CSS = """
:root{--series-2:#eb6834;--series-3:#1baf7a}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--series-2:#d95926;--series-3:#199e70}}
:root[data-theme="dark"]{--series-2:#d95926;--series-3:#199e70}
.node rect{fill:var(--surface-1);stroke:var(--border);stroke-width:1.5}.node.datastore rect{stroke:var(--text-secondary);stroke-dasharray:4 2}
.node text{fill:var(--text-primary);font-size:11px}
.node.fault rect,.node.down rect{stroke:var(--critical);stroke-width:3;stroke-dasharray:none}
.node.impacted rect{stroke:var(--serious);stroke-width:3;stroke-dasharray:none}
.node.fault text,.node.down text{fill:var(--critical);font-weight:700}.node.impacted text{font-weight:700}.legend{display:flex;gap:18px;flex-wrap:wrap;font-size:13px;margin:6px 0}
.sw{display:inline-block;width:26px;height:0;border-top:3px solid;vertical-align:middle;margin-right:6px}
"""


def page(title: str, body: str, active: str | None = None) -> str:
    return ui.page(title, body, CSS + EXTRA_CSS, active)


# ---------------------------------------------------------------- checklist
def _runs(cfg: Config) -> list[dict[str, Any]]:
    out = []
    for d in sorted(cfg.runs_dir.glob("*"), reverse=True) if cfg.runs_dir.exists() else []:
        if (d / "analysis.json").exists():
            out.append(read_json(d / "analysis.json"))
    return out


def milestone1(cfg: Config, health: Callable[[], dict[str, Any]], storefront: Callable[[], tuple[bool, str]]) -> list[dict]:
    rows = []
    try:
        h = health()
        down = sorted(t for t in cfg.allowlist if h.get(t, {}).get("state") != "running")
        unhealthy = sorted(n for n, s in h.items() if "unhealthy" in (s.get("status") or ""))
        ok_front, front = storefront()
        rows.append(("1", "Setup and app selection", "App running end to end in the sandbox",
                     not down and not unhealthy and ok_front,
                     f"{sum(1 for s in h.values() if s.get('state') == 'running')} containers running; "
                     f"not running: {down or 'none'}; unhealthy: {unhealthy or 'none'}; storefront: {front}", "/"))
    except Exception as exc:  # docker unreachable from the dashboard
        rows.append(("1", "Setup and app selection", "App running end to end in the sandbox", False, f"{exc}", "/"))
    mp = cfg.artifacts_dir / "dependency-map.json"
    if mp.exists():
        m = read_json(mp)
        edges = [e for e in m["edges"] if not e.get("telemetry")]
        verified = sum(1 for e in edges if e["status"] == "verified-static+runtime")
        flows = sorted({e.get("flow", "?") for e in edges})
        rows.append(("2", "Application mapping", "Services, dependencies and data flows mapped", bool(edges),
                     f"{len(m['services'])} services, {len(edges)} dependencies ({verified} confirmed by config AND live "
                     f"traces); data flows: {', '.join(flows)}; manual corrections: "
                     f"{len(m.get('manual_corrections', {}).get('added', [])) + len(m.get('manual_corrections', {}).get('removed', []))}",
                     "/map"))
    else:
        rows.append(("2", "Application mapping", "Services, dependencies and data flows mapped", False,
                     "not yet: run `chaosctl map`", "/map"))
    bp = cfg.artifacts_dir / "baseline.json"
    if bp.exists():
        b = read_json(bp)
        q = b.get("quality") or {}
        o = b["overall"]
        rows.append(("3", "Monitoring baseline", "Normal error rate, latency and health under steady load",
                     bool(q.get("pass")), f"{o['n']} samples over {b['duration_s']:.0f} s; error rate {pct(o['error_rate'])}; "
                     f"p95 {o['p95_ms']:.0f} ms; quality gate {'ACCEPTED' if q.get('pass') else 'REJECTED'}", "/baseline"))
    else:
        rows.append(("3", "Monitoring baseline", "Normal error rate, latency and health under steady load", False,
                     "not yet: run `chaosctl baseline`", "/baseline"))
    runs = _runs(cfg)
    failures = [a for a in runs if a["timeline"].get("inject") and not a["experiment_id"].startswith("TRIPWIRE")]
    fp = cfg.artifacts_dir / "fmea.json"
    chooser = ""
    if fp.exists():
        fm = read_json(fp)["meta"]
        chooser = ("chosen by AI FMEA (" + str((fm.get("ai_meta") or {}).get("model_served")) + "); "
                   if fm["method"] in ("ai", "ai-cached") else "chosen by rule-based FMEA (AI not available); ")
    rows.append(("4", "First failure injection", "At least one failure triggered and observed", bool(failures),
                 chooser + "; ".join(f"{a['experiment_id']}: impacted {', '.join(a['impacted_journeys']) or 'none'}, restore "
                           f"{a.get('time_to_restore_s')} s" for a in failures[:3]) or "not yet",
                 f"/runs/{failures[0]['run_id']}/" if failures else "/runs"))
    trip = [a for a in runs if a["experiment_id"].startswith("TRIPWIRE") and (a.get("abort") or {}).get("by") == "watchdog"]
    killed = [a for a in runs if a["outcome"] == "kill_switch"]
    rows.append(("5", "Safety controls", "Automatic stop/rollback on threshold breach, tested", bool(trip),
                 (f"auto-stop: {trip[0]['abort']['reason']} in {trip[0]['run_id']}" if trip else "auto-stop: not yet") +
                 (f"; kill switch: tested in {killed[0]['run_id']}" if killed else "; kill switch: not yet tested live"),
                 f"/runs/{trip[0]['run_id']}/" if trip else "/runs"))
    done = all(r[3] for r in rows)
    rows.append(("6", "Day-1 checkpoint", "Short demo: app running, one safe failure, impact visible", done,
                 "all rows above complete" if done else "complete rows 1-5", "/"))
    return [dict(zip(("n", "milestone", "expected", "ok", "evidence", "link"), r, strict=True)) for r in rows]


OK_DONE = '<span class="status ok">✓ done</span>'
OPEN = '<span class="status bad">✕ open</span>'
TICK, WARN, CROSS = ('<span class="status ok">✓</span>', '<span class="status warn">● warn</span>',
                     '<span class="status bad">✕</span>')


def _experiment(cfg: Config, run_id: str) -> dict[str, Any]:
    p = cfg.runs_dir / run_id / "experiment.json"
    return read_json(p)["experiment"] if p.exists() else {}


def _top_fix(cfg: Config, run_id: str) -> dict[str, Any] | None:
    p = cfg.runs_dir / run_id / "incident.json"
    if not p.exists():
        return None
    fixes = sorted(read_json(p)["analysis"].get("fixes") or [], key=lambda f: f["priority"])
    return fixes[0] if fixes else None


def result_card(cfg: Config, a: dict[str, Any]) -> str:
    """One experiment in plain words: what was broken, what customers felt, how fast it healed, the top fix."""
    exp = _experiment(cfg, a["run_id"])
    f0 = (exp.get("faults") or [{}])[0]
    sev = a["severity"]
    facts = [f"<span><b>Customers affected:</b> {esc(', '.join(a['impacted_journeys']) or 'none')}</span>",
             f"<span><b>Back to normal in:</b> {esc(a.get('time_to_restore_s'))} s</span>"
             if a.get("time_to_restore_s") is not None else "<span><b>Back to normal:</b> not within the watch window</span>",
             f"<span><b>Severity:</b> {esc(sev['label'])} ({sev['score'] or 0:.0f}/100)</span>"]
    for c in a.get("checks", []):
        facts.append(f"<span><b>Saved carts lost:</b> {c['lost']} of {c['seeded']}</span>")
    bs = a.get("health_blind_spot") or {}
    if bs.get("silent_impact_s"):
        facts.append(f"<span><b>Unnoticed by health checks:</b> {bs['silent_impact_s']:.0f} s</span>")
    fix = _top_fix(cfg, a["run_id"])
    fix_html = (f"<div class='fix'><b>Top fix:</b> {esc(fix['pattern_name'])} — {esc(fix['change'])}</div>" if fix else "")
    return (f"<div class='card result sev-{esc(sev['label'])}'><h3>{esc(exp.get('title') or a['experiment_id'])}</h3>"
            f"<div class='small muted'>Injected: {esc(f0.get('fault_type'))} on <b>{esc(f0.get('target'))}</b>"
            f"{' · chosen by the AI test plan' if exp.get('candidate_id') else ''}</div>"
            f"<div class='facts'>{''.join(facts)}</div>{fix_html}"
            f"<p class='small'><a href='/runs/{esc(a['run_id'])}/'>Full result: where, how, why →</a></p></div>")


def experiments(cfg: Config) -> tuple[list[dict[str, Any]], list[dict[str, Any]]]:
    """(failure experiments, safety tests) from analysed runs, newest first."""
    runs = [a for a in _runs(cfg) if a["timeline"].get("inject")]
    safety_runs = [a for a in runs if a["experiment_id"].startswith("TRIPWIRE") or a["outcome"] == "kill_switch"]
    return [a for a in runs if a not in safety_runs], safety_runs


def home_html(cfg: Config, rows: list[dict]) -> str:
    failures, _ = experiments(cfg)
    latest: dict[str, dict[str, Any]] = {}
    for a in failures:  # newest run per experiment, most severe first
        latest.setdefault(a["experiment_id"], a)
    top = sorted(latest.values(), key=lambda a: -(a["severity"]["score"] or 0))[:2]
    results = ("".join(result_card(cfg, a) for a in top) if top else
               "<div class='card muted'>No failure tested yet. Follow the steps above; results appear here.</div>")
    done = sum(r["ok"] for r in rows)
    return f"""<h1>How resilient is the Astronomy Shop?</h1>
<p class="lead">An AI studies a live microservice shop, decides which failures are most worth testing, and a safety engine
injects them one at a time. We measure what customers actually experienced, explain where, how and why it failed,
and recommend fixes. Every failure is undone automatically and the undo is verified.</p>
<h2>Key findings</h2>{results}
<h2>Day-1 checkpoint · {done} of {len(rows)} complete</h2>{checklist_html(rows)}"""


def checklist_html(rows: list[dict]) -> str:
    items = "".join(
        f"<div class='check'><span class='s'>{TICK if r['ok'] else CROSS}</span><div><b>{esc(r['milestone'])}</b> "
        f"<span class='small muted'>— {esc(r['expected'])}</span></div>"
        f"<a class='small' href='{esc(r['link'])}'>{'view' if r['ok'] else 'open'} →</a>"
        f"<div class='e'>{OK_DONE if r['ok'] else OPEN} · {esc(r['evidence'])}</div></div>" for r in rows)
    return f"""<div class="card">{items}</div><p class="small muted">Each row is computed from recorded evidence and the
live container state; nothing is ticked by hand.</p>"""


# ---------------------------------------------------------------- map
def _layers(nodes: list[str], edges: list[dict[str, Any]]) -> list[list[str]]:
    """Right-aligned longest-path layering: column = (max height) - height(node), where height is the longest
    path to a sink. Every arrow then points strictly left-to-right and datastores/leaf services line up on the right.
    Startup-only edges are ignored for layout (they are not data flows)."""
    adj: dict[str, set[str]] = {}
    for e in edges:
        if e.get("flow") != "startup ordering only":
            adj.setdefault(e["from"], set()).add(e["to"])
    height: dict[str, int] = {}

    def h(n: str, seen: frozenset = frozenset()) -> int:
        if n in height:
            return height[n]
        if n in seen:  # cycle guard
            return 0
        height[n] = 1 + max((h(m, seen | {n}) for m in adj.get(n, ())), default=-1)
        return height[n]
    for n in nodes:
        h(n)
    top = max(height.values(), default=0)
    layers: dict[int, list[str]] = {}
    for n in nodes:
        layers.setdefault(top - height[n], []).append(n)
    return [sorted(layers[k]) for k in sorted(layers)]


def map_svg(m: dict[str, Any], overlay: dict[str, str] | None = None) -> str:
    """overlay: node -> 'fault' | 'down' | 'impacted' (live blast radius). Marked by border AND a text prefix."""
    overlay = overlay or {}
    edges = [e for e in m["edges"] if not e.get("telemetry")]
    names = sorted({e["from"] for e in edges} | {e["to"] for e in edges})
    kinds = {s["name"]: s.get("kind") for s in m.get("services", [])}
    layers = _layers(names, edges)
    bw, bh, gx, gy, pad = 130, 30, 70, 22, 16
    width = pad * 2 + len(layers) * bw + (len(layers) - 1) * gx
    height = pad * 2 + max(len(ly) for ly in layers) * (bh + gy)
    pos = {}
    for i, ly in enumerate(layers):
        x = pad + i * (bw + gx)
        off = (height - len(ly) * (bh + gy)) / 2
        for j, n in enumerate(ly):
            pos[n] = (x, off + j * (bh + gy))
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" style="max-width:{width * 1.3:.0f}px" role="img" aria-label="dependency map">',
             '<defs><marker id="arr" viewBox="0 0 10 10" refX="9" refY="5" markerWidth="6" markerHeight="6" orient="auto">'
             '<path d="M0,0 L10,5 L0,10 z" fill="var(--text-muted)"/></marker></defs>']
    for e in edges:
        (x1, y1), (x2, y2) = pos[e["from"]], pos[e["to"]]
        sx, sy, tx, ty = x1 + bw, y1 + bh / 2, x2, y2 + bh / 2
        if x2 <= x1:  # same or backward layer: route from the box bottom
            sx, sy, tx, ty = x1 + bw / 2, y1 + bh, x2 + bw / 2, y2
        colour = FLOW_GROUP.get(e.get("flow"), ("other", "var(--text-muted)"))[1]
        dash = "" if e["status"] == "verified-static+runtime" else ' stroke-dasharray="6 4"' if e["status"] == "verified-static" \
            else ' stroke-dasharray="2 3"'
        parts.append(f'<path d="M{sx:.0f},{sy:.0f} C{(sx + tx) / 2:.0f},{sy:.0f} {(sx + tx) / 2:.0f},{ty:.0f} {tx:.0f},{ty:.0f}" '
                     f'fill="none" stroke="{colour}" stroke-width="2"{dash} marker-end="url(#arr)">'
                     f'<title>{esc(e["from"])} → {esc(e["to"])}: {esc(e.get("flow"))}, {esc(e["status"])}</title></path>')
    prefix = {"fault": "✕ ", "down": "✕ ", "impacted": "! "}
    for n, (x, y) in pos.items():
        cls = "node datastore" if kinds.get(n) == "datastore" else "node"
        state = overlay.get(n)
        if state:
            cls += f" {state}"
        parts.append(f'<g class="{cls}"><rect x="{x}" y="{y}" width="{bw}" height="{bh}" rx="6"/>'
                     f'<text x="{x + bw / 2}" y="{y + bh / 2 + 4}" text-anchor="middle">{prefix.get(state, "")}{esc(n)}</text>'
                     f'<title>{esc(n)} ({esc(kinds.get(n))}){": " + state if state else ""}</title></g>')
    parts.append("</svg>")
    return "".join(parts)


GAP_LABELS = {
    "no_healthcheck": ("No health check", "the platform cannot tell when these fail"),
    "missing_depends_on": ("Datastore used without a declared dependency", "start-up order is not guaranteed"),
    "static_only": ("In the configuration, not seen in live traffic", "unused, or the trace is missing"),
    "runtime_only": ("Seen in live traffic, missing from the configuration", "an undocumented dependency"),
}


def map_html(cfg: Config) -> str:
    mp = cfg.artifacts_dir / "dependency-map.json"
    if not mp.exists():
        return page("System map", "<h1>System map</h1><p class='lead'>Not created yet. Run <code>chaosctl map</code>.</p>", "/map")
    m = read_json(mp)
    edges = [e for e in m["edges"] if not e.get("telemetry")]
    names = {e["from"] for e in edges} | {e["to"] for e in edges}
    confirmed = sum(1 for e in edges if e["status"] == "verified-static+runtime")
    legend = "".join(f'<span><span class="sw" style="border-color:{c}"></span>{esc(g)}</span>'
                     for g, c in dict(FLOW_GROUP.values()).items())
    legend += "<span>solid = confirmed by live traffic · dashed = configuration only</span><span>dashed box = database / store</span>"
    gaps = "".join(f"<li><b>{esc(GAP_LABELS.get(k, (k, ''))[0])}</b> <span class='muted'>({esc(GAP_LABELS.get(k, ('', ''))[1])})</span>"
                   f": {esc(', '.join(v))}</li>" for k, v in m["drift"].items() if v)
    rows = "".join(f"<tr><td>{esc(e['from'])}</td><td>{esc(e['to'])}</td><td>{esc(e.get('flow'))}</td><td>{esc(e['status'])}</td>"
                   f"<td class='small'>{esc('; '.join(x.get('ref') or x.get('type', '') for x in e['evidence']))}</td></tr>"
                   for e in edges)
    body = f"""<h1>System map</h1>
<p class="lead">{len(names)} services and {len(edges)} connections on the customer path; {confirmed} confirmed by both the
configuration and live traffic. Arrows point from caller to the service it depends on.</p>
<div class="legend">{legend}</div><div class="card wrap">{map_svg(m)}</div>
<h2>Weak spots found while mapping</h2><div class="card"><ul class="small">{gaps or '<li>none</li>'}</ul></div>
<details><summary>All connections with their evidence (table)</summary><div class="card wrap"><table><tr><th>From</th><th>To</th>
<th>Connection</th><th>Status</th><th>Evidence</th></tr>{rows}</table>
<p class="small muted">Generated {esc(m['generated'])} from the compose configuration{' and live Jaeger traces' if m['sources']['runtime'] else ''}.
Monitoring, feature-flag and operator-UI links are excluded from the customer path.</p></div></details>"""
    return page("System map", body, "/map")


# ---------------------------------------------------------------- baseline
def baseline_html(cfg: Config) -> str:
    bp = cfg.artifacts_dir / "baseline.json"
    if not bp.exists():
        return page("Normal behaviour", "<h1>Normal behaviour</h1><p class='lead'>Not measured yet. Run "
                    "<code>chaosctl baseline</code>.</p>", "/baseline")
    b = read_json(bp)
    q = b.get("quality") or {"pass": None, "checks": []}
    rows = "".join(f"<tr><td>{esc(j)}</td><td class='num'>{s['n']}</td><td class='num'>{pct(s['error_rate'])}</td>"
                   f"<td class='num'>{esc(s['p50_ms'])}</td><td class='num'>{esc(s['p95_ms'])}</td></tr>"
                   for j, s in b["journeys"].items())
    checks = "".join(f"<li>{TICK if c['ok'] else WARN if c['ok'] is None else CROSS} "
                     f"<b>{esc(c['check'])}</b> — {esc(c['detail'])}</li>" for c in q["checks"])
    hs, he = (b.get("health") or {}).get("start", {}), (b.get("health") or {}).get("end", {})
    hrows = "".join(f"<tr><td>{esc(n)}</td><td>{esc(hs.get(n, {}).get('status'))}</td><td>{esc(he.get(n, {}).get('status'))}</td></tr>"
                    for n in sorted(set(hs) | set(he)))
    run_dir = Path(b["run_dir"])
    charts = ""
    if (run_dir / "metrics.jsonl").exists():
        samples = [s for s in read_jsonl(run_dir / "metrics.jsonl")]
        if samples:
            t0, t1 = min(s["ts"] for s in samples), max(s["ts"] for s in samples)
            from .htmlreport import series
            warm = b.get("warmup_s") or 0
            charts = "".join(small_multiple(f"{j} — p95 latency ms", series(samples, j, t0, t1, 10.0), "p95",
                                            {"warm-up end": warm}, (0, warm) if warm else (None, None), None, " ms")
                             for j in b["journeys"])
    verdict = ('<span class="status ok">✓ Steady state accepted — safe to start testing</span>' if q["pass"] else
               '<span class="status bad">✕ Steady state rejected — fix the app before testing</span>')
    o = b["overall"]
    load = b.get("load") or {}
    body = f"""<h1>Normal behaviour</h1>
<p class="lead">Before breaking anything, we prove the shop is healthy: {b['duration_s']:.0f} s of test requests on every customer
journey while simulated shoppers ({esc(load.get('user_count'))} users) keep the shop busy.</p>
<div class="card">{verdict}</div>
<div class="kpis"><div class="card kpi"><div class="v">{pct(o['error_rate'])}</div><div class="l">error rate</div></div>
<div class="card kpi"><div class="v">{o['p95_ms']:.0f} ms</div><div class="l">slowest 5% of requests (p95)</div></div>
<div class="card kpi"><div class="v">{o['n']}</div><div class="l">test requests measured</div></div></div>
<h2>Per customer journey</h2><div class="card wrap"><table><tr><th>Journey</th><th>Requests</th><th>Error rate</th>
<th>Typical ms (p50)</th><th>Slowest 5% ms (p95)</th></tr>{rows}</table></div>
<details><summary>Quality checks behind the verdict</summary><div class="card"><ul class="small">{checks}</ul></div></details>
<details><summary>Latency over time per journey</summary><p class="small muted">10 s buckets; shaded = warm-up, excluded.</p>
<div class="grid">{charts}</div></details>
<details><summary>Container health at start and end</summary><div class="card wrap"><table><tr><th>Container</th><th>Start</th>
<th>End</th></tr>{hrows}</table></div></details>
<p class="small muted">Measured {esc(b['generated'])} after {b.get('warmup_s', 0):.0f} s warm-up · run <code>{esc(run_dir.name)}</code></p>"""
    return page("Normal behaviour", body, "/baseline")


# ---------------------------------------------------------------- live blast radius
def live_overlay(cfg: Config, active_targets: list[str], snapshot: dict[str, dict[str, str]],
                 failing_journeys: list[str]) -> dict[str, str]:
    overlay: dict[str, str] = {}
    for j in failing_journeys:
        for s in cfg.journeys[j].services if j in cfg.journeys else []:
            overlay[s] = "impacted"
    for n, s in snapshot.items():
        if s.get("state") != "running" or "unhealthy" in s.get("status", ""):
            overlay[n] = "down"
    for t in active_targets:
        overlay[t] = "fault"
    return overlay


def live_fragment(cfg: Config, overlay: dict[str, str], journeys: dict[str, dict[str, int]], run_id: str | None,
                  platform_green: bool | None) -> str:
    mp = cfg.artifacts_dir / "dependency-map.json"
    svg = map_svg(read_json(mp), overlay) if mp.exists() else "<p>Run <code>chaosctl map</code> first.</p>"
    tiles = "".join(
        f'<div class="card kpi"><div class="v">{v["ok"]}/{v["n"]}</div><div class="l">{esc(j)} '
        f'{TICK + " OK" if v["ok"] == v["n"] else CROSS + " down" if v["ok"] == 0 else WARN.replace("warn", "degraded")}</div></div>'
        for j, v in sorted(journeys.items()))
    plat = ("unknown" if platform_green is None else
            '<span class="status ok">✓ all containers healthy</span>' if platform_green else
            '<span class="status bad">✕ a container is down or unhealthy</span>')
    blind = ""
    if platform_green and any(v["ok"] < v["n"] for v in journeys.values()):
        blind = ('<div class="card"><span class="status warn">● Hidden outage</span> customers are failing while every '
                 'container still reports healthy</div>')
    head = (f"Test in progress (<code>{esc(run_id)}</code>)" if run_id else "No fault active right now; "
            "this view updates by itself.")
    return f"""<p>{head} · Platform health checks: {plat}</p>{blind}
{('<h3>Customer journeys, last 10 s (successful / total)</h3><div class="kpis">' + tiles + '</div>') if tiles else ''}
<div class="legend"><span>✕ red border = broken on purpose / container down</span><span>! orange border = on a failing customer journey</span></div>
<div class="card wrap">{svg}</div>"""


# ---------------------------------------------------------------- FMEA
def fmea_html(cfg: Config) -> str:
    fp = cfg.artifacts_dir / "fmea.json"
    if not fp.exists():
        return page("AI test plan", "<h1>AI test plan</h1><p class='lead'>Not created yet. Start an <a href='/mission'>Agent mission</a> "
                    "(or run <code>chaosctl plan</code>).</p>", "/fmea")
    f = read_json(fp)
    m = f["meta"]
    if m["method"] == "ai":
        who = f"Written by AI ({esc(m['ai_meta'].get('model_served'))}); every answer checked by code before use."
    elif m["method"] == "ai-cached":
        who = (f"Last AI answer that passed validation (the AI service was unreachable: "
               f"{esc(m['ai_meta'].get('cache_reason'))}).")
    else:
        who = (f"<span class='status warn'>● Rule-based fallback</span>, AI not used: "
               f"{esc(m.get('ai_unavailable') or m.get('ai_rejected') or 'reason not recorded')}.")
    rows_by_id = {r["candidate_id"]: r for r in f["rows"]}
    cards = []
    for s in sorted(f["selected"], key=lambda s: s["rank"]):
        r = rows_by_id.get(s["candidate_id"], {})
        cards.append(f"<div class='card result'><div class='small muted'>Test {esc(s['rank'])}</div>"
                     f"<h3>{esc(r.get('failure_mode'))} · {esc(r.get('component'))}</h3>"
                     f"<p class='small'><b>Why:</b> {esc(s['reason'])}</p><p class='small'><b>We expect:</b> {esc(s['hypothesis'])}</p>"
                     f"<p class='small muted'>Risk score {esc(r.get('rpn'))} = severity {esc(r.get('severity'))} × "
                     f"likelihood {esc(r.get('occurrence'))} × hard-to-detect {esc(r.get('detection'))}</p></div>")
    rows = []
    for r in f["rows"]:
        me = r.get("measured")
        if me:
            gap = me["rpn"] - r["rpn"]
            verdict = (f"{CROSS} worse than predicted" if gap > 50 else f"{WARN.replace('warn', 'better than predicted')}"
                       if gap < -50 else f"{TICK} as predicted")
            meas = (f"<td class='num'><b>{me['rpn']}</b></td><td style='white-space:nowrap'>{verdict} · "
                    f"<a class='small' href='/runs/{esc(me['run_id'])}/'>result</a></td>")
        else:
            meas = "<td class='num muted'>—</td><td class='small muted' style='white-space:nowrap'>not tested yet</td>"
        rows.append(f"<tr><td><b>{esc(r['component'])}</b></td><td>{esc(r['failure_mode'])}</td>"
                    f"<td class='small'>{esc(r['effect'])}</td><td class='num' title='S {r['severity']} × O {r['occurrence']} × "
                    f"D {r['detection']}'><b>{r['rpn']}</b></td>{meas}</tr>")
    body = f"""<h1>AI test plan</h1>
<p class="lead">Like an engineering FMEA, the AI lists how each part of the shop can fail and scores the risk:
how bad for customers (severity), how likely (occurrence) and how hard to notice (detection), each 1–10.
It then picks the tests that teach us the most. After each test, the measured result replaces the prediction.</p>
<p class="small">{who}</p>
<h2>{'What the AI chose to test, and why' if m['method'].startswith('ai') else 'Chosen tests, and why'}</h2><div class="grid">{''.join(cards)}</div>
<h2>Risk register: predicted vs measured</h2>
<div class="card wrap"><table><tr><th>Component</th><th>Failure</th><th>Expected effect</th><th>Predicted risk</th>
<th>Measured risk</th><th>Prediction</th></tr>{''.join(rows)}</table>
<p class="small muted">Risk score = S × O × D (max 1000). Measured severity comes from customer impact; measured detection from how long
health checks missed it. Likelihood cannot be measured by one test, so it stays predicted (method: docs/SCORING.md).
Generated {esc(f['generated'])}.</p></div>"""
    return page("AI test plan", body, "/fmea")


# ---------------------------------------------------------------- database error map + mission report (Results page)
def db_error_map(cfg: Config) -> list[dict[str, Any]]:
    """Latest measured run per (datastore, database error): % of customer requests that failed during the fault,
    overall and per journey, plus where the failing traces originated (Jaeger)."""
    latest: dict[tuple[str, str], dict[str, Any]] = {}
    retests: dict[str, dict[str, Any]] = {}  # original run id -> its re-test after a fix
    runs = [a for a in _runs(cfg) if a["timeline"].get("inject") and not a["experiment_id"].startswith("TRIPWIRE")]
    for a in runs:
        exp = _experiment(cfg, a["run_id"])
        if exp.get("verification_of"):
            retests.setdefault(exp["verification_of"], a)
    for a in runs:  # newest first
        exp = _experiment(cfg, a["run_id"])
        if exp.get("verification_of"):
            continue
        f0 = (exp.get("faults") or [{}])[0]
        if f0.get("target") not in cfg.datastores:
            continue
        label = exp.get("db_error") or f0.get("fault_type")
        key = (f0["target"], label)
        if key in latest:
            continue
        fs = [j["fault"] for j in a["journeys"].values() if j.get("fault") and j["fault"].get("n")]
        n = sum(f["n"] for f in fs)
        errs = sum(f["n"] * (f["error_rate"] or 0) for f in fs)
        base = [j["baseline"]["error_rate"] for j in a["journeys"].values() if j["baseline"].get("error_rate") is not None]
        ev_p = cfg.runs_dir / a["run_id"] / "evidence.json"
        votes = ((read_json(ev_p).get("traces") or {}).get("origin_service_votes") or []) if ev_p.exists() else []
        total_votes = sum(c for _, c in votes) or 0
        lost = next((c for c in a.get("checks", []) if c.get("lost") is not None), None)
        latest[key] = {
            "datastore": f0["target"], "db_error": label, "run_id": a["run_id"],
            "error_pct": round(100 * errs / n, 1) if n else None,
            "baseline_error_pct": round(100 * sum(base) / len(base), 1) if base else None,
            "journeys": {jn: {"error_pct": None if j["fault"]["error_rate"] is None else round(100 * j["fault"]["error_rate"], 1),
                              "impacted": j["impacted"]} for jn, j in a["journeys"].items()},
            "origin": [{"service": sv, "pct": round(100 * c / total_votes, 1)} for sv, c in votes] if total_votes else [],
            "time_to_restore_s": a.get("time_to_restore_s"),
            "silent_s": (a.get("health_blind_spot") or {}).get("silent_impact_s"),
            "data_lost": f"{lost['lost']}/{lost['seeded']}" if lost else None, "severity": a["severity"]["label"],
            "after_fix": _after_fix(retests.get(a["run_id"]))}
    return sorted(latest.values(), key=lambda r: (r["datastore"], -(r["error_pct"] or 0)))


def _after_fix(a: dict[str, Any] | None) -> dict[str, Any] | None:
    if not a:
        return None
    fs = [j["fault"] for j in a["journeys"].values() if j.get("fault") and j["fault"].get("n")]
    n = sum(f["n"] for f in fs)
    lost = next((c for c in a.get("checks", []) if c.get("lost") is not None), None)
    return {"run_id": a["run_id"], "error_pct": round(100 * sum(f["n"] * (f["error_rate"] or 0) for f in fs) / n, 1) if n else None,
            "data_lost": f"{lost['lost']}/{lost['seeded']}" if lost else None}


def db_map_html(cfg: Config) -> str:
    rows = db_error_map(cfg)
    if not rows:
        return ("<div class='card muted'>No database failure tested yet. On the Agent mission page choose "
                "<b>test set: database errors</b>.</div>")
    journeys = sorted(cfg.journeys)
    head = "".join(f"<th class='num'>{esc(j)}</th>" for j in journeys)
    trs = []
    for r in rows:
        cells = "".join(
            f"<td class='num'>{'<b>' if r['journeys'].get(j, {}).get('impacted') else ''}"
            f"{esc(r['journeys'].get(j, {}).get('error_pct'))}{'% ▲</b>' if r['journeys'].get(j, {}).get('impacted') else '%'}</td>"
            if r["journeys"].get(j, {}).get("error_pct") is not None else "<td class='num muted'>—</td>" for j in journeys)
        origin = ", ".join(f"{esc(o['service'])} {o['pct']}%" for o in r["origin"][:3]) or "—"
        trs.append(f"<tr><td><b>{esc(r['datastore'])}</b></td><td><a href='/runs/{esc(r['run_id'])}/'>{esc(r['db_error'])}</a></td>"
                   f"<td class='num'><b>{esc(r['error_pct'])}%</b><br><span class='small muted'>normal "
                   f"{esc(r['baseline_error_pct'])}%</span></td>{cells}<td class='small'>{origin}</td>"
                   f"<td class='num'>{esc(r['time_to_restore_s'])}</td><td class='num'>{esc(r['data_lost'] or '—')}</td>"
                   + (f"<td class='small'><a href='/runs/{esc(r['after_fix']['run_id'])}/'>{esc(r['after_fix']['error_pct'])}% "
                      f"failed, data lost {esc(r['after_fix']['data_lost'] or '—')}</a></td>" if r["after_fix"] else
                      "<td class='small muted'>no fix applied</td>") + "</tr>")
    return (f"<div class='card wrap'><table><tr><th>Database</th><th>Database error injected</th><th class='num'>Customer "
            f"requests failed</th>{head}<th>Where failing traces started (Jaeger)</th><th class='num'>Restore s</th>"
            f"<th class='num'>Data lost</th><th>After the approved fix (re-test)</th></tr>{''.join(trs)}</table><p class='small muted'>% = share of test requests that "
            f"failed while the database error was active (all journeys, then per journey). ▲ bold = journey hurt (errors or slowness outside its normal band, so 0.0% ▲ means slow, not failing). "
            f"Latest run per database error; click a row for the full evidence.</p></div>")


def _latest_mission(cfg: Config) -> dict[str, Any] | None:
    d = cfg.artifacts_dir / "missions"
    reps = sorted(d.glob("*/report.json")) if d.exists() else []
    return read_json(reps[-1]) if reps else None


def mission_report_html(cfg: Config) -> str:
    rep = _latest_mission(cfg)
    if not rep:
        return "<div class='card muted'>No mission report yet. Start one on the <a href='/mission'>Agent mission</a> page.</div>"
    fixes = {f["before_run"]: f for f in rep.get("fixes", [])}
    after = {f.get("after_run"): f for f in rep.get("fixes", [])}
    cards = []
    for r in rep["results"]:
        if r.get("run_id") in after:  # a re-test is reported inside the card of the test it proves
            continue
        an = r.get("analysis") or {}
        method = ("AI" if an.get("method", "").startswith("ai") else "rule-based fallback (AI not used)") if an else "not analysed"
        found = (f"<p class='small'><b>Where:</b> {esc(an.get('where'))} · <b>path:</b> "
                 f"{esc(' → '.join(an.get('path') or []))}<br><b>How:</b> {esc(an.get('how'))}<br><b>Why:</b> "
                 f"{esc(an.get('why'))}</p>" if an else "")
        did = [f"Rolled the failure back: {'✓ undo verified against the pre-test snapshot' if r.get('rollback_verified') else 'n/a'}"]
        fx = fixes.get(r.get("run_id"))
        if fx:
            did.append(f"Applied <b>{esc(fx['title'])}</b> after human approval, then re-ran the same test: "
                       f"<b>{esc(fx.get('verdict'))}</b> (severity {esc(fx.get('severity_before'))} → "
                       f"{esc(fx.get('severity_after'))}){' and kept it' if fx.get('verdict') == 'fixed' else '; reverted'}"
                       f" · <a href='/compare/{esc(fx.get('comparison'))}'>before / after</a>")
        elif r.get("remediation"):
            did.append(esc(r["remediation"]))
        recs = "".join(
            f"<tr><td>{esc(x.get('priority'))}</td><td><b>{esc(x.get('pattern_name'))}</b></td><td class='small'>{esc(x.get('change'))}"
            f"</td><td class='small'>{esc(x.get('expected_effect'))}</td><td class='small'>"
            + "<br>".join(f"{esc(e['id'])}: {esc(e['title'])}" for e in x.get("evidence", []))
            + "</td><td class='small'>" + " ".join(f"<a href='{esc(u)}'>[{i + 1}]</a>" for i, u in enumerate(x.get("references") or []))
            + "</td></tr>" for x in r.get("recommendations") or [])
        dec = r.get("decision")
        dec_html = (f"<p class='small'><b>Human decision:</b> {esc(dec['fix'])} → <b>{esc(dec['decision'])}</b> by "
                    f"{esc(dec['by'])} at {esc(dec['decided'])}</p>" if dec else "")
        cards.append(
            f"<div class='card result sev-{esc(r.get('severity'))}'><div class='small muted'>P{esc(r.get('priority'))} · "
            f"{esc(r.get('db_error') or r.get('source'))}</div><h3>{esc(r['title'])}</h3>"
            f"<p class='small'><b>Customer impact:</b> {esc(', '.join(r.get('impacted') or []) or 'none')} · severity "
            f"{esc(r.get('severity'))} · restore {esc(r.get('time_to_restore_s'))} s"
            f"{' · carts lost ' + esc(r['carts_lost']) if r.get('carts_lost') else ''}</p>"
            f"<h4>What the analysis found <span class='small muted'>({method})</span></h4>{found}"
            f"<h4>What the system did</h4><ul class='small'>{''.join(f'<li>{d}</li>' for d in did)}</ul>"
            f"<h4>What we recommend you do (with evidence)</h4>"
            + (f"<div class='wrap'><table><tr><th>Prio</th><th>Fix</th><th>Change</th><th>Expected effect</th><th>Evidence</th>"
               f"<th>Refs</th></tr>{recs}</table></div>" if recs else "<p class='small muted'>no recommendation (no customer impact)</p>")
            + f"{dec_html}<p class='small'><a href='/runs/{esc(r.get('run_id'))}/'>Full evidence →</a></p></div>")
    decs = "".join(f"<tr><td class='small'>{esc(d['decided'])}</td><td>{esc(d['test'])}</td><td>{esc(d['fix'])}</td>"
                   f"<td><b>{esc(d['decision'])}</b></td><td class='small'>{esc(d['by'])}</td></tr>" for d in rep.get("decisions", []))
    return (f"<p class='small muted'>Mission <code>{esc(rep['mission_id'])}</code> · finished {esc(rep['finished'])}"
            f"{' · stopped: ' + esc(rep['stop_reason']) if rep.get('stop_reason') else ''}</p>"
            f"<h3>Human-in-the-loop decisions</h3>"
            + (f"<div class='card wrap'><table><tr><th>When</th><th>Test</th><th>Fix proposed</th><th>Decision</th><th>By</th></tr>"
               f"{decs}</table></div>" if decs else "<div class='card small muted'>No fix needed a decision in this mission.</div>")
            + "".join(cards))
