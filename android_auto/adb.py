"""Low-level ADB access via the official `adb` binary (android-tools).

Everything here is plain subprocess calls so it works even when uiautomator2
is unavailable, and so failures produce clear, actionable errors.
"""

from __future__ import annotations

import logging
import os
import re
import shlex
import shutil
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

log = logging.getLogger("android_auto.adb")


class AdbError(RuntimeError):
    pass


class DeviceNotFound(AdbError):
    pass


class DeviceUnauthorized(AdbError):
    pass


UNAUTHORIZED_HELP = (
    "The phone has not authorized this computer for USB debugging.\n"
    "  -> Unlock the phone and look for the dialog 'Allow USB debugging?'.\n"
    "  -> Check the RSA fingerprint, tick 'Always allow from this computer', tap Allow.\n"
    "  -> If no dialog appears: Settings > Additional settings > Developer options >\n"
    "     'Revoke USB debugging authorizations', then unplug/replug the cable."
)

NOT_FOUND_HELP = (
    "No Android device in ADB mode was found.\n"
    "  -> Enable Developer options: Settings > About phone > tap 'OS version' 7 times.\n"
    "  -> Settings > Additional settings > Developer options > enable 'USB debugging'.\n"
    "  -> Use a data-capable USB cable; set USB mode to 'File transfer' if asked."
)


@dataclass
class AdbDevice:
    serial: str
    state: str  # device | unauthorized | offline | recovery | ...
    attrs: dict[str, str] = field(default_factory=dict)

    @property
    def is_usb(self) -> bool:
        return "usb" in self.attrs and ":" not in self.serial


def adb_binary() -> str:
    path = shutil.which("adb")
    if not path:
        raise AdbError("`adb` not found. Install it with: sudo pacman -S android-tools")
    return path


class Adb:
    """Wrapper bound to one device serial (or auto-selected single USB device)."""

    def __init__(self, serial: str | None = None):
        self.bin = adb_binary()
        self.serial = serial or os.environ.get("ANDROID_SERIAL") or None

    # ---- raw execution -------------------------------------------------
    def _base(self) -> list[str]:
        return [self.bin] + (["-s", self.serial] if self.serial else [])

    def run(self, *args: str, timeout: float = 30, check: bool = True, binary: bool = False):
        cmd = self._base() + list(args)
        log.debug("exec: %s", shlex.join(cmd))
        try:
            proc = subprocess.run(cmd, capture_output=True, timeout=timeout, text=not binary)
        except subprocess.TimeoutExpired as e:
            raise AdbError(f"adb timed out after {timeout}s: {shlex.join(args)}") from e
        if check and proc.returncode != 0:
            err = proc.stderr if not binary else proc.stderr.decode(errors="replace")
            raise AdbError(f"adb {shlex.join(args)} failed ({proc.returncode}): {err.strip()}")
        return proc.stdout

    def shell(self, command: str, timeout: float = 30, check: bool = True) -> str:
        return self.run("shell", command, timeout=timeout, check=check).strip()

    # ---- discovery -----------------------------------------------------
    @classmethod
    def devices(cls) -> list[AdbDevice]:
        out = subprocess.run([adb_binary(), "devices", "-l"], capture_output=True, text=True, timeout=15).stdout
        result = []
        for line in out.splitlines()[1:]:
            parts = line.split()
            if len(parts) < 2:
                continue
            attrs = dict(p.split(":", 1) for p in parts[2:] if ":" in p)
            result.append(AdbDevice(parts[0], parts[1], attrs))
        return result

    def resolve(self) -> str:
        """Pick the target device and make sure it is authorized. Returns serial."""
        devs = self.devices()
        if self.serial:
            match = [d for d in devs if d.serial == self.serial]
            if not match:
                raise DeviceNotFound(f"Device {self.serial} not connected.\n{NOT_FOUND_HELP}")
            dev = match[0]
        else:
            if not devs:
                raise DeviceNotFound(NOT_FOUND_HELP)
            usb = [d for d in devs if d.is_usb] or devs
            if len(usb) > 1:
                raise AdbError(
                    "Several devices connected: "
                    + ", ".join(d.serial for d in usb)
                    + ". Set ANDROID_SERIAL=<serial> to choose one."
                )
            dev = usb[0]
        if dev.state == "unauthorized":
            raise DeviceUnauthorized(UNAUTHORIZED_HELP)
        if dev.state != "device":
            raise AdbError(f"Device {dev.serial} is in state '{dev.state}' (expected 'device'). Replug the cable.")
        self.serial = dev.serial
        return dev.serial

    # ---- convenience ---------------------------------------------------
    def getprop(self, name: str) -> str:
        return self.shell(f"getprop {name}")

    def screen_size(self) -> tuple[int, int]:
        out = self.shell("wm size")
        # "Physical size: 1080x2400" and maybe "Override size: ..."; prefer override
        sizes = re.findall(r"(\w+) size: (\d+)x(\d+)", out)
        d = {k: (int(w), int(h)) for k, w, h in sizes}
        return d.get("Override") or d.get("Physical") or (0, 0)

    def density(self) -> int:
        m = re.findall(r"density: (\d+)", self.shell("wm density"))
        return int(m[-1]) if m else 0

    def screencap(self, path: str | Path) -> Path:
        data = self.run("exec-out", "screencap", "-p", binary=True, timeout=30)
        if not data.startswith(b"\x89PNG"):
            raise AdbError("screencap did not return PNG data")
        path = Path(path)
        path.write_bytes(data)
        return path

    def tap(self, x: int, y: int) -> None:
        self._input(f"tap {int(x)} {int(y)}")

    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration_ms: int = 300) -> None:
        self._input(f"swipe {int(x1)} {int(y1)} {int(x2)} {int(y2)} {int(duration_ms)}")

    def keyevent(self, key: str | int) -> None:
        self._input(f"keyevent {key}")

    def input_text(self, text: str) -> None:
        """ASCII-only text via `input text` (spaces encoded as %s)."""
        if not text.isascii():
            raise AdbError("adb `input text` supports ASCII only; use Phone.type_text() for Unicode")
        escaped = text.replace("%", r"\%").replace(" ", "%s")
        self._input("text " + shlex.quote(escaped))

    def _input(self, args: str) -> None:
        cmd = self._base() + ["shell", f"input {args} 2>&1"]
        out = subprocess.run(cmd, capture_output=True, text=True, timeout=30).stdout
        if "SecurityException" in out or "INJECT_EVENTS" in out:
            raise AdbError(
                "Input injection was blocked by the phone. On Xiaomi/Redmi/POCO enable\n"
                "  Developer options > 'USB debugging (Security settings)', then reboot the phone.\n" + out
            )

    def start_activity_action(self, action: str) -> str:
        return self.shell(f"am start -W -a {action}")

    def home_package(self) -> str:
        out = self.shell("cmd package resolve-activity --brief -a android.intent.action.MAIN -c android.intent.category.HOME")
        last = out.strip().splitlines()[-1] if out.strip() else ""
        return last.split("/")[0] if "/" in last else ""

    def foreground_package(self) -> str:
        out = self.shell("dumpsys activity activities | grep -E 'mResumedActivity|topResumedActivity|ResumedActivity' ", check=False)
        m = re.search(r"u0 ([\w.]+)/", out)
        if m:
            return m.group(1)
        out = self.shell("dumpsys window | grep -E 'mCurrentFocus|mFocusedApp'", check=False)
        m = re.search(r"([\w.]+)/[\w.$]+", out)
        return m.group(1) if m else ""

    def screen_state(self) -> dict[str, bool | None]:
        """Best-effort: is the display on, is the keyguard (lock screen) showing."""
        power = self.shell("dumpsys power | grep -E 'mWakefulness=|Display Power: state='", check=False)
        on = None
        if "mWakefulness=" in power:
            on = "mWakefulness=Awake" in power
        kg = self.shell("dumpsys window | grep -E 'mDreamingLockscreen|isKeyguardShowing|mShowingLockscreen|KeyguardShowing'", check=False)
        locked = None
        m = re.search(r"(?:mShowingLockscreen|isKeyguardShowing|KeyguardShowing)=(true|false)", kg)
        if m:
            locked = m.group(1) == "true"
        else:
            kg2 = self.shell("dumpsys trust | grep -i 'deviceLocked'", check=False)
            m2 = re.search(r"deviceLocked=(\d|true|false)", kg2)
            if m2:
                locked = m2.group(1) in ("1", "true")
        return {"screen_on": on, "locked": locked}
