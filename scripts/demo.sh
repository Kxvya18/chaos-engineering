#!/usr/bin/env bash
# Wrapper around the OpenTelemetry Demo compose stack with the exact flags chaosctl expects.
# Usage: scripts/demo.sh install|up|down|ps|stats|logs <service>
set -euo pipefail
DEMO_DIR="${CHAOS_DEMO_DIR:-$HOME/chaos/opentelemetry-demo}"
DEMO_TAG="3.1.0"
COMPOSE=(docker compose -p opentelemetry-demo --env-file .env --env-file .env.override
         -f compose.yaml -f compose.full.yaml -f compose.observability.yaml)

case "${1:-}" in
  install)
    if [ -d "$DEMO_DIR/.git" ]; then echo "already cloned: $DEMO_DIR"; else
      mkdir -p "$(dirname "$DEMO_DIR")"
      git clone --depth 1 --branch "$DEMO_TAG" https://github.com/open-telemetry/opentelemetry-demo.git "$DEMO_DIR"
    fi
    cd "$DEMO_DIR"
    grep -q '^DEMO_VERSION=' .env.override 2>/dev/null || echo "DEMO_VERSION=$DEMO_TAG" >> .env.override
    git -C "$DEMO_DIR" describe --tags --always ;;
  up)    cd "$DEMO_DIR" && "${COMPOSE[@]}" up --detach --remove-orphans ;;
  down)  cd "$DEMO_DIR" && "${COMPOSE[@]}" down ;;
  ps)    cd "$DEMO_DIR" && "${COMPOSE[@]}" ps --format 'table {{.Name}}\t{{.State}}\t{{.Status}}' ;;
  stats) docker stats --no-stream --format 'table {{.Name}}\t{{.MemUsage}}\t{{.CPUPerc}}' ;;
  logs)  cd "$DEMO_DIR" && "${COMPOSE[@]}" logs --tail 100 "${2:?service name}" ;;
  *) echo "usage: $0 install|up|down|ps|stats|logs <service>"; exit 2 ;;
esac
