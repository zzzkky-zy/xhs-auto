#!/usr/bin/env python3
"""Durable host-mediated action ledger. This module NEVER operates a browser.

Trust boundary
--------------
The assistant host must independently enforce the user's permission, the tool
policy, and the actual browser account before EVERY external action. JSON in
this ledger is a host report, not an authorization credential or platform proof.
In particular, ``ready`` means that a bound host attestation was recorded; it
never grants Python, an MCP caller, or a background worker permission to act.
An optional injected ``host_verifier(phase, binding, evidence)`` may veto reports
and claims. It must return exactly True to accept, and must be a short, trusted,
non-browser callback. A CLI/MCP argument cannot install this callback.

Protocol: prepare -> request_authorization -> record_authorization; record a
fresh identity observation; claim a due action; begin_submit BEFORE the first
possibly consequential browser step; then report_result. begin_submit commits
``submitted_unknown`` before returning. Never replay an ambiguous submission.
Only pre-submission leases can be released/recovered. Reconciliation records
actual host evidence and cannot dispatch or requeue an action. There is no daemon.
Active mutations are serialized by expected platform account across aliases in
one ledger. The host must use one shared state directory per platform account;
separate ledger files cannot coordinate account-wide uncertainty or idempotency.
"""
import argparse
import contextlib
import datetime as dt
import hashlib
import json
import math
import os
from pathlib import Path
import re
import secrets
import sqlite3
import stat
import sys
import time
import unicodedata
import uuid
import urllib.parse
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError


ACTION_OPERATIONS = frozenset({
    'publish_content', 'publish_with_video', 'inspect_publication_schedule', 'post_comment_to_feed',
    'reply_comment_in_feed', 'like_feed', 'favorite_feed', 'like_notification',
    'reply_notification', 'reply_direct_message', 'list_notifications',
    'check_login_status', 'get_my_profile', 'get_unread_count',
    'list_direct_messages', 'list_feeds', 'search_feeds', 'get_feed_detail', 'user_profile',
})
READ_OPERATIONS = frozenset({'check_login_status', 'get_my_profile', 'get_unread_count',
                             'list_direct_messages', 'list_feeds', 'search_feeds',
                             'get_feed_detail', 'user_profile'})
IDENTITY_READS = frozenset({'get_my_profile', 'get_unread_count', 'list_direct_messages'})
SCHEDULE_INSPECTION = 'inspect_publication_schedule'
IMAGE_CONTINUATION_OPERATIONS = frozenset({'publish_content', SCHEDULE_INSPECTION})
MEDIA_OPERATIONS = frozenset({'publish_content', 'publish_with_video'})
PUBLICATION_REVISION_FIELDS = frozenset({'is_original', 'group_chat', 'activity', 'schedule_at'})
CONTENT_WRITES = MEDIA_OPERATIONS | {'post_comment_to_feed', 'reply_comment_in_feed'}
COMMENT_OPERATIONS = frozenset({'post_comment_to_feed', 'reply_comment_in_feed'})
STATES = ('prepared', 'awaiting_authorization', 'ready', 'dispatching',
          'submitted_unknown', 'host_reported_success', 'not_applied', 'cancelled')
TERMINAL = frozenset({'host_reported_success', 'not_applied', 'cancelled'})
MAX_JSON = 65536
MAX_LEDGER = 32 * 1024 * 1024
HOST_REPORT = 'host_reported_unverified; host policy is the sole execution authority'
FORBIDDEN_KEYS = frozenset({
    'approved', 'password', 'passwd', 'cookies', 'cookie',
    'authorization', 'access_token', 'refresh_token', 'web_session', 'otp',
    'sms_code', 'verification_code', 'code', 'script', 'javascript', 'shell',
    'command', 'evaluate', 'expression', 'exec', 'cdp',
})
SECRET_PATTERN = re.compile(r'(?i)(?:web_session|authorization|access_token|refresh_token|password)\s*[:=]\s*\S+')


def _text(value, name, limit=500, empty=False):
    if not isinstance(value, str) or len(value.encode('utf-8')) > limit or (not empty and not value.strip()):
        raise ValueError('invalid ' + name)
    if any(ord(c) < 32 and c not in '\r\n\t' for c in value):
        raise ValueError('control character in ' + name)
    if SECRET_PATTERN.search(value):
        raise ValueError('authentication material must not be recorded')
    return value


def _json(value, name='payload'):
    """Bound inert JSON, rejecting nonfinite numbers, secrets and code channels."""
    count = [0]
    def visit(item, depth=0):
        count[0] += 1
        if count[0] > 4096 or depth > 12:
            raise ValueError(name + ' too complex')
        if isinstance(item, dict):
            for key, child in item.items():
                _text(key, name + ' key', 128)
                if key.lower() in FORBIDDEN_KEYS:
                    raise ValueError('forbidden action field: ' + key)
                if key == 'approval_ref':
                    _text(child, 'inert approval_ref provenance', 1000, empty=True)
                visit(child, depth + 1)
        elif isinstance(item, list):
            if len(item) > 1000: raise ValueError(name + ' list too long')
            for child in item: visit(child, depth + 1)
        elif isinstance(item, str): _text(item, name, MAX_JSON, empty=True)
        elif item is None or type(item) is bool: pass
        elif type(item) is int:
            if abs(item) > 2 ** 53: raise ValueError(name + ' integer outside interoperable range')
        elif type(item) is float:
            if not math.isfinite(item): raise ValueError(name + ' must contain finite numbers')
        else: raise ValueError(name + ' must be JSON')
    visit(value)
    raw = json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)
    if len(raw.encode('utf-8')) > MAX_JSON: raise ValueError(name + ' too large')
    return raw


def _digest(value):
    return hashlib.sha256(_json(value, 'binding').encode('utf-8')).hexdigest()


def _semantic_digest(binding):
    """Content identity independent of transport IDs and provenance metadata."""
    operation = binding['operation']
    if operation not in CONTENT_WRITES:
        return None
    payload = {key: value for key, value in binding['payload'].items()
               if key not in {'operation_id', 'run_id', 'approval_ref', 'xsec_token'}}
    manifest = {asset['path']: asset['sha256'] for asset in binding.get('media_manifest', [])}
    for field in ('images', 'video'):
        if field in payload:
            if field == 'images' and not payload[field]:
                payload.pop(field)
                continue
            values = payload[field] if isinstance(payload[field], list) else [payload[field]]
            payload[field] = ['sha256:' + manifest[path] if isinstance(path, str) and path in manifest
                              else {'unbound_media': path} for path in values]
    for key in ('feed_id', 'comment_id', 'user_id'):
        if isinstance(payload.get(key), str) and re.fullmatch('[a-fA-F0-9]{24}', payload[key]):
            payload[key] = payload[key].lower()
        elif not payload.get(key):
            payload.pop(key, None)
    if payload.get('schedule_at'):
        payload['schedule_at'] = _iso(_timestamp(payload['schedule_at'], 'schedule_at'))
    else:
        payload.pop('schedule_at', None)
    if operation in MEDIA_OPERATIONS:
        for field in ('tags', 'products'):
            if not payload.get(field): payload.pop(field, None)
        if payload.get('is_original') is False: payload.pop('is_original', None)
        if payload.get('visibility') in (None, '', '公开可见'): payload.pop('visibility', None)
        for field in ('activity', 'topic_template', 'long_article_template'):
            if not payload.get(field): payload.pop(field, None)
        destination = {'own_account': binding['expected_account']}
    else:
        target = binding['target']
        url = urllib.parse.urlsplit(target.get('url', '') if isinstance(target.get('url', ''), str) else '')
        fallback_feed = target.get('feed_id') or (url.path.rsplit('/', 1)[-1] if '/explore/' in url.path else '')
        destination = {'feed_id': payload.get('feed_id', fallback_feed)}
        if operation == 'reply_comment_in_feed':
            destination.update(comment_id=payload.get('comment_id', target.get('comment_id', '')),
                               user_id=payload.get('user_id', target.get('user_id', '')))
        for key, value in destination.items():
            if isinstance(value, str) and re.fullmatch('[a-fA-F0-9]{24}', value):
                destination[key] = value.lower()
        for key in ('feed_id', 'comment_id', 'user_id'):
            payload.pop(key, None)
    return _digest({'expected_account': binding['expected_account'], 'operation': operation,
                    'destination': destination, 'payload': payload})


def _timestamp(value, name):
    if not isinstance(value, str) or len(value) > 64:
        raise ValueError(name + ' must be an ISO-8601 timestamp with timezone')
    try:
        parsed = dt.datetime.fromisoformat(value.replace('Z', '+00:00'))
        if parsed.tzinfo is None or parsed.utcoffset() is None: raise ValueError()
        return parsed.timestamp()
    except (ValueError, OverflowError):
        raise ValueError(name + ' must be an ISO-8601 timestamp with timezone') from None


def _iso(value):
    return dt.datetime.fromtimestamp(value, dt.timezone.utc).isoformat()


def _fields(value, required, name):
    if not isinstance(value, dict) or set(value) != set(required):
        raise ValueError('exact ' + name + ' fields required: ' + ', '.join(sorted(required)))
    _json(value, name)


def _identifier(value, name='operation_id'):
    if not isinstance(value, str) or not re.fullmatch(r'[A-Za-z0-9_-]{1,128}', value):
        raise ValueError('invalid ' + name)
    return value


class CloudActions:
    """Local queue and evidence only; inject a registry payload_validator in MCP.

    payload_validator(operation, payload) must raise on invalid input. It may
    not silently transform the payload; a defensive copy is supplied. The
    default validates only the closed operation vocabulary and bounded JSON.
    ``clock`` is an injectable UNIX-seconds function, for deterministic tests.
    """
    def __init__(self, config, *, host_verifier=None, operation_allowlist=None,
                 payload_validator=None, clock=None, identity_max_age=120):
        self.config = Path(config).resolve()
        self.host_verifier = host_verifier
        self.payload_validator = payload_validator
        if host_verifier is not None and not callable(host_verifier):
            raise ValueError('host_verifier must be an injected callable')
        if payload_validator is not None and not callable(payload_validator):
            raise ValueError('payload_validator must be an injected callable')
        self.operations = frozenset(ACTION_OPERATIONS if operation_allowlist is None else operation_allowlist)
        if not self.operations or any(not isinstance(x, str) or not re.fullmatch('[a-z][a-z0-9_]{0,63}', x) for x in self.operations):
            raise ValueError('invalid operation allowlist')
        self.clock = clock or time.time
        if type(identity_max_age) not in (int, float) or not 1 <= identity_max_age <= 600:
            raise ValueError('identity_max_age must be between 1 and 600 seconds')
        self.identity_max_age = identity_max_age
        data = self._config()
        self.account = data['account_id']
        directory = Path(data['state_dir']).expanduser()
        if not directory.is_absolute(): directory = self.config.parent / directory
        # Do not follow a linked directory (including an intermediate component).
        if any(p.is_symlink() for p in (directory, *directory.parents)):
            raise ValueError('refuse linked state directory')
        directory.mkdir(mode=0o700, parents=True, exist_ok=True)
        if not directory.is_dir() or stat.S_IMODE(directory.stat().st_mode) & 0o077:
            raise ValueError('state directory must be private (0700)')
        self.directory = Path(os.path.abspath(directory))
        self.path = self.directory / 'cloud-actions.sqlite3'
        for suffix in ('', '-journal', '-wal', '-shm'):
            candidate = Path(str(self.path) + suffix)
            if candidate.is_symlink(): raise ValueError('refuse linked cloud action ledger')
            if candidate.exists() and (not candidate.is_file() or stat.S_IMODE(candidate.stat().st_mode) & 0o077):
                raise ValueError('cloud action ledger files must be private (0600)')
        fd = os.open(self.path, os.O_CREAT | os.O_WRONLY | os.O_NOFOLLOW, 0o600)
        try:
            info = os.fstat(fd)
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('cloud action ledger must be a private regular file (0600)')
            self._file_identity = (info.st_dev, info.st_ino)
        finally: os.close(fd)
        self.db = sqlite3.connect(self.path, timeout=5, isolation_level=None)
        self.db.row_factory = sqlite3.Row
        try:
            self.db.execute('PRAGMA journal_mode=DELETE')
            self.db.execute('PRAGMA synchronous=FULL')
            self.db.execute('PRAGMA foreign_keys=ON')
            self.db.executescript('''
                CREATE TABLE IF NOT EXISTS action_meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);
                INSERT OR IGNORE INTO action_meta VALUES ('schema_version', '1');
                CREATE TABLE IF NOT EXISTS execution_control_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, expected_account TEXT NOT NULL,
                    ledger_account TEXT NOT NULL, paused INTEGER NOT NULL,
                    recorded_at REAL NOT NULL, detail TEXT NOT NULL);
                CREATE TRIGGER IF NOT EXISTS execution_control_immutable_update BEFORE UPDATE ON execution_control_events
                BEGIN SELECT RAISE(ABORT, 'immutable execution control evidence'); END;
                CREATE TRIGGER IF NOT EXISTS execution_control_immutable_delete BEFORE DELETE ON execution_control_events
                BEGIN SELECT RAISE(ABORT, 'immutable execution control evidence'); END;
                CREATE TABLE IF NOT EXISTS actions (
                    account TEXT NOT NULL, operation_id TEXT NOT NULL,
                    binding_json TEXT NOT NULL, binding_digest TEXT NOT NULL,
                    status TEXT NOT NULL, due_at REAL NOT NULL, created_at REAL NOT NULL,
                    updated_at REAL NOT NULL, authorization TEXT, identity TEXT,
                    attempt INTEGER NOT NULL DEFAULT 0, attempt_id TEXT,
                    owner TEXT, claim_hash TEXT, claim_context TEXT, lease_until REAL,
                    submitted_at REAL, result TEXT,
                    PRIMARY KEY(account, operation_id));
                CREATE INDEX IF NOT EXISTS action_due ON actions(account,status,due_at);
                CREATE TABLE IF NOT EXISTS action_events (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    operation_id TEXT NOT NULL, event TEXT NOT NULL,
                    recorded_at REAL NOT NULL, detail TEXT NOT NULL,
                    FOREIGN KEY(account, operation_id) REFERENCES actions(account, operation_id));
                CREATE TRIGGER IF NOT EXISTS action_immutable
                BEFORE UPDATE OF account,operation_id,binding_json,binding_digest,due_at,created_at ON actions
                BEGIN SELECT RAISE(ABORT, 'immutable action binding'); END;
                CREATE TRIGGER IF NOT EXISTS events_immutable_update BEFORE UPDATE ON action_events
                BEGIN SELECT RAISE(ABORT, 'immutable action evidence'); END;
                CREATE TRIGGER IF NOT EXISTS events_immutable_delete BEFORE DELETE ON action_events
                BEGIN SELECT RAISE(ABORT, 'immutable action evidence'); END;
                CREATE TABLE IF NOT EXISTS unknown_continuations (
                    account TEXT NOT NULL, operation_id TEXT NOT NULL,
                    blocked_operation_id TEXT NOT NULL, attestation TEXT NOT NULL,
                    recorded_at REAL NOT NULL, PRIMARY KEY(account,operation_id),
                    FOREIGN KEY(account,operation_id) REFERENCES actions(account,operation_id),
                    FOREIGN KEY(account,blocked_operation_id) REFERENCES actions(account,operation_id));
                CREATE TRIGGER IF NOT EXISTS continuation_immutable_update BEFORE UPDATE ON unknown_continuations
                BEGIN SELECT RAISE(ABORT, 'immutable continuation evidence'); END;
                CREATE TRIGGER IF NOT EXISTS continuation_immutable_delete BEFORE DELETE ON unknown_continuations
                BEGIN SELECT RAISE(ABORT, 'immutable continuation evidence'); END;
                CREATE TABLE IF NOT EXISTS publication_unknown_continuations (
                    account TEXT NOT NULL, operation_id TEXT NOT NULL,
                    blocked_operation_id TEXT NOT NULL, attestation TEXT NOT NULL,
                    recorded_at REAL NOT NULL, PRIMARY KEY(account,operation_id,blocked_operation_id),
                    FOREIGN KEY(account,operation_id) REFERENCES actions(account,operation_id),
                    FOREIGN KEY(account,blocked_operation_id) REFERENCES actions(account,operation_id));
                CREATE TRIGGER IF NOT EXISTS publication_continuation_immutable_update BEFORE UPDATE ON publication_unknown_continuations
                BEGIN SELECT RAISE(ABORT, 'immutable publication continuation evidence'); END;
                CREATE TRIGGER IF NOT EXISTS publication_continuation_immutable_delete BEFORE DELETE ON publication_unknown_continuations
                BEGIN SELECT RAISE(ABORT, 'immutable publication continuation evidence'); END;
                CREATE TABLE IF NOT EXISTS publication_binding_revisions (
                    id INTEGER PRIMARY KEY AUTOINCREMENT, account TEXT NOT NULL,
                    operation_id TEXT NOT NULL, attempt_id TEXT NOT NULL,
                    old_binding_json TEXT NOT NULL, old_binding_digest TEXT NOT NULL,
                    old_semantic_digest TEXT NOT NULL,
                    new_binding_json TEXT NOT NULL, new_binding_digest TEXT NOT NULL,
                    new_semantic_digest TEXT NOT NULL, attestation TEXT NOT NULL,
                    observation TEXT NOT NULL, continuation_rebindings TEXT NOT NULL,
                    recorded_at REAL NOT NULL,
                    UNIQUE(account,operation_id,new_binding_digest),
                    FOREIGN KEY(account,operation_id) REFERENCES actions(account,operation_id));
                CREATE TRIGGER IF NOT EXISTS publication_revision_immutable_update
                BEFORE UPDATE ON publication_binding_revisions
                BEGIN SELECT RAISE(ABORT, 'immutable publication binding revision'); END;
                CREATE TRIGGER IF NOT EXISTS publication_revision_immutable_delete
                BEFORE DELETE ON publication_binding_revisions
                BEGIN SELECT RAISE(ABORT, 'immutable publication binding revision'); END;
                CREATE TRIGGER IF NOT EXISTS publication_revision_scope
                BEFORE INSERT ON publication_binding_revisions
                WHEN NOT EXISTS (
                    SELECT 1 FROM actions a
                    WHERE a.account=NEW.account AND a.operation_id=NEW.operation_id
                    AND a.status='submitted_unknown' AND a.result IS NULL
                    AND a.attempt_id=NEW.attempt_id
                    AND NEW.old_binding_json=COALESCE(
                        (SELECT new_binding_json FROM publication_binding_revisions
                         WHERE account=NEW.account AND operation_id=NEW.operation_id ORDER BY id DESC LIMIT 1),
                        a.binding_json)
                    AND NEW.old_binding_digest=COALESCE(
                        (SELECT new_binding_digest FROM publication_binding_revisions
                         WHERE account=NEW.account AND operation_id=NEW.operation_id ORDER BY id DESC LIMIT 1),
                        a.binding_digest)
                    AND json_extract(NEW.old_binding_json,'$.operation')='publish_content'
                    AND json_remove(NEW.old_binding_json,'$.payload.is_original','$.payload.group_chat',
                        '$.payload.activity','$.payload.schedule_at','$.user_source')
                        =json_remove(NEW.new_binding_json,'$.payload.is_original','$.payload.group_chat',
                        '$.payload.activity','$.payload.schedule_at','$.user_source')
                    AND NOT EXISTS (SELECT 1 FROM action_events e
                        WHERE e.account=NEW.account AND e.operation_id=NEW.operation_id
                        AND e.event='publication_final_step_permitted')
                )
                BEGIN SELECT RAISE(ABORT, 'invalid publication binding revision'); END;
            ''')
            if self.db.execute("SELECT value FROM action_meta WHERE key='schema_version'").fetchone()[0] != '1':
                raise ValueError('unsupported cloud action ledger schema')
            with self._transaction():
                if 'semantic_digest' not in {row[1] for row in self.db.execute('PRAGMA table_info(actions)')}:
                    self.db.execute('ALTER TABLE actions ADD COLUMN semantic_digest TEXT')
                for row in self.db.execute('SELECT account,operation_id,binding_json FROM actions WHERE semantic_digest IS NULL').fetchall():
                    digest = _semantic_digest(json.loads(row['binding_json']))
                    if digest:
                        self.db.execute('UPDATE actions SET semantic_digest=? WHERE account=? AND operation_id=?',
                                        (digest, row['account'], row['operation_id']))
                self.db.execute('CREATE INDEX IF NOT EXISTS action_semantics ON actions(semantic_digest)')
        except BaseException:
            self.db.close()
            raise

    def _config(self):
        data = json.loads(self.config.read_text())
        if not isinstance(data, dict) or data.get('schema_version') != 1:
            raise ValueError('unsupported config schema')
        if data.get('browser_mode') != 'cloud_host':
            raise ValueError('ERR_BROWSER_MODE: configure browser_mode=cloud_host explicitly')
        if type(data.get('allow_write')) is not bool or type(data.get('allow_reconcile')) is not bool:
            raise ValueError('write and reconcile switches must be explicit booleans')
        _identifier(data.get('account_id'), 'account_id')
        _text(data.get('state_dir'), 'state_dir', 4096)
        if 'expected_user_id' in data: _text(data['expected_user_id'], 'expected_user_id', 256)
        if hasattr(self, 'account') and data['account_id'] != self.account:
            raise ValueError('ERR_ACCOUNT_CHANGED: reopen the ledger for the new account')
        if hasattr(self, 'directory'):
            directory = Path(data['state_dir']).expanduser()
            if not directory.is_absolute(): directory = self.config.parent / directory
            if Path(os.path.abspath(directory)) != self.directory:
                raise ValueError('ERR_STATE_DIRECTORY_CHANGED: reopen the configured ledger before dispatch')
        return data

    def close(self): self.db.close()

    def _check_storage(self):
        if any(os.path.islink(p) for p in (self.directory, *self.directory.parents)):
            raise ValueError('refuse linked state directory')
        info = os.stat(self.directory)
        if not stat.S_ISDIR(info.st_mode) or stat.S_IMODE(info.st_mode) & 0o077:
            raise ValueError('state directory must remain private (0700)')
        for suffix in ('', '-journal', '-wal', '-shm'):
            candidate = str(self.path) + suffix
            try: info = os.lstat(candidate)
            except FileNotFoundError:
                if suffix: continue
                raise ValueError('ERR_LEDGER_REPLACED: original cloud action ledger is missing') from None
            if not stat.S_ISREG(info.st_mode) or info.st_nlink != 1 or stat.S_IMODE(info.st_mode) & 0o077:
                raise ValueError('cloud action ledger must remain private and unlinked')
            if not suffix and (info.st_dev, info.st_ino) != self._file_identity:
                raise ValueError('ERR_LEDGER_REPLACED: original cloud action ledger identity changed')

    @contextlib.contextmanager
    def _transaction(self):
        self._check_storage()
        # Dispatch verification can re-enter ledger-backed cover review. Never
        # commit/rollback the caller's claim transaction from a nested helper.
        nested = self.db.in_transaction
        savepoint = 'cloud_nested_' + uuid.uuid4().hex
        self.db.execute('SAVEPOINT ' + savepoint if nested else 'BEGIN IMMEDIATE')
        try:
            yield
            if nested:self.db.execute('RELEASE SAVEPOINT ' + savepoint)
            else:self.db.commit()
        except BaseException:
            if nested:
                self.db.execute('ROLLBACK TO SAVEPOINT ' + savepoint)
                self.db.execute('RELEASE SAVEPOINT ' + savepoint)
            else:self.db.rollback()
            raise

    def _admit(self, value):
        if self.path.stat().st_size + 3 * (len(_json(value).encode('utf-8')) + 8192) > MAX_LEDGER:
            raise ValueError('ERR_QUOTA: cloud action ledger admission budget exceeded')

    def _row(self, operation_id):
        _identifier(operation_id)
        row = self.db.execute('SELECT * FROM actions WHERE account=? AND operation_id=?', (self.account, operation_id)).fetchone()
        if row is None: raise ValueError('unknown operation_id for this account')
        return self._effective_row(row)

    def _effective_row(self, row):
        """Overlay append-only revisions; the original action is never rewritten."""
        row = dict(row)
        binding = json.loads(row['binding_json'])
        if (_digest(binding) != row['binding_digest']
                or binding['operation_id'] != row['operation_id']
                or binding['ledger_account'] != row['account']):
            raise ValueError('ERR_BINDING_INTEGRITY: action binding does not match ledger')
        revisions = self.db.execute(
            'SELECT * FROM publication_binding_revisions WHERE account=? AND operation_id=? ORDER BY id',
            (row['account'], row['operation_id'])).fetchall()
        for revision in revisions:
            new_binding = json.loads(revision['new_binding_json'])
            if (revision['old_binding_json'] != row['binding_json']
                    or revision['old_binding_digest'] != row['binding_digest']
                    or revision['old_semantic_digest'] != row['semantic_digest']
                    or revision['attempt_id'] != row['attempt_id']
                    or _digest(new_binding) != revision['new_binding_digest']
                    or _semantic_digest(new_binding) != revision['new_semantic_digest']):
                raise ValueError('ERR_BINDING_INTEGRITY: broken publication revision chain')
            old_fixed = {key: value for key, value in binding.items() if key not in {'payload', 'user_source'}}
            new_fixed = {key: value for key, value in new_binding.items() if key not in {'payload', 'user_source'}}
            if (binding['operation'] != 'publish_content' or _json(old_fixed) != _json(new_fixed)
                    or _json({k: v for k, v in binding['payload'].items() if k not in PUBLICATION_REVISION_FIELDS})
                    != _json({k: v for k, v in new_binding['payload'].items() if k not in PUBLICATION_REVISION_FIELDS})):
                raise ValueError('ERR_BINDING_INTEGRITY: revision changed immutable manuscript')
            authorization = json.loads(revision['attestation'])
            if any(authorization.get(key) != expected for key, expected in (
                    ('operation_id', row['operation_id']), ('binding_digest', revision['new_binding_digest']),
                    ('expected_account', new_binding['expected_account']), ('user_source', new_binding['user_source']))):
                raise ValueError('ERR_BINDING_INTEGRITY: revision authorization mismatch')
            row.update(binding_json=revision['new_binding_json'], binding_digest=revision['new_binding_digest'],
                       semantic_digest=revision['new_semantic_digest'], authorization=revision['attestation'])
            binding = new_binding
        return row

    def _event(self, operation_id, event, detail=None):
        self.db.execute('INSERT INTO action_events(account,operation_id,event,recorded_at,detail) VALUES (?,?,?,?,?)',
                        (self.account, operation_id, event, self.clock(), _json(detail or {})))

    def _set(self, operation_id, status, **values):
        values.update(status=status, updated_at=self.clock())
        self.db.execute('UPDATE actions SET ' + ','.join(key + '=?' for key in values) + ' WHERE account=? AND operation_id=?',
                        (*values.values(), self.account, operation_id))

    def _verify(self, phase, row, evidence):
        if self.host_verifier is not None:
            # Copies prevent a validator from mutating the canonical action or caller input.
            binding = json.loads(row['binding_json'])
            copied = json.loads(_json(evidence))
            if self.host_verifier(phase, binding, copied) is not True:
                raise ValueError('ERR_HOST_VERIFIER_DENIED: ' + phase)

    def _guard_write(self, operation):
        config = self._config()
        allowed = config.get('allowed_write_operations')
        if allowed is not None:
            if not isinstance(allowed, list) or any(not isinstance(name, str) or name not in ACTION_OPERATIONS - READ_OPERATIONS for name in allowed):
                raise ValueError('ERR_WRITE_SCOPE: invalid configured operation allowlist')
            permission_operation = 'publish_content' if operation == SCHEDULE_INSPECTION else operation
            if operation not in allowed and permission_operation not in allowed:
                raise ValueError('ERR_WRITE_SCOPE: operation outside configured write scope')
        if config['allow_write'] is not True:
            raise ValueError('ERR_WRITE_DISABLED: host dispatch disabled by configuration')

    def _guard_authorization(self, row):
        if not row['authorization']: raise ValueError('ERR_AUTHORIZATION_REQUIRED: bound host attestation required')
        report = json.loads(row['authorization'])
        binding = json.loads(row['binding_json'])
        if any(report.get(key) != expected for key, expected in (
                ('operation_id', row['operation_id']), ('binding_digest', row['binding_digest']),
                ('expected_account', binding['expected_account']), ('user_source', binding['user_source']))):
            raise ValueError('ERR_BINDING_MISMATCH: authorization does not match current effective binding')
        if _timestamp(report['expires_at'], 'expires_at') <= self.clock():
            raise ValueError('ERR_AUTHORIZATION_EXPIRED: obtain a new bound host attestation')
        self._verify('authorization_at_dispatch', row, report)

    def _guard_identity(self, row):
        if not row['identity']: raise ValueError('ERR_IDENTITY_REQUIRED: fresh host identity observation required')
        observed = json.loads(row['identity'])
        age = self.clock() - _timestamp(observed['observed_at'], 'observed_at')
        if age < -5 or age > self.identity_max_age:
            raise ValueError('ERR_IDENTITY_STALE: observe the current browser account again')
        binding = json.loads(row['binding_json'])
        if observed['status'] != 'observed' or observed['account_id'] != binding['expected_account']:
            raise ValueError('ERR_IDENTITY_MISMATCH: current browser identity is not the expected account')
        self._verify('identity_at_dispatch', row, observed)
        return observed

    def _manifest(self, manifest):
        if not isinstance(manifest, list) or len(manifest) > 20:
            raise ValueError('media_manifest must be an array of at most 20 assets')
        paths = set(); cleaned = []
        for asset in manifest:
            _fields(asset, {'path', 'size', 'sha256'}, 'media manifest entry')
            _text(asset['path'], 'media path', 4096)
            path = Path(asset['path'])
            if not path.is_absolute() or str(path) != asset['path'] or '..' in path.parts:
                raise ValueError('media manifest paths must be normalized absolute local paths')
            if asset['path'] in paths: raise ValueError('duplicate media manifest path')
            paths.add(asset['path'])
            if type(asset['size']) is not int or not 1 <= asset['size'] <= 2 * 1024 ** 3:
                raise ValueError('media size must be between 1 byte and 2 GiB')
            if not isinstance(asset['sha256'], str) or not re.fullmatch('[a-f0-9]{64}', asset['sha256']):
                raise ValueError('media SHA256 must be lowercase hexadecimal')
            cleaned.append(dict(asset))
        if sum(asset['size'] for asset in cleaned) > 4 * 1024 ** 3:
            raise ValueError('media manifest exceeds 4 GiB')
        return cleaned

    def _guard_media(self, row):
        binding = json.loads(row['binding_json'])
        if binding['operation'] not in MEDIA_OPERATIONS | {SCHEDULE_INSPECTION}: return
        payload = binding['payload']
        paths = payload.get('images', []) if binding['operation'] in IMAGE_CONTINUATION_OPERATIONS else [payload.get('video')]
        manifest = binding['media_manifest']
        if binding['operation'] == 'publish_content' and not paths and payload.get('long_article_template'):
            _text(payload['long_article_template'], 'long_article_template', 1000)
            if manifest: raise ValueError('ERR_MEDIA_BINDING: no-upload long article must not contain unused media')
            return  # Text/composer changes still require begin_submit and host policy.
        if not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths) or not manifest:
            raise ValueError('ERR_MEDIA_REQUIRED: prepare a new immutable action with local media paths and SHA256 manifest')
        if len(paths) != len(set(paths)) or set(paths) != {asset['path'] for asset in manifest}:
            raise ValueError('ERR_MEDIA_BINDING: every payload media path must match its manifest; materialize remote URLs before preparation')
        for asset in manifest:
            path = Path(asset['path'])
            if any(p.is_symlink() for p in (path, *path.parents)):
                raise ValueError('ERR_MEDIA_CHANGED: linked media is not an immutable upload input')
            try:
                fd = os.open(path, os.O_RDONLY | os.O_NOFOLLOW | os.O_NONBLOCK)
                with os.fdopen(fd, 'rb') as source:
                    before = os.fstat(source.fileno())
                    if not stat.S_ISREG(before.st_mode) or before.st_size != asset['size']:
                        raise ValueError('ERR_MEDIA_CHANGED: media type or size differs from approved manifest')
                    digest = hashlib.sha256()
                    remaining = asset['size'] + 1
                    while remaining:
                        block = source.read(min(1024 * 1024, remaining))
                        if not block: break
                        digest.update(block); remaining -= len(block)
                    if remaining == 0:
                        raise ValueError('ERR_MEDIA_CHANGED: media grew during validation')
                    after = os.fstat(source.fileno())
                    if (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns) != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns) or digest.hexdigest() != asset['sha256']:
                        raise ValueError('ERR_MEDIA_CHANGED: media content differs from approved manifest')
            except OSError as error:
                raise ValueError('ERR_MEDIA_UNAVAILABLE: approved local media cannot be read') from error

    def _guard_browser_activity(self, row):
        binding = json.loads(row['binding_json'])
        others = self.db.execute(
            "SELECT * FROM actions WHERE NOT (account=? AND operation_id=?) AND status IN ('dispatching','submitted_unknown')",
            (self.account, row['operation_id'])).fetchall()
        for other in others:
            other = self._effective_row(other)
            previous = json.loads(other['binding_json'])
            if previous['expected_account'] != binding['expected_account']:
                continue
            if other['status'] == 'submitted_unknown' and binding['mutation'] and previous.get('mutation', True):
                continuation = self._unknown_continuation(row['operation_id'], other['operation_id'])
                if (other['account'] != self.account or continuation is None
                        or continuation['attestation']['blocked_operation_id'] != other['operation_id']):
                    raise ValueError('ERR_ACCOUNT_UNCERTAIN: another account mutation awaits evidence; do not dispatch another write')
                self._validate_unknown_continuation(row, other, continuation['attestation'])
                self._verify('unknown_continuation_at_dispatch', row, continuation['attestation'])
            active = (other['status'] == 'dispatching'
                      or other['result'] is None and other['lease_until'] is not None and other['lease_until'] > self.clock())
            if active:
                raise ValueError('ERR_ACCOUNT_BUSY: another browser read or write may still be running; serialize host calls')
        # An explicit uncertain report or an elapsed submission lease permits
        # read-side reconciliation. A separately bound continuation may exempt
        # an explicitly reported unknown, never an unreported call. Publications
        # require separate bound evidence for EVERY unresolved eligible action.
        # The host must still check the prior call ended. Unknown writes never retry.

    @staticmethod
    def _comment_scope(binding):
        if binding['operation'] not in COMMENT_OPERATIONS or not binding['mutation']:
            raise ValueError('ERR_CONTINUATION_SCOPE: only comment/reply actions are eligible')
        payload, target = binding['payload'], binding['target']
        feed = payload.get('feed_id')
        if not isinstance(feed, str) or not re.fullmatch('[a-fA-F0-9]{24}', feed):
            raise ValueError('ERR_CONTINUATION_TARGET: exact valid feed_id required')
        feed = feed.lower()
        if 'feed_id' in target and str(target['feed_id']).lower() != feed:
            raise ValueError('ERR_CONTINUATION_TARGET: payload and bound target differ')
        if isinstance(target.get('url'), str):
            path = urllib.parse.urlsplit(target['url']).path
            if '/explore/' in path and path.rstrip('/').rsplit('/', 1)[-1].lower() != feed:
                raise ValueError('ERR_CONTINUATION_TARGET: payload and bound URL differ')
        return feed, CloudActions._normalized_content(payload.get('content'))

    @staticmethod
    def _normalized_content(content):
        content = _text(content, 'content', MAX_JSON)
        normalized = ''.join(c for c in unicodedata.normalize('NFKC', content).casefold()
                             if not c.isspace() and unicodedata.category(c)[0] not in {'P', 'Z', 'C'})
        if not any(c.isalnum() for c in normalized):
            raise ValueError('ERR_CONTINUATION_CONTENT: substantive content required')
        return normalized

    @staticmethod
    def _publication_scope(binding):
        if binding['operation'] not in IMAGE_CONTINUATION_OPERATIONS or not binding['mutation']:
            raise ValueError('ERR_CONTINUATION_SCOPE: only independently reviewed image publications are eligible')
        payload = binding['payload']
        _text(payload.get('title'), 'publication title', MAX_JSON)
        content = CloudActions._normalized_content(payload.get('content'))
        paths, manifest = payload.get('images'), binding.get('media_manifest', [])
        if (not isinstance(paths, list) or not paths or any(not isinstance(path, str) for path in paths)
                or len(paths) != len(set(paths)) or not manifest
                or set(paths) != {asset['path'] for asset in manifest}):
            raise ValueError('ERR_CONTINUATION_MEDIA: complete immutable image manifest required')
        # Ignore copying, path renaming, order and duplicated bytes when comparing
        # assets. Actual new bytes are still rehashed by claim and begin_submit.
        return content, frozenset(asset['sha256'] for asset in manifest)

    def _unknown_continuations(self, operation_id):
        rows = self.db.execute(
            'SELECT attestation,recorded_at,blocked_operation_id FROM unknown_continuations WHERE account=? AND operation_id=? '
            'UNION ALL SELECT attestation,recorded_at,blocked_operation_id FROM publication_unknown_continuations '
            'WHERE account=? AND operation_id=? ORDER BY recorded_at,blocked_operation_id',
            (self.account, operation_id, self.account, operation_id)).fetchall()
        records = [{'attestation': json.loads(row['attestation']), 'recorded_at': _iso(row['recorded_at']),
                    'verification': HOST_REPORT} for row in rows]
        revisions = self.db.execute(
            'SELECT continuation_rebindings FROM publication_binding_revisions '
            'WHERE account=? AND operation_id=? ORDER BY id', (self.account, operation_id)).fetchall()
        for revision in revisions:
            for rebound in json.loads(revision['continuation_rebindings']):
                matching = [record for record in records
                            if record['attestation']['blocked_operation_id'] == rebound['old']['blocked_operation_id']]
                if len(matching) != 1 or matching[0]['attestation'] != rebound['old']:
                    raise ValueError('ERR_BINDING_INTEGRITY: continuation revision chain mismatch')
                preserved = {k: v for k, v in rebound['old'].items() if k != 'binding_digest'}
                if preserved != {k: v for k, v in rebound['new'].items() if k != 'binding_digest'}:
                    raise ValueError('ERR_BINDING_INTEGRITY: continuation source evidence changed')
                matching[0]['attestation'] = rebound['new']
        return records

    def _unknown_continuation(self, operation_id, blocked_operation_id=None):
        records = self._unknown_continuations(operation_id)
        if blocked_operation_id is not None:
            records = [record for record in records if record['attestation']['blocked_operation_id'] == blocked_operation_id]
        return records[0] if len(records) == 1 else None

    def _validate_unknown_continuation(self, row, blocked, attestation):
        required = {'operation_id', 'binding_digest', 'blocked_operation_id', 'blocked_binding_digest',
                    'expected_account', 'user_source', 'user_quote', 'confirmed_at', 'expires_at', 'evidence_ref'}
        binding, previous = json.loads(row['binding_json']), json.loads(blocked['binding_json'])
        optional = {'instruction_scope'} if isinstance(attestation, dict) and 'instruction_scope' in attestation else set()
        _fields(attestation, required | optional, 'unknown continuation attestation')
        standing_publications = bool(optional)
        if standing_publications and (binding['operation'] not in IMAGE_CONTINUATION_OPERATIONS
                or attestation['instruction_scope'] != 'future_independent_image_publications'):
            raise ValueError('ERR_CONTINUATION_SCOPE: standing scope is limited to future independent image publications')
        for key, expected in (('operation_id', row['operation_id']), ('binding_digest', row['binding_digest']),
                              ('blocked_operation_id', blocked['operation_id']), ('blocked_binding_digest', blocked['binding_digest']),
                              ('expected_account', binding['expected_account'])):
            if attestation[key] != expected:
                raise ValueError('ERR_BINDING_MISMATCH: continuation ' + key)
        if (row['operation_id'] == blocked['operation_id'] or blocked['account'] != row['account']
                or previous['expected_account'] != binding['expected_account']
                or _digest(previous) != blocked['binding_digest']):
            raise ValueError('ERR_CONTINUATION_SCOPE: two distinct actions on the same bound account required')
        if blocked['status'] != 'submitted_unknown' or not blocked['result']:
            raise ValueError('ERR_CONTINUATION_UNREPORTED: retain uncertainty and report the ended browser attempt first')
        result = json.loads(blocked['result'])
        if (result.get('status') != 'submitted_unknown' or result.get('evidence_kind') != 'uncertain'
                or result.get('operation_id') != blocked['operation_id']
                or result.get('binding_digest') != blocked['binding_digest']
                or result.get('attempt_id') != blocked['attempt_id']):
            raise ValueError('ERR_CONTINUATION_UNREPORTED: exact host-reported unknown attempt required')
        if binding['operation'] in IMAGE_CONTINUATION_OPERATIONS:
            content, media = self._publication_scope(binding)
            if previous['operation'] in IMAGE_CONTINUATION_OPERATIONS:
                old_content, old_media = self._publication_scope(previous)
                if media == old_media:
                    raise ValueError('ERR_CONTINUATION_MEDIA: the same image hash set cannot be republished under a new action or schedule')
            else:
                _, old_content = self._comment_scope(previous)
        else:
            feed, content = self._comment_scope(binding)
            old_feed, old_content = self._comment_scope(previous)
            if feed == old_feed:
                raise ValueError('ERR_CONTINUATION_TARGET: new comment must address a different feed')
        if content == old_content:
            raise ValueError('ERR_CONTINUATION_CONTENT: normalized content must differ; host must verify substantive distinction')
        for key, limit in (('user_source', 500), ('user_quote', 8000), ('evidence_ref', 1000)):
            _text(attestation[key], key, limit)
        confirmed = _timestamp(attestation['confirmed_at'], 'confirmed_at')
        expires = _timestamp(attestation['expires_at'], 'expires_at')
        if ((not standing_publications and confirmed < blocked['submitted_at']) or confirmed > self.clock() + 5
                or expires <= self.clock() or expires <= confirmed or expires - confirmed > 366 * 86400):
            raise ValueError('ERR_CONTINUATION_EXPIRED: valid operational window and actual scoped confirmation timestamp required')

    def record_unknown_continuation(self, operation_id, blocked_operation_id, attestation):
        """Record one independently authorized comment/publication exception.

        This never resolves/retries the old unknown or authorizes the browser.
        The host must check the actual source/quote, distinct meaning and ended
        browser call. expires_at narrows this action's operational validity, not
        the user's standing approval. Every new target needs its own binding.
        Image publications need separate evidence for each blocker; a typed
        future-publication scope preserves the genuine earlier user timestamp.
        Its string never substitutes for host verification of standing approval.
        Normal authorization, review, identity, pacing and other blockers remain.
        """
        with self._transaction():
            row = self._row(operation_id)
            _identifier(blocked_operation_id)
            raw = _json(attestation)
            prior = self._unknown_continuation(operation_id, blocked_operation_id)
            if prior is not None:
                if (_json(prior['attestation']) != raw
                        or prior['attestation']['blocked_operation_id'] != blocked_operation_id):
                    raise ValueError('ERR_CONTINUATION_CONFLICT: preserve the original exact continuation evidence')
            else:
                publication = json.loads(row['binding_json'])['operation'] in IMAGE_CONTINUATION_OPERATIONS
                if not publication and self._unknown_continuations(operation_id):
                    raise ValueError('ERR_CONTINUATION_CONFLICT: comments retain their original single-blocker scope')
                blocked = self._row(blocked_operation_id)
                if row['status'] not in {'prepared', 'ready'}:
                    raise ValueError('ERR_STATE: continuation must bind a new prepared or ready action')
                self._validate_unknown_continuation(row, blocked, attestation)
                self._verify('unknown_continuation_report', row, attestation)
                self._admit(attestation)
                table = 'publication_unknown_continuations' if publication else 'unknown_continuations'
                self.db.execute('INSERT INTO ' + table + ' VALUES (?,?,?,?,?)',
                                (self.account, operation_id, blocked_operation_id, raw, self.clock()))
                self._event(operation_id, 'unknown_continuation_reported', attestation)
        result = self.get(operation_id); result['duplicate'] = prior is not None
        return result

    def guard_retry_lineage(self, binding):
        """Recheck legacy prepared retries; never rewrite their retained facts."""
        parent_id = binding.get('retry_of')
        if parent_id is None:
            return
        parent = self._row(parent_id)
        if json.loads(parent['binding_json']).get('retry_of') is not None:
            raise ValueError('ERR_RETRY_LIMIT: retained retry chain cannot dispatch')
        first = self.db.execute(
            "SELECT operation_id FROM actions WHERE account=? AND json_extract(binding_json,'$.retry_of')=? ORDER BY created_at,rowid LIMIT 1",
            (self.account, parent_id)).fetchone()
        if first is None or first['operation_id'] != binding['operation_id']:
            raise ValueError('ERR_RETRY_LIMIT: original action has already consumed its single retry')

    def _guard_dispatch(self, row, browser_context=None):
        binding = json.loads(row['binding_json'])
        data = self._config()
        if data.get('expected_user_id') is not None and data['expected_user_id'] != binding['expected_account']:
            raise ValueError('ERR_ACCOUNT_CHANGED: configured expected_user_id differs from immutable action account')
        self._guard_browser_activity(row)
        if binding['mutation']:
            self.guard_retry_lineage(binding)
            self.guard_execution_pause(binding)
            self._guard_write(binding['operation']); self._guard_authorization(row)
        else:
            self._config()  # Account and mode changes still invalidate a read claim.
        if binding['identity_required']:
            observed = self._guard_identity(row)
            if browser_context is not None and observed['browser_context'] != browser_context:
                raise ValueError('ERR_BROWSER_CONTEXT_CHANGED: current observation belongs to a different browser context')
        else:
            # A public read needs a concrete host tab/session, not a logged-in account.
            prior = json.loads(row['identity']) if row['identity'] else None
            context = browser_context or (prior['browser_context'] if prior else None)
            _text(context, 'browser_context', 1000)
            observed = {'browser_context': context, 'identity_required': False}
        self._guard_media(row)
        return observed

    def _semantic_matches(self, semantic):
        if not semantic:
            return []
        # Keep all previous semantics reserved too: a settings correction must
        # never make the already uploaded manuscript replayable under its old ID.
        return self.db.execute(
            'SELECT a.account,a.operation_id,a.status FROM actions a WHERE a.semantic_digest=? '
            'OR EXISTS (SELECT 1 FROM publication_binding_revisions r '
            'WHERE r.account=a.account AND r.operation_id=a.operation_id '
            'AND (r.old_semantic_digest=? OR r.new_semantic_digest=?)) '
            'ORDER BY a.created_at DESC,a.rowid DESC', (semantic, semantic, semantic)).fetchall()

    def prepare(self, operation, payload, *, expected_account, target, user_source,
                operation_id=None, due_at=None, timezone='UTC', media_manifest=None, dependency=None, retry_of=None):
        """Plan without enabling writes. Reusing an ID requires the exact binding."""
        if not isinstance(operation, str) or operation not in self.operations:
            raise ValueError('ERR_CLOUD_UNSUPPORTED: operation outside action allowlist')
        if not isinstance(payload, dict) or not isinstance(target, dict) or not target:
            raise ValueError('payload and nonempty target must be JSON objects')
        payload = json.loads(_json(payload)); target = json.loads(_json(target, 'target'))
        manifest = self._manifest([] if media_manifest is None else media_manifest)
        if operation == SCHEDULE_INSPECTION:
            _text(target.get('inspection_id'), 'inspection_id', 256)
            if 'schedule_at' in payload:
                raise ValueError('ERR_INSPECTION_SCOPE: schedule inspection must not contain schedule_at or publish')
            if not isinstance(payload.get('images'), list) or not payload['images']:
                raise ValueError('ERR_MEDIA_REQUIRED: schedule inspection requires actual reviewed images')
        if self.payload_validator is not None:
            candidate = json.loads(_json(payload))
            self.payload_validator('publish_content' if operation == SCHEDULE_INSPECTION else operation, candidate)
            if candidate != payload: raise ValueError('payload validator must not transform the action')
        _text(expected_account, 'expected_account', 256)
        _text(user_source, 'user_source', 500)
        _text(timezone, 'timezone', 128)
        try: ZoneInfo(timezone)
        except (ValueError, ZoneInfoNotFoundError): raise ValueError('invalid IANA timezone') from None
        operation_id = _identifier(uuid.uuid4().hex if operation_id is None else operation_id)
        if retry_of is not None:
            _identifier(retry_of, 'retry_of')
            if retry_of == operation_id: raise ValueError('a retry requires a new operation_id')
        if dependency is not None:
            _identifier(dependency, 'dependency')
            if dependency == operation_id: raise ValueError('action cannot depend on itself')
        if 'operation_id' in payload and payload['operation_id'] != operation_id:
            raise ValueError('ERR_BINDING_MISMATCH: payload operation_id differs')
        with self._transaction():
            if dependency is not None: self._row(dependency)
            existing = self.db.execute('SELECT operation_id FROM actions WHERE account=? AND operation_id=?', (self.account, operation_id)).fetchone()
            prior = self._row(operation_id) if existing else None
            due = (_timestamp(due_at, 'due_at') if due_at is not None else prior['due_at'] if prior else self.clock())
            binding = {'schema_version': 1, 'operation_id': operation_id, 'ledger_account': self.account,
                       'expected_account': expected_account, 'operation': operation, 'target': target,
                       'payload': payload, 'media_manifest': manifest, 'dependency': dependency,
                       'mutation': operation not in READ_OPERATIONS,
                       'identity_required': operation not in READ_OPERATIONS or operation in IDENTITY_READS,
                       'user_source': user_source, 'due_at': _iso(due), 'timezone': timezone}
            if retry_of is not None: binding['retry_of'] = retry_of
            semantic = _semantic_digest(binding)
            raw = _json(binding); fingerprint = _digest(binding)
            if prior:
                if prior['binding_digest'] != fingerprint:
                    raise ValueError('ERR_IDEMPOTENCY_CONFLICT: operation_id is bound to a different action')
            else:
                matches = self._semantic_matches(semantic)
                blocking = next((row for row in matches if row['status'] not in {'cancelled', 'not_applied'}), None)
                if blocking:
                    raise ValueError('ERR_CONTENT_DUPLICATE: same semantic action already exists as ' + blocking['operation_id'] + ' (' + blocking['status'] + '); inspect its original evidence')
                if matches and (retry_of is None or matches[0]['operation_id'] != retry_of):
                    raise ValueError('ERR_EXPLICIT_RETRY_REQUIRED: set retry_of=' + matches[0]['operation_id'] + ' for a new, separately authorized attempt')
                if retry_of is not None and (not matches or matches[0]['operation_id'] != retry_of):
                    raise ValueError('ERR_RETRY_BINDING: retry_of must identify the latest cancelled/not_applied semantic predecessor')
                if retry_of is not None:
                    predecessor = self._row(retry_of)
                    if json.loads(predecessor['binding_json']).get('retry_of') is not None:
                        raise ValueError('ERR_RETRY_LIMIT: retry chains are prohibited; inspect original evidence')
                    child = self.db.execute(
                        "SELECT operation_id FROM actions WHERE account=? AND json_extract(binding_json,'$.retry_of')=? LIMIT 1",
                        (self.account, retry_of)).fetchone()
                    if child is not None:
                        raise ValueError('ERR_RETRY_LIMIT: original action already has its single retry')
                self._admit(binding)
                timestamp = self.clock()
                self.db.execute('INSERT INTO actions(account,operation_id,binding_json,binding_digest,status,due_at,created_at,updated_at,semantic_digest) VALUES (?,?,?,?,?,?,?,?,?)',
                                (self.account, operation_id, raw, fingerprint, 'prepared', due, timestamp, timestamp, semantic))
                self._event(operation_id, 'prepared')
        result = self.get(operation_id); result['duplicate'] = prior is not None
        return result

    def get(self, operation_id):
        row = self._row(operation_id)
        return {'operation_id': operation_id, 'binding': json.loads(row['binding_json']),
                'binding_digest': row['binding_digest'], 'semantic_digest': row['semantic_digest'], 'status': row['status'],
                'created_at': _iso(row['created_at']), 'updated_at': _iso(row['updated_at']),
                'authorization': json.loads(row['authorization']) if row['authorization'] else None,
                'identity': json.loads(row['identity']) if row['identity'] else None,
                'attempt': row['attempt'], 'attempt_id': row['attempt_id'], 'owner': row['owner'],
                'claim_browser_context': row['claim_context'],
                'lease_until': _iso(row['lease_until']) if row['lease_until'] is not None else None,
                'submitted_at': _iso(row['submitted_at']) if row['submitted_at'] is not None else None,
                'result': json.loads(row['result']) if row['result'] else None,
                'verification': HOST_REPORT, 'execution_authority': 'host_policy_only',
                'platform_success_independently_verified': False, 'automatic_retry': False,
                'inspection_scope': ({'upload_only': True, 'final_publish_allowed': False,
                                      'success_means': 'schedule_controls_observed_not_published'}
                                     if json.loads(row['binding_json'])['operation'] == SCHEDULE_INSPECTION else None),
                'read_provenance': self._read_provenance(row),
                'unknown_continuation': self._unknown_continuation(operation_id),
                'unknown_continuations': self._unknown_continuations(operation_id),
                'binding_revisions': self.binding_revisions(operation_id)}

    def binding_revisions(self, operation_id):
        """Full immutable old/new evidence; no user approval is inferred here."""
        _identifier(operation_id)
        rows = self.db.execute(
            'SELECT * FROM publication_binding_revisions WHERE account=? AND operation_id=? ORDER BY id',
            (self.account, operation_id)).fetchall()
        return [{'revision_id': row['id'], 'attempt_id': row['attempt_id'],
                 'old_binding': json.loads(row['old_binding_json']), 'old_binding_digest': row['old_binding_digest'],
                 'old_semantic_digest': row['old_semantic_digest'],
                 'new_binding': json.loads(row['new_binding_json']), 'new_binding_digest': row['new_binding_digest'],
                 'new_semantic_digest': row['new_semantic_digest'],
                 'attestation': json.loads(row['attestation']), 'observation': json.loads(row['observation']),
                 'continuation_rebindings': json.loads(row['continuation_rebindings']),
                 'recorded_at': _iso(row['recorded_at']), 'verification': HOST_REPORT} for row in rows]

    @staticmethod
    def _read_provenance(row):
        if json.loads(row['binding_json'])['operation'] not in READ_OPERATIONS or not row['result']:
            return None
        report = json.loads(row['result'])
        if report['status'] != 'host_reported_success':
            return None
        return {'read_at': report.get('read_at', report['observed_at']), 'cached': False,
                'provenance': 'host_reported_actual_read; not independently verified',
                'timestamp_source': 'read_at' if 'read_at' in report else 'observed_at'}

    def list_actions(self, *, status=None, due_only=False, limit=50):
        if status is not None and status not in STATES: raise ValueError('invalid action status')
        if type(due_only) is not bool or type(limit) is not int or not 1 <= limit <= 200:
            raise ValueError('invalid list bounds')
        where = ['account=?']; args = [self.account]
        if status: where.append('status=?'); args.append(status)
        if due_only: where.append('due_at<=?'); args.append(self.clock())
        rows = self.db.execute('SELECT operation_id FROM actions WHERE ' + ' AND '.join(where) + ' ORDER BY due_at,created_at,operation_id LIMIT ?', (*args, limit)).fetchall()
        return [self.get(row[0]) for row in rows]

    def execution_pause_status(self, expected_account=None):
        config = self._config()
        expected = expected_account or config.get('expected_user_id')
        if not isinstance(expected, str) or not expected.strip():
            raise ValueError('ERR_EXECUTION_CONTROL: configured exact expected_user_id required')
        if config.get('expected_user_id') is not None and config['expected_user_id'] != expected:
            raise ValueError('ERR_EXECUTION_CONTROL: configured account differs from action')
        row = self.db.execute('SELECT value FROM action_meta WHERE key=?',
                              ('execution_paused:' + expected,)).fetchone()
        if row is not None and row[0] not in {'true', 'false'}:
            raise ValueError('ERR_EXECUTION_CONTROL: invalid persistent pause state')
        return {'expected_account': expected, 'paused': row is not None and row[0] == 'true'}

    def set_execution_pause(self, paused, user_source, reason):
        """Local admission latch; user_source is audit context, NOT authorization."""
        if type(paused) is not bool:
            raise ValueError('ERR_EXECUTION_CONTROL: paused must be a boolean')
        _text(user_source, 'user_source', 500); _text(reason, 'reason', 1000)
        detail = {'user_source': user_source, 'reason': reason,
                  'authorization_provenance': 'caller_must_verify_user_authority; not_a_permission_grant'}
        _json(detail)
        with self._transaction():
            state = self.execution_pause_status()
            self._admit(detail)
            self.db.execute('INSERT OR REPLACE INTO action_meta VALUES (?,?)',
                            ('execution_paused:' + state['expected_account'], 'true' if paused else 'false'))
            self.db.execute('INSERT INTO execution_control_events(expected_account,ledger_account,paused,recorded_at,detail) VALUES (?,?,?,?,?)',
                            (state['expected_account'], self.account, int(paused), self.clock(), _json(detail)))
        return dict(self.execution_pause_status(), in_flight_calls_cancelled=False,
                    existing_authorizations_renewed=False, grants_execution_authority=False)

    def guard_execution_pause(self, binding, *, observation_only=False):
        if observation_only or not binding['mutation']:
            return
        if self.execution_pause_status(binding['expected_account'])['paused']:
            raise ValueError('ERR_EXECUTION_PAUSED: explicit user-directed resume required; no new write permit')

    def queue_enabled(self):
        row = self.db.execute('SELECT value FROM action_meta WHERE key=?', ('queue_enabled:' + self.account,)).fetchone()
        return row is None or row['value'] == 'true'

    def set_queue_enabled(self, enabled):
        """Pause/resume new LOCAL claims; never start a browser worker or daemon."""
        if type(enabled) is not bool: raise ValueError('queue enabled must be a boolean')
        with self._transaction():
            self._admit({})
            self.db.execute('INSERT INTO action_meta(key,value) VALUES (?,?) ON CONFLICT(key) DO UPDATE SET value=excluded.value',
                            ('queue_enabled:' + self.account, 'true' if enabled else 'false'))
        return {'queue_enabled': enabled, 'autonomous_browser': False, 'existing_attempts_interrupted': False}

    def status(self):
        data = self._config()
        counts = dict(self.db.execute('SELECT status,count(*) FROM actions WHERE account=? GROUP BY status', (self.account,)))
        return {'transport': 'cloud_host', 'execution': 'assistant_mediated', 'write_enabled': data['allow_write'],
                'host_verifier_configured': self.host_verifier is not None,
                'payload_validation': 'injected_registry_and_bounded_json' if self.payload_validator else 'allowlist_and_bounded_json',
                'execution_authority': 'host_policy_only', 'authorization_evidence': HOST_REPORT,
                'browser_executor': False, 'autonomous_browser': False, 'automatic_retry': False,
                'supported_actions': sorted(self.operations), 'states': counts,
                'execution_pause': self.execution_pause_status() if data.get('expected_user_id') else None,
                'queue_enabled': self.queue_enabled(), 'ledger_limit_bytes': MAX_LEDGER,
                'due_ready': self.db.execute("SELECT count(*) FROM actions WHERE account=? AND status='ready' AND due_at<=?", (self.account, self.clock())).fetchone()[0]}

    def request_authorization(self, operation_id):
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] == 'prepared':
                binding = json.loads(row['binding_json'])
                next_state = 'awaiting_authorization' if binding['mutation'] else 'ready'
                self._admit({}); self._set(operation_id, next_state)
                self._event(operation_id, 'authorization_requested' if binding['mutation'] else 'read_plan_ready')
            elif row['status'] != 'awaiting_authorization':
                raise ValueError('ERR_STATE: only prepared actions can request authorization')
        return self.get(operation_id)

    def record_authorization(self, operation_id, attestation):
        """Record a host claim of permission, NEVER grant execution permission."""
        required = {'operation_id', 'binding_digest', 'expected_account', 'user_source',
                    'user_quote', 'confirmed_at', 'expires_at', 'evidence_ref'}
        _fields(attestation, required, 'host attestation')
        for key, limit in [('user_quote', 8000), ('evidence_ref', 1000), ('user_source', 500)]: _text(attestation[key], key, limit)
        confirmed = _timestamp(attestation['confirmed_at'], 'confirmed_at')
        expires = _timestamp(attestation['expires_at'], 'expires_at')
        if confirmed > self.clock() + 5 or expires <= self.clock() or expires <= confirmed or expires - confirmed > 366 * 86400:
            raise ValueError('invalid or expired host attestation time range')
        with self._transaction():
            row = self._row(operation_id); binding = json.loads(row['binding_json'])
            for key in ('operation_id', 'expected_account', 'user_source'):
                if attestation[key] != binding[key]: raise ValueError('ERR_BINDING_MISMATCH: host attestation ' + key)
            if attestation['binding_digest'] != row['binding_digest']: raise ValueError('ERR_BINDING_MISMATCH: host attestation digest')
            if row['status'] not in {'awaiting_authorization', 'ready'}: raise ValueError('ERR_STATE: action is not awaiting authorization')
            self._verify('authorization_report', row, attestation)
            raw = _json(attestation)
            if row['authorization'] != raw:
                self._admit(attestation); self._set(operation_id, 'ready', authorization=raw)
                self._event(operation_id, 'host_authorization_reported', attestation)
        return self.get(operation_id)

    # Explicitly named alias; callers must not mistake this for a policy check.
    record_host_authorization = record_authorization

    def observe_identity(self, operation_id, observation):
        required = {'status', 'account_id', 'observed_at', 'evidence_ref', 'browser_context'}
        _fields(observation, required, 'identity observation')
        if observation['status'] not in {'observed', 'logged_out', 'unavailable'}: raise ValueError('invalid identity status')
        _text(observation['account_id'], 'account_id', 256, empty=observation['status'] != 'observed')
        if observation['status'] != 'observed' and observation['account_id']:
            raise ValueError('non-observed identity must have empty account_id')
        for key in ('evidence_ref', 'browser_context'): _text(observation[key], key, 1000)
        if _timestamp(observation['observed_at'], 'observed_at') > self.clock() + 5:
            raise ValueError('identity observation cannot be from the future')
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] in TERMINAL: raise ValueError('ERR_STATE: terminal action identity cannot change')
            self._verify('identity_report', row, observation)
            raw = _json(observation)
            if raw != row['identity']:
                self._admit(observation); self._set(operation_id, row['status'], identity=raw)
                self._event(operation_id, 'identity_observed', observation)
        return self.get(operation_id)

    def claim(self, operation_id, worker_id, lease_seconds=120, browser_context=None):
        """Reserve local work only. This is not a browser-action authorization."""
        _identifier(worker_id, 'worker_id')
        if type(lease_seconds) not in (int, float) or not 5 <= lease_seconds <= 600:
            raise ValueError('lease_seconds must be between 5 and 600')
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] != 'ready': raise ValueError('ERR_STATE: action is not ready; never replay dispatched work')
            if not self.queue_enabled(): raise ValueError('ERR_QUEUE_PAUSED: local claims are paused')
            dependency = json.loads(row['binding_json'])['dependency']
            if dependency and self._row(dependency)['status'] != 'host_reported_success':
                raise ValueError('ERR_DEPENDENCY_NOT_SUCCEEDED: dependency lacks a host-reported successful outcome')
            if row['due_at'] > self.clock(): raise ValueError('ERR_NOT_DUE: action due time has not arrived')
            identity = self._guard_dispatch(row, browser_context=browser_context)
            self._verify('claim', row, {'authorization_report': json.loads(row['authorization']) if row['authorization'] else None, 'identity': identity})
            self._admit({}); token = secrets.token_urlsafe(32); attempt_id = uuid.uuid4().hex
            self._set(operation_id, 'dispatching', attempt=row['attempt'] + 1, attempt_id=attempt_id,
                      owner=worker_id, claim_hash=hashlib.sha256(token.encode()).hexdigest(),
                      claim_context=identity['browser_context'], lease_until=self.clock() + lease_seconds)
            self._event(operation_id, 'claimed', {'attempt_id': attempt_id, 'owner': worker_id})
        result = self.get(operation_id); result['claim_token'] = token
        return result

    def _claim(self, row, claim_token, *, live=False):
        _text(claim_token, 'claim_token', 128)
        if not row['claim_hash'] or not secrets.compare_digest(hashlib.sha256(claim_token.encode()).hexdigest(), row['claim_hash']):
            raise ValueError('ERR_CLAIM: stale or incorrect claim token')
        if live and (row['lease_until'] is None or row['lease_until'] <= self.clock()):
            raise ValueError('ERR_LEASE_EXPIRED: do not perform browser actions')

    def renew_inspection_claim(self, operation_id, claim_token, lease_seconds, observation, *, after_upload=False):
        """Extend the same pre-upload inspection attempt; never grant authority.

        The trusted host must first verify every pacing step is resolved and all
        media requests are definitively not applied. This host report alone is
        not evidence of browser state. Next real steps still require current
        authorization, identity, batch review and ordinary host policy checks.
        """
        if type(lease_seconds) not in (int, float) or not 5 <= lease_seconds <= 600:
            raise ValueError('lease_seconds must be between 5 and 600')
        _fields(observation, {'expected_account', 'browser_context', 'call_ended',
                             'upload_invoked', 'observed_at', 'evidence_ref'},
                'inspection renewal observation')
        if type(after_upload) is not bool or observation['call_ended'] is not True or observation['upload_invoked'] is not after_upload:
            raise ValueError('ERR_INSPECTION_RENEWAL: ended call and no invoked upload required')
        _text(observation['evidence_ref'], 'evidence_ref', 1000)
        observed = _timestamp(observation['observed_at'], 'observed_at')
        with self._transaction():
            row = self._row(operation_id)
            binding = json.loads(row['binding_json'])
            if (binding['operation'] != SCHEDULE_INSPECTION or row['status'] != 'submitted_unknown'
                    or row['result'] is not None):
                raise ValueError('ERR_INSPECTION_RENEWAL: only an unreported schedule inspection may extend its original claim')
            self._claim(row, claim_token, live=False)
            self._config()
            if (observation['expected_account'] != binding['expected_account']
                    or observation['browser_context'] != row['claim_context']):
                raise ValueError('ERR_BINDING_MISMATCH: renewal account or browser context')
            if (observed > self.clock() or self.clock() - observed > 120
                    or observed < row['submitted_at']):
                raise ValueError('ERR_INSPECTION_RENEWAL: fresh post-dispatch observation required')
            self._verify('inspection_claim_renewal', row, observation)
            self._admit(observation)
            self._set(operation_id, row['status'], lease_until=self.clock() + lease_seconds)
            self._event(operation_id, 'inspection_controls_claim_renewed' if after_upload else 'inspection_claim_renewed_before_upload',
                        {'attempt_id': row['attempt_id'], 'observation': observation,
                         'lease_until': _iso(self.clock() + lease_seconds)})
        return self.get(operation_id)

    def renew_unsubmitted_publication_claim(self, operation_id, claim_token, lease_seconds, observation):
        """Renew a genuinely unsubmitted retained editor without changing its attempt."""
        if type(lease_seconds) not in (int,float) or not 5 <= lease_seconds <= 600:
            raise ValueError('lease_seconds must be between 5 and 600')
        retained=observation.get('mode')=='retained_editor' if isinstance(observation,dict) else False
        extra={'mode','visible_title','visible_body','uploaded_media_match','media_evidence_ref','editor_retained','target_schedule_at','visible_schedule_at'} if retained else {'denied_step_id'}
        _fields(observation, {'expected_account','browser_context','call_ended',
            'final_submit_invoked','observed_at','evidence_ref'} | extra, 'publication renewal observation')
        if observation['call_ended'] is not True or observation['final_submit_invoked'] is not False:
            raise ValueError('ERR_PUBLICATION_RENEWAL: ended calls and no final submission required')
        _text(observation['evidence_ref'],'evidence_ref',1000)
        if not retained:_text(observation['denied_step_id'],'denied_step_id',200)
        observed=_timestamp(observation['observed_at'],'observed_at')
        with self._transaction():
            row=self._row(operation_id);binding=json.loads(row['binding_json'])
            if binding['operation']!='publish_content' or row['status']!='submitted_unknown' or row['result'] is not None:
                raise ValueError('ERR_PUBLICATION_RENEWAL: unreported publication only')
            self._claim(row,claim_token,live=False);self._config()
            if retained:
                payload=binding['payload']
                if (observation['editor_retained'] is not True or observation['uploaded_media_match'] is not True
                        or observation['visible_title']!=payload['title'] or observation['visible_body']!=payload['content']
                        or observation['target_schedule_at']!=payload.get('schedule_at')):
                    raise ValueError('ERR_PUBLICATION_RENEWAL: retained editor must match reviewed media, title, body and target time')
                _text(observation['media_evidence_ref'],'media_evidence_ref',1000)
                if observation['visible_schedule_at'] is not None:_text(observation['visible_schedule_at'],'visible_schedule_at',100)
                if self.db.execute("SELECT 1 FROM action_events WHERE account=? AND operation_id=? AND event='publication_final_step_permitted'",(self.account,operation_id)).fetchone():
                    raise ValueError('ERR_PUBLICATION_RENEWAL: final submission permit already issued')
            if observation['expected_account']!=binding['expected_account'] or observation['browser_context']!=row['claim_context']:
                raise ValueError('ERR_BINDING_MISMATCH: publication renewal context')
            if observed>self.clock() or self.clock()-observed>120 or observed<row['submitted_at']:
                raise ValueError('ERR_PUBLICATION_RENEWAL: fresh observation required')
            self._guard_dispatch(row,browser_context=row['claim_context'])
            self._verify('claim',row,observation)
            self._admit(observation)
            self._set(operation_id,row['status'],lease_until=self.clock()+lease_seconds)
            self._event(operation_id,'unsubmitted_publication_claim_renewed',
                {'attempt_id':row['attempt_id'],'observation':observation,'lease_until':_iso(self.clock()+lease_seconds)})
        return self.get(operation_id)

    def _publication_settings_candidate(self, row, payload, user_source):
        binding = json.loads(row['binding_json'])
        if (binding['operation'] != 'publish_content' or row['status'] != 'submitted_unknown'
                or row['result'] is not None):
            raise ValueError('ERR_PUBLICATION_REVISION: only an unreported unsubmitted publication may be revised')
        if self.db.execute(
                "SELECT 1 FROM action_events WHERE account=? AND operation_id=? "
                "AND event='publication_final_step_permitted'", (self.account, row['operation_id'])).fetchone():
            raise ValueError('ERR_PUBLICATION_REVISION: final submission permit already issued')
        if not isinstance(payload, dict):
            raise ValueError('publication payload must be a JSON object')
        payload = json.loads(_json(payload))
        previous = binding['payload']
        fixed = lambda value: {key: item for key, item in value.items() if key not in PUBLICATION_REVISION_FIELDS}
        if _json(fixed(payload)) != _json(fixed(previous)):
            raise ValueError('ERR_PUBLICATION_REVISION: only original, group, activity and schedule settings may change')
        if payload == previous:
            raise ValueError('ERR_PUBLICATION_REVISION: a settings change is required')
        if 'is_original' in payload and type(payload['is_original']) is not bool:
            raise ValueError('is_original must be a boolean')
        for field in ('group_chat', 'activity'):
            if field in payload:
                _text(payload[field], field, 1000, empty=True)
        if 'schedule_at' in payload:
            _timestamp(payload['schedule_at'], 'schedule_at')
        if self.payload_validator is not None:
            candidate = json.loads(_json(payload))
            self.payload_validator('publish_content', candidate)
            if _json(candidate) != _json(payload):
                raise ValueError('payload validator must not transform the action')
        _text(user_source, 'user_source', 500)
        binding.update(payload=payload, user_source=user_source)
        return binding

    def preview_unsubmitted_publication_settings(self, operation_id, payload, user_source):
        """Compute a prospective binding for exact host attestation, without mutation."""
        with self._transaction():
            row = self._row(operation_id)
            binding = self._publication_settings_candidate(row, payload, user_source)
            return {'operation_id': operation_id, 'attempt_id': row['attempt_id'],
                    'previous_binding_digest': row['binding_digest'], 'binding': binding,
                    'binding_digest': _digest(binding), 'semantic_digest': _semantic_digest(binding)}

    def revise_unsubmitted_publication_settings(self, operation_id, claim_token, payload, attestation, observation):
        """Append a narrow, explicitly attested correction to a retained editor.

        No action binding, attempt, token, lease, old approval or outcome is
        overwritten. All consumers resolve the append-only revision chain.
        The host must independently re-review the manuscript and actual editor,
        verify source evidence and ended browser calls, and later renew the
        claim and record a NEW final editor preflight. This method never clicks,
        uploads, permits publication or infers the user's permission.
        """
        _fields(attestation, {'operation_id', 'binding_digest', 'expected_account', 'user_source',
                             'user_quote', 'confirmed_at', 'expires_at', 'evidence_ref'}, 'host attestation')
        for key, limit in (('user_source', 500), ('user_quote', 8000), ('evidence_ref', 1000)):
            _text(attestation[key], key, limit)
        confirmed = _timestamp(attestation['confirmed_at'], 'confirmed_at')
        expires = _timestamp(attestation['expires_at'], 'expires_at')
        if (confirmed > self.clock() + 5 or expires <= self.clock() or expires <= confirmed
                or expires - confirmed > 366 * 86400):
            raise ValueError('invalid or expired host attestation time range')
        _fields(observation, {'expected_account', 'browser_context', 'call_ended', 'final_submit_invoked',
                              'editor_retained', 'visible_title', 'visible_body', 'uploaded_media_match',
                              'media_evidence_ref', 'observed_at', 'evidence_ref'}, 'publication revision observation')
        if (observation['call_ended'] is not True or observation['final_submit_invoked'] is not False
                or observation['editor_retained'] is not True or observation['uploaded_media_match'] is not True):
            raise ValueError('ERR_PUBLICATION_REVISION: ended calls and the same unsubmitted retained editor required')
        for key in ('media_evidence_ref', 'evidence_ref', 'browser_context'):
            _text(observation[key], key, 1000)
        observed = _timestamp(observation['observed_at'], 'observed_at')
        with self._transaction():
            row = self._row(operation_id)
            self._claim(row, claim_token, live=False)
            binding = self._publication_settings_candidate(row, payload, attestation['user_source'])
            digest, semantic = _digest(binding), _semantic_digest(binding)
            for key, expected in (('operation_id', operation_id), ('binding_digest', digest),
                                  ('expected_account', binding['expected_account']),
                                  ('user_source', binding['user_source'])):
                if attestation[key] != expected:
                    raise ValueError('ERR_BINDING_MISMATCH: revision attestation ' + key)
            if (observation['expected_account'] != binding['expected_account']
                    or observation['browser_context'] != row['claim_context']
                    or observation['visible_title'] != binding['payload'].get('title')
                    or observation['visible_body'] != binding['payload'].get('content')):
                raise ValueError('ERR_BINDING_MISMATCH: revision account, context or retained manuscript')
            if (observed > self.clock() or self.clock() - observed > self.identity_max_age
                    or observed < row['submitted_at']):
                raise ValueError('ERR_PUBLICATION_REVISION: fresh post-dispatch editor observation required')
            self._config()
            self._guard_write(binding['operation'])
            candidate = dict(row, binding_json=_json(binding), binding_digest=digest,
                             semantic_digest=semantic, authorization=_json(attestation))
            config = self._config()
            if config.get('expected_user_id') not in (None, binding['expected_account']):
                raise ValueError('ERR_ACCOUNT_CHANGED: configured expected_user_id differs from publication account')
            identity = self._guard_identity(candidate)
            if identity['browser_context'] != row['claim_context']:
                raise ValueError('ERR_BROWSER_CONTEXT_CHANGED: fresh identity belongs to another editor context')
            self._guard_media(candidate)
            if any(match['account'] != self.account or match['operation_id'] != operation_id
                   for match in self._semantic_matches(semantic)):
                raise ValueError('ERR_CONTENT_DUPLICATE: revised semantic action already belongs to another action')
            self._verify('authorization_report', candidate, attestation)
            # Rebind only the digest of the SAME action. Original permission,
            # quote, timestamps, blocker and source evidence remain byte-for-byte
            # identical and are independently checked again against the blocker.
            rebindings = []
            for record in self._unknown_continuations(operation_id):
                previous = record['attestation']
                blocked = self._row(previous['blocked_operation_id'])
                self._validate_unknown_continuation(row, blocked, previous)
                rebound = dict(previous, binding_digest=digest)
                self._validate_unknown_continuation(candidate, blocked, rebound)
                self._verify('unknown_continuation_binding_revision', candidate,
                             {'old': previous, 'new': rebound, 'revision_attestation': attestation})
                rebindings.append({'old': previous, 'new': rebound})
            evidence = {'old_binding_digest': row['binding_digest'], 'attestation': attestation,
                        'observation': observation, 'continuation_rebindings': rebindings}
            self._verify('publication_settings_revision', candidate, evidence)
            self._admit({'old_binding': json.loads(row['binding_json']), 'new_binding': binding, **evidence})
            cursor = self.db.execute(
                'INSERT INTO publication_binding_revisions(account,operation_id,attempt_id,'
                'old_binding_json,old_binding_digest,old_semantic_digest,new_binding_json,new_binding_digest,'
                'new_semantic_digest,attestation,observation,continuation_rebindings,recorded_at) '
                'VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)',
                (self.account, operation_id, row['attempt_id'], row['binding_json'], row['binding_digest'],
                 row['semantic_digest'], candidate['binding_json'], digest, semantic, candidate['authorization'],
                 _json(observation), _json(rebindings), self.clock()))
            # Run ordinary account uncertainty, authorization, identity and media
            # guards against the effective new binding before committing anything.
            self._guard_dispatch(self._row(operation_id), browser_context=row['claim_context'])
            self._set(operation_id, row['status'])
            self._event(operation_id, 'unsubmitted_publication_settings_revised',
                        {'revision_id': cursor.lastrowid, 'attempt_id': row['attempt_id'],
                         'old_binding_digest': row['binding_digest'], 'new_binding_digest': digest,
                         'observation': observation, 'attestation': attestation})
        return self.get(operation_id)

    def begin_submit(self, operation_id, claim_token):
        """Persist uncertainty BEFORE any possible external side effect.

        Calling this twice fails even for the same attempt. The host must never
        treat a replayed/failed response as permission to repeat a browser step.
        """
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] != 'dispatching': raise ValueError('ERR_STATE: already submitted or not claimed; do not replay')
            self._claim(row, claim_token, live=True)
            identity = self._guard_dispatch(row, browser_context=row['claim_context'])
            if identity['browser_context'] != row['claim_context']:
                raise ValueError('ERR_BROWSER_CONTEXT_CHANGED: release and re-claim after inspection')
            self._verify('begin_submit', row, {'attempt_id': row['attempt_id'], 'identity': identity})
            self._admit({}); self._set(operation_id, 'submitted_unknown', submitted_at=self.clock())
            self._event(operation_id, 'submission_boundary_entered', {'attempt_id': row['attempt_id']})
        return self.get(operation_id)

    record_host_dispatch = begin_submit
    mark_submitted_unknown = begin_submit

    def release_claim(self, operation_id, claim_token, reason):
        """Only safe before begin_submit and before any consequential host step."""
        _text(reason, 'reason', 2000)
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] != 'dispatching': raise ValueError('ERR_STATE: submitted work cannot be released or requeued')
            self._claim(row, claim_token)
            self._admit({'reason': reason})
            self._set(operation_id, 'ready', owner=None, claim_hash=None, claim_context=None, lease_until=None)
            self._event(operation_id, 'claim_released_before_submit', {'attempt_id': row['attempt_id'], 'reason': reason})
        return self.get(operation_id)

    def recover_expired(self):
        """Reclaim ONLY pre-submit leases; submitted_unknown is never touched."""
        recovered = []
        with self._transaction():
            rows = self.db.execute("SELECT * FROM actions WHERE account=? AND status='dispatching' AND lease_until<=?", (self.account, self.clock())).fetchall()
            for row in rows:
                self._admit({}); operation_id = row['operation_id']
                self._set(operation_id, 'ready', owner=None, claim_hash=None, claim_context=None, lease_until=None)
                self._event(operation_id, 'pre_submit_lease_expired', {'attempt_id': row['attempt_id']})
                recovered.append(operation_id)
        return {'recovered_before_submit': recovered, 'submitted_unknown_requeued': 0}

    def _result(self, row, report, *, reconciliation=False):
        required = {'operation_id', 'binding_digest', 'attempt_id', 'account_id', 'browser_context',
                    'status', 'observed_at', 'evidence_ref', 'evidence_kind', 'details'}
        binding = json.loads(row['binding_json'])
        optional = {'read_at', 'cached'} if binding['operation'] in READ_OPERATIONS else set()
        if not isinstance(report, dict) or not required <= set(report) or set(report) - required - optional:
            raise ValueError('exact result report fields required, with read_at/cached optional only for read operations')
        _json(report, 'result report')
        for key, expected in [('operation_id', row['operation_id']), ('binding_digest', row['binding_digest']),
                              ('attempt_id', row['attempt_id']), ('account_id', binding['expected_account'])]:
            if report[key] != expected: raise ValueError('ERR_BINDING_MISMATCH: result ' + key)
        if report['browser_context'] != row['claim_context']:
            if not reconciliation:
                raise ValueError('ERR_BINDING_MISMATCH: result browser_context')
            identity = self._guard_identity(row)
            if identity['browser_context'] != report['browser_context']:
                raise ValueError('ERR_BINDING_MISMATCH: replacement context requires its own fresh matching identity')
        if report['status'] not in {'host_reported_success', 'not_applied', 'submitted_unknown'}:
            raise ValueError('invalid host result status')
        for key, limit in [('details', 12000), ('evidence_ref', 1000)]: _text(report[key], key, limit)
        observed = _timestamp(report['observed_at'], 'observed_at')
        if binding['operation'] in READ_OPERATIONS:
            if 'cached' in report and report['cached'] is not False:
                raise ValueError('ERR_READ_PROVENANCE: a dispatched read cannot be reported as a cache hit')
            read_at = _timestamp(report.get('read_at', report['observed_at']), 'read_at')
            if read_at > self.clock() or observed > self.clock() or read_at > observed or read_at < (row['submitted_at'] or row['created_at']):
                raise ValueError('ERR_READ_PROVENANCE: actual read timestamp must follow dispatch and not be in the future')
        if observed > self.clock() + 5 or observed < (row['submitted_at'] or row['created_at']):
            raise ValueError('result evidence must follow this action attempt')
        allowed = {'host_reported_success': {'visible_result', 'platform_receipt'},
                   'not_applied': {'platform_rejection', 'confirmed_not_applied'},
                   'submitted_unknown': {'uncertain'}}[report['status']]
        if binding['operation'] == SCHEDULE_INSPECTION and report['status'] == 'host_reported_success':
            allowed = {'visible_result'}  # Inspection never produces a publication receipt.
        if row['status'] == 'dispatching' and not reconciliation:
            allowed = {'host_no_submit'} if report['status'] == 'not_applied' else set()
        if report['evidence_kind'] not in allowed:
            raise ValueError('ERR_EVIDENCE_REQUIRED: actual outcome evidence of the correct kind required')
        self._verify('reconciliation_report' if reconciliation else 'result_report', row, report)

    def report_result(self, operation_id, claim_token, report):
        """Record host-observed outcome. Success remains an unverified host report."""
        with self._transaction():
            row = self._row(operation_id); self._claim(row, claim_token)
            raw = _json(report)
            if row['result'] == raw:
                return self.get(operation_id)
            if row['status'] not in {'dispatching', 'submitted_unknown'}:
                raise ValueError('ERR_STATE: terminal result cannot be overwritten')
            self._result(row, report)
            self._admit(report); self._set(operation_id, report['status'], result=raw)
            self._event(operation_id, 'host_result_reported', report)
        return self.get(operation_id)

    def reconcile(self, operation_id, report):
        """Read-side evidence reconciliation only; never retry or change a site.

        The legacy allow_reconcile flag cannot authorize external browser writes.
        Recording actual outcome evidence is permitted while writes are disabled.
        An optional trusted verifier can validate the evidence reference/path.
        """
        with self._transaction():
            row = self._row(operation_id); raw = _json(report)
            if row['result'] == raw: return self.get(operation_id)
            if row['status'] != 'submitted_unknown': raise ValueError('ERR_STATE: only ambiguous submissions may be reconciled')
            if not isinstance(report, dict) or report.get('status') not in {'host_reported_success', 'not_applied'}:
                raise ValueError('reconciliation requires an observed terminal outcome')
            self._result(row, report, reconciliation=True)
            self._admit(report); self._set(operation_id, report['status'], result=raw)
            self._event(operation_id, 'host_reconciliation_reported', report)
        return self.get(operation_id)

    def cancel(self, operation_id, reason):
        _text(reason, 'reason', 2000)
        with self._transaction():
            row = self._row(operation_id)
            if row['status'] not in {'prepared', 'awaiting_authorization', 'ready'}:
                raise ValueError('ERR_STATE: claimed/submitted/terminal work cannot be cancelled locally')
            self._admit({'reason': reason}); self._set(operation_id, 'cancelled')
            self._event(operation_id, 'cancelled_before_claim', {'reason': reason})
        return self.get(operation_id)

    def events(self, operation_id, limit=100):
        self._row(operation_id)
        if type(limit) is not int or not 1 <= limit <= 500: raise ValueError('invalid event limit')
        rows = self.db.execute('SELECT event,recorded_at,detail FROM action_events WHERE account=? AND operation_id=? ORDER BY id DESC LIMIT ?', (self.account, operation_id, limit)).fetchall()
        return [{'event': row['event'], 'recorded_at': _iso(row['recorded_at']), 'detail': json.loads(row['detail'])} for row in reversed(rows)]


def main():
    """Intentionally read-only CLI: no approval booleans or action executor."""
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--config', required=True)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('status'); sub.add_parser('list')
    sub.add_parser('get').add_argument('operation_id')
    args = parser.parse_args()
    with contextlib.closing(CloudActions(args.config)) as ledger:
        result = ledger.status() if args.command == 'status' else ledger.list_actions() if args.command == 'list' else ledger.get(args.operation_id)
    print(json.dumps(result, ensure_ascii=False, indent=2))


if __name__ == '__main__':
    try: main()
    except (ValueError, KeyError, TypeError, sqlite3.Error) as error:
        print(json.dumps({'error': str(error)}, ensure_ascii=False), file=sys.stderr)
        raise SystemExit(1)
