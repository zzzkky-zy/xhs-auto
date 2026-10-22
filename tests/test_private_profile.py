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

    def test_export_omits_full_config_and_unknown_nested_credentials(self):
        data = json.loads(self.config.read_text())
        data.update(api_key='SECRET_SENTINEL', auth_token_env='SECRET_ENV',
                    timeout_seconds=123,
                    operations={'timezone': 'Asia/Shanghai', 'api_key': 'SECRET_NESTED'})
        self.config.write_text(json.dumps(data))
        module.export(self.config, self.bundle)
        self.assertFalse((self.bundle / 'same-host-settings.json').exists())
        metadata = json.loads((self.bundle / 'transfer.json').read_text())
        self.assertNotIn('SECRET', json.dumps(metadata))
        self.assertEqual(metadata['runtime_preferences']['timeout_seconds'], 123)
        self.assertEqual(metadata['runtime_preferences']['operations'], {'timezone': 'Asia/Shanghai'})

    def test_old_bundle_unknown_preferences_not_imported(self):
        module.export(self.config, self.bundle)
        p = self.bundle / 'transfer.json'; data = json.loads(p.read_text())
        data['runtime_preferences'].update(api_key='SECRET', allow_write=True,
                                           operations={'timezone': 'Asia/Shanghai', 'cookie': 'SECRET'})
        p.write_text(json.dumps(data))
        module.prepare(self.bundle, self.root / 'new', self.plugin)
        config = json.loads((self.root / 'new/xhs.local.json').read_text())
        self.assertNotIn('SECRET', json.dumps(config)); self.assertFalse(config['allow_write'])

    def test_unlisted_asset_rejected(self):
        module.export(self.config, self.bundle)
        (self.bundle / 'assets/extra').write_text('secret')
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new').exists())

    def test_symlink_and_hardlink_assets_rejected(self):
        import os
        module.export(self.config, self.bundle)
        asset = next((self.bundle / 'assets').iterdir()); content = asset.read_bytes()
        asset.unlink(); asset.symlink_to(self.material)
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        asset.unlink(); os.link(self.material, asset)
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new').exists())

    def test_parent_symlink_destination_rejected(self):
        module.export(self.config, self.bundle)
        link = self.root / 'link'; link.symlink_to(self.state, target_is_directory=True)
        with self.assertRaises(ValueError): module.prepare(self.bundle, link / 'new', self.plugin)
        self.assertFalse((self.state / 'new').exists())

    def test_manifest_traversal_rejected(self):
        module.export(self.config, self.bundle)
        p = self.bundle / 'transfer.json'; data = json.loads(p.read_text())
        for name in ('assets/../../original.json', '/tmp/escape', 'assets/../escape', 'assets/a/b'):
            data['files'] = {name: module.digest(self.material)}; p.write_text(json.dumps(data))
            with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new').exists())

    def test_replacement_after_validation_rechecked_before_copy(self):
        from unittest.mock import patch
        module.export(self.config, self.bundle)
        original = module.copy_verified
        def replace(source, target, expected):
            source.write_text('changed after validation')
            return original(source, target, expected)
        with patch.object(module, 'copy_verified', side_effect=replace):
            with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new/xhs.local.json').exists())
        self.assertFalse((self.root / 'new/configure-profile.json').exists())

    def test_identity_mismatch_rejected_before_destination(self):
        module.export(self.config, self.bundle)
        p = self.bundle / 'transfer.json'; data = json.loads(p.read_text())
        data['expected_user_id'] = 'different-user'; p.write_text(json.dumps(data))
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new').exists())

    def test_required_material_manifest_binding_checked(self):
        module.export(self.config, self.bundle)
        p = self.bundle / 'content-profile.json'; data = json.loads(p.read_text())
        data['product_materials'][0]['file'] = '${PRIVATE_ROOT}/assets/not-listed'
        p.write_text(json.dumps(data))
        meta = self.bundle / 'transfer.json'; data = json.loads(meta.read_text())
        data['profile_sha256'] = module.digest(p); meta.write_text(json.dumps(data))
        with self.assertRaises(ValueError): module.prepare(self.bundle, self.root / 'new', self.plugin)
        self.assertFalse((self.root / 'new').exists())

    def test_export_symlink_source_rejected_before_destination(self):
        original = self.root / 'original-material'; self.material.rename(original)
        self.material.symlink_to(original)
        with self.assertRaises(ValueError): module.export(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_known_structured_preference_not_silently_dropped(self):
        data = json.loads(self.config.read_text())
        data['operations'] = {'schedule': {'unknown': 'configuration'}}
        self.config.write_text(json.dumps(data))
        with self.assertRaises(ValueError): module.export(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_special_file_rejected_without_blocking(self):
        import os
        fifo = self.root / 'fifo'; os.mkfifo(fifo)
        with self.assertRaises(ValueError): module.read_bytes(fifo)

    def test_profile_cannot_indirectly_export_original_config(self):
        data = json.loads(self.config.read_text()); data['api_key'] = 'SECRET_REPRO'
        self.config.write_text(json.dumps(data))
        self.profile['confirmation_sources']['runtime_settings'] = str(self.config)
        with sqlite3.connect(self.state / 'tasks.sqlite3') as db:
            db.execute('UPDATE ops_profile_versions SET body=?', (json.dumps(self.profile),))
        with self.assertRaisesRegex(ValueError, 'configuration or credential'):
            module.export(self.config, self.bundle)
        self.assertFalse(self.bundle.exists())

    def test_real_cloud_runtime_profile_roundtrip(self):
        import sys
        sys.path.insert(0, str(ROOT / 'runtime'))
        import test_cloud_batch
        fixture = test_cloud_batch.CloudBatchTests(); fixture.setUp()
        try:
            fixture.batch.close(); fixture.actions.close()
            config_before = fixture.path.read_bytes()
            database = fixture.base / 'state/cloud-batches.sqlite3'
            db_before = database.read_bytes()
            bundle = fixture.base / 'business-bundle'
            module.export(fixture.path, bundle)
            prepared = fixture.base / 'business-prepared'
            result = module.prepare(bundle, prepared, self.plugin)
            settings = json.loads((prepared / 'xhs.local.json').read_text())
            self.assertEqual(settings['browser_mode'], 'cloud_host')
            self.assertEqual(settings['expected_user_id'], fixture.data['expected_user_id'])
            self.assertFalse(settings['allow_write']); self.assertFalse(result['state_initialized'])
            self.assertEqual(fixture.path.read_bytes(), config_before)
            self.assertEqual(database.read_bytes(), db_before)
            self.assertFalse((fixture.base / 'state/tasks.sqlite3').exists())
        finally:
            fixture.tmp.cleanup()

    def test_missing_cloud_database_does_not_fallback_or_initialize(self):
        data = json.loads(self.config.read_text()); data['browser_mode'] = 'cloud_host'
        self.config.write_text(json.dumps(data))
        with self.assertRaises(FileNotFoundError): module.export(self.config, self.bundle)
        self.assertFalse((self.state / 'cloud-batches.sqlite3').exists())
        self.assertFalse(self.bundle.exists())

    def test_confirmation_file_reference_must_be_in_manifest(self):
        module.export(self.config, self.bundle)
        for reference in ('${PRIVATE_ROOT}/assets/missing-evidence.json', '${PRIVATE_ROOT}/../outside'):
            p = self.bundle / 'content-profile.json'; data = json.loads(p.read_text())
            data['confirmation_sources']['extra'] = reference
            p.write_text(json.dumps(data))
            meta = self.bundle / 'transfer.json'; data = json.loads(meta.read_text())
            data['profile_sha256'] = module.digest(p); meta.write_text(json.dumps(data))
            with self.assertRaisesRegex(ValueError, 'profile file reference'):
                module.prepare(self.bundle, self.root / 'new', self.plugin)
            self.assertFalse((self.root / 'new').exists())
