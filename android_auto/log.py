"""Logging setup: human-readable console output plus a rotating log file."""

from __future__ import annotations

import logging
import logging.handlers
from pathlib import Path

PROJECT_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = PROJECT_DIR / "logs"

_configured = False


def setup_logging(level: int = logging.INFO, logfile: str = "automation.log") -> logging.Logger:
    global _configured
    root = logging.getLogger()
    if _configured:
        return logging.getLogger("android_auto")
    LOG_DIR.mkdir(exist_ok=True)

    console = logging.StreamHandler()
    console.setLevel(level)
    console.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(message)s", "%H:%M:%S"))

    fileh = logging.handlers.RotatingFileHandler(
        LOG_DIR / logfile, maxBytes=2_000_000, backupCount=3, encoding="utf-8"
    )
    fileh.setLevel(logging.DEBUG)
    fileh.setFormatter(logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s"))

    root.setLevel(logging.DEBUG)
    root.addHandler(console)
    root.addHandler(fileh)
    # third-party libraries are chatty at DEBUG
    for noisy in ("urllib3", "PIL", "adbutils", "uiautomator2"):
        logging.getLogger(noisy).setLevel(logging.WARNING)
    _configured = True
    return logging.getLogger("android_auto")
