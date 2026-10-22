"""Upgrade fixtures in temporary ledgers only; never migrate live unknowns."""
import json
import hashlib
import unittest
from unittest import mock
import test_cloud_actions as fixtures
from cloud_host import CloudHost

class MigrationTests(unittest.TestCase):
 setUp=fixtures.CloudActionTests.setUp
 tearDown=fixtures.CloudActionTests.tearDown
 save_config=fixtures.CloudActionTests.save_config
 stamp=fixtures.CloudActionTests.stamp
 prepare=fixtures.CloudActionTests.prepare
 attestation=fixtures.CloudActionTests.attestation
 identity=fixtures.CloudActionTests.identity
 ready=fixtures.CloudActionTests.ready
 enabled=fixtures.CloudActionTests.enabled
 claimed=fixtures.CloudActionTests.claimed
 submitted=fixtures.CloudActionTests.submitted
 result=fixtures.CloudActionTests.result
 def test_previously_authorized_not_begun_gets_protocol_at_real_begin(self):
  claim=self.claimed()
  self.assertFalse(self.ledger.db.execute("SELECT 1 FROM action_events WHERE event='final_send_protocol_started'").fetchone())
  self.ledger.begin_submit('action-1',claim['claim_token'])
  row=self.ledger._row('action-1');self.ledger.guard_final_send_protocol(row)
  saved=json.loads(self.ledger.db.execute("SELECT detail FROM action_events WHERE event='final_send_protocol_started'").fetchone()[0])
  self.assertEqual(saved,{'attempt_id':claim['attempt_id'],'version':1})
 def test_old_active_comment_no_marker_cannot_gain_send_from_new_preflight(self):
  claim=self.claimed()
  # Faithful legacy boundary: old code persisted unknown, but no new marker.
  self.ledger.db.execute("UPDATE actions SET status='submitted_unknown',submitted_at=? WHERE operation_id='action-1'",(self.time,))
  self.ledger._event('action-1','submission_boundary_entered',{'attempt_id':claim['attempt_id']})
  self.ledger._event('action-1','comment_editor_preflight',{'attempt_id':claim['attempt_id'],'report':{'fixture':'new report cannot prove no prior Send'}})
  with self.assertRaisesRegex(ValueError,'FINAL_SEND_LEGACY_UNKNOWN'):
   self.ledger._guard_dispatch(self.ledger._row('action-1'))
  # Actual read-only retrieval and late result recording are still available.
  self.assertEqual(self.ledger.get('action-1')['status'],'submitted_unknown')
  self.ledger.report_result('action-1',claim['claim_token'],self.result(claim))
  self.assertEqual(self.ledger.get('action-1')['status'],'host_reported_success')
 def test_old_publication_final_permit_blocks_without_new_consumption(self):
  self.prepare()
  self.ledger._event('action-1','publication_final_step_permitted',{'step_id':'old-final','attempt_id':'different-old-attempt'})
  host=CloudHost(self.config);host.actions.close();host.actions=self.ledger
  try:
   with mock.patch.object(host,'_pacing',side_effect=AssertionError('must not reserve')):
    with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):
     host._reserve_final_send({'attempt_id':'old-attempt','binding_digest':'fixture'},{'operation_id':'action-1','step_id':'new-final','purpose':'submit_publication','kind':'click','target':'SYNTHETIC Publish'})
  finally:host.actions=None

 def publication_claim(self):
  image=self.base/'SYNTHETIC.png';image.write_bytes(b'OFFLINE SYNTHETIC IMAGE FIXTURE')
  return self.claimed(operation='publish_content',payload={'title':'SYNTHETIC','content':'SYNTHETIC','images':[str(image)]},media_manifest=[{'path':str(image),'size':image.stat().st_size,'sha256':hashlib.sha256(image.read_bytes()).hexdigest()}])
 def test_old_active_publication_missing_legacy_event_remains_read_only(self):
  claim=self.publication_claim()
  self.ledger.db.execute("UPDATE actions SET status='submitted_unknown',submitted_at=? WHERE operation_id='action-1'",(self.time,))
  self.ledger._event('action-1','submission_boundary_entered',{'attempt_id':claim['attempt_id']})
  # Old host may have committed pacing permit and lost the final event. No
  # event is not proof that the browser never received Publish permission.
  self.ledger._event('action-1','publication_editor_preflight',{'attempt_id':claim['attempt_id'],'report':{'fixture':'new report cannot reconstruct old dispatch'}})
  with self.assertRaisesRegex(ValueError,'FINAL_SEND_LEGACY_UNKNOWN'):
   self.ledger._guard_dispatch(self.ledger._row('action-1'))
  self.assertEqual(self.ledger.get('action-1')['status'],'submitted_unknown')
  self.ledger.report_result('action-1',claim['claim_token'],self.result(claim))
  self.assertEqual(self.ledger.get('action-1')['status'],'host_reported_success')
 def test_authorized_not_started_publication_gets_real_begin_marker(self):
  claim=self.publication_claim()
  self.ledger.begin_submit('action-1',claim['claim_token'])
  self.ledger.guard_final_send_protocol(self.ledger._row('action-1'))
  self.assertTrue(self.ledger.db.execute("SELECT 1 FROM action_events WHERE event='final_send_protocol_started'").fetchone())
