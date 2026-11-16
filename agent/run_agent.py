"""Minimal LLM-agent example using DroidRun (now published as `mobilerun`).

Runs in the separate venv: ~/android-automation/.venv-agent (Python 3.12).

SAFETY DEFAULTS
  * Nothing runs without --run. Without it the script only checks readiness.
  * Telemetry is forced off (MOBILERUN_TELEMETRY_ENABLED=false).
  * Default LLM is a LOCAL Ollama model: no API key, no data leaves the PC.
  * Cloud providers (Anthropic, OpenAI, Gemini ...) send the phone's UI text -
    and with --vision, screenshots - to that provider and cost money. They are
    refused unless you also pass --allow-cloud.
  * auto_setup is off: the Mobilerun Portal app is NOT silently installed.
    Install it once, explicitly, with:  agent/agent.sh setup

Examples
  agent/agent.sh check
  agent/agent.sh run --goal "Open Settings and tell me the Android version"            # Ollama
  ANTHROPIC_API_KEY=... agent/agent.sh run --provider Anthropic --allow-cloud \
        --model claude-sonnet-5-5 --goal "Open the Clock app and read the next alarm"
"""

from __future__ import annotations

import argparse
import asyncio
import os
import shutil
import subprocess
import sys

os.environ["MOBILERUN_TELEMETRY_ENABLED"] = "false"
os.environ["DROIDRUN_TELEMETRY_ENABLED"] = "false"

LOCAL_PROVIDERS = {"Ollama"}


def check() -> bool:
    good = True
    adb = shutil.which("adb")
    print(f"adb: {adb or 'MISSING'}")
    good &= bool(adb)
    if adb:
        out = subprocess.run([adb, "devices"], capture_output=True, text=True).stdout
        devs = [l.split() for l in out.splitlines()[1:] if l.strip()]
        print(f"devices: {devs or 'none'}")
        good &= any(d[1] == "device" for d in devs)
        if devs:
            pk = subprocess.run([adb, "shell", "pm", "list", "packages", "com.mobilerun.portal", "com.droidrun.portal"],
                                capture_output=True, text=True).stdout.strip()
            print(f"portal app on phone: {pk or 'NOT INSTALLED (run: agent/agent.sh setup)'}")
    try:
        from importlib.metadata import version
        import mobilerun  # noqa: F401
        print(f"mobilerun {version('mobilerun')} importable, telemetry disabled")
    except Exception as e:  # noqa: BLE001
        print(f"mobilerun import failed: {e}")
        good = False
    print(f"ollama: {shutil.which('ollama') or 'not installed (needed only for the local-LLM default)'}")
    return good


async def run(goal: str, provider: str, model: str | None, steps: int, vision: bool) -> int:
    from mobilerun import AgentConfig, DeviceConfig, MobileAgent, MobileConfig, TelemetryConfig, load_llm

    cfg = MobileConfig(
        agent=AgentConfig(max_steps=steps, reasoning=False),
        device=DeviceConfig(serial=os.environ.get("ANDROID_SERIAL"), auto_setup=False),
        telemetry=TelemetryConfig(enabled=False),
    )
    cfg.agent.executor.vision = vision
    cfg.agent.manager.vision = vision
    cfg.agent.fast_agent.vision = vision

    kwargs = {"model": model} if model else {}
    llm = load_llm(provider, **kwargs)
    agent = MobileAgent(goal=goal, config=cfg, llms=llm, timeout=600)
    handler = agent.run()
    async for ev in handler.stream_events():
        pass  # mobilerun's own logger prints progress
    result = await handler
    print(f"\nsuccess={result.success} steps={getattr(result, 'steps', '?')}\nreason: {result.reason}")
    return 0 if result.success else 1


def main() -> int:
    p = argparse.ArgumentParser()
    sub = p.add_subparsers(dest="cmd", required=True)
    sub.add_parser("check")
    r = sub.add_parser("run")
    r.add_argument("--goal", required=True)
    r.add_argument("--provider", default="Ollama")
    r.add_argument("--model", default=None, help="e.g. qwen3:8b for Ollama")
    r.add_argument("--steps", type=int, default=15)
    r.add_argument("--vision", action="store_true", help="send screenshots to the LLM")
    r.add_argument("--allow-cloud", action="store_true", help="confirm sending phone data to a cloud LLM")
    a = p.parse_args()

    if a.cmd == "check":
        return 0 if check() else 1
    if a.provider not in LOCAL_PROVIDERS and not a.allow_cloud:
        print(f"Refusing: provider {a.provider!r} is a cloud API (costs money, uploads phone UI"
              f"{' and screenshots' if a.vision else ''}). Re-run with --allow-cloud to confirm.")
        return 3
    if a.provider == "Ollama" and not a.model:
        p.error("--model is required for Ollama (e.g. --model qwen3:8b)")
    return asyncio.run(run(a.goal, a.provider, a.model, a.steps, a.vision))


if __name__ == "__main__":
    sys.exit(main())
