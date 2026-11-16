#!/usr/bin/env python3
"""Render the sing-box config from proxy.env + gw.conf. Prints JSON to stdout.

Usage: render_config.py proxy.env gw.conf proxy|direct [api_secret] > sing-box.json
  proxy  - all phone traffic + DNS through the upstream proxy (paid traffic)
  direct - same hotspot, but straight out through this PC's own connection (real IP!)
Never prints credentials anywhere else.
"""

import json
import os
import sys

BLOCK_FILE = os.path.join(os.path.dirname(os.path.abspath(__file__)), "direct-block.txt")


def load_block_list() -> list[str]:
    try:
        lines = open(BLOCK_FILE, encoding="utf-8").read().splitlines()
    except FileNotFoundError:
        return []
    return [x.split("#", 1)[0].strip().lower().lstrip(".") for x in lines if x.split("#", 1)[0].strip()]


def load_env(path: str) -> dict[str, str]:
    out = {}
    for line in open(path, encoding="utf-8"):
        line = line.split("#", 1)[0].strip() if not line.lstrip().startswith(("PROXY_PASS", "PROXY_USER")) else line.strip()
        if "=" in line:
            k, v = line.split("=", 1)
            out[k.strip()] = v.strip().strip('"').strip("'")
    return out


def main() -> None:
    p, g = load_env(sys.argv[1]), load_env(sys.argv[2])
    mode = sys.argv[3] if len(sys.argv) > 3 else "proxy"
    secret = sys.argv[4] if len(sys.argv) > 4 else ""
    kind = p.get("PROXY_TYPE", "socks5").lower()
    common = {
        "tag": "proxy",
        "server": p["PROXY_HOST"],
        "server_port": int(p["PROXY_PORT"]),
        "username": p["PROXY_USER"],
        "password": p["PROXY_PASS"],
        "domain_resolver": "local",  # resolve the proxy's own hostname on the PC
    }
    if kind == "socks5":
        outbound = {"type": "socks", "version": "5", **common}
    elif kind in ("http", "https"):
        outbound = {"type": "http", **common}
    else:
        sys.exit(f"unsupported PROXY_TYPE {kind!r}")

    if mode == "direct":
        outbound = {"type": "direct", "tag": "proxy"}  # same tag so the rest of the config is identical
    elif mode != "proxy":
        sys.exit(f"unknown mode {mode!r}")

    cfg = {
        "log": {"level": p.get("LOG_LEVEL", "warn"), "timestamp": True},
        "dns": {
            "servers": [
                # phone DNS: DNS-over-HTTPS, sent THROUGH the proxy (no local DNS leak)
                {"type": "https", "tag": "remote", "server": "1.1.1.1", "detour": "proxy"},
                # only used to resolve the proxy server hostname itself
                {"type": "local", "tag": "local"},
            ],
            "final": "remote",
            "strategy": "ipv4_only",
        },
        "inbounds": [
            {"type": "redirect", "tag": "redir-in", "listen": g["GW_IP"], "listen_port": int(g["REDIR_PORT"])},
            {"type": "direct", "tag": "dns-in", "listen": g["GW_IP"], "listen_port": int(g["DNS_PORT"])},
        ]
        # loopback-only SOCKS inbound for tzsync.sh: looks up the exit IP through the SAME outbound
        # (same sticky session) the phone uses. Not reachable from the hotspot.
        + ([{"type": "socks", "tag": "probe-in", "listen": "127.0.0.1", "listen_port": int(g["PROBE_PORT"])}]
           if g.get("PROBE_PORT") else []),
        "outbounds": [outbound],
        "route": {
            "rules": [
                {"action": "sniff"},
                {"inbound": ["dns-in"], "action": "hijack-dns"},
                {"protocol": "dns", "action": "hijack-dns"},
            ],
            "final": "proxy",
            "default_domain_resolver": "local",
        },
    }
    if mode == "direct" and (block := load_block_list()):
        # Accounts must never be seen from the real IP: account-related domains get NXDOMAIN
        # from DNS, and any connection that still reaches them (cached DNS, hard-coded IP
        # with TLS SNI / HTTP Host) is rejected after sniffing.
        cfg["dns"]["rules"] = [{"domain_suffix": block, "action": "predefined", "rcode": "NXDOMAIN"}]
        cfg["route"]["rules"].append({"domain_suffix": block, "action": "reject"})
    if mode == "direct":
        # sing-box refuses a DNS detour to a plain direct outbound; DoH goes out directly anyway
        del cfg["dns"]["servers"][0]["detour"]
    if secret:
        # local stats API (traffic counters for proxy-status.sh), loopback only
        cfg["experimental"] = {"clash_api": {"external_controller": "127.0.0.1:9091", "secret": secret}}
    json.dump(cfg, sys.stdout, indent=2)
    print()


if __name__ == "__main__":
    main()
