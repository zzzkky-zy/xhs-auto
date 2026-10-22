"""Offline host dispatch tests; no actual browser operations."""
import unittest
from unittest import mock
import test_cloud_host
from cloud_host import TOOLS

class RecoveryHostTests(unittest.TestCase):
 def setUp(self):
  self.f=test_cloud_host.CloudHostTests(); self.f.setUp(); self.h=self.f.host
 def tearDown(self):self.f.tearDown()
 def test_helpers_exposed(self):
  names={x['name'] for x in TOOLS}
  self.assertTrue({'begin_cloud_browser_recovery','advance_cloud_browser_recovery','get_cloud_browser_recovery','clear_cloud_recovered_block'}<=names)
 def test_helper_dispatch(self):
  for tool,method,args in [
   ('begin_cloud_browser_recovery','begin',{'operation_id':'fixture','incident_id':'incident','observation':{}}),
   ('advance_cloud_browser_recovery','advance',{'incident_id':'incident','event':'diagnose','observation':{}}),
   ('get_cloud_browser_recovery','get',{'incident_id':'incident'})]:
   with mock.patch('cloud_host.CloudRecovery') as cls:
    getattr(cls.return_value,method).return_value={'fixture':True}
    self.assertEqual(self.h.call(tool,args),{'fixture':True})
    getattr(cls.return_value,method).assert_called_once_with(**args)
 def test_unverified_clear_rejected(self):
  with mock.patch('cloud_host.CloudRecovery') as cls:
   cls.return_value.get.return_value={'state':'challenge'}
   with self.assertRaisesRegex(ValueError,'UNVERIFIED'):
    self.h.call('clear_cloud_recovered_block',{'incident_id':'i','scope':'account'})
 def test_clear_preserves_observation_timestamp(self):
  obs={'evidence_ref':'actual-ui','identity':{'account_id':self.f.settings['expected_user_id'],'observed_at':'2030-01-01T00:00:00Z','evidence_ref':'actual-self'}}
  with mock.patch('cloud_host.CloudRecovery') as cls,mock.patch.object(self.h,'_pacing') as pacing:
   cls.return_value.get.return_value={'state':'verified','observation':obs}
   self.h.call('clear_cloud_recovered_block',{'incident_id':'i','scope':'account'})
   scope,evidence=pacing.return_value.clear_block.call_args.args
   self.assertEqual(scope,'account');self.assertEqual(evidence['observed_at'],obs['identity']['observed_at']);self.assertTrue(evidence['challenge_absent'])
 def test_platform_block_routes_operator_to_recovery(self):
  with mock.patch.object(self.h,'_pacing') as pacing:
   pacing.return_value.report_block.return_value={'status':'platform_blocked'}
   value=self.h.call('report_cloud_platform_block',{'scope':'account','reason':'captcha','evidence':{'fixture':True}})
   self.assertEqual(value['recovery_workflow']['tool'],'begin_cloud_browser_recovery')
   self.assertFalse(value['recovery_workflow']['automatic_retry'])
 def test_home_navigation_requires_incident_and_binds_one_step(self):
  import json
  self.h.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','operation_id':'op'})
  row=dict(self.h.actions._row('op'));row.update(status='submitted_unknown',submitted_at=1,attempt_id='attempt',claim_context='fixture-browser')
  recovery={'call_ended':True,'expected_account':self.f.settings['expected_user_id'],'evidence_ref':'actual-inventory','recovery_reason':'old tab gone','user_source':'fixture','session_recovery':True,'new_browser_context':'fixture-browser','session_evidence_ref':'inventory','prior_session_unavailable':True}
  args={'operation_id':'op','claim_token':'fixture','step_id':'one','kind':'navigation','target':'https://www.xiaohongshu.com/','purpose':'recover_official_session','recovery_evidence':recovery}
  with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h.actions,'_claim'),mock.patch.object(self.h,'_pacing') as pacing,mock.patch('cloud_host.CloudRecovery') as cls:
   with self.assertRaisesRegex(ValueError,'durable recovery incident'):self.h._browser_step(args)
   recovery['incident_id']='incident'
   cls.return_value.get.return_value={'operation_id':'op','new_tab_consumed':True,'state':'new_tab_consumed'}
   pacing.return_value.reserve_step.return_value={'permitted':False,'reason':'waiting'}
   self.h._browser_step(args);self.h._browser_step(args)
   args['step_id']='another'
   with self.assertRaisesRegex(ValueError,'already bound'):self.h._browser_step(args)
   self.assertEqual(pacing.return_value.reserve_step.call_count,2)
