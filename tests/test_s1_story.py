"""The Day-1 demo story, end to end in simulation: crash -> self-heal -> data loss -> AI/rule
analysis -> apply fix -> re-run -> measurable improvement."""

import copy

from chaosctl import compare, dashboard, htmlreport, incident, scenarios
from chaosctl.util import read_json

from .conftest import ROOT


def _s1(env):
    exp, ref = scenarios.load(env.cfg, "cart-store-crash")
    exp = copy.deepcopy(exp)
    exp["faults"][0]["duration_s"] = 5
    return exp, ref


def test_s1_before_and_after_fix(env, monkeypatch, tmp_path):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    env.cfg.root = ROOT  # scenarios/ live in the repo
    exp, ref = _s1(env)

    before = env.orchestrator().run(exp, ref)
    a = read_json(before / "analysis.json")
    assert a["outcome"] == "completed"
    assert any(a["self_healed"].values()), a["target_timeline"]          # Docker restarted valkey-cart
    assert any(r["exit_code"] == 137 for r in a["target_timeline"])     # it really crashed
    dur = a["checks"][0]
    assert dur["seeded"] == 5 and dur["lost"] == 5                      # ...and every cart was lost
    assert set(a["impacted_journeys"]) <= {"cart", "checkout"}          # blast radius contained
    assert a["unexpected_impacted_journeys"] == []
    inc = read_json(before / "incident.json")
    assert inc["analysis"]["verdict"] == "hypothesis_refuted"
    pats = [f["pattern_id"] for f in inc["analysis"]["fixes"]]
    assert pats[0] == "DATA_PERSISTENCE" and all(f["references"] for f in inc["analysis"]["fixes"])
    ev = read_json(before / "evidence.json")
    assert ev["traces"]["origin_service_votes"][0][0] == "cart"         # deepest errored span

    env.docker.persistent["valkey-cart"] = True                         # == scripts/demo.sh fix cart-store-crash
    after = env.orchestrator().run(exp, ref)
    b = read_json(after / "analysis.json")
    assert b["checks"][0]["lost"] == 0
    c, path = compare.run(before, after, tmp_path)
    assert c["verdict"] == "improved" and c["after"]["carts_lost"] == "0/5"

    page = htmlreport.render_run(env.cfg, before)
    assert "Recommended fixes" in page and "valkey-cart" in page and "<svg" in page
    assert "Before / after" in htmlreport.render_compare(c)
    assert before.name in dashboard.index_html(env.cfg)
    assert dashboard.status(env.cfg)["kill_switch"] is False


def test_ai_incident_validation_rejects_unknown_ids_and_flags_numbers(env, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    env.cfg.root = ROOT
    exp, ref = _s1(env)
    run_dir = env.orchestrator().run(exp, ref)
    items = incident.build_items(env.cfg, run_dir)
    good = {"verdict": "hypothesis_refuted", "summary": "carts lost", "how": "x", "customer_impact": "y",
            "detection_gap": "z", "verification_plan": "rerun",
            "where": {"failed_component": "valkey-cart", "propagation_path": ["valkey-cart", "cart"],
                      "impacted_journeys": ["cart"]},
            "why": {"root_cause": "no persistence", "contributing_factors": [{"factor": "f", "evidence_ids": ["E1"]}]},
            "fixes": [{"pattern_id": "DATA_PERSISTENCE", "change": "aof", "priority": "P1", "expected_effect": "e",
                       "evidence_ids": ["E2"]}]}
    assert incident.validate(good, items) == []
    bad = copy.deepcopy(good)
    bad["fixes"][0]["evidence_ids"] = ["E99"]
    assert incident.validate(bad, items)
    assert "4242" in incident.unverified_numbers("restored in 4242 s", items)

    def fake(system, payload, schema, effort="medium", max_tokens=16000, **kw):
        assert {it["id"] for it in payload["evidence"]} >= {"E1", "E2"}
        return good, {"model_served": "test-model"}
    monkeypatch.setattr(incident.ai, "structured_call", fake)
    inc = incident.explain(env.cfg, run_dir)
    assert inc["meta"]["method"] == "ai" and inc["analysis"]["fixes"][0]["references"]
    calls = []

    def bad_then_good(*a, **k):  # repair loop: the AI gets its validation errors back and corrects itself
        calls.append(k.get("messages"))
        return (bad if len(calls) == 1 else good), {"model_served": "m"}
    monkeypatch.setattr(incident.ai, "structured_call", bad_then_good)
    inc = incident.explain(env.cfg, run_dir)
    assert inc["meta"]["method"] == "ai" and inc["meta"]["ai_meta"]["attempts"] == 2
    assert "failed validation" in calls[1][-1]["content"]

    monkeypatch.setattr(incident.ai, "structured_call", lambda *a, **k: (bad, {"model_served": "m"}))
    inc = incident.explain(env.cfg, run_dir)  # never valid -> last validated answer for this run is reused
    assert inc["meta"]["method"] == "ai" and inc["meta"]["ai_meta"]["cached"] is True

    import shutil
    shutil.rmtree(env.cfg.artifacts_dir / "ai-cache")
    inc = incident.explain(env.cfg, run_dir)  # no cache either -> deterministic fallback, clearly labelled
    assert inc["meta"]["method"] == "rule-based" and "still invalid" in inc["meta"]["ai_unavailable"]
