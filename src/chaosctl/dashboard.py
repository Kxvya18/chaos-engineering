"""Small web dashboard (stdlib only): live run status, run reports, comparisons, kill switch.

Read-only except POST /api/kill. Binds to 127.0.0.1 unless --host is given (the container uses 0.0.0.0
with the port published on 127.0.0.1 only).
"""

from __future__ import annotations

import json
import mimetypes
import re
import subprocess
import sys
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import Any, Callable
from urllib.parse import parse_qs

from . import faults, health, htmlreport, metrics, pages, safety
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
    failures, safety_runs = pages.experiments(cfg)
    cards = "".join(pages.result_card(cfg, a) for a in failures) or "<div class='card muted'>No failure tested yet.</div>"
    safety_rows = []
    for a in safety_runs:
        ab = a.get("abort") or {}
        what = ("Automatic stop: damage spread beyond the allowed area" if a["experiment_id"].startswith("TRIPWIRE")
                else "Manual kill switch pressed during a test")
        how = (f"stopped by the watchdog ({esc(ab.get('reason'))})" if ab.get("by") == "watchdog" else
               "all faults rolled back" if a["outcome"] == "kill_switch" else f"outcome: {esc(a['outcome'])}")
        rb = a.get("rollback") or []
        verified = rb and all(r.get("verified") for r in rb)
        safety_rows.append(f"<tr><td>{esc(what)}</td><td>{pages.TICK + ' ' if ab or a['outcome'] == 'kill_switch' else ''}{how}</td>"
                           f"<td>{'✓ verified' if verified else 'n/a'}</td><td><a href='/runs/{esc(a['run_id'])}/'>details</a></td></tr>")
    safety_html = (f"<div class='card wrap'><table><tr><th>Safety test</th><th>What happened</th><th>Undo verified</th><th></th></tr>"
                   f"{''.join(safety_rows)}</table></div>") if safety_rows else "<div class='card muted'>No safety test run yet.</div>"
    comps = "".join(f"<li><a href='/compare/{esc(p.stem)}'>{esc(p.stem.replace('comparison-', ''))}</a></li>"
                    for p in sorted(cfg.artifacts_dir.glob("comparison-*.json")))
    body = f"""<h1>Results</h1>
<p class="lead">Every failure the system injected, what customers felt, and the fix. Open a result for the full
where / how / why analysis and the raw evidence.</p>
<h2>Failure tests</h2>{cards}
<h2>Safety controls, proven</h2><p class="small muted">The system must stop itself when damage spreads, and one button must undo everything.</p>
{safety_html}
{'<h2>Before / after a fix</h2><ul>' + comps + '</ul>' if comps else ''}"""
    return pages.page("Results", body, "/runs")


LIVE_JS = """<script>
async function tick(){try{const s=await (await fetch('/api/status')).json();
let h=s.live?s.live.events.map(e=>`${e.time} [${e.source}] ${e.action} ${e.reason||''}`).join('\\n'):'';
document.getElementById('events').textContent=h||'no test running';}catch(e){}}
tick();setInterval(tick,2000);
</script>"""


def live_panel() -> str:
    return """<div class="card"><h3>Emergency stop</h3><p class="small">Undoes every active fault immediately and blocks new tests.</p>
<form method="post" action="/api/kill" onsubmit="return confirm('Stop everything and undo all faults?')">
<button style="background:var(--critical);color:#fff;border:0;border-radius:8px;padding:10px 16px;font-weight:650;width:100%">■ KILL SWITCH</button></form>
<p class="small muted">Re-enable later with <code>chaosctl kill --reset</code>.</p></div>
<details><summary>Engine event log</summary><pre id="events">loading…</pre></details>""" + LIVE_JS


MISSION_JS = """<script>
const esc=s=>String(s??'').replace(/[&<>"]/g,c=>({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;'}[c]));
const ICON={idle:'○',working:'◐',waiting:'⏸',done:'✓',stopped:'■',failed:'✕'};
function approval(m){const a=m.approval,el=document.getElementById('approval');
if(!a||a.status!=='pending'){el.innerHTML=a?`<div class="card small">Last fix decision: <b>${esc(a.fix.title)}</b> → <b>${esc(a.status)}</b></div>`:'';return}
el.innerHTML=`<div class="card result sev-high"><h3>Human approval needed: apply a fix?</h3>
<p class="small"><b>Test:</b> ${esc(a.test)}<br><b>Impact:</b> ${esc(a.impact)}</p>
<p><b>Proposed vetted fix:</b> ${esc(a.fix.title)} <span class="small muted">(pattern ${esc(a.fix.pattern)}, service ${esc(a.fix.service)})</span><br>
<span class="small"><b>How it will be proven:</b> ${esc(a.fix.proof)}</span></p>
<p class="small"><b>Other recommended fixes from the Incident Analyst:</b></p><ol class="small">${(a.recommendations||[]).map(r=>`<li><b>${esc(r.pattern_name)}</b>: ${esc(r.change)}</li>`).join('')}</ol>
<form method="post" action="/api/mission/approve" style="display:flex;gap:10px"><input type="hidden" name="id" value="${esc(a.id)}">
<button class="btn primary" name="decision" value="apply">✓ Approve: apply fix and re-test</button>
<button class="btn" name="decision" value="skip">✕ Decline: keep rollback only</button></form></div>`}
async function mission(){try{const m=await (await fetch('/api/mission')).json();
const box=document.getElementById('agents');if(!m.mission_id){box.innerHTML='<p class="muted">No mission yet. Start one above.</p>';return}
document.getElementById('mstatus').innerHTML=`Mission <code>${esc(m.mission_id)}</code> · <b>${esc(m.status)}</b>`+
 (m.current?` · now: <b>${esc(m.current.title)}</b> (${esc(m.current.phase)})`:'')+(m.stop_reason?` · stopped: ${esc(m.stop_reason)}`:'');
box.innerHTML=Object.entries(m.agents).map(([k,a])=>`<div class="card agent ${a.status}${m.active===k&&m.status==='running'?' on':''}">
<div class="an"><span class="ai">${ICON[a.status]||'○'}</span> ${esc(a.name)}</div><div class="small muted">${esc(a.task)||'waiting'}</div></div>`).join('');
document.getElementById('mres').innerHTML=(m.results||[]).map(r=>`<tr><td>${esc(r.priority)}</td><td>${r.run_id?`<a href="/runs/${esc(r.run_id)}/">${esc(r.title)}</a>`:esc(r.title)}</td>
<td>${esc((r.impacted||[]).join(', ')||'none')}</td><td>${esc(r.severity)}</td><td>${r.rollback_verified?'✓ verified':esc(r.rollback_verified??'n/a')}</td><td class="small">${esc(r.remediation||r.reason)}</td></tr>`).join('');
document.getElementById('mlog').textContent=(m.log||[]).slice(-25).join('\\n');
document.getElementById('startbtn').disabled=m.status==='running';approval(m);}catch(e){}}
mission();setInterval(mission,2000);</script>"""

MISSION_CSS = """<style>.agents{display:grid;grid-template-columns:repeat(auto-fill,minmax(200px,1fr));gap:8px}
.agent{margin:0;padding:10px 12px}.agent .an{font-weight:650}.agent.done .ai{color:var(--good)}.agent.failed .ai{color:var(--critical)}
.agent.stopped .ai{color:var(--serious)}.agent.on{border-color:var(--series-1);box-shadow:0 0 0 2px var(--series-1)}
.agent.working .ai{color:var(--series-1)}.agent.waiting{border-color:var(--serious);box-shadow:0 0 0 2px var(--serious)}form.start{display:flex;gap:14px;align-items:center;flex-wrap:wrap}</style>"""


def mission_html(cfg: Config) -> str:
    body = f"""{MISSION_CSS}<h1>Agent mission</h1>
<p class="lead">Nine agents, coordinated by LangGraph, test the shop end to end: map it, prove it is healthy, plan the riskiest
failures, then for each one (in priority order) clear it, inject it, undo it with proof, explain it and fix it or roll it back.</p>
<div class="card"><form class="start" method="post" action="/api/mission/start"
onsubmit="return confirm('Start the mission? Failures will be injected into the sandbox one at a time, with all safety controls on.')">
<button id="startbtn" class="btn primary" type="submit">▶ Start mission</button>
<label>fixes <select name="approval"><option value="ask" selected>ask me before each fix</option>
<option value="auto">pre-approve vetted fixes</option><option value="never">recommend only</option></select></label>
<label><input type="checkbox" name="reuse" value="1"> reuse the accepted map + baseline</label>
<label>fault seconds <input name="fault_seconds" value="30" size="3"></label>
<label>tests <input name="max" value="5" size="2"></label></form>
<p class="small muted" id="mstatus"></p></div><div id="approval"></div>
<h2>Agents</h2><div class="agents" id="agents"></div>
<div class="two" style="margin-top:14px"><div><h2>Live blast radius</h2>{LIVE_MAP_JS}</div><div>{live_panel()}</div></div>
<h2>Test results</h2><div class="card wrap"><table><tr><th>P</th><th>Test</th><th>Customers affected</th><th>Severity</th>
<th>Undo</th><th>Remediation</th></tr><tbody id="mres"></tbody></table></div>
<details open><summary>Agent activity log</summary><pre id="mlog"></pre></details>{MISSION_JS}"""
    return pages.page("Agent mission", body, "/mission")


def start_mission(cfg: Config, form: dict[str, str]) -> str:
    from . import agents
    if agents.mission_running(cfg):
        return "a mission is already running"
    if safety.kill_engaged(cfg):
        return "kill switch engaged: reset it with `chaosctl kill --reset` first"
    args = [sys.executable, "-m", "chaosctl", "mission", "--yes",
            "--max", str(int(form.get("max") or 5)), "--fault-seconds", str(int(form.get("fault_seconds") or 30))]
    args += ["--approval", form.get("approval") if form.get("approval") in ("ask", "auto", "never") else "ask"]
    if form.get("reuse"):
        args.append("--reuse")
    log = (cfg.artifacts_dir / "mission-launch.log").open("a")
    subprocess.Popen(args, stdout=log, stderr=subprocess.STDOUT, start_new_session=True, cwd=str(cfg.root))
    return "started"


def project_health(cfg: Config, ctx: faults.Ctx) -> dict[str, Any]:
    from .cli import project_health as ph
    return ph(cfg, ctx)


def storefront(cfg: Config, ctx: faults.Ctx) -> tuple[bool, str]:
    try:
        status, _, ms = ctx.http.request("GET", cfg.sandbox["base_url"] + "/")
        return status < 400, f"HTTP {status} in {ms:.0f} ms"
    except Exception as exc:  # unreachable shop is a FAIL row, not a crash
        return False, str(exc)


def live_state(cfg: Config, ctx: faults.Ctx) -> str:
    st = status(cfg)
    try:
        snap = health.project_health(cfg, ctx.docker)
        green = health.verdict(cfg, snap)["green"]
    except Exception:  # docker unreachable: show the map without platform state
        snap, green = {}, None
    journeys = (st["live"] or {}).get("journeys", {})
    failing = [j for j, v in journeys.items() if v["ok"] < v["n"]]
    overlay = pages.live_overlay(cfg, [f["target"] for f in st["active_faults"]], snap, failing)
    return pages.live_fragment(cfg, overlay, journeys, (st["live"] or {}).get("run_id"), green)


LIVE_MAP_JS = """<div id="livemap">loading…</div><script>
async function lm(){try{document.getElementById('livemap').innerHTML=await (await fetch('/api/live-map')).text()}catch(e){}}
lm();setInterval(lm,2000);</script>"""


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
                    return self._send(200, pages.page("Overview", pages.home_html(cfg, rows), "/"))
                if parts == ["runs"]:
                    return self._send(200, index_html(cfg))
                if parts == ["mission"]:
                    return self._send(200, mission_html(cfg))
                if parts == ["api", "mission"]:
                    from . import agents
                    return self._send(200, json.dumps(agents.current_mission(cfg) or {}, default=str), "application/json")
                if parts == ["api", "live-map"]:
                    return self._send(200, live_state(cfg, ctx_factory()))
                if parts == ["fmea"]:
                    return self._send(200, pages.fmea_html(cfg))
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
            if self.path.rstrip("/") == "/api/mission/start":
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                msg = start_mission(cfg, form)
                self.send_response(303)
                self.send_header("Location", "/mission?msg=" + msg.replace(" ", "+")[:80])
                self.end_headers()
                return None
            if self.path.rstrip("/") == "/api/mission/approve":
                from . import agents
                n = int(self.headers.get("Content-Length") or 0)
                form = {k: v[0] for k, v in parse_qs(self.rfile.read(n).decode()).items()}
                agents.decide(cfg, form.get("id", ""), form.get("decision", ""))
                self.send_response(303)
                self.send_header("Location", "/mission")
                self.end_headers()
                return None
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

