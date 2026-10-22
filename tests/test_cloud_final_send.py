"""Offline temporary ledgers: one-use permission, not real browser acceptance."""
import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from cloud_host import CloudHost

class FinalSendTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.config=Path(self.tmp.name)/'config.json'
  self.config.write_text(json.dumps({'schema_version':1,'browser_mode':'cloud_host','account_id':'fixture','state_dir':'state','allow_write':False,'allow_reconcile':False,'expected_user_id':'a'*24}))
  self.host=CloudHost(self.config)
  self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','operation_id':'action'})
  self.operation='action'
  self.row={'attempt_id':'attempt','binding_digest':'digest'}
 def tearDown(self):self.host.close();self.tmp.cleanup()
 def args(self,purpose='submit_comment',step='send'):
  return {'operation_id':self.operation,'step_id':step,'purpose':purpose,'kind':'click','target':'SYNTHETIC final button'}
 def reserve(self,purpose='submit_comment',step='send'):
  return self.host._reserve_final_send(self.row,self.args(purpose,step))
 def test_same_and_changed_step_never_reissued_after_successful_permit(self):
  for purpose in ('submit_comment','submit_publication'):
   self.row['attempt_id']=purpose
   self.operation=purpose
   self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','operation_id':purpose})
   fake=mock.Mock();fake.reserve_step.return_value={'permitted':True,'status':'reserved'}
   with mock.patch.object(self.host,'_pacing',return_value=fake):
    self.reserve(purpose)
    for step in ('send','different'):
     with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve(purpose,step)
    self.assertEqual(fake.reserve_step.call_count,1)
 def test_restart_does_not_reissue_after_pacing_commit_reply_lost(self):
  for purpose in ('submit_comment','submit_publication'):
   self.row['attempt_id']=purpose
   self.operation=purpose
   self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','operation_id':purpose})
   fake=mock.Mock();fake.reserve_step.side_effect=TimeoutError('synthetic committed but response lost')
   with mock.patch.object(self.host,'_pacing',return_value=fake):
    with self.assertRaises(TimeoutError):self.reserve(purpose)
   self.host.close();self.host=CloudHost(self.config)
   with mock.patch.object(self.host,'_pacing',side_effect=AssertionError('must not call pacing')):
    with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve(purpose,'new-after-restart')
 def test_failure_before_pacing_is_conservative_not_success(self):
  with mock.patch.object(self.host,'_pacing',side_effect=RuntimeError('cross-store boundary')):
   with self.assertRaises(RuntimeError):self.reserve()
  with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve(step='new')
  self.assertEqual(self.host.actions.get('action')['status'],'prepared')
 def test_explicit_wait_without_permit_allows_later_attempt(self):
  fake=mock.Mock();fake.reserve_step.side_effect=[{'permitted':False,'status':'wait','retry_at':123},{'permitted':True,'status':'reserved'}]
  with mock.patch.object(self.host,'_pacing',return_value=fake):
   self.assertFalse(self.reserve()['permitted']);self.assertTrue(self.reserve()['permitted'])
 def test_already_reserved_is_not_a_release(self):
  fake=mock.Mock();fake.reserve_step.return_value={'permitted':False,'status':'already_reserved'}
  with mock.patch.object(self.host,'_pacing',return_value=fake):
   self.reserve()
   with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve(step='different')
 def test_publication_audit_write_failure_does_not_release(self):
  original=self.host.actions._event
  def event(operation_id,name,detail):
   if name=='publication_final_step_permitted':raise RuntimeError('synthetic after pacing commit')
   return original(operation_id,name,detail)
  fake=mock.Mock();fake.reserve_step.return_value={'permitted':True,'status':'reserved'}
  with mock.patch.object(self.host,'_pacing',return_value=fake),mock.patch.object(self.host.actions,'_event',side_effect=event):
   with self.assertRaises(RuntimeError):self.reserve('submit_publication')
  with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve('submit_publication','second')

 def test_changed_attempt_cannot_wash_consumed_send(self):
  fake=mock.Mock();fake.reserve_step.return_value={'permitted':True}
  with mock.patch.object(self.host,'_pacing',return_value=fake):
   self.reserve('submit_publication')
   self.row['attempt_id']='invented-new-attempt'
   with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):self.reserve('submit_publication','other')
  self.assertEqual(fake.reserve_step.call_count,1)
