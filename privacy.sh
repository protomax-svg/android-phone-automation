#!/usr/bin/env bash
# Location privacy of a phone: everything that can reveal where it is.
#   ./privacy.sh          check only (read-only)
#   ./privacy.sh --fix    turn every item OFF
# Several phones connected: ANDROID_SERIAL=<serial> ./privacy.sh
# Wi-Fi itself is left alone (the phone needs it for internet); with location and
# scanning off, apps cannot see nearby networks.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
FIX=0; [[ ${1:-} == --fix ]] && FIX=1

pick_device || exit 1
A=(adb -s "$DEVICE_SERIAL" shell)
SDK=$("${A[@]}" getprop ro.build.version.sdk | tr -d '\r')
echo "${B}Location privacy on $DEVICE_SERIAL ($("${A[@]}" getprop ro.product.model | tr -d '\r'), Android $("${A[@]}" getprop ro.build.version.release | tr -d '\r'))${N}"

get() { "${A[@]}" settings get "$1" "$2" 2>/dev/null | tr -d '\r'; }
BAD=0

# name | namespace key | value meaning OFF | how to turn off
check() {
    local label=$1 ns=$2 key=$3 off=$4 cur
    cur=$(get "$ns" "$key")
    [[ $cur == null ]] && { echo "  - $label: not on this phone"; return; }
    if [[ $cur == "$off" ]]; then ok "$label: off"; return; fi
    if ((FIX)); then
        "${A[@]}" settings put "$ns" "$key" "$off" >/dev/null
        cur=$(get "$ns" "$key")
        [[ $cur == "$off" ]] && ok "$label: turned OFF" || { bad "$label: could not turn off (still $cur)"; BAD=1; }
    else
        bad "$label: ON ($key=$cur)"; BAD=1
    fi
}

# location master switch
if [[ $(get secure location_mode) != 0 ]]; then
    if ((FIX)); then
        if ((SDK >= 30)); then "${A[@]}" cmd location set-location-enabled false; else "${A[@]}" settings put secure location_mode 0; fi
        [[ $(get secure location_mode) == 0 ]] && ok "location: turned OFF" || { bad "location: could not turn off"; BAD=1; }
    else bad "location: ON (location_mode=$(get secure location_mode))"; BAD=1; fi
else ok "location: off"; fi

check "Wi-Fi scanning (finds you even with Wi-Fi off)" global wifi_scan_always_enabled 0
check "Bluetooth scanning (finds you even with Bluetooth off)" global ble_scan_always_enabled 0
check "assisted GPS (sends cell/position data to a server)" global assisted_gps_enabled 0
check "Wi-Fi turns itself on near saved networks" global wifi_wakeup_enabled 0
check "Samsung nearby device scanning" system nearby_scanning_enabled 0

# Bluetooth radio
if [[ $(get global bluetooth_on) != 0 ]]; then
    if ((FIX)); then
        if ((SDK >= 33)); then "${A[@]}" cmd bluetooth_manager disable >/dev/null 2>&1; else "${A[@]}" svc bluetooth disable >/dev/null 2>&1; fi
        sleep 2
        [[ $(get global bluetooth_on) == 0 ]] && ok "Bluetooth: turned OFF" || { bad "Bluetooth: could not turn off"; BAD=1; }
    else bad "Bluetooth: ON"; BAD=1; fi
else ok "Bluetooth: off"; fi

# NFC works only at a few cm and does not reveal the location to online services: informational
if "${A[@]}" pm list features | grep -q android.hardware.nfc; then
    echo "  - NFC: $("${A[@]}" dumpsys nfc 2>/dev/null | tr -d '\r' | grep -m1 -oE "mState=[a-z_]+" | cut -d= -f2) (short range only, not a location leak)"
fi

# informational
[[ -n $("${A[@]}" pm list packages com.samsung.android.fmm | tr -d '\r') ]] && \
    echo "  - Samsung Find My Mobile: check in Settings > Biometrics and security (it can locate the phone remotely)"
echo "  - timezone: $(get global auto_time_zone | sed 's/1/automatic/;s/0/manual/'), $("${A[@]}" getprop persist.sys.timezone | tr -d '\r')"
echo "  - Wi-Fi: $(get global wifi_on | sed 's/^0$/off/;s/^[12]$/on/'), airplane mode: $(get global airplane_mode_on | sed 's/1/on/;s/0/off/')"

echo
if ((BAD)); then echo "${R}${B}Privacy: something can still reveal the location$( ((FIX)) || echo " - run with --fix")${N}"; exit 1
else echo "${G}${B}Privacy: location, scanning and Bluetooth are off${N}"; fi
