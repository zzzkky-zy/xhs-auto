"""Offline future-only host preflight dispatch; no browser action."""
import json
import unittest
from unittest import mock
import test_cloud_host
import test_cloud_publication_preflight
class PublicationHostTests(unittest.TestCase):
 def setUp(self):
  self.f=test_cloud_host.CloudHostTests();self.f.setUp();self.h=self.f.host
  self.p=test_cloud_publication_preflight.PublicationPreflightTests();self.p.setUp()
  self.p.binding.update(mutation=True,expected_account='a'*24)
  self.p.report['expected_account']='a'*24
  self.h.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','operation_id':'op'})
  self.row=dict(self.h.actions._row('op'));self.row.update(status='submitted_unknown',submitted_at=1,attempt_id='attempt',claim_context='browser',binding_json=json.dumps(self.p.binding))
  self.h.actions._event('op','final_send_protocol_started',{'attempt_id':'attempt','version':1})  # synthetic already-started new-protocol fixture
 def tearDown(self):self.f.tearDown()
 def test_old_run_does_not_require_new_preflight(self):
  with mock.patch.object(self.h.actions,'_row',return_value=self.row),mock.patch.object(self.h,'_future_publication_policy',return_value=None):
   with self.assertRaisesRegex(ValueError,'historical runs unchanged'):
    self.h.call('record_cloud_publication_preflight',{'operation_id':'op','claim_token':'token','report':self.p.report})
 def test_final_click_requires_bound_fresh_report(self):
  args={'operation_id':'op','claim_token':'token','step_id':'publish-once','kind':'click','target':'actual Publish button','purpose':'submit_publication'}
  with mock.patch.object(self.h.actions,'_row',return_value=self.row),mock.patch.object(self.h.actions,'_claim'),mock.patch.object(self.h.actions,'_guard_dispatch'),mock.patch.object(self.h,'_verify_dispatch_review'),mock.patch.object(self.h,'_future_publication_policy',return_value={'version':1}),mock.patch.object(self.h,'_publication_topic_expectations',return_value=test_cloud_publication_preflight.fixture_expectations(self.p.binding)),mock.patch.object(self.h.actions,'clock',return_value=self.p.now),mock.patch.object(self.h,'_pacing') as pacing:
   with self.assertRaisesRegex(ValueError,'record actual'):self.h._browser_step(args)
   value=self.h.call('record_cloud_publication_preflight',{'operation_id':'op','claim_token':'token','report':self.p.report})
   self.assertEqual(value['status'],'validated_host_report')
   pacing.return_value.reserve_step.return_value={'permitted':True}
   self.assertTrue(self.h._browser_step(args)['permitted'])
   self.row['attempt_id']='different'
   with self.assertRaisesRegex(ValueError,'stale action binding'):self.h._browser_step(args)

 def test_topic_baseline_retained_for_exact_binding_and_cannot_be_replaced(self):
  self.p.binding.update(operation_id='op')
  self.p.binding['payload']['run_id']='SYNTHETIC-run'
  self.row['binding_json']=json.dumps(self.p.binding)
  expectations=test_cloud_publication_preflight.fixture_expectations(self.p.binding)
  batch=mock.Mock();batch.continuous.run.return_value={'body':{'content_profile_version':'SYNTHETIC-version'}}
  batch.continuous.content.profile_for_run.return_value=('SYNTHETIC-version',{},[])
  with mock.patch.object(self.h.actions,'_row',return_value=self.row),mock.patch.object(self.h,'_batch',return_value=batch),mock.patch.object(self.h,'_future_publication_policy',return_value={'version':1}):
   self.assertIsNone(self.h._publication_topic_expectations(self.p.binding))
   result=self.h.call('record_cloud_publication_topic_expectations',{'operation_id':'op','expectations':expectations})
   self.assertFalse(result['browser_state_independently_verified'])
   self.assertEqual(self.h._publication_topic_expectations(self.p.binding),expectations)
   changed={**expectations,'template_topics':['different actual list']}
   with self.assertRaisesRegex(ValueError,'silently replaced'):
    self.h.call('record_cloud_publication_topic_expectations',{'operation_id':'op','expectations':changed})
   self.row['binding_digest']='changed-settings'
   self.assertIsNone(self.h._publication_topic_expectations(self.p.binding))
 def test_terminal_publication_topic_evidence_not_rewritten(self):
  for status in ('host_reported_success','not_applied','cancelled'):
   self.row['status']=status
   with mock.patch.object(self.h.actions,'_row',return_value=self.row),mock.patch.object(self.h,'_future_publication_policy',return_value={'version':1}):
    with self.assertRaisesRegex(ValueError,'historical terminal'):
     self.h.call('record_cloud_publication_topic_expectations',{'operation_id':'op','expectations':test_cloud_publication_preflight.fixture_expectations(self.p.binding)})
