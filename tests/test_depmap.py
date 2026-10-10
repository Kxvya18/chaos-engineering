from chaosctl import depmap

COMPOSE = {"services": {
    "frontend": {"image": "otel/demo:frontend", "environment": {"CART_ADDR": "cart:7070",
                                                                "OTEL_EXPORTER_OTLP_ENDPOINT": "http://otel-collector:4317"},
                 "depends_on": {"cart": {"condition": "service_started"}}, "healthcheck": {"test": ["CMD"]}},
    "cart": {"image": "otel/demo:cart", "environment": {"VALKEY_ADDR": "valkey-cart:6379"}},
    "valkey-cart": {"image": "valkey/valkey:8", "healthcheck": {"test": ["CMD"]}},
    "otel-collector": {"image": "otel/collector"},
}}


def test_static_and_runtime_edges_are_graded(cfg):
    runtime = [{"from": "frontend", "to": "cart", "call_count": 10},
               {"from": "cart", "to": "product-catalog", "call_count": 3}]
    m = depmap.build_map(cfg, COMPOSE, runtime)
    st = {(e["from"], e["to"]): e["status"] for e in m["edges"]}
    assert st[("frontend", "cart")] == "verified-static+runtime"
    assert st[("cart", "valkey-cart")] == "verified-static"
    assert st[("cart", "product-catalog")] == "verified-runtime"
    assert "cart->valkey-cart" in m["drift"]["missing_depends_on"]
    assert "cart" in m["drift"]["no_healthcheck"]
    tele = next(e for e in m["edges"] if e["to"] == "otel-collector")
    assert tele["telemetry"]


def test_dependents_transitive(cfg):
    m = depmap.build_map(cfg, COMPOSE, None)
    assert depmap.dependents(m, "valkey-cart") == {"cart", "frontend"}
    assert "Drift and gaps" in depmap.render_markdown(m)


def test_flow_types_label_data_flows(cfg):
    m = depmap.build_map(cfg, COMPOSE, None)
    flows = {(e["from"], e["to"]): e["flow"] for e in m["edges"]}
    assert flows[("cart", "valkey-cart")] == "key-value store"
    assert flows[("frontend", "otel-collector")] == "telemetry"
    assert flows[("frontend", "cart")] == "sync request (gRPC/HTTP)"
    assert "Data flow" in depmap.render_markdown(m)


class FakeJaeger:
    def get_json(self, url, params=None):
        if params and params.get("service") == "cart":
            return {"data": [{"processes": {"p1": {"serviceName": "cart"}},
                              "spans": [{"processID": "p1", "operationName": "HGET",
                                         "tags": [{"key": "db.system", "value": "redis"}]}]}]}
        return {"data": []}


def test_datastore_edge_verified_by_client_spans(cfg):
    spans = depmap.client_span_edges(cfg, FakeJaeger(), ["cart", "frontend"])
    assert spans == [{"from": "cart", "to": "valkey-cart", "spans": 1, "attribute": "redis",
                      "example_operation": "HGET"}]
    m = depmap.build_map(cfg, COMPOSE, [], client_spans=spans)
    e = next(e for e in m["edges"] if (e["from"], e["to"]) == ("cart", "valkey-cart"))
    assert e["status"] == "verified-static+runtime"
    assert "cart->valkey-cart" not in m["drift"]["static_only"]


def test_manual_corrections_are_applied_and_recorded(cfg):
    corr = {"add": [{"from": "checkout", "to": "kafka", "reason": "async order event"}],
            "remove": [{"from": "frontend", "to": "cart", "reason": "test"}]}
    m = depmap.build_map(cfg, COMPOSE, None, corrections=corr)
    keys = {(e["from"], e["to"]): e for e in m["edges"]}
    assert ("frontend", "cart") not in keys
    assert keys[("checkout", "kafka")]["status"] == "manual"
    assert keys[("checkout", "kafka")]["flow"] == "async event (Kafka)"
    md = depmap.render_markdown(m)
    assert "added checkout → kafka: async order event" in md and "removed frontend → cart" in md


def test_feature_flag_edges_are_not_called_telemetry(cfg):
    compose = {"services": {"ad": {"image": "x", "environment": {"FLAGD_HOST": "flagd"}}, "flagd": {"image": "y"}}}
    m = depmap.build_map(cfg, compose, None)
    e = next(e for e in m["edges"] if e["to"] == "flagd")
    assert e["flow"] == "feature-flag config" and e["telemetry"]


def test_edges_from_monitoring_are_telemetry(cfg):
    compose = {"services": {"otel-collector": {"image": "c", "environment": {"POSTGRES_HOST": "astronomy-db"}},
                            "astronomy-db": {"image": "postgres:18"}}}
    m = depmap.build_map(cfg, compose, None)
    e = m["edges"][0]
    assert e["telemetry"] and e["flow"] == "telemetry (monitoring scrape)"
    assert "otel-collector->astronomy-db" not in m["drift"]["missing_depends_on"]


def test_real_otel_demo_compose(cfg):
    """Regression against the real OpenTelemetry Demo 3.1.0 compose configuration."""
    import json
    from pathlib import Path

    compose = json.loads((Path(__file__).parent / "fixtures" / "otel-demo-3.1.0-compose.json").read_text())
    m = depmap.build_map(cfg, compose, None)
    app = {(e["from"], e["to"]): e["flow"] for e in m["edges"] if not e["telemetry"]}
    assert app[("cart", "valkey-cart")] == "key-value store"
    assert app[("product-catalog", "astronomy-db")] == "database (SQL)"
    assert app[("checkout", "kafka")] == "async event (Kafka)"
    assert app[("checkout", "payment")] == "sync request (gRPC/HTTP)"
    assert app[("shipping", "quote")] == "sync request (gRPC/HTTP)"
    assert not any(src == "otel-collector" for src, _ in app)
    assert m["drift"]["no_healthcheck"] == ["accounting", "cart", "fraud-detection"]
    assert m["drift"]["missing_depends_on"] == ["accounting->astronomy-db"]
    for j in cfg.journeys.values():  # every configured journey service exists in the real app
        assert set(j.services) <= {s["name"] for s in m["services"]}, j.name
