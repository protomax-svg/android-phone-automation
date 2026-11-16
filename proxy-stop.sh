#!/usr/bin/env bash
# Stop the phone gateway (either mode). The phone then has NO internet (by design).
# Records this session's traffic in logs/proxy-usage.log.
set -uo pipefail
source "$(dirname "$0")/lib.sh"
source "$PROJECT_DIR/proxy/common.sh"

mode=$(gw_mode)
if [[ -z $mode ]]; then echo "  - gateway not running"; exit 0; fi
read -r down up <<<"$(gw_traffic)"
if [[ -n ${down:-} ]]; then
    printf '%s %s %s %s %s\n' "$(date +%F)" "$(date +%T)" "$mode" "$down" "$up" >>"$USAGE_LOG"
fi
systemctl stop "android-proxy-gw@$mode.service" \
    && ok "gateway stopped ($mode mode, session traffic: $(human "${down:-0}") down / $(human "${up:-0}") up)"
