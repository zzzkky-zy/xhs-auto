import io
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
ROOT=Path(__file__).resolve().parents[1]
sys.path.insert(0,str(ROOT/'runtime'))
from cloud_host import CloudHost,TOOLS
from cloud_capabilities import ORIGINAL
from cloud_mcp import serve

class CloudHostTests(unittest.TestCase):
 def setUp(self):
  self.temp=tempfile.TemporaryDirectory();self.path=Path(self.temp.name)/'config.json'
  self.settings={'schema_version':1,'browser_mode':'cloud_host','account_id':'fixture','state_dir':'state','allow_write':False,'allow_reconcile':False,'expected_user_id':'a'*24}
  self.path.write_text(json.dumps(self.settings));self.host=CloudHost(self.path)
 def tearDown(self):self.host.close();self.temp.cleanup()
 def test_unknown_continuation_helper_dispatches_bound_arguments(self):
  from unittest import mock
  args={'operation_id':'new','blocked_operation_id':'old','attestation':{'fixture':'not real approval'}}
  with mock.patch.object(self.host.actions,'record_unknown_continuation',return_value={'fixture':True},create=True) as call:
   self.assertEqual(self.host.call('record_cloud_unknown_continuation',args),{'fixture':True})
   call.assert_called_once_with(**args)
 def test_projection_diagnosis_never_initializes_batch_storage(self):
  before=set(self.path.parent.rglob('*'))
  with self.assertRaisesRegex(ValueError,'ERR_BATCH_NOT_OPEN'):
   self.host.call('cloud_batch_projection_status',{'run_id':'missing'})
  self.assertEqual(set(self.path.parent.rglob('*')),before)
  self.assertIsNone(self.host.batch)
 def test_original_names_schemas_preserved(self):
  indexed={x['name']:x for x in TOOLS}
  for name,definition in ORIGINAL.items():self.assertEqual(indexed[name]['inputSchema'],definition['inputSchema'])
 def test_original_read_returns_pending_not_receipt(self):
  result=self.host.call('list_feeds',{})
  self.assertEqual(result['status'],'prepared_not_executed');self.assertFalse(result['automatic_execution'])
 def test_original_write_does_not_dispatch(self):
  result=self.host.call('like_feed',{'feed_id':'b'*24,'xsec_token':'observed-token'})
  self.assertEqual(result['status'],'prepared_not_executed');self.assertEqual(self.host.actions.list_actions(),[])
 def test_prepare_binds_real_source_and_config_account(self):
  result=self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture:user-request','operation_id':'one'})
  self.assertEqual(result['binding']['expected_account'],'a'*24)
  self.assertEqual(result['binding']['user_source'],'fixture:user-request')
  ready=self.host.call('request_cloud_authorization',{'operation_id':'one'})
  self.assertEqual(ready['status'],'ready')
 def test_due_requires_explicit_timezone(self):
  with self.assertRaises(ValueError):self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture','due_at':'2030-01-01T00:00:00Z'})
 def test_local_legacy_not_falsely_claimed(self):
  result=self.host.call('manage_operation_run',{'operation':'status'})
  self.assertIn('cloud_batch_capabilities',result)
 def test_auth_not_saved(self):
  result=self.host.call('login_with_sms',{'phone':'12345678901','code':'never-store'})
  self.assertEqual(result['status'],'host_auth_required');self.assertNotIn('never-store',json.dumps(result))
 def test_strict_helper_args(self):
  with self.assertRaises(ValueError):self.host.call('cloud_capabilities',{'approved':True})
 def test_original_queue_save_status_pause(self):
  result=self.host.call('submit_task',{'tool':'list_feeds','payload':{},'auto_run':True})
  self.assertFalse(result['auto_run']);self.assertEqual(result['status'],'prepared')
  task=self.host.call('get_task_status',{'task_id':result['task_id'],'include_result':False})
  self.assertNotIn('result',task)
  self.host.call('control_task_queue',{'enabled':False})
  self.host.call('request_cloud_authorization',{'operation_id':result['task_id']})
  with self.assertRaises(ValueError):self.host.call('claim_cloud_action',{'operation_id':result['task_id'],'worker_id':'fixture','browser_context':'fixture:tab'})
 def test_legacy_publication_submit_requires_reviewed_run(self):
  media=Path(self.temp.name)/'fixture.png';media.write_bytes(b'OFFLINE SYNTHETIC IMAGE BYTES')
  with self.assertRaises(ValueError):self.host.call('submit_task',{'tool':'publish_content','payload':{'title':'fixture','content':'not a real publication','images':[str(media)]},'approval_ref':'fixture-only'})
  self.assertEqual(self.host.actions.list_actions(),[])
 def test_legacy_remote_media_rejected_before_dead_end_enqueue(self):
  with self.assertRaises(ValueError):self.host.call('submit_task',{'tool':'publish_content','payload':{'title':'fixture','content':'fixture','images':['https://example.invalid/image.png']}})
  self.assertEqual(self.host.actions.list_actions(),[])
 def test_original_resolution_cannot_spoof_success(self):
  result=self.host.call('submit_task',{'tool':'list_feeds','payload':{}})
  value=self.host.call('resolve_operation',{'operation_id':result['task_id'],'status':'confirmed','evidence':'unverified string'})
  self.assertEqual(value['status'],'prepared')
 def test_source_identity_is_reloaded_after_config_change(self):
  result=self.host.call('prepare_cloud_action',{'operation':'list_feeds','payload':{},'user_source':'fixture'})
  self.host.call('request_cloud_authorization',{'operation_id':result['operation_id']})
  self.settings['expected_user_id']='b'*24;self.path.write_text(json.dumps(self.settings))
  with self.assertRaises(ValueError):self.host.call('claim_cloud_action',{'operation_id':result['operation_id'],'worker_id':'fixture','browser_context':'fixture:tab'})
 def test_mcp_exposes_original_and_lifecycle_without_backend(self):
  source=io.StringIO('\n'.join(json.dumps(x) for x in [{'jsonrpc':'2.0','id':1,'method':'initialize'},{'jsonrpc':'2.0','id':2,'method':'tools/list'},{'jsonrpc':'2.0','id':3,'method':'tools/call','params':{'name':'list_feeds','arguments':{}}}])+'\n');sink=io.StringIO()
  serve(self.path,source,sink);rows=[json.loads(x) for x in sink.getvalue().splitlines()]
  names={x['name'] for x in rows[1]['result']['tools']};self.assertTrue(set(ORIGINAL)<=names)
  self.assertEqual(json.loads(rows[2]['result']['content'][0]['text'])['status'],'prepared_not_executed')
 def test_mcp_can_start_explicit_write_config_without_old_read_adapter(self):
  self.settings['allow_write']=True;self.path.write_text(json.dumps(self.settings))
  source=io.StringIO('{"jsonrpc":"2.0","id":1,"method":"initialize"}\n');sink=io.StringIO();serve(self.path,source,sink)
  self.assertIn('result',json.loads(sink.getvalue()))

if __name__=='__main__':unittest.main()
