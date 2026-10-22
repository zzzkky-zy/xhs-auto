"""Offline same-page diagnostic contract; no browser faults are triggered."""
import json
import unittest
from unittest import mock
import test_cloud_recovery
import test_cloud_browser_steps
from cloud_recovery import CloudRecovery

class ReloadIncidentTests(test_cloud_recovery.RecoveryTests):
 def setUp(self):
  super().setUp()
  self.actions.prepare(operation='check_login_status',payload={},expected_account='expected-user',
   target={'url':'https://www.xiaohongshu.com/explore'},user_source='fixture',operation_id='read')
  self.actions.request_authorization('read')
  self.claim=self.actions.claim('read',worker_id='fixture',browser_context='same-session',lease_seconds=120)
  self.actions.begin_submit('read',self.claim['claim_token'])
 def begin_reload(self,**kwargs):
  self.recovery.begin('read','reload-incident',self.obs(control_available=True,challenge_active=True))
  return self.recovery.advance('reload-incident','request_page_reload',self.obs(control_available=True,challenge_active=True,
   same_page={'url':'https://www.xiaohongshu.com/explore','tab_id':'observed-tab','browser_context':'same-session'},**kwargs))
 def test_reload_one_use_wait_and_preserves_action(self):
  before=self.actions.get('read')['status'];r=self.begin_reload()
  self.assertTrue(r['dispatch_once']);self.assertEqual(r['reload_not_before'],self.now+5)
  self.assertEqual(self.actions.get('read')['status'],before)
  self.assertIn('challenge_active',r['restriction_latches'])
  self.assertFalse(CloudRecovery(self.actions).get('reload-incident')['dispatch_once'])
  with self.assertRaisesRegex(ValueError,'already consumed'):
   self.recovery.advance('reload-incident','request_page_reload',r['observation'])
  with self.assertRaisesRegex(ValueError,'unresolved incident'):
   self.recovery.begin('read','another-id',self.obs())
 def test_reload_rejects_other_route_context_and_rate(self):
  self.recovery.begin('read','reload-incident',self.obs(control_available=True,challenge_active=True))
  for page in [{'url':'https://www.xiaohongshu.com/other','tab_id':'observed-tab','browser_context':'same-session'},
               {'url':'https://www.xiaohongshu.com/explore','tab_id':'observed-tab','browser_context':'other-session'}]:
   with self.assertRaises(ValueError):self.recovery.advance('reload-incident','request_page_reload',self.obs(control_available=True,challenge_active=True,same_page=page))
  with self.assertRaises(ValueError):self.recovery.advance('reload-incident','request_page_reload',self.obs(control_available=True,challenge_active=True,rate_limited=True))
 def test_write_scene_never_reload(self):
  self.actions.prepare(operation='like_feed',payload={'feed_id':'b'*24},expected_account='expected-user',
   target={'url':'https://www.xiaohongshu.com/explore/'+'b'*24},user_source='fixture',operation_id='write')
  self.recovery.begin('write','write-incident',self.obs(control_available=True,challenge_active=True))
  with self.assertRaisesRegex(ValueError,'forbidden for writes'):
   self.recovery.advance('write-incident','request_page_reload',self.obs(control_available=True,challenge_active=True))

 def test_failed_reload_then_single_challenge_then_handoff(self):
  self.begin_reload();self.now+=10
  o=self.obs(control_available=True,challenge_active=True)
  r=self.recovery.advance('reload-incident','page_reload_result',o)
  self.assertEqual(r['next_host_action'],'solve_challenge_in_normal_authorized_ui')
  r=self.recovery.advance('reload-incident','request_challenge',o);self.assertTrue(r['dispatch_once'])
  r=self.recovery.advance('reload-incident','challenge_result',o);self.assertEqual(r['state'],'handoff')
  with self.assertRaises(ValueError):self.recovery.advance('reload-incident','request_challenge',o)

class ReloadHostTests(test_cloud_browser_steps.BrowserStepHostTests):
 def test_live_bound_reload_gate_waits_and_never_issues_twice(self):
  self.start();self.now+=20
  self.h._pacing().report_block('account','captcha',{'ref':'offline observed captcha'})
  from cloud_actions import _iso
  o={'observed_at':_iso(self.now),'evidence_ref':'offline','call_ended':True,'control_available':True,'tab_present':True,'challenge_active':True,'rate_limited':False,'permission_denied':False}
  self.h.call('begin_cloud_browser_recovery',{'operation_id':'step-fixture','incident_id':'reload','observation':o})
  url=self.h.actions.get('step-fixture')['binding']['target']['url']
  r=self.h.call('advance_cloud_browser_recovery',{'incident_id':'reload','event':'request_page_reload','observation':{**o,'same_page':{'url':url,'tab_id':'tab-fixture','browser_context':'fixture-tab'}}})
  evidence={'incident_id':'reload','tab_id':'tab-fixture','page_reload':True,'expected_account':'a'*24,'evidence_ref':'offline','user_source':'fixture','call_ended':True,'recovery_reason':'one same page diagnostic'}
  self.assertEqual(self.step('reload-step','reload',target=url,recovery_evidence=evidence)['status'],'wait_before_reload')
  self.now+=10
  self.assertTrue(self.step('reload-step','reload',target=url,recovery_evidence=evidence)['permitted'])
  self.assertFalse(self.step('reload-step','reload',target=url,recovery_evidence=evidence)['permitted'])
  with self.assertRaisesRegex(ValueError,'already bound'):self.step('other-id','reload',target=url,recovery_evidence=evidence)
