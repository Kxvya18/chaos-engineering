# chaosctl — AI-driven chaos engineering for cloud-native resilience

Bosch hackathon project. `chaosctl` injects **controlled, bounded, reversible** failures into the
[OpenTelemetry Demo](https://github.com/open-telemetry/opentelemetry-demo) 3.1.0 (Apache-2.0) running in
Docker Compose on our own machine. It measures the impact on customer journeys and on recovery, then reports ranked
weaknesses with fixes. The AI (Claude API) only **selects experiments** and **explains findings**.
Measurement, safety enforcement, fault execution and rollback are all deterministic code.

- What makes it different: an AI-written FMEA that chaos experiments then verify; a measured "hidden outage"
  (customers failing while health checks say OK); verified rollback; AI answers validated, self-corrected and
  evidence-cited; a human approves every fix, which is proven by a re-test; nine coordinated agents (LangGraph).
- Architecture (deliverable 3): [`docs/ARCHITECTURE.md`](docs/ARCHITECTURE.md). Safety policy: [`docs/SAFETY.md`](docs/SAFETY.md).
  Scoring: [`docs/SCORING.md`](docs/SCORING.md).

## Prerequisites (team Mac, Apple Silicon)
| Need | Check |
|---|---|
| Docker Desktop, **Memory ≥ 10 GB, CPUs ≥ 6** (Settings → Resources) | `docker version && docker compose version` |
| Python **3.11+** (macOS's bundled `python3` may be 3.9: `brew install python@3.12`) | `python3.12 --version` |
| ~20 GB free disk for images (estimate) | `df -h ~` |
| Optional: Anthropic API key with a spend cap (runtime AI) | `echo ${ANTHROPIC_API_KEY:+set}` |

## Quick start: whole platform + dashboard (final demo)
```bash
mkdir -p ~/chaos && cd ~/chaos
git clone https://github.com/Kxvya18/chaos-engineering.git && cd chaos-engineering
scripts/demo.sh install            # clones opentelemetry-demo 3.1.0 to ~/chaos/opentelemetry-demo (pinned)
export ANTHROPIC_API_KEY=...       # optional: without it the two AI agents use the labelled rule-based fallback
scripts/deploy.sh up               # shop + monitoring + chaos platform (agents + dashboard) as containers
open http://127.0.0.1:8090/mission # Start mission -> watch the agents -> Approve/Decline the fix -> Results
```
`scripts/deploy.sh status|logs|down`. The shop is at http://localhost:8080 (Jaeger `/jaeger/`, load generator `/loadgen/`).

## How it works: nine agents (LangGraph)
Cartographer → Baseline Sentinel → Risk Planner (AI FMEA) → for every failure, in priority order: Safety Officer →
Chaos Injector → Recovery Agent (verified rollback) → Incident Analyst (AI) → Remediation Agent (asks a human to
approve a vetted fix, re-tests, keeps it only if proven) → Reporter. The watchdog and kill switch sit outside the graph.
Details: [`docs/AGENTS.md`](docs/AGENTS.md).

## Developer setup (tests, CLI)
```bash
python3.12 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev]"
python -m pytest -q          # unit + simulated end-to-end tests, including the full agent mission
chaosctl selftest            # simulated safety suite -> artifacts/safety-checks.md
chaosctl setup               # builds the netem helper image (alpine + iproute2)
chaosctl doctor              # sandbox identity, containers, endpoints, kill switch, AI availability
```

| Command | What it does |
|---|---|
| `chaosctl mission [--reuse] [--fault-seconds 30] [--approval ask\|auto\|never]` | the full agent mission (same as the dashboard button) |
| `chaosctl decide apply\|skip` | answer the Remediation agent's approval request from a terminal |
| `chaosctl map` / `baseline` / `plan` | the Cartographer / Sentinel / Planner steps on their own |
| `chaosctl run --scenario cart-store-crash` / `--tripwire` | one experiment / the automatic-stop safety test |
| `chaosctl explain runs/<id>` / `compare` / `report` | incident analysis / before-after / cross-run findings report |
| `chaosctl kill [--reset]` / `status` / `reconcile` | emergency stop / active faults / clean leftovers |
| `chaosctl dashboard` / `export-site` | website at 127.0.0.1:8090 / static snapshot of real results |

Milestone 1 (Day 1) runbook in the slide's order: `scripts/m1.sh 0` … `6` — see [`docs/MILESTONE1.md`](docs/MILESTONE1.md).
Plain words: [`docs/EXPLAINED.md`](docs/EXPLAINED.md). Demo script: [`docs/DEMO.md`](docs/DEMO.md).

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
| `artifacts/missions/<id>/report.md`, `agents.jsonl` | Mission report and every agent's actions |
| `artifacts/fmea.json` | FMEA: predicted vs measured risk, fix outcome |

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
| Mission stops at Baseline Sentinel | The shop is not healthy yet: `scripts/demo.sh ps`, fix unhealthy containers, start again |
| After an approved fix | The cart store keeps persistence; `scripts/demo.sh unfix` restores the original configuration |

## Cost lines
Development: Claude Code on the team's Claude Pro plan. Runtime AI: Claude API: one FMEA call per mission plus one incident analysis per test (each up to 2 repair calls). The tokens used are
recorded in the plan and report metadata.

## Licence
Our code: **not yet chosen by the team** (no LICENSE file yet; Apache-2.0 would match the sample app). Sample app: OpenTelemetry Demo, Apache-2.0.
