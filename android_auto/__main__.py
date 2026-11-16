"""Command line interface: `python -m android_auto <command>`.

Examples:
  python -m android_auto info
  python -m android_auto screenshot output/now.png
  python -m android_auto ui                 # list visible UI elements
  python -m android_auto ui --json
  python -m android_auto tap 540 1200
  python -m android_auto swipe up
  python -m android_auto text "hello"
  python -m android_auto open com.android.settings
  python -m android_auto key home
  python -m android_auto wait --text "Wi-Fi" --timeout 5
"""

from __future__ import annotations

import argparse
import json
import sys
import time

from .adb import AdbError
from .device import Phone
from .log import PROJECT_DIR, setup_logging


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(prog="android_auto")
    p.add_argument("-s", "--serial")
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("info")
    sp = sub.add_parser("screenshot"); sp.add_argument("path", nargs="?")
    sp = sub.add_parser("ui"); sp.add_argument("--json", action="store_true"); sp.add_argument("--all", action="store_true")
    sp = sub.add_parser("xml")
    sp = sub.add_parser("tap"); sp.add_argument("x", type=int); sp.add_argument("y", type=int)
    sp = sub.add_parser("swipe"); sp.add_argument("coords", nargs="+", help="up|down|left|right or x1 y1 x2 y2")
    sp = sub.add_parser("text"); sp.add_argument("text"); sp.add_argument("--clear", action="store_true")
    sp = sub.add_parser("open"); sp.add_argument("package")
    sp = sub.add_parser("key"); sp.add_argument("name", help="home|back|enter|recent|... or KEYCODE_*")
    sp = sub.add_parser("wait"); sp.add_argument("--text"); sp.add_argument("--id"); sp.add_argument("--desc"); sp.add_argument("--timeout", type=float, default=10)
    sp = sub.add_parser("click"); sp.add_argument("--text"); sp.add_argument("--id"); sp.add_argument("--desc"); sp.add_argument("--timeout", type=float, default=10)
    sub.add_parser("packages")
    sub.add_parser("stop-server")
    a = p.parse_args(argv)
    setup_logging()

    try:
        ph = Phone(a.serial)
        if a.cmd == "info":
            print(json.dumps(ph.info(), indent=2))
        elif a.cmd == "screenshot":
            path = a.path or str(PROJECT_DIR / "output" / f"screenshot-{time.strftime('%Y%m%d-%H%M%S')}.png")
            print(ph.screenshot(path))
        elif a.cmd == "ui":
            els = ph.elements(only_meaningful=not a.all)
            if a.json:
                print(json.dumps([e.to_dict() for e in els], indent=1, ensure_ascii=False))
            else:
                print(f"package: {ph.current_package()}   (flags: C=clickable S=scrollable F=focused)")
                for e in els:
                    print(e)
        elif a.cmd == "xml":
            print(ph.dump_xml())
        elif a.cmd == "tap":
            ph.tap(a.x, a.y)
        elif a.cmd == "swipe":
            if len(a.coords) == 1:
                ph.swipe_direction(a.coords[0])
            else:
                ph.swipe(*map(int, a.coords[:4]))
        elif a.cmd == "text":
            ph.type_text(a.text, clear=a.clear)
        elif a.cmd == "open":
            ph.open_app(a.package)
        elif a.cmd == "key":
            ph.key(a.name)
        elif a.cmd in ("wait", "click"):
            sel = {k: v for k, v in (("text", a.text), ("resourceId", a.id), ("description", a.desc)) if v}
            if not sel:
                p.error("give --text, --id or --desc")
            if a.cmd == "wait":
                ph.wait_for(timeout=a.timeout, **sel)
                print("found")
            else:
                ph.tap_element(timeout=a.timeout, **sel)
        elif a.cmd == "packages":
            print("\n".join(ph.installed_packages()))
        elif a.cmd == "stop-server":
            ph.stop_server()
        return 0
    except (AdbError, TimeoutError) as e:
        print(f"ERROR: {e}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
