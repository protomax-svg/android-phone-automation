"""Progress picture for the user (web chat agent): captures the phone screen and shows it in the chat.

The agent does NOT look at it (Read of work/shots/ is denied): it only documents the steps for the user,
so it costs no tokens. To read the screen the agent uses `android_auto ui`.

  .venv/bin/python web/show_shot.py "<short caption of the step>"
"""

import io
import os
import subprocess
import sys
import time
from pathlib import Path

from PIL import Image

ROOT = Path(__file__).resolve().parent.parent
OUT = ROOT / "work" / "shots"

caption = " ".join(sys.argv[1:]).strip()
if not caption:
    sys.exit('usage: show_shot.py "<short caption of the step>"')

serial = os.environ.get("ANDROID_SERIAL", "")
cmd = ["adb"] + (["-s", serial] if serial else []) + ["exec-out", "screencap", "-p"]
png = subprocess.run(cmd, capture_output=True, timeout=20).stdout
try:
    img = Image.open(io.BytesIO(png)).convert("RGB")
except Exception:  # noqa: BLE001
    sys.exit("could not capture the screen (phone locked/disconnected?)")
img.thumbnail((720, 720 * 3))
OUT.mkdir(parents=True, exist_ok=True)
path = OUT / f"{serial or 'phone'}-{time.strftime('%Y%m%d-%H%M%S')}-{int(time.time() * 1000) % 1000:03d}.jpg"
img.save(path, "JPEG", quality=70)
# The panel turns this line into a picture in the chat. Nothing for the agent to inspect.
print(f"@@SHOW {path.relative_to(ROOT)} {caption}")
print("shown to the user in the chat")
