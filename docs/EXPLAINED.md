# What we built, in plain words

> **Day 1 vs Day 2.** Day 1 (now) is: app running, map, baseline, inject a crash and a network delay safely,
> watch the impact, and prove the automatic stop and kill switch. The AI parts below (incident analyst, fixes) belong to
> Day 2. They are built and tested in simulation, but they are **off** in the Day-1 run.

## The idea in one paragraph
We run a real online shop (the OpenTelemetry Demo: about 20 small services, a database, a cache and a queue) on our own laptop.
Our tool, **chaosctl**, breaks one part of it on purpose, safely and for a limited time, and watches what customers
would experience. Then an **AI** reads all the evidence and explains *where* it broke, *how* the failure spread and
*why*. It recommends fixes based on well-known industry patterns. We apply the top fix, break the same thing again,
and **prove** the fix worked with numbers.

## The pieces (and what each one is for)
| Piece | Simple explanation |
|---|---|
| **The shop** (OpenTelemetry Demo 3.1.0) | Our "victim" system, built from many containers. It ships with fake shoppers (Locust) and with tracing (Jaeger), which records every request's path through the services |
| **Map** (`chaosctl map`) | Draws who-calls-whom. It reads the shop's configuration, then checks it against the traces of real requests. Every arrow is marked as *seen in config*, *seen in real traffic*, or both |
| **Probes** | 7 robot customers: open the home page, browse, view a product, get recommendations, see ads, add to cart, check out. Every 2 s each one records whether it worked and how long it took. These recordings are our ground truth |
| **Baseline** | 5 minutes of probes while nothing is broken: "what normal looks like" |
| **Scenario** | A failure written down *before* we run it: what we break, what we expect to happen (the hypothesis), and why this test matters. It's fingerprinted so nobody can change it after seeing the results |
| **Fault adapters** | The actual "breaking" tools: crash a process, stop or freeze a container, slow the network, starve CPU, or flood with traffic. Each has an "undo" (rollback) that is safe to run twice |
| **Watchdog** | A separate safety guard process. It stops the experiment and undoes the fault if anything *unexpected* breaks, if everything breaks, if the data stops coming in, if the time limit passes, or if the main program dies. It never depends on the AI |
| **Kill switch** | `chaosctl kill`, Ctrl-C, or the red button on the dashboard. Undoes everything immediately and blocks new runs |
| **Evidence collector** | After a run it gathers: the container's state changes (crashed? restarted? exit code?), error traces from Jaeger, error lines from logs, and the configuration of the broken part |
| **AI incident analyst** | Claude reads only that evidence and writes where/how/why plus fixes. **Code then checks the AI.** Every piece of evidence it cites must exist, every fix must come from our catalog of industry patterns (each with public references), and any number it writes that isn't in the evidence is flagged. Without AI access, a rule-based analyst produces the same structure |
| **Dashboard** | A web page (http://127.0.0.1:8090) with a live view during a run, a full report per run, before/after comparisons, and the kill switch |

## The failure we perfected: S1 "cart datastore crash"
- **What we break:** the process inside `valkey-cart`, the small in-memory database holding every shopper's cart. We kill it
  the way the operating system kills a program that runs out of memory. That's realistic, because its memory limit is only 20 MB.
- **Why this one:** it sits under the two most valuable journeys (cart and checkout), and the map shows it has no disk to save to.
- **Hypothesis (what a resilient shop *should* do):** only cart and checkout wobble, Docker restarts the database
  within seconds, everything else stays normal, and **carts created before the crash are still there afterwards**.
- **How we test the last part:** just before the crash, the tool creates 5 "sentinel" carts with known contents. After
  recovery it reads them back.
- **What we expect to find on the Mac** (simulation shows this; the real run must confirm it):
  Docker restarts the database in seconds, the health checks go green again, and the cart and checkout journeys recover.
  **But the 5 sentinel carts are gone.** The system looks healthy and silently lost every customer's cart. That's the
  weakness: "self-healing" restored the *process*, not the *data*.
- **Expected AI analysis:** Where: valkey-cart → cart service → cart/checkout journeys. How: crash (exit 137) → restart
  → errors for N seconds → green again → data lost. Why: state kept only in memory, no persistence, no volume.
  Fixes (ranked): **P1 durable storage** (Valkey append-only file on a volume), P2 replica with failover,
  P2 graceful degradation / circuit breaker in the cart service, P3 alert on customer-journey success, not container health.
- **Proof:** `scripts/demo.sh fix cart-store-crash` turns on persistence. We re-run the *same* fingerprinted scenario and
  expect `5/5 intact`. `chaosctl compare` puts both runs side by side.

## Second, shallower failure (safety proof): the tripwire
We deliberately slow `product-catalog` while telling the watchdog that *no* journey is expected to suffer.
Browse and product slow down, which the watchdog sees as damage outside the allowed blast radius. It **stops the
experiment by itself and undoes the slowdown.** That proves the automatic stop works.

## What is "industry standard" here
| Practice | Where it comes from (external) | Where it is in our tool |
|---|---|---|
| Steady state, hypothesis, real-world events, minimal blast radius | Principles of Chaos Engineering (principlesofchaos.org) | baseline, scenario files, watchdog A1 |
| Measure customer-facing journeys, not only infrastructure | Google SRE workbook, "Alerting on SLOs" | journey probes, SLO_ALERTING fix |
| Fix patterns: retries/backoff, circuit breaker, bulkhead, graceful degradation, load shedding | AWS Builders' Library, AWS Well-Architected (Reliability), Azure Architecture Center patterns | `src/chaosctl/patterns.py` (with links) |
| Self-healing, health probes, resource limits | Kubernetes and Docker documentation | patterns + compose facts |

## Who runs what
- **Claude (this cloud session):** writes and tests the code against a full simulation, and manages GitHub (branches, PRs, CI, merges).
  It **cannot** run Docker here. Starting a Docker engine in this session was blocked as a security boundary.
- **You (the Mac):** run the commands in `docs/MILESTONE1.md`, then paste the outputs back. Claude turns them into
  evidence and fixes anything that behaves differently on the real system.

## Honest limits
- Nothing in this repo has run against the real shop yet. All results so far come from simulation.
- Latency numbers on Docker Desktop come from a Linux VM on the Mac, not production hardware.
- A container on Docker Desktop is not Kubernetes. The same patterns apply, but we haven't run on K8s.
