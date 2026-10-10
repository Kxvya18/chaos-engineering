# Milestone 1 (Day 1) — runbook in the slide's exact order

**Scope.** The Day-1 slide itself requires no AI. The team decided that the *AI chooses what to break* from Day 1, because the problem statement is "an AI system that intelligently introduces controlled failures". The AI experiment planner and AI findings are Day-2 milestones. Here we deliver the
six Day-1 rows below, done properly. Optional extras that preview Day 2 are listed separately at the end and are not
part of the Day-1 flow.

**How to run.** From the `chaosctl` repo on the Mac, with Docker Desktop running (`docker info` must work):
`scripts/m1.sh 0` → `1` → `2` → `3` → `4` → `5` → `6`. Each step saves its output to `evidence/m1-step<N>.log`.
Paste one line at a time. zsh does not treat `#` as a comment by default.

| # | Slide milestone | Slide's expected outcome | `scripts/m1.sh` runs | Pass criteria (what you must see) | Evidence |
|---|---|---|---|---|---|
| 0 | (preparation) | — | `python -m pytest -q`, `chaosctl selftest` | All tests pass. Every simulated safety case PASS | `evidence/m1-step0.log`, `artifacts/safety-checks.md` |
| 1 | Setup and app selection | Sample app chosen and running end to end in the sandbox | `demo.sh install/up/ps/stats`, `chaosctl setup`, `chaosctl doctor` | All demo containers `running` (health checks `healthy`). The shop works at http://localhost:8080. `doctor`: no FAIL rows | `evidence/m1-step1.log` |
| 2 | Application mapping | Services, dependencies and **data flows** mapped (manual corrections allowed) | `chaosctl map` (corrections: `config/map-corrections.toml`) | Map with each edge's **data flow** (sync request, async Kafka event, SQL database, key-value store) and evidence (config, live traces or both). Datastore and Kafka edges are confirmed from the caller's database/messaging trace spans. Monitoring and operator-UI edges are kept off the customer path. Gaps are listed. On the 3.1.0 config, expect: no health check on `accounting`, `cart`, `fraud-detection`; `accounting → astronomy-db` without `depends_on` | `docs/DEPENDENCIES.md`, `artifacts/dependency-map.json`, dashboard `/map` |
| 3 | Monitoring baseline | Normal **error rate, latency and health** captured under steady load | `chaosctl baseline --duration 300` (plus 15 s warm-up, excluded) | A table per journey (samples, error rate, p50/p95/p99, req/s) and container **health at start and end**. Every quality check `[PASS]`, ending in **STEADY STATE ACCEPTED** | `artifacts/baseline.json`, `runs/*-baseline/` |
| 4 | First failure injection | At least one failure (e.g. **container crash** or **network delay**) triggered and observed | `chaosctl autopilot --max 2` | 1) The **AI writes an FMEA**: every injectable failure mode with predicted Severity/Occurrence/Detection and RPN. 2) It **selects** the riskiest/most informative failures, with a reason and a hypothesis for each. 3) After you confirm, the engine injects each one safely, with live lines every 2 s. 4) For each: impact, time to restore, **health-check blind spot**, and where/how/why with industry-pattern fixes. 5) The FMEA row is updated with **measured** S/D/RPN | `artifacts/fmea.json`, `docs/FMEA.md`, `runs/<id>/` (incident.md) |
| 5 | Safety controls | **Automatic stop or rollback on threshold breach**, tested at least once | `run --tripwire`, then a live run stopped with `chaosctl kill` | Tripwire: `abort=A1_blast_radius_…` with the fault rolled back by itself. Kill switch: the run ends `kill_switch` and `status` shows `active_faults: []` | `runs/<id>-TRIPWIRE-…/ABORT.json`, `events.jsonl` |
| 6 | Day-1 checkpoint | Short demo: app running, one safe failure injected, impact visible | `chaosctl dashboard` | http://127.0.0.1:8090 opens on the **Day-1 checklist**, each row ✓/✕ computed from evidence, plus `/map`, `/baseline` and `/runs`. The run page shows the hypothesis, KPIs, the blast-radius table and per-journey charts with the fault window | screen recording |

**Day-1 minimum (slide):** a running app (1), a dependency map (2), and one safe failure injection (4) with a working kill
switch (5). All are covered, and the baseline (3) is required before any injection.

## What each failure demonstrates (observed, not interpreted by AI)
| Failure | Slide example | Target | What we observe |
|---|---|---|---|
| S1 cart datastore crash | container crash | `valkey-cart` (the cart store) | Cart and checkout fail and the other journeys stay OK, which shows the blast radius. Docker restarts the container by itself, so we measure time to restore. Sentinel carts are checked before and after: are customers' carts still there? |
| S2 slow product catalog | network delay | `product-catalog` (+400 ms) | Latency spreads to browse, product, recommendations, cart and checkout. Home and ads are unaffected. Latency returns to normal after rollback |
| Tripwire | (safety test) | `product-catalog` (+1500 ms) declared as "no journey may suffer" | The watchdog detects impact outside the allowed blast radius, **stops the experiment and undoes it automatically** |

## Optional extras (Day-2 preview, not required today)
| Extra | Command |
|---|---|
| Incident analysis of a run (AI if `ANTHROPIC_API_KEY` is set, otherwise rule-based) | `chaosctl explain runs/<id>` |
| Apply the persistence fix, re-run S1, compare before/after | `scripts/m1.sh fix` |
| Dashboard as a container on the demo network | `docker compose -f deploy/compose.chaosctl.yaml up -d --build` |

## Why this order is safe
In step 4 the watchdog is already active: abort rules A1–A6, the time limit on each fault, and the dead-man's switch.
Step 5 then proves the automatic stop and the kill switch on purpose. Step 0 proves every safety rule in simulation
before anything touches the app.
