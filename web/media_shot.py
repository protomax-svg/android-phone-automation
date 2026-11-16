"""Screenshot for steps that involve IMAGES only (web chat agent).

The web panel's Claude agent is blocked from the general `android_auto screenshot` /
`screencap` commands; it must read the screen with `android_auto ui`. This is the one
allowed way to capture the screen, for picking/checking photos and videos.

  .venv/bin/python web/media_shot.py "<what image you need to see>"   -> prints the PNG path to Read
"""

import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from android_auto import Phone  # noqa: E402

if len(sys.argv) < 2 or not sys.argv[1].strip():
    sys.exit("usage: media_shot.py \"<which image/video you need to look at>\"")
path = ROOT / "work" / f"media-{time.strftime('%H%M%S')}.png"
Phone().screenshot(path)
print(path.relative_to(ROOT))
