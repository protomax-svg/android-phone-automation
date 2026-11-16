#!/usr/bin/env bash
# Report device connection, authorization and environment health. Read-only.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$PROJECT_DIR"
FAIL=0

echo "${B}Host tools${N}"
for t in adb scrcpy; do
    if command -v $t >/dev/null; then ok "$t: $($t --version 2>&1 | head -1)"; else bad "$t missing"; FAIL=1; fi
done
venv_info() { "$1" -c 'import sys; from importlib.metadata import version as v; print("Python", sys.version.split()[0] + ",", sys.argv[1], v(sys.argv[1]))' "$2" 2>/dev/null; }
if i=$(venv_info .venv/bin/python uiautomator2); then ok "automation venv: $i"; else bad "automation venv missing/broken"; FAIL=1; fi
if i=$(venv_info .venv-agent/bin/python mobilerun); then ok "agent venv: $i"; else warn "agent venv (DroidRun/mobilerun) not installed"; fi
ls /usr/lib/udev/rules.d/*android*.rules >/dev/null 2>&1 && ok "udev rules: $(ls /usr/lib/udev/rules.d/*android*.rules | xargs -n1 basename)" || warn "android udev rules not installed (android-udev package)"
ok "session: ${XDG_SESSION_TYPE:-?} / ${XDG_CURRENT_DESKTOP:-?} (WAYLAND_DISPLAY=${WAYLAND_DISPLAY:-unset})"

echo "${B}USB${N}"
usb=$(lsusb 2>/dev/null | grep -iE "xiaomi|google|samsung|oneplus|android|motorola|huawei|oppo|vivo|realme" || true)
[[ -n $usb ]] && ok "$usb" || warn "no known phone vendor on USB bus"

echo "${B}ADB device${N}"
if command -v adb >/dev/null && pick_device; then
    A=(adb -s "$DEVICE_SERIAL" shell)
    prop() { "${A[@]}" getprop "$1" | tr -d '\r'; }
    ok "serial $DEVICE_SERIAL: authorized (state=device)"
    ok "model: $(prop ro.product.manufacturer) $(prop ro.product.marketname) ($(prop ro.product.model))"
    ok "Android $(prop ro.build.version.release) (SDK $(prop ro.build.version.sdk)), $(prop ro.mi.os.version.name)$(prop ro.miui.ui.version.name) $(prop ro.build.version.incremental)"
    ok "$("${A[@]}" wm size | tr -d '\r' | tail -1), $("${A[@]}" wm density | tr -d '\r' | tail -1)"
    ok "battery: $("${A[@]}" dumpsys battery | tr -d '\r' | awk -F': ' '/ level:/{print $2"%"}')"
    wake=$("${A[@]}" dumpsys power | tr -d '\r' | grep -o 'mWakefulness=[A-Za-z]*' | head -1)
    ok "power: ${wake:-unknown}"
    # harmless KEYCODE_UNKNOWN probe: detects Xiaomi's "USB debugging (Security settings)" gate
    probe=$("${A[@]}" input keyevent 0 2>&1)
    if grep -q SecurityException <<<"$probe"; then
        bad "input injection BLOCKED -> enable Developer options > 'USB debugging (Security settings)', then reboot the phone"; FAIL=1
    else
        ok "input injection allowed (tap/swipe/keys work)"
    fi
    if [[ -n $("${A[@]}" pgrep -f com.wetest.uia2.Main 2>/dev/null | tr -d '\r') ]]; then
        ok "uiautomator2 server: running on phone"
    else
        echo "  - uiautomator2 server: not running (starts automatically on first use)"
    fi
else
    FAIL=1
fi

echo "${B}Project processes${N}"
pid_matches "$SCRCPY_PID" scrcpy && ok "scrcpy running (pid $(<"$SCRCPY_PID"))" || echo "  - scrcpy (project) not running"
pid_matches "$DEMO_PID" scripts/demo.py && ok "demo running (pid $(<"$DEMO_PID"))" || echo "  - demo not running"
other=$(pgrep -a scrcpy | grep -v "^$(cat "$SCRCPY_PID" 2>/dev/null || echo x) " || true)
[[ -n $other ]] && echo "  - other (non-project) scrcpy: $other"

echo
((FAIL)) && echo "${R}${B}Status: problems found (see ✘ above)${N}" || echo "${G}${B}Status: healthy${N}"
exit $FAIL
