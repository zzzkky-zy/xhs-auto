"""Scoped host evidence, not credentials or a second comment ledger.

DailyComments remains authoritative for locally charged operations. Outside
coverage is a conservative host-reported upper bound, never platform proof.
"""
import datetime as dt
import json
from cloud_actions import COMMENT_OPERATIONS, HOST_REPORT, _timestamp
from cloud_daily_comments import DailyComments, TIMEZONE, DAILY_LIMIT


class CloudCommentBoundary:
    def __init__(self, actions, *, evidence_verifier=None):
        self.actions = actions
        if evidence_verifier is not None and not callable(evidence_verifier):
            raise ValueError("evidence_verifier must be callable")
        self.evidence_verifier = evidence_verifier
        with actions._transaction():
            actions.db.execute('CREATE TABLE IF NOT EXISTS comment_boundary_evidence '
                               '(account TEXT NOT NULL, kind TEXT NOT NULL, evidence_id TEXT NOT NULL, '
                               'body TEXT NOT NULL, PRIMARY KEY(account,kind,evidence_id))')

    def _rows(self, account, kind):
        return [json.loads(r[0]) for r in self.actions.db.execute(
            'SELECT body FROM comment_boundary_evidence WHERE account=? AND kind=? ORDER BY rowid',
            (account, kind))]

    def _verify_evidence(self, phase, binding, body):
        if self.evidence_verifier is None:
            return False
        try:
            return self.evidence_verifier(phase, json.loads(json.dumps(binding)),
                                          json.loads(json.dumps(body))) is True
        except Exception:
            raise ValueError('ERR_COMMENT_EVIDENCE_VERIFICATION_REQUIRED: evidence verification unavailable; reads remain available') from None

    @staticmethod
    def _text(value, field):
        if not isinstance(value, str) or not value.strip() or len(value) > 8000:
            raise ValueError('ERR_COMMENT_BOUNDARY: invalid ' + field)
        return value

    def _day(self):
        return DailyComments(self.actions)._bounds(None, TIMEZONE)

    def record(self, operation_id, kind, evidence):
        """Persist explicit host claims. The original host/tool approval still applies.

        Each claim references the original user message and the actual read
        evidence. A caller's source string does NOT authenticate that message.
        """
        if kind not in {'history_gap', 'daily_coverage', 'public_scope', 'daily_grant'}:
            raise ValueError('ERR_COMMENT_BOUNDARY: unsupported evidence kind')
        if not isinstance(evidence, dict):
            raise ValueError('ERR_COMMENT_BOUNDARY: evidence object required')
        with self.actions._transaction():
            row = self.actions._row(operation_id)
            binding = json.loads(row['binding_json'])
            if binding['operation'] not in COMMENT_OPERATIONS:
                raise ValueError('ERR_COMMENT_BOUNDARY: evidence must bind a public comment')
            body = json.loads(json.dumps(evidence))
            for field in ('evidence_id', 'source_ref', 'source_quote', 'source_read_evidence_ref'):
                self._text(body.get(field), field)
            account = binding['expected_account']
            if body.get('expected_account') != account:
                raise ValueError('ERR_COMMENT_BOUNDARY: exact account mismatch')
            day, start, end = self._day()
            now = self.actions.clock()
            observed = _timestamp(body.get('observed_at'), 'observed_at')
            if observed > now + 5:
                raise ValueError('ERR_COMMENT_BOUNDARY: future observation')
            if kind in {'daily_coverage', 'daily_grant'}:
                if body.get('date') != day.isoformat() or body.get('timezone') != TIMEZONE:
                    raise ValueError('ERR_COMMENT_BOUNDARY: current Shanghai day required')
                expiry = _timestamp(body.get('expires_at'), 'expires_at')
                if not start <= observed <= now or not now < expiry <= end:
                    raise ValueError('ERR_COMMENT_BOUNDARY: finite current-day evidence required')
            if kind == 'history_gap':
                scope = body.get('coverage_scope', 'known_plugin_executions')
                if scope not in {'known_plugin_executions', 'account_activity_upper_bound'}:
                    raise ValueError('ERR_COMMENT_BOUNDARY: invalid gap coverage scope')
                body['coverage_scope'] = scope
                first, last = body.get('first_date'), body.get('last_date')
                if first is not None:
                    first = dt.date.fromisoformat(first).isoformat()
                    last = dt.date.fromisoformat(last).isoformat()
                    if first > last:
                        raise ValueError('ERR_COMMENT_BOUNDARY: reversed history range')
                elif last is not None:
                    raise ValueError('ERR_COMMENT_BOUNDARY: both history dates required or both unknown')
                body['first_date'], body['last_date'] = first, last
                for field in ('excluded_author_ids', 'excluded_target_ids'):
                    values = body.get(field, [])
                    if not isinstance(values, list):
                        raise ValueError('ERR_COMMENT_BOUNDARY: exclusions must be lists')
                    for value in values:
                        self._text(value, field)
                    body[field] = sorted(set(values))
            elif kind == 'daily_coverage':
                if body.get('coverage_scope') not in {'known_plugin_executions', 'account_activity_upper_bound'}:
                    raise ValueError('ERR_COMMENT_BOUNDARY: explicit coverage scope required')
                summary, charged = DailyComments(self.actions)._summary(None, TIMEZONE, account)
                upper = body.get('total_upper_bound')
                if type(upper) is not int or upper < summary['charged_total'] or upper < 0:
                    raise ValueError('ERR_COMMENT_BOUNDARY: upper bound below known charges')
                # Never accept caller-selected snapshot membership or subtract
                # later reconciliations from this conservative ceiling.
                body['snapshot_charged_keys'] = [list(key) for key in sorted(charged)]
                body['snapshot_charged_total'] = summary['charged_total']
                body['snapshot_gap_ids'] = [g['evidence_id'] for g in self._rows(account, 'history_gap')
                    if g['first_date'] is None or g['first_date'] <= day.isoformat() <= g['last_date']]
            elif kind == 'public_scope':
                if body.get('channel') != 'public_comment' or body.get('private_messages') is not False or body.get('benefit_fulfilment') is not False:
                    raise ValueError('ERR_COMMENT_BOUNDARY: public invitation is not DM or benefit fulfilment authority')
                for field in ('scope_id', 'semantic_scope', 'commitment_fingerprint', 'risk_fingerprint'):
                    self._text(body.get(field), field)
                if type(body.get('revoked', False)) is not bool:
                    raise ValueError('ERR_COMMENT_BOUNDARY: revoked must be boolean')
            elif kind == 'daily_grant':
                if type(body.get('revoked', False)) is not bool:
                    raise ValueError('ERR_COMMENT_BOUNDARY: revoked must be boolean')
                for field in ('run_id', 'profile_version', 'scope_id', 'commitment_fingerprint', 'risk_fingerprint'):
                    self._text(body.get(field), field)
                if body['run_id'] != binding['payload'].get('run_id'):
                    raise ValueError('ERR_COMMENT_BOUNDARY: exact run required')
                scope = self._scope(account, body, require_verified=False)
                body['scope_semantic_snapshot'] = scope['semantic_scope']
            self.actions._verify('comment_boundary_' + kind, row, body)
            self.actions._admit(body)
            verified = self._verify_evidence(kind, json.loads(row['binding_json']), body)
            body.update(provenance=HOST_REPORT, grants_execution_authority=False,
                        source_authenticated_by_plugin=False, independently_verified=verified)
            existing = self.actions.db.execute('SELECT body FROM comment_boundary_evidence WHERE account=? AND kind=? AND evidence_id=?',
                                               (account, kind, body['evidence_id'])).fetchone()
            if existing and json.loads(existing[0]) != body:
                raise ValueError('ERR_COMMENT_BOUNDARY: evidence IDs are immutable; use a new revision ID')
            self.actions.db.execute('INSERT INTO comment_boundary_evidence VALUES (?,?,?,?) '
                                   'ON CONFLICT(account,kind,evidence_id) DO UPDATE SET body=excluded.body',
                                   (account, kind, body['evidence_id'], json.dumps(body, sort_keys=True)))
            return body

    def _scope(self, account, grant, require_verified=True):
        matches = [s for s in self._rows(account, 'public_scope') if s['scope_id'] == grant['scope_id']]
        if not matches or matches[-1].get('revoked'):
            raise ValueError('ERR_COMMENT_SCOPE: active public scope evidence required')
        scope = matches[-1]
        if require_verified and not scope['independently_verified']:
            raise ValueError('ERR_COMMENT_EVIDENCE_VERIFICATION_REQUIRED: original user-message evidence needs verification')
        if 'scope_semantic_snapshot' in grant and scope['semantic_scope'] != grant['scope_semantic_snapshot']:
            raise ValueError('ERR_COMMENT_SCOPE: semantic scope changed or narrowed; fresh evidence required')
        for field in ('commitment_fingerprint', 'risk_fingerprint'):
            if scope[field] != grant[field]:
                raise ValueError('ERR_COMMENT_SCOPE: changed commitment or risk requires new scope evidence')
        return scope

    def status(self, expected_account=None):
        summary, charged = DailyComments(self.actions)._summary(None, TIMEZONE, expected_account)
        account, date = summary['expected_account'], summary['date']
        gaps = self._rows(account, 'history_gap')
        relevant = [g for g in gaps if g['first_date'] is None or g['first_date'] <= date <= g['last_date']]
        coverage = [c for c in self._rows(account, 'daily_coverage')
                    if c['date'] == date and c['independently_verified'] and _timestamp(c['expires_at'], 'expires_at') > self.actions.clock()]
        if any(g['coverage_scope'] == 'account_activity_upper_bound' for g in relevant):
            coverage = [c for c in coverage if c['coverage_scope'] == 'account_activity_upper_bound']
        gap_ids = {g['evidence_id'] for g in relevant}
        coverage = [c for c in coverage if gap_ids <= set(c['snapshot_gap_ids'])]
        current = coverage[-1] if coverage else None
        needs = bool(relevant)
        if current:
            snapshot = {tuple(k) for k in current['snapshot_charged_keys']}
            upper = max(summary['charged_total'], current['total_upper_bound'] + len(charged - snapshot))
        else:
            upper = None if needs else summary['charged_total']
        return dict(summary, coverage_scope=current['coverage_scope'] if current else 'known_plugin_executions_only',
                    account_wide_external_activity_verified=False, history_gap_requires_current_coverage=needs,
                    current_day_upper_bound=upper, safe_remaining=None if upper is None else max(0, DAILY_LIMIT-upper),
                    active_daily_coverage=current, historical_gaps=gaps,
                    finite_grants=self._rows(account, 'daily_grant'))

    def guard(self, operation_id, profile_version=None, author_evidence=None):
        row = self.actions._row(operation_id)
        binding = json.loads(row['binding_json'])
        if binding['operation'] not in COMMENT_OPERATIONS:
            return {'applies': False, 'grants_execution_authority': False}
        account = binding['expected_account']
        with self.actions._transaction():
            local = DailyComments(self.actions).guard(operation_id)
            status = self.status(account)
            target = binding.get('target', {})
            payload = binding['payload']
            authors = {payload.get('user_id'), target.get('author_id'), target.get('user_id'), target.get('target_user_id')}
            if isinstance(author_evidence, dict):
                for field in ('note_author_id', 'target_author_id', 'target_user_id', 'author_id'):
                    value = author_evidence.get(field)
                    if isinstance(value, str):
                        authors.add(value)
            targets = {payload.get('feed_id'), payload.get('comment_id'), target.get('feed_id'), target.get('comment_id')}
            for gap in status['historical_gaps']:
                if authors.intersection(gap['excluded_author_ids']) or targets.intersection(gap['excluded_target_ids']):
                    raise ValueError('ERR_COMMENT_HISTORY_EXCLUDED: known prior author or uncertain target must not be replayed')
            upper = status['current_day_upper_bound']
            if upper is None:
                raise ValueError('ERR_COMMENT_COVERAGE_REQUIRED: current-day upper bound unknown; reads remain available')
            projected = upper + (0 if local['current_operation_already_charged'] else 1)
            if projected > DAILY_LIMIT:
                raise ValueError('ERR_DAILY_COMMENT_LIMIT: trusted upper bound exhausts current-day capacity')
            grants = status['finite_grants']
            if grants or self.actions._config().get('cloud_comment_authorization_mode') == 'limited_daily':
                matching = [g for g in grants if g['run_id'] == payload.get('run_id') and g['date'] == status['date']]
                if not matching:
                    raise ValueError('ERR_COMMENT_DAILY_GRANT: new run/day needs fresh finite evidence')
                grant = matching[-1]
                if grant.get('revoked'):
                    raise ValueError('ERR_COMMENT_DAILY_GRANT: finite grant revoked')
                if not grant['independently_verified']:
                    raise ValueError('ERR_COMMENT_EVIDENCE_VERIFICATION_REQUIRED: finite evidence needs verification, not new user consent')
                if _timestamp(grant['expires_at'], 'expires_at') <= self.actions.clock():
                    raise ValueError('ERR_COMMENT_DAILY_GRANT: finite evidence expired; reads remain available')
                if grant['profile_version'] != profile_version:
                    raise ValueError('ERR_COMMENT_DAILY_GRANT: changed or missing pinned profile')
                scope = self._scope(account, grant)
                if not self._verify_evidence('daily_grant_use', json.loads(row['binding_json']),
                                             {'grant': grant, 'scope': scope}):
                    raise ValueError('ERR_COMMENT_EVIDENCE_VERIFICATION_REQUIRED: current comment semantics need scope verification, not new user consent')
            return dict(status, applies=True, projected_total=projected, grants_execution_authority=False)
