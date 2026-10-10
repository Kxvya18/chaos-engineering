# chaosctl — AI-driven chaos engineering for cloud-native resilience

Bosch hackathon project. `chaosctl` injects **controlled, bounded, reversible** failures into the
[OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) 3.1.0 (Apache-2.0) running in
Docker Compose on our own machine. It measures the impact on customer journeys and on recovery, then reports ranked
weaknesses with fixes. The AI (Claude API) only **selects experiments** and **explains findings**.
Measurement, safety enforcement, fault execution and rollback are all deterministic code.

- What makes it different: impact is measured on customer journeys, the dependency map is graded against
  evidence, safety is built into the code path, hypotheses are recorded before each run, and AI claims are
  checked against the data. See `docs/PLAN.md` §2.
- Architecture (deliverable 3): [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Safety policy: [`docs/SAFETY.md`](docs/SAFETY.md).
  Scoring: [`docs/SCORING.md`](docs/SCORING.md).

## Prerequisites (team Mac, Apple Silicon)
| Need | Check |
|---|---|
| Docker Desktop, **Memory ≥ 10 GB, CPUs ≥ 6** (Settings → Resources) | `docker version && docker compose version` |
| Python **3.11+** (macOS's bundled `python3` may be 3.9: `brew install python@3.12`) | `python3.12 --version` |
| ~20 GB free disk for images (estimate) | `df -h ~` |
| Optional: Anthropic API key with a spend cap (runtime AI) | `echo ${ANTHROPIC_API_KEY:+set}` |

## 1. Start the sample application
```bash
mkdir -p ~/chaos && cd ~/chaos
git clone https://github.com/Kxvya18/chaos-engineering.git
cd chaos-engineering
scripts/demo.sh install      # clones opentelemetry-demo 3.1.0 to ~/chaos/opentelemetry-demo, pins DEMO_VERSION=3.1.0
scripts/demo.sh up           # first pull takes several minutes
scripts/demo.sh ps           # wait until services are running/healthy
scripts/demo.sh stats        # memory per container (record in docs/STATE.md)
open http://localhost:8080/ http://localhost:8080/jaeger/ http://localhost:8080/loadgen/
```

## 2. Install chaosctl and prove the safety controls (no live faults)
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # unit + simulated end-to-end tests (use the venv's python)
chaosctl selftest            # simulated safety suite -> artifacts/safety-checks.md
chaosctl setup               # builds the tiny netem helper image (alpine + iproute2)
chaosctl doctor              # sandbox identity, containers, endpoints, kill switch, AI availability
```

## 3. Milestone 1 (Day 1), end to end in the slide's order. No AI needed
```
scripts/m1.sh 0
scripts/m1.sh 1
scripts/m1.sh 2
scripts/m1.sh 3
scripts/m1.sh 4
scripts/m1.sh 5
scripts/m1.sh 6
```
1 app running · 2 dependency + data-flow map · 3 baseline (error rate, latency, health, quality gate) ·
4 two failures: container crash (S1) and network delay (S2) · 5 automatic stop (tripwire) + kill switch · 6 dashboard demo.
Pass criteria and evidence for every step: **[`docs/MILESTONE1.md`](docs/MILESTONE1.md)**. Plain words: [`docs/EXPLAINED.md`](docs/EXPLAINED.md).

## 4. Day-2 path (later): AI planning, ≥ 3 failure types, AI findings
```bash
export ANTHROPIC_API_KEY=...          # never commit it; optional CHAOS_AI_MODEL (default claude-opus-5-5)
chaosctl plan                         # AI selects/ranks from validated candidates (falls back to rules)
chaosctl run --experiment 1           # repeat for 2, 3, ... (≥ 3 distinct fault types)
chaosctl report                       # artifacts/findings-report.md: ranked weaknesses, fixes, resilience score
```

## Outputs (evidence)
| Path | Content |
|---|---|
| `runs/<run_id>/experiment.json` | Pre-registered experiment, plan hash, baseline snapshot, identity check |
| `runs/<run_id>/metrics.jsonl` | Raw probe samples (every request) |
| `runs/<run_id>/events.jsonl` | Orchestrator and watchdog actions (inject, abort, rollback, recovery) |
| `runs/<run_id>/analysis.json`, `findings.md` | Values computed by code: impact, time to impact, recovery, severity |
| `artifacts/findings-report.md` | Ranked weaknesses + fixes + resilience score (deliverable 4) |
| `artifacts/safety-checks.md` | Safety-control evidence |
| `runs/<run_id>/evidence.json` | Jaeger error traces, container log lines, compose facts |
| `runs/<run_id>/incident.md` / `.json` | Where / how / why, with ranked fixes from the industry pattern catalog |
| `artifacts/comparison-<scenario>.md` | Before/after the fix |

## Fault types
`container-stop` (stop / kill / crash), `container-pause` (hung dependency), `network-delay` (tc netem: latency/jitter/loss),
`cpu-throttle` (overloaded service/DB), `traffic-spike` (Locust users). Bounds are in `config/sandbox.toml`
and `docs/SAFETY.md`.

## Troubleshooting
| Symptom | Fix |
|---|---|
| `doctor`: allowlisted targets missing | `scripts/demo.sh ps`; check memory pressure with `scripts/demo.sh stats` |
| `REFUSED by safety policy: kill switch engaged` | `chaosctl kill --reset` |
| `netem did not become active` | `chaosctl setup`; confirm Docker Desktop allows `NET_ADMIN` on the helper |
| `steady_state_not_met` | The app isn't healthy, or the baseline is stale. Re-run `chaosctl baseline` |
| AI notes `unavailable` | Expected without `ANTHROPIC_API_KEY`. Rule-based fallback is used and labelled |

## Cost lines
Development: Claude Code on the team's Claude Pro plan. Runtime AI: Claude API, about 2 calls per `plan`/`report`. The tokens used are
recorded in the plan and report metadata.

## Licence
Our code: **not yet chosen by the team** (no LICENSE file yet; Apache-2.0 would match the sample app). Sample app: OpenTelemetry Demo, Apache-2.0.
