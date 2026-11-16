#!/usr/bin/env bash
# Remove everything install.sh added (packages are kept). Run: sudo proxy/uninstall.sh
set -uo pipefail
[[ $EUID == 0 ]] || { echo "Run with sudo"; exit 1; }
SRC="$(cd "$(dirname "$0")" && pwd)"; source "$SRC/gw.conf"
systemctl disable android-proxy-gw@proxy.service android-proxy-gw-sleep.service 2>/dev/null
systemctl stop 'android-proxy-gw@*.service' 'android-proxy-gw-dhcp@*.service' 'android-proxy-gw-tz@*.service' android-proxy-gw.service 2>/dev/null
rm -f /etc/systemd/system/android-proxy-gw.service /etc/systemd/system/android-proxy-gw-dhcp.service \
      /etc/systemd/system/android-proxy-gw@.service /etc/systemd/system/android-proxy-gw-dhcp@.service /etc/systemd/system/android-proxy-gw-tz@.service \
      /etc/systemd/system/android-proxy-gw-sleep.service
systemctl daemon-reload
nmcli connection delete "$NM_CON" 2>/dev/null
nft delete table inet android_gw 2>/dev/null
for rule in "67 proto udp" "$REDIR_PORT proto tcp" "$DNS_PORT"; do
    # shellcheck disable=SC2086
    ufw delete allow in on "$IFACE" to any port $rule >/dev/null 2>&1
done
rm -f /etc/polkit-1/rules.d/50-android-proxy-gw.rules
rm -rf /usr/local/lib/android-proxy-gw /etc/android-proxy-gw /var/lib/android-proxy-gw
echo "Removed. (sing-box/dnsmasq packages kept: 'sudo pacman -Rs sing-box dnsmasq' or 'sudo apt remove sing-box dnsmasq-base')"
