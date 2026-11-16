#!/usr/bin/env bash
# Start the graphical phone interface (scrcpy over USB) after checking ADB.
#   ./start.sh                 mirror + control (mouse & keyboard)
#   ./start.sh --screen-off    same, but blank the phone's own display
#   ./start.sh --foreground    run scrcpy in this terminal instead of background
#   ./start.sh -- <scrcpy args>  pass extra flags straight to scrcpy
set -uo pipefail
source "$(dirname "$0")/lib.sh"

FOREGROUND=0; EXTRA=()
while (($#)); do
    case $1 in
        --screen-off) SCRCPY_SCREEN_OFF=1 ;;
        --screen-on)  SCRCPY_SCREEN_OFF=0 ;;
        --foreground) FOREGROUND=1 ;;
        --) shift; EXTRA+=("$@"); break ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) EXTRA+=("$1") ;;
    esac; shift
done

echo "${B}Checking ADB connection${N}"
pick_device || exit 1
ok "Device $DEVICE_SERIAL authorized ($(adb -s "$DEVICE_SERIAL" shell getprop ro.product.model | tr -d '\r'), Android $(adb -s "$DEVICE_SERIAL" shell getprop ro.build.version.release | tr -d '\r'))"

command -v scrcpy >/dev/null || { bad "scrcpy not installed (run ./install.sh)"; exit 1; }
if [[ -z ${WAYLAND_DISPLAY:-}${DISPLAY:-} ]]; then
    bad "No graphical session (WAYLAND_DISPLAY/DISPLAY unset) - scrcpy cannot open a window here."
    exit 1
fi

if pid_matches "$SCRCPY_PID" scrcpy; then
    warn "scrcpy already running (pid $(<"$SCRCPY_PID")). Use ./stop.sh first."
    exit 0
fi

ARGS=(--serial "$DEVICE_SERIAL" --window-title "$SCRCPY_TITLE"
      --video-bit-rate "${SCRCPY_VIDEO_BITRATE:-8M}" --video-codec "${SCRCPY_VIDEO_CODEC:-h264}")
[[ ${SCRCPY_STAY_AWAKE:-1} == 1 ]] && ARGS+=(--stay-awake)
[[ ${SCRCPY_SCREEN_OFF:-0} == 1 ]] && ARGS+=(--turn-screen-off)
[[ ${SCRCPY_MAX_SIZE:-0} != 0 ]] && ARGS+=(--max-size "$SCRCPY_MAX_SIZE")
# shellcheck disable=SC2206
[[ -n ${SCRCPY_EXTRA_FLAGS:-} ]] && ARGS+=($SCRCPY_EXTRA_FLAGS)
ARGS+=("${EXTRA[@]}")

echo "${B}Launching scrcpy${N}: scrcpy ${ARGS[*]}"
if ((FOREGROUND)); then
    exec scrcpy "${ARGS[@]}"
fi

LOG="$LOG_DIR/scrcpy.log"
for attempt in 1 2; do
    setsid scrcpy "${ARGS[@]}" >"$LOG" 2>&1 < /dev/null &
    echo $! >"$SCRCPY_PID"

    # Verify: process alive, video stream up, and (on Hyprland) a real window mapped.
    for _ in $(seq 1 30); do
        sleep 0.5
        pid_matches "$SCRCPY_PID" scrcpy || break
        if command -v hyprctl >/dev/null && [[ -n ${HYPRLAND_INSTANCE_SIGNATURE:-} ]]; then
            hyprctl clients 2>/dev/null | grep -qF "title: $SCRCPY_TITLE" && { WINDOW=1; break; }
        elif grep -q "Texture:" "$LOG"; then WINDOW=1; break; fi
    done
    # a previous on-device server still shutting down makes the first connect fail
    if ((attempt == 1)) && ! pid_matches "$SCRCPY_PID" scrcpy && grep -q "Server connection failed" "$LOG"; then
        warn "scrcpy server connection failed (previous session still closing?) - retrying"
        sleep 2; continue
    fi
    break
done

if ! pid_matches "$SCRCPY_PID" scrcpy; then
    bad "scrcpy exited. Log ($LOG):"; sed 's/^/     /' "$LOG"; rm -f "$SCRCPY_PID"; exit 1
fi
if [[ ${WINDOW:-0} == 1 ]]; then
    ok "scrcpy running (pid $(<"$SCRCPY_PID")), window '$SCRCPY_TITLE' is open"
    grep -E "Device:|Renderer:|Texture:" "$LOG" | sed 's/^/     /'
    echo "     Mouse = touch, keyboard = phone keyboard. Right-click = BACK, middle-click = HOME."
    echo "     Shortcuts use MOD=Alt (or Super): MOD+o screen off, MOD+Shift+o screen on, MOD+h home, MOD+b back."
    if [[ ${SCRCPY_SCREEN_OFF:-0} == 1 ]]; then echo "     Phone display turned OFF (mirroring/control remain active)."; fi
    if [[ $(adb -s "$DEVICE_SERIAL" shell getprop persist.security.adbinput 2>/dev/null | tr -d '\r') == 0 ]] || grep -q INJECT_EVENTS "$LOG"; then
        warn "Phone REJECTS mouse/keyboard input: enable Developer options > 'USB debugging (Security settings)' and reboot the phone."
    fi
    exit 0
else
    warn "scrcpy is running but no window was detected within 15s. Log:"; sed 's/^/     /' "$LOG"; exit 1
fi
