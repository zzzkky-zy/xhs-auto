"""Durable notification decisions, never a message sender or permission grant.

Host must verify event meaning, urgency, and delivery evidence. Canonical event
keys identify substantive state, never runs, timestamps or evidence references.
This module applies only to proactive operations, never direct user replies. All aliases
for one exact account must share the same action ledger. Consume BEFORE sending;
a lost response stays unknown and must never trigger another send.
"""
import datetime as dt
import hashlib
import json
from zoneinfo import ZoneInfo

from cloud_actions import _identifier, _iso, _json, _text


class CloudNotifications:
    def __init__(self, actions):
        self.actions = actions
        self.expected = actions._config().get('expected_user_id')
        _text(self.expected, 'configured exact expected_user_id', 256)
        with actions._transaction():
            actions.db.execute('''CREATE TABLE IF NOT EXISTS operator_notifications (
                expected_account TEXT NOT NULL, notification_id TEXT NOT NULL,
                fingerprint TEXT NOT NULL, data TEXT NOT NULL,
                PRIMARY KEY(expected_account, notification_id),
                UNIQUE(expected_account, fingerprint))''')

    def _account(self):
        if self.actions._config().get('expected_user_id') != self.expected:
            raise ValueError('notification exact account changed')
        return self.actions.execution_pause_status(self.expected)

    def _read(self, notification_id):
        _identifier(notification_id, 'notification_id')
        self._account()
        row = self.actions.db.execute('SELECT data FROM operator_notifications WHERE expected_account=? AND notification_id=?',
                                      (self.expected, notification_id)).fetchone()
        if row is None:
            raise ValueError('unknown notification')
        return json.loads(row['data'])

    def _save(self, record):
        record['updated_at'] = _iso(self.actions.clock())
        self.actions._admit(record)
        self.actions.db.execute('INSERT INTO operator_notifications VALUES (?,?,?,?) ON CONFLICT(expected_account,notification_id) DO UPDATE SET data=excluded.data',
                               (self.expected, record['notification_id'], record['fingerprint'], _json(record)))

    def _view(self, record):
        result = json.loads(_json(record))
        result.update(message_sent=False, automatic_execution=False, send_once=False)
        if record['status'] in {'sent', 'unknown'}:
            result.update(decision='suppress', reason='already_sent_or_delivery_unknown')
            return result
        paused = self._account()['paused']
        if paused and not record['urgent'] and (record['kind'] in {'blocker', 'decision'} or record['requests_resume']):
            result.update(decision='suppress', reason='user_paused_no_operational_nag')
            return result
        local = dt.datetime.fromtimestamp(self.actions.clock(), ZoneInfo('Asia/Shanghai'))
        if not record['urgent'] and (local.hour >= 22 or local.hour < 9):
            next_day = local.date() + dt.timedelta(days=int(local.hour >= 22))
            wake = dt.datetime.combine(next_day, dt.time(9), tzinfo=ZoneInfo('Asia/Shanghai'))
            result.update(decision='hold', reason='quiet_hours', not_before=wake.isoformat())
            return result
        result.update(decision='send', reason='substantive_event')
        return result

    def decide(self, event, kind, evidence_ref, urgent=False, requests_resume=False, urgency_evidence_ref=None):
        """Record a verified substantive event; no progress/heartbeat kind exists.

        Same fingerprint ignores new evidence refs and cannot change event meaning.
        A pending event may escalate urgency based on new host-verified evidence;
        consumed events remain suppressed, even when urgency changes.
        """
        if not isinstance(event, dict) or set(event) != {'target_id', 'reason_code', 'state_version'}:
            raise ValueError('canonical event requires target_id, reason_code and substantive state_version only')
        for key, value in event.items():
            _text(value, key, 256)
        fingerprint = hashlib.sha256(_json({'kind': kind, 'event': event}).encode()).hexdigest()
        _text(evidence_ref, 'evidence_ref', 1000)
        if kind not in {'blocker', 'decision', 'completed'}:
            raise ValueError('only substantive blocker, decision or actual completion may notify')
        if type(urgent) is not bool or type(requests_resume) is not bool:
            raise ValueError('notification flags must be booleans')
        if urgent:
            _text(urgency_evidence_ref, 'host-verified urgency_evidence_ref', 1000)
        elif urgency_evidence_ref is not None:
            raise ValueError('urgency evidence requires urgent event')
        with self.actions._transaction():
            self._account()
            notification_id = 'notice-' + hashlib.sha256((self.expected + '\0' + fingerprint).encode()).hexdigest()
            row = self.actions.db.execute('SELECT data FROM operator_notifications WHERE expected_account=? AND notification_id=?',
                                          (self.expected, notification_id)).fetchone()
            if row:
                record = json.loads(row['data'])
                if record['kind'] != kind or record['requests_resume'] != requests_resume:
                    raise ValueError('same fingerprint must retain event meaning')
                if urgent and not record['urgent'] and record['status'] == 'pending':
                    record.update(urgent=True, urgency_evidence_ref=urgency_evidence_ref)
                    self._save(record)
            else:
                record = dict(notification_id=notification_id, expected_account=self.expected,
                              fingerprint=fingerprint, event=event, kind=kind, evidence_ref=evidence_ref,
                              urgency_evidence_ref=urgency_evidence_ref,
                              urgent=urgent, requests_resume=requests_resume, status='pending',
                              created_at=_iso(self.actions.clock()))
                self._save(record)
            return self._view(record)

    def consume(self, notification_id):
        """One dispatch boundary. Commit unknown before the host actually sends."""
        with self.actions._transaction():
            record = self._read(notification_id)
            view = self._view(record)
            if view['decision'] != 'send':
                return view
            record.update(status='unknown', consumed_at=_iso(self.actions.clock()))
            self._save(record)
            result = self._view(record)
            result.update(decision='dispatch_once', send_once=True, reason='consumed_before_external_send')
            return result

    def record_result(self, notification_id, status, evidence_ref):
        if status not in {'sent', 'unknown'}:
            raise ValueError('delivery outcome must be sent or unknown; no resend transition')
        _text(evidence_ref, 'delivery evidence_ref', 1000)
        with self.actions._transaction():
            record = self._read(notification_id)
            if record['status'] == 'pending':
                raise ValueError('consume before sending or recording delivery')
            if record['status'] == 'sent' and status == 'unknown':
                raise ValueError('confirmed delivery cannot regress')
            record.update(status=status, delivery_evidence_ref=evidence_ref)
            self._save(record)
            return self._view(record)

    def get(self, notification_id):
        with self.actions._transaction():
            return self._view(self._read(notification_id))

    def pending(self, limit=100):
        """Rediscover held/suppressed notices after restart without consuming them."""
        if type(limit) is not int or not 1 <= limit <= 500:
            raise ValueError('pending limit must be integer 1..500')
        with self.actions._transaction():
            self._account()
            records = [json.loads(row['data']) for row in self.actions.db.execute(
                'SELECT data FROM operator_notifications WHERE expected_account=? ORDER BY notification_id',
                (self.expected,))]
            pending = [record for record in records if record['status'] == 'pending']
            pending.sort(key=lambda record: (self._view(record)['decision'] != 'send',
                                             record['created_at'], record['notification_id']))
            return dict(notifications=[self._view(record) for record in pending[:limit]],
                        pending_count=len(pending), truncated=len(pending) > limit,
                        automatic_execution=False, message_sent=False)
