"""Validate actual host observations before a future-policy publication click.

No browser execution, independent UI verification, or user authority is created.
The host must use official native browser interactions and inspect real results.
"""
import datetime as dt
import math


def validate_publication_preflight(binding, browser_context, report, now):
    if binding['operation'] != 'publish_content':
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: image publication required')
    payload=binding['payload']
    keys={'expected_account','browser_context','observed_at','evidence_ref',
          'browser_call_succeeded','visible_title','visible_body','publish_enabled',
          'topic_template_name','topic_template_applied','visible_topics',
          'original_enabled','optional_declaration_selected','mandatory_declaration'}
    selections={'group_chat':'selected_group_chat','activity':'selected_activity'}
    keys |= {observed for requested,observed in selections.items() if requested in payload}
    if payload.get('schedule_at'):keys |= {'visible_schedule_at','schedule_error_absent'}
    if not isinstance(report,dict) or set(report)!=keys:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: exact observation fields required')
    if payload.get('schedule_at') and (report['visible_schedule_at']!=payload['schedule_at'] or report['schedule_error_absent'] is not True):
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: exact observed scheduled time and no platform time error required')
    if report['expected_account']!=binding['expected_account'] or report['browser_context']!=browser_context:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: account/context mismatch')
    for flag in ('browser_call_succeeded','publish_enabled','topic_template_applied'):
        if report[flag] is not True:raise ValueError('ERR_PUBLICATION_PREFLIGHT: required visible setting not confirmed')
    if type(payload.get('is_original')) is not bool or report['original_enabled'] is not payload['is_original']:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: exact reviewed original declaration state required')
    for requested,observed in selections.items():
        if requested in payload:
            value=payload[requested]
            if not isinstance(value,str) or not value.strip() or report[observed]!=value:
                raise ValueError('ERR_PUBLICATION_PREFLIGHT: exact reviewed '+requested+' selection required')
    if report['optional_declaration_selected'] is not False:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: optional content declaration must remain unselected')
    if report['visible_title']!=payload['title'] or report['visible_body']!=payload['content']:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: visible title/body differs from reviewed content')
    if not payload.get('topic_template') or report['topic_template_name']!=payload['topic_template']:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: exact reviewed topic template required')
    topics=report['visible_topics']
    if not isinstance(topics,list) or not topics or any(not isinstance(x,str) or not x.strip() for x in topics):
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: observe actual applied topic chips/text')
    declaration=report['mandatory_declaration']
    if not isinstance(declaration,dict) or set(declaration)!={'required','truthfully_completed','evidence_ref'} or type(declaration['required']) is not bool:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: actual mandatory declaration observation required')
    if declaration['required'] and declaration['truthfully_completed'] is not True:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: required truthful platform declaration unresolved')
    if not declaration['required'] and declaration['truthfully_completed'] is not False:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: do not invent an unnecessary declaration')
    for value in (report['evidence_ref'],declaration['evidence_ref']):
        if not isinstance(value,str) or not value.strip():raise ValueError('ERR_PUBLICATION_PREFLIGHT: actual evidence reference required')
    try:
        stamp=dt.datetime.fromisoformat(report['observed_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError()
        at=stamp.timestamp()
    except (AttributeError,TypeError,ValueError):
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: timezone-aware observed_at required') from None
    if not math.isfinite(at) or at>now+5 or now-at>120:
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: stale observation')
    return {'status':'validated_host_report','browser_state_independently_verified':False}
