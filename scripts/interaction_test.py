"""Non-destructive input test: swipe, coordinate tap, text input (both paths).

Works inside Settings > search box only; types a search query, never changes a setting.
Requires input injection (Xiaomi: 'USB debugging (Security settings)').
"""

from __future__ import annotations

import sys
import time
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_auto import Phone  # noqa: E402
from android_auto.log import PROJECT_DIR, setup_logging  # noqa: E402

SETTINGS = "com.android.settings"
log = setup_logging(logfile="interaction_test.log")


def field(phone: Phone):
    """The search input: matched by focus (class name changes to ClearableEditText on MIUI)."""
    return phone.find(focused=True)


def first_text_y(phone: Phone, text: str) -> int | None:
    for e in phone.elements():
        if e.text == text:
            return e.center[1]
    return None


def main() -> int:
    out = PROJECT_DIR / "output" / f"interaction-{time.strftime('%Y%m%d-%H%M%S')}"
    results: dict[str, str] = {}
    phone = Phone()
    phone.ensure_unlocked()
    phone.adb.shell(f"am force-stop {SETTINGS}")  # start from Settings' top-level page
    phone.open_app(SETTINGS)
    phone.wait_for(text="About phone", timeout=10)
    time.sleep(0.8)

    def check(name: str, cond: bool, detail: str = "") -> None:
        results[name] = "PASS" if cond else f"FAIL {detail}"
        log.info("%-22s %s %s", name, results[name], detail if cond else "")

    # 1. swipe: scroll the list up, the 'About phone' row should move/disappear
    y0 = first_text_y(phone, "About phone")
    phone.swipe_direction("up", fraction=0.5)
    time.sleep(1.0)
    y1 = first_text_y(phone, "About phone")
    check("swipe_scroll_up", y0 is not None and (y1 is None or y1 < y0), f"y {y0} -> {y1}")
    phone.screenshot(out / "1_scrolled.png")
    phone.swipe_direction("down", fraction=0.6)
    time.sleep(1.0)
    check("swipe_scroll_down", first_text_y(phone, "About phone") is not None)

    # 2. coordinate tap on the search box -> an EditText gains focus
    box = phone.wait_for(text="Search settings", timeout=5)
    b = box.info["bounds"]
    phone.tap((b["left"] + b["right"]) // 2, (b["top"] + b["bottom"]) // 2)
    focused = phone.find(className="android.widget.EditText", focused=True).wait(timeout=5)
    log.info("search field: %s", field(phone).info.get("resourceName"))
    check("tap_coordinates", bool(focused))

    # 3a. text via accessibility setText (Unicode-safe, no IME change)
    phone.type_text("wifi", clear=True)
    time.sleep(0.8)
    t = field(phone).get_text()
    check("text_accessibility", t == "wifi", f"got {t!r}")
    phone.screenshot(out / "2_typed.png")

    # 3b. text via adb `input text` (key injection path)
    field(phone).clear_text()
    phone.adb.input_text("bluetooth")
    time.sleep(1.0)
    t = field(phone).get_text()
    check("text_adb_input", t == "bluetooth", f"got {t!r}")

    # 4. leave: back out of search, then home via real key event
    field(phone).clear_text()
    phone.back(); time.sleep(0.4); phone.back(); time.sleep(0.4)
    phone.key("home")
    home = phone.adb.home_package()
    check("key_home", phone.wait_foreground(home, 6), f"fg={phone.current_package()}")

    log.info("RESULTS: %s", results)
    ok = all(v == "PASS" for v in results.values())
    log.info("INTERACTION TEST %s", "SUCCEEDED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
