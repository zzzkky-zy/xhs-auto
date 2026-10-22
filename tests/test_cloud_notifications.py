"""Synthetic notification decisions only: no user sends, no account/browser access."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_actions import CloudActions
from cloud_notifications import CloudNotifications


class NotificationTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'cloud.json'
        self.settings = dict(schema_version=1, browser_mode='cloud_host', account_id='fixture',
                             expected_user_id='exact-fixture', state_dir='state', allow_write=False,
                             allow_reconcile=False)
        self.config.write_text(json.dumps(self.settings))
        self.now = dt.datetime(2026, 10, 22, 2, tzinfo=dt.timezone.utc).timestamp()  # 10 Beijing
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.notes = CloudNotifications(self.actions)

    def tearDown(self):
        self.actions.close(); self.tmp.cleanup()

    def event(self, target='original-page'):
        return dict(target_id=target, reason_code='control-unavailable', state_version='unresolved')

    def notice(self, **kw):
        if kw.get('urgent') is True: kw['urgency_evidence_ref'] = 'fixture:host-verified-urgent'
        return self.notes.decide(self.event(), 'blocker', 'fixture:evidence', **kw)

    def test_pending_not_delivery_then_consume_once_across_alias_restart(self):
        note = self.notice()
        self.assertEqual(note['decision'], 'send'); self.assertFalse(note['message_sent'])
        first = self.notes.consume(note['notification_id'])
        self.assertTrue(first['send_once']); self.assertEqual(first['status'], 'unknown')
        self.actions.close()
        self.settings['account_id'] = 'different-alias'
        self.config.write_text(json.dumps(self.settings))
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.notes = CloudNotifications(self.actions)
        self.assertEqual(self.notice()['decision'], 'suppress')
        self.assertFalse(self.notes.consume(note['notification_id'])['send_once'])
        sent = self.notes.record_result(note['notification_id'], 'sent', 'fixture:delivery')
        self.assertEqual(sent['status'], 'sent')
        self.assertFalse(sent['message_sent'])  # module never sends
        self.assertEqual(self.notice(urgent=True)['decision'], 'suppress')

    def test_night_hold_becomes_send_at_9(self):
        self.now += 12 * 3600  # 22 Beijing
        note = self.notice()
        self.assertEqual(note['decision'], 'hold')
        self.assertTrue(note['not_before'].endswith('09:00:00+08:00'))
        self.assertFalse(self.notes.consume(note['notification_id'])['send_once'])
        self.now += 11 * 3600
        self.assertEqual(self.notes.get(note['notification_id'])['decision'], 'send')

    def test_pause_suppresses_nags_not_urgent_or_actual_completion(self):
        self.actions.set_execution_pause(True, 'fixture:user', 'paused')
        self.assertEqual(self.notice()['reason'], 'user_paused_no_operational_nag')
        self.assertEqual(self.notice(urgent=True)['decision'], 'send')
        complete = self.notes.decide(self.event('engineering-done'), 'completed', 'fixture:verified')
        self.assertEqual(complete['decision'], 'send')

    def test_urgency_unholds_night_but_not_consumed(self):
        self.now += 12 * 3600
        self.assertEqual(self.notice()['decision'], 'hold')
        urgent = self.notice(urgent=True)
        self.assertEqual(urgent['decision'], 'send')
        self.notes.consume(urgent['notification_id'])
        self.assertEqual(self.notice(urgent=True)['decision'], 'suppress')

    def test_new_reference_does_not_evade_dedup(self):
        note = self.notice(); self.notes.consume(note['notification_id'])
        other = self.notes.decide(self.event(), 'blocker', 'fixture:new-run-ref')
        self.assertEqual(other['decision'], 'suppress')
        with self.assertRaises(ValueError): self.notes.decide(self.event(), 'blocker', 'fixture:x', requests_resume=True)

    def test_reject_progress_unconsumed_delivery_bad_flags_account_change(self):
        with self.assertRaises(ValueError): self.notes.decide(self.event(), 'progress', 'fixture:x')
        with self.assertRaises(ValueError): self.notice(urgent=1)
        note = self.notice()
        with self.assertRaises(ValueError): self.notes.record_result(note['notification_id'], 'sent', 'fixture:x')
        self.settings['expected_user_id'] = 'other-exact'
        self.config.write_text(json.dumps(self.settings))
        with self.assertRaises(ValueError): self.notes.get(note['notification_id'])

    def test_sent_cannot_regress_and_unknown_cannot_reset(self):
        note = self.notice(); self.notes.consume(note['notification_id'])
        with self.assertRaises(ValueError): self.notes.record_result(note['notification_id'], 'pending', 'fixture:x')
        self.notes.record_result(note['notification_id'], 'sent', 'fixture:x')
        with self.assertRaises(ValueError): self.notes.record_result(note['notification_id'], 'unknown', 'fixture:y')

    def test_two_live_connections_only_one_consumes(self):
        note = self.notice()
        peer = CloudActions(self.config, clock=lambda: self.now)
        try:
            other = CloudNotifications(peer)
            self.assertTrue(self.notes.consume(note['notification_id'])['send_once'])
            self.assertFalse(other.consume(note['notification_id'])['send_once'])
        finally:
            peer.close()

    def test_other_exact_account_has_separate_notification(self):
        self.notice()
        self.actions.close()
        self.settings.update(account_id='other-alias', expected_user_id='other-exact')
        self.config.write_text(json.dumps(self.settings))
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.notes = CloudNotifications(self.actions)
        self.assertEqual(self.notice()['decision'], 'send')
        self.assertEqual(self.actions.db.execute('SELECT COUNT(*) FROM operator_notifications').fetchone()[0], 2)

    def test_quiet_hour_edges_and_completion_hold(self):
        self.now -= 3601  # 08:59:59 Beijing
        note = self.notes.decide(self.event('complete'), 'completed', 'fixture:actual')
        self.assertEqual(note['decision'], 'hold')
        self.now += 1
        self.assertEqual(self.notes.get(note['notification_id'])['decision'], 'send')
        self.now += 13 * 3600 - 1
        self.assertEqual(self.notes.get(note['notification_id'])['decision'], 'send')
        self.now += 1
        self.assertEqual(self.notes.get(note['notification_id'])['decision'], 'hold')

    def test_pending_decision_rechecks_pause_before_consume(self):
        note = self.notice()
        self.actions.set_execution_pause(True, 'fixture:user', 'paused')
        self.assertFalse(self.notes.consume(note['notification_id'])['send_once'])
        self.assertEqual(self.notes.get(note['notification_id'])['status'], 'pending')


    def test_canonical_key_rejects_caller_fingerprint_and_requires_urgency_evidence(self):
        with self.assertRaises(ValueError):
            self.notes.decide('arbitrary-new-fingerprint', 'blocker', 'fixture:x')
        changed = dict(self.event(), run_id='new-run')
        with self.assertRaises(ValueError): self.notes.decide(changed, 'blocker', 'fixture:x')
        with self.assertRaises(ValueError): self.notes.decide(self.event(), 'blocker', 'fixture:x', urgent=True)
        first = self.notice(); self.notes.consume(first['notification_id'])
        reordered = dict(reversed(list(self.event().items())))
        second = self.notes.decide(reordered, 'blocker', 'fixture:different-evidence')
        self.assertEqual(first['notification_id'], second['notification_id'])
        self.assertEqual(second['decision'], 'suppress')

    def test_pending_rediscovers_held_notice_after_restart_without_consuming(self):
        self.now += 12 * 3600
        note = self.notice()
        self.actions.close()
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.notes = CloudNotifications(self.actions)
        self.now += 11 * 3600
        pending = self.notes.pending()
        self.assertEqual(pending['pending_count'], 1)
        self.assertEqual(pending['notifications'][0]['notification_id'], note['notification_id'])
        self.assertEqual(pending['notifications'][0]['decision'], 'send')
        self.assertFalse(pending['notifications'][0]['send_once'])
        self.notes.consume(note['notification_id'])
        self.assertEqual(self.notes.pending()['pending_count'], 0)
        with self.assertRaises(ValueError): self.notes.pending(limit=True)


if __name__ == '__main__': unittest.main()
