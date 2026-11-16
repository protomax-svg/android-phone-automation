"""High-level phone control: uiautomator2 for UI, adb for low-level ops.

Every public method goes through `_call`, which on a connection-type failure
reconnects (re-resolving the USB device and restarting the uiautomator2
on-device server) and retries once.
"""

from __future__ import annotations

import functools
import logging
import time
from pathlib import Path
from typing import Any, Callable, TypeVar

import adbutils
import requests
import uiautomator2 as u2

from .adb import Adb, AdbError, DeviceNotFound, DeviceUnauthorized
from .ui import UIElement, parse_hierarchy

log = logging.getLogger("android_auto.device")
T = TypeVar("T")

# Failures that indicate a lost connection rather than a logic error
_RECONNECTABLE = (
    adbutils.AdbError,
    ConnectionError,
    requests.exceptions.ConnectionError,
    u2.exceptions.DeviceError if hasattr(u2.exceptions, "DeviceError") else ConnectionError,
    u2.exceptions.HTTPError if hasattr(u2.exceptions, "HTTPError") else ConnectionError,
)

_KEYS = {"home": "KEYCODE_HOME", "back": "KEYCODE_BACK", "enter": "KEYCODE_ENTER",
         "recent": "KEYCODE_APP_SWITCH", "power": "KEYCODE_POWER", "wakeup": "KEYCODE_WAKEUP",
         "volume_up": "KEYCODE_VOLUME_UP", "volume_down": "KEYCODE_VOLUME_DOWN", "delete": "KEYCODE_DEL"}


def _reconnecting(fn: Callable[..., T]) -> Callable[..., T]:
    @functools.wraps(fn)
    def wrapper(self: "Phone", *a: Any, **kw: Any) -> T:
        try:
            return fn(self, *a, **kw)
        except (DeviceUnauthorized, DeviceNotFound):
            raise
        except _RECONNECTABLE as e:
            log.warning("%s failed (%s: %s) - reconnecting and retrying once", fn.__name__, type(e).__name__, e)
            self.reconnect()
            return fn(self, *a, **kw)
    return wrapper


class Phone:
    def __init__(self, serial: str | None = None, connect: bool = True, retries: int = 3):
        self.adb = Adb(serial)
        self._d: u2.Device | None = None
        self.retries = retries
        if connect:
            self.connect()

    # ---- connection ----------------------------------------------------
    @property
    def serial(self) -> str | None:
        return self.adb.serial

    @property
    def d(self) -> u2.Device:
        if self._d is None:
            self.connect()
        assert self._d is not None
        return self._d

    def connect(self) -> "Phone":
        last: Exception | None = None
        for attempt in range(1, self.retries + 1):
            try:
                serial = self.adb.resolve()
                log.info("connecting to %s via uiautomator2 (attempt %d)", serial, attempt)
                self._d = u2.connect(serial)
                # forces the on-device server to start and answer
                _ = self._d.info
                log.info("connected: %s", serial)
                return self
            except (DeviceUnauthorized, DeviceNotFound):
                raise
            except Exception as e:  # noqa: BLE001 - want to retry on anything
                last = e
                log.warning("connect attempt %d failed: %s: %s", attempt, type(e).__name__, e)
                time.sleep(1.5 * attempt)
        raise AdbError(f"could not connect after {self.retries} attempts: {last}")

    def reconnect(self) -> "Phone":
        self._d = None
        self.adb.serial = self.adb.serial  # keep chosen serial
        return self.connect()

    def stop_server(self) -> None:
        """Stop the uiautomator2 server process on the phone (frees accessibility)."""
        if self._d is not None:
            self._d.stop_uiautomator()

    # ---- information ---------------------------------------------------
    def info(self) -> dict[str, Any]:
        a = self.adb
        w, h = a.screen_size()
        battery = a.shell("dumpsys battery | grep -E ' level:| status:| AC powered:| USB powered:'", check=False)
        bat = {k.strip(): v.strip() for k, v in (l.split(":", 1) for l in battery.splitlines() if ":" in l)}
        return {
            "serial": self.serial,
            "manufacturer": a.getprop("ro.product.manufacturer"),
            "brand": a.getprop("ro.product.brand"),
            "model": a.getprop("ro.product.model"),
            "marketname": a.getprop("ro.product.marketname") or a.getprop("ro.product.vendor.marketname"),
            "device": a.getprop("ro.product.device"),
            "android_version": a.getprop("ro.build.version.release"),
            "sdk": a.getprop("ro.build.version.sdk"),
            "security_patch": a.getprop("ro.build.version.security_patch"),
            "rom": a.getprop("ro.mi.os.version.name") or a.getprop("ro.miui.ui.version.name") or a.getprop("ro.build.display.id"),
            "resolution": f"{w}x{h}",
            "density_dpi": a.density(),
            "battery_level": bat.get("level"),
            "usb_powered": bat.get("USB powered"),
            **a.screen_state(),
            "foreground_package": a.foreground_package(),
        }

    # ---- screen --------------------------------------------------------
    @_reconnecting
    def screenshot(self, path: str | Path) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        try:
            self.d.screenshot(str(path))
        except Exception as e:  # noqa: BLE001
            if isinstance(e, _RECONNECTABLE):
                raise
            log.warning("u2 screenshot failed (%s); falling back to adb screencap", e)
            self.adb.screencap(path)
        log.info("screenshot saved: %s", path)
        return path

    def wake(self) -> None:
        """Turn the display on (does NOT unlock a secure lock screen)."""
        self.adb.keyevent("KEYCODE_WAKEUP")

    def ensure_unlocked(self) -> None:
        st = self.adb.screen_state()
        if st["screen_on"] is False:
            self.wake()
            time.sleep(0.8)
            st = self.adb.screen_state()
        if st["locked"]:
            raise AdbError("The phone is on the lock screen. Unlock it by hand (automation will not bypass it).")

    # ---- input ---------------------------------------------------------
    @_reconnecting
    def tap(self, x: int, y: int) -> None:
        log.info("tap (%d, %d)", x, y)
        self.d.click(x, y)

    @_reconnecting
    def swipe(self, x1: int, y1: int, x2: int, y2: int, duration: float = 0.3) -> None:
        log.info("swipe (%d,%d)->(%d,%d) %.2fs", x1, y1, x2, y2, duration)
        self.d.swipe(x1, y1, x2, y2, duration)

    def swipe_direction(self, direction: str, fraction: float = 0.5) -> None:
        """Scroll gesture: direction in up|down|left|right (finger movement)."""
        w, h = self.d.window_size()
        cx, cy = w // 2, h // 2
        dx, dy = int(w * fraction / 2), int(h * fraction / 2)
        moves = {"up": (cx, cy + dy, cx, cy - dy), "down": (cx, cy - dy, cx, cy + dy),
                 "left": (cx + dx, cy, cx - dx, cy), "right": (cx - dx, cy, cx + dx, cy)}
        self.swipe(*moves[direction])

    @_reconnecting
    def type_text(self, text: str, clear: bool = False) -> None:
        """Type into the focused field.

        Uses the accessibility setText action (Unicode-safe, does NOT change the
        phone's keyboard/IME). Falls back to `adb input text` for ASCII.
        """
        log.info("type text %r", text)
        field = self.d(focused=True)
        if field.exists(timeout=2):
            current = "" if clear else (field.get_text() or "")
            # Some fields report their hint as text when empty
            hint = field.info.get("hintText") if hasattr(field, "info") else None
            if current and hint and current == hint:
                current = ""
            if field.set_text(current + text):
                return
        log.info("no focused editable via accessibility; using adb input text")
        self.adb.input_text(text)

    def key(self, name: str) -> None:
        log.info("key %s", name)
        self.adb.keyevent(_KEYS.get(name, name))

    def home(self) -> None:
        try:
            self.key("home")
        except AdbError as e:
            # input injection blocked (e.g. Xiaomi security toggle off): the HOME
            # intent does the same thing without injecting a key event
            log.warning("HOME key blocked (%s); using HOME intent", str(e).splitlines()[0])
            self.adb.shell("am start -a android.intent.action.MAIN -c android.intent.category.HOME")

    def back(self) -> None:
        self.key("back")

    # ---- apps ----------------------------------------------------------
    @_reconnecting
    def open_app(self, package: str, wait: bool = True, timeout: float = 10) -> None:
        log.info("open app %s", package)
        self.d.app_start(package, wait=wait)
        if wait and not self.wait_foreground(package, timeout):
            raise AdbError(f"{package} did not come to the foreground within {timeout}s")

    def stop_app(self, package: str) -> None:
        self.d.app_stop(package)

    def current_package(self) -> str:
        try:
            return self.d.app_current().get("package", "")
        except Exception:  # noqa: BLE001
            return self.adb.foreground_package()

    def wait_foreground(self, package: str, timeout: float = 10) -> bool:
        end = time.time() + timeout
        while time.time() < end:
            if self.current_package() == package:
                return True
            time.sleep(0.4)
        return False

    def installed_packages(self, third_party_only: bool = False) -> list[str]:
        out = self.adb.shell("pm list packages" + (" -3" if third_party_only else ""))
        return sorted(l.removeprefix("package:") for l in out.splitlines())

    # ---- UI inspection -------------------------------------------------
    @_reconnecting
    def dump_xml(self) -> str:
        return self.d.dump_hierarchy(compressed=False)

    def elements(self, only_meaningful: bool = True) -> list[UIElement]:
        return parse_hierarchy(self.dump_xml(), only_meaningful)

    def find(self, **selector: Any):
        """uiautomator2 selector, e.g. find(text="Wi-Fi"), find(resourceId=...), find(textContains=...)."""
        return self.d(**selector)

    @_reconnecting
    def wait_for(self, timeout: float = 10, **selector: Any):
        """Wait until an element matching the selector exists. Returns it or raises."""
        log.info("wait for %s (%.0fs)", selector, timeout)
        el = self.d(**selector)
        if not el.wait(timeout=timeout):
            raise TimeoutError(f"element {selector} did not appear within {timeout}s")
        return el

    def wait_gone(self, timeout: float = 10, **selector: Any) -> bool:
        return self.d(**selector).wait_gone(timeout=timeout)

    @_reconnecting
    def tap_element(self, timeout: float = 10, **selector: Any) -> None:
        el = self.wait_for(timeout=timeout, **selector)
        log.info("tap element %s", selector)
        el.click()
