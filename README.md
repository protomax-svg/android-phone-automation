# android-automation

Control and automate the USB-connected Redmi Note 11 (Android 13 / HyperOS 1.0).

## Install (Ubuntu 24.04+ / Debian, or Arch)
```bash
git clone https://github.com/protomax-svg/android-phone-automation.git
cd android-phone-automation
./install.sh            # as your normal user; asks for sudo for packages
```
Installs Python 3 + venv, the phone udev rules (adds you to `plugdev`), scrcpy + adb from the official
scrcpy release (`/opt/scrcpy-v5.0.1`, linked into `/usr/local/bin`), the Python deps in `.venv/`, and
starts the web panel as a service. `./install.sh --no-service` skips the service.
The chat panel also needs [Claude Code](https://claude.com/claude-code): `curl -fsSL https://claude.ai/install.sh | bash`,
run `claude` once to log in, then `web/install-service.sh` again so the service sees it in `PATH`.
Then plug the phone in (USB debugging ON, accept the prompt) and run `./status.sh`.
On Ubuntu, log out/in once after the first install if adb shows `no permissions` (new `plugdev` group).

## Commands
| | |
|---|---|
| `./start.sh` | check ADB, open scrcpy window (mouse + keyboard control) |
| `./start.sh --screen-off` | same, phone panel stays dark (Alt+Shift+o turns it back on) |
| `./status.sh` | device / authorization / input-permission / environment health |
| `./demo.sh` | read-only demo: info → screenshot → Settings → UI dump → home |
| `./stop.sh [--device]` | stop project scrcpy/demo (and optionally the on-phone u2 server) |
| `.venv/bin/python scripts/interaction_test.py` | tap / swipe / text test inside Settings search |
| `.venv/bin/python -m android_auto --help` | CLI: info, screenshot, ui, tap, swipe, text, open, key, wait, click |
| `agent/agent.sh check` | DroidRun/mobilerun agent readiness (telemetry off) |
| `./web.sh` | **web control panel** at http://localhost:8765/ in this terminal (normally runs as a service, see below) |

Defaults for scrcpy live in `config.env` (max-size 1920: this phone's H.264 encoder
cannot do native 1080x2400 and would fall back to 488x1088).

## Python
```python
from android_auto import Phone
p = Phone()                       # auto-picks the single USB device, reconnects on failure
p.info(); p.screenshot("x.png")
p.open_app("com.android.settings")
p.wait_for(text="Wi-Fi", timeout=5)
for e in p.elements(): print(e)   # accessible UI elements with centers
p.tap(540, 1200); p.swipe_direction("up"); p.type_text("hello"); p.home()
```

## Layout
`android_auto/` library (adb.py low level, device.py Phone, ui.py hierarchy parser) ·
`scripts/` demo + tests · `agent/` LLM agent (separate Python 3.12 venv `.venv-agent`) ·
`output/` screenshots/dumps · `logs/` · `run/` pid files.

## Xiaomi notes
* Developer options › **USB debugging (Security settings)** must be ON (then reboot) for
  taps/keys/typing and scrcpy control; without it the phone is view-only.
* **Install via USB** is only needed for `agent/agent.sh setup` (Portal APK).

## Proxy gateway (phone internet only via the paid proxy)
PC Wi-Fi card = hotspot `phone-gw`; everything the phone sends is forced through sing-box.
| | |
|---|---|
| `./proxy-start.sh` | PROXY mode: US proxy IP, paid traffic (default, also at boot) |
| `./proxy-start.sh --direct` | DIRECT mode: free home internet, REAL IP; account apps blocked (`proxy/direct-block.txt`) |
| `./proxy-start.sh --connect-phone` | (re)join the phone to the hotspot if it stops auto-joining |
| `./proxy-stop.sh` | phone offline; logs session traffic to `logs/proxy-usage.log` |
| `./proxy-status.sh` | mode, traffic used, phone IP / DNS / IPv6 leak checks |

* Optional. Proxy credentials: copy `proxy/proxy.env.example` → `proxy/proxy.env` (git-ignored), set `IFACE` (PC Wi-Fi card)
  and `PHONE_SERIAL` in `proxy/gw.conf`, then `sudo proxy/install.sh` (Arch or Ubuntu 24.04+; needs NetworkManager). Settings: `proxy/gw.conf` (`AUTOSTART`).
* Boot: starts in PROXY mode. Sleep: stopped before, same mode restored after resume.
* Kill switch: gateway down = phone offline (no SIM, no other saved Wi-Fi).
* Timezone follows the exit IP (`TZ_SYNC=1`): at start the phone gets only DHCP until its
  timezone = the exit IP's timezone (set over USB via adb); re-checked every `TZ_CHECK_SEC`.
  If the proxy rotates to another timezone: hold → change → release. Phone not on USB and
  timezone differs → stays offline until plugged in. Log: `journalctl -u android-proxy-gw-tz@proxy`.
* `PRIVACY_ENFORCE=1`: location, Wi-Fi/BT scanning, auto/geo timezone kept OFF on the phone
  (re-applied every check while on USB); phone clock set from the PC (NTP/UDP is blocked).
* Remove: `sudo proxy/uninstall.sh`.

## Web control panel (`./web.sh` → http://localhost:8765/)
Runs as a systemd **user** service (`android-automation-web`), started at boot (linger) - installed by
`./install.sh`, or alone with `web/install-service.sh` (re-run it after moving the folder or changing `PATH`).
| | |
|---|---|
| `systemctl --user status android-automation-web` | state |
| `systemctl --user restart android-automation-web` | restart (e.g. after editing `web/web.env` or the code) |
| `journalctl --user -u android-automation-web -f` | log |
| `web/install-service.sh --uninstall` | remove the service |

* **Phone**: live screen (scrcpy H.264 stream decoded in the browser). Click/drag = touch, wheel = scroll,
  right-click = Back, middle-click = Home, keyboard types when the screen has focus, Ctrl+V pastes (Unicode via
  uiautomator2). Buttons: Back/Home/Recents/Volume/Power, and turning the phone's own panel off/on.
* **Claude**: chat that runs `claude -p` in this folder (session kept between messages, "New chat" resets). It
  drives the phone through `android_auto`/`adb` only. Allow/deny lists live in `web/server.py`
  (`ALLOWED_TOOLS`/`DISALLOWED_TOOLS`): proxy-start/stop, `--direct`, Wi-Fi/data changes, systemctl and sudo are blocked.
* **Scripts**: buttons for the scripts above. There is intentionally **no** button for DIRECT (real IP) mode.
* **Files**: upload videos → `uploads/`, copied to the phone at `/sdcard/Movies/Upload/` and media-scanned (newest
  in the gallery). "Post…" pre-fills a publish request for Claude for the ticked networks.
* Settings: `web/web.env` (host/port, model, spend cap). Binds to 127.0.0.1 only by default. Chat log: `logs/web-chat.json`.

## Several phones
* `proxy/gw.conf` → `PHONE_SERIAL` = the ONE phone behind the proxy gateway (Redmi, `e0296b53`). The proxy scripts and
  the timezone/privacy service only ever touch that phone. After changing it: `sudo proxy/install.sh`.
* Every other phone is "direct": its own internet, real IP, never joined to `phone-gw`.
* Web panel: phone tabs at the top; screen, scripts, file copies and the Claude chat follow the selected phone
  (each phone has its own chat; `logs/web-chat-<serial>.json`). Proxy buttons only show for the gateway phone.
* CLI with two phones plugged in: prefix commands with the phone, e.g. `ANDROID_SERIAL=5200bfaf4706c503 ./start.sh`.
