"""First-use preparation with isolated private paths and no platform calls."""
import importlib.util
import json
import os
from pathlib import Path
import stat
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("onboarding", ROOT / "runtime/onboarding.py")
MODULE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(MODULE)


class OnboardingTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name).resolve()
        self.env = mock.patch.dict(os.environ, {"XDG_CONFIG_HOME": str(self.base / "config"),
            "XDG_DATA_HOME": str(self.base / "data"), "XHS_OPS_CONFIG": "", "TZ": "Asia/Shanghai"})
        self.env.start()

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_new_user_gets_private_brand_neutral_paths_without_tasks_or_writes(self):
        result = MODULE.prepare()
        path = Path(result["config"])
        data = json.loads(path.read_text())
        self.assertEqual(stat.S_IMODE(path.stat().st_mode), 0o600)
        self.assertFalse(data["allow_write"])
        self.assertFalse(data["expected_user_id"])
        self.assertFalse(data["expected_state_id"])
        self.assertEqual(data["operations"]["activity"], "")
        self.assertEqual(data["operations"]["topic_template"], "")
        self.assertEqual(data["operations"]["timezone"], "Asia/Shanghai")
        for folder in (data["state_dir"], data["output_dir"], result["private_assets"]):
            self.assertEqual(stat.S_IMODE(Path(folder).stat().st_mode), 0o700)
            self.assertTrue(Path(folder).is_relative_to(self.base))
        self.assertEqual(list(Path(data["state_dir"]).iterdir()), [])
        self.assertTrue((Path(data["output_dir"]) / "onboarding").is_dir())
        original = path.read_bytes()
        self.assertEqual(MODULE.prepare()["status"], "existing")
        self.assertEqual(path.read_bytes(), original)

    def test_existing_config_and_sending_records_are_untouched(self):
        path = self.base / "existing.json"
        original = b'{"account":"existing", "allow_write":true, "unknown":"preserve"}'
        path.write_bytes(original)
        receipt = self.base / "operation-journal.db"
        receipt.write_bytes(b"existing sending facts")
        with mock.patch.dict(os.environ, {"XHS_OPS_CONFIG": str(path)}):
            self.assertEqual(MODULE.prepare()["status"], "existing")
        self.assertEqual(path.read_bytes(), original)
        self.assertEqual(receipt.read_bytes(), b"existing sending facts")
        self.assertFalse((self.base / "data").exists())

    def test_explicit_config_wins_over_environment(self):
        with mock.patch.dict(os.environ, {"XHS_OPS_CONFIG": str(self.base / "unused.json")}):
            result = MODULE.prepare(self.base / "explicit.json")
        self.assertEqual(Path(result["config"]), self.base / "explicit.json")
        self.assertFalse((self.base / "unused.json").exists())

    def test_dangling_existing_link_is_preserved(self):
        path = self.base / "linked.json"
        path.symlink_to(self.base / "missing.json")
        self.assertEqual(MODULE.prepare(path)["status"], "existing")
        self.assertTrue(path.is_symlink())
        self.assertFalse((self.base / "missing.json").exists())

    def test_private_paths_inside_package_are_rejected(self):
        with self.assertRaisesRegex(ValueError, "outside the plugin"):
            MODULE.prepare(ROOT / "never-created.local.json")
        self.assertFalse((ROOT / "never-created.local.json").exists())

    def test_concurrent_existing_config_is_not_overwritten(self):
        path = self.base / "raced.json"
        real_open = os.open
        def race(target, flags, mode):
            Path(target).write_bytes(b"other process config")
            return real_open(target, flags, mode)
        with mock.patch.object(MODULE.os, "open", side_effect=race):
            self.assertEqual(MODULE.prepare(path)["status"], "existing")
        self.assertEqual(path.read_bytes(), b"other process config")
