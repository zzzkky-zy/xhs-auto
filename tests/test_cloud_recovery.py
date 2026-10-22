"""Offline recovery contracts: no real browser, credentials, or platform calls."""
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_actions import CloudActions, _iso
from cloud_recovery import CloudRecovery


class RecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'cloud.json'
        self.config.write_text(json.dumps({'schema_version': 1, 'browser_mode': 'cloud_host',
                                         'account_id': 'fixture', 'state_dir': 'state',
                                         'allow_write': False, 'allow_reconcile': False}))
        self.now = 1792404000.0
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.actions.prepare(operation='get_my_profile', payload={}, expected_account='expected-user',
                             target={'own_account': 'expected-user'}, user_source='fixture:user', operation_id='operation')
        self.recovery = CloudRecovery(self.actions)

    def tearDown(self):
        self.actions.close()
        self.tmp.cleanup()

    def obs(self, **overrides):
        value = dict(observed_at=_iso(self.now), evidence_ref='fixture:observation',
                     call_ended=True, control_available=False, tab_present=True,
                     challenge_active=False, rate_limited=False, permission_denied=False)
        value.update(overrides)
        return value

    def begin(self, **overrides):
        return self.recovery.begin('operation', 'incident', self.obs(**overrides))

    def request(self, method='kernel_reset', **overrides):
        return self.recovery.advance('incident', 'request_recovery', self.obs(
            capability={'method': method, 'supported': True, 'documentation_ref': 'fixture:official-tool-docs'},
            **overrides))

    def verify(self, **overrides):
        return self.recovery.advance('incident', 'verify', self.obs(control_available=True,
            challenge_absent=True, identity={'account_id': 'expected-user', 'observed_at': _iso(self.now),
            'evidence_ref': 'fixture:visible-profile', 'browser_context': 'fixture:tab'}, **overrides))

    def test_consumed_before_return_and_persists_reopen(self):
        self.begin()
        result = self.request()
        self.assertTrue(result['dispatch_once'])
        self.assertEqual(result['method'], 'kernel_reset')
        self.assertEqual(result['next_host_action'], 'kernel_reset')
        self.actions.close()
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.recovery = CloudRecovery(self.actions)
        self.assertTrue(self.recovery.get('incident')['attempt_consumed'])
        self.assertFalse(self.recovery.get('incident')['dispatch_once'])
        self.assertFalse(self.begin()['dispatch_once'])
        for method in ('kernel_reset', 'browser_restart', 'reconnect'):
            with self.assertRaisesRegex(ValueError, 'already consumed'): self.request(method)

    def test_challenge_never_reset(self):
        result = self.begin(challenge_active=True)
        self.assertEqual(result['next_host_action'], 'solve_challenge_in_normal_authorized_ui')
        self.assertFalse(self.request(challenge_active=True, control_available=True)['dispatch_once'])
        self.assertFalse(self.recovery.get('incident')['attempt_consumed'])

    def test_blockers_and_running_call_never_reset(self):
        self.begin()
        with self.assertRaises(ValueError): self.request(call_ended=False)
        with self.assertRaises(ValueError): self.request(control_available=True)
        for key in ('rate_limited', 'permission_denied'):
            with self.subTest(key=key):
                self.assertFalse(self.request(**{key: True})['dispatch_once'])

    def test_unknown_is_not_absence(self):
        self.begin()
        observation = self.obs()
        observation.pop('challenge_active')
        result = self.recovery.advance('incident', 'diagnose', observation)
        self.assertEqual(result['state'], 'manual')
        observation['capability'] = {'method': 'kernel_reset', 'supported': True, 'documentation_ref': 'fixture:docs'}
        with self.assertRaises(ValueError): self.recovery.advance('incident', 'request_recovery', observation)

    def test_supported_documentation_required(self):
        self.begin()
        for capability in (None, {}, {'method': 'browser_restart', 'supported': False, 'documentation_ref': 'fixture:docs'},
                           {'method': 'shell', 'supported': True, 'documentation_ref': 'fixture:docs'},
                           {'method': 'kernel_reset', 'supported': True, 'documentation_ref': ''}):
            with self.subTest(capability=capability), self.assertRaises(ValueError):
                self.recovery.advance('incident', 'request_recovery', self.obs(capability=capability))

    def test_methods_remain_distinct(self):
        for index, method in enumerate(('kernel_reset', 'browser_restart', 'reconnect')):
            operation = 'method-operation-' + str(index)
            self.actions.prepare(operation='get_my_profile', payload={}, expected_account='expected-user',
                                 target={'own_account': 'expected-user'}, user_source='fixture:user', operation_id=operation)
            incident = 'incident-' + str(index)
            self.recovery.begin(operation, incident, self.obs())
            result = self.recovery.advance(incident, 'request_recovery', self.obs(capability={
                'method': method, 'supported': True, 'documentation_ref': 'fixture:docs'}))
            self.assertEqual(result['method'], method)
            self.close_incident(incident)

    def close_incident(self, incident):
        self.now += 1
        self.recovery.advance(incident, 'verify', self.obs(control_available=True, challenge_absent=True,
            identity={'account_id': 'expected-user', 'observed_at': _iso(self.now),
                      'evidence_ref': 'fixture:profile', 'browser_context': 'fixture:tab'}))
        self.now += 1

    def test_failure_and_lost_result_do_not_allow_replacement(self):
        self.begin(); self.request()
        result = self.recovery.advance('incident', 'recovery_result', self.obs())
        self.assertEqual(result['state'], 'manual')
        with self.assertRaises(ValueError): self.request('reconnect')

    def test_verification_fresh_exact_identity(self):
        self.begin(); self.request()
        self.now += 1
        self.assertEqual(self.verify()['state'], 'verified')
        self.assertFalse(self.recovery.get('incident')['dispatch_once'])

    def test_stale_and_wrong_identity_rejected(self):
        self.begin(); self.request()
        for account, delta in [('wrong-user', 0), ('expected-user', -121), ('expected-user', 1), ('expected-user', -1)]:
            observation = self.obs(control_available=True, challenge_absent=True, identity={
                'account_id': account, 'observed_at': _iso(self.now + delta),
                'evidence_ref': 'fixture:profile', 'browser_context': 'fixture:tab'})
            with self.subTest(account=account, delta=delta), self.assertRaises(ValueError):
                self.recovery.advance('incident', 'verify', observation)

    def test_pre_recovery_identity_cannot_verify_after_reset(self):
        self.begin(); self.request()
        with self.assertRaisesRegex(ValueError, 'post-recovery'): self.verify()

    def test_verification_requires_explicit_absence(self):
        self.begin()
        with self.assertRaises(ValueError): self.recovery.advance('incident', 'verify', self.obs(control_available=True))
        with self.assertRaises(ValueError): self.verify(challenge_active=True)

    def test_new_tab_distinct_only_when_missing(self):
        self.begin()
        with self.assertRaises(ValueError):
            self.recovery.advance('incident', 'request_new_tab', self.obs(control_available=True))
        result = self.recovery.advance('incident', 'request_new_tab', self.obs(control_available=True, tab_present=False,
            session_already_absent=True, inventory_evidence_ref='fixture:actual-tab-inventory'))
        self.assertEqual(result['next_host_action'], 'open_new_tab_official_homepage')
        self.assertIsNone(result['method'])
        self.assertFalse(result['attempt_consumed'])
        with self.assertRaises(ValueError):
            self.recovery.advance('incident', 'request_new_tab', self.obs(control_available=True, tab_present=False))

    def test_unknown_action_snapshot_and_action_unchanged(self):
        # Fixture simulates a submission whose response was lost; recovery never writes this row.
        self.actions.db.execute("UPDATE actions SET status='submitted_unknown',attempt=3,attempt_id='attempt-three' WHERE operation_id='operation'")
        before = self.actions.get('operation')
        result = self.begin(editor_evidence={'screenshot_ref': 'fixture:editor', 'unsaved_changes': True})
        self.assertEqual(result['snapshot']['status'], 'submitted_unknown')
        self.assertEqual(result['snapshot']['attempt_id'], 'attempt-three')
        self.assertEqual(result['snapshot']['binding'], before['binding'])
        self.request(); self.now += 1; self.verify()
        self.assertEqual(self.actions.get('operation'), before)

    def test_new_incident_cannot_bypass_attempt_budget(self):
        self.begin(); self.request()
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.recovery.begin('operation', 'replacement', self.obs())
        self.now += 1; self.verify()
        with self.assertRaisesRegex(ValueError, 'newer evidence'):
            self.recovery.begin('operation', 'replacement', self.obs())
        self.now += 1
        self.assertFalse(self.recovery.begin('operation', 'replacement', self.obs())['attempt_consumed'])

    def test_observed_restrictions_survive_missing_page(self):
        for key in ('challenge_active', 'rate_limited', 'permission_denied'):
            operation = 'restricted-' + key
            self.actions.prepare(operation='get_my_profile', payload={}, expected_account='expected-user',
                                 target={'own_account': 'expected-user'}, user_source='fixture:user', operation_id=operation)
            self.recovery.begin(operation, operation, self.obs(**{key: True}))
            missing = self.obs(tab_present=False, capability={'method': 'browser_restart', 'supported': True,
                                                            'documentation_ref': 'fixture:docs'})
            self.recovery.advance(operation, 'diagnose', missing)
            with self.subTest(key=key):
                self.assertFalse(self.recovery.advance(operation, 'request_recovery', missing)['dispatch_once'])
                self.assertFalse(self.recovery.advance(operation, 'request_new_tab',
                    self.obs(tab_present=False, control_available=True))['dispatch_once'])
            self.close_incident(operation)

    def test_new_operation_cannot_bypass_active_account_incident(self):
        self.begin(); self.request()
        self.actions.prepare(operation='get_my_profile', payload={}, expected_account='expected-user',
                             target={'own_account': 'expected-user'}, user_source='fixture:user', operation_id='unrelated')
        with self.assertRaisesRegex(ValueError, 'unresolved'):
            self.recovery.begin('unrelated', 'replacement', self.obs())

    def test_restriction_in_denied_request_is_persisted(self):
        self.begin()
        self.assertFalse(self.request(challenge_active=True, control_available=True)['dispatch_once'])
        self.assertFalse(self.request('browser_restart', tab_present=False)['dispatch_once'])
        self.assertIn('challenge_active', self.recovery.get('incident')['restriction_latches'])

    def test_kernel_restore_keeps_challenge_and_unknown_action(self):
        self.actions.db.execute("UPDATE actions SET status='submitted_unknown' WHERE operation_id='operation'")
        before = self.actions.get('operation')
        self.begin(challenge_active=True)
        result = self.request(challenge_active=True)
        self.assertTrue(result['dispatch_once'])
        self.assertTrue(result['no_page_replacement'])
        self.assertTrue(result['preserve_browser_session_required'])
        self.assertTrue(result['observation_only'])
        self.assertIn('challenge_active', result['restriction_latches'])
        self.assertEqual(self.actions.get('operation'), before)
        with self.assertRaisesRegex(ValueError, 'consumed'): self.request(challenge_active=True)

    def test_natural_session_loss_allows_one_homepage_observation(self):
        self.begin(challenge_active=True)
        observation = self.obs(tab_present=False, control_available=True, session_already_absent=True,
                               inventory_evidence_ref='fixture:actual-tab-inventory')
        result = self.recovery.advance('incident', 'request_new_tab', observation)
        self.assertTrue(result['dispatch_once'])
        self.assertIn('challenge_active', result['restriction_latches'])
        self.assertFalse(result['attempt_consumed'])
        self.assertTrue(result['no_page_replacement'])
        with self.assertRaisesRegex(ValueError, 'consumed'):
            self.recovery.advance('incident', 'request_new_tab', observation)
        self.now += 1
        self.assertEqual(self.verify()['state'], 'verified')

    def test_homepage_does_not_bypass_rate_or_permission_latches(self):
        self.begin(rate_limited=True)
        result = self.recovery.advance('incident', 'request_new_tab', self.obs(tab_present=False,
            control_available=True, session_already_absent=True, inventory_evidence_ref='fixture:inventory'))
        self.assertFalse(result['dispatch_once'])

    def test_changed_configured_account_rejected(self):
        self.begin()
        config = json.loads(self.config.read_text())
        config['expected_user_id'] = 'other-user'
        self.config.write_text(json.dumps(config))
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_CHANGED'): self.recovery.get('incident')
        with self.assertRaisesRegex(ValueError, 'ACCOUNT_CHANGED'): self.verify()

    def test_invalid_evidence_and_secrets(self):
        for overrides in ({'observed_at': _iso(self.now - 121)}, {'editor_evidence': {'cookies': 'secret'}},
                          {'challenge_active': 'false'}, {'command': 'restart'}):
            with self.subTest(overrides=overrides), self.assertRaises(ValueError): self.begin(**overrides)

    def test_incident_cannot_change_binding(self):
        self.begin()
        self.actions.prepare(operation='get_my_profile', payload={}, expected_account='expected-user',
                             target={'own_account': 'expected-user'}, user_source='fixture:user', operation_id='other')
        with self.assertRaises(ValueError): self.recovery.begin('other', 'incident', self.obs())


if __name__ == '__main__': unittest.main()
