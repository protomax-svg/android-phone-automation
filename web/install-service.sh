#!/usr/bin/env bash
# Run the web control panel as a systemd USER service (no root; uses your adb key and Claude login).
#   web/install-service.sh              install + enable + start (also starts at boot)
#   web/install-service.sh --uninstall  stop + remove
# Manage:  systemctl --user status|restart|stop android-automation-web
# Logs:    journalctl --user -u android-automation-web -f
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
UNIT=android-automation-web.service
UNIT_DIR="${XDG_CONFIG_HOME:-$HOME/.config}/systemd/user"
[[ $EUID != 0 ]] || { echo "Run as your normal user, not root/sudo."; exit 1; }

if [[ ${1:-} == --uninstall ]]; then
    systemctl --user disable --now "$UNIT" 2>/dev/null || true
    rm -f "$UNIT_DIR/$UNIT"
    systemctl --user daemon-reload
    echo "Removed $UNIT"
    exit 0
fi

[[ -x $DIR/.venv/bin/python ]] || { echo "Python venv missing - run ./install.sh first"; exit 1; }

# The service gets the PATH of this shell, so claude/adb/scrcpy are found the same way as in a terminal.
mkdir -p "$UNIT_DIR"
cat >"$UNIT_DIR/$UNIT" <<EOF
[Unit]
Description=Android automation web control panel ($DIR)
After=network-online.target

[Service]
Type=simple
WorkingDirectory=$DIR
Environment="PATH=${PATH//%/%%}"
Environment=PYTHONUNBUFFERED=1
ExecStart=$DIR/web.sh
Restart=on-failure
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload
systemctl --user enable "$UNIT" >/dev/null
systemctl --user restart "$UNIT"
echo "Installed $UNIT_DIR/$UNIT"

# Start at boot even before you log in (otherwise it starts at login).
if [[ $(loginctl show-user "$USER" -p Linger --value 2>/dev/null) != yes ]]; then
    if loginctl enable-linger "$USER" 2>/dev/null || sudo loginctl enable-linger "$USER"; then
        echo "Linger enabled: the panel starts at boot"
    else
        echo "Could not enable linger: the panel starts when you log in"
    fi
fi

source "$DIR/web/web.env" 2>/dev/null || true
for _ in $(seq 1 20); do
    (exec 3<>/dev/tcp/127.0.0.1/"${WEB_PORT:-8765}") 2>/dev/null && break; sleep 0.5
done
if systemctl --user is-active -q "$UNIT"; then
    echo "Running: http://localhost:${WEB_PORT:-8765}/"
else
    echo "Service failed to start:"; journalctl --user -u "$UNIT" -n 20 --no-pager; exit 1
fi
