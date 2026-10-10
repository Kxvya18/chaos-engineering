"""Shared app shell for every dashboard page: header + tabs (active state), live safety pill, guided demo bar
(step N of 6 with Prev/Next) and footer. One place for layout so every page looks and navigates the same."""

from __future__ import annotations

import html
from typing import Any

STEPS = [  # (path, tab label, demo-bar title, one-line purpose)
    ("/", "Overview", "Day-1 checkpoint", "Every milestone, ticked from evidence"),
    ("/map", "Map", "Application map", "Services, dependencies and data flows"),
    ("/baseline", "Baseline", "Steady state", "What normal looks like under load"),
    ("/fmea", "AI FMEA", "AI decides what to break", "Failure modes rated and selected by AI"),
    ("/live", "Live", "Live blast radius", "Watch the injected failure spread and heal"),
    ("/runs", "Runs", "Results & incidents", "Where, how and why — with verified rollback"),
]

SHELL_CSS = """
header.app{position:sticky;top:0;z-index:5;background:var(--surface-1);border-bottom:1px solid var(--border)}
header.app .in{max-width:1180px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:18px;flex-wrap:wrap}
.brand{font-weight:750;font-size:17px;letter-spacing:-.01em}.brand span{color:var(--text-secondary);font-weight:500;font-size:13px;margin-left:8px}
nav.tabs{display:flex;gap:2px;flex-wrap:wrap;flex:1}
nav.tabs a{padding:7px 11px;border-radius:8px;color:var(--text-secondary);text-decoration:none;font-size:14px;font-weight:550}
nav.tabs a:hover{background:var(--surface-0);color:var(--text-primary)}
nav.tabs a.on{color:var(--text-primary);background:var(--surface-0);box-shadow:inset 0 -2px 0 var(--series-1)}
.pill{font-size:13px;font-weight:650;padding:5px 10px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.pill.ok{color:var(--good)}.pill.bad{color:#fff;background:var(--critical);border-color:var(--critical)}
.demobar{display:flex;align-items:center;gap:12px;flex-wrap:wrap;margin:14px 0 6px;padding:10px 14px;border:1px solid var(--border);
border-radius:10px;background:var(--surface-1)}.demobar .t{flex:1;min-width:220px}.demobar .n{font-size:12px;color:var(--text-secondary);
text-transform:uppercase;letter-spacing:.06em}.btn{display:inline-block;padding:7px 12px;border-radius:8px;border:1px solid var(--border);
text-decoration:none;font-weight:600;font-size:14px;color:var(--text-primary);background:var(--surface-0)}.btn.primary{background:var(--series-1);
border-color:var(--series-1);color:#fff}.btn[aria-disabled=true]{opacity:.4;pointer-events:none}
.hero{padding:22px 0 6px}.hero h1{font-size:28px;letter-spacing:-.02em}.hero p{font-size:16px;color:var(--text-secondary);max-width:820px}
.flow{display:grid;grid-template-columns:repeat(auto-fit,minmax(150px,1fr));gap:10px}.flow .card{margin:0}.flow b{display:block;margin-bottom:2px}
.flow .k{font-size:12px;color:var(--text-secondary)}
footer.app{max-width:1180px;margin:0 auto;padding:18px 16px 40px;color:var(--text-muted);font-size:12px;border-top:1px solid var(--border)}
@media (max-width:640px){.brand span{display:none}nav.tabs a{padding:6px 8px}}
"""

PILL_JS = """<script>
async function pill(){try{const s=await (await fetch('/api/status')).json();const p=document.getElementById('safety');
if(s.kill_switch){p.className='pill bad';p.textContent='■ KILL SWITCH ON'}
else{p.className='pill ok';p.textContent='● Safety armed · '+s.active_faults.length+' active fault'+(s.active_faults.length===1?'':'s')}}catch(e){}}
pill();setInterval(pill,3000);</script>"""


def esc(v: Any) -> str:
    return html.escape("n/a" if v is None else str(v))


def demo_bar(active: str | None) -> str:
    idx = next((i for i, s in enumerate(STEPS) if s[0] == active), None)
    if idx is None:
        return ""
    path, _, title, purpose = STEPS[idx]
    prev = STEPS[idx - 1][0] if idx > 0 else None
    nxt = STEPS[idx + 1][0] if idx < len(STEPS) - 1 else None
    return (f'<div class="demobar"><div class="t"><div class="n">Demo step {idx + 1} of {len(STEPS)}</div>'
            f'<b>{esc(title)}</b> · <span class="muted">{esc(purpose)}</span></div>'
            f'<a class="btn" href="{prev or "#"}" aria-disabled="{str(prev is None).lower()}">← Prev</a>'
            f'<a class="btn primary" href="{nxt or "#"}" aria-disabled="{str(nxt is None).lower()}">Next →</a></div>')


def page(title: str, body: str, css: str, active: str | None = None, sandbox: str = "opentelemetry-demo") -> str:
    tabs = "".join(f'<a href="{p}" class="{"on" if p == active else ""}">{esc(lbl)}</a>' for p, lbl, _, _ in STEPS)
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · chaosctl</title><style>{css}{SHELL_CSS}</style></head><body>
<header class="app"><div class="in"><div class="brand">◆ chaosctl<span>AI chaos engineering</span></div>
<nav class="tabs">{tabs}</nav><span id="safety" class="pill">● Safety …</span></div></header>
<main>{demo_bar(active)}{body}</main>
<footer class="app">Sandbox: local Docker, compose project <code>{esc(sandbox)}</code> · every fault is allowlisted, bounded in time,
watched by an independent watchdog and rolled back with verification · numbers are computed from raw probe samples.</footer>
{PILL_JS}</body></html>"""
