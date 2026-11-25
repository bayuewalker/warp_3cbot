#!/usr/bin/env bash
# Runs every local gate in one pass and prints a one-line result per step. Loopback only, no production access.
# Usage: scripts/qa/run_suites.sh [--skip-browser]
# Needs: python3 with httpx, fastapi, uvicorn, playwright; node 22; ruff (python3 -m ruff or uvx ruff); a Chrome at
# CHROMIUM_PATH (default /usr/bin/google-chrome). Exit status is the number of failed steps.
set -u
cd "$(dirname "$0")/../.."
export PYTHONPATH="src:web/qa"
export CHROMIUM_PATH="${CHROMIUM_PATH:-/usr/bin/google-chrome}"
LOG="${QA_LOG_DIR:-/tmp/p5-qa-logs}"
mkdir -p "$LOG"
fails=0

step() {
  local name="$1"; shift
  if "$@" >"$LOG/$name.log" 2>&1; then
    printf 'PASS  %-24s %s\n' "$name" "$(tail -n 3 "$LOG/$name.log" | tr '\n' ' ' | cut -c1-110)"
  else
    printf 'FAIL  %-24s see %s/%s.log\n' "$name" "$LOG" "$name"
    fails=$((fails + 1))
  fi
}

step unittest      python3 -m unittest discover -s tests
step ruff          python3 -m ruff check .
step tsc           bash -c 'cd web && npx tsc --noEmit'
step vite-build    bash -c 'cd web && npm run build'
step node-tests    bash -c 'cd web && npm test'
step bundle        python3 scripts/qa/bundle_report.py --expect-index-kb 352.93
step secret-scan   python3 scripts/qa/secret_scan.py
step diff-review   python3 scripts/qa/diff_review.py

if [ "${1:-}" != "--skip-browser" ]; then
  for suite in account_reads telemetry shell room opportunities control_room accounts activity \
               p5_sse p5_a11y p5_layout p5_flows p5_perf; do
    step "$suite" python3 "web/qa/$suite.py"
  done
  step layout-audit python3 web/qa/account_reads.py --layout "${QA_LAYOUT_DIR:-/tmp/p5-layout-shots}"
fi

echo "failed steps: $fails (logs in $LOG)"
exit "$fails"
