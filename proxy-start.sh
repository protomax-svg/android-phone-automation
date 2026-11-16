#!/usr/bin/env bash
# Start the phone gateway (PC Wi-Fi hotspot 'phone-gw').
#   ./proxy-start.sh                  PROXY mode: all phone traffic via the paid proxy (US IP)
#   ./proxy-start.sh --direct         DIRECT mode: normal internet via this PC (real IP, free)
#   ./proxy-start.sh --connect-phone  also join the phone to the hotspot (first time only)
# Switching modes restarts the hotspot; the phone reconnects by itself in ~20 s.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
source "$PROJECT_DIR/proxy/common.sh"
cd "$PROJECT_DIR"
[[ -f /etc/systemd/system/android-proxy-gw@.service ]] || { bad "not installed: sudo $P/install.sh"; exit 1; }

MODE=proxy; CONNECT=0
for a in "$@"; do
    case $a in
        --direct) MODE=direct ;;
        --proxy) MODE=proxy ;;
        --connect-phone) CONNECT=1 ;;
        -h|--help) sed -n '2,7p' "$0"; exit 0 ;;
        *) bad "unknown option $a"; exit 2 ;;
    esac
done

cur=$(gw_mode)
if [[ $cur == "$MODE" ]]; then
    ok "already running in $MODE mode"
else
    [[ -n $cur ]] && "$PROJECT_DIR/proxy-stop.sh" | sed 's/^/  /'
    echo "${B}Starting gateway in ${MODE^^} mode${N}"
    systemctl start "android-proxy-gw@$MODE.service" || { bad "start failed:"; journalctl -u "android-proxy-gw@$MODE" -n 20 --no-pager; exit 1; }
    for _ in $(seq 1 20); do systemctl is-active -q "android-proxy-gw-dhcp@$MODE.service" && break; sleep 0.5; done
    sleep 3   # sing-box exits within ~1 s on a runtime config error; make sure it stayed up
    if ! systemctl is-active -q "android-proxy-gw@$MODE.service"; then
        bad "sing-box not running:"; journalctl -u "android-proxy-gw@$MODE" -n 6 --no-pager -o cat | grep -iE "fatal|error" | sed 's/^/     /'; exit 1
    fi
    ok "sing-box running"
    systemctl is-active -q "android-proxy-gw-dhcp@$MODE.service" && ok "DHCP running" || bad "DHCP not running"
    ok "hotspot '$SSID' up on $IFACE ($GW_IP)"
fi
if [[ ${TZ_SYNC:-0} == 1 ]]; then
    for _ in $(seq 1 40); do grep -q '^HELD=0' /run/android-proxy-gw/tz.status 2>/dev/null && break; sleep 0.5; done
    tz_report
fi
if [[ $MODE == direct ]]; then
    warn "DIRECT mode: the phone now uses your REAL IP (free traffic)."
    ok "Google/YouTube/Play, TikTok, Instagram/Facebook are BLOCKED in this mode"
    echo "    (list: proxy/direct-block.txt) - those apps only ever connect via the proxy."
    echo "    Play Store updates therefore only work in PROXY mode."
else
    ok "PROXY mode: phone traffic goes only through the paid proxy"
fi

if ((CONNECT)); then
    source "$P/ap.env"
    pick_device || exit 1
    adb -s "$DEVICE_SERIAL" shell svc wifi enable
    joined() { adb -s "$DEVICE_SERIAL" shell cmd wifi status | grep -q "connected to \"$AP_SSID\""; }
    if adb -s "$DEVICE_SERIAL" shell cmd wifi list-networks | grep -q " $AP_SSID "; then
        for _ in $(seq 1 15); do joined && break; sleep 1; done
    fi
    if ! joined; then
        # New network, or Android disabled auto-join (e.g. it misread an interrupted
        # handshake as a wrong password): a manual connect via Wi-Fi settings fixes both.
        echo "  - joining '$AP_SSID' via the phone's Wi-Fi settings"
        "$PROJECT_DIR/.venv/bin/python" - "$AP_SSID" "$AP_PSK" <<'EOF' || bad "could not join phone via UI"
import sys, time
sys.path.insert(0, ".")
from android_auto import Phone
ssid, psk = sys.argv[1], sys.argv[2]
p = Phone(); p.ensure_unlocked()
p.adb.shell("am start -a android.settings.WIFI_SETTINGS")
p.wait_for(text=ssid, timeout=30).click()
f = p.d(focused=True, className="android.widget.EditText")
if f.exists(timeout=4):
    f.set_text(psk)                  # not logged
    p.d(text="Connect").click()
p.home()
EOF
    fi
    for _ in $(seq 1 30); do adb -s "$DEVICE_SERIAL" shell cmd wifi status | grep -q "connected to \"$AP_SSID\"" && break; sleep 1; done
    adb -s "$DEVICE_SERIAL" shell cmd wifi status | grep -q "connected to \"$AP_SSID\"" && ok "phone connected to '$AP_SSID'" || bad "phone not connected to '$AP_SSID'"
fi
echo "Check with: ./proxy-status.sh"
