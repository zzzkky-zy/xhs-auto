"""Entirely synthetic recovery tests; never open live account state."""
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import sys
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'scripts'))
import cloud_checkpoint as checkpoint
import test_cloud_batch
from cloud_pacing import CloudPacing


class CheckpointTests(unittest.TestCase):
    def setUp(self):
        fixture = test_cloud_batch.CloudBatchTests(); fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        self.base = fixture.base
        self.config = fixture.path
        self.run, items = fixture.reviewed()
        fixture.prepare_action(self.run, items[0])
        fixture.actions.set_execution_pause(True, 'synthetic pause', 'synthetic user')
        pacing = CloudPacing(fixture.path)
        pacing.reserve('search_feeds', 'synthetic-unknown')
        pacing.close(); fixture.batch.close(); fixture.actions.close()
        settings = json.loads(self.config.read_text()); settings['private_token'] = 'CONFIG_SECRET_NEVER_COPY'
        self.config.write_text(json.dumps(settings))
        self.bundle = self.base / 'checkpoint'
        self.restored = self.base / 'recovered'

    def create(self):
        return checkpoint.create(self.config, self.bundle)

    def test_real_schema_roundtrip_private_quarantine(self):
        result = self.create()
        self.assertFalse(result['latest_verified'])
        self.assertTrue(result['may_contain_credentials'])
        self.assertEqual(checkpoint.verify(self.bundle, result['checkpoint_digest'])['trusted_digest_matched'], True)
        self.assertNotIn('CONFIG_SECRET_NEVER_COPY', (self.bundle / 'checkpoint.json').read_text())
        result = checkpoint.restore(self.bundle, self.restored)
        self.assertFalse(result['activated'])
        self.assertTrue((self.restored / 'evidence/state' / checkpoint.MARKER).exists())
        for item in json.loads((self.bundle / 'checkpoint.json').read_text())['files']:
            self.assertEqual((self.bundle / item['file']).read_bytes(), (self.restored / 'evidence' / item['file']).read_bytes())
        with sqlite3.connect(self.restored / 'evidence/state/cloud-actions.sqlite3') as db:
            self.assertEqual(db.execute('SELECT paused FROM execution_control_events ORDER BY id DESC LIMIT 1').fetchone()[0], 1)
        with sqlite3.connect(self.restored / 'evidence/state/cloud-pacing.sqlite3') as db:
            self.assertEqual(db.execute('SELECT status FROM pacing_attempts').fetchone()[0], 'unknown')
        for p in self.bundle.rglob('*'):
            self.assertEqual(p.stat().st_mode & 0o777, 0o700 if p.is_dir() else 0o600)

    def test_missing_db_never_created(self):
        path = self.base / 'state/cloud-actions.sqlite3'; path.unlink()
        with self.assertRaisesRegex(ValueError, 'REQUIRED_FILE_MISSING'): self.create()
        self.assertFalse(path.exists()); self.assertFalse(self.bundle.exists())

    def test_missing_run_owner_rejected(self):
        (self.base / 'output/operation-runs' / self.run['id'] / '.owner.json').unlink()
        with self.assertRaisesRegex(ValueError, 'REQUIRED_FILE_MISSING'): self.create()

    def test_unknown_structured_material_missing_rejected(self):
        with sqlite3.connect(self.base / 'state/cloud-batches.sqlite3') as db:
            db.execute('CREATE TABLE synthetic_evidence(body)')
            db.execute('INSERT INTO synthetic_evidence VALUES (?)', (json.dumps({'file': str(self.base / 'missing-proof.png')}),))
        with self.assertRaisesRegex(ValueError, 'REQUIRED_FILE_MISSING'): self.create()

    def test_source_change_leaves_incomplete_not_usable(self):
        original = checkpoint.backup_database
        def changing(source, target):
            original(source, target)
            self.config.write_text(self.config.read_text() + ' ')
        with mock.patch.object(checkpoint, 'backup_database', side_effect=changing):
            with self.assertRaisesRegex(ValueError, 'SOURCE_CHANGED_DURING_CHECKPOINT'): self.create()
        self.assertTrue((self.bundle / 'INCOMPLETE.json').exists())
        self.assertFalse((self.bundle / 'checkpoint.json').exists())

    def test_existing_restore_destination_untouched(self):
        self.create(); self.restored.mkdir(); (self.restored / 'keep').write_text('keep')
        with self.assertRaisesRegex(ValueError, 'DESTINATION_EXISTS'): checkpoint.restore(self.bundle, self.restored)
        self.assertEqual((self.restored / 'keep').read_text(), 'keep')

    def test_added_bundle_file_rejected(self):
        self.create(); (self.bundle / 'extra').write_text('not listed')
        with self.assertRaisesRegex(ValueError, 'UNLISTED'): checkpoint.restore(self.bundle, self.restored)
        self.assertFalse(self.restored.exists())

    def test_wrong_trusted_digest_rejected(self):
        self.create()
        with self.assertRaisesRegex(ValueError, 'TRUSTED_CHECKPOINT_MISMATCH'):
            checkpoint.restore(self.bundle, self.restored, '0' * 64)
        self.assertFalse(self.restored.exists())

    def test_link_in_run_rejected(self):
        path = self.base / 'output/operation-runs' / self.run['id'] / 'work/link'
        path.symlink_to(self.config)
        with self.assertRaisesRegex(ValueError, 'UNSAFE_SYMLINK'): self.create()

    def test_hardlink_in_run_rejected(self):
        path = self.base / 'output/operation-runs' / self.run['id'] / 'work/link'
        os.link(self.config, path)
        with self.assertRaisesRegex(ValueError, 'UNSAFE_FILE_TYPE'): self.create()

    def test_nonempty_sidecar_rejected(self):
        (self.base / 'state/cloud-actions.sqlite3-wal').write_bytes(b'active')
        with self.assertRaisesRegex(ValueError, 'SNAPSHOT_NOT_QUIESCENT'): self.create()

    def test_restore_copy_failure_already_quarantined(self):
        self.create()
        with mock.patch.object(checkpoint, 'copy_file', side_effect=OSError('synthetic')):
            with self.assertRaises(OSError): checkpoint.restore(self.bundle, self.restored)
        self.assertTrue((self.restored / checkpoint.MARKER).exists())
        self.assertTrue((self.restored / 'evidence/state' / checkpoint.MARKER).exists())
        self.assertFalse((self.restored / 'checkpoint.json').exists())

    def test_empty_database_rejected_without_initializing(self):
        path = self.base / 'state/cloud-actions.sqlite3'; path.write_bytes(b'')
        with self.assertRaises(ValueError): self.create()
        self.assertEqual(path.read_bytes(), b'')
        self.assertFalse(self.bundle.exists())

    def test_wrong_run_owner_rejected(self):
        owner = self.base / 'output/operation-runs' / self.run['id'] / '.owner.json'
        data = json.loads(owner.read_text()); data['state_id'] = 'wrong'; owner.write_text(json.dumps(data))
        with self.assertRaisesRegex(ValueError, 'RUN_OWNER_MISMATCH'): self.create()

    def test_removed_material_not_hidden_by_manifest_list(self):
        self.create(); path = self.bundle / 'checkpoint.json'; manifest = json.loads(path.read_text())
        removed = next(item for item in manifest['files'] if item['kind'] == 'material')
        manifest['files'].remove(removed); (self.bundle / removed['file']).unlink()
        path.write_text(json.dumps(manifest))
        with self.assertRaisesRegex(ValueError, 'INCOMPLETE_CHECKPOINT_COVERAGE'): checkpoint.verify(self.bundle)

    def test_source_files_remain_byte_identical(self):
        before = {p: p.read_bytes() for p in self.base.rglob('*') if p.is_file()}
        self.create(); checkpoint.restore(self.bundle, self.restored)
        for path, data in before.items(): self.assertEqual(path.read_bytes(), data)

    def test_later_fact_not_removed_by_old_checkpoint(self):
        self.create()
        path = self.base / 'state/cloud-actions.sqlite3'
        with sqlite3.connect(path) as db:
            db.execute("INSERT INTO execution_control_events(expected_account,ledger_account,paused,recorded_at,detail) VALUES(?,?,?,?,?)",
                       ('a' * 24, 'fixture', 1, 1900000000, '{"later_fact":true}'))
        later = path.read_bytes()
        result = checkpoint.restore(self.bundle, self.restored)
        self.assertFalse(result['latest_verified'])
        self.assertEqual(path.read_bytes(), later)

    def test_copy_changed_file_is_rejected(self):
        original = checkpoint.copy_file
        def mutate(source, destination):
            original(source, destination)
            destination.write_bytes(destination.read_bytes() + b'changed')
        with mock.patch.object(checkpoint, 'copy_file', side_effect=mutate):
            with self.assertRaisesRegex(ValueError, 'MATERIAL_CHANGED_DURING_COPY'): self.create()
        self.assertTrue((self.bundle / 'INCOMPLETE.json').exists())

    def test_cli_error_does_not_disclose_path_or_config(self):
        import subprocess
        self.config.write_text('[]')
        result = subprocess.run([sys.executable, str(ROOT / 'scripts/cloud_checkpoint.py'), 'create',
            '--config', str(self.config), '--destination', str(self.bundle)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(result.stderr, '')
        self.assertNotIn(str(self.base), result.stdout)
        self.assertFalse(json.loads(result.stdout)['execution_allowed'])

    def test_named_credential_file_refused_not_silently_dropped(self):
        path = self.base / 'output/operation-runs' / self.run['id'] / 'work/.env'
        path.write_text('SECRET=never-copy')
        with self.assertRaisesRegex(ValueError, 'CREDENTIAL_FILE_IN_MATERIAL_SET'): self.create()
        self.assertFalse(self.bundle.exists())

    def test_config_cannot_be_smuggled_as_required_material(self):
        with self.assertRaisesRegex(ValueError, 'CONFIG_FILE_NOT_ALLOWED_AS_MATERIAL'):
            checkpoint.create(self.config, self.bundle, [self.config])
        self.assertFalse(self.bundle.exists())

    def test_manifest_second_read_replacement_rejected_before_any_copy(self):
        import copy
        self.create()
        original = checkpoint.handoff.read_json
        count = [0]
        def replace_second(path):
            value = original(path)
            if Path(path) == self.bundle / 'checkpoint.json':
                count[0] += 1
                if count[0] == 2:
                    value = copy.deepcopy(value)
                    value['files'].append({'file': '../outside', 'source': 'outside', 'sha256': 'bad', 'size': 1})
            return value
        with mock.patch.object(checkpoint.handoff, 'read_json', side_effect=replace_second), mock.patch.object(checkpoint, 'copy_file') as copying:
            with self.assertRaisesRegex(ValueError, 'CHECKPOINT_CHANGED_BEFORE_RESTORE'):
                checkpoint.restore(self.bundle, self.restored)
            copying.assert_not_called()
        self.assertFalse(self.restored.exists())
        self.assertFalse((self.base / 'outside').exists())

    def stage_file(self):
        with sqlite3.connect(self.base / 'state/cloud-batches.sqlite3') as db:
            row = db.execute('SELECT body FROM ops_content_stages ORDER BY seq LIMIT 1').fetchone()
        return Path(json.loads(row[0])['files'][0]['path'])

    def add_historical_reference(self, value):
        with sqlite3.connect(self.base / 'state/cloud-batches.sqlite3') as db:
            db.execute('CREATE TABLE synthetic_historical_reference(body)')
            db.execute('INSERT INTO synthetic_historical_reference VALUES(?)', (json.dumps(value),))

    def test_real_stage_file_changed_since_review_rejected(self):
        path = self.stage_file(); path.write_bytes(path.read_bytes() + b' ')
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_MATERIAL_HASH_CONFLICT') as caught:
            self.create()
        self.assertTrue(any(x['path'] == str(path) for x in caught.exception.conflicts))
        self.assertFalse(self.bundle.exists())

    def test_same_bytes_at_new_path_does_not_rewrite_history(self):
        path = self.stage_file(); original = path.read_bytes()
        moved = path.with_name('moved-stage.json'); path.rename(moved)
        with self.assertRaisesRegex(ValueError, 'REQUIRED_FILE_MISSING'): self.create()
        self.assertEqual(moved.read_bytes(), original)
        with sqlite3.connect(self.base / 'state/cloud-batches.sqlite3') as db:
            row = db.execute('SELECT body FROM ops_content_stages ORDER BY seq LIMIT 1').fetchone()
        self.assertEqual(json.loads(row[0])['files'][0]['path'], str(path))

    def test_same_filename_elsewhere_does_not_satisfy_historical_hash(self):
        import hashlib
        first = self.base / 'one'; second = self.base / 'two'; first.mkdir(); second.mkdir()
        (first / 'proof.json').write_bytes(b'expected')
        (second / 'proof.json').write_bytes(b'different')
        self.add_historical_reference({'files': [{'path': str(second / 'proof.json'),
            'sha256': hashlib.sha256(b'expected').hexdigest()}]})
        with self.assertRaisesRegex(ValueError, 'HISTORICAL_MATERIAL_HASH_CONFLICT'): self.create()

    def test_historical_hash_type_invalid_rejected(self):
        self.add_historical_reference({'path': str(self.stage_file()), 'sha256': 123})
        with self.assertRaisesRegex(ValueError, 'INVALID_HISTORICAL_FILE_HASH'): self.create()

    def test_historical_path_traversal_rejected(self):
        self.add_historical_reference({'path': str(self.base) + '/state/../proof.json', 'sha256': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'UNSAFE_HISTORICAL_FILE_PATH'): self.create()

    def test_no_hash_reference_recorded_as_unverified_not_fabricated(self):
        path = self.base / 'legacy-proof.txt'; path.write_text('legacy')
        self.add_historical_reference({'file': str(path), 'text': '/ordinary prose is not a file'})
        self.create()
        manifest = json.loads((self.bundle / 'checkpoint.json').read_text())
        self.assertIn(str(path), manifest['source_inventory']['unverified_historical_paths'])

    def test_hashed_reference_nonstring_path_rejected(self):
        self.add_historical_reference({'path': 123, 'sha256': '0' * 64})
        with self.assertRaisesRegex(ValueError, 'INVALID_HISTORICAL_FILE_PATH'): self.create()
        self.assertFalse(self.bundle.exists())

    def test_confirmation_file_reference_traversal_rejected(self):
        self.add_historical_reference({'confirmation_sources': {'proof': str(self.base) + '/state/../proof.json'}})
        with self.assertRaisesRegex(ValueError, 'UNSAFE_HISTORICAL_FILE_PATH'): self.create()
        self.assertFalse(self.bundle.exists())


if __name__ == '__main__': unittest.main()
