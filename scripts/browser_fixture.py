"""Keep local fixture tests out of the user's everyday macOS Chrome app."""
from pathlib import Path
import os
import plistlib
import sys

DEFAULT_BROWSER = "/Applications/AI Test Browser.app/Contents/MacOS/AI Test Browser"

def select_browser(explicit=None, env=None):
    env = os.environ if env is None else env
    selected = explicit or env.get("XHS_TEST_CHROME") or env.get("AI_TEST_BROWSER_EXECUTABLE")
    if not selected and sys.platform == "darwin" and Path(DEFAULT_BROWSER).is_file():
        selected = DEFAULT_BROWSER
    if not selected:
        return None
    binary = Path(selected).expanduser().resolve(strict=True)
    if not binary.is_file() or not os.access(binary, os.X_OK):
        raise ValueError("Fixture browser is not executable")
    if sys.platform == "darwin":
        for app in binary.parents:
            if app.suffix.lower() != ".app":
                continue
            with (app/"Contents/Info.plist").open("rb") as f:
                bundle_id = plistlib.load(f).get("CFBundleIdentifier", "")
            if bundle_id.lower() == "com.google.chrome" or app.name.lower() == "google chrome.app":
                raise ValueError("禁止用日常 Google Chrome 跑夹具测试；请使用 AI 测试浏览器")
            break
    return str(binary)
