"""Offline structural review-gate tests; never platform or approval evidence."""
import json
import unittest
from unittest import mock
import test_cloud_batch
from cloud_host import CloudHost


class CloudReviewGateTests(unittest.TestCase):
    def setUp(self):
        self.fixture = test_cloud_batch.CloudBatchTests()
        self.fixture.setUp()
        self.host = CloudHost(self.fixture.path)
        self.host.actions.clock = lambda: self.fixture.now[0]
        self.host.batch = self.fixture.batch
        self.host.batch.action_ledger = self.host.actions

    def tearDown(self):
        self.host.close()
        self.fixture.actions.close()
        self.fixture.tmp.cleanup()

    def prepare(self):
        run, items = self.fixture.reviewed()
        payload = {**items[1]['payload'], 'run_id': run['id']}
        value = self.host.call('prepare_cloud_action', {
            'operation': items[1]['tool'], 'payload': payload,
            'user_source': 'SYNTHETIC TEST ONLY', 'operation_id': 'reviewed-comment'})
        return run, items, value

    def test_prepare_and_submit_cannot_omit_run(self):
        payload = {'feed_id': 'b'*24, 'content': 'SYNTHETIC', 'xsec_token': 'fixture'}
        for name, args in (
            ('prepare_cloud_action', {'operation': 'post_comment_to_feed', 'payload': payload, 'user_source': 'fixture'}),
            ('submit_task', {'tool': 'post_comment_to_feed', 'payload': payload, 'approval_ref': 'fixture'})):
            with self.subTest(name=name), self.assertRaisesRegex(ValueError, 'ERR_CONTENT_RUN_REQUIRED'):
                self.host.call(name, args)
        self.assertEqual(self.host.actions.list_actions(), [])

    def test_prepare_correlates_review_and_valid_dispatch_rechecks(self):
        run, items, action = self.prepare()
        self.assertEqual(self.host.batch._linked_action(run['id'], action['operation_id'])['operation_id'], action['operation_id'])
        self.assertTrue(self.host._verify_dispatch_review('claim', action['binding'], {}))
        with mock.patch.object(self.host, '_pacing') as pacing:
            pacing.return_value.reserve.return_value={'permitted': True}
            self.assertTrue(self.host._verify_dispatch_review('begin_submit', action['binding'], {'attempt_id':'fixture-attempt'}))
            pacing.return_value.reserve.assert_called_once()

    def test_closed_run_blocks_both_dispatch_boundaries(self):
        run, items, action = self.prepare()
        stored = self.host.batch.continuous.run(run['id'])
        stored['state'] = 'completed'
        self.host.batch.continuous.save(stored)
        for phase in ('claim', 'begin_submit'):
            with self.assertRaisesRegex(ValueError, 'ERR_RUN_CLOSED'):
                self.host._verify_dispatch_review(phase, action['binding'], {})

    def test_changed_plan_blocks_dispatch(self):
        run, items, action = self.prepare()
        stored = self.host.batch.continuous.run(run['id'])
        stored['body']['plan']['comments'] = 2
        self.host.batch.continuous.save(stored)
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_REVIEW_STALE'):
            self.host._verify_dispatch_review('begin_submit', action['binding'], {})

    def test_revoked_tool_blocks_dispatch(self):
        run, items, action = self.prepare()
        policy = self.host.batch.continuous.policy()
        policy['allowed_tools'] = ['publish_content']
        self.host.batch.continuous.save_policy(policy)
        with self.assertRaisesRegex(ValueError, 'ERR_POLICY_SCOPE'):
            self.host._verify_dispatch_review('claim', action['binding'], {})

    def test_unbound_existing_action_cannot_dispatch(self):
        run, items = self.fixture.reviewed()
        payload = {**items[1]['payload'], 'run_id': run['id']}
        action = self.host.actions.prepare('post_comment_to_feed', payload,
            expected_account='a'*24, target={'feed_id': 'b'*24}, user_source='fixture')
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.host._verify_dispatch_review('begin_submit', action['binding'], {})

    def test_public_read_and_non_content_actions_do_not_require_batch(self):
        for operation in ('search_feeds', 'like_feed'):
            self.assertTrue(self.host._verify_dispatch_review('claim', {'operation': operation}, {}))

    def test_rate_limited_begin_never_crosses_submission_boundary(self):
        run, items, action = self.prepare()
        actions=self.host.actions
        actions.request_authorization(action['operation_id'])
        import datetime as dt
        now=dt.datetime.fromtimestamp(self.fixture.now[0],dt.timezone.utc)
        actions.record_authorization(action['operation_id'], {
            'operation_id':action['operation_id'],'binding_digest':action['binding_digest'],
            'expected_account':'a'*24,'user_source':'SYNTHETIC TEST ONLY','user_quote':'SYNTHETIC',
            'confirmed_at':now.isoformat(),'expires_at':(now+dt.timedelta(minutes=5)).isoformat(),
            'evidence_ref':'synthetic'})
        actions.observe_identity(action['operation_id'], {'status':'observed','account_id':'a'*24,
            'observed_at':now.isoformat(),'evidence_ref':'synthetic','browser_context':'synthetic-tab'})
        data=json.loads(self.fixture.path.read_text());data['allow_write']=True
        self.fixture.path.write_text(json.dumps(data))
        claimed=actions.claim(action['operation_id'],'synthetic',browser_context='synthetic-tab')
        with mock.patch.object(self.host, '_pacing') as pacing:
            pacing.return_value.reserve.return_value={'permitted':False,'status':'rate_limited','retry_at':self.fixture.now[0]+60}
            with self.assertRaisesRegex(ValueError,'ERR_BROWSER_PACING'):
                self.host.call('begin_cloud_dispatch', {'operation_id':action['operation_id'],
                    'claim_token':claimed['claim_token'],'target_author_id':'c'*24,'thread_id':'b'*24,
                    'author_evidence':{'evidence_ref':'synthetic'}})
        saved=actions.get(action['operation_id'])
        self.assertEqual(saved['status'],'dispatching')
        self.assertIsNone(saved['submitted_at'])

    def test_reply_target_must_match_immutable_comment_before_reserving(self):
        payload={'feed_id':'b'*24,'comment_id':'exact-comment','content':'synthetic','run_id':'synthetic-run'}
        binding={'operation':'reply_comment_in_feed','payload':payload,'user_source':'synthetic',
                 'operation_id':'synthetic-action','expected_account':'a'*24}
        for target in ('different-comment', None):
            self.host._dispatch_context={'thread_id':'b'*24,'author_evidence':{'target_comment_id':target}}
            with mock.patch.object(self.host,'_review_payload',return_value={'run_id':'synthetic-run','payload':payload}), \
                 mock.patch.object(self.host,'_batch'), mock.patch.object(self.host,'_pacing') as pacing:
                with self.assertRaisesRegex(ValueError,'ERR_PACING_BINDING_MISMATCH'):
                    self.host._verify_dispatch_review('begin_submit',binding,{'attempt_id':'synthetic-attempt'})
                pacing.assert_not_called()
        self.host._dispatch_context={}

    def test_paired_parent_must_match_platform_account(self):
        binding={'operation':'reply_notification','payload':{'feed_id':'b'*24},'expected_account':'a'*24}
        self.host._dispatch_context={'thread_id':'b'*24,'parent_operation_id':'parent'}
        parent={'status':'host_reported_success','attempt_id':'old-attempt','binding':{
            'expected_account':'c'*24,'operation':'post_comment_to_feed','payload':{'feed_id':'b'*24}}}
        with mock.patch.object(self.host.actions,'get',return_value=parent),mock.patch.object(self.host,'_pacing') as pacing:
            with self.assertRaisesRegex(ValueError,'ERR_PACING_BINDING_MISMATCH'):
                self.host._verify_dispatch_review('begin_submit',binding,{'attempt_id':'synthetic-attempt'})
            pacing.assert_not_called()
        self.host._dispatch_context={}

    def test_prepare_schema_exposes_explicit_retry_lineage(self):
        from cloud_host import TOOLS
        schema=next(t['inputSchema'] for t in TOOLS if t['name']=='prepare_cloud_action')
        self.assertIn('retry_of',schema['properties'])

    def test_claim_and_begin_are_wired_to_injected_verifier(self):
        self.assertEqual(self.host.actions.host_verifier, self.host._verify_dispatch_review)
        run, items, action = self.prepare()
        row = self.host.actions._row(action['operation_id'])
        with mock.patch.object(self.host, '_review_payload', side_effect=ValueError('review revoked')):
            for phase in ('claim', 'begin_submit'):
                with self.assertRaisesRegex(ValueError, 'review revoked'):
                    self.host.actions._verify(phase, row, {})


if __name__ == '__main__':
    unittest.main()
