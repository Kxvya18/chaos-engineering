"""Self-contained HTML pages for a run (incident report) and for a before/after comparison.

Charts follow the dataviz rules: one single-series small multiple per journey (no legend needed,
title names it), recessive grid, 2px lines, fault window shaded, event markers labelled in text,
status shown with icon + label (never colour alone), and a table view of the same numbers.
"""

from __future__ import annotations

import html
import json
from pathlib import Path
from typing import Any

from . import metrics
from .util import read_json, read_jsonl

CSS = """
:root{--surface-0:#f4f3ef;--surface-1:#fcfcfb;--border:#e2e0d9;--grid:#ecebe6;--text-primary:#0b0b0b;
--text-secondary:#52514e;--text-muted:#7a7974;--series-1:#2a78d6;--shade:#2a78d61a;--good:#0ca30c;--warning:#fab219;
--serious:#ec835a;--critical:#d03b3b;color-scheme:light}
@media (prefers-color-scheme:dark){:root:where(:not([data-theme="light"])){--surface-0:#121211;--surface-1:#1a1a19;
--border:#2e2d2a;--grid:#262624;--text-primary:#f0efec;--text-secondary:#c3c2b7;--text-muted:#8f8e86;--series-1:#3987e5;
--shade:#3987e526;color-scheme:dark}}
:root[data-theme="dark"]{--surface-0:#121211;--surface-1:#1a1a19;--border:#2e2d2a;--grid:#262624;--text-primary:#f0efec;
--text-secondary:#c3c2b7;--text-muted:#8f8e86;--series-1:#3987e5;--shade:#3987e526;color-scheme:dark}
*{box-sizing:border-box}body{margin:0;background:var(--surface-0);color:var(--text-primary);
font:15px/1.5 system-ui,-apple-system,Segoe UI,Roboto,sans-serif}
main{max-width:1180px;margin:0 auto;padding:24px 16px 64px}h1{font-size:24px;margin:0 0 4px}h2{font-size:18px;margin:28px 0 10px}
h3{font-size:15px;margin:14px 0 6px}.muted{color:var(--text-secondary)}.small{font-size:13px}
.card{background:var(--surface-1);border:1px solid var(--border);border-radius:10px;padding:14px 16px;margin:10px 0}
.grid{display:grid;grid-template-columns:repeat(auto-fill,minmax(260px,1fr));gap:12px}
.kpis{display:grid;grid-template-columns:repeat(auto-fill,minmax(170px,1fr));gap:10px}
.kpi .v{font-size:24px;font-weight:650}.kpi .l{font-size:12px;color:var(--text-secondary)}
table{width:100%;border-collapse:collapse;font-size:14px}th,td{text-align:left;padding:6px 8px;border-bottom:1px solid var(--border);vertical-align:top}
th{color:var(--text-secondary);font-weight:600}td.num{font-variant-numeric:tabular-nums;text-align:right}
.chip{display:inline-block;padding:1px 8px;border-radius:999px;border:1px solid var(--border);margin:2px 3px 2px 0;font-size:13px}
.chip.failed{border-color:var(--critical);color:var(--critical);font-weight:650}
.status{font-weight:650}.status.bad{color:var(--critical)}.status.ok{color:var(--good)}.status.warn{color:var(--serious)}
pre{white-space:pre-wrap;word-break:break-word;background:var(--surface-0);border:1px solid var(--border);border-radius:8px;
padding:10px;font-size:12px;max-height:280px;overflow:auto}a{color:var(--series-1)}
svg text{fill:var(--text-muted);font-size:10px}.wrap{overflow-x:auto}
"""


def esc(v: Any) -> str:
    return html.escape("n/a" if v is None else str(v))


def pct(v: Any) -> str:
    return "n/a" if v is None else f"{100 * v:.1f}%"


def _status(ok: bool | None, good: str, bad: str) -> str:
    if ok is None:
        return '<span class="status warn">● n/a</span>'
    return f'<span class="status ok">✓ {esc(good)}</span>' if ok else f'<span class="status bad">✕ {esc(bad)}</span>'


def series(samples: list[dict[str, Any]], journey: str, t0: float, t1: float, step: float = 2.0) -> list[dict[str, Any]]:
    out = []
    for t, w in metrics.windows([s for s in samples if s["journey"] == journey], t0, t1, step):
        st = metrics.stats(w)
        out.append({"t": round(t - t0, 1), "ok_pct": None if not st["n"] else round(100 * (1 - st["error_rate"]), 1),
                    "p95": st["p95_ms"], "n": st["n"]})
    return out


def small_multiple(title: str, pts: list[dict[str, Any]], key: str, marks: dict[str, float | None],
                   window: tuple[float | None, float | None], ymax: float | None = None, unit: str = "") -> str:
    w, h, pl, pr, pt, pb = 260, 120, 34, 8, 8, 18
    vals = [p[key] for p in pts if p[key] is not None]
    top = ymax if ymax is not None else max([1.0, *vals]) * 1.15
    tmax = max([p["t"] for p in pts] + [1])

    def x(t: float) -> float:
        return pl + (w - pl - pr) * t / tmax

    def y(v: float) -> float:
        return pt + (h - pt - pb) * (1 - v / top)
    parts = [f'<svg viewBox="0 0 {w} {h}" width="100%" role="img" aria-label="{esc(title)}">']
    for frac in (0, 0.5, 1):
        gy = y(top * frac)
        parts.append(f'<line x1="{pl}" x2="{w - pr}" y1="{gy:.1f}" y2="{gy:.1f}" stroke="var(--grid)"/>'
                     f'<text x="{pl - 4}" y="{gy + 3:.1f}" text-anchor="end">{top * frac:.0f}</text>')
    if window[0] is not None:
        x0, x1 = x(window[0]), x(window[1] if window[1] is not None else tmax)
        parts.append(f'<rect x="{x0:.1f}" y="{pt}" width="{max(1, x1 - x0):.1f}" height="{h - pt - pb}" fill="var(--shade)"/>')
    for label, t in marks.items():
        if t is not None and 0 <= t <= tmax:
            parts.append(f'<line x1="{x(t):.1f}" x2="{x(t):.1f}" y1="{pt}" y2="{h - pb}" stroke="var(--text-muted)" '
                         f'stroke-dasharray="3 3"><title>{esc(label)} at +{t:.0f}s</title></line>')
    d, pen = [], "M"
    for p in pts:
        if p[key] is None:
            pen = "M"
            continue
        d.append(f"{pen}{x(p['t']):.1f},{y(p[key]):.1f}")
        pen = "L"
    parts.append(f'<path d="{" ".join(d)}" fill="none" stroke="var(--series-1)" stroke-width="2" stroke-linejoin="round"/>')
    for p in pts:
        if p[key] is not None:
            parts.append(f'<circle cx="{x(p["t"]):.1f}" cy="{y(p[key]):.1f}" r="6" fill="transparent">'
                         f'<title>+{p["t"]:.0f}s: {p[key]}{unit} (n={p["n"]})</title></circle>')
    parts.append(f'<text x="{pl}" y="{h - 4}">0s</text><text x="{w - pr}" y="{h - 4}" text-anchor="end">{tmax:.0f}s</text>')
    parts.append("</svg>")
    return f'<div class="card"><h3>{esc(title)}</h3>{"".join(parts)}</div>'


def _blind_html(a: dict[str, Any]) -> str:
    bs = a.get("health_blind_spot")
    if not bs or not bs.get("customer_impact_s"):
        return ""
    return (f"<div class='card'><b>Health-check blind spot:</b> customers were impacted for <b>{bs['customer_impact_s']:.0f} s</b>; "
            f"for <b>{bs['silent_impact_s']:.0f} s</b> of that, every container still reported healthy. Customers first failed at "
            f"+{esc(bs['customers_first_failed_at_s'])} s; "
            f"{('the platform first showed a problem at +' + esc(bs['platform_first_not_green_at_s']) + ' s') if bs['platform_first_not_green_at_s'] is not None else 'the platform never showed a problem'}."
            f"<br><span class='small muted'>{esc(bs['method'])}</span></div>")


def render_run(cfg, run_dir: Path) -> str:
    a = read_json(run_dir / "analysis.json")
    exp = read_json(run_dir / "experiment.json")["experiment"]
    inc = read_json(run_dir / "incident.json") if (run_dir / "incident.json").exists() else None
    ev = read_json(run_dir / "evidence.json") if (run_dir / "evidence.json").exists() else None
    samples = read_jsonl(run_dir / "metrics.jsonl")
    tl = a["timeline"]
    t0 = tl.get("pre_start") or (min(s["ts"] for s in samples) if samples else 0)
    t1 = tl.get("end") or (max(s["ts"] for s in samples) if samples else t0 + 1)
    rel = {k: (round(v - t0, 1) if v else None) for k, v in tl.items()}
    abort_t = round(a["abort"]["ts"] - t0, 1) if a.get("abort") else None
    marks = {"inject": rel.get("inject"), "rollback": rel.get("rollback_complete"), "abort": abort_t}
    window = (rel.get("inject"), rel.get("rollback_complete"))
    targets = {f["target"] for f in exp["faults"]}
    f0 = exp["faults"][0]

    k = [("Outcome", a["outcome"]), ("Time to impact", f"{a['time_to_impact_s']} s" if a["time_to_impact_s"] is not None else "n/a"),
         ("Time to restore", f"{a.get('time_to_restore_s')} s" if a.get("time_to_restore_s") is not None else "not restored"),
         ("Self-healed", "yes" if any(a.get("self_healed", {}).values()) else "no"),
         ("Severity", f"{a['severity']['label']} ({a['severity']['score']})")]
    for c in a.get("checks", []):
        k.append(("Carts lost", f"{c['lost']} of {c['seeded']}"))
    bs = a.get("health_blind_spot")
    if bs and bs["customer_impact_s"]:
        k.append(("Impact invisible to health checks", f"{bs['silent_impact_s']:.0f} of {bs['customer_impact_s']:.0f} s"))
    kpis = "".join(f'<div class="card kpi"><div class="v">{esc(v)}</div><div class="l">{esc(lbl)}</div></div>' for lbl, v in k)

    rows = []
    for name, j in a["journeys"].items():
        path = cfg.journeys[name].services if name in cfg.journeys else []
        chips = "".join(f'<span class="chip{" failed" if s in targets else ""}">{"✕ " if s in targets else ""}{esc(s)}</span>'
                        for s in path)
        rows.append(f"<tr><td>{esc(name)}</td><td>{_status(not j['impacted'], 'within baseline', 'impacted')}</td>"
                    f"<td>{chips}</td><td class='num'>{pct(j['baseline']['error_rate'])}</td>"
                    f"<td class='num'>{pct(j['fault']['error_rate'])}</td><td class='num'>{esc(j['baseline']['p95_ms'])}</td>"
                    f"<td class='num'>{esc(j['fault']['p95_ms'])}</td></tr>")
    charts_ok = "".join(small_multiple(f"{n} — success rate %", series(samples, n, t0, t1), "ok_pct", marks, window, 100, "%")
                        for n in a["journeys"])
    charts_lat = "".join(small_multiple(f"{n} — p95 latency ms", series(samples, n, t0, t1), "p95", marks, window, None, " ms")
                         for n in a["journeys"])
    tl_rows = "".join(f"<tr><td class='num'>{esc(r['t_s'])}</td><td>{esc(r['target'])}</td><td>{esc(r['status'])}</td>"
                      f"<td>{esc(r['health'])}</td><td class='num'>{esc(r['restart_count'])}</td><td class='num'>{esc(r['exit_code'])}</td></tr>"
                      for r in a.get("target_timeline", []))

    ai_html = "<p class='muted'>No incident analysis yet — run <code>chaosctl explain</code>.</p>"
    if inc:
        r, m = inc["analysis"], inc["meta"]
        method = (f"AI · {esc(m['ai_meta'].get('model_served'))} · evidence &amp; pattern ids validated by code"
                  + (f" · <span class='status warn'>⚠ unverified numbers: {esc(m['unverified_numbers'])}</span>"
                     if m.get("unverified_numbers") else " · all numbers found in evidence")
                  if m["method"] == "ai" else f"Rule-based fallback ({esc(m.get('ai_unavailable') or m.get('ai_rejected'))})")
        factors = "".join(f"<li>{esc(c['factor'])} <span class='muted small'>({esc(', '.join(c['evidence_ids']))})</span></li>"
                          for c in r["why"]["contributing_factors"])
        fixes = "".join(
            f"<tr><td>{esc(fx['priority'])}</td><td><b>{esc(fx['pattern_name'])}</b><br><span class='muted small'>{esc(fx['pattern_id'])}</span></td>"
            f"<td>{esc(fx['change'])}</td><td>{esc(fx['expected_effect'])}</td><td class='small'>{esc(', '.join(fx['evidence_ids']))}</td>"
            f"<td class='small'>{' '.join(f'<a href={esc(u)!r}>[{i + 1}]</a>' for i, u in enumerate(fx['references']))}</td></tr>"
            for fx in sorted(r["fixes"], key=lambda f: f["priority"]))
        verdict_ok = r["verdict"] == "hypothesis_confirmed"
        ai_html = f"""<p class="small muted">{method}</p>
<div class="card"><b>Verdict:</b> {_status(verdict_ok, 'hypothesis confirmed', r['verdict'].replace('_', ' '))}<p>{esc(r['summary'])}</p></div>
<div class="grid"><div class="card"><h3>Where</h3><p><b>{esc(r['where']['failed_component'])}</b></p>
<p class="small">{' → '.join(esc(p) for p in r['where']['propagation_path'])}</p></div>
<div class="card"><h3>How</h3><p class="small">{esc(r['how'])}</p></div>
<div class="card"><h3>Why</h3><p class="small"><b>{esc(r['why']['root_cause'])}</b></p><ul class="small">{factors}</ul></div>
<div class="card"><h3>Customer impact &amp; detection gap</h3><p class="small">{esc(r['customer_impact'])}</p>
<p class="small muted">{esc(r['detection_gap'])}</p></div></div>
<h3>Recommended fixes (industry-standard patterns)</h3><div class="card wrap"><table><tr><th>Prio</th><th>Pattern</th><th>Change</th>
<th>Expected effect</th><th>Evidence</th><th>Refs</th></tr>{fixes}</table></div>
<div class="card small"><b>Verification plan:</b> {esc(r['verification_plan'])}</div>"""

    ev_html = ""
    if ev:
        tr = ev.get("traces") or {}
        ops = "".join(f"<tr><td>{esc(o)}</td><td class='num'>{c}</td></tr>" for o, c in tr.get("error_spans_by_operation", []))
        votes = ", ".join(f"{esc(s)} ({c})" for s, c in tr.get("origin_service_votes", [])) or "none"
        logs = "".join(f"<h3>{esc(lg['container'])} — {esc(lg.get('error_lines'))} error-like lines</h3>"
                       f"<pre>{esc(chr(10).join((lg.get('first') or []) + (lg.get('last') or [])) or lg.get('error') or '(none)')}</pre>"
                       for lg in ev.get("logs", []))
        ev_html = f"""<h2>Evidence collected after the run</h2>
<div class="card"><h3>Jaeger error traces ({esc(tr.get('error_traces'))} traces)</h3>
<p class="small">Where failures originated (deepest errored span per trace): <b>{votes}</b></p>
<div class="wrap"><table><tr><th>service :: operation</th><th>error spans</th></tr>{ops}</table></div>
<pre>{esc(json.dumps(tr.get('top_messages'), indent=1))}</pre></div>
<div class="card"><h3>Compose facts</h3><pre>{esc(json.dumps(ev.get('compose_facts'), indent=1))}</pre></div>
<div class="card">{logs}</div>"""

    files = " · ".join(f'<a href="{n}">{n}</a>' for n in ("experiment.json", "metrics.jsonl", "events.jsonl", "analysis.json",
                                                         "evidence.json", "incident.json") if (run_dir / n).exists())
    rb_rows = "".join(f"<tr><td>{esc(r['fault_id'])}</td><td>{_status(r['verified'], 'verified', 'NOT verified')}</td>"
                      f"<td class='num'>{esc(r['attempts'])}</td><td class='small'>"
                      + esc("; ".join(f"{c['check']}: expected {c['expected']}, got {c['actual']}" for c in (r.get('checks') or [])))
                      + "</td></tr>" for r in a.get("rollback", []))
    rollback_html = (f"<h2>Rollback verification</h2><div class='card wrap'><table><tr><th>Fault</th><th>Result</th><th>Attempts</th>"
                     f"<th>State checks against the pre-injection snapshot</th></tr>{rb_rows}</table></div>") if rb_rows else ""
    from .ui import page as ui_page
    return ui_page(f"Run {a['run_id']}", f"""<p class="small"><a href="/runs">← all runs</a></p>
<h1>{esc(exp.get('title') or a['experiment_id'])}</h1>
<p class="muted">{esc(f0['fault_type'])} on <b>{esc(f0['target'])}</b> {esc(f0.get('params') or '')} for {esc(f0['duration_s'])} s · run <code>{esc(a['run_id'])}</code></p>
<div class="card small"><b>Pre-registered hypothesis:</b> {esc(exp.get('hypothesis'))}<br><b>Why this experiment:</b> {esc(exp.get('why') or exp.get('reason'))}</div>
<div class="kpis">{kpis}</div>
{_blind_html(a)}
<h2>AI incident analysis</h2>{ai_html}
<h2>Blast radius: journeys and the services they pass through</h2>
<div class="card wrap"><table><tr><th>Journey</th><th>Status</th><th>Service path (✕ = fault target)</th><th>Base err</th><th>Fault err</th><th>Base p95 ms</th><th>Fault p95 ms</th></tr>{''.join(rows)}</table></div>
<h2>Customer journeys over time</h2><p class="small muted">Shaded = fault active (inject → rollback). Dashed lines = inject, rollback, abort (hover for time). 2 s buckets from raw probe samples.</p>
<div class="grid">{charts_ok}</div><div class="grid">{charts_lat}</div>
<h2>Target container timeline (state changes)</h2>
<div class="card wrap"><table><tr><th>t (s from inject)</th><th>container</th><th>status</th><th>health</th><th>restarts</th><th>exit code</th></tr>{tl_rows}</table></div>
{ev_html}
{rollback_html}
<h2>Raw evidence files</h2><p class="small">{files}</p>""", CSS, "/runs")


def render_compare(c: dict[str, Any]) -> str:
    keys = [k for k in c["before"] if k != "run_id"]
    rows = "".join(f"<tr><td>{esc(k.replace('_', ' '))}</td><td>{esc(c['before'][k])}</td><td>{esc(c['after'][k])}</td></tr>" for k in keys)
    good = c["verdict"] == "improved"
    from .ui import page as ui_page
    return ui_page("Before / after", f"""<p class="small"><a href="/runs">← all runs</a></p>
<h1>Before / after the fix — {esc(c['experiment_id'])}</h1>
<div class="card">{_status(good, 'improved', c['verdict'])} · severity score change {esc(c['severity_delta'])}</div>
<div class="card wrap"><table><tr><th>Metric</th><th><a href="/runs/{esc(c['before']['run_id'])}/">before</a></th>
<th><a href="/runs/{esc(c['after']['run_id'])}/">after</a></th></tr>{rows}</table></div>""", CSS, "/runs")
