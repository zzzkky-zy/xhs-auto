"""Durable host-reported browser recovery; never executes browser operations.

Every returned instruction is a manual host boundary, not execution authority.
Capabilities are fresh host reports with documentation references, not discovered
or trusted by Python. The host must check actual tool support and policy. A
consumed recovery permit is never reissued, including after a lost response.
Recovery never updates action status, account identity, or platform blocks.
"""
import json

from cloud_actions import _identifier, _iso, _json, _text, _timestamp

METHODS = frozenset({'kernel_reset', 'browser_restart', 'reconnect'})
EVENTS = frozenset({'diagnose', 'request_recovery', 'recovery_result', 'request_new_tab', 'request_page_reload', 'page_reload_result', 'request_challenge', 'challenge_result', 'verify', 'abandon_read_only'})
HOMEPAGE = 'https://www.xiaohongshu.com/'
FLAGS = ('call_ended', 'control_available', 'tab_present', 'challenge_active',
         'rate_limited', 'permission_denied')


class CloudRecovery:
    """Persistent incident journal using a CloudActions connection and clock.

    Observation: observed_at (ISO timestamp), evidence_ref, optional boolean
    diagnostic FLAGS, editor_evidence (inert JSON), challenge_absent, identity
    {account_id, observed_at, evidence_ref, browser_context}, and capability
    {method, supported, documentation_ref}. request_recovery needs every FLAG.
    Only the call returning dispatch_once=True carries a one-use host instruction.
    get() and duplicate begin() always return inspection-only instructions.
    """
    def __init__(self, actions):
        self.actions = actions
        with actions._transaction():
            actions.db.execute('''CREATE TABLE IF NOT EXISTS recovery_incidents (
                account TEXT NOT NULL, incident_id TEXT NOT NULL,
                operation_id TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(account, incident_id))''')

    def _observation(self, observation):
        allowed = set(FLAGS) | {'observed_at', 'evidence_ref', 'editor_evidence',
                              'capability', 'identity', 'challenge_absent',
                              'session_already_absent', 'inventory_evidence_ref', 'same_page'}
        if not isinstance(observation, dict) or set(observation) - allowed:
            raise ValueError('unknown recovery observation fields')
        copied = json.loads(_json(observation, 'recovery observation'))
        stamp = _timestamp(copied.get('observed_at'), 'observed_at')
        if not 0 <= self.actions.clock() - stamp <= min(120, self.actions.identity_max_age):
            raise ValueError('fresh recovery observation required')
        _text(copied.get('evidence_ref'), 'evidence_ref', 1000)
        for key in set(FLAGS) | {'challenge_absent', 'session_already_absent'}:
            if key in copied and type(copied[key]) is not bool:
                raise ValueError(key + ' must be boolean')
        if 'inventory_evidence_ref' in copied:
            _text(copied['inventory_evidence_ref'], 'inventory_evidence_ref', 1000)
        if copied.get('challenge_active') is True and copied.get('challenge_absent') is True:
            raise ValueError('contradictory challenge observation')
        return copied

    def _read(self, incident_id):
        _identifier(incident_id, 'incident_id')
        row = self.actions.db.execute(
            'SELECT data FROM recovery_incidents WHERE account=? AND incident_id=?',
            (self.actions.account, incident_id)).fetchone()
        if row is None:
            raise ValueError('unknown recovery incident')
        record = json.loads(row['data'])
        self._check_account(record['snapshot']['binding']['expected_account'])
        return record

    def _check_account(self, expected):
        config = self.actions._config()
        if config.get('expected_user_id', expected) != expected:
            raise ValueError('ERR_ACCOUNT_CHANGED: recovery expected account changed')

    def _save(self, record, event, observation):
        record['updated_at'] = _iso(self.actions.clock())
        self.actions._admit(record)
        self.actions.db.execute(
            'UPDATE recovery_incidents SET data=? WHERE account=? AND incident_id=?',
            (_json(record), self.actions.account, record['incident_id']))
        self.actions._event(record['operation_id'], 'recovery_' + event,
                            {'incident_id': record['incident_id'], 'observation': observation,
                             'state': record['state'], 'attempt_consumed': record['attempt_consumed']})

    def _view(self, record, instruction=None):
        result = json.loads(_json(record))
        result.update(manual_host_boundary=True, automatic_execution=False,
                      execution_authority='host_policy_only', dispatch_once=instruction is not None,
                      next_host_action=instruction or self._next(record))
        return result

    @staticmethod
    def _next(record):
        if record.get('state') == 'challenge' and record.get('challenge_attempt_consumed'):return 'request_user_takeover_preserving_block_and_unknown_writes'
        if record.get('state') == 'challenge' and record.get('observation',{}).get('control_available') is True and not record.get('page_reload_consumed') and record['snapshot']['binding']['operation'] in {'get_feed_detail','user_profile','get_my_profile','list_feeds','search_feeds','check_login_status'}:
            return 'request_one_same_page_reload_diagnostic_if_exact_live_page_available'
        if record.get('state') == 'handoff': return 'request_user_takeover_preserving_block_and_unknown_writes'
        if record.get('state') == 'challenge_consumed': return 'observe_challenge_result_without_retry'
        if record.get('state') == 'reload_consumed': return 'wait_then_reload_same_page_once'
        return {'challenge': 'solve_challenge_in_normal_authorized_ui',
                'rate_limited': 'wait_for_platform_or_manual_resolution',
                'permission_denied': 'request_required_permission',
                'call_running': 'wait_for_call_to_end',
                'missing_session': 'inspect_documented_recovery_capabilities',
                'hung_control': 'inspect_documented_recovery_capabilities',
                'ready_to_verify': 'observe_fresh_exact_account_and_challenge_absence',
                'recovery_consumed': 'inspect_recovery_result_without_retry',
                'new_tab_consumed': 'inspect_official_homepage_without_reopening',
                'verified': 'host_may_consider_existing_block_clear_protocol',
                'abandoned_read_only': 'preserve_original_unknown_no_replay_independent_reads_only',
                'manual': 'manual_host_intervention'}[record['state']]

    @staticmethod
    def _diagnose(observation):
        if observation.get('permission_denied') is True: return 'permission_denied'
        if observation.get('rate_limited') is True: return 'rate_limited'
        if observation.get('challenge_active') is True: return 'challenge'
        if not all(key in observation for key in FLAGS): return 'manual'
        if not observation['call_ended']: return 'call_running'
        if not observation['tab_present']: return 'missing_session'
        if not observation['control_available']: return 'hung_control'
        return 'ready_to_verify'

    def begin(self, operation_id, incident_id, observation):
        _identifier(incident_id, 'incident_id')
        observation = self._observation(observation)
        with self.actions._transaction():
            action = self.actions._row(operation_id)
            existing = self.actions.db.execute(
                'SELECT operation_id FROM recovery_incidents WHERE account=? AND incident_id=?',
                (self.actions.account, incident_id)).fetchone()
            if existing:
                if existing['operation_id'] != operation_id:
                    raise ValueError('incident already bound to another operation')
                return self._view(self._read(incident_id))
            binding = json.loads(action['binding_json'])
            self._check_account(binding['expected_account'])
            prior_rows = self.actions.db.execute(
                'SELECT data FROM recovery_incidents').fetchall()
            for prior_row in prior_rows:
                prior = json.loads(prior_row['data'])
                if prior['snapshot']['binding']['expected_account'] != binding['expected_account']:
                    continue
                if prior['state'] == 'abandoned_read_only' and prior['operation_id'] == operation_id:
                    raise ValueError('abandoned original read cannot be restarted')
                if prior['state'] not in {'verified', 'abandoned_read_only'}:
                    raise ValueError('unresolved incident already exists for this account')
                if _timestamp(observation['observed_at'], 'observed_at') <= _timestamp(prior['updated_at'], 'updated_at'):
                    raise ValueError('new incident requires newer evidence after verified closure')
            record = {'incident_id': incident_id, 'operation_id': operation_id,
                      'snapshot': {'binding': binding, 'binding_digest': action['binding_digest'],
                                   'attempt': action['attempt'], 'attempt_id': action['attempt_id'],
                                   'status': action['status'],
                                   'submitted_unknown': action['status'] == 'submitted_unknown',
                                   'editor_evidence': observation.get('editor_evidence')},
                      'created_at': _iso(self.actions.clock()), 'state': self._diagnose(observation),
                      'attempt_consumed': False, 'method': None, 'new_tab_consumed': False,
                      'restriction_latches': [key for key in ('challenge_active', 'rate_limited', 'permission_denied')
                                              if observation.get(key) is True],
                      'observation': observation}
            self.actions._admit(record)
            self.actions.db.execute('INSERT INTO recovery_incidents VALUES (?,?,?,?)',
                                    (self.actions.account, incident_id, operation_id, _json(record)))
            self._save(record, 'began', observation)
            return self._view(record)

    def advance(self, incident_id, event, observation):
        if event not in EVENTS: raise ValueError('unknown recovery event')
        observation = self._observation(observation)
        instruction = None
        with self.actions._transaction():
            record = self._read(incident_id)
            if record['state'] in {'verified', 'abandoned_read_only'}: raise ValueError('incident already verified or abandoned')
            if _timestamp(observation['observed_at'], 'observed_at') < _timestamp(record['observation']['observed_at'], 'observed_at'):
                raise ValueError('out-of-order observation')
            action = self.actions._row(record['operation_id'])
            if action['binding_digest'] != record['snapshot']['binding_digest']:
                raise ValueError('incident binding changed')
            diagnosis = self._diagnose(observation)
            record['restriction_latches'] = sorted(set(record.get('restriction_latches', [])) |
                {key for key in ('challenge_active', 'rate_limited', 'permission_denied') if observation.get(key) is True})
            capability = observation.get('capability')
            diagnostic_complete = all(key in observation for key in FLAGS)
            no_rate_or_permission = not ({'rate_limited', 'permission_denied'} & set(record['restriction_latches']))
            kernel_observation_restore = (event == 'request_recovery' and diagnostic_complete and
                no_rate_or_permission and observation['call_ended'] and not observation['control_available'] and
                isinstance(capability, dict) and capability.get('method') == 'kernel_reset')
            homepage_observation_restore = (event == 'request_new_tab' and diagnosis == 'missing_session' and
                observation.get('control_available') is True and no_rate_or_permission and
                observation.get('session_already_absent') is True and bool(observation.get('inventory_evidence_ref')))
            if (event in {'request_recovery', 'request_new_tab'} and record['restriction_latches'] and
                    not (kernel_observation_restore or homepage_observation_restore)):
                # Persist newly observed restrictions even when the requested action
                # is denied; a subsequent missing page must not erase them.
                record['state'] = diagnosis if diagnosis in {'challenge', 'rate_limited', 'permission_denied'} else 'manual'
                record['observation'] = observation
                self._save(record, 'restriction_retained', observation)
                return self._view(record)
            if event == 'abandon_read_only' and record['restriction_latches']:
                record['state'] = diagnosis if diagnosis in {'challenge', 'rate_limited', 'permission_denied'} else 'manual'
                record['observation'] = observation
                self._save(record, 'restriction_retained', observation)
                return self._view(record)
            if event == 'abandon_read_only':
                from cloud_actions import READ_OPERATIONS
                binding = record['snapshot']['binding']
                if (binding.get('operation') not in READ_OPERATIONS or binding.get('mutation') is not False
                        or observation.get('call_ended') is not True
                        or record['restriction_latches']
                        or not all(observation.get(key) is False for key in
                                   ('challenge_active', 'rate_limited', 'permission_denied'))):
                    raise ValueError('abandon requires ended pure read with explicit absence of all restrictions and no latched restriction')
                record['state'] = 'abandoned_read_only'
                record['original_result_preserved'] = True
                record['original_read_replay_forbidden'] = True
            elif event == 'request_page_reload':
                from cloud_actions import READ_OPERATIONS
                if record.get('page_reload_consumed') or record.get('challenge_attempt_consumed'):raise ValueError('page reload already consumed or challenge attempted; never retry')
                if (not diagnostic_complete or diagnosis!='challenge' or not no_rate_or_permission
                        or not observation['call_ended'] or not observation['control_available'] or not observation['tab_present']):
                    raise ValueError('page reload requires ended call, live original page and only CAPTCHA restriction')
                if record['snapshot']['binding']['operation'] not in READ_OPERATIONS:
                    raise ValueError('page reload forbidden for writes or editors; preserve unknown submission')
                page=observation.get('same_page')
                if not isinstance(page,dict) or set(page)!={'url','tab_id','browser_context'}:
                    raise ValueError('exact original same-page binding required')
                for key in page:_text(page[key],key,8192 if key=='url' else 1000)
                if page['url']!=record['snapshot']['binding']['target'].get('url') or page['browser_context']!=action['claim_context']:
                    raise ValueError('same-page target/context mismatch; no alternate routes')
                record.update(page_reload_consumed=True,same_page=page,state='reload_consumed',
                              reload_requested_at=_iso(self.actions.clock()),reload_not_before=self.actions.clock()+5)
                instruction='wait_then_reload_same_page_once'
            elif event == 'page_reload_result':
                if not record.get('page_reload_consumed'):raise ValueError('no page reload attempt')
                record['state']=diagnosis
            elif event == 'request_challenge':
                if record.get('challenge_attempt_consumed'):raise ValueError('challenge attempt already consumed')
                if diagnosis!='challenge' or not diagnostic_complete or not no_rate_or_permission or not observation['call_ended'] or not observation['control_available']:
                    raise ValueError('normal challenge requires available controls and CAPTCHA only')
                record.update(challenge_attempt_consumed=True,state='challenge_consumed')
                instruction='solve_challenge_in_normal_authorized_ui'
            elif event == 'challenge_result':
                if not record.get('challenge_attempt_consumed'):raise ValueError('no challenge attempt')
                record['state']='handoff' if diagnosis=='challenge' else diagnosis
            elif event == 'request_recovery':
                if record['attempt_consumed']: raise ValueError('recovery attempt already consumed; never retry')
                if diagnosis not in {'missing_session', 'hung_control'} and not kernel_observation_restore:
                    raise ValueError('recovery requires ended call and missing session or hung control, without challenge, rate or permission block')
                capability = observation.get('capability')
                if not isinstance(capability, dict) or set(capability) != {'method', 'supported', 'documentation_ref'}:
                    raise ValueError('documented supported recovery capability required')
                if capability['method'] not in METHODS or capability['supported'] is not True:
                    raise ValueError('unsupported recovery method')
                _text(capability['documentation_ref'], 'documentation_ref', 1000)
                record.update(attempt_consumed=True, method=capability['method'],
                              recovery_requested_at=_iso(self.actions.clock()), state='recovery_consumed')
                record['observation_only'] = True
                record['preserve_browser_session_required'] = kernel_observation_restore
                record['no_page_replacement'] = kernel_observation_restore
                instruction = capability['method']
            elif event == 'request_new_tab':
                if record['new_tab_consumed']: raise ValueError('homepage attempt already consumed')
                if not homepage_observation_restore:
                    raise ValueError('new official-homepage tab requires available control and inventory evidence of already absent prior session')
                record.update(new_tab_consumed=True, state='new_tab_consumed', homepage=HOMEPAGE,
                              new_tab_requested_at=_iso(self.actions.clock()))
                record['observation_only'] = True
                record['preserve_browser_session_required'] = True
                record['no_page_replacement'] = True
                instruction = 'open_new_tab_official_homepage'
            elif event == 'verify':
                identity = observation.get('identity')
                if diagnosis != 'ready_to_verify' or observation.get('challenge_absent') is not True:
                    raise ValueError('working controls, present tab and explicit challenge absence required')
                if not isinstance(identity, dict) or set(identity) != {'account_id', 'observed_at', 'evidence_ref', 'browser_context'}:
                    raise ValueError('exact fresh identity evidence required')
                if identity['account_id'] != record['snapshot']['binding']['expected_account']:
                    raise ValueError('expected account mismatch')
                observed = _timestamp(identity['observed_at'], 'identity observed_at')
                boundary = max(record.get('recovery_requested_at', record['created_at']),
                               record.get('new_tab_requested_at', record['created_at']),
                               record.get('reload_requested_at', record['created_at']))
                if not (0 <= self.actions.clock() - observed <= min(120, self.actions.identity_max_age) and
                        observed >= _timestamp(boundary, 'recovery boundary') and
                        (not (record['attempt_consumed'] or record['new_tab_consumed'] or record.get('page_reload_consumed')) or
                         observed > _timestamp(boundary, 'recovery boundary')) and
                        observed <= _timestamp(observation['observed_at'], 'observed_at')):
                    raise ValueError('fresh post-recovery identity required')
                for key in ('evidence_ref', 'browser_context'): _text(identity[key], key, 1000)
                record['state'] = 'verified'
            else:
                if event == 'recovery_result' and not record['attempt_consumed']:
                    raise ValueError('no consumed recovery attempt')
                record['state'] = diagnosis
                if record['restriction_latches'] and diagnosis in {'missing_session', 'hung_control'}:
                    record['state'] = 'manual'
                if record['attempt_consumed'] and diagnosis in {'missing_session', 'hung_control'}:
                    record['state'] = 'manual'
            record['observation'] = observation
            self._save(record, event, observation)
            return self._view(record, instruction)

    def get(self, incident_id):
        with self.actions._transaction():
            return self._view(self._read(incident_id))
