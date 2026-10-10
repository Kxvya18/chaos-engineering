# Requirements (interpreted from 5 slides + team brief .docx)

Source of truth: the Bosch slides (photos) and the team brief. Items marked *(inferred)* are our interpretation.

## Mandatory
| # | Requirement | Source |
|---|---|---|
| M1 | Public repo, open-source licensed, containerized (ideally multi-service), deployable via Docker Compose or Kubernetes (minikube/kind), runs entirely in our sandbox | Guidelines slide |
| M2 | Map services, dependencies, data flows (manual corrections allowed) | Problem + Day 1 slides |
| M3 | Baseline: normal error rate, latency, health under steady load | Day 1 slide |
| M4 | Inject controlled failures; stop automatically when impact exceeds thresholds (error rate, latency, blast radius) | Problem slide step 3 |
| M5 | Automatic stop or rollback on threshold breach, tested at least once | Day 1 slide |
| M6 | Working kill switch | Day 1 minimum |
| M7 | AI ranks/selects experiments using the dependency map, short reason each | Day 2 slide |
| M8 | ≥3 failure types run; errors, slowdowns, recovery time recorded | Day 2 slide |
| M9 | AI explains findings, ranks weaknesses by severity, recommends fixes | Day 2 slide |
| M10 | Five deliverables via one submission form/portal | Deliverables slide |
| M11 | Out of scope: real/production systems, anything outside own sandbox, anything affecting others/shared infra | Guidelines slide |

## Desirable
- Resilience score ("ideally"), with an explicit, defensible method.
- Feature freeze, bug fixes, clean run from scratch; submit 30 min early.

## Stretch
- Re-run after applying a fix and show improvement.
- Live dashboard / simple UI.
- Combined failures (e.g., traffic spike while a service is down).

## Example failure types (pick ≥3)
Service/container crash · slow or lossy network · full disk · overloaded database · failed dependency · sudden traffic spike.

## Gaps and unreadable text (do not guess)
| Gap | Detail |
|---|---|
| Missing slide | Brief refers to six slides; five were provided. |
| Occluded text | Problem slide, step 1 body ("Pick any free, open-so… cloud-n… can be … from a … Map… and data… or semi-a…") is blocked by the presenter. Likely about mapping automatically or semi-automatically — **unconfirmed**. |
| Occluded label | Day 2 slide last row label partly hidden ("…mission"); read as "Submission" from context. |
| Judging criteria | Not on any slide. Scoring weights unknown. |
| Deadlines | Exact Day 1/Day 2 times and submission cut-off not given. |
| Runtime AI | No provider/model/budget constraint stated; whether paid APIs are allowed is unknown. |
| Sandbox | Whether a cloud VM counts as "own sandbox" vs. local laptop is unstated. |
| Blast radius | Named as an abort dimension but not defined; we define it in `docs/SAFETY.md`. |
