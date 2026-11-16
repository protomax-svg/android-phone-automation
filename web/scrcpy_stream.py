"""Minimal scrcpy (v5.x) client: H.264 video packets out, control messages in.

Pushes the scrcpy-server shipped with the installed scrcpy package to the phone,
starts it over `adb shell`, forwards its abstract socket to a local TCP port and
speaks the raw protocol. Runs alongside any other scrcpy session (own scid).
"""

from __future__ import annotations

import asyncio
import logging
import os
import random
import re
import shutil
import socket
import struct
import subprocess
from dataclasses import dataclass

log = logging.getLogger("web.scrcpy")

def _find_server_jar() -> str:
    """scrcpy-server of the installed scrcpy: distro package (/usr/share/scrcpy) or the
    official release tarball (server sits next to the real scrcpy binary, e.g. /opt/scrcpy)."""
    cands = [os.environ.get("SCRCPY_SERVER_PATH", "")]
    exe = shutil.which("scrcpy")
    if exe:
        cands.append(os.path.join(os.path.dirname(os.path.realpath(exe)), "scrcpy-server"))
    cands += ["/usr/share/scrcpy/scrcpy-server", "/usr/local/share/scrcpy/scrcpy-server"]
    for c in cands:
        if c and os.path.isfile(c):
            return c
    return "/usr/share/scrcpy/scrcpy-server"


SERVER_JAR_LOCAL = _find_server_jar()
SERVER_JAR_REMOTE = "/data/local/tmp/scrcpy-server-web.jar"

FLAG_SESSION = 1 << 63
FLAG_CONFIG = 1 << 62
FLAG_KEY = 1 << 61
PTS_MASK = (1 << 61) - 1


def scrcpy_version() -> str:
    out = subprocess.run(["scrcpy", "--version"], capture_output=True, text=True).stdout
    m = re.search(r"scrcpy (\d+\.\d+(?:\.\d+)?)", out)
    if not m:
        raise RuntimeError("cannot determine scrcpy version (is scrcpy installed?)")
    return m.group(1)


def _free_port() -> int:
    with socket.socket() as s:
        s.bind(("127.0.0.1", 0))
        return s.getsockname()[1]


@dataclass
class Packet:
    config: bool
    key: bool
    pts: int
    data: bytes


class ScrcpySession:
    def __init__(self, serial: str, max_size: int = 1280, bit_rate: int = 4_000_000, max_fps: int = 30):
        self.serial = serial
        self.max_size = max_size
        self.bit_rate = bit_rate
        self.max_fps = max_fps
        self.scid = f"{random.randrange(1, 0x7FFFFFFF):08x}"
        self.port = _free_port()
        self.proc: asyncio.subprocess.Process | None = None
        self.video_r: asyncio.StreamReader | None = None
        self.video_w: asyncio.StreamWriter | None = None
        self.ctrl_r: asyncio.StreamReader | None = None
        self.ctrl_w: asyncio.StreamWriter | None = None
        self.device_name = ""
        self.width = 0
        self.height = 0

    def _adb(self, *args: str) -> list[str]:
        return [shutil.which("adb") or "adb", "-s", self.serial, *args]

    async def _run(self, *args: str) -> None:
        p = await asyncio.create_subprocess_exec(*self._adb(*args), stdout=asyncio.subprocess.PIPE,
                                                 stderr=asyncio.subprocess.STDOUT)
        out, _ = await p.communicate()
        if p.returncode != 0:
            raise RuntimeError(f"adb {' '.join(args)} failed: {out.decode(errors='replace').strip()}")

    async def start(self) -> None:
        version = scrcpy_version()
        await self._run("push", SERVER_JAR_LOCAL, SERVER_JAR_REMOTE)
        await self._run("forward", f"tcp:{self.port}", f"localabstract:scrcpy_{self.scid}")
        cmd = (f"CLASSPATH={SERVER_JAR_REMOTE} app_process / com.genymobile.scrcpy.Server {version} "
               f"scid={self.scid} log_level=info tunnel_forward=true audio=false control=true "
               f"video_codec=h264 max_size={self.max_size} video_bit_rate={self.bit_rate} "
               f"max_fps={self.max_fps} stay_awake=true clipboard_autosync=false cleanup=true")
        self.proc = await asyncio.create_subprocess_exec(*self._adb("shell", cmd), stdout=asyncio.subprocess.PIPE,
                                                         stderr=asyncio.subprocess.STDOUT)
        asyncio.create_task(self._pump_server_log())

        # The server needs a moment to listen; adb forward accepts and immediately
        # closes the connection until then, so retry until the dummy byte arrives.
        last: Exception | None = None
        for _ in range(50):
            if self.proc.returncode is not None:
                break
            try:
                r, w = await asyncio.open_connection("127.0.0.1", self.port)
                dummy = await asyncio.wait_for(r.readexactly(1), timeout=2)
                if dummy == b"\x00":
                    self.video_r, self.video_w = r, w
                    break
                w.close()
            except (OSError, asyncio.IncompleteReadError, asyncio.TimeoutError) as e:
                last = e
            await asyncio.sleep(0.2)
        if self.video_r is None:
            await self.stop()
            raise RuntimeError(f"could not connect to scrcpy server: {last}")
        self.ctrl_r, self.ctrl_w = await asyncio.open_connection("127.0.0.1", self.port)
        name = await self.video_r.readexactly(64)
        self.device_name = name.split(b"\0", 1)[0].decode(errors="replace")
        codec = struct.unpack(">I", await self.video_r.readexactly(4))[0]
        if codec != 0x68323634:
            raise RuntimeError(f"unexpected video codec id 0x{codec:08x}")
        asyncio.create_task(self._drain_device_messages())
        log.info("scrcpy connected: %s (scid %s)", self.device_name, self.scid)

    async def _pump_server_log(self) -> None:
        assert self.proc and self.proc.stdout
        async for line in self.proc.stdout:
            log.info("server: %s", line.decode(errors="replace").rstrip())

    async def _drain_device_messages(self) -> None:
        # clipboard etc. - not used, but must be read so the socket never blocks
        try:
            while self.ctrl_r and await self.ctrl_r.read(4096):
                pass
        except Exception:  # noqa: BLE001
            pass

    async def packets(self):
        """Yield ('size', w, h) session events and Packet objects."""
        assert self.video_r
        while True:
            hdr = await self.video_r.readexactly(12)
            pts_flags, length = struct.unpack(">QI", hdr)
            if pts_flags & FLAG_SESSION:
                self.width = pts_flags & 0xFFFFFFFF
                self.height = length
                yield ("size", self.width, self.height)
                continue
            data = await self.video_r.readexactly(length)
            yield Packet(bool(pts_flags & FLAG_CONFIG), bool(pts_flags & FLAG_KEY), pts_flags & PTS_MASK, data)

    # ---- control -------------------------------------------------------
    def _send(self, data: bytes) -> None:
        if self.ctrl_w and not self.ctrl_w.is_closing():
            self.ctrl_w.write(data)

    def touch(self, action: int, x: int, y: int, pointer_id: int = -2, pressure: float = 1.0) -> None:
        # action: 0 down, 1 up, 2 move. pointer -1 = mouse, -2 = generic finger
        if action == 1:
            pressure = 0.0
        p = 0xFFFF if pressure >= 1 else max(0, int(pressure * 65536))
        buttons = 1 if action != 1 else 0  # primary button held while down/moving
        self._send(struct.pack(">BBqiiHHHii", 2, action, pointer_id, int(x), int(y),
                               self.width, self.height, p, 1, buttons))

    def scroll(self, x: int, y: int, hscroll: float, vscroll: float) -> None:
        enc = lambda v: max(-0x7FFF, min(0x7FFF, round(v * 2048)))  # noqa: E731
        self._send(struct.pack(">BiiHHhhi", 3, int(x), int(y), self.width, self.height,
                               enc(hscroll), enc(vscroll), 0))

    def key(self, keycode: int, action: int | None = None, meta: int = 0) -> None:
        actions = (0, 1) if action is None else (action,)
        for a in actions:
            self._send(struct.pack(">BBiii", 0, a, keycode, 0, meta))

    def text(self, text: str) -> None:
        b = text.encode("utf-8")[:300]
        self._send(struct.pack(">BI", 1, len(b)) + b)

    def back_or_screen_on(self) -> None:
        self._send(struct.pack(">BB", 4, 0) + struct.pack(">BB", 4, 1))

    def reset_video(self) -> None:
        """Ask the server to restart the encoder (new config + key frame)."""
        self._send(bytes([17]))

    def display_power(self, on: bool) -> None:
        self._send(struct.pack(">BB", 10, 1 if on else 0))

    async def stop(self) -> None:
        for w in (self.video_w, self.ctrl_w):
            if w:
                w.close()
        if self.proc and self.proc.returncode is None:
            self.proc.terminate()
            try:
                await asyncio.wait_for(self.proc.wait(), 3)
            except asyncio.TimeoutError:
                self.proc.kill()
        for args in (("forward", "--remove", f"tcp:{self.port}"),
                     ("shell", f"pkill -f scid={self.scid} || true")):  # belt and braces: no orphaned server
            try:
                await self._run(*args)
            except RuntimeError:
                pass
