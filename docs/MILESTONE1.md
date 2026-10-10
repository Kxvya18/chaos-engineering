# Milestone 1 runbook — in the exact order of the Day-1 slide

Run everything from the `chaos-engineering` folder on the team Mac. The easiest way is the step runner:
`scripts/m1.sh 0`, then `1`, `2`, `3`, `4`, `5`, then `fix`, then `6`. It activates `.venv`, runs the commands below,
and saves each step's output to `evidence/m1-step<N>.log`. Don't paste lines that contain `#` into zsh: by default it
does not treat `#` as a comment. Docker Desktop must be running first (`docker info` must succeed).
**Status column:** `NOT RUN` until it has been executed on the Mac. Simulation results are in `artifacts/safety-checks.md`.

| # | Slide milestone | Expected outcome (slide) | Command(s) | What you should see | Evidence file | Status |
|---|---|---|---|---|---|---|
| 1 | Setup and app selection | Sample app chosen and running end to end in the sandbox | `scripts/demo.sh install` · `scripts/demo.sh up` · `scripts/demo.sh ps` · `chaosctl setup` · `chaosctl doctor` | All demo containers `running` (most `healthy`). The shop opens at http://localhost:8080. `doctor` shows PASS for docker context, containers, storefront, Locust and Jaeger | `doctor` output → STATE.md | NOT RUN |
| 2 | Application mapping | Services, dependencies and data flows mapped (manual corrections allowed) | `chaosctl map` | `services=… edges=…`, plus a drift list: e.g. `cart` with no healthcheck, `cart → valkey-cart` without `depends_on`, `valkey-cart` with no volume | `artifacts/dependency-map.json`, `docs/DEPENDENCIES.md` | NOT RUN |
| 3 | Monitoring baseline | Normal error rate, latency and health captured under steady load | `chaosctl baseline --duration 300` | 7 journeys, each with n≈150, error rate ≈ 0 and its p50/p95. Load comes from the demo's own Locust (5 users) | `artifacts/baseline.json`, `runs/*-baseline/` | NOT RUN |
| 4 | First failure injection | At least one failure (e.g. container crash) triggered and observed | `chaosctl run --scenario cart-store-crash` | Live lines every 2 s. `>>> INJECTED`. `cart`/`checkout` go `ERR`, `valkey-cart=exited` → `running (restarts=1)`. Then `cart durability: 0/5 intact, 5 lost`, followed by the incident analysis and its fixes | `runs/<id>-S1-cart-store-crash/` (`analysis.json`, `evidence.json`, `incident.md`) | NOT RUN |
| 5 | Safety controls | Automatic stop or rollback on threshold breach, tested at least once | (a) `chaosctl run --tripwire` · (b) during any run, in a 2nd terminal: `chaosctl kill` → `chaosctl status` → `chaosctl kill --reset` | (a) `abort=A1_blast_radius_latency` within ~15 s and the fault rolled back automatically. (b) The run ends `kill_switch`, with no active faults afterwards | `runs/<id>-TRIPWIRE-*/ABORT.json`, `events.jsonl` | NOT RUN |
| 6 | Day-1 checkpoint | Short demo: app running, one safe failure injected, impact visible | `chaosctl dashboard` → http://127.0.0.1:8090 | The run page shows the hypothesis, KPIs (time to restore, carts lost), AI where/how/why, ranked fixes with references, the blast-radius table and journey charts | screen recording (see `docs/DEMO.md`) | NOT RUN |

**Day-1 minimum (slide):** a running app (1), a dependency map (2), and one safe failure with a working kill switch (4 + 5b). All are covered above.

## Extra (proves competency; still Day 1)
| Step | Command | Expected |
|---|---|---|
| Apply the AI's P1 fix | `scripts/demo.sh fix cart-store-crash` | `valkey-cart` recreated with append-only persistence on a named volume |
| Re-run the same pre-registered scenario | `chaosctl run --scenario cart-store-crash` | `cart durability: 5/5 intact, 0 lost` (hypothesis). Measured on the Mac, not assumed |
| Compare | `chaosctl compare runs/<before> runs/<after>` | `artifacts/comparison-S1-cart-store-crash.md`, verdict `improved` if severity falls by more than 5 points |
| Restore original config | `scripts/demo.sh unfix` | Original demo restored |
| Deployed mode | `docker compose -f deploy/compose.chaosctl.yaml up -d --build` | Dashboard served from a container on the demo network |

## Why this order is safe
In step 4 the watchdog is already active: abort rules A1–A6, the per-fault time limit, and the dead-man's switch if the orchestrator dies.
Step 5 then *proves* the automatic stop and kill switch on purpose. The simulated proof for all safety cases (`chaosctl selftest`) is run in step 1 before anything touches the app.
