from chaosctl import faults
from chaosctl.registry import Registry


def entry(ftype, target, params=None, dur=30):
    return {"fault_id": f"t-{ftype}-{target}", "run_id": "t", "fault_type": ftype, "target": target,
            "params": params or {}, "duration_s": dur, "injected_at": 0, "expires_at": 1e12, "state": {}}


def test_netem_inject_and_idempotent_rollback(ctx):
    e = entry("network-delay", "cart", {"delay_ms": 300, "jitter_ms": 20, "loss_pct": 2})
    e["state"] = faults.inject(ctx, e)
    assert "delay 300ms 20ms loss 2.0%" in ctx.docker.netem["cart"]
    assert faults.rollback(ctx, e)["ok"]
    assert "cart" not in ctx.docker.netem and not ctx.docker.helpers
    assert faults.rollback(ctx, e)["ok"]  # second rollback is a no-op, not an error


def test_netem_helper_script_has_its_own_ttl(ctx):
    e = entry("network-delay", "cart", {"delay_ms": 100}, dur=40)
    faults.inject(ctx, e)
    run = next(c for c in ctx.docker.calls if c[0] == "run" and "-d" in c)
    assert "sleep 42" in run[-1] and "trap cleanup TERM INT" in run[-1]
    assert run[run.index("--cap-add") + 1] == "NET_ADMIN"


def test_stop_and_pause_rollback(ctx):
    for ftype in ("container-stop", "container-pause"):
        e = entry(ftype, "valkey-cart")
        e["state"] = faults.inject(ctx, e)
        assert ctx.docker.containers["valkey-cart"]["status"] in ("exited", "paused")
        assert faults.rollback(ctx, e)["ok"]
        assert ctx.docker.containers["valkey-cart"]["status"] == "running"


def test_cpu_throttle_restores_original(ctx):
    ctx.docker.containers["astronomy-db"]["nano_cpus"] = 2_000_000_000
    e = entry("cpu-throttle", "astronomy-db", {"cpus": 0.1})
    e["state"] = faults.inject(ctx, e)
    assert ctx.docker.containers["astronomy-db"]["nano_cpus"] == 100_000_000
    faults.rollback(ctx, e)
    assert ctx.docker.containers["astronomy-db"]["nano_cpus"] == 2_000_000_000


def test_cpu_throttle_unlimited_restores_to_host_cpus(ctx):
    e = entry("cpu-throttle", "cart", {"cpus": 0.2})
    e["state"] = faults.inject(ctx, e)
    faults.rollback(ctx, e)
    assert ctx.docker.containers["cart"]["nano_cpus"] == 8_000_000_000


def test_traffic_spike_restores_user_count(ctx):
    e = entry("traffic-spike", "load-generator", {"users": 40, "spawn_rate": 5})
    e["state"] = faults.inject(ctx, e)
    assert ctx.http.users == 40
    faults.rollback(ctx, e)
    assert ctx.http.users == 5


def test_reconcile_cleans_registry_and_orphan_helpers(ctx, cfg):
    reg = Registry(cfg.state_dir)
    e = entry("container-stop", "email")
    faults.inject(ctx, e)
    reg.add(e)
    orphan = entry("network-delay", "quote", {"delay_ms": 100})
    faults.inject(ctx, orphan)  # not registered: simulates a crash after starting the helper
    report = faults.reconcile(ctx, reg)
    assert not reg.list()
    assert ctx.docker.containers["email"]["status"] == "running"
    assert "quote" not in ctx.docker.netem
    assert len(report) == 2


def test_stop_rollback_tolerates_restart_policy(ctx):
    ctx.docker.containers["checkout"]["status"] = "restarting"
    calls = []
    orig = ctx.docker.state

    def state(name):
        calls.append(name)
        st = orig(name)
        if len(calls) > 1:
            ctx.docker.containers["checkout"]["status"] = "running"
        return st
    ctx.docker.state = state
    res = faults.rollback(ctx, entry("container-stop", "checkout"))
    assert res["ok"] and res["action"] == "none"
    assert ["start", "checkout"] not in ctx.docker.calls
