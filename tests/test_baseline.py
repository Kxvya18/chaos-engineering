from chaosctl import baseline, metrics


def _summary(cfg, n=150, err=0.0):
    samples = [{"ts": i * 2.0, "journey": j, "ok": (i % 100) >= err * 100, "latency_ms": 20.0}
               for j in cfg.journeys for i in range(n)]
    return {"duration_s": n * cfg.probes["interval_s"], **metrics.summarize(samples)}


def _health(cfg, state="running", status="Up (healthy)"):
    return {t: {"state": state, "status": status} for t in cfg.allowlist}


def test_good_baseline_is_accepted(cfg):
    q = baseline.quality(cfg, _summary(cfg), _health(cfg), _health(cfg), {"state": "running", "user_count": 5})
    assert q["pass"], q


def test_baseline_rejected_without_load_errors_or_health(cfg):
    good_h = _health(cfg)
    q = baseline.quality(cfg, _summary(cfg), good_h, good_h, {"state": "stopped", "user_count": 0})
    assert not q["pass"] and not q["checks"][0]["ok"]
    q = baseline.quality(cfg, _summary(cfg, err=0.05), good_h, good_h, {"state": "running", "user_count": 5})
    assert not next(c for c in q["checks"] if c["check"] == "journeys healthy at rest")["ok"]
    bad_h = {**good_h, "cart": {"state": "running", "status": "Up (unhealthy)"}}
    q = baseline.quality(cfg, _summary(cfg), good_h, bad_h, {"state": "running", "user_count": 5})
    assert not next(c for c in q["checks"] if c["check"] == "containers healthy at end")["ok"]


def test_baseline_run_excludes_warmup_and_reports_stability(cfg):
    import json

    run_dir = baseline.run(cfg, {"home": lambda: (True, 200, None)}, duration_s=1.0, context={}, warmup_s=0.5,
                           health=lambda: {t: {"state": "running", "status": "Up"} for t in cfg.allowlist},
                           load=lambda: {"state": "running", "user_count": 5}, progress=lambda m: None)
    s = json.loads((run_dir / "summary.json").read_text())
    all_samples = [json.loads(x) for x in (run_dir / "metrics.jsonl").read_text().splitlines() if x]
    assert s["warmup_s"] == 0.5 and s["journeys"]["home"]["n"] < len(all_samples)
    assert "stability" in s and s["quality"]["checks"]
