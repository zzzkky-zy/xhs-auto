"""Offline content-resource checks; never generate images or contact Xiaohongshu."""
import hashlib
import json
from pathlib import Path
import re
import unittest


ROOT = Path(__file__).resolve().parents[1]
SKILL = ROOT / "skills/xiaohongshu-operations"
VISUAL = SKILL / "references/visuals"


def load(name):
    return json.loads((VISUAL / name).read_text())


def table(path):
    return [
        [part.strip().strip("`") for part in line.split("|")[1:-1]]
        for line in path.read_text().splitlines()
        if line.startswith("|")
    ]


class VisualContentTests(unittest.TestCase):
    def setUp(self):
        self.catalog = load("resource-catalog.json")
        self.modules = self.catalog["modules"]
        self.provenance = load("upstream-sources.json")

    def test_full_preset_inventory_matches_pinned_source_after_exact_exclusions(self):
        expected = {
            ("infographic", "layout"): ("sweety-infographic", "layouts"),
            ("infographic", "style"): ("sweety-infographic", "styles"),
            ("cover", "palette"): ("sweety-cover-image", "palettes"),
            ("cover", "rendering"): ("sweety-cover-image", "renderings"),
            ("comic", "art"): ("sweety-comic", "art-styles"),
            ("comic", "tone"): ("sweety-comic", "tones"),
            ("comic", "layout"): ("sweety-comic", "layouts"),
        }
        excluded = set(self.catalog["exclusions"]["new-modules"])
        for (module, dimension), (upstream, folder) in expected.items():
            prefix = "skills/" + upstream + "/references/" + folder + "/"
            source_values = {
                Path(item["source_path"]).stem
                for item in self.provenance["inventory"]
                if item["source_path"].startswith(prefix)
            }
            actual = set(self.modules[module]["dimensions"][dimension]["values"])
            self.assertEqual(actual, source_values - excluded, (module, dimension))
        self.assertEqual(
            set(self.modules["xhs-series"]["dimensions"]["style"]["values"]),
            {"cute", "fresh", "warm", "notion", "chalkboard", "study-notes", "screen-print"},
        )

    def test_every_selection_has_a_definition_and_defaults_are_valid(self):
        for name, module in self.modules.items():
            self.assertTrue((VISUAL / module["guide"]).is_file(), name)
            for dimension, spec in module.get("dimensions", {}).items():
                values = spec["values"]
                self.assertEqual(len(values), len(set(values)), (name, dimension))
                if "definition_dir" in spec:
                    folder = VISUAL / spec["definition_dir"]
                    self.assertEqual(set(values), {p.stem for p in folder.glob("*.md")})
                else:
                    content = (VISUAL / spec["definition_file"]).read_text()
                    for value in values:
                        self.assertIn(value, content, (name, dimension, value))
            for dimension, value in module.get("defaults", {}).items():
                self.assertIn(value, module["dimensions"][dimension]["values"])
            for preset, expansion in module.get("presets", {}).items():
                for dimension, value in expansion.items():
                    if dimension == "definition":
                        self.assertTrue((VISUAL / value).is_file(), preset)
                    else:
                        self.assertIn(value, module["dimensions"][dimension]["values"], preset)

    def test_combo_tables_and_catalog_agree(self):
        for name, path, dimensions in [
            ("xhs-series", "style-presets.md", ("style", "layout")),
            ("cover", "modules/cover/references/style-presets.md", ("palette", "rendering")),
        ]:
            actual = {
                row[0]: dict(zip(dimensions, row[1:]))
                for row in table(VISUAL / path)
                if len(row) == 3 and row[0] in self.modules[name]["presets"]
            }
            self.assertEqual(actual, self.modules[name]["presets"])
            declared_rows = [line for line in (VISUAL / path).read_text().splitlines() if line.startswith("| `")]
            self.assertEqual(len(declared_rows), len(actual), "unknown or duplicated shortcut")

    def test_removed_options_cannot_be_recommended_as_selectors(self):
        removed_xhs = self.catalog["exclusions"]["xhs-series.style"]
        xhs_files = list((VISUAL / "presets").glob("*.md"))
        xhs_files += list((VISUAL / "elements").glob("*.md"))
        xhs_files += [VISUAL / "style-presets.md", VISUAL / "xhs-series.md"]
        for name in removed_xhs:
            self.assertFalse((VISUAL / "presets" / (name + ".md")).exists())
            for path in xhs_files:
                self.assertNotRegex(path.read_text(), r"`" + re.escape(name) + r"`", str(path))
                self.assertNotRegex(path.read_text(), r"(?im)^### " + name + r" Style$", str(path))
        removed = self.catalog["exclusions"]["new-modules"]
        for path in (VISUAL / "modules").rglob("*.md"):
            self.assertNotIn(path.stem, removed)
            for name in removed:
                self.assertNotRegex(path.read_text(), r"`" + re.escape(name) + r"`", str(path))
        # These are different dimensions or distinct names and must survive.
        for key, values in self.catalog["dimension_boundaries"].items():
            module, dimension = key.split(".")
            actual = self.modules[module]["presets"] if dimension == "presets" else (
                self.modules[module]["dimensions"]["art"]["values"]
                if dimension == "preserved_styles" else self.modules[module]["dimensions"][dimension]["values"]
            )
            for value in values:
                self.assertIn(value, actual, key)

    def test_recommendations_use_valid_dimensions_without_compatibility_conflicts(self):
        styles = set(self.modules["infographic"]["dimensions"]["style"]["values"])
        for path in (VISUAL / "modules/infographic/references/layouts").glob("*.md"):
            recommendations = path.read_text().split("## Recommended Pairings", 1)[1]
            for value in re.findall(r"`([a-z0-9-]+)`", recommendations):
                self.assertIn(value, styles, str(path))
        comic = self.modules["comic"]["dimensions"]
        for path in [VISUAL / "modules/comic/references/auto-selection.md"]:
            for row in table(path):
                if row[0] not in comic["art"]["values"] or len(row) != 4:
                    continue
                groups = [set(re.findall(r"[a-z-]+", cell)) - {"—"} for cell in row[1:]]
                for group in groups:
                    self.assertLessEqual(group, set(comic["tone"]["values"]))
                self.assertFalse(groups[0] & groups[1], row)
                self.assertFalse(groups[0] & groups[2], row)
                self.assertFalse(groups[1] & groups[2], row)
        for path in (VISUAL / "modules/comic/references/art-styles").glob("*.md"):
            for row in table(path):
                if len(row) == 3 and row[1] in {"✓✓", "✓", "✗"}:
                    self.assertIn(row[0], comic["tone"]["values"], str(path))

    def test_local_links_resolve_without_network(self):
        paths = list(VISUAL.rglob("*.md")) + [
            SKILL / "SKILL.md", SKILL / "references/content.md", SKILL / "references/visual-production.md"
        ]
        for path in paths:
            for target in re.findall(r"\[[^\]]*\]\(([^)]+)\)", path.read_text()):
                if re.match(r"[a-z]+:", target) or target.startswith("#"):
                    continue
                target = target.split("#", 1)[0].strip("<>")
                self.assertTrue((path.parent / target).resolve().exists(), str(path) + " -> " + target)

    def test_provenance_and_unmodified_naturalizer_references(self):
        self.assertEqual(self.provenance["commit"], "3ddcaeb80310daf991797ad2772bba82709d6f3e")
        inputs = {r["source_path"]: r["sha256"] for r in self.provenance["inventory"]}
        destinations = set()
        for item in self.provenance["resources"]:
            self.assertNotIn(item["path"], destinations)
            destinations.add(item["path"])
            self.assertEqual(item["source_sha256"], inputs[item["source_path"]])
            data = (VISUAL / item["path"]).read_bytes()
            digest = hashlib.sha256(data).hexdigest()
            self.assertEqual(digest, item["local_sha256"], item["path"])
            self.assertEqual(item["adapted"], digest != item["source_sha256"])
        for item in self.provenance["inventory"]:
            if item["disposition"] == "bundled":
                self.assertTrue(any(r["source_path"] == item["source_path"] for r in self.provenance["resources"]), item)
            else:
                self.assertTrue(item["reason"], item)
        naturalizer = self.modules["naturalizer"]
        self.assertEqual(set(naturalizer["applies_to"]), {"xhs-series", "infographic", "cover", "gbro-cover-design", "comic", "character-sheet"})
        for name in naturalizer["required_references"]:
            record = next(r for r in self.provenance["resources"] if r["path"] == name)
            self.assertFalse(record["adapted"], "complete Naturalizer reference must be preserved")
        self.assertTrue((VISUAL / "UPSTREAM-LICENSE-NOTICE.txt").is_file())

    def test_current_methods_are_complete_and_historical_snapshots_absent(self):
        self.assertFalse((VISUAL / "originals").exists())
        for module in ("infographic", "cover", "comic", "naturalizer"):
            self.assertTrue((VISUAL / "modules" / module / "method.md").is_file())
        for name in ("analysis-framework.md", "outline-template.md", "prompt-assembly.md"):
            self.assertGreater(len((VISUAL / "workflows" / name).read_text().splitlines()), 100)

    def test_canonical_brand_bytes_are_unchanged(self):
        brand = SKILL / "assets/brand"
        for item in json.loads((brand / "manifest.json").read_text())["assets"]:
            self.assertEqual(hashlib.sha256((brand / item["file"]).read_bytes()).hexdigest(), item["sha256"])

    def test_gbro_complete_original_tree_and_catalog_match(self):
        module = self.modules["gbro-cover-design"]
        manifest_path = VISUAL / module["provenance"]
        manifest = json.loads(manifest_path.read_text())
        self.assertEqual(manifest["repository"], "https://github.com/pyang5166/gbro-cover-design")
        self.assertEqual(manifest["commit"], "8d1a0a5487e9ee6539b2b0a471b58469aadfedd6")
        self.assertEqual(len(manifest["files"]), 24)
        original = manifest_path.parent / "upstream"
        self.assertEqual(
            {item["source_path"] for item in manifest["files"]},
            {str(path.relative_to(original)) for path in original.rglob("*") if path.is_file()},
        )
        for item in manifest["files"]:
            data = (manifest_path.parent / item["path"]).read_bytes()
            self.assertEqual(len(data), item.get("local_bytes", item["bytes"]))
            self.assertEqual(hashlib.sha256(data).hexdigest(), item.get("local_sha256", item["sha256"]), item["path"])
            if not item.get("adapted"):
                self.assertEqual(
                    hashlib.sha1(b"blob " + str(len(data)).encode() + b"\0" + data).hexdigest(),
                item["git_blob"], item["path"],
            )
            self.assertEqual(item["mode"], "100644")
        self.assertEqual(set(module["style_templates"]), {f"style-{n:02}" for n in range(1, 11)})
        self.assertEqual(
            {(VISUAL / item["definition"]).resolve() for item in module["style_templates"].values()},
            {path.resolve() for path in (original / "references").glob("style-*.md")},
        )
        self.assertEqual(len(list((original / "assets/examples").glob("*.webp"))), 8)
        self.assertTrue((original / "LICENSE").is_file())
        self.assertFalse((original / "assets/my-face.png").exists(), "user portraits stay outside packages")
        self.assertFalse((original / "config.md").exists(), "user configuration stays outside packages")

    def test_restored_original_content_files_are_unmodified(self):
        manifest = json.loads((ROOT / "docs/content-originals.json").read_text())
        self.assertGreater(len(manifest["files"]), 5)
        actual = {str(p.relative_to(ROOT)) for p in (SKILL / "references/original-skills").rglob("*") if p.is_file()}
        declared = {item["path"] for item in manifest["files"]}
        self.assertTrue(actual <= declared)
        for item in manifest["files"]:
            self.assertEqual(hashlib.sha256((ROOT / item["path"]).read_bytes()).hexdigest(),
                             item["sha256"], item["path"])


if __name__ == "__main__":
    unittest.main()
