"""android_auto - deterministic automation for a USB-connected Android phone.

Layers:
  adb.py      - thin subprocess wrapper around the `adb` binary (low level, no deps)
  ui.py       - parsing of the accessibility (uiautomator) hierarchy into elements
  device.py   - `Phone`: high-level API (uiautomator2 + adb) with reconnection
  log.py      - logging setup (console + logs/automation.log)
"""

from .adb import Adb, AdbError, DeviceNotFound, DeviceUnauthorized
from .device import Phone
from .ui import UIElement, parse_hierarchy

__all__ = [
    "Adb",
    "AdbError",
    "DeviceNotFound",
    "DeviceUnauthorized",
    "Phone",
    "UIElement",
    "parse_hierarchy",
]
