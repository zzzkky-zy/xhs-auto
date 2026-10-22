"""Synthetic local settings revisions; never real review or publication evidence."""
import copy
import datetime as dt
import json
from pathlib import Path
import unittest
from unittest import mock

import test_cloud_batch as base
import test_future_content_policy as future
from cloud_batch import _CloudContentReview


class BatchSettingsRevisionTests(unittest.TestCase):
    for _name in ('setUp', 'tearDown', 'prepare_action'):
        locals()[_name] = getattr(base.CloudBatchTests, _name)
    for _name in ('future_profile', 'future_begin', 'copy_fixture', 'future_items', 'schedule_evidence'):
        locals()[_name] = getattr(future.FutureContentPolicyTests, _name)

    settings = {'is_original': False, 'group_chat': 'SYNTHETIC observed group',
                'activity': 'SYNTHETIC observed activity'}
    evidence = {'user_source': 'SYNTHETIC exact user message',
                'user_quote': 'SYNTHETIC change original, group and activity for current and future posts.',
                'evidence_ref': 'SYNTHETIC user message and actual observed labels'}

    def stamp(self, delta=0):
        return dt.datetime.fromtimestamp(self.now[0] + delta, dt.timezone.utc).isoformat()

    def finals(self, run, items, slots, settings=None):
        for index, item in enumerate(items):
            old = self.batch.continuous.content.chain(run['id'], item['content_id'])[-1]
            final = copy.deepcopy(old['body']['value'])
            item['payload'].update(schedule_at=slots[index], **(settings or {}))
            final['payload'] = copy.deepcopy(item['payload'])
            path = self.batch.continuous.directory(run['id']) / 'work' / (
                'revision-final-%s-%s.json' % (item['content_id'], old['seq']))
            path.write_text(json.dumps(final))
            self.batch.dispatch('update', {'run_id': run['id'], 'content_record': {
                'id': item['content_id'], 'stage': 'final', 'artifact': str(path)}})

    def started(self):
        self.now[0] = dt.datetime.fromisoformat('2026-10-21T08:00:00+08:00').timestamp()
        profile, run, items = self.future_items()
        slots = self.batch.dispatch('schedule', {'run_id': run['id'],
            'future_schedule': self.schedule_evidence('2026-10-21T09:00:00+08:00')})['schedule_slots']
        self.finals(run, items, slots)
        self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC six-post review'})
        action = self.prepare_action(run, items[0])
        operation_id = action['operation_id']
        self.data['allow_write'] = True
        self.path.write_text(json.dumps(self.data))
        self.actions.request_authorization(operation_id)
        self.actions.record_authorization(operation_id, {
            'operation_id': operation_id, 'binding_digest': action['binding_digest'],
            'expected_account': self.data['expected_user_id'], 'user_source': 'SYNTHETIC source',
            'user_quote': 'SYNTHETIC original test permission', 'confirmed_at': self.stamp(),
            'expires_at': self.stamp(3600), 'evidence_ref': 'SYNTHETIC original evidence'})
        self.actions.observe_identity(operation_id, {'status': 'observed',
            'account_id': self.data['expected_user_id'], 'observed_at': self.stamp(),
            'evidence_ref': 'SYNTHETIC visible account', 'browser_context': 'synthetic-retained-editor'})
        claim = self.actions.claim(operation_id, 'synthetic-worker', 600, 'synthetic-retained-editor')
        self.actions.begin_submit(operation_id, claim['claim_token'])
        return profile, run, items, self.actions.get(operation_id), claim

    def revised_review(self, run, items):
        revision = self.batch.revise_publication_settings(run['id'], self.settings, **self.evidence)
        slots = self.batch.dispatch('schedule', {'run_id': run['id'], 'replace_unsubmitted': True,
            'future_schedule': self.schedule_evidence('2026-10-21T11:00:00+08:00')})['schedule_slots']
        self.finals(run, items, slots, self.settings)
        review = self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items,
            'evidence': 'SYNTHETIC same manuscripts, settings and exact dates reviewed'})
        return revision, review

    @mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', return_value={'allowed': True})
    def test_whole_six_stage_chain_review_and_action_sync_preserve_originals(self, _verify):
        profile, run, items, old_action, claim = self.started()
        original_stages = [dict(row) for row in self.batch.db.execute('SELECT * FROM ops_content_stages ORDER BY seq')]
        old_run = self.batch.continuous.run(run['id'])
        raw_action = dict(self.actions.db.execute('SELECT * FROM actions').fetchone())
        revision, review = self.revised_review(run, items)
        self.assertEqual(review['reviewed_payloads'], 6)
        self.assertEqual(self.actions.get(old_action['operation_id']), old_action)
        self.assertEqual([dict(self.batch.db.execute('SELECT * FROM ops_content_stages WHERE seq=?', (r['seq'],)).fetchone())
                          for r in original_stages], original_stages)
        current_run = self.batch.continuous.run(run['id'])
        saved = current_run['body']['publication_settings_revisions'][0]
        self.assertEqual(saved['previous_profile'], profile)
        self.assertEqual(saved['previous_review'], old_run['body']['review'])
        self.assertEqual(saved['evidence'], self.evidence)
        self.assertEqual(len(saved['previous_calendar']), 6)
        self.assertEqual(self.batch.db.execute('SELECT status FROM tasks').fetchone()[0], 'submitted_unknown')
        self.assertEqual(len(current_run['body']['schedule_slots']), 6)
        prepared = self.batch.prepare_payload(run['id'], 'publish_content', items[0]['payload'], self.evidence['user_source'])
        preview = self.actions.preview_unsubmitted_publication_settings(
            old_action['operation_id'], prepared['payload'], self.evidence['user_source'])
        attestation = {'operation_id': old_action['operation_id'], 'binding_digest': preview['binding_digest'],
            'expected_account': self.data['expected_user_id'], **self.evidence,
            'confirmed_at': self.stamp(), 'expires_at': self.stamp(3600)}
        observation = {'expected_account': self.data['expected_user_id'], 'browser_context': 'synthetic-retained-editor',
            'call_ended': True, 'final_submit_invoked': False, 'editor_retained': True,
            'visible_title': prepared['payload']['title'], 'visible_body': prepared['payload']['content'],
            'uploaded_media_match': True, 'media_evidence_ref': 'SYNTHETIC retained media',
            'observed_at': self.stamp(), 'evidence_ref': 'SYNTHETIC retained same editor'}
        changed = self.actions.revise_unsubmitted_publication_settings(
            old_action['operation_id'], claim['claim_token'], prepared['payload'], attestation, observation)
        # Simulate interruption after ledger commit, before local projection.
        with self.assertRaisesRegex(ValueError, 'original action binding differs'):
            self.batch._linked_action(run['id'], changed['operation_id'])
        self.assertFalse(self.batch.sync_publication_settings_revision(changed)['duplicate'])
        self.assertTrue(self.batch.sync_publication_settings_revision(changed)['duplicate'])
        self.assertEqual(self.batch._linked_action(run['id'], changed['operation_id']), changed)
        self.assertEqual(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE schedule_at=?',
            (prepared['payload']['schedule_at'],)).fetchone()[0], changed['operation_id'])
        self.assertEqual(dict(self.actions.db.execute('SELECT * FROM actions').fetchone())['binding_json'],
                         raw_action['binding_json'])
        self.assertEqual(changed['attempt_id'], old_action['attempt_id'])
        self.assertEqual(changed['lease_until'], old_action['lease_until'])
        self.assertEqual(self.batch.revise_publication_settings(run['id'], self.settings, **self.evidence)['revision_id'],
                         revision['revision_id'])

    @mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', return_value={'allowed': True})
    def test_final_permit_or_any_result_blocks_calendar_release(self, _verify):
        _, run, _, action, _ = self.started()
        before = self.batch.continuous.run(run['id'])
        self.actions._event(action['operation_id'], 'publication_final_step_permitted', {'step_id': 'SYNTHETIC'})
        with self.assertRaisesRegex(ValueError, 'permit already issued'):
            self.batch.revise_publication_settings(run['id'], self.settings, **self.evidence)
        self.assertEqual(self.batch.continuous.run(run['id']), before)
        self.assertEqual(self.batch.db.execute('SELECT task_id FROM ops_calendar WHERE task_id IS NOT NULL').fetchone()[0],
                         action['operation_id'])
        with mock.patch.object(self.actions, 'get', return_value={**action, 'result': {'status': 'submitted_unknown'}}):
            with self.assertRaisesRegex(ValueError, 'unreported'):
                self.batch.revise_publication_settings(run['id'], self.settings, **self.evidence)

    @mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', return_value={'allowed': True})
    def test_changed_manuscript_is_still_a_seventh_post(self, _verify):
        _, run, items, _, _ = self.started()
        self.revised_review(run, items)
        chain = self.batch.continuous.content.chain(run['id'], items[0]['content_id'])
        edited, final = copy.deepcopy(chain[-2]['body']['value']), copy.deepcopy(chain[-1]['body']['value'])
        items[0]['payload']['title'] = 'SYNTHETIC different manuscript'
        edited['fields']['title'] = final['fields']['title'] = items[0]['payload']['title']
        final['payload'] = copy.deepcopy(items[0]['payload'])
        final['learning_review']['title']['text'] = items[0]['payload']['title']
        for stage, value in [('edited', edited), ('final', final)]:
            path = self.batch.continuous.directory(run['id']) / 'work' / ('changed-' + stage + '.json')
            path.write_text(json.dumps(value))
            self.batch.dispatch('update', {'run_id': run['id'], 'content_record': {
                'id': items[0]['content_id'], 'stage': stage, 'artifact': str(path)}})
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_PLAN_SCOPE'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})
        self.assertEqual(self.batch.db.execute('SELECT status FROM tasks').fetchone()[0], 'submitted_unknown')

    def test_future_v2_exact_fields_and_legacy_profile_untouched(self):
        old = self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC old-ten',
            'plan': {'posts': 10, 'comments': 50}})
        self.batch.db.execute("UPDATE ops_runs SET state='completed' WHERE id=?", (old['id'],))
        _, run = self.future_begin()
        old_before = self.batch.continuous.run(old['id'])
        self.batch.revise_publication_settings(run['id'], self.settings, **self.evidence)
        self.assertEqual(self.batch.continuous.run(old['id']), old_before)
        _, profile, _ = self.batch.continuous.content.profile()
        self.assertEqual(profile['future_content_policy']['version'], 2)
        final, payload = self.copy_fixture()
        payload.update(self.settings)
        _CloudContentReview.future_copy(profile, 'post', final, payload)
        for changes in ({'is_original': True}, {'is_original': 0}, {'group_chat': 'wrong'},
                        {'activity': ''}, {'group_chat': None}):
            with self.subTest(changes=changes), self.assertRaisesRegex(ValueError, 'ERR_FUTURE_PUBLISH_FIELDS'):
                _CloudContentReview.future_copy(profile, 'post', final, {**payload, **changes})
        self.batch.db.execute("UPDATE ops_runs SET state='completed' WHERE id=?", (run['id'],))
        following = self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC following', 'plan': run['body']['plan']})
        self.assertEqual(self.batch.continuous.content.profile_for_run(following)[1], profile)


if __name__ == '__main__':
    unittest.main()
