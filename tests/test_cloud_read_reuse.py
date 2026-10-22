"""Offline synthetic observations; no live speed or account claims."""
import copy
import datetime as dt
from pathlib import Path
import sys
import unittest
from unittest import mock
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_read_reuse import plan_read_reuse

class ReadReuseTests(unittest.TestCase):
    def setUp(self):
        self.now = 1800000000.0
        self.binding = {'operation':'reply_comment_in_feed','expected_account':'account',
                        'payload':{'feed_id':'note','comment_id':'reply'}}
        self.original = {'account_id':'account','feed_id':'note','author_id':'author',
            'read_at':self.stamp(-900), 'evidence_ref':'synthetic-original',
            'body_complete':True,'body_fingerprint':'body-v1','images_complete':True,
            'image_count':2,'image_fingerprints':{'1':'image-one','2':'image-two'},
            'comment_fingerprints':{'reply':'reply-v1','unrelated':'unrelated-v1'}}
        self.current = {'account_id':'account','feed_id':'note','author_id':'author',
            'observed_at':self.stamp(-10),'evidence_ref':'synthetic-current',
            'browser_context':'browser','reply_comment_id':'reply',
            'body_fingerprint':'body-v1','image_fingerprints':{'1':'image-one','2':'image-two'},
            'relevant_comment_ids':['reply'],'relevant_comments_observed':True,
            'comment_fingerprints':{'reply':'reply-v1'}}
    def stamp(self, offset):
        return dt.datetime.fromtimestamp(self.now+offset,dt.timezone.utc).isoformat()
    def plan(self):
        return plan_read_reuse(self.binding,self.original,self.current,self.now)
    def scopes(self):
        return {x['scope'] for x in self.plan()['required_work']}
    def test_complete_evidence_reuses_without_unrelated_expansion(self):
        result=self.plan()
        self.assertEqual(result['status'],'reusable')
        self.assertEqual(result['reusable_scopes'],['body','images','comment:reply'])
        self.assertEqual(result['read_at'],self.original['read_at'])
        self.assertTrue(result['cached']);self.assertFalse(result['fresh_read'])
        self.assertFalse(result['send_authorized']);self.assertEqual(result['budget_charge'],0)
    def test_stale_target_only_requires_target_refresh(self):
        self.current['observed_at']=self.stamp(-121)
        self.assertEqual(self.scopes(),{'target_observation'})
        self.assertIn('body',self.plan()['reusable_scopes'])
    def test_exact_120_second_boundary(self):
        self.current['observed_at']=self.stamp(-120)
        self.assertEqual(self.plan()['status'],'reusable')
    def test_body_change_only_requires_body(self):
        self.current['body_fingerprint']='body-v2'
        self.assertEqual(self.scopes(),{'body'})
    def test_images_change_only_requires_images(self):
        self.current['image_fingerprints']['2']='image-new'
        self.assertEqual(self.scopes(),{'images'})
    def test_missing_current_fingerprint_is_not_proof_of_unchanged(self):
        del self.current['body_fingerprint'];del self.current['image_fingerprints']
        self.assertEqual(self.scopes(),{'body_fingerprint','image_fingerprints'})
    def test_missing_original_does_not_block_normal_read(self):
        self.original=None
        self.assertEqual(self.scopes(),{'body','images','comment:reply'})
    def test_cached_flag_does_not_prove_full_read(self):
        self.original['cached']=True;self.original['body_complete']=False
        self.assertIn('body',self.scopes())
    def test_image_incomplete_needs_images_only(self):
        self.original['image_count']=3
        self.assertEqual(self.scopes(),{'images'})
    def test_unchanged_comment_count_does_not_prove_unchanged_thread(self):
        self.original['comment_count']=44;self.current['comment_count']=44
        self.current['comment_fingerprints']['reply']='reply-edited'
        self.assertEqual(self.scopes(),{'comment:reply'})
    def test_new_relevant_comment_only_reads_that_comment(self):
        self.current['relevant_comment_ids'].append('new')
        self.current['comment_fingerprints']['new']='new-content'
        self.assertEqual(self.scopes(),{'comment:new'})
    def test_missing_relevant_scope_and_current_comment_rejected_as_complete(self):
        del self.current['relevant_comment_ids'];self.current['comment_fingerprints']={}
        self.assertEqual(self.scopes(),{'relevant_comments','comment:reply'})
    def test_missing_fresh_comment_observation(self):
        self.current['relevant_comments_observed']=False
        self.assertEqual(self.scopes(),{'relevant_comments'})
    def test_account_note_author_reply_mismatch_rejected(self):
        for key in ['account_id','feed_id','author_id','reply_comment_id']:
            with self.subTest(key=key):
                old=self.current[key];self.current[key]='different'
                with self.assertRaises(ValueError):self.plan()
                self.current[key]=old
    def test_original_account_and_note_mismatch_rejected(self):
        for key in ['account_id','feed_id']:
            old=self.original[key];self.original[key]='different'
            with self.assertRaises(ValueError):self.plan()
            self.original[key]=old
    def test_future_original_and_current_rejected(self):
        for obj,key in [(self.original,'read_at'),(self.current,'observed_at')]:
            old=obj[key];obj[key]=self.stamp(1)
            with self.assertRaises(ValueError):self.plan()
            obj[key]=old
    def test_current_cannot_predate_original(self):
        self.current['observed_at']=self.stamp(-901)
        with self.assertRaises(ValueError):self.plan()
    def test_missing_current_requires_observation_not_fake_freshness(self):
        self.current=None
        self.assertEqual(self.scopes(),{'target_observation'})
    def test_binding_must_be_note_bound(self):
        del self.binding['payload']['feed_id']
        with self.assertRaises(ValueError):self.plan()
    def test_missing_old_image_map_does_not_invent_zero_image_proof(self):
        del self.original['image_fingerprints']
        self.original['image_count']=0
        self.current['image_fingerprints']={}
        self.assertEqual(self.scopes(),{'images'})
        self.assertNotIn('images',self.plan()['reusable_scopes'])
    def test_explicit_complete_zero_images_can_be_reused(self):
        self.original['image_count']=0
        self.original['image_fingerprints']={}
        self.current['image_fingerprints']={}
        self.assertEqual(self.plan()['status'],'reusable')
    def test_malformed_operation_and_payload_have_stable_domain_error(self):
        for key,value in [('operation',[]),('operation',{}),('payload',None),('payload',[]),('payload','wrong')]:
            with self.subTest(key=key,value=value):
                before=self.binding[key];self.binding[key]=value
                with self.assertRaisesRegex(ValueError,'ERR_READ_REUSE:'):self.plan()
                self.binding[key]=before
    def test_malformed_old_image_map_has_stable_domain_error(self):
        for value in [None,[],True]:
            self.original['image_fingerprints']=value
            with self.assertRaisesRegex(ValueError,'ERR_READ_REUSE:'):self.plan()
    def test_bound_reply_user_requires_separate_observed_reply_author(self):
        self.binding['payload']['user_id']='reply-author'
        self.assertEqual(self.scopes(),{'reply_author'})
        self.current['reply_user_id']='reply-author'
        self.assertEqual(self.plan()['status'],'reusable')
    def test_note_author_cannot_substitute_for_bound_reply_author(self):
        self.binding['payload']['user_id']='reply-author'
        self.current['reply_user_id']=self.current['author_id']
        with self.assertRaisesRegex(ValueError,'bound reply author mismatch'):self.plan()
    def test_legacy_unbound_reply_user_does_not_invent_author_proof(self):
        self.assertEqual(self.plan()['status'],'reusable')
        self.assertNotIn('reply_author',self.plan()['reusable_scopes'])
        self.assertFalse(self.plan()['send_authorized'])
    def test_no_input_mutation_io_or_budget_calls(self):
        before=copy.deepcopy((self.binding,self.original,self.current))
        with mock.patch('builtins.open',side_effect=AssertionError('no filesystem')),mock.patch('socket.socket',side_effect=AssertionError('no network')):
            self.plan()
        self.assertEqual(before,(self.binding,self.original,self.current))

class SharedBudgetScopeTests(unittest.TestCase):
    def test_two_configs_runs_and_restart_share_same_account_directory(self):
        import json
        import tempfile
        from cloud_pacing import CloudPacing
        with tempfile.TemporaryDirectory() as directory:
            root=Path(directory); state=root/'shared-state'; now=[1800000000.0]
            config={'browser_mode':'cloud_host','expected_user_id':'fixture-account',
                    'state_dir':str(state),'read_max_per_hour':2}
            paths=[root/'run-one.json',root/'run-two.json']
            for path in paths:path.write_text(json.dumps(config))
            def gate(path):return CloudPacing(path,clock=lambda:now[0],random_uniform=lambda a,b:a)
            first=gate(paths[0]);second=gate(paths[1])
            try:
                self.assertTrue(first.reserve('list_feeds','run-one-read')['permitted'])
                now[0]+=20
                self.assertTrue(second.reserve('list_feeds','run-two-read')['permitted'])
                now[0]+=20
                self.assertFalse(first.reserve('list_feeds','run-one-next')['permitted'])
                second.close();second=gate(paths[1])
                blocked=second.reserve('list_feeds','run-three-after-restart')
                self.assertFalse(blocked['permitted'])
                self.assertEqual(blocked['retry_at'],1800003600.0)
            finally:first.close();second.close()

if __name__=='__main__':unittest.main()
