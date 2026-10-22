"""Original tool inventory and explicit cloud-host execution plans.

Plans never invoke browser tools, verify chat authority or perform website writes.
The assistant host is responsible for policy, fresh UI inspection and execution.
"""
import copy
import datetime as dt
import json
import math
from pathlib import Path
import re
import urllib.parse

ROOT = Path(__file__).resolve().parents[1]
ORIGINAL = {item['name']: item for item in json.loads((ROOT/'schemas/original-tools.json').read_text())['tools']}
AUTH = {'get_login_qrcode', 'login_with_sms'}
LOCAL = {'get_runtime_status', 'get_operation_status', 'submit_task', 'get_task_status', 'control_task_queue', 'reconcile_task', 'resume_task', 'manage_operation_run', 'resolve_operation'}
WRITES = {'publish_content','publish_with_video','post_comment_to_feed','reply_comment_in_feed','like_feed','favorite_feed','reply_notification','like_notification','reply_direct_message','list_notifications'}
PRIVATE_READS = {'check_login_status','get_my_profile','get_unread_count','list_direct_messages'}
BASE = 'https://www.xiaohongshu.com'
CREATOR = 'https://creator.xiaohongshu.com/publish/publish?source=official'
FILTERS = {'sort_by': {'综合','最新','最多点赞','最多评论','最多收藏'}, 'note_type': {'不限','视频','图文'}, 'publish_time': {'不限','一天内','一周内','半年内'}, 'search_scope': {'不限','已看过','未看过','已关注'}, 'location': {'不限','同城','附近'}}


def capability(name):
    if name not in ORIGINAL: raise ValueError('ERR_CLOUD_UNSUPPORTED: unknown original operation')
    route = 'host_ui'
    state_change = name in WRITES
    blockers = []
    if name in AUTH:
        route='host_auth_handoff';blockers=['Use official browser sign-in/handoff, never persist credentials or OTP in action ledger.']
    elif name == 'delete_cookies':
        route='not_applicable';blockers=['Cloud adapter stores no cookies; deleting upstream local backup is not cloud-browser logout.']
    elif name in LOCAL: route='local_lifecycle'
    if name in {'publish_content','publish_with_video'}:
        blockers += ['File upload requires a documented upload/file-picker capability.', 'Creator identity and every requested publish setting must be observed before any submit.']
    if name == 'list_notifications': blockers += ['Opening a notification section may clear its unread marker; treat as account-state change.']
    return {'operation':name,'route':route,'account_state_change':state_change,'requires_identity':name in WRITES|PRIVATE_READS,
            'requires_explicit_host_authorization':state_change or name in AUTH,
            'execution':'assistant_tool_boundary' if route.startswith('host') else route,
            'tested_level':'local_contract_only','automatic_execution':False,'blockers':blockers,
            'output_contract':'bounded host-observed UI evidence, not guaranteed upstream hidden-state API fields'}


def catalog():
    return {'original_tool_count':len(ORIGINAL),'follow_unfollow_in_original':False,'capabilities':[capability(name) for name in sorted(ORIGINAL)],'native_host_bridge':'assistant executes official CUA calls; no external browser RPC endpoint','approval_boundary':'Ledger evidence does not grant authority. Host checks actual owner instruction and policy before each side effect.'}


def _validate(value, schema, path='payload', depth=0):
    if depth>12: raise ValueError('payload nesting limit')
    typ=schema.get('type');types=typ if isinstance(typ,list) else [typ]
    matches={'object':isinstance(value,dict),'array':isinstance(value,list),'string':isinstance(value,str),'integer':type(value) is int,'number':type(value) is int or (type(value) is float and math.isfinite(value)),'boolean':type(value) is bool,'null':value is None,None:True}
    if not any(matches.get(t,False) for t in types): raise ValueError(path+' has wrong type')
    if value is None:return
    if isinstance(value,str):
        if len(value)>32000 or any(ord(c)<32 and c not in '\n\r\t' for c in value):raise ValueError(path+' invalid string')
    elif isinstance(value,dict):
        if len(value)>100:raise ValueError(path+' too many keys')
        props=schema.get('properties',{})
        if not set(schema.get('required',[])).issubset(value):raise ValueError(path+' missing required fields')
        if schema.get('additionalProperties') is False and set(value)-set(props):raise ValueError(path+' contains unknown fields')
        for key, val in value.items():
            if not isinstance(key,str) or len(key)>200:raise ValueError('invalid object key')
            if re.search(r'(?i)^(password|cookies?|web_session|authorization|access_token|refresh_token)$',key):raise ValueError('authentication material prohibited in action payload')
            _validate(val,props.get(key,{}),path+'.'+key,depth+1)
    elif isinstance(value,list):
        if len(value)>100:raise ValueError(path+' too many items')
        for i,item in enumerate(value):_validate(item,schema.get('items',{}),path+'['+str(i)+']',depth+1)


def validate_payload(operation, payload):
    info=capability(operation)
    if operation in AUTH:raise ValueError('ERR_HOST_AUTH_HANDOFF: do not put authentication fields in the action ledger')
    if operation=='delete_cookies':raise ValueError('ERR_NOT_APPLICABLE: no cloud cookie backup exists')
    schema=ORIGINAL[operation]['inputSchema']
    if operation=='publish_content' and 'group_chat' in payload:
        schema=copy.deepcopy(schema);schema['properties']['group_chat']={'type':'string'}
        if not isinstance(payload['group_chat'],str) or not payload['group_chat'].strip() or len(payload['group_chat'])>200:
            raise ValueError('group_chat requires exact observed visible name')
    _validate(payload,schema)
    if len(json.dumps(payload,ensure_ascii=False).encode())>128*1024:raise ValueError('payload too large')
    for key in ('feed_id','user_id'):
        if key in payload and (not isinstance(payload[key],str) or not re.fullmatch('[a-fA-F0-9]{24}',payload[key])):raise ValueError(key+' must be an observed public 24-character identifier')
    for key in ('limit','result_limit','reply_limit'):
        if key in payload and not 0<=payload[key]<=100:raise ValueError(key+' must be 0..100 (0 uses upstream default)')
    if 'keyword' in payload and not 1<=len(payload['keyword'].strip())<=200:raise ValueError('invalid keyword')
    for key,value in payload.get('filters',{}).items():
        if value and value not in FILTERS[key]:raise ValueError('unsupported search filter '+key)
    if payload.get('visibility') not in (None,'','公开可见','仅自己可见','仅互关好友可见'):raise ValueError('unsupported visibility')
    if payload.get('schedule_at'):
        try:stamp=dt.datetime.fromisoformat(payload['schedule_at'].replace('Z','+00:00'))
        except ValueError:raise ValueError('schedule_at must be ISO8601') from None
        if stamp.utcoffset() is None:raise ValueError('schedule_at needs explicit timezone offset')
    if operation=='publish_content' and not payload.get('images') and not payload.get('long_article_template'):raise ValueError('publish_content requires images or a long-article template')
    if operation in {'post_comment_to_feed','reply_comment_in_feed','reply_notification'} and not payload['content'].strip():raise ValueError('empty reply/comment')
    if operation=='reply_comment_in_feed' and not (payload.get('comment_id') or payload.get('user_id')):raise ValueError('reply target must identify comment or user')
    if operation=='reply_direct_message':
        if not (payload.get('conversation_id') or payload.get('name')):raise ValueError('DM target required')
        if not payload.get('open_only') and not payload.get('content','').strip():raise ValueError('DM content required')
    return copy.deepcopy(payload)


def action_plan(operation,payload,expected_user_id):
    payload=validate_payload(operation,payload);info=capability(operation)
    if info['route']!='host_ui':raise ValueError('ERR_LOCAL_LIFECYCLE: use corresponding local action-ledger command')
    if not isinstance(expected_user_id,str) or not re.fullmatch('[a-fA-F0-9]{24}',expected_user_id):raise ValueError('configured expected_user_id required')
    write=info['account_state_change']
    url=BASE+'/explore'
    if operation=='search_feeds':url=BASE+'/search_result?'+urllib.parse.urlencode({'keyword':payload['keyword'],'source':'web_search_result_notes'})
    elif operation=='user_profile':url=BASE+'/user/profile/'+payload['user_id']
    elif operation=='get_my_profile':url=BASE+'/user/profile/'+expected_user_id
    elif operation in {'get_feed_detail','post_comment_to_feed','reply_comment_in_feed','like_feed','favorite_feed'}:url=BASE+'/explore/'+payload['feed_id']
    elif operation in {'publish_content','publish_with_video'}:url=CREATOR
    elif operation in {'list_notifications','reply_notification','like_notification'}:url=BASE+'/notification'
    elif operation in {'list_direct_messages','reply_direct_message'}:url=BASE+'/chat'
    if payload.get('xsec_token') and operation in {'get_feed_detail','user_profile','post_comment_to_feed','reply_comment_in_feed','like_feed','favorite_feed'}:
        url+='?'+urllib.parse.urlencode({'xsec_token':payload['xsec_token'],'xsec_source':'pc_note'})
    steps=[{'phase':'inspect_identity','require':'Fresh visible sidebar/self-profile account matches expected_user_id; creator identity separately checked for publishing.'}, {'phase':'inspect_target','url':url,'require':'Use official host browser tool; inspect fresh rendered UI, no hidden state/network scripts.'}]
    if write:steps.insert(0,{'phase':'host_policy_check','require':'Actual owner approval for exact account, target, content/media and side effects. An approval_ref string is not permission.'})
    if operation in {'publish_content','publish_with_video'}:
        steps += [{'phase':'upload_media','field':'video' if operation=='publish_with_video' else 'images','require':'Only after approval; documented host file chooser/upload API, exact file/hash manifest. If unavailable stop; never invent upload success.'}, {'phase':'prepare_composer','fields':[key for key in ('title','content','tags','is_original','visibility','schedule_at','products','activity','group_chat','long_article_template','topic_template') if key in payload],'require':'Observe and satisfy EVERY requested field; uploads and fills are external changes, not a free dry-run.'}]
    elif operation=='search_feeds':steps += [{'phase':'apply_filters','fields':list(payload.get('filters',{})),'require':'Use observed labels; mark unsupported/missing UI, never ignore a filter.'}]
    elif write:steps += [{'phase':'prepare_exact_action','operation':operation,'require':'Inspect target conversation/comment/note and current toggle state; unlike/unfavorite are desired-state operations, never blind repeated toggle.'}]
    if operation=='publish_content' and payload.get('long_article_template') and not payload.get('images'):
        steps=[step for step in steps if step['phase']!='upload_media']
    if write:
        dispatch={'phase':'record_dispatch_before_side_effect','require':'Persist same action and attempt BEFORE upload, fill, mark-read navigation or click that can affect account; any interruption after dispatch is ambiguous.'}
        position=next((i for i,step in enumerate(steps) if step['phase'] in {'upload_media','prepare_exact_action','prepare_composer'}),len(steps))
        if operation in {'list_notifications','reply_notification','like_notification','reply_direct_message'}:position=next(i for i,step in enumerate(steps) if step['phase']=='inspect_target')
        steps.insert(position,dispatch)
    if write:steps += [{'phase':'execute_via_host','require':'Host performs only approved observed UI action; local plan does not execute.'}, {'phase':'observe_result','require':'Inspect actual posted content/message bubble/desired toggle state. Click completion is not proof; timeout becomes unknown, never blindly resend.'}]
    else:steps += [{'phase':'observe_read_result','require':'Bounded visible text and explicit missing/truncated fields; no invented hidden-state parity.'}]
    return {'operation':operation,'transport':'cloud_host','status':'prepared_not_executed','account_state_change':write,'expected_user_id':expected_user_id,'target_url':url,'payload':payload,'steps':steps,'evidence_contract':'host_reported; source reference and exact payload/account binding, no independent authentication claim','automatic_execution':False}
