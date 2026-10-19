import importlib.util
import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('private_profile', ROOT / 'scripts/private_profile.py')
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)


class PrivateProfileTransferTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        self.state = self.root / 'state'; self.state.mkdir()
        self.material = self.root / '完整产品.md'; self.material.write_text('完整能力、场景和福利，不概括。')
        self.profile = {'product_materials': [{'file': str(self.material), 'sha256': module.digest(self.material), 'source_ref': 'user'}],
                        'confirmation_sources': {'product_materials': str(self.material)},
                        'voice_samples': [{'text': '私我给你免费体验。'}]}
        self.config = self.root / 'original.json'
        self.config.write_text(json.dumps({'account_id': 'test-account', 'expected_user_id': 'test-user',
                                          'state_dir': str(self.state), 'expected_state_id': 'old-state', 'allow_write': True}))
        with sqlite3.connect(self.state / 'tasks.sqlite3') as db:
            db.execute('create table ops_policy(id integer primary key,body text)')
            db.execute('create table ops_profile_versions(version text,body text)')
            db.execute('insert into ops_policy values(1,?)', (json.dumps({'content_profile_version': 'v1','allowed_tools': ['post_comment_to_feed']}),))
            db.execute('insert into ops_profile_versions values(?,?)', ('v1',json.dumps(self.profile)))
        self.bundle = self.root / 'bundle'
        self.plugin = self.root / 'plugin'; (self.plugin / 'runtime').mkdir(parents=True)
        (self.plugin / 'bin').mkdir(); (self.plugin / 'config').mkdir()
        (self.plugin / 'runtime/xhs_ops.py').touch(); (self.plugin / 'bin/xhs-mcp-custom').touch()
        (self.plugin / 'config/xhs.example.json').write_text((ROOT / 'config/xhs.example.json').read_text())

    def test_export_and_relocate_preserves_full_material_and_original_state(self):
        original_config = self.config.read_bytes(); original_db = (self.state / 'tasks.sqlite3').read_bytes()
        module.export(self.config, self.bundle)
        moved = self.root / 'moved'; self.bundle.rename(moved)
        destination = self.root / 'prepared'
        module.prepare(moved, destination, self.plugin)
        restored = json.loads((destination / 'configure-profile.json').read_text())['content_profile']
        self.assertEqual(Path(restored['product_materials'][0]['file']).read_text(), self.material.read_text())
        self.assertEqual(restored['voice_samples'], self.profile['voice_samples'])
        self.assertTrue(Path(restored['confirmation_sources']['product_materials']).is_file())
        self.assertEqual(self.config.read_bytes(), original_config)
        self.assertEqual((self.state / 'tasks.sqlite3').read_bytes(), original_db)
        config = json.loads((destination / 'xhs.local.json').read_text())
        self.assertFalse(config['allow_write']); self.assertEqual(config['expected_state_id'], '')
        self.assertFalse((destination / 'state/tasks.sqlite3').exists())

    def test_existing_destination_is_never_overwritten(self):
        module.export(self.config, self.bundle)
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.state, self.plugin)
        self.assertTrue((self.state / 'tasks.sqlite3').is_file())

    def test_tampered_material_rejected_before_creating_destination(self):
        module.export(self.config, self.bundle)
        next((self.bundle / 'assets').iterdir()).write_text('bad')
        dest = self.root / 'new'
        with self.assertRaises(ValueError): module.prepare(self.bundle, dest, self.plugin)
        self.assertFalse(dest.exists())

    def test_tampered_profile_rejected(self):
        module.export(self.config, self.bundle)
        (self.bundle / 'content-profile.json').write_text('{}')
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)

    def test_missing_source_fails_export_without_partial_bundle(self):
        self.material.unlink()
        with self.assertRaises(ValueError): module.export(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())
