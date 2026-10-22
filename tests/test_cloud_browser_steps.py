"""Offline host integration only; no browser or fabricated platform execution."""
import datetime as dt
import json
import unittest
from unittest import mock
import test_cloud_host
from cloud_pacing import CloudPacing

class BrowserStepHostTests(unittest.TestCase):
 def setUp(self):
  self.f=test_cloud_host.CloudHostTests();self.f.setUp();self.h=self.f.host;self.now=1800000000.0
  self.f.settings['require_browser_steps']=True;self.f.path.write_text(json.dumps(self.f.settings))
  self.h.actions.clock=lambda:self.now
  self.h.pacing=CloudPacing(self.f.path,clock=lambda:self.now,random_uniform=lambda a,b:a)
 def tearDown(self):self.f.tearDown()
 def start(self):
  a=self.h.call('prepare_cloud_action',{'operation':'check_login_status','payload':{},'user_source':'OFFLINE FIXTURE','operation_id':'step-fixture'})
  self.h.call('request_cloud_authorization',{'operation_id':a['operation_id']})
  c=self.h.call('claim_cloud_action',{'operation_id':a['operation_id'],'worker_id':'fixture','browser_context':'fixture-tab','lease_seconds':120})
  self.token=c['claim_token'];self.h.call('begin_cloud_dispatch',{'operation_id':a['operation_id'],'claim_token':self.token});return a
 def step(self,step_id='one',kind='read_observation',**extra):
  return self.h.call('reserve_cloud_browser_step',{'operation_id':'step-fixture','claim_token':self.token,'step_id':step_id,'kind':kind,'target':'fixture observed UI',**extra})
 def complete(self,status='completed',**extra):
  return self.h.call('record_cloud_browser_step',{'operation_id':'step-fixture','claim_token':self.token,'step_id':'one','outcome':status,'evidence':{'fixture':'actual synthetic test call ended'},**extra})
 def report(self,a,status='host_reported_success'):
  current=self.h.actions.get(a['operation_id'])
  r={'operation_id':a['operation_id'],'binding_digest':a['binding_digest'],'attempt_id':current['attempt_id'],'account_id':'a'*24,'browser_context':'fixture-tab','status':status,'observed_at':dt.datetime.fromtimestamp(self.now,dt.timezone.utc).isoformat(),'evidence_ref':'OFFLINE FIXTURE','evidence_kind':'visible_result' if status=='host_reported_success' else 'confirmed_not_applied','details':'Synthetic fixture, not real platform evidence'}
  return self.h.call('record_cloud_result',{'operation_id':a['operation_id'],'claim_token':self.token,'report':r})
 def test_first_step_waits_then_single_call_and_result_gate(self):
  a=self.start()
  with self.assertRaisesRegex(ValueError,'ERR_BROWSER_STEP_EVIDENCE'):self.report(a)
  denied=self.step();self.assertFalse(denied['permitted']);self.now=denied['retry_at']
  self.assertTrue(self.step()['permitted']);self.assertFalse(self.step()['permitted'])
  with self.assertRaisesRegex(ValueError,'ERR_BROWSER_STEP_EVIDENCE'):self.report(a)
  self.complete();self.assertEqual(self.report(a)['status'],'host_reported_success')
  self.assertEqual(self.report(a)['status'],'host_reported_success')
  with self.assertRaisesRegex(ValueError,'ERR_STEP_STATE'):self.step('two')
 def test_compound_targets_do_not_create_more_than_one_permit(self):
  self.start();self.now+=20;self.assertTrue(self.step()['permitted']);self.now+=60
  denied=self.step('two');self.assertFalse(denied['permitted'])
  self.complete();self.assertTrue(self.step('two')['permitted'])
 def test_unknown_requires_fresh_recovery_and_never_reissues(self):
  self.start();self.now+=20;self.step();self.complete('unknown');self.now+=20
  self.assertFalse(self.step('two')['permitted'])
  with self.assertRaises(ValueError):self.step('recovery',recovery_evidence={'call_ended':False})
  evidence={'call_ended':True,'expected_account':'a'*24,'evidence_ref':'OFFLINE FIXTURE','recovery_reason':'inspect ended uncertain call','user_source':'OFFLINE FIXTURE'}
  self.assertTrue(self.step('recovery',recovery_evidence=evidence)['permitted'])
  self.assertEqual(self.h.pacing.get_step('one')['status'],'unknown')
 def test_captcha_completion_persists_block(self):
  self.start();self.now+=20;self.step();self.complete(platform_restriction={'scope':'account','reason':'captcha','evidence':{'fixture':'observed synthetic challenge'}});self.now+=20
  result=self.step('two');self.assertFalse(result['permitted']);self.assertEqual(result['status'],'platform_blocked')
 def test_terminal_parent_can_record_ended_step_without_new_permission(self):
  a=self.start();self.now+=20;self.step()
  with self.assertRaisesRegex(ValueError,'ERR_BROWSER_STEP_EVIDENCE'):self.report(a,'not_applied')
  self.complete();self.report(a,'not_applied')
  self.assertFalse(self.complete()['permitted'])
  with self.assertRaisesRegex(ValueError,'ERR_STEP_STATE'):self.step('two')

 def test_historical_receipt_idempotent_after_step_enforcement_enabled(self):
  self.f.settings['require_browser_steps']=False;self.f.path.write_text(json.dumps(self.f.settings))
  a=self.start();self.report(a)
  self.f.settings['require_browser_steps']=True;self.f.path.write_text(json.dumps(self.f.settings))
  self.assertEqual(self.report(a)['status'],'host_reported_success')
  self.assertEqual(self.h.call('get_cloud_browser_steps',{'operation_id':a['operation_id']})['steps'],[])

 def test_terminal_captcha_can_observe_recovery_without_reopening_action(self):
  a=self.start();self.now+=20;self.step()
  self.complete('not_applied',platform_restriction={'scope':'account','reason':'captcha','evidence':{'fixture':'synthetic challenge'}})
  self.report(a,'not_applied');self.now+=20
  evidence={'call_ended':True,'expected_account':'a'*24,'evidence_ref':'OFFLINE FIXTURE','recovery_reason':'user completed challenge; inspect current view','user_source':'OFFLINE FIXTURE'}
  self.assertTrue(self.step('recovery',recovery_evidence=evidence)['permitted'])
  self.h.call('record_cloud_browser_step',{'operation_id':'step-fixture','claim_token':self.token,'step_id':'recovery','outcome':'completed','evidence':{'fixture':'synthetic current view normal'}})
  self.h.call('clear_cloud_platform_block',{'scope':'account','evidence':{'expected_account':'a'*24,'status':'resolved','evidence_ref':'OFFLINE FIXTURE','observed_at':self.now}})
  self.assertEqual(self.h.actions.get(a['operation_id'])['status'],'not_applied')
  with self.assertRaisesRegex(ValueError,'ERR_STEP_STATE'):self.step('ordinary')
  with self.assertRaisesRegex(ValueError,'actual unresolved'):self.step('unnecessary-recovery',recovery_evidence=evidence)

 def test_old_restriction_receipt_retry_does_not_reblock_after_recovery(self):
  a=self.start();self.now+=20;self.step();self.complete('not_applied')
  # First result fallback persists its block before the action receipt is saved.
  current=self.h.actions.get(a['operation_id'])
  report={'operation_id':a['operation_id'],'binding_digest':a['binding_digest'],'attempt_id':current['attempt_id'],'account_id':'a'*24,'browser_context':'fixture-tab','status':'not_applied','observed_at':dt.datetime.fromtimestamp(self.now,dt.timezone.utc).isoformat(),'evidence_ref':'OFFLINE FIXTURE','evidence_kind':'platform_rejection','details':'Synthetic CAPTCHA fixture'}
  args={'operation_id':a['operation_id'],'claim_token':self.token,'report':report,'platform_restriction':{'scope':'account','reason':'captcha','evidence':{'fixture':'synthetic challenge'}}}
  self.h.call('record_cloud_result',args);self.now+=20
  self.h.call('clear_cloud_platform_block',{'scope':'account','evidence':{'expected_account':'a'*24,'status':'resolved','evidence_ref':'OFFLINE FIXTURE','observed_at':self.now}})
  self.h.call('record_cloud_result',args)
  self.assertEqual(self.h.pacing.db.execute('SELECT COUNT(*) FROM pacing_blocks WHERE active=1').fetchone()[0],0)

 def test_passive_result_observation_can_refresh_stale_identity_without_input(self):
  self.start();self.now+=20
  with mock.patch.object(self.h.actions,'_guard_dispatch',side_effect=ValueError('ERR_IDENTITY_STALE')):
   self.assertTrue(self.step()['permitted'])
   self.complete();self.now+=20
   with self.assertRaisesRegex(ValueError,'ERR_IDENTITY_STALE'):self.step('no-input','click')
  self.assertEqual(self.h.actions.get('step-fixture')['status'],'submitted_unknown')
