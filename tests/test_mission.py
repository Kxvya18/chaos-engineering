"""Multi-agent mission (LangGraph) end to end in simulation: every agent runs, every failure type is tested in
priority order, every fault is rolled back with verification, and the Remediation agent applies the vetted fix,
re-tests it and keeps it only because the re-test proves it."""

import json
import threading
import time
from pathlib import Path

import pytest

pytest.importorskip("langgraph")

from chaosctl import agents, baseline, depmap, safety  # noqa: E402
from chaosctl.orchestrator import Orchestrator  # noqa: E402
from chaosctl.registry import Registry  # noqa: E402
from chaosctl.util import read_json, write_json  # noqa: E402
from chaosctl.watchdog import Watchdog  # noqa: E402

from .conftest import ROOT  # noqa: E402
from .test_depmap import COMPOSE  # noqa: E402


def make_tools(env, monkeypatch, tmp_path, approve=lambda exps: True):
    cfg = env.cfg
    root = tmp_path / "repo"  # docs/FMEA.md is written under cfg.root: keep the real repo untouched
    root.mkdir()
    for d in ("fixes", "scenarios", "config"):
        (root / d).symlink_to(ROOT / d)
    cfg.root = root
    monkeypatch.setenv("CHAOS_DEMO_DIR", str(tmp_path / "demo"))
    (tmp_path / "demo").mkdir()
    health = {t: {"state": "running", "status": "Up 5 minutes (healthy)"} for t in cfg.allowlist}

    def make_map():
        m = depmap.build_map(cfg, COMPOSE, [{"from": "frontend", "to": "cart", "call_count": 9}])
        write_json(cfg.artifacts_dir / "dependency-map.json", m)
        return m

    def run_baseline():
        d = baseline.run(cfg, env.journeys, duration_s=3, context={}, warmup_s=0.5, health=lambda: health,
                         load=lambda: {"state": "running", "user_count": 5}, progress=lambda m: None)
        return json.loads((d / "summary.json").read_text())

    def orchestrator(base, progress):
        def start_wd(run_dir: Path):
            t = threading.Thread(target=Watchdog(cfg, run_dir, env.ctx).run, daemon=True)
            t.start()
            return t
        return Orchestrator(cfg, env.ctx, env.journeys, base, watchdog_starter=start_wd, cfg_path=Path("/nonexistent"),
                            progress=progress, explain=True)

    return agents.Tools(cfg=cfg, ctx=env.ctx, make_map=make_map, run_baseline=run_baseline,
                        orchestrator=orchestrator, approve=approve, echo=lambda s: None)


def click(cfg, decision, seen):
    """A human on the dashboard: wait for the approval card, then click Approve/Decline (POST -> agents.decide)."""
    def loop():
        for _ in range(3000):
            ap = (agents.current_mission(cfg) or {}).get("approval") or {}
            if ap.get("status") == "pending":
                seen.append(ap)
                assert agents.decide(cfg, ap["id"], decision)
                return
            time.sleep(0.05)
    t = threading.Thread(target=loop, daemon=True)
    t.start()
    return t


def test_mission_all_agents_priority_rollback_and_verified_fix(env, monkeypatch, tmp_path):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    tools = make_tools(env, monkeypatch, tmp_path)
    seen = []
    click(env.cfg, "apply", seen)
    m = agents.Mission(tools, agents.Options(use_ai=False, approval="ask", max_tests=5, fault_seconds=3))
    final = m.run()
    cfg = env.cfg

    # human in the loop: the fix was proposed with the analyst's recommendations and applied only after approval
    assert len(seen) == 1 and seen[0]["fix"]["id"] == "cart-store-persistence" and seen[0]["recommendations"]
    assert agents.current_mission(cfg)["approval"]["status"] == "apply"

    # the Safety Officer's tripwire proved the automatic stop
    trip = next(r for r in final["results"] if r["source"] == "safety-test")
    assert trip["outcome"] == "watchdog_abort" and trip["rollback_verified"] and "PASSED" in trip["remediation"]

    # every agent ran and finished
    snap = agents.current_mission(cfg)
    assert snap["status"] == "done", snap.get("stop_reason")
    assert {n for n, a in snap["agents"].items() if a["status"] in ("done", "stopped")} == {a[0] for a in agents.AGENTS}

    # priority order and coverage: every fault type tested
    tests = [r for r in final["results"] if r.get("source") not in ("remediation", "safety-test")]
    types = {read_json(cfg.runs_dir / r["run_id"] / "experiment.json")["experiment"]["faults"][0]["fault_type"]
             for r in tests}
    assert types == {"container-stop", "container-pause", "network-delay", "cpu-throttle", "traffic-spike"}
    assert [r["priority"] for r in tests] == sorted(r["priority"] for r in tests)

    # every injected fault was undone and the undo verified; nothing left behind
    assert all(r["rollback_verified"] for r in final["results"] if r["injected"])
    assert Registry(cfg.state_dir).list() == []
    assert env.docker.netem == {} and all(c["status"] == "running" for c in env.docker.containers.values())

    # the crash of the in-memory cart store lost carts -> vetted fix applied -> re-test proves it -> fix kept
    assert len(final["fixes"]) == 1
    fx = final["fixes"][0]
    assert fx["fix_id"] == "cart-store-persistence" and fx["verdict"] == "fixed", fx
    before = next(r for r in final["results"] if r["run_id"] == fx["before_run"])
    after = next(r for r in final["results"] if r["run_id"] == fx["after_run"])
    assert before["data_lost"] and not after["data_lost"]
    assert env.docker.persistent["valkey-cart"] is True
    assert (tmp_path / "demo" / "compose.chaos-fix.yaml").exists()
    fm = read_json(cfg.artifacts_dir / "fmea.json")
    assert any((r.get("fix") or {}).get("verdict") == "fixed" for r in fm["rows"])

    # coordination trail and report
    trail = [json.loads(x) for x in (cfg.artifacts_dir / "missions" / m.mission_id / "agents.jsonl").read_text().splitlines()]
    order = [t["agent"] for t in trail if t.get("status") == "working"]
    assert [t["decision"] for t in trail if t["agent"] == "human"] == ["apply"]  # the human decision is on record
    assert order[:3] == ["cartographer", "sentinel", "planner"] and order[-1] == "reporter"
    assert order.count("injector") == order.count("recovery") == len([r for r in final["results"] if r["injected"]])
    assert "## Fixes" in (cfg.artifacts_dir / "missions" / m.mission_id / "report.md").read_text()


def test_mission_human_declines_fix_keeps_rollback(env, monkeypatch, tmp_path):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    tools = make_tools(env, monkeypatch, tmp_path)
    seen = []
    click(env.cfg, "skip", seen)
    final = agents.Mission(tools, agents.Options(use_ai=False, approval="ask", max_tests=2, fault_seconds=3,
                                                 tripwire=False)).run()
    assert seen and final["fixes"] == [] and env.docker.persistent.get("valkey-cart") is not True
    r = next(r for r in final["results"] if "valkey-cart" in r["experiment"])
    assert r["rollback_verified"] and "human declined" in r["remediation"]


def test_mission_stops_on_kill_switch_and_declined_plan(env, monkeypatch, tmp_path):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    tools = make_tools(env, monkeypatch, tmp_path, approve=lambda exps: False)
    final = agents.Mission(tools, agents.Options(use_ai=False, fault_seconds=3)).run()
    assert final["stop_reason"] == "operator declined the test plan" and final["results"] == []

    safety.engage_kill(env.cfg, "test")
    tools.approve = lambda exps: True
    final = agents.Mission(tools, agents.Options(use_ai=False, reuse=True, fault_seconds=3)).run()
    assert final["stop_reason"] == "kill switch engaged" and Registry(env.cfg.state_dir).list() == []
    safety.reset_kill(env.cfg)


def test_database_suite_error_map_and_results_report(env, monkeypatch, tmp_path):
    from chaosctl import dashboard, pages
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    tools = make_tools(env, monkeypatch, tmp_path)
    seen = []
    click(env.cfg, "apply", seen)
    final = agents.Mission(tools, agents.Options(use_ai=False, approval="ask", suite="database", max_tests=6,
                                                 fault_seconds=3, tripwire=False)).run()
    cfg = env.cfg
    tests = [r for r in final["results"] if r.get("source") == "database-suite"]
    # only database errors, on datastores, each labelled, in risk order
    assert len(tests) == 6 and all(r["target"] in cfg.datastores and r["db_error"] for r in tests)
    assert [r["priority"] for r in tests] == sorted(r["priority"] for r in tests)
    # the map has a measured failure % per database error, and per journey
    rows = pages.db_error_map(cfg)
    assert rows and all(r["error_pct"] is not None and r["journeys"] for r in rows)
    crash = next(r for r in rows if r["datastore"] == "valkey-cart" and r["db_error"].startswith("crash"))
    assert crash["error_pct"] > 0 and crash["journeys"]["cart"]["impacted"]
    assert crash["data_lost"] == "5/5" and crash["after_fix"]["data_lost"] == "0/5"  # before vs after the approved fix
    # results report: findings, what the system did, recommendations with evidence, the human decision
    html = dashboard.index_html(cfg)
    assert "Database error map" in html and "Human-in-the-loop decisions" in html
    assert "What we recommend you do" in html and "E" in html and "operator on the dashboard" in html
    rep = read_json(next((cfg.artifacts_dir / "missions").glob("*/report.json")))
    assert rep["decisions"] and rep["decisions"][0]["decision"] == "apply"
    assert any(x["evidence"] for r in rep["results"] for x in r.get("recommendations") or [])
