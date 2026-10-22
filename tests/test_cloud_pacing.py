"""Offline timing, concurrency and crash/restart contract tests."""
import concurrent.futures
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_pacing import CloudPacing


class PacingTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.config = Path(self.temp.name) / 'config.json'
        self.settings = {'browser_mode':'cloud_host','expected_user_id':'actual-account','state_dir':'state','browser_step_interval_min_seconds':1,'browser_step_interval_max_seconds':5}
        self.config.write_text(json.dumps(self.settings))
        self.now = 1000000.
        self.draws = []
        self.gate = self.make_gate()

    def make_gate(self):
        def draw(a, b):
            self.draws.append((a,b))
            return a
        return CloudPacing(self.config, clock=lambda:self.now, random_uniform=draw)

    def tearDown(self):
        self.gate.close()
        self.temp.cleanup()

    def comment(self, name, **kw):
        return self.gate.reserve('post_comment_to_feed', name, target_author_id='author', thread_id='note', author_evidence={'ref':'observed:ui'}, **kw)

    def test_shared_deadline_blocks_new_ids_and_identity(self):
        self.assertTrue(self.gate.reserve('like_feed','write')['permitted'])
        self.assertEqual(self.gate.reserve('check_login_status','identity')['retry_at'],self.now+10)
        self.assertEqual(self.gate.reserve('list_feeds','new_id')['retry_at'],self.now+10)
        self.now += 10
        self.assertTrue(self.gate.reserve('check_login_status','identity')['permitted'])
        self.assertEqual(self.gate.reserve('like_feed','write2')['retry_at'],1000060)

    def test_restart_no_redraw_or_reexecution(self):
        self.gate.reserve('search_feeds','search')
        draws = len(self.draws)
        self.gate.close(); self.gate = self.make_gate()
        self.assertEqual(self.gate.reserve('search_feeds','search')['status'],'already_reserved')
        self.assertEqual(self.gate.reserve('search_feeds','another')['retry_at'],self.now+60)
        self.assertEqual(len(self.draws),draws)
        self.now += 60
        self.assertTrue(self.gate.reserve('search_feeds','another')['permitted'])

    def test_binding_immutable(self):
        self.gate.reserve('list_feeds','one')
        with self.assertRaises(ValueError): self.gate.reserve('get_feed_detail','one')

    def test_read_and_search_rolling_quotas(self):
        self.settings.update(read_max_per_hour=3,search_max_per_hour=2)
        self.config.write_text(json.dumps(self.settings))
        self.gate.reserve('search_feeds','s1');self.now+=60
        self.gate.reserve('search_feeds','s2');self.now+=60
        self.assertEqual(self.gate.reserve('search_feeds','s3')['retry_at'],1003600)
        self.assertTrue(self.gate.reserve('list_notifications','r1')['permitted']);self.now+=20
        self.assertEqual(self.gate.reserve('get_my_profile','r2')['retry_at'],1003600)
        self.now=1003600
        self.assertTrue(self.gate.reserve('search_feeds','s3')['permitted'])

    def test_publication_minimum_and_writes_do_not_debit_reads(self):
        self.settings.update(write_interval_seconds=0,read_max_per_hour=1)
        self.config.write_text(json.dumps(self.settings))
        self.gate.reserve('publish_content','p1');self.now+=10
        self.assertEqual(self.gate.reserve('publish_with_video','p2')['retry_at'],1000060)
        self.assertTrue(self.gate.reserve('get_feed_detail','r')['permitted'])

    def test_author_unknown_preserved_and_success_paired_reply(self):
        self.assertTrue(self.comment('main')['permitted']);self.now+=180
        self.assertEqual(self.comment('other')['retry_at'],1000000+3*86400)
        with self.assertRaises(ValueError):
            self.gate.reserve('reply_comment_in_feed','reply',target_author_id='author',thread_id='note',parent_operation_id='main',author_evidence={'ref':'ui','target_comment_id':'comment-one'})
        self.gate.record_result('main','host_reported_success',{'ref':'visible-comment'})
        self.assertTrue(self.gate.reserve('reply_comment_in_feed','reply',target_author_id='author',thread_id='note',parent_operation_id='main',author_evidence={'ref':'ui','target_comment_id':'comment-one'})['permitted'])
        self.now+=180
        with self.assertRaises(ValueError):
            self.gate.reserve('reply_comment_in_feed','reply2',target_author_id='author',thread_id='note',parent_operation_id='main',author_evidence={'ref':'ui','target_comment_id':'comment-one'})
        self.gate.record_result('reply','success',{'ref':'visible-reply'})
        self.assertTrue(self.gate.reserve('reply_comment_in_feed','different_reply',target_author_id='author',thread_id='note',parent_operation_id='main',author_evidence={'ref':'ui','target_comment_id':'comment-two'})['permitted'])
        self.assertIn('reserved_unknown',[x['event'] for x in self.gate.events('main')])

    def test_author_evidence_required_and_not_applied_does_not_suppress(self):
        with self.assertRaises(ValueError): self.gate.reserve('post_comment_to_feed','missing')
        self.comment('main')
        self.gate.record_result('main','not_applied',{'ref':'explicit-no-submit'})
        self.now+=180
        self.assertTrue(self.comment('replacement')['permitted'])
        self.assertEqual(self.comment('main')['status'],'already_reserved')

    def test_concurrent_reservations_only_one_passes(self):
        def reserve(i):
            gate=self.make_gate()
            try: return gate.reserve('list_feeds','parallel_'+str(i))['permitted']
            finally: gate.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve,range(8))),1)

    def test_aliases_share_platform_account_budget(self):
        self.gate.reserve('list_feeds','first')
        self.settings['account_id']='new-alias';self.config.write_text(json.dumps(self.settings))
        other=self.make_gate()
        try:self.assertFalse(other.reserve('list_feeds','second')['permitted'])
        finally:other.close()

    def test_platform_blocks_persist_without_timer_unlock(self):
        self.gate.report_block('search','300013',{'ref':'platform-observed'})
        self.now+=86400*5
        self.gate.close();self.gate=self.make_gate()
        self.assertEqual(self.gate.reserve('search_feeds','search')['status'],'platform_blocked')
        self.assertTrue(self.gate.reserve('list_feeds','read')['permitted'])
        with self.assertRaises(ValueError):self.gate.clear_block('search',{'expected_account':'wrong','status':'resolved','observed_at':self.now,'evidence_ref':'ui'})
        self.gate.clear_block('search',{'expected_account':'actual-account','status':'resolved','observed_at':self.now,'evidence_ref':'ui'})
        self.now+=20
        self.assertTrue(self.gate.reserve('search_feeds','search')['permitted'])

    def test_account_block_and_stale_recovery(self):
        self.gate.report_block('account','login',{'ref':'logged-out'})
        for op in ['check_login_status','like_feed','search_feeds']:
            self.assertEqual(self.gate.reserve(op,op)['status'],'platform_blocked')
        with self.assertRaises(ValueError):self.gate.clear_block('account',{'expected_account':'actual-account','status':'resolved','observed_at':self.now,'evidence_ref':'ui'})

    def test_notification_listing_counts_as_read_and_write(self):
        self.settings['read_max_per_hour']=1
        self.config.write_text(json.dumps(self.settings))
        self.gate.reserve('list_notifications','notifications')
        self.now+=10
        self.assertEqual(self.gate.reserve('like_feed','write')['retry_at'],1000060)
        self.assertEqual(self.gate.reserve('list_feeds','read')['retry_at'],1003600)

    def test_live_storage_permission_guard(self):
        os.chmod(self.gate.path,0o644)
        with self.assertRaises(ValueError):self.gate.reserve('list_feeds','r')
        os.chmod(self.gate.path,0o600)
        os.chmod(self.gate.directory,0o755)
        with self.assertRaises(ValueError):self.gate.reserve('list_feeds','r')
        os.chmod(self.gate.directory,0o700)

    def test_live_storage_replacement_guard(self):
        self.gate.path.rename(str(self.gate.path)+'.saved')
        self.gate.path.touch(mode=0o600)
        with self.assertRaises(ValueError):self.gate.reserve('list_feeds','r')

    def test_live_config_state_change_guard(self):
        self.settings['state_dir']='elsewhere';self.config.write_text(json.dumps(self.settings))
        with self.assertRaises(ValueError):self.gate.reserve('list_feeds','r')

    def step(self, ident='step',kind='click',target='visible single element',**kw):
        return self.gate.reserve_step('flow',ident,kind,target,**kw)

    def start_flow(self,operation='list_feeds'):
        self.gate.reserve(operation,'flow'); self.now+=60

    def test_step_shared_deadline_persisted_and_one_use(self):
        self.gate.reserve('list_feeds','flow')
        self.assertEqual(self.step()['retry_at'],self.now+1)
        count=len(self.draws);self.gate.close();self.gate=self.make_gate()
        self.assertEqual(self.step()['retry_at'],self.now+1)
        self.assertEqual(len(self.draws),count)
        self.now+=10
        self.assertTrue(self.step()['permitted'])
        self.assertEqual(self.step()['status'],'already_reserved')
        with self.assertRaises(ValueError):self.step(target='different target')
        self.gate.complete_step('step','completed',{'ref':'observed call completed'})
        self.assertEqual(self.step('next')['retry_at'],self.now+1)
        self.now+=10
        self.assertTrue(self.step('next')['permitted'])

    def test_unknown_step_never_expires_or_allows_new_logical_action(self):
        self.start_flow();self.step()
        self.now+=86400
        self.assertEqual(self.step('other')['status'],'unfinished_browser_step')
        self.assertEqual(self.gate.reserve('list_feeds','otherflow')['status'],'unfinished_browser_step')
        self.gate.complete_step('step','unknown',{'ref':'call ended uncertain'})
        self.gate.close();self.gate=self.make_gate();self.now+=86400
        self.assertEqual(self.step('other')['status'],'unfinished_browser_step')
        self.gate.complete_step('step','not_applied',{'evidence_ref':'actual verified absent effect','expected_account':'actual-account','observed_at':self.now})
        self.assertTrue(self.step('other')['permitted'])

    def test_search_credit_once_extra_filter_charged(self):
        self.settings['search_max_per_hour']=2;self.config.write_text(json.dumps(self.settings))
        self.start_flow('search_feeds')
        result=self.step('first','search','official search query')
        self.assertTrue(result['permitted']);self.assertFalse(result['charged_search'])
        self.gate.complete_step('first','completed',{'ref':'search rendered'})
        self.now+=60
        self.assertTrue(self.step('filter','search','official filter click')['charged_search'])
        self.gate.complete_step('filter','completed',{'ref':'filter results'})
        self.now+=60
        self.assertEqual(self.step('filter2','search','second filter')['retry_at'],1003600)
        self.assertEqual(self.gate.reserve('search_feeds','newflow')['retry_at'],1003600)

    def test_navigation_credit_and_observation_no_read_quota(self):
        self.settings['read_max_per_hour']=1;self.config.write_text(json.dumps(self.settings))
        self.start_flow()
        self.assertFalse(self.step('nav','navigation','https://www.xiaohongshu.com/explore')['charged_read'])
        self.gate.complete_step('nav','completed',{'ref':'page loaded'});self.now+=20
        self.assertTrue(self.step('snap','read_observation','single screenshot')['permitted'])
        self.gate.complete_step('snap','completed',{'ref':'screenshot'});self.now+=20
        self.assertTrue(self.step('media','media_request','next image')['permitted'])
        self.gate.complete_step('media','completed',{'ref':'existing detail image'});self.now+=5
        self.assertEqual(self.step('newpage','navigation','different detail')['retry_at'],1003600)

    def test_atomic_step_restriction_and_readonly_recovery(self):
        self.start_flow();self.step()
        recovery={'expected_account':'actual-account','evidence_ref':'host observation','user_source':'explicit user recovery request','call_ended':True,'recovery_reason':'inspect blocked page'}
        self.now+=20
        self.assertEqual(self.step('recovery','read_observation','screenshot',recovery=True,recovery_evidence=recovery)['status'],'unfinished_browser_step')
        self.gate.complete_step('step','unknown',{'ref':'call ended with CAPTCHA'},platform_restriction={'scope':'account','reason':'captcha','evidence':{'ref':'visible challenge'}})
        self.assertTrue(self.step('recovery','read_observation','screenshot',recovery=True,recovery_evidence=recovery)['permitted'])
        self.gate.complete_step('recovery','completed',{'ref':'visible result'});self.now+=20
        self.assertEqual(self.step('normal')['status'],'unfinished_browser_step')
        self.gate.complete_step('step','not_applied',{'evidence_ref':'verified no effect','expected_account':'actual-account','observed_at':self.now})
        self.assertEqual(self.step('normal')['status'],'platform_blocked')
        self.now+=86400
        self.assertEqual(self.step('normal')['status'],'platform_blocked')
        with self.assertRaises(ValueError):self.step('bad','click','button',recovery=True,recovery_evidence=recovery)

    def test_completed_inspection_upload_allows_only_passive_verification(self):
        self.start_flow('inspect_publication_schedule')
        self.assertTrue(self.step('upload','media_request','sealed files')['permitted'])
        self.gate.complete_step('upload','completed',{'ref':'actual upload call ended'})
        self.now+=1000
        evidence={'expected_account':'actual-account','evidence_ref':'actual ended upload',
                  'user_source':'user-authorized upload verification','call_ended':True,
                  'recovery_reason':'verify upload after approval outlived lease'}
        self.assertTrue(self.step('verify','read_observation','same uploader',recovery=True,recovery_evidence=evidence)['permitted'])
        self.gate.complete_step('verify','completed',{'ref':'actual UI'})
        self.now+=20
        for kind in ('click','navigation','media_request'):
            with self.assertRaises(ValueError):
                self.step('bad_'+kind,kind,'forbidden action',recovery=True,recovery_evidence=evidence)

    def test_completed_noninspection_media_does_not_grant_recovery(self):
        self.start_flow('list_feeds')
        self.assertTrue(self.step('media','media_request','read image')['permitted'])
        self.gate.complete_step('media','completed',{'ref':'ended'})
        self.now+=1000
        evidence={'expected_account':'actual-account','evidence_ref':'ended',
                  'user_source':'fixture','call_ended':True,'recovery_reason':'fixture'}
        with self.assertRaises(ValueError):
            self.step('verify','read_observation','same page',recovery=True,recovery_evidence=evidence)

    def test_inspection_without_completed_upload_does_not_grant_recovery(self):
        self.start_flow('inspect_publication_schedule')
        evidence={'expected_account':'actual-account','evidence_ref':'ended',
                  'user_source':'fixture','call_ended':True,'recovery_reason':'fixture'}
        with self.assertRaises(ValueError):
            self.step('verify','read_observation','same page',recovery=True,recovery_evidence=evidence)

    def test_denied_publication_can_refresh_identity_without_replaying_click(self):
        self.start_flow('publish_content')
        self.assertTrue(self.step('denied','click','final submit')['permitted'])
        self.gate.complete_step('denied','not_applied',{'ref':'reviewer denied before browser invocation'})
        self.now+=1000
        evidence={'expected_account':'actual-account','evidence_ref':'denial','user_source':'authorized task',
                  'call_ended':True,'recovery_reason':'refresh identity for same editor',
                  'final_submit_invoked':False,'denied_step_id':'denied'}
        self.assertTrue(self.step('identity','read_observation','retained own profile',recovery=True,recovery_evidence=evidence)['permitted'])
        self.gate.complete_step('identity','completed',{'ref':'actual profile'})
        self.now+=20
        with self.assertRaises(ValueError):
            self.step('bad','click','submit',recovery=True,recovery_evidence=evidence)
        with self.assertRaises(ValueError):
            self.step('unbound','read_observation','profile',recovery=True,recovery_evidence={**evidence,'denied_step_id':'missing'})

    def test_retained_publication_passive_refresh_after_real_upload(self):
        self.start_flow('publish_content')
        evidence={'expected_account':'actual-account','evidence_ref':'retained real editor','user_source':'authorized task',
                  'call_ended':True,'recovery_reason':'fresh identity before editing renewal',
                  'final_submit_invoked':False,'retained_editor':True}
        with self.assertRaises(ValueError):
            self.step('tooearly','read_observation','same page',recovery=True,recovery_evidence=evidence)
        self.assertTrue(self.step('upload','media_request','sealed media')['permitted'])
        self.gate.complete_step('upload','completed',{'ref':'actual upload finished'})
        self.now+=1000
        self.assertTrue(self.step('identity','read_observation','retained same-session profile',recovery=True,recovery_evidence=evidence)['permitted'])
        self.gate.complete_step('identity','completed',{'ref':'actual identity'})
        self.now+=20
        with self.assertRaises(ValueError):
            self.step('bad','click','submit',recovery=True,recovery_evidence=evidence)

    def test_explicit_captcha_interaction_keeps_block_and_rejects_other_scopes(self):
        self.start_flow()
        evidence={'expected_account':'actual-account','evidence_ref':'visible slider','user_source':'user task',
                  'call_ended':True,'recovery_reason':'handle visible challenge',
                  'challenge_interaction':True,'challenge_authorization_source':'explicit user permission'}
        with self.assertRaisesRegex(ValueError,'ERR_CAPTCHA_SCOPE'):
            self.step('no-block','click','slider',recovery=True,recovery_evidence=evidence)
        self.gate.report_block('account','captcha',{'ref':'actual slider'})
        self.assertTrue(self.step('drag','click','visible slider only',recovery=True,recovery_evidence=evidence)['permitted'])
        self.gate.complete_step('drag','completed',{'ref':'single challenge drag ended'})
        self.now+=20
        self.assertEqual(self.step('ordinary','click','anything')['status'],'platform_blocked')
        with self.assertRaises(ValueError):
            self.step('badnav','navigation','different page',recovery=True,recovery_evidence=evidence)
        self.gate.report_block('write','300013',{'ref':'actual rate limit'})
        with self.assertRaisesRegex(ValueError,'ERR_CAPTCHA_SCOPE'):
            self.step('rateblocked','click','slider',recovery=True,recovery_evidence=evidence)

    def test_lost_session_recovery_only_official_home_preserves_block(self):
        self.start_flow()
        self.gate.report_block('account','captcha',{'ref':'old session challenge'})
        evidence={'expected_account':'actual-account','evidence_ref':'current inventory','user_source':'user recovery task',
                  'call_ended':True,'recovery_reason':'inspect login after lost session',
                  'session_recovery':True,'prior_session_unavailable':True,
                  'new_browser_context':'new official browser','session_evidence_ref':'actual tab inventory'}
        for target in ('https://evil.invalid/','https://www.xiaohongshu.com/explore'):
            with self.assertRaisesRegex(ValueError,'ERR_SESSION_RECOVERY'):
                self.step('bad'+str(len(target)),'navigation',target,recovery=True,recovery_evidence=evidence)
        self.assertTrue(self.step('recover','navigation','https://www.xiaohongshu.com/',recovery=True,recovery_evidence=evidence)['permitted'])
        self.gate.complete_step('recover','completed',{'ref':'actual home rendered'})
        self.now+=20
        self.assertEqual(self.step('ordinary')['status'],'platform_blocked')
        self.gate.report_block('write','300013',{'ref':'rate limit'})
        with self.assertRaisesRegex(ValueError,'ERR_SESSION_RECOVERY'):
            self.step('rate-bypass','navigation','https://www.xiaohongshu.com/',recovery=True,recovery_evidence=evidence)

    def test_step_concurrent_single_inflight(self):
        self.start_flow()
        def reserve(i):
            gate=self.make_gate()
            try:return gate.reserve_step('flow','concurrent_'+str(i),'scroll','one scroll')['permitted']
            finally:gate.close()
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            self.assertEqual(sum(pool.map(reserve,range(8))),1)

    def test_step_closed_kind_and_bounded_inert_target(self):
        self.start_flow()
        with self.assertRaises(ValueError):self.step(kind='javascript_batch')
        with self.assertRaises(ValueError):self.step(target={'script':'batch()'})
        with self.assertRaises(ValueError):self.gate.complete_step('absent','completed',{'ref':'fake'})

    def test_step_receipts_and_fresh_reconciliation(self):
        self.start_flow();self.step()
        self.assertEqual(self.gate.get_step('step')['attempt_id'],'flow')
        self.assertEqual(len(self.gate.steps_for_attempt('flow')),1)
        self.gate.complete_step('step','unknown',{'ref':'observed uncertain'})
        with self.assertRaises(ValueError):self.gate.complete_step('step','completed',{'ref':'unbound assertion'})
        self.gate.complete_step('step','completed',{'expected_account':'actual-account','evidence_ref':'actual observed outcome','observed_at':self.now})
        with self.assertRaises(ValueError):self.gate.complete_step('step','not_applied',{'ref':'conflict'})
        self.now+=20;self.step('second')
        with self.assertRaises(ValueError):self.gate.steps_for_attempt('flow',limit=1)

    def test_first_search_step_credit_waits_only_shared_gap(self):
        self.gate.reserve('search_feeds','flow')
        self.assertEqual(self.step('first','search','initial query')['retry_at'],self.now+10)
        self.now+=10
        self.assertTrue(self.step('first','search','initial query')['permitted'])
        self.gate.complete_step('first','completed',{'ref':'actual search rendered'})
        self.assertEqual(self.step('filter','search','new filter')['retry_at'],self.now+60)

    def test_search_credit_cannot_skip_intervening_workflow(self):
        self.gate.reserve('search_feeds','flow');self.now+=60
        self.gate.reserve('search_feeds','otherflow');self.now+=10
        self.assertEqual(self.step('late','search','initial query')['retry_at'],1000120)

    def test_write_operation_has_no_network_read_credit(self):
        self.start_flow('like_feed')
        result=self.step('nav','navigation','actual target page')
        self.assertTrue(result['charged_read'])

    def test_internal_gap_is_separate_and_immutable_draws_recorded(self):
        self.gate.reserve('search_feeds','flow')
        self.now+=10
        self.step('search','search','query');self.gate.complete_step('search','completed',{'ref':'visible search'})
        self.assertEqual(self.step('inside','scroll','single scroll')['retry_at'],self.now+1)
        self.now+=1
        self.assertTrue(self.step('inside','scroll','single scroll')['permitted'])
        self.gate.complete_step('inside','completed',{'ref':'scrolled'})
        self.assertEqual(self.step('query2','search','other query')['retry_at'],1000070)
        events=self.gate.events('flow')
        drawn=[json.loads(e['evidence']) for e in events if e['event'] in {'reserved_unknown','step_reserved'}]
        self.assertTrue(all('draws' in e and 'deadlines' in e for e in drawn))
        self.assertEqual(drawn[-1]['draws']['browser_step'],1)
        self.assertIsNone(drawn[-1]['draws']['action'])

    def test_internal_config_change_keeps_persisted_deadline(self):
        self.settings.update(browser_step_interval_min_seconds=5,browser_step_interval_max_seconds=5)
        self.config.write_text(json.dumps(self.settings));self.gate.reserve('list_feeds','flow')
        self.assertEqual(self.step()['retry_at'],self.now+5)
        self.settings.update(browser_step_interval_min_seconds=1,browser_step_interval_max_seconds=1)
        self.config.write_text(json.dumps(self.settings));self.gate.close();self.gate=self.make_gate()
        self.assertEqual(self.step()['retry_at'],self.now+5)
        self.now+=5;self.assertTrue(self.step()['permitted'])
        self.gate.complete_step('step','completed',{'ref':'done'})
        self.assertEqual(self.step('second')['retry_at'],self.now+1)

    def test_unconfigured_internal_gap_preserves_original_default(self):
        self.settings.pop('browser_step_interval_min_seconds');self.settings.pop('browser_step_interval_max_seconds')
        self.config.write_text(json.dumps(self.settings));self.gate.reserve('list_feeds','flow')
        self.assertEqual(self.step()['retry_at'],self.now+10)
        self.now+=10;result=self.step()
        self.assertEqual(result['next_step_at'],self.now+10)


    def test_each_native_profile_card_consumes_read_budget(self):
        self.settings['read_max_per_hour']=3
        self.config.write_text(json.dumps(self.settings))
        self.start_flow('user_profile')
        for i in range(2):
            r=self.step('card'+str(i),'card_read','profile-card:'+'b'*24)
            self.assertTrue(r['permitted']);self.assertTrue(r['charged_read']);self.assertFalse(r['charged_search'])
            self.gate.complete_step('card'+str(i),'completed',{'ref':'offline actual card fixture'})
            self.now+=20
        r=self.step('card2','card_read','profile-card:'+'c'*24)
        self.assertFalse(r['permitted']);self.assertEqual(r['error'],'ERR_RATE_LIMIT')


if __name__=='__main__':unittest.main()
