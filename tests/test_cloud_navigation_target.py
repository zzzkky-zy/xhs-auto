"""Offline routing regressions; fixture tokens are inert strings, never fetched."""
import unittest
import test_cloud_host
from cloud_navigation_target import observed_note_url

class ObservedRouteTests(unittest.TestCase):
 def setUp(self):
  self.feed='b'*24;self.payload={'feed_id':self.feed,'xsec_token':''}
  self.url='https://www.xiaohongshu.com/explore/'+self.feed+'?xsec_token=OFFLINE%2Bfixture&xsec_source=pc_search'
 def test_preserves_observed_route_despite_empty_payload_token(self):
  self.assertEqual(observed_note_url('get_feed_detail',self.payload,self.url),self.url)
 def test_rejects_wrong_feed_origin_scheme_and_fragment(self):
  for url in [self.url.replace(self.feed,'c'*24),self.url.replace('www.xiaohongshu.com','example.invalid'),self.url.replace('https:','http:'),self.url+'#fragment']:
   with self.assertRaises(ValueError):observed_note_url('get_feed_detail',self.payload,url)
 def test_observed_search_result_note_route_is_preserved(self):
  for family in ['/explore/','/discovery/item/','/search_result/']:
   url=self.url.replace('/explore/',family)
   self.assertEqual(observed_note_url('get_feed_detail',self.payload,url),url)
  with self.assertRaises(ValueError):observed_note_url('get_feed_detail',self.payload,self.url.replace('/explore/','/unknown/'))
 def test_does_not_invent_missing_token(self):
  url=self.url.split('?')[0];self.assertEqual(observed_note_url('post_comment_to_feed',self.payload,url),url)
 def test_other_operations_cannot_receive_note_route(self):
  with self.assertRaises(ValueError):observed_note_url('publish_content',{},self.url)
 def test_host_binds_original_url_not_regenerated_tokenless_route(self):
  f=test_cloud_host.CloudHostTests();f.setUp()
  try:
   r=f.host.call('prepare_cloud_action',{'operation':'get_feed_detail','payload':self.payload,'user_source':'OFFLINE FIXTURE','observed_target_url':self.url})
   self.assertEqual(r['binding']['target']['url'],self.url)
   self.assertEqual(r['host_plan']['target_url'],self.url)
   self.assertEqual(r['binding']['payload']['xsec_token'],'')
  finally:f.tearDown()

 def test_observed_profile_note_preserves_exact_route_and_query(self):
  url=self.url.replace('/explore/', '/user/profile/'+'a'*24+'/')
  for operation in ('get_feed_detail','post_comment_to_feed','reply_comment_in_feed'):
   self.assertEqual(observed_note_url(operation,self.payload,url),url)
 def test_profile_note_rejects_wrong_note_and_malformed_profile(self):
  prefix='https://www.xiaohongshu.com/user/profile/'
  for path in ['a'*24+'/'+'c'*24,'short/'+self.feed,'g'*24+'/'+self.feed,
               'a'*24+'/extra/'+self.feed,'a'*24+'/'+self.feed+'/',
               'a'*24+'/%62'+self.feed[1:], 'a'*24+'/../'+self.feed]:
   with self.subTest(path=path),self.assertRaises(ValueError):
    observed_note_url('get_feed_detail',self.payload,prefix+path)
 def test_profile_route_host_refuses_direct_history_navigation(self):
  f=test_cloud_host.CloudHostTests();f.setUp()
  try:
   url=self.url.replace('/explore/', '/user/profile/'+'a'*24+'/')
   for extra in ({'observed_target_url':url},{'observed_target_url':self.url,'source_context':'profile_history'}):
    with self.assertRaisesRegex(ValueError,'ERR_PROFILE_CARD_ONLY'):
     f.host.call('prepare_cloud_action',{'operation':'get_feed_detail','payload':self.payload,'user_source':'OFFLINE FIXTURE',**extra})
   plan=f.host.call('user_profile',{'user_id':'a'*24,'xsec_token':''})
   self.assertEqual(plan['profile_card_policy']['mode'],'card_only')
  finally:f.tearDown()
 def test_profile_card_steps_no_url_fallback_or_repeat_goto(self):
  from cloud_navigation_target import profile_card_step
  b={'target':{'url':'https://www.xiaohongshu.com/user/profile/'+'a'*24}}
  profile_card_step(b,{'kind':'navigation','target':b['target']['url']},[])
  profile_card_step(b,{'kind':'card_read','target':'profile-card:'+self.feed},[])
  profile_card_step(b,{'kind':'read_observation','target':'visible complete text'},[])
  for args,prior in [({'kind':'navigation','target':self.url},[]),
   ({'kind':'navigation','target':b['target']['url']},[{'kind':'navigation','status':'completed'}]),
   ({'kind':'search','target':'anything'},[]),({'kind':'card_read','target':self.url},[])]:
   with self.assertRaisesRegex(ValueError,'ERR_PROFILE_CARD_ONLY'):profile_card_step(b,args,prior)
 def test_card_read_charges_read_budget_and_not_search_or_logical_credit(self):
  from cloud_pacing import NETWORK_READ_STEPS,INTERNAL_STEPS,STEP_KINDS
  self.assertIn('card_read',STEP_KINDS);self.assertIn('card_read',NETWORK_READ_STEPS)
  self.assertNotIn('card_read',INTERNAL_STEPS)
 def test_search_card_steps_keep_query_and_read_boundaries(self):
  from cloud_navigation_target import search_card_step
  b={'target':{'url':'https://www.xiaohongshu.com/search_result?keyword=fixture'}}
  search_card_step(b,{'kind':'search','target':b['target']['url']},[])
  search_card_step(b,{'kind':'search','target':'visible filter: newest'},[])
  search_card_step(b,{'kind':'card_read','target':'search-card:'+self.feed},[])
  for args,prior in [({'kind':'navigation','target':self.url},[]),
   ({'kind':'search','target':self.url},[]),
   ({'kind':'search','target':b['target']['url']},[{'kind':'search','status':'completed'}]),
   ({'kind':'card_read','target':'profile-card:'+self.feed},[]),
   ({'kind':'card_read','target':'iframe:external'},[])]:
   with self.assertRaisesRegex(ValueError,'ERR_SEARCH_CARD_ONLY'):search_card_step(b,args,prior)
 def test_search_plan_exposes_native_cards(self):
  f=test_cloud_host.CloudHostTests();f.setUp()
  try:
   r=f.host.call('search_feeds',{'keyword':'fixture'})
   self.assertTrue(r['search_card_policy']['each_card_charged_read'])
   self.assertEqual(r['search_card_policy']['initial_query_step_kind'],'search')
  finally:f.tearDown()
 def test_comment_native_entry_and_sealed_card_guard(self):
  from cloud_navigation_target import observed_comment_entry,comment_card_step
  url='https://www.xiaohongshu.com/search_result?keyword=fixture'
  self.assertEqual(observed_comment_entry(url),url)
  with self.assertRaises(ValueError):observed_comment_entry(self.url)
  b={'target':{'url':url,'native_card_entry':True},'payload':self.payload}
  comment_card_step(b,{'kind':'search','target':url},[])
  comment_card_step(b,{'kind':'card_read','target':'comment-card:'+self.feed},[])
  for args in [{'kind':'navigation','target':self.url},{'kind':'card_read','target':'comment-card:'+'c'*24},{'kind':'click','target':'Send','purpose':'submit_comment'}]:
   with self.assertRaisesRegex(ValueError,'ERR_COMMENT_CARD_ENTRY'):comment_card_step(b,args,[])
  comment_card_step(b,{'kind':'click','target':'Send','purpose':'submit_comment'},[{'kind':'card_read','status':'completed'}])
 def test_comment_prepare_persists_entry_and_sealed_feed(self):
  from unittest import mock
  f=test_cloud_host.CloudHostTests();f.setUp()
  try:
   payload={'feed_id':self.feed,'xsec_token':'','content':'Offline reviewed fixture','run_id':'fixture-run'}
   entry='https://www.xiaohongshu.com/search_result?keyword=fixture'
   with mock.patch.object(f.host,'_review_payload',return_value={'payload':payload,'media_manifest':[]}),mock.patch.object(f.host,'_batch'):
    r=f.host.call('prepare_cloud_action',{'operation':'post_comment_to_feed','payload':payload,'observed_entry_url':entry,'user_source':'OFFLINE FIXTURE'})
   self.assertEqual(r['binding']['target']['url'],entry)
   self.assertTrue(r['binding']['target']['native_card_entry'])
   self.assertEqual(r['binding']['target']['feed_id'],self.feed)
   self.assertTrue(r['host_plan']['native_card_entry'])
  finally:f.tearDown()
