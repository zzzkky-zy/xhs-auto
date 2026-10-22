"""Synthetic pixels/provenance only. No real images, reviews or publications."""
import builtins
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
import unittest
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / 'runtime'))
from cloud_actions import CloudActions
from cloud_cover_history import CloudCoverHistory, DIMENSIONS, fingerprint
try:
    from PIL import Image, ImageDraw, PngImagePlugin
except ImportError:
    Image = None


@unittest.skipIf(Image is None, 'optional Pillow is not installed')
class CoverHistoryTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.config = self.root / 'config.json'
        self.config.write_text(json.dumps({
            'schema_version': 1, 'browser_mode': 'cloud_host', 'account_id': 'synthetic',
            'expected_user_id': 'a' * 24, 'state_dir': 'state', 'output_dir': 'output',
            'allow_write': False, 'allow_reconcile': False,
            'resources': {'min_free_disk_mb': 1, 'min_free_inodes': 1}}))
        self.actions = CloudActions(self.config)
        self.history = CloudCoverHistory(self.actions)
        self.a = self.make_image('a.png', 'vertical')
        self.b = self.make_image('b.png', 'horizontal')

    def tearDown(self):
        self.actions.close()
        self.tmp.cleanup()

    def make_image(self, name, kind):
        image = Image.new('RGB', (96, 96), 'white')
        draw = ImageDraw.Draw(image)
        draw.rectangle((0, 0, 45, 95) if kind == 'vertical' else (0, 0, 95, 45), fill='black')
        path = self.root / name
        image.save(path)
        return path

    def ingest(self, image=None, status='published'):
        return self.history.ingest(image or self.a, status, 'SYNTHETIC source observation',
                                   reference_person='SYNTHETIC supplied reference')

    def review(self, image=None, peers=None):
        assessment = self.history.assess(image or self.b, reference_person='SYNTHETIC supplied reference',
                                         peer_image_paths=peers)
        return {'visual_reviewed': True, 'reviewer': 'SYNTHETIC test host',
                'evidence_ref': 'SYNTHETIC visual inspection only; not a real review',
                'candidate_pixel_sha256': assessment['candidate']['pixel_sha256'],
                'history_digest': assessment['history_digest'],
                'reference_person': 'SYNTHETIC supplied reference', 'same_reference_person': True,
                'reference_comparison': {key: 'SYNTHETIC reference comparison ' + key for key in DIMENSIONS},
                'comparisons': [dict(entry_id=item['entry_id'], changed_dimensions=['composition'],
                                     near_match_override=True,
                                     near_match_explanation='SYNTHETIC actual visible difference explanation',
                                     **{key: 'SYNTHETIC observed ' + key for key in DIMENSIONS})
                                for item in assessment['comparisons']]}

    def assess(self, image=None, review=None, peers=None):
        return self.history.assess(image or self.b, reference_person='SYNTHETIC supplied reference',
                                   visual_review=review, peer_image_paths=peers)

    def test_separate_status_persistence_and_legacy_preservation(self):
        self.actions.db.execute('CREATE TABLE legacy_cover_history (payload TEXT)')
        self.actions.db.execute("INSERT INTO legacy_cover_history VALUES ('old unverified entry')")
        self.ingest(status='scheduled')
        self.ingest(status='published')
        self.actions.close()
        self.actions = CloudActions(self.config)
        self.history = CloudCoverHistory(self.actions)
        self.assertEqual(len(self.history.list_history()), 2)
        self.assertEqual(len(self.history.list_history('scheduled')), 1)
        self.assertEqual(len(self.history.list_history('published')), 1)
        self.assertEqual(self.actions.db.execute('SELECT payload FROM legacy_cover_history').fetchone()[0],
                         'old unverified entry')
        for query in ('DELETE FROM cover_visual_history', "UPDATE cover_visual_history SET publication_status='published'"):
            with self.assertRaises(sqlite3.IntegrityError):
                self.actions.db.execute(query)

    def test_reencoded_exact_pixels_always_block(self):
        self.ingest()
        alternate = self.root / 'other.png'
        metadata = PngImagePlugin.PngInfo()
        metadata.add_text('fixture', 'different file bytes, identical pixels')
        with Image.open(self.a) as image:
            image.save(alternate, pnginfo=metadata)
        self.assertNotEqual(fingerprint(self.a)['file_sha256'], fingerprint(alternate)['file_sha256'])
        result = self.assess(alternate, self.review(alternate))
        self.assertFalse(result['allowed'])
        self.assertTrue(result['comparisons'][0]['exact_duplicate'])

    def test_perceptual_near_requires_explicit_visual_override(self):
        self.ingest()
        near = self.root / 'near.png'
        with Image.open(self.a) as image:
            image.resize((192, 192)).save(near)
        review = self.review(near)
        review['comparisons'][0].pop('near_match_override')
        result = self.assess(near, review)
        self.assertTrue(result['comparisons'][0]['perceptual_near_duplicate'])
        self.assertFalse(result['allowed'])
        self.assertTrue(self.assess(near, self.review(near))['allowed'])

    def test_unreviewed_and_incomplete_qualitative_evidence_fail_closed(self):
        self.ingest(status='scheduled')
        self.assertFalse(self.assess()['allowed'])
        review = self.review()
        review['comparisons'][0]['changed_dimensions'] = []
        self.assertFalse(self.assess(review=review)['allowed'])
        review = self.review()
        review['comparisons'][0]['pose'] = ''
        self.assertFalse(self.assess(review=review)['allowed'])
        review = self.review()
        review['same_reference_person'] = 1
        self.assertFalse(self.assess(review=review)['allowed'])
        self.assertTrue(self.assess(review=self.review())['allowed'])

    def test_persisted_review_revalidates_history_and_pixels(self):
        self.ingest()
        record = self.history.record_review(self.b, reference_person='SYNTHETIC supplied reference',
                                            visual_review=self.review())
        verified = self.history.verify_review(record['review_id'], self.b,
                                               reference_person='SYNTHETIC supplied reference')
        self.assertTrue(verified['allowed'])
        self.ingest(status='scheduled')
        self.assertFalse(self.history.verify_review(record['review_id'], self.b,
                         reference_person='SYNTHETIC supplied reference')['allowed'])
        record = self.history.record_review(self.b, reference_person='SYNTHETIC supplied reference',
                                            visual_review=self.review())
        self.make_image('b.png', 'vertical')
        self.assertFalse(self.history.verify_review(record['review_id'], self.b,
                         reference_person='SYNTHETIC supplied reference')['allowed'])

    def test_peer_cover_duplicate_and_changed_peer_invalidate_review(self):
        self.assertFalse(self.assess(peers=[self.b], review=self.review(peers=[self.b]))['allowed'])
        record = self.history.record_review(self.b, reference_person='SYNTHETIC supplied reference',
                                            visual_review=self.review(peers=[self.a]), peer_image_paths=[self.a])
        self.assertTrue(self.history.verify_review(record['review_id'], self.b,
                        reference_person='SYNTHETIC supplied reference')['allowed'])
        self.make_image('a.png', 'horizontal')
        self.assertFalse(self.history.verify_review(record['review_id'], self.b,
                         reference_person='SYNTHETIC supplied reference')['allowed'])

    def test_invalid_file_status_missing_dependency(self):
        bad = self.root / 'invalid.png'
        bad.write_text('not an image')
        with self.assertRaises(ValueError):
            fingerprint(bad)
        with self.assertRaises(ValueError):
            self.ingest(status='draft')
        original = builtins.__import__
        def without_pillow(name, *args, **kwargs):
            if name == 'PIL':
                raise ImportError('synthetic missing dependency')
            return original(name, *args, **kwargs)
        with mock.patch('builtins.__import__', side_effect=without_pillow):
            with self.assertRaisesRegex(ValueError, 'optional Pillow'):
                fingerprint(self.a)

    def test_safe_paths_reject_links_directories_fifo_and_relative(self):
        link = self.root / 'linked.png'
        link.symlink_to(self.a)
        directory_link = self.root / 'linked-directory'
        directory_link.symlink_to(self.root, target_is_directory=True)
        fifo = self.root / 'pipe.png'
        os.mkfifo(fifo)
        for path in (link, directory_link / 'a.png', self.root, fifo, Path('relative.png')):
            with self.subTest(path=path), self.assertRaises(ValueError):
                fingerprint(path)

    def test_jpeg_reencoding_is_perceptual_match_not_exact_pixels(self):
        self.ingest()
        jpeg = self.root / 'compressed.jpg'
        with Image.open(self.a) as image:
            image.save(jpeg, quality=70)
        comparison = self.assess(jpeg)['comparisons'][0]
        self.assertFalse(comparison['exact_duplicate'])
        self.assertTrue(comparison['perceptual_near_duplicate'])
        self.assertFalse(self.assess(jpeg)['allowed'])

    def test_review_unknown_and_account_isolation(self):
        self.ingest()
        with self.assertRaisesRegex(ValueError, 'unknown cover'):
            self.history.verify_review('missing', self.b, reference_person='fixture')
        self.actions.account = 'different-account'
        self.assertEqual(self.history.list_history(), [])


if __name__ == '__main__':
    unittest.main()
