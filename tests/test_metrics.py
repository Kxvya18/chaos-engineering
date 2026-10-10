from chaosctl import metrics


def s(ts, ok=True, lat=10.0, journey="home"):
    return {"ts": ts, "journey": journey, "ok": ok, "status": 200 if ok else 500, "latency_ms": lat, "error": None}


def test_percentile_nearest_rank_on_raw_values():
    vals = list(range(1, 101))
    assert metrics.percentile(vals, 50) == 50
    assert metrics.percentile(vals, 95) == 95
    assert metrics.percentile(vals, 99) == 99
    assert metrics.percentile([], 95) is None


def test_overall_p95_pools_raw_samples_not_averaged_percentiles():
    a = [s(i, lat=10, journey="a") for i in range(19)] + [s(19, lat=1000, journey="a")]
    b = [s(i, lat=10, journey="b") for i in range(20)]
    summ = metrics.summarize(a + b)
    assert summ["journeys"]["a"]["p95_ms"] == 10
    assert summ["overall"]["p95_ms"] == 10  # pooled 40 samples: rank 38 -> 10ms
    assert summ["overall"]["error_rate"] == 0


def test_recovery_time_requires_consecutive_good_windows():
    base = {"overall": {"error_rate": 0, "p95_ms": 10}, "journeys": {"home": {"error_rate": 0, "p95_ms": 10}}}
    rec = {"window_s": 1, "consecutive": 3, "error_rate_pp": 5, "p95_factor": 1.5, "p95_slack_ms": 50, "timeout_s": 30}
    bad = [s(100 + i * 0.25, ok=False) for i in range(8)]          # 100-102 failing
    good = [s(102 + i * 0.25) for i in range(40)]                  # 102-112 healthy
    assert metrics.recovery_time(bad + good, 100.0, base, ["home"], rec, 112) == 2.0
    assert metrics.recovery_time(bad, 100.0, base, ["home"], rec, 102) is None


def test_missing_samples_are_not_recovery():
    base = {"overall": {"error_rate": 0, "p95_ms": 10}}
    assert not metrics.window_ok([], base, ["home"], 5, 1.5, 50)
