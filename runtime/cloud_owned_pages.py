"""Durable, host-reported owned-page cleanup. Never executes browser tools.

Construction and inspection do not create tables. Ownership is not established
by a URL or a locally invented generation: the host must supply a documented,
externally checkable page-instance witness and recheck it immediately before use.
"""
import json
from cloud_actions import READ_OPERATIONS, _identifier, _iso, _json, _text, _timestamp

PAGE_KEYS = ('browser_session', 'browser_context', 'tab_id', 'page_instance')


class CloudOwnedPages:
    def __init__(self, actions):
        self.actions = actions

    def _exists(self):
        return self.actions.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='owned_pages'").fetchone() is not None

    def _create(self):
        self.actions.db.execute('''CREATE TABLE IF NOT EXISTS owned_pages (
            expected_account TEXT NOT NULL, resource_id TEXT NOT NULL,
            data TEXT NOT NULL, PRIMARY KEY(expected_account, resource_id))''')

    def _account(self, operation_id):
        row = self.actions._row(operation_id)
        expected = json.loads(row['binding_json'])['expected_account']
        if self.actions._config().get('expected_user_id', expected) != expected:
            raise ValueError('ERR_ACCOUNT_CHANGED')
        return row, expected

    def _fresh(self, observation):
        if not isinstance(observation, dict):
            raise ValueError('observation required')
        stamp = _timestamp(observation.get('observed_at'), 'observed_at')
        if not 0 <= self.actions.clock() - stamp <= min(120, self.actions.identity_max_age):
            raise ValueError('fresh page observation required')
        _text(observation.get('evidence_ref'), 'evidence_ref', 1000)
        return stamp

    def _page(self, page):
        if not isinstance(page, dict) or set(page) != set(PAGE_KEYS):
            raise ValueError('exact page instance required; URL is not ownership')
        for key in PAGE_KEYS:
            _text(page[key], key, 1000)
        return dict(page)

    def _read(self, operation_id, resource_id):
        _identifier(resource_id, 'resource_id')
        row, expected = self._account(operation_id)
        if not self._exists():
            raise ValueError('ERR_OWNED_PAGE_NOT_FOUND')
        saved = self.actions.db.execute('SELECT data FROM owned_pages WHERE expected_account=? AND resource_id=?', (expected, resource_id)).fetchone()
        if saved is None:
            raise ValueError('ERR_OWNED_PAGE_NOT_FOUND')
        record = json.loads(saved['data'])
        if (record['operation_id'] != operation_id or record['ledger_account'] != self.actions.account
                or record['binding_digest'] != row['binding_digest'] or record['attempt_id'] != row['attempt_id']
                or record['page']['browser_context'] != row['claim_context']):
            raise ValueError('owned page action/context changed')
        return row, record

    @staticmethod
    def _view(record, dispatch=False):
        value = json.loads(_json(record))
        value.update(dispatch_once=dispatch, automatic_execution=False,
                     execution_authority='host_policy_only', automatic_retry=False)
        return value

    def _save(self, record, event, evidence):
        record['updated_at'] = _iso(self.actions.clock())
        self.actions._admit(record)
        self.actions.db.execute('UPDATE owned_pages SET data=? WHERE expected_account=? AND resource_id=?',
                                (_json(record), record['expected_account'], record['resource_id']))
        self.actions._event(record['operation_id'], 'owned_page_' + event,
                            {'resource_id': record['resource_id'], 'evidence': evidence, 'state': record['state']})

    def register(self, operation_id, resource_id, observation):
        """Register a newly created, claimed action page from actual creation evidence."""
        _identifier(resource_id, 'resource_id')
        self._fresh(observation)
        page = self._page(observation.get('page'))
        for field in ('creation_evidence_ref', 'instance_documentation_ref'):
            _text(observation.get(field), field, 1000)
        if observation.get('created_by_host') is not True or observation.get('instance_verifiable') is not True:
            raise ValueError('actual host creation and verifiable instance required')
        with self.actions._transaction():
            row, expected = self._account(operation_id)
            if observation.get('account_id') != expected or not row['attempt_id'] or page['browser_context'] != row['claim_context']:
                raise ValueError('creation action/account/context mismatch')
            self._create()
            existing = self.actions.db.execute('SELECT data FROM owned_pages WHERE expected_account=? AND resource_id=?', (expected, resource_id)).fetchone()
            if existing:
                _, prior = self._read(operation_id, resource_id)
                if prior['page'] != page or prior['creation_evidence_ref'] != observation['creation_evidence_ref']:
                    raise ValueError('ownership registration mismatch')
                return self._view(prior)
            if row['status'] != 'dispatching':
                raise ValueError('register only during actual claimed action, never adopt historical pages')
            for saved in self.actions.db.execute('SELECT data FROM owned_pages WHERE expected_account=?', (expected,)):
                prior = json.loads(saved['data'])
                same_slot = all(prior['page'][k] == page[k] for k in ('browser_session', 'tab_id'))
                if prior['page'] == page or (same_slot and prior['state'] != 'closed_observed'):
                    raise ValueError('page already owned or prior tab instance unresolved')
            record = dict(resource_id=resource_id, operation_id=operation_id, ledger_account=self.actions.account,
                          expected_account=expected, binding_digest=row['binding_digest'], attempt_id=row['attempt_id'],
                          page=page, creation_evidence_ref=observation['creation_evidence_ref'],
                          instance_documentation_ref=observation['instance_documentation_ref'],
                          created_at=_iso(self.actions.clock()), state='registered', close_consumed=False)
            self.actions.db.execute('INSERT INTO owned_pages VALUES (?,?,?)', (expected, resource_id, _json(record)))
            self._save(record, 'registered', observation)
            return self._view(record)

    def get(self, operation_id, resource_id):
        return self._view(self._read(operation_id, resource_id)[1])

    def _eligible(self, row, record, observation):
        self._fresh(observation)
        if (self._page(observation.get('page')) != record['page']
                or observation.get('account_id') != record['expected_account']):
            raise ValueError('fresh exact page/account ownership required')
        for field in ('inventory_evidence_ref', 'instance_evidence_ref'):
            _text(observation.get(field), field, 1000)
        for key in ('call_ended', 'steps_resolved', 'editor_empty', 'owned_by_host', 'instance_verified', 'tab_present'):
            if observation.get(key) is not True:
                raise ValueError('cleanup requires affirmative ' + key)
        for key in ('challenge_active', 'rate_limited', 'permission_denied'):
            if observation.get(key) is not False:
                raise ValueError('cleanup requires explicit absence of ' + key)
        if record['close_consumed']:
            raise ValueError('close already consumed; inventory observation only')
        result = json.loads(row['result']) if row['result'] else {}
        status = row['status']
        success = status == 'host_reported_success' and result.get('status') == status
        not_applied = status in {'not_applied', 'cancelled'} and result.get('status') == 'not_applied' and result.get('evidence_kind') in {'host_no_submit', 'confirmed_not_applied', 'platform_rejection'}
        if not (success or not_applied):
            raise ValueError('confirmed terminal outcome required; cancellation is not proof')
        if self.actions.db.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name='recovery_incidents'").fetchone():
            for saved in self.actions.db.execute('SELECT account, operation_id, data FROM recovery_incidents'):
                incident = json.loads(saved['data'])
                binding = incident['snapshot']['binding']
                if binding['expected_account'] != record['expected_account'] or incident['state'] == 'verified':
                    continue
                observed = incident.get('observation', {})
                restrictions = ('challenge_active', 'rate_limited', 'permission_denied')
                if (set(incident.get('restriction_latches', [])) & set(restrictions)
                        or any(observed.get(key) is True for key in restrictions)):
                    raise ValueError('unresolved account restriction incident; preserve scene')
                original = self.actions.db.execute('SELECT claim_context FROM actions WHERE account=? AND operation_id=?',
                                                   (saved['account'], saved['operation_id'])).fetchone()
                context = original['claim_context'] if original else None
                same_page = incident.get('same_page') or observed.get('same_page') or {}
                same_target = (saved['account'] == record['ledger_account'] and saved['operation_id'] == record['operation_id'])
                same_context = context == record['page']['browser_context']
                same_tab = same_page.get('tab_id') == record['page']['tab_id']
                # Only positively independent, ended, unrestricted reads may be
                # ignored. Missing ownership/restriction evidence stays blocked.
                independent_read = (binding.get('operation') in READ_OPERATIONS
                    and binding.get('mutation') is False and bool(context)
                    and not same_target and not same_context and not same_tab
                    and observed.get('call_ended') is True
                    and all(observed.get(key) is False for key in restrictions))
                if not independent_read:
                    raise ValueError('unresolved related or unproven-independent recovery incident; preserve scene')

    def candidate(self, operation_id, resource_id, observation):
        """Pure inspection: no schema creation, events, permits or state changes."""
        row, record = self._read(operation_id, resource_id)
        self._eligible(row, record, observation)
        return dict(self._view(record), cleanup_candidate=True, paused=self.actions.execution_pause_status(record['expected_account'])['paused'])

    def request_close(self, operation_id, resource_id, observation, capability):
        """Consume one close instruction before returning; host rechecks actual tool policy."""
        if not isinstance(capability, dict) or set(capability) != {'method', 'supported', 'documentation_ref'} or capability['method'] != 'close_owned_tab' or capability['supported'] is not True:
            raise ValueError('documented official close capability required')
        _text(capability['documentation_ref'], 'documentation_ref', 1000)
        with self.actions._transaction():
            row, record = self._read(operation_id, resource_id)
            if self.actions.execution_pause_status(record['expected_account'])['paused']:
                raise ValueError('ERR_EXECUTION_PAUSED: no cleanup permit')
            self._eligible(row, record, observation)
            record.update(close_consumed=True, state='close_consumed', close_requested_at=_iso(self.actions.clock()), capability=capability)
            self._save(record, 'close_permitted', observation)
            return self._view(record, dispatch=True)

    def record_result(self, operation_id, resource_id, outcome, observation):
        """All outcomes retain the consumed permit. Even completed needs inventory."""
        if outcome not in {'completed', 'not_applied', 'unknown'}:
            raise ValueError('invalid close outcome')
        self._fresh(observation)
        with self.actions._transaction():
            _, record = self._read(operation_id, resource_id)
            if not record['close_consumed'] or record['state'] not in {'close_consumed', 'awaiting_inventory'}:
                raise ValueError('no pending close result')
            if _timestamp(observation['observed_at'], 'observed_at') < _timestamp(record['close_requested_at'], 'close_requested_at'):
                raise ValueError('close result must follow request')
            if observation.get('account_id') != record['expected_account'] or self._page(observation.get('page')) != record['page']:
                raise ValueError('close result binding mismatch')
            if 'close_outcome' in record:
                if record['close_outcome'] != outcome or record['close_result'] != observation:
                    raise ValueError('close result immutable; inspect inventory')
                return self._view(record)
            record.update(state='awaiting_inventory', close_outcome=outcome, close_result=observation)
            self._save(record, 'close_result', observation)
            return self._view(record)

    def observe_inventory(self, operation_id, resource_id, observation):
        """Record absence only. Never clears recovery, resends, or renews authority."""
        stamp = self._fresh(observation)
        with self.actions._transaction():
            _, record = self._read(operation_id, resource_id)
            if not record['close_consumed'] or stamp < _timestamp(record['close_requested_at'], 'close_requested_at'):
                raise ValueError('post-close inventory required')
            if observation.get('account_id') != record['expected_account'] or observation.get('browser_session') != record['page']['browser_session'] or observation.get('complete') is not True or observation.get('call_ended') is not True:
                raise ValueError('complete same-session inventory and ended call required')
            pages = observation.get('pages')
            if not isinstance(pages, list):
                raise ValueError('inventory pages required')
            pages = [self._page(page) for page in pages]
            if any(page['browser_session'] != record['page']['browser_session'] for page in pages):
                raise ValueError('mixed inventory sessions')
            # A reused numeric ID remains ambiguous. Do not infer ownership transfer.
            present = any(page['tab_id'] == record['page']['tab_id'] for page in pages)
            record.update(state='retained_after_close' if present else 'closed_observed', inventory=observation)
            self._save(record, 'inventory_observed', observation)
            return self._view(record)
