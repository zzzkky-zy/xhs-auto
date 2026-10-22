"""Pure, non-authorizing rule diagnostics. No state stores or browser imports.

Caller supplies the profile pinned to the run, not an arbitrary latest profile.
References identify supplied evidence; this module cannot authenticate consent.
"""
from copy import deepcopy
from datetime import datetime
from content_review import digest

_INTERVALS = {
    'browser_step_interval_seconds': ('browser_step', [10, 20]),
    'read_interval_seconds': ('read', [10, 20]),
    'search_interval_seconds': ('search', [60, 90]),
    'write_interval_seconds': ('write', [60, 180]),
}
_FIELDS = set(_INTERVALS) | {
    'publication_spacing_seconds', 'read_max_per_hour', 'search_max_per_hour',
    'comment_strategy', 'is_original', 'required_group', 'required_activity',
    'execution_paused', 'allow_c',
}


def _instant(value):
    parsed = datetime.fromisoformat(value.replace('Z', '+00:00'))
    if parsed.tzinfo is None:
        raise ValueError('timezone required')
    return parsed.timestamp()


def _source(value):
    return isinstance(value, str) and bool(value.strip())


def _valid(field, value):
    if field in _INTERVALS:
        return (isinstance(value, list) and len(value) == 2
                and all(type(v) is int and v > 0 for v in value)
                and value[0] <= value[1])
    if field in {'is_original', 'execution_paused', 'allow_c'}:
        return type(value) is bool
    if field in {'required_group', 'required_activity'}:
        return isinstance(value, str) and bool(value.strip())
    if field == 'comment_strategy':
        if not (isinstance(value, dict) and {'A', 'B', 'C_max'} <= set(value) <= {'A', 'B', 'C_max', 'fallback'}
                and all(type(value[k]) is int for k in ('A', 'B', 'C_max'))
                and value['A'] > 0 and value['B'] > 0 and 0 <= value['C_max'] <= 100
                and value['A'] + value['B'] + value['C_max'] == 100):
            return False
        fallback = value.get('fallback')
        return fallback is None or (isinstance(fallback, dict) and set(fallback) == {'A', 'B', 'C'}
            and all(type(v) is int for v in fallback.values()) and fallback['A'] > 0
            and fallback['B'] > 0 and fallback['C'] == 0 and sum(fallback.values()) == 100)
    return type(value) is int and value > 0


def _scope(scope, context):
    """No wildcards or inherited exceptions. All claims bind exact account/run."""
    if not isinstance(scope, dict) or set(scope) != {'account_id', 'run_id', 'batch_kind'}:
        raise ValueError('exact account/run/batch scope required')
    return all(scope[k] == context[k] for k in scope)


def explain_effective_rules(*, config, profile, context, user_rules=(),
                            mandatory_rules=(), runtime_values=None):
    """Return diagnostics only. Mandatory rules support equal/minimum/maximum.

    user_rules entries: field, value, source_ref, issued_at, scope.
    mandatory_rules additionally require operator and expires_at; constraints
    are intersected, never overridden. Scope applies to this run only.
    runtime_values is an optional observed executor-policy snapshot, not state.
    Its omission leaves executor compatibility explicitly unverified.
    """
    if not isinstance(context, dict) or not all(context.get(k) for k in
            ('account_id', 'run_id', 'batch_kind', 'evaluated_at', 'profile_version')):
        raise ValueError('complete context required')
    if context['batch_kind'] not in {'legacy', 'future'}:
        raise ValueError('batch_kind must be legacy or future')
    if config.get('expected_user_id') != context['account_id']:
        raise ValueError('exact configured identity mismatch')
    if digest(profile) != context['profile_version']:
        raise ValueError('pinned profile fingerprint mismatch')
    now = _instant(context['evaluated_at'])
    result = {'diagnostic_only': True, 'execution_authorized': False,
              'effective_rules': {}, 'conflicts': [], 'unresolved': [],
              'ignored': [], 'runtime_mismatches': [],
              'runtime_compatibility': 'unverified' if runtime_values is None else 'checked_supplied_snapshot'}
    rules = result['effective_rules']

    def put(field, value, source):
        previous = rules.get(field)
        rules[field] = {'value': deepcopy(value), 'source_ref': source,
                        'scope': {k: context[k] for k in ('account_id', 'run_id', 'batch_kind')},
                        'overridden_sources': ([] if previous is None else
                            previous['overridden_sources'] + [previous['source_ref']])}

    def problem(bucket, field, reason):
        result[bucket].append({'field': field, 'reason': reason})

    # Existing generic pacing defaults; consistency tests pin their source.
    for field, (prefix, default) in _INTERVALS.items():
        put(field, default, 'skill_default:runtime/cloud_pacing.py')
        fixed = config.get(prefix + '_interval_seconds')
        low = config.get(prefix + '_interval_min_seconds', fixed if fixed is not None else default[0])
        high = config.get(prefix + '_interval_max_seconds', fixed if fixed is not None else default[1])
        if any(k.startswith(prefix + '_interval_') for k in config):
            if _valid(field, [low, high]):
                put(field, [low, high], 'config:' + prefix)
            else:
                problem('conflicts', field, 'invalid configured interval')
    for field, default in [('read_max_per_hour', 120), ('search_max_per_hour', 20)]:
        put(field, default, 'skill_default:runtime/cloud_pacing.py')
        if field in config:
            if _valid(field, config[field]):
                put(field, config[field], 'config:' + field)
            else:
                problem('conflicts', field, 'invalid configured budget')
    put('publication_spacing_seconds', 5400 if context['batch_kind'] == 'future' else 3600,
        'runtime/cloud_batch.py:' + context['batch_kind'])
    confirmation = profile.get('confirmation_sources', {})
    if not isinstance(confirmation, dict):
        problem('unresolved', 'confirmation_sources', 'confirmation sources must be an object of nonempty strings')
        confirmation = {}
    strategy = profile.get('strategy')
    if strategy is not None:
        value = strategy
        if _valid('comment_strategy', value) and _source(confirmation.get('strategy')):
            put('comment_strategy', value, confirmation['strategy'])
        else:
            problem('unresolved', 'comment_strategy', 'confirmed strategy source required')
    if type(profile.get('allow_c')) is bool and _source(confirmation.get('allow_c')):
        put('allow_c', profile['allow_c'], confirmation['allow_c'])
    elif 'allow_c' in profile:
        problem('unresolved', 'allow_c', 'confirmed boolean allow_c source required')
    if context['batch_kind'] == 'future':
        policy = profile.get('future_content_policy', {})
        if isinstance(policy, dict) and policy and _source(confirmation.get('future_content_policy')):
            for field, key in [('is_original', 'is_original'), ('required_group', 'group_chat'), ('required_activity', 'activity')]:
                if key in policy:
                    if _valid(field, policy[key]):
                        put(field, policy[key], confirmation['future_content_policy'])
                    else:
                        problem('conflicts', field, 'invalid profile value')
        else:
            problem('unresolved', 'future_content_policy', 'confirmed pinned future profile required')
    # An exception is not a blanket exemption: do not infer applicability or consent.
    if config.get('cloud_schedule_overlap_exception') is not None:
        problem('unresolved', 'cloud_schedule_overlap_exception',
                'requires existing scheduler first-run/grid validation; never inherited here')

    candidates = {}
    for rule in user_rules:
        field = rule.get('field') if isinstance(rule, dict) else None
        try:
            if field not in _FIELDS or not _valid(field, rule.get('value')):
                raise ValueError('unknown field or invalid value')
            if not isinstance(rule.get('source_ref'), str) or not rule['source_ref'].strip():
                raise ValueError('explicit instruction source required')
            stamp = _instant(rule['issued_at'])
            if stamp > now:
                raise ValueError('instruction is in the future')
            if not _scope(rule.get('scope'), context):
                result['ignored'].append({'field': field, 'reason': 'different exact scope'})
                continue
            candidates.setdefault(field, []).append((stamp, rule))
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            problem('unresolved', field, str(exc))
    for field, entries in sorted(candidates.items()):
        latest = max(stamp for stamp, _ in entries)
        winners = [rule for stamp, rule in entries if stamp == latest]
        if any(rule['value'] != winners[0]['value'] for rule in winners):
            problem('conflicts', field, 'latest instructions disagree; no winner selected')
            continue
        put(field, winners[0]['value'], winners[0]['source_ref'])
    for rule in mandatory_rules:
        field = rule.get('field') if isinstance(rule, dict) else None
        try:
            if (field not in _FIELDS or not _source(rule.get('source_ref'))
                    or not _valid(field, rule.get('value'))):
                raise ValueError('known field and constraint evidence required')
            if not _scope(rule.get('scope'), context):
                continue
            if _instant(rule['issued_at']) > now or _instant(rule['expires_at']) < now:
                raise ValueError('constraint evidence is not current')
            value = rules.get(field, {}).get('value')
            if value is None:
                raise ValueError('effective value missing')
            op, limit = rule['operator'], rule['value']
            if op == 'equal':
                okay = value == limit
            elif op in {'minimum', 'maximum'} and type(value) is int and type(limit) is int:
                okay = value >= limit if op == 'minimum' else value <= limit
            else:
                raise ValueError('unsupported constraint; not silently applied')
            rules[field].setdefault('mandatory_sources', []).append(rule['source_ref'])
            if not okay:
                problem('conflicts', field, 'mandatory constraint contradicts effective request')
        except (ValueError, TypeError, KeyError, AttributeError) as exc:
            problem('unresolved', field, str(exc))
    if runtime_values is not None:
        for field, value in runtime_values.items():
            if field not in _FIELDS or not _valid(field, value):
                problem('unresolved', field, 'invalid executor snapshot field')
            elif field in rules and rules[field]['value'] != value:
                result['runtime_mismatches'].append({'field': field, 'runtime_value': deepcopy(value),
                                                     'required_value': rules[field]['value']})
    for field, entry in rules.items():
        entry['resolution_status'] = ('conflicted' if any(item['field'] == field for item in result['conflicts'])
            else 'unresolved' if any(item['field'] == field for item in result['unresolved']) else 'explained')
    result['checked_runtime_fields'] = sorted(runtime_values) if runtime_values is not None else []
    result['needs_decision'] = bool(result['conflicts'] or result['unresolved'] or result['runtime_mismatches'])
    return result


def check_comment_allocation(strategy, counts, *, allow_c):
    """C is only a cap. Remaining comments retain A:B, with integer rounding."""
    if not _valid('comment_strategy', strategy):
        raise ValueError('invalid strategy')
    if (not isinstance(counts, dict) or set(counts) != {'A', 'B', 'C'}
            or any(type(v) is not int or v < 0 for v in counts.values())):
        raise ValueError('invalid counts')
    total = sum(counts.values())
    remainder = total - counts['C']
    if type(allow_c) is not bool:
        raise ValueError('explicit allow_c required')
    ratio = strategy.get('fallback', strategy) if not counts['C'] else strategy
    target = remainder * ratio['A'] / (ratio['A'] + ratio['B'])
    return ((allow_c or not counts['C']) and counts['C'] * 100 <= total * strategy['C_max']
            and abs(counts['A'] - target) <= 1
            and (total < 2 or bool(counts['A'] and counts['B'])))
