import copy

import pytest

from chaosctl import planner, report, safety
from chaosctl.util import write_json


def _inputs(cfg):
    d = cfg.artifacts_dir
    write_json(d / "dependency-map.json", {"services": [{"name": "cart", "healthcheck": False}],
                                           "edges": [{"from": "frontend", "to": "cart", "telemetry": False},
                                                     {"from": "cart", "to": "valkey-cart", "telemetry": False}],
                                           "drift": {"missing_depends_on": ["cart->valkey-cart"]}})
    write_json(d / "baseline.json", {"overall": {"error_rate": 0.0, "p95_ms": 50}})
    return d / "dependency-map.json", d / "baseline.json"


def test_rule_based_plan_is_valid_and_preregistered(cfg, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    plan = planner.make_plan(cfg, *_inputs(cfg), history=[])
    assert plan["meta"]["status"] == "executable"
    assert len({e["faults"][0]["fault_type"] for e in plan["experiments"]}) >= 3
    planner.verify_plan(plan)
    bad = copy.deepcopy(plan)
    bad["experiments"][0]["faults"][0]["target"] = "frontend-proxy"
    with pytest.raises(safety.SafetyError):
        planner.verify_plan(bad)


def test_plan_is_draft_without_baseline(cfg, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    plan = planner.make_plan(cfg, cfg.artifacts_dir / "nope.json", cfg.artifacts_dir / "nope2.json", history=[])
    assert plan["meta"]["status"] == "draft"
    with pytest.raises(safety.SafetyError):
        planner.verify_plan(plan)


def test_ai_selection_validation(cfg):
    by_id = {c["candidate_id"]: c for c in planner.candidates(cfg, {})}
    ok = [{"candidate_id": "container-stop:cart", "rank": 1, "reason": "r", "hypothesis": "h"},
          {"candidate_id": "container-pause:valkey-cart", "rank": 2, "reason": "r", "hypothesis": "h"},
          {"candidate_id": "network-delay:checkout", "rank": 3, "reason": "r", "hypothesis": "h"}]
    assert planner.validate_selection(ok, by_id) is None
    invented = ok[:2] + [{"candidate_id": "container-stop:jaeger", "rank": 3, "reason": "r", "hypothesis": "h"}]
    assert "unknown" in planner.validate_selection(invented, by_id)
    same_type = [dict(x, candidate_id=f"container-stop:{t}") for x, t in zip(ok, ["cart", "ad", "email"], strict=True)]
    assert "distinct" in planner.validate_selection(same_type, by_id)


def test_ai_plan_used_when_valid(cfg, monkeypatch):
    def fake_call(system, payload, schema, effort="medium", max_tokens=16000):
        ids = [c["candidate_id"] for c in payload["candidates"]]
        assert "traffic-spike:load-generator" in ids
        sel = [{"candidate_id": i, "rank": n + 1, "reason": "fan-in evidence", "hypothesis": "h"}
               for n, i in enumerate(["container-stop:valkey-cart", "network-delay:product-catalog",
                                      "traffic-spike:load-generator"])]
        return {"selections": sel, "strategy_note": "datastores first"}, {"model_served": "test"}
    monkeypatch.setattr(planner.ai, "structured_call", fake_call)
    plan = planner.make_plan(cfg, *_inputs(cfg), history=[])
    assert plan["meta"]["planner"]["method"] == "ai"
    assert plan["experiments"][0]["id"] == "E01-container-stop-valkey-cart"


def test_ai_plan_rejected_falls_back(cfg, monkeypatch):
    monkeypatch.setattr(planner.ai, "structured_call",
                        lambda *a, **k: ({"selections": [], "strategy_note": ""}, {}))
    plan = planner.make_plan(cfg, *_inputs(cfg), history=[])
    assert plan["meta"]["planner"]["method"] == "rule-based" and "ai_rejected" in plan["meta"]["planner"]


ANALYSIS = {"run_id": "r1", "recovery_time_s": 12.5, "recovered": True,
            "journeys": {"checkout": {"impact": {"error_rate_delta_pp": 87.5}}}}


@pytest.mark.parametrize("metric,value,ok", [
    ("recovery_time_s", "12.5", True),
    ("recovery_time_s", "12.5s", True),
    ("recovery_time_s", "20", False),
    ("journeys.checkout.impact.error_rate_delta_pp", "87.5", True),
    ("journeys.checkout.impact.missing", "1", False),
    ("recovered", "true", True),
])
def test_citation_check(metric, value, ok):
    f = {"evidence": [{"run_id": "r1", "metric": metric, "value": value}]}
    assert (report.check_citations(f, {"r1": ANALYSIS}) == []) is ok


def test_citation_check_unknown_run():
    f = {"evidence": [{"run_id": "made-up", "metric": "recovered", "value": "true"}]}
    assert report.check_citations(f, {"r1": ANALYSIS})
