# Demo script (≤ 3 minutes)

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
