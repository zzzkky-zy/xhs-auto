import importlib.util
from pathlib import Path
import plistlib
import sys
import tempfile
import unittest

spec = importlib.util.spec_from_file_location("browser_fixture", Path(__file__).resolve().parents[1]/"scripts/browser_fixture.py")
browser_fixture = importlib.util.module_from_spec(spec)
spec.loader.exec_module(browser_fixture)

class BrowserIsolationTests(unittest.TestCase):
    @unittest.skipUnless(sys.platform == "darwin", "macOS application identity")
    def test_personal_chrome_and_symlink_are_rejected(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            contents = root/"Renamed.app/Contents"
            (contents/"MacOS").mkdir(parents=True)
            (contents/"Info.plist").write_bytes(plistlib.dumps({"CFBundleIdentifier":"com.google.Chrome"}))
            binary = contents/"MacOS/Browser"
            binary.write_text("#!/bin/sh\nexit 0\n")
            binary.chmod(0o700)
            link = root/"browser"
            link.symlink_to(binary)
            for path in [binary,link]:
                with self.assertRaisesRegex(ValueError,"Google Chrome"):
                    browser_fixture.select_browser(str(path), {})

    def test_missing_browser_fails_instead_of_discovering_chrome(self):
        with self.assertRaises(FileNotFoundError):
            browser_fixture.select_browser("/no-such-fixture-browser", {})
