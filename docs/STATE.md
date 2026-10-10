# STATE

_Last updated: 2026-10-10 — chaosctl implemented; verified in simulation only._

## Completed
- Project context, 9 manual skills, CI (`repo-checks`, `python`) — Kxvya18/chaos-engineering#1.
- Decision: OpenTelemetry Demo 3.1.0 on Docker Compose (Mac M2 16 GB); safety policy v1 — Kxvya18/chaos-engineering#2.
- `chaosctl` implemented (`src/chaosctl/`): dependency map, journey probes, baseline, planner (AI + rule-based),
  5 fault adapters, orchestrator, independent watchdog (A1–A6), kill switch, reconcile, analysis, AI report with
  citation checks, simulated selftest. Docs: README, ARCHITECTURE, SCORING.

## Current blocker
- No live evidence yet: everything below the simulation must run on the team Mac (no Docker daemon in the Claude cloud session).
- Owner: switch GitHub default branch to `main`; provide `ANTHROPIC_API_KEY` (spend-capped) for runtime AI on Day 2.

## Failed approaches
- None.

## Verification evidence
| Item | Evidence |
|---|---|
| Docker daemon (cloud session) | `docker ps` → "failed to connect to the docker API at unix:///var/run/docker.sock" |
| GitHub identity | GitHub integration `get_me` → `Kxvya18` |
| Lint | `ruff check .` → All checks passed (cloud session) |
| Tests | `pytest -q` → 46 passed (cloud session, simulated) |
| Safety (simulated) | `chaosctl selftest` → 11/11 PASS, live cases NOT RUN — `artifacts/safety-checks.md` |
| SDK request shape | `anthropic` 1.13.0: `beta.messages.create` accepts `fallbacks`, `output_config`, `betas` (inspected) |

## Next action (on the Mac, in order)
1. README §1: `scripts/demo.sh install && scripts/demo.sh up`, then `ps` + `stats` → paste outputs here.
2. README §2: `pytest -q`, `chaosctl selftest`, `chaosctl setup`, `chaosctl doctor`.
3. README §3: `map` → `baseline --duration 300` → `run --tripwire` → `plan --no-ai` → `run --experiment 1` → kill-switch test.
4. Commit `runs/`, `artifacts/`, `docs/DEPENDENCIES.md` produced on the Mac as Day-1 evidence (PR).
