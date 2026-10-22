"""Real local six-post/three-cover review dispatch, synthetic pixels only."""
import datetime as dt
import json
import unittest
import test_future_content_policy as fixture
from cloud_host import CloudHost

class NestedCoverDispatchTests(unittest.TestCase):
 def setUp(self):
  self.f=fixture.FutureContentPolicyTests();self.f.setUp()
 def tearDown(self):self.f.tearDown()
 def test_six_three_review_prepare_claim_begin_reenters_cover_history(self):
  f=self.f;profile,run,items=f.future_items(real_cover_reviews=True)
  review=f.batch.review_upload_inspection(run['id'],items,'SYNTHETIC full batch review')
  f.data['allow_write']=True;f.path.write_text(json.dumps(f.data))
  host=CloudHost(f.path);host.actions.close();host.actions=f.actions;host.batch=f.batch
  f.actions.host_verifier=host._verify_dispatch_review
  a=host.call('prepare_cloud_upload_inspection',{'run_id':run['id'],'payload':items[0]['payload'],
   'inspection_id':review['inspection_id'],'approval_ref':'SYNTHETIC','user_source':'SYNTHETIC','operation_id':'six-cover-fixture'})
  stamp=dt.datetime.fromtimestamp(f.now[0],dt.timezone.utc).isoformat()
  expires=dt.datetime.fromtimestamp(f.now[0]+600,dt.timezone.utc).isoformat()
  host.call('request_cloud_authorization',{'operation_id':a['operation_id']})
  host.call('record_cloud_authorization',{'operation_id':a['operation_id'],'attestation':{
   'operation_id':a['operation_id'],'binding_digest':a['binding_digest'],'expected_account':f.data['expected_user_id'],
   'user_source':'SYNTHETIC','user_quote':'SYNTHETIC never publish','confirmed_at':stamp,'expires_at':expires,'evidence_ref':'SYNTHETIC'}})
  host.call('record_cloud_identity',{'operation_id':a['operation_id'],'observation':{'status':'observed','account_id':f.data['expected_user_id'],'observed_at':stamp,'evidence_ref':'SYNTHETIC','browser_context':'synthetic-tab'}})
  c=host.call('claim_cloud_action',{'operation_id':a['operation_id'],'worker_id':'fixture','browser_context':'synthetic-tab'})
  self.assertEqual(c['status'],'dispatching')
  r=host.call('begin_cloud_dispatch',{'operation_id':a['operation_id'],'claim_token':c['claim_token']})
  self.assertEqual(r['status'],'submitted_unknown');self.assertEqual(r['attempt'],1)
  self.assertEqual(f.actions.db.execute('SELECT count(*) FROM cover_visual_reviews').fetchone()[0],3)
  self.assertFalse(f.actions.db.in_transaction)
  if host.pacing:host.pacing.close()
 def test_nested_savepoint_failure_keeps_outer_transaction(self):
  a=self.f.actions
  with a._transaction():
   a.db.execute('CREATE TABLE transaction_fixture (value TEXT)')
   a.db.execute("INSERT INTO transaction_fixture VALUES ('outer')")
   with self.assertRaisesRegex(ValueError,'inner'):
    with a._transaction():
     a.db.execute("INSERT INTO transaction_fixture VALUES ('inner')")
     raise ValueError('inner')
   self.assertTrue(a.db.in_transaction)
  self.assertEqual([r[0] for r in a.db.execute('SELECT value FROM transaction_fixture')],['outer'])
 def test_outer_failure_rolls_back_nested_success(self):
  a=self.f.actions
  a.db.execute('CREATE TABLE transaction_fixture (value TEXT)')
  with self.assertRaisesRegex(ValueError,'outer'):
   with a._transaction():
    with a._transaction():a.db.execute("INSERT INTO transaction_fixture VALUES ('nested')")
    raise ValueError('outer')
  self.assertEqual(a.db.execute('SELECT count(*) FROM transaction_fixture').fetchone()[0],0)
