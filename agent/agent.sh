#!/usr/bin/env bash
# Wrapper for the DroidRun/mobilerun agent venv (telemetry always off).
#   agent/agent.sh check              readiness check (no network, no phone changes)
#   agent/agent.sh setup              install + enable Mobilerun Portal on the phone (asks first)
#   agent/agent.sh run --goal "..."   run an agent (see agent/run_agent.py --help)
#   agent/agent.sh cli <args>         raw `mobilerun` CLI
set -euo pipefail
DIR="$(cd "$(dirname "$0")/.." && pwd)"
PY="$DIR/.venv-agent/bin/python"
export MOBILERUN_TELEMETRY_ENABLED=false DROIDRUN_TELEMETRY_ENABLED=false
[[ -x $PY ]] || { echo "agent venv missing: cd $DIR && uv venv --python 3.12 .venv-agent && uv pip install --python .venv-agent/bin/python 'droidrun[anthropic]'"; exit 1; }

case ${1:-} in
    setup)
        echo "This installs the Mobilerun Portal APK on the phone and enables it as an"
        echo "ACCESSIBILITY SERVICE (it can read the screen and act on it). Needed only for the AI agent."
        echo "On Xiaomi also enable 'Install via USB' in Developer options."
        read -rp "Proceed? [y/N] " a; [[ $a == [yY] ]] || exit 1
        shift; exec "$DIR/.venv-agent/bin/mobilerun" setup "$@" ;;
    cli) shift; exec "$DIR/.venv-agent/bin/mobilerun" "$@" ;;
    *)   exec "$PY" "$DIR/agent/run_agent.py" "$@" ;;
esac
