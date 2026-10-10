"""Day-1 checkpoint pages: milestone checklist, dependency map (SVG), baseline report.

Charts follow the dataviz rules used in htmlreport.py: max 3 categorical hues for a graph (all-pairs
validated slots 1-3), meaning never by colour alone (line style + legend + table), status = icon + label.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any, Callable

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
nav a{margin-right:14px}
"""


def page(title: str, body: str) -> str:
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)}</title><style>{CSS}{EXTRA_CSS}</style></head><body><main>
<nav class="small"><a href="/">Day-1 checklist</a><a href="/live">Live blast radius</a><a href="/map">Dependency map</a><a href="/baseline">Baseline</a><a href="/fmea">AI FMEA</a><a href="/runs">Runs</a></nav>
{body}</main></body></html>"""


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
                   if fm["method"] == "ai" else "chosen by rule-based FMEA (AI not available); ")
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


def checklist_html(rows: list[dict]) -> str:
    trs = "".join(
        f"<tr><td>{esc(r['n'])}</td><td><b>{esc(r['milestone'])}</b><br><span class='small muted'>{esc(r['expected'])}</span></td>"
        f"<td>{OK_DONE if r['ok'] else OPEN}</td>"
        f"<td class='small'>{esc(r['evidence'])}</td><td><a href='{esc(r['link'])}'>view</a></td></tr>" for r in rows)
    return f"""<h1>Milestone 1 — Day-1 checkpoint</h1><p class="muted">Every row is computed from evidence files and live
container state; nothing is entered by hand.</p><div class="card wrap"><table><tr><th>#</th><th>Milestone</th><th>Status</th>
<th>Evidence</th><th></th></tr>{trs}</table></div>"""


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
    parts = [f'<svg viewBox="0 0 {width} {height}" width="100%" role="img" aria-label="dependency map">',
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


def map_html(cfg: Config) -> str:
    mp = cfg.artifacts_dir / "dependency-map.json"
    if not mp.exists():
        return page("Dependency map", "<h1>Dependency map</h1><p>Not yet: run <code>chaosctl map</code>.</p>")
    m = read_json(mp)
    edges = [e for e in m["edges"] if not e.get("telemetry")]
    legend = "".join(f'<span><span class="sw" style="border-color:{c}"></span>{esc(g)}</span>'
                     for g, c in dict(FLOW_GROUP.values()).items())
    legend += ('<span><span class="sw" style="border-color:var(--text-muted)"></span>other / startup only</span>'
               '<span>—— config + live traces</span><span>- - - config only</span><span>· · · traces / manual only</span>'
               '<span>dashed box = datastore</span>')
    rows = "".join(f"<tr><td>{esc(e['from'])}</td><td>{esc(e['to'])}</td><td>{esc(e.get('flow'))}</td><td>{esc(e['status'])}</td>"
                   f"<td class='small'>{esc('; '.join(x.get('ref') or x.get('type', '') for x in e['evidence']))}</td></tr>"
                   for e in edges)
    d = m["drift"]
    gaps = "".join(f"<li><b>{esc(k.replace('_', ' '))}:</b> {esc(', '.join(v) or 'none')}</li>" for k, v in d.items())
    body = f"""<h1>Application map — services, dependencies, data flows</h1>
<p class="muted small">Generated {esc(m['generated'])} from the compose configuration{' and live Jaeger traces' if m['sources']['runtime'] else ''}.
Hover an arrow for details.</p><div class="legend">{legend}</div><div class="card wrap">{map_svg(m)}</div>
<h2>Gaps found while mapping</h2><div class="card"><ul class="small">{gaps}</ul></div>
<h2>All dependencies (table view)</h2><div class="card wrap"><table><tr><th>From</th><th>To</th><th>Data flow</th><th>Status</th>
<th>Evidence</th></tr>{rows}</table></div>"""
    return page("Dependency map", body)


# ---------------------------------------------------------------- baseline
def baseline_html(cfg: Config) -> str:
    bp = cfg.artifacts_dir / "baseline.json"
    if not bp.exists():
        return page("Baseline", "<h1>Baseline</h1><p>Not yet: run <code>chaosctl baseline</code>.</p>")
    b = read_json(bp)
    q = b.get("quality") or {"pass": None, "checks": []}
    rows = "".join(f"<tr><td>{esc(j)}</td><td class='num'>{s['n']}</td><td class='num'>{pct(s['error_rate'])}</td>"
                   f"<td class='num'>{esc(s['p50_ms'])}</td><td class='num'>{esc(s['p95_ms'])}</td><td class='num'>{esc(s['p99_ms'])}</td>"
                   f"<td class='num'>{esc(round(s['throughput_rps'], 2) if s['throughput_rps'] else None)}</td></tr>"
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
    verdict = ('<span class="status ok">✓ STEADY STATE ACCEPTED</span>' if q["pass"] else
               '<span class="status bad">✕ STEADY STATE REJECTED</span>')
    o = b["overall"]
    body = f"""<h1>Monitoring baseline — normal behaviour under steady load</h1>
<p class="muted small">{esc(b['generated'])} · {b['duration_s']:.0f} s measured after {b.get('warmup_s', 0):.0f} s warm-up ·
load: Locust {esc((b.get('load') or {}).get('state'))}, {esc((b.get('load') or {}).get('user_count'))} users · run <code>{esc(run_dir.name)}</code></p>
<div class="card">{verdict}<ul class="small">{checks}</ul></div>
<div class="kpis"><div class="card kpi"><div class="v">{o['n']}</div><div class="l">probe samples</div></div>
<div class="card kpi"><div class="v">{pct(o['error_rate'])}</div><div class="l">error rate</div></div>
<div class="card kpi"><div class="v">{o['p95_ms']:.0f} ms</div><div class="l">p95 latency (all journeys)</div></div></div>
<h2>Per customer journey</h2><div class="card wrap"><table><tr><th>Journey</th><th>Samples</th><th>Error rate</th><th>p50 ms</th>
<th>p95 ms</th><th>p99 ms</th><th>probe req/s</th></tr>{rows}</table></div>
<h2>Latency over time (10 s buckets; shaded = warm-up, excluded)</h2><div class="grid">{charts}</div>
<h2>Container health (start → end)</h2><div class="card wrap"><table><tr><th>Container</th><th>Start</th><th>End</th></tr>{hrows}</table></div>"""
    return page("Baseline", body)




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
        f'{TICK + " OK" if v["ok"] == v["n"] else CROSS + " DOWN" if v["ok"] == 0 else WARN.replace("warn", "degraded")}</div></div>'
        for j, v in sorted(journeys.items()))
    plat = ("n/a" if platform_green is None else
            '<span class="status ok">✓ all containers healthy</span>' if platform_green else
            '<span class="status bad">✕ a container is down/unhealthy</span>')
    blind = ""
    if platform_green and any(v["ok"] < v["n"] for v in journeys.values()):
        blind = '<div class="card"><span class="status warn">● HEALTH-CHECK BLIND SPOT</span> customers are failing while every container reports healthy</div>'
    return f"""<p class="small">{'Run <code>' + esc(run_id) + '</code>' if run_id else 'No run in progress'} · platform view: {plat}</p>
{blind}<div class="kpis">{tiles}</div>
<div class="legend"><span>✕ red border = fault target / container down</span><span>! orange border = on a failing customer journey</span></div>
<div class="card wrap">{svg}</div>"""


# ---------------------------------------------------------------- FMEA
def fmea_html(cfg: Config) -> str:
    fp = cfg.artifacts_dir / "fmea.json"
    if not fp.exists():
        return page("AI FMEA", "<h1>AI FMEA</h1><p>Not yet: run <code>chaosctl plan</code> or <code>chaosctl autopilot</code>.</p>")
    f = read_json(fp)
    m = f["meta"]
    who = (f"AI · {esc(m['ai_meta'].get('model_served'))}" if m["method"] == "ai"
           else f"rule-based fallback ({esc(m.get('ai_unavailable') or m.get('ai_rejected'))})")
    rows = []
    for r in f["rows"]:
        me = r.get("measured")
        if me:
            gap = me["rpn"] - r["rpn"]
            verdict = (f"{CROSS} underestimated" if gap > 50 else f"{WARN} overestimated" if gap < -50 else f"{TICK} confirmed")
            meas = f"<td class='num'>{me['severity']}</td><td class='num'>{me['detection']}</td><td class='num'><b>{me['rpn']}</b></td>" \
                   f"<td>{verdict}<br><a class='small' href='/runs/{esc(me['run_id'])}/'>run</a></td>"
        else:
            meas = "<td class='num'>—</td><td class='num'>—</td><td class='num'>—</td><td class='small muted'>not injected</td>"
        rows.append(f"<tr><td><b>{esc(r['component'])}</b></td><td>{esc(r['failure_mode'])}</td><td class='small'>{esc(r['effect'])}"
                    f"<br><span class='muted'>{esc(r['rationale'])}</span></td><td class='num'>{r['severity']}</td>"
                    f"<td class='num'>{r['occurrence']}</td><td class='num'>{r['detection']}</td><td class='num'><b>{r['rpn']}</b></td>{meas}</tr>")
    sel = "".join(f"<li><b>{esc(s['candidate_id'])}</b> — {esc(s['reason'])}<br><span class='small muted'>Hypothesis: "
                  f"{esc(s['hypothesis'])}</span></li>" for s in f["selected"])
    body = f"""<h1>FMEA — AI-generated, validated by chaos experiments</h1>
<p class="muted small">{who} · generated {esc(f['generated'])}. Predicted Severity, Occurrence and Detection (1 best … 10 worst),
RPN = S×O×D. After each injection, Severity is re-rated from measured customer impact and Detection from the measured
health-check blind spot (method: docs/SCORING.md). Occurrence can't be measured by one experiment.</p>
<div class="card"><h3>What the AI chose to break first, and why</h3><ol class="small">{sel}</ol></div>
<div class="card wrap"><table><tr><th>Component</th><th>Failure mode</th><th>Predicted effect / rationale</th><th>S</th><th>O</th><th>D</th>
<th>RPN</th><th>Meas. S</th><th>Meas. D</th><th>Meas. RPN</th><th>Prediction</th></tr>{''.join(rows)}</table></div>"""
    return page("AI FMEA", body)
