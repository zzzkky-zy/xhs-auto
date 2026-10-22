"""Pure read-reuse advice from explicitly scoped host observations.

No browser, disk, clocks, quota reservation or Send authority. Fingerprints and
coverage remain host reports, never independent proof of platform contents.
"""
import datetime as dt
import math


def _text(value, name):
    if not isinstance(value, str) or not value.strip():
        raise ValueError('ERR_READ_REUSE: nonempty ' + name + ' required')
    return value


def _time(value, now, name):
    try:
        stamp = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if stamp.tzinfo is None:
            raise ValueError()
        at = stamp.timestamp()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('ERR_READ_REUSE: timezone-aware ' + name + ' required') from None
    if not math.isfinite(at) or at > now:
        raise ValueError('ERR_READ_REUSE: future or invalid ' + name)
    return at


def _fingerprints(value, name):
    if not isinstance(value, dict):
        raise ValueError('ERR_READ_REUSE: ' + name + ' must map actual IDs to fingerprints')
    for key, fingerprint in value.items():
        _text(key, name + ' ID'); _text(fingerprint, name + ' fingerprint')
    return value


def plan_read_reuse(binding, original_read, current_observation, now):
    """Return scoped advice, never permission to skip source/Send requirements.

    binding must be loaded from the persisted action by the trusted host.
    original_read is the original full-read coverage record, not get-action's
    cached flag. current_observation is a separate actual UI observation. Missing
    observations produce specific work items; malformed/mismatched evidence is
    rejected, not silently repaired. Caller preserves both evidence records.
    """
    if type(now) not in (int, float) or not math.isfinite(now):
        raise ValueError('ERR_READ_REUSE: finite current time required')
    if not isinstance(binding, dict) or not isinstance(binding.get('operation'), str) or binding['operation'] not in {
            'get_feed_detail', 'post_comment_to_feed', 'reply_comment_in_feed'}:
        raise ValueError('ERR_READ_REUSE: note-bound operation required')
    account = _text(binding.get('expected_account'), 'bound account')
    payload = binding.get('payload')
    if not isinstance(payload, dict):
        raise ValueError('ERR_READ_REUSE: bound payload must be an object')
    feed = _text(payload.get('feed_id'), 'bound feed_id')
    reply = payload.get('comment_id') if binding['operation'] == 'reply_comment_in_feed' else None
    if binding['operation'] == 'reply_comment_in_feed':
        _text(reply, 'bound reply comment_id')
    result = {'status': 'needs_read', 'reusable_scopes': [], 'required_work': [],
              'cached': True, 'fresh_read': False, 'read_at': None,
              'original_evidence_ref': None, 'send_authorized': False,
              'browser_state_independently_verified': False,
              'budget_charge': 0, 'budget_scope': 'local_advice_only'}
    work = result['required_work']
    def need(scope, reason):
        item = {'scope': scope, 'reason': reason}
        if item not in work: work.append(item)
    if original_read is None:
        need('body', 'missing_full_read_evidence'); need('images', 'missing_full_read_evidence')
    elif not isinstance(original_read, dict):
        raise ValueError('ERR_READ_REUSE: original_read must be an object or null')
    else:
        for key, expected in [('account_id', account), ('feed_id', feed)]:
            if original_read.get(key) != expected:
                raise ValueError('ERR_READ_REUSE: original ' + key + ' mismatch')
        _text(original_read.get('author_id'), 'original author_id')
        _time(original_read.get('read_at'), now, 'original read_at')
        result['read_at'] = original_read['read_at']
        result['original_evidence_ref'] = _text(original_read.get('evidence_ref'), 'original evidence_ref')
        if original_read.get('body_complete') is not True:
            need('body', 'original_read_incomplete')
        else:
            _text(original_read.get('body_fingerprint'), 'original body fingerprint')
        images = None
        if 'image_fingerprints' not in original_read:
            need('images', 'missing_original_image_fingerprints')
        else:
            images = _fingerprints(original_read['image_fingerprints'], 'original images')
        count = original_read.get('image_count')
        if type(count) is not int or count < 0 or original_read.get('images_complete') is not True or (images is not None and len(images) != count):
            need('images', 'original_read_incomplete')
        _fingerprints(original_read.get('comment_fingerprints', {}), 'original comments')
    if current_observation is None:
        need('target_observation', 'missing_fresh_account_note_author_comments')
        return result
    if not isinstance(current_observation, dict):
        raise ValueError('ERR_READ_REUSE: current_observation must be an object or null')
    current = current_observation
    for key, expected in [('account_id', account), ('feed_id', feed)]:
        if current.get(key) != expected:
            raise ValueError('ERR_READ_REUSE: current ' + key + ' mismatch')
    author = _text(current.get('author_id'), 'current author_id')
    _text(current.get('evidence_ref'), 'current evidence_ref')
    _text(current.get('browser_context'), 'current browser_context')
    observed = _time(current.get('observed_at'), now, 'current observed_at')
    if original_read and (author != original_read['author_id'] or observed < _time(original_read['read_at'], now, 'original read_at')):
        raise ValueError('ERR_READ_REUSE: author changed or current observation predates original')
    if payload.get('author_id') is not None and author != payload['author_id']:
        raise ValueError('ERR_READ_REUSE: bound author mismatch')
    if reply and current.get('reply_comment_id') != reply:
        raise ValueError('ERR_READ_REUSE: exact bound reply target required')
    if reply and payload.get('user_id') is not None:
        reply_user = _text(payload['user_id'], 'bound reply user_id')
        if current.get('reply_user_id') is None:
            need('reply_author', 'bound_reply_author_not_observed')
        elif _text(current['reply_user_id'], 'observed reply_user_id') != reply_user:
            raise ValueError('ERR_READ_REUSE: bound reply author mismatch')
    if now - observed > 120:
        need('target_observation', 'older_than_120_seconds')
    if original_read:
        if original_read.get('body_complete') is True:
            if not current.get('body_fingerprint'):
                need('body_fingerprint', 'current_fingerprint_not_observed')
            elif current['body_fingerprint'] != original_read['body_fingerprint']:
                need('body', 'content_changed')
            else:
                result['reusable_scopes'].append('body')
        if not any(item['scope'] == 'images' for item in work):
            current_images = current.get('image_fingerprints')
            if current_images is None:
                need('image_fingerprints', 'current_fingerprints_not_observed')
            elif _fingerprints(current_images, 'current images') != original_read['image_fingerprints']:
                need('images', 'content_changed')
            else:
                result['reusable_scopes'].append('images')
    comments = _fingerprints(current.get('comment_fingerprints', {}), 'current comments')
    relevant = current.get('relevant_comment_ids')
    if not isinstance(relevant, list) or any(not isinstance(x, str) or not x.strip() for x in relevant) or len(set(relevant)) != len(relevant):
        need('relevant_comments', 'explicit_observed_scope_required')
        relevant = []
    if current.get('relevant_comments_observed') is not True:
        need('relevant_comments', 'fresh_related_comments_not_observed')
    targets = list(dict.fromkeys(relevant + ([reply] if reply else [])))
    old_comments = original_read.get('comment_fingerprints', {}) if original_read else {}
    for target in targets:
        if target not in comments:
            need('comment:' + target, 'current_comment_not_observed')
        elif old_comments.get(target) != comments[target]:
            need('comment:' + target, 'new_or_changed_comment_requires_read')
        else:
            result['reusable_scopes'].append('comment:' + target)
    if not work:
        result['status'] = 'reusable'
    return result
