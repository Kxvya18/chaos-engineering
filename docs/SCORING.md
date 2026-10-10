# Scoring method (severity, resilience score, planner ranking)

All formulas are implemented in code and reproducible from `runs/<id>/analysis.json`.
The AI never computes these numbers. It may disagree with a computed severity, and the report shows both values.

## Inputs (per run, computed from raw `metrics.jsonl` samples)
- Per journey *j* in the fault phase: `Δerr_j` = fault error rate − baseline error rate, and `ratio_j` = fault p95 / baseline p95.
- `w_j` = journey weight (business criticality, `config/sandbox.toml [journeys.*].weight`):
  checkout 1.0, cart 0.8, home 0.7, browse 0.6, product 0.6, recommendations 0.3, ads 0.2.
- Recovery time per `docs/SAFETY.md`.
- Unexpected impact: a journey that was impacted but not in the experiment's `expected_journeys`.

## Severity score (0–100, higher = worse) — `analysis.severity()`
```
lat_j     = clamp((ratio_j − 1) / 4, 0, 1)          # 5× p95 slowdown counts as total degradation
impact    = max_j  w_j · min(1, max(Δerr_j, lat_j))
recovery  = 1 if not recovered else min(1, recovery_s / recovery.timeout_s)
blast     = 1 if any unexpected journey impacted else 0
severity  = 100 · (0.5·impact + 0.3·recovery + 0.2·blast)
```
Labels: ≥ 70 critical · ≥ 45 high · ≥ 20 medium · else low.

**Rationale.** Customer impact dominates (50 %). Slow or failed recovery is the next-biggest operational risk (30 %).
Impact that escapes the expected blast radius points to hidden coupling (20 %). Using `max` over journeys
means the worst-hit business journey decides the impact, so severity is not diluted by journeys that were unaffected.

## Resilience score (0–100, higher = better) — `report.resilience_score()`
`100 − mean(severity)` over all executed experiments. Tripwire runs (safety validation) are excluded,
and so are runs that never injected (for example when the steady-state check failed). The score depends on which
experiments were run, so the report always lists them with the hash of each `analysis.json`.

## Planner fallback ranking — `planner.score()`
```
score = 2·Σ w_j(expected journeys) + 0.5·fan_in + 1.5·[datastore] + 1·[no healthcheck]
        + 1·[datastore call without depends_on] − 3·times_already_run
```
Rule-based selection takes the best candidate per fault type until it has 3 distinct types, then the
best remaining candidates on new targets, up to 4 experiments. The AI planner chooses from the same candidates,
and its selection is rejected if it invents an id, has fewer than 3 fault types, or leaves a reason or hypothesis empty.

## Limits (known, stated in the report)
- The weights are judgement calls for a demo shop, not measured business value.
- Probes come from one client host. They measure customer-visible behaviour through `frontend-proxy`, not internal SLIs.
- On Docker Desktop, network faults act inside the Linux VM. Absolute latencies differ from production hardware.
