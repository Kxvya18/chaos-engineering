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
    html = re.sub(r"""href=(["'])/runs/([A-Za-z0-9._-]+)/\1""", r'href="run-\2.html"', html)
    html = re.sub(r"""href=(["'])/compare/([A-Za-z0-9._-]+)\1""", r'href="\2.html"', html)
    pages_map = {"/map": "map", "/baseline": "baseline", "/fmea": "fmea", "/live": "live", "/runs": "runs", "/": "index"}
    return re.sub(r"""href=(["'])(/[a-z]*)\1""",
                  lambda m: f'href="{pages_map[m.group(2)]}.html"' if m.group(2) in pages_map else m.group(0), html)


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
    write("live.html", pages.page("Live test", "<h1>Live test</h1><p class='lead'>The live view needs the running "
                                  "dashboard (<code>chaosctl dashboard</code>). In this snapshot, open a test from "
                                  "<a href='/runs'>Results</a> to see how the failure spread and healed.</p>", "/live"))
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
