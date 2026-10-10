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
