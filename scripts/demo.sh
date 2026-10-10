#!/usr/bin/env bash
# Wrapper around the OpenTelemetry Demo compose stack with the exact flags chaosctl expects.
# Usage: scripts/demo.sh install|up|down|ps|stats|logs <service>|fix <name>|unfix
set -euo pipefail
DEMO_DIR="${CHAOS_DEMO_DIR:-$HOME/chaos/opentelemetry-demo}"
DEMO_TAG="3.1.0"
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
COMPOSE=(docker compose -p opentelemetry-demo --env-file .env --env-file .env.override
         -f compose.yaml -f compose.full.yaml -f compose.observability.yaml)
# an applied fix (see `fix` below) is layered on top as one more compose file
[ -f "$DEMO_DIR/compose.chaos-fix.yaml" ] && COMPOSE+=(-f compose.chaos-fix.yaml)

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
  fix)   # apply a recommended fix as a compose override, e.g. `fix cart-store-crash`
    src="$REPO_DIR/fixes/${2:?fix name}.compose.yaml"; [ -f "$src" ] || { echo "no fix $src"; exit 2; }
    cp "$src" "$DEMO_DIR/compose.chaos-fix.yaml"
    cd "$DEMO_DIR" && docker compose -p opentelemetry-demo --env-file .env --env-file .env.override \
      -f compose.yaml -f compose.full.yaml -f compose.observability.yaml -f compose.chaos-fix.yaml up --detach --wait
    echo "fix '$2' applied" ;;
  unfix) rm -f "$DEMO_DIR/compose.chaos-fix.yaml"
    cd "$DEMO_DIR" && docker compose -p opentelemetry-demo --env-file .env --env-file .env.override \
      -f compose.yaml -f compose.full.yaml -f compose.observability.yaml up --detach --wait
    echo "fix removed (original demo configuration restored)" ;;
  *) echo "usage: $0 install|up|down|ps|stats|logs <service>|fix <name>|unfix"; exit 2 ;;
esac
