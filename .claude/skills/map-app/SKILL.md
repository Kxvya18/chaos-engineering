---
name: map-app
description: Map the sample app's services, dependencies and data flows into artifacts/dependency-map.json and docs/DEPENDENCIES.md, separating verified edges from assumptions.
disable-model-invocation: true
---
# /map-app

## Inputs
- Sample app path/repo (from docs/PLAN.md). Deployment config (compose/k8s manifests), service source, env vars.
- Optional runtime evidence: running containers, traces, connection tables, logs.

## Workflow
1. Read deployment config: list services, images, ports, networks, volumes, `depends_on`, healthchecks, env vars naming other hosts.
2. Grep source for outbound calls (hostnames, gRPC/HTTP clients, DB/queue URLs). Record file:line.
3. If the app is running, collect runtime evidence with a script (not by pasting raw logs): e.g. trace service graph, `docker inspect`, connection counts.
4. Define user journeys (e.g. browse, checkout) → ordered services touched.
5. Classify each edge: `verified-static` (config/source path), `verified-runtime` (observation), `assumed` (reason).
6. Write outputs; list discrepancies between static and runtime views.

## Outputs
- `artifacts/dependency-map.json`: `{app, version, services:[{name, kind, image, ports, healthcheck, stateful}], edges:[{from,to,protocol,purpose,evidence:[{type,ref}],status}], journeys:[{name, path:[...]}]}`
- `docs/DEPENDENCIES.md`: table of edges + Mermaid diagram + discrepancies + manual corrections section.

## Verification
- JSON parses; every edge has ≥1 evidence entry or status `assumed` with reason; every service in config appears.

## Stop when
Outputs written and verified. Do not inject faults or modify the app. Update docs/STATE.md.
