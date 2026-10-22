"""Validate actual host observations before a future-policy publication click.

No browser execution, independent UI verification, or user authority is created.
The host must use official native browser interactions and inspect real results.
"""
import datetime as dt
import math


def validate_topic_expectations(binding, expectations):
    """Compare against separately retained actual template/activity observations.

    This is host-reported evidence, not independent browser verification or
    permission. No topic is guessed, normalized, inserted or removed.
    """
    payload=binding['payload']
    if not payload.get('topic_template') and not payload.get('activity'):
        return None
    required={'template_name','template_topics','template_evidence_ref',
              'activity_name','activity_topics','activity_evidence_ref'}
    if not isinstance(expectations,dict) or set(expectations)!=required:
        raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: first read actual template and activity topic lists and record_cloud_publication_topic_expectations for this action/run')
    for kind,field in (('template','topic_template'),('activity','activity')):
        requested=payload.get(field)
        if expectations[kind+'_name']!=requested:
            raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: exact '+kind+' name differs')
        topics=expectations[kind+'_topics']
        if (not isinstance(topics,list) or any(not isinstance(t,str) or not t.strip() for t in topics)
                or len(set(topics))!=len(topics) or (not requested and topics)
                or (kind=='template' and requested and not topics)):
            raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: actual distinct '+kind+' topic list required')
        evidence=expectations[kind+'_evidence_ref']
        if requested and (not isinstance(evidence,str) or not evidence.strip()):
            raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: separate '+kind+' observation evidence required')
        if not requested and evidence is not None:
            raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: do not invent unrequested '+kind+' evidence')
    return expectations


def validate_publication_preflight(binding, browser_context, report, now, topic_expectations=None):
    if binding['operation'] != 'publish_content':
        raise ValueError('ERR_PUBLICATION_PREFLIGHT: image publication required')
    payload=binding['payload']
    keys={'expected_account','browser_context','observed_at','evidence_ref',
          'browser_call_succeeded','visible_title','visible_body','publish_enabled',
          'topic_template_name','topic_template_applied','visible_topics',
          'original_enabled','optional_declaration_selected','mandatory_declaration'}
    if payload.get('topic_template') or payload.get('activity'):
        keys |= {'visible_template_topics','visible_activity_topics'}
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
    expected=validate_topic_expectations(binding,topic_expectations)
    if expected is not None:
        for kind in ('template','activity'):
            seen=report['visible_'+kind+'_topics']
            if (not isinstance(seen,list) or any(not isinstance(t,str) for t in seen)
                    or len(set(seen))!=len(seen) or set(seen)!=set(expected[kind+'_topics'])):
                raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: observed '+kind+' topics differ from retained actual list')
        if len(set(topics))!=len(topics) or set(topics)!=set(expected['template_topics'])|set(expected['activity_topics']):
            raise ValueError('ERR_PUBLICATION_TOPICS_UNVERIFIED: combined visible topics differ; inspect without deleting activity-added topics or changing body')
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
