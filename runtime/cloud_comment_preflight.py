"""Pure validation of host-observed comment editor evidence, never browser execution.

The host must inspect the actual official tool result and current visible editor.
Reports are not proof of browser state or permission to send.
"""
import datetime as dt
import math


def validate_preflight(binding, browser_context, report, now):
    if binding['operation'] not in {'post_comment_to_feed', 'reply_comment_in_feed'}:
        raise ValueError('ERR_COMMENT_PREFLIGHT: comment action required')
    required = {'expected_account','browser_context','observed_at','evidence_ref',
                'browser_call_succeeded','editor_expanded','editor_visible',
                'visible_text','send_enabled'}
    if not isinstance(report,dict) or set(report)!=required:
        raise ValueError('ERR_COMMENT_PREFLIGHT: exact observation fields required')
    if report['expected_account']!=binding['expected_account'] or report['browser_context']!=browser_context:
        raise ValueError('ERR_COMMENT_PREFLIGHT: account/context mismatch')
    if any(report[k] is not True for k in ('browser_call_succeeded','editor_expanded','editor_visible','send_enabled')):
        raise ValueError('ERR_COMMENT_PREFLIGHT: successful visible expanded editor and enabled Send required')
    if report['visible_text'] != binding['payload']['content']:
        raise ValueError('ERR_COMMENT_PREFLIGHT: visible full text differs from reviewed content')
    if not isinstance(report['evidence_ref'],str) or not report['evidence_ref'].strip():
        raise ValueError('ERR_COMMENT_PREFLIGHT: actual evidence reference required')
    try:
        stamp=dt.datetime.fromisoformat(report['observed_at'].replace('Z','+00:00'))
        if stamp.tzinfo is None:raise ValueError()
        at=stamp.timestamp()
    except (AttributeError,TypeError,ValueError):
        raise ValueError('ERR_COMMENT_PREFLIGHT: timezone-aware observed_at required') from None
    if not math.isfinite(at) or at>now+5 or now-at>120:
        raise ValueError('ERR_COMMENT_PREFLIGHT: observation is stale')
    return {'status':'validated_host_report','browser_state_independently_verified':False}
