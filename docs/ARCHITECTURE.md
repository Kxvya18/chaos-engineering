# Architecture overview (one page)

**chaosctl** is an AI-assisted chaos-engineering tool with safety controls enforced by code. It targets the
OpenTelemetry Demo 3.1.0 running in Docker Compose on the team Mac.

```mermaid
flowchart LR
  subgraph AI["Runtime AI (Claude API) — advisory only"]
    P[Planner: select + rank\npre-validated candidates]
    R[Reporter: explain + rank\nweaknesses, cite evidence]
  end
  subgraph Det["Deterministic core (Python)"]
    M[map: compose config + Jaeger\n→ evidence-graded dependency map]
    B[baseline: journey probes\nunder steady Locust load]
    C[candidate catalog\n(allowlist × bounded faults)]
    V[validate + sha256\npre-registration]
    O[orchestrator]
    A[analysis: raw samples →\nimpact, recovery, severity]
    K[citation checker]
  end
  subgraph Safety["Safety plane (independent of AI)"]
    W[watchdog process\nA1–A6]
    REG[(fault registry)]
    KS[kill switch\nchaosctl kill / Ctrl-C]
  end
  subgraph Sandbox["Docker Desktop sandbox (opentelemetry-demo project)"]
    APP[21+ demo services\n+ postgres, valkey, kafka]
    FP[frontend-proxy :8080]
    LG[Locust]
    J[Jaeger]
  end
  M --> C --> P --> V --> O
  C -.fallback rule-based.-> V
  B --> O
  O -- inject / rollback --> APP
  O -- probes --> FP --> APP
  O -- spike --> LG
  J --> M
  O --> REG
  W --> REG
  KS --> REG
  W -- abort + rollback --> APP
  O --> A --> R --> K --> REPORT[findings-report.md]
  A -.fallback rule-based.-> REPORT
```

## Components
| Component | Responsibility | Module |
|---|---|---|
| Dependency mapper | Static edges from `docker compose config` env vars and `depends_on`, runtime edges from the Jaeger dependency API. Each edge is graded, and drift is reported | `depmap.py` |
| Journey probes | 7 black-box customer journeys (home, browse, product, recommendations, ads, cart, checkout) through `frontend-proxy`, written to raw JSONL | `probes.py` |
| Planner | Catalog of bounded, allowlisted candidates. AI selects and ranks them with reasons and hypotheses. Code validates the result, and the plan is hashed | `planner.py`, `ai.py` |
| Orchestrator | Steady-state check → inject → observe → rollback → recovery watch → analysis | `orchestrator.py` |
| Fault adapters | container-stop/kill, container-pause, network-delay (netem helper), cpu-throttle, traffic-spike. Each rollback is idempotent | `faults.py` |
| Watchdog | Separate OS process. Rolling-window abort rules, TTL and dead-man's switch. Never calls AI | `watchdog.py`, `safety.py` |
| Analysis + report | Metrics computed by code. The AI writes the narrative, and every cited number is checked against `analysis.json` | `analysis.py`, `report.py` |

## Agents (LangGraph)
Nine agents share one mission state; LangGraph routes between them (`src/chaosctl/agents.py`, [`AGENTS.md`](AGENTS.md)).
Cartographer · Baseline Sentinel · Risk Planner (AI) · Safety Officer · Chaos Injector · Recovery Agent · Incident
Analyst (AI) · Remediation Agent (human approval, vetted fix, re-test) · Reporter. Only two agents call the LLM;
the watchdog runs outside the graph. Deployed as one container next to the demo (`scripts/deploy.sh up`).

## How the AI makes decisions
| Decision point | AI input | AI output (JSON schema) | What code enforces |
|---|---|---|---|
| Experiment selection | Candidates with evidence (fan-in, journey weight, datastore, missing healthcheck/`depends_on`, history), baseline, drift | `selections[{candidate_id, rank, reason, hypothesis}]` | Only known ids. 3–6 picks, ≥ 3 fault types, ranks 1..n. Parameters cannot be changed. Invalid output → rule-based ranking |
| Findings | Per-run analyses (numbers computed by code) | `findings[{title, severity, evidence[(run_id, metric, value)], observation, hypothesis, fixes}]` | Each citation is looked up in `analysis.json`. Mismatches are rejected and listed. The computed severity is shown next to the AI's |
Model: `claude-opus-5-5` (`CHAOS_AI_MODEL`), structured outputs, server-side refusal fallback. With no key or
an API error, every command still works using deterministic fallbacks.

## Safety controls
1. **Identity:** local `unix://` Docker context. Target must be in compose project `opentelemetry-demo` and on its network.
2. **Allowlist and bounds:** static validation before any action. Observability, ingress and load-generator containers are protected.
3. **Pre-registration:** the plan's sha256 is checked at `run`, so an edited plan is refused.
4. **Steady-state check:** no injection unless the pre-phase is within the baseline band.
5. **Hard TTL in 3 places:** inside the adapter (the netem helper deletes its own qdisc), the orchestrator loop, and the watchdog (A5).
6. **Watchdog (separate process):** A1 blast radius (unexpected journey degraded), A2 global outage,
   A3 stale telemetry, A4 orchestrator heartbeat lost, A5 TTL, A6 kill switch → `ABORT.json` + rollback.
7. **Kill switch:** `chaosctl kill` rolls back everything and blocks new runs. Ctrl-C triggers the same rollback.
8. **Startup cleanup:** before each injection, leftover registry entries and orphan netem helpers are rolled back.
9. **Evidence:** every action is written to `events.jsonl`, raw samples are kept, and the report lists the hash of each analysis.
