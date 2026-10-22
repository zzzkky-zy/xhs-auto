"""Append-only, account-scoped cover evidence in the private CloudActions ledger.

Only explicitly supplied images are opened. No filesystem discovery, publication,
identity recognition or automatic host visual review occurs here. Perceptual hashes are
conservative duplicate heuristics, not proof of semantic uniqueness or identity.
Visual-review records are host attestations; the host must verify their provenance.
Legacy action/history tables are never rewritten or treated as visual evidence.
"""
import hashlib
import io
import json
import os
import stat
from pathlib import Path
import uuid

from cloud_actions import _json, _text

DIMENSIONS = ('pose', 'facial_expression', 'clothes', 'composition')
STATUSES = ('published', 'scheduled')
ALGORITHM = 'exif-rgb-ahash-dhash-v1'
MAX_IMAGE_BYTES = 32 * 1024 * 1024


def fingerprint(image_path):
    """Read exactly one existing image; fail closed without optional Pillow."""
    try:
        from PIL import Image, ImageOps
    except ImportError as exc:
        raise ValueError('Pillow is required for visual cover comparison; install the optional Pillow dependency') from exc
    path = Path(image_path)
    if not path.is_absolute() or str(path) != os.path.normpath(str(path)):
        raise ValueError('cover requires a normalized absolute local path')
    if any(part.is_symlink() for part in (path, *path.parents)):
        raise ValueError('linked cover paths are not permitted')
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as handle:
            before = os.fstat(handle.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_IMAGE_BYTES:
                raise ValueError('cover must be a regular image of 1 byte to 32 MiB')
            raw = handle.read(MAX_IMAGE_BYTES + 1)
            after = os.fstat(handle.fileno())
            current = os.stat(path, follow_symlinks=False)
            def identity(info):
                return (info.st_dev, info.st_ino, info.st_size, info.st_mtime_ns, info.st_ctime_ns)
            if len(raw) != before.st_size or identity(before) != identity(after) or identity(after) != identity(current):
                raise ValueError('cover changed while reading')
            if any(part.is_symlink() for part in (path, *path.parents)):
                raise ValueError('linked cover paths are not permitted')
    except OSError as exc:
        raise ValueError('local cover unavailable') from exc
    try:
        with Image.open(io.BytesIO(raw)) as source:
            if source.width * source.height > 40_000_000 or getattr(source, 'n_frames', 1) != 1:
                raise ValueError('cover must be a single image of at most 40 megapixels')
            rgb = ImageOps.exif_transpose(source).convert('RGB')
            pixels = rgb.tobytes()
            gray = rgb.convert('L')
            a = list(gray.resize((8, 8), Image.Resampling.LANCZOS).tobytes())
            d = list(gray.resize((9, 8), Image.Resampling.LANCZOS).tobytes())
            average = sum(a) / 64
            ahash = sum((value >= average) << i for i, value in enumerate(a))
            dhash = sum((d[y * 9 + x] > d[y * 9 + x + 1]) << (y * 8 + x)
                        for y in range(8) for x in range(8))
            return {'algorithm': ALGORITHM, 'path': str(path),
                    'file_sha256': hashlib.sha256(raw).hexdigest(),
                    'pixel_sha256': hashlib.sha256(
                        ('%s,%s:' % rgb.size).encode() + pixels).hexdigest(),
                    'width': rgb.width, 'height': rgb.height,
                    'ahash': '%016x' % ahash, 'dhash': '%016x' % dhash}
    except (OSError, Image.DecompressionBombError) as exc:
        raise ValueError('cover is not a safely decodable image') from exc


def _digest(value):
    return hashlib.sha256(_json(value).encode()).hexdigest()


def _distance(a, b):
    return bin(int(a, 16) ^ int(b, 16)).count('1')


class CloudCoverHistory:
    """Shares actions.db; caller owns connection lifetime. Never grants publish authority."""
    def __init__(self, actions):
        self.actions = actions
        with actions._transaction():
            actions.db.execute('''CREATE TABLE IF NOT EXISTS cover_visual_history (
                entry_id TEXT PRIMARY KEY, account TEXT NOT NULL,
                publication_status TEXT NOT NULL CHECK(publication_status IN ('published','scheduled')),
                recorded_at REAL NOT NULL, evidence_json TEXT NOT NULL)''')
            actions.db.execute('''CREATE TRIGGER IF NOT EXISTS cover_history_immutable_update
                BEFORE UPDATE ON cover_visual_history BEGIN
                SELECT RAISE(ABORT, 'immutable cover visual history'); END''')
            actions.db.execute('''CREATE TRIGGER IF NOT EXISTS cover_history_immutable_delete
                BEFORE DELETE ON cover_visual_history BEGIN
                SELECT RAISE(ABORT, 'immutable cover visual history'); END''')

            actions.db.execute('''CREATE TABLE IF NOT EXISTS cover_visual_reviews (
                review_id TEXT PRIMARY KEY, account TEXT NOT NULL, recorded_at REAL NOT NULL,
                review_json TEXT NOT NULL)''')
            for operation in ('UPDATE', 'DELETE'):
                actions.db.execute('CREATE TRIGGER IF NOT EXISTS cover_reviews_immutable_' +
                                   operation.lower() + ' BEFORE ' + operation +
                                   " ON cover_visual_reviews BEGIN SELECT RAISE(ABORT, 'immutable visual review'); END")

    def ingest(self, image_path, publication_status, source_evidence, *, reference_person):
        """Append a host-evidenced observation, never infer published from scheduled.

        source_evidence must identify the actual source (e.g. platform post/schedule
        observation) and its provenance. It is not independently verified here.
        Both observations are retained if a scheduled image later gets published.
        """
        if publication_status not in STATUSES:
            raise ValueError('publication_status must be published or scheduled')
        _text(source_evidence, 'source_evidence', 4000)
        _text(reference_person, 'reference_person', 1000)
        entry = {'entry_id': uuid.uuid4().hex, 'publication_status': publication_status,
                 'recorded_at': self.actions.clock(), 'source_evidence': source_evidence,
                 'reference_person': reference_person, 'fingerprint': fingerprint(image_path)}
        raw = _json(entry)
        self.actions._admit(entry)
        with self.actions._transaction():
            self.actions.db.execute('INSERT INTO cover_visual_history VALUES (?,?,?,?,?)',
                                    (entry['entry_id'], self.actions.account, publication_status,
                                     entry['recorded_at'], raw))
        return entry

    def list_history(self, publication_status=None):
        """Return immutable evidence snapshots, optionally separated by status."""
        if publication_status is not None and publication_status not in STATUSES:
            raise ValueError('publication_status must be published or scheduled')
        self.actions._check_storage()
        rows = self.actions.db.execute(
            'SELECT evidence_json FROM cover_visual_history WHERE account=? ORDER BY entry_id',
            (self.actions.account,)).fetchall()
        entries = [json.loads(row[0]) for row in rows]
        return [entry for entry in entries if publication_status is None or
                entry['publication_status'] == publication_status]

    def assess(self, image_path, *, reference_person, visual_review=None, peer_image_paths=None):
        """Compare with ALL published and scheduled covers, then check bound review.

        Host visual review schema: visual_reviewed=True, reviewer, evidence_ref,
        candidate_pixel_sha256, history_digest, reference_person,
        same_reference_person=True, reference_comparison (all four DIMENSIONS),
        comparisons=[{entry_id, changed_dimensions: [...], pose, facial_expression,
                      clothes, composition}]. All descriptions must be nonempty.
        The host, not this structural validator, establishes an actual host visual review.
        """
        _text(reference_person, 'reference_person', 1000)
        candidate = fingerprint(image_path)
        history = self.list_history()
        for peer in peer_image_paths or []:
            peer_fp = fingerprint(peer)
            history.append({'entry_id': 'peer:' + peer_fp['pixel_sha256'],
                            'publication_status': 'candidate_peer', 'fingerprint': peer_fp})
        history_digest = _digest(history)
        comparisons, reasons = [], []
        for entry in history:
            previous = entry['fingerprint']
            a = _distance(candidate['ahash'], previous['ahash'])
            d = _distance(candidate['dhash'], previous['dhash'])
            exact = (candidate['file_sha256'] == previous['file_sha256'] or
                     candidate['pixel_sha256'] == previous['pixel_sha256'])
            near = a <= 6 and d <= 6
            comparisons.append({'entry_id': entry['entry_id'],
                                'publication_status': entry['publication_status'],
                                'ahash_distance': a, 'dhash_distance': d,
                                'perceptual_similarity': 1 - (a + d) / 128,
                                'exact_duplicate': exact, 'perceptual_near_duplicate': near,
                                'qualitative_comparison_required': True})
            if exact:
                reasons.append('duplicate_cover:' + entry['entry_id'])
        review_errors = self._review_errors(visual_review, candidate, history_digest,
                                            reference_person, history, comparisons)
        reasons.extend(review_errors)
        return {'allowed': not reasons, 'reasons': reasons, 'candidate': candidate,
                'history_digest': history_digest, 'comparisons': comparisons,
                'required_review': {'dimensions': list(DIMENSIONS),
                                    'entry_ids': [entry['entry_id'] for entry in history],
                                    'actual_visual_review_required': True},
                'limitations': 'Perceptual similarity is a heuristic. Visual evidence is '
                'host-attested; this does not prove identity, a real photograph, '
                'semantic uniqueness, history completeness, or permission to publish.'}

    @staticmethod
    def _review_errors(review, candidate, history_digest, reference_person, history, pixel_comparisons):
        if not isinstance(review, dict):
            return ['actual_host_visual_review_required']
        try:
            _json(review)
            if review.get('visual_reviewed') is not True:
                raise ValueError('actual host visual review required')
            for key in ('reviewer', 'evidence_ref'):
                _text(review.get(key), key, 2000)
            for key, expected in (('candidate_pixel_sha256', candidate['pixel_sha256']),
                                  ('history_digest', history_digest),
                                  ('reference_person', reference_person),
                                  ('same_reference_person', True)):
                if review.get(key) != expected or (key == 'same_reference_person' and review.get(key) is not True):
                    raise ValueError('review binding mismatch: ' + key)
            reference = review.get('reference_comparison')
            if not isinstance(reference, dict):
                raise ValueError('reference comparison required')
            for dimension in DIMENSIONS:
                _text(reference.get(dimension), 'reference ' + dimension, 2000)
            comparisons = review.get('comparisons')
            if not isinstance(comparisons, list) or len(comparisons) != len(history):
                raise ValueError('review every published and scheduled history entry')
            seen = set()
            for comparison in comparisons:
                if not isinstance(comparison, dict):
                    raise ValueError('invalid qualitative comparison')
                entry_id = comparison.get('entry_id')
                if not isinstance(entry_id, str) or entry_id in seen:
                    raise ValueError('duplicate or invalid comparison entry')
                seen.add(entry_id)
                pixel_comparison = next((item for item in pixel_comparisons
                                         if item['entry_id'] == entry_id), None)
                if pixel_comparison and pixel_comparison['perceptual_near_duplicate']:
                    if comparison.get('near_match_override') is not True:
                        raise ValueError('near match requires explicit visual override')
                    _text(comparison.get('near_match_explanation'), 'near match explanation', 2000)
                changed = comparison.get('changed_dimensions')
                if not isinstance(changed, list) or not changed or any(
                        dimension not in DIMENSIONS for dimension in changed):
                    raise ValueError('explicit host-reviewed visual differences required')
                for dimension in DIMENSIONS:
                    _text(comparison.get(dimension), dimension, 2000)
            if seen != {entry['entry_id'] for entry in history}:
                raise ValueError('history comparison binding mismatch')
        except ValueError as exc:
            return ['invalid_host_visual_review:' + str(exc)]
        return []

    def record_review(self, image_path, *, reference_person, visual_review, peer_image_paths=None):
        """Persist an allowed, bound host review. No publication side effect."""
        peers = list(peer_image_paths or [])
        with self.actions._transaction():
            assessment = self.assess(image_path, reference_person=reference_person,
                                     visual_review=visual_review, peer_image_paths=peers)
            if not assessment['allowed']:
                raise ValueError('cover visual review blocked: ' + '; '.join(assessment['reasons']))
            record = {'review_id': uuid.uuid4().hex, 'reference_person': reference_person,
                      'peer_image_paths': [str(Path(p).expanduser().resolve()) for p in peers],
                      'visual_review': visual_review, 'assessment': assessment}
            self.actions._admit(record)
            self.actions.db.execute('INSERT INTO cover_visual_reviews VALUES (?,?,?,?)',
                                    (record['review_id'], self.actions.account, self.actions.clock(), _json(record)))
        return record

    def verify_review(self, review_id, image_path, *, reference_person, peer_image_paths=None):
        """Re-read pixels and current histories; stale reviews fail closed.

        Call immediately before the host's publication gate. This checks evidence,
        not host authority, and cannot prevent files changing after it returns.
        """
        _text(review_id, 'review_id', 128)
        self.actions._check_storage()
        row = self.actions.db.execute('SELECT review_json FROM cover_visual_reviews WHERE review_id=? AND account=?',
                                      (review_id, self.actions.account)).fetchone()
        if row is None:
            raise ValueError('unknown cover visual review')
        record = json.loads(row[0])
        peers = record['peer_image_paths'] if peer_image_paths is None else list(peer_image_paths)
        current = self.assess(image_path, reference_person=reference_person,
                              visual_review=record['visual_review'], peer_image_paths=peers)
        if current['candidate']['path'] != record['assessment']['candidate']['path']:
            current['allowed'] = False
            current['reasons'].append('review_candidate_path_changed')
        current['review_id'] = review_id
        return current
