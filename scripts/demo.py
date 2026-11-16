"""Demonstration: connect -> device info -> screenshot -> open Settings ->
inspect UI -> return home. Read-only: it changes no setting.

Results go to output/demo-<timestamp>/ ; exit code 0 only if every step passed.
"""

from __future__ import annotations

import json
import sys
import time
import traceback
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from android_auto import AdbError, Phone  # noqa: E402
from android_auto.log import PROJECT_DIR, setup_logging  # noqa: E402

SETTINGS = "com.android.settings"
log = setup_logging(logfile="demo.log")


def main() -> int:
    out = PROJECT_DIR / "output" / f"demo-{time.strftime('%Y%m%d-%H%M%S')}"
    out.mkdir(parents=True)
    results: dict[str, str] = {}
    phone: Phone | None = None

    def step(name: str, fn):
        log.info("=== %s", name)
        try:
            value = fn()
            results[name] = "PASS"
            return value
        except Exception as e:  # noqa: BLE001
            results[name] = f"FAIL: {type(e).__name__}: {e}"
            log.error("%s failed: %s", name, e)
            log.debug(traceback.format_exc())
            raise

    try:
        phone = step("1_connect", lambda: Phone())

        def device_info():
            info = phone.info()
            (out / "device_info.json").write_text(json.dumps(info, indent=2))
            for k in ("manufacturer", "marketname", "model", "android_version", "sdk", "rom", "resolution", "battery_level", "screen_on", "locked"):
                log.info("  %-16s %s", k, info.get(k))
            return info
        step("2_device_info", device_info)

        def screenshot():
            p = phone.screenshot(out / "01_initial.png")
            assert p.stat().st_size > 10_000, "screenshot suspiciously small"
        step("3_screenshot", screenshot)

        step("4_check_unlocked", phone.ensure_unlocked)

        def open_settings():
            phone.open_app(SETTINGS)
            phone.wait_for(timeout=10, packageName=SETTINGS)
            time.sleep(1.0)  # let the list finish rendering
            phone.screenshot(out / "02_settings.png")
            assert phone.current_package() == SETTINGS, f"foreground is {phone.current_package()}"
        step("5_open_settings", open_settings)

        def inspect():
            xml = phone.dump_xml()
            (out / "settings_hierarchy.xml").write_text(xml)
            els = phone.elements()
            (out / "settings_elements.json").write_text(json.dumps([e.to_dict() for e in els], indent=1, ensure_ascii=False))
            texts = [e for e in els if e.text]
            log.info("  %d meaningful elements, %d with text; first entries:", len(els), len(texts))
            for e in els[:15]:
                log.info("    %s", e)
            assert texts, "no text elements found in Settings"
            # demonstrate wait_for on a real, visible label
            label = texts[min(2, len(texts) - 1)].text
            phone.wait_for(timeout=5, text=label)
            log.info("  wait_for(text=%r) OK", label)
        step("6_inspect_ui", inspect)

        def go_home():
            home_pkg = phone.adb.home_package()
            phone.home()
            ok = phone.wait_foreground(home_pkg, timeout=8) if home_pkg else True
            time.sleep(0.8)
            phone.screenshot(out / "03_home.png")
            assert ok, f"expected launcher {home_pkg}, got {phone.current_package()}"
            log.info("  launcher in foreground: %s", home_pkg)
        step("7_return_home", go_home)
    except Exception:  # noqa: BLE001 - already recorded
        pass

    (out / "results.json").write_text(json.dumps(results, indent=2))
    log.info("=== RESULTS (artifacts in %s)", out)
    for k, v in results.items():
        log.info("  %-18s %s", k, v)
    ok = len(results) == 7 and all(v == "PASS" for v in results.values())
    log.info("DEMO %s", "SUCCEEDED" if ok else "FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    try:
        sys.exit(main())
    except AdbError as e:
        print(f"ERROR: {e}", file=sys.stderr)
        sys.exit(2)
