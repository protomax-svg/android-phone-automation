#!/usr/bin/env bash
# Proxy gateway health + leak checks, measured FROM THE PHONE.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
source "$PROJECT_DIR/proxy/common.sh"; source "$P/proxy.env"
FAIL=0

phone_get() {  # HTTP GET executed ON THE PHONE (phone DNS + TCP stack); stdin held open for the reply
    adb -s "$DEVICE_SERIAL" shell "(printf 'GET $2 HTTP/1.0\r\nHost: $1\r\nUser-Agent: curl/8\r\n\r\n'; sleep 6) | nc -w 12 $1 80" 2>&1 | tr -d '\r'
}
http_ip() {
    phone_get ipinfo.io /json | sed -n '/^{/,/^}/p' | python3 -c "import sys,json
try: d=json.load(sys.stdin); print(d['ip'], d.get('city',''), d.get('country',''), d.get('org',''), d.get('timezone',''))
except Exception: print('NO-RESPONSE')"
}
dns_resolver() {  # which DNS resolver the internet sees for the phone's lookups
    local loc host
    loc=$(phone_get edns.ip-api.com /json | sed -n 's/^Location: http:\/\///p'); host=${loc%%/*}
    [[ -n $host ]] || { echo "?"; return; }
    phone_get "$host" /json | sed -n '/^{/,$p' | python3 -c "import sys,json
try: d=json.load(sys.stdin)['dns']; print(d['ip'], '-', d['geo'])
except Exception: print('?')"
}

echo "${B}Gateway (PC)${N}"
MODE=$(gw_mode)
if [[ -z $MODE ]]; then bad "gateway not running (./proxy-start.sh or ./proxy-start.sh --direct)"; exit 1; fi
[[ $MODE == proxy ]] && ok "mode: PROXY (paid traffic, US IP)" || warn "mode: DIRECT (free traffic, phone uses your REAL IP)"
for u in android-proxy-gw@$MODE android-proxy-gw-dhcp@$MODE; do
    systemctl is-active -q $u && ok "$u active" || { bad "$u inactive"; FAIL=1; }
done
read -r down up <<<"$(gw_traffic)"
[[ -n ${down:-} ]] && ok "this session: $(human "$down") down / $(human "$up") up ($MODE)"
ok "paid proxy traffic in earlier sessions: $(human "$(paid_total)")  (log: logs/proxy-usage.log)"
ip -4 -br addr show dev "$IFACE" 2>/dev/null | grep -q "$GW_IP" && ok "hotspot '$SSID' on $IFACE" || { bad "hotspot down"; FAIL=1; }
lease=$(cat /run/android-proxy-gw-dhcp.leases 2>/dev/null | awk '{print $3" "$4}')
[[ -n $lease ]] && ok "DHCP clients: $lease" || warn "no DHCP clients yet"
real=$(curl -s -m 10 https://api.ipify.org); echo "  - PC real IP: ${real:-?}"
upip=$(curl -s -m 20 -x "socks5h://$PROXY_USER:$PROXY_PASS@$PROXY_HOST:$PROXY_PORT" https://api.ipify.org)
tz_report || FAIL=1
[[ -n $upip ]] && ok "upstream proxy reachable, exit IP $upip" || { bad "upstream proxy NOT reachable"; [[ $MODE == proxy ]] && FAIL=1; }

echo "${B}Phone${N}"
if pick_device; then
    st=$(adb -s "$DEVICE_SERIAL" shell cmd wifi status 2>/dev/null)
    if grep -q "\"$SSID\"" <<<"$st"; then ok "phone on Wi-Fi '$SSID'"
    else
        bad "phone not on '$SSID'"; FAIL=1
        adb -s "$DEVICE_SERIAL" shell dumpsys wifi 2>/dev/null | grep -q "\"$SSID\".*WRONG_PASSWORD" \
            && warn "phone disabled auto-join for '$SSID' (thinks password is wrong) -> run ./proxy-start.sh --connect-phone"
    fi
    others=$(adb -s "$DEVICE_SERIAL" shell cmd wifi list-networks 2>/dev/null | tail -n +2 | awk '{print $2}' | sort -u | grep -vx "$SSID" | xargs)
    [[ -z $others ]] && ok "no other saved Wi-Fi networks" || warn "other saved Wi-Fi networks: $others  (phone could fall back to them = real IP leak)"
    loc=$(adb -s "$DEVICE_SERIAL" shell 'echo $(cmd location is-location-enabled) $(settings get global wifi_scan_always_enabled) $(settings get global ble_scan_always_enabled)' | tr -d '\r')
    [[ $loc == "false 0 0" ]] && ok "phone location OFF, Wi-Fi/BT scanning OFF" || warn "phone location/scanning [loc wifiScan btScan] = [$loc] (tzsync turns it off within ${TZ_CHECK_SEC:-60}s)"
    v6=$(adb -s "$DEVICE_SERIAL" shell ip -6 addr show scope global 2>/dev/null | grep -c inet6)
    ((v6 == 0)) && ok "no global IPv6 on phone" || { bad "phone has global IPv6 address(es)"; FAIL=1; }
    pip=$(http_ip)
    if [[ $pip == NO-RESPONSE ]]; then bad "phone has no internet"; FAIL=1
    elif [[ $MODE == direct ]]; then
        [[ ${pip%% *} == "$real" ]] && ok "phone public IP: $pip (your real IP, as expected in DIRECT mode)" || warn "phone IP $pip"
    elif [[ ${pip%% *} == "$real" ]]; then bad "LEAK: phone exits with the PC's real IP ($pip)"; FAIL=1
    else ok "phone public IP: $pip"; [[ ${pip%% *} == "$upip" ]] && ok "matches proxy exit IP"; fi
    res=$(dns_resolver)
    case $res in
        \?) warn "DNS leak test inconclusive" ;;
        *Czech*|*"$real"*) if [[ $MODE == proxy ]]; then bad "DNS LEAK: resolver $res"; FAIL=1; else ok "DNS resolver: $res (direct)"; fi ;;
        *) ok "DNS resolver seen by internet: $res" ;;
    esac
fi
echo
if ((FAIL)); then echo "${R}${B}Gateway: problems found${N}"
elif [[ $MODE == proxy ]]; then echo "${G}${B}Gateway OK - PROXY mode: phone traffic goes only through the proxy${N}"
else echo "${Y}${B}Gateway OK - DIRECT mode: phone uses your real IP (no proxy traffic used)${N}"; fi
exit $FAIL
