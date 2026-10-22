"""Synthetic editor observations only; no live UI or publication claims."""
import copy
import datetime as dt
import unittest
from unittest import mock
import test_cloud_host
from cloud_comment_preflight import validate_preflight

class CommentPreflightTests(unittest.TestCase):
 def setUp(self):
  self.now=1800000000.0
  self.binding={'operation':'post_comment_to_feed','mutation':True,'expected_account':'fixture-account','payload':{'content':'Exact reviewed fixture text'}}
  self.report={'expected_account':'fixture-account','browser_context':'fixture-browser','observed_at':dt.datetime.fromtimestamp(self.now,dt.timezone.utc).isoformat(),'evidence_ref':'OFFLINE SYNTHETIC FIXTURE','browser_call_succeeded':True,'editor_expanded':True,'editor_visible':True,'visible_text':'Exact reviewed fixture text','send_enabled':True}
 def check(self,**changes):
  return validate_preflight(self.binding,'fixture-browser',{**self.report,**changes},self.now)
 def test_exact_expanded_editor_is_host_report_only(self):
  self.assertFalse(self.check()['browser_state_independently_verified'])
 def test_collapsed_strip_text_is_not_send_ready(self):
  with self.assertRaisesRegex(ValueError,'expanded'):self.check(editor_expanded=False)
 def test_expansion_clearing_text_is_rejected(self):
  with self.assertRaisesRegex(ValueError,'differs'):self.check(visible_text='')
 def test_partial_or_changed_reviewed_text_rejected(self):
  for text in ['Exact reviewed fixture','Exact reviewed fixture text ','Different content']:
   with self.assertRaises(ValueError):self.check(visible_text=text)
 def test_hidden_editor_disabled_send_and_tool_error_rejected(self):
  for key in ['editor_visible','send_enabled','browser_call_succeeded']:
   with self.assertRaises(ValueError):self.check(**{key:False})
 def test_truthy_strings_do_not_count_as_observations(self):
  with self.assertRaises(ValueError):self.check(send_enabled='true')
 def test_account_and_context_bound(self):
  for key in ['expected_account','browser_context']:
   with self.assertRaises(ValueError):self.check(**{key:'other'})
 def test_old_or_future_observation_rejected(self):
  for offset in [-121,6]:
   with self.assertRaises(ValueError):self.check(observed_at=dt.datetime.fromtimestamp(self.now+offset,dt.timezone.utc).isoformat())
 def test_missing_or_fabricated_extra_fields_rejected(self):
  with self.assertRaises(ValueError):self.check(approved=True)
  with self.assertRaises(ValueError):self.check(evidence_ref='')
 def test_read_action_cannot_use_preflight(self):
  self.binding['operation']='get_feed_detail'
  with self.assertRaises(ValueError):self.check()
 def test_reply_uses_same_exact_editor_gate(self):
  self.binding['operation']='reply_comment_in_feed';self.check()
 def test_pure_validator_never_operates_browser(self):
  with mock.patch('subprocess.run',side_effect=AssertionError('No execution')),mock.patch('socket.socket',side_effect=AssertionError('No network')):self.check()

class PreflightHostTests(unittest.TestCase):
 def setUp(self):
  self.f=test_cloud_host.CloudHostTests();self.f.setUp();self.h=self.f.host
 def tearDown(self):self.f.tearDown()
 def test_helper_is_exposed_with_strict_arguments(self):
  with self.assertRaisesRegex(ValueError,'invalid tool arguments'):
   self.h.call('record_cloud_comment_preflight',{'operation_id':'fixture','claim_token':'fixture','report':{},'approved':True})
 def test_send_purpose_requires_record_before_reserving(self):
  import json
  row={'binding_json':json.dumps({'operation':'post_comment_to_feed','mutation':True,'expected_account':'a'*24,'payload':{'content':'fixture'}}),'attempt_id':'fixture','binding_digest':'fixture','claim_context':'fixture'}
  args={'operation_id':'fixture','claim_token':'fixture','step_id':'fixture:send','kind':'click','target':'visible Send','purpose':'submit_comment'}
  with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h.actions,'_claim'):
   with self.assertRaisesRegex(ValueError,'record actual editor preflight'):self.h.call('reserve_cloud_browser_step',args)
