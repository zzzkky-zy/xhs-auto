"""Offline upload-inspection review tests. All files and approvals are synthetic."""
import hashlib
import json
from pathlib import Path
import sqlite3
import unittest
from unittest import mock

import test_cloud_batch as fixtures
import content_fixture

cloud_batch = fixtures.cloud_batch


class UploadInspectionTests(unittest.TestCase):
    setUp = fixtures.CloudBatchTests.setUp
    tearDown = fixtures.CloudBatchTests.tearDown
    begin = fixtures.CloudBatchTests.begin
    daily_slots = fixtures.CloudBatchTests.daily_slots

    def final_posts(self, count=10, people=2, planned=None):
        profile = content_fixture.profile(self.base)
        material = self.base / 'inspection-source.txt'; material.write_text('SYNTHETIC source material')
        face = self.base / 'inspection-reference.png'; face.write_bytes(b'SYNTHETIC not a real face')
        sha = lambda path: hashlib.sha256(path.read_bytes()).hexdigest()
        profile.update(product_materials=[{'file': str(material), 'sha256': sha(material), 'source_ref': 'SYNTHETIC'}],
                       cover_person_enabled=True,
                       face_reference={'file': str(face), 'sha256': sha(face), 'source_ref': 'SYNTHETIC'},
                       content_requirements={'post_cta': 'SYNTHETIC CTA', 'comment_offer_phrases': ['SYNTHETIC offer'],
                                             'comment_positioning_terms': ['synthetic'], 'min_person_covers': 2,
                                             'exact_person_covers': 2})
        for key in ('product_materials', 'cover_person_enabled', 'face_reference', 'content_requirements'):
            profile['confirmation_sources'][key] = 'SYNTHETIC TEST ONLY'
        self.batch.dispatch('configure', {'content_profile': profile})
        run = self.begin()
        self.batch.dispatch('update', {'run_id': run['id'], 'plan_ref': 'SYNTHETIC current plan',
                                      'plan': {'posts': count if planned is None else planned, 'comments': 1,
                                               'comment_strategy': {'A': 1, 'B': 0, 'C': 0}}})
        items = []
        for n in range(count):
            asset = self.batch.continuous.directory(run['id']) / 'final' / ('post-' + str(n) + '.png')
            asset.write_bytes(('SYNTHETIC final image ' + str(n)).encode())
            items.append({'content_id': 'fixture-post-' + str(n), 'tool': 'publish_content',
                          'payload': {'title': 'SYNTHETIC title ' + str(n), 'content': 'SYNTHETIC body. SYNTHETIC CTA',
                                      'images': [str(asset)]}})
        def transform(item, fields, final):
            n = items.index(item); fields['cta'] = 'SYNTHETIC CTA'
            asset = Path(item['payload']['images'][0])
            final['cover_review'] = {'batch_id': 'synthetic-inspection', 'batch_content_ids': [i['content_id'] for i in items],
                                    'kind': 'person' if n < people else 'illustrated', 'asset': str(asset),
                                    'asset_sha256': sha(asset), 'style': 'SYNTHETIC unique ' + str(n),
                                    'selection_reason': 'SYNTHETIC', 'observation': 'SYNTHETIC no visual assertion',
                                    'reference_file': str(face), 'reference_sha256': sha(face),
                                    'likeness_observation': 'SYNTHETIC no likeness assertion'}
        self.transform = transform
        content_fixture.register(self.batch.continuous, run, items, transform=transform)
        return self.batch.continuous.run(run['id']), items, profile

    def review(self, run, items):
        return self.batch.review_upload_inspection(run['id'], items, 'SYNTHETIC content/media cross-item review')

    def prepare_inspection(self, run, item, review, **kwargs):
        return self.batch.prepare_upload_inspection(run['id'], item['payload'],
                                                   kwargs.pop('approval_ref', 'SYNTHETIC upload source'),
                                                   inspection_id=review['inspection_id'], **kwargs)

    def test_complete_unscheduled_batch_is_separately_reviewed_and_media_sealed(self):
        run, items, _ = self.final_posts()
        before = self.batch.continuous.run(run['id'])
        with mock.patch('socket.socket', side_effect=AssertionError('network forbidden')), \
             mock.patch('subprocess.Popen', side_effect=AssertionError('process forbidden')):
            review = self.review(run, items)
            prepared = self.prepare_inspection(run, items[0], review)
        self.assertEqual(review['reviewed_payloads'], 10)
        self.assertEqual(prepared['tool'], 'inspect_publication_schedule')
        self.assertEqual(prepared['inspection_id'], review['inspection_id'])
        self.assertEqual(prepared['review_digest'], review['review_digest'])
        self.assertNotIn('schedule_at', prepared['payload'])
        self.assertFalse(prepared['publishing_authorized']); self.assertFalse(prepared['authorization_granted'])
        self.assertFalse(prepared['schedule_reserved']); self.assertFalse(prepared['browser_action_performed'])
        sealed = Path(prepared['payload']['images'][0])
        self.assertEqual(sealed.parent.name, 'sealed')
        self.assertEqual(sealed.read_bytes(), Path(items[0]['payload']['images'][0]).read_bytes())
        self.assertEqual(prepared['media_manifest'][0]['sha256'], hashlib.sha256(sealed.read_bytes()).hexdigest())
        self.assertEqual(sealed.stat().st_mode & 0o777, 0o400)
        self.assertEqual(self.batch.continuous.run(run['id']), before)
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM ops_calendar').fetchone()[0], 0)
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM tasks').fetchone()[0], 0)
        self.assertEqual(self.actions.list_actions(), [])

    def test_inspection_never_satisfies_or_overwrites_normal_publication_review(self):
        run, items, _ = self.final_posts()
        original = self.batch.continuous.run(run['id']); original['body']['review'] = {'SYNTHETIC': 'preserve normal review'}
        self.batch.continuous.save(original)
        review = self.review(run, items); self.prepare_inspection(run, items[0], review)
        self.assertEqual(self.batch.continuous.run(run['id'])['body']['review'], original['body']['review'])
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_REVIEW_STALE'):
            self.batch.prepare_payload(run['id'], 'publish_content', items[0]['payload'], 'SYNTHETIC')
        with self.assertRaisesRegex(ValueError, 'ERR_SCHEDULE_REQUIRED'):
            self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC'})

    def test_review_is_idempotent_across_time_order_and_reopen(self):
        run, items, _ = self.final_posts(); first = self.review(run, items)
        before = tuple(self.batch.db.execute('SELECT * FROM cloud_upload_inspection_reviews').fetchone())
        self.now[0] += 60
        second = self.review(run, list(reversed(items)))
        self.assertTrue(second['duplicate']); self.assertEqual(second['inspection_id'], first['inspection_id'])
        self.assertEqual(tuple(self.batch.db.execute('SELECT * FROM cloud_upload_inspection_reviews').fetchone()), before)
        self.batch.close(); self.batch = cloud_batch.CloudBatch(self.path, self.actions, clock=lambda: self.now[0])
        prepared = self.prepare_inspection(run, items[0], first)
        self.assertEqual(prepared['inspection_id'], first['inspection_id'])
        # Host dispatch revalidation receives the already sealed immutable payload.
        again = self.batch.prepare_upload_inspection(run['id'], prepared['payload'], 'SYNTHETIC upload source',
                                                      inspection_id=first['inspection_id'])
        self.assertEqual(again['payload'], prepared['payload'])
        self.assertEqual(again['media_manifest'], prepared['media_manifest'])

    def test_evidence_object_is_canonical_bounded_and_revalidates_as_original_string(self):
        run, items, _ = self.final_posts()
        evidence = {'source': 'SYNTHETIC QA file reference', 'checks': ['copy', 'covers', 'media']}
        reviewed = self.batch.review_upload_inspection(run['id'], items, evidence)
        row = self.batch.db.execute('SELECT record_json FROM cloud_upload_inspection_reviews').fetchone()
        stored = json.loads(row['record_json'])
        self.assertEqual(json.loads(stored['review']['evidence']), evidence)
        repeated = self.batch.review_upload_inspection(run['id'], items, dict(reversed(list(evidence.items()))))
        self.assertEqual(reviewed['inspection_id'], repeated['inspection_id'])
        self.assertTrue(self.prepare_inspection(run, items[0], reviewed)['media_manifest'])
        for invalid in ({'oversize': 'x' * 4000}, ['not an evidence object'], True, None):
            with self.assertRaisesRegex(ValueError, 'ERR_QUALITY_REVIEW_REQUIRED'):
                self.batch.review_upload_inspection(run['id'], items, invalid)

    def test_count_derives_from_current_plan_and_not_a_universal_ten(self):
        run, items, _ = self.final_posts(count=3)
        self.assertEqual(self.review(run, items)['reviewed_payloads'], 3)
        with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_BATCH'):
            self.review(run, items[:2])

    def test_incomplete_batch_and_ineligible_tools_and_images_fail(self):
        run, items, _ = self.final_posts()
        invalid = [items[:9], items + [items[0]], [items[0]] * 10,
                   [{**items[0], 'tool': 'publish_with_video'}, *items[1:]],
                   [{**items[0], 'payload': {**items[0]['payload'], 'images': []}}, *items[1:]],
                   [{**items[0], 'payload': {**items[0]['payload'], 'images': ['SYNTHETIC'] * 6}}, *items[1:]]]
        for entries in invalid:
            with self.assertRaises(ValueError): self.review(run, entries)
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM cloud_upload_inspection_reviews').fetchone()[0], 0)

    def test_no_fake_or_final_schedule_values_are_accepted(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        for value in (None, '', '2026-10-20T09:00:00+08:00'):
            modified = {**items[0], 'payload': {**items[0]['payload'], 'schedule_at': value}}
            with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_SCHEDULE'):
                self.review(run, [modified, *items[1:]])
            with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_SCHEDULE'):
                self.prepare_inspection(run, modified, review)

    def test_changed_selected_copy_or_image_is_not_reviewed(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        for change in ({'title': 'Changed synthetic title'}, {'content': 'Changed synthetic body'},
                       {'images': items[1]['payload']['images']}):
            modified = {**items[0], 'payload': {**items[0]['payload'], **change}}
            with self.assertRaisesRegex(ValueError, 'ERR_QUALITY_REVIEW_REQUIRED'):
                self.prepare_inspection(run, modified, review)

    def test_other_post_media_changes_invalidate_the_whole_inspection(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        Path(items[-1]['payload']['images'][0]).write_bytes(b'SYNTHETIC changed last cover')
        with self.assertRaises(ValueError): self.prepare_inspection(run, items[0], review)
        self.assertIsNone(self.batch.continuous.run(run['id'])['body']['review'])

    def test_plan_changes_invalidate_but_profile_changes_preserve_pinned_inspection(self):
        run, items, profile = self.final_posts(); review = self.review(run, items)
        self.batch.dispatch('update', {'run_id': run['id'], 'plan_ref': 'SYNTHETIC changed plan reference', 'plan': run['body']['plan']})
        with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_STALE'):
            self.prepare_inspection(run, items[0], review)
        self.batch.dispatch('update', {'run_id': run['id'], 'plan_ref': run['body']['plan_ref'], 'plan': run['body']['plan']})
        self.batch.dispatch('configure', {'content_profile': {**profile, 'persona': 'SYNTHETIC changed persona'}})
        prepared = self.prepare_inspection(run, items[0], review)
        self.assertTrue(prepared)
        pinned = self.batch.continuous.content.profile_for_run(self.batch.continuous.run(run['id']))[1]
        self.assertEqual(pinned, profile)

    def test_new_stage_chain_even_same_bytes_requires_new_inspection(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        content_fixture.register(self.batch.continuous, run, items, transform=self.transform)
        with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_STALE'):
            self.prepare_inspection(run, items[0], review)
        replacement = self.review(run, items)
        self.assertNotEqual(replacement['inspection_id'], review['inspection_id'])
        self.assertEqual(self.batch.db.execute('SELECT count(*) FROM cloud_upload_inspection_reviews').fetchone()[0], 2)

    def test_missing_user_reference_or_wrong_receipt_cannot_prepare_upload(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        for source in ('', None, True):
            with self.assertRaisesRegex(ValueError, 'ERR_APPROVAL_REQUIRED'):
                self.prepare_inspection(run, items[0], review, approval_ref=source)
        with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_BINDING'):
            self.prepare_inspection(run, items[0], {**review, 'inspection_id': '0' * 64})
        wrong = {**items[0], 'payload': {**items[0]['payload'], 'run_id': 'f' * 32}}
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.prepare_inspection(run, wrong, review)
        wrong = {**items[0], 'payload': {**items[0]['payload'], 'approval_ref': 'Different source'}}
        with self.assertRaisesRegex(ValueError, 'ERR_CLOUD_BATCH_BINDING'):
            self.prepare_inspection(run, wrong, review)

    def test_original_exact_cover_review_cannot_be_bypassed(self):
        run, items, _ = self.final_posts(people=3)
        with self.assertRaisesRegex(ValueError, 'ERR_PERSON_COVER_EXACT'):
            self.review(run, items)

    def test_original_stage_chain_is_still_required(self):
        run, items, _ = self.final_posts()
        missing = {**items[0], 'content_id': 'unregistered-content'}
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_STAGE'):
            self.review(run, [missing, *items[1:]])

    def test_inspection_evidence_is_immutable_and_sealing_failure_is_recoverable(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        for command in ("UPDATE cloud_upload_inspection_reviews SET record_json='{}'", 'DELETE FROM cloud_upload_inspection_reviews'):
            with self.assertRaises(sqlite3.IntegrityError): self.batch.db.execute(command)
        with mock.patch('cloud_batch.os.link', side_effect=OSError('SYNTHETIC interrupted sealing')):
            with self.assertRaises(OSError): self.prepare_inspection(run, items[0], review)
        sealed = self.batch.continuous.directory(run['id']) / 'sealed'
        self.assertEqual(list(sealed.iterdir()), [])
        self.assertTrue(self.prepare_inspection(run, items[0], review)['media_manifest'])
        self.assertIsNone(self.batch.continuous.run(run['id'])['body']['review'])

    def test_real_schedule_and_new_final_stages_are_still_needed_for_publication(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        self.prepare_inspection(run, items[0], review)
        grid = self.daily_slots()
        self.batch.dispatch('schedule', {'run_id': run['id'], 'explicit_slots': grid})
        with self.assertRaisesRegex(ValueError, 'ERR_CONTENT_REVIEW_STALE'):
            self.batch.prepare_payload(run['id'], 'publish_content', items[0]['payload'], 'SYNTHETIC')
        for item, slot in zip(items, grid): item['payload']['schedule_at'] = slot
        content_fixture.register(self.batch.continuous, run, items, transform=self.transform)
        self.batch.dispatch('review', {'run_id': run['id'], 'payloads': items, 'evidence': 'SYNTHETIC final scheduled review'})
        final = self.batch.prepare_payload(run['id'], 'publish_content', items[0]['payload'], 'SYNTHETIC')
        self.assertEqual(final['payload']['schedule_at'], grid[0])
        self.assertEqual(final['tool'], 'publish_content')
        with self.assertRaisesRegex(ValueError, 'ERR_UPLOAD_INSPECTION_SCHEDULE'):
            self.prepare_inspection(run, items[0], review)

    def test_closed_run_or_disallowed_publication_cannot_use_inspection(self):
        run, items, _ = self.final_posts(); review = self.review(run, items)
        restricted = {**self.batch.continuous.policy(), 'allowed_tools': ['post_comment_to_feed']}
        with mock.patch.object(self.batch.continuous, 'policy', return_value=restricted):
            with self.assertRaisesRegex(ValueError, 'ERR_POLICY_SCOPE'):
                self.prepare_inspection(run, items[0], review)
        self.batch.dispatch('finish', {'run_id': run['id'], 'abandon': True})
        with self.assertRaisesRegex(ValueError, 'ERR_RUN_CLOSED'):
            self.prepare_inspection(run, items[0], review)


if __name__ == '__main__':
    unittest.main()
