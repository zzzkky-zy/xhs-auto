"""Offline lifecycle tests only. No website, login, upload, or browser writes."""
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import subprocess
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import cloud_actions as cloud


class CloudActionTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.config = self.base / 'cloud.json'
        self.data = {'schema_version': 1, 'browser_mode': 'cloud_host',
                     'account_id': 'fixture', 'state_dir': 'state',
                     'allow_write': False, 'allow_reconcile': False}
        self.save_config()
        self.time = 1792404000.0
        self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)

    def tearDown(self):
        self.ledger.close()
        self.tmp.cleanup()

    def test_optional_write_scope_excludes_unapproved_mutations(self):
        self.data.update(allow_write=True, allowed_write_operations=['publish_content', 'post_comment_to_feed', 'reply_comment_in_feed'])
        self.save_config()
        self.ledger._guard_write('post_comment_to_feed')
        for operation in ('reply_direct_message', 'like_feed', 'favorite_feed', 'publish_with_video', 'list_notifications'):
            with self.subTest(operation=operation), self.assertRaisesRegex(ValueError, 'ERR_WRITE_SCOPE'):
                self.ledger._guard_write(operation)
        self.data['allowed_write_operations']=[]; self.save_config()
        with self.assertRaisesRegex(ValueError, 'ERR_WRITE_SCOPE'):
            self.ledger._guard_write('post_comment_to_feed')

    def test_invalid_write_scope_fails_closed(self):
        self.data.update(allow_write=True, allowed_write_operations='publish_content'); self.save_config()
        with self.assertRaisesRegex(ValueError, 'ERR_WRITE_SCOPE'):
            self.ledger._guard_write('publish_content')

    def save_config(self): self.config.write_text(json.dumps(self.data))
    def stamp(self, delta=0): return cloud._iso(self.time + delta)

    def prepare(self, ident='action-1', **overrides):
        args = {'operation': 'post_comment_to_feed', 'payload': {'feed_id': 'a' * 24, 'content': 'Fixture only' if ident == 'action-1' else 'Fixture only ' + ident},
                'expected_account': 'public-user-1', 'target': {'feed_id': 'a' * 24},
                'user_source': 'user-message:123', 'operation_id': ident}
        args.update(overrides)
        return self.ledger.prepare(**args)

    def attestation(self, action, **overrides):
        value = {'operation_id': action['operation_id'], 'binding_digest': action['binding_digest'],
                 'expected_account': action['binding']['expected_account'],
                 'user_source': action['binding']['user_source'], 'user_quote': 'Post this exact fixture comment to this note.',
                 'confirmed_at': self.stamp(), 'expires_at': self.stamp(600), 'evidence_ref': 'fixture:user-message:123'}
        value.update(overrides)
        return value

    def identity(self, **overrides):
        value = {'status': 'observed', 'account_id': 'public-user-1', 'observed_at': self.stamp(),
                 'evidence_ref': 'fixture:actual-visible-account', 'browser_context': 'fixture:browser-1/tab-2'}
        value.update(overrides)
        return value

    def ready(self, ident='action-1', **overrides):
        action = self.prepare(ident, **overrides)
        self.ledger.request_authorization(ident)
        if action['binding']['mutation']:
            self.ledger.record_authorization(ident, self.attestation(action))
        self.ledger.observe_identity(ident, self.identity())
        return self.ledger.get(ident)

    def enabled(self):
        self.data['allow_write'] = True; self.save_config()

    def claimed(self, ident='action-1', **overrides):
        self.ready(ident, **overrides); self.enabled()
        return self.ledger.claim(ident, 'host-worker')

    def submitted(self, ident='action-1', **overrides):
        claim = self.claimed(ident, **overrides)
        self.ledger.begin_submit(ident, claim['claim_token'])
        return claim

    def result(self, action, **overrides):
        report = {'operation_id': action['operation_id'], 'binding_digest': action['binding_digest'],
                  'attempt_id': action['attempt_id'], 'account_id': action['binding']['expected_account'],
                  'browser_context': 'fixture:browser-1/tab-2', 'status': 'host_reported_success',
                  'observed_at': self.stamp(), 'evidence_ref': 'fixture:actual-visible-comment',
                  'evidence_kind': 'visible_result', 'details': 'The exact comment is visible on the intended note.'}
        report.update(overrides)
        return report

    def test_plan_and_status_work_with_writes_disabled(self):
        action = self.prepare()
        self.assertEqual(action['status'], 'prepared')
        self.assertFalse(self.ledger.status()['write_enabled'])
        self.assertFalse(self.ledger.status()['browser_executor'])
        self.assertEqual(self.ledger.status()['execution_authority'], 'host_policy_only')
        self.assertFalse(action['platform_success_independently_verified'])
        self.assertFalse(action['automatic_retry'])
        self.assertEqual(self.ledger.path.name, 'cloud-actions.sqlite3')
        self.assertEqual(os.stat(self.ledger.path).st_mode & 0o777, 0o600)
        self.assertEqual(os.stat(self.ledger.path.parent).st_mode & 0o777, 0o700)

    def test_operation_id_idempotence_and_every_binding_dimension(self):
        first = self.prepare(); self.time += 1
        repeat = self.prepare()
        self.assertTrue(repeat['duplicate'])
        self.assertEqual(first['binding'], repeat['binding'])
        for changes in ({'payload': {'content': 'Changed'}}, {'expected_account': 'other'},
                        {'target': {'feed_id': 'b' * 24}}, {'user_source': 'other-message'},
                        {'operation': 'reply_notification'}, {'due_at': self.stamp(300)},
                        {'timezone': 'Asia/Shanghai'}):
            with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
                self.prepare(**changes)
        self.assertEqual(len(self.ledger.events('action-1')), 1)

    def test_payload_is_a_deep_immutable_copy(self):
        payload = {'content': 'original', 'tags': ['one']}
        action = self.prepare(payload=payload)
        payload['tags'].append('changed')
        action['binding']['payload']['content'] = 'changed'
        self.assertEqual(self.ledger.get('action-1')['binding']['payload'], {'content': 'original', 'tags': ['one']})
        with self.assertRaises(sqlite3.IntegrityError):
            self.ledger.db.execute("UPDATE actions SET binding_json='{}'")

    def test_bare_approval_ref_boolean_and_wrong_bindings_do_not_advance(self):
        action = self.prepare(); self.ledger.request_authorization('action-1')
        for value in (True, 'approval-ref', {'approved': True}, {'approval_ref': 'anything'},
                      self.attestation(action, binding_digest='0' * 64),
                      self.attestation(action, expected_account='other'),
                      self.attestation(action, operation_id='other'),
                      self.attestation(action, user_source='other-message')):
            with self.assertRaises(ValueError): self.ledger.record_authorization('action-1', value)
        self.assertEqual(self.ledger.get('action-1')['status'], 'awaiting_authorization')

    def test_usable_bound_host_report_is_not_a_platform_or_policy_verification(self):
        action = self.ready()
        self.assertEqual(action['status'], 'ready')
        self.assertIn('host_reported_unverified', action['verification'])
        self.assertFalse(self.ledger.status()['host_verifier_configured'])
        with self.assertRaisesRegex(ValueError, 'WRITE_DISABLED'): self.ledger.claim('action-1', 'host')
        self.enabled(); claim = self.ledger.claim('action-1', 'host')
        self.assertEqual(claim['status'], 'dispatching')
        self.assertEqual(claim['execution_authority'], 'host_policy_only')
        self.assertNotIn('claim_token', self.ledger.get('action-1'))

    def test_optional_trusted_verifier_can_veto_and_receives_exact_binding(self):
        calls = []
        def verify(phase, binding, evidence):
            calls.append((phase, binding, evidence))
            return phase != 'begin_submit'
        self.ledger.host_verifier = verify
        claim = self.claimed()
        with self.assertRaisesRegex(ValueError, 'HOST_VERIFIER_DENIED'):
            self.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.ledger.get('action-1')['status'], 'dispatching')
        self.assertEqual(calls[-1][0], 'begin_submit')
        self.assertEqual(calls[-1][1], claim['binding'])
        self.ledger.host_verifier = lambda *args: 'yes'
        with self.assertRaisesRegex(ValueError, 'HOST_VERIFIER_DENIED'):
            self.ledger.observe_identity('action-1', self.identity())

    def test_verifier_mutation_cannot_change_action_or_evidence(self):
        def verify(phase, binding, evidence):
            binding['payload']['content'] = 'injected'; evidence.clear(); return True
        self.ledger.host_verifier = verify
        claim = self.claimed()
        self.assertEqual(claim['binding']['payload']['content'], 'Fixture only')
        self.assertIn('user_quote', claim['authorization'])

    def test_missing_stale_wrong_and_future_identity_guards(self):
        action = self.prepare(); self.ledger.request_authorization('action-1')
        self.ledger.record_authorization('action-1', self.attestation(action)); self.enabled()
        with self.assertRaisesRegex(ValueError, 'IDENTITY_REQUIRED'): self.ledger.claim('action-1', 'host')
        for observation, error in [(self.identity(observed_at=self.stamp(-121)), 'IDENTITY_STALE'),
                                   (self.identity(account_id='wrong'), 'IDENTITY_MISMATCH'),
                                   (self.identity(status='logged_out', account_id=''), 'IDENTITY_MISMATCH')]:
            self.ledger.observe_identity('action-1', observation)
            with self.assertRaisesRegex(ValueError, error): self.ledger.claim('action-1', 'host')
        with self.assertRaises(ValueError): self.ledger.observe_identity('action-1', self.identity(observed_at=self.stamp(6)))

    def test_new_mismatch_replaces_old_valid_identity(self):
        self.ready(); self.enabled()
        self.ledger.observe_identity('action-1', self.identity(account_id='wrong'))
        with self.assertRaisesRegex(ValueError, 'IDENTITY_MISMATCH'): self.ledger.claim('action-1', 'host')

    def test_identity_and_configuration_rechecked_before_any_upload_or_fill(self):
        claim = self.claimed()
        self.data['allow_write'] = False; self.save_config()
        with self.assertRaisesRegex(ValueError, 'WRITE_DISABLED'): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.enabled(); self.time += 121
        with self.assertRaisesRegex(ValueError, 'LEASE_EXPIRED'): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.ledger.get('action-1')['status'], 'dispatching')

    def test_identity_can_expire_during_longer_lease(self):
        self.ready(); self.enabled(); claim = self.ledger.claim('action-1', 'host', lease_seconds=300)
        self.time += 121
        with self.assertRaisesRegex(ValueError, 'IDENTITY_STALE'): self.ledger.begin_submit('action-1', claim['claim_token'])

    def test_changed_browser_context_cannot_use_old_claim(self):
        claim = self.claimed()
        self.ledger.observe_identity('action-1', self.identity(browser_context='different-tab'))
        with self.assertRaisesRegex(ValueError, 'BROWSER_CONTEXT_CHANGED'): self.ledger.begin_submit('action-1', claim['claim_token'])

    def test_expired_host_authorization_is_rejected_at_claim_and_submit(self):
        self.ready(); self.enabled(); self.time += 601
        self.ledger.observe_identity('action-1', self.identity())
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_EXPIRED'): self.ledger.claim('action-1', 'host')
        action = self.ledger.get('action-1')
        self.ledger.record_authorization('action-1', self.attestation(action, expires_at=self.stamp(10)))
        claim = self.ledger.claim('action-1', 'host'); self.time += 11
        with self.assertRaisesRegex(ValueError, 'AUTHORIZATION_EXPIRED'): self.ledger.begin_submit('action-1', claim['claim_token'])

    def test_authorization_timestamp_validation(self):
        action = self.prepare(); self.ledger.request_authorization('action-1')
        for changes in ({'confirmed_at': self.stamp(6)}, {'expires_at': self.stamp()},
                        {'confirmed_at': '2026-10-19T10:00:00'}, {'expires_at': self.stamp(367 * 86400)}):
            with self.assertRaises(ValueError): self.ledger.record_authorization('action-1', self.attestation(action, **changes))

    def test_due_timestamp_and_original_timezone_are_bound(self):
        self.ready(due_at=self.stamp(60), timezone='Asia/Shanghai'); self.enabled()
        self.assertEqual(self.ledger.get('action-1')['binding']['timezone'], 'Asia/Shanghai')
        self.assertEqual(self.ledger.list_actions(due_only=True), [])
        with self.assertRaisesRegex(ValueError, 'NOT_DUE'): self.ledger.claim('action-1', 'host')
        self.time += 60
        self.assertEqual(self.ledger.status()['due_ready'], 1)
        self.assertEqual(self.ledger.claim('action-1', 'host')['status'], 'dispatching')

    def test_atomic_claim_across_connections_and_tokens_are_attempt_bound(self):
        self.ready(); self.enabled()
        other = cloud.CloudActions(self.config, clock=lambda: self.time)
        try:
            first = self.ledger.claim('action-1', 'host-1')
            with self.assertRaisesRegex(ValueError, 'ERR_STATE'): other.claim('action-1', 'host-2')
            with self.assertRaisesRegex(ValueError, 'ERR_CLAIM'): other.begin_submit('action-1', 'made-up-token')
            self.ledger.release_claim('action-1', first['claim_token'], 'No browser side effect attempted.')
            second = other.claim('action-1', 'host-2')
            self.assertNotEqual(first['claim_token'], second['claim_token'])
            self.assertEqual(second['attempt'], 2)
            with self.assertRaisesRegex(ValueError, 'ERR_CLAIM'): self.ledger.begin_submit('action-1', first['claim_token'])
        finally: other.close()

    def test_only_predispatch_lease_recovers(self):
        claim = self.claimed(); self.time += 121
        self.assertEqual(self.ledger.recover_expired()['recovered_before_submit'], ['action-1'])
        self.assertEqual(self.ledger.get('action-1')['status'], 'ready')
        with self.assertRaises(ValueError): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.ledger.observe_identity('action-1', self.identity())
        new = self.ledger.claim('action-1', 'host')
        self.ledger.begin_submit('action-1', new['claim_token']); self.time += 121
        self.assertEqual(self.ledger.recover_expired()['recovered_before_submit'], [])
        self.assertEqual(self.ledger.get('action-1')['status'], 'submitted_unknown')

    def test_timeout_after_begin_submit_is_never_replayed_and_later_evidence_reconciles(self):
        # Real host tools can time out after navigation/click already completed.
        claim = self.submitted()
        report = self.result(claim, status='submitted_unknown', evidence_kind='uncertain',
                             evidence_ref='fixture:host-timeout', details='Host timed out; action may already have completed.')
        self.ledger.report_result('action-1', claim['claim_token'], report)
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        self.time += 1000; self.ledger.recover_expired()
        for operation in (lambda: self.ledger.claim('action-1', 'host'),
                          lambda: self.ledger.begin_submit('action-1', claim['claim_token']),
                          lambda: self.ledger.release_claim('action-1', claim['claim_token'], 'Timeout')):
            with self.assertRaises(ValueError): operation()
        self.data['allow_write'] = False; self.save_config()
        outcome = self.ledger.reconcile('action-1', self.result(claim, details='Later readback shows the exact comment on the intended note.'))
        self.assertEqual(outcome['status'], 'host_reported_success')
        self.assertEqual(outcome['attempt'], 1)
        self.assertFalse(outcome['platform_success_independently_verified'])

    def test_begin_submit_is_not_an_idempotent_execution_ticket(self):
        claim = self.submitted()
        with self.assertRaisesRegex(ValueError, 'do not replay'): self.ledger.begin_submit('action-1', claim['claim_token'])

    def test_success_must_follow_submit_boundary_and_match_all_evidence_bindings(self):
        claim = self.claimed()
        with self.assertRaisesRegex(ValueError, 'EVIDENCE_REQUIRED'):
            self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))
        self.ledger.begin_submit('action-1', claim['claim_token'])
        for changes in ({'operation_id': 'other'}, {'binding_digest': '0' * 64}, {'attempt_id': 'other'},
                        {'account_id': 'wrong'}, {'browser_context': 'wrong'}, {'observed_at': self.stamp(-1)},
                        {'evidence_ref': ''}, {'evidence_kind': 'Sent=true'}):
            with self.assertRaises(ValueError): self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, **changes))
        self.assertEqual(self.ledger.get('action-1')['status'], 'submitted_unknown')

    def test_result_idempotence_and_terminal_evidence_cannot_be_replaced(self):
        claim = self.submitted(); report = self.result(claim)
        first = self.ledger.report_result('action-1', claim['claim_token'], report)
        self.assertEqual(first, self.ledger.report_result('action-1', claim['claim_token'], report))
        self.assertEqual(first, self.ledger.reconcile('action-1', report))
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'):
            self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, details='Different evidence'))
        with self.assertRaises(sqlite3.IntegrityError): self.ledger.db.execute('DELETE FROM action_events')

    def test_not_applied_never_auto_requeues(self):
        claim = self.submitted()
        outcome = self.ledger.reconcile('action-1', self.result(claim, status='not_applied', evidence_kind='platform_rejection', details='Visible platform rejection confirms no comment was accepted.'))
        self.assertEqual(outcome['status'], 'not_applied')
        with self.assertRaises(ValueError): self.ledger.claim('action-1', 'host')
        self.assertEqual(self.prepare()['status'], 'not_applied')

    def test_timeout_is_not_evidence_of_not_applied(self):
        claim = self.submitted()
        for evidence in ('uncertain', 'host_no_submit'):
            with self.assertRaisesRegex(ValueError, 'EVIDENCE_REQUIRED'):
                self.ledger.reconcile('action-1', self.result(claim, status='not_applied', evidence_kind=evidence))

    def test_predispatch_host_no_submit_can_finish_without_claiming_platform_success(self):
        claim = self.claimed()
        out = self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, status='not_applied', evidence_kind='host_no_submit', details='Host stopped before the first possible side effect.'))
        self.assertEqual(out['status'], 'not_applied')

    def test_public_reads_can_run_locally_planned_with_writes_disabled(self):
        action = self.prepare(operation='search_feeds', payload={'keyword': 'cats'}, target={'search': 'cats'})
        self.assertFalse(action['binding']['mutation'])
        self.assertEqual(self.ledger.request_authorization('action-1')['status'], 'ready')
        claim = self.ledger.claim('action-1', 'host', browser_context='fixture:browser-1/tab-2')
        self.assertIsNone(claim['authorization'])
        self.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))['status'], 'host_reported_success')
        self.assertFalse(self.ledger.status()['write_enabled'])

    def test_private_reads_require_fresh_identity_but_no_write_permission(self):
        self.prepare(operation='get_my_profile', payload={}); self.ledger.request_authorization('action-1')
        with self.assertRaisesRegex(ValueError, 'IDENTITY_REQUIRED'): self.ledger.claim('action-1', 'host')
        self.ledger.observe_identity('action-1', self.identity())
        self.assertEqual(self.ledger.claim('action-1', 'host')['status'], 'dispatching')

    def test_notification_list_is_a_mutation_even_if_upstream_calls_it_readonly(self):
        action = self.ready(operation='list_notifications', payload={})
        self.assertTrue(action['binding']['mutation'])
        with self.assertRaisesRegex(ValueError, 'WRITE_DISABLED'): self.ledger.claim('action-1', 'host')

    def test_authentication_and_arbitrary_code_are_not_dispatchable(self):
        for operation in ('login_with_sms', 'get_login_qrcode', 'delete_cookies', 'evaluate', 'exec', 'goto', 'follow_user'):
            with self.assertRaises(ValueError): self.prepare(operation=operation)
        for payload in ({'approved': True}, {'script': 'evil'}, {'shell': 'sh'},
                        {'password': 'secret'}, {'code': '123456'}, {'a': {'cookies': 'secret'}},
                        {'content': 'access_token=secret'}, {'value': float('nan')}, {'value': object()}):
            with self.assertRaises(ValueError): self.prepare(payload=payload)
        self.assertEqual(self.ledger.list_actions(), [])

    def test_payload_limits_recursion_and_strict_schema_injection(self):
        for payload in ({'content': 'x' * 70000}, {'list': [1] * 1001}, {'value': 2 ** 54}):
            with self.assertRaises(ValueError): self.prepare(payload=payload)
        deep = {}; current = deep
        for _ in range(15): current['child'] = {}; current = current['child']
        with self.assertRaises(ValueError): self.prepare(payload=deep)
        def validate(operation, payload):
            if set(payload) != {'feed_id', 'content'}: raise ValueError('strict schema')
        self.ledger.payload_validator = validate
        with self.assertRaisesRegex(ValueError, 'strict schema'): self.prepare(payload={'unexpected': 1})
        self.prepare()

    def test_media_manifest_binds_file_bytes_and_is_rechecked_before_dispatch(self):
        image = self.base / 'image.png'; image.write_bytes(b'fixture-image')
        manifest = [{'path': str(image), 'size': image.stat().st_size, 'sha256': hashlib.sha256(image.read_bytes()).hexdigest()}]
        self.ready(operation='publish_content', payload={'title': 'Fixture', 'content': 'Fixture', 'images': [str(image)]}, media_manifest=manifest)
        self.enabled(); claim = self.ledger.claim('action-1', 'host')
        image.write_bytes(b'changed-image')  # Same size; hash must still catch the change.
        with self.assertRaisesRegex(ValueError, 'MEDIA_CHANGED'): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.ledger.get('action-1')['status'], 'dispatching')
        image.write_bytes(b'fixture-image')
        self.assertEqual(self.ledger.begin_submit('action-1', claim['claim_token'])['status'], 'submitted_unknown')

    def test_publish_without_manifest_and_remote_media_are_plannable_but_not_claimable(self):
        self.ready(operation='publish_content', payload={'title': 'Fixture', 'content': 'Fixture', 'images': ['https://example.test/image.png']})
        self.enabled()
        with self.assertRaisesRegex(ValueError, 'MEDIA_REQUIRED'): self.ledger.claim('action-1', 'host')
        self.assertEqual(self.ledger.get('action-1')['status'], 'ready')

    def test_manifest_rejects_missing_file_and_symlink(self):
        missing = self.base / 'missing.png'
        manifest = [{'path': str(missing), 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}]
        self.ready(operation='publish_content', payload={'title': 'Fixture', 'images': [str(missing)]}, media_manifest=manifest)
        self.enabled()
        with self.assertRaisesRegex(ValueError, 'MEDIA_UNAVAILABLE'): self.ledger.claim('action-1', 'host')
        real = self.base / 'real.png'; real.write_bytes(b'x'); missing.symlink_to(real)
        with self.assertRaisesRegex(ValueError, 'MEDIA_CHANGED'): self.ledger.claim('action-1', 'host')

    def test_media_manifest_cannot_be_changed_after_approval(self):
        asset = self.base / 'asset.png'; asset.write_bytes(b'x')
        manifest = [{'path': str(asset), 'size': 1, 'sha256': hashlib.sha256(b'x').hexdigest()}]
        self.prepare(operation='publish_content', payload={'images': [str(asset)]}, media_manifest=manifest)
        manifest[0]['sha256'] = '0' * 64
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'):
            self.prepare(operation='publish_content', payload={'images': [str(asset)]}, media_manifest=manifest)

    def test_cancel_only_before_claim_and_no_automatic_retry(self):
        self.prepare(); self.assertEqual(self.ledger.cancel('action-1', 'User cancelled the plan.')['status'], 'cancelled')
        with self.assertRaises(ValueError): self.ledger.request_authorization('action-1')
        self.claimed('action-2')
        with self.assertRaises(ValueError): self.ledger.cancel('action-2', 'Too late')

    def test_account_isolation_and_live_configuration_account_change(self):
        self.prepare(); self.data['account_id'] = 'other'; self.save_config()
        other = cloud.CloudActions(self.config, clock=lambda: self.time)
        try:
            with self.assertRaises(ValueError): other.get('action-1')
            self.assertEqual(other.list_actions(), [])
            with self.assertRaisesRegex(ValueError, 'ACCOUNT_CHANGED'): self.ledger.status()
        finally: other.close()

    def test_private_files_and_symlink_guards(self):
        os.chmod(self.ledger.path, 0o644)
        with self.assertRaisesRegex(ValueError, 'private'): cloud.CloudActions(self.config)
        os.chmod(self.ledger.path, 0o600)
        journal = Path(str(self.ledger.path) + '-journal'); target = self.base / 'target'; target.write_text('untouched')
        journal.symlink_to(target)
        with self.assertRaisesRegex(ValueError, 'linked'): cloud.CloudActions(self.config)
        journal.unlink()
        os.chmod(self.ledger.path.parent, 0o755)
        with self.assertRaisesRegex(ValueError, 'private'): cloud.CloudActions(self.config)
        os.chmod(self.ledger.path.parent, 0o700)

    def test_config_requires_cloud_mode_and_boolean_switches(self):
        for changes in ({'browser_mode': 'local_cdp'}, {'allow_write': 'true'}, {'allow_reconcile': 0}, {'account_id': '../wrong'}):
            original = dict(self.data); self.data.update(changes); self.save_config()
            with self.assertRaises(ValueError): cloud.CloudActions(self.config)
            self.data = original; self.save_config()

    def test_no_network_subprocess_or_browser_code_in_lifecycle(self):
        with mock.patch('socket.socket', side_effect=AssertionError('no network')), mock.patch('subprocess.Popen', side_effect=AssertionError('no subprocess')):
            claim = self.submitted()
            self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))

    def test_read_only_cli_has_no_approval_or_execute_switch(self):
        self.prepare()
        result = subprocess.run([sys.executable, str(ROOT / 'runtime/cloud_actions.py'), '--config', str(self.config), 'get', 'action-1'], capture_output=True, text=True, check=True)
        self.assertEqual(json.loads(result.stdout)['status'], 'prepared')
        result = subprocess.run([sys.executable, str(ROOT / 'runtime/cloud_actions.py'), '--config', str(self.config), 'approve', '--approved'], capture_output=True, text=True)
        self.assertNotEqual(result.returncode, 0)

    def test_quota_failure_preserves_pre_submit_state(self):
        claim = self.claimed()
        with mock.patch.object(Path, 'stat', return_value=mock.Mock(st_size=cloud.MAX_LEDGER)):
            with self.assertRaisesRegex(ValueError, 'ERR_QUOTA'): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.ledger.get('action-1')['status'], 'dispatching')

    def test_original_approval_ref_is_inert_provenance_not_authority(self):
        self.prepare(payload={'content': 'Fixture', 'approval_ref': 'original-provenance-ref'})
        self.ledger.request_authorization('action-1'); self.enabled()
        self.ledger.observe_identity('action-1', self.identity())
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'): self.ledger.claim('action-1', 'host')
        self.assertEqual(self.ledger.get('action-1')['status'], 'awaiting_authorization')
        with self.assertRaises(ValueError): self.prepare('other', payload={'approval_ref': True})

    def test_changed_configured_platform_account_invalidates_pending_dispatch(self):
        self.data['expected_user_id'] = 'public-user-1'; self.save_config()
        claim = self.claimed()
        self.data['expected_user_id'] = 'public-user-2'; self.save_config()
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_CHANGED'): self.ledger.begin_submit('action-1', claim['claim_token'])
        self.ledger.release_claim('action-1', claim['claim_token'], 'Configured account changed before submission.')
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_CHANGED'): self.ledger.claim('action-1', 'host')

    def test_long_article_has_explicit_no_upload_route(self):
        self.ready(operation='publish_content', payload={'title': 'Fixture', 'content': 'Text', 'images': None, 'long_article_template': 'Observed template'})
        self.enabled(); claim = self.ledger.claim('action-1', 'host')
        self.assertEqual(claim['binding']['media_manifest'], [])
        self.assertEqual(self.ledger.begin_submit('action-1', claim['claim_token'])['status'], 'submitted_unknown')

    def test_positive_verifier_accepts_full_claim_submission_and_result(self):
        phases = []
        self.ledger.host_verifier = lambda phase, binding, evidence: phases.append(phase) is None
        claim = self.submitted()
        self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))
        self.assertIn('claim', phases)
        self.assertIn('begin_submit', phases)
        self.assertIn('result_report', phases)

    def test_queue_pause_is_durable_and_blocks_claims_without_starting_worker(self):
        self.ready(); self.enabled()
        self.assertTrue(self.ledger.queue_enabled())
        self.assertFalse(self.ledger.set_queue_enabled(False)['autonomous_browser'])
        with self.assertRaisesRegex(ValueError, 'QUEUE_PAUSED'): self.ledger.claim('action-1', 'host')
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        self.assertFalse(self.ledger.status()['queue_enabled'])
        self.ledger.set_queue_enabled(True)
        self.assertEqual(self.ledger.claim('action-1', 'host')['status'], 'dispatching')
        with self.assertRaises(ValueError): self.ledger.set_queue_enabled(1)

    def test_queue_pause_does_not_interrupt_already_claimed_attempt(self):
        claim = self.claimed(); self.ledger.set_queue_enabled(False)
        self.assertEqual(self.ledger.begin_submit('action-1', claim['claim_token'])['status'], 'submitted_unknown')

    def test_dependency_requires_reported_success_and_does_not_replay_unknown(self):
        first = self.submitted('first')
        self.ready('second', dependency='first')
        with self.assertRaisesRegex(ValueError, 'DEPENDENCY_NOT_SUCCEEDED'): self.ledger.claim('second', 'host')
        self.ledger.reconcile('first', self.result(first))
        self.assertEqual(self.ledger.claim('second', 'host')['status'], 'dispatching')
        with self.assertRaisesRegex(ValueError, 'IDEMPOTENCY_CONFLICT'): self.prepare('second')

    def test_dependency_is_account_scoped_existing_and_not_self_referential(self):
        with self.assertRaises(ValueError): self.prepare(dependency='missing')
        with self.assertRaises(ValueError): self.prepare(dependency='action-1')
        self.prepare('first'); self.ledger.cancel('first', 'Cancelled')
        self.ready('second', dependency='first'); self.enabled()
        with self.assertRaisesRegex(ValueError, 'DEPENDENCY_NOT_SUCCEEDED'): self.ledger.claim('second', 'host')

    def test_reconciliation_can_use_rebound_browser_only_with_fresh_matching_identity(self):
        claim = self.submitted()
        report = self.result(claim, browser_context='fixture:replacement-browser/tab-9')
        with self.assertRaisesRegex(ValueError, 'BINDING_MISMATCH'): self.ledger.reconcile('action-1', report)
        self.ledger.observe_identity('action-1', self.identity(browser_context=report['browser_context']))
        self.data['allow_write'] = False; self.save_config()
        outcome = self.ledger.reconcile('action-1', report)
        self.assertEqual(outcome['status'], 'host_reported_success')
        self.assertEqual(outcome['claim_browser_context'], 'fixture:browser-1/tab-2')
        self.assertEqual(outcome['result']['browser_context'], 'fixture:replacement-browser/tab-9')
        self.assertEqual(outcome['attempt_id'], claim['attempt_id'])

    def test_replacement_context_reconciliation_rejects_wrong_or_stale_identity(self):
        claim = self.submitted(); report = self.result(claim, browser_context='replacement-tab')
        for observation, expected in ((self.identity(browser_context='replacement-tab', account_id='wrong'), 'IDENTITY_MISMATCH'),
                                      (self.identity(browser_context='replacement-tab', observed_at=self.stamp(-121)), 'IDENTITY_STALE')):
            self.ledger.observe_identity('action-1', observation)
            with self.assertRaisesRegex(ValueError, expected): self.ledger.reconcile('action-1', report)
        self.ledger.observe_identity('action-1', self.identity(browser_context='replacement-tab'))
        with self.assertRaisesRegex(ValueError, 'BINDING_MISMATCH'):
            self.ledger.reconcile('action-1', self.result(claim, browser_context='replacement-tab', attempt_id='another-attempt'))

    def test_normal_result_cannot_silently_switch_to_replacement_context(self):
        claim = self.submitted()
        self.ledger.observe_identity('action-1', self.identity(browser_context='replacement-tab'))
        with self.assertRaisesRegex(ValueError, 'BINDING_MISMATCH'):
            self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, browser_context='replacement-tab'))

    def test_unknown_mutation_blocks_other_account_writes_but_not_evidence_reads(self):
        first = self.submitted('first')
        self.ready('second')
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_UNCERTAIN'): self.ledger.claim('second', 'host')
        self.ledger.report_result('first', first['claim_token'], self.result(first, status='submitted_unknown', evidence_kind='uncertain'))
        self.ready('read', operation='get_my_profile', payload={})
        read = self.ledger.claim('read', 'host')
        self.assertEqual(read['status'], 'dispatching')
        self.ledger.release_claim('read', read['claim_token'], 'Read-side inspection ended before dispatch.')
        self.ledger.reconcile('first', self.result(first))
        self.assertEqual(self.ledger.claim('second', 'host')['status'], 'dispatching')

    def test_concurrent_mutation_claims_are_account_serialized(self):
        first = self.claimed('first'); self.ready('second')
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_BUSY'): self.ledger.claim('second', 'host')
        self.ledger.release_claim('first', first['claim_token'], 'No consequential step occurred.')
        self.assertEqual(self.ledger.claim('second', 'host')['status'], 'dispatching')

    def test_long_upload_after_submit_boundary_cannot_be_recovered_as_ready(self):
        claim = self.submitted()
        self.time += 3600  # A slow upload or host interruption outlives the lease.
        self.assertEqual(self.ledger.recover_expired()['recovered_before_submit'], [])
        with self.assertRaises(ValueError): self.ledger.claim('action-1', 'host')
        self.assertEqual(self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))['status'], 'host_reported_success')

    def test_live_private_permissions_and_replaced_inode_fail_closed(self):
        self.ready(); self.enabled()
        os.chmod(self.ledger.path, 0o644)
        with self.assertRaisesRegex(ValueError, 'private'): self.ledger.claim('action-1', 'host')
        os.chmod(self.ledger.path, 0o600)
        original = self.ledger.path.with_suffix('.original')
        self.ledger.path.rename(original)
        self.ledger.path.write_bytes(original.read_bytes()); os.chmod(self.ledger.path, 0o600)
        with self.assertRaisesRegex(ValueError, 'LEDGER_REPLACED'): self.ledger.claim('action-1', 'host')
        self.ledger.path.unlink(); original.rename(self.ledger.path)

    def test_live_state_directory_config_change_blocks_dispatch(self):
        claim = self.claimed(); self.data['state_dir'] = 'different-state'; self.save_config()
        with self.assertRaisesRegex(ValueError, 'STATE_DIRECTORY_CHANGED'): self.ledger.begin_submit('action-1', claim['claim_token'])

    def test_aliases_share_platform_account_mutation_guard_but_allow_reads_and_distinct_accounts(self):
        first = self.claimed('same-id')
        config = self.base / 'alias.json'
        config.write_text(json.dumps(dict(self.data, account_id='second-alias')))
        other = cloud.CloudActions(config, clock=lambda: self.time)
        try:
            def ready_other(ident, expected='public-user-1', operation='post_comment_to_feed'):
                action = other.prepare(operation, {'content': 'Fixture'} if operation == 'post_comment_to_feed' else {},
                                       expected_account=expected, target={'feed_id': 'a' * 24},
                                       user_source='user-message:alias', operation_id=ident)
                other.request_authorization(ident)
                if action['binding']['mutation']: other.record_authorization(ident, self.attestation(action))
                other.observe_identity(ident, self.identity(account_id=expected, browser_context='fixture:alias-tab'))
            # Identical operation IDs in different aliases must still be compared.
            ready_other('same-id')
            with self.assertRaisesRegex(ValueError, 'ACCOUNT_BUSY'): other.claim('same-id', 'host-2')
            self.ledger.begin_submit('same-id', first['claim_token'])
            with self.assertRaisesRegex(ValueError, 'ACCOUNT_UNCERTAIN'): other.claim('same-id', 'host-2')
            self.ledger.report_result('same-id', first['claim_token'], self.result(first, status='submitted_unknown', evidence_kind='uncertain'))
            ready_other('evidence-read', operation='get_my_profile')
            read = other.claim('evidence-read', 'host-2')
            self.assertEqual(read['status'], 'dispatching')
            other.release_claim('evidence-read', read['claim_token'], 'No browser action attempted.')
            ready_other('different-account', expected='public-user-2')
            self.assertEqual(other.claim('different-account', 'host-2')['status'], 'dispatching')
            self.ledger.reconcile('same-id', self.result(first))
            self.assertEqual(other.claim('same-id', 'host-2')['status'], 'dispatching')
        finally: other.close()

    def test_semantic_dedup_ignores_ids_due_user_run_approval_and_tokens(self):
        payload = {'feed_id': 'a' * 24, 'content': 'Exact same comment', 'xsec_token': 'original-token',
                   'run_id': 'first-run', 'approval_ref': 'first-source'}
        first = self.prepare(payload=payload)
        altered = {**payload, 'operation_id': 'fresh-id', 'xsec_token': 'new-token',
                   'run_id': 'new-run', 'approval_ref': 'new-source'}
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
            self.prepare('fresh-id', payload=altered, user_source='another-message', due_at=self.stamp(7200),
                         target={'url': 'https://www.xiaohongshu.com/explore/' + 'a' * 24 + '?xsec_token=new'})
        self.assertEqual(len(self.ledger.list_actions()), 1)
        self.assertIsNotNone(first['semantic_digest'])

    def test_semantic_success_unknown_and_pending_never_accept_fresh_id(self):
        claim = self.submitted()
        for terminal in (False, True):
            if terminal: self.ledger.report_result('action-1', claim['claim_token'], self.result(claim))
            with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
                self.prepare('duplicate', payload=claim['binding']['payload'])
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_DUPLICATE'):
            self.prepare('after-restart', payload=claim['binding']['payload'])

    def test_semantic_retry_requires_latest_definite_terminal_predecessor_and_new_authorization(self):
        first = self.prepare(); self.ledger.cancel('action-1', 'Explicit user cancellation.')
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_RETRY_REQUIRED'):
            self.prepare('retry', payload=first['binding']['payload'])
        retry = self.prepare('retry', payload=first['binding']['payload'], retry_of='action-1')
        self.assertEqual(retry['status'], 'prepared')
        self.assertIsNone(retry['authorization'])
        self.assertEqual(retry['semantic_digest'], first['semantic_digest'])
        self.ledger.cancel('retry', 'Explicit user cancellation of second attempt.')
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_RETRY_REQUIRED'):
            self.prepare('third', payload=first['binding']['payload'], retry_of='action-1')
        with self.assertRaisesRegex(ValueError, 'ERR_RETRY_LIMIT'):
            self.prepare('third', payload=first['binding']['payload'], retry_of='retry')
        self.assertTrue(self.prepare('retry', payload=first['binding']['payload'], retry_of='action-1')['duplicate'])
        self.ledger.close(); self.ledger = cloud.CloudActions(self.config, clock=lambda: self.time)
        with self.assertRaisesRegex(ValueError, 'ERR_RETRY_LIMIT'):
            self.prepare('after-restart', payload=first['binding']['payload'], retry_of='retry')
        with self.assertRaisesRegex(ValueError, 'RETRY_BINDING'):
            self.prepare('unrelated', payload={'content': 'different action'}, retry_of='retry')

    def test_legacy_prepared_retry_chain_cannot_dispatch_after_upgrade(self):
        self.enabled()
        first = self.prepare(); self.ledger.cancel('action-1', 'SYNTHETIC')
        retry = self.prepare('retry', payload=first['binding']['payload'], retry_of='action-1')
        self.ledger.cancel('retry', 'SYNTHETIC')
        # Synthesize a retained pre-upgrade second-generation retry, not real evidence.
        binding = dict(retry['binding'], operation_id='legacy-third', retry_of='retry')
        raw = cloud._json(binding); digest = cloud._digest(binding)
        self.ledger.db.execute(
            "INSERT INTO actions(account,operation_id,binding_json,binding_digest,status,due_at,created_at,updated_at,semantic_digest) SELECT account,?,?,?,'prepared',due_at,created_at+1,updated_at+1,semantic_digest FROM actions WHERE account=? AND operation_id='retry'",
            ('legacy-third', raw, digest, self.ledger.account))
        self.ledger.request_authorization('legacy-third')
        action = self.ledger.get('legacy-third')
        self.ledger.record_authorization('legacy-third', self.attestation(action))
        self.ledger.observe_identity('legacy-third', self.identity())
        with self.assertRaisesRegex(ValueError, 'ERR_RETRY_LIMIT'):
            self.ledger.claim('legacy-third', 'fixture')
        self.assertEqual(self.ledger.get('legacy-third')['binding'], binding)

    def test_not_applied_can_create_only_explicit_new_attempt(self):
        claim = self.submitted()
        self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, status='not_applied', evidence_kind='platform_rejection'))
        with self.assertRaisesRegex(ValueError, 'EXPLICIT_RETRY_REQUIRED'):
            self.prepare('retry', payload=claim['binding']['payload'])
        self.assertEqual(self.prepare('retry', payload=claim['binding']['payload'], retry_of='action-1')['status'], 'prepared')

    def test_semantic_media_hash_and_normalized_schedule_ignore_copy_path(self):
        first = self.base / 'first.png'; second = self.base / 'copy.png'
        first.write_bytes(b'same bytes'); second.write_bytes(b'same bytes')
        def manifest(path): return [{'path': str(path), 'size': 10, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}]
        scheduled = self.stamp(7200)
        payload = {'title': 'Same title', 'content': 'Same content', 'images': [str(first)], 'schedule_at': scheduled}
        self.prepare(operation='publish_content', payload=payload, media_manifest=manifest(first))
        converted = cloud.dt.datetime.fromisoformat(scheduled).astimezone(cloud.dt.timezone(cloud.dt.timedelta(hours=8))).isoformat()
        with self.assertRaisesRegex(ValueError, 'CONTENT_DUPLICATE'):
            self.prepare('copy', operation='publish_content', payload={**payload, 'images': [str(second)], 'schedule_at': converted}, media_manifest=manifest(second))
        different = self.prepare('next-slot', operation='publish_content', payload={**payload, 'schedule_at': self.stamp(10800)}, media_manifest=manifest(first))
        self.assertEqual(different['status'], 'prepared')

    def test_semantic_dedup_crosses_aliases_but_not_actual_accounts(self):
        first = self.prepare()
        config = self.base / 'alias.json'; config.write_text(json.dumps(dict(self.data, account_id='alias')))
        other = cloud.CloudActions(config, clock=lambda: self.time)
        try:
            common = dict(operation='post_comment_to_feed', payload=first['binding']['payload'],
                          target={'feed_id': 'a' * 24}, user_source='different-source', operation_id='another-id')
            with self.assertRaisesRegex(ValueError, 'CONTENT_DUPLICATE'):
                other.prepare(**common, expected_account='public-user-1')
            self.assertEqual(other.prepare(**common, expected_account='public-user-2')['status'], 'prepared')
        finally: other.close()

    def test_all_active_reads_and_writes_are_serialized(self):
        self.ready('read-one', operation='list_feeds', payload={})
        first = self.ledger.claim('read-one', 'host', browser_context='fixture:browser-1/tab-2')
        self.ready('read-two', operation='get_my_profile', payload={})
        self.ready('write'); self.enabled()
        for ident in ('read-two', 'write'):
            with self.assertRaisesRegex(ValueError, 'ACCOUNT_BUSY'): self.ledger.claim(ident, 'host')
        self.ledger.begin_submit('read-one', first['claim_token'])
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_BUSY'): self.ledger.claim('read-two', 'host')
        self.ledger.report_result('read-one', first['claim_token'], self.result(first))
        self.assertEqual(self.ledger.claim('read-two', 'host')['status'], 'dispatching')

    def test_live_unreported_submission_blocks_reads_but_timeout_report_releases_only_reads(self):
        first = self.submitted(); self.ready('read', operation='get_my_profile', payload={})
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_BUSY'): self.ledger.claim('read', 'host')
        self.ledger.report_result('action-1', first['claim_token'], self.result(first, status='submitted_unknown', evidence_kind='uncertain'))
        self.assertEqual(self.ledger.claim('read', 'host')['status'], 'dispatching')
        self.assertEqual(self.ledger.get('action-1')['status'], 'submitted_unknown')

    def test_elapsed_submission_lease_allows_read_reconciliation_without_write_replay(self):
        self.submitted(); self.ready('read', operation='get_my_profile', payload={}); self.time += 121
        self.ledger.observe_identity('read', self.identity())
        self.assertEqual(self.ledger.claim('read', 'host')['status'], 'dispatching')
        self.assertEqual(self.ledger.get('action-1')['status'], 'submitted_unknown')

    def test_read_provenance_preserves_original_timestamp_and_raw_evidence(self):
        claim = self.submitted(operation='list_feeds', payload={})
        self.time += 2
        report = self.result(claim, read_at=self.stamp(-1), cached=False)
        value = self.ledger.report_result('action-1', claim['claim_token'], report)
        self.assertEqual(value['result'], report)
        self.assertEqual(value['read_provenance']['read_at'], report['read_at'])
        self.assertFalse(value['read_provenance']['cached'])
        self.time += 1000
        self.assertEqual(self.ledger.get('action-1')['read_provenance'], value['read_provenance'])

    def test_legacy_read_report_derives_provenance_without_modifying_evidence(self):
        claim = self.submitted(operation='list_feeds', payload={}); report = self.result(claim)
        value = self.ledger.report_result('action-1', claim['claim_token'], report)
        self.assertNotIn('read_at', value['result'])
        self.assertEqual(value['read_provenance']['read_at'], report['observed_at'])
        self.assertEqual(value['read_provenance']['timestamp_source'], 'observed_at')

    def test_dispatched_read_rejects_cache_hit_future_or_predispatch_time(self):
        claim = self.submitted(operation='list_feeds', payload={})
        for overrides in ({'cached': True}, {'cached': 0}, {'read_at': self.stamp(1)},
                          {'observed_at': self.stamp(1)}, {'read_at': self.stamp(-1)}):
            with self.assertRaisesRegex(ValueError, 'READ_PROVENANCE'):
                self.ledger.report_result('action-1', claim['claim_token'], self.result(claim, **overrides))
        self.assertIsNone(self.ledger.get('action-1')['read_provenance'])

    def test_semantic_destination_is_stable_across_payload_and_observed_target(self):
        self.prepare(payload={'content': 'Same comment'}, target={'feed_id': 'a' * 24})
        with self.assertRaisesRegex(ValueError, 'CONTENT_DUPLICATE'):
            self.prepare('different-envelope', payload={'feed_id': 'A' * 24, 'content': 'Same comment'},
                         target={'url': 'https://www.xiaohongshu.com/explore/' + 'a' * 24})

    def test_semantic_no_upload_article_normalizes_null_empty_and_missing_images(self):
        payload = {'title': 'Same title', 'content': 'Same text', 'long_article_template': 'Same template'}
        self.prepare(operation='publish_content', payload={**payload, 'images': None})
        for images in ([], 'missing'):
            candidate = payload if images == 'missing' else {**payload, 'images': images}
            with self.assertRaisesRegex(ValueError, 'CONTENT_DUPLICATE'):
                self.prepare('another-envelope', operation='publish_content', payload=candidate)

    def test_invalid_ids_report_shapes_and_list_bounds(self):
        for value in ('../bad', '', True, 'x' * 129):
            with self.assertRaises(ValueError): self.ledger.get(value)
        self.submitted()
        for report in (True, [], {'status': 'not_applied'}):
            with self.assertRaises(ValueError): self.ledger.reconcile('action-1', report)
        for kwargs in ({'status': 'bogus'}, {'limit': True}, {'limit': 201}, {'due_only': 'true'}):
            with self.assertRaises(ValueError): self.ledger.list_actions(**kwargs)


if __name__ == '__main__': unittest.main()
