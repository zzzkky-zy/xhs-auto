"""Offline verified-not-submitted publication renewal fixtures."""
import json
import unittest
from unittest import mock
import test_cloud_inspection_renewal as fixtures
import test_cloud_host

class PublicationRenewalTests(fixtures.InspectionRenewalTests):
    def publication_started(self):
        args=self.inspection_args('pub');args['operation']='publish_content'
        claim=self.claimed('pub',**args)
        self.ledger.begin_submit('pub',claim['claim_token']);self.time+=121
        self.ledger.observe_identity('pub',self.identity())
        return claim
    def pub_observation(self,**kw):
        return {'expected_account':'public-user-1','browser_context':'fixture:browser-1/tab-2',
                'call_ended':True,'final_submit_invoked':False,'observed_at':self.stamp(),
                'evidence_ref':'SYNTHETIC denied click plus retained editor','denied_step_id':'denied-final',**kw}
    def test_renew_preserves_publication_attempt(self):
        claim=self.publication_started();before=self.ledger.get('pub')
        result=self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.pub_observation())
        for key in ('binding','attempt_id','submitted_at','result','authorization'):
            self.assertEqual(result[key],before[key])
        self.assertEqual(result['lease_until'],self.stamp(600))
    def test_rejects_invoked_submit_wrong_context_and_stale_evidence(self):
        claim=self.publication_started()
        for changes in ({'final_submit_invoked':True},{'call_ended':False},{'expected_account':'wrong'},{'observed_at':self.stamp(-121)}):
            with self.assertRaises(ValueError):
                self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.pub_observation(**changes))
    def test_stale_identity_still_blocks_renewal(self):
        claim=self.publication_started();self.time+=121
        with self.assertRaisesRegex(ValueError,'ERR_IDENTITY_STALE'):
            self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.pub_observation())

class PublicationHostRenewalTests(unittest.TestCase):
    def test_only_real_not_applied_final_step_and_resolved_calls(self):
        f=test_cloud_host.CloudHostTests();f.setUp()
        try:
            h=f.host;row={'binding_json':json.dumps({'operation':'publish_content'}),'attempt_id':'same'}
            args={'operation_id':'pub','claim_token':'same','lease_seconds':600,'observation':{'denied_step_id':'final'}}
            for status in ('completed','unknown','in_flight'):
                pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'step_id':'final','kind':'click','status':status}]
                with mock.patch.object(h.actions,'_row',return_value=row),mock.patch.object(h,'_pacing',return_value=pacing):
                    with self.assertRaisesRegex(ValueError,'ERR_PUBLICATION_RENEWAL'):
                        h.call('renew_cloud_unsubmitted_publication_claim',args)
            pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'step_id':'final','kind':'click','status':'not_applied'}]
            with mock.patch.object(h.actions,'_row',return_value=row),mock.patch.object(h,'_pacing',return_value=pacing),mock.patch.object(h,'_verify_dispatch_review') as review,mock.patch.object(h.actions,'renew_unsubmitted_publication_claim',return_value={'ok':True}) as renew:
                self.assertEqual(h.call('renew_cloud_unsubmitted_publication_claim',args),{'ok':True})
                review.assert_called_once();renew.assert_called_once_with(**args)
        finally:f.tearDown()

class ResultNavigationTests(unittest.TestCase):
    def test_renewed_publication_requires_bound_completed_click_for_readback(self):
        f=test_cloud_host.CloudHostTests();f.setUp()
        try:
            h=f.host
            row={'binding_json':json.dumps({'operation':'publish_content','mutation':True,'expected_account':'a'*24}),'attempt_id':'same','status':'submitted_unknown','submitted_at':1,'claim_context':'fixture'}
            args={'operation_id':'pub','claim_token':'same','step_id':'readback','kind':'navigation','target':'observed notes management menu','purpose':'verify_publication_result','verification_step_id':'submitted'}
            for status in ('not_applied','unknown','completed'):
                pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'step_id':'submitted','kind':'click','status':status}]
                pacing.reserve_step.return_value={'permitted':True}
                db=mock.Mock();db.execute.return_value.fetchone.return_value={'event':'renewed'}
                with mock.patch.object(h.actions,'_row',return_value=row),mock.patch.object(h.actions,'_claim'),mock.patch.object(h.actions,'db',db),mock.patch.object(h.actions,'guard_execution_pause'),mock.patch.object(h,'_pacing',return_value=pacing),mock.patch.object(h.actions,'_guard_dispatch'),mock.patch.object(h,'_verify_dispatch_review'):
                    if status=='completed':
                        self.assertTrue(h.call('reserve_cloud_browser_step',args)['permitted'])
                    else:
                        with self.assertRaisesRegex(ValueError,'ERR_PUBLICATION_RENEWAL'):
                            h.call('reserve_cloud_browser_step',args)
        finally:f.tearDown()

class RetainedEditorRenewalTests(PublicationRenewalTests):
    def retained(self, **changes):
        payload=self.ledger.get('pub')['binding']['payload']
        obs=self.pub_observation();obs.pop('denied_step_id')
        return {**obs,'mode':'retained_editor','visible_title':payload['title'],
                'visible_body':payload['content'],'uploaded_media_match':True,
                'media_evidence_ref':'SYNTHETIC actual same media observed','editor_retained':True,
                'target_schedule_at':payload.get('schedule_at'),'visible_schedule_at':None,**changes}
    def test_retained_editor_after_expiry_keeps_attempt_and_payload(self):
        args=self.inspection_args('pub');args['operation']='publish_content'
        self.ready('pub',**args);self.enabled()
        self.ledger.record_authorization('pub',self.attestation(self.ledger.get('pub'),expires_at=self.stamp(3600)))
        self.ledger.observe_identity('pub',self.identity())
        claim=self.ledger.claim('pub','worker',600,'fixture:browser-1/tab-2')
        self.ledger.begin_submit('pub',claim['claim_token']);self.time+=601
        self.ledger.observe_identity('pub',self.identity());before=self.ledger.get('pub')
        result=self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.retained())
        for key in ('binding','attempt_id','submitted_at','authorization','result'):
            self.assertEqual(result[key],before[key])
        self.assertEqual(result['lease_until'],self.stamp(600))
    def test_mismatch_or_final_permit_cannot_renew_editor(self):
        claim=self.publication_started()
        for change in ({'visible_body':'different'},{'uploaded_media_match':False},{'editor_retained':False},{'target_schedule_at':'wrong'},{'final_submit_invoked':True}):
            with self.assertRaises(ValueError):
                self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.retained(**change))
        with self.ledger._transaction():self.ledger._event('pub','publication_final_step_permitted',{'step_id':'final'})
        with self.assertRaisesRegex(ValueError,'permit already issued|final Send consumed'):
            self.ledger.renew_unsubmitted_publication_claim('pub',claim['claim_token'],600,self.retained())

class RetainedHostTests(unittest.TestCase):
    def test_retained_mode_requires_resolved_steps_and_review_without_fake_denial(self):
        f=test_cloud_host.CloudHostTests();f.setUp()
        try:
            h=f.host;row={'binding_json':json.dumps({'operation':'publish_content'}),'attempt_id':'same'}
            args={'operation_id':'pub','claim_token':'same','lease_seconds':600,'observation':{'mode':'retained_editor'}}
            for status in ('unknown','in_flight','completed'):
                pacing=mock.Mock();pacing.steps_for_attempt.return_value=[{'step_id':'editor','kind':'click','status':status}]
                with mock.patch.object(h.actions,'_row',return_value=row),mock.patch.object(h,'_pacing',return_value=pacing),mock.patch.object(h,'_verify_dispatch_review') as review,mock.patch.object(h.actions,'renew_unsubmitted_publication_claim',return_value={'ok':True}) as renew:
                    if status=='completed':
                        self.assertEqual(h.call('renew_cloud_unsubmitted_publication_claim',args),{'ok':True})
                        review.assert_called_once();renew.assert_called_once()
                    else:
                        with self.assertRaisesRegex(ValueError,'unresolved'):h.call('renew_cloud_unsubmitted_publication_claim',args)
                        renew.assert_not_called()
        finally:f.tearDown()

class RetainedEndToEndTests(PublicationRenewalTests):
    def test_expired_claim_observe_renew_preflight_and_one_final_permit(self):
        from cloud_host import CloudHost
        from cloud_pacing import CloudPacing
        args=self.inspection_args('pub');args['operation']='publish_content'
        args['payload'].update(topic_template='answereq',is_original=True)
        self.ready('pub',**args);self.enabled()
        self.ledger.record_authorization('pub',self.attestation(self.ledger.get('pub'),expires_at=self.stamp(3600)))
        claim=self.ledger.claim('pub','worker',5,'fixture:browser-1/tab-2')
        self.ledger.begin_submit('pub',claim['claim_token'])
        self.data['expected_user_id']='public-user-1';self.save_config()
        h=CloudHost(self.config);h.actions.close();h.actions=self.ledger
        h.pacing=CloudPacing(self.config,clock=lambda:self.time,random_uniform=lambda a,b:a)
        h.pacing.reserve('publish_content',claim['attempt_id']);self.time+=20
        def step(ident,kind='read_observation',**extra):
            return h.call('reserve_cloud_browser_step',{'operation_id':'pub','claim_token':claim['claim_token'],'step_id':ident,'kind':kind,'target':extra.pop('target','synthetic same editor'),**extra})
        def done(ident):h.call('record_cloud_browser_step',{'operation_id':'pub','claim_token':claim['claim_token'],'step_id':ident,'outcome':'completed','evidence':{'ref':'SYNTHETIC ended observation'}})
        # Actual completed-upload fixture, prior to the expired editing session.
        h.pacing.reserve_step(claim['attempt_id'],'uploaded','media_request','synthetic sealed media')
        h.pacing.complete_step('uploaded','completed',{'ref':'SYNTHETIC real upload receipt'})
        self.time+=130
        recovery={'call_ended':True,'expected_account':'public-user-1','evidence_ref':'SYNTHETIC retained editor','recovery_reason':'refresh exact same editor','user_source':'SYNTHETIC','retained_editor':True,'final_submit_invoked':False}
        self.assertTrue(step('observe',recovery_evidence=recovery)['permitted']);done('observe')
        self.ledger.observe_identity('pub',self.identity())
        payload=self.ledger.get('pub')['binding']['payload']
        obs=self.pub_observation();obs.pop('denied_step_id')
        obs.update(mode='retained_editor',visible_title=payload['title'],visible_body=payload['content'],uploaded_media_match=True,media_evidence_ref='SYNTHETIC same media',editor_retained=True,target_schedule_at=payload.get('schedule_at'),visible_schedule_at=None)
        # Review itself is covered by full six-cover dispatch integration; here exercise live gates and pacing together.
        with mock.patch.object(h,'_verify_dispatch_review'),mock.patch.object(h,'_future_publication_policy',return_value={'enabled':True}),mock.patch.object(h,'_publication_topic_expectations',return_value={'template_name':'answereq','template_topics':['actual observed topic'],'template_evidence_ref':'SYNTHETIC template','activity_name':None,'activity_topics':[],'activity_evidence_ref':None}):
            h.call('renew_cloud_unsubmitted_publication_claim',{'operation_id':'pub','claim_token':claim['claim_token'],'lease_seconds':600,'observation':obs})
            import test_cloud_publication_preflight as pf
            f=pf.PublicationPreflightTests();f.setUp();report=f.report
            report.update(expected_account='public-user-1',browser_context='fixture:browser-1/tab-2',observed_at=self.stamp(),visible_title=payload['title'],visible_body=payload['content'])
            h.call('record_cloud_publication_preflight',{'operation_id':'pub','claim_token':claim['claim_token'],'report':report})
            self.time+=20
            self.assertTrue(step('final','click',purpose='submit_publication')['permitted'])
            done('final')
            self.time+=20
            with self.assertRaisesRegex(ValueError,'FINAL_SEND_CONSUMED'):step('final-again','click',purpose='submit_publication')
            self.time+=700  # both claim and identity now expired after real final completion
            readback={'call_ended':True,'expected_account':'public-user-1','evidence_ref':'SYNTHETIC completed final',
                      'recovery_reason':'read management receipt only','user_source':'SYNTHETIC publication instruction',
                      'post_submit_readback':True,'final_submit_invoked':True,'final_step_id':'final',
                      'browser_context':'fixture:browser-1/tab-2','observed_management_target':'笔记管理',
                      'management_target_evidence_ref':'SYNTHETIC actual visible management menu'}
            self.assertTrue(step('after-final-identity',recovery_evidence=readback)['permitted']);done('after-final-identity')
            self.ledger.observe_identity('pub',self.identity());self.time+=20
            self.assertTrue(step('management','navigation',target='笔记管理',purpose='verify_publication_result',verification_step_id='final',recovery_evidence=readback)['permitted']);done('management')
            self.time+=20
            self.assertTrue(step('management-receipt',recovery_evidence=readback)['permitted']);done('management-receipt')
            with self.assertRaisesRegex(ValueError,'already permitted'):
                step('management-again','navigation',target='笔记管理',purpose='verify_publication_result',verification_step_id='final',recovery_evidence=readback)
            for kind in ('click','media_request','search'):
                with self.assertRaises(ValueError):step('bad-'+kind,kind,recovery_evidence=readback)
            with self.assertRaisesRegex(ValueError,'permit already issued|final Send consumed'):
                h.call('renew_cloud_unsubmitted_publication_claim',{'operation_id':'pub','claim_token':claim['claim_token'],'lease_seconds':600,'observation':obs})
        h.pacing.close()
