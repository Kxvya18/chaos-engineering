# Safety policy (v1, agreed 2026-10-10)

Principle: **AI proposes validated JSON; deterministic code enforces limits.** The watchdog and
kill switch run independently of AI availability. All values below live in `config/sandbox.toml`;
this document and that file must agree (the code reads only the file).

## Sandbox identity (checked before every injection)
| Check | Required value |
|---|---|
| Docker context endpoint | local `unix://` socket (Docker Desktop on the team Mac) — remote contexts are refused |
| Compose project label (`com.docker.compose.project`) | `opentelemetry-demo` |
| Target network | `opentelemetry-demo` |
| App | OpenTelemetry Demo `3.1.0`, `DEMO_VERSION=3.1.0` in `.env.override` |

## Target allowlist
- **Allowed:** ad, cart, checkout, currency, email, frontend, image-provider, payment, product-catalog,
  quote, recommendation, shipping, astronomy-db, valkey-cart, kafka, accounting, fraud-detection.
- **Protected (never a fault target):** frontend-proxy (our observation path), otel-collector, jaeger,
  prometheus, grafana, opensearch, flagd, flagd-ui, telemetry-docs, opamp-server, and every container
  outside the compose project. `load-generator` is only reachable through the `traffic-spike` adapter's Locust API.
- The demo's own `flagd` failure flags are **not used** for injection.

## Fault types and bounds
| Fault (`fault_type`) | Mechanism | Bounds | Max duration | Rollback (idempotent) |
|---|---|---|---|---|
| `container-stop` | `docker stop -t 5` (mode `stop`) or `docker kill` (mode `kill`) | 1 container | 120 s | `docker start` if not running |
| `container-pause` | `docker pause` (hung dependency) | 1 container | 60 s | `docker unpause` if paused |
| `network-delay` | `tc netem` from a helper container sharing the target's network namespace (`NET_ADMIN` on the helper only) | delay ≤ 2000 ms, jitter ≤ 500 ms, loss ≤ 30 % | 120 s | remove helper + `tc qdisc del dev eth0 root` |
| `cpu-throttle` | `docker update --cpus` (overloaded service/DB) | cpus ≥ 0.05 | 120 s | restore recorded `NanoCpus` (0 → host CPU count, equivalent to unlimited) |
| `traffic-spike` | Locust `/swarm` via `/loadgen/` | ≤ 50 users, spawn rate ≤ 10/s | 180 s | restore recorded user count |

- Max **1 active fault**; combined experiments (stretch) may hold 2 and must set `combined = true`.
- Every fault has a **hard TTL in three places**: the adapter (e.g. netem helper sleeps then deletes its qdisc),
  the orchestrator loop, and the watchdog (rolls back any registry entry past `expires_at`).

## Abort conditions (watchdog, separate process, rolling 10 s window, ≥ 5 samples)
| # | Condition | Threshold |
|---|---|---|
| A1 | **Blast radius exceeded** — a journey *not* in the experiment's `expected_journeys` degrades | error rate > baseline + 20 pp, or p95 > max(5 × baseline p95, baseline p95 + 1000 ms) |
| A2 | Global outage | error rate over all journeys > 90 % |
| A3 | Stale telemetry | no new probe sample for 10 s |
| A4 | Orchestrator dead (dead-man's switch) | orchestrator heartbeat older than 10 s |
| A5 | Duration | any fault past its `expires_at` |
| A6 | Kill switch | `state/KILL` exists |
On abort the watchdog writes `runs/<id>/ABORT.json` and rolls back every fault of that run itself.

## Kill switch
- `chaosctl kill` → creates `state/KILL`, rolls back **all** registered faults, then reconciles orphans.
- Ctrl-C in `chaosctl run` → same rollback path. New runs are refused while `state/KILL` exists
  (`chaosctl kill --reset` clears it).
- `chaosctl reconcile` (also run at the start of every command that injects) rolls back leftover registry
  entries and removes orphan `chaosctl-netem-*` helpers.

## Steady-state gate (before injection)
Pre-phase (20 s) must be within baseline: overall error rate ≤ baseline + 5 pp. Otherwise the run ends
with `steady_state_not_met` and nothing is injected.

## Recovery definition
Recovery time = time from rollback complete to the **start** of the first of **3 consecutive 5 s windows**
in which every journey has error rate ≤ baseline + 5 pp and p95 ≤ 1.5 × baseline p95 + 50 ms.
Not recovered within 180 s ⇒ `recovered = false` (recorded as a finding).

## Required tests before live use
`chaosctl selftest` (simulated, no Docker) and `pytest` cover: allowlist, invalid params, timeout,
threshold abort, kill switch, rollback after exception, stale telemetry, AI unavailable.
Results: `artifacts/safety-checks.md`. Live checks happen only via the tripwire run on the Mac.
