# MVP scope

## Required (must ship)
- [ ] Open-source sample app running in our local sandbox (repo link + license + deploy steps documented).
- [ ] Services, dependencies and data-flow map (`artifacts/dependency-map.json`, `docs/DEPENDENCIES.md`), each edge tagged verified/assumed with evidence.
- [ ] Baseline under steady load: error rate, latency (p50/p95/p99), throughput, health.
- [ ] AI-ranked experiments with short reasons (`artifacts/experiment-plan.json`).
- [ ] ≥3 distinct failure types executed against the sandbox.
- [ ] Automatic abort/rollback on threshold breach (tested) + manual kill switch (tested).
- [ ] Measured impact and recovery time per experiment, from raw recorded data.
- [ ] AI findings ranked by severity with recommended fixes (`artifacts/findings-report.md`).
- [ ] README/source repo, ≤3-minute demo, architecture overview, findings report, team form.

## Desirable
- [ ] Resilience score with documented, reproducible method.

## Stretch (only after all required items are green)
- [ ] Live dashboard / simple UI.
- [ ] Combined failures (e.g., traffic spike during service outage).
- [ ] Apply a recommended fix, re-run, show before/after comparison.
