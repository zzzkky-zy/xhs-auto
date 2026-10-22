"""Offline host scope checks, with a synthetic reviewed batch fixture only."""
import hashlib
import json
import unittest
from pathlib import Path
from unittest import mock
import test_cloud_host

class UploadInspectionHostTests(unittest.TestCase):
 def setUp(self):
  self.f=test_cloud_host.CloudHostTests();self.f.setUp();self.h=self.f.host
  self.path=Path(self.f.temp.name)/'fixture.png';self.path.write_bytes(b'OFFLINE IMAGE FIXTURE')
  self.payload={'title':'Fixture','content':'Reviewed fixture body','images':[str(self.path)],'run_id':'fixture-run'}
  self.prepared={'payload':self.payload,'media_manifest':[{'path':str(self.path),'size':self.path.stat().st_size,'sha256':hashlib.sha256(self.path.read_bytes()).hexdigest()}]}
 def tearDown(self):self.f.tearDown()
 def test_preparation_is_distinct_and_does_not_authorize_publish(self):
  batch=mock.Mock();batch.prepare_upload_inspection.return_value=self.prepared
  with mock.patch.object(self.h,'_batch',return_value=batch):
   value=self.h.call('prepare_cloud_upload_inspection',{'run_id':'fixture-run','payload':self.payload,'approval_ref':'OFFLINE FIXTURE','inspection_id':'fixture-review','user_source':'OFFLINE FIXTURE'})
  self.assertEqual(value['binding']['operation'],'inspect_publication_schedule')
  self.assertFalse(value['host_plan']['final_publish_allowed'])
  self.assertEqual(value['status'],'prepared')
  batch.bind_action.assert_not_called()
 def test_inspection_disallows_generic_click_and_submit_purpose(self):
  binding={'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24,'payload':self.payload}
  row={'binding_json':json.dumps(binding),'attempt_id':'fixture','claim_context':'fixture'}
  for kind,purpose in [('click',None),('click','submit_comment'),('click','publish'),('media_request',None),('search',None)]:
   with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h.actions,'_claim'):
    with self.assertRaisesRegex(ValueError,'ERR_INSPECTION_ONLY'):
     self.h.call('reserve_cloud_browser_step',{'operation_id':'fixture','claim_token':'fixture','step_id':'fixture:step','kind':kind,'target':'fixture',**({'purpose':purpose} if purpose else {})})
 def test_dispatch_revalidates_inspection_receipt_and_media(self):
  binding={'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24,'payload':self.payload,'media_manifest':self.prepared['media_manifest'],'user_source':'OFFLINE FIXTURE','target':{'inspection_id':'fixture-review'}}
  batch=mock.Mock();batch.prepare_upload_inspection.return_value={**self.prepared,'payload':{**self.payload,'content':'changed'}}
  with mock.patch.object(self.h,'_batch',return_value=batch):
   with self.assertRaisesRegex(ValueError,'ERR_INSPECTION_CHANGED'):self.h._verify_dispatch_review('claim',binding,{})
 def test_renewal_rejects_completed_or_uncertain_upload_before_ledger_change(self):
  row={'binding_json':json.dumps({'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24}),'attempt_id':'fixture'}
  args={'operation_id':'fixture','claim_token':'fixture','lease_seconds':600,'observation':{}}
  for status in ['completed','unknown','in_flight']:
   pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'kind':'media_request','status':status}]
   with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h,'_pacing',return_value=pacing):
    with self.assertRaisesRegex(ValueError,'ERR_INSPECTION_RENEWAL'):self.h.call('renew_cloud_upload_inspection_claim',args)
 def test_renewal_revalidates_review_then_preserves_same_attempt(self):
  row={'binding_json':json.dumps({'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24}),'attempt_id':'fixture'}
  args={'operation_id':'fixture','claim_token':'fixture','lease_seconds':600,'observation':{'fixture':'not real evidence'}}
  pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'kind':'media_request','status':'not_applied'}]
  with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h,'_pacing',return_value=pacing),mock.patch.object(self.h,'_verify_dispatch_review') as review,mock.patch.object(self.h.actions,'renew_inspection_claim',return_value={'attempt_id':'fixture'},create=True) as renew:
   self.assertEqual(self.h.call('renew_cloud_upload_inspection_claim',args),{'attempt_id':'fixture'})
   review.assert_called_once();renew.assert_called_once_with(**args)

 def test_controls_renewal_requires_completed_upload_and_no_inflight(self):
  row={'binding_json':json.dumps({'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24}),'attempt_id':'fixture'}
  args={'operation_id':'fixture','claim_token':'fixture','lease_seconds':600,'observation':{'upload_invoked':True}}
  for status in ['not_applied','unknown','in_flight']:
   pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'kind':'media_request','status':status}]
   with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h,'_pacing',return_value=pacing):
    with self.assertRaisesRegex(ValueError,'ERR_INSPECTION_RENEWAL'):self.h.call('renew_cloud_schedule_controls_claim',args)
  pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'kind':'media_request','status':'completed'}]
  with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h,'_pacing',return_value=pacing),mock.patch.object(self.h,'_verify_dispatch_review'),mock.patch.object(self.h.actions,'renew_inspection_claim',return_value={'ok':True}) as renew:
   self.assertEqual(self.h.call('renew_cloud_schedule_controls_claim',args),{'ok':True})
   renew.assert_called_once_with(**args,after_upload=True)
 def test_uploaded_inspection_cannot_navigate_reupload_or_reopen_chooser(self):
  row={'binding_json':json.dumps({'operation':'inspect_publication_schedule','mutation':True,'expected_account':'a'*24}),'attempt_id':'fixture'}
  pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'kind':'media_request','status':'completed'}]
  for kind,purpose in [('navigation','open_creator'),('media_request','upload_reviewed_media'),('click','open_upload_chooser')]:
   with mock.patch.object(self.h.actions,'_row',return_value=row),mock.patch.object(self.h.actions,'_claim'),mock.patch.object(self.h,'_pacing',return_value=pacing):
    with self.assertRaisesRegex(ValueError,'ERR_INSPECTION_ONLY'):
     self.h.call('reserve_cloud_browser_step',{'operation_id':'fixture','claim_token':'fixture','step_id':'fixture:blocked','kind':kind,'purpose':purpose,'target':'fixture'})
