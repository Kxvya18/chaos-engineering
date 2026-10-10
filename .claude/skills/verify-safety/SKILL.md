---
name: verify-safety
description: Test safety controls (allowlist, invalid params, timeout, threshold abort, kill switch, rollback, stale telemetry) in simulated mode by default and write artifacts/safety-checks.md.
disable-model-invocation: true
---
# /verify-safety [live sandbox test]

## Modes
- Default: simulated (mocked Docker/telemetry). 
- Live only if the invocation contains the exact phrase `live sandbox test` AND sandbox identity verified (SAFETY.md) AND rollback for the fault already PASSED in simulated mode. Otherwise run simulated and say why.

## Cases
| Case | Expect |
|---|---|
| Non-allowlisted target | rejected before any action |
| Invalid/out-of-bound params | rejected |
| Duration timeout | fault removed at TTL |
| Threshold abort | watchdog aborts + rollback when synthetic error rate/p95 exceeds threshold |
| Kill switch (command + sentinel + Ctrl-C) | all faults rolled back, event logged |
| Rollback after exception | rollback runs in finally |
| Stale telemetry | abort when no fresh sample within limit |
| AI unavailable | watchdog/kill switch still work |

## Output
`artifacts/safety-checks.md`: per case PASS / FAIL / NOT RUN, mode, command, evidence (output excerpt, event log path), commit SHA.

## Stop when
All cases attempted once. Never mark PASS without evidence. Update docs/STATE.md.
