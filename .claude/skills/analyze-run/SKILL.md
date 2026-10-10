---
name: analyze-run
description: Analyze one recorded run against baseline using code, separate observations from hypotheses, and write run findings plus update artifacts/findings-report.md.
disable-model-invocation: true
---
# /analyze-run <run_dir>

## Inputs
`<run_dir>/experiment.json`, `metrics.jsonl` (raw samples), `events.jsonl`, baseline summary.

## Workflow
1. Compute with a script (not by reading raw logs): error rate, p50/p95/p99 from **raw samples** per phase (pre, fault, post), throughput, time-to-impact, time-to-abort (if any), recovery time per SAFETY.md definition. Never average percentiles.
2. Compare to baseline and to the pre-registered hypothesis.
3. Write observations (numbers + source) separately from hypotheses (causes, fixes).
4. Severity: documented rule (proposed: journey impact × recovery time × detection), applied by code.
5. Resilience score only if the formula is documented in the report and reproducible from analysis.json.

## Outputs
`<run_dir>/analysis.json`, `<run_dir>/findings.md`; update `artifacts/findings-report.md` (ranked weaknesses: severity, evidence path, recommended fix).

## Stop when
Outputs written; any missing data reported as missing, never filled in.
