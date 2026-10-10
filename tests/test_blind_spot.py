import copy

from chaosctl import health, pages, scenarios
from chaosctl.util import read_json

from .conftest import ROOT


def _s(ts, ok, j="browse"):
    return {"ts": ts, "journey": j, "ok": ok, "latency_ms": 10.0}


def test_blind_spot_counts_impact_while_platform_green(cfg):
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    samples = [_s(100 + i * 0.5, i >= 8) for i in range(16)]           # bad for the first 4 s
    platform = [{"ts": 99, "green": True}, {"ts": 102.5, "green": False}]
    bs = health.blind_spot(cfg, samples, platform, 100.0, 108.0, base)
    assert bs["customer_impact_s"] == 4.0 and bs["silent_impact_s"] == 2.0
    assert bs["customers_first_failed_at_s"] == 0.0 and bs["platform_first_not_green_at_s"] == 2.0


def test_verdict_treats_starting_and_exited_as_not_green(cfg):
    snap = {t: {"state": "running", "status": "Up 1 minute (healthy)"} for t in cfg.allowlist}
    assert health.verdict(cfg, snap)["green"]
    snap["valkey-cart"] = {"state": "running", "status": "Up 1 second (health: starting)"}
    assert not health.verdict(cfg, snap)["green"]
    snap["valkey-cart"] = {"state": "exited", "status": "Exited (137)"}
    assert "valkey-cart: exited" in health.verdict(cfg, snap)["problems"]


def test_latency_fault_is_a_full_blind_spot(env, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    env.cfg.root = ROOT
    exp, ref = scenarios.load(env.cfg, "catalog-latency")
    exp = copy.deepcopy(exp)
    exp["faults"][0]["duration_s"] = 6
    run_dir = env.orchestrator().run(exp, ref)
    bs = read_json(run_dir / "analysis.json")["health_blind_spot"]
    assert bs["customer_impact_s"] > 0
    assert bs["silent_impact_s"] == bs["customer_impact_s"] and bs["covered_pct"] == 0.0


def test_live_overlay_marks_fault_down_and_impacted(cfg):
    snap = {t: {"state": "running", "status": "Up (healthy)"} for t in cfg.allowlist}
    snap["ad"] = {"state": "exited", "status": "Exited (1)"}
    ov = pages.live_overlay(cfg, ["valkey-cart"], snap, ["cart"])
    assert ov["valkey-cart"] == "fault" and ov["ad"] == "down" and ov["cart"] == "impacted"
    assert "browse" not in ov
