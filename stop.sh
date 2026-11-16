#!/usr/bin/env bash
# Stop processes started by THIS project only (pid files in run/).
# The shared adb server and any scrcpy you started yourself are left alone.
#   ./stop.sh            stop project scrcpy + running demo
#   ./stop.sh --device   also stop the uiautomator2 server process on the phone
set -uo pipefail
source "$(dirname "$0")/lib.sh"

stop_pid() {  # pidfile pattern label
    local f=$1 pat=$2 label=$3 pid
    if pid_matches "$f" "$pat"; then
        pid=$(<"$f")
        kill -TERM "$pid" 2>/dev/null
        for _ in $(seq 1 20); do kill -0 "$pid" 2>/dev/null || break; sleep 0.25; done
        kill -0 "$pid" 2>/dev/null && kill -KILL "$pid" 2>/dev/null
        ok "stopped $label (pid $pid)"
        [[ $label == scrcpy ]] && STOPPED_SCRCPY=1
    else
        echo "  - $label not running"
    fi
    rm -f "$f"
}

echo "${B}Stopping project-managed processes${N}"
stop_pid "$SCRCPY_PID" scrcpy "scrcpy"
stop_pid "$DEMO_PID" "scripts/demo.py" "demo"

# give the on-phone scrcpy server time to restore settings and exit (wait only, never kill:
# other scrcpy sessions may own servers too)
if [[ ${STOPPED_SCRCPY:-0} == 1 ]] && command -v adb >/dev/null; then
    for _ in $(seq 1 10); do
        [[ -z $(adb shell pgrep -f com.genymobile.scrcpy.Server 2>/dev/null | tr -d '\r') ]] && break
        sleep 0.5
    done
fi

if [[ ${1:-} == --device ]]; then
    if pick_device >/dev/null 2>&1; then
        "$PROJECT_DIR/.venv/bin/python" -m android_auto stop-server >/dev/null 2>&1 \
            && ok "uiautomator2 server on phone stopped" || warn "could not stop uiautomator2 server"
    fi
fi
echo "  (adb server left running - it may be shared with other tools; 'adb kill-server' if you really want it gone)"
