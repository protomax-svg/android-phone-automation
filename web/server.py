"""Phone control web UI: script buttons, video uploads, live touch screen, Claude chat.

Run with ./web.sh (binds 127.0.0.1:8765 by default, see web/web.env).
"""

from __future__ import annotations

import asyncio
import io
import json
import logging
import os
import re
import hashlib
import shlex
import shutil
import signal
import sys
import time
from pathlib import Path
from urllib.parse import quote

from aiohttp import WSMsgType, web

ROOT = Path(__file__).resolve().parent.parent
WEB = ROOT / "web"
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(WEB))

from scrcpy_stream import Packet, ScrcpySession  # noqa: E402

UPLOAD_DIR = ROOT / "uploads"
WORK_DIR = ROOT / "work"
SHOT_DIR = ROOT / "output"
CHAT_DIR = ROOT / "logs"
PHONE_UPLOAD_DIR = "/sdcard/Movies/Upload"
for d in (UPLOAD_DIR, WORK_DIR, SHOT_DIR, CHAT_DIR):
    d.mkdir(parents=True, exist_ok=True)

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
log = logging.getLogger("web")

# --------------------------------------------------------------------------- scripts
# Fixed command lines only - nothing from the browser is ever passed to a shell.
# There is deliberately NO entry that switches the gateway to DIRECT (real IP) mode.
SCRIPTS: dict[str, list[str]] = {
    "status": ["./status.sh"],
    "info": [".venv/bin/python", "-m", "android_auto", "info"],
    "demo": ["./demo.sh"],
    "interaction-test": [".venv/bin/python", "scripts/interaction_test.py"],
    "scrcpy-start": ["./start.sh"],
    "scrcpy-start-screen-off": ["./start.sh", "--screen-off"],
    "stop": ["./stop.sh"],
    "stop-device": ["./stop.sh", "--device"],
    "proxy-start": ["./proxy-start.sh"],
    "proxy-connect-phone": ["./proxy-start.sh", "--connect-phone"],
    "proxy-stop": ["./proxy-stop.sh"],
    "proxy-status": ["./proxy-status.sh"],
    "agent-check": ["agent/agent.sh", "check"],
    "privacy-check": ["./privacy.sh"],
    "privacy-fix": ["./privacy.sh", "--fix"],
}
# These act on the proxy gateway / the gateway phone only (proxy/common.sh pins PHONE_SERIAL),
# whichever phone is selected in the panel.
GATEWAY_SCRIPTS = {"proxy-start", "proxy-connect-phone", "proxy-stop", "proxy-status"}
running_scripts: set[str] = set()
ANSI = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")


async def run_script(request: web.Request) -> web.StreamResponse:
    name = request.match_info["name"]
    cmd = SCRIPTS.get(name)
    if cmd is None:
        raise web.HTTPNotFound(text="unknown script")
    if name in GATEWAY_SCRIPTS:
        serial, key = GATEWAY_SERIAL, name
    else:
        serial = await serial_from(request)
        key = f"{serial}:{name}"
    if key in running_scripts:
        raise web.HTTPConflict(text=f"{name} is already running")
    resp = web.StreamResponse(headers={"Content-Type": "text/plain; charset=utf-8", "Cache-Control": "no-cache",
                                       "X-Accel-Buffering": "no"})
    await resp.prepare(request)
    running_scripts.add(key)
    client_gone = False

    async def out(text: str) -> None:
        nonlocal client_gone
        if client_gone:
            return
        try:
            await resp.write(text.encode())
        except (ConnectionResetError, RuntimeError):
            client_gone = True  # keep the script running, just stop streaming

    try:
        await out(f"$ ANDROID_SERIAL={serial} {' '.join(cmd)}\n")
        env = {**os.environ, "TERM": "dumb", "PYTHONUNBUFFERED": "1"}
        if serial:
            env["ANDROID_SERIAL"] = serial
        proc = await asyncio.create_subprocess_exec(*cmd, cwd=ROOT, env=env, stdin=asyncio.subprocess.DEVNULL,
                                                    stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
        assert proc.stdout
        while True:
            chunk = await proc.stdout.read(4096)
            if not chunk:
                break
            await out(ANSI.sub("", chunk.decode(errors="replace")))
        rc = await proc.wait()
        await out(f"\n[exit code {rc}]\n")
    finally:
        running_scripts.discard(key)
    return resp


# --------------------------------------------------------------------------- devices
def _gateway_serial() -> str:
    for line in (ROOT / "proxy" / "gw.conf").read_text().splitlines():
        m = re.match(r"\s*PHONE_SERIAL=([^\s#]+)", line)
        if m:
            return m.group(1)
    return ""


# open pages compare this with the value they started with and reload themselves after an update
PAGE_BUILD = hashlib.sha256((WEB / "static" / "index.html").read_bytes()).hexdigest()[:12]
GATEWAY_SERIAL = _gateway_serial()   # the phone behind the proxy gateway; every other phone is "direct"
_dev_props: dict[str, dict] = {}


async def _exec(*cmd: str, timeout: float = 600) -> tuple[int, str]:
    p = await asyncio.create_subprocess_exec(*cmd, stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.STDOUT)
    try:
        out, _ = await asyncio.wait_for(p.communicate(), timeout)
    except asyncio.TimeoutError:
        p.kill()
        return 124, "timeout"
    return p.returncode or 0, out.decode(errors="replace")


async def _props(serial: str) -> dict:
    if serial not in _dev_props:
        rc, out = await _exec("adb", "-s", serial, "shell",
                              "getprop ro.product.manufacturer; getprop ro.product.marketname; getprop ro.product.model;"
                              "getprop ro.build.version.release; wm size | tail -1", timeout=15)
        lines = [l.strip() for l in out.replace("\r", "").split("\n")] + [""] * 5
        if rc != 0:
            return {}
        size = re.search(r"(\d+)x(\d+)", lines[4])
        manu, market, model = lines[0].capitalize(), lines[1], lines[2]
        _dev_props[serial] = {
            "name": market or f"{manu} {model}".strip(), "manufacturer": manu, "model": model,
            "android": lines[3], "width": int(size.group(1)) if size else 0, "height": int(size.group(2)) if size else 0,
        }
    return _dev_props[serial]


async def list_devices() -> list[dict]:
    rc, out = await _exec("adb", "devices", "-l", timeout=15)
    devs = []
    for line in out.splitlines()[1:]:
        parts = line.split()
        if len(parts) < 2:
            continue
        serial, state = parts[0], parts[1]
        d = {"serial": serial, "state": state, "role": "proxy" if serial == GATEWAY_SERIAL else "direct",
             "name": next((p.split(":", 1)[1].replace("_", " ") for p in parts[2:] if p.startswith("model:")), serial)}
        if state == "device":
            d.update(await _props(serial))
        devs.append(d)
    devs.sort(key=lambda d: (d["role"] != "proxy", d["serial"]))
    return devs


async def api_devices(request: web.Request) -> web.Response:
    devs = await list_devices()
    for d in devs:
        c = chats.get(d["serial"])
        d["chat_running"] = bool(c and c.running)
    return web.json_response({"devices": devs, "gateway_serial": GATEWAY_SERIAL, "build": PAGE_BUILD})


async def client_log(request: web.Request) -> web.Response:
    """Video diagnostics the page sends once per stream (decoder state, frames, black canvas)."""
    body = await request.json()
    log.info("client video report: %s", json.dumps(body)[:600])
    return web.json_response({"ok": True})


async def serial_from(request: web.Request) -> str:
    """The phone a request is about (?serial=...), validated against the connected devices."""
    devs = [d for d in await list_devices() if d["state"] == "device"]
    want = request.query.get("serial")
    if want:
        if any(d["serial"] == want for d in devs):
            return want
        raise web.HTTPServiceUnavailable(text=f"phone {want} is not connected / not authorized")
    if len(devs) == 1:
        return devs[0]["serial"]
    raise web.HTTPBadRequest(text="several phones connected: choose one")


async def adb(serial: str, *args: str, timeout: float = 600) -> tuple[int, str]:
    return await _exec("adb", "-s", serial, *args, timeout=timeout)


_phones: dict[str, object] = {}


def phone(serial: str):
    """Cached uiautomator2-backed Phone per device (Unicode typing)."""
    if serial not in _phones:
        from android_auto import Phone
        _phones[serial] = Phone(serial)
    return _phones[serial]


async def phone_type(request: web.Request) -> web.Response:
    body = await request.json()
    text = str(body.get("text", ""))
    if not text:
        raise web.HTTPBadRequest(text="empty text")
    serial = await serial_from(request)
    try:
        await asyncio.to_thread(lambda: phone(serial).type_text(text))
    except Exception as e:  # noqa: BLE001
        _phones.pop(serial, None)
        raise web.HTTPInternalServerError(text=f"typing failed: {e}")
    return web.json_response({"ok": True})


async def screenshot(request: web.Request) -> web.Response:
    """JPEG of the phone screen (fallback when the browser cannot decode the H.264 stream)."""
    serial = await serial_from(request)
    p = await asyncio.create_subprocess_exec("adb", "-s", serial, "exec-out", "screencap", "-p",
                                             stdout=asyncio.subprocess.PIPE, stderr=asyncio.subprocess.DEVNULL)
    data, _ = await p.communicate()
    if not data.startswith(b"\x89PNG"):
        raise web.HTTPServiceUnavailable(text="screencap failed")

    def to_jpeg() -> tuple[bytes, int, int]:
        from PIL import Image
        im = Image.open(io.BytesIO(data)).convert("RGB")
        w, h = im.size
        im.thumbnail((720, 720 * 3))
        buf = io.BytesIO()
        im.save(buf, "JPEG", quality=70)
        return buf.getvalue(), w, h

    jpg, w, h = await asyncio.to_thread(to_jpeg)
    return web.Response(body=jpg, content_type="image/jpeg",
                        headers={"Cache-Control": "no-store", "X-Device-Size": f"{w}x{h}"})


# --------------------------------------------------------------------------- live screen
async def ws_screen(request: web.Request) -> web.WebSocketResponse:
    ws = web.WebSocketResponse(max_msg_size=0, heartbeat=20)
    await ws.prepare(request)
    want_video = request.query.get("video", "1") != "0"
    max_size = int(request.query.get("max_size", "1280"))
    sess: ScrcpySession | None = None
    pump: asyncio.Task | None = None
    try:
        try:
            sess = ScrcpySession(await serial_from(request), max_size=max(480, min(max_size, 1920)))
            await sess.start()
        except Exception as e:  # noqa: BLE001
            await ws.send_json({"type": "error", "message": str(e)})
            return ws
        await ws.send_json({"type": "hello", "device": sess.device_name})

        async def pump_video() -> None:
            assert sess
            try:
                async for p in sess.packets():
                    if isinstance(p, tuple):
                        await ws.send_json({"type": "size", "width": p[1], "height": p[2]})
                    elif want_video:
                        assert isinstance(p, Packet)
                        flags = (1 if p.config else 0) | (2 if p.key else 0)
                        await ws.send_bytes(bytes([flags]) + p.pts.to_bytes(8, "big") + p.data)
            except (asyncio.IncompleteReadError, ConnectionResetError):
                pass
            except Exception as e:  # noqa: BLE001
                log.warning("video pump: %s", e)
            if not ws.closed:
                try:
                    await ws.send_json({"type": "error", "message": "video stream ended"})
                    await ws.close()
                except Exception:  # noqa: BLE001
                    pass

        pump = asyncio.create_task(pump_video())
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            t = m.get("t")
            if t == "touch":
                sess.touch(int(m["a"]), int(m["x"]), int(m["y"]), pointer_id=int(m.get("id", -2)))
            elif t == "scroll":
                sess.scroll(int(m["x"]), int(m["y"]), float(m.get("h", 0)), float(m.get("v", 0)))
            elif t == "key":
                sess.key(int(m["code"]), m.get("action"), int(m.get("meta", 0)))
            elif t == "text":
                sess.text(str(m["text"]))
            elif t == "power":
                sess.display_power(bool(m["on"]))
            elif t == "reset":
                sess.reset_video()
    except (ConnectionResetError, RuntimeError) as e:
        log.info("screen client went away: %s", e)
    finally:
        if pump:
            pump.cancel()
        if sess:
            await sess.stop()   # always stop the on-phone server, even if the browser vanished
        if not ws.closed:
            await ws.close()
    return ws


# --------------------------------------------------------------------------- uploads
SAFE = re.compile(r"[^A-Za-z0-9._ -]+")


def safe_name(name: str) -> str:
    name = SAFE.sub("_", Path(name).name).strip(" .") or "file"
    stem, suf = os.path.splitext(name)
    cand, i = name, 1
    while (UPLOAD_DIR / cand).exists():
        cand = f"{stem}-{i}{suf}"
        i += 1
    return cand


def file_entry(p: Path) -> dict:
    st = p.stat()
    meta = UPLOAD_DIR / ".meta" / (p.name + ".json")
    info = json.loads(meta.read_text()) if meta.exists() else {}
    phones = dict(info.get("phones", {}))
    if info.get("phone_path") and GATEWAY_SERIAL:   # files pushed before multi-phone support
        phones.setdefault(GATEWAY_SERIAL, info["phone_path"])
    return {"name": p.name, "size": st.st_size, "mtime": int(st.st_mtime), "phones": phones}


def set_meta(name: str, **kw) -> None:
    d = UPLOAD_DIR / ".meta"
    d.mkdir(exist_ok=True)
    f = d / (name + ".json")
    info = json.loads(f.read_text()) if f.exists() else {}
    info.update(kw)
    f.write_text(json.dumps(info))


async def list_files(request: web.Request) -> web.Response:
    files = sorted((p for p in UPLOAD_DIR.iterdir() if p.is_file()), key=lambda p: p.stat().st_mtime, reverse=True)
    return web.json_response([file_entry(p) for p in files])


async def ashell(serial: str, *args: str) -> tuple[int, str]:
    """adb shell with every argument quoted for the phone's shell (file names may contain spaces)."""
    return await adb(serial, "shell", shlex.join(args))


async def media_scan(serial: str, path: str) -> None:
    await ashell(serial, "am", "broadcast", "-a", "android.intent.action.MEDIA_SCANNER_SCAN_FILE",
                 "-d", "file://" + quote(path))


async def push_to_phone(serial: str, name: str) -> str:
    src = UPLOAD_DIR / name
    dst = f"{PHONE_UPLOAD_DIR}/{name}"
    await ashell(serial, "mkdir", "-p", PHONE_UPLOAD_DIR)
    rc, out = await adb(serial, "push", str(src), dst)
    if rc != 0:
        raise RuntimeError(out.strip())
    # make it show up in the gallery / app pickers as the newest item
    await ashell(serial, "touch", dst)
    await media_scan(serial, dst)
    phones = file_entry(src)["phones"]
    phones[serial] = dst
    set_meta(name, phones=phones, phone_path=None)
    return dst


async def upload(request: web.Request) -> web.Response:
    reader = await request.multipart()
    push = request.query.get("push", "1") == "1"
    serial = await serial_from(request) if push else ""
    saved = []
    async for part in reader:
        if part.name != "file" or not part.filename:
            continue
        name = safe_name(part.filename)
        tmp = UPLOAD_DIR / f".part-{name}"
        with tmp.open("wb") as f:
            while chunk := await part.read_chunk(1 << 20):
                f.write(chunk)
        tmp.rename(UPLOAD_DIR / name)
        saved.append(name)
    result = []
    for name in saved:
        entry = {"name": name}
        if push:
            try:
                entry["phone_path"] = await push_to_phone(serial, name)
            except Exception as e:  # noqa: BLE001
                entry["push_error"] = str(e)
        result.append(entry)
    return web.json_response(result)


def _upload_path(name: str) -> Path:
    p = (UPLOAD_DIR / name).resolve()
    if p.parent != UPLOAD_DIR.resolve() or not p.is_file() or p.name.startswith("."):
        raise web.HTTPNotFound()
    return p


async def file_push(request: web.Request) -> web.Response:
    p = _upload_path(request.match_info["name"])
    serial = await serial_from(request)
    try:
        return web.json_response({"phone_path": await push_to_phone(serial, p.name)})
    except Exception as e:  # noqa: BLE001
        raise web.HTTPInternalServerError(text=str(e))


async def file_delete(request: web.Request) -> web.Response:
    p = _upload_path(request.match_info["name"])
    info = file_entry(p)
    connected = {d["serial"] for d in await list_devices() if d["state"] == "device"}
    left_on = []
    if request.query.get("phone", "1") == "1":
        for serial, path in info["phones"].items():
            if serial not in connected:
                left_on.append(serial)
                continue
            await ashell(serial, "rm", "-f", path)
            await media_scan(serial, path)  # scanning a deleted path drops it from the gallery
    p.unlink()
    (UPLOAD_DIR / ".meta" / (p.name + ".json")).unlink(missing_ok=True)
    return web.json_response({"ok": True, "not_removed_from": left_on})


async def file_get(request: web.Request) -> web.FileResponse:
    return web.FileResponse(_upload_path(request.match_info["name"]))


async def output_get(request: web.Request) -> web.FileResponse:
    """Images Claude looked at (screenshots under output/ or work/)."""
    p = (ROOT / request.match_info["path"]).resolve()
    if not any(p.is_relative_to(d.resolve()) for d in (SHOT_DIR, WORK_DIR)) or not p.is_file():
        raise web.HTTPNotFound()
    return web.FileResponse(p)


# --------------------------------------------------------------------------- Claude chat
def _load_env(path: Path) -> dict[str, str]:
    out = {}
    if path.exists():
        for line in path.read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                out[k.strip()] = v.strip().strip('"').strip("'")
    return out


CFG = _load_env(WEB / "web.env")

def system_prompt(dev: dict) -> str:
    serial = dev["serial"]
    res = f"{dev.get('width', 0)}x{dev.get('height', 0)}"
    if dev["role"] == "proxy":
        network = ("This phone reaches the internet ONLY through the PC's paid proxy gateway (US exit IP).\n"
                   "- NEVER change how the phone reaches the internet: do not run proxy-start.sh/proxy-stop.sh, never use\n"
                   "  --direct, do not touch Wi-Fi networks, mobile data, VPN, location or timezone settings.")
    else:
        network = ("This phone is NOT behind the proxy gateway: it uses its own normal internet connection (real IP).\n"
                   "- Never connect it to the 'phone-gw' hotspot and never run the proxy scripts.\n"
                   "- Do not change its Wi-Fi / mobile data / VPN settings unless the user explicitly asks.")
    return f"""You are driving a real Android phone ({dev.get('name', '')}, {dev.get('manufacturer', '')} {dev.get('model', '')},
Android {dev.get('android', '?')}, screen {res}) connected to this PC over USB (serial {serial}), on behalf of the
user chatting with you from a web control panel. The user sees this phone's screen live and may touch it too.
Other phones may be connected to the PC as well: work ONLY on this one. ANDROID_SERIAL={serial} is already set for
`adb` and `android_auto`, so never pass -s/--serial and never address another device.

How to operate the phone (working directory = project root {ROOT}):
- READ THE SCREEN WITH THE UI ELEMENT LIST, NOT SCREENSHOTS:
  `.venv/bin/python -m android_auto ui` lists the current app package and every on-screen element as
  `#idx [flags] class 'text or description' @(x, y)` - flags C=clickable S=scrollable F=focused, (x, y) = exact
  center in device pixels ({res}). Add --json for resource ids and bounds, --all to include layout containers.
- Tap elements by their label or by the exact center from that list:
  `click --text "Next"` / `click --desc "Profile"` / `click --id <resource-id>` or `tap X Y` with the listed center.
  Never estimate tap coordinates when the element is in the list.
- After each action, wait (sleep 1-3 s or `wait --text "..."`) and run `ui` again to confirm the result.
- Screenshots ONLY when images are involved: choosing a photo/video from gallery thumbnails, checking a media
  preview, crop or cover frame, or reading something that exists only as a picture.
  The general screenshot/screencap commands are BLOCKED. The only way is:
  `.venv/bin/python web/media_shot.py "<which image you need to see>"` -> prints a PNG path; Read it
  (coordinates = device pixels). Never use it for buttons, menus, text, settings or to "check" a step.
  Exception: if `ui` returns no usable elements for the screen (web page / custom-drawn view), media_shot is allowed.
- SHOW THE USER YOUR PROGRESS: after each meaningful step (app opened, screen/tab reached, video picked, caption
  filled in, right before the final Post/Share/Save tap, and the final result) run
  `.venv/bin/python web/show_shot.py "<short caption of the step>"` - it puts a picture of the phone screen into the
  user's chat. It is ONLY for the user: do NOT Read/open/analyze these pictures (work/shots/ is blocked for you) and
  never rely on them; you still read the screen with `ui`. Not after every tap or scroll - roughly one per step.
  Combine it with the check in one command, e.g. `.venv/bin/python -m android_auto ui; .venv/bin/python web/show_shot.py "TikTok opened"`.
  Never put the word "screenshot" in the caption (that word is blocked in commands).
- Other actions: `.venv/bin/python -m android_auto swipe up|down|left|right | swipe X1 Y1 X2 Y2 |
  text "..." [--clear] | key back|home|enter|recent | open <package> | wait --text "..." [--timeout N]`.
  `text` is Unicode-safe (Cyrillic, emoji) and types into the focused field.
- Installed apps: `.venv/bin/python -m android_auto packages`. Raw `adb` is also available.
- For multi-step logic write a Python script in work/ (from android_auto import Phone; p = Phone();
  p.elements(), p.tap_element(text=...)) and run it with .venv/bin/python.
- Before reporting success, confirm the final state with `ui` and show it with show_shot.py.

Files the user uploaded through the panel (mostly videos to publish on social networks):
- On the PC: {UPLOAD_DIR}/<name>
- On this phone (when copied to it): {PHONE_UPLOAD_DIR}/<name> (media-scanned, newest item in the gallery / pickers).
  If a file is only on the PC: `adb push "{UPLOAD_DIR}/<name>" "{PHONE_UPLOAD_DIR}/<name>"` then
  `adb shell am broadcast -a android.intent.action.MEDIA_SCANNER_SCAN_FILE -d "file://{PHONE_UPLOAD_DIR}/<name>"`.
To publish a video: open the app (TikTok com.zhiliaoapp.musically, Instagram com.instagram.android,
YouTube com.google.android.youtube, Facebook com.facebook.katana, etc.), use its create/+ flow, pick the video from
the gallery, add the caption/hashtags the user asked for, and post. Before the final "Post/Share" tap, make sure the
right video and caption are selected.

Rules:
- {network}
- NEVER turn on location, Bluetooth, Wi-Fi/Bluetooth scanning, Samsung "Nearby device scanning", Find My Mobile /
  Find My Device, or allow an app's location permission or "turn on location?" prompt - decline those. Nothing may
  reveal where this phone is. Check anytime with `./privacy.sh` (read-only).
- If an app asks for a login, password, 2FA code or CAPTCHA you were not given, stop and ask the user.
- Do not uninstall apps, factory reset, or delete user data other than files in {PHONE_UPLOAD_DIR}.
- Reply briefly, in the language the user writes in. Say what you did and what you saw.
"""


ALLOWED_TOOLS = [
    "Bash(adb *)", "Bash(.venv/bin/python *)", "Bash(./status.sh*)", "Bash(./proxy-status.sh*)", "Bash(./privacy.sh)",
    "Bash(sleep *)", "Bash(ls *)", "Read", "Glob", "Grep", "Write(./work/**)", "Edit(./work/**)",
]
DISALLOWED_TOOLS = [
    "Bash(*proxy-start.sh*)", "Bash(*proxy-stop.sh*)", "Bash(*--direct*)", "Bash(*gw.sh*)",
    "Bash(*systemctl*)", "Bash(*sudo*)", "Bash(*svc wifi*)", "Bash(*cmd wifi*)", "Bash(*svc data*)",
    "Bash(*install.sh*)",
    # location privacy: nothing that turns location / Bluetooth / scanning back on
    "Bash(*location_mode*)", "Bash(*set-location-enabled*)", "Bash(*location_providers_allowed*)",
    "Bash(*bluetooth*enable*)", "Bash(*bluetooth_on*)", "Bash(*scan_always_enabled*)", "Bash(*assisted_gps*)",
    "Bash(*nearby_scanning*)", "Bash(*privacy.sh --fix*)",
    # screen must be read with `android_auto ui`; images only via web/media_shot.py
    "Bash(*screenshot*)", "Bash(*screencap*)", "Bash(*screenrecord*)",
    # progress pictures (web/show_shot.py) are for the user only - the agent must not look at them
    "Read(./work/shots/**)",
    # each chat is pinned to its phone through ANDROID_SERIAL; no addressing other devices
    "Bash(*adb -s*)", "Bash(*adb -t*)", "Bash(*android_auto -s*)", "Bash(*--serial*)", "Bash(*ANDROID_SERIAL=*)",
]


def prompt_version(prompt: str) -> str:
    # A Claude session keeps its earlier habits; start a new one whenever these instructions change.
    return hashlib.sha256((prompt + repr(ALLOWED_TOOLS) + repr(DISALLOWED_TOOLS)).encode()).hexdigest()[:12]


class Chat:
    def __init__(self, serial: str) -> None:
        self.serial = serial
        # the gateway phone keeps the original log file name
        self.file = CHAT_DIR / ("web-chat.json" if serial == GATEWAY_SERIAL else f"web-chat-{serial}.json")
        self.events: list[dict] = []
        self.session_id: str | None = None
        self.proc: asyncio.subprocess.Process | None = None
        self.clients: set[web.WebSocketResponse] = set()
        self.prompt_version: str | None = None
        if self.file.exists():
            try:
                d = json.loads(self.file.read_text())
                self.events, self.session_id = d.get("events", []), d.get("session_id")
                self.prompt_version = d.get("prompt_version")
            except Exception:  # noqa: BLE001
                pass

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.returncode is None

    def save(self) -> None:
        self.file.write_text(json.dumps({"session_id": self.session_id, "prompt_version": self.prompt_version,
                                         "events": self.events[-500:]}))

    async def emit(self, ev: dict) -> None:
        ev.setdefault("ts", time.time())
        self.events.append(ev)
        for ws in list(self.clients):
            try:
                await ws.send_json({"type": "event", "event": ev})
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)

    async def broadcast_state(self) -> None:
        for ws in list(self.clients):
            try:
                await ws.send_json({"type": "state", "running": self.running, "session_id": self.session_id})
            except Exception:  # noqa: BLE001
                self.clients.discard(ws)

    async def send(self, text: str) -> None:
        if self.running:
            await self.emit({"kind": "error", "text": "Claude is still working on the previous request."})
            return
        await self.emit({"kind": "user", "text": text})
        dev = next((d for d in await list_devices() if d["serial"] == self.serial and d["state"] == "device"), None)
        if dev is None:
            await self.emit({"kind": "error", "text": "This phone is not connected (or USB debugging not allowed)."})
            return
        prompt = system_prompt(dev)
        version = prompt_version(prompt)
        if self.session_id and self.prompt_version != version:
            self.session_id = None
            await self.emit({"kind": "info", "text": "Claude's instructions changed - started a fresh Claude session "
                                                     "(it does not remember the messages above)."})
        self.prompt_version = version
        cmd = [shutil.which("claude") or "claude", "-p", text, "--output-format", "stream-json", "--verbose",
               "--append-system-prompt", prompt,
               "--allowedTools", *ALLOWED_TOOLS, "--disallowedTools", *DISALLOWED_TOOLS]
        if CFG.get("CLAUDE_MODEL"):
            cmd += ["--model", CFG["CLAUDE_MODEL"]]
        if CFG.get("CLAUDE_MAX_BUDGET_USD"):
            cmd += ["--max-budget-usd", CFG["CLAUDE_MAX_BUDGET_USD"]]
        if self.session_id:
            cmd += ["--resume", self.session_id]
        self.proc = await asyncio.create_subprocess_exec(
            *cmd, cwd=ROOT, env={**os.environ, "ANDROID_SERIAL": self.serial}, stdin=asyncio.subprocess.DEVNULL, stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE, start_new_session=True, limit=1 << 24)
        await self.broadcast_state()
        asyncio.create_task(self._read(self.proc))

    async def _read(self, proc: asyncio.subprocess.Process) -> None:
        assert proc.stdout and proc.stderr
        got_result = False
        tool_names: dict[str, str] = {}
        tool_cmds: dict[str, str] = {}
        async for raw in proc.stdout:
            try:
                d = json.loads(raw)
            except json.JSONDecodeError:
                continue
            t = d.get("type")
            if t == "system" and d.get("subtype") == "init":
                self.session_id = d.get("session_id") or self.session_id
            elif t == "assistant":
                for c in d.get("message", {}).get("content", []):
                    if c.get("type") == "text" and c.get("text", "").strip():
                        await self.emit({"kind": "assistant", "text": c["text"]})
                    elif c.get("type") == "tool_use":
                        tool_names[c.get("id", "")] = c.get("name", "")
                        tool_cmds[c.get("id", "")] = str((c.get("input") or {}).get("command", ""))
                        await self.emit({"kind": "tool", "name": c.get("name"), "summary": tool_summary(c)})
                        img = tool_image(c)
                        if img:
                            await self.emit({"kind": "image", "url": img})
            elif t == "user":
                for c in d.get("message", {}).get("content", []):
                    if isinstance(c, dict) and c.get("type") == "tool_result":
                        content = c.get("content")
                        if isinstance(content, list):
                            content = " ".join(x.get("text", "[image]") if x.get("type") == "text" else "[image]"
                                               for x in content if isinstance(x, dict))
                        content = str(content or "")
                        shots = []
                        if "show_shot.py" in tool_cmds.get(c.get("tool_use_id", ""), ""):
                            shots = re.findall(r"^@@SHOW (work/shots/\S+\.jpg) ?(.*)$", content, re.M)
                            content = re.sub(r"^@@SHOW .*\n?", "", content, flags=re.M)
                        await self.emit({"kind": "tool_result", "error": bool(c.get("is_error")),
                                         "text": content[:1500]})
                        for path, caption in shots:
                            if (ROOT / path).is_file():
                                await self.emit({"kind": "image", "url": f"/api/output/{path}", "caption": caption[:200],
                                                 "progress": True})
            elif t == "result":
                got_result = True
                self.session_id = d.get("session_id") or self.session_id
                await self.emit({"kind": "done", "error": bool(d.get("is_error")),
                                 "cost": d.get("total_cost_usd"), "turns": d.get("num_turns"),
                                 "text": d.get("result") if d.get("is_error") else None})
        err = (await proc.stderr.read()).decode(errors="replace").strip()
        rc = await proc.wait()
        if not got_result:
            await self.emit({"kind": "done", "error": True,
                             "text": f"Claude exited (code {rc}). {err[-1500:]}" if rc else "stopped"})
        self.save()
        await self.broadcast_state()

    async def stop(self) -> None:
        if self.running and self.proc:
            try:
                os.killpg(self.proc.pid, signal.SIGINT)
                await asyncio.wait_for(self.proc.wait(), 5)
            except (ProcessLookupError, asyncio.TimeoutError):
                try:
                    os.killpg(self.proc.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass

    async def reset(self) -> None:
        await self.stop()
        self.events, self.session_id = [], None
        self.save()
        for ws in list(self.clients):
            await ws.send_json({"type": "history", "events": [], "running": False})


def tool_summary(c: dict) -> str:
    inp = c.get("input") or {}
    if "command" in inp:
        return str(inp["command"])[:400]
    if "file_path" in inp:
        return str(inp["file_path"])
    if "pattern" in inp:
        return str(inp["pattern"])
    return json.dumps(inp, ensure_ascii=False)[:300]


def tool_image(c: dict) -> str | None:
    if c.get("name") != "Read":
        return None
    p = Path(str((c.get("input") or {}).get("file_path", "")))
    if p.suffix.lower() not in (".png", ".jpg", ".jpeg", ".webp"):
        return None
    if not p.is_absolute():
        p = ROOT / p
    try:
        rel = p.resolve().relative_to(ROOT)
    except ValueError:
        return None
    return f"/api/output/{rel}?t={int(time.time() * 1000)}"


chats: dict[str, Chat] = {}


def chat_for(serial: str) -> Chat:
    if serial not in chats:
        chats[serial] = Chat(serial)
    return chats[serial]


async def ws_chat(request: web.Request) -> web.WebSocketResponse:
    serial = request.query.get("serial") or await serial_from(request)
    if not re.fullmatch(r"[\w.:-]+", serial):
        raise web.HTTPBadRequest(text="bad serial")
    chat = chat_for(serial)   # history stays readable while the phone is unplugged
    ws = web.WebSocketResponse(heartbeat=20)
    await ws.prepare(request)
    chat.clients.add(ws)
    await ws.send_json({"type": "history", "events": chat.events[-300:], "running": chat.running})
    try:
        async for msg in ws:
            if msg.type != WSMsgType.TEXT:
                continue
            m = json.loads(msg.data)
            if m.get("t") == "send" and str(m.get("text", "")).strip():
                await chat.send(str(m["text"]).strip())
            elif m.get("t") == "stop":
                await chat.stop()
            elif m.get("t") == "new":
                await chat.reset()
    finally:
        chat.clients.discard(ws)
    return ws


# --------------------------------------------------------------------------- app
async def index(request: web.Request) -> web.FileResponse:
    return web.FileResponse(WEB / "static" / "index.html", headers={"Cache-Control": "no-cache"})


@web.middleware
async def same_origin(request: web.Request, handler):
    # block other websites from driving the phone through this browser (CSRF / cross-site WebSocket)
    origin = request.headers.get("Origin")
    if origin and (request.method != "GET" or request.headers.get("Upgrade", "").lower() == "websocket"):
        host = request.headers.get("Host", "")
        if origin.split("://", 1)[-1] != host:
            raise web.HTTPForbidden(text="cross-origin request refused")
    return await handler(request)


def make_app() -> web.Application:
    app = web.Application(client_max_size=8 * 1024**3, middlewares=[same_origin])
    app.router.add_get("/", index)
    app.router.add_static("/static", WEB / "static")
    app.router.add_post("/api/run/{name}", run_script)
    app.router.add_get("/api/scripts", lambda r: web.json_response(sorted(SCRIPTS)))
    app.router.add_get("/api/devices", api_devices)
    app.router.add_post("/api/clientlog", client_log)
    app.router.add_get("/api/screenshot", screenshot)
    app.router.add_post("/api/type", phone_type)
    app.router.add_get("/api/files", list_files)
    app.router.add_post("/api/upload", upload)
    app.router.add_get("/api/files/{name}", file_get)
    app.router.add_post("/api/files/{name}/push", file_push)
    app.router.add_delete("/api/files/{name}", file_delete)
    app.router.add_get("/api/output/{path:.+}", output_get)
    app.router.add_get("/ws/screen", ws_screen)
    app.router.add_get("/ws/chat", ws_chat)

    async def cleanup(_app):
        for c in chats.values():
            await c.stop()
    app.on_shutdown.append(cleanup)
    return app


if __name__ == "__main__":
    host = CFG.get("WEB_HOST", "127.0.0.1")
    port = int(CFG.get("WEB_PORT", "8765"))
    print(f"Phone control panel: http://{'localhost' if host in ('127.0.0.1', '0.0.0.0') else host}:{port}/")
    web.run_app(make_app(), host=host, port=port, print=None)
