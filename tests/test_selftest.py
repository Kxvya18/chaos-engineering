from chaosctl import config, selftest

from .conftest import ROOT


def test_full_simulated_safety_suite(tmp_path):
    cfg = config.load(ROOT / "config" / "sandbox.toml")
    out = tmp_path / "safety-checks.md"
    results = selftest.run_all(cfg, out, progress=lambda m: None)
    failed = [r for r in results if r["status"] != "PASS"]
    assert not failed, failed
    assert "NOT RUN" in out.read_text()  # live checks are never claimed by the simulation


def test_end_to_end_sim_run_analysis_and_rule_based_report(env, monkeypatch):
    from chaosctl import report

    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    exp = {"id": "SIM-E2E", "faults": [{"fault_type": "container-stop", "target": "valkey-cart", "params": {},
                                        "duration_s": 4}], "expected_journeys": ["cart", "checkout"],
           "hypothesis": "cart and checkout fail while valkey-cart is down"}
    run_dir = env.orchestrator().run(exp)
    a = (run_dir / "analysis.json").read_text()
    assert '"impacted_journeys"' in a
    r = report.run(env.cfg, use_ai=True)
    assert r["meta"]["method"] == "rule-based" and r["findings"]
    assert r["resilience_score"] is not None
    md = (env.cfg.artifacts_dir / "findings-report.md").read_text()
    assert "SIM-E2E" in md and "Resilience score" in md
