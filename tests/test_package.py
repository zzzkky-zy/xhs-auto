"""Runtime/source boundaries: preserve skill resources without shipping build dependencies."""
import importlib.util
from pathlib import Path
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location("package_files", ROOT / "scripts/package.py")
package = importlib.util.module_from_spec(spec)
spec.loader.exec_module(package)


class PackageBoundaryTests(unittest.TestCase):
    def test_runtime_keeps_full_skills_but_no_build_sources_or_tests(self):
        runtime = {p.relative_to(ROOT).as_posix() for p in package.files("linux-amd64")}
        source = {p.relative_to(ROOT).as_posix() for p in package.files("source")}
        skills = {p for p in source if p.startswith("skills/")}
        self.assertTrue(skills)
        self.assertTrue(skills.issubset(runtime))
        self.assertNotIn("scripts/build.py", runtime)
        self.assertNotIn("scripts/test.py", runtime)
        self.assertFalse(any(p.startswith("tests/") or p.endswith(".go") for p in runtime))
        self.assertIn("mcp/xhs-mcp-custom/go.mod", source)
        self.assertIn("mcp/xhs-mcp-custom/vendor/modules.txt", source)
        self.assertTrue(any(p.startswith("tests/") for p in source))
        self.assertIn("scripts/setup_codex.py", runtime)
        self.assertIn("scripts/cloud_host.py", runtime)

    def test_runtime_keeps_dependency_licenses(self):
        runtime = set(package.files("darwin-arm64"))
        module = ROOT / "mcp/xhs-mcp-custom"
        self.assertIn(module / "LICENSE", runtime)
        licenses = [p for p in (module / "vendor").rglob("*") if p.is_file() and
                    p.name.lower().startswith(("license", "copying", "notice"))]
        self.assertTrue(licenses)
        self.assertTrue(set(licenses).issubset(runtime))


if __name__ == "__main__":
    unittest.main()
