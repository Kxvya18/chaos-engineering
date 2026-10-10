#!/usr/bin/env bash
# One command for the whole platform: the shop (OpenTelemetry Demo + monitoring) and the chaos platform
# (agents + dashboard) as containers.
#   scripts/deploy.sh up      start everything, then open http://127.0.0.1:8090/mission
#   scripts/deploy.sh status  containers + dashboard health
#   scripts/deploy.sh logs    chaos platform logs
#   scripts/deploy.sh down    stop the chaos platform (the shop keeps running; `scripts/demo.sh down` stops it)
set -euo pipefail
REPO_DIR="$(cd "$(dirname "$0")/.." && pwd)"
export CHAOS_DEMO_DIR="${CHAOS_DEMO_DIR:-$HOME/chaos/opentelemetry-demo}"
CC=(docker compose -f "$REPO_DIR/deploy/compose.chaosctl.yaml")
case "${1:-}" in
  up)
    docker info >/dev/null 2>&1 || { echo "Docker is not running: start Docker Desktop and retry"; exit 1; }
    "$REPO_DIR/scripts/demo.sh" up
    docker build -q -t chaosctl-netem:local "$REPO_DIR/tools/netem" >/dev/null   # network-delay helper image
    mkdir -p "$REPO_DIR/runs" "$REPO_DIR/artifacts" "$REPO_DIR/state"
    "${CC[@]}" up --detach --build --wait
    echo "Platform up. Dashboard: http://127.0.0.1:8090/mission  (shop: http://localhost:8080)" ;;
  status) "${CC[@]}" ps; curl -fsS http://127.0.0.1:8090/api/status && echo ;;
  logs)   "${CC[@]}" logs -f --tail 200 ;;
  down)   "${CC[@]}" down ;;
  *) echo "usage: scripts/deploy.sh up|status|logs|down"; exit 2 ;;
esac
