import hashlib
import importlib.util
import json
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
SPEC = importlib.util.spec_from_file_location("xhs_package", ROOT / "scripts/package.py")
PACKAGE = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(PACKAGE)


class HumanizerBundleTests(unittest.TestCase):
    def test_pinned_upstream_files_are_in_package_with_original_bytes(self):
        folder = ROOT / "skills/humanizer-zh"
        provenance = json.loads((folder / "UPSTREAM.json").read_text())
        self.assertEqual(provenance["commit"], "f4518a8eab97b8bfebc66a89d34320a89bef6930")
        self.assertEqual(provenance["license"], "MIT")
        self.assertEqual(provenance["modified_upstream_files"], [])
        selected = set(PACKAGE.files())
        for name, digest in provenance["upstream_files"].items():
            path = folder / name
            self.assertIn(path, selected)
            self.assertEqual(hashlib.sha256(path.read_bytes()).hexdigest(), digest)
        self.assertIn(folder / "UPSTREAM.json", selected)
        self.assertIn(folder / "README.md", selected)


if __name__ == "__main__":
    unittest.main()
