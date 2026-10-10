"""Static, self-contained snapshot of the dashboard (for sharing/deploying results without the live sandbox).

Every page is rendered by the same code as the live dashboard; links are rewritten to relative files, live-only
parts (status pill, live map, kill switch) are replaced by a snapshot label. Only real run data is exported.
"""

from __future__ import annotations

import re
import shutil
from pathlib import Path

from . import dashboard, htmlreport, pages
from .config import Config
from .util import iso, read_json

RUN_FILES = ("experiment.json", "events.jsonl", "metrics.jsonl", "analysis.json", "evidence.json", "incident.json",
             "incident.md", "findings.md", "ABORT.json", "outcome.json")


def _static(html: str, stamp: str) -> str:
    html = html.replace('<span id="safety" class="pill">● Safety …</span>',
                        f'<span class="pill">Snapshot · {stamp}</span>')
    html = re.sub(r"<script>\s*async function (pill|tick|lm)\(\).*?</script>", "", html, flags=re.DOTALL)
    html = re.sub(r'<form method="post" action="/api/kill".*?</form>', "", html, flags=re.DOTALL)
    html = re.sub(r'<form class="start".*?</form>', "", html, flags=re.DOTALL)
    html = re.sub(r"""href=(["'])/runs/([A-Za-z0-9._-]+)/\1""", r'href="run-\2.html"', html)
    html = re.sub(r"""href=(["'])/compare/([A-Za-z0-9._-]+)\1""", r'href="\2.html"', html)
    pages_map = {"/map": "map", "/baseline": "baseline", "/fmea": "fmea", "/mission": "mission",
                 "/runs": "runs", "/": "index"}
    return re.sub(r"""href=(["'])(/[a-z]*)\1""",
                  lambda m: f'href="{pages_map[m.group(2)]}.html"' if m.group(2) in pages_map else m.group(0), html)


def mission_static(cfg: Config) -> str:
    """Snapshot of the last mission: agents, results and fixes (the live page needs the running dashboard)."""
    from . import agents
    m = agents.current_mission(cfg) or {}
    esc = htmlreport.esc
    cards = "".join(f"<div class='card'><b>{esc(a['name'])}</b> · {esc(a['status'])}<br><span class='small muted'>"
                    f"{esc(a['task'])}</span></div>" for a in (m.get("agents") or {}).values())
    rows = "".join(f"<tr><td>{esc(r.get('priority'))}</td><td>{esc(r.get('title'))}</td>"
                   f"<td>{esc(', '.join(r.get('impacted') or []) or 'none')}</td><td>{esc(r.get('severity'))}</td>"
                   f"<td>{'✓ verified' if r.get('rollback_verified') else esc(r.get('rollback_verified'))}</td>"
                   f"<td class='small'>{esc(r.get('remediation') or r.get('reason'))}</td></tr>" for r in m.get("results") or [])
    body = (f"<h1>Agent mission</h1><p class='lead'>Mission {esc(m.get('mission_id'))} · {esc(m.get('status'))}</p>"
            f"<div class='grid'>{cards}</div><h2>Test results</h2><div class='card wrap'><table><tr><th>P</th><th>Test</th>"
            f"<th>Customers affected</th><th>Severity</th><th>Undo</th><th>Remediation</th></tr>{rows}</table></div>")
    return pages.page("Agent mission", body if m else "<h1>Agent mission</h1><p>No mission recorded.</p>", "/mission")


def export(cfg: Config, out: Path) -> list[Path]:
    stamp = iso()[:16].replace("T", " ") + " UTC"
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    written = []

    def write(name: str, html: str) -> None:
        p = out / name
        p.write_text(_static(html, stamp))
        written.append(p)

    bp = cfg.artifacts_dir / "baseline.json"
    base = read_json(bp) if bp.exists() else {}
    end_health = (base.get("health") or {}).get("end") or {}
    rows = pages.milestone1(cfg, lambda: end_health,
                            lambda: (bool((base.get("quality") or {}).get("pass")), "from the recorded baseline run"))
    write("index.html", pages.page("Overview", pages.home_html(cfg, rows), "/"))
    write("map.html", pages.map_html(cfg))
    write("baseline.html", pages.baseline_html(cfg))
    write("fmea.html", pages.fmea_html(cfg))
    write("mission.html", mission_static(cfg))
    write("runs.html", dashboard.index_html(cfg).replace(dashboard.live_panel(), ""))
    for d in sorted(cfg.runs_dir.glob("*")) if cfg.runs_dir.exists() else []:
        if not (d / "analysis.json").exists():
            continue
        html = htmlreport.render_run(cfg, d)
        for f in RUN_FILES:  # raw evidence travels with the page
            if (d / f).exists():
                (out / "evidence" / d.name).mkdir(parents=True, exist_ok=True)
                shutil.copy2(d / f, out / "evidence" / d.name / f)
                html = html.replace(f'href="{f}"', f'href="evidence/{d.name}/{f}"')
        write(f"run-{d.name}.html", html)
    for c in sorted(cfg.artifacts_dir.glob("comparison-*.json")):
        write(f"{c.stem}.html", htmlreport.render_compare(read_json(c)))
    return written
