# Safety policy

Principle: **AI proposes validated JSON; deterministic code enforces limits.** The watchdog and
kill switch run independently of AI availability. Any value marked TBD blocks live execution.
Values marked *proposed* need team agreement before they become enforced defaults.

## Sandbox identity
| Item | Value |
|---|---|
| Host | TBD (laptop/VM name) |
| Runtime | Docker Compose (proposed) |
| Compose project name | TBD — all targets must carry this project label |
| Docker network(s) | TBD |
| Identity check | Before any fault: Docker context is local, compose project label matches, target container is in allowlist. Abort otherwise. |

## Target allowlist
Containers/services explicitly listed after `/map-app`: TBD. Never: Docker daemon/host, the
orchestrator, watchdog, load generator (except for traffic-spike), monitoring stack, any non-project container.

## Fault types
| Fault | Mechanism (proposed) | Intensity bounds | Max duration | Rollback |
|---|---|---|---|---|
| Container crash/stop | Docker API stop/kill on one container | 1 container at a time | TBD (proposed 60 s) | `docker start` / compose up for that service |
| Network latency/loss | `tc netem` in target network namespace via helper container | latency ≤ TBD ms, loss ≤ TBD % | TBD (proposed 120 s) | `tc qdisc del`; helper removed |
| Traffic spike | Load generator user/rate step | ≤ TBD × baseline rate | TBD | return to baseline rate |
| Failed dependency / overloaded DB / full disk | TBD (Day 2 candidates) | TBD | TBD | TBD |

Every fault carries a **hard TTL enforced by the injector itself**, not only by the watchdog.

## Abort thresholds (evaluated by watchdog on rolling window)
| Signal | Threshold | Window |
|---|---|---|
| Error rate | TBD (proposed: > baseline + 20 pp, or > 50 % absolute) | TBD (proposed 10 s) |
| p95 latency | TBD (proposed: > 5 × baseline p95) | TBD |
| Blast radius | TBD (proposed: unhealthy services > planned targets + direct dependents; or user journeys failing > planned) | per poll |
| Stale telemetry | No fresh probe sample for TBD (proposed 10 s) → abort | — |
| Duration | elapsed > max duration → abort | — |

## Kill switch
- Manual: single command (TBD name) **and** sentinel file (TBD path); Ctrl-C on orchestrator triggers the same path.
- Effect: stop all fault injection, run all registered rollbacks (idempotent), record event.
- Startup reconciler: on orchestrator start, detect and remove orphaned faults (e.g., leftover netem qdisc, stopped allowlisted container).

## Recovery criteria
Recovery time = time from rollback complete to the start of the first of **N** consecutive windows
where error rate ≤ baseline + δ and p95 ≤ k × baseline p95 and all target healthchecks pass.
N, window, δ, k: TBD (proposed N=3, 5 s, δ=2 pp, k=1.5). Not recovered within TBD (proposed 120 s) = finding.

## Required tests before live use
See `/verify-safety`: allowlist, invalid params, timeout, threshold abort, kill switch, rollback, stale telemetry.
