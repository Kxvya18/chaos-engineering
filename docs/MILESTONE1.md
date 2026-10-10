# Milestone 1 (Day 1) — runbook in the slide's exact order

**Scope.** Day 1 needs no AI. The AI experiment planner and AI findings are Day-2 milestones. Here we deliver the
six Day-1 rows below, done properly. Optional extras that preview Day 2 are listed separately at the end and are not
part of the Day-1 flow.

**How to run.** From the `chaosctl` repo on the Mac, with Docker Desktop running (`docker info` must work):
`scripts/m1.sh 0` → `1` → `2` → `3` → `4` → `5` → `6`. Each step saves its output to `evidence/m1-step<N>.log`.
Paste one line at a time. zsh does not treat `#` as a comment by default.

| # | Slide milestone | Slide's expected outcome | `scripts/m1.sh` runs | Pass criteria (what you must see) | Evidence |
|---|---|---|---|---|---|
| 0 | (preparation) | — | `python -m pytest -q`, `chaosctl selftest` | All tests pass. Every simulated safety case PASS | `evidence/m1-step0.log`, `artifacts/safety-checks.md` |
| 1 | Setup and app selection | Sample app chosen and running end to end in the sandbox | `demo.sh install/up/ps/stats`, `chaosctl setup`, `chaosctl doctor` | All demo containers `running` (health checks `healthy`). The shop works at http://localhost:8080. `doctor`: no FAIL rows | `evidence/m1-step1.log` |
| 2 | Application mapping | Services, dependencies and **data flows** mapped (manual corrections allowed) | `chaosctl map` | Map with each edge's **data flow** (sync request, async Kafka event, SQL database, key-value store) and evidence (config, live traces or both). Gaps listed | `docs/DEPENDENCIES.md`, `artifacts/dependency-map.json` |
| 3 | Monitoring baseline | Normal **error rate, latency and health** captured under steady load | `chaosctl baseline --duration 300` | A table per journey (samples, error rate, p50/p95/p99, req/s) and container **health at start and end**. Every quality check `[PASS]`, ending in **STEADY STATE ACCEPTED** | `artifacts/baseline.json`, `runs/*-baseline/` |
| 4 | First failure injection | At least one failure (e.g. **container crash** or **network delay**) triggered and observed | `run --scenario cart-store-crash` then `run --scenario catalog-latency` | Live status lines every 2 s with `>>> INJECTED`. Expected journeys show `ERR`/`DEG` while the others stay `OK`. The target container's state changes (S1: `exited` → `running`, restarts +1), then `<<< ROLLED BACK` and recovery. S1 also reports the cart-durability result. The summary line gives impact, time to restore and blast radius | `runs/<id>-S1-…/`, `runs/<id>-S2-…/` |
| 5 | Safety controls | **Automatic stop or rollback on threshold breach**, tested at least once | `run --tripwire`, then a live run stopped with `chaosctl kill` | Tripwire: `abort=A1_blast_radius_…` with the fault rolled back by itself. Kill switch: the run ends `kill_switch` and `status` shows `active_faults: []` | `runs/<id>-TRIPWIRE-…/ABORT.json`, `events.jsonl` |
| 6 | Day-1 checkpoint | Short demo: app running, one safe failure injected, impact visible | `chaosctl dashboard` | http://127.0.0.1:8090: the run page shows the hypothesis, KPIs, the blast-radius table and per-journey charts with the fault window | screen recording |

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
