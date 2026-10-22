"""Restored isolated directories never initialize executable ledgers."""
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'runtime'))
from cloud_actions import CloudActions
from cloud_pacing import CloudPacing
from cloud_batch import CloudBatch

class QuarantineTests(unittest.TestCase):
 def setUp(self):
  self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.config=self.root/'config.json'
  self.config.write_text(json.dumps({'schema_version':1,'browser_mode':'cloud_host','account_id':'fixture','state_dir':'evidence/state','allow_write':False,'allow_reconcile':False,'expected_user_id':'a'*24}))
 def tearDown(self):self.tmp.cleanup()
 def test_ancestor_marker_blocks_all_constructors_before_creation(self):
  (self.root/'RECOVERY-QUARANTINE.json').write_text('malformed marker must also block')
  before=set(self.root.rglob('*'))
  for cls in (CloudActions,CloudPacing,CloudBatch):
   with self.subTest(cls=cls),self.assertRaisesRegex(ValueError,'RECOVERY_QUARANTINED'):cls(self.config)
  self.assertEqual(before,set(self.root.rglob('*')))
 def test_state_marker_blocks_all_constructors_without_migration(self):
  state=self.root/'evidence/state';state.mkdir(parents=True,mode=0o700)
  (state/'RECOVERY-QUARANTINE.json').write_text('{}')
  for cls in (CloudActions,CloudPacing,CloudBatch):
   with self.assertRaisesRegex(ValueError,'RECOVERY_QUARANTINED'):cls(self.config)
  self.assertFalse(any(state.glob('*.sqlite3')))
 def test_marker_added_to_open_ledger_blocks_unpause(self):
  ledger=CloudActions(self.config)
  try:
   (self.root/'RECOVERY-QUARANTINE.json').write_text('{}')
   with self.assertRaisesRegex(ValueError,'RECOVERY_QUARANTINED'):
    ledger.set_execution_pause(False,'SYNTHETIC','fixture')
  finally:ledger.close()
 def test_linked_marker_is_not_an_escape(self):
  (self.root/'RECOVERY-QUARANTINE.json').symlink_to(self.root/'absent')
  with self.assertRaisesRegex(ValueError,'RECOVERY_QUARANTINED'):CloudActions(self.config)
