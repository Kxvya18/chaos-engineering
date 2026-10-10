"""Small web dashboard (stdlib only): live run status, run reports, comparisons, kill switch.

Read-only except POST /api/kill. Binds to 127.0.0.1 unless --host is given (the container uses 0.0.0.0
with the port published on 127.0.0.1 only).
"""

from __future__ import annotations

import json
import mimetypes
import re
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable

from . import faults, htmlreport, metrics, pages, safety
from .config import Config
from .registry import Registry
from .util import now, read_json, read_jsonl

SAFE = re.compile(r"^[A-Za-z0-9._-]+$")


def _runs(cfg: Config) -> list[dict[str, Any]]:
    out = []
    for d in sorted(cfg.runs_dir.glob("*"), reverse=True) if cfg.runs_dir.exists() else []:
        if not d.is_dir():
            continue
        row = {"run_id": d.name, "done": (d / "DONE").exists(), "baseline": d.name.endswith("-baseline")}
        if (d / "analysis.json").exists():
            a = read_json(d / "analysis.json")
            row.update(experiment=a["experiment_id"], outcome=a["outcome"], severity=a["severity"]["label"],
                       score=a["severity"]["score"], restore=a.get("time_to_restore_s"),
                       lost=next((f"{c['lost']}/{c['seeded']}" for c in a.get("checks", [])), None))
        out.append(row)
    return out


def status(cfg: Config) -> dict[str, Any]:
    active = next((r for r in _runs(cfg) if not r["done"] and not r["baseline"]), None)
    live = None
    if active:
        d = cfg.runs_dir / active["run_id"]
        t = now()
        recent = metrics.between(read_jsonl(d / "metrics.jsonl"), t - 10, t + 1)
        live = {"run_id": active["run_id"],
                "journeys": {j: {"ok": sum(s["ok"] for s in ss), "n": len(ss)}
                             for j, ss in sorted(metrics.by_journey(recent).items())},
                "events": [{k: e.get(k) for k in ("time", "source", "action", "reason")}
                           for e in read_jsonl(d / "events.jsonl") if e["action"] != "target_state"][-8:]}
    return {"kill_switch": safety.kill_engaged(cfg), "active_faults": Registry(cfg.state_dir).list(), "live": live}


def index_html(cfg: Config) -> str:
    esc = htmlreport.esc
    rows = "".join(
        f"<tr><td><a href='/runs/{esc(r['run_id'])}/'>{esc(r['run_id'])}</a></td><td>{esc(r.get('experiment', ''))}</td>"
        f"<td>{esc(r.get('outcome'))}</td><td>{esc(r.get('severity'))} {esc(r.get('score'))}</td>"
        f"<td class='num'>{esc(r.get('restore'))}</td><td>{esc(r.get('lost'))}</td></tr>"
        for r in _runs(cfg) if not r["baseline"])
    comps = "".join(f"<li><a href='/compare/{esc(p.stem)}'>{esc(p.stem)}</a></li>"
                    for p in sorted(cfg.artifacts_dir.glob("comparison-*.json")))
    body = f"""<h1>Experiment runs</h1>{live_panel()}
<h2>Runs</h2><div class="card wrap"><table><tr><th>Run</th><th>Experiment</th><th>Outcome</th><th>Severity</th><th>Restore (s)</th><th>Carts lost</th></tr>{rows}</table></div>
<h2>Before / after comparisons (optional)</h2><ul>{comps or '<li class="muted">none yet</li>'}</ul>"""
    return pages.page("Runs", body)


LIVE_JS = """<script>
async function tick(){try{const s=await (await fetch('/api/status')).json();
let h=`<b>Kill switch:</b> ${s.kill_switch?'<span class="status bad">✕ ENGAGED</span>':'<span class="status ok">✓ off</span>'} · <b>Active faults:</b> ${s.active_faults.length}`;
if(s.live){h+=`<h3>Live run ${s.live.run_id}</h3><div class="kpis">`+Object.entries(s.live.journeys).map(([j,v])=>{
const st=v.ok===v.n?'<span class="status ok">✓ OK</span>':v.ok===0?'<span class="status bad">✕ DOWN</span>':'<span class="status warn">● DEGRADED</span>';
return `<div class="card kpi"><div class="v">${v.ok}/${v.n}</div><div class="l">${j} ${st}</div></div>`}).join('')+`</div>`+
`<pre>${s.live.events.map(e=>`${e.time} [${e.source}] ${e.action} ${e.reason||''}`).join('\\n')}</pre>`}
else h+=' · <span class="muted">no run in progress</span>';
document.getElementById('live').innerHTML=h}catch(e){}}
tick();setInterval(tick,2000);
</script>"""


def live_panel() -> str:
    return """<h2>Live</h2><div class="card" id="live">loading live status…</div>
<form method="post" action="/api/kill" onsubmit="return confirm('Engage the kill switch and roll back ALL faults?')">
<button style="background:var(--critical);color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:650">■ KILL SWITCH</button>
<span class="small muted">rolls back every active fault and blocks new runs (CLI: <code>chaosctl kill --reset</code> to re-enable)</span></form>""" + LIVE_JS


def project_health(cfg: Config, ctx: faults.Ctx) -> dict[str, Any]:
    from .cli import project_health as ph
    return ph(cfg, ctx)


def storefront(cfg: Config, ctx: faults.Ctx) -> tuple[bool, str]:
    try:
        status, _, ms = ctx.http.request("GET", cfg.sandbox["base_url"] + "/")
        return status < 400, f"HTTP {status} in {ms:.0f} ms"
    except Exception as exc:  # unreachable shop is a FAIL row, not a crash
        return False, str(exc)


def make_handler(cfg: Config, ctx_factory: Callable[[], faults.Ctx]):
    class Handler(BaseHTTPRequestHandler):
        def log_message(self, fmt: str, *args: Any) -> None:
            pass

        def _send(self, code: int, body: str | bytes, ctype: str = "text/html; charset=utf-8") -> None:
            data = body.encode() if isinstance(body, str) else body
            self.send_response(code)
            self.send_header("Content-Type", ctype)
            self.send_header("Content-Length", str(len(data)))
            self.send_header("Cache-Control", "no-store")
            self.end_headers()
            self.wfile.write(data)

        def do_GET(self) -> None:  # noqa: N802
            parts = [p for p in self.path.split("?")[0].split("/") if p]
            try:
                if not parts:
                    rows = pages.milestone1(cfg, lambda: project_health(cfg, ctx_factory()), lambda: storefront(cfg, ctx_factory()))
                    return self._send(200, pages.page("Day-1 checkpoint", pages.checklist_html(rows) + live_panel()))
                if parts == ["runs"]:
                    return self._send(200, index_html(cfg))
                if parts == ["map"]:
                    return self._send(200, pages.map_html(cfg))
                if parts == ["baseline"]:
                    return self._send(200, pages.baseline_html(cfg))
                if parts == ["api", "status"]:
                    return self._send(200, json.dumps(status(cfg), default=str), "application/json")
                if parts[0] == "runs" and len(parts) in (2, 3) and all(SAFE.match(p) for p in parts[1:]):
                    d = cfg.runs_dir / parts[1]
                    if len(parts) == 2:
                        if not (d / "analysis.json").exists():
                            return self._send(404, "no analysis for this run yet")
                        return self._send(200, htmlreport.render_run(cfg, d))
                    f = d / parts[2]
                    if f.is_file() and f.parent == d:
                        return self._send(200, f.read_bytes(), mimetypes.guess_type(f.name)[0] or "text/plain")
                if parts[0] == "compare" and len(parts) == 2 and SAFE.match(parts[1]):
                    f = cfg.artifacts_dir / f"{parts[1]}.json"
                    if f.is_file():
                        return self._send(200, htmlreport.render_compare(read_json(f)))
                return self._send(404, "not found")
            except Exception as exc:  # show errors instead of dropping the connection
                return self._send(500, f"error: {type(exc).__name__}: {exc}", "text/plain")

        def do_POST(self) -> None:  # noqa: N802
            if self.path.rstrip("/") == "/api/kill":
                safety.engage_kill(cfg, "dashboard kill switch")
                rep = faults.reconcile(ctx_factory(), Registry(cfg.state_dir))
                return self._send(200, f"<p>KILL SWITCH ENGAGED. Rolled back: {htmlreport.esc(rep)}</p><a href='/'>back</a>")
            return self._send(404, "not found")

    return Handler


def serve(cfg: Config, ctx_factory: Callable[[], faults.Ctx], host: str = "127.0.0.1", port: int = 8090) -> None:
    srv = ThreadingHTTPServer((host, port), make_handler(cfg, ctx_factory))
    print(f"chaosctl dashboard on http://{host}:{port}/  (Ctrl-C to stop)")
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    finally:
        srv.server_close()

