#!/usr/bin/env bash
# One-time root install of the Android proxy gateway. Run:  sudo ./install.sh
# Re-run after editing proxy.env (new credentials / SOCKS<->HTTP) to regenerate the config.
#
# System changes (all scoped to this feature):
#   * packages: sing-box, dnsmasq, nftables (Arch: official repo; Ubuntu/Debian: sing-box from the
#     SagerNet apt repo, dnsmasq-base)                        - dnsmasq.service is NOT enabled
#   * /etc/android-proxy-gw/ (root, 0700): config incl. proxy credentials, hotspot password
#   * /usr/local/lib/android-proxy-gw/gw.sh (root-owned helper)
#   * systemd template units android-proxy-gw@{proxy,direct} + android-proxy-gw-dhcp@{proxy,direct}
#   * android-proxy-gw-tz@{proxy,direct}: phone timezone follows the exit IP (traffic held until it does),
#     phone location/scanning kept OFF; runs adb as '$SUDO_USER'. State in /var/lib/android-proxy-gw
#   * android-proxy-gw-sleep.service (enabled): stops the gateway before sleep, restores its mode after resume
#   * android-proxy-gw@proxy.service enabled at boot when AUTOSTART=1 in gw.conf
#   * NetworkManager connection "android-proxy-ap" (hotspot on wlp0s20f3, autoconnect off)
#   * ufw: allow DHCP/DNS/proxy ports IN ON wlp0s20f3 ONLY
#   * polkit: lets user '$SUDO_USER' start/stop/restart ONLY the gateway units without a password
set -euo pipefail
[[ $EUID == 0 ]] || { echo "Run with sudo: sudo $0"; exit 1; }
USER_NAME=${SUDO_USER:?run via sudo from your normal user}
SRC="$(cd "$(dirname "$0")" && pwd)"
ETC=/etc/android-proxy-gw
source "$SRC/gw.conf"

echo "== packages"
[[ -s $SRC/proxy.env ]] || { echo "Missing $SRC/proxy.env - copy proxy.env.example and fill in your proxy"; exit 1; }
command -v nmcli >/dev/null || { echo "NetworkManager (nmcli) is required for the hotspot"; exit 1; }
if command -v pacman >/dev/null; then
    pacman -S --needed --noconfirm sing-box dnsmasq nftables
elif command -v apt-get >/dev/null; then
    # sing-box is not in Ubuntu/Debian: official SagerNet apt repo. dnsmasq-base = binary only,
    # no system dnsmasq.service (which would fight systemd-resolved for port 53).
    if ! command -v sing-box >/dev/null; then
        apt-get install -y -q curl ca-certificates
        install -d -m 755 /etc/apt/keyrings
        curl -fsSL https://sing-box.app/gpg.key -o /etc/apt/keyrings/sagernet.asc
        chmod a+r /etc/apt/keyrings/sagernet.asc
        echo "deb [arch=$(dpkg --print-architecture) signed-by=/etc/apt/keyrings/sagernet.asc] https://deb.sagernet.org/ * *" \
            >/etc/apt/sources.list.d/sagernet.list
        apt-get update -q
    fi
    DEBIAN_FRONTEND=noninteractive apt-get install -y -q sing-box dnsmasq-base nftables
    systemctl disable --now sing-box.service 2>/dev/null || true   # the package's own unit is not used
else
    echo "Unsupported distro (need pacman or apt)"; exit 1
fi
DNSMASQ=$(command -v dnsmasq) || { echo "dnsmasq not found"; exit 1; }

echo "== config"
install -d -m 700 "$ETC"
install -m 644 "$SRC/gw.conf" "$ETC/gw.conf"
echo "ADB_USER=$USER_NAME          # tzsync.sh runs adb as this user (their adb key)" >>"$ETC/gw.conf"
[[ -s $ETC/api.secret ]] || ( umask 077; python3 -c "import secrets; print(secrets.token_hex(16), end='')" >"$ETC/api.secret" )
API_SECRET=$(<"$ETC/api.secret")
for mode in proxy direct; do
    tmp=$(mktemp); chmod 600 "$tmp"
    python3 "$SRC/render_config.py" "$SRC/proxy.env" "$SRC/gw.conf" "$mode" "$API_SECRET" >"$tmp"
    if ! sing-box check -c "$tmp"; then echo "sing-box $mode config check FAILED"; rm -f "$tmp"; exit 1; fi
    install -m 600 "$tmp" "$ETC/sing-box-$mode.json"; rm -f "$tmp"
    echo "   sing-box $mode config OK"
done
rm -f "$ETC/sing-box.json"

if [[ ! -s $ETC/ap.psk ]]; then
    ( umask 077; python3 -c "import secrets,string; a=string.ascii_letters+string.digits; print(''.join(secrets.choice(a) for _ in range(20)), end='')" >"$ETC/ap.psk" )
fi
PSK=$(<"$ETC/ap.psk")
install -m 600 -o "$USER_NAME" -g "$USER_NAME" /dev/null "$SRC/ap.env"
printf 'AP_SSID=%s\nAP_PSK=%s\nAPI_SECRET=%s\n' "$SSID" "$PSK" "$API_SECRET" >"$SRC/ap.env"

echo "== helper + systemd units"
# stop a running gateway cleanly (restarted at the end) so the phone sees a normal disconnect
RUNNING_MODE=""
for m in proxy direct; do
    if systemctl is-active -q "android-proxy-gw@$m.service"; then RUNNING_MODE=$m; systemctl stop "android-proxy-gw@$m.service"; fi
done
install -D -m 755 -o root -g root "$SRC/gw.sh" /usr/local/lib/android-proxy-gw/gw.sh
install -D -m 755 -o root -g root "$SRC/tzsync.sh" /usr/local/lib/android-proxy-gw/tzsync.sh
install -d -m 755 /var/lib/android-proxy-gw
# migrate from the earlier non-template units
systemctl stop android-proxy-gw.service android-proxy-gw-dhcp.service 2>/dev/null || true
rm -f /etc/systemd/system/android-proxy-gw.service /etc/systemd/system/android-proxy-gw-dhcp.service
install -m 644 "$SRC/android-proxy-gw@.service" /etc/systemd/system/android-proxy-gw@.service
install -m 644 "$SRC/android-proxy-gw-tz@.service" /etc/systemd/system/android-proxy-gw-tz@.service
cat >/etc/systemd/system/android-proxy-gw-dhcp@.service <<EOF
[Unit]
Description=DHCP for the Android gateway hotspot (mode %i)
BindsTo=android-proxy-gw@%i.service
After=android-proxy-gw@%i.service

[Service]
ExecStart=$DNSMASQ --keep-in-foreground --conf-file=/dev/null --port=0 \\
  --interface=$IFACE --bind-interfaces --except-interface=lo \\
  --dhcp-range=$DHCP_START,$DHCP_END,255.255.255.0,12h \\
  --dhcp-option=option:router,$GW_IP --dhcp-option=option:dns-server,$GW_IP \\
  --dhcp-leasefile=/run/android-proxy-gw-dhcp.leases --dhcp-authoritative
Restart=on-failure
RestartSec=2
EOF
install -m 644 "$SRC/android-proxy-gw-sleep.service" /etc/systemd/system/android-proxy-gw-sleep.service
systemctl daemon-reload
systemctl enable android-proxy-gw-sleep.service 2>&1 | sed 's/^/   /'
echo "   sleep hook: ON (gateway stopped before sleep, same mode restored after resume)"
if [[ ${AUTOSTART:-0} == 1 ]]; then
    systemctl enable android-proxy-gw@proxy.service 2>&1 | sed 's/^/   /'
    echo "   autostart at boot: ON (proxy mode)"
else
    systemctl disable android-proxy-gw@proxy.service 2>/dev/null || true
    echo "   autostart at boot: OFF"
fi

echo "== NetworkManager hotspot '$SSID' on $IFACE"
nmcli connection delete "$NM_CON" >/dev/null 2>&1 || true
nmcli connection add type wifi ifname "$IFACE" con-name "$NM_CON" autoconnect no ssid "$SSID" \
    802-11-wireless.mode ap 802-11-wireless.band bg 802-11-wireless.channel 6 \
    wifi-sec.key-mgmt wpa-psk wifi-sec.proto rsn wifi-sec.pairwise ccmp wifi-sec.group ccmp wifi-sec.psk "$PSK" \
    ipv4.method manual ipv4.addresses "$GW_IP/$PREFIX" ipv6.method disabled >/dev/null

echo "== ufw (hotspot interface only)"
ufw_state=$(ufw status 2>/dev/null || true)
if [[ $ufw_state == *"Status: active"* ]]; then
    ufw allow in on "$IFACE" to any port 67 proto udp comment 'android-proxy-gw DHCP' >/dev/null
    ufw allow in on "$IFACE" to any port "$REDIR_PORT" proto tcp comment 'android-proxy-gw proxy' >/dev/null
    ufw allow in on "$IFACE" to any port "$DNS_PORT" comment 'android-proxy-gw DNS' >/dev/null
    # PROBE_PORT listens on 127.0.0.1 only - no ufw rule needed
    ufw status | grep android-proxy-gw | sed 's/^/   /' || true
else
    echo "   ufw not active - skipped"
fi

echo "== polkit (start/stop/restart of the two units for $USER_NAME)"
cat >/etc/polkit-1/rules.d/50-android-proxy-gw.rules <<EOF
polkit.addRule(function(action, subject) {
    if (action.id == "org.freedesktop.systemd1.manage-units" && subject.user == "$USER_NAME") {
        var unit = action.lookup("unit"), verb = action.lookup("verb");
        var units = ["android-proxy-gw@proxy.service", "android-proxy-gw@direct.service",
                     "android-proxy-gw-dhcp@proxy.service", "android-proxy-gw-dhcp@direct.service",
                     "android-proxy-gw-tz@proxy.service", "android-proxy-gw-tz@direct.service",
                     "android-proxy-gw-sleep.service"];
        if (units.indexOf(unit) >= 0 &&
            (verb == "start" || verb == "stop" || verb == "restart")) {
            return polkit.Result.YES;
        }
    }
});
EOF
chmod 644 /etc/polkit-1/rules.d/50-android-proxy-gw.rules

if [[ -n $RUNNING_MODE ]]; then
    echo "== restarting gateway in $RUNNING_MODE mode"
    systemctl start "android-proxy-gw@$RUNNING_MODE.service"
fi

echo
echo "Installed. Start with: ./proxy-start.sh   (hotspot '$SSID', password in proxy/ap.env)"
echo "Uninstall with:      sudo proxy/uninstall.sh"
