"""Synthetic offline evidence only; never real editorial review or permission."""
import copy
import datetime as dt
from zoneinfo import ZoneInfo
import hashlib
import json
from pathlib import Path
import unittest
from unittest import mock

import content_fixture
import test_cloud_batch as existing
from cloud_batch import FUTURE_POST_CTA, _CloudContentReview


class FutureContentPolicyTests(unittest.TestCase):
    setUp = existing.CloudBatchTests.setUp
    tearDown = existing.CloudBatchTests.tearDown
    begin = existing.CloudBatchTests.begin
    reviewed = existing.CloudBatchTests.reviewed
    prepare_action = existing.CloudBatchTests.prepare_action
    report_success = existing.CloudBatchTests.report_success
    exact_cover_fixture = existing.CloudBatchTests.exact_cover_fixture
    daily_slots = existing.CloudBatchTests.daily_slots
    enable_overlap = existing.CloudBatchTests.enable_overlap

    def future_profile(self, base=None):
        p = copy.deepcopy(base or content_fixture.profile(self.base))
        asset = self.base / 'future-material.txt'
        asset.write_text('SYNTHETIC offline material and face fixture only')
        sha = hashlib.sha256(asset.read_bytes()).hexdigest()
        p.update(product_materials=[{'file': str(asset), 'sha256': sha, 'source_ref': 'SYNTHETIC'}],
                 cover_person_enabled=True,
                 face_reference={'file': str(asset), 'sha256': sha, 'source_ref': 'SYNTHETIC'},
                 content_requirements={'post_cta': FUTURE_POST_CTA, 'comment_offer_phrases': ['offer'],
                                       'comment_positioning_terms': ['synthetic'],
                                       'min_person_covers': 3, 'exact_person_covers': 3},
                 future_content_policy={'version': 1, 'posts': 6, 'comments': 50, 'exact_person_covers': 3,
                                        'post_cta': FUTURE_POST_CTA, 'topic_template': 'answereq',
                                        'is_original': True, 'optional_content_declaration': 'none'})
        for key in ('product_materials', 'cover_person_enabled', 'face_reference', 'content_requirements', 'future_content_policy'):
            p['confirmation_sources'][key] = 'SYNTHETIC provenance, not user permission'
        return p

    def future_begin(self):
        p = self.future_profile()
        self.batch.dispatch('configure', {'content_profile': p})
        run = self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC', 'plan': {
            'posts': 6, 'comments': 50, 'comment_strategy': {'A': 25, 'B': 25, 'C': 0}}})
        return p, run

    def copy_fixture(self):
        parts = ['SYNTHETIC problem: introduce a data project to an interviewer.',
                 'SYNTHETIC steps: identify the problem, describe your action, then quantify the result.',
                 'SYNTHETIC result: the example reduces review time from ten hours to five hours.',
                 'SYNTHETIC template: I am [role], I solved [problem] using [action], producing [result].',
                 'SYNTHETIC filled: I am an analyst, I automated reporting with SQL, saving five hours.']
        explanation = 'SYNTHETIC detailed offline judgement only; this is not an actual human review.'
        def record(value):
            return {'excerpt': value, 'explanation': explanation}
        fields = {'title': 'SYNTHETIC why did this answer fail?', 'cover_text': 'SYNTHETIC contrast two answers',
                  'cta': FUTURE_POST_CTA}
        fields['content'] = '\n'.join(parts) + '\n\n' + FUTURE_POST_CTA
        review = {'reviewer': 'SYNTHETIC test', 'review_source': 'SYNTHETIC offline fixture',
                  'content_type': 'educational', 'worked_example': {
                      'problem': record(parts[0]), 'steps': record(parts[1]), 'result': record(parts[2]),
                      'completeness_check': explanation},
                  'copyable': {**record(parts[3]), 'kind': 'template', 'usage_instructions': explanation},
                  'self_introduction': {'fill_in_blank': record(parts[3]), 'filled_example': record(parts[4]),
                                        'substitution_check': explanation}}
        for field in ('title', 'cover_text'):
            review[field] = {'text': fields[field], 'mechanism': 'question',
                             'mechanism_explanation': explanation, 'promise': explanation,
                             'payoff': record(parts[1])}
        final = {'fields': fields, 'learning_review': review}
        return final, {'title': fields['title'], 'content': fields['content'],
                       'topic_template': 'answereq', 'is_original': True}

    def test_legacy_ten_two_review_survives_future_profile_and_reopen(self):
        run, items, old_profile = self.exact_cover_fixture()
        self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})
        old_review = self.batch.continuous.run(run['id'])['body']['review']
        # Simulate an existing database created before pinning was introduced.
        old_run = self.batch.continuous.run(run['id'])
        old_run['body'].pop('content_profile_version')
        self.batch.continuous.save(old_run)
        self.batch.dispatch('configure', {'content_profile': self.future_profile(old_profile)})
        preserved = self.batch.continuous.run(run['id'])
        self.assertEqual(preserved['body']['review'], old_review)
        self.assertEqual(self.batch.continuous.content.profile_for_run(preserved)[1], old_profile)
        self.batch.continuous.content.authorize(preserved, items[0]['tool'], items[0]['payload'])
        self.batch.close()
        self.batch = existing.cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.batch.continuous.content.authorize(self.batch.continuous.run(run['id']), items[0]['tool'], items[0]['payload'])

    def test_new_future_run_pinned_and_plan_enforced_without_schedule(self):
        p, run = self.future_begin()
        self.assertEqual(run['body']['plan']['posts'], 6)
        self.assertEqual(run['body']['schedule_slots'], [])
        self.assertEqual(self.batch.continuous.content.profile_for_run(run)[1], p)
        with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_BATCH_PLAN'):
            self.batch.dispatch('update', {'run_id': run['id'], 'plan_ref': 'SYNTHETIC', 'plan': {'posts': 10, 'comments': 50}})
        changed = copy.deepcopy(p); changed['persona'] = 'different future author'
        self.batch.dispatch('configure', {'content_profile': changed})
        self.assertEqual(self.batch.continuous.content.profile_for_run(run)[1], p)

    def test_subsequent_run_gets_new_profile_and_existing_pin_cannot_be_swapped(self):
        p, run = self.future_begin()
        changed = copy.deepcopy(p); changed['persona'] = 'SYNTHETIC next-run persona'
        self.batch.dispatch('configure', {'content_profile': changed})
        self.batch.dispatch('update', {'run_id': run['id'],
            'content_profile_version': self.batch.continuous.policy()['content_profile_version'],
            'item': {'id': 'synthetic', 'stage': 'research'}})
        self.assertEqual(self.batch.continuous.content.profile_for_run(self.batch.continuous.run(run['id']))[1], p)
        # Test-only lifecycle fixture, no real task or publication exists.
        self.batch.db.execute("UPDATE ops_runs SET state='completed' WHERE id=?", (run['id'],))
        following = self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC next', 'plan': run['body']['plan']})
        self.assertEqual(self.batch.continuous.content.profile_for_run(following)[1], changed)

    def test_missing_pinned_profile_fails_closed(self):
        _, run = self.future_begin()
        self.batch.db.execute('DELETE FROM ops_profile_versions WHERE version=?', (run['body']['content_profile_version'],))
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED'):
            self.batch.continuous.content.profile_for_run(run)

    def test_bad_new_plan_and_unconfirmed_policy_rejected(self):
        p = self.future_profile()
        self.batch.dispatch('configure', {'content_profile': p})
        with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_BATCH_PLAN'):
            self.begin()
        del p['confirmation_sources']['future_content_policy']
        self.assertIn('content_requirements', self.batch.continuous.content.profile_missing(p))
        with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_CONTENT_POLICY'):
            _CloudContentReview.future_policy(p)

    def test_educational_body_can_omit_product_and_records_remain_inspectable(self):
        final, payload = self.copy_fixture()
        self.assertNotIn('Answereq', payload['content'][:-len(FUTURE_POST_CTA)])
        _CloudContentReview.future_copy(self.future_profile(), 'post', final, payload)

    def test_native_publish_fields_required_before_upload(self):
        final, payload = self.copy_fixture()
        for change in ({'topic_template': 'wrong'}, {'is_original': False}, {'is_original': 1}):
            with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_PUBLISH_FIELDS'):
                _CloudContentReview.future_copy(self.future_profile(), 'post', final, {**payload, **change})

    def test_cta_must_be_exact_and_last(self):
        final, payload = self.copy_fixture()
        for body in (payload['content'] + 'after', payload['content'].replace('⬇️', '↓')):
            with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_CTA'):
                _CloudContentReview.future_copy(self.future_profile(), 'post', final, {**payload, 'content': body})

    def test_generic_hook_or_absent_payoff_rejected(self):
        for modification in ({'mechanism_explanation': 'pass'}, {'payoff': {'excerpt': 'not present', 'explanation': 'x'*40}}, {'text': 'different title'}):
            final, payload = self.copy_fixture()
            final['learning_review']['title'].update(modification)
            with self.assertRaisesRegex(ValueError, 'ERR_HOOK_REVIEW'):
                _CloudContentReview.future_copy(self.future_profile(), 'post', final, payload)

    def test_incomplete_example_and_noncopyable_claim_rejected(self):
        for field, error in (('worked_example', 'ERR_WORKED_EXAMPLE'), ('copyable', 'ERR_COPYABLE')):
            final, payload = self.copy_fixture()
            final['learning_review'][field] = {'result': 'pass'}
            with self.assertRaisesRegex(ValueError, error):
                _CloudContentReview.future_copy(self.future_profile(), 'post', final, payload)

    def test_self_intro_requires_both_template_and_filled_example(self):
        final, payload = self.copy_fixture()
        final['learning_review']['content_type'] = 'self_introduction'
        _CloudContentReview.future_copy(self.future_profile(), 'post', final, payload)
        del final['learning_review']['self_introduction']['filled_example']
        with self.assertRaisesRegex(ValueError, 'ERR_SELF_INTRODUCTION'):
            _CloudContentReview.future_copy(self.future_profile(), 'post', final, payload)

    def future_items(self, people=3, real_cover_reviews=False):
        profile, run = self.future_begin()
        items = []
        for index in range(6):
            image = self.batch.continuous.directory(run['id']) / 'final' / ('future-%d.png' % index)
            if real_cover_reviews:
                from PIL import Image
                image_data=Image.new('RGB',(32,32),(index*30,20,180-index*20))
                image_data.putpixel((index+1,index+1),(255,0,0));image_data.save(image)
            else:image.write_bytes(('SYNTHETIC image bytes %d' % index).encode())
            _, payload = self.copy_fixture()
            payload.update(title='SYNTHETIC question %d?' % index, images=[str(image)])
            items.append({'content_id': 'future-%d' % index, 'tool': 'publish_content', 'payload': payload})
        review_ids={}
        if real_cover_reviews:
            from cloud_cover_history import CloudCoverHistory,DIMENSIONS
            history=CloudCoverHistory(self.actions);reference='sha256:'+profile['face_reference']['sha256']
            for index in range(people):
                image=items[index]['payload']['images'][0]
                peers=[items[n]['payload']['images'][0] for n in range(people) if n!=index]
                assessment=history.assess(image,reference_person=reference,peer_image_paths=peers)
                visual={'visual_reviewed':True,'reviewer':'SYNTHETIC test','evidence_ref':'SYNTHETIC pixel fixture',
                  'candidate_pixel_sha256':assessment['candidate']['pixel_sha256'],'history_digest':assessment['history_digest'],
                  'reference_person':reference,'same_reference_person':True,
                  'reference_comparison':{key:'SYNTHETIC difference '+key for key in DIMENSIONS},
                  'comparisons':[dict(entry_id=e['entry_id'],changed_dimensions=['composition'],near_match_override=True,
                   near_match_explanation='SYNTHETIC distinct color fixture comparison',**{key:'SYNTHETIC difference '+key for key in DIMENSIONS}) for e in assessment['comparisons']]}
                review_ids[index]=history.record_review(image,reference_person=reference,visual_review=visual,peer_image_paths=peers)['review_id']
        def transform(item, fields, final):
            index = items.index(item)
            fixture, _ = self.copy_fixture()
            fields.update(cta=FUTURE_POST_CTA, cover_text=fixture['fields']['cover_text'])
            final['learning_review'] = fixture['learning_review']
            final['learning_review']['title']['text'] = fields['title']
            asset = item['payload']['images'][0]
            face = profile['face_reference']
            final['cover_review'] = {
                'batch_id': 'SYNTHETIC-six', 'batch_content_ids': [i['content_id'] for i in items],
                'kind': 'person' if index < people else 'illustrated', 'asset': asset,
                'asset_sha256': hashlib.sha256(Path(asset).read_bytes()).hexdigest(),
                'style': 'SYNTHETIC-style-%d' % index, 'selection_reason': 'SYNTHETIC',
                'observation': 'SYNTHETIC no real visual review', 'reference_file': face['file'],
                'reference_sha256': face['sha256'], 'likeness_observation': 'SYNTHETIC',
                'visual_history_review_id': review_ids.get(index,'SYNTHETIC-review-%d' % index)}
        content_fixture.register(self.batch.continuous, run, items, transform=transform)
        return profile, self.batch.continuous.run(run['id']), items

    def test_six_three_batch_binds_history_peers_and_rechecks_before_authorize(self):
        profile, run, items = self.future_items()
        with mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', return_value={'allowed': True}) as verify:
            review = self.batch.continuous.content.review(run, items, 'SYNTHETIC offline cross-item review')
            self.assertEqual(len(review['entries']), 6)
            self.assertEqual(verify.call_count, 3)
            for call in verify.call_args_list:
                self.assertEqual(len(call.kwargs['peer_image_paths']), 2)
                self.assertNotIn(call.args[1], call.kwargs['peer_image_paths'])
                self.assertEqual(call.kwargs['reference_person'], 'sha256:' + profile['face_reference']['sha256'])
            run['body']['review'] = review
            with mock.patch('cloud_cover_history.CloudCoverHistory.verify_review',
                            return_value={'allowed': False, 'reasons': ['SYNTHETIC changed history']}):
                with self.assertRaisesRegex(ValueError, 'ERR_COVER_HISTORY_REQUIRED'):
                    self.batch.continuous.content.authorize(run, items[0]['tool'], items[0]['payload'])

    def test_submitted_covers_remain_counted_without_retroactive_history_failure(self):
        _, run, items = self.future_items()
        with mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', return_value={'allowed': True}):
            review = self.batch.continuous.content.review(run, items, 'SYNTHETIC')
        for index, entry in enumerate(review['entries'].values()):
            if entry['cover_review']['kind'] != 'person':
                continue
            operation = 'SYNTHETIC-operation-%d' % index
            self.batch.db.execute('INSERT INTO tasks(id,account,tool,fingerprint,payload,status,run_id) VALUES (?,?,?,?,?,?,?)',
                (operation, self.batch.account, 'publish_content', entry['fingerprint'], '{}',
                 'submitted_unknown' if index == 0 else 'host_reported_success', run['id']))
            self.batch.db.execute('INSERT INTO cloud_batch_bindings(operation_id,binding_digest,host_status) VALUES (?,?,?)',
                (operation, 'SYNTHETIC', 'SYNTHETIC'))
        with mock.patch('cloud_cover_history.CloudCoverHistory.verify_review', side_effect=AssertionError('historical cover rechecked')):
            self.batch.continuous.content.strategy(run, self.batch.continuous.content.profile_for_run(run)[1], review['entries'])
        self.assertEqual(sum(e['cover_review']['kind'] == 'person' for e in review['entries'].values()), 3)
        self.batch.db.execute("UPDATE tasks SET status='dispatching' WHERE id='SYNTHETIC-operation-0'")
        with mock.patch('cloud_cover_history.CloudCoverHistory.verify_review',
                        return_value={'allowed': False, 'reasons': ['SYNTHETIC history changed']}) as verify:
            with self.assertRaisesRegex(ValueError, 'ERR_COVER_HISTORY_REQUIRED'):
                self.batch.continuous.content.strategy(run, self.batch.continuous.content.profile_for_run(run)[1], review['entries'])
            self.assertEqual(verify.call_count, 1)


    def test_future_four_person_batch_rejected_even_with_good_individual_evidence(self):
        _, run, items = self.future_items(people=4)
        with self.assertRaisesRegex(ValueError, 'ERR_PERSON_COVER_EXACT'):
            self.batch.continuous.content.review(run, items, 'SYNTHETIC offline review')

    def test_successor_requires_actual_bound_success_and_preserves_old_unknown_comments(self):
        prior, items = self.reviewed()
        action = self.prepare_action(prior, items[0])
        self.batch.dispatch('configure', {'content_profile': self.future_profile()})
        request = {'successor_of': prior['id'], 'plan_ref': 'SYNTHETIC successor',
                   'plan': {'posts': 6, 'comments': 50, 'comment_strategy': {'A': 25, 'B': 25, 'C': 0}}}
        with self.assertRaisesRegex(ValueError, 'ERR_SUCCESSOR_POSTS_PENDING'):
            self.batch.dispatch('begin', request)
        self.report_success(action)
        comment = self.prepare_action(prior, items[1])
        self.report_success(comment, outcome='submitted_unknown')
        old = self.batch.continuous.run(prior['id'])
        successor = self.batch.dispatch('begin', request)
        self.assertFalse(successor['duplicate'])
        self.assertNotEqual(successor['id'], prior['id'])
        self.assertEqual(successor['body']['successor_of'], prior['id'])
        self.assertEqual(self.batch.continuous.run(prior['id']), old)
        self.assertEqual(self.actions.get(comment['operation_id'])['status'], 'submitted_unknown')
        again = self.batch.dispatch('begin', request)
        self.assertTrue(again['duplicate'])
        self.assertEqual(again['id'], successor['id'])
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM ops_runs').fetchone()[0], 2)
        self.assertTrue(self.batch.dispatch('begin', {'plan_ref': 'SYNTHETIC', 'plan': request['plan']})['duplicate'])

    def test_successor_cannot_use_plan_or_review_instead_of_receipts(self):
        prior, _ = self.reviewed()
        self.batch.dispatch('configure', {'content_profile': self.future_profile()})
        with self.assertRaisesRegex(ValueError, 'ERR_SUCCESSOR_POSTS_PENDING'):
            self.batch.dispatch('begin', {'successor_of': prior['id'], 'plan_ref': 'SYNTHETIC',
                                         'plan': {'posts': 6, 'comments': 50}})

    def schedule_evidence(self, earliest, mode='ready_batch', latest=None):
        return {'mode': mode, 'ready': True, 'readiness_ref': 'SYNTHETIC content ready',
                'platform_evidence_ref': 'SYNTHETIC observed picker limits',
                'platform_observed_at': dt.datetime.fromtimestamp(self.now[0], dt.timezone.utc).isoformat(),
                'platform_earliest': earliest,
                'platform_latest': latest or (dt.datetime.fromtimestamp(self.now[0], dt.timezone.utc) + dt.timedelta(days=7)).isoformat()}

    def test_ready_schedule_rolls_overnight_and_reuses_stable_slots(self):
        self.now[0] = dt.datetime.fromisoformat('2026-10-21T21:00:00+08:00').timestamp()
        _, run = self.future_begin()
        evidence = self.schedule_evidence('2026-10-21T22:15:00+08:00')
        result = self.batch.dispatch('schedule', {'run_id': run['id'], 'future_schedule': evidence})
        slots = [dt.datetime.fromisoformat(value) for value in result['schedule_slots']]
        self.assertEqual([value.strftime('%d %H:%M') for value in slots],
                         ['21 22:15', '22 09:00', '22 10:30', '22 12:00', '22 13:30', '22 15:00'])
        self.assertTrue(all((b-a).total_seconds() >= 5400 for a, b in zip(slots, slots[1:])))
        self.now[0] += 7200
        reused = self.batch.dispatch('schedule', {'run_id': run['id'], 'future_schedule': evidence})
        self.assertTrue(reused['reused'])
        self.assertEqual(reused['schedule_slots'], result['schedule_slots'])
        self.batch.close()
        self.batch = existing.cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        self.assertEqual(self.batch.dispatch('schedule', {'run_id': run['id']})['schedule_slots'], result['schedule_slots'])

    def test_preferred_schedule_uses_grid_and_sequentially_shifts_missed_slots(self):
        self.now[0] = dt.datetime.fromisoformat('2026-10-21T07:00:00+08:00').timestamp()
        _, run = self.future_begin()
        early = self.schedule_evidence('2026-10-21T08:00:00+08:00', 'preferred')
        slots = self.batch.continuous._future_slots(run, early)
        self.assertEqual([dt.datetime.fromisoformat(x).strftime('%H:%M') for x in slots], ['09:00','11:00','14:00','16:00','19:00','22:00'])
        late = self.schedule_evidence('2026-10-21T15:00:00+08:00', 'preferred')
        slots = self.batch.continuous._future_slots(run, late)
        self.assertEqual([dt.datetime.fromisoformat(x).strftime('%H:%M') for x in slots], ['15:00','16:30','18:00','19:30','21:00','22:30'])

    def test_old_ten_slot_overlap_exception_remains_narrow_for_future_six(self):
        self.now[0] = dt.datetime.fromisoformat('2026-10-21T07:00:00+08:00').timestamp()
        first = self.begin(); self.enable_overlap(first)
        grid = self.daily_slots(day=0)
        self.batch.dispatch('schedule', {'run_id': first['id'], 'explicit_slots': grid})
        self.batch.dispatch('finish', {'run_id': first['id'], 'abandon': True})
        _, second = self.future_begin()
        evidence = self.schedule_evidence('2026-10-21T09:00:00+08:00')
        result = self.batch.dispatch('schedule', {'run_id': second['id'], 'future_schedule': evidence})
        self.assertEqual(len(result['schedule_slots']), 6)
        self.assertEqual(self.batch.continuous.run(first['id'])['body']['schedule_slots'], grid)
        self.batch.dispatch('finish', {'run_id': second['id'], 'abandon': True})
        _, third = self.future_begin()
        shifted=self.batch.dispatch('schedule', {'run_id': third['id'], 'future_schedule': evidence})
        self.assertTrue(all(dt.datetime.fromisoformat(x).date()>dt.datetime.fromisoformat(result['schedule_slots'][-1]).date() for x in shifted['schedule_slots']))

    def test_future_schedule_requires_ready_actual_platform_bounds(self):
        self.now[0] = dt.datetime.fromisoformat('2026-10-21T07:00:00+08:00').timestamp()
        _, run = self.future_begin()
        for evidence in (None, {'ready': False}, self.schedule_evidence('2026-10-21T06:00:00+08:00'),
                         self.schedule_evidence('2026-10-21T22:00:00+08:00', latest='2026-10-21T23:00:00+08:00')):
            with self.assertRaisesRegex(ValueError, 'ERR_FUTURE_SCHEDULE'):
                self.batch.dispatch('schedule', {'run_id': run['id'], 'future_schedule': evidence})
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['schedule_slots'], [])

    def test_legacy_copy_does_not_acquire_future_requirements(self):
        _CloudContentReview.future_copy({}, 'post', {'fields': {}}, {'content': 'legacy'})
        _CloudContentReview.future_copy(self.future_profile(), 'comment', {'fields': {}}, {'content': 'helpful'})


if __name__ == '__main__':
    unittest.main()

    def test_conservative_candidate_and_shared_carryover_daily_cap(self):
        self.now[0]=dt.datetime.fromisoformat('2026-10-21T15:00:00+08:00').timestamp()
        _,run=self.future_begin();ev=self.schedule_evidence('2026-10-21T17:30:00+08:00')
        ev.update(lower_bound_kind='conservative_candidate',planning_lower_bound_ref='SYNTHETIC observed default17:15;17:30 candidate, not certified earliest')
        first=self.batch.dispatch('schedule',{'run_id':run['id'],'future_schedule':ev})['schedule_slots']
        self.assertEqual([dt.datetime.fromisoformat(x).strftime('%H:%M') for x in first],['17:30','19:00','20:30','22:00','09:00','10:30'])
        self.batch.dispatch('finish',{'run_id':run['id'],'abandon':True})
        _,other=self.future_begin();ev2=self.schedule_evidence('2026-10-22T09:00:00+08:00')
        second=self.batch.dispatch('schedule',{'run_id':other['id'],'future_schedule':ev2})['schedule_slots']
        counts={}
        for x in first+second:
            d=dt.datetime.fromisoformat(x).date();counts[d]=counts.get(d,0)+1
        self.assertLessEqual(max(counts.values()),6)
        self.assertEqual(dt.datetime.fromisoformat(second[-1]).date().isoformat(),'2026-10-23')
