---
name: plan-chaos
description: Produce a ranked experiment plan (artifacts/experiment-plan.json) from the dependency map, measured baseline and safety policy, without executing anything.
disable-model-invocation: true
---
# /plan-chaos

## Inputs
`artifacts/dependency-map.json`, latest baseline summary (`runs/baseline-*/summary.json` or path given), `docs/SAFETY.md`.

## Workflow
1. Check inputs exist. If baseline or any SAFETY value used by a fault is TBD → plan `status: "draft"` (not executable), state why.
2. Score candidate targets by: dependents/journeys affected (graph), statefulness, missing healthcheck/retry evidence, observed baseline variance.
3. Select ≥3 distinct failure types. For each experiment: `id, rank, fault_type, target, parameters, duration_s, hypothesis, reason (≤2 sentences, cites map/baseline), abort_conditions, rollback, success_criteria, expected_blast_radius`.
4. Validate every target against the allowlist and every parameter against SAFETY bounds; reject/flag out-of-bounds.
5. Record a SHA-256 of the plan file in the plan's `meta` (pre-registration).

## Output
`artifacts/experiment-plan.json` with `meta {status: draft|executable, created, inputs, sha256}` and `experiments[]`.

## Verification
JSON parses; ≥3 distinct `fault_type`; all targets allowlisted; parameters within bounds.

## Stop when
Plan written. **Never execute experiments in this skill.** Update docs/STATE.md.
