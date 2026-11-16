#!/usr/bin/env bash
# Root helper (installed root-owned to /usr/local/lib/android-proxy-gw/gw.sh).
# Called by android-proxy-gw.service:  gw.sh up | down ;  by tzsync.sh: gw.sh hold | release
set -euo pipefail
source /etc/android-proxy-gw/gw.conf
STATE=/run/android-proxy-gw
mkdir -p "$STATE"

nft_rules() {
cat <<EOF
table inet android_gw {
    # HOLD: while the phone's timezone does not match the exit IP (tzsync.sh), drop
    # everything from the phone except DHCP. Filled/flushed by "gw.sh hold|release".
    chain hold {
        type filter hook prerouting priority -155; policy accept;
    }
    # Everything the phone sends is answered by THIS PC: DNS -> sing-box DNS,
    # TCP -> sing-box transparent inbound -> upstream proxy. Nothing is routed.
    chain prerouting {
        type nat hook prerouting priority dstnat - 5; policy accept;
        iifname "$IFACE" meta nfproto ipv4 udp dport 53 redirect to :$DNS_PORT
        iifname "$IFACE" meta nfproto ipv4 tcp dport 53 redirect to :$DNS_PORT
        iifname "$IFACE" meta nfproto ipv4 ip daddr != $GW_IP meta l4proto tcp redirect to :$REDIR_PORT
    }
    chain input {
        type filter hook input priority filter - 5; policy accept;
        iifname "$IFACE" meta nfproto ipv6 counter drop comment "no IPv6 from phone"
        iifname "$IFACE" udp dport 67 accept comment "DHCP"
        iifname "$IFACE" tcp dport $REDIR_PORT counter accept comment "phone TCP -> proxy"
        iifname "$IFACE" meta l4proto { tcp, udp } th dport $DNS_PORT counter accept comment "phone DNS"
        iifname "$IFACE" meta l4proto icmp accept
        iifname "$IFACE" counter drop comment "anything else (UDP/QUIC, PC services)"
    }
    chain forward {
        type filter hook forward priority filter - 5; policy accept;
        iifname "$IFACE" counter drop comment "kill switch: never route phone traffic directly"
        oifname "$IFACE" counter drop
    }
}
EOF
}

wait_nm() {  # at boot / after resume NetworkManager may not be ready yet
    local st
    for _ in $(seq 1 60); do
        st=$(nmcli -t -f STATE general 2>/dev/null || true)
        if [[ -n $st && $st != asleep && $st != starting ]] && nmcli -t -f DEVICE device 2>/dev/null | grep -qx "$IFACE"; then
            return 0
        fi
        sleep 1
    done
    echo "NetworkManager/$IFACE not ready after 60s"; return 1
}

up() {
    wait_nm
    # Wi-Fi radio must be on for the AP; remember whether we switched it on.
    if [[ $(nmcli radio wifi) != enabled ]]; then nmcli radio wifi on; touch "$STATE/radio-was-off"; sleep 2; fi
    nft delete table inet android_gw 2>/dev/null || true
    nft_rules | nft -f -          # firewall first: no window where phone traffic could pass
    rm -f "$STATE/held" "$STATE/tz.status"
    [[ ${TZ_SYNC:-0} == 1 ]] && hold   # phone stays offline until tzsync.sh has matched its timezone
    nmcli connection up "$NM_CON" >/dev/null
    for _ in $(seq 1 20); do ip -4 addr show dev "$IFACE" | grep -q "$GW_IP/" && break; sleep 0.5; done
    ip -4 addr show dev "$IFACE" | grep -q "$GW_IP/" || { echo "AP did not get $GW_IP"; exit 1; }
    # belt and braces: never offer IPv6 on the hotspot
    sysctl -qw "net.ipv6.conf.$IFACE.disable_ipv6=1" || true
}

hold() {  # atomic: flush + add in one nft transaction
    nft -f - <<EOF
flush chain inet android_gw hold
add rule inet android_gw hold iifname "$IFACE" udp dport 67 accept comment "DHCP"
add rule inet android_gw hold iifname "$IFACE" counter drop comment "held: phone timezone differs from exit IP timezone"
EOF
    touch "$STATE/held"
}
release() { nft flush chain inet android_gw hold; rm -f "$STATE/held"; }

down() {
    nmcli connection down "$NM_CON" >/dev/null 2>&1 || true
    nft delete table inet android_gw 2>/dev/null || true
    rm -f "$STATE/held" "$STATE/tz.status"
    if [[ -f $STATE/radio-was-off ]]; then nmcli radio wifi off; rm -f "$STATE/radio-was-off"; fi
}

# Sleep hook: remember the running mode and stop cleanly; restore it after resume.
pre_sleep() {
    rm -f "$STATE/mode-before-sleep"
    local m
    for m in proxy direct; do
        if systemctl is-active -q "android-proxy-gw@$m.service"; then
            echo "$m" >"$STATE/mode-before-sleep"
            systemctl stop "android-proxy-gw@$m.service"
        fi
    done
}
post_resume() {
    [[ -f $STATE/mode-before-sleep ]] || return 0
    local m; m=$(<"$STATE/mode-before-sleep"); rm -f "$STATE/mode-before-sleep"
    # --no-block: we run inside the resume transaction; the unit's ExecStartPre waits for NetworkManager
    systemctl start --no-block "android-proxy-gw@$m.service"
}

case ${1:-} in
    up) up ;;
    down) down ;;
    hold) hold ;;
    release) release ;;
    pre-sleep) pre_sleep ;;
    post-resume) post_resume ;;
    *) echo "usage: $0 up|down|hold|release|pre-sleep|post-resume"; exit 2 ;;
esac
