#!/usr/bin/env bash
# Web control panel: script buttons, video uploads, live touch screen, Claude chat.
#   ./web.sh            start in this terminal (Ctrl+C stops), then open http://localhost:8765/
# As a background service (starts at boot):  web/install-service.sh   (see README)
# Settings: web/web.env
set -uo pipefail
source "$(dirname "$0")/lib.sh"
cd "$PROJECT_DIR"
[[ -x .venv/bin/python ]] || { bad "Python venv missing - run ./install.sh"; exit 1; }
if ! .venv/bin/python -c "import aiohttp" 2>/dev/null; then
    if command -v uv >/dev/null; then uv pip install --python .venv/bin/python aiohttp || exit 1
    else .venv/bin/python -m pip install -q aiohttp || exit 1; fi
fi
command -v claude >/dev/null || warn "'claude' CLI not in PATH - the chat panel will not work"
command -v scrcpy >/dev/null || warn "scrcpy not installed - the live screen will not work"

# Under systemd: start the adb server in its own transient unit, otherwise adb forks it
# inside this service and it (plus every adb user on this PC) dies on each restart.
if [[ -n ${INVOCATION_ID:-} ]] && command -v adb >/dev/null && ! (exec 3<>/dev/tcp/127.0.0.1/5037) 2>/dev/null; then
    systemctl --user is-active -q android-automation-adb.service \
        || systemd-run --user --quiet --collect --unit=android-automation-adb "$(command -v adb)" server nodaemon \
        || warn "could not start the adb server unit"
fi
exec .venv/bin/python web/server.py
