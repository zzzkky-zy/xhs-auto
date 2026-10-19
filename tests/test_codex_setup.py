import hashlib
import importlib.util
import io
import json
from pathlib import Path
import tarfile
import tempfile
import unittest
from unittest import mock

SPEC = importlib.util.spec_from_file_location("codex_setup", Path(__file__).resolve().parents[1] / "scripts/setup_codex.py")
SETUP = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(SETUP)


class CodexPackageTests(unittest.TestCase):
    def archive(self, base, payload, extra=None):
        archive = base / "package.tar.gz"
        manifest = "".join(hashlib.sha256(data).hexdigest() + "  " + name + "\n" for name, data in payload.items()).encode()
        items = [("bundle/" + name, data, None) for name, data in {**payload, "MANIFEST.sha256": manifest}.items()]
        if extra: items.append(extra)
        with tarfile.open(archive, "w:gz") as tar:
            for name, data, kind in items:
                info = tarfile.TarInfo(name)
                info.size = len(data)
                if kind: info.type = kind
                tar.addfile(info, io.BytesIO(data))
        return archive, hashlib.sha256(archive.read_bytes()).hexdigest()

    def check(self, extra=None, bad_digest=False):
        with tempfile.TemporaryDirectory() as tmp:
            base = Path(tmp)
            archive, digest = self.archive(base, {"VERSION": b"0.2.1\n"}, extra)
            dest = base / "unpack"
            dest.mkdir()
            return SETUP.unpack_verified(archive, dest, "0" * 64 if bad_digest else digest)[1]

    def test_full_manifest(self):
        self.assertEqual(set(self.check()), {"VERSION"})

    def test_changed_archive(self):
        with self.assertRaisesRegex(ValueError, "SHA256 mismatch"):
            self.check(bad_digest=True)

    def test_path_traversal(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.check(("bundle/../outside", b"x", None))

    def test_symlink(self):
        with self.assertRaisesRegex(ValueError, "unsafe"):
            self.check(("bundle/link", b"", tarfile.SYMTYPE))

    def test_duplicate_member(self):
        with self.assertRaisesRegex(ValueError, "duplicate"):
            self.check(("bundle/VERSION", b"other", None))

    def test_unlisted_payload(self):
        with self.assertRaisesRegex(ValueError, "cover archive"):
            self.check(("bundle/extra", b"private", None))

    def test_catalog_preserves_other_entries(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_file = root / ".agents/plugins/marketplace.json"
            catalog_file.parent.mkdir(parents=True)
            catalog_file.write_text(json.dumps({"name": "xhs-operations-local", "plugins": [{"name": "other-plugin"}], "custom": "preserve"}))
            with mock.patch.object(SETUP.subprocess, "run", return_value=mock.Mock(returncode=0, stdout="", stderr="")) as run:
                SETUP.register(root / "plugins/xhs", root, "codex")
            catalog = json.loads(catalog_file.read_text())
            self.assertEqual([p["name"] for p in catalog["plugins"]], ["other-plugin", "xiaohongshu-operations"])
            self.assertEqual(catalog["custom"], "preserve")
            self.assertEqual(run.call_count, 2)

    def test_foreign_catalog_is_not_replaced(self):
        with tempfile.TemporaryDirectory() as tmp:
            root = Path(tmp)
            catalog_file = root / ".agents/plugins/marketplace.json"
            catalog_file.parent.mkdir(parents=True)
            original = '{"name":"unrelated","plugins":[]}'
            catalog_file.write_text(original)
            with mock.patch.object(SETUP.subprocess, "run") as run:
                with self.assertRaisesRegex(ValueError, "another marketplace"):
                    SETUP.register(root / "plugins/xhs", root, "codex")
            self.assertFalse(run.called)
            self.assertEqual(catalog_file.read_text(), original)


if __name__ == "__main__":
    unittest.main()
