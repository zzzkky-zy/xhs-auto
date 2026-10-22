"""Offline adapters; synthetic source claims never assert live verification."""
import datetime as dt
import hashlib
import json
import sys
from pathlib import Path
from unittest import mock
import unittest
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
import test_cloud_host_round2 as fixtures
from cloud_comment_boundary import CloudCommentBoundary
from cloud_actions import _iso
from cloud_host import CloudHost, TOOLS

class UnifiedHostTests(unittest.TestCase):
 setUp=fixtures.RoundTwoHostTests.setUp
 tearDown=fixtures.RoundTwoHostTests.tearDown
 action=fixtures.RoundTwoHostTests.action
 def test_cold_repair_does_not_create_batch(self):
  before=set(self.base.rglob('*'))
  with self.assertRaisesRegex(ValueError,'BATCH_NOT_OPEN'):
   self.host.call('repair_cloud_batch_projection',{'run_id':'missing'})
  self.assertEqual(before,set(self.base.rglob('*')))
 def test_pressure_is_unknown_not_zero_and_no_arbitrary_override(self):
  result=self.host.call('cloud_owned_resource_pressure',{})
  self.assertIsNone(result['retained_count']);self.assertIsNone(result['admit_new_owned_page'])
  with self.assertRaisesRegex(ValueError,'invalid tool arguments'):
   self.host.call('cloud_owned_resource_pressure',{'max_owned_open_pages':100})
 def test_notifications_stable_event_once_and_reopen(self):
  self.now=dt.datetime(2026,10,20,4,tzinfo=dt.timezone.utc).timestamp()
  args={'event':{'target_id':'SYNTHETIC-target','reason_code':'completed','state_version':'receipt-v1'},'kind':'completed','evidence_ref':'SYNTHETIC'}
  result=self.host.call('decide_cloud_notification',args);ident=result['notification_id']
  self.assertTrue(self.host.call('consume_cloud_notification',{'notification_id':ident})['send_once'])
  self.host.close();self.host=CloudHost(self.path);self.host.actions.clock=lambda:self.now
  self.assertFalse(self.host.call('consume_cloud_notification',{'notification_id':ident})['send_once'])
  self.assertEqual(self.host.call('decide_cloud_notification',{**args,'evidence_ref':'different ref'})['notification_id'],ident)
  self.assertIsInstance(self.host.call('list_cloud_operator_notifications',{})['notifications'],list)
 def test_urgent_requires_evidence_and_caller_cannot_supply_fingerprint(self):
  args={'event':{'target_id':'x','reason_code':'blocked','state_version':'v1'},'kind':'blocker','evidence_ref':'SYNTHETIC','urgent':True}
  with self.assertRaises(ValueError):self.host.call('decide_cloud_notification',args)
  with self.assertRaisesRegex(ValueError,'invalid tool arguments'):
   self.host.call('decide_cloud_notification',{'fingerprint':'invented','kind':'blocker','evidence_ref':'x'})
 def comment(self):
  self.now=dt.datetime(2026,10,20,4,tzinfo=dt.timezone.utc).timestamp()
  return self.host.actions.prepare('post_comment_to_feed',{'feed_id':'b'*24,'xsec_token':'SYNTHETIC','content':'SYNTHETIC','run_id':'run'},expected_account='a'*24,target={'feed_id':'b'*24},user_source='SYNTHETIC',operation_id='comment')
 def evidence(self,ident,**changes):
  return dict(evidence_id=ident,expected_account='a'*24,source_ref='SYNTHETIC-message',source_quote='SYNTHETIC instruction',source_read_evidence_ref='SYNTHETIC-read',observed_at=_iso(self.now),**changes)
 def test_default_host_verifier_is_not_independent_daily_authentication(self):
  self.comment()
  self.host.call('record_cloud_comment_boundary',{'operation_id':'comment','kind':'history_gap','evidence':self.evidence('gap',first_date=None,last_date=None)})
  evidence=self.evidence('coverage',date='2026-10-20',timezone='Asia/Shanghai',expires_at='2026-10-20T16:00:00Z',coverage_scope='account_activity_upper_bound',total_upper_bound=0,undated_risk_accounted=True)
  result=self.host.call('record_cloud_comment_boundary',{'operation_id':'comment','kind':'daily_coverage','evidence':evidence})
  self.assertFalse(result['independently_verified'])
  self.assertIsNone(self.host.call('cloud_comment_boundary_status',{})['current_day_upper_bound'])
  with self.assertRaisesRegex(ValueError,'COVERAGE_REQUIRED'):CloudCommentBoundary(self.host.actions).guard('comment')
 def test_explicit_limited_mode_activates_gate_without_50_flag(self):
  self.settings['cloud_comment_authorization_mode']='limited_daily';self.path.write_text(json.dumps(self.settings))
  self.assertTrue(self.host.actions.comment_boundary_enabled())
 def read_step(self,ended=True):
  self.action('list_feeds')
  token='SYNTHETIC-token'
  self.host.actions.db.execute("UPDATE actions SET status='submitted_unknown',attempt_id='attempt',claim_context='ctx',claim_hash=?,submitted_at=? WHERE operation_id='op'",(hashlib.sha256(token.encode()).hexdigest(),self.now))
  pacing=self.host._pacing();pacing.clock=lambda:self.now;pacing.random_uniform=lambda a,b:a
  pacing.reserve('list_feeds','attempt');self.now+=20
  self.assertTrue(pacing.reserve_step('attempt','read','read_observation','SYNTHETIC page')['permitted'])
  self.host.call('record_cloud_browser_step',{'operation_id':'op','claim_token':token,'step_id':'read','outcome':'unknown','evidence':{'call_ended':ended,'challenge_active':False,'rate_limited':False,'permission_denied':False}})
  row=self.host.actions._row('op')
  return {'operation_id':'op','snapshot':{'attempt_id':row['attempt_id'],'binding_digest':row['binding_digest']}}
 def test_abandon_adapter_uses_persisted_supported_step_evidence(self):
  incident=self.read_step()
  before=self.host.pacing.db.total_changes
  with mock.patch('cloud_host.CloudRecovery') as cls:
   cls.return_value.get.return_value=incident;cls.return_value.advance.return_value={'state':'abandoned_read_only'}
   self.assertEqual(self.host.call('advance_cloud_browser_recovery',{'incident_id':'incident','event':'abandon_read_only','observation':{}})['state'],'abandoned_read_only')
  self.assertEqual(before,self.host.pacing.db.total_changes)
  self.assertEqual(self.host.pacing.get_step('read')['status'],'unknown')
 def test_abandon_rejects_unended_old_attempt_and_real_restrictions(self):
  incident=self.read_step(False)
  with mock.patch('cloud_host.CloudRecovery') as cls:
   cls.return_value.get.return_value=incident
   with self.assertRaisesRegex(ValueError,'persisted ended'):self.host._check_read_abandon_pacing('incident')
   incident['snapshot']['attempt_id']='other'
   with self.assertRaisesRegex(ValueError,'original incident'):self.host._check_read_abandon_pacing('incident')
 def test_abandon_missing_store_does_not_create_one(self):
  self.action('list_feeds');row=self.host.actions._row('op')
  with mock.patch('cloud_host.CloudRecovery') as cls:
   cls.return_value.get.return_value={'operation_id':'op','snapshot':{'attempt_id':row['attempt_id'],'binding_digest':row['binding_digest']}}
   with self.assertRaisesRegex(ValueError,'existing pacing'):self.host._check_read_abandon_pacing('incident')
  self.assertFalse((self.host.actions.directory/'cloud-pacing.sqlite3').exists())
 def test_all_new_helpers_are_exposed(self):
  names={t['name'] for t in TOOLS}
  self.assertTrue({'repair_cloud_batch_projection','cloud_owned_resource_pressure','record_cloud_publication_topic_expectations','record_cloud_comment_boundary','cloud_comment_boundary_status','decide_cloud_notification','consume_cloud_notification','record_cloud_notification_result','list_cloud_operator_notifications'}<=names)
