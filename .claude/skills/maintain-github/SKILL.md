---
name: maintain-github
description: One maintenance pass over this repository's open PRs and CI — prioritize blockers, fix own branches, review teammate PRs, merge eligible PRs per policy, update STATE.md, then exit.
disable-model-invocation: true
---
# /maintain-github

## Workflow
1. Verify repo identity (`Kxvya18/chaos-engineering` only).
2. List open PRs: head SHA, CI status, mergeability, review state, dependencies between PRs.
3. Order: merge conflicts/red CI on own branches → teammate reviews → eligible merges.
4. Own branches: fix and push (validated). Teammate branches: review only; no rewrites without their authorization.
5. Merge only PRs meeting all docs/GITHUB.md criteria, verified on the current head SHA.
6. Update docs/STATE.md: actions taken (with PR links/SHAs), remaining blockers.

## Stop when
One pass complete. **No polling loops.**
