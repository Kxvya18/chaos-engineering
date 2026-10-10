---
name: review-pr
description: Review one PR's current diff for correctness, regressions, safety boundaries and tests; report file/line findings with the reviewed SHA; never approve own PR or merge.
disable-model-invocation: true
---
# /review-pr <number>

## Workflow
1. Fetch PR metadata and head SHA; read the diff and relevant surrounding code.
2. Check: correctness, regressions, safety (allowlist, TTL, rollback, kill switch, watchdog independence from AI), fabricated or hard-coded metrics, meaningful tests.
3. Findings: severity (blocking / non-blocking), file:line, failure scenario, suggested fix. No manufactured findings; "no findings" is valid.
4. Post findings as PR review comments when GitHub access works (comment review, never "approve" on our own PR). Include reviewed SHA.

## Stop when
Findings posted/reported with SHA. **Do not merge.**
