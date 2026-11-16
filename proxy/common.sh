# Shared helpers for proxy-start/stop/status. Sourced.
# shellcheck shell=bash
P="$PROJECT_DIR/proxy"
source "$P/gw.conf"
# proxy scripts only ever act on the gateway phone, even when other phones are plugged in
[[ -n ${PHONE_SERIAL:-} ]] && export ANDROID_SERIAL=$PHONE_SERIAL
USAGE_LOG="$LOG_DIR/proxy-usage.log"

# Prints the running mode (proxy|direct) or nothing.
gw_mode() {
    local m
    for m in proxy direct; do
        systemctl is-active -q "android-proxy-gw@$m.service" && { echo "$m"; return; }
    done
}

# "down up" byte counters of the running sing-box since it started (empty if unavailable)
gw_traffic() {
    local secret
    secret=$(sed -n 's/^API_SECRET=//p' "$P/ap.env" 2>/dev/null)
    [[ -n $secret ]] || return
    curl -s -m 3 -H "Authorization: Bearer $secret" http://127.0.0.1:9091/connections 2>/dev/null \
        | python3 -c "import sys,json
try: d=json.load(sys.stdin); print(d['downloadTotal'], d['uploadTotal'])
except Exception: pass"
}

human() { numfmt --to=iec --suffix=B --format=%.1f "${1:-0}"; }

# Total proxy (paid) bytes recorded from finished sessions
paid_total() {
    awk '$3=="proxy"{s+=$4+$5} END{print s+0}' "$USAGE_LOG" 2>/dev/null || echo 0
}

# Timezone sync state written by tzsync.sh (root) -> prints a summary, returns 1 if phone traffic is held
tz_report() {
    local f=/run/android-proxy-gw/tz.status
    [[ ${TZ_SYNC:-0} == 1 ]] || { echo "  - timezone sync off (TZ_SYNC=0 in proxy/gw.conf)"; return 0; }
    if [[ ! -r $f ]]; then warn "timezone sync: no status yet (phone traffic held until it runs)"; return 1; fi
    local EXIT_IP="" EXIT_TZ="" EXIT_PLACE="" PHONE_TZ="" HELD=1 NOTE="" UPDATED=0 MODE=""
    source "$f"
    if ((HELD)); then bad "phone traffic HELD: ${NOTE:-waiting for timezone sync}"; return 1; fi
    ok "phone timezone $PHONE_TZ = exit IP timezone ($EXIT_IP, $EXIT_PLACE)"
    [[ -n $NOTE ]] && echo "  - $NOTE"
    return 0
}
