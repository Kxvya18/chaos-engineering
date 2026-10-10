---
name: package-demo
description: Prepare submission documents (README, DEMO, ARCHITECTURE, findings report, TEAM, SUBMISSION) and check evidence and links; does not publish or submit.
disable-model-invocation: true
---
# /package-demo

## Workflow
1. README.md: purpose, prerequisites, setup, run, kill switch, clean-run-from-scratch steps (only verified commands).
2. docs/DEMO.md: ≤3-minute script — inject → impact → abort/recovery → findings; timestamps; required evidence per scene.
3. docs/ARCHITECTURE.md: one page — components, AI decision points (inputs/outputs/validation), safety controls.
4. artifacts/findings-report.md: confirm it comes from real runs (run IDs + paths exist).
5. docs/TEAM.md: team name, members, tools, AI models (dev vs runtime), third-party libraries + licenses, data used. Unknown → `PENDING`.
6. docs/SUBMISSION.md: checklist of 5 deliverables with link/path and status; video = `PENDING` until a URL exists.
7. Check every referenced path/link exists.

## Stop when
Files updated and checklist accurate. **Do not publish, upload or submit.**
