---
name: build-fault
description: Implement exactly one requested fault adapter with target/parameter validation, bounded duration, cancellation, idempotent rollback, event logging and watchdog/kill-switch integration. No live injection.
disable-model-invocation: true
---
# /build-fault <fault_type>

## Inputs
Fault type named in invocation; `docs/SAFETY.md`; existing orchestrator/safety modules.

## Workflow
1. Read SAFETY bounds for this fault. If TBD, stop and report.
2. Implement adapter interface: `validate(target, params)`, `inject()`, `rollback()` (idempotent, safe to call repeatedly / when nothing injected), `status()`.
3. Enforce: allowlist + sandbox identity check, parameter bounds, hard TTL inside the adapter, cancellation via kill switch, rollback in `finally`, registration with startup reconciler.
4. Emit structured events (JSONL): `ts, run_id, fault, target, action, result`.
5. Write unit tests with the Docker/tc layer mocked: invalid target, out-of-bound params, TTL expiry, rollback after exception, double rollback.
6. Run lint + tests; record command and result.

## Output
Adapter source + tests; STATE.md entry with test evidence.

## Stop when
Tests pass (or failures reported verbatim). **Do not inject live faults.** Do not implement a second fault.
