import pytest

from chaosctl import safety


def test_protected_and_unknown_targets_rejected(cfg):
    for target in ("frontend-proxy", "otel-collector", "jaeger", "not-ours"):
        with pytest.raises(safety.SafetyError):
            safety.validate_fault(cfg, {"fault_type": "container-stop", "target": target, "duration_s": 10})


@pytest.mark.parametrize("fault", [
    {"fault_type": "network-delay", "target": "cart", "params": {"delay_ms": 2001}, "duration_s": 10},
    {"fault_type": "network-delay", "target": "cart", "params": {"loss_pct": 31}, "duration_s": 10},
    {"fault_type": "network-delay", "target": "cart", "params": {}, "duration_s": 10},
    {"fault_type": "cpu-throttle", "target": "cart", "params": {"cpus": 0.01}, "duration_s": 10},
    {"fault_type": "container-pause", "target": "cart", "params": {}, "duration_s": 61},
    {"fault_type": "traffic-spike", "target": "cart", "params": {"users": 10}, "duration_s": 10},
    {"fault_type": "traffic-spike", "target": "load-generator", "params": {"users": 51}, "duration_s": 10},
    {"fault_type": "container-stop", "target": "cart", "params": {"mode": "rm"}, "duration_s": 10},
    {"fault_type": "disk-fill", "target": "cart", "duration_s": 10},
])
def test_out_of_bounds_rejected(cfg, fault):
    with pytest.raises(safety.SafetyError):
        safety.validate_fault(cfg, fault)


def test_combined_needs_flag_and_cap(cfg):
    f1 = {"fault_type": "container-stop", "target": "ad", "duration_s": 10}
    f2 = {"fault_type": "traffic-spike", "target": "load-generator", "params": {"users": 20}, "duration_s": 10}
    with pytest.raises(safety.SafetyError):
        safety.validate_experiment(cfg, {"id": "x", "faults": [f1, f2]})
    safety.validate_experiment(cfg, {"id": "x", "faults": [f1, f2], "combined": True})


def _w(journey, n, ok=True, lat=10.0):
    return [{"ts": i, "journey": journey, "ok": ok, "latency_ms": lat} for i in range(n)]


def test_blast_radius_only_counts_unexpected_journeys(cfg):
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    window = _w("checkout", 5, ok=False) + _w("home", 5)
    assert safety.evaluate_window(window, base, ["checkout"], cfg.watchdog) == []
    breaches = safety.evaluate_window(window, base, [], cfg.watchdog)
    assert breaches and breaches[0]["rule"] == "A1_blast_radius_error"


def test_single_stray_slow_sample_does_not_abort(cfg):
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    window = _w("home", 4) + [{"ts": 9, "journey": "home", "ok": True, "latency_ms": 5000}]
    assert safety.evaluate_window(window, base, [], cfg.watchdog) == []
    window.append({"ts": 9.5, "journey": "home", "ok": True, "latency_ms": 5000})
    assert safety.evaluate_window(window, base, [], cfg.watchdog)[0]["rule"] == "A1_blast_radius_latency"


def test_global_outage_aborts_even_when_expected(cfg):
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    window = _w("checkout", 10, ok=False)
    assert safety.evaluate_window(window, base, ["checkout"], cfg.watchdog)[0]["rule"] == "A2_global_outage"


def test_too_few_samples_no_decision(cfg):
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    assert safety.evaluate_window(_w("home", 2, ok=False), base, [], cfg.watchdog) == []


def test_kill_switch_file(cfg):
    assert not safety.kill_engaged(cfg)
    safety.engage_kill(cfg, "test")
    assert safety.kill_engaged(cfg)
    safety.reset_kill(cfg)
    assert not safety.kill_engaged(cfg)
