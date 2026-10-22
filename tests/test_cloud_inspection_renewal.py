"""Synthetic local-only pre-upload inspection renewal; no live state/browser."""
import unittest
import test_cloud_schedule_inspection as fixtures


class InspectionRenewalTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'save_config', 'stamp', 'prepare', 'attestation',
                  'identity', 'ready', 'enabled', 'claimed', 'submitted', 'result', 'inspection_args'):
        locals()[_name] = getattr(fixtures.ScheduleInspectionTests, _name)

    def started(self):
        claim = self.claimed('inspect', **self.inspection_args())
        self.ledger.begin_submit('inspect', claim['claim_token'])
        self.time += 121
        return claim

    def observation(self, **changes):
        return {'expected_account': 'public-user-1', 'browser_context': 'fixture:browser-1/tab-2',
                'call_ended': True, 'upload_invoked': False, 'observed_at': self.stamp(),
                'evidence_ref': 'SYNTHETIC no-upload call failure observation', **changes}

    def test_expired_same_attempt_retained_with_immutable_binding_and_evidence(self):
        claim = self.started(); before = self.ledger.get('inspect')
        value = self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation())
        for key in ('binding', 'binding_digest', 'status', 'attempt', 'attempt_id', 'owner', 'submitted_at', 'authorization', 'identity', 'result'):
            self.assertEqual(value[key], before[key])
        self.assertEqual(value['status'], 'submitted_unknown')
        self.assertEqual(value['lease_until'], self.stamp(120))
        self.ledger._claim(self.ledger._row('inspect'), claim['claim_token'], live=True)
        self.assertEqual(self.ledger.events('inspect')[-1]['event'], 'inspection_claim_renewed_before_upload')
        # Local renewal does not refresh identity or grant another dispatch.
        with self.assertRaisesRegex(ValueError, 'ERR_IDENTITY_STALE'):
            self.ledger._guard_dispatch(self.ledger._row('inspect'), browser_context='fixture:browser-1/tab-2')
        with self.assertRaisesRegex(ValueError, 'ERR_STATE'): self.ledger.begin_submit('inspect', claim['claim_token'])

    def test_bad_scope_context_time_flags_token_and_bounds_rejected(self):
        claim = self.started()
        invalid = [{'expected_account': 'wrong'}, {'browser_context': 'wrong'}, {'call_ended': False},
                   {'call_ended': 1}, {'upload_invoked': True}, {'upload_invoked': 0},
                   {'observed_at': self.stamp(-121)}, {'observed_at': self.stamp(1)}, {'evidence_ref': ''}]
        for change in invalid:
            with self.subTest(change=change), self.assertRaises(ValueError):
                self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation(**change))
        for seconds in (True, 0, 4, 601, float('nan'), float('inf')):
            with self.assertRaises(ValueError):
                self.ledger.renew_inspection_claim('inspect', claim['claim_token'], seconds, self.observation())
        with self.assertRaisesRegex(ValueError, 'ERR_CLAIM'):
            self.ledger.renew_inspection_claim('inspect', 'wrong', 120, self.observation())

    def test_controls_renewal_requires_truthful_completed_upload_observation(self):
        claim=self.started()
        with self.assertRaises(ValueError):
            self.ledger.renew_inspection_claim('inspect',claim['claim_token'],120,self.observation(),after_upload=True)
        value=self.ledger.renew_inspection_claim('inspect',claim['claim_token'],120,self.observation(upload_invoked=True),after_upload=True)
        self.assertEqual(value['attempt_id'],claim['attempt_id'])
        self.assertEqual(self.ledger.events('inspect')[-1]['event'],'inspection_controls_claim_renewed')

    def test_reported_unknown_and_success_are_not_renewable(self):
        claim = self.started()
        self.ledger.report_result('inspect', claim['claim_token'], self.result(claim, status='submitted_unknown', evidence_kind='uncertain'))
        with self.assertRaisesRegex(ValueError, 'ERR_INSPECTION_RENEWAL'):
            self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation())
        self.ledger.report_result('inspect', claim['claim_token'], self.result(claim))
        with self.assertRaisesRegex(ValueError, 'ERR_INSPECTION_RENEWAL'):
            self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation())

    def test_comment_and_publication_not_renewable(self):
        for operation in ('post_comment_to_feed', 'publish_content'):
            with self.subTest(operation=operation):
                args = self.inspection_args(operation) if operation == 'publish_content' else {}
                if args: args['operation'] = operation
                claim = self.claimed(operation, **args)
                self.ledger.begin_submit(operation, claim['claim_token'])
                with self.assertRaisesRegex(ValueError, 'ERR_INSPECTION_RENEWAL'):
                    self.ledger.renew_inspection_claim(operation, claim['claim_token'], 120, self.observation())
                self.ledger.report_result(operation, claim['claim_token'], self.result(claim, status='not_applied', evidence_kind='confirmed_not_applied'))

    def test_pre_submit_not_renewable_and_verifier_can_veto(self):
        claim = self.claimed('inspect', **self.inspection_args())
        with self.assertRaisesRegex(ValueError, 'ERR_INSPECTION_RENEWAL'):
            self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation())
        self.ledger.begin_submit('inspect', claim['claim_token']); self.time += 121
        self.ledger.host_verifier = lambda *args: False
        with self.assertRaisesRegex(ValueError, 'ERR_HOST_VERIFIER_DENIED'):
            self.ledger.renew_inspection_claim('inspect', claim['claim_token'], 120, self.observation())


if __name__ == '__main__': unittest.main()
