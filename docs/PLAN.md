# PLAN — Milestone 1 (Day 1) — PROVISIONAL, not agreed

Status legend: **[proposed]** = recommendation awaiting team agreement; **[TBD]** = depends on open questions.

## 1. How we stand out (differentiators)
Most teams will do: compose app → `docker kill` → Grafana screenshot → LLM summary. We differentiate on
rigor and on framing impact in business terms. Each pillar maps to a slide requirement, so it is not gold-plating.

| # | Differentiator | What it is | Slide requirement it strengthens | Day |
|---|---|---|---|---|
| D1 | **Journey-centric blast radius** | Map user journeys (e.g. browse / add-to-cart / checkout) onto services, in the spirit of TM Forum SID customer-facing vs resource-facing services (external concept). SLIs and abort limits are per journey, so findings read "checkout unavailable 42 s", not "container exited". | Dependency map; blast radius; findings | 1 (map), 2 (scoring) |
| D2 | **Evidence-graded dependency map** | Every edge tagged `verified-static` (config/source file:line), `verified-runtime` (observed traffic) or `assumed`. Discrepancies shown. | "Manual corrections allowed"; occluded "semi-automatic" mapping hint | 1 |
| D3 | **Fail-safe by construction** | (a) hard TTL inside each fault adapter, (b) independent watchdog process with its own probe — stale telemetry = abort, (c) kill switch via command + sentinel file + Ctrl-C, (d) startup reconciler removes orphaned faults. A deliberate **tripwire experiment** proves auto-abort on Day 1. | Safety limits, auto-stop, kill switch | 1 |
| D4 | **Pre-registered hypotheses + run ledger** | Plan (incl. hypothesis) is hashed before execution; each run is an evidence bundle (plan, events.jsonl, raw metrics, analysis). Anyone can re-run analysis from raw data. Directly answers "never fabricate". | Findings report from real output | 1 (ledger), 2 (AI hypotheses) |
| D5 | **AI with deterministic fallback** | Runtime AI ranks experiments via schema-validated JSON; if AI is unavailable or output invalid, a graph-based ranker produces a plan. Demo cannot die on an API hiccup. | AI planner; safety independent of AI | 2 (design on Day 1) |
| D6 | **Defensible resilience score** | Explicit formula, e.g. per-journey SLO-budget consumed + recovery time vs target + detection, weights documented. | "Ideally a resilience score" | 2 |

Trade-off: D1/D4 cost ~1–2 h extra on Day 1 but are what judges can't get from other teams. D6 and stretch goals wait for Day 2.

## 2. Approaches (max two)
| | **A — OpenTelemetry Demo (Astronomy Shop) on Docker Compose** [proposed if hardware allows] | **B — Docker example-voting-app on Docker Compose** [fallback] |
|---|---|---|
| Repo | `open-telemetry/opentelemetry-demo` | `dockersamples/example-voting-app` |
| Why | Many services across languages, built-in load generator and tracing → runtime-verified map (D2) and journey SLIs (D1) nearly free; AI ranking is meaningful with many targets | ~5 components (web, Redis queue, worker, Postgres, results); very light; easy to reason about async queue + DB failures |
| Cost | Heavy: needs a machine with several GB free RAM for Docker; longer startup; more to understand | Small graph → AI ranking looks trivial; we must build load + metrics ourselves |
| Watch-out | Ships its own feature-flag failure scenarios — we will **not** use those as our injection, to keep faults ours and transparent | Less "cloud-native" impressiveness |
| To verify before choosing | License, current resource requirement, compose startup on our hardware | License, still builds/runs |

Both: faults via Docker API (crash), `tc netem` in the target's network namespace via a helper container (latency/loss), load-generator rate step (traffic spike). Kubernetes (kind) rejected for Day 1: more moving parts for the same requirements. (Upstream details above come from general knowledge and must be verified against each repo before we commit.)

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
| Laptop RAM/CPU too small for approach A | Day-1 slip | Switch to B immediately (decide in first hour) |
| No Docker daemon where Claude Code runs (true for the cloud session today) | Claude can't run live checks | Run the app on a team laptop; Claude Code locally there, or start a daemon in a VM we control |
| `tc netem` needs NET_ADMIN / image lacks iproute2 | F2 blocked | helper container with NET_ADMIN sharing target netns; else Toxiproxy; else swap F2 for `docker pause` (freeze) |
| Runtime AI access/budget unclear | Day-2 planner/analysis blocked | deterministic fallback ranker (D5); smaller/cheaper model for planning |
| Single point of failure: one laptop hosts everything | demo lost | push run bundles frequently; second machine can replay analysis from ledger |
| Over-scoping differentiators | miss Day-1 minimum | D1–D4 are minimal on Day 1 (tags/fields, not features); cut D1 to one journey if behind |
| Empty GitHub repo: first push sets default branch | wrong default | create `main` first, set as default (owner approval) |

## 9. Open questions (answer these to finalize)
1. **Team & time:** team size, who codes (Python/Docker comfort), exact Day-1 / Day-2 times and submission cut-off? Any judging criteria shared?
2. **Hardware:** where will the app run (laptop OS, RAM, CPU), is Docker Desktop/Engine installed, can we use Linux `tc`/NET_ADMIN?
3. **App:** OK with A (OTel Demo) with B as fallback, or do you already have an app in mind?
4. **Runtime AI:** which provider/model can the running system call (API key available? budget cap?), and which Claude plan for development?
5. **Slides:** can you share the missing sixth slide and confirm the occluded step-1 text on the problem slide?

## Decisions log
| Decision | Status |
|---|---|
| Sample app | TBD |
| Stack (Compose + Python) | proposed |
| Day-1 faults F1, F2, tripwire | proposed |
| Metrics + recovery definition | proposed (SAFETY.md) |
| Runtime AI provider/model | TBD |
| Team responsibilities | TBD |
| GitHub default branch `main` | proposed |
