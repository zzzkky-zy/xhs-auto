"""Read-only daily comment accounting over the shared action ledger.

The fixed limit covers main comments and replies for one expected platform
account, across local aliases and runs. Success means an unverified host report,
not independently verified platform delivery. This helper grants no execution
permission and does not dispatch, reconcile, reset, or reserve anything. Call
``guard`` inside the ledger's submission transaction, and again before Send.
Separate ledger files cannot provide account-wide coordination.
"""
import datetime as dt
import json
import math
from zoneinfo import ZoneInfo

from cloud_actions import COMMENT_OPERATIONS, HOST_REPORT, _timestamp

TIMEZONE = 'Asia/Shanghai'
DAILY_LIMIT = 50


class DailyComments:
    def __init__(self, actions):
        self.actions = actions

    def _bounds(self, date, timezone):
        if timezone != TIMEZONE:
            raise ValueError('ERR_DAILY_COMMENTS: timezone must be Asia/Shanghai')
        zone = ZoneInfo(TIMEZONE)
        if date is None:
            day = dt.datetime.fromtimestamp(self.actions.clock(), zone).date()
        elif type(date) is dt.date:
            day = date
        elif isinstance(date, str):
            try:
                day = dt.date.fromisoformat(date)
                if date != day.isoformat():
                    raise ValueError()
            except ValueError:
                raise ValueError('ERR_DAILY_COMMENTS: date must be YYYY-MM-DD') from None
        else:
            raise ValueError('ERR_DAILY_COMMENTS: date must be YYYY-MM-DD')
        start = dt.datetime.combine(day, dt.time.min, zone).timestamp()
        end = dt.datetime.combine(day + dt.timedelta(days=1), dt.time.min, zone).timestamp()
        return day, start, end

    def _rows(self):
        # Deliberately do not filter by local account alias or run identifier.
        rows = self.actions.db.execute(
            'SELECT account,operation_id,binding_json,status,submitted_at,updated_at,lease_until,result FROM actions'
        ).fetchall()
        return [(row, json.loads(row['binding_json'])) for row in rows]

    def _expected_account(self, rows, explicit):
        if explicit is not None:
            if not isinstance(explicit, str) or not explicit.strip():
                raise ValueError('ERR_DAILY_COMMENTS: expected account required')
            return explicit
        configured = self.actions._config().get('expected_user_id')
        if configured:
            return configured
        accounts = {binding['expected_account'] for row, binding in rows
                    if row['account'] == self.actions.account}
        if len(accounts) != 1:
            raise ValueError('ERR_DAILY_COMMENTS: unambiguous expected account required')
        return accounts.pop()

    def _summary(self, date, timezone, expected_account):
        day, start, end = self._bounds(date, timezone)
        rows = self._rows()
        account = self._expected_account(rows, expected_account)
        confirmed = cumulative = pending = carryover = undated = prior_day = 0
        charged = set()
        for row, binding in rows:
            if (binding['expected_account'] != account
                    or binding['operation'] not in COMMENT_OPERATIONS):
                continue
            state = row['status']
            report = json.loads(row['result']) if row['result'] is not None else None
            if state == 'host_reported_success':
                # Missing/invalid success evidence is never silently called success.
                if not isinstance(report, dict) or report.get('status') != state:
                    raise ValueError('ERR_DAILY_COMMENTS: success lacks a host result report')
                observed = _timestamp(report.get('observed_at'), 'observed_at')
                cumulative += 1
                if start <= observed < end:
                    confirmed += 1
                    charged.add((row['account'], row['operation_id']))
            elif state in {'submitted_unknown', 'dispatching'}:
                stamp = row['submitted_at']
                if stamp is None and state == 'dispatching':
                    stamp = row['updated_at']
                if stamp is None:
                    # Legacy uncertainty without a date must not disappear.
                    undated += 1
                else:
                    if type(stamp) not in (int, float) or not math.isfinite(stamp):
                        raise ValueError('ERR_DAILY_COMMENTS: invalid submission timestamp')
                    if stamp >= end:
                        continue
                    if stamp < start:
                        prior_day += 1
                        # Only a still-live cross-midnight attempt can plausibly
                        # perform its actual Send now. Static old uncertainty
                        # is retained separately, never reset or called success.
                        lease = row['lease_until']
                        if lease is None or lease <= self.actions.clock():
                            continue
                        carryover += 1
                pending += 1
                charged.add((row['account'], row['operation_id']))
        result = {
            'expected_account': account, 'date': day.isoformat(), 'timezone': TIMEZONE,
            'limit': DAILY_LIMIT,
            'today_confirmed': confirmed,
            'cumulative_known_success': cumulative,
            'pending_uncertain': pending,
            'pending_uncertain_carryover': carryover,
            'prior_day_unresolved': prior_day,
            'pending_uncertain_undated': undated,
            'charged_total': confirmed + pending,
            'remaining': max(0, DAILY_LIMIT - confirmed - pending),
            'success_provenance': HOST_REPORT,
            'success_day_source': 'result.observed_at',
            'uncertainty_day_source': 'submitted_at; pre-submit claims use updated_at; only live prior-day claims consume current capacity',
            'browser_state_independently_verified': False,
            'grants_execution_authority': False,
            'scope': 'expected_account across aliases and runs in this shared ledger',
        }
        return result, charged

    def summary(self, date=None, *, timezone=TIMEZONE, expected_account=None):
        """Current ledger evidence grouped into a Shanghai natural day.

        Cumulative success is all-time. Old unresolved dispatches are reported
        separately and consume capacity only while their claim is still live.
        They remain unresolved and subject to the existing write-unknown guard.
        This is current evidence, not an as-of history replay.
        With no explicit account, use configured expected_user_id or the sole
        expected account recorded for the current local alias.
        """
        return self._summary(date, timezone, expected_account)[0]

    def guard(self, operation_id):
        """Veto comment admission at 50; a pass is not permission or a reservation."""
        row = self.actions._row(operation_id)
        binding = json.loads(row['binding_json'])
        if binding['operation'] not in COMMENT_OPERATIONS:
            return {'applies': False, 'grants_execution_authority': False}
        if row['status'] in {'host_reported_success', 'not_applied', 'cancelled'}:
            raise ValueError('ERR_DAILY_COMMENTS: terminal comment must not be sent again')
        result, charged = self._summary(None, TIMEZONE, binding['expected_account'])
        already_charged = (row['account'], operation_id) in charged
        projected = result['charged_total'] + (0 if already_charged else 1)
        if projected > DAILY_LIMIT:
            raise ValueError('ERR_DAILY_COMMENT_LIMIT: 50 comments per Asia/Shanghai day; '
                             'confirmed and unresolved dispatches exhaust capacity')
        return dict(result, applies=True, current_operation_already_charged=already_charged,
                    projected_total=projected)
