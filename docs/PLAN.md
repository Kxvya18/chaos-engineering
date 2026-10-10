# PLAN — Milestone 1 (Day 1) — agreed 2026-10-10

Status legend: **[proposed]** = recommendation awaiting team agreement; **[TBD]** = depends on open questions.

## 1. Decision: Approach A — OpenTelemetry Demo 3.1.0 on Docker Compose — AGREED
Fallback B (Docker example-voting-app) only if A fails the hour-1 hardware gate below.
Host: team Mac (Apple Silicon M2) with Docker Desktop. Kubernetes rejected for Day 1.

**Hour-1 hardware gate (verify, do not assume):** Mac RAM ≥ 16 GB and Docker Desktop memory raised
enough for the demo's documented requirement; all images pull for arm64; store + load generator +
Jaeger reachable; `tc netem` works in a helper container sharing a target's network namespace inside
Docker Desktop's VM. Any FAIL → record in STATE.md and decide A-trimmed vs B within the hour.
Verified from source (tag `3.1.0`, 2026-09-18) and Docker Hub: Apache-2.0; `otel/demo:3.1.0-*` images are amd64+arm64;
compose memory limits ≈ 4.2 GB core + 3.0 GB observability + 1.1 GB full (limits, not measured usage);
UI paths `/`, `/jaeger/`, `/loadgen/` on port 8080; Locust 2.44.4 autostarts 5 users.
Static gaps visible in compose (verified by running the mapper on the 3.1.0 compose files): `cart`, `accounting` and
`fraud-detection` have no healthcheck; `accounting → astronomy-db` has no `depends_on`. (Correction: `cart` *does* declare
`depends_on: valkey-cart`, in `compose.observability.yaml`; an earlier note here said otherwise.)

## 2. The 5 best differentiators (ranked by judge impact ÷ effort)
| Rank | Differentiator | What judges see | Why it's unique | Effort | Day |
|---|---|---|---|---|---|
| 1 | **Fail-safe by construction + "safety case"** | Tripwire experiment deliberately breaches a threshold → watchdog aborts → rollback → recovery measured; kill switch pressed live; `safety-checks.md` shows every control PASS with evidence | Most teams have a kill switch; few *prove* every control. Faults self-expire (TTL in adapter), watchdog is a separate process with its own probe (dead-man's switch: stale telemetry = abort), startup reconciler removes orphaned faults | M | 1 |
| 2 | **Journey SLOs with error-budget burn-rate abort** | Impact stated as "checkout journey: 38 % failures for 42 s, 61 % of hourly error budget burned", not "container exited" | Blast radius measured in customer journeys (TM Forum customer-facing vs resource-facing service idea), abort uses SRE burn-rate, not a raw threshold | M | 1 (SLIs+abort), 2 (budget in score) |
| 3 | **Trace-verified dependency map with drift report** | Map where each edge is `verified-static` (compose/source file:line), `verified-runtime` (trace evidence) or `assumed`, plus a list of where config and reality disagree | Uses the demo's tracing as evidence instead of hand-drawn boxes; answers "manual corrections allowed" with a visible audit trail | S–M | 1 |
| 4 | **Pre-registered hypotheses + reproducible run ledger** | Before each run: hypothesis + abort rules, hashed. After: raw-data bundle; one command re-computes every number in the report | Turns "never fabricate" into something checkable — strongest anti-hallucination story | S | 1 (ledger), 2 (AI hypotheses) |
| 5 | **Citation-checked AI findings with deterministic fallback** | AI explains propagation path and fixes; every claim cites a metric/run/trace ID, and code rejects claims whose cited numbers don't match analysis.json; if AI is unavailable a graph-based ranker/plain report still works | Grounded, verified AI rather than a free-text summary; demo can't die on an API hiccup | M | 2 (schemas designed Day 1) |

Not in top 5 (Day 2 stretch, in order): fix-and-rerun before/after; combined failure (traffic spike + dependency down); live dashboard (Grafana from the demo first, custom UI last).

Trade-off: ranks 1–4 add ~2 h to Day 1 but are mostly fields, files and tests on top of work we need anyway.
If behind schedule, cut in this order: drift report (3) → burn-rate (keep simple threshold) → one journey only.

## 3. Components and data flow [proposed]
```
            ┌──────────── sandbox host (Docker) ──────────────┐
 load gen ─▶│  sample app services  ◀── fault adapters (crash, netem)
            └────────▲──────────────────────────▲─────────────┘
                     │ probes (journey SLIs)    │ inject / rollback (TTL)
            ┌────────┴────────┐        ┌────────┴──────────┐
            │ probe/metrics   │──────▶ │ orchestrator (CLI)│◀── plan.json (AI or fallback)
            │ recorder (JSONL)│        └────────▲──────────┘
            └────────┬────────┘                 │ abort signal
                     └────────▶ watchdog (separate process) ◀── kill switch (cmd / file / Ctrl-C)
                                     run ledger: runs/<run_id>/{experiment,events,metrics,analysis}
```
- Python orchestrator (Docker SDK), black-box journey probes as the primary SLI source (independent of app instrumentation), app telemetry (traces) as secondary evidence.
- Runtime AI touches only: plan.json in, findings out. Never the inject path.

## 4. Day-1 failure types and metrics [proposed]
| Fault | Target choice | Why first |
|---|---|---|
| F1 container stop/kill | a mid-graph stateless service on the checkout journey | simplest, safest rollback, visible impact |
| F2 network latency (netem) | edge into a heavily depended-on service | shows slowdowns, retry/timeout weaknesses |
| Tripwire | F2 with latency high enough to breach abort threshold | proves auto-abort + rollback (Day-1 checkpoint) |
Day 2 adds ≥1 more distinct type (traffic spike or failed/overloaded DB/dependency).

Metrics: per journey request success rate, p50/p95/p99 from raw samples, throughput, container health/restart count.
Recovery: as defined in `docs/SAFETY.md` (rollback-complete → first of N consecutive in-band windows).
Also recorded: time-to-impact, time-to-abort.

## 5. Smallest complete end-to-end prototype (Day-1 target)
`chaos baseline` (steady load, 5 min, summary.json) → `chaos run F1` (inject, observe, TTL rollback, recovery measured) →
`chaos run tripwire` (auto-abort proven) → `chaos kill` tested mid-run → map + baseline + 2 run bundles committed.
AI not required for Day-1 minimum; plan.json can be hand-written against the same schema the AI will fill on Day 2.

## 6. Day-1 sequence (timeboxes relative; real clock TBD)
| # | Task | Timebox | Output | Owner [TBD] |
|---|---|---|---|---|
| 1 | Hardware check, pick A/B, deploy app, smoke test journeys | 1.5 h | app running, deploy steps in README | |
| 2 | `/map-app` + journeys | 1 h | dependency-map.json, DEPENDENCIES.md | |
| 3 | Probe recorder + baseline (steady load) | 1.5 h | runs/baseline-*/summary.json | |
| 4 | Safety core: allowlist, watchdog, kill switch, reconciler | 1.5 h | unit tests (simulated) | |
| 5 | `/build-fault` F1, then F2 | 1.5 h | adapters + tests | |
| 6 | `/verify-safety` simulated → `live sandbox test` | 1 h | artifacts/safety-checks.md | |
| 7 | Live F1 + tripwire runs, short demo | 1 h | run bundles, demo clip | |
Parallelism (if ≥2 people): track 1–3 (app/observability) ‖ track 4–5 (safety/faults); merge at step 6.

## 7. Acceptance checks (Day 1)
- [ ] App journeys succeed under steady load from a clean `compose up`.
- [ ] Map: every service listed; every edge has evidence or `assumed` + reason.
- [ ] Baseline summary computed from raw samples, values recorded in STATE.md.
- [ ] One live fault injected, impact visible in metrics, rolled back, recovery time measured.
- [ ] Auto-abort fired on threshold breach (event log shows breach → abort → rollback).
- [ ] Kill switch stops a live run and leaves no orphaned fault (reconciler check clean).

## 8. Risks and fallbacks
| Risk | Impact | Fallback |
|---|---|---|
| Mac RAM too small / arm64 image missing for approach A | Day-1 slip | Trim A (stop non-journey services) or switch to B within hour 1 |
| `tc netem` unsupported in Docker Desktop VM | latency fault blocked | Toxiproxy in front of one dependency, else `docker pause` |
| No Docker daemon where Claude Code runs (true for the cloud session today) | Claude can't run live checks | Run the app on a team laptop; Claude Code locally there, or start a daemon in a VM we control |
| `tc netem` needs NET_ADMIN / image lacks iproute2 | F2 blocked | helper container with NET_ADMIN sharing target netns; else Toxiproxy; else swap F2 for `docker pause` (freeze) |
| Runtime AI: Claude Pro covers Claude Code, not API calls (verify) | Day-2 AI blocked | Separate Anthropic API key with a small spend cap, cheap model; deterministic fallback (rank 5) |
| Single point of failure: one laptop hosts everything | demo lost | push run bundles frequently; second machine can replay analysis from ledger |
| Over-scoping differentiators | miss Day-1 minimum | Ranks 1–4 are minimal on Day 1 (fields, files, tests); follow the cut order in §2 |
| Empty GitHub repo: first push sets default branch | wrong default | create `main` first, set as default (owner approval) |

## 9. Open questions
1. Mac RAM (8 / 16 / 24 GB)? Docker Desktop installed?
2. Team size, Python/Docker comfort, exact Day-1/Day-2 times and submission cut-off, judging criteria?
3. Runtime AI: willing to create an Anthropic API key with a spend cap (e.g. $5)?
4. Missing sixth slide / occluded step-1 text?

## Decisions log
| Decision | Status |
|---|---|
| Sample app | **OpenTelemetry Demo 3.1.0 (A), agreed** |
| Stack (Compose + Python `chaosctl`) | agreed |
| Fault adapters: container-stop, container-pause, network-delay, cpu-throttle, traffic-spike; tripwire | agreed |
| Metrics + recovery definition | agreed (SAFETY.md v1) |
| Runtime AI provider/model | Claude API `claude-opus-5-5` + deterministic fallback; API key with spend cap (owner) |
| Team responsibilities | TBD |
| GitHub default branch `main` | branch created; default switch pending owner (Settings) |
