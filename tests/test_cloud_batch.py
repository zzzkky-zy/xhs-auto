"""Synthetic local batch tests. Never evidence of real review or publication."""
import datetime as dt
import hashlib
import json
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock
from zoneinfo import ZoneInfo

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
import cloud_batch
from cloud_actions import CloudActions
import content_fixture


class CloudBatchTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.base = Path(self.tmp.name)
        self.path = self.base / 'config.json'
        self.data = {'schema_version': 1, 'browser_mode': 'cloud_host', 'account_id': 'fixture',
                     'expected_user_id': 'a' * 24, 'state_dir': 'state', 'output_dir': 'output',
                     'allow_write': False, 'allow_reconcile': False,
                     'resources': {'min_free_disk_mb': 1, 'min_free_inodes': 1}}
        self.path.write_text(json.dumps(self.data))
        self.now = [1800000000.0]
        self.actions = CloudActions(self.path, clock=lambda: self.now[0])
        self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.batch.dispatch('configure', {
            'authorization_ref': 'SYNTHETIC TEST ONLY: not user permission',
            'quality_standard': 'SYNTHETIC TEST ONLY: offline structural checks',
            'allowed_tools': ['publish_content', 'post_comment_to_feed'],
            'content_profile': content_fixture.profile(self.base)})

    def tearDown(self):
        self.batch.close()
        self.actions.close()
        self.tmp.cleanup()

    def begin(self):
        return self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC TEST ONLY',
                                           'plan': {'posts': 1, 'comments': 1,
                                                    'comment_strategy': {'A': 1, 'B': 0, 'C': 0}}})

    def reviewed(self):
        run = self.begin()
        root = self.batch.continuous.directory(run['id'])
        image = root / 'final' / 'synthetic.png'
        image.write_bytes(b'SYNTHETIC TEST ONLY: inert image fixture')
        first = dt.datetime.fromtimestamp(self.now[0] + 7200, dt.timezone.utc).isoformat()
        slots = self.batch.dispatch('schedule', {'run_id': run['id'], 'count': 2, 'first_at': first})
        items = [{'tool': 'publish_content', 'payload': {'title': 'synthetic', 'content': 'never publish this fixture',
                                                       'images': [str(image)], 'schedule_at': slots['schedule_slots'][0]}},
                 {'tool': 'post_comment_to_feed', 'payload': {'feed_id': 'b' * 24, 'xsec_token': 'nonsecret-fixture',
                                                             'content': 'SYNTHETIC TEST ONLY: no external action'}}]
        content_fixture.register(self.batch.continuous, run, items)
        self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items,
                                       'evidence': 'SYNTHETIC TEST ONLY: not semantic or visual review'})
        return run, items

    def test_local_contract_never_claims_authority_or_generation(self):
        result = self.batch.dispatch('status', {'include_profile': True})
        self.assertEqual(result['mode'], 'validation_required')
        self.assertEqual(result['execution'], 'local_only')
        self.assertFalse(result['browser_action_performed'])
        self.assertFalse(result['cloud_batch_capabilities']['generates_content'])
        self.assertFalse(result['cloud_batch_capabilities']['automatic_execution'])
        self.assertEqual(self.actions.list_actions(), [])

    def test_original_stage_review_and_media_sealing_stop_at_boundary(self):
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('subprocess forbidden')):
            run, items = self.reviewed()
            prepared = self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC reference only')
        self.assertTrue(prepared['host_action_required'])
        self.assertFalse(prepared['authorization_granted'])
        fixed = Path(prepared['payload']['images'][0])
        self.assertEqual(fixed.parent.name, 'sealed')
        self.assertEqual(fixed.read_bytes(), Path(items[0]['payload']['images'][0]).read_bytes())
        self.assertEqual(prepared['media_manifest'][0]['sha256'], hashlib.sha256(fixed.read_bytes()).hexdigest())
        self.assertEqual(prepared['payload']['run_id'], run['id'])
        self.assertEqual(self.actions.list_actions(), [])

    def test_approval_reference_missing_does_not_authorize_first_batch(self):
        run, items = self.reviewed()
        with self.assertRaisesRegex(ValueError, 'ERR_APPROVAL_REQUIRED'):
            self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'])

    def test_stage_evidence_mutation_invalidates_prepare(self):
        run, items = self.reviewed()
        Path(items[0]['payload']['images'][0]).write_bytes(b'changed')
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_REVIEW_STALE|ERR_QUALITY_REVIEW_REQUIRED'):
            self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')

    def test_changed_copy_is_not_current_review(self):
        run, items = self.reviewed()
        payload = {**items[1]['payload'], 'content': 'changed synthetic content'}
        with self.assertRaisesRegex(ValueError, 'ERR_QUALITY_REVIEW_REQUIRED'):
            self.batch.prepare_payload(run['id'], items[1]['tool'], payload, 'SYNTHETIC')

    def test_original_schedule_is_persisted_across_reopen(self):
        run, _ = self.reviewed()
        before = self.batch.dispatch('schedule', {'run_id': run['id']})['schedule_slots']
        self.batch.close()
        self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': run['id']})['schedule_slots'], before)
        stamps = [dt.datetime.fromisoformat(s).timestamp() for s in before]
        self.assertTrue(all(3600 <= b - a <= 5400 for a, b in zip(stamps, stamps[1:])))
        self.assertEqual(self.batch.dispatch('begin', {})['id'], run['id'])

    def test_incomplete_lifecycle_preserves_evidence(self):
        run, items = self.reviewed()
        for operation in ('finish', 'accept', 'repair'):
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.batch.dispatch(operation, {'run_id': run['id'], 'human_acceptance_ref': 'SYNTHETIC'})
        image = Path(items[0]['payload']['images'][0])
        result = self.batch.dispatch('maintain')
        self.assertFalse(result['cleanup_performed'])
        self.assertTrue(image.exists())
        self.assertEqual(self.batch.dispatch('status')['mode'], 'validation_required')

    def test_bind_existing_action_keeps_host_status_without_verifying_success(self):
        run, items = self.reviewed()
        prepared = self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')
        action = self.actions.prepare(items[0]['tool'], prepared['payload'], expected_account=self.data['expected_user_id'],
                                      target={'surface': 'creator'}, user_source='SYNTHETIC TEST ONLY',
                                      media_manifest=prepared['media_manifest'])
        result = self.batch.bind_action(run['id'], action['operation_id'])
        self.assertEqual(result['host_status'], 'prepared')
        self.assertFalse(result['platform_success_verified'])
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'prepared')
        self.assertEqual(self.batch.bind_action(run['id'], action['operation_id'])['host_status'], 'prepared')
        row = self.batch.db.execute('SELECT status FROM tasks WHERE id=?', (action['operation_id'],)).fetchone()
        self.assertEqual(row['status'], 'prepared')
        # An injected fake report is still only a report, never an original success.
        observed = {**action, 'status': 'host_reported_success', 'result': {'status': 'succeeded', 'evidence_ref': 'SYNTHETIC'}}
        with mock.patch.object(self.actions, 'get', return_value=observed):
            result = self.batch.bind_action(run['id'], action['operation_id'])
        self.assertEqual(result['host_status'], 'host_reported_success')
        self.assertFalse(result['platform_success_verified'])
        with self.assertRaisesRegex(ValueError, 'ERR_RUN_UNRESOLVED'):
            self.batch.dispatch('finish', {'run_id': run['id']})

    def test_wrong_account_and_run_cannot_bind(self):
        run, items = self.reviewed()
        prepared = self.batch.prepare_payload(run['id'], items[1]['tool'], items[1]['payload'], 'SYNTHETIC')
        for expected, payload in [('wrong-account', prepared['payload']),
                                  (self.data['expected_user_id'], {**prepared['payload'], 'run_id': 'c' * 32})]:
            action = self.actions.prepare(items[1]['tool'], payload, expected_account=expected,
                                          target={'feed_id': 'b' * 24}, user_source='SYNTHETIC')
            with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
                self.batch.bind_action(run['id'], action['operation_id'])

    def test_correlated_schedule_slot_cannot_be_silently_released(self):
        run, items = self.reviewed()
        prepared = self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')
        action = self.actions.prepare(items[0]['tool'], prepared['payload'], expected_account=self.data['expected_user_id'],
                                      target={'surface': 'creator'}, user_source='SYNTHETIC', media_manifest=prepared['media_manifest'])
        self.batch.bind_action(run['id'], action['operation_id'])
        slot = self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE schedule_at=?', (items[0]['payload']['schedule_at'],)).fetchone()
        self.assertEqual(slot['task_id'], action['operation_id'])
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'replace_unsubmitted': True, 'count': 1,
                                                 'first_at': dt.datetime.fromtimestamp(self.now[0] + 30000, dt.timezone.utc).isoformat()})
        self.assertIn(items[0]['payload']['schedule_at'], result['schedule_slots'])

    def explicit_slots(self, hours=(2, 3.5, 5), timezone=dt.timezone.utc):
        return [dt.datetime.fromtimestamp(self.now[0] + hour * 3600, timezone).isoformat() for hour in hours]

    def test_explicit_daily_ten_slots_are_exact_in_shanghai_without_randomness(self):
        run = self.begin()
        zone = ZoneInfo('Asia/Shanghai')
        tomorrow = dt.datetime.fromtimestamp(self.now[0], zone).date() + dt.timedelta(days=1)
        first = dt.datetime.combine(tomorrow, dt.time(9), zone)
        values = [(first + dt.timedelta(minutes=90 * n)).isoformat() for n in range(10)]
        with mock.patch('continuous_ops.secrets.randbelow', side_effect=AssertionError('explicit slots must not be randomized')):
            result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        self.assertEqual(result['schedule_slots'], values)
        self.assertEqual(result['new_slots'], values)
        self.assertEqual(len(values), 10)
        self.assertEqual([dt.datetime.fromisoformat(value).strftime('%H:%M') for value in values],
                         ['09:00', '10:30', '12:00', '13:30', '15:00', '16:30', '18:00', '19:30', '21:00', '22:30'])
        self.assertTrue(all(value.endswith('+08:00') for value in values))
        self.assertFalse(result['browser_action_performed'])
        self.assertEqual(self.actions.list_actions(), [])

    def test_explicit_two_daily_grids_preserve_each_night_gap(self):
        run = self.begin()
        zone = ZoneInfo('Asia/Shanghai')
        tomorrow = dt.datetime.fromtimestamp(self.now[0], zone).date() + dt.timedelta(days=1)
        values = [(dt.datetime.combine(tomorrow + dt.timedelta(days=day), dt.time(9), zone)
                   + dt.timedelta(minutes=90 * n)).isoformat() for day in range(2) for n in range(10)]
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        self.assertEqual(result['schedule_slots'], values)
        self.assertEqual((dt.datetime.fromisoformat(values[10]) - dt.datetime.fromisoformat(values[9])).total_seconds(), 10.5 * 3600)

    def test_explicit_repeat_survives_reopen_and_elapsed_time_without_changing_review(self):
        run = self.begin(); values = self.explicit_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        saved = self.batch.continuous.run(run['id']); saved['body']['review'] = {'synthetic': 'preserve on exact retry'}
        self.batch.continuous.save(saved)
        self.batch.close(); self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.now[0] += 2 * 86400
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        self.assertTrue(result['reused'])
        self.assertEqual(result['schedule_slots'], values)
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['review'], {'synthetic': 'preserve on exact retry'})
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM ops_calendar').fetchone()[0], 3)

    def test_explicit_equivalent_timezone_repeat_retains_original_spelling(self):
        run = self.begin(); values = self.explicit_slots(timezone=ZoneInfo('Asia/Shanghai'))
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        equivalent = [dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc).isoformat().replace('+00:00', 'Z') for value in values]
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': equivalent})
        self.assertTrue(result['reused']); self.assertEqual(result['schedule_slots'], values)

    def test_explicit_validation_rejects_count_type_naive_duplicates_order_and_spacing(self):
        run = self.begin(); valid = self.explicit_slots()
        duplicate_in_utc = dt.datetime.fromisoformat(valid[0]).astimezone(ZoneInfo('Asia/Shanghai')).isoformat()
        invalid = [None, [], 'not-list', [True], valid * 34, ['2027-01-01T09:00:00'],
                   ['2027-02-30T09:00:00+08:00'], [valid[0], duplicate_in_utc], list(reversed(valid)),
                   self.explicit_slots((2, 2.5))]
        for values in invalid:
            with self.subTest(values=str(values)[:100]), self.assertRaises(ValueError):
                self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], [])

    def test_explicit_new_slot_window_is_inclusive_one_hour_to_fourteen_days(self):
        run = self.begin()
        values = self.explicit_slots((1, 14 * 24))
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values})['schedule_slots'], values)
        for hour in (0, 0.999, 14 * 24 + 0.001):
            with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_WINDOW'):
                self.batch.dispatch('schedule', {'run_id': run['id'], 'replace_unsubmitted': True,
                                                  'explicit_slots': self.explicit_slots((hour,))})
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], values)

    def test_explicit_count_first_at_and_replace_flag_cannot_conflict(self):
        run = self.begin(); values = self.explicit_slots()
        for extra in ({'count': True}, {'count': 2}, {'first_at': values[1]}, {'replace_unsubmitted': 'true'}):
            with self.assertRaises(ValueError):
                self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values, **extra})
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': values, 'count': 3, 'first_at': values[0]})
        self.assertEqual(result['schedule_slots'], values)

    def test_different_explicit_plan_requires_replacement_and_invalidates_review_when_changed(self):
        run = self.begin(); original = self.explicit_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': original})
        replacement = self.explicit_slots((8, 9.5))
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXISTS'):
            self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': replacement})
        current = self.batch.continuous.run(run['id']); current['body']['review'] = {'synthetic': 'old review'}
        self.batch.continuous.save(current)
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': replacement, 'replace_unsubmitted': True})
        self.assertEqual(result['schedule_slots'], replacement)
        self.assertFalse(result['reused'])
        self.assertIsNone(self.batch.continuous.run(run['id'])['body']['review'])

    def test_explicit_replacement_preserves_owned_slot_and_cloud_cancel_releases_only_that_slot(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[0])
        owned = items[0]['payload']['schedule_at']
        replacement = self.explicit_slots((8, 9.5))
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': replacement, 'replace_unsubmitted': True})
        self.assertEqual(result['schedule_slots'], [owned, *replacement])
        self.assertEqual(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE schedule_at=?', (owned,)).fetchone()[0], action['operation_id'])
        # Including a reserved task-owned instant on a repeated desired plan must
        # not duplicate or detach the original action's calendar ownership.
        repeat = self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': [owned, *replacement], 'replace_unsubmitted': True})
        self.assertTrue(repeat['reused'])
        self.batch.dispatch('cancel', {'run_id': run['id'], 'task_id': action['operation_id']})
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], replacement)
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'cancelled')

    def test_explicit_owned_slot_collision_rolls_back_all_unsubmitted_changes(self):
        run, items = self.reviewed(); self.prepare_action(run, items[0])
        before = self.batch.continuous.run(run['id'])['body']['schedule_slots']
        owned_at = dt.datetime.fromisoformat(items[0]['payload']['schedule_at']).timestamp()
        conflict = dt.datetime.fromtimestamp(owned_at + 1800, dt.timezone.utc).isoformat()
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_CONFLICT'):
            self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': [conflict], 'replace_unsubmitted': True})
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], before)

    def test_explicit_cross_batch_collision_is_atomic_and_exact_hour_boundary_is_allowed(self):
        first = self.begin(); occupied = self.explicit_slots((8,))
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': occupied})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin(); original = self.explicit_slots((2, 3.5))
        self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': original})
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_CONFLICT'):
            self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': self.explicit_slots((8.5,)), 'replace_unsubmitted': True})
        self.assertEqual(self.batch.continuous.run(first['id'])['body']['schedule_slots'], occupied)
        self.assertEqual(self.batch.continuous.run(second['id'])['body']['schedule_slots'], original)
        result = self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': self.explicit_slots((9,)), 'replace_unsubmitted': True})
        self.assertEqual(result['schedule_slots'], self.explicit_slots((9,)))

    def test_explicit_slots_do_not_change_original_random_fallback(self):
        run = self.begin(); first = self.explicit_slots((2,))[0]
        with mock.patch('continuous_ops.secrets.randbelow', return_value=900) as random:
            result = self.batch.dispatch('schedule', {'run_id': run['id'], 'count': 3, 'first_at': first})
        self.assertEqual(random.call_count, 2)
        expected = self.explicit_slots((2, 3.25, 4.5))
        self.assertEqual(result['schedule_slots'], expected)
        self.assertNotIn('schedule_source', result)

    def enable_overlap(self, first):
        self.data['cloud_schedule_overlap_exception'] = {
            'first_run_id': first['id'], 'user_source': 'SYNTHETIC owner source',
            'user_quote': 'SYNTHETIC TEST ONLY: first batch may overlap later regular schedules'}
        self.path.write_text(json.dumps(self.data))

    def daily_slots(self, day=1):
        zone = ZoneInfo('Asia/Shanghai')
        date = dt.datetime.fromtimestamp(self.now[0], zone).date() + dt.timedelta(days=day)
        first = dt.datetime.combine(date, dt.time(9), zone)
        return [(first + dt.timedelta(minutes=90 * n)).isoformat() for n in range(10)]

    def test_bound_overlap_allows_exact_ties_and_preserves_both_run_slots_after_reopen(self):
        first = self.begin(); self.enable_overlap(first); grid = self.daily_slots()
        first_start = dt.datetime.fromisoformat(grid[0]) - dt.timedelta(hours=12)
        initial = [(first_start + dt.timedelta(minutes=90 * n)).isoformat() for n in range(10)]
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': initial})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        later = self.begin()
        result = self.batch.dispatch('schedule', {'run_id': later['id'], 'explicit_slots': grid})
        self.assertEqual(result['schedule_slots'], grid)
        self.assertFalse(result['browser_action_performed'])
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM ops_calendar').fetchone()[0], 20)
        overlap = self.batch.db.execute('SELECT count(*) FROM ops_calendar WHERE at=?',
                                       (dt.datetime.fromisoformat(grid[0]).timestamp(),)).fetchone()[0]
        self.assertEqual(overlap, 2)
        saved = self.batch.continuous.run(later['id'])['body']['cloud_schedule_plan']
        self.assertEqual(saved['overlapped_run_ids'], [first['id']])
        self.assertEqual(saved['exception'], self.data['cloud_schedule_overlap_exception'])
        self.batch.close(); self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.assertEqual(self.batch.continuous.run(first['id'])['body']['schedule_slots'], initial)
        self.assertTrue(self.batch.dispatch('schedule', {'run_id': later['id'], 'explicit_slots': grid})['reused'])
        self.assertEqual(self.actions.list_actions(), [])

    def test_bound_overlap_never_exempts_two_regular_batches(self):
        first = self.begin(); self.enable_overlap(first); grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': grid})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin()
        self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': grid})
        self.batch.dispatch('finish', {'run_id': second['id'], 'abandon': True})
        third = self.begin()
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_CONFLICT'):
            self.batch.dispatch('schedule', {'run_id': third['id'], 'explicit_slots': grid})
        self.assertEqual(self.batch.continuous.run(third['id'])['body']['schedule_slots'], [])
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': third['id'], 'explicit_slots': self.daily_slots(2)})['schedule_slots'], self.daily_slots(2))

    def test_bound_overlap_requires_exact_first_spacing_and_complete_regular_grids(self):
        first = self.begin(); self.enable_overlap(first)
        for hours in ((2, 3), (2, 4), (2, 3.5, 5.01), tuple(2 + 1.5 * n for n in range(20))):
            with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION_GRID'):
                self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': self.explicit_slots(hours)})
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': self.daily_slots()})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        later = self.begin(); grid = self.daily_slots()
        for values in (grid[:9], grid[1:], [value.replace('09:00:', '09:01:') for value in grid],
                       self.explicit_slots(), grid + self.daily_slots(2), [value.replace('+08:00', '+00:00') for value in grid]):
            with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION_GRID|ERR_SCHEDULE_SPACING'):
                self.batch.dispatch('schedule', {'run_id': later['id'], 'explicit_slots': values})
        utc_grid = [dt.datetime.fromisoformat(value).astimezone(dt.timezone.utc).isoformat() for value in grid]
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': later['id'], 'explicit_slots': utc_grid})['schedule_slots'], utc_grid)

    def test_bound_overlap_live_removal_restores_collision_checks_without_erasing_history(self):
        first = self.begin(); self.enable_overlap(first); grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': grid})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin()
        del self.data['cloud_schedule_overlap_exception']; self.path.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_CONFLICT'):
            self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': grid})
        self.assertEqual(self.batch.continuous.run(first['id'])['body']['schedule_slots'], grid)
        self.enable_overlap(first)
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': grid})['schedule_slots'], grid)

    def test_bound_overlap_rejects_bare_flags_missing_sources_and_nonoriginal_first_id(self):
        first = self.begin(); self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin(); self.enable_overlap(second)
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION'):
            self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': self.daily_slots()})
        self.enable_overlap(first); valid = dict(self.data['cloud_schedule_overlap_exception'])
        for invalid in (True, None, {}, {**valid, 'user_source': ''}, {**valid, 'user_quote': False},
                        {**valid, 'first_run_id': 'missing'}, {**valid, 'allow': True}):
            self.data['cloud_schedule_overlap_exception'] = invalid; self.path.write_text(json.dumps(self.data))
            with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION'):
                self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': self.daily_slots()})

    def test_overlap_replacement_is_atomic_and_does_not_erase_regular_conflicts(self):
        first = self.begin(); self.enable_overlap(first); first_grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': first_grid})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin(); occupied = self.daily_slots(2)
        self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': occupied})
        self.batch.dispatch('finish', {'run_id': second['id'], 'abandon': True})
        third = self.begin(); original = self.daily_slots(3)
        self.batch.dispatch('schedule', {'run_id': third['id'], 'explicit_slots': original})
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_CONFLICT'):
            self.batch.dispatch('schedule', {'run_id': third['id'], 'explicit_slots': occupied,
                                             'replace_unsubmitted': True})
        self.assertEqual(self.batch.continuous.run(third['id'])['body']['schedule_slots'], original)
        self.assertEqual(self.batch.continuous.run(second['id'])['body']['schedule_slots'], occupied)
        result = self.batch.dispatch('schedule', {'run_id': third['id'], 'explicit_slots': first_grid,
                                                 'replace_unsubmitted': True})
        self.assertEqual(result['schedule_slots'], first_grid)

    def test_overlap_exception_rejects_random_schedule_for_first_and_regular_batches(self):
        first = self.begin(); self.enable_overlap(first); grid = self.daily_slots()
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION_EXPLICIT_REQUIRED'):
            self.batch.dispatch('schedule', {'run_id': first['id'], 'first_at': grid[0], 'count': 10})
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': grid})
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': first['id']})['schedule_slots'], grid)
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        later = self.begin()
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_EXCEPTION_EXPLICIT_REQUIRED'):
            self.batch.dispatch('schedule', {'run_id': later['id'], 'first_at': grid[0], 'count': 10})

    def test_cloud_calendar_migration_preserves_task_ownership_and_unique_per_run(self):
        run = self.begin(); slots = self.explicit_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': slots})
        at = dt.datetime.fromisoformat(slots[0]).timestamp()
        self.batch.db.execute('UPDATE ops_calendar SET task_id=? WHERE at=?', ('synthetic-owner', at))
        before = [tuple(row) for row in self.batch.db.execute('SELECT run_id,at,schedule_at,task_id FROM ops_calendar ORDER BY at')]
        # Recreate the shipped legacy schema to exercise a genuine upgrade.
        with self.batch.store.transaction():
            self.batch.db.execute('ALTER TABLE ops_calendar RENAME TO calendar_fixture')
            self.batch.db.execute('CREATE TABLE ops_calendar(run_id TEXT NOT NULL, at REAL NOT NULL UNIQUE, schedule_at TEXT, task_id TEXT)')
            self.batch.db.execute('INSERT INTO ops_calendar SELECT run_id,at,schedule_at,task_id FROM calendar_fixture')
            self.batch.db.execute('DROP TABLE calendar_fixture')
        self.batch.close(); self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        after = [tuple(row) for row in self.batch.db.execute('SELECT run_id,at,schedule_at,task_id FROM ops_calendar ORDER BY at')]
        self.assertEqual(after, before)
        with self.assertRaises(sqlite3.IntegrityError):
            self.batch.db.execute('INSERT INTO ops_calendar(run_id,at) VALUES (?,?)', (run['id'], at))
        self.assertEqual(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE run_id=? AND at=?', (run['id'], at)).fetchone()[0], 'synthetic-owner')

    def test_overlap_cloud_cancel_releases_only_its_own_exact_tie(self):
        first = self.begin(); self.enable_overlap(first); grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': grid})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        second = self.begin()
        self.batch.dispatch('schedule', {'run_id': second['id'], 'explicit_slots': grid})
        root = self.batch.continuous.directory(second['id'])
        image = root / 'final' / 'synthetic.png'; image.write_bytes(b'SYNTHETIC TEST ONLY')
        item = {'tool': 'publish_content', 'payload': {'title': 'synthetic', 'content': 'never publish fixture',
                                                      'images': [str(image)], 'schedule_at': grid[0]}}
        content_fixture.register(self.batch.continuous, second, [item])
        self.batch.dispatch('review', {'run_id': second['id'], 'payloads': [item], 'evidence': 'SYNTHETIC TEST ONLY'})
        action = self.prepare_action(second, item)
        self.batch.dispatch('cancel', {'run_id': second['id'], 'task_id': action['operation_id']})
        self.assertEqual(self.batch.continuous.run(first['id'])['body']['schedule_slots'], grid)
        self.assertEqual(self.batch.continuous.run(second['id'])['body']['schedule_slots'], grid[1:])
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'cancelled')
        self.assertEqual(self.batch.continuous._reserved_kind(second['id'], self.data['cloud_schedule_overlap_exception']), 'regular_daily')

    def test_calendar_migration_failure_rolls_back_every_reservation(self):
        run = self.begin(); slots = self.explicit_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': slots})
        with self.batch.store.transaction():
            self.batch.db.execute('ALTER TABLE ops_calendar RENAME TO calendar_fixture')
            self.batch.db.execute('CREATE TABLE ops_calendar(run_id TEXT NOT NULL, at REAL NOT NULL UNIQUE, schedule_at TEXT, task_id TEXT)')
            self.batch.db.execute('INSERT INTO ops_calendar SELECT run_id,at,schedule_at,task_id FROM calendar_fixture')
            self.batch.db.execute('DROP TABLE calendar_fixture')
        before = [tuple(row) for row in self.batch.db.execute('SELECT * FROM ops_calendar ORDER BY at')]
        def deny_drop(code, name, _arg, _database, _trigger):
            return sqlite3.SQLITE_DENY if code == sqlite3.SQLITE_DROP_TABLE and name == 'ops_calendar' else sqlite3.SQLITE_OK
        self.batch.db.set_authorizer(deny_drop)
        try:
            with self.assertRaises(sqlite3.DatabaseError):
                self.batch.continuous._migrate_cloud_calendar()
        finally:
            self.batch.db.set_authorizer(None)
        self.assertEqual([tuple(row) for row in self.batch.db.execute('SELECT * FROM ops_calendar ORDER BY at')], before)
        self.assertIsNone(self.batch.db.execute("SELECT name FROM sqlite_master WHERE name='ops_calendar_cloud_v2'").fetchone())
        self.batch.continuous._migrate_cloud_calendar()
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], slots)

    def exact_cover_fixture(self, people=2, count=10, planned=10):
        profile = content_fixture.profile(self.base)
        material = self.base / 'cover-source.txt'; material.write_text('SYNTHETIC TEST ONLY')
        face = self.base / 'synthetic-face.png'; face.write_bytes(b'SYNTHETIC not a real face')
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        profile.update(product_materials=[{'file': str(material), 'sha256': sha(material), 'source_ref': 'SYNTHETIC'}],
                       cover_person_enabled=True,
                       face_reference={'file': str(face), 'sha256': sha(face), 'source_ref': 'SYNTHETIC'},
                       content_requirements={'post_cta': 'SYNTHETIC CTA', 'comment_offer_phrases': ['SYNTHETIC offer'],
                                             'comment_positioning_terms': ['synthetic'], 'min_person_covers': 2,
                                             'exact_person_covers': 2})
        for key in ('product_materials', 'cover_person_enabled', 'face_reference', 'content_requirements'):
            profile['confirmation_sources'][key] = 'SYNTHETIC TEST ONLY'
        self.assertEqual(self.batch.dispatch('configure', {'content_profile': profile})['content_profile_pending'], [])
        run = self.begin()
        self.batch.dispatch('update', {'run_id': run['id'], 'plan_ref': 'SYNTHETIC',
                                      'plan': {'posts': planned, 'comments': 1, 'comment_strategy': {'A': 1, 'B': 0, 'C': 0}}})
        grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': grid})
        items = []
        for n in range(count):
            asset = self.batch.continuous.directory(run['id']) / 'final' / ('synthetic-' + str(n) + '.png')
            asset.write_bytes(('SYNTHETIC cover ' + str(n)).encode())
            items.append({'content_id': 'fixture-cover-' + str(n), 'tool': 'publish_content',
                          'payload': {'title': 'SYNTHETIC ' + str(n), 'content': 'SYNTHETIC body. SYNTHETIC CTA',
                                      'images': [str(asset)], 'schedule_at': grid[n]}})
        def transform(item, fields, final):
            n = items.index(item); fields['cta'] = 'SYNTHETIC CTA'
            asset = Path(item['payload']['images'][0])
            final['cover_review'] = {'batch_id': 'synthetic-covers', 'batch_content_ids': [i['content_id'] for i in items],
                                    'kind': 'person' if n < people else 'illustrated', 'asset': str(asset),
                                    'asset_sha256': sha(asset), 'style': 'SYNTHETIC unique ' + str(n),
                                    'selection_reason': 'SYNTHETIC', 'observation': 'SYNTHETIC no visual assertion',
                                    'reference_file': str(face), 'reference_sha256': sha(face),
                                    'likeness_observation': 'SYNTHETIC no likeness assertion'}
        content_fixture.register(self.batch.continuous, run, items, transform=transform)
        return run, items, profile

    def test_cloud_exact_person_covers_accepts_two_in_complete_ten_at_review_and_prepare(self):
        run, items, _ = self.exact_cover_fixture()
        result = self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})
        self.assertEqual(result['reviewed_payloads'], 10)
        self.assertTrue(self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')['host_action_required'])
        self.assertEqual(self.actions.list_actions(), [])

    def test_cloud_exact_person_covers_rejects_three_despite_original_minimum(self):
        run, items, _ = self.exact_cover_fixture(people=3)
        with self.assertRaisesRegex(ValueError, 'ERR_PERSON_COVER_EXACT'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})

    def test_cloud_exact_person_covers_preserves_original_minimum(self):
        run, items, _ = self.exact_cover_fixture(people=1)
        with self.assertRaisesRegex(ValueError, 'ERR_PERSON_COVER_REQUIRED'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})

    def test_cloud_exact_person_covers_requires_whole_planned_batch_not_declared_subset(self):
        run, items, _ = self.exact_cover_fixture(count=2, planned=10)
        with self.assertRaisesRegex(ValueError, 'ERR_COVER_BATCH_INCOMPLETE'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})

    def test_cloud_exact_person_covers_does_not_block_comment_only_review(self):
        run, _, _ = self.exact_cover_fixture()
        item = {'tool': 'post_comment_to_feed', 'payload': {'feed_id': 'b' * 24, 'content': 'SYNTHETIC helpful fixture'}}
        content_fixture.register(self.batch.continuous, run, [item])
        result = self.batch.dispatch('review', {'run_id': run['id'], 'payloads': [item], 'evidence': 'SYNTHETIC'})
        self.assertEqual(result['reviewed_payloads'], 1)

    def test_cloud_exact_person_cover_count_cannot_be_satisfied_by_unreviewed_video_covers(self):
        run, _, profile = self.exact_cover_fixture()
        current = self.batch.continuous.run(run['id'])
        entry = {'kind': 'video', 'tool': 'publish_with_video', 'fingerprint': 'SYNTHETIC video'}
        with self.assertRaisesRegex(ValueError, 'ERR_COVER_BATCH_INCOMPLETE'):
            self.batch.continuous.content.strategy(current, profile, {'synthetic-video': entry})

    def test_cloud_exact_person_cover_count_needs_typed_evidenced_profile(self):
        _, _, profile = self.exact_cover_fixture()
        for exact in (True, 0, -1, 1, 101, '2'):
            modified = {**profile, 'content_requirements': {**profile['content_requirements'], 'exact_person_covers': exact}}
            self.assertIn('content_requirements', self.batch.continuous.content.profile_missing(modified))
        for minimum in (0, None, True, 3):
            modified = {**profile, 'content_requirements': {**profile['content_requirements'], 'min_person_covers': minimum}}
            self.assertIn('content_requirements', self.batch.continuous.content.profile_missing(modified))

    def test_account_change_and_incompatible_ledger_fail_closed(self):
        self.data['expected_user_id'] = 'c' * 24
        self.path.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'ERR_ACCOUNT_CHANGED'):
            self.batch.dispatch('status')
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            cloud_batch.CloudBatch(self.path)

    def test_linked_media_and_paths_rejected(self):
        source = self.base / 'actual.png'; source.write_bytes(b'fixture')
        linked = self.base / 'linked.png'; linked.symlink_to(source)
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_PATH'):
            cloud_batch.fingerprint('publish_content', {'images': [str(linked)]})
        other = self.base / 'other'; other.mkdir(mode=0o700)
        linked_dir = self.base / 'linked-state'; linked_dir.symlink_to(other, target_is_directory=True)
        self.data['state_dir'] = str(linked_dir); self.path.write_text(json.dumps(self.data))
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_PATH'):
            cloud_batch.CloudBatch(self.path)

    def test_inert_bounded_inputs_and_only_content_write_review(self):
        for data in ([], {'x': float('nan')}, {'cookies': 'forbidden'}):
            with self.assertRaises(ValueError):
                self.batch.dispatch('update', data)
        run = self.begin()
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_TOOL'):
            self.batch.prepare_payload(run['id'], 'like_feed', {'feed_id': 'b' * 24}, 'SYNTHETIC')

    def test_no_cdp_executor_import_or_claim_in_module(self):
        source = (ROOT / 'runtime/cloud_batch.py').read_text()
        for forbidden in ('import xhs_ops', 'import urllib', 'import requests', 'import subprocess', '.claim(', '.begin_submit(', '.report_result('):
            self.assertNotIn(forbidden, source)

    def test_lost_ledger_with_existing_batch_files_cannot_reset_state(self):
        self.begin()
        self.batch.close()
        database = self.base / 'state' / 'cloud-batches.sqlite3'
        saved = database.read_bytes()
        database.unlink()
        try:
            with self.assertRaisesRegex(ValueError, 'ERR_STATE_LOST'):
                cloud_batch.CloudBatch(self.path)
        finally:
            database.write_bytes(saved); database.chmod(0o600)
            self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])

    def prepare_action(self, run, item):
        prepared = self.batch.prepare_payload(run['id'], item['tool'], item['payload'], 'SYNTHETIC source')
        action = self.actions.prepare(item['tool'], prepared['payload'], expected_account=self.data['expected_user_id'],
                                      target={'fixture_target': item['tool']}, user_source='SYNTHETIC source',
                                      media_manifest=prepared['media_manifest'])
        self.batch.bind_action(run['id'], action['operation_id'])
        return action

    def report_success(self, action, outcome='host_reported_success'):
        """Exercise the local protocol with explicitly synthetic host reports."""
        self.data['allow_write'] = True; self.path.write_text(json.dumps(self.data))
        stamp = dt.datetime.fromtimestamp(self.now[0], dt.timezone.utc).isoformat()
        expires = dt.datetime.fromtimestamp(self.now[0] + 600, dt.timezone.utc).isoformat()
        ident = action['operation_id']
        self.actions.request_authorization(ident)
        self.actions.record_authorization(ident, {'operation_id': ident, 'binding_digest': action['binding_digest'],
            'expected_account': self.data['expected_user_id'], 'user_source': 'SYNTHETIC source',
            'user_quote': 'SYNTHETIC TEST ONLY: never publish', 'confirmed_at': stamp,
            'expires_at': expires, 'evidence_ref': 'SYNTHETIC TEST ONLY'})
        self.actions.observe_identity(ident, {'status': 'observed', 'account_id': self.data['expected_user_id'],
            'observed_at': stamp, 'evidence_ref': 'SYNTHETIC TEST ONLY', 'browser_context': 'synthetic-tab'})
        claim = self.actions.claim(ident, 'synthetic-worker', browser_context='synthetic-tab')
        pending = self.actions.begin_submit(ident, claim['claim_token'])
        self.actions.report_result(ident, claim['claim_token'], {'operation_id': ident, 'binding_digest': action['binding_digest'],
            'attempt_id': pending['attempt_id'], 'account_id': self.data['expected_user_id'], 'browser_context': 'synthetic-tab',
            'status': outcome, 'observed_at': stamp, 'evidence_ref': 'SYNTHETIC TEST ONLY',
            'evidence_kind': {'host_reported_success': 'visible_result', 'not_applied': 'platform_rejection', 'submitted_unknown': 'uncertain'}[outcome], 'details': 'SYNTHETIC TEST ONLY: no browser was operated'})

    def owner_voice_profile(self):
        profile = content_fixture.profile(self.base)
        profile['voice_samples'] = []
        profile['confirmation_sources'].pop('voice_samples')
        profile['voice_mode'] = {'mode': 'owner_instructions', 'calibrated': False,
                                 'persona_quote': 'SYNTHETIC owner persona instructions',
                                 'persona_source': 'SYNTHETIC owner message reference',
                                 'no_review_quote': '发布无需交给我审核。',
                                 'no_review_source': 'SYNTHETIC owner message reference'}
        return profile

    def test_owner_instruction_voice_is_explicitly_uncalibrated_without_fake_samples(self):
        profile = self.owner_voice_profile()
        result = self.batch.dispatch('configure', {'content_profile': profile})
        self.assertNotIn('voice_samples', result['content_profile_pending'])
        self.assertFalse(result['voice_calibrated'])
        status = self.batch.dispatch('status', {'include_profile': True})
        self.assertEqual(status['content_profile']['voice_samples'], [])
        self.assertEqual(status['voice_mode'], 'owner_instructions')
        self.assertFalse(status['voice_calibrated'])
        self.assertEqual(status['mode'], 'validation_required')
        self.assertIn('no automatic execution authority', status['voice_evidence'])
        # The original local-engine requirement has not been altered.
        original = cloud_batch.ContentReview(self.batch.continuous)
        self.assertIn('voice_samples', original.profile_missing(profile))

    def test_owner_instruction_voice_cannot_bypass_missing_quotes_or_claim_calibration(self):
        for change in ({'no_review_quote': ''}, {'no_review_quote': 'different instruction'},
                       {'no_review_source': ''}, {'persona_quote': ''}, {'persona_source': ''},
                       {'calibrated': True}, {'calibrated': 0}, {'mode': 'inferred'}):
            profile = self.owner_voice_profile(); profile['voice_mode'].update(change)
            with self.subTest(change=change):
                self.assertIn('voice_samples', self.batch.continuous.content.profile_missing(profile))
        profile = self.owner_voice_profile(); profile['voice_samples'] = [{'text': 'fabricated style'}]
        self.assertIn('voice_samples', self.batch.continuous.content.profile_missing(profile))

    def test_owner_instruction_voice_preserves_every_other_profile_requirement(self):
        profile = self.owner_voice_profile(); profile['persona'] = ''; profile['verified_selling_points'] = []
        missing = self.batch.continuous.content.profile_missing(profile)
        self.assertNotIn('voice_samples', missing)
        self.assertIn('persona', missing); self.assertIn('verified_selling_points', missing)

    def replacement_comment(self, run, original):
        replacement = {'tool': original['tool'], 'content_id': original['content_id'],
                       'payload': {**original['payload'], 'content': 'Different SYNTHETIC replacement; never publish'}}
        content_fixture.register(self.batch.continuous, run, [replacement])
        return replacement

    def test_projection_status_exposes_stale_receipt_without_writing(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1])
        self.report_success(action, outcome='not_applied')
        self.batch.db.execute("UPDATE tasks SET status='submitted_unknown',result=NULL WHERE id=?", (action['operation_id'],))
        self.batch.db.execute("UPDATE cloud_batch_bindings SET host_status='submitted_unknown' WHERE operation_id=?", (action['operation_id'],))
        before = self.batch.db.total_changes
        events_before = self.actions.events(action['operation_id'])
        report = self.batch.projection_status(run['id'])
        self.assertEqual(report['mismatch_count'], 1)
        self.assertFalse(report['state_changed'])
        entry = report['entries'][0]
        self.assertEqual(entry['ledger_status'], 'not_applied')
        self.assertEqual(entry['stored_task_status'], 'submitted_unknown')
        self.assertEqual(entry['expected_task_status'], 'failed')
        self.assertEqual(self.batch.db.total_changes, before)
        self.assertEqual(self.actions.events(action['operation_id']), events_before)
        self.batch.bind_action(run['id'], action['operation_id'])
        self.assertEqual(self.batch.projection_status(run['id'])['mismatch_count'], 0)
        self.assertEqual(self.actions.events(action['operation_id']), events_before)

    def test_projection_status_does_not_hide_missing_binding(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1])
        self.batch.db.execute('DELETE FROM cloud_batch_bindings WHERE operation_id=?', (action['operation_id'],))
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.batch.projection_status(run['id'])

    def test_projection_status_rejects_changed_binding_and_preserves_unknown(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1])
        self.report_success(action, outcome='submitted_unknown')
        report = self.batch.projection_status(run['id'])
        self.assertEqual(report['entries'][0]['ledger_status'], 'submitted_unknown')
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'submitted_unknown')
        self.batch.db.execute("UPDATE cloud_batch_bindings SET binding_digest='changed' WHERE operation_id=?", (action['operation_id'],))
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.batch.projection_status(run['id'])

    def test_definite_not_applied_refresh_frees_quota_but_preserves_raw_receipt(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1])
        self.report_success(action, outcome='not_applied')
        replacement = self.replacement_comment(run, items[1])
        self.batch.dispatch('review', {'run_id': run['id'], 'payloads': [items[0], replacement], 'evidence': 'SYNTHETIC new local review'})
        row = self.batch.db.execute('SELECT t.status,b.host_status,t.result FROM tasks t JOIN cloud_batch_bindings b ON b.operation_id=t.id WHERE t.id=?', (action['operation_id'],)).fetchone()
        self.assertEqual(row['status'], 'failed'); self.assertEqual(row['host_status'], 'not_applied')
        self.assertEqual(json.loads(row['result'])['status'], 'not_applied')
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'not_applied')
        self.batch.prepare_payload(run['id'], replacement['tool'], replacement['payload'], 'SYNTHETIC exact new source')

    def test_success_projection_still_consumes_original_batch_quota(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1]); self.report_success(action)
        replacement = self.replacement_comment(run, items[1])
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_PLAN_SCOPE'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': [items[0], replacement], 'evidence': 'SYNTHETIC'})
        self.assertEqual(self.batch.db.execute('SELECT status FROM tasks WHERE id=?', (action['operation_id'],)).fetchone()[0], 'host_reported_success')

    def test_unknown_projection_still_consumes_original_batch_quota(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[1]); self.report_success(action, outcome='submitted_unknown')
        replacement = self.replacement_comment(run, items[1])
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_PLAN_SCOPE'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': [items[0], replacement], 'evidence': 'SYNTHETIC'})
        self.assertEqual(self.batch.db.execute('SELECT status FROM tasks WHERE id=?', (action['operation_id'],)).fetchone()[0], 'submitted_unknown')

    def test_not_applied_reservation_is_released_only_by_explicit_local_replacement(self):
        run, items = self.reviewed(); action = self.prepare_action(run, items[0]); self.report_success(action, outcome='not_applied')
        slots = self.batch.continuous.run(run['id'])['body']['schedule_slots']
        self.assertEqual(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE schedule_at=?', (slots[0],)).fetchone()[0], action['operation_id'])
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': slots, 'replace_unsubmitted': True})
        self.assertIsNone(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE schedule_at=?', (slots[0],)).fetchone()[0])
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'not_applied')

    def test_cancel_uses_exact_unclaimed_action_and_releases_only_its_slot(self):
        run, items = self.reviewed()
        action = self.prepare_action(run, items[0])
        result = self.batch.dispatch('cancel', {'run_id': run['id'], 'task_id': action['operation_id']})
        self.assertEqual(result['status'], 'cancelled')
        self.assertFalse(result['platform_cancellation_performed'])
        self.assertEqual(self.actions.get(action['operation_id'])['status'], 'cancelled')
        self.assertIsNone(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE task_id=?', (action['operation_id'],)).fetchone())
        self.assertTrue(Path(items[0]['payload']['images'][0]).exists())

    def test_finish_and_bound_acceptance_remain_host_reports_not_autonomous_authority(self):
        run, items = self.reviewed()
        for item in items:
            self.report_success(self.prepare_action(run, item))
        finish = self.batch.dispatch('finish', {'run_id': run['id']})
        self.assertEqual(finish['status'], 'host_reported_complete')
        self.assertEqual(finish['state'], 'awaiting_acceptance')
        self.assertEqual(finish['outcomes']['host_reported_success'], 2)
        self.assertFalse(finish['platform_success_verified'])
        report = {'run_id': run['id'], 'account_id': self.data['expected_user_id'],
                  'completion_digest': finish['completion_digest'], 'user_source': 'SYNTHETIC owner source',
                  'user_quote': 'SYNTHETIC TEST ONLY: no actual user acceptance',
                  'accepted_at': dt.datetime.fromtimestamp(self.now[0], dt.timezone.utc).isoformat(),
                  'evidence_ref': 'SYNTHETIC TEST ONLY'}
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.batch.dispatch('accept', {'run_id': run['id'], 'human_acceptance_ref': 'SYNTHETIC',
                                          'host_acceptance': {**report, 'completion_digest': 'wrong'}})
        result = self.batch.dispatch('accept', {'run_id': run['id'], 'human_acceptance_ref': 'SYNTHETIC', 'host_acceptance': report})
        self.assertEqual(result['mode'], 'host_accepted')
        self.assertFalse(result['automatic_authority_granted'])
        self.assertTrue(result['host_must_check_each_action_authority'])
        self.assertEqual(self.begin()['body']['kind'], 'host_reviewed')
        self.assertTrue(Path(items[0]['payload']['images'][0]).exists())

    def test_finish_unknown_does_not_cleanup_or_complete(self):
        run, items = self.reviewed()
        action = self.prepare_action(run, items[0])
        with mock.patch.object(self.actions, 'get', return_value={**action, 'status': 'submitted_unknown'}):
            with self.assertRaisesRegex(ValueError, 'ERR_RUN_UNRESOLVED'):
                self.batch.dispatch('finish', {'run_id': run['id']})
        self.assertEqual(self.batch.continuous.run(run['id'])['state'], 'active')
        self.assertTrue(Path(items[0]['payload']['images'][0]).exists())

    def test_repair_checks_actual_hashes_without_running_code_or_activating(self):
        report = {'version': (ROOT/'VERSION').read_text().strip(), 'status': 'passed',
                  'records': [{'exit_code': 0, 'description': 'SYNTHETIC host-reported check'}],
                  'runtime_files_sha256': {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in (ROOT/'runtime').glob('*.py')}}
        path = self.base/'verification.json'; path.write_text(json.dumps(report))
        data = {k: 'SYNTHETIC fixture' for k in ('failure', 'change', 'verification', 'before_version', 'rollback_ref', 'skill_update')}
        data.update(after_version=report['version'], verification_report=str(path))
        result = self.batch.dispatch('repair', data)
        self.assertTrue(result['local_hashes_checked']); self.assertFalse(result['production_activated'])
        report['runtime_files_sha256']['cloud_batch.py'] = 'wrong'; path.write_text(json.dumps(report))
        with self.assertRaisesRegex(ValueError, 'ERR_REPAIR_VERIFICATION_REQUIRED'):
            self.batch.dispatch('repair', data)

    def test_replaced_sealed_directory_and_live_storage_are_rejected(self):
        run, items = self.reviewed()
        sealed = self.batch.continuous.directory(run['id'])/'sealed'
        sealed.rmdir(); outside = self.base/'outside'; outside.mkdir(mode=0o700); sealed.symlink_to(outside, target_is_directory=True)
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_PATH'):
            self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')
        self.assertEqual(list(outside.iterdir()), [])
        sealed.unlink(); sealed.mkdir(mode=0o700)
        lock = self.base/'state'/'operation-runs.lock'; lock.unlink(); lock.symlink_to(self.base/'outside-lock')
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_PATH'):
            self.batch.dispatch('status')

    def test_interrupted_seal_does_not_poison_final_hash_path(self):
        run, items = self.reviewed()
        sealed = self.batch.continuous.directory(run['id'])/'sealed'
        with mock.patch('cloud_batch.os.link', side_effect=OSError('SYNTHETIC interrupted commit')):
            with self.assertRaisesRegex(OSError, 'interrupted commit'):
                self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')
        self.assertEqual(list(sealed.iterdir()), [])
        retried = self.batch.prepare_payload(run['id'], items[0]['tool'], items[0]['payload'], 'SYNTHETIC')
        self.assertEqual(Path(retried['payload']['images'][0]).read_bytes(), Path(items[0]['payload']['images'][0]).read_bytes())
        self.assertEqual(len(list(sealed.iterdir())), 1)


if __name__ == '__main__':
    unittest.main()
