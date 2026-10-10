"""Milestone 1 end to end in simulation: map -> baseline -> failure -> tripwire -> kill switch -> checklist."""

import copy
import threading
import time

from chaosctl import baseline, dashboard, depmap, pages, planner, safety, scenarios
from chaosctl.registry import Registry
from chaosctl.util import write_json

from .conftest import ROOT
from .test_depmap import COMPOSE


def test_milestone1_checklist_all_green(env, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    cfg = env.cfg
    cfg.root = ROOT
    health = {t: {"state": "running", "status": "Up 5 minutes (healthy)"} for t in cfg.allowlist}

    # 2 mapping
    m = depmap.build_map(cfg, COMPOSE, [{"from": "frontend", "to": "cart", "call_count": 9}])
    write_json(cfg.artifacts_dir / "dependency-map.json", m)
    # 3 baseline (simulated app, quality gate must ACCEPT)
    bdir = baseline.run(cfg, env.journeys, duration_s=3, context={}, warmup_s=0.5, health=lambda: health,
                        load=lambda: {"state": "running", "user_count": 5}, progress=lambda m: None)
    env.baseline = __import__("json").loads((bdir / "summary.json").read_text())
    assert env.baseline["quality"]["pass"], env.baseline["quality"]
    # 4 failure: S1 crash (short)
    exp, ref = scenarios.load(cfg, "cart-store-crash")
    exp = copy.deepcopy(exp)
    exp["faults"][0]["duration_s"] = 4
    env.orchestrator().run(exp, ref)
    # 5 safety: tripwire + kill switch
    trip = planner.tripwire(cfg)
    trip["faults"][0]["duration_s"] = 8
    env.orchestrator().run(trip)
    reg = Registry(cfg.state_dir)

    def press():
        for _ in range(200):
            if reg.list():
                time.sleep(0.3)
                safety.engage_kill(cfg, "test")
                return
            time.sleep(0.05)
    threading.Thread(target=press, daemon=True).start()
    env.orchestrator().run({"id": "SIM-kill", "faults": [{"fault_type": "container-stop", "target": "ad", "params": {},
                                                           "duration_s": 20}], "expected_journeys": ["ads"]})
    safety.reset_kill(cfg)

    rows = pages.milestone1(cfg, lambda: health, lambda: (True, "HTTP 200 in 12 ms"))
    assert [r["ok"] for r in rows] == [True] * 6, rows
    html = pages.checklist_html(rows)
    assert html.count("✓ done") == 6
    assert "<svg" in pages.map_html(cfg) and "key-value store" in pages.map_html(cfg)
    assert "STEADY STATE ACCEPTED" in pages.baseline_html(cfg)
    assert "Experiment runs" in dashboard.index_html(cfg)
