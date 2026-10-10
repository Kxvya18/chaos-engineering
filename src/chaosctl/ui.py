"""Shared app shell for every dashboard page: header with numbered steps, live safety pill, "next step" link, footer.
One place for layout so every page looks and navigates the same. Wording is plain language for reviewers;
technical detail lives in collapsible sections on each page."""

from __future__ import annotations

import html
from typing import Any

STEPS = [  # (path, tab label) in the order a reviewer should walk through them
    ("/", "Overview"),
    ("/map", "System map"),
    ("/baseline", "Normal behaviour"),
    ("/fmea", "AI test plan"),
    ("/live", "Live test"),
    ("/runs", "Results"),
]

SHELL_CSS = """
header.app{position:sticky;top:0;z-index:5;background:var(--surface-1);border-bottom:1px solid var(--border)}
header.app .in{max-width:1180px;margin:0 auto;padding:10px 16px;display:flex;align-items:center;gap:14px}
.brand{font-weight:750;font-size:16px;letter-spacing:-.01em;white-space:nowrap}
.brand small{display:block;font-weight:500;font-size:12px;color:var(--text-secondary);letter-spacing:0}
nav.tabs{display:flex;gap:2px;flex:1;min-width:0;overflow-x:auto;scrollbar-width:none}
nav.tabs a{padding:7px 9px;white-space:nowrap;border-radius:8px;color:var(--text-secondary);text-decoration:none;font-size:14px;font-weight:550}
nav.tabs a .i{display:inline-block;min-width:18px;height:18px;line-height:18px;border-radius:999px;text-align:center;font-size:11px;
margin-right:6px;background:var(--surface-0);border:1px solid var(--border)}
nav.tabs a:hover{background:var(--surface-0);color:var(--text-primary)}
nav.tabs a.on{color:var(--text-primary);background:var(--surface-0);box-shadow:inset 0 -2px 0 var(--series-1)}
nav.tabs a.on .i{background:var(--series-1);border-color:var(--series-1);color:#fff}
.pill{font-size:13px;font-weight:650;padding:5px 10px;border-radius:999px;border:1px solid var(--border);white-space:nowrap}
.pill.ok{color:var(--good)}.pill.warn{color:var(--serious);border-color:var(--serious)}
.pill.bad{color:#fff;background:var(--critical);border-color:var(--critical)}
.lead{font-size:16px;color:var(--text-secondary);max-width:860px;margin:4px 0 18px}
.btn{display:inline-block;padding:8px 14px;border-radius:8px;border:1px solid var(--border);text-decoration:none;font-weight:600;
font-size:14px;color:var(--text-primary);background:var(--surface-1)}.btn.primary{background:var(--series-1);border-color:var(--series-1);color:#fff}
.next{display:flex;justify-content:flex-end;margin-top:28px}
.result{border-left:4px solid var(--series-1)}.result h3{margin-top:0;font-size:17px}.result .facts{display:flex;flex-wrap:wrap;gap:6px 18px;
font-size:14px;margin:6px 0}.result .fix{font-size:14px;margin-top:8px;padding-top:8px;border-top:1px solid var(--border)}
.result.sev-critical,.result.sev-high{border-left-color:var(--critical)}.result.sev-medium{border-left-color:var(--serious)}
.result.sev-low{border-left-color:var(--good)}
.check{display:grid;grid-template-columns:28px 1fr auto;gap:2px 10px;align-items:start;padding:10px 0;border-bottom:1px solid var(--border)}
.check:last-child{border-bottom:0}.check .s{font-size:18px;line-height:1.2}.check .e{grid-column:2;font-size:13px;color:var(--text-secondary)}
details{margin:12px 0}details>summary{cursor:pointer;font-weight:600;color:var(--text-secondary);padding:6px 0}
details[open]>summary{color:var(--text-primary)}
.two{display:grid;grid-template-columns:minmax(0,2fr) minmax(260px,1fr);gap:14px;align-items:start}
@media (max-width:900px){.two{grid-template-columns:1fr}}
footer.app{max-width:1180px;margin:0 auto;padding:18px 16px 40px;color:var(--text-muted);font-size:12px;border-top:1px solid var(--border)}
@media (max-width:1100px){.brand small{display:none}nav.tabs a .i{display:none}}
@media (max-width:640px){header.app .in{flex-wrap:wrap}nav.tabs{order:3;flex-basis:100%}}
"""

PILL_JS = """<script>
async function pill(){try{const s=await (await fetch('/api/status')).json();const p=document.getElementById('safety');
const n=s.active_faults.length;
if(s.kill_switch){p.className='pill bad';p.textContent='■ Stopped (kill switch on)'}
else if(n){p.className='pill warn';p.textContent='● Test running · '+n+' fault'+(n===1?'':'s')+' active'}
else{p.className='pill ok';p.textContent='● Safe · no faults active'}}catch(e){}}
pill();setInterval(pill,3000);</script>"""


def esc(v: Any) -> str:
    return html.escape("n/a" if v is None else str(v))


def params(p: Any) -> str:
    """Fault parameters in plain form: {'mode': 'crash'} -> 'mode crash'."""
    if not p:
        return ""
    return ", ".join(f"{k} {v}" for k, v in p.items()) if isinstance(p, dict) else str(p)


def next_link(active: str | None) -> str:
    idx = next((i for i, s in enumerate(STEPS) if s[0] == active), None)
    if idx is None or idx == len(STEPS) - 1:
        return ""
    path, label = STEPS[idx + 1]
    return f'<div class="next"><a class="btn primary" href="{path}">Next: {esc(label)} →</a></div>'


def page(title: str, body: str, css: str, active: str | None = None, sandbox: str = "opentelemetry-demo") -> str:
    tabs = "".join(f'<a href="{p}" class="{"on" if p == active else ""}">'
                   f'{f"<span class=i>{i}</span>" if i else ""}{esc(lbl)}</a>' for i, (p, lbl) in enumerate(STEPS))
    return f"""<!doctype html><html lang="en"><head><meta charset="utf-8"><meta name="viewport" content="width=device-width,initial-scale=1">
<title>{esc(title)} · Resilience Lab</title><style>{css}{SHELL_CSS}</style></head><body>
<header class="app"><div class="in"><div class="brand">◆ Resilience Lab<small>AI-driven chaos engineering</small></div>
<nav class="tabs">{tabs}</nav><span id="safety" class="pill">● Safety …</span></div></header>
<main>{body}{next_link(active)}</main>
<footer class="app">Runs only inside the local Docker sandbox <code>{esc(sandbox)}</code>. Every fault is allow-listed, time-limited,
watched by an independent watchdog and rolled back with verification. Every number on these pages comes from recorded measurements.</footer>
{PILL_JS}</body></html>"""
