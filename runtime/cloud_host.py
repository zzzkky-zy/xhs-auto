"""Supported assistant-host dispatch surface. No browser or network execution.

Original platform tool names accept original arguments and return a pending host
plan, never the original Go success-shaped result. A host binds its actual user
request through prepare_cloud_action before executing any state change.
"""
import argparse
import datetime as dt
import json
import hashlib
import os
import stat
import sys
from pathlib import Path
from cloud_actions import CloudActions, READ_OPERATIONS, _text
from cloud_batch import CloudBatch, fingerprint
from cloud_comment_preflight import validate_preflight
from cloud_publication_preflight import validate_publication_preflight
from cloud_navigation_target import observed_note_url
from cloud_pacing import CloudPacing
from cloud_recovery import CloudRecovery
from cloud_cover_history import CloudCoverHistory
from cloud_capabilities import ORIGINAL, LOCAL, AUTH, action_plan, catalog, validate_payload, CREATOR


def obj(properties=None, required=()):
    return {'type':'object','properties':properties or {},'required':list(required),'additionalProperties':False}


S={'type':'string'}
J={'type':'object'}
ID={'operation_id':S}
HELPERS=[
 ('set_cloud_execution_pause',{'paused':{'type':'boolean'},'user_source':S,'reason':S},('paused','user_source','reason'),'Persist explicit user-directed execution pause or resume; audit source is not permission, never renews authorizations or cancels in-flight calls.'),
 ('cloud_daily_comment_status',{'date':S},(),'Summarize actual account-wide Shanghai-day comment receipts and unresolved reservations across all runs; cumulative totals are separate.'),
 ('record_cloud_cover_history',{'image_path':S,'publication_status':S,'source_evidence':S,'reference_person':S},('image_path','publication_status','source_evidence','reference_person'),'Append actual published/scheduled cover evidence and perceptual image fingerprint; no platform mutation.'),
 ('list_cloud_cover_history',{'publication_status':S},(),'List retained published and scheduled cover observations separately.'),
 ('assess_cloud_cover_visuals',{'image_path':S,'reference_person':S,'visual_review':J,'peer_image_paths':{'type':'array','items':S}},('image_path','reference_person'),'Compare pixels/perceptual similarity and explicit visual differences against history and current-batch peers.'),
 ('record_cloud_cover_visual_review',{'image_path':S,'reference_person':S,'visual_review':J,'peer_image_paths':{'type':'array','items':S}},('image_path','reference_person','visual_review'),'Persist an actual passing host visual review bound to exact pixels, history and peer images; not user approval.'),
 ('record_cloud_publication_preflight',{**ID,'claim_token':S,'report':J},('operation_id','claim_token','report'),'Validate actual title/body, applied topic template, original declaration and truthful platform settings before future-policy publication.'),
 ('clear_cloud_recovered_block',{'incident_id':S,'scope':S},('incident_id','scope'),'Clear an existing restriction only from a fresh verified recovery incident with exact account and challenge absence; never changes unknown actions.'),
 ('begin_cloud_browser_recovery',{**ID,'incident_id':S,'observation':J},('operation_id','incident_id','observation'),'Snapshot original action and start durable host recovery diagnosis; never executes or retries a browser action.'),
 ('advance_cloud_browser_recovery',{'incident_id':S,'event':S,'observation':J},('incident_id','event','observation'),'Advance evidence-bound recovery and consume at most one supported reset attempt; host executes official tools separately.'),
 ('get_cloud_browser_recovery',{'incident_id':S},('incident_id',),'Inspect recovery state and preserved unknown without reissuing an execution permit.'),
 ('revise_cloud_publication_batch_settings',{'run_id':S,'settings':J,'user_source':S,'user_quote':S,'evidence_ref':S},('run_id','settings','user_source','user_quote','evidence_ref'),'Version explicitly changed publication settings for an unsubmitted batch; preserve content/media and historical runs.'),
 ('sync_cloud_publication_settings_revision',ID,('operation_id',),'Repair local batch projection from an already committed reviewed settings revision; no new authority or browser action.'),
 ('preview_cloud_publication_settings_revision',{**ID,'payload':J,'user_source':S},('operation_id','payload','user_source'),'Review and preview exact new settings binding for the same unsubmitted manuscript; no browser action.'),
 ('revise_cloud_publication_settings',{**ID,'claim_token':S,'payload':J,'attestation':J,'observation':J},('operation_id','claim_token','payload','attestation','observation'),'Append a reviewed user-directed settings revision on the same retained unsubmitted editor; no reupload or submission.'),
 ('renew_cloud_unsubmitted_publication_claim',{**ID,'claim_token':S,'lease_seconds':{'type':'integer'},'observation':J},('operation_id','claim_token','lease_seconds','observation'),'Renew same reviewed unsubmitted editor with fresh matching evidence, or verified denied final click; no replay or reupload.'),
 ('renew_cloud_schedule_controls_claim',{**ID,'claim_token':S,'lease_seconds':{'type':'integer'},'observation':J},('operation_id','claim_token','lease_seconds','observation'),'Renew same completed-upload inspection solely for schedule controls; no upload replay or publication.'),
 ('renew_cloud_upload_inspection_claim',{**ID,'claim_token':S,'lease_seconds':{'type':'integer'},'observation':J},('operation_id','claim_token','lease_seconds','observation'),'Renew same inspection attempt only before any upload; does not replay a step or authorize publication.'),
 ('review_cloud_upload_inspection',{'run_id':S,'entries':{'type':'array','items':J},'evidence':J},('run_id','entries','evidence'),'Review exact content/media solely for schedule-control inspection, without reserving dates or authorizing final publication.'),
 ('prepare_cloud_upload_inspection',{'run_id':S,'payload':J,'approval_ref':S,'inspection_id':S,'user_source':S,'operation_id':S},('run_id','payload','approval_ref','inspection_id','user_source'),'Seal reviewed images for upload-only schedule inspection; final Publish is prohibited.'),
 ('prepare_cloud_batch_payload',{'run_id':S,'tool':S,'payload':J,'approval_ref':S},('run_id','tool','payload'),'Reuse original local content review and seal exact media. No platform upload or authority granted.'),
 ('cloud_batch_projection_status',{'run_id':S},('run_id',),'Read authoritative linked action outcomes and stale batch projection differences without synchronization or browser actions.'),
 ('bind_cloud_batch_action',{'run_id':S,'operation_id':S},('run_id','operation_id'),'Bind an already prepared exact reviewed action to its local batch, preserving host-report provenance.'),
 ('cloud_capabilities',{},(), 'List all original capabilities, tested scope and cloud deviations.'),
 ('prepare_cloud_action',{'operation':S,'payload':J,'user_source':S,'operation_id':S,'retry_of':S,'observed_target_url':S,'observed_entry_url':S,'source_context':S,'due_at':S,'timezone':S,'media_manifest':{'type':'array','items':J}},('operation','payload','user_source'), 'Bind exact original payload, account, target, provenance and media hashes. Does not authorize or execute.'),
 ('get_cloud_action',ID,('operation_id',),'Read immutable action and evidence.'),
 ('list_cloud_actions',{'status':S,'due_only':{'type':'boolean'},'limit':{'type':'integer'}},(), 'List durable local actions; never executes them.'),
 ('request_cloud_authorization',ID,('operation_id',),'Mark action awaiting host authorization; reads become ready.'),
 ('record_cloud_authorization',{**ID,'attestation':J},('operation_id','attestation'),'Record bound host evidence; not an authorization credential. Host must independently check actual user instruction.'),
 ('record_cloud_unknown_continuation',{**ID,'blocked_operation_id':S,'attestation':J},('operation_id','blocked_operation_id','attestation'),'Record actual host-approved independent comment or image-publication continuation past named preserved unknowns; no blanket authorization or replay.'),
 ('record_cloud_identity',{**ID,'observation':J},('operation_id','observation'),'Record fresh visible account evidence. Not an independent authentication assertion.'),
 ('claim_cloud_action',{**ID,'worker_id':S,'lease_seconds':{'type':'integer'},'browser_context':S},('operation_id','worker_id'),'Claim one due local action with bounded lease; no browser operation.'),
 ('begin_cloud_dispatch',{**ID,'claim_token':S,'target_author_id':S,'thread_id':S,'parent_operation_id':S,'author_evidence':J},('operation_id','claim_token'),'Persist unknown BEFORE first possible side effect. Timeout cannot permit replay.'),
 ('record_cloud_result',{**ID,'claim_token':S,'report':J,'platform_restriction':J},('operation_id','claim_token','report'),'Record actual bounded host-observed outcome, never infer success from click completion.'),
 ('reconcile_cloud_action',{**ID,'report':J},('operation_id','report'),'Resolve unknown only with observed actual outcome; never redispatch.'),
 ('release_cloud_claim',{**ID,'claim_token':S,'reason':S},('operation_id','claim_token','reason'),'Release only a definitively pre-dispatch attempt.'),
 ('cancel_cloud_action',{**ID,'reason':S},('operation_id','reason'),'Cancel only unclaimed pre-dispatch local work.'),
 ('recover_cloud_leases',{},(),'Recover expired pre-dispatch leases; never retry ambiguous dispatch.'),
 ('cloud_action_events',{**ID,'limit':{'type':'integer'}},('operation_id',),'Read append-only evidence history.'),
]
HELPERS += [
 ('reserve_cloud_browser_step',{**ID,'claim_token':S,'step_id':S,'kind':S,'target':S,'recovery_evidence':J,'purpose':S,'verification_step_id':S},('operation_id','claim_token','step_id','kind','target'),'Reserve exactly one official browser operation; never authorizes a compound script or grants user permission.'),
 ('record_cloud_browser_step',{**ID,'claim_token':S,'step_id':S,'outcome':S,'evidence':J,'platform_restriction':J},('operation_id','claim_token','step_id','outcome','evidence'),'Record a single observed browser step; restrictions persist and unknown never replays.'),
 ('record_cloud_comment_preflight',{**ID,'claim_token':S,'report':J},('operation_id','claim_token','report'),'Validate and append host-observed expanded editor/exact text/enabled Send evidence; not independent browser proof or authority.'),
 ('get_cloud_browser_steps',ID,('operation_id',),'Inspect persisted step outcomes/deadlines locally without touching browser.'),
 ('record_cloud_pacing_result',{'attempt_id':S,'status':S,'evidence':J},('attempt_id','status','evidence'),'Record actual read/identity reservation outcome; never grants execution permission.'),
 ('report_cloud_platform_block',{'scope':S,'reason':S,'evidence':J},('scope','reason','evidence'),'Persist observed platform restriction; elapsed time does not clear it.'),
 ('clear_cloud_platform_block',{'scope':S,'evidence':J},('scope','evidence'),'Clear restriction only using fresh matching-account resolution evidence.'),
]
TOOLS=[{'name':name,'description':description,'inputSchema':obj(properties,required)} for name,properties,required,description in HELPERS]
TOOLS += [{'name':name,'description':'Cloud host adapter: '+item['description']+' Returns a pending host plan or explicit limitation, NOT a platform success receipt.','inputSchema':item['inputSchema']} for name,item in ORIGINAL.items()]


def media_manifest(operation,payload):
    paths=payload.get('images',[]) if operation=='publish_content' else [payload.get('video')] if operation=='publish_with_video' else []
    result=[]
    for value in paths:
        if not isinstance(value,str):raise ValueError('local media path required')
        path=Path(value)
        if not path.is_absolute() or str(path)!=value or '..' in path.parts or any(p.is_symlink() for p in (path,*path.parents)):
            raise ValueError('Materialize approved remote assets to normalized local files before submit_task; no download occurs here')
        try:
            fd=os.open(path,os.O_RDONLY|os.O_NOFOLLOW|os.O_NONBLOCK)
            with os.fdopen(fd,'rb') as source:
                before=os.fstat(source.fileno())
                if not stat.S_ISREG(before.st_mode) or not 0<before.st_size<=2*1024**3:raise ValueError('media must be regular file of 1 byte..2 GiB')
                remaining=before.st_size;digest=hashlib.sha256()
                while remaining:
                    block=source.read(min(1024*1024,remaining))
                    if not block:raise ValueError('media changed during hashing')
                    digest.update(block);remaining-=len(block)
                after=os.fstat(source.fileno())
                if source.read(1) or (before.st_dev,before.st_ino,before.st_size,before.st_mtime_ns)!=(after.st_dev,after.st_ino,after.st_size,after.st_mtime_ns):raise ValueError('media changed during hashing')
        except OSError as error:raise ValueError('local media unavailable') from error
        result.append({'path':value,'size':before.st_size,'sha256':digest.hexdigest()})
    return result


CONTENT_WRITES = frozenset({'publish_content', 'publish_with_video', 'post_comment_to_feed', 'reply_comment_in_feed'})


class CloudHost:
    def __init__(self,config):
        self.config=config
        self.settings=json.loads(Path(config).read_text())
        self.batch=None
        self.pacing=None
        self._dispatch_context={}
        self.actions=CloudActions(config,payload_validator=lambda op,p:validate_payload('publish_content' if op=='inspect_publication_schedule' else op,p),host_verifier=self._verify_dispatch_review)

    def close(self):
        if self.batch is not None:self.batch.close()
        if self.pacing is not None:self.pacing.close()
        self.actions.close()

    def _pacing(self):
        if self.pacing is None:self.pacing=CloudPacing(self.config)
        return self.pacing

    def _batch(self):
        if self.batch is None:self.batch=CloudBatch(self.config,action_ledger=self.actions)
        return self.batch

    def _review_payload(self, operation, payload, approval_ref=''):
        """Every content mutation must use an explicit, current reviewed run.

        A source reference records provenance only; host policy still decides
        whether the actual user authorized the external action.
        """
        run_id = payload.get('run_id')
        if not isinstance(run_id, str) or not run_id.strip():
            raise ValueError('ERR_CONTENT_RUN_REQUIRED: content writes require an explicit reviewed run_id')
        return self._batch().prepare_payload(run_id, operation, payload, approval_ref)

    def _verify_dispatch_review(self, phase, binding, evidence):
        if phase not in {'claim', 'begin_submit'}:
            return True
        if binding['operation']=='inspect_publication_schedule':
            prepared=self._batch().prepare_upload_inspection(binding['payload']['run_id'],binding['payload'],binding['user_source'],inspection_id=binding['target']['inspection_id'])
            if prepared['payload']!=binding['payload'] or prepared['media_manifest']!=binding['media_manifest']:
                raise ValueError('ERR_INSPECTION_CHANGED: reviewed upload payload or media changed')
        if binding['operation'] in CONTENT_WRITES:
            payload = binding['payload']
            # Recheck profile, plan, stages, source bytes, allowed tools and quota
            # at BOTH boundaries, not merely when the action was prepared.
            prepared = self._review_payload(binding['operation'], payload, binding['user_source'])
            self._batch()._linked_action(prepared['run_id'], binding['operation_id'])
            if fingerprint(binding['operation'], prepared['payload']) != fingerprint(binding['operation'], payload):
                raise ValueError('ERR_CONTENT_REVIEW_STALE: immutable action differs from current reviewed payload')
        if phase == 'begin_submit' and binding['operation'] in {'post_comment_to_feed','reply_comment_in_feed'} and self.actions._config().get('cloud_daily_comment_limit')==50:
            from cloud_daily_comments import DailyComments
            DailyComments(self.actions).guard(binding['operation_id'])
        if phase == 'begin_submit':
            context = dict(self._dispatch_context)
            if context.get('thread_id') is not None and context['thread_id'] != binding['payload'].get('feed_id'):
                raise ValueError('ERR_PACING_BINDING_MISMATCH: observed thread differs from immutable target')
            if binding['operation'] == 'reply_comment_in_feed':
                target=binding['payload'].get('comment_id')
                author_evidence=context.get('author_evidence')
                if not target or not isinstance(author_evidence,dict) or author_evidence.get('target_comment_id') != target:
                    raise ValueError('ERR_PACING_BINDING_MISMATCH: reply requires the exact observed immutable comment_id')
            if context.get('parent_operation_id'):
                parent=self.actions.get(context['parent_operation_id'])
                parent_binding=parent['binding']
                if (parent_binding['expected_account'] != binding['expected_account']
                        or parent_binding['operation'] != 'post_comment_to_feed'
                        or parent_binding['payload'].get('feed_id') != binding['payload'].get('feed_id')):
                    raise ValueError('ERR_PACING_BINDING_MISMATCH: paired main comment account or thread differs')
                if parent['status'] != 'host_reported_success':
                    raise ValueError('ERR_DEPENDENCY_NOT_SUCCEEDED: paired main comment needs actual success')
                context['parent_operation_id']=parent['attempt_id']
            reservation=self._pacing().reserve(binding['operation'],evidence['attempt_id'],**context)
            if not reservation['permitted']:
                raise ValueError('ERR_BROWSER_PACING: '+json.dumps(reservation,ensure_ascii=False))
        return True

    def _future_publication_policy(self, binding):
        if binding['operation']!='publish_content' or not binding['payload'].get('run_id'):return None
        batch=self._batch();run=batch.continuous.run(binding['payload']['run_id'])
        _,profile,_=batch.continuous.content.profile_for_run(run)
        return profile.get('future_content_policy')

    def _browser_step(self, args, complete=False):
        row=self.actions._row(args['operation_id'])
        self.actions._claim(row,args['claim_token'],live=not complete and not args.get('recovery_evidence'))
        binding=json.loads(row['binding_json'])
        if complete:
            step=self._pacing().get_step(args['step_id'])
            if step['attempt_id'] != row['attempt_id']:
                raise ValueError('ERR_STEP_BINDING: step belongs to another action attempt')
            result=self._pacing().complete_step(args['step_id'],args['outcome'],args['evidence'],platform_restriction=args.get('platform_restriction'))
            if args['outcome']=='unknown' or args.get('platform_restriction'):
                result=dict(result, recovery_workflow={'required_before_replay':True,'tool':'begin_cloud_browser_recovery','operation_id':args['operation_id'],'guide':'docs/CLOUD-BROWSER-RECOVERY.md','automatic_retry':False})
            return result
        if args['kind']!='read_observation' and binding['mutation']:
            self.actions.guard_retry_lineage(binding)
        self.actions.guard_execution_pause(binding, observation_only=args['kind']=='read_observation')
        if args['kind']=='reload' and not (isinstance(args.get('recovery_evidence'),dict) and args['recovery_evidence'].get('page_reload') is True):
            raise ValueError('ERR_PAGE_RELOAD: only incident-bound one-use recovery reload')
        if args['kind']=='card_read' and binding['operation'] not in {'user_profile','get_my_profile','search_feeds','post_comment_to_feed','reply_comment_in_feed'}:
            raise ValueError('ERR_PROFILE_CARD_ONLY: card_read is only for a bound profile/search action')
        if binding['operation'] in {'post_comment_to_feed','reply_comment_in_feed'} and (binding.get('target',{}).get('native_card_entry') or args['kind']=='card_read'):
            if not binding.get('target',{}).get('native_card_entry'):raise ValueError('ERR_COMMENT_CARD_ENTRY: prepare exact observed entry first')
            from cloud_navigation_target import comment_card_step
            comment_card_step(binding,args,self._pacing().steps_for_attempt(row['attempt_id']))
        if binding['operation']=='search_feeds':
            from cloud_navigation_target import search_card_step
            search_card_step(binding,args,self._pacing().steps_for_attempt(row['attempt_id']))
        if binding['operation'] in {'user_profile','get_my_profile'}:
            from cloud_navigation_target import profile_card_step
            profile_card_step(binding, args, self._pacing().steps_for_attempt(row['attempt_id']))
        if binding['operation']=='inspect_publication_schedule':
            allowed={'navigation':{'open_creator'},'click':{'select_image_upload_mode','open_upload_chooser','inspect_schedule_controls'},'media_request':{'upload_reviewed_media'},'read_observation':{None,'inspect_schedule_controls'},'scroll':{'inspect_schedule_controls'}}
            if args['kind'] not in allowed or args.get('purpose') not in allowed[args['kind']]:
                raise ValueError('ERR_INSPECTION_ONLY: upload/read schedule controls only; no content entry, save or final Publish')
        if binding['operation']=='inspect_publication_schedule':
            uploaded=any(step['kind']=='media_request' and step['status']=='completed'
                         for step in self._pacing().steps_for_attempt(row['attempt_id']))
            if uploaded and (args['kind'] not in {'read_observation','click','scroll'}
                             or (args['kind']!='read_observation' and args.get('purpose')!='inspect_schedule_controls')):
                raise ValueError('ERR_INSPECTION_ONLY: after upload only schedule controls and observation; no navigation or upload replay')
        if binding['operation']=='publish_content':
            renewed=self.actions.db.execute("SELECT 1 FROM action_events WHERE account=? AND operation_id=? AND event IN ('unsubmitted_publication_claim_renewed','unsubmitted_publication_settings_revised')",(self.actions.account,args['operation_id'])).fetchone()
            if renewed and args['kind'] in {'navigation','search','media_request'}:
                verification = args['kind']=='navigation' and args.get('purpose')=='verify_publication_result'
                steps=self._pacing().steps_for_attempt(row['attempt_id']) if verification else []
                submitted=next((step for step in steps if step['step_id']==args.get('verification_step_id')),None)
                if not verification or not submitted or submitted['kind']!='click' or submitted['status']!='completed':
                    raise ValueError('ERR_PUBLICATION_RENEWAL: only bound post-submit result navigation; no reupload')
        purpose=args.get('purpose')
        if purpose=='submit_publication':
            permitted_steps=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='publication_final_step_permitted'",(self.actions.account,args['operation_id'])).fetchall()
            for previous in permitted_steps:
                previous_id=json.loads(previous['detail'])['step_id']
                if previous_id!=args['step_id'] and self._pacing().get_step(previous_id)['status']!='not_applied':
                    raise ValueError('ERR_PUBLICATION_PREFLIGHT: prior final permit unresolved or submitted; no resend')
            if args['kind']!='click' or not self._future_publication_policy(binding):
                raise ValueError('ERR_PUBLICATION_PREFLIGHT: future-policy publication click required')
            latest=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='publication_editor_preflight' ORDER BY id DESC LIMIT 1",(self.actions.account,args['operation_id'])).fetchone()
            if not latest:raise ValueError('ERR_PUBLICATION_PREFLIGHT: record actual template/original/body settings before Publish')
            detail=json.loads(latest['detail'])
            if detail['attempt_id']!=row['attempt_id'] or detail['binding_digest']!=row['binding_digest']:raise ValueError('ERR_PUBLICATION_PREFLIGHT: stale action binding')
            validate_publication_preflight(binding,row['claim_context'],detail['report'],self.actions.clock())
        if purpose is not None and purpose!='submit_publication' and binding['operation']!='inspect_publication_schedule' and not (purpose=='solve_captcha' and args['kind']=='click' and isinstance(args.get('recovery_evidence'),dict) and args['recovery_evidence'].get('challenge_interaction') is True) and not (purpose=='recover_official_session' and args['kind']=='navigation' and isinstance(args.get('recovery_evidence'),dict) and args['recovery_evidence'].get('session_recovery') is True) and not (binding['operation']=='publish_content' and purpose=='verify_publication_result' and args['kind']=='navigation'):
            if purpose!='submit_comment' or args['kind']!='click':raise ValueError('ERR_COMMENT_PREFLIGHT: unsupported step purpose')
            latest=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='comment_editor_preflight' ORDER BY id DESC LIMIT 1",(self.actions.account,args['operation_id'])).fetchone()
            if not latest:raise ValueError('ERR_COMMENT_PREFLIGHT: record actual editor preflight before Send')
            detail=json.loads(latest['detail'])
            if detail['attempt_id']!=row['attempt_id'] or detail['binding_digest']!=row['binding_digest']:raise ValueError('ERR_COMMENT_PREFLIGHT: stale action binding')
            validate_preflight(binding,row['claim_context'],detail['report'],self.actions.clock())
            if self.actions._config().get('cloud_daily_comment_limit')==50:
                from cloud_daily_comments import DailyComments
                DailyComments(self.actions).guard(args['operation_id'])
        recovery=args.get('recovery_evidence')
        if row['status'] != 'submitted_unknown' and not (recovery is not None and row['submitted_at'] is not None):
            raise ValueError('ERR_STEP_STATE: enter dispatch before browser steps; terminal actions only permit bounded recovery observation')
        if recovery is not None:
            if (not isinstance(recovery,dict) or (args['kind'] != 'read_observation' and not (args['kind']=='reload' and recovery.get('page_reload') is True) and not (args['kind']=='click' and args.get('purpose')=='solve_captcha' and recovery.get('challenge_interaction') is True) and not (args['kind']=='navigation' and args.get('purpose')=='recover_official_session' and recovery.get('session_recovery') is True) and not (args['kind']=='navigation' and args.get('purpose')=='verify_publication_result' and recovery.get('post_submit_readback') is True))
                    or recovery.get('call_ended') is not True
                    or recovery.get('expected_account') != binding['expected_account']):
                raise ValueError('ERR_STEP_RECOVERY: bounded observation requires matching account and actual prior-call-ended evidence')
            for key in ('evidence_ref','recovery_reason','user_source'):
                _text(recovery.get(key),key,1000)
            if recovery.get('post_submit_readback') is True:
                if (binding['operation']!='publish_content' or args['kind'] not in {'navigation','read_observation'}
                        or recovery.get('final_submit_invoked') is not True
                        or recovery.get('browser_context')!=row['claim_context']):
                    raise ValueError('ERR_PUBLICATION_READBACK: same-context submitted publication observation only')
                final_id=recovery.get('final_step_id')
                final=self._pacing().get_step(final_id)
                if final['attempt_id']!=row['attempt_id'] or final['kind']!='click' or final['status']!='completed':
                    raise ValueError('ERR_PUBLICATION_READBACK: completed same-attempt final click required')
                permits=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='publication_final_step_permitted'",(self.actions.account,args['operation_id'])).fetchall()
                if not any(json.loads(p['detail']).get('step_id')==final_id for p in permits):
                    raise ValueError('ERR_PUBLICATION_READBACK: exact final publication permit required')
                if args['kind']=='navigation':
                    if (args.get('verification_step_id')!=final_id or args['target']!=recovery.get('observed_management_target')
                            or args.get('purpose')!='verify_publication_result' or args['target']!='笔记管理'):
                        raise ValueError('ERR_PUBLICATION_READBACK: exact observed management menu required')
                    _text(recovery.get('management_target_evidence_ref'),'management target evidence',1000)
                    if any(json.loads(p['detail']).get('attempt_id')==row['attempt_id'] for p in self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='publication_readback_navigation_permitted'",(self.actions.account,args['operation_id']))):
                        raise ValueError('ERR_PUBLICATION_READBACK: management navigation already permitted; observe existing page')
            if recovery.get('retained_editor') is True:
                if (binding['operation']!='publish_content' or args['kind']!='read_observation'
                        or row['status']!='submitted_unknown' or recovery.get('final_submit_invoked') is not False):
                    raise ValueError('ERR_PUBLICATION_RENEWAL: passive retained editor observation only')
                steps=self._pacing().steps_for_attempt(row['attempt_id'])
                if any(step['status'] in {'unknown','in_flight'} for step in steps):
                    raise ValueError('ERR_PUBLICATION_RENEWAL: unresolved browser calls')
                if self.actions.db.execute("SELECT 1 FROM action_events WHERE account=? AND operation_id=? AND event='publication_final_step_permitted'",(self.actions.account,args['operation_id'])).fetchone():
                    raise ValueError('ERR_PUBLICATION_RENEWAL: final permit already issued')
            if recovery.get('page_reload') is True:
                incident=CloudRecovery(self.actions).get(recovery.get('incident_id'))
                if (incident['operation_id']!=args['operation_id'] or incident['state']!='reload_consumed'
                        or not incident.get('page_reload_consumed') or args['kind']!='reload'
                        or args['target']!=incident['same_page']['url']
                        or recovery.get('tab_id')!=incident['same_page']['tab_id']
                        or row['claim_context']!=incident['same_page']['browser_context']):
                    raise ValueError('ERR_PAGE_RELOAD: exact same incident, page, tab and context required')
                if self.actions.clock()<incident['reload_not_before']:
                    return {'permitted':False,'status':'wait_before_reload','retry_at':incident['reload_not_before']}
                with self.actions._transaction():
                    entries=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='recovery_reload_step_bound'",(self.actions.account,args['operation_id'])).fetchall()
                    prior=next((json.loads(e['detail']) for e in entries if json.loads(e['detail']).get('incident_id')==incident['incident_id']),None)
                    if prior and prior['step_id']!=args['step_id']:raise ValueError('ERR_PAGE_RELOAD: already bound to original one-use step')
                    if prior is None:self.actions._event(args['operation_id'],'recovery_reload_step_bound',{'incident_id':incident['incident_id'],'step_id':args['step_id']})
            if recovery.get('session_recovery') is True:
                incident_id=recovery.get('incident_id')
                if not incident_id:raise ValueError('ERR_SESSION_RECOVERY: begin/advance durable recovery incident first')
                incident=CloudRecovery(self.actions).get(incident_id)
                if incident['operation_id']!=args['operation_id'] or not incident['new_tab_consumed']:
                    raise ValueError('ERR_SESSION_RECOVERY: bound one-use official-home incident required')
                if incident['state']!='new_tab_consumed':
                    raise ValueError('ERR_SESSION_RECOVERY: official-home inspection is no longer pending')
                if args['kind']!='navigation' or args.get('purpose')!='recover_official_session' or args['target']!='https://www.xiaohongshu.com/':
                    raise ValueError('ERR_SESSION_RECOVERY: official home authentication inspection only')
                for key in ('new_browser_context','session_evidence_ref'):
                    _text(recovery.get(key),key,1000)
                if recovery.get('prior_session_unavailable') is not True:
                    raise ValueError('ERR_SESSION_RECOVERY: actual missing-session evidence required')
                with self.actions._transaction():
                    entries=self.actions.db.execute("SELECT detail FROM action_events WHERE account=? AND operation_id=? AND event='recovery_home_step_bound'",(self.actions.account,args['operation_id'])).fetchall()
                    prior=next((json.loads(entry['detail']) for entry in entries if json.loads(entry['detail']).get('incident_id')==incident_id),None)
                    if prior and prior['step_id']!=args['step_id']:
                        raise ValueError('ERR_SESSION_RECOVERY: homepage step already bound; inspect original step without replay')
                    if prior is None:self.actions._event(args['operation_id'],'recovery_home_step_bound',{'incident_id':incident_id,'step_id':args['step_id']})
            if recovery.get('challenge_interaction') is True:
                _text(recovery.get('challenge_authorization_source'),'challenge_authorization_source',2000)
                if args['kind']!='click' or args.get('purpose')!='solve_captcha':
                    raise ValueError('ERR_CAPTCHA_SCOPE: challenge UI only')
            # This is an inert host attestation, not independent user authority.
            # Ordinary recovery remains passive; explicit CAPTCHA scope is
            # separately bound to an actual CAPTCHA block by the pacing gate.
            # This is an inert host attestation, never a permission to interact,
            # solve a challenge, navigate or replay an uncertain browser action.
            if self.actions._config().get('expected_user_id') != binding['expected_account']:
                raise ValueError('ERR_ACCOUNT_CHANGED')
        elif args['kind'] == 'read_observation':
            # A passive DOM/screenshot observation is how a host verifies a
            # submitted result or refreshes identity. Requiring fresh identity
            # here creates a circular gate after a successful send. Keep the
            # same live claim/account/serialization; never permit UI input.
            if self.actions._config().get('expected_user_id') != binding['expected_account']:
                raise ValueError('ERR_ACCOUNT_CHANGED')
            self.actions._guard_browser_activity(row)
        else:
            self.actions._guard_dispatch(row,browser_context=row['claim_context'])
            self._verify_dispatch_review('claim',binding,{'attempt_id':row['attempt_id']})
        result=self._pacing().reserve_step(row['attempt_id'],args['step_id'],args['kind'],args['target'],recovery=recovery is not None,recovery_evidence=recovery)
        if result.get('permitted') and args['kind']=='navigation' and isinstance(recovery,dict) and recovery.get('post_submit_readback') is True:
            with self.actions._transaction():
                self.actions._event(args['operation_id'],'publication_readback_navigation_permitted',{'attempt_id':row['attempt_id'],'step_id':args['step_id'],'target':args['target']})
        if result.get('permitted') and purpose=='submit_publication':
            with self.actions._transaction():
                self.actions._event(args['operation_id'],'publication_final_step_permitted',{'attempt_id':row['attempt_id'],'step_id':args['step_id']})
        return result

    def _plan(self,operation,payload):
        self.settings=json.loads(Path(self.config).read_text())
        plan=action_plan(operation,payload,self.settings.get('expected_user_id',''))
        if operation in {'user_profile','get_my_profile'}:
            plan['profile_card_policy']={'mode':'card_only','initial_profile_navigation_only':True,'direct_note_url_navigation':False,'canonical_fallback':False,'open_card_step_kind':'card_read','full_source_requires':'Actually observed complete body and every image; card summary alone is incomplete.'}
            plan['steps'].append({'phase':'read_profile_cards','require':'Stay in the owned profile page; native-click visible cards and close observed modal controls. Each distinct card open is a separately charged card_read step bound to its observed note ID. Never extract href to open another note page.'})
        if operation=='search_feeds':
            plan['search_card_policy']={'mode':'card_only','initial_query_step_kind':'search','open_card_step_kind':'card_read','target_format':'search-card:<observed note ID>','direct_note_url_navigation':False,'each_card_charged_read':True}
            plan['steps'].append({'phase':'read_search_cards','require':'In the same owned search page, native-click actual visible cards; each opening is a separately charged card_read bound to actual note ID. Keep query/filter actions separately charged as search. Read complete body/all images before qualifying a source; retain per-note source evidence for comment review. Close observed modal; never open extracted note URLs, external links or frames.'})
        return plan

    def call(self,name,args):
        definition=next((x for x in TOOLS if x['name']==name),None)
        if definition is None:raise ValueError('ERR_CLOUD_UNSUPPORTED: unknown tool')
        schema=definition['inputSchema']
        if not isinstance(args,dict) or not set(schema.get('required',[])).issubset(args) or (schema.get('additionalProperties') is False and set(args)-set(schema.get('properties',{}))):raise ValueError('invalid tool arguments')
        if name=='set_cloud_execution_pause':return self.actions.set_execution_pause(**args)
        if name=='cloud_daily_comment_status':
            from cloud_daily_comments import DailyComments
            return DailyComments(self.actions).summary(**args)
        if name in {'record_cloud_cover_history','list_cloud_cover_history','assess_cloud_cover_visuals','record_cloud_cover_visual_review'}:
            history=CloudCoverHistory(self.actions)
            method={'record_cloud_cover_history':history.ingest,'list_cloud_cover_history':history.list_history,'assess_cloud_cover_visuals':history.assess,'record_cloud_cover_visual_review':history.record_review}[name]
            return method(**args)
        if name=='clear_cloud_recovered_block':
            incident=CloudRecovery(self.actions).get(args['incident_id'])
            if incident['state']!='verified':raise ValueError('ERR_RECOVERY_UNVERIFIED: observe exact account and challenge absence first')
            observation=incident['observation'];identity=observation['identity']
            if identity['account_id']!=self.actions._config().get('expected_user_id'):
                raise ValueError('ERR_ACCOUNT_CHANGED')
            evidence={'expected_account':identity['account_id'],'status':'resolved',
                      'observed_at':identity['observed_at'],'evidence_ref':observation['evidence_ref'],
                      'challenge_absent':True,'incident_id':args['incident_id'],
                      'identity_evidence_ref':identity['evidence_ref']}
            return self._pacing().clear_block(args['scope'],evidence)
        if name=='begin_cloud_browser_recovery':return CloudRecovery(self.actions).begin(**args)
        if name=='advance_cloud_browser_recovery':return CloudRecovery(self.actions).advance(**args)
        if name=='get_cloud_browser_recovery':return CloudRecovery(self.actions).get(**args)
        if name=='cloud_capabilities':return catalog()
        if name=='record_cloud_publication_preflight':
            row=self.actions._row(args['operation_id']);binding=json.loads(row['binding_json'])
            if not self._future_publication_policy(binding):raise ValueError('ERR_PUBLICATION_PREFLIGHT: future-policy run required; historical runs unchanged')
            with self.actions._transaction():
                row=self.actions._row(args['operation_id']);self.actions._claim(row,args['claim_token'],live=True)
                if row['status']!='submitted_unknown':raise ValueError('ERR_PUBLICATION_PREFLIGHT: active dispatch required')
                result=validate_publication_preflight(binding,row['claim_context'],args['report'],self.actions.clock())
                self.actions._event(args['operation_id'],'publication_editor_preflight',{'binding_digest':row['binding_digest'],'attempt_id':row['attempt_id'],'report':args['report']})
            return result
        if name=='record_cloud_comment_preflight':
            with self.actions._transaction():
                row=self.actions._row(args['operation_id']);self.actions._claim(row,args['claim_token'],live=True)
                if row['status']!='submitted_unknown':raise ValueError('ERR_COMMENT_PREFLIGHT: active dispatch required')
                binding=json.loads(row['binding_json'])
                result=validate_preflight(binding,row['claim_context'],args['report'],self.actions.clock())
                detail={'binding_digest':row['binding_digest'],'attempt_id':row['attempt_id'],'report':args['report']}
                self.actions._event(args['operation_id'],'comment_editor_preflight',detail)
            return result
        if name=='reserve_cloud_browser_step':return self._browser_step(args)
        if name=='record_cloud_browser_step':return self._browser_step(args,complete=True)
        if name=='get_cloud_browser_steps':
            action=self.actions.get(args['operation_id'])
            return {'operation_id':args['operation_id'],'steps':self._pacing().steps_for_attempt(action['attempt_id']) if action['attempt_id'] else []}
        if name=='record_cloud_pacing_result':return self._pacing().record_result(**args)
        if name=='report_cloud_platform_block':
            return dict(self._pacing().report_block(**args),recovery_workflow={'tool':'begin_cloud_browser_recovery','guide':'docs/CLOUD-BROWSER-RECOVERY.md','preserve_original_operation':True,'automatic_retry':False})
        if name=='clear_cloud_platform_block':return self._pacing().clear_block(**args)
        if name=='begin_cloud_dispatch':
            self._dispatch_context={k:v for k,v in args.items() if k not in {'operation_id','claim_token'}}
            try:return self.actions.begin_submit(args['operation_id'],args['claim_token'])
            finally:self._dispatch_context={}
        if name in {'record_cloud_result','reconcile_cloud_action'}:
            method=self.actions.report_result if name=='record_cloud_result' else self.actions.reconcile
            values=dict(args);restriction=values.pop('platform_restriction',None)
            before=self.actions.get(args['operation_id'])
            if restriction is not None:
                if not isinstance(restriction,dict) or set(restriction) != {'scope','reason','evidence'}:
                    raise ValueError('ERR_PLATFORM_RESTRICTION: exact scope/reason/evidence required')
                if before['result'] != args['report']:
                    self._pacing().report_block(**restriction)
            if (self.actions._config().get('require_browser_steps') is True
                    and before['result'] != args['report']):
                steps=self._pacing().steps_for_attempt(before['attempt_id']) if before['attempt_id'] else []
                terminal=args['report']['status'] in {'host_reported_success','not_applied'}
                missing=(args['report']['status']=='host_reported_success' and
                         (not steps or not any(step['status']=='completed' for step in steps)
                          or any(step['status']=='waiting' for step in steps)))
                if missing or (terminal and any(step['status'] in {'in_flight','unknown'} for step in steps)):
                    raise ValueError('ERR_BROWSER_STEP_EVIDENCE: resolve every actual browser step before a terminal report')
            result=method(**values)
            # Original receipts remain authoritative if a second local ledger
            # fails. Retrying this report is local and never repeats the UI action.
            if self._pacing().events(result['attempt_id']):
                self._pacing().record_result(result['attempt_id'],result['status'],args['report'])
            if result['binding']['operation'] in READ_OPERATIONS:
                result['read_at']=args['report'].get('read_at',args['report']['observed_at']);result['cached']=False
            return result
        if name=='sync_cloud_publication_settings_revision':return self._batch().sync_publication_settings_revision(self.actions.get(args['operation_id']))
        if name=='revise_cloud_publication_batch_settings':
            # Only ended, never-final-submitted editors may enter settings revision.
            batch=self._batch()
            for task in batch.db.execute("SELECT id FROM tasks WHERE run_id=? AND tool='publish_content'",(args['run_id'],)):
                action=self.actions.get(task['id'])
                if action.get('attempt_id') and any(step['status'] in {'unknown','in_flight'} for step in self._pacing().steps_for_attempt(action['attempt_id'])):
                    raise ValueError('ERR_PUBLICATION_REVISION: unresolved browser call')
            return batch.revise_publication_settings(**args)
        if name in {'preview_cloud_publication_settings_revision','revise_cloud_publication_settings'}:
            source=args.get('user_source') or args.get('attestation',{}).get('user_source','')
            prepared=self._review_payload('publish_content',args['payload'],source)
            preview=self.actions.preview_unsubmitted_publication_settings(args['operation_id'],prepared['payload'],source)
            if name=='preview_cloud_publication_settings_revision':return preview
            current=self.actions.get(args['operation_id'])
            if any(step['status'] in {'unknown','in_flight'} for step in self._pacing().steps_for_attempt(current['attempt_id'])):
                raise ValueError('ERR_PUBLICATION_REVISION: unresolved browser call')
            value=self.actions.revise_unsubmitted_publication_settings(args['operation_id'],args['claim_token'],prepared['payload'],args['attestation'],args['observation'])
            self._batch().sync_publication_settings_revision(value)
            return value
        if name=='renew_cloud_unsubmitted_publication_claim':
            row=self.actions._row(args['operation_id']);binding=json.loads(row['binding_json'])
            if binding['operation']!='publish_content':raise ValueError('ERR_PUBLICATION_RENEWAL: publication only')
            steps=self._pacing().steps_for_attempt(row['attempt_id'])
            if any(step['status'] in {'in_flight','unknown'} for step in steps):
                raise ValueError('ERR_PUBLICATION_RENEWAL: unresolved browser call')
            if args['observation'].get('mode')!='retained_editor':
                denied=next((step for step in steps if step['step_id']==args['observation'].get('denied_step_id')),None)
                if not denied or denied['kind']!='click' or denied['status']!='not_applied':
                    raise ValueError('ERR_PUBLICATION_RENEWAL: exact final-click not-applied receipt required')
            self._verify_dispatch_review('claim',binding,{'attempt_id':row['attempt_id']})
            return self.actions.renew_unsubmitted_publication_claim(**args)
        if name in {'renew_cloud_upload_inspection_claim','renew_cloud_schedule_controls_claim'}:
            row=self.actions._row(args['operation_id'])
            binding=json.loads(row['binding_json'])
            if binding['operation']!='inspect_publication_schedule':raise ValueError('ERR_INSPECTION_RENEWAL: upload inspection only')
            steps=self._pacing().steps_for_attempt(row['attempt_id'])
            if any(step['status'] in {'in_flight','unknown'} for step in steps):
                raise ValueError('ERR_INSPECTION_RENEWAL: resolve actual browser calls before renewal')
            after_upload = name=='renew_cloud_schedule_controls_claim'
            if after_upload:
                if not any(step['kind']=='media_request' and step['status']=='completed' for step in steps):
                    raise ValueError('ERR_INSPECTION_RENEWAL: completed upload required for controls-only renewal')
            elif any(step['kind']=='media_request' and step['status']!='not_applied' for step in steps):
                raise ValueError('ERR_INSPECTION_RENEWAL: uploaded or uncertain media cannot be renewed')
            self._verify_dispatch_review('claim',binding,{'attempt_id':row['attempt_id']})
            return self.actions.renew_inspection_claim(**args,**({'after_upload':True} if after_upload else {}))
        if name=='review_cloud_upload_inspection':return self._batch().review_upload_inspection(**args)
        if name=='prepare_cloud_upload_inspection':
            prepared=self._batch().prepare_upload_inspection(args['run_id'],args['payload'],args['approval_ref'],inspection_id=args['inspection_id'])
            settings=self.actions._config()
            values={'user_source':args['user_source'],'media_manifest':prepared['media_manifest']}
            if args.get('operation_id'):values['operation_id']=args['operation_id']
            value=self.actions.prepare('inspect_publication_schedule',prepared['payload'],expected_account=settings['expected_user_id'],target={'url':CREATOR,'inspection_id':args['inspection_id'],'execution_scope':'upload_and_inspect_schedule_only'},**values)
            value['host_plan']={'operation':'inspect_publication_schedule','target_url':CREATOR,'execution_scope':'upload_and_inspect_schedule_only','final_publish_allowed':False,'schedule_submission_allowed':False,'allowed_steps':['open_creator','select_image_upload_mode','open_upload_chooser','upload_reviewed_media','inspect_schedule_controls'],'media_manifest':prepared['media_manifest']}
            return value
        if name=='prepare_cloud_batch_payload':return self._batch().prepare_payload(**args)
        if name=='cloud_batch_projection_status':
            if self.batch is None:
                raise ValueError('ERR_BATCH_NOT_OPEN: inspect an already initialized batch; diagnosis must not create or migrate storage')
            return self.batch.projection_status(**args)
        if name=='bind_cloud_batch_action':return self._batch().bind_action(**args)
        if name=='prepare_cloud_action':
            values=dict(args);operation=values.pop('operation');payload=values.pop('payload');observed_url=values.pop('observed_target_url',None);source_context=values.pop('source_context',None);entry_url=values.pop('observed_entry_url',None)
            if source_context not in (None,'profile_history','search','other'):raise ValueError('ERR_SOURCE_CONTEXT: invalid source context')
            if operation=='get_feed_detail' and (source_context=='profile_history' or (isinstance(observed_url,str) and '/user/profile/' in observed_url)):
                raise ValueError('ERR_PROFILE_CARD_ONLY: read history through native cards in a profile action; no direct note URL or canonical fallback')
            plan=self._plan(operation,payload)
            if 'operation_id' not in values and payload.get('operation_id'):values['operation_id']=payload['operation_id']
            if values.get('due_at') and 'timezone' not in values:raise ValueError('scheduled local work requires explicit IANA timezone')
            if operation in CONTENT_WRITES:
                reviewed=self._review_payload(operation,payload,values['user_source'])
                supplied=values.get('media_manifest')
                if supplied is not None and supplied != media_manifest(operation,payload):
                    raise ValueError('ERR_MEDIA_BINDING: supplied manifest differs from actual reviewed media')
                payload=reviewed['payload'];values['media_manifest']=reviewed['media_manifest']
                plan=self._plan(operation,payload)
            if observed_url is not None:plan['target_url']=observed_note_url(operation,payload,observed_url)
            target={'url':plan['target_url'],'operation':operation}
            if entry_url is not None:
                if operation not in {'post_comment_to_feed','reply_comment_in_feed'} or observed_url is not None:
                    raise ValueError('ERR_COMMENT_CARD_ENTRY: comment entry cannot combine detail URL or other operation')
                from cloud_navigation_target import observed_comment_entry
                target={'url':observed_comment_entry(entry_url),'operation':operation,'native_card_entry':True,'feed_id':payload['feed_id']}
                plan['target_url']=target['url'];plan['native_card_entry']=True
                plan['steps'].append({'phase':'open_bound_native_comment_card','require':'Use exact observed search/profile entry in a new owned page; card_read comment-card:<sealed feed_id> via actual visible card. No direct detail URL. Preserve actual note/author/reply target evidence before editor and one Send.'})
            result=self.actions.prepare(operation,payload,expected_account=plan['expected_user_id'],target=target,**values)
            if operation in CONTENT_WRITES:self._batch().bind_action(payload['run_id'],result['operation_id'])
            result['host_plan']=plan
            return result
        methods={'record_cloud_unknown_continuation':'record_unknown_continuation','get_cloud_action':'get','list_cloud_actions':'list_actions','request_cloud_authorization':'request_authorization','record_cloud_authorization':'record_authorization','record_cloud_identity':'observe_identity','claim_cloud_action':'claim','begin_cloud_dispatch':'begin_submit','record_cloud_result':'report_result','reconcile_cloud_action':'reconcile','release_cloud_claim':'release_claim','cancel_cloud_action':'cancel','recover_cloud_leases':'recover_expired','cloud_action_events':'events'}
        if name in methods:
            value=getattr(self.actions,methods[name])(**args)
            if name=='get_cloud_action' and value['binding']['operation'] in READ_OPERATIONS and value.get('result'):
                value['read_at']=value['result'].get('read_at',value['result']['observed_at']);value['cached']=True
                value['lookup_provenance']='saved host observation; local lookup performs no fresh platform read'
            return value
        if name in AUTH:return {'status':'host_auth_required','operation':name,'instruction':'Use official browserAuth or user handoff. No authentication fields are stored or interpreted by this adapter.'}
        if name=='delete_cookies':return {'status':'not_applicable','reason':'This adapter stores no cookies; deleting an upstream backup does not log out the cloud browser.'}
        validate_payload(name,args)
        if name=='get_runtime_status':return {'transport':'cloud_host','actions':self.actions.status(),'native_browser_connection':False,'host_required':True}
        if name in {'get_task_status','get_operation_status'}:
            value=self.actions.get(args.get('task_id',args.get('operation_id')))
            value['task_id']=value['operation_id']
            if args.get('include_result') is False:value.pop('result',None)
            return value
        if name=='submit_task':
            operation=args['tool'];payload=dict(args['payload'])
            plan=self._plan(operation,payload)
            # A legacy approval reference is provenance, not permission. Actual
            # host attestation remains separate and bound to the final action.
            values={'user_source':args.get('approval_ref') or 'legacy submit_task; host must supply actual owner evidence',
                    'operation_id':payload.get('operation_id'),
                    'dependency':args.get('dependency'),
                    'media_manifest':media_manifest(operation,payload)}
            run_id=args.get('run_id') or payload.get('run_id')
            if operation in CONTENT_WRITES or run_id:
                if payload.get('run_id') not in (None,run_id):raise ValueError('run_id mismatch')
                payload['run_id']=run_id
                reviewed=self._review_payload(operation,payload,args.get('approval_ref',''))
                payload=reviewed['payload'];values['media_manifest']=reviewed['media_manifest']
                plan=self._plan(operation,payload)
            if args.get('not_before') is not None:
                zone=self.settings.get('timezone')
                if not zone:raise ValueError('scheduled local work requires configured explicit IANA timezone')
                values.update(due_at=dt.datetime.fromtimestamp(args['not_before'],dt.timezone.utc).isoformat(),timezone=zone)
            value=self.actions.prepare(operation,payload,expected_account=plan['expected_user_id'],target={'url':plan['target_url'],'operation':operation},**values)
            if run_id:self._batch().bind_action(run_id,value['operation_id'])
            value.update(task_id=value['operation_id'],host_plan=plan,auto_run=False,execution_note='Saved only; an assistant host must claim and execute. auto_run cannot create a background CUA worker.')
            return value
        if name=='control_task_queue':return self.actions.set_queue_enabled(args['enabled'])
        if name=='reconcile_task':
            value=self.actions.get(args['task_id'])
            value['next_step']='Inspect the actual platform result using supported host browser tools, then reconcile_cloud_action with bound evidence. No replay occurs.'
            return value
        if name in {'resolve_operation','resume_task'}:
            value=self.actions.get(args.get('operation_id',args.get('task_id')))
            value['requested_operation']=name
            value['next_step']='Legacy free-text evidence cannot establish account/context/attempt outcome. Use bound reconcile_cloud_action evidence; unknown work is never resumed automatically.'
            return value
        if name=='manage_operation_run':return self._batch().dispatch(args['operation'],args.get('data'))
        if name in LOCAL:return {'status':'unsupported_cloud_contract','operation':name,'reason':'Original local queue/batch contract is not yet migrated. Use explicit cloud lifecycle tools where applicable; no silent CDP fallback.'}
        plan=self._plan(name,args)
        plan['next_step']='Call prepare_cloud_action with this operation/payload and the actual user request reference, then follow the host lifecycle. Do not report a prepared plan as execution.'
        return plan


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config',required=True)
    parser.add_argument('tool',choices=[x['name'] for x in TOOLS])
    parser.add_argument('--arguments',help='Inert JSON arguments; omitted reads bounded JSON from stdin. Never put credentials here.')
    args=parser.parse_args()
    raw=args.arguments if args.arguments is not None else sys.stdin.read(131073)
    if len(raw)>131072:raise ValueError('input too large')
    host=CloudHost(args.config)
    try:print(json.dumps(host.call(args.tool,json.loads(raw or '{}')),ensure_ascii=False))
    finally:host.close()


if __name__=='__main__':
    try:main()
    except (ValueError,TypeError,KeyError) as error:
        print(json.dumps({'error':str(error)}),file=sys.stderr);sys.exit(1)
