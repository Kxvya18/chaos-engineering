"""Multi-agent resilience mission, orchestrated with LangGraph.

Each agent owns one component of the system and one step of the mission. LangGraph decides who works next
(conditional edges), and the agents coordinate through the shared mission state: the Planner fills the test queue,
the Safety Officer clears each test, the Injector breaks, the Recovery agent undoes and proves the undo, the Analyst
explains, the Remediation agent fixes (and re-tests) or keeps the rollback, and the loop continues in priority order.

Rules kept from CLAUDE.md: only the Planner and the Analyst use the LLM (schema-validated, with fallbacks).
Safety, injection and rollback are deterministic code; the watchdog is a separate OS process OUTSIDE the graph so
it can stop any agent. A fault is always rolled back: the Recovery agent runs after every injection, and the
mission's safety net closes any open run and reconciles the fault registry even if the graph itself fails.
"""

from __future__ import annotations

import json
import os
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable, TypedDict

from langgraph.graph import END, START, StateGraph

from . import compare, faults, fmea, planner, remediate, safety
from .config import Config
from .orchestrator import Orchestrator, RunAborted, RunSession
from .registry import Registry
from .util import iso, now, read_json, stamp, write_json

# (node, name, components it owns, uses the LLM, responsibility)
AGENTS: list[tuple[str, str, str, bool, str]] = [
    ("cartographer", "Cartographer", "Docker Compose config, Jaeger traces", False,
     "maps services, dependencies and data flows"),
    ("sentinel", "Baseline Sentinel", "journey probes, Locust load, container health", False,
     "proves the system is healthy before anything breaks"),
    ("planner", "Risk Planner", "Claude API, FMEA, failure catalog", True,
     "rates every failure mode and queues the tests in priority order"),
    ("safety", "Safety Officer", "allow-list, identity check, kill switch, watchdog, steady-state gate", False,
     "clears each test for injection, or refuses it"),
    ("injector", "Chaos Injector", "Docker CLI, tc netem helper, CPU limits, Locust", False,
     "injects the failure and holds it while the impact is measured"),
    ("recovery", "Recovery Agent", "verified rollback, recovery watch, data-durability check", False,
     "undoes the failure and proves the system is back"),
    ("analyst", "Incident Analyst", "Jaeger, container logs, compose facts, Claude API", True,
     "explains where, how and why it failed, with industry-pattern fixes"),
    ("remediator", "Remediation Agent", "vetted fix catalog, compose override, re-test", False,
     "applies a vetted fix and proves it with a re-test, or keeps the rollback and recommends fixes"),
    ("reporter", "Reporter", "FMEA, mission report, website", False, "publishes the mission result"),
]
AGENT_NAMES = {a[0]: a[1] for a in AGENTS}


class MissionState(TypedDict, total=False):
    mission_id: str
    baseline_ok: bool
    queue: list[dict[str, Any]]
    current: dict[str, Any] | None
    results: list[dict[str, Any]]
    fixes: list[dict[str, Any]]
    stop_reason: str | None


# ------------------------------------------------------------------ activity bus (read by the dashboard)
def mission_file(cfg: Config) -> Path:
    return cfg.state_dir / "mission.json"


def current_mission(cfg: Config) -> dict[str, Any] | None:
    p = mission_file(cfg)
    try:
        return json.loads(p.read_text()) if p.exists() else None
    except (OSError, json.JSONDecodeError):
        return None


def mission_running(cfg: Config) -> bool:
    m = current_mission(cfg)
    if not m or m.get("status") != "running":
        return False
    try:
        os.kill(int(m.get("pid") or 0), 0)
        return True
    except (OSError, ValueError):
        return False  # the process is gone: a stale record, not a running mission


class Bus:
    """Agent activity: append-only log per mission + a live snapshot (state/mission.json) for the dashboard."""

    def __init__(self, cfg: Config, mission_id: str, options: dict[str, Any]):
        self.cfg = cfg
        self.dir = cfg.artifacts_dir / "missions" / mission_id
        self.dir.mkdir(parents=True, exist_ok=True)
        cfg.state_dir.mkdir(parents=True, exist_ok=True)
        self.snap: dict[str, Any] = {
            "mission_id": mission_id, "status": "running", "started": iso(), "finished": None, "pid": os.getpid(),
            "options": options, "agents": {n: {"name": nm, "status": "idle", "task": "", "updated": None}
                                           for n, nm, *_ in AGENTS},
            "active": None, "current": None, "queue": [], "results": [], "fixes": [], "decisions": [], "log": [],
            "stop_reason": None}
        self._save()

    def _save(self) -> None:
        tmp = mission_file(self.cfg).with_suffix(".tmp")
        tmp.write_text(json.dumps(self.snap, default=str))
        tmp.replace(mission_file(self.cfg))

    def agent(self, node: str, status: str, task: str, **detail: Any) -> None:
        a = self.snap["agents"][node]
        a.update(status=status, task=task, updated=iso())
        self.snap["active"] = node if status == "working" else self.snap["active"]
        rec = {"ts": iso(), "agent": node, "name": AGENT_NAMES[node], "status": status, "task": task, **detail}
        with (self.dir / "agents.jsonl").open("a") as fh:
            fh.write(json.dumps(rec, default=str) + "\n")
        self.log(f"[{AGENT_NAMES[node]}] {status}: {task}")

    def log(self, line: str) -> None:
        self.snap["log"] = (self.snap["log"] + [f"{iso()[11:19]} {line}"])[-80:]
        self._save()

    def set(self, **kw: Any) -> None:
        self.snap.update(kw)
        self._save()

    def wait_decision(self, req: dict[str, Any], timeout_s: float, sleep: Callable[[float], None]) -> str:
        """Human in the loop: publish the request on the dashboard and wait for Approve / Decline."""
        dec_file = self.cfg.state_dir / "approval-decision.json"
        dec_file.unlink(missing_ok=True)
        self.set(approval={**req, "status": "pending", "asked": iso()})
        waited = 0.0
        while waited < timeout_s:
            if safety.kill_engaged(self.cfg):
                decision = "skip"
                break
            try:
                d = json.loads(dec_file.read_text())
                if d.get("id") == req["id"] and d.get("decision") in ("apply", "skip"):
                    decision = d["decision"]
                    break
            except (OSError, json.JSONDecodeError):
                pass
            sleep(1.0)
            waited += 1.0
        else:
            decision = "skip"  # no answer: never change the system without a human decision
            self.snap["approval_timeout"] = True
        by = ("no answer within the time limit (no change)" if self.snap.pop("approval_timeout", False) else
              "kill switch engaged (no change)" if safety.kill_engaged(self.cfg) else "operator on the dashboard")
        self.set(approval={**req, "status": decision, "decided": iso(), "by": by})
        return decision

    def record_decision(self, req: dict[str, Any], decision: str, by: str) -> dict[str, Any]:
        rec = {"id": req["id"], "test": req["test"], "run_id": req["run_id"], "fix": req["fix"]["title"],
               "fix_id": req["fix"]["id"], "decision": decision, "by": by,
               "asked": (self.snap.get("approval") or {}).get("asked") or iso(), "decided": iso()}
        self.snap["decisions"] = self.snap.get("decisions", []) + [rec]
        with (self.dir / "agents.jsonl").open("a") as fh:
            fh.write(json.dumps({"ts": iso(), "agent": "human", "name": "Human decision", **rec}) + "\n")
        self._save()
        return rec


def decide(cfg: Config, req_id: str, decision: str) -> bool:
    """Called by the dashboard when the human clicks Approve / Decline."""
    m = current_mission(cfg) or {}
    ap = m.get("approval") or {}
    if ap.get("id") != req_id or ap.get("status") != "pending" or decision not in ("apply", "skip"):
        return False
    (cfg.state_dir / "approval-decision.json").write_text(json.dumps({"id": req_id, "decision": decision, "at": iso()}))
    return True


# ------------------------------------------------------------------ tools the agents use
@dataclass
class Tools:
    cfg: Config
    ctx: faults.Ctx
    make_map: Callable[[], dict[str, Any]]
    run_baseline: Callable[[], dict[str, Any]]
    orchestrator: Callable[[dict[str, Any], Callable[[str], None]], Orchestrator]
    approve: Callable[[list[dict[str, Any]]], bool] = lambda exps: True
    echo: Callable[[str], None] = print
    ask_human: Callable[[dict[str, Any]], str] | None = None  # returns "apply" or "skip"; None = dashboard


@dataclass
class Options:
    use_ai: bool = True
    apply_fixes: bool = False  # True = the human pre-approved vetted fixes (no question asked)
    approval: str = "ask"  # ask = human in the loop per fix; auto = pre-approved; never = recommend only
    approval_timeout_s: int = 900
    tripwire: bool = True  # Safety Officer proves the automatic stop at the end of the mission
    max_tests: int | None = None  # None = the whole queue
    suite: str = "priority"  # priority = one per failure type; database = every database error on every datastore
    fault_seconds: int | None = None
    reuse: bool = False  # reuse an accepted map + baseline instead of re-measuring (re-takes of the demo)


@dataclass
class Mission:
    tools: Tools
    opts: Options
    mission_id: str = field(default_factory=lambda: f"{stamp(now())}-mission")
    sleep: Callable[[float], None] = time.sleep
    sessions: dict[str, tuple[Orchestrator, RunSession]] = field(default_factory=dict)

    # -------------------------------------------------------------- helpers
    @property
    def cfg(self) -> Config:
        return self.tools.cfg

    def _say(self, line: str) -> None:
        self.tools.echo(line)
        self.bus.log(line)

    def _work(self, node: str, task: str) -> None:
        self.tools.echo(f"\n=== {AGENT_NAMES[node]} === {task}")
        self.bus.agent(node, "working", task)

    def _done(self, node: str, task: str, status: str = "done", **detail: Any) -> None:
        self.tools.echo(f"    {AGENT_NAMES[node]}: {task}")
        self.bus.agent(node, status, task, **detail)

    # -------------------------------------------------------------- agents
    def cartographer(self, st: MissionState) -> MissionState:
        mp = self.cfg.artifacts_dir / "dependency-map.json"
        if self.opts.reuse and mp.exists():
            m = read_json(mp)
            self._work("cartographer", "reusing the recorded dependency map")
        else:
            self._work("cartographer", "mapping services, dependencies and data flows")
            m = self.tools.make_map()
        edges = [e for e in m["edges"] if not e.get("telemetry")]
        gaps = sum(len(v) for v in m.get("drift", {}).values())
        self._done("cartographer", f"{len(m['services'])} services, {len(edges)} customer-path connections, "
                                   f"{gaps} weak spots", services=len(m["services"]), edges=len(edges))
        return {}

    def sentinel(self, st: MissionState) -> MissionState:
        bp = self.cfg.artifacts_dir / "baseline.json"
        if self.opts.reuse and bp.exists() and (read_json(bp).get("quality") or {}).get("pass"):
            self._work("sentinel", "reusing the accepted baseline")
            b = read_json(bp)
        else:
            self._work("sentinel", "measuring normal behaviour under steady load")
            b = self.tools.run_baseline()
        ok = bool((b.get("quality") or {}).get("pass"))
        o = b["overall"]
        msg = f"error rate {100 * o['error_rate']:.1f}%, p95 {o['p95_ms']:.0f} ms over {o['n']} requests"
        if ok:
            self._done("sentinel", f"steady state ACCEPTED: {msg}")
            return {"baseline_ok": True}
        self._done("sentinel", f"steady state REJECTED ({msg}); nothing will be injected", status="failed")
        return {"baseline_ok": False, "stop_reason": "baseline rejected: the system is not healthy yet"}

    def planner(self, st: MissionState) -> MissionState:
        self._work("planner", "writing the FMEA and choosing tests (AI)" if self.opts.use_ai
                   else "writing the FMEA (rule-based: AI disabled)")
        out = fmea.run(self.cfg, use_ai=self.opts.use_ai, coverage=self.opts.suite == "priority",
                       fault_seconds=self.opts.fault_seconds, suite=self.opts.suite)
        plan = out["plan"]
        planner.verify_plan(plan)
        exps = plan["experiments"][: self.opts.max_tests]
        m = out["fmea"]["meta"]
        who = {"ai": "AI", "ai-cached": "AI (last validated answer)"}.get(m["method"], "rule-based fallback")
        for e in exps:
            self._say(f"    P{e['rank']} [{e.get('source')}] {e['id']}: {e['why']}")
        self._done("planner", f"{len(out['fmea']['rows'])} failure modes rated by {who}; "
                              f"{len(exps)} tests queued in priority order", method=m["method"])
        if self.opts.tripwire:
            exps = exps + [{**planner.tripwire(self.cfg), "rank": len(exps) + 1, "source": "safety-test",
                            "title": "Safety test: damage beyond the allowed area must be stopped automatically"}]
            self._say("    + safety test (tripwire): the watchdog must stop it on its own")
        if not self.tools.approve(exps):
            return {"queue": [], "stop_reason": "operator declined the test plan"}
        self.plan_ref = {"path": "artifacts/experiment-plan.json", "sha256": plan["meta"]["sha256"]}
        return {"queue": exps}

    def safety(self, st: MissionState) -> MissionState:
        exp, rest = st["queue"][0], st["queue"][1:]
        label = exp.get("title") or exp["id"]
        self._work("safety", f"pre-flight checks for {label}")
        self.bus.set(current={"experiment": exp["id"], "title": label, "phase": "pre-flight"},
                     queue=[e["id"] for e in rest])
        if safety.kill_engaged(self.cfg):
            self._done("safety", "kill switch engaged: mission stopped", status="failed")
            return {"queue": rest, "current": None, "stop_reason": "kill switch engaged"}
        orch = self.tools.orchestrator(read_json(self.cfg.artifacts_dir / "baseline.json"), self._say)
        try:
            s = orch.open(exp, getattr(self, "plan_ref", None))
        except safety.SafetyError as exc:
            self._done("safety", f"REFUSED {exp['id']}: {exc}", status="failed")
            return {"queue": rest, "current": None,
                    "results": st.get("results", []) + [{"experiment": exp["id"], "title": label,
                                                          "status": "refused", "reason": str(exc)}]}
        self.sessions[s.run_id] = (orch, s)
        cur = {"experiment": exp, "run_id": s.run_id, "cleared": False}
        try:
            cur["cleared"] = orch.steady(s)
        except (RunAborted, safety.SafetyError) as exc:
            s.outcome = {"status": str(exc)}
        if cur["cleared"]:
            self._done("safety", "cleared: allow-listed target, identity verified, watchdog armed, steady state OK")
        else:
            self._done("safety", f"NOT cleared ({s.outcome.get('status')}); handing to Recovery to close the run",
                       status="failed")
        return {"queue": rest, "current": cur}

    def injector(self, st: MissionState) -> MissionState:
        cur = st["current"]
        orch, s = self.sessions[cur["run_id"]]
        f0 = cur["experiment"]["faults"][0]
        self._work("injector", f"injecting {f0['fault_type']} on {f0['target']} for {f0['duration_s']} s")
        self.bus.set(current={**self.bus.snap["current"], "phase": "fault active", "run_id": s.run_id})
        try:
            orch.inject_and_hold(s)
        except RunAborted as exc:
            s.outcome = {"status": str(exc)}
        except safety.SafetyError as exc:
            s.outcome = {"status": "safety_error", "error": str(exc)}
        except Exception as exc:  # anything else still goes to Recovery: the fault must be undone
            s.outcome = {"status": f"injector_error: {exc}"}
        self._done("injector", f"fault window ended: {s.outcome.get('status')}",
                   status="done" if s.outcome.get("status") == "completed" else "stopped")
        return {}

    def recovery(self, st: MissionState) -> MissionState:
        cur = st["current"]
        orch, s = self.sessions.pop(cur["run_id"])
        self._work("recovery", "rolling back, verifying against the pre-test snapshot, watching recovery")
        self.bus.set(current={**self.bus.snap["current"], "phase": "rollback + recovery"})
        rbs = orch.close(s)
        verified = all(r.get("verified") for r in rbs)
        if rbs and not verified:
            self._done("recovery", "rollback NOT verified: mission stopped, operator needed", status="failed")
            return {"current": {**cur, "rollback_verified": False},
                    "stop_reason": "rollback could not be verified; check `chaosctl status`"}
        self._done("recovery", "undo verified, system back to its pre-test state" if rbs
                   else "nothing was injected; run closed")
        return {"current": {**cur, "rollback_verified": verified if rbs else None, "injected": bool(rbs)}}

    def analyst(self, st: MissionState) -> MissionState:
        cur = st["current"]
        run_dir = self.cfg.runs_dir / cur["run_id"]
        self._work("analyst", "collecting evidence and explaining where, how and why")
        self.bus.set(current={**self.bus.snap["current"], "phase": "analysis"})
        orch = self.tools.orchestrator(read_json(self.cfg.artifacts_dir / "baseline.json"), self._say)
        orch.post(_closed_session(cur["run_id"], run_dir, cur["experiment"], cur.get("injected")), explain=True)
        res = summarize(self.cfg, run_dir, cur)
        self._done("analyst", f"{res['outcome']}: customers affected {', '.join(res['impacted']) or 'none'}; "
                              f"severity {res['severity']}; top fix: {res.get('top_fix') or 'n/a'}")
        return {"results": st.get("results", []) + [res]}

    def remediator(self, st: MissionState) -> MissionState:
        cur, res = st["current"], st["results"][-1]
        exp = cur["experiment"]
        fixes = list(st.get("fixes", []))
        self._work("remediator", "deciding: apply a vetted fix, or keep the rollback and recommend")
        if exp.get("verification_of"):  # this run re-tested a fix: keep it or revert it
            fx = next(f for f in fixes if f["before_run"] == exp["verification_of"])
            cmp, path = compare.run(self.cfg.runs_dir / fx["before_run"], self.cfg.runs_dir / cur["run_id"],
                                    self.cfg.artifacts_dir)
            fx.update(after_run=cur["run_id"], comparison=path.stem, verdict=remediate.verdict(cmp),
                      severity_before=cmp["before"]["severity_score"], severity_after=cmp["after"]["severity_score"])
            if fx["verdict"] == "fixed":
                res["remediation"] = f"verification run for fix {fx['fix_id']}: fix KEPT"
                self._done("remediator", f"FIXED and proven: severity {fx['severity_before']} -> "
                                         f"{fx['severity_after']}, carts lost {cmp['after']['carts_lost']}")
            else:
                rv = remediate.revert_fix(self.cfg, self.tools.ctx, read_fix(self.cfg, fx["fix_id"]))
                fx["reverted"] = rv["ok"]
                res["remediation"] = f"fix {fx['fix_id']} did not help: reverted"
                self._done("remediator", f"fix did not prove itself ({cmp['verdict']}): reverted to the original "
                                         "configuration", status="stopped")
            _record_fix(self.cfg, fx)
            self.bus.set(fixes=fixes)
            return {"fixes": fixes, "results": st["results"]}

        harmed = bool(res["impacted"]) or bool(res.get("data_lost"))
        fix = remediate.find_fix(self.cfg, exp) if harmed and res.get("injected") else None
        queue = st["queue"]
        recs = recommendations(self.cfg, cur["run_id"])
        res["recommendations"] = recs
        if exp.get("source") == "safety-test":
            ok = res["outcome"] == "watchdog_abort"
            res["remediation"] = ("safety test PASSED: the watchdog stopped the experiment by itself and the fault was "
                                  "undone" if ok else f"safety test FAILED: outcome {res['outcome']}")
            self._done("remediator", res["remediation"], status="done" if ok else "failed")
            self.bus.set(results=st["results"], fixes=fixes)
            return {"queue": queue, "fixes": fixes, "results": st["results"]}
        decision = "skip"
        if fix and harmed and not st.get("stop_reason"):
            req = {"id": f"{cur['run_id']}:{fix['id']}", "run_id": cur["run_id"], "test": res["title"],
                   "impact": f"customers affected: {', '.join(res['impacted']) or 'none'}; severity "
                             f"{res['severity']}; carts lost {res.get('carts_lost') or 'n/a'}",
                   "fix": {k: fix[k] for k in ("id", "title", "pattern", "proof", "service")},
                   "recommendations": recs}
            if self.opts.apply_fixes or self.opts.approval == "auto":
                decision, by = "apply", "pre-approved by the operator at mission start (--approval auto)"
            elif self.opts.approval == "never":
                decision, by = "skip", "operator policy at mission start: recommend only"
            else:
                self.bus.set(current={**(self.bus.snap.get("current") or {}), "phase": "waiting for human approval"})
                self.bus.agent("remediator", "waiting", f"waiting for human approval: {fix['title']}")
                self.tools.echo(f"    APPROVAL NEEDED: apply '{fix['title']}' to {fix['service']}? "
                                "(dashboard: Agent mission page)")
                if self.tools.ask_human:
                    decision, by = self.tools.ask_human(req), "operator (terminal)"
                else:
                    decision = self.bus.wait_decision(req, self.opts.approval_timeout_s, self.sleep)
                    by = self.bus.snap["approval"]["by"]
                self._say(f"    human decision: {decision} ({by})")
            res["decision"] = self.bus.record_decision(req, decision, by)
        if not harmed:
            res["remediation"] = "no customer impact: nothing to fix (rollback verified)"
            self._done("remediator", res["remediation"])
        elif fix and decision == "apply":
            ap = remediate.apply_fix(self.cfg, self.tools.ctx, fix)
            if ap["ok"]:
                retest = {**{k: v for k, v in exp.items() if k != "candidate_id"},
                          "verification_of": cur["run_id"], "fix_id": fix["id"],
                          "title": f"Re-test after fix: {fix['title']}", "source": "remediation"}
                fixes.append({"fix_id": fix["id"], "title": fix["title"], "pattern": fix["pattern"],
                              "before_run": cur["run_id"], "applied": iso(), "command": ap["command"]})
                queue = [retest] + queue  # the proof runs next, before the remaining tests
                res["remediation"] = f"applied vetted fix {fix['id']}; re-test queued"
                self._done("remediator", f"applied {fix['title']}; the same test runs again to prove it")
            else:
                res["remediation"] = f"fix {fix['id']} could not be applied: {ap['error']} (rollback kept)"
                self._done("remediator", res["remediation"], status="failed")
        elif fix:
            res["remediation"] = (f"vetted fix available ({fix['title']}); not applied: the human declined or did not "
                                  "approve it. Rollback verified")
            self._done("remediator", res["remediation"])
        else:
            res["remediation"] = ("rolled back (verified); recommended fixes: " + (res.get("top_fix") or "see analysis")
                                  + ". No vetted automatic fix exists for this failure")
            self._done("remediator", res["remediation"])
        self.bus.set(results=st["results"], fixes=fixes)
        return {"queue": queue, "fixes": fixes, "results": st["results"]}

    def reporter(self, st: MissionState) -> MissionState:
        self._work("reporter", "publishing the mission report")
        rep = {"mission_id": self.mission_id, "finished": iso(), "stop_reason": st.get("stop_reason"),
               "options": vars(self.opts), "results": st.get("results", []), "fixes": st.get("fixes", []),
               "decisions": self.bus.snap.get("decisions", []),
               "agents": [{"node": n, "name": nm, "components": c, "uses_llm": llm, "role": r}
                          for n, nm, c, llm, r in AGENTS]}
        write_json(self.bus.dir / "report.json", rep)
        (self.bus.dir / "report.md").write_text(render_md(rep))
        tested = sum(1 for r in rep["results"] if r.get("injected"))
        self._done("reporter", f"{tested} failure(s) tested, {len(rep['fixes'])} fix(es) attempted"
                               + (f"; stopped: {rep['stop_reason']}" if rep["stop_reason"] else ""))
        return {}

    # -------------------------------------------------------------- routing
    @staticmethod
    def after_sentinel(st: MissionState) -> str:
        return "planner" if st.get("baseline_ok") else "reporter"

    @staticmethod
    def after_planner(st: MissionState) -> str:
        return "safety" if st.get("queue") and not st.get("stop_reason") else "reporter"

    @staticmethod
    def after_safety(st: MissionState) -> str:
        cur = st.get("current")
        if cur is None:  # refused before anything started
            return "safety" if st.get("queue") and not st.get("stop_reason") else "reporter"
        return "injector" if cur["cleared"] else "recovery"

    @staticmethod
    def after_remediator(st: MissionState) -> str:
        return "safety" if st.get("queue") and not st.get("stop_reason") else "reporter"

    def graph(self):
        g = StateGraph(MissionState)
        for node, *_ in AGENTS:
            g.add_node(node, getattr(self, node))
        g.add_edge(START, "cartographer")
        g.add_edge("cartographer", "sentinel")
        g.add_conditional_edges("sentinel", self.after_sentinel, ["planner", "reporter"])
        g.add_conditional_edges("planner", self.after_planner, ["safety", "reporter"])
        g.add_conditional_edges("safety", self.after_safety, ["injector", "recovery", "safety", "reporter"])
        g.add_edge("injector", "recovery")
        g.add_edge("recovery", "analyst")
        g.add_edge("analyst", "remediator")
        g.add_conditional_edges("remediator", self.after_remediator, ["safety", "reporter"])
        g.add_edge("reporter", END)
        return g.compile()

    # -------------------------------------------------------------- run
    def run(self) -> dict[str, Any]:
        if mission_running(self.cfg):
            raise safety.SafetyError("another mission is already running")
        self.bus = Bus(self.cfg, self.mission_id, vars(self.opts))
        final: dict[str, Any] = {}
        status = "failed"
        try:
            final = self.graph().invoke({"mission_id": self.mission_id, "results": [], "fixes": [], "queue": []},
                                        {"recursion_limit": 400})
            status = "stopped" if final.get("stop_reason") else "done"
            return final
        except BaseException as exc:
            self.bus.log(f"mission error: {type(exc).__name__}: {exc}")
            raise
        finally:  # safety net: no fault survives the mission, whatever happened inside the graph
            for orch, s in list(self.sessions.values()):
                orch.close(s)
            self.sessions.clear()
            left = faults.reconcile(self.tools.ctx, Registry(self.cfg.state_dir))
            if left:
                self.bus.log(f"safety net reconciled leftovers: {left}")
            self.bus.set(status=status, finished=iso(), active=None, current=None,
                         stop_reason=final.get("stop_reason"))


def _closed_session(run_id: str, run_dir: Path, exp: dict[str, Any], injected: bool | None) -> RunSession:
    s = RunSession(run_id=run_id, run_dir=run_dir, experiment=exp, closed=True)
    s.injected = [{"run_id": run_id}] if injected else []
    return s


def recommendations(cfg: Config, run_id: str, n: int = 3) -> list[dict[str, Any]]:
    """The Analyst's top industry-pattern fixes for this run (shown to the human with the approval request)."""
    p = cfg.runs_dir / run_id / "incident.json"
    if not p.exists():
        return []
    inc = read_json(p)
    titles = {it["id"]: it["title"] for it in inc.get("evidence", [])}
    fx = sorted(inc["analysis"].get("fixes") or [], key=lambda f: f["priority"])[:n]
    return [{**{k: f.get(k) for k in ("priority", "pattern_id", "pattern_name", "change", "expected_effect", "references")},
             "evidence": [{"id": e, "title": titles.get(e, "?")} for e in f.get("evidence_ids", [])]} for f in fx]


def read_fix(cfg: Config, fix_id: str) -> dict[str, Any]:
    return next(f for f in remediate.catalog(cfg) if f["id"] == fix_id)


def _record_fix(cfg: Config, fx: dict[str, Any]) -> None:
    """Attach the fix outcome to the FMEA row of the failure it addressed."""
    p = cfg.artifacts_dir / "fmea.json"
    before = cfg.runs_dir / fx["before_run"] / "experiment.json"
    if not p.exists() or not before.exists():
        return
    cid = read_json(before)["experiment"].get("candidate_id")
    f = read_json(p)
    for r in f["rows"]:
        if r["candidate_id"] == cid:
            r["fix"] = {k: fx.get(k) for k in ("fix_id", "title", "verdict", "after_run", "severity_before",
                                               "severity_after")}
    write_json(p, f)


def summarize(cfg: Config, run_dir: Path, cur: dict[str, Any]) -> dict[str, Any]:
    exp = cur["experiment"]
    out: dict[str, Any] = {"experiment": exp["id"], "title": exp.get("title") or exp["id"], "run_id": cur["run_id"],
                           "priority": exp.get("rank"), "source": exp.get("source"), "injected": bool(cur.get("injected")),
                           "rollback_verified": cur.get("rollback_verified")}
    ap = run_dir / "analysis.json"
    if not ap.exists():
        return {**out, "outcome": "not analysed", "impacted": [], "severity": "n/a"}
    a = read_json(ap)
    lost = next((c for c in a.get("checks", []) if c.get("lost") is not None), None)
    out.update(outcome=a["outcome"], impacted=a["impacted_journeys"], severity=a["severity"]["label"],
               severity_score=a["severity"]["score"], time_to_restore_s=a.get("time_to_restore_s"),
               silent_s=(a.get("health_blind_spot") or {}).get("silent_impact_s"),
               data_lost=bool(lost and lost["lost"]), carts_lost=f"{lost['lost']}/{lost['seeded']}" if lost else None)
    out["db_error"] = exp.get("db_error")
    out["target"] = exp["faults"][0]["target"]
    ip = run_dir / "incident.json"
    if ip.exists():
        inc = read_json(ip)
        r, m = inc["analysis"], inc["meta"]
        fx = sorted(r.get("fixes") or [], key=lambda f: f["priority"])
        out["top_fix"] = fx[0]["pattern_name"] if fx else None
        out["analysis"] = {"method": m["method"] + (" (last validated answer)" if (m.get("ai_meta") or {}).get("cached")
                                                    else ""),
                           "summary": r.get("summary"), "verdict": r.get("verdict"),
                           "where": (r.get("where") or {}).get("failed_component"),
                           "path": (r.get("where") or {}).get("propagation_path"), "how": r.get("how"),
                           "why": (r.get("why") or {}).get("root_cause"),
                           "detection_gap": r.get("detection_gap"), "unverified_numbers": m.get("unverified_numbers")}
    return out


def render_md(rep: dict[str, Any]) -> str:
    lines = [f"# Mission {rep['mission_id']}", "", f"Finished {rep['finished']}"
             + (f" · stopped: {rep['stop_reason']}" if rep["stop_reason"] else ""), "",
             "| P | Test | Outcome | Customers affected | Severity | Restore (s) | Carts lost | Undo verified | Remediation |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rep["results"]:
        lines.append(f"| {r.get('priority', '')} | {r['title']} | {r.get('outcome', r.get('status'))} | "
                     f"{', '.join(r.get('impacted') or []) or '—'} | {r.get('severity', '')} | "
                     f"{r.get('time_to_restore_s', '')} | {r.get('carts_lost') or '—'} | "
                     f"{'yes' if r.get('rollback_verified') else 'n/a' if r.get('rollback_verified') is None else 'NO'} | "
                     f"{r.get('remediation', r.get('reason', ''))} |")
    if rep.get("decisions"):
        lines += ["", "## Human-in-the-loop decisions", "", "| Test | Fix proposed | Decision | By | Decided |",
                  "|---|---|---|---|---|"]
        lines += [f"| {d['test']} | {d['fix']} | **{d['decision']}** | {d['by']} | {d['decided']} |" for d in rep["decisions"]]
    if rep["fixes"]:
        lines += ["", "## Fixes", ""]
        lines += [f"- {f['title']} (`{f['fix_id']}`): **{f.get('verdict', 'pending')}** — severity "
                  f"{f.get('severity_before')} → {f.get('severity_after')} (comparison `{f.get('comparison')}`)"
                  for f in rep["fixes"]]
    lines += ["", "## Agents", "", "| Agent | Components | LLM | Role |", "|---|---|---|---|"]
    lines += [f"| {a['name']} | {a['components']} | {'yes' if a['uses_llm'] else 'no'} | {a['role']} |"
              for a in rep["agents"]]
    return "\n".join(lines) + "\n"
