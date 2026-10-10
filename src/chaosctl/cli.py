"""chaosctl command line. Run from the repository root."""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path
from typing import Any

from . import (
    ai,
    analysis,
    baseline,
    compare,
    config,
    dashboard,
    depmap,
    faults,
    fmea,
    health,
    incident,
    planner,
    report,
    safety,
    scenarios,
    selftest,
)
from .dockerctl import DockerCLI, DockerError
from .http import Http, HttpError
from .orchestrator import Orchestrator
from .probes import Journeys
from .registry import Registry
from .util import read_json, sha256_file
from .watchdog import Watchdog


def _ctx(cfg: config.Config) -> faults.Ctx:
    return faults.Ctx(cfg, DockerCLI(), Http(cfg.probes["timeout_s"]))


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def _git_commit() -> str | None:
    try:
        return subprocess.run(["git", "describe", "--always", "--dirty"], capture_output=True, text=True,
                              timeout=5).stdout.strip() or None
    except (OSError, subprocess.SubprocessError):
        return None


# --- commands -----------------------------------------------------------------
def cmd_doctor(cfg: config.Config, args) -> int:
    ctx = _ctx(cfg)
    checks: list[tuple[str, str, str]] = []

    def add(name: str, ok: bool | None, detail: str) -> None:
        checks.append((name, "PASS" if ok else "WARN" if ok is None else "FAIL", detail))

    add("python >= 3.11", sys.version_info >= (3, 11), sys.version.split()[0])
    try:
        ep = ctx.docker.context_endpoint()
        add("docker context is local", ep.startswith("unix://"), ep)
        res = ctx.docker.run(["ps", "--filter", f"label=com.docker.compose.project={cfg.sandbox['compose_project']}",
                              "--format", "{{.Names}}\t{{.State}}\t{{.Status}}"], check=False)
        rows = [r.split("\t") for r in res.stdout.strip().splitlines() if r]
        running = {r[0] for r in rows if len(r) > 1 and r[1] == "running"}
        add("demo containers running", len(running) > 0, f"{len(running)} running in project "
            f"{cfg.sandbox['compose_project']}")
        missing = sorted(set(cfg.allowlist) - running)
        add("allowlisted targets running", not missing, "missing: " + ", ".join(missing) if missing else "all present")
        unhealthy = [r[0] for r in rows if len(r) > 2 and "unhealthy" in r[2]]
        add("no unhealthy containers", not unhealthy, ", ".join(unhealthy) or "none")
        for name in unhealthy:  # say WHY: the container's own last health-check result
            info = ctx.docker.inspect(name) or {}
            log = ((info.get("State") or {}).get("Health") or {}).get("Log") or []
            last = (log[-1].get("Output") or "").strip().replace("\n", " ")[:200] if log else "no health log"
            streak = ((info.get("State") or {}).get("Health") or {}).get("FailingStreak")
            add(f"  why {name} is unhealthy", False,
                f"last health check: {last!r}; failing streak={streak}. Next: docker logs {name} --tail 50")
        img = ctx.docker.run(["image", "inspect", cfg.sandbox["netem_image"]], check=False)
        add("netem helper image built", img.returncode == 0,
            cfg.sandbox["netem_image"] + ("" if img.returncode == 0 else " (run `chaosctl setup`)"))
    except DockerError as exc:
        add("docker reachable", False, str(exc))
    try:
        status, _, ms = ctx.http.request("GET", cfg.sandbox["base_url"] + "/")
        add("storefront reachable", status < 400, f"HTTP {status} in {ms:.0f} ms")
    except HttpError as exc:
        add("storefront reachable", False, str(exc))
    try:
        add("locust reachable", True, json.dumps(faults.locust_status(ctx)))
    except (HttpError, ValueError) as exc:
        add("locust reachable", False, str(exc))
    try:
        svcs = ctx.http.get_json(cfg.sandbox["jaeger_api"].rstrip("/") + "/services")
        add("jaeger API reachable", True, f"{len((svcs or {}).get('data') or [])} services traced")
    except (HttpError, ValueError) as exc:
        add("jaeger API reachable", None, f"{exc} (map falls back to static-only)")
    add("kill switch not engaged", not safety.kill_engaged(cfg), str(safety.kill_file(cfg)))
    add("no active faults", not Registry(cfg.state_dir).list(), "registry empty" if not Registry(cfg.state_dir).list()
        else "run `chaosctl reconcile`")
    ok, why = ai.available()
    add("runtime AI available (optional)", True if ok else None, f"{ai.model_name()}: {why}")
    add("baseline recorded", (cfg.artifacts_dir / "baseline.json").exists() or None, "artifacts/baseline.json")
    for name, st, detail in checks:
        print(f"[{st:4}] {name}: {detail}")
    if getattr(args, "collect", False):
        collect_diagnostics(cfg, ctx)
    return 1 if any(st == "FAIL" for _, st, _ in checks) else 0


def collect_diagnostics(cfg: config.Config, ctx: faults.Ctx) -> None:
    """Write evidence/diagnostics-<container>.txt for every unhealthy/non-running app container."""
    out_dir = cfg.root / "evidence"
    out_dir.mkdir(exist_ok=True)
    snap = health.project_health(cfg, ctx.docker)
    sick = [n for n, s in snap.items() if s["state"] != "running" or "unhealthy" in s["status"]]
    if not sick:
        print("diagnostics: no unhealthy containers")
        return
    for name in sick:
        info = ctx.docker.inspect(name) or {}
        st = info.get("State") or {}
        hc = info.get("HostConfig") or {}
        env = [e for e in (info.get("Config") or {}).get("Env") or [] if e.split("=")[0] in ("GOMEMLIMIT", "GOGC")]
        logs = ctx.docker.run(["logs", "--tail", "200", name], check=False)
        stats = ctx.docker.run(["stats", "--no-stream", "--format", "{{.MemUsage}} cpu={{.CPUPerc}}", name],
                               check=False)
        state = {k: st.get(k) for k in ("Status", "ExitCode", "OOMKilled", "StartedAt", "Restarting")}
        lines = [f"# diagnostics for {name}", f"status: {snap[name]}", f"state: {json.dumps(state)}",
                 f"memory limit bytes: {hc.get('Memory')}  nano_cpus: {hc.get('NanoCpus')}  env: {env}",
                 f"restart count: {info.get('RestartCount')}", f"stats: {stats.stdout.strip()}", "",
                 "## health-check log (last 5)"]
        for h in ((st.get("Health") or {}).get("Log") or [])[-5:]:
            lines.append(f"{h.get('Start')} exit={h.get('ExitCode')} {(h.get('Output') or '').strip()[:300]}")
        lines += ["", "## last 200 log lines", logs.stdout, logs.stderr]
        path = out_dir / f"diagnostics-{name}.txt"
        path.write_text("\n".join(lines))
        print(f"diagnostics: {path}")


def cmd_setup(cfg: config.Config, args) -> int:
    path = cfg.root / "tools" / "netem"
    p = subprocess.run(["docker", "build", "-t", cfg.sandbox["netem_image"], str(path)])
    return p.returncode


def cmd_map(cfg: config.Config, args) -> int:
    m = depmap.run(cfg, Http(10), cfg.artifacts_dir / "dependency-map.json", cfg.root / "docs" / "DEPENDENCIES.md")
    print(f"services={len(m['services'])} edges={len(m['edges'])} runtime={m['sources']['runtime']}"
          f" -> artifacts/dependency-map.json, docs/DEPENDENCIES.md")
    _print(m["drift"])
    return 0


def cmd_baseline(cfg: config.Config, args) -> int:
    ctx = _ctx(cfg)
    leftovers = faults.reconcile(ctx, Registry(cfg.state_dir))
    if Registry(cfg.state_dir).list():
        print(f"refusing: active faults remain {leftovers}")
        return 2
    context: dict[str, Any] = {"identity": safety.check_identity(cfg, ctx.docker, "frontend")}
    try:
        context["locust"] = faults.locust_status(ctx)
    except HttpError as exc:
        context["locust"] = {"error": str(exc)}
    def load() -> dict[str, Any]:
        try:
            return faults.locust_status(ctx)
        except HttpError as exc:
            return {"error": str(exc)}

    run_dir = baseline.run(cfg, Journeys(cfg, ctx.http).all(), args.duration, context,
                           health=lambda: project_health(cfg, ctx), load=load, warmup_s=args.warmup)
    s = read_json(run_dir / "summary.json")
    print(f"\nbaseline -> {run_dir}/summary.json and artifacts/baseline.json\n")
    print(f"  {'journey':16} {'samples':>7} {'error rate':>10} {'p50 ms':>8} {'p95 ms':>8} {'p99 ms':>8} {'probe/s':>7}")
    for j, st in s["journeys"].items():
        rps = f"{st['throughput_rps']:.2f}" if st["throughput_rps"] else "n/a"
        print(f"  {j:16} {st['n']:>7} {st['error_rate']:>10.1%} {st['p50_ms']:>8.0f} {st['p95_ms']:>8.0f} "
              f"{st['p99_ms']:>8.0f} {rps:>7}")
    print("\n  baseline quality:")
    for c in s["quality"]["checks"]:
        print(f"   [{'PASS' if c['ok'] else 'WARN' if c['ok'] is None else 'FAIL'}] {c['check']}: {c['detail']}")
    print(f"\n  STEADY STATE {'ACCEPTED' if s['quality']['pass'] else 'REJECTED - fix the app/load and re-run baseline'}")
    return 0 if s["quality"]["pass"] else 1


def project_health(cfg: config.Config, ctx: faults.Ctx) -> dict[str, Any]:
    return health.project_health(cfg, ctx.docker)


def cmd_plan(cfg: config.Config, args) -> int:
    out = Path(args.out)
    res = fmea.run(cfg, use_ai=not args.no_ai)  # AI writes the FMEA and selects what to inject
    plan = res["plan"]
    if out.resolve() != (cfg.artifacts_dir / "experiment-plan.json").resolve():
        planner.save(plan, out)
    print_fmea(res["fmea"])
    return 0


def print_fmea(f: dict[str, Any], top: int = 8) -> None:
    m = f["meta"]
    who = f"AI ({m['ai_meta'].get('model_served')})" if m["method"] == "ai" else \
        f"rule-based (AI not used: {m.get('ai_unavailable') or m.get('ai_rejected')})"
    print(f"\nFMEA by {who} -> artifacts/fmea.json, docs/FMEA.md")
    print(f"  {'component':16} {'failure mode':22} {'S':>2} {'O':>2} {'D':>2} {'RPN':>4}   measured S/D/RPN")
    for r in f["rows"][:top]:
        me = r.get("measured") or {}
        meas = f"{me['severity']}/{me['detection']}/{me['rpn']}" if me else "-"
        print(f"  {r['component']:16} {r['failure_mode']:22} {r['severity']:>2} {r['occurrence']:>2} {r['detection']:>2} "
              f"{r['rpn']:>4}   {meas}")
    print("\n  selected for injection:")
    for s in f["selected"]:
        print(f"   {s['rank']}. {s['candidate_id']}: {s['reason']}")
    for p in f["problems"]:
        print(f"  NOT EXECUTABLE: {p}")


def cmd_mission(cfg: config.Config, args) -> int:
    """Multi-agent mission (LangGraph): map -> baseline -> AI plan -> per test: safety, inject, rollback, analyse,
    remediate -> report. Also started from the dashboard's Mission page."""
    from . import agents

    ctx = _ctx(cfg)

    def make_map() -> dict[str, Any]:
        return depmap.run(cfg, Http(10), cfg.artifacts_dir / "dependency-map.json", cfg.root / "docs" / "DEPENDENCIES.md")

    def run_baseline() -> dict[str, Any]:
        def load() -> dict[str, Any]:
            try:
                return faults.locust_status(ctx)
            except HttpError as exc:
                return {"error": str(exc)}
        d = baseline.run(cfg, Journeys(cfg, ctx.http).all(), args.baseline_seconds, {"locust": load()},
                         health=lambda: project_health(cfg, ctx), load=load, warmup_s=15)
        return read_json(d / "summary.json")

    def orchestrator(base: dict[str, Any], progress) -> Orchestrator:
        return Orchestrator(cfg, ctx, Journeys(cfg, ctx.http).all(), base, progress=progress,
                            use_ai=not args.no_ai, explain=True)

    def approve(exps: list[dict[str, Any]]) -> bool:
        if args.yes:
            return True
        ans = input(f"\nInject these {len(exps)} failure(s) one at a time, with all safety controls on? [y/N] ")
        return ans.strip().lower() in ("y", "yes")

    tools = agents.Tools(cfg=cfg, ctx=ctx, make_map=make_map, run_baseline=run_baseline, orchestrator=orchestrator,
                         approve=approve)
    opts = agents.Options(use_ai=not args.no_ai, apply_fixes=args.apply_fixes, approval=args.approval,
                          max_tests=args.max, fault_seconds=args.fault_seconds, reuse=args.reuse,
                          tripwire=not args.no_tripwire)
    try:
        final = agents.Mission(tools, opts).run()
    except safety.SafetyError as exc:
        print(f"REFUSED by safety policy: {exc}")
        return 3
    print(f"\nmission finished{': ' + final['stop_reason'] if final.get('stop_reason') else ''}. "
          "Dashboard: http://127.0.0.1:8090/mission")
    return 0 if not final.get("stop_reason") else 1


def cmd_decide(cfg: config.Config, args) -> int:
    from . import agents
    ap = (agents.current_mission(cfg) or {}).get("approval") or {}
    if ap.get("status") != "pending":
        print("no fix is waiting for approval")
        return 2
    print(f"{ap['fix']['title']} -> {args.decision}")
    return 0 if agents.decide(cfg, ap["id"], args.decision) else 2


def cmd_run(cfg: config.Config, args) -> int:
    base_path = cfg.artifacts_dir / "baseline.json"
    if not base_path.exists():
        print("refusing: no baseline (run `chaosctl baseline` first)")
        return 2
    base = read_json(base_path)
    if base.get("quality") and not base["quality"]["pass"] and not args.force:
        print("refusing: the recorded baseline failed its quality gate (re-run `chaosctl baseline`, or --force)")
        return 2
    plan_ref = None
    if args.tripwire:
        exp = planner.tripwire(cfg)
    elif args.scenario:
        exp, plan_ref = scenarios.load(cfg, args.scenario)
    else:
        plan_path = Path(args.plan)
        plan = read_json(plan_path)
        planner.verify_plan(plan)
        sel = [e for e in plan["experiments"] if e["id"] == args.experiment or str(e["rank"]) == str(args.experiment)]
        if not sel:
            print(f"experiment {args.experiment!r} not in plan; ids: {[e['id'] for e in plan['experiments']]}")
            return 2
        exp = sel[0]
        plan_ref = {"path": str(plan_path), "sha256": plan["meta"]["sha256"], "file_sha256": sha256_file(plan_path)}
    ctx = _ctx(cfg)
    orch = Orchestrator(cfg, ctx, Journeys(cfg, ctx.http).all(), base, progress=print,
                        use_ai=args.explain and not args.no_ai, explain=args.explain)
    print(f"running {exp['id']} (Ctrl-C or `chaosctl kill` aborts and rolls back) ...")
    try:
        run_dir = orch.run(exp, plan_ref)
    except safety.SafetyError as exc:
        print(f"REFUSED by safety policy: {exc}")
        return 3
    print(f"run dir: {run_dir}")
    if not (run_dir / "analysis.json").exists():
        print(f"analysis failed: {(run_dir / 'analysis_error.txt').read_text().strip()}")
        return 1
    a = read_json(run_dir / "analysis.json")
    print(f"outcome={a['outcome']} abort={(a['abort'] or {}).get('reason')} tti={a['time_to_impact_s']}s "
          f"recovered={a['recovered']} recovery={a['recovery_time_s']}s severity={a['severity']['label']} "
          f"({a['severity']['score']})")
    print(f"impacted={a['impacted_journeys']} unexpected={a['unexpected_impacted_journeys']} "
          f"time_to_restore={a.get('time_to_restore_s')}s self_healed={a.get('self_healed')}")
    for c in a.get("checks", []):
        print(f"check {c['check']}: {c['intact']}/{c['seeded']} intact, {c['lost']} LOST")
    bs = a.get("health_blind_spot")
    if bs and bs["customer_impact_s"]:
        print(f"HEALTH-CHECK BLIND SPOT: customers impacted {bs['customer_impact_s']:.0f} s, of which "
              f"{bs['silent_impact_s']:.0f} s while every container looked healthy "
              f"(platform first noticed at {bs['platform_first_not_green_at_s']} s, customers at "
              f"{bs['customers_first_failed_at_s']} s)")
    if (run_dir / "incident.json").exists():
        inc = read_json(run_dir / "incident.json")
        r = inc["analysis"]
        print(f"\nINCIDENT ANALYSIS ({inc['meta']['method']}): {r['verdict']}\n  {r['summary']}")
        print(f"  root cause: {r['why']['root_cause']}")
        for fx in sorted(r["fixes"], key=lambda f: f["priority"]):
            print(f"  {fx['priority']} {fx['pattern_name']}: {fx['change']}")
    if not (run_dir / "incident.json").exists():
        print("\n(incident analysis is a Day-2 feature: `chaosctl explain " + str(run_dir) + "`)")
    print(f"\nreport: chaosctl dashboard  ->  http://127.0.0.1:8090/runs/{run_dir.name}/")
    return 0


def cmd_explain(cfg: config.Config, args) -> int:
    from .evidence import collect

    run_dir = Path(args.run_dir)
    if args.recollect:
        collect(cfg, _ctx(cfg), run_dir)
    inc = incident.explain(cfg, run_dir, use_ai=not args.no_ai)
    print(f"{run_dir}/incident.md ({inc['meta']['method']}): {inc['analysis']['verdict']}")
    for k in ("ai_unavailable", "ai_rejected", "unverified_numbers"):
        if inc["meta"].get(k):
            print(f"  note: {k}: {inc['meta'][k]}")
    return 0


def cmd_compare(cfg: config.Config, args) -> int:
    c, path = compare.run(Path(args.before), Path(args.after), cfg.artifacts_dir)
    print(path.read_text())
    return 0


def cmd_scenarios(cfg: config.Config, args) -> int:
    for s in scenarios.list_all(cfg):
        f = s["faults"][0]
        print(f"{s['name']:20} {s['id']}: {f['fault_type']} {f['target']} {f.get('params')} {f['duration_s']}s")
        print(f"{'':20} hypothesis: {s['hypothesis']}")
    return 0


def cmd_export_site(cfg: config.Config, args) -> int:
    from .export import export

    files = export(cfg, Path(args.out))
    print(f"static site: {len(files)} pages -> {args.out}/index.html (open it in a browser, or publish the folder)")
    return 0


def cmd_dashboard(cfg: config.Config, args) -> int:
    dashboard.serve(cfg, lambda: _ctx(cfg), args.host, args.port)
    return 0


def cmd_kill(cfg: config.Config, args) -> int:
    if args.reset:
        safety.reset_kill(cfg)
        print("kill switch cleared")
        return 0
    safety.engage_kill(cfg, "manual kill")
    rep = faults.reconcile(_ctx(cfg), Registry(cfg.state_dir))
    print("KILL SWITCH ENGAGED: all faults rolled back; new runs refused until `chaosctl kill --reset`")
    _print(rep)
    return 0 if all(r.get("ok") for r in rep) else 1


def cmd_reconcile(cfg: config.Config, args) -> int:
    rep = faults.reconcile(_ctx(cfg), Registry(cfg.state_dir))
    _print(rep or "nothing to reconcile")
    return 0 if all(r.get("ok") for r in rep) else 1


def cmd_status(cfg: config.Config, args) -> int:
    _print({"kill_switch": safety.kill_engaged(cfg), "active_faults": Registry(cfg.state_dir).list(),
            "recent_runs": [p.name for p in sorted(cfg.runs_dir.glob("*"))[-10:]] if cfg.runs_dir.exists() else []})
    return 0


def cmd_analyze(cfg: config.Config, args) -> int:
    a = analysis.analyze_run(cfg, Path(args.run_dir))
    print(f"{args.run_dir}/analysis.json, findings.md: severity={a['severity']['label']} ({a['severity']['score']})")
    return 0


def cmd_report(cfg: config.Config, args) -> int:
    r = report.run(cfg, use_ai=not args.no_ai)
    print(f"artifacts/findings-report.md ({r['meta']['method']}), resilience score={r['resilience_score']}, "
          f"findings={len(r['findings'])}, rejected AI claims={len(r['rejected_ai_claims'])}")
    if "ai_unavailable" in r["meta"]:
        print(f"  note: AI unavailable: {r['meta']['ai_unavailable']}")
    return 0


def cmd_selftest(cfg: config.Config, args) -> int:
    out = Path(args.out) if args.out else cfg.artifacts_dir / "safety-checks.md"
    res = selftest.run_all(cfg, out, _git_commit())
    print(f"-> {out}")
    return 0 if all(r["status"] == "PASS" for r in res) else 1


def cmd_watchdog(cfg: config.Config, args) -> int:
    import signal

    # The watchdog must outlive a Ctrl-C in the operator's terminal; it exits on its own when the run is DONE.
    signal.signal(signal.SIGINT, signal.SIG_IGN)
    Watchdog(cfg, Path(args.run_dir), _ctx(cfg)).run()
    return 0


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="chaosctl", description=__doc__)
    p.add_argument("--config", default=None, help="path to sandbox.toml (default config/sandbox.toml)")
    sub = p.add_subparsers(dest="cmd", required=True)
    dr = sub.add_parser("doctor", help="check sandbox, app, tools and safety state")
    dr.add_argument("--collect", action="store_true", help="save diagnostics of unhealthy containers to evidence/")
    sub.add_parser("setup", help="build the netem helper image")
    sub.add_parser("map", help="build evidence-graded dependency map")
    b = sub.add_parser("baseline", help="measure steady state under normal load")
    b.add_argument("--duration", type=float, default=300)
    b.add_argument("--warmup", type=float, default=15, help="seconds probed but excluded from the numbers")
    mi = sub.add_parser("mission", help="multi-agent mission (LangGraph): every failure type in priority order, "
                                         "verified rollback, vetted fix + re-test")
    mi.add_argument("--max", type=int, default=5, help="max tests (default 5 = one per failure type)")
    mi.add_argument("--fault-seconds", type=int, default=None, help="shorter fault windows for a demo, e.g. 30")
    mi.add_argument("--baseline-seconds", type=int, default=120)
    mi.add_argument("--approval", choices=["ask", "auto", "never"], default="ask",
                    help="ask = human approves each fix on the dashboard (or `chaosctl decide`); auto = pre-approved; "
                         "never = recommend only")
    mi.add_argument("--apply-fixes", action="store_true", help="same as --approval auto")
    mi.add_argument("--no-tripwire", action="store_true", help="skip the automatic-stop safety test")
    de = sub.add_parser("decide", help="answer the Remediation agent's pending fix approval")
    de.add_argument("decision", choices=["apply", "skip"])
    mi.add_argument("--reuse", action="store_true", help="reuse an accepted map + baseline")
    mi.add_argument("--yes", action="store_true", help="do not ask for confirmation")
    mi.add_argument("--no-ai", action="store_true")
    pl = sub.add_parser("plan", help="AI FMEA: rate failure modes, select what to inject (no injection)")
    pl.add_argument("--no-ai", action="store_true")
    pl.add_argument("--out", default="artifacts/experiment-plan.json")
    r = sub.add_parser("run", help="run one experiment from the plan, or the tripwire")
    g = r.add_mutually_exclusive_group(required=True)
    g.add_argument("--experiment", help="experiment id or rank from the plan")
    g.add_argument("--tripwire", action="store_true", help="safety test: must be aborted by the watchdog")
    g.add_argument("--scenario", help="pre-registered scenario name from scenarios/ (e.g. cart-store-crash)")
    r.add_argument("--plan", default="artifacts/experiment-plan.json")
    r.add_argument("--explain", action="store_true",
                   help="Day-2 preview: incident analysis after the run (AI if ANTHROPIC_API_KEY is set)")
    r.add_argument("--no-ai", action="store_true", help="with --explain: rule-based analysis only")
    r.add_argument("--force", action="store_true", help="run even if the baseline failed its quality gate")
    ex = sub.add_parser("explain", help="AI incident analysis of one run (where/how/why + fixes)")
    ex.add_argument("run_dir")
    ex.add_argument("--no-ai", action="store_true")
    ex.add_argument("--recollect", action="store_true", help="re-collect traces/logs before explaining")
    cp = sub.add_parser("compare", help="before/after comparison of two runs of the same scenario")
    cp.add_argument("before")
    cp.add_argument("after")
    sub.add_parser("scenarios", help="list pre-registered scenarios")
    ex2 = sub.add_parser("export-site", help="static snapshot of the dashboard with all evidence (for deployment)")
    ex2.add_argument("--out", default="site")
    db = sub.add_parser("dashboard", help="web dashboard: live status, reports, kill switch")
    db.add_argument("--host", default="127.0.0.1")
    db.add_argument("--port", type=int, default=8090)
    k = sub.add_parser("kill", help="KILL SWITCH: roll back everything and block new runs")
    k.add_argument("--reset", action="store_true", help="clear the kill switch")
    sub.add_parser("reconcile", help="roll back leftover/orphan faults")
    sub.add_parser("status", help="kill switch, active faults, recent runs")
    a = sub.add_parser("analyze", help="re-compute analysis for a run directory")
    a.add_argument("run_dir")
    rp = sub.add_parser("report", help="AI findings report with citation checks")
    rp.add_argument("--no-ai", action="store_true")
    st = sub.add_parser("selftest", help="simulated safety verification -> artifacts/safety-checks.md")
    st.add_argument("--out", help="write the report here instead (e.g. evidence/safety-checks.md)")
    w = sub.add_parser("watchdog", help=argparse.SUPPRESS)
    w.add_argument("--run-dir", required=True)
    return p


COMMANDS = {"doctor": cmd_doctor, "setup": cmd_setup, "map": cmd_map, "baseline": cmd_baseline, "plan": cmd_plan,
            "run": cmd_run, "kill": cmd_kill, "reconcile": cmd_reconcile, "status": cmd_status,
            "analyze": cmd_analyze, "report": cmd_report, "selftest": cmd_selftest, "watchdog": cmd_watchdog,
            "explain": cmd_explain, "mission": cmd_mission, "decide": cmd_decide, "compare": cmd_compare,
            "scenarios": cmd_scenarios,
            "dashboard": cmd_dashboard, "export-site": cmd_export_site}


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        cfg = config.load(args.config)
    except config.ConfigError as exc:
        print(f"config error: {exc}")
        return 2
    try:
        return COMMANDS[args.cmd](cfg, args)
    except (DockerError, safety.SafetyError, FileNotFoundError, RuntimeError, ValueError) as exc:
        print(f"error: {exc}")
        return 2
