
import pytest

from chaosctl import report
from chaosctl.util import write_json


def _inputs(cfg):
    d = cfg.artifacts_dir
    write_json(d / "dependency-map.json", {"services": [{"name": "cart", "healthcheck": False}],
                                           "edges": [{"from": "frontend", "to": "cart", "telemetry": False},
                                                     {"from": "cart", "to": "valkey-cart", "telemetry": False}],
                                           "drift": {"missing_depends_on": ["cart->valkey-cart"]}})
    write_json(d / "baseline.json", {"overall": {"error_rate": 0.0, "p95_ms": 50}})
    return d / "dependency-map.json", d / "baseline.json"


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
