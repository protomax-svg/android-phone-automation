# Shared helpers for start/status/demo/stop. Sourced, not executed.
# shellcheck shell=bash

PROJECT_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
RUN_DIR="$PROJECT_DIR/run"
LOG_DIR="$PROJECT_DIR/logs"
# per-device files when ANDROID_SERIAL is set (several phones); plain names otherwise
SCRCPY_PID="$RUN_DIR/scrcpy${ANDROID_SERIAL:+-$ANDROID_SERIAL}.pid"
DEMO_PID="$RUN_DIR/demo${ANDROID_SERIAL:+-$ANDROID_SERIAL}.pid"
SCRCPY_TITLE="Android (android-automation)${ANDROID_SERIAL:+ $ANDROID_SERIAL}"
mkdir -p "$RUN_DIR" "$LOG_DIR"

# user overrides (screen-off default, bitrate, extra flags...)
[[ -f "$PROJECT_DIR/config.env" ]] && source "$PROJECT_DIR/config.env"

if [[ -t 1 ]]; then G=$'\e[32m'; R=$'\e[31m'; Y=$'\e[33m'; B=$'\e[1m'; N=$'\e[0m'; else G= R= Y= B= N=; fi
ok()   { echo "  ${G}✔${N} $*"; }
bad()  { echo "  ${R}✘${N} $*"; }
warn() { echo "  ${Y}!${N} $*"; }

# Is the PID in $1 alive AND running a command matching $2? (avoids killing reused PIDs)
pid_matches() {
    local pidfile=$1 pattern=$2 pid
    [[ -f $pidfile ]] || return 1
    pid=$(<"$pidfile")
    [[ $pid =~ ^[0-9]+$ ]] || return 1
    [[ -r /proc/$pid/cmdline ]] || return 1
    tr '\0' ' ' </proc/"$pid"/cmdline | grep -q -- "$pattern"
}

# Prints the serial of the single target device, or explains the problem.
# Sets DEVICE_STATE. Honors ANDROID_SERIAL.
pick_device() {
    command -v adb >/dev/null || { bad "adb not installed (run ./install.sh)"; return 1; }
    adb start-server >/dev/null 2>&1
    local lines
    lines=$(adb devices | tail -n +2 | grep -v '^\s*$')
    if [[ -n ${ANDROID_SERIAL:-} ]]; then
        lines=$(grep "^${ANDROID_SERIAL}\s" <<<"$lines")
    else
        # prefer USB (non ip:port) devices
        local usb; usb=$(grep -v '^[0-9.]*:[0-9]*\s' <<<"$lines")
        [[ -n $usb ]] && lines=$usb
    fi
    local n; n=$(grep -c . <<<"$lines")
    if [[ -z $lines ]]; then
        DEVICE_STATE=none
        bad "No device in ADB mode."
        echo "     On the phone: Settings > About phone > tap 'OS version' 7x, then"
        echo "     Settings > Additional settings > Developer options > enable 'USB debugging'."
        return 1
    elif (( n > 1 )); then
        DEVICE_STATE=multiple
        bad "Several devices: $(awk '{print $1}' <<<"$lines" | xargs). Set ANDROID_SERIAL."
        return 1
    fi
    DEVICE_SERIAL=$(awk '{print $1}' <<<"$lines")
    DEVICE_STATE=$(awk '{print $2}' <<<"$lines")
    case $DEVICE_STATE in
        device) return 0 ;;
        unauthorized)
            bad "Device $DEVICE_SERIAL is UNAUTHORIZED."
            echo "     Unlock the phone, accept 'Allow USB debugging?' and tick 'Always allow from this computer'."
            return 1 ;;
        *)  bad "Device $DEVICE_SERIAL is in state '$DEVICE_STATE'. Replug the USB cable."; return 1 ;;
    esac
}
