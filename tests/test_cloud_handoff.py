"""Offline synthetic snapshots; no operational constructors or browser access."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sqlite3
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location('handoff', ROOT / 'scripts/cloud_handoff.py')
handoff = importlib.util.module_from_spec(spec); spec.loader.exec_module(handoff)


class HandoffTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory(); self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name); self.state = self.root / 'state'; self.state.mkdir()
        self.config = self.root / 'config.json'; self.manifest = self.root / 'manifest.json'
        self.config.write_text(json.dumps({'browser_mode': 'cloud_host', 'account_id': 'alias',
            'expected_user_id': 'exact-user', 'state_dir': 'state', 'private_token': 'NEVER_EXPORT_SECRET'}))
        self.media = self.root / 'cover.png'; self.media.write_bytes(b'fixture pixels')
        sha = hashlib.sha256(self.media.read_bytes()).hexdigest()
        self.db('cloud-actions.sqlite3', [
            'CREATE TABLE actions(account,operation_id,status,binding_json)',
            'CREATE TABLE action_events(id INTEGER PRIMARY KEY,event)',
            "INSERT INTO action_events VALUES (1,'prepared')"],
            ('INSERT INTO actions VALUES (?,?,?,?)', ('alias', 'op', 'submitted_unknown', json.dumps({
                'ledger_account': 'alias', 'expected_account': 'exact-user',
                'payload': {'xsec_token': 'NEVER_EXPORT_SECRET'},
                'media_manifest': [{'path': str(self.media), 'sha256': sha}]}))))
        self.db('cloud-batches.sqlite3', [
            'CREATE TABLE tasks(account,id,status)', "INSERT INTO tasks VALUES ('alias','op','submitted_unknown')",
            'CREATE TABLE ops_content_stages(seq INTEGER PRIMARY KEY)', 'INSERT INTO ops_content_stages VALUES (1)',
            'CREATE TABLE ops_runs(id)', "INSERT INTO ops_runs VALUES ('run')",
            'CREATE TABLE ops_policy(id,body)', 'CREATE TABLE ops_profile_versions(version,body)',
            "INSERT INTO ops_policy VALUES (1,'{\"content_profile_version\":\"v1\"}')",
            "INSERT INTO ops_profile_versions VALUES ('v1','{\"product_materials\":[]}')"])
        self.db('cloud-pacing.sqlite3', [
            'CREATE TABLE pacing_accounts(account)', "INSERT INTO pacing_accounts VALUES ('exact-user')",
            'CREATE TABLE pacing_events(id INTEGER PRIMARY KEY)', 'INSERT INTO pacing_events VALUES (1)',
            'CREATE TABLE pacing_attempts(account,attempt,status)',
            'CREATE TABLE pacing_steps(account,step,status)', "INSERT INTO pacing_steps VALUES ('exact-user','step','unknown')"])

    def db(self, name, statements, insert=None):
        with sqlite3.connect(self.state / name) as db:
            for statement in statements: db.execute(statement)
            if insert: db.execute(*insert)

    def create(self):
        return handoff.write_manifest(self.config, self.manifest)

    def tree(self):
        return {str(p.relative_to(self.root)): (p.stat().st_size, p.stat().st_mtime_ns, hashlib.sha256(p.read_bytes()).hexdigest())
                for p in self.root.rglob('*') if p.is_file()}

    def test_roundtrip_no_secret_export_and_zero_check_writes(self):
        report = self.create()
        self.assertNotIn('NEVER_EXPORT_SECRET', self.manifest.read_text() + json.dumps(report))
        self.assertEqual(self.manifest.stat().st_mode & 0o777, 0o600)
        before = self.tree()
        self.assertEqual(handoff.check_manifest(self.manifest)['status'], 'matches_recorded_snapshot')
        self.assertEqual(before, self.tree())
        data = json.loads(self.manifest.read_text())
        self.assertEqual(data['metadata']['actions']['unknown_ids'], ['op'])
        self.assertFalse(data['proves_latest_external_state'])

    def test_missing_database_is_not_created(self):
        (self.state / 'cloud-actions.sqlite3').unlink()
        before = self.tree()
        with self.assertRaisesRegex(handoff.HandoffError, 'REQUIRED_FILE_MISSING'): self.create()
        self.assertEqual(before, self.tree())

    def test_empty_database_or_wrong_account_is_rejected(self):
        self.db('cloud-actions.sqlite3', ['DELETE FROM actions'])
        with self.assertRaisesRegex(handoff.HandoffError, 'NO_EXISTING_ACCOUNT_HISTORY'): self.create()
        settings = json.loads(self.config.read_text()); settings['account_id'] = 'other'; self.config.write_text(json.dumps(settings))
        with self.assertRaisesRegex(handoff.HandoffError, 'NO_EXISTING_ACCOUNT_HISTORY'): self.create()

    def test_expected_identity_mismatch(self):
        settings = json.loads(self.config.read_text()); settings['expected_user_id'] = 'wrong'; self.config.write_text(json.dumps(settings))
        with self.assertRaisesRegex(handoff.HandoffError, 'ACCOUNT_BINDING_MISMATCH'): self.create()

    def test_wal_and_missing_or_changed_material_fail(self):
        sidecar = self.state / 'cloud-actions.sqlite3-wal'; sidecar.write_bytes(b'not quiescent')
        with self.assertRaisesRegex(handoff.HandoffError, 'SNAPSHOT_NOT_QUIESCENT'): self.create()
        sidecar.unlink(); self.media.write_bytes(b'changed')
        with self.assertRaisesRegex(handoff.HandoffError, 'MATERIAL_HASH_MISMATCH'): self.create()
        self.media.unlink()
        with self.assertRaisesRegex(handoff.HandoffError, 'REQUIRED_FILE_MISSING'): self.create()

    def test_old_snapshot_detects_event_and_unknown_change(self):
        self.create()
        self.db('cloud-actions.sqlite3', ["INSERT INTO action_events VALUES(2,'result')", "UPDATE actions SET status='not_applied'"])
        report = handoff.check_manifest(self.manifest)
        self.assertEqual(report['status'], 'review_required')
        self.assertIn('SNAPSHOT_METADATA_DIFFERS', report['difference_codes'])
        self.assertNotIn('NEVER_EXPORT_SECRET', json.dumps(report))

    def test_code_upgrade_is_review_not_data_loss(self):
        self.create()
        with mock.patch.object(handoff, 'code_fingerprint', return_value={'version': 'next', 'runtime': {}}):
            result = handoff.check_manifest(self.manifest)
        self.assertEqual(result['difference_codes'], ['CODE_VERSION_REVIEW_REQUIRED'])

    def test_no_overwrite_and_symlink_rejected(self):
        self.create(); before = self.tree()
        with self.assertRaisesRegex(handoff.HandoffError, 'OUTPUT_EXISTS'): self.create()
        self.assertEqual(before, self.tree())
        link = self.root / 'linked.json'; link.symlink_to(self.config)
        with self.assertRaisesRegex(handoff.HandoffError, 'UNSAFE_SYMLINK'): handoff.inspect(link)

    def test_extra_required_files_are_verified(self):
        missing = self.root / 'source.json'
        with self.assertRaisesRegex(handoff.HandoffError, 'REQUIRED_FILE_MISSING'):
            handoff.write_manifest(self.config, self.manifest, [missing])

    def test_mutation_during_inspection_detected(self):
        original = handoff.highwater
        changed = [False]
        def mutate(db, table, key):
            result = original(db, table, key)
            if not changed[0]:
                changed[0] = True
                self.config.write_text(self.config.read_text() + ' ')
            return result
        with mock.patch.object(handoff, 'highwater', side_effect=mutate):
            with self.assertRaisesRegex(handoff.HandoffError, 'SNAPSHOT_CHANGED_DURING_READ'): self.create()
        self.assertFalse(self.manifest.exists())

    def test_real_runtime_schema_integration(self):
        # All constructors are restricted to the synthetic fixture's fresh tempdir.
        import test_cloud_batch
        from cloud_pacing import CloudPacing
        fixture = test_cloud_batch.CloudBatchTests(); fixture.setUp()
        self.addCleanup(fixture.tmp.cleanup)
        try:
            run, items = fixture.reviewed()
            fixture.prepare_action(run, items[0])
            pacing = CloudPacing(fixture.path)
            try:
                pacing.reserve('search_feeds', 'synthetic-read')
            finally:
                pacing.close()
        finally:
            fixture.batch.close(); fixture.actions.close()
        manifest = fixture.base / 'handoff.json'
        handoff.write_manifest(fixture.path, manifest)
        self.assertEqual(handoff.check_manifest(manifest)['status'], 'matches_recorded_snapshot')
        data = json.loads(manifest.read_text())
        self.assertGreater(data['metadata']['batches']['content_stage_highwater']['count'], 0)
        self.assertNotEqual(data['account_id'], data['expected_user_id'])
        self.assertEqual(data['metadata']['pacing']['unknown_attempts'], ['synthetic-read'])

    def test_nonobject_config_has_stable_error(self):
        self.config.write_text('[]')
        with self.assertRaisesRegex(handoff.HandoffError, 'INVALID_CONFIG_OBJECT'): self.create()

    def test_cli_redacts_errors_without_traceback(self):
        import subprocess
        self.config.write_text('[]')
        result = subprocess.run([os.sys.executable, str(ROOT / 'scripts/cloud_handoff.py'),
            'manifest', '--config', str(self.config), '--output', str(self.manifest)], capture_output=True, text=True)
        self.assertEqual(result.returncode, 2)
        self.assertEqual(json.loads(result.stdout)['code'], 'INVALID_CONFIG_OBJECT')
        self.assertEqual(result.stderr, '')
        self.assertNotIn(str(self.root), result.stdout)

    def test_empty_sqlite_schema_is_rejected_without_migration(self):
        path = self.state / 'cloud-actions.sqlite3'
        path.write_bytes(b'')
        before = self.tree()
        with self.assertRaisesRegex(handoff.HandoffError, 'UNSUPPORTED_OR_INCOMPLETE_SCHEMA'): self.create()
        self.assertEqual(before, self.tree())

    def test_configuration_drift_requires_review(self):
        self.create()
        settings = json.loads(self.config.read_text()); settings['private_token'] = 'CHANGED_SECRET'
        self.config.write_text(json.dumps(settings))
        result = handoff.check_manifest(self.manifest)
        self.assertEqual(result['status'], 'review_required')
        self.assertIn('SNAPSHOT_FILES_DIFFERS', result['difference_codes'])
        self.assertNotIn('CHANGED_SECRET', json.dumps(result))
