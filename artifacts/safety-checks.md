# Safety checks

Generated 2026-10-10T07:17:46+00:00 by `chaosctl selftest` at commit `b1af682-dirty`.

Mode: **SIMULATED** (fake Docker, fake app, fake Locust; real orchestrator, watchdog, registry, adapters and safety code). Timing is shortened for simulation (config overrides in `src/chaosctl/selftest.py:FAST`).

| Case | Result | Evidence |
|---|---|---|
| Allowlist enforcement | PASS | protected target rejected (SafetyError: target 'frontend-proxy' is protected); unknown target rejected (SafetyError: target 'some-other-team-db' is not in the allowlist) |
| Invalid parameters | PASS | SafetyError: delay_ms=5000 outside 0..2000; SafetyError: duration 999s exceeds max 120s for container-stop; SafetyError: users must be an int in 1..50 |
| Sandbox identity check | PASS | remote context refused (SafetyError: docker context endpoint 'tcp://10.0.0.5:2376' is not a local unix socket); foreign project refused (SafetyError: target 'cart' belongs to project 'someone-else', expected 'opentelemetry-demo') |
| Threshold abort (tripwire, A1) | PASS | watchdog abort A1_blast_radius_latency at +0.8s after inject; netem removed; registry empty; recovered=True in 0.0s |
| Kill switch | PASS | fault active 1.0s of a planned 20s, then kill switch rolled it back; `ad` running; registry empty |
| Rollback after exception | PASS | outcome 'inject_failed: docker pause valkey-cart failed: simulated failure of pause'; events: watchdog_ready > pre_start > steady_state_check > inject_start > inject_failed > rollback > rollback_complete > recovery_watch_start > target_state > target_state > recovered > run_end |
| Timeout / TTL (A5) | PASS | fault past expires_at rolled back by watchdog (A5); `email` running again |
| Stale telemetry (A3) | PASS | no samples for 3s -> A3 abort + rollback |
| Dead orchestrator (A4) | PASS | orchestrator heartbeat stale -> A4_orchestrator_dead rollback, watchdog exits |
| Steady-state gate | PASS | pre-phase error rate 1.00 > limit -> nothing injected |
| No injection without a running watchdog | PASS | watchdog did not report in -> run aborted before any injection |
| AI unavailable + plan tamper check | PASS | AI disabled -> rule-based plan with 4 experiments, fault types ['container-stop', 'cpu-throttle', 'network-delay', 'traffic-spike']; tampered plan refused (SafetyError: plan experiments changed after pre-registration (sha256 mismatch)) |
| Live tripwire on sandbox (`chaosctl run --tripwire`) | NOT RUN | requires the Mac sandbox |
| Live kill switch on sandbox (`chaosctl kill` during a run) | NOT RUN | requires the Mac sandbox |
