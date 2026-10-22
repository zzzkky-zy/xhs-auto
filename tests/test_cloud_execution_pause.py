"""Synthetic offline pause tests; never operate a real account or browser."""
import json
import unittest
from unittest import mock
import test_cloud_actions
from cloud_actions import CloudActions
from cloud_host import CloudHost


class ExecutionPauseTests(unittest.TestCase):
    def setUp(self):
        self.f = test_cloud_actions.CloudActionTests()
        self.f.setUp()
        self.f.data['expected_user_id'] = 'public-user-1'
        self.f.save_config()

    def tearDown(self):
        self.f.tearDown()

    def pause(self, paused=True):
        return self.f.ledger.set_execution_pause(paused, 'SYNTHETIC owner request', 'SYNTHETIC test')

    def test_pause_after_claim_blocks_submit_and_survives_restart(self):
        claim = self.f.claimed()
        self.pause()
        self.f.ledger.close()
        self.f.ledger = CloudActions(self.f.config, clock=lambda: self.f.time)
        with self.assertRaisesRegex(ValueError, 'ERR_EXECUTION_PAUSED'):
            self.f.ledger.begin_submit('action-1', claim['claim_token'])
        self.assertEqual(self.f.ledger.get('action-1')['status'], 'dispatching')
        self.assertTrue(self.f.ledger.status()['execution_pause']['paused'])

    def test_pause_before_claim_and_explicit_resume_keep_other_guards(self):
        self.f.enabled(); self.f.ready()
        self.pause()
        with self.assertRaisesRegex(ValueError, 'ERR_EXECUTION_PAUSED'):
            self.f.ledger.claim('action-1', 'fixture')
        self.pause(False)
        self.f.time += 601
        with self.assertRaisesRegex(ValueError, 'ERR_AUTHORIZATION_EXPIRED'):
            self.f.ledger.claim('action-1', 'fixture')

    def test_pause_between_submit_and_step_blocks_every_nonobservation(self):
        claim = self.f.submitted()
        host = CloudHost.__new__(CloudHost); host.actions = self.f.ledger
        # A second connection changes the latch after claim/begin_submit.
        other = CloudActions(self.f.config, clock=lambda: self.f.time)
        other.set_execution_pause(True, 'SYNTHETIC owner', 'SYNTHETIC pause')
        other.close()
        for kind in ('click', 'type', 'navigation', 'reload', 'search', 'media_request'):
            with self.subTest(kind=kind), self.assertRaisesRegex(ValueError, 'ERR_EXECUTION_PAUSED'):
                host._browser_step({'operation_id': 'action-1', 'claim_token': claim['claim_token'],
                                    'step_id': 'new-' + kind, 'kind': kind, 'target': 'SYNTHETIC'})
        self.f.ledger.guard_execution_pause(claim['binding'], observation_only=True)
        self.f.ledger.report_result('action-1', claim['claim_token'], self.f.result(claim))
        self.assertEqual(self.f.ledger.get('action-1')['status'], 'host_reported_success')

    def test_identity_scope_shared_alias_and_distinct_account(self):
        self.pause()
        self.f.data['account_id'] = 'second-alias'; self.f.save_config()
        other = CloudActions(self.f.config, clock=lambda: self.f.time)
        try:
            self.assertTrue(other.execution_pause_status()['paused'])
            self.f.data['expected_user_id'] = 'different-user'; self.f.save_config()
            self.assertFalse(other.execution_pause_status()['paused'])
        finally:
            other.close()

    def test_explicit_fields_and_audit_are_not_authorization(self):
        with self.assertRaises(ValueError): self.pause(1)
        with self.assertRaises(ValueError): self.f.ledger.set_execution_pause(False, '', 'test')
        result = self.pause(False)
        self.assertFalse(result['grants_execution_authority'])
        self.assertFalse(result['existing_authorizations_renewed'])
        self.assertEqual(self.f.ledger.list_actions(), [])
        with self.assertRaises(Exception):
            self.f.ledger.db.execute('DELETE FROM execution_control_events')
        self.f.data.pop('expected_user_id'); self.f.save_config()
        with self.assertRaisesRegex(ValueError, 'ERR_EXECUTION_CONTROL'):
            self.pause(False)

    def test_queue_pause_semantics_unchanged_and_read_actions_allowed(self):
        claim = self.f.claimed()
        self.f.ledger.set_queue_enabled(False)
        self.f.ledger.begin_submit('action-1', claim['claim_token'])
        self.pause()
        self.f.ledger.guard_execution_pause({'mutation': False})
        self.assertFalse(self.f.ledger.queue_enabled())
