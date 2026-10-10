# Demo scripts

## Day-1 checkpoint demo (≈ 2 min, no AI)
Have open: dashboard (`scripts/m1.sh 6`) and a terminal. Run everything once beforehand so all six checklist rows are ✓.

| Time | Show | Say |
|---|---|---|
| 0:00–0:15 | Dashboard home: **Day-1 checklist** (6 ✓ rows) | "Every row comes from evidence files and live container state, not ticked by hand." |
| 0:15–0:35 | `/map` | "Services, dependencies and data flows, left to right: requests in blue, Kafka events in orange, databases in green. Solid lines are confirmed by config *and* live traces. It also found gaps: three services with no health check." |
| 0:35–0:55 | `/baseline` | "Five minutes of normal behaviour under steady load: error rate, p50/p95/p99 and container health at start and end. The steady state is accepted only if every quality check passes." |
| 0:55–1:30 | Terminal: `chaosctl run --scenario cart-store-crash` (live lines), then its run page | "We crash the cart store. Cart and checkout go red, the other five journeys stay green, so the blast radius is contained. Docker restarts it, and we measure time to restore. The cart check shows whether customers' carts survived." |
| 1:30–1:50 | Tripwire run page (`abort=A1…`) | "Safety: when damage spreads beyond the declared blast radius, the independent watchdog stops the experiment and undoes it by itself." |
| 1:50–2:00 | Dashboard KILL SWITCH button | "And one button, or `chaosctl kill`, rolls back everything immediately." |

# Final demo (≤ 3 minutes, Day 2)

Record it with the dashboard open in the browser, plus a terminal for `chaosctl run`. Do it after the full runbook works,
so the "before" run already exists. Times are targets.

| Time | Show | Say (short) | Evidence on screen |
|---|---|---|---|
| 0:00–0:15 | Shop at http://localhost:8080 and the Jaeger dependency graph | "A real cloud-native shop: around 20 services, a database, a cache, a queue, running only in our sandbox." | live shop |
| 0:15–0:35 | `docs/DEPENDENCIES.md` (Mermaid map, drift list) | "We map it automatically and check every link against live traces. Gaps show up here, like a cart store with no disk." | drift list |
| 0:35–0:50 | Scenario file `scenarios/cart-store-crash.json` | "We write the hypothesis down and fingerprint it **before** breaking anything." | hypothesis, sha256 in `experiment.json` |
| 0:50–1:30 | Terminal: `chaosctl run --scenario cart-store-crash` (live lines) + dashboard live tiles | "We crash the cart database the way an out-of-memory kill would. Cart and checkout go red, everything else stays green. That's the blast radius. Docker restarts it… health is green again… but 5 of 5 carts are gone." | `>>> INJECTED`, `restarts=1`, `5 lost` |
| 1:30–2:10 | Run page: AI incident analysis | "The AI read only the evidence: container timeline, error traces, logs, config. Where: valkey-cart → cart. How: crash, restart, data loss. Why: memory-only state. Code checked every citation; the fixes come from industry patterns with references." | where/how/why cards, fixes table, method line |
| 2:10–2:40 | `scripts/demo.sh fix cart-store-crash`, re-run, compare page | "We apply the P1 fix, persistence, and re-run the same fingerprinted experiment: 0 carts lost, lower severity." | comparison table |
| 2:40–3:00 | Tripwire run page (ABORT) + kill-switch button | "Safety: the watchdog stops any experiment that spreads beyond its declared blast radius, here within seconds. And one button undoes everything." | `ABORT.json`, abort reason A1 |

Backup if the AI is unavailable during recording: the method line says "rule-based fallback". The structure and the
fixes are still produced, and the citation checks still run. Say so; don't hide it.
