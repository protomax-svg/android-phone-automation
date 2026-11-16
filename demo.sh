#!/usr/bin/env bash
# Run the read-only automation demo (Settings open/inspect/home).
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$PROJECT_DIR"

pick_device || exit 1
[[ -x .venv/bin/python ]] || { bad "Python venv missing: run 'uv venv .venv && uv pip install --python .venv/bin/python -r requirements.txt'"; exit 1; }
if pid_matches "$DEMO_PID" scripts/demo.py; then warn "demo already running (pid $(<"$DEMO_PID"))"; exit 1; fi

.venv/bin/python scripts/demo.py "$@" &
echo $! >"$DEMO_PID"
wait $!; rc=$?
rm -f "$DEMO_PID"
exit $rc
