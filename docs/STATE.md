# STATE

_Last updated: 2026-10-10 — app chosen (OTel Demo), main + CI set up._

## Completed
- Project context created: CLAUDE.md, docs/*, 9 manual project skills, `.claude/settings.json` (model sonnet, effortLevel medium).
- GitHub auth verified via session GitHub integration as `Kxvya18`; remote `Kxvya18/chaos-engineering` had no branches.

- Decision: Approach A (OpenTelemetry Demo 3.1.0), host Mac M2 16 GB. `main` created; CI merged (Kxvya18/chaos-engineering#1). Safety policy v1 agreed.

## Current blocker
- Open: team/deadlines, runtime AI API key (owner). Owner to switch GitHub default branch to `main`.
- Mac: 16 GB RAM, Docker available (owner-reported, not yet measured).
- Docker: client 29.8.2 + Compose v5.6.0 present in the Claude cloud container, but **no Docker daemon** (`/var/run/docker.sock` missing). Sandbox host for the app is TBD.

## Failed approaches
- None yet.

## Verification evidence
| Item | Evidence |
|---|---|
| Docker daemon | `docker ps` → "failed to connect to the docker API at unix:///var/run/docker.sock" |
| GitHub identity | GitHub integration `get_me` → login `Kxvya18`; `list_branches` → `[]` |

## Next action
On the Mac: hour-1 hardware gate (docs/PLAN.md §1) → deploy OTel Demo → `/map-app`.
