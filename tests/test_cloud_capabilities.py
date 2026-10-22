import json
from pathlib import Path
import sys
import unittest
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT/'runtime'))
import cloud_capabilities as cap
ID='a'*24

class CapabilitiesTests(unittest.TestCase):
 def test_all_thirty_classified_no_fabricated_follow(self):
  result=cap.catalog();self.assertEqual(result['original_tool_count'],30);self.assertFalse(result['follow_unfollow_in_original']);self.assertEqual(len(result['capabilities']),30)
 def test_auth_handoff_not_credentials_ledger(self):
  for name,payload in [('login_with_sms',{'phone':'dummy','code':'dummy'}),('get_login_qrcode',{}),('delete_cookies',{})]:
   with self.assertRaises(ValueError):cap.validate_payload(name,payload)
 def test_notification_mark_read_is_mutation(self):self.assertTrue(cap.capability('list_notifications')['account_state_change'])
 def test_types_unknown_properties_and_sensitive_fields_rejected(self):
  for payload in [{'keyword':False},{'keyword':'x','script':'code'},{'keyword':'x','filters':{'sort_by':'wrong'}},{'keyword':'x','result_limit':101}]:
   with self.assertRaises(ValueError):cap.validate_payload('search_feeds',payload)
  with self.assertRaises(ValueError):cap.validate_payload('manage_operation_run',{'operation':'configure','data':{'password':'secret'}})
 def test_ids_and_reply_targets(self):
  with self.assertRaises(ValueError):cap.validate_payload('get_feed_detail',{'feed_id':'bad','xsec_token':''})
  with self.assertRaises(ValueError):cap.validate_payload('reply_comment_in_feed',{'feed_id':ID,'xsec_token':'','content':'hi'})
 def test_publish_requires_media_and_explicit_schedule_timezone(self):
  for payload in [{'title':'x','content':'y'},{'title':'x','content':'y','images':['/tmp/a.png'],'schedule_at':'2026-10-20T12:00:00'}]:
   with self.assertRaises(ValueError):cap.validate_payload('publish_content',payload)
 def test_image_plan_preserves_all_requested_fields_and_dispatches_before_upload(self):
  p={'title':'x','content':'y','images':['/tmp/a.png'],'tags':['tag'],'visibility':'仅自己可见','is_original':False,'schedule_at':'2026-10-20T12:00:00+08:00','activity':'event','topic_template':'named','products':['exact']}
  plan=cap.action_plan('publish_content',p,ID);self.assertEqual(plan['payload'],p)
  phases=[s['phase'] for s in plan['steps']];self.assertLess(phases.index('record_dispatch_before_side_effect'),phases.index('upload_media'));self.assertFalse(plan['automatic_execution'])
 def test_notification_dispatch_before_mark_read_navigation(self):
  phases=[s['phase'] for s in cap.action_plan('list_notifications',{},ID)['steps']]
  self.assertLess(phases.index('record_dispatch_before_side_effect'),phases.index('inspect_target'))
 def test_dm_open_only_is_not_send(self):
  plan=cap.action_plan('reply_direct_message',{'conversation_id':'known','open_only':True},ID);self.assertTrue(plan['account_state_change'])
  with self.assertRaises(ValueError):cap.action_plan('reply_direct_message',{'conversation_id':'known'},ID)
 def test_public_read_and_encoded_site_token(self):
  plan=cap.action_plan('get_feed_detail',{'feed_id':ID,'xsec_token':'site/path?x=1'},ID)
  self.assertTrue(plan['target_url'].startswith('https://www.xiaohongshu.com/explore/'));self.assertIn('%3F',plan['target_url'])
 def test_no_local_lifecycle_plan_as_external_action(self):
  with self.assertRaises(ValueError):cap.action_plan('get_runtime_status',{},ID)
 def test_recursive_payload_bound(self):
  value={};current=value
  for i in range(20):current['a']={};current=current['a']
  with self.assertRaises(ValueError):cap.validate_payload('manage_operation_run',{'operation':'configure','data':value})

if __name__=='__main__':unittest.main()
