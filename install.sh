#!/usr/bin/env bash
# Install android-automation on Ubuntu/Debian (apt) or Arch (pacman). Run as your normal user
# from the cloned repo; it asks for sudo for the system parts.
#   ./install.sh                 packages + scrcpy + Python venv + web panel service
#   ./install.sh --no-service    same, but do not install/start the web panel service
#
# System changes:
#   apt:    python3, python3-venv, curl, android-sdk-platform-tools-common (udev rules for phones);
#           scrcpy + adb from the official scrcpy release -> /opt/scrcpy-vX, linked in /usr/local/bin;
#           your user is added to the 'plugdev' group (USB access to the phone)
#   pacman: python, android-tools, scrcpy
#   user:   .venv/ in this folder; systemd user service android-automation-web (web/install-service.sh)
# The proxy gateway is separate and optional: copy proxy/proxy.env.example -> proxy/proxy.env,
# check proxy/gw.conf (IFACE, PHONE_SERIAL), then: sudo proxy/install.sh
set -euo pipefail
DIR="$(cd "$(dirname "$0")" && pwd)"
cd "$DIR"
SCRCPY_VERSION=5.0.1
SCRCPY_SHA256=9f969d30cc574816077edecda65719c1c70b0aee1df1ba5fa00779ac07b8fd6e   # scrcpy-linux-x86_64-v5.0.1.tar.gz
SERVICE=1
for a in "$@"; do
    case $a in
        --no-service) SERVICE=0 ;;
        -h|--help) sed -n '2,15p' "$0"; exit 0 ;;
        *) echo "unknown option $a"; exit 2 ;;
    esac
done
[[ $EUID != 0 ]] || { echo "Run as your normal user (not sudo): ./install.sh"; exit 1; }
say() { echo; echo "== $*"; }

if command -v apt-get >/dev/null; then PM=apt
elif command -v pacman >/dev/null; then PM=pacman
else echo "Unsupported distro (need apt or pacman)"; exit 1; fi

say "system packages ($PM)"
if [[ $PM == apt ]]; then
    sudo apt-get update -q
    sudo DEBIAN_FRONTEND=noninteractive apt-get install -y -q \
        python3 python3-venv python3-pip curl ca-certificates android-sdk-platform-tools-common
    if ! id -nG "$USER" | grep -qw plugdev; then
        sudo usermod -aG plugdev "$USER"
        echo "   added $USER to 'plugdev' (USB access) - log out/in or reboot once if the phone shows 'no permissions'"
    fi
    sudo udevadm control --reload-rules 2>/dev/null || true
else
    sudo pacman -S --needed --noconfirm python android-tools scrcpy
fi

if [[ $PM == apt ]]; then
    say "scrcpy $SCRCPY_VERSION (official release, includes a matching adb)"
    cur=$(scrcpy --version 2>/dev/null | sed -n 's/^scrcpy \([0-9.]*\).*/\1/p' | head -1 || true)
    if [[ $cur == "$SCRCPY_VERSION" ]]; then
        echo "   already installed"
    else
        [[ $(uname -m) == x86_64 ]] || { echo "   no official scrcpy build for $(uname -m): install scrcpy >= 3 yourself"; exit 1; }
        name=scrcpy-linux-x86_64-v$SCRCPY_VERSION
        tmp=$(mktemp -d)
        curl -fL --progress-bar -o "$tmp/$name.tar.gz" \
            "https://github.com/Genymobile/scrcpy/releases/download/v$SCRCPY_VERSION/$name.tar.gz"
        echo "$SCRCPY_SHA256  $tmp/$name.tar.gz" | sha256sum -c --quiet
        tar -xzf "$tmp/$name.tar.gz" -C "$tmp"
        sudo rm -rf "/opt/scrcpy-v$SCRCPY_VERSION"
        sudo install -d -m 755 "/opt/scrcpy-v$SCRCPY_VERSION"
        sudo cp -a "$tmp/$name/." "/opt/scrcpy-v$SCRCPY_VERSION/"
        rm -rf "$tmp"
        sudo ln -sfn "/opt/scrcpy-v$SCRCPY_VERSION/scrcpy" /usr/local/bin/scrcpy
        # one adb for everything (scrcpy's own) - two different adb versions keep killing each other's server
        sudo ln -sfn "/opt/scrcpy-v$SCRCPY_VERSION/adb" /usr/local/bin/adb
        sudo install -d /usr/local/share/man/man1
        sudo ln -sfn "/opt/scrcpy-v$SCRCPY_VERSION/scrcpy.1" /usr/local/share/man/man1/scrcpy.1
        echo "   installed /opt/scrcpy-v$SCRCPY_VERSION (scrcpy + adb in /usr/local/bin)"
    fi
fi

say "Python venv (.venv)"
python3 -c 'import sys; sys.exit(sys.version_info < (3, 10))' || { echo "   Python >= 3.10 required"; exit 1; }
[[ -x .venv/bin/python ]] || python3 -m venv .venv
.venv/bin/python -m pip install -q --upgrade pip
.venv/bin/python -m pip install -q -r requirements.txt -c requirements.lock
echo "   OK ($(.venv/bin/python --version))"

say "checks"
command -v adb >/dev/null && echo "   adb:    $(adb version | head -1)" || echo "   adb:    MISSING"
command -v scrcpy >/dev/null && echo "   scrcpy: $(scrcpy --version 2>/dev/null | head -1)" || echo "   scrcpy: MISSING"
if command -v claude >/dev/null; then
    echo "   claude: $(command -v claude)"
else
    echo "   claude: not found - the panel's chat needs Claude Code:"
    echo "           curl -fsSL https://claude.ai/install.sh | bash   then run 'claude' once to log in,"
    echo "           then re-run: web/install-service.sh   (so the service picks up the new PATH)"
fi

if ((SERVICE)); then
    say "web panel service"
    "$DIR/web/install-service.sh"
fi

echo
echo "Done. Plug the phone in via USB (USB debugging ON, accept the prompt), then: ./status.sh"
