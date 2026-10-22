"""Temporary offline integration; no browser, Send, live state or real cleanup."""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from cloud_host import CloudHost, TOOLS
from cloud_actions import _iso
import content_fixture
import test_cloud_read_reuse


class RoundTwoHostTests(unittest.TestCase):
    def setUp(self):
        self.temp=tempfile.TemporaryDirectory(); self.base=Path(self.temp.name); self.path=self.base/'config.json'
        self.settings=dict(schema_version=1,browser_mode='cloud_host',account_id='fixture',expected_user_id='a'*24,
                           state_dir='state',allow_write=False,allow_reconcile=False,resources={'min_free_disk_mb':1,'min_free_inodes':1})
        self.path.write_text(json.dumps(self.settings)); self.host=CloudHost(self.path)
        self.now=1800000000.; self.host.actions.clock=lambda:self.now
    def tearDown(self):
        self.host.close(); self.temp.cleanup()
    def action(self, operation='get_feed_detail'):
        payload={'feed_id':'b'*24,'xsec_token':'SYNTHETIC'} if operation=='get_feed_detail' else {}
        return self.host.actions.prepare(operation,payload,expected_account='a'*24,target={'fixture':'only'},user_source='SYNTHETIC',operation_id='op')
    def reuse_args(self):
        fixture=test_cloud_read_reuse.ReadReuseTests(); fixture.setUp()
        for observation in (fixture.original,fixture.current):
            observation.update(account_id='a'*24,feed_id='b'*24)
        return dict(operation_id='op',original_read=fixture.original,current_observation=fixture.current)
    def test_read_advice_uses_persisted_binding_without_budget_or_authority(self):
        self.action(); before=self.host.actions.db.total_changes
        result=self.host.call('plan_cloud_read_reuse',self.reuse_args())
        self.assertFalse(result['send_authorized']); self.assertFalse(result['fresh_read'])
        self.assertEqual(result['budget_charge'],0); self.assertEqual(before,self.host.actions.db.total_changes)
        self.assertIsNone(self.host.pacing); self.assertIsNone(self.host.batch)
        with self.assertRaisesRegex(ValueError,'invalid tool arguments'):
            self.host.call('plan_cloud_read_reuse',{**self.reuse_args(),'binding':{'expected_account':'forged'}})
    def test_read_advice_rejects_missing_or_mismatched_action_not_future_guess(self):
        with self.assertRaises(ValueError):self.host.call('plan_cloud_read_reuse',{'operation_id':'missing'})
        self.action(); args=self.reuse_args(); args['current_observation']['feed_id']='other'
        with self.assertRaisesRegex(ValueError,'ERR_READ_REUSE'):self.host.call('plan_cloud_read_reuse',args)
    def test_paused_read_advice_is_not_send_and_new_account_cannot_reuse(self):
        self.action(); self.host.actions.set_execution_pause(True,'SYNTHETIC','pause')
        self.assertFalse(self.host.call('plan_cloud_read_reuse',self.reuse_args())['send_authorized'])
        self.settings['expected_user_id']='other'; self.path.write_text(json.dumps(self.settings))
        with self.assertRaisesRegex(ValueError,'ERR_EXECUTION_CONTROL'):self.host.call('plan_cloud_read_reuse',self.reuse_args())
    def test_rule_diagnostic_cold_batch_does_not_initialize_or_accept_forged_profile(self):
        before=set(self.base.rglob('*'))
        with self.assertRaisesRegex(ValueError,'ERR_BATCH_NOT_OPEN'):
            self.host.call('explain_cloud_effective_rules',{'run_id':'missing'})
        self.assertEqual(before,set(self.base.rglob('*')))
        with self.assertRaisesRegex(ValueError,'invalid tool arguments'):
            self.host.call('explain_cloud_effective_rules',{'run_id':'missing','profile':{}})
    def make_run(self):
        batch=self.host._batch()
        batch.dispatch('configure',{'authorization_ref':'SYNTHETIC','quality_standard':'SYNTHETIC',
            'allowed_tools':['publish_content','post_comment_to_feed'],'content_profile':content_fixture.profile(self.base)})
        return batch.dispatch('begin',{'plan_ref':'SYNTHETIC','plan':{'posts':1,'comments':1,'comment_strategy':{'A':1,'B':0,'C':0}}})
    def test_rule_diagnostic_reads_pinned_profile_without_changes(self):
        run=self.make_run(); before=self.host.batch.db.total_changes
        result=self.host.call('explain_cloud_effective_rules',{'run_id':run['id']})
        self.assertTrue(result['diagnostic_only']); self.assertFalse(result['execution_authorized'])
        self.assertEqual(before,self.host.batch.db.total_changes)
        old=self.host.batch.continuous.run(run['id'])
        self.assertEqual(old['body']['content_profile_version'],run['body']['content_profile_version'])
    def test_rule_diagnostic_missing_pin_never_falls_back_latest(self):
        run=self.make_run(); body=copy.deepcopy(run['body']); body.pop('content_profile_version')
        self.host.batch.db.execute('UPDATE ops_runs SET body=? WHERE id=?',(json.dumps(body),run['id']))
        with self.assertRaisesRegex(ValueError,'ERR_RULES_PINNED_PROFILE_REQUIRED'):
            self.host.call('explain_cloud_effective_rules',{'run_id':run['id']})
    def owned_ready(self):
        self.action('get_my_profile')
        self.host._pacing()  # Explicit isolated fixture initialization, never performed by close helper.
        self.host.actions.db.execute("UPDATE actions SET status='dispatching',attempt_id='attempt',claim_context='context' WHERE operation_id='op'")
        self.page=dict(browser_session='session',browser_context='context',tab_id='7',page_instance='SYNTHETIC-instance')
        observation=self.obs(created_by_host=True,instance_verifiable=True,creation_evidence_ref='SYNTHETIC',instance_documentation_ref='SYNTHETIC')
        self.host.call('register_cloud_owned_page',dict(operation_id='op',resource_id='page',observation=observation))
        self.host.actions.db.execute("UPDATE actions SET status='host_reported_success',result=? WHERE operation_id='op'",(json.dumps({'status':'host_reported_success','evidence_kind':'visible_result'}),))
    def obs(self,**values):
        return dict(observed_at=_iso(self.now),evidence_ref='SYNTHETIC',account_id='a'*24,page=self.page,**values)
    def close_args(self):
        return dict(operation_id='op',resource_id='page',capability={'method':'close_owned_tab','supported':True,'documentation_ref':'SYNTHETIC'},
          observation=self.obs(inventory_evidence_ref='SYNTHETIC',instance_evidence_ref='SYNTHETIC',call_ended=True,steps_resolved=True,editor_empty=True,owned_by_host=True,instance_verified=True,tab_present=True,challenge_active=False,rate_limited=False,permission_denied=False))
    def test_owned_close_paused_even_read_action_and_result_still_recordable(self):
        self.owned_ready(); self.host.actions.set_execution_pause(True,'SYNTHETIC','pause')
        with self.assertRaisesRegex(ValueError,'ERR_EXECUTION_PAUSED'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
        self.host.actions.set_execution_pause(False,'SYNTHETIC','explicit resume')
        self.assertTrue(self.host.call('request_cloud_owned_page_close',self.close_args())['dispatch_once'])
        self.host.actions.set_execution_pause(True,'SYNTHETIC','pause after permit')
        result=self.host.call('record_cloud_owned_page_close',dict(operation_id='op',resource_id='page',outcome='unknown',observation=self.obs()))
        self.assertFalse(result['dispatch_once'])
    def test_owned_unknown_close_cannot_reissue_after_host_reopen(self):
        self.owned_ready(); self.host.call('request_cloud_owned_page_close',self.close_args())
        self.host.close(); self.host=CloudHost(self.path); self.host.actions.clock=lambda:self.now
        with self.assertRaisesRegex(ValueError,'consumed'):self.host.call('request_cloud_owned_page_close',self.close_args())
        self.assertFalse(self.host.call('get_cloud_owned_page',{'operation_id':'op','resource_id':'page'})['dispatch_once'])
    def test_new_capabilities_explicitly_offline_only(self):
        report=self.host.call('cloud_capabilities',{})
        self.assertIn('offline',report['efficiency_verification_scope'])
        self.assertIn('read_reuse',report['local_efficiency_helpers'])

    def test_close_requires_existing_pacing_without_creating_missing_database(self):
        self.owned_ready(); self.host.pacing.close(); self.host.pacing=None
        path=self.host.actions.directory/'cloud-pacing.sqlite3'; path.unlink()
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_PACING_REQUIRED'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
        self.assertFalse(path.exists())
    def test_close_rechecks_real_active_blocks_and_related_unknown_steps(self):
        self.owned_ready(); db=self.host.pacing.db
        db.execute("INSERT INTO pacing_steps(account,step,attempt,binding,kind,status) VALUES(?,?,?,?,?,?)",('a'*24,'step','attempt','{}','click','unknown'))
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_STEP_PENDING'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
        db.execute("UPDATE pacing_steps SET status='completed'")
        db.execute("INSERT INTO pacing_blocks VALUES(?,?,?,?,?,?)",('a'*24,'captcha',self.now,'SYNTHETIC','{}',1))
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_PACING_BLOCK'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
    def test_independent_old_read_unknown_does_not_block_but_inflight_does(self):
        self.owned_ready()
        self.host.actions.prepare('get_my_profile',{},expected_account='a'*24,target={'fixture':'other'},user_source='SYNTHETIC',operation_id='other')
        self.host.actions.db.execute("UPDATE actions SET attempt_id='other-attempt',claim_context='other-context' WHERE operation_id='other'")
        db=self.host.pacing.db
        db.execute("INSERT INTO pacing_steps(account,step,attempt,binding,kind,status) VALUES(?,?,?,?,?,?)",('a'*24,'step','other-attempt','{}','click','in_flight'))
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_STEP_PENDING'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
        self.host.actions.db.execute("UPDATE actions SET claim_hash=? WHERE operation_id='other'",(hashlib.sha256(b'SYNTHETIC-token').hexdigest(),))
        db.execute("UPDATE pacing_steps SET reserved_at=?",(self.now,))
        self.host.call('record_cloud_browser_step',dict(operation_id='other',claim_token='SYNTHETIC-token',step_id='step',outcome='unknown',evidence={'call_ended':True,'challenge_active':False,'rate_limited':False,'permission_denied':False}))
        before=db.total_changes
        self.assertTrue(self.host.call('request_cloud_owned_page_close',self.close_args())['dispatch_once'])
        self.assertEqual(before,db.total_changes)

    def test_old_unmapped_attempt_unknown_is_not_lost_after_current_attempt_changes(self):
        self.owned_ready()
        self.host.actions._event('op','claimed',{'attempt_id':'historical-attempt','claim_context':'ctx'})
        self.host.pacing.db.execute("INSERT INTO pacing_steps(account,step,attempt,binding,kind,status) VALUES(?,?,?,?,?,?)",('a'*24,'old-step','historical-attempt','{}','click','unknown'))
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_STEP_PENDING'):
            self.host.call('request_cloud_owned_page_close',self.close_args())
        self.assertEqual('registered',self.host.call('get_cloud_owned_page',{'operation_id':'op','resource_id':self.close_args()['resource_id']})['state'])

    def test_old_own_attempt_cannot_be_hidden_by_an_independent_peer_reusing_attempt_id(self):
        self.owned_ready()
        self.host.actions._event('op','claimed',{'attempt_id':'old-attempt'})
        self.host.actions.prepare('get_my_profile',{},expected_account='a'*24,target={'fixture':'other'},user_source='SYNTHETIC',operation_id='other')
        self.host.actions.db.execute("UPDATE actions SET attempt_id='old-attempt',claim_context='other-context' WHERE operation_id='other'")
        proof=json.dumps({'call_ended':True,'challenge_active':False,'rate_limited':False,'permission_denied':False})
        self.host.pacing.db.execute("INSERT INTO pacing_steps(account,step,attempt,binding,kind,status,result) VALUES(?,?,?,?,?,?,?)",('a'*24,'old-step','old-attempt','{}','click','unknown',proof))
        with self.assertRaisesRegex(ValueError,'ERR_OWNED_PAGE_STEP_PENDING'):
            self.host.call('request_cloud_owned_page_close',self.close_args())

    def test_rules_caller_snapshot_cannot_hide_actual_persistent_pause(self):
        run=self.make_run();self.host.actions.set_execution_pause(True,'SYNTHETIC-user','test')
        result=self.host.call('explain_cloud_effective_rules',{'run_id':run['id'],'runtime_values':{'execution_paused':False}})
        self.assertTrue(result['authoritative_execution_control']['paused'])
        self.assertTrue(result['authoritative_execution_control']['write_admission_blocked'])
        self.assertTrue(result['runtime_control_mismatches'][0]['authoritative_value'])
        self.assertFalse(result['execution_authorized'])
        self.assertTrue(self.host.actions.execution_pause_status()['paused'])
