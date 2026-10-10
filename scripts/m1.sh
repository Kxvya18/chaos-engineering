#!/usr/bin/env bash
# Milestone 1, step by step, in the slide's order. Each step's output is also saved to evidence/m1-step<N>.log
# Usage: scripts/m1.sh 0|1|2|3|4|5|6|fix|all
set -uo pipefail
cd "$(dirname "$0")/.."
mkdir -p evidence
# shellcheck disable=SC1091
[ -z "${VIRTUAL_ENV:-}" ] && [ -f .venv/bin/activate ] && source .venv/bin/activate

banner() { printf '\n==================== %s ====================\n' "$1"; }
need_docker() { docker info >/dev/null 2>&1 || { echo "Docker is not running. Open Docker Desktop, wait for 'Engine running', then retry."; exit 1; }; }

step0() { banner "0. Tests + simulated safety proof"
  python -m pytest -q && chaosctl selftest --out evidence/safety-checks.md; }
step1() { banner "1. Setup and app selection: app running end to end"; need_docker
  scripts/demo.sh install && scripts/demo.sh up && scripts/demo.sh ps && scripts/demo.sh stats && chaosctl setup
  if ! chaosctl doctor; then
    echo; echo "STOP: fix the FAIL rows above before step 2/3 (a baseline of a broken app is not a baseline)."
    echo "Unhealthy container? try: docker logs <name> --tail 50   then   docker restart <name>"
    return 1
  fi
  echo "Open the shop: http://localhost:8080/  (Jaeger: /jaeger/  Load generator: /loadgen/)"; }
step2() { banner "2. Application mapping"; need_docker
  chaosctl map && echo "Map: docs/DEPENDENCIES.md"; }
step3() { banner "3. Monitoring baseline (steady load, ${BASELINE_S:-300} s)"; need_docker
  chaosctl baseline --duration "${BASELINE_S:-300}"; }
step4() { banner "4a. Failure 1 (slide example: container crash) - S1 cart datastore crash"; need_docker
  chaosctl run --scenario cart-store-crash
  banner "4b. Failure 2 (slide example: network delay) - S2 slow product catalog"
  chaosctl run --scenario catalog-latency; }
step5() { banner "5a. Safety: automatic stop on threshold breach (tripwire)"; need_docker
  chaosctl run --tripwire
  banner "5b. Safety: kill switch during a live run"
  chaosctl run --scenario catalog-latency &
  RUN_PID=$!
  sleep 45
  echo ">>> pressing the kill switch"
  chaosctl kill
  wait "$RUN_PID"
  chaosctl status
  chaosctl kill --reset; }
step6() { banner "6. Day-1 checkpoint demo"
  echo "Dashboard: http://127.0.0.1:8090  (Ctrl-C to stop)"
  chaosctl dashboard; }
stepfix() { banner "Optional (Day-2 preview): apply the persistence fix, re-run S1, compare"; need_docker
  # same baseline as the "before" run, so the comparison is fair
  scripts/demo.sh fix cart-store-crash && chaosctl run --scenario cart-store-crash
  BEFORE=$(ls -d runs/*-S1-cart-store-crash 2>/dev/null | tail -2 | head -1)
  AFTER=$(ls -d runs/*-S1-cart-store-crash 2>/dev/null | tail -1)
  if [ -n "$BEFORE" ] && [ "$BEFORE" != "$AFTER" ]; then chaosctl compare "$BEFORE" "$AFTER"; else
    echo "need two S1 runs to compare (run step 4 first)"; fi
  scripts/demo.sh unfix; }

run() { local s="$1"; "step$s" 2>&1 | tee "evidence/m1-step$s.log"; }
case "${1:-}" in
  0|1|2|3|4|5|fix) run "$1" ;;
  6) step6 ;;
  all) for s in 0 1 2 3 4 5; do run "$s"; done; echo; echo "Now: scripts/m1.sh 6 (demo dashboard)" ;;
  *) echo "usage: scripts/m1.sh 0|1|2|3|4|5|6|fix|all"; exit 2 ;;
esac
