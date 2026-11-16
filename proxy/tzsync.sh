#!/usr/bin/env bash
# Root helper (installed to /usr/local/lib/android-proxy-gw/tzsync.sh), run by
# android-proxy-gw-tz@MODE.service next to the gateway.
#
# Keeps the phone's timezone equal to the timezone of the gateway's exit IP:
#   1. gw.sh up starts the hotspot with phone traffic HELD (only DHCP passes)
#   2. look up the exit IP through sing-box's loopback probe inbound (same outbound /
#      sticky session as the phone) -> IANA timezone
#   3. phone on USB: set its timezone via adb (AlarmManager.setTimeZone), verify
#      phone not on USB: use the timezone it was last set to (remembered on disk)
#   4. timezones match -> release; otherwise stay held (strict)
#   5. repeat every TZ_CHECK_SEC: if the proxy rotates to an IP in another timezone,
#      hold, change the phone's timezone, release
# Also (PRIVACY_ENFORCE=1) keeps location, Wi-Fi/BT scanning and automatic
# timezone OFF on the phone, and keeps its clock in sync with the PC (the
# gateway blocks UDP, so the phone cannot reach NTP itself).
set -uo pipefail
source /etc/android-proxy-gw/gw.conf
MODE=${1:-proxy}
[[ ${TZ_SYNC:-0} == 1 ]] || { echo "TZ_SYNC=0 - nothing to do"; exit 0; }
STATE=/run/android-proxy-gw
LIB=/var/lib/android-proxy-gw
GW=/usr/local/lib/android-proxy-gw/gw.sh
mkdir -p "$STATE" "$LIB"
UHOME=$(getent passwd "$ADB_USER" | cut -d: -f6)
CHECK=${TZ_CHECK_SEC:-60}

log() { echo "$*"; }

# setpriv, not runuser: no PAM session (= no 2 journal lines per adb call)
adb_() { timeout 25 setpriv --reuid="$ADB_USER" --regid="$ADB_USER" --init-groups env HOME="$UHOME" adb "$@"; }

ensure_adb_server() {
    # Never let adb fork its server inside this unit (it would die with the unit and
    # take scrcpy down with it): start it as its own transient unit, as the user.
    (exec 3<>/dev/tcp/127.0.0.1/5037) 2>/dev/null && return 0
    systemctl is-active -q android-proxy-gw-adb.service \
        || systemd-run --quiet --collect --unit=android-proxy-gw-adb --uid="$ADB_USER" \
               --setenv=HOME="$UHOME" "$(command -v adb)" server nodaemon 2>/dev/null
    for _ in 1 2 3 4 5 6; do (exec 3<>/dev/tcp/127.0.0.1/5037) 2>/dev/null && return 0; sleep 0.5; done
    return 1
}

usb_device() {  # serial of the gateway phone (PHONE_SERIAL) if it is on USB and authorized, or nothing
    ensure_adb_server || return
    if [[ -n ${PHONE_SERIAL:-} ]]; then
        # other phones may be plugged in too - never touch them
        adb_ devices 2>/dev/null | awk -F'\t' -v s="$PHONE_SERIAL" '$1==s && $2=="device" {print $1; exit}'
    else
        adb_ devices 2>/dev/null | awk -F'\t' '$2=="device" && $1 !~ /:/ {print $1; exit}'
    fi
}

lookup() {  # -> "IP|TZ|place", via the gateway's own outbound
    local px="--socks5-hostname 127.0.0.1:$PROBE_PORT" out
    # shellcheck disable=SC2086
    out=$(curl -s -m 15 $px "http://ip-api.com/json/?fields=status,query,timezone,city,regionName,countryCode" \
          | python3 -c 'import sys,json
d=json.load(sys.stdin); assert d["status"]=="success" and d["timezone"]
print(d["query"], d["timezone"], ", ".join(x for x in (d["city"], d["regionName"], d["countryCode"]) if x), sep="|")' 2>/dev/null)
    [[ -n $out ]] || out=$(curl -s -m 15 $px https://ipinfo.io/json | python3 -c 'import sys,json
d=json.load(sys.stdin); assert d["timezone"]
print(d["ip"], d["timezone"], ", ".join(x for x in (d.get("city"), d.get("region"), d.get("country")) if x), sep="|")' 2>/dev/null)
    [[ -n $out ]] && echo "$out"
}

enforce_privacy() {  # $1 serial. One adb round trip when nothing needs fixing.
    local s want="false 0 0 0 false"
    s=$(adb_ -s "$1" shell 'echo $(cmd location is-location-enabled) $(settings get global wifi_scan_always_enabled) $(settings get global ble_scan_always_enabled) $(settings get global auto_time_zone) $(cmd time_zone_detector is_geo_detection_enabled)' | tr -d '\r')
    [[ -z $s || $s == "$want" ]] && return
    adb_ -s "$1" shell 'cmd location set-location-enabled false; settings put global wifi_scan_always_enabled 0;
        settings put global ble_scan_always_enabled 0; settings put global assisted_gps_enabled 0;
        cmd time_zone_detector set_auto_detection_enabled false; cmd time_zone_detector set_geo_detection_enabled false' >/dev/null
    log "privacy: phone had [location scanWifi scanBT autoTZ geoTZ] = [$s] -> turned OFF"
}

sync_clock() {  # $1 serial. Phone can't reach NTP through the gateway (UDP blocked).
    local p d
    p=$(adb_ -s "$1" shell date +%s | tr -d '\r'); [[ $p =~ ^[0-9]+$ ]] || return
    d=$(( p - $(date +%s) )); ((d < 0)) && d=$((-d))
    if ((d > 3)); then
        adb_ -s "$1" shell service call alarm 2 i64 "$(date +%s%3N)" >/dev/null && log "clock: phone was off by ${d}s -> set from PC"
    fi
}

held() { [[ -f $STATE/held ]]; }
hold() { held || { "$GW" hold && log "HOLD: phone traffic blocked ($*)"; }; }
release() { held && { "$GW" release && log "RELEASE: phone traffic flowing"; }; }

write_status() {
    local f=$STATE/tz.status.tmp
    { echo "MODE=$MODE"; echo "EXIT_IP=$ip"; echo "EXIT_TZ=$tz"; echo "EXIT_PLACE='$place'"
      echo "PHONE_TZ=$cur"; echo "HELD=$(held && echo 1 || echo 0)"; echo "NOTE='$1'"; echo "UPDATED=$(date +%s)"; } >"$f"
    chmod 644 "$f"; mv "$f" "$STATE/tz.status"
}

last_ip="" ip="" tz="" place="" cur=""
log "tzsync started (mode $MODE, check every ${CHECK}s, privacy enforce ${PRIVACY_ENFORCE:-0})"
while :; do
    if ! info=$(lookup); then
        write_status "exit IP lookup failed - retrying"; sleep 10; continue
    fi
    IFS='|' read -r ip tz place <<<"$info"
    # values come from a web API and end up in a phone shell command / a sourced file: validate
    place=${place//[^A-Za-z0-9 ,.-]/}
    if [[ ! $ip =~ ^[0-9a-fA-F.:]+$ || ! $tz =~ ^[A-Za-z_]+(/[A-Za-z0-9_+-]+){0,2}$ ]]; then
        write_status "exit IP lookup returned garbage - retrying"; sleep 10; continue
    fi
    [[ $ip != "$last_ip" ]] && log "exit IP $ip - $place - $tz"
    last_ip=$ip
    note=""
    serial=$(usb_device)
    if [[ -n $serial ]]; then
        [[ ${PRIVACY_ENFORCE:-0} == 1 ]] && enforce_privacy "$serial"
        cur=$(adb_ -s "$serial" shell getprop persist.sys.timezone | tr -d '\r')
        if [[ -n $cur && $cur != "$tz" ]]; then
            hold "phone timezone $cur, exit IP timezone $tz"
            adb_ -s "$serial" shell service call alarm 3 s16 "$tz" >/dev/null
            new=$(adb_ -s "$serial" shell getprop persist.sys.timezone | tr -d '\r')
            if [[ $new == "$tz" ]]; then log "phone timezone $cur -> $tz"; else log "FAILED to set phone timezone to $tz (still '$new')"; fi
            cur=$new
        fi
        [[ -n $cur ]] && echo "$cur" >"$LIB/phone-tz"
        sync_clock "$serial"
    else
        cur=$(cat "$LIB/phone-tz" 2>/dev/null)
        note="phone not on USB - using last known phone timezone"
    fi
    if [[ -n $tz && $cur == "$tz" ]]; then
        release
    else
        hold "timezone mismatch"
        note="HELD: phone timezone '${cur:-unknown}' != exit IP timezone '$tz' - connect the phone by USB"
    fi
    [[ -n $note && $note != "${prev_note:-}" ]] && log "$note"
    prev_note=$note
    write_status "$note"
    if held; then sleep 10; else sleep "$CHECK"; fi
done
