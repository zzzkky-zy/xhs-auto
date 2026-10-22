"""Local-only reuse of the original batch/content-review pipeline.

No browser, CDP, network, scheduler, model or media generator is invoked here.
File registrations prove order and bytes, never human consent or content quality.
The original operation/data envelope is retained. Completion and acceptance are
explicitly host-reported cloud records, never independent Go/platform receipts
or permission to act without the host checking the current user's authority.
"""
import contextlib
import copy
import datetime as dt
import fcntl
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import sqlite3
import stat
import time
from types import SimpleNamespace
import uuid
from zoneinfo import ZoneInfo

from continuous_ops import ContinuousOps, WRITES
from content_review import ContentReview, POSTS, digest, text
import content_requirements


SUPPORTED = frozenset({'configure', 'begin', 'update', 'schedule', 'review', 'status', 'maintain',
                       'cancel', 'finish', 'accept', 'repair'})
UNSUPPORTED = {}
MAX_JSON = 256 * 1024
MAX_ASSET = 64 * 1024 * 1024
FUTURE_POST_CTA = "⬇️下方加入用户群，用Answereq轻松陪跑面试\n\n（群内发放免费体验资格）\n\n关注我获取最新大厂岗位list（日更）。"
PUBLICATION_SETTINGS_FIELDS = frozenset({'is_original', 'group_chat', 'activity'})
PUBLICATION_REVISION_FIELDS = PUBLICATION_SETTINGS_FIELDS | {'schedule_at'}


def _publication_core(tool, payload):
    """Exact manuscript/other-fields identity, with local media bound by bytes."""
    return fingerprint(tool, {k: v for k, v in payload.items() if k not in PUBLICATION_REVISION_FIELDS})


def _publication_settings(settings):
    if (not isinstance(settings, dict) or set(settings) != PUBLICATION_SETTINGS_FIELDS
            or settings.get('is_original') is not False
            or any(not text(settings.get(k)) or settings[k] != settings[k].strip()
                   or len(settings[k]) > 1000 for k in ('group_chat', 'activity'))):
        raise ValueError('ERR_PUBLICATION_SETTINGS: exact false original flag and observed group/activity names required')
    return settings


def _json(value):
    """Validate bounded inert JSON without interpreting its prose as code."""
    count = [0]
    def check(item, depth=0):
        count[0] += 1
        if depth > 16 or count[0] > 10000:
            raise ValueError('ERR_CLOUD_BATCH_INPUT: JSON too complex')
        if isinstance(item, dict):
            for key, child in item.items():
                if not isinstance(key, str):
                    raise ValueError('ERR_CLOUD_BATCH_INPUT: string object keys required')
                if key.lower() in {'password', 'passwd', 'cookies', 'cookie', 'access_token', 'refresh_token', 'web_session', 'otp', 'sms_code'}:
                    raise ValueError('ERR_CLOUD_BATCH_INPUT: authentication material is not batch evidence')
                check(child, depth + 1)
        elif isinstance(item, list):
            for child in item:
                check(child, depth + 1)
        elif item is None or isinstance(item, str) or type(item) in (bool, int):
            pass
        elif type(item) is float and math.isfinite(item):
            pass
        else:
            raise ValueError('ERR_CLOUD_BATCH_INPUT: finite JSON values required')
    check(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf-8')) > MAX_JSON:
        raise ValueError('ERR_CLOUD_BATCH_INPUT: JSON too large')
    return raw


def _unlinked(path):
    if any(item.is_symlink() for item in (path, *path.parents)):
        raise ValueError('ERR_CLOUD_BATCH_PATH: linked paths are not managed')


def _directory(path):
    _unlinked(path)
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    if not path.is_dir() or stat.S_IMODE(path.stat().st_mode) & 0o077:
        raise ValueError('ERR_CLOUD_BATCH_PATH: private directory (0700) required')
    return path


def _private_file(path):
    _unlinked(path)
    fd = os.open(path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK, 0o600)
    try:
        info = os.fstat(fd)
        if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('ERR_CLOUD_BATCH_PATH: private regular file (0600) required')
    finally:
        os.close(fd)


def _asset(path):
    path = Path(path)
    if not path.is_absolute():
        raise ValueError('ERR_CONTENT_ARTIFACT: absolute local media required')
    _unlinked(path)
    try:
        fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'rb') as source:
            before = os.fstat(source.fileno())
            if not stat.S_ISREG(before.st_mode) or not 0 < before.st_size <= MAX_ASSET:
                raise ValueError('ERR_CONTENT_ARTIFACT: media must be a regular file of 1 byte to 64 MiB')
            h = hashlib.sha256()
            remaining = before.st_size
            while remaining:
                block = source.read(min(1024 * 1024, remaining))
                if not block:
                    raise ValueError('ERR_ASSET_CHANGED: media shortened while hashing')
                h.update(block)
                remaining -= len(block)
            if source.read(1):
                raise ValueError('ERR_ASSET_CHANGED: media grew while hashing')
            after = os.fstat(source.fileno())
            if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns):
                raise ValueError('ERR_ASSET_CHANGED: media changed while hashing')
            return {'path': str(path), 'size': before.st_size, 'sha256': h.hexdigest()}
    except OSError as error:
        raise ValueError('ERR_CONTENT_ARTIFACT: local media unavailable') from error


def fingerprint(tool, payload):
    """Same content fingerprint as xhs_ops, without importing its executor."""
    clean = {k: v for k, v in payload.items() if k not in {'operation_id', 'xsec_token', 'run_id', 'approval_ref'}}
    if tool == 'search_feeds' and isinstance(clean.get('keyword'), str):
        clean['keyword'] = clean['keyword'].strip()
    for field in ('images', 'video'):
        if field not in clean:
            continue
        values = clean[field] if isinstance(clean[field], list) else [clean[field]]
        clean[field] = [value if isinstance(value, str) and value.startswith(('http://', 'https://'))
                        else 'sha256:' + _asset(value)['sha256'] for value in values]
    raw = json.dumps([tool, clean], ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    return hashlib.sha256(raw.encode('utf-8')).hexdigest()


class _LocalStore:
    """Minimal non-executing interface consumed by original ContinuousOps."""
    def __init__(self, config, db, clock):
        self.config, self.db, self.now = config, db, clock

    fingerprint = staticmethod(fingerprint)

    @contextlib.contextmanager
    def transaction(self):
        self.db.execute('BEGIN IMMEDIATE')
        try:
            yield
            self.db.commit()
        except BaseException:
            self.db.rollback()
            raise

    def require_resources(self, required_bytes=0):
        limits = self.config.resources
        state_bytes = sum(p.lstat().st_size for p in self.config.state_dir.iterdir() if p.is_file() and not p.is_symlink())
        locations = (self.config.state_dir, self.config.output_dir)
        if state_bytes >= limits['state_disk_limit_mb'] * 1024**2:
            raise ValueError('ERR_RESOURCE_BUDGET: state budget; preserve evidence')
        if min(shutil.disk_usage(p).free for p in locations) - required_bytes < limits['min_free_disk_mb'] * 1024**2:
            raise ValueError('ERR_RESOURCE_BUDGET: free disk reserve; preserve evidence')
        inodes = [v.f_favail for v in (os.statvfs(p) for p in locations) if v.f_files]
        if inodes and min(inodes) < limits['min_free_inodes']:
            raise ValueError('ERR_RESOURCE_BUDGET: free inode reserve; preserve evidence')


class _CloudContentReview(ContentReview):
    """Cloud-only, explicitly uncalibrated owner-instruction voice option."""
    def profile_for_run(self, run, required=True):
        version = run['body'].get('content_profile_version')
        if not version:
            return self.profile(required=required)
        row = self.db.execute('SELECT body FROM ops_profile_versions WHERE version=?', (version,)).fetchone()
        profile = json.loads(row[0]) if row else {}
        missing = self.profile_missing(profile)
        if not row or digest(profile) != version or (required and missing):
            raise ValueError('ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED: pinned run profile unavailable or incomplete')
        return version, profile, missing

    @staticmethod
    def future_policy(profile):
        policy = profile.get('future_content_policy')
        if policy is None:
            return None
        expected = {'version': 1, 'posts': 6, 'comments': 50, 'exact_person_covers': 3,
                    'post_cta': FUTURE_POST_CTA, 'topic_template': 'answereq',
                    'is_original': True, 'optional_content_declaration': 'none'}
        if isinstance(policy, dict) and policy.get('version') == 2:
            settings = {k: policy.get(k) for k in PUBLICATION_SETTINGS_FIELDS}
            _publication_settings(settings)
            expected.update(version=2, **settings)
        rules = profile.get('content_requirements', {})
        confirmations = profile.get('confirmation_sources', {})
        if (not isinstance(policy, dict) or policy != expected
                or any(type(policy.get(k)) is not int for k in ('version', 'posts', 'comments', 'exact_person_covers'))
                or type(policy.get('is_original')) is not bool
                or not isinstance(confirmations, dict) or not text(confirmations.get('future_content_policy'))
                or not isinstance(rules, dict) or rules.get('min_person_covers') != 3
                or rules.get('exact_person_covers') != 3 or rules.get('post_cta') != FUTURE_POST_CTA):
            raise ValueError('ERR_FUTURE_CONTENT_POLICY: confirmed six-post/fifty-comment/three-person-cover policy required')
        return policy

    @staticmethod
    def future_copy(profile, kind, final, payload):
        policy = _CloudContentReview.future_policy(profile)
        if not policy or kind == 'comment':
            return
        if (payload.get('topic_template') != policy['topic_template']
                or payload.get('is_original') is not policy['is_original']
                or (policy['version'] == 2 and any(payload.get(k) != policy[k] for k in ('group_chat', 'activity')))):
            raise ValueError('ERR_FUTURE_PUBLISH_FIELDS: exact confirmed topic, original, group and activity fields required')
        body, fields = payload.get('content', ''), final['fields']
        if fields.get('cta') != FUTURE_POST_CTA or not body.endswith(FUTURE_POST_CTA):
            raise ValueError('ERR_FUTURE_CTA: exact confirmed ending required')
        # Exclude the CTA from instructional/payoff evidence. No product mention
        # is required in the body. These records are inspectable judgements,
        # never an automatic claim of semantic quality or human consent.
        teaching = body[:-len(FUTURE_POST_CTA)]
        review = final.get('learning_review')
        def substantial(value):
            return isinstance(value, str) and len(value.strip()) >= 20
        def excerpt(record, source):
            return (isinstance(record, dict) and isinstance(record.get('excerpt'), str)
                    and len(record['excerpt'].strip()) >= 8 and record['excerpt'] in source
                    and substantial(record.get('explanation')))
        if (not isinstance(review, dict) or not text(review.get('reviewer'))
                or not text(review.get('review_source'))):
            raise ValueError('ERR_LEARNING_REVIEW: actual reviewer and review source required')
        for field in ('title', 'cover_text'):
            hook = review.get(field)
            actual = fields.get(field, '')
            if (not isinstance(hook, dict) or hook.get('text') != actual or not text(actual)
                    or hook.get('mechanism') not in {'curiosity', 'question', 'contrast', 'pain_point'}
                    or not substantial(hook.get('mechanism_explanation'))
                    or not substantial(hook.get('promise')) or not excerpt(hook.get('payoff'), teaching)):
                raise ValueError('ERR_HOOK_REVIEW: explain each actual title/cover hook and quote its body payoff')
        category = review.get('content_type')
        if category not in {'educational', 'self_introduction', 'other'}:
            raise ValueError('ERR_LEARNING_REVIEW: classify the actual content')
        if category == 'other':
            if not substantial(review.get('noneducational_reason')):
                raise ValueError('ERR_LEARNING_REVIEW: explain why instructional requirements do not apply')
            return
        example = review.get('worked_example')
        if (not isinstance(example, dict) or not all(excerpt(example.get(k), teaching)
                for k in ('problem', 'steps', 'result'))
                or len({example[k]['excerpt'] for k in ('problem', 'steps', 'result')}) != 3
                or not substantial(example.get('completeness_check'))):
            raise ValueError('ERR_WORKED_EXAMPLE: quote a complete problem, worked steps and result')
        reusable = review.get('copyable')
        if (not excerpt(reusable, teaching) or reusable.get('kind') not in {'template', 'formula', 'script'}
                or not substantial(reusable.get('usage_instructions'))):
            raise ValueError('ERR_COPYABLE: quote the usable template, formula or script and explain how to use it')
        if category == 'self_introduction':
            intro = review.get('self_introduction')
            if (not isinstance(intro, dict) or not excerpt(intro.get('fill_in_blank'), teaching)
                    or not excerpt(intro.get('filled_example'), teaching)
                    or intro['fill_in_blank']['excerpt'] == intro['filled_example']['excerpt']
                    or not substantial(intro.get('substitution_check'))):
                raise ValueError('ERR_SELF_INTRODUCTION: quote a fill-in template and its filled example')

    def entry(self, run, item, version, profile):
        # Settings-only revisions do not invent a fresh writing/editing history.
        # Project only compatible draft/edited version labels for the inherited
        # check; original stage rows, files, sequence IDs and digests stay intact.
        reviewer = copy.copy(self)
        def compatible_chain(run_id, content_id, through='final'):
            chain = self.chain(run_id, content_id, through)
            if through != 'final':
                return chain
            projected = copy.deepcopy(chain)
            for row in projected[1:-1]:
                stage_version = row['body']['profile_version']
                if self._settings_profile_compatible(run, stage_version, version):
                    row['body']['profile_version'] = version
            return projected
        reviewer.chain = compatible_chain
        result = ContentReview.entry(reviewer, run, item, version, profile)
        final = self.chain(run['id'], item['content_id'])[-1]['body']['value']
        self.future_copy(profile, result['kind'], final, item['payload'])
        return result

    def _settings_profile_compatible(self, run, original, current):
        if original == current:
            return True
        version = original
        for revision in run['body'].get('publication_settings_revisions', []):
            if revision.get('old_profile_version') != version:
                continue
            old, new = revision.get('previous_profile'), revision.get('profile')
            if (not isinstance(old, dict) or not isinstance(new, dict)
                    or digest(old) != version or digest(new) != revision.get('new_profile_version')):
                return False
            self.future_policy(old)
            self.future_policy(new)
            def fixed(value):
                value = copy.deepcopy(value)
                value.pop('future_content_policy', None)
                value.pop('publication_settings_evidence', None)
                value.get('confirmation_sources', {}).pop('future_content_policy', None)
                return value
            if fixed(old) != fixed(new):
                return False
            version = revision['new_profile_version']
            if version == current:
                return True
        return False

    def _settings_replaced_tasks(self, run, entries):
        """Exclude only an evidenced old fingerprint for the same exact post."""
        revisions = run['body'].get('publication_settings_revisions', [])
        if not revisions:
            return set()
        revision = revisions[-1]
        if revision.get('new_profile_version') != run['body'].get('content_profile_version'):
            return set()
        permitted = {action['operation_id']: action for action in revision.get('bound_actions', [])}
        replacements = set()
        for row in self.db.execute(
                "SELECT id,tool,payload FROM tasks WHERE run_id=? AND status NOT IN ('cancelled','failed')",
                (run['id'],)):
            action = permitted.get(row['id'])
            if not action or row['tool'] != 'publish_content':
                continue
            previous = action['binding']['payload']
            current = json.loads(row['payload'])
            if _publication_core(row['tool'], current) != _publication_core(row['tool'], previous):
                raise ValueError('ERR_PUBLICATION_REVISION: correlated manuscript changed')
            for entry in entries.values():
                if entry['tool'] != row['tool']:
                    continue
                final = self.chain(run['id'], entry['content_id'])[-1]
                payload = final['body']['value']['payload']
                if (final['body']['profile_version'] == revision['new_profile_version']
                        and entry['fingerprint'] == self.s.fingerprint(row['tool'], payload)
                        and all(payload.get(k) == v for k, v in revision['settings'].items())
                        and _publication_core(row['tool'], payload) == _publication_core(row['tool'], previous)):
                    replacements.add(row['id'])
        return replacements

    def _base_strategy(self, run, profile, entries):
        """Original strategy checks, with an explicit same-manuscript projection.

        This local view never changes task status, quota, receipts or base code.
        """
        content_requirements.check_batches(self, profile, entries)
        plan = run['body']['plan']
        comments = [e for e in entries.values() if e['kind'] == 'comment']
        posts = [e for e in entries.values() if e['kind'] in {'post', 'video'}]
        replaced = self._settings_replaced_tasks(run, entries)
        existing = self.db.execute(
            "SELECT id,tool,fingerprint FROM tasks WHERE run_id=? AND status NOT IN ('cancelled','failed')",
            (run['id'],)).fetchall()
        for key, items in (('comments', comments), ('posts', posts)):
            fingerprints = {e['fingerprint'] for e in items}
            fingerprints.update(r['fingerprint'] for r in existing if r['id'] not in replaced
                                and (r['tool'] in POSTS) == (key == 'posts')
                                and r['tool'] in POSTS | {'post_comment_to_feed', 'reply_comment_in_feed'})
            if fingerprints and (type(plan.get(key)) is not int or plan[key] < len(fingerprints)):
                raise ValueError('ERR_CONTENT_PLAN_SCOPE: ' + key)
        if not comments:
            return
        strategy, total = plan.get('comment_strategy'), plan['comments']
        if (not isinstance(strategy, dict) or set(strategy) != {'A', 'B', 'C'}
                or any(type(v) is not int or v < 0 for v in strategy.values()) or sum(strategy.values()) != total):
            raise ValueError('ERR_COMMENT_STRATEGY_REQUIRED: record the agreed batch counts')
        agreed = profile['strategy']
        if strategy['C'] and (not profile['allow_c'] or strategy['C'] * 100 > total * agreed['C_max']):
            raise ValueError('ERR_COMMENT_STRATEGY_SCOPE: C not allowed or above its cap')
        remainder = total - strategy['C']
        ratio = agreed.get('fallback', agreed) if not strategy['C'] else agreed
        target_a = remainder * ratio['A'] / (ratio['A'] + ratio['B'])
        if abs(strategy['A'] - target_a) > 1 or (total >= 2 and (not strategy['A'] or not strategy['B'])):
            if not text(plan.get('strategy_adjustment_ref')):
                raise ValueError('ERR_COMMENT_STRATEGY_CONFIRMATION_REQUIRED: retain the user’s A/B agreement')
        counts = {k: sum(e.get('comment_type') == k for e in comments) for k in ('A', 'B', 'C')}
        if any(counts[k] > strategy[k] for k in counts):
            raise ValueError('ERR_COMMENT_STRATEGY_SCOPE: reviewed items exceed agreed type counts')

    @staticmethod
    def owner_instruction_voice(profile):
        mode = profile.get('voice_mode')
        return (profile.get('voice_samples') == [] and isinstance(mode, dict)
                and mode.get('mode') == 'owner_instructions' and mode.get('calibrated') is False
                and all(isinstance(mode.get(key), str) and mode[key].strip()
                        for key in ('persona_quote', 'persona_source', 'no_review_quote', 'no_review_source'))
                and mode['no_review_quote'].strip() == '发布无需交给我审核。')

    def profile_missing(self, profile):
        missing = super().profile_missing(profile)
        if self.owner_instruction_voice(profile):
            missing = [key for key in missing if key != 'voice_samples']
        try:
            self._exact_person_covers(profile)
            self.future_policy(profile)
        except ValueError:
            if 'content_requirements' not in missing:
                missing.append('content_requirements')
        return missing

    @staticmethod
    def _exact_person_covers(profile):
        rules = profile.get('content_requirements', {})
        if not isinstance(rules, dict) or 'exact_person_covers' not in rules:
            return None
        exact, minimum = rules['exact_person_covers'], rules.get('min_person_covers')
        if (type(exact) is not int or not 1 <= exact <= 100
                or type(minimum) is not int or not 1 <= minimum <= exact):
            raise ValueError('ERR_PERSON_COVER_EXACT: exact count requires positive bounded counts and cover evidence')
        return exact

    def strategy(self, run, profile, entries):
        policy = self.future_policy(profile)
        if policy and any(run['body']['plan'].get(k) != policy[k] for k in ('posts', 'comments')):
            raise ValueError('ERR_FUTURE_BATCH_PLAN: future batches require six posts and fifty comments')
        exact = self._exact_person_covers(profile)
        self._base_strategy(run, profile, entries)
        if exact is None:
            return
        if any(entry['kind'] == 'video' for entry in entries.values()):
            raise ValueError('ERR_COVER_BATCH_INCOMPLETE: exact person-cover count requires evidenced image-post covers')
        groups = {}
        for entry in entries.values():
            if entry['kind'] == 'post':
                cover = entry['cover_review']  # Original checks already bind bytes and complete membership.
                groups.setdefault(cover['batch_id'], []).append(entry)
        if not groups:
            return  # A comment-only review makes no claim about the batch's covers.
        if len(groups) != 1 or len(next(iter(groups.values()))) != run['body']['plan'].get('posts'):
            raise ValueError('ERR_COVER_BATCH_INCOMPLETE: exact cover count requires the complete planned post batch')
        if sum(entry['cover_review']['kind'] == 'person' for entry in next(iter(groups.values()))) != exact:
            raise ValueError('ERR_PERSON_COVER_EXACT: complete batch does not match the configured exact person-cover count')
        if policy:
            ledger = getattr(self.o, 'action_ledger', None)
            if ledger is None:
                raise ValueError('ERR_COVER_HISTORY_REQUIRED: actual account cover history ledger required')
            from cloud_cover_history import CloudCoverHistory
            history = CloudCoverHistory(ledger)
            people = [entry['cover_review'] for entry in next(iter(groups.values()))
                      if entry['cover_review']['kind'] == 'person']
            submitted = {row['fingerprint'] for row in self.db.execute(
                'SELECT t.fingerprint FROM tasks t JOIN cloud_batch_bindings b ON b.operation_id=t.id '
                "WHERE t.run_id=? AND t.status IN ('host_reported_success','submitted_unknown')",
                (run['id'],))}
            for cover in people:
                entry = next(e for e in next(iter(groups.values())) if e['cover_review'] is cover)
                if entry['fingerprint'] in submitted:
                    # Historical covers stay counted; they are not new candidates.
                    # Unknown actions remain protected by the host ledger,
                    # never made retryable by this prospective comparison check.
                    continue
                review_id = cover.get('visual_history_review_id')
                if not text(review_id):
                    raise ValueError('ERR_COVER_HISTORY_REQUIRED: persist the actual cover comparison review first')
                peers = sorted(other['asset'] for other in people if other is not cover)
                checked = history.verify_review(review_id, cover['asset'],
                    reference_person='sha256:' + profile['face_reference']['sha256'], peer_image_paths=peers)
                if not checked['allowed']:
                    raise ValueError('ERR_COVER_HISTORY_REQUIRED: ' + '; '.join(checked['reasons']))



class _LocalContinuous(ContinuousOps):
    def __init__(self, store):
        super().__init__(store)
        self._migrate_cloud_calendar()
        self.content = _CloudContentReview(self)

    def _migrate_cloud_calendar(self):
        """Preserve reservations while allowing a narrowly scoped cross-run tie.

        Only the separate cloud database is migrated. The original local
        ContinuousOps calendar and its global uniqueness remain untouched.
        BEGIN IMMEDIATE makes the table swap all-or-nothing across host opens.
        """
        with self.s.transaction():
            unique = []
            for row in self.db.execute('PRAGMA index_list(ops_calendar)').fetchall():
                if row['unique']:
                    name = row['name'].replace('"', '""')
                    unique.append(tuple(item['name'] for item in self.db.execute(
                        'PRAGMA index_info("' + name + '")')))
            if unique == [('at',)]:
                columns = {row['name'] for row in self.db.execute('PRAGMA table_info(ops_calendar)')}
                if columns != {'run_id', 'at', 'schedule_at', 'task_id'}:
                    raise ValueError('ERR_CLOUD_CALENDAR_SCHEMA: preserve unrecognized calendar')
                self.db.execute('CREATE TABLE ops_calendar_cloud_v2 ('
                                'run_id TEXT NOT NULL, at REAL NOT NULL, schedule_at TEXT, '
                                'task_id TEXT, UNIQUE(run_id,at))')
                self.db.execute('INSERT INTO ops_calendar_cloud_v2(run_id,at,schedule_at,task_id) '
                                'SELECT run_id,at,schedule_at,task_id FROM ops_calendar')
                self.db.execute('DROP TABLE ops_calendar')
                self.db.execute('ALTER TABLE ops_calendar_cloud_v2 RENAME TO ops_calendar')
            elif unique != [('run_id', 'at')]:
                raise ValueError('ERR_CLOUD_CALENDAR_SCHEMA: preserve unrecognized calendar uniqueness')
            self.db.execute('CREATE INDEX IF NOT EXISTS cloud_calendar_at ON ops_calendar(at)')

    def _overlap_exception(self):
        evidence = self.c.data.get('cloud_schedule_overlap_exception')
        if evidence is None:
            return None
        # The bound ID must be the original first run, not an arbitrary run
        # chosen later to exempt two regular batches from collision checks.
        first = self.db.execute('SELECT id FROM ops_runs ORDER BY created,rowid LIMIT 1').fetchone()
        if first is None or first['id'] != evidence['first_run_id']:
            raise ValueError('ERR_SCHEDULE_EXCEPTION: first_run_id must identify the original first batch')
        return evidence

    @staticmethod
    def _schedule_kind(run_id, times, evidence):
        if len(times) != 10:
            return None
        if run_id == evidence['first_run_id']:
            return 'first_batch' if all(b - a == 5400 for a, b in zip(times, times[1:])) else None
        days = {}
        for at in times:
            local = dt.datetime.fromtimestamp(at, ZoneInfo('Asia/Shanghai'))
            days.setdefault(local.date(), []).append((local.hour, local.minute, local.second, local.microsecond))
        grid = [(9 + (90 * n) // 60, (90 * n) % 60, 0, 0) for n in range(10)]
        return 'regular_daily' if len(days) == 1 and next(iter(days.values())) == grid else None

    def _reserved_kind(self, run_id, evidence):
        run = self.run(run_id)
        times = [row['at'] for row in self.db.execute(
            'SELECT at FROM ops_calendar WHERE run_id=? ORDER BY at', (run_id,))]
        future_plan = run['body'].get('future_schedule_plan', {})
        if future_plan.get('kind') in {'future_ready_batch', 'future_preferred'} and set(times).issubset(future_plan.get('instants', [])):
            return future_plan['kind']
        kind = self._schedule_kind(run_id, times, evidence)
        if kind:
            return kind
        # Explicit local cancellation may leave a subset of a valid plan.
        # Keep its original classification without freeing another run's slot.
        planned = run['body'].get('cloud_schedule_plan', {})
        if planned.get('exception', {}).get('first_run_id') == evidence['first_run_id']:
            original = planned.get('instants', [])
            if set(times).issubset(original):
                return self._schedule_kind(run_id, original, evidence)
        return None

    @contextlib.contextmanager
    def lock(self):
        path = self.c.state_dir / 'operation-runs.lock'
        _private_file(path)
        fd = os.open(path, os.O_WRONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
        with os.fdopen(fd, 'a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            yield

    def check_owner(self, run_id):
        path = self.directory(run_id)
        _unlinked(path)
        _unlinked(path / '.owner.json')
        return super().check_owner(run_id)

    def configure(self, data):
        # Freeze every pre-existing run before changing the default. Preserve
        # legacy reviews on a profile-only edit; account/tool authorization
        # changes retain the original invalidation behavior.
        previous = self.policy() or {}
        with self.s.transaction():
            saved = []
            for row in self.db.execute('SELECT id FROM ops_runs').fetchall():
                run = self.run(row['id'])
                if not run['body'].get('content_profile_version'):
                    review = run['body'].get('review') or {}
                    version = review.get('profile_version') or previous.get('content_profile_version')
                    if version:
                        run['body']['content_profile_version'] = version
                        self.save(run)
                saved.append(run)
            result = super().configure(data)
            current = self.policy() or {}
            if ({k: v for k, v in previous.items() if k != 'content_profile_version'} ==
                    {k: v for k, v in current.items() if k != 'content_profile_version'}):
                for run in saved:
                    if run['body'].get('review'):
                        self.save(run)
            return result

    def _begin_successor(self, data):
        """Start an explicit independent batch after all predecessor posts succeed.

        The predecessor stays active for comments/unknowns. The caller's existing
        operation lock serializes repeated requests and makes successor_of unique.
        """
        prior_id = data.get('successor_of')
        prior = self.run(prior_id)
        for row in self.db.execute('SELECT id FROM ops_runs ORDER BY created,rowid').fetchall():
            candidate = self.run(row['id'])
            if candidate['body'].get('successor_of') == prior_id:
                return {'duplicate': True, **candidate}
        policy = self.policy()
        if not policy or policy['account_user_id'] != self.c.data.get('expected_user_id'):
            raise ValueError('ERR_ACCOUNT_UNCONFIRMED')
        version, profile, _ = self.content.profile()
        future = self.content.future_policy(profile)
        plan = data.get('plan')
        if (not future or not text(data.get('plan_ref')) or not isinstance(plan, dict)
                or any(type(plan.get(k)) is not int or plan[k] != future[k] for k in ('posts', 'comments'))):
            raise ValueError('ERR_FUTURE_BATCH_PLAN: successor requires confirmed six-post/fifty-comment profile and plan')
        if len(json.dumps(data).encode()) > 32768:
            raise ValueError('ERR_PLAN_TOO_LARGE')
        ledger = getattr(self, 'action_ledger', None)
        count = prior['body']['plan'].get('posts')
        rows = self.db.execute(
            'SELECT t.id,t.fingerprint,t.tool,b.binding_digest FROM tasks t '
            'JOIN cloud_batch_bindings b ON b.operation_id=t.id '
            "WHERE t.run_id=? AND t.tool IN ('publish_content','publish_with_video') "
            "AND t.status NOT IN ('cancelled','failed')", (prior_id,)).fetchall()
        if ledger is None or type(count) is not int or count < 1 or len(rows) != count:
            raise ValueError('ERR_SUCCESSOR_POSTS_PENDING: all planned predecessor posts need bound success receipts')
        fingerprints = set()
        for row in rows:
            receipt = ledger.get(row['id'])
            binding = receipt.get('binding', {})
            payload = binding.get('payload', {})
            if (receipt.get('status') != 'host_reported_success'
                    or receipt.get('binding_digest') != row['binding_digest']
                    or binding.get('operation_id') != row['id']
                    or binding.get('operation') != row['tool']
                    or binding.get('ledger_account') != self.c.account
                    or binding.get('expected_account') != self.c.data['expected_user_id']
                    or payload.get('run_id') != prior_id
                    or self.s.fingerprint(row['tool'], payload) != row['fingerprint']):
                raise ValueError('ERR_SUCCESSOR_POSTS_PENDING: exact predecessor success binding required')
            fingerprints.add(row['fingerprint'])
        if len(fingerprints) != count:
            raise ValueError('ERR_SUCCESSOR_POSTS_PENDING: distinct planned predecessor posts required')
        self.s.require_resources()
        if self.usage() >= self.c.data.get('retention', {}).get('managed_disk_limit_mb', 256) * 1024**2:
            raise ValueError('ERR_RESOURCE_BUDGET: preserve unresolved evidence')
        run_id = uuid.uuid4().hex
        path = self.directory(run_id)
        path.mkdir(parents=True, mode=0o700)
        (path / '.owner.json').write_text(json.dumps({'run_id': run_id, 'account': self.c.account, 'state_id': self.c.state_id}))
        (path / '.owner.json').chmod(0o600)
        for name in ('work', 'final', 'sealed'):
            (path / name).mkdir(mode=0o700)
        body = {'kind': 'host_reviewed' if policy['mode'] == 'host_accepted' else 'validation',
                'plan_ref': data['plan_ref'], 'plan': plan, 'items': {}, 'fresh_search_each_batch': True,
                'review': None, 'content_profile_version': version, 'successor_of': prior_id}
        self.db.execute('INSERT INTO ops_runs VALUES (?,?,?,?,?)',
                        (run_id, 'active', self.s.now(), self.s.now(), json.dumps(body, ensure_ascii=False)))
        return {'duplicate': False, 'directory': str(path), **self.run(run_id)}

    def begin(self, data):
        if 'successor_of' in data:
            return self._begin_successor(data)
        # A duplicate begin always returns the existing run and its old policy.
        if not self.status()['active_run']:
            version, profile, _ = self.content.profile(required=False)
            policy = self.content.future_policy(profile)
            plan = data.get('plan')
            if policy and (not isinstance(plan, dict) or any(type(plan.get(k)) is not int or
                              plan[k] != policy[k] for k in ('posts', 'comments'))):
                raise ValueError('ERR_FUTURE_BATCH_PLAN: future batches require six posts and fifty comments')
        value = super().begin(data)
        if not value['duplicate']:
            run = self.run(value['id'])
            version = self.policy().get('content_profile_version')
            if version:
                run['body']['content_profile_version'] = version
            if self.policy().get('mode') == 'host_accepted':
                run['body']['kind'] = 'host_reviewed'
            self.save(run)
            value = {**value, **self.run(value['id'])}
        return value

    def update(self, data):
        if isinstance(data.get('plan'), dict) and data.get('plan_ref'):
            run = self.run(data['run_id'])
            _, profile, _ = self.content.profile_for_run(run, required=False)
            policy = self.content.future_policy(profile)
            if policy and any(type(data['plan'].get(k)) is not int or
                              data['plan'][k] != policy[k] for k in ('posts', 'comments')):
                raise ValueError('ERR_FUTURE_BATCH_PLAN: future batches require six posts and fifty comments')
        return super().update(data)

    def _future_slots(self, run, evidence):
        """Plan from an evidenced conservative lower bound after actual readiness.

        The evidence is an inspectable host attestation, not proof of readiness
        or a platform receipt. No timezone or start instant is inferred from chat.
        """
        if (not isinstance(evidence, dict) or evidence.get('ready') is not True
                or evidence.get('mode') not in {'ready_batch', 'preferred'}
                or not all(text(evidence.get(k)) for k in ('readiness_ref', 'platform_evidence_ref'))):
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: actual readiness and platform bounds required')
        def parse(key):
            try:
                value = dt.datetime.fromisoformat(evidence[key].replace('Z', '+00:00'))
                if value.utcoffset() is None:
                    raise ValueError()
                return value.timestamp()
            except (KeyError, TypeError, AttributeError, ValueError, OverflowError):
                raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: timezone-aware ' + key) from None
        bound_kind=evidence.get('lower_bound_kind','observed_platform_bound')
        if bound_kind not in {'observed_platform_bound','conservative_candidate'}:
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: unknown lower-bound evidence kind')
        if bound_kind=='conservative_candidate' and not text(evidence.get('planning_lower_bound_ref')):
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: conservative candidate requires evidence and rationale')
        upper_kind=evidence.get('upper_bound_kind','observed_platform_bound')
        if upper_kind not in {'observed_platform_bound','calendar_date_window'}:
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: unknown upper-bound evidence kind')
        if upper_kind=='calendar_date_window' and not text(evidence.get('planning_upper_bound_ref')):
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: calendar date window requires explicit evidence limits')
        earliest, latest, observed = (parse(k) for k in ('platform_earliest', 'platform_latest', 'platform_observed_at'))
        now = self.s.now()
        if not now - 1800 <= observed <= now or not now < earliest <= latest:
            raise ValueError('ERR_FUTURE_SCHEDULE_EVIDENCE: fresh platform bounds must describe future times')
        latest = min(latest, now + 14 * 86400)
        zone = ZoneInfo('Asia/Shanghai')
        first = dt.datetime.fromtimestamp(earliest, zone)
        # Publish controls use minute precision; round up, never before earliest.
        if first.second or first.microsecond:
            first = first.replace(second=0, microsecond=0) + dt.timedelta(minutes=1)
        day = first.date()
        preferred = ((9, 0), (11, 0), (14, 0), (16, 0), (19, 0), (22, 0))
        exception=self._overlap_exception()
        excluded=exception['first_run_id'] if exception else None
        occupied=[r['at'] for r in self.db.execute(
            'SELECT c.at,c.run_id,t.status FROM ops_calendar c LEFT JOIN tasks t ON t.id=c.task_id')
            if r['run_id'] not in {run['id'],excluded} and r['status'] not in {'cancelled','failed'}]
        slots, cursor = [], first
        for hour, minute in preferred:
            if evidence['mode'] == 'preferred':
                cursor = max(cursor, dt.datetime.combine(day, dt.time(hour, minute), zone))
            opening = cursor.replace(hour=9, minute=0, second=0, microsecond=0)
            closing = cursor.replace(hour=23, minute=0, second=0, microsecond=0)
            if cursor < opening:
                cursor = opening
            if cursor > closing:
                cursor = opening + dt.timedelta(days=1)
            while True:
                current_day=cursor.date()
                same_day=[at for at in occupied if dt.datetime.fromtimestamp(at,zone).date()==current_day]
                if len(same_day)>=6:
                    cursor=dt.datetime.combine(current_day+dt.timedelta(days=1),dt.time(9),zone)
                    continue
                conflicts=[at for at in occupied if abs(at-cursor.timestamp())<5400]
                if conflicts:
                    cursor=dt.datetime.fromtimestamp(max(conflicts)+5400,zone)
                    if cursor.time()>dt.time(23):cursor=dt.datetime.combine(cursor.date()+dt.timedelta(days=1),dt.time(9),zone)
                    continue
                break
            if cursor.timestamp() > latest:
                raise ValueError('ERR_FUTURE_SCHEDULE_WINDOW: six posts do not fit observed platform bounds')
            occupied.append(cursor.timestamp())
            slots.append(cursor.isoformat())
            cursor += dt.timedelta(minutes=90)
        return slots

    def schedule(self, data):
        """Reserve exact cloud slots, or preserve the original random fallback.

        explicit_slots are absolute ISO-8601 instants with offsets. Existing
        reservations retain their original spelling. A replacement changes only
        unsubmitted reservations; every task-owned slot stays reserved until the
        existing cancellation workflow explicitly releases that task's slot.
        A private cloud_schedule_overlap_exception may permit only the original
        exact-90-minute first batch versus later Shanghai ten-slot daily grids.
        Its user quote/source are inert host evidence, never platform authority.
        """
        run = self.run(data['run_id'])
        _, profile, _ = self.content.profile_for_run(run, required=False)
        future = self.content.future_policy(profile)
        schedule_evidence = None
        if future:
            if run['body']['schedule_slots'] and not data.get('replace_unsubmitted'):
                if set(data) == {'run_id'} or ('future_schedule' in data and 'explicit_slots' not in data):
                    return {'schedule_slots': run['body']['schedule_slots'], 'reused': True,
                            'schedule_source': 'existing_calendar'}
            schedule_evidence = data.get('future_schedule')
            generated = self._future_slots(run, schedule_evidence)
            if 'explicit_slots' in data and data['explicit_slots'] != generated:
                raise ValueError('ERR_FUTURE_SCHEDULE_INPUT: slots must match observed readiness plan')
            data = {**data, 'explicit_slots': generated}
        spacing = 5400 if future else 3600
        if 'explicit_slots' not in data:
            if self._overlap_exception():
                run = self.run(data['run_id'])
                if set(data) == {'run_id'} and run['body']['schedule_slots']:
                    return {'schedule_slots': run['body']['schedule_slots'], 'reused': True,
                            'schedule_source': 'existing_calendar'}
                raise ValueError('ERR_SCHEDULE_EXCEPTION_EXPLICIT_REQUIRED: configured first/regular schedules require exact explicit slots')
            return super().schedule(data)
        values = data['explicit_slots']
        if not isinstance(values, list) or not 1 <= len(values) <= 100:
            raise ValueError('ERR_SCHEDULE_COUNT: explicit_slots must contain 1..100 timestamps')
        if 'replace_unsubmitted' in data and type(data['replace_unsubmitted']) is not bool:
            raise ValueError('ERR_SCHEDULE_INPUT: replace_unsubmitted must be a boolean')
        if 'count' in data and (type(data['count']) is not int or data['count'] != len(values)):
            raise ValueError('ERR_SCHEDULE_COUNT: count must match explicit_slots length')

        def instant(value):
            if (not isinstance(value, str) or len(value) > 64 or not re.fullmatch(
                    r'\d{4}-\d{2}-\d{2}T\d{2}:\d{2}(?::\d{2}(?:\.\d{1,6})?)?(?:Z|[+-]\d{2}:\d{2})', value)):
                raise ValueError('ERR_SCHEDULE_WINDOW: explicit slots require ISO timestamps with offsets')
            try:
                parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
                if parsed.utcoffset() is None:
                    raise ValueError()
                return parsed.timestamp()
            except (ValueError, OverflowError):
                raise ValueError('ERR_SCHEDULE_WINDOW: invalid explicit timestamp') from None

        times = [instant(value) for value in values]
        if len(set(times)) != len(times):
            raise ValueError('ERR_SCHEDULE_DUPLICATE: explicit slots must identify unique instants')
        if any(later <= earlier for earlier, later in zip(times, times[1:])):
            raise ValueError('ERR_SCHEDULE_ORDER: explicit slots must be strictly chronological')
        if any(later - earlier < spacing for earlier, later in zip(times, times[1:])):
            raise ValueError('ERR_SCHEDULE_SPACING: explicit slots must be at least one hour apart')
        if 'first_at' in data and instant(data['first_at']) != times[0]:
            raise ValueError('ERR_SCHEDULE_INPUT: first_at must match the first explicit slot')
        requested = dict(zip(times, values))
        replace = data.get('replace_unsubmitted') is True
        with self.s.transaction():
            run = self.run(data['run_id'])
            if run['state'] != 'active':
                raise ValueError('ERR_RUN_CLOSED')
            exception = self._overlap_exception()
            rows = self.db.execute(
                'SELECT c.at,c.schedule_at,c.task_id,t.status FROM ops_calendar c '
                'LEFT JOIN tasks t ON t.id=c.task_id WHERE c.run_id=? ORDER BY c.at',
                (run['id'],)).fetchall()
            existing = {row['at']: row for row in rows}
            owned = {row['at'] for row in rows if row['task_id'] is not None
                     and row['status'] not in {'cancelled', 'failed'}}
            if existing and not replace and set(existing) != set(requested):
                raise ValueError('ERR_SCHEDULE_EXISTS: explicit slots differ; use replace_unsubmitted to change the unsubmitted plan')
            desired = set(requested) | owned
            released = ({row['at'] for row in rows if row['task_id'] is not None
                         and row['status'] in {'cancelled', 'failed'}} if replace else set())
            removed = (set(existing) - desired) | released
            added = set(requested) - (set(existing) - released)
            all_times = sorted(desired)
            kind = ('future_' + schedule_evidence['mode']) if future else (self._schedule_kind(run['id'], all_times, exception) if exception else None)
            if future:
                zone=ZoneInfo('Asia/Shanghai');counts={}
                excluded=exception['first_run_id'] if exception else None
                dates=[r['at'] for r in self.db.execute('SELECT c.at,c.run_id,t.status FROM ops_calendar c LEFT JOIN tasks t ON t.id=c.task_id')
                    if r['run_id'] not in {run['id'],excluded} and r['status'] not in {'cancelled','failed'}]+all_times
                for at in dates:
                    day=dt.datetime.fromtimestamp(at,zone).date().isoformat();counts[day]=counts.get(day,0)+1
                affected={dt.datetime.fromtimestamp(at,zone).date().isoformat() for at in all_times}
                if any(counts[day]>6 for day in affected):raise ValueError('ERR_FUTURE_DAILY_CAP: carryover counts toward shared six-post day')
            if future and len(all_times) != 6:
                raise ValueError('ERR_FUTURE_SCHEDULE_COUNT: preserve exactly six slots including task-owned slots')
            if exception and not kind:
                raise ValueError('ERR_SCHEDULE_EXCEPTION_GRID: first batch requires exactly ten slots 90 minutes apart; '
                                 'regular batches require exactly ten Asia/Shanghai slots from 09:00 through 22:30 on one day')
            if not added and not removed:
                return {'schedule_slots': run['body']['schedule_slots'], 'new_slots': [],
                        'reused': True, 'schedule_source': 'explicit_slots'}
            now = self.s.now()
            lower_bound = now if future else now + 3600
            if any(not lower_bound <= at <= now + 14 * 86400 for at in added):
                raise ValueError('ERR_SCHEDULE_WINDOW: new slots must be 1 hour through 14 days ahead')
            if any(later - earlier < spacing for earlier, later in zip(all_times, all_times[1:])):
                raise ValueError('ERR_SCHEDULE_CONFLICT: preserve task-owned publications and their one-hour spacing')
            # Only unowned/cancelled/failed slots can be removed. The transaction
            # restores these rows and the review on any later collision/error.
            for at in removed:
                self.db.execute('DELETE FROM ops_calendar WHERE run_id=? AND at=?', (run['id'], at))
            skipped_runs = set()
            for at in sorted(added):
                collisions = self.db.execute('SELECT DISTINCT run_id FROM ops_calendar WHERE at>? AND at<?',
                                             (at - spacing, at + spacing)).fetchall()
                for row in collisions:
                    other = row['run_id']
                    if (exception and other != run['id']
                            and exception['first_run_id'] in {run['id'], other}
                            and ('first_batch' in {kind, self._reserved_kind(other, exception)})
                            and bool({kind, self._reserved_kind(other, exception)} & {'regular_daily', 'future_ready_batch', 'future_preferred'})):
                        skipped_runs.add(other)
                    else:
                        raise ValueError('ERR_SCHEDULE_CONFLICT: preserve already reserved publications')
            new_slots = [requested[at] for at in times if at in added]
            self.db.executemany('INSERT INTO ops_calendar(run_id,at,schedule_at) VALUES (?,?,?)',
                                [(run['id'], at, requested[at]) for at in times if at in added])
            if exception:
                run['body']['cloud_schedule_plan'] = {
                    'kind': kind, 'instants': all_times, 'exception': dict(exception),
                    'overlapped_run_ids': sorted(skipped_runs),
                    'evidence_boundary': 'host_schedule_evidence_only; no platform authorization'}
            if future:
                run['body']['future_schedule_plan'] = {'kind': kind, 'instants': all_times,
                    'evidence': schedule_evidence, 'timezone': 'Asia/Shanghai', 'minimum_interval_minutes': 90}
            run['body']['review'] = None
            self.save(run)
            return {'schedule_slots': self.run(run['id'])['body']['schedule_slots'],
                    'new_slots': new_slots, 'reused': False, 'schedule_source': 'explicit_slots'}

    def authorize(self, run_id, tool, payload, approval_ref):
        """Original review/sealing checks; references remain inert host input."""
        run = self.run(run_id)
        if run['state'] != 'active':
            raise ValueError('ERR_RUN_CLOSED')
        policy = self.policy()
        if policy['account_user_id'] != self.c.data['expected_user_id'] or tool not in policy['allowed_tools']:
            raise ValueError('ERR_POLICY_SCOPE')
        self.content.authorize(run, tool, payload)
        return self.seal_reviewed_payload(run, tool, payload, approval_ref)

    def seal_reviewed_payload(self, run, tool, payload, approval_ref):
        """Share byte sealing with detached upload review; never save a review."""
        if not approval_ref.strip():
            raise ValueError('ERR_APPROVAL_REQUIRED: supply actual user-source reference; host must independently check current authority')
        directory = self.check_owner(run['id'])
        sealed = _directory(directory / 'sealed')
        fixed = dict(payload)
        for field in ('images', 'video'):
            if field not in fixed:
                continue
            values = fixed[field] if isinstance(fixed[field], list) else [fixed[field]]
            copies = []
            for value in values:
                if not isinstance(value, str) or value.startswith(('http://', 'https://')):
                    raise ValueError('ERR_FIXED_ASSET_REQUIRED: materialize approved media on this host')
                manifest = _asset(value)
                target = sealed / (manifest['sha256'] + Path(value).suffix)
                _unlinked(target)
                if not target.exists():
                    if self.usage() + manifest['size'] > self.c.data.get('retention', {}).get('managed_disk_limit_mb', 256) * 1024**2:
                        raise ValueError('ERR_RESOURCE_BUDGET: preserve current batch assets')
                    self.s.require_resources(manifest['size'])
                    temporary = sealed / ('.pending-' + uuid.uuid4().hex)
                    fd = os.open(temporary, os.O_CREAT | os.O_EXCL | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
                    created = os.fstat(fd)
                    try:
                        with os.fdopen(fd, 'wb') as sink:
                            source_fd = os.open(value, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                            with os.fdopen(source_fd, 'rb') as source:
                                remaining = manifest['size']
                                while remaining:
                                    block = source.read(min(1024 * 1024, remaining))
                                    if not block:
                                        raise ValueError('ERR_ASSET_CHANGED: source shortened during sealing')
                                    sink.write(block)
                                    remaining -= len(block)
                                if source.read(1):
                                    raise ValueError('ERR_ASSET_CHANGED: source grew during sealing')
                            sink.flush()
                            os.fsync(sink.fileno())
                        if _asset(temporary)['sha256'] != manifest['sha256']:
                            raise ValueError('ERR_ASSET_CHANGED: sealed bytes differ; no action prepared')
                        temporary.chmod(0o400)
                        try:
                            # Link publishes complete bytes atomically without
                            # ever replacing an existing approved final asset.
                            os.link(temporary, target, follow_symlinks=False)
                        except FileExistsError:
                            if _asset(target)['sha256'] != manifest['sha256']:
                                raise ValueError('ERR_ASSET_CHANGED: existing sealed asset differs')
                        directory_fd = os.open(sealed, os.O_RDONLY | os.O_DIRECTORY | os.O_NOFOLLOW)
                        try:
                            os.fsync(directory_fd)
                        finally:
                            os.close(directory_fd)
                    finally:
                        # Only this attempt's temporary inode may be removed.
                        # A process crash can retain .pending-* for later local
                        # maintenance, but can never poison the final hash name.
                        try:
                            current = temporary.lstat()
                            if (current.st_dev, current.st_ino) == (created.st_dev, created.st_ino):
                                temporary.unlink()
                        except FileNotFoundError:
                            pass
                copies.append(str(target))
            fixed[field] = copies if isinstance(fixed[field], list) else copies[0]
        if self.s.fingerprint(tool, fixed) not in run['body']['review']['fingerprints']:
            raise ValueError('ERR_ASSET_CHANGED: sealed bytes no longer match review')
        return fixed, approval_ref

    def maintain(self):
        # Cloud action bindings may still refer to sealed paths. Retention is
        # intentionally conservative until those assets can be safely compacted.
        return {'removed_batch_directories': 0, 'managed_bytes': self.usage(),
                'deduplication_records_preserved': True, 'cleanup_performed': False,
                'reason': 'conservative_cloud_retention_preserves_sealed_media_and_audit_evidence'}


class CloudBatch:
    """Keep original manage_operation_run(operation, data) where implementable.

    action_ledger is an injected CloudActions object. Only .get() and the local
    pre-dispatch .cancel() are used; no dispatch method is ever invoked.
    prepare_payload returns reviewed sealed bytes; the caller still must enforce
    current user authority, host policy, browser identity and dispatch protocol.
    """
    def __init__(self, config, action_ledger=None, clock=None):
        self.path = Path(config).resolve()
        self.clock = clock or time.time
        self.action_ledger = action_ledger
        data = self._read_config()
        self.account = data['account_id']
        self.expected_user_id = data['expected_user_id']
        from cloud_quarantine import assert_not_quarantined
        assert_not_quarantined(self._resolve(data['state_dir']))
        state = _directory(self._resolve(data['state_dir']))
        output = _directory(self._resolve(data.get('output_dir', str(state / 'cloud-batch-output'))))
        database = state / 'cloud-batches.sqlite3'
        owned_root = output / 'operation-runs'
        _unlinked(owned_root)
        if ((not database.exists() or database.stat().st_size == 0)
                and owned_root.exists() and any(owned_root.iterdir())):
            raise ValueError('ERR_STATE_LOST: batch files exist without their ledger; preserve evidence and restore matching state')
        for suffix in ('', '-journal', '-wal', '-shm'):
            candidate = Path(str(database) + suffix)
            if candidate.exists() or candidate.is_symlink():
                _private_file(candidate)
        _private_file(database)
        _private_file(state / 'operation-runs.lock')
        self._storage_bindings = {}
        for item in (state, output, database, state / 'operation-runs.lock'):
            info = item.stat()
            self._storage_bindings[item] = (info.st_dev, info.st_ino)
        self.db = sqlite3.connect(database, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS cloud_batch_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS tasks (
                    id TEXT PRIMARY KEY, account TEXT NOT NULL, tool TEXT NOT NULL,
                    fingerprint TEXT NOT NULL, payload TEXT NOT NULL, status TEXT NOT NULL,
                    run_id TEXT, dependency TEXT, result TEXT, completed_at REAL NOT NULL DEFAULT 0);
                CREATE TABLE IF NOT EXISTS cloud_batch_bindings (
                    operation_id TEXT PRIMARY KEY, binding_digest TEXT NOT NULL);
                CREATE TABLE IF NOT EXISTS cloud_upload_inspection_reviews (
                    inspection_id TEXT PRIMARY KEY, run_id TEXT NOT NULL,
                    record_json TEXT NOT NULL, recorded_at REAL NOT NULL);
                CREATE TRIGGER IF NOT EXISTS cloud_upload_inspection_immutable_update
                    BEFORE UPDATE ON cloud_upload_inspection_reviews
                    BEGIN SELECT RAISE(ABORT, 'immutable upload inspection evidence'); END;
                CREATE TRIGGER IF NOT EXISTS cloud_upload_inspection_immutable_delete
                    BEFORE DELETE ON cloud_upload_inspection_reviews
                    BEGIN SELECT RAISE(ABORT, 'immutable upload inspection evidence'); END;
            ''')
            if 'host_status' not in {row[1] for row in self.db.execute('PRAGMA table_info(cloud_batch_bindings)')}:
                self.db.execute('ALTER TABLE cloud_batch_bindings ADD COLUMN host_status TEXT')
            for key, value in (('schema', '1'), ('account', self.account), ('expected_user_id', self.expected_user_id)):
                existing = self.db.execute('SELECT value FROM cloud_batch_meta WHERE key=?', (key,)).fetchone()
                if existing and existing[0] != value:
                    raise ValueError('ERR_CLOUD_BATCH_BINDING: preserve existing account/state')
                self.db.execute('INSERT OR IGNORE INTO cloud_batch_meta VALUES (?,?)', (key, value))
            self.db.execute('INSERT OR IGNORE INTO cloud_batch_meta VALUES (?,?)', ('state_id', uuid.uuid4().hex))
            state_id = self.db.execute("SELECT value FROM cloud_batch_meta WHERE key='state_id'").fetchone()[0]
            resources = {'state_disk_limit_mb': 512, 'min_free_disk_mb': 2048, 'min_free_inodes': 1024}
            resources.update({k: v for k, v in data.get('resources', {}).items() if k in resources})
            if any(type(v) is not int or v < 1 for v in resources.values()):
                raise ValueError('ERR_RESOURCE_BUDGET: positive integer local resource limits required')
            retention = data.get('retention', {})
            budget = retention.get('managed_disk_limit_mb', 256)
            if type(budget) is not int or not 32 <= budget <= 4096:
                raise ValueError('ERR_RESOURCE_BUDGET: managed_disk_limit_mb must be 32..4096')
            self.config = SimpleNamespace(data=data, account=self.account, state_id=state_id,
                                          state_dir=state, output_dir=output, resources=resources)
            self.store = _LocalStore(self.config, self.db, self.clock)
            self.continuous = _LocalContinuous(self.store)
            self.continuous.action_ledger = action_ledger
        except BaseException:
            self.db.close()
            raise

    def _resolve(self, value):
        if not isinstance(value, str) or not value.strip():
            raise ValueError('ERR_CLOUD_BATCH_PATH: path required')
        path = Path(value).expanduser()
        return path if path.is_absolute() else self.path.parent / path

    def _read_config(self):
        data = json.loads(self.path.read_text())
        if not isinstance(data, dict) or data.get('schema_version') != 1 or data.get('browser_mode') != 'cloud_host':
            raise ValueError('ERR_BROWSER_MODE: cloud batch requires explicit cloud_host config')
        if not isinstance(data.get('account_id'), str) or not re.fullmatch('[A-Za-z0-9_-]{1,64}', data['account_id']):
            raise ValueError('ERR_ACCOUNT_UNCONFIRMED: account_id required')
        if not isinstance(data.get('expected_user_id'), str) or not data['expected_user_id'].strip():
            raise ValueError('ERR_ACCOUNT_UNCONFIRMED: expected_user_id required; configuration is not login proof')
        if any(not isinstance(data.get(key, {}), dict) for key in ('resources', 'retention')):
            raise ValueError('ERR_CLOUD_BATCH_INPUT: resources and retention must be objects')
        if 'cloud_schedule_overlap_exception' in data:
            evidence = data['cloud_schedule_overlap_exception']
            if (not isinstance(evidence, dict) or set(evidence) != {'first_run_id', 'user_source', 'user_quote'}
                    or not isinstance(evidence.get('first_run_id'), str)
                    or not re.fullmatch('[0-9a-f]{32}', evidence['first_run_id'])
                    or any(not isinstance(evidence.get(key), str) or not evidence[key].strip()
                           or len(evidence[key]) > maximum
                           for key, maximum in (('user_source', 512), ('user_quote', 4096)))):
                raise ValueError('ERR_SCHEDULE_EXCEPTION: exact first_run_id, user_source and user_quote required')
        _json(data)
        if hasattr(self, 'account') and (data['account_id'] != self.account or data['expected_user_id'] != self.expected_user_id):
            raise ValueError('ERR_ACCOUNT_CHANGED: preserve original batch/account binding')
        return data

    def close(self):
        self.db.close()

    def _guard(self):
        data = self._read_config()
        from cloud_quarantine import assert_not_quarantined
        assert_not_quarantined(self._resolve(data['state_dir']))
        if hasattr(self, 'config'):
            for key in ('state_dir', 'output_dir'):
                if data.get(key) != self.config.data.get(key):
                    raise ValueError('ERR_CLOUD_BATCH_BINDING: storage configuration changed; reopen explicitly')
        for path, identity in self._storage_bindings.items():
            _unlinked(path)
            try:
                info = path.stat()
            except OSError as error:
                raise ValueError('ERR_STATE_LOST: original batch storage disappeared') from error
            if (info.st_dev, info.st_ino) != identity or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('ERR_CLOUD_BATCH_BINDING: storage identity or private permissions changed')
        _unlinked(self.config.output_dir / 'operation-runs')
        # Read the live exception on every operation: removal must immediately
        # restore cross-batch checks for all newly requested reservations.
        self.config.data.pop('cloud_schedule_overlap_exception', None)
        if 'cloud_schedule_overlap_exception' in data:
            self.config.data['cloud_schedule_overlap_exception'] = data['cloud_schedule_overlap_exception']

    def capabilities(self):
        return {'supported_local_operations': sorted(SUPPORTED),
                'unsupported_operations': dict(UNSUPPORTED), 'generates_content': False,
                'browser_executor': False, 'automatic_execution': False,
                'cleanup': 'inspection_only_retains_all_batch_evidence',
                'platform_receipts': 'bound_host_reports_not_independently_verified',
                'completion': 'host_reported_complete_with_exact_action_bindings',
                'upload_inspection': 'separate_immutable_preschedule_content_review; upload_only_never_publish_authority',
                'scheduling': 'explicit_offset_slots_or_original_random_fallback; task_owned_slots_preserved; '
                              'optional_bound_first_batch_overlap_with_regular_shanghai_daily_grid_only',
                'acceptance': 'bound_host_report_never_automatic_execution_authority',
                'repair': 'actual_local_hash_check_with_host_reported_test_results; no Go/CDP activation',
                'limitations': ['No content generation or media synthesis.',
                                'Maintenance retains all batch assets; automatic deletion/compaction is not migrated.',
                                'Image-free long-article review remains limited by the original nonempty page evidence contract.',
                                'No platform cancellation, autonomous worker or inferred external authorization.']}

    def dispatch(self, operation, data=None):
        self._guard()
        if not isinstance(operation, str):
            raise ValueError('ERR_RUN_ACTION: operation must be a string')
        if operation in UNSUPPORTED:
            raise ValueError('ERR_CLOUD_BATCH_UNSUPPORTED: ' + operation + ': ' + UNSUPPORTED[operation])
        if operation not in SUPPORTED:
            raise ValueError('ERR_RUN_ACTION: unknown batch operation')
        if data is None:
            data = {}
        if not isinstance(data, dict):
            raise ValueError('ERR_CLOUD_BATCH_INPUT: data object required')
        clean = json.loads(_json(data))
        if operation != 'status':
            self.store.require_resources()
        if operation in {'cancel', 'finish', 'accept', 'repair', 'review', 'schedule'}:
            with self.continuous.lock():
                if operation in {'review', 'schedule'}:
                    self._refresh_action_statuses(clean['run_id'])
                    value = getattr(self.continuous, operation)(clean)
                else:
                    value = getattr(self, '_' + operation)(clean)
        else:
            value = self.continuous.dispatch(operation, clean)
        if operation in {'status', 'configure'}:
            _, profile, _ = self.continuous.content.profile(required=False)
            if self.continuous.content.owner_instruction_voice(profile):
                value.update(voice_mode='owner_instructions', voice_calibrated=False,
                             voice_evidence='owner_instruction_quotes_only; no calibrated samples and no automatic execution authority')
        return {**value, 'transport': 'cloud_host', 'execution': 'local_only',
                'browser_action_performed': False, 'platform_success_verified': False,
                'cloud_batch_capabilities': self.capabilities()}

    def _linked_action(self, run_id, operation_id):
        if self.action_ledger is None:
            raise ValueError('ERR_CLOUD_BATCH_ACTION: injected action ledger required')
        row = self.db.execute('SELECT t.*,b.binding_digest FROM tasks t JOIN cloud_batch_bindings b ON b.operation_id=t.id WHERE t.id=? AND t.run_id=?', (operation_id, run_id)).fetchone()
        if row is None:
            raise ValueError('ERR_CLOUD_BATCH_BINDING: action is not correlated with this run')
        value = self.action_ledger.get(operation_id)
        binding = value['binding']
        if (value['binding_digest'] != row['binding_digest'] or binding.get('ledger_account') != self.account
                or binding.get('expected_account') != self.expected_user_id or binding.get('payload', {}).get('run_id') != run_id
                or binding.get('operation') != row['tool']):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: original action binding differs')
        return value

    def _unsubmitted_settings_action(self, value):
        """Verify ledger facts only; actual retained-editor checks belong to host."""
        ledger = self.action_ledger
        if (ledger is None or value.get('binding', {}).get('operation') != 'publish_content'
                or value.get('status') != 'submitted_unknown' or value.get('result') is not None
                or not text(value.get('attempt_id')) or not text(value.get('claim_browser_context'))
                or value.get('binding', {}).get('ledger_account') != self.account
                or value.get('binding', {}).get('expected_account') != self.expected_user_id):
            raise ValueError('ERR_PUBLICATION_REVISION: same retained unreported image-publication attempt required')
        if ledger.has_unresolved_final_send(value['operation_id']):
            raise ValueError('ERR_PUBLICATION_REVISION: final Send consumed; preserve uncertainty')
        if ledger.db.execute(
                "SELECT 1 FROM action_events WHERE account=? AND operation_id=? "
                "AND event='publication_final_step_permitted'",
                (ledger.account, value['operation_id'])).fetchone():
            raise ValueError('ERR_PUBLICATION_REVISION: final submission permit already issued')

    def revise_publication_settings(self, run_id, settings, user_source, user_quote, evidence_ref):
        """Version current-six and future settings; preserve all prior evidence.

        No browser action, final-review assertion or user permission is inferred.
        Bound calendar ownership may be released only for unchanged unreported
        attempts that have NEVER received a final publication permit.
        """
        self._guard()
        settings = json.loads(_json(_publication_settings(settings)))
        evidence = {'user_source': user_source, 'user_quote': user_quote, 'evidence_ref': evidence_ref}
        if any(not text(evidence[k]) or len(evidence[k]) > limit
               for k, limit in (('user_source', 500), ('user_quote', 8000), ('evidence_ref', 1000))):
            raise ValueError('ERR_PUBLICATION_REVISION: full bounded user source, quote and evidence required')
        _json(evidence)
        if self.action_ledger is None:
            raise ValueError('ERR_CLOUD_BATCH_ACTION: injected action ledger required')
        self.store.require_resources()
        with self.continuous.lock(), self.action_ledger._transaction(), self.store.transaction():
            run = self.continuous.run(run_id)
            if run['state'] != 'active':
                raise ValueError('ERR_RUN_CLOSED')
            old_version, old_profile, _ = self.continuous.content.profile_for_run(run)
            old_policy = self.continuous.content.future_policy(old_profile)
            if (not old_policy or any(run['body']['plan'].get(k) != v
                                     for k, v in (('posts', 6), ('comments', 50)))):
                raise ValueError('ERR_PUBLICATION_REVISION: only the current six-post/fifty-comment batch may be revised')
            prior = run['body'].get('publication_settings_revisions', [])
            default_version, default_profile, _ = self.continuous.content.profile()
            if (prior and prior[-1]['settings'] == settings and prior[-1]['evidence'] == evidence
                    and old_version == prior[-1]['new_profile_version']
                    and default_version == prior[-1]['new_default_profile_version']):
                return {'run_id': run_id, 'revision_id': prior[-1]['revision_id'], 'duplicate': True,
                        'content_profile_version': old_version, 'future_profile_version': default_version,
                        'browser_action_performed': False, 'authorization_granted': False}
            if not self.continuous.content.future_policy(default_profile):
                raise ValueError('ERR_PUBLICATION_REVISION: confirmed future policy required')
            actions = []
            for row in self.db.execute(
                    "SELECT id FROM tasks WHERE run_id=? AND tool IN ('publish_content','publish_with_video')",
                    (run_id,)).fetchall():
                value = self._linked_action(run_id, row['id'])
                self._unsubmitted_settings_action(value)
                actions.append(value)
            calendar = [dict(row) for row in self.db.execute(
                'SELECT run_id,at,schedule_at,task_id FROM ops_calendar WHERE run_id=? ORDER BY at', (run_id,))]
            bound_ids = {value['operation_id'] for value in actions}
            if any(row['task_id'] and row['task_id'] not in bound_ids for row in calendar):
                raise ValueError('ERR_PUBLICATION_REVISION: every owned calendar slot needs an eligible bound action')
            def revised(profile):
                profile = copy.deepcopy(profile)
                profile['future_content_policy'].update(version=2, **settings)
                profile['confirmation_sources']['future_content_policy'] = user_source
                profile['publication_settings_evidence'] = dict(evidence)
                version, missing = self.continuous.content.save_profile(profile)
                if missing:
                    raise ValueError('ERR_CONTENT_PROFILE_CONFIRMATION_REQUIRED: ' + ','.join(missing))
                return version, profile
            new_version, new_profile = revised(old_profile)
            new_default_version, new_default_profile = revised(default_profile)
            revision = {
                'revision_id': uuid.uuid4().hex, 'recorded_at': self.clock(), 'settings': settings,
                'evidence': evidence, 'old_profile_version': old_version,
                'new_profile_version': new_version, 'previous_profile': old_profile, 'profile': new_profile,
                'old_default_profile_version': default_version, 'new_default_profile_version': new_default_version,
                'previous_default_profile': default_profile, 'default_profile': new_default_profile,
                'previous_review': run['body'].get('review'), 'previous_calendar': calendar,
                'previous_schedule_plan': run['body'].get('future_schedule_plan'),
                'bound_actions': actions,
                'evidence_boundary': 'host evidence only; no final review, browser action or inferred authorization'}
            # Freeze any legacy unpinned runs before changing the future default.
            for row in self.db.execute('SELECT id FROM ops_runs WHERE id!=?', (run_id,)).fetchall():
                other = self.continuous.run(row['id'])
                if not other['body'].get('content_profile_version'):
                    other['body']['content_profile_version'] = (
                        (other['body'].get('review') or {}).get('profile_version') or default_version)
                    self.continuous.save(other)
            policy = self.continuous.policy()
            policy['content_profile_version'] = new_default_version
            self.continuous.save_policy(policy)
            run['body'].setdefault('publication_settings_revisions', []).append(revision)
            run['body']['content_profile_version'] = new_version
            run['body']['review'] = None
            for value in actions:
                self.db.execute('UPDATE ops_calendar SET task_id=NULL WHERE run_id=? AND task_id=?',
                                (run_id, value['operation_id']))
            self.continuous.save(run)
        return {'run_id': run_id, 'revision_id': revision['revision_id'], 'duplicate': False,
                'content_profile_version': new_version, 'future_profile_version': new_default_version,
                'settings': settings, 'requires_final_reregistration': True,
                'released_calendar_ownership': sorted(bound_ids),
                'browser_action_performed': False, 'authorization_granted': False}

    def sync_publication_settings_revision(self, action):
        """Project one ledger-owned immutable revision after whole-batch review.

        A cross-database interruption is recoverable by replaying this exact sync.
        Full original/current action bindings remain in ledger and run history.
        """
        self._guard()
        if not isinstance(action, dict) or not text(action.get('operation_id')) or self.action_ledger is None:
            raise ValueError('ERR_PUBLICATION_REVISION: exact ledger action required')
        operation_id = action['operation_id']
        with self.continuous.lock(), self.action_ledger._transaction(), self.store.transaction():
            current = self.action_ledger.get(operation_id)
            if current != action:
                raise ValueError('ERR_PUBLICATION_REVISION: supplied action is not the current ledger value')
            self._unsubmitted_settings_action(current)
            revisions = current.get('binding_revisions', [])
            if not revisions:
                raise ValueError('ERR_PUBLICATION_REVISION: append-only ledger binding revision required')
            changed = revisions[-1]
            binding = current['binding']
            run_id, payload = binding.get('payload', {}).get('run_id'), binding.get('payload', {})
            row = self.db.execute(
                'SELECT t.*,b.binding_digest FROM tasks t JOIN cloud_batch_bindings b ON b.operation_id=t.id '
                'WHERE t.id=? AND t.run_id=?', (operation_id, run_id)).fetchone()
            if row is None:
                raise ValueError('ERR_CLOUD_BATCH_BINDING: existing correlated action required')
            run = self.continuous.run(run_id)
            history = run['body'].get('publication_settings_revisions', [])
            if run['state'] != 'active' or not history:
                raise ValueError('ERR_PUBLICATION_REVISION: active settings revision required')
            revision = history[-1]
            old, new = changed.get('old_binding'), changed.get('new_binding')
            original = next((value for value in revision.get('bound_actions', [])
                             if value['operation_id'] == operation_id), None)
            if (not original or not isinstance(old, dict) or new != binding
                    or changed.get('old_binding_digest') != digest(old)
                    or changed.get('new_binding_digest') != digest(new)
                    or current['binding_digest'] != changed['new_binding_digest']
                    or old != original['binding'] or changed['old_binding_digest'] != original['binding_digest']
                    or current['attempt_id'] != original['attempt_id']
                    or current['claim_browser_context'] != original['claim_browser_context']
                    or _publication_core('publish_content', old['payload']) != _publication_core('publish_content', payload)
                    or any(payload.get(k) != v for k, v in revision['settings'].items())
                    or any(changed.get('attestation', {}).get(k) != v for k, v in revision['evidence'].items())):
                raise ValueError('ERR_PUBLICATION_REVISION: exact prior binding, manuscript and user evidence required')
            if row['binding_digest'] == current['binding_digest']:
                if json.loads(row['payload']) != payload:
                    raise ValueError('ERR_CLOUD_BATCH_BINDING: revised task projection differs')
                return {'operation_id': operation_id, 'run_id': run_id, 'duplicate': True,
                        'binding_digest': current['binding_digest'], 'browser_action_performed': False}
            if (row['binding_digest'] != changed['old_binding_digest']
                    or json.loads(row['payload']) != old['payload']):
                raise ValueError('ERR_CLOUD_BATCH_BINDING: original projected task binding differs')
            self.continuous.content.authorize(run, 'publish_content', payload)
            self.continuous.bind_slot(run_id, 'publish_content', payload, operation_id)
            self.db.execute('UPDATE tasks SET payload=?,fingerprint=? WHERE id=?',
                            (_json(payload), fingerprint('publish_content', payload), operation_id))
            self.db.execute('UPDATE cloud_batch_bindings SET binding_digest=? WHERE operation_id=?',
                            (current['binding_digest'], operation_id))
            self._sync_action_projection(current)
            run['body'].setdefault('publication_settings_action_syncs', []).append({
                'operation_id': operation_id, 'settings_revision_id': revision['revision_id'],
                'ledger_revision': changed, 'recorded_at': self.clock()})
            self.continuous.save(run)
        return {'operation_id': operation_id, 'run_id': run_id, 'duplicate': False,
                'binding_digest': current['binding_digest'], 'browser_action_performed': False,
                'platform_success_verified': False}

    @staticmethod
    def _project_host_status(value):
        """Free quota only for a bound, definite no-application receipt.

        The immutable CloudActions result remains untouched. The original local
        quota/calendar code understands failed/cancelled, so not_applied is
        projected to failed only in this batch's local tasks view.
        """
        if value['status'] != 'not_applied':
            return value['status']
        report = value.get('result')
        if (not isinstance(report, dict) or report.get('status') != 'not_applied'
                or report.get('evidence_kind') not in {'host_no_submit', 'platform_rejection', 'confirmed_not_applied'}
                or report.get('operation_id') != value['operation_id']
                or report.get('binding_digest') != value['binding_digest']):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: definite not_applied requires its original bound outcome evidence')
        return 'failed'

    def _sync_action_projection(self, value):
        projected = self._project_host_status(value)
        self.db.execute('UPDATE tasks SET status=?,result=? WHERE id=?',
                        (projected, _json(value.get('result')), value['operation_id']))
        self.db.execute('UPDATE cloud_batch_bindings SET host_status=? WHERE operation_id=?',
                        (value['status'], value['operation_id']))
        return projected

    def _refresh_action_statuses(self, run_id):
        self.continuous.run(run_id)
        ids = [row[0] for row in self.db.execute('SELECT id FROM tasks WHERE run_id=?', (run_id,))]
        if not ids:
            return
        values = [self._linked_action(run_id, operation_id) for operation_id in ids]
        with self.store.transaction():
            for value in values:
                self._sync_action_projection(value)

    def _cancel(self, data):
        run_id, task_id = data['run_id'], data['task_id']
        if self.continuous.run(run_id)['state'] != 'active':
            raise ValueError('ERR_RUN_CLOSED')
        current = self._linked_action(run_id, task_id)
        if current['status'] == 'cancelled':
            value = current
        else:
            reason = data.get('reason', 'Explicit local batch task cancellation before host dispatch')
            value = self.action_ledger.cancel(task_id, reason)
        with self.store.transaction():
            self.db.execute("UPDATE tasks SET status='cancelled',completed_at=? WHERE id=?", (self.clock(), task_id))
            self.db.execute("UPDATE cloud_batch_bindings SET host_status='cancelled' WHERE operation_id=?", (task_id,))
            self.db.execute('DELETE FROM ops_calendar WHERE task_id=?', (task_id,))
        return {'task_id': task_id, 'status': value['status'], 'platform_cancellation_performed': False}

    def _completion(self, run_id):
        receipts = []
        for row in self.db.execute('SELECT id,tool FROM tasks WHERE run_id=? ORDER BY id', (run_id,)).fetchall():
            value = self._linked_action(run_id, row['id'])
            if value['status'] not in {'host_reported_success', 'not_applied', 'cancelled'}:
                raise ValueError('ERR_RUN_UNRESOLVED: retain ' + value['status'] + ' action and all evidence')
            self._sync_action_projection(value)
            receipts.append({'operation_id': row['id'], 'tool': row['tool'], 'binding_digest': value['binding_digest'],
                             'status': value['status'], 'result_digest': hashlib.sha256(_json(value.get('result')).encode()).hexdigest()})
        return receipts

    def _finish(self, data):
        run = self.continuous.run(data['run_id'])
        if run['state'] not in {'active', 'awaiting_acceptance', 'completed', 'abandoned'}:
            raise ValueError('ERR_RUN_CLOSED')
        receipts = self._completion(run['id'])
        if not data.get('abandon') and (not receipts or not run['body'].get('review')):
            raise ValueError('ERR_RUN_INCOMPLETE: actual correlated terminal host actions and local review required')
        digest = hashlib.sha256(_json(receipts).encode()).hexdigest()
        old = run['body'].get('cloud_completion')
        if old and old['completion_digest'] != digest:
            raise ValueError('ERR_CLOUD_BATCH_BINDING: completion evidence changed')
        if old:
            return {'run_id': run['id'], 'state': run['state'], **old, 'reused': True}
        run['state'] = 'abandoned' if data.get('abandon') is True else 'awaiting_acceptance' if run['body']['kind'] == 'validation' else 'completed'
        completion = {'status': 'host_reported_complete', 'completion_digest': digest, 'recorded_at': self.clock(),
                      'receipts': receipts, 'outcomes': {s: sum(r['status'] == s for r in receipts) for s in ('host_reported_success', 'not_applied', 'cancelled')},
                      'provenance': 'bound_host_action_reports; not independent platform verification'}
        run['body']['cloud_completion'] = completion
        self.continuous.save(run)
        return {'run_id': run['id'], 'state': run['state'], **completion, 'cleanup': self.continuous.maintain()}

    def _accept(self, data):
        run, policy = self.continuous.run(data['run_id']), self.continuous.policy()
        report = data.get('host_acceptance')
        required = {'run_id', 'account_id', 'completion_digest', 'user_source', 'user_quote', 'accepted_at', 'evidence_ref'}
        if (not isinstance(data.get('human_acceptance_ref'), str) or not data['human_acceptance_ref'].strip()
                or not isinstance(report, dict) or set(report) != required):
            raise ValueError('ERR_HUMAN_ACCEPTANCE_REQUIRED: human_acceptance_ref plus exact bound host_acceptance record required')
        completion = run['body'].get('cloud_completion')
        if (not completion or report['run_id'] != run['id'] or report['account_id'] != self.expected_user_id
                or report['completion_digest'] != completion['completion_digest']
                or any(not isinstance(report[k], str) or not report[k].strip() for k in required)):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: acceptance must identify this account and exact completion')
        try:
            at = dt.datetime.fromisoformat(report['accepted_at'].replace('Z', '+00:00'))
            valid_time = at.tzinfo is not None and completion['recorded_at'] <= at.timestamp() <= self.clock() + 5
        except (ValueError, OverflowError):
            valid_time = False
        if not valid_time:
            raise ValueError('ERR_HUMAN_ACCEPTANCE_REQUIRED: actual dated acceptance after completion required')
        if run['state'] == 'completed' and policy.get('cloud_host_acceptance') == report:
            return {**self.continuous.status(), 'acceptance_recorded': True, 'reused': True, 'automatic_authority_granted': False}
        if run['state'] != 'awaiting_acceptance':
            raise ValueError('ERR_HUMAN_ACCEPTANCE_REQUIRED: run is not awaiting first-batch acceptance')
        receipts = self._completion(run['id'])
        if hashlib.sha256(_json(receipts).encode()).hexdigest() != completion['completion_digest']:
            raise ValueError('ERR_CLOUD_BATCH_BINDING: acceptance completion changed')
        done = {r['tool'] for r in receipts if r['status'] == 'host_reported_success'}
        for family in ({'publish_content', 'publish_with_video'}, {'post_comment_to_feed', 'reply_comment_in_feed'}):
            if set(policy['allowed_tools']) & family and not done & family:
                raise ValueError('ERR_VALIDATION_INCOMPLETE: actual host-reported success required in each enabled workflow family')
        policy.update(mode='host_accepted', acceptance_run=run['id'], human_acceptance_ref=data['human_acceptance_ref'], cloud_host_acceptance=report)
        self.continuous.save_policy(policy)
        run['state'] = 'completed'
        self.continuous.save(run)
        return {**self.continuous.status(), 'acceptance_recorded': True, 'provenance': 'host_reported_user_acceptance',
                'automatic_authority_granted': False, 'host_must_check_each_action_authority': True,
                'cleanup': self.continuous.maintain()}

    def _repair(self, data):
        required = ('failure', 'change', 'verification', 'before_version', 'after_version', 'rollback_ref', 'skill_update')
        if any(not isinstance(data.get(k), str) or not data[k].strip() for k in required):
            raise ValueError('ERR_REPAIR_EVIDENCE_REQUIRED: original repair description fields required')
        report_path = Path(data.get('verification_report', ''))
        _unlinked(report_path)
        if not report_path.is_absolute() or not report_path.is_file() or report_path.stat().st_size > 128 * 1024:
            raise ValueError('ERR_REPAIR_VERIFICATION_REQUIRED: bounded local verification_report required')
        report = json.loads(report_path.read_text())
        if not isinstance(report, dict):
            raise ValueError('ERR_REPAIR_VERIFICATION_REQUIRED: verification report must be a JSON object')
        _json(report)
        root = Path(__file__).resolve().parents[1]
        actual = {p.name: hashlib.sha256(p.read_bytes()).hexdigest() for p in sorted((root / 'runtime').glob('*.py'))}
        version = (root / 'VERSION').read_text().strip()
        records = report.get('records')
        if (data['before_version'] == data['after_version'] or data['after_version'] != version
                or report.get('version') != version or report.get('status') != 'passed'
                or report.get('runtime_files_sha256') != actual or not isinstance(records, list) or not records
                or any(not isinstance(r, dict) or type(r.get('exit_code')) is not int or r['exit_code'] != 0 for r in records)):
            raise ValueError('ERR_REPAIR_VERIFICATION_REQUIRED: report must match current runtime hashes/version and passing check records')
        repair_id = uuid.uuid4().hex
        body = {k: data[k][:2000] for k in required}
        body.update(verification_report_sha256=hashlib.sha256(report_path.read_bytes()).hexdigest(),
                    provenance='local_file_hashes_verified; test_outcomes_host_reported; no Go/CDP or production activation')
        self.db.execute('INSERT INTO ops_repairs VALUES (?,?,?)', (repair_id, self.clock(), _json(body)))
        self.db.execute('DELETE FROM ops_repairs WHERE id NOT IN (SELECT id FROM ops_repairs ORDER BY at DESC LIMIT 20)')
        return {'repair_id': repair_id, 'verified_version': version, 'local_hashes_checked': True,
                'production_activated': False, 'verification_boundary': body['provenance']}

    def _upload_inspection_record(self, run_id, entries, evidence):
        """Build detached content evidence under the caller's batch lock.

        Only ContinuousOps.review's calendar prerequisite is inapplicable here.
        The underlying content review, stage chain, complete cover batch,
        profile, strategy, quota and byte checks are reused without substitution.
        """
        run = self.continuous.run(run_id)
        if run['state'] != 'active':
            raise ValueError('ERR_RUN_CLOSED')
        self.continuous.check_owner(run_id)
        policy = self.continuous.policy()
        if policy['account_user_id'] != self.expected_user_id or 'publish_content' not in policy['allowed_tools']:
            raise ValueError('ERR_POLICY_SCOPE')
        count = run['body']['plan'].get('posts')
        if type(count) is not int or not 1 <= count <= 100 or not isinstance(entries, list) or len(entries) != count:
            raise ValueError('ERR_UPLOAD_INSPECTION_BATCH: inspect the complete planned post batch')
        if isinstance(evidence, dict):
            evidence = _json(evidence)
        if not isinstance(evidence, str) or not evidence.strip() or len(evidence) > 4000:
            raise ValueError('ERR_QUALITY_REVIEW_REQUIRED: actual bounded cross-item inspection evidence required')
        clean = json.loads(_json(entries))
        ids = set()
        for item in clean:
            if (not isinstance(item, dict) or set(item) != {'content_id', 'tool', 'payload'}
                    or item['tool'] != 'publish_content' or not isinstance(item['payload'], dict)
                    or not isinstance(item['content_id'], str) or not item['content_id'].strip()
                    or item['content_id'] in ids):
                raise ValueError('ERR_UPLOAD_INSPECTION_BATCH: distinct registered image-post entries required')
            ids.add(item['content_id'])
            payload = item['payload']
            if 'schedule_at' in payload:
                raise ValueError('ERR_UPLOAD_INSPECTION_SCHEDULE: no invented or final publish time in upload-only review')
            images = payload.get('images')
            if not isinstance(images, list) or not 1 <= len(images) <= 5 or any(not isinstance(path, str) for path in images):
                raise ValueError('ERR_CLOUD_BATCH_MEDIA: each inspected post requires one to five final images')
            if len(images) != len(set(images)):
                raise ValueError('ERR_CLOUD_BATCH_MEDIA: duplicate image paths are not inspection evidence')
        clean.sort(key=lambda item: item['content_id'])
        detached = {**run, 'body': {**run['body'], 'review': None}}
        review = self.continuous.content.review(detached, clean, evidence)
        review.pop('registered_at', None)  # Durable row records time; exact content retries stay idempotent.
        return {'schema_version': 1, 'purpose': 'upload_inspection_only', 'run_id': run_id,
                'account': self.account, 'expected_user_id': self.expected_user_id,
                'items': clean, 'review': review}, detached

    def review_upload_inspection(self, run_id, entries, evidence):
        """Review unscheduled final content only, without publication authority."""
        self._guard(); self.store.require_resources()
        with self.continuous.lock():
            self._refresh_action_statuses(run_id)
            record, _ = self._upload_inspection_record(run_id, entries, evidence)
            raw = _json(record); inspection_id = hashlib.sha256(raw.encode()).hexdigest()
            with self.store.transaction():
                old = self.db.execute('SELECT record_json FROM cloud_upload_inspection_reviews WHERE inspection_id=?',
                                      (inspection_id,)).fetchone()
                if old and old['record_json'] != raw:
                    raise ValueError('ERR_UPLOAD_INSPECTION_BINDING: immutable inspection differs')
                self.db.execute('INSERT OR IGNORE INTO cloud_upload_inspection_reviews VALUES (?,?,?,?)',
                                (inspection_id, run_id, raw, self.clock()))
        return {'inspection_id': inspection_id, 'run_id': run_id,
                'review_digest': hashlib.sha256(_json(record['review']).encode()).hexdigest(),
                'reviewed_payloads': len(record['items']), 'duplicate': old is not None,
                'purpose': 'upload_inspection_only', 'authorization_granted': False,
                'publishing_authorized': False, 'schedule_reserved': False, 'browser_action_performed': False,
                'evidence_boundary': 'registered content and unchanged bytes; host must verify actual upload authority'}

    def prepare_upload_inspection(self, run_id, payload, approval_ref='', *, inspection_id):
        """Revalidate one immutable complete-batch review and seal selected images.

        The returned payload is for inspect_publication_schedule only. It cannot
        satisfy the normal publication review or reserve a publishing slot.
        """
        self._guard(); self.store.require_resources()
        if not isinstance(inspection_id, str) or not re.fullmatch('[0-9a-f]{64}', inspection_id):
            raise ValueError('ERR_UPLOAD_INSPECTION_BINDING: exact inspection_id required')
        if not isinstance(payload, dict):
            raise ValueError('ERR_UPLOAD_INSPECTION_BINDING: exact reviewed payload required')
        clean = json.loads(_json(payload))
        if clean.get('run_id') not in (None, '', run_id):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: payload run_id differs')
        clean.pop('run_id', None)
        supplied_ref = clean.pop('approval_ref', '')
        if not isinstance(approval_ref, str) or not isinstance(supplied_ref, str):
            raise ValueError('ERR_APPROVAL_REQUIRED: approval reference must be a string')
        if approval_ref and supplied_ref and approval_ref != supplied_ref:
            raise ValueError('ERR_CLOUD_BATCH_BINDING: approval references differ')
        reference = approval_ref or supplied_ref
        if not reference.strip() or len(reference) > 5000:
            raise ValueError('ERR_APPROVAL_REQUIRED: actual bounded user-source reference required')
        if 'schedule_at' in clean:
            raise ValueError('ERR_UPLOAD_INSPECTION_SCHEDULE: inspection payload cannot include a publish time')
        with self.continuous.lock():
            self._refresh_action_statuses(run_id)
            row = self.db.execute('SELECT record_json FROM cloud_upload_inspection_reviews WHERE inspection_id=? AND run_id=?',
                                  (inspection_id, run_id)).fetchone()
            if row is None or hashlib.sha256(row['record_json'].encode()).hexdigest() != inspection_id:
                raise ValueError('ERR_UPLOAD_INSPECTION_BINDING: missing or changed inspection evidence')
            stored = json.loads(row['record_json'])
            current, detached = self._upload_inspection_record(run_id, stored['items'], stored['review']['evidence'])
            if _json(current) != row['record_json']:
                raise ValueError('ERR_UPLOAD_INSPECTION_STALE: profile, plan, stages or final content changed')
            detached['body']['review'] = current['review']
            self.continuous.content.authorize(detached, 'publish_content', clean)
            fixed, reference = self.continuous.seal_reviewed_payload(detached, 'publish_content', clean, reference)
            manifest = [_asset(path) for path in fixed['images']]
        return {'inspection_id': inspection_id, 'run_id': run_id, 'tool': 'inspect_publication_schedule',
                'review_digest': hashlib.sha256(_json(current['review']).encode()).hexdigest(),
                'payload': {**fixed, 'run_id': run_id}, 'approval_ref': reference, 'media_manifest': manifest,
                'purpose': 'upload_inspection_only', 'status': 'reviewed_upload_inspection_only',
                'host_action_required': True, 'authorization_granted': False, 'publishing_authorized': False,
                'schedule_reserved': False, 'browser_action_performed': False, 'platform_success_verified': False}

    def prepare_payload(self, run_id, tool, payload, approval_ref=''):
        """Validate review and seal local media; never prepare/claim an action."""
        self._guard()
        if tool not in WRITES or not isinstance(payload, dict):
            raise ValueError('ERR_CLOUD_BATCH_TOOL: only the four original content writes are reviewed')
        clean = json.loads(_json(payload))
        if clean.get('run_id') not in (None, '', run_id):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: payload run_id differs')
        clean.pop('run_id', None)
        supplied_ref = clean.pop('approval_ref', '')
        if not isinstance(approval_ref, str) or not isinstance(supplied_ref, str):
            raise ValueError('ERR_APPROVAL_REQUIRED: approval reference must be a string')
        if approval_ref and supplied_ref and approval_ref != supplied_ref:
            raise ValueError('ERR_CLOUD_BATCH_BINDING: approval references differ')
        approval_ref = approval_ref or supplied_ref
        if tool == 'publish_content' and len(clean.get('images', [])) > 5:
            raise ValueError('ERR_CLOUD_BATCH_MEDIA: original operational limit is five image pages')
        with self.continuous.lock():
            self._refresh_action_statuses(run_id)
            fixed, reference = self.continuous.authorize(run_id, tool, clean, approval_ref)
        paths = fixed.get('images', []) if tool == 'publish_content' else [fixed['video']] if tool == 'publish_with_video' else []
        manifest = [_asset(path) for path in paths]
        return {'run_id': run_id, 'tool': tool, 'payload': {**fixed, 'run_id': run_id},
                'approval_ref': reference, 'media_manifest': manifest,
                'status': 'reviewed_local_payload_only', 'host_action_required': True,
                'authorization_granted': False, 'platform_success_verified': False,
                'evidence_boundary': 'original file/hash review; real authority and execution remain with host'}

    def projection_status(self, run_id):
        """Read authoritative linked receipts alongside possibly stale projections.

        No synchronization, outcome reconciliation, quota release or browser
        action occurs. The two databases are not an atomic cross-store snapshot.
        """
        self._guard()
        self.continuous.run(run_id)
        entries = []
        for row in self.db.execute(
                'SELECT t.id,t.status,t.result,b.host_status FROM tasks t '
                'LEFT JOIN cloud_batch_bindings b ON b.operation_id=t.id WHERE t.run_id=? ORDER BY t.id',
                (run_id,)).fetchall():
            value = self._linked_action(run_id, row['id'])
            expected = self._project_host_status(value)
            receipt_matches = row['result'] == _json(value.get('result'))
            entries.append({'operation_id': row['id'], 'ledger_status': value['status'],
                            'stored_task_status': row['status'], 'stored_host_status': row['host_status'],
                            'expected_task_status': expected, 'receipt_matches': receipt_matches,
                            'projection_matches': row['status'] == expected and
                            row['host_status'] == value['status'] and receipt_matches})
        return {'run_id': run_id, 'entries': entries,
                'mismatch_count': sum(not entry['projection_matches'] for entry in entries),
                'authority': 'immutable_linked_action_ledger; host_reports_not_independent_platform_verification',
                'cross_store_atomic_snapshot': False, 'state_changed': False,
                'browser_action_performed': False, 'grants_execution_authority': False}

    def bind_action(self, run_id, operation_id):
        """Correlate one existing immutable host action; never infer success."""
        self._guard()
        if self.action_ledger is None:
            raise ValueError('ERR_CLOUD_BATCH_ACTION: injected action ledger required')
        value = self.action_ledger.get(operation_id)
        binding = value.get('binding', {})
        payload = binding.get('payload', {})
        if (binding.get('operation_id') != operation_id or binding.get('ledger_account') != self.account
                or binding.get('expected_account') != self.expected_user_id or payload.get('run_id') != run_id
                or binding.get('operation') not in WRITES):
            raise ValueError('ERR_CLOUD_BATCH_BINDING: exact action/account/run binding required')
        tool = binding['operation']
        with self.continuous.lock():
            run = self.continuous.run(run_id)
            if run['state'] != 'active':
                raise ValueError('ERR_RUN_CLOSED')
            self._refresh_action_statuses(run_id)
            self.continuous.content.authorize(run, tool, payload)
            old = self.db.execute('SELECT binding_digest FROM cloud_batch_bindings WHERE operation_id=?', (operation_id,)).fetchone()
            if old and old[0] != value['binding_digest']:
                raise ValueError('ERR_CLOUD_BATCH_BINDING: immutable host action changed')
            with self.store.transaction():
                if not old:
                    self.continuous.bind_slot(run_id, tool, payload, operation_id)
                    self.db.execute('INSERT INTO tasks(id,account,tool,fingerprint,payload,status,run_id,result) VALUES (?,?,?,?,?,?,?,?)',
                                    (operation_id, self.account, tool, fingerprint(tool, payload), _json(payload), self._project_host_status(value), run_id, _json(value.get('result'))))
                    self.db.execute('INSERT INTO cloud_batch_bindings(operation_id,binding_digest,host_status) VALUES (?,?,?)', (operation_id, value['binding_digest'], value['status']))
                else:
                    self._sync_action_projection(value)
        return {'operation_id': operation_id, 'run_id': run_id, 'host_status': value['status'],
                'batch_status': self._project_host_status(value),
                'status': 'host_action_correlated', 'platform_success_verified': False,
                'browser_action_performed': False, 'receipt_source': 'host_reported_unverified'}
