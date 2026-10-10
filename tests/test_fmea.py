import copy

from chaosctl import fmea
from chaosctl.util import read_json, write_json

from .conftest import ROOT


def _inputs(cfg):
    import json
    from pathlib import Path

    from chaosctl import depmap
    compose = json.loads((Path(__file__).parent / "fixtures" / "otel-demo-3.1.0-compose.json").read_text())
    write_json(cfg.artifacts_dir / "dependency-map.json", depmap.build_map(cfg, compose, None))
    write_json(cfg.artifacts_dir / "baseline.json", {"overall": {"error_rate": 0, "p95_ms": 50},
                                                     "quality": {"pass": True, "checks": []}})


def test_rule_based_fmea_on_real_config(cfg, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    cfg.root = ROOT
    _inputs(cfg)
    out = fmea.build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json")
    rows = out["fmea"]["rows"]
    assert all(r["rpn"] == r["severity"] * r["occurrence"] * r["detection"] for r in rows)
    assert rows == sorted(rows, key=lambda r: -r["rpn"])
    assert out["plan"]["meta"]["status"] == "executable"
    types = {e["faults"][0]["fault_type"] for e in out["plan"]["experiments"]}
    assert len(types) >= 2 and all(e["candidate_id"] for e in out["plan"]["experiments"])
    # 20 MiB memory limit (product-catalog, valkey-cart...) raises occurrence in the rule-based method
    pc = next(r for r in rows if r["component"] == "valkey-cart")
    assert pc["occurrence"] >= 5


def test_ai_fmea_validated_and_used(cfg, monkeypatch):
    cfg.root = ROOT
    _inputs(cfg)

    def fake(system, payload, schema, effort="medium", max_tokens=16000):
        ids = [c["candidate_id"] for c in payload["catalog"]]
        assert "container-stop:valkey-cart" in ids and "rating_scales" in payload
        rows = [{"candidate_id": "container-stop:valkey-cart", "effect": "cart/checkout fail; carts may be lost",
                 "affected_journeys": ["cart", "checkout"], "severity": 9, "occurrence": 6, "detection": 3,
                 "rationale": "20M memory limit, single instance"},
                {"candidate_id": "network-delay:product-catalog", "effect": "5 journeys slow",
                 "affected_journeys": ["browse"], "severity": 6, "occurrence": 5, "detection": 8, "rationale": "fan-in"}]
        sel = [{"candidate_id": r["candidate_id"], "rank": i + 1, "reason": "r", "hypothesis": "h"} for i, r in enumerate(rows)]
        return {"rows": rows, "selected": sel, "summary": "s"}, {"model_served": "test"}
    monkeypatch.setattr(fmea.ai, "structured_call", fake)
    out = fmea.build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json")
    rows = out["fmea"]["rows"]
    assert out["fmea"]["meta"]["method"] == "ai"
    assert [r["rpn"] for r in rows] == [6 * 5 * 8, 9 * 6 * 3]  # sorted by risk
    assert [e["candidate_id"] for e in out["plan"]["experiments"]] == ["container-stop:valkey-cart",
                                                                      "network-delay:product-catalog"]
    bad = lambda *a, **k: ({"rows": [{"candidate_id": "container-stop:jaeger", "effect": "", "affected_journeys": [],  # noqa: E731
                                      "severity": 11, "occurrence": 1, "detection": 1, "rationale": ""}],
                            "selected": [], "summary": ""}, {})
    monkeypatch.setattr(fmea.ai, "structured_call", bad)
    out = fmea.build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json")
    assert out["fmea"]["meta"]["method"] == "rule-based" and "ai_rejected" in out["fmea"]["meta"]


def test_draft_when_baseline_rejected(cfg, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    cfg.root = ROOT
    _inputs(cfg)
    write_json(cfg.artifacts_dir / "baseline.json", {"overall": {}, "quality": {"pass": False, "checks": []}})
    out = fmea.build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json")
    assert out["plan"]["meta"]["status"] == "draft"


def test_run_updates_fmea_with_measured_values(env, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    env.cfg.root = ROOT
    _inputs(env.cfg)
    out = fmea.run(env.cfg, use_ai=False)
    exp = copy.deepcopy(next(e for e in out["plan"]["experiments"] if e["faults"][0]["fault_type"] == "network-delay"))
    exp["faults"][0]["duration_s"] = 6
    env.orchestrator().run(exp)
    row = next(r for r in read_json(env.cfg.artifacts_dir / "fmea.json")["rows"] if r["candidate_id"] == exp["candidate_id"])
    me = row["measured"]
    assert me and me["detection"] == 10  # latency: health checks saw nothing (full blind spot)
    assert me["rpn"] == me["severity"] * row["occurrence"] * me["detection"]


def test_rule_based_selection_spreads_over_components(cfg, monkeypatch):
    monkeypatch.setenv("CHAOS_AI_DISABLED", "1")
    cfg.root = ROOT
    _inputs(cfg)
    exps = fmea.build(cfg, cfg.artifacts_dir / "dependency-map.json", cfg.artifacts_dir / "baseline.json")["plan"]["experiments"]
    targets = [e["faults"][0]["target"] for e in exps]
    assert len(set(targets)) == len(targets) == 3
    for e in exps:
        if e["faults"][0]["target"] in ("valkey-cart", "cart"):
            assert e["checks"] == ["cart_durability"]
