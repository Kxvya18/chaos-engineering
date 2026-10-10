# STATE

_Last updated: 2026-10-10 — S1 deep-dive (crash → evidence → AI incident → fix → compare), dashboard, deployed mode; simulation only._

## Completed
- Project context, 9 manual skills, CI (`repo-checks`, `python`) — Kxvya18/chaos-engineering#1.
- Decision: OpenTelemetry Demo 3.1.0 on Docker Compose (Mac M2 16 GB); safety policy v1 — Kxvya18/chaos-engineering#2.
- `chaosctl` implemented (`src/chaosctl/`): dependency map, journey probes, baseline, planner (AI + rule-based),
  5 fault adapters, orchestrator, independent watchdog (A1–A6), kill switch, reconcile, analysis, AI report with
  citation checks, simulated selftest. Docs: README, ARCHITECTURE, SCORING.

- S1 cart-store-crash scenario + fix, S2 catalog-latency scenario, crash fault mode, cart durability check,
  evidence collector (Jaeger/logs/compose), AI incident analyst with industry pattern catalog, compare, HTML reports,
  dashboard with kill switch, container deployment, docs MILESTONE1/EXPLAINED/DEMO.
- Correction: every demo service already has `restart: unless-stopped` (an earlier claim that valkey-cart lacked it was wrong).

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
| Tests | `pytest -q` → see latest PR validation (cloud session, simulated) |
| Safety (simulated) | `chaosctl selftest` → 11/11 PASS, live cases NOT RUN — `artifacts/safety-checks.md` |
| SDK request shape | `anthropic` 1.13.0: `beta.messages.create` accepts `fallbacks`, `output_config`, `betas` (inspected) |

## Next action (on the Mac)
Run `docs/MILESTONE1.md` steps 1→6 in order, then the extra fix/re-run/compare; paste outputs; commit `runs/`,
`artifacts/`, `docs/DEPENDENCIES.md` as Day-1 evidence (PR).
