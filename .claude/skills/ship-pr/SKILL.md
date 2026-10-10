---
name: ship-pr
description: Implement one scoped change on a feature branch, validate, commit only related files, push, open/update a PR, run a separate review pass and apply the merge policy in docs/GITHUB.md.
disable-model-invocation: true
---
# /ship-pr <change>

## Workflow
1. `git status`; preserve unrelated work (do not stage it). Verify remote is `Kxvya18/chaos-engineering`.
2. Create/checkout feature branch (`feat/…`, `fix/…`, `docs/…`). Never the default branch.
3. Implement the scoped change only.
4. Run checks (lint, tests, schema validation — whatever exists). Record outputs.
5. `git diff` review; stage only related files; commit with clear message.
6. Push with `git push -u origin <branch>`. Open/update PR: Problem, Change, Validation (commands + results), Risks.
7. Separate review pass (like /review-pr) on the pushed SHA; fix substantive findings, re-validate.
8. Merge only if every criterion in docs/GITHUB.md holds (no CI defined → do not auto-merge; report).

## Tooling
`gh` if authenticated; otherwise the session's GitHub integration. Never ask for tokens.

## Stop when
PR is ready (or merged per policy) with evidence; or a blocker is reported. Update docs/STATE.md.
