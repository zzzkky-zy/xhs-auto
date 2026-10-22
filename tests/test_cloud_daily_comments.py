"""Synthetic offline accounting fixtures. No private state or browser access."""
import datetime as dt
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'runtime'))
from cloud_actions import CloudActions
from cloud_daily_comments import DailyComments


def stamp(value):
    return dt.datetime.fromisoformat(value.replace('Z', '+00:00')).timestamp()


class DailyCommentTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.config = Path(self.tmp.name) / 'config.json'
        self.data = dict(schema_version=1, browser_mode='cloud_host', account_id='alias-a',
                         expected_user_id='platform-a', state_dir='state',
                         allow_write=False, allow_reconcile=False)
        self.config.write_text(json.dumps(self.data))
        self.now = stamp('2026-10-20T04:00:00Z')
        self.actions = CloudActions(self.config, clock=lambda: self.now)
        self.daily = DailyComments(self.actions)
        self.index = 0

    def tearDown(self):
        self.actions.close()
        self.tmp.cleanup()

    def add(self, state='host_reported_success', *, observed='2026-10-20T03:00:00Z',
            submitted='2026-10-20T02:59:00Z', account='platform-a', alias='alias-a',
            operation='post_comment_to_feed', run='run-1'):
        self.index += 1
        ident = 'synthetic-%d' % self.index
        # Use prepare for real bindings; update only this test's temporary ledger.
        prior = self.actions.account
        self.actions.account = alias
        self.data['account_id'] = alias
        self.config.write_text(json.dumps(self.data))
        try:
            self.actions.prepare(operation, {'feed_id': 'a' * 24, 'content': ident,
                                             'run_id': run}, expected_account=account,
                                 target={'feed_id': 'a' * 24}, user_source='fixture', operation_id=ident)
        finally:
            self.actions.account = prior
            self.data['account_id'] = prior
            self.config.write_text(json.dumps(self.data))
        result = (json.dumps({'status': state, 'observed_at': observed})
                  if state == 'host_reported_success' else None)
        self.actions.db.execute('UPDATE actions SET status=?,submitted_at=?,result=?,updated_at=? '
                                'WHERE account=? AND operation_id=?',
                                (state, stamp(submitted) if submitted else None, result,
                                 self.now, alias, ident))
        return ident

    def test_success_day_is_observation_not_cumulative_or_submission(self):
        for _ in range(9):
            self.add(observed='2026-10-19T15:59:59Z', submitted='2026-10-19T15:58:00Z')
        for _ in range(3):
            self.add(submitted='2026-10-19T15:59:59Z', run='run-2')
        result = self.daily.summary()
        self.assertEqual(result['today_confirmed'], 3)
        self.assertEqual(result['cumulative_known_success'], 12)
        self.assertEqual(result['remaining'], 47)
        self.assertIn('host_reported_unverified', result['success_provenance'])
        self.assertFalse(result['grants_execution_authority'])

    def test_shanghai_midnight_and_fixed_timezone(self):
        self.add(observed='2026-10-19T15:59:59Z')
        self.add(observed='2026-10-19T16:00:00Z')
        self.add(observed='2026-10-20T16:00:00Z')
        self.assertEqual(self.daily.summary('2026-10-20')['today_confirmed'], 1)
        self.assertEqual(self.daily.summary(dt.date(2026, 10, 19))['today_confirmed'], 1)
        with self.assertRaisesRegex(ValueError, 'Asia/Shanghai'):
            self.daily.summary(timezone='UTC')
        with self.assertRaises(ValueError):
            self.daily.summary('20261020')

    def test_main_and_reply_across_runs_and_aliases_share_account_limit(self):
        for i in range(50):
            self.add(alias='alias-a' if i < 25 else 'alias-b', run='run-%d' % i,
                     operation='post_comment_to_feed' if i % 2 else 'reply_comment_in_feed')
        self.add(account='platform-b')
        self.add(operation='like_feed')
        candidate = self.add('prepared')
        result = self.daily.summary()
        self.assertEqual(result['today_confirmed'], 50)
        self.assertEqual(result['cumulative_known_success'], 50)
        with self.assertRaisesRegex(ValueError, 'ERR_DAILY_COMMENT_LIMIT'):
            self.daily.guard(candidate)

    def test_old_unknowns_remain_uncertain_separate_from_today(self):
        self.add('submitted_unknown', submitted='2026-10-10T01:00:00Z')
        self.add('submitted_unknown', submitted=None)
        self.add('submitted_unknown')
        self.add('not_applied')
        self.add('ready', submitted=None)
        self.add('prepared', submitted=None)
        self.add('cancelled', submitted=None)
        result = self.daily.summary()
        self.assertEqual(result['today_confirmed'], 0)
        self.assertEqual(result['cumulative_known_success'], 0)
        self.assertEqual(result['pending_uncertain'], 2)
        self.assertEqual(result['pending_uncertain_carryover'], 0)
        self.assertEqual(result['prior_day_unresolved'], 1)
        self.assertEqual(result['pending_uncertain_undated'], 1)
        self.assertEqual(result['charged_total'], 2)

    def test_live_cross_midnight_submission_counts_until_lease_expires(self):
        ident = self.add('submitted_unknown', submitted='2026-10-19T15:59:59Z')
        self.actions.db.execute('UPDATE actions SET lease_until=? WHERE operation_id=?',
                                (self.now + 60, ident))
        result = self.daily.summary()
        self.assertEqual(result['pending_uncertain'], 1)
        self.assertEqual(result['prior_day_unresolved'], 1)
        self.assertEqual(result['pending_uncertain_carryover'], 1)
        self.now += 61
        result = self.daily.summary()
        self.assertEqual(result['pending_uncertain'], 0)
        self.assertEqual(result['prior_day_unresolved'], 1)
        self.assertEqual(result['cumulative_known_success'], 0)

    def test_current_pending_not_double_counted_at_fiftieth_slot(self):
        for _ in range(49):
            self.add()
        current = self.add('dispatching', submitted=None)
        result = self.daily.guard(current)
        self.assertTrue(result['current_operation_already_charged'])
        self.assertEqual(result['projected_total'], 50)
        self.actions.db.execute("UPDATE actions SET status='submitted_unknown',submitted_at=? WHERE operation_id=?",
                                (self.now, current))
        self.assertEqual(self.daily.guard(current)['projected_total'], 50)
        next_one = self.add('prepared', submitted=None)
        with self.assertRaisesRegex(ValueError, 'ERR_DAILY_COMMENT_LIMIT'):
            self.daily.guard(next_one)

    def test_dispatching_other_operation_consumes_last_slot(self):
        for _ in range(49):
            self.add()
        self.add('dispatching', submitted=None)
        with self.assertRaisesRegex(ValueError, 'ERR_DAILY_COMMENT_LIMIT'):
            self.daily.guard(self.add('ready', submitted=None))

    def test_unknown_reconciliation_releases_charge_without_reset(self):
        unknown = self.add('submitted_unknown')
        self.assertEqual(self.daily.summary()['remaining'], 49)
        self.actions.db.execute("UPDATE actions SET status='not_applied' WHERE operation_id=?", (unknown,))
        self.assertEqual(self.daily.summary()['remaining'], 50)

    def test_constructor_summary_and_guard_do_not_mutate_ledger(self):
        ident = self.add('prepared', submitted=None)
        before = list(self.actions.db.iterdump())
        daily = DailyComments(self.actions)
        daily.summary()
        daily.guard(ident)
        self.assertEqual(before, list(self.actions.db.iterdump()))

    def test_no_terminal_replay_and_noncomment_noop(self):
        with self.assertRaisesRegex(ValueError, 'terminal'):
            self.daily.guard(self.add())
        result = self.daily.guard(self.add('ready', operation='like_feed'))
        self.assertFalse(result['applies'])
        self.assertFalse(result['grants_execution_authority'])

    def test_missing_success_report_fails_closed(self):
        ident = self.add()
        self.actions.db.execute('UPDATE actions SET result=NULL WHERE operation_id=?', (ident,))
        with self.assertRaisesRegex(ValueError, 'success lacks'):
            self.daily.summary()

    def test_account_inference_without_configuration_and_ambiguity(self):
        self.data.pop('expected_user_id')
        self.config.write_text(json.dumps(self.data))
        self.add()
        self.assertEqual(self.daily.summary()['expected_account'], 'platform-a')
        self.add(account='platform-b')
        with self.assertRaisesRegex(ValueError, 'unambiguous'):
            self.daily.summary()
        self.assertEqual(self.daily.summary(expected_account='platform-b')['today_confirmed'], 1)


if __name__ == '__main__':
    unittest.main()
